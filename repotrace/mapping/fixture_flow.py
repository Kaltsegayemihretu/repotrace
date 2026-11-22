"""pytest fixtures: give a test's parameter the type of the fixture pytest would inject into it.

    @pytest.fixture                     def test_create(app):
    def app():                ──►           app.call(...)        # `app` is what the fixture returned
        return build_app()

pytest matches fixtures to parameters by NAME, looking in this order (the first definition found wins):
  1. the test's own class,  2. the test's module,  3. conftest.py in the test's folder, then each parent folder.
A fixture that asks for a fixture of its own name (an override that wraps the outer one) gets the next one outward.

Bounded on purpose; each of these leaves the parameter without a hint (so calls through it stay unresolved):
  - the fixture is not defined in the project (a plugin or built-in such as tmp_path, monkeypatch, request);
  - the parameter is filled by @pytest.mark.parametrize, or has a default value;
  - the fixture is parametrized (`params=`), or its return/yield values do not all have one known type;
  - the fixture yields more than once, or is defined in a class with base classes (inherited fixtures are not modeled).
Plugins, `pytest_plugins` and command-line options can also replace a fixture; that is disclosed on every hint.
Links that use these hints are POSSIBLE links, like every other inferred method call.
"""
import ast
import posixpath

from repotrace.mapping.method_resolver import Unsupported, span

WARNING = ('pytest injects fixtures by name; a plugin, pytest_plugins or a fixture defined outside the analyzed '
           'files could supply a different object.')


def _is_test_module(path):
    name = posixpath.basename(path)
    return name == 'conftest.py' or (name.endswith('.py') and (name.startswith('test_') or name.endswith('_test.py')))


def _fixture_decorator(node):
    """(is_fixture, explicit name or None, parametrized?) for one decorator expression."""
    call = node if isinstance(node, ast.Call) else None
    target = call.func if call else node
    is_fixture = (isinstance(target, ast.Name) and target.id == 'fixture') or (
        isinstance(target, ast.Attribute) and target.attr == 'fixture' and isinstance(target.value, ast.Name) and target.value.id == 'pytest')
    if not is_fixture:
        return False, None, False
    name, params = None, False
    for kw in (call.keywords if call else []):
        if kw.arg == 'name' and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            name = kw.value.value
        if kw.arg == 'params':
            params = True
    return True, name, params


def _parametrized_names(function):
    names = set()
    for d in function.decorator_list:
        if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == 'parametrize' and d.args
                and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str)):
            names |= {n.strip() for n in d.args[0].value.split(',')}
    return names


class Fixtures:
    def __init__(self, model):
        self.model = model
        self.where = {}           # (file, class node or None) -> {fixture name: function node}  (last definition wins)
        self.info = {}            # function node -> {'file', 'class', 'name', 'params', 'unit'}
        for path, unit in model.project.units.items():
            if not _is_test_module(path):
                continue
            parents = model.parents[path]
            for node in parents:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                owner = parents.get(node)
                if owner is not None and not isinstance(owner, ast.ClassDef):
                    continue                                  # only module- and class-level functions
                for d in node.decorator_list:
                    is_fixture, name, params = _fixture_decorator(d)
                    if is_fixture:
                        fixture_name = name or node.name
                        self.info[node] = {'file': path, 'class': owner, 'name': fixture_name, 'params': params, 'unit': unit}
                        current = self.where.setdefault((path, owner), {}).get(fixture_name)
                        if current is None or (node.lineno, node.col_offset) > (current.lineno, current.col_offset):
                            self.where[(path, owner)][fixture_name] = node

    def levels(self, path, cls):
        """Where pytest looks for a fixture used in `path` (inside class `cls`), nearest first."""
        if cls is not None:
            yield (path, cls)
        yield (path, None)
        folder = posixpath.dirname(path)
        while True:
            conftest = posixpath.join(folder, 'conftest.py') if folder else 'conftest.py'
            if conftest != path:
                yield (conftest, None)
            if not folder:
                return
            folder = posixpath.dirname(folder)

    def lookup(self, name, path, cls, skip=None):
        """The fixture function pytest would use for `name`, or None. `skip`: the overriding fixture asking for it."""
        passed = skip is None
        for level in self.levels(path, cls):
            node = self.where.get(level, {}).get(name)
            if node is None:
                continue
            if not passed:
                passed = node is skip
                continue
            if level[1] is not None and level[1].bases:
                return None                                  # inherited class fixtures are not modeled
            return node
        return None

    def value(self, fixture):
        """Receiver for what the fixture hands to its users: its one yielded value, or its return values' one type."""
        info = self.info[fixture]
        if info['params']:
            raise Unsupported('A parametrized fixture can hand out a different object for each parameter.')
        unit, model = info['unit'], self.model
        scope = model.function_scope(unit, fixture)
        own = [n for n in ast.walk(fixture) if isinstance(n, (ast.Yield, ast.YieldFrom))
               and not any(isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and p is not fixture
                           for p in _ancestors(model.parents[info['file']], n))]
        if own:
            if len(own) != 1 or isinstance(own[0], ast.YieldFrom) or own[0].value is None:
                raise Unsupported('The fixture does not yield exactly one value.')
            statement = model.parents[info['file']].get(own[0])
            parts = [(own[0].value, statement)]
        else:
            parts = [(r.value, r) for r in model.own_returns(fixture)
                     if r.value is not None and not (isinstance(r.value, ast.Constant) and r.value.value is None)]
        if not parts:
            raise Unsupported('The fixture returns no typed value.')
        receivers = [model.receiver(unit, expr, scope, at) for expr, at in parts]
        if len({(r['class'], r['mode']) for r in receivers}) != 1 or any(r.get('multi') for r in receivers):
            raise Unsupported('The fixture can return objects of different types.')
        return receivers[0]


def _ancestors(parents, node):
    node = parents.get(node)
    while node is not None:
        yield node
        node = parents.get(node)


def add_fixture_hints(model, max_rounds=8):
    """Set model.parameter_hints for parameters filled by project fixtures. Never replaces an existing hint."""
    fixtures = Fixtures(model)
    consumers = []
    for path, unit in model.project.units.items():
        if not _is_test_module(path):
            continue
        parents = model.parents[path]
        for node in parents:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            owner = parents.get(node)
            if owner is not None and not isinstance(owner, ast.ClassDef):
                continue
            is_test = node.name.startswith('test')
            if not is_test and node not in fixtures.info:
                continue
            args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            if owner is not None and args and args[0].arg in ('self', 'cls'):
                args = args[1:]
            with_default = {a.arg for a in (node.args.posonlyargs + node.args.args)[len(node.args.posonlyargs + node.args.args) - len(node.args.defaults):]}
            with_default |= {a.arg for a, d in zip(node.args.kwonlyargs, node.args.kw_defaults) if d is not None}
            skip_names = _parametrized_names(node) | with_default | {'request'}
            for arg in args:
                if arg.arg in skip_names or arg in model.parameter_hints:
                    continue
                own = fixtures.info.get(node)
                override = node if own is not None and own['name'] == arg.arg else None
                fixture = fixtures.lookup(arg.arg, path, owner, skip=override)
                if fixture is not None:
                    consumers.append((arg, fixture, path))
    added, refused = {}, {}
    for _ in range(max_rounds):                              # fixtures that use fixtures need their inputs first
        progress = False
        for arg, fixture, path in consumers:
            if arg in added:
                continue
            try:
                receiver = fixtures.value(fixture)
            except Unsupported as exc:
                refused[arg] = str(exc)
                continue
            info = fixtures.info[fixture]
            added[arg] = {**receiver, 'evidence': list(receiver.get('evidence', [])) + [
                {'kind': 'pytest_fixture', 'fixture': info['name'], 'file': info['file'], 'span': span(fixture),
                 'parameter': arg.arg, 'parameter_file': path, 'parameter_span': span(arg), 'warning': WARNING}],
                'pytest_fixture': True}
            model.parameter_hints[arg] = added[arg]
            refused.pop(arg, None)
            progress = True
        if not progress:
            break
    return {'kind': 'pytest_fixture_flow', 'fixtures': len(fixtures.info), 'parameters_matched': len(consumers),
            'parameters_with_hints': len(added), 'parameters_refused': len(refused), 'warning': WARNING}
