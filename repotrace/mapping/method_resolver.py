"""Bounded, evidence-carrying method candidates; never executes analyzed code.

These are possible targets, not a Python runtime simulator. In particular, type
hints and the first parameter of a method do not establish its runtime type.
Only this module's public entry point mutates reports; the graph adapter decides
whether a consumer opts into candidate edges.
"""
import ast
import importlib
from collections import defaultdict
from functools import lru_cache

from repotrace.reading.resolver import lookup, position


class Unsupported(Exception):
    pass


# Standard-library base classes whose every attribute name can be listed exactly, so a project class built on them
# still has a known method order. Any name these bases define (class attributes, and attributes their __init__ sets
# on each instance) is refused rather than resolved: the base could supply or replace it.
KNOWN_STDLIB_BASES = (('unittest', 'TestCase'), ('unittest', 'IsolatedAsyncioTestCase'))


@lru_cache(maxsize=None)
def stdlib_base_names(module, attr):
    """Every attribute an instance of this standard-library class has before project code runs. Imports only the
    standard library, never analyzed code."""
    cls = getattr(importlib.import_module(module), attr)
    names = set(dir(cls))
    if (module, attr) == ('typing', 'Generic'):
        # Generic supplies typing machinery but no instance fields, and cannot
        # itself be instantiated. Reserve its names just like other known bases.
        return frozenset(names)
    try:
        names |= set(vars(cls()))
    except Exception:                        # if it cannot be made, refuse every name rather than guess
        names.add('*')
    return frozenset(names)


def span(node):
    return dict(zip(('line', 'column', 'end_line', 'end_column'), position(node)))


class ClassBindings(ast.NodeVisitor):
    """Writes in a class body, without entering method/nested-class bodies."""
    def __init__(self, node):
        self.bindings = defaultdict(list)
        self.wildcard = False
        for item in node.body:
            self.visit(item)

    def visit_FunctionDef(self, node):
        self.bindings[node.name].append(node)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bindings[node.id].append(node)

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.visit(node.target)
            self.visit(node.value)

    def visit_Lambda(self, node):
        pass

    def visit_Import(self, node):
        for alias in node.names:
            self.bindings[alias.asname or alias.name.split('.')[0]].append(node)

    def visit_ImportFrom(self, node):
        for alias in node.names:
            if alias.name == '*':
                self.wildcard = True
            else:
                self.bindings[alias.asname or alias.name].append(node)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.bindings[node.name].append(node)
        self.generic_visit(node)

    def visit_MatchAs(self, node):
        if node.name:
            self.bindings[node.name].append(node)
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node):
        if node.rest:
            self.bindings[node.rest].append(node)
        self.generic_visit(node)


class MethodCandidates:
    def __init__(self, project, max_depth=16, max_candidates=8):
        if max_depth < 1 or max_candidates < 1:
            raise ValueError('Method inference limits must be positive.')
        self.project = project
        self.max_depth = max_depth
        self.max_candidates = max_candidates
        self.classes = {}
        self.node_classes = {}
        self.parents = {}
        self.scopes = {}
        self.mro_cache = {}
        self.runtime_class_cache = {}
        self.parameter_hints = {}
        self.tainted = defaultdict(set)
        self.field_index = None
        self.return_cache = {}
        for path, unit in project.units.items():
            parents = {}
            scopes = {unit['index'].root}
            for _, scope, _ in unit['index'].calls.values():
                while scope is not None:
                    scopes.add(scope)
                    scope = scope.parent
            for statement in unit['index'].root.direct:
                parents[statement] = None
                for parent in ast.walk(statement):
                    for child in ast.iter_child_nodes(parent):
                        parents[child] = parent
            self.parents[path] = parents
            self.scopes[path] = scopes
            statement_scopes = {node: scope for scope in scopes for node in scope.direct}
            for node in parents:
                if not isinstance(node, ast.ClassDef):
                    continue
                symbol = unit['symbols'].get((node.lineno, node.col_offset))
                if symbol is None:
                    continue
                owner = statement_scopes.get(node)
                info = {'id': symbol['id'], 'symbol': symbol, 'unit': unit,
                        'file': path, 'node': node, 'owner': owner,
                        'bindings': ClassBindings(node).bindings}
                self.classes[symbol['id']] = info
                self.node_classes[node] = info
        self.scan_mutations()

    def reference(self, unit, expression, scope, node, enforce_order=True):
        # Import locally to avoid a module-import cycle at integration time.
        from repotrace.mapping.project_resolver import Unresolved
        try:
            return self.project.reference_value(unit, expression, scope, node,
                                                enforce_order=enforce_order)
        except Unresolved as exc:
            raise Unsupported(str(exc))

    def class_reference(self, unit, expression, scope, node, enforce_order=True):
        ref = self.reference(unit, expression, scope, node, enforce_order)
        if ref['kind'] != 'class':
            raise Unsupported('The expression is not an established class binding.')
        info = self.classes.get(ref['symbol']['id'])
        if info is None:
            raise Unsupported('The class has no analyzed declaration.')
        if info['owner'] is None:
            info['owner'] = ref.get('owner_scope')
        self.check_class(info)
        return info, ref

    def check_class(self, info):
        node = info['node']
        if 'checked' in info:
            if info['checked'] is not None:
                raise Unsupported(info['checked'])
            return
        if info['owner'] is None:
            # The defining scope is not known yet; do not cache a verdict.
            raise Unsupported('A conditional or unavailable class scope cannot establish its type.')
        try:
            if getattr(node, 'type_params', []):
                raise Unsupported('Decorated, metaclass-configured or generic classes are not inferred.')
            if node not in info['owner'].direct:
                raise Unsupported('A conditional or unavailable class scope cannot establish its type.')
            for keyword in node.keywords:
                if not (keyword.arg == 'metaclass' and self.stdlib_name(
                        info['unit'], keyword.value, info['owner'], 'abc', 'ABCMeta')):
                    raise Unsupported('Decorated, metaclass-configured or generic classes are not inferred.')
            writes = set()
            for decorator in node.decorator_list:
                writes |= self.identity_decorator(info, decorator)
            info['decorator_writes'] = writes
            # A custom attribute interceptor may replace even an ordinary method.
            if '__getattribute__' in info['bindings']:
                raise Unsupported('Custom __getattribute__ can replace method lookup.')
        except Unsupported as exc:
            info['checked'] = str(exc)
            raise
        info['checked'] = None

    def stdlib_name(self, unit, expr, scope, module, attr):
        """True only for an unshadowed, unconditional import of the standard-library name."""
        if module in self.project.modules or any(m.startswith(module + '.') for m in self.project.modules):
            return False
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.attr == attr:
            name, kind = expr.value.id, 'module'
        elif isinstance(expr, ast.Name):
            name, kind = expr.id, 'member'
        else:
            return False
        bindings, owner, reason = lookup(scope, name, unit['index'].root)
        if reason or len(bindings) != 1 or bindings[0]['kind'] != 'import':
            return False
        statement = bindings[0]['node']
        if statement not in owner.direct:
            return False
        if kind == 'module' and isinstance(statement, ast.Import):
            return any(a.name == module and (a.asname or a.name) == name for a in statement.names)
        if kind == 'member' and isinstance(statement, ast.ImportFrom):
            return (statement.module == module and statement.level == 0 and
                    any(a.name == attr and (a.asname or a.name) == name for a in statement.names))
        return False

    def identity_decorator(self, info, decorator):
        """Names written on the class, if this decorator provably returns the class itself."""
        factory = isinstance(decorator, ast.Call)
        try:
            ref = self.reference(info['unit'], decorator.func if factory else decorator,
                                 info['owner'], info['node'])
        except Unsupported:
            raise Unsupported('Decorated, metaclass-configured or generic classes are not inferred.')
        if ref['kind'] != 'function':
            raise Unsupported('Decorated, metaclass-configured or generic classes are not inferred.')
        function = ref['node']
        if factory:
            inner = [s for s in function.body if isinstance(s, ast.FunctionDef)]
            returns = self.own_returns(function)
            if (len(returns) != 1 or not isinstance(returns[0].value, ast.Name) or len(inner) != 1
                    or returns[0].value.id != inner[0].name
                    or sum(isinstance(n, ast.Name) and n.id == inner[0].name and
                           isinstance(n.ctx, (ast.Store, ast.Del)) for n in ast.walk(function)) != 0):
                raise Unsupported('Decorated, metaclass-configured or generic classes are not inferred.')
            function = inner[0]
        writes = self.identity_writes(function)
        if writes is None:
            raise Unsupported('Decorated, metaclass-configured or generic classes are not inferred.')
        return writes

    @staticmethod
    def own_returns(function):
        found, stack = [], list(function.body)
        while stack:
            node = stack.pop()
            if isinstance(node, ast.Return):
                found.append(node)
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                stack.extend(ast.iter_child_nodes(node))
        return found

    def identity_writes(self, function):
        """Attributes the decorator sets on its argument; None unless it returns that argument."""
        if not isinstance(function, ast.FunctionDef) or function.decorator_list:
            return None
        args = function.args
        if (len(args.posonlyargs) + len(args.args) != 1 or args.vararg or args.kwarg or args.kwonlyargs):
            return None
        param = (args.posonlyargs + args.args)[0].arg
        returns = self.own_returns(function)
        if not returns or any(not (isinstance(r.value, ast.Name) and r.value.id == param) for r in returns):
            return None
        writes = set()
        for node in ast.walk(function):
            if isinstance(node, ast.Name) and node.id == param and not isinstance(node.ctx, ast.Load):
                return None
            if isinstance(node, (ast.Global, ast.Nonlocal)) and param in node.names:
                return None
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and
                    node.func.id in {'setattr', 'delattr', 'exec', 'eval', 'vars', 'type'}):
                return None
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == param:
                if node.attr in {'__dict__', '__class__', '__bases__'}:
                    return None
                if not isinstance(node.ctx, ast.Load):
                    writes.add(node.attr)
        return writes

    def builtin_name(self, unit, scope, name):
        if scope.kind == 'class' and (scope.wildcard or name in scope.bindings):
            return False
        bindings, _, reason = lookup(scope, name, unit['index'].root)
        return not bindings and reason == 'No definition found in the visible function or module scopes.'

    def bases(self, info):
        self.check_class(info)
        bases, unknown = [], []
        for expr in info['node'].bases:
            # A nested class header evaluates in the enclosing class body,
            # whereas normal function lookup deliberately skips that namespace.
            # Do not accidentally resolve an outer class alias as a global.
            root = expr
            if isinstance(root, ast.Subscript):
                root = root.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if (info['owner'].kind == 'class' and isinstance(root, ast.Name) and
                    (info['owner'].wildcard or root.id in info['owner'].bindings)):
                unknown.append(ast.unparse(expr))
                continue
            if isinstance(expr, ast.Name) and expr.id == 'object' and self.builtin_name(
                    info['unit'], info['owner'], 'object'):
                continue
            if self.stdlib_name(info['unit'], expr, info['owner'], 'abc', 'ABC'):
                continue
            if isinstance(expr, ast.Subscript) and self.stdlib_name(
                    info['unit'], expr.value, info['owner'], 'typing', 'Generic'):
                info.setdefault('stdlib_bases', set()).add('typing.Generic')
                continue
            known = next((m + '.' + a for m, a in KNOWN_STDLIB_BASES if self.stdlib_name(info['unit'], expr, info['owner'], m, a)), None)
            if known:
                info.setdefault('stdlib_bases', set()).add(known)
                continue
            try:
                base, _ = self.class_reference(info['unit'], expr, info['owner'], info['node'])
            except Unsupported:
                unknown.append(ast.unparse(expr))
            else:
                bases.append(base['id'])
        return bases, unknown

    def mro(self, class_id, trail=()):
        if class_id in self.mro_cache:
            return self.mro_cache[class_id]
        if class_id in trail or len(trail) >= self.max_depth:
            raise Unsupported('Inheritance cycle or inference depth limit.')
        info = self.classes[class_id]
        bases, unknown = self.bases(info)
        if unknown:
            raise Unsupported('Unknown base classes: ' + ', '.join(unknown))
        if len(set(bases)) != len(bases):
            raise Unsupported('Duplicate bases do not establish a valid method order.')
        sequences = [list(self.mro(b, trail + (class_id,))) for b in bases] + [list(bases)]
        result = [class_id]
        while any(sequences):
            sequences = [s for s in sequences if s]
            head = next((s[0] for s in sequences if all(s[0] not in t[1:] for t in sequences)), None)
            if head is None:
                raise Unsupported('Conflicting base classes do not establish a valid method order.')
            result.append(head)
            for sequence in sequences:
                if sequence and sequence[0] == head:
                    sequence.pop(0)
        self.mro_cache[class_id] = tuple(result)
        return tuple(result)

    def stdlib_base_attrs(self, class_id):
        """Names supplied by a known standard-library base anywhere in this class's method order."""
        names = set()
        for member in self.mro(class_id):
            for base in self.classes[member].get('stdlib_bases', ()):
                names |= stdlib_base_names(*base.rsplit('.', 1))
        return names

    def refuse_stdlib_name(self, class_id, name):
        names = self.stdlib_base_attrs(class_id)
        if name in names or '*' in names:
            bases = sorted({b for m in self.mro(class_id) for b in self.classes[m].get('stdlib_bases', ())})
            raise Unsupported(f"The standard-library base {', '.join(bases)} also defines '{name}'.")

    def transparent_decorator(self, info, decorator):
        """True if this method decorator provably forwards *args/**kwargs to the original."""
        root = decorator.func if isinstance(decorator, ast.Call) else decorator
        base = root
        while isinstance(base, ast.Attribute):
            base = base.value
        if not isinstance(base, ast.Name) or base.id in info['bindings']:
            return False
        try:
            ref = self.reference(info['unit'], root, info['owner'], info['node'])
        except Unsupported:
            return False
        if ref['kind'] != 'function' or ref['file'] not in self.project.units:
            return False
        unit, function = self.project.units[ref['file']], ref['node']
        if isinstance(decorator, ast.Call):
            function = self.returned_inner(function)
        return function is not None and self.forwarding_decorator(unit, function)

    def returned_inner(self, function):
        """The one nested def a function returns by name, else None."""
        if not isinstance(function, ast.FunctionDef) or function.decorator_list:
            return None
        returns = self.own_returns(function)
        if len(returns) != 1 or not isinstance(returns[0].value, ast.Name):
            return None
        name = returns[0].value.id
        inner = [s for s in function.body if isinstance(s, ast.FunctionDef) and s.name == name]
        if len(inner) != 1 or any(isinstance(n, ast.Name) and n.id == name and
                                  not isinstance(n.ctx, ast.Load) for n in ast.walk(function)):
            return None
        return inner[0]

    def forwarding_decorator(self, unit, function):
        if not isinstance(function, ast.FunctionDef) or function.decorator_list:
            return False
        args = function.args
        if (len(args.posonlyargs) + len(args.args) != 1 or args.vararg or args.kwarg or args.kwonlyargs):
            return False
        wrapped = (args.posonlyargs + args.args)[0].arg
        wrapper = self.returned_inner(function)
        if wrapper is None:
            return False
        wargs = wrapper.args
        if (wargs.posonlyargs or wargs.args or wargs.kwonlyargs or wargs.vararg is None
                or wargs.kwarg is None):
            return False
        for decorator in wrapper.decorator_list:
            if not (isinstance(decorator, ast.Call) and self.stdlib_name(
                    unit, decorator.func, unit['index'].root, 'functools', 'wraps')):
                return False
        names = {wrapped, wargs.vararg.arg, wargs.kwarg.arg}
        if any(isinstance(n, ast.Name) and n.id in names and not isinstance(n.ctx, ast.Load)
               for n in ast.walk(function)):
            return False
        for node in ast.walk(wrapper):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == wrapped
                    and len(node.args) == 1 and isinstance(node.args[0], ast.Starred)
                    and isinstance(node.args[0].value, ast.Name) and node.args[0].value.id == wargs.vararg.arg
                    and len(node.keywords) == 1 and node.keywords[0].arg is None
                    and isinstance(node.keywords[0].value, ast.Name)
                    and node.keywords[0].value.id == wargs.kwarg.arg):
                return True
        return False

    def method_kind(self, info, method):
        if not method.decorator_list:
            if method.name in {'__new__', '__init_subclass__', '__class_getitem__'}:
                return 'class'
            return 'instance'
        if all(self.stdlib_name(info['unit'], d, info['owner'], 'abc', 'abstractmethod')
               or self.transparent_decorator(info, d) for d in method.decorator_list):
            return 'instance'
        if len(method.decorator_list) == 1:
            decorator = method.decorator_list[0]
            if isinstance(decorator, ast.Name) and decorator.id in {'classmethod', 'staticmethod'}:
                if self.builtin_name(info['unit'], info['owner'], decorator.id):
                    # A preceding class assignment can shadow the builtin too.
                    if decorator.id not in info['bindings']:
                        return 'class' if decorator.id == 'classmethod' else 'static'
        raise Unsupported('A property, descriptor or custom decorator may replace the method.')

    def parameter_receiver(self, unit, arg, owner):
        path = unit['report']['file']
        parent = self.parents[path].get(arg)
        method = self.parents[path].get(parent)
        cls = self.parents[path].get(method)
        info = self.node_classes.get(cls)
        if info and isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = method.args.posonlyargs + method.args.args
            if args and args[0] is arg and owner.parent is not None and owner.parent.kind == 'class':
                self.check_class(info)
                mode = self.method_kind(info, method)
                if mode != 'static':
                    return {'class': info['id'], 'mode': mode, 'polymorphic': True,
                            'evidence': [{'kind': 'method_first_parameter', 'file': path,
                                          'span': span(arg), 'class_id': info['id']} ]}
        # An annotation is a hint, never a runtime guarantee.
        annotation = getattr(arg, 'annotation', None)
        if annotation is not None and isinstance(annotation, (ast.Name, ast.Attribute)):
            if owner.parent is None:
                raise Unsupported('No annotation definition scope.')
            info, _ = self.class_reference(unit, annotation, owner.parent, arg, enforce_order=False)
            return {'class': info['id'], 'mode': 'instance', 'polymorphic': True,
                    'evidence': [{'kind': 'parameter_type_hint', 'file': path,
                                  'span': span(arg), 'annotation': ast.unparse(annotation),
                                  'warning': 'Python does not enforce type hints at runtime.'}]}
        if arg in self.parameter_hints:
            hint = self.parameter_hints[arg]
            if hint.get('class') not in self.classes or hint.get('mode') not in {'instance', 'class'}:
                raise Unsupported('The observed argument has no supported analyzed type.')
            self.check_class(self.classes[hint['class']])
            return {**hint, 'evidence': list(hint.get('evidence', []))}
        raise Unsupported('The parameter has no supported receiver evidence.')

    def receiver(self, unit, expr, scope, at_node, depth=0, seen=(), enforce_order=True):
        if depth >= self.max_depth:
            raise Unsupported('Receiver inference depth limit.')
        path = unit['report']['file']
        if isinstance(expr, ast.Call):
            try:
                info, ref = self.class_reference(unit, expr.func, scope, at_node, enforce_order)
            except Unsupported as construction_error:
                try:
                    return self.call_result(unit, expr, scope, depth, seen)
                except Unsupported:
                    raise construction_error
            # Custom __new__ can return an entirely different object's type.
            for class_id in self.mro(info['id']):
                if ('__new__' in self.classes[class_id]['bindings'] or
                        self.tainted[class_id].intersection({'__new__', '__bases__', '__class__', '*'})):
                    raise Unsupported('A custom __new__ may return another type.')
            return {'class': info['id'], 'mode': 'instance', 'polymorphic': False,
                    'evidence': [{'kind': 'constructor_expression', 'file': path,
                                  'span': span(expr), 'expression': ast.unparse(expr.func)}]}
        if isinstance(expr, ast.Name):
            bindings, owner, reason = lookup(scope, expr.id, unit['index'].root)
            if reason or not bindings or len(bindings) != 1:
                raise Unsupported(reason or 'The receiver is reassigned or ambiguous.')
            binding = bindings[0]
            node = binding['node']
            if node in seen:
                raise Unsupported('Receiver alias cycle.')
            if binding['kind'] == 'parameter':
                return self.parameter_receiver(unit, node, owner)
            if binding['kind'] == 'assignment or deletion':
                statement = self.parents[path].get(node)
                if not isinstance(statement, (ast.Assign, ast.AnnAssign)) or statement not in owner.direct:
                    raise Unsupported('Only unconditional simple receiver assignments are inferred.')
                if isinstance(statement, ast.Assign) and not all(isinstance(t, ast.Name) for t in statement.targets):
                    raise Unsupported('Destructuring assignments do not establish one receiver.')
                if statement.value is None:
                    raise Unsupported('An annotation without a value does not construct a receiver.')
                if enforce_order and position(statement)[:2] >= position(at_node)[:2]:
                    raise Unsupported('The receiver assignment is not before this use.')
                value = self.receiver(unit, statement.value, owner, statement,
                                      depth + 1, seen + (node,), enforce_order)
                return {**value, 'evidence': value['evidence'] + [
                    {'kind': 'receiver_assignment', 'file': path, 'span': span(statement),
                     'name': expr.id}]}
        if isinstance(expr, ast.Attribute):
            try:
                base = self.receiver(unit, expr.value, scope, at_node, depth + 1, seen, enforce_order)
            except Unsupported:
                base = None
            if base is not None and base['mode'] == 'instance':
                return self.field_receiver(base, expr.attr, depth, seen)
        info, ref = self.class_reference(unit, expr, scope, at_node, enforce_order)
        evidence = [{'kind': 'class_binding', 'file': info['file'],
                     'span': span(info['node']), 'class_id': info['id']}]
        if ref.get('import_chain'):
            evidence.append({'kind': 'import_chain', 'chain': ref['import_chain']})
        return {'class': info['id'], 'mode': 'class', 'polymorphic': False, 'evidence': evidence}

    def runtime_classes(self, receiver):
        if receiver.get('multi'):
            merged = []
            for part in receiver['multi']:
                merged += [c for c in self.runtime_classes(part) if c not in merged]
            return merged
        key = receiver['class'], receiver.get('polymorphic', False)
        if key in self.runtime_class_cache:
            return self.runtime_class_cache[key]
        result = [receiver['class']]
        if receiver.get('polymorphic'):
            for class_id in sorted(self.classes):
                if class_id == receiver['class']:
                    continue
                try:
                    if receiver['class'] in self.mro(class_id)[1:]:
                        result.append(class_id)
                except Unsupported:
                    continue
        self.runtime_class_cache[key] = tuple(result)
        return result

    def build_field_index(self):
        """Every attribute write in the project, split into self-writes and foreign writes."""
        self_writes, foreign, star = defaultdict(list), defaultdict(list), []
        for unit in self.project.units.values():
            path = unit['report']['file']
            parents = self.parents[path]
            for node, scope in unit['index'].attribute_writes:
                record = self.self_write(unit, parents, node, scope)
                if record is None:
                    foreign[node.attr].append((unit, node.value, scope, node))
                else:
                    self_writes[node.attr].append(record)
            for node, scope, _ in unit['index'].calls.values():
                if (isinstance(node.func, ast.Name) and node.func.id in {'setattr', 'delattr'}
                        and len(node.args) >= 2):
                    attr = node.args[1]
                    record = (unit, node.args[0], scope, node)
                    if isinstance(attr, ast.Constant) and isinstance(attr.value, str):
                        foreign[attr.value].append(record)
                    else:
                        star.append(record)
        self.field_index = {'self': self_writes, 'foreign': foreign, 'star': star}
        self.foreign_active = set()

    def may_write_field(self, record, classes, attr):
        """Could this non-self write hit an instance of one of `classes`?

        True: it may. False: its object is a known unrelated class. None: object type unknown.
        """
        unit, expr, scope, node = record
        key = id(node)
        if key in self.foreign_active:
            return None
        self.foreign_active.add(key)
        try:
            receiver = self.receiver(unit, expr, scope, node, enforce_order=False)
        except Unsupported:
            return None
        finally:
            self.foreign_active.discard(key)
        reachable = set(self.runtime_classes(receiver))
        for member in list(reachable):
            try:
                reachable.update(self.mro(member))
            except Unsupported:
                return None
        return bool(reachable.intersection(classes))

    def self_write(self, unit, parents, node, scope):
        """A write like `self.x = value` in an instance method of an analyzed class, else None."""
        if not isinstance(node.value, ast.Name) or not isinstance(node.ctx, ast.Store):
            return None
        method = parents.get(node)
        while method is not None and not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                             ast.Lambda, ast.ClassDef)):
            method = parents.get(method)
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None
        info = self.node_classes.get(parents.get(method))
        args = method.args.posonlyargs + method.args.args
        if info is None or not args or args[0].arg != node.value.id:
            return None
        if any(isinstance(d, ast.Name) and d.id in {'classmethod', 'staticmethod'}
               for d in method.decorator_list):
            return None
        bindings, _, reason = lookup(scope, node.value.id, unit['index'].root)
        if reason or len(bindings) != 1 or bindings[0]['node'] is not args[0]:
            return None
        statement = parents.get(node)
        value = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and statement.targets[0] is node:
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.target is node:
            value = statement.value
        return {'class': info['id'], 'unit': unit, 'scope': scope, 'node': node,
                'statement': statement, 'value': value}

    def field_receiver(self, base, attr, depth, seen):
        """Type of `obj.attr` when every assignment to it in the class hierarchy is typed."""
        if depth >= self.max_depth:
            raise Unsupported('Receiver inference depth limit.')
        if self.field_index is None:
            self.build_field_index()
        index = self.field_index
        classes = []
        for class_id in self.runtime_classes(base):
            for member in self.mro(class_id):
                if member not in classes:
                    classes.append(member)
        if any(self.may_write_field(r, classes, attr) is not False for r in index['foreign'].get(attr, [])):
            raise Unsupported('This attribute may also be assigned through another object.')
        if any('*' in self.tainted[member] for member in classes):
            raise Unsupported('A setattr() call with a computed name targets this class.')
        disclosed = []
        for record in index['star']:
            verdict = self.may_write_field(record, classes, attr)
            if verdict:
                raise Unsupported('A setattr() call with a computed name may assign this attribute.')
            if verdict is None:
                disclosed.append({'kind': 'unverified_dynamic_write', 'file': record[0]['report']['file'],
                                  'span': span(record[3]),
                                  'warning': 'setattr() with a computed attribute name on an object '
                                             'of unknown type; it could assign this field.'})
        for member in classes:
            self.check_class(self.classes[member])
            self.refuse_stdlib_name(member, attr)
            if attr in self.classes[member]['bindings']:
                raise Unsupported('A class attribute or method has the same name as this field.')
        writes = [w for w in index['self'].get(attr, ()) if w['class'] in classes]
        if not writes:
            raise Unsupported('No assignment to this field was found in the class hierarchy.')
        parts = []
        for write in writes:
            value = write['value']
            if value is None:
                raise Unsupported('A field assignment form is not supported (augmented, destructuring or deleted).')
            if isinstance(value, ast.Constant) and value.value is None:
                continue
            key = ('field', write['class'], attr, write['node'].lineno, write['node'].col_offset)
            if key in seen:
                raise Unsupported('Field assignments depend on each other.')
            part = self.receiver(write['unit'], value, write['scope'], write['statement'],
                                 depth + 1, seen + (key,), True)
            path = write['unit']['report']['file']
            part = {**part, 'evidence': part['evidence'] + [
                {'kind': 'field_assignment', 'file': path, 'span': span(write['node']),
                 'attribute': attr, 'class_id': write['class']}] + disclosed}
            parts.append(part)
        if not parts:
            raise Unsupported('The field is only ever assigned None.')
        if len({p['mode'] for p in parts}) != 1:
            raise Unsupported('Field assignments mix classes and instances.')
        if len(parts) == 1:
            return parts[0]
        return {'class': parts[0]['class'], 'mode': parts[0]['mode'], 'multi': parts,
                'polymorphic': any(p.get('polymorphic') for p in parts),
                'evidence': [step for p in parts for step in p['evidence']]}

    def scan_mutations(self):
        # Explicit writes are collected before any candidates are issued. A write
        # anywhere is treated conservatively, without claiming execution order.
        for unit in self.project.units.values():
            writes = [(node.value, node.attr, scope, node)
                      for node, scope in unit['index'].attribute_writes]
            for node, scope, _ in unit['index'].calls.values():
                if (isinstance(node.func, ast.Name) and node.func.id in {'setattr', 'delattr'}
                        and len(node.args) >= 2):
                    attr = node.args[1]
                    writes.append((node.args[0], attr.value if isinstance(attr, ast.Constant)
                                   and isinstance(attr.value, str) else '*', scope, node))
            for expr, name, scope, node in writes:
                try:
                    receiver = self.receiver(unit, expr, scope, node, enforce_order=False)
                except Unsupported:
                    continue
                self.tainted[receiver['class']].add(name)

    def find_method(self, class_id, name, after=None):
        info = self.classes[class_id]
        self.check_class(info)
        # Do not fall back to a direct declaration when inheritance fails:
        # an unknown metaclass or inherited __getattribute__ can replace it.
        order = list(self.mro(class_id))
        self.refuse_stdlib_name(class_id, name)
        # A mutation of a class or known base invalidates old targets.
        for current in order:
            if self.tainted[current].intersection({name, '*', '__getattribute__', '__bases__', '__class__'}):
                raise Unsupported('An explicit attribute write may replace this method.')
        if after is not None:
            if after not in order:
                raise Unsupported('The super class is absent from the receiver method order.')
            order = order[order.index(after) + 1:]
        for current in order:
            owner = self.classes[current]
            self.check_class(owner)
            if name in owner.get('decorator_writes', ()):
                raise Unsupported('A class decorator sets this attribute and may replace the method.')
            bindings = owner['bindings'].get(name, [])
            if not bindings:
                continue
            if len(bindings) != 1:
                raise Unsupported('The method name has competing class bindings.')
            method = bindings[0]
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) or method not in owner['node'].body:
                raise Unsupported('The attribute is not an unconditional method definition.')
            if getattr(method, 'type_params', []):
                raise Unsupported('Generic method scope is unsupported.')
            self.method_kind(owner, method)
            symbol = owner['unit']['symbols'].get((method.lineno, method.col_offset))
            if symbol is None:
                raise Unsupported('No method symbol was extracted.')
            return symbol, owner, []
        raise Unsupported('No supported method exists in the known method order.')

    def super_receiver(self, unit, expr, scope):
        if expr.args or expr.keywords or not self.builtin_name(unit, scope, 'super'):
            raise Unsupported('Only the unshadowed zero-argument super form is supported.')
        parent = self.parents[unit['report']['file']]
        node = expr
        while node in parent:
            node = parent[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                break
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or parent.get(node) not in self.node_classes:
            raise Unsupported('Zero-argument super must be in a directly declared method.')
        args = node.args.posonlyargs + node.args.args
        if not args:
            raise Unsupported('Zero-argument super requires the method first parameter.')
        bindings, owner, reason = lookup(scope, args[0].arg, unit['index'].root)
        if reason or len(bindings) != 1 or bindings[0]['node'] is not args[0]:
            raise Unsupported('The super receiver parameter was reassigned.')
        receiver = self.parameter_receiver(unit, args[0], owner)
        return receiver, receiver['class']

    def targets(self, unit, node, scope):
        kind, after = 'method', None
        if isinstance(node.func, ast.Attribute):
            expression = node.func.value
            if (isinstance(expression, ast.Call) and isinstance(expression.func, ast.Name)
                    and expression.func.id == 'super'):
                receiver, after = self.super_receiver(unit, expression, scope)
            else:
                receiver = self.receiver(unit, expression, scope, node)
            names = [node.func.attr]
        else:
            receiver = self.receiver(unit, node.func, scope, node)
            if receiver['mode'] == 'class':
                names, kind = ['__new__', '__init__'], 'constructor'
            else:
                names, kind = ['__call__'], 'callable_instance'
        targets, seen, truncated = [], set(), False
        for class_id in self.runtime_classes(receiver):
            for name in names:
                try:
                    symbol, owner, unknown = self.find_method(class_id, name, after=after)
                except Unsupported:
                    continue
                if symbol['id'] in seen:
                    continue
                seen.add(symbol['id'])
                if len(targets) >= self.max_candidates:
                    truncated = True
                    continue
                evidence = list(receiver['evidence']) + [
                    {'kind': 'declared_method', 'file': owner['file'], 'span': symbol['span'],
                     'receiver_class': class_id, 'defined_in_class': owner['id'],
                     'method': name, 'unknown_bases': unknown,
                     'super_after': after}]
                targets.append({'target_id': symbol['id'], 'file': owner['file'],
                                'qualified_name': symbol['qualified_name'],
                                'reason': 'Supported static receiver evidence suggests this declaration; '
                                          'runtime dispatch, unobserved subclasses or mutation may differ.',
                                'evidence': {'steps': evidence, 'assumptions': [
                                    'The observed class binding is used with ordinary Python attribute dispatch.',
                                    'Unobserved subclasses, dynamic code or external mutation may change the target.'
                                ] + (['The field is typed from every assignment to it in the class hierarchy; '
                                      'no write through a typed foreign object was found.']
                                     if any(s['kind'] == 'field_assignment' for s in evidence) else [])
                                  + (['An untyped setattr() with a computed name exists and could change this field.']
                                     if any(s['kind'] == 'unverified_dynamic_write' for s in evidence) else [])}})
        if not targets:
            raise Unsupported('The receiver has no supported callable declaration.')
        return targets, kind, truncated

    def run(self):
        for unit in self.project.units.values():
            for call in unit['report']['calls']:
                if call.get('target_id') is not None or call.get('reason_code') == 'ATTRIBUTE_MUTATION':
                    continue
                s = call['span']
                key = (s['line'], s['column'], s['end_line'], s['end_column'])
                if unit['positions'][key] != 1:
                    continue
                node, scope, context = unit['index'].calls[key]
                if context != 'function':
                    continue
                try:
                    targets, kind, truncated = self.targets(unit, node, scope)
                except Unsupported as exc:
                    if isinstance(node.func, ast.Attribute):
                        call['method_resolution_reason'] = str(exc)
                    continue
                call.update(resolution='POSSIBLE', resolution_kind=kind, target_id=None,
                            candidate_targets=targets, reason_code='METHOD_CANDIDATE' if kind == 'method'
                            else 'CONSTRUCTOR_CANDIDATE' if kind == 'constructor' else 'CALLABLE_INSTANCE_CANDIDATE',
                            reason='Possible targets based on static receiver evidence; not a guaranteed runtime call.',
                            candidate_limit_reached=truncated)


    # ---- return-type inference: the instance a call returns, when every return is typed ----

    def function_scope(self, unit, function):
        mapping = unit.setdefault('scope_by_node', None)
        if mapping is None:
            mapping = unit['scope_by_node'] = {s.node: s for s in unit['index'].all_scopes}
        scope = mapping.get(function)
        if scope is None:
            raise Unsupported('The function scope is unavailable.')
        return scope

    def merge_receivers(self, parts):
        if len({p['mode'] for p in parts}) != 1:
            raise Unsupported('Return values mix classes and instances.')
        unique, seen_keys = [], set()
        for part in parts:
            key = (part['class'], part.get('polymorphic', False), part['mode'])
            if key not in seen_keys:
                seen_keys.add(key)
                unique.append(part)
        if len(unique) == 1:
            return unique[0]
        if len(unique) > self.max_candidates:
            raise Unsupported('Too many distinct return classes.')
        return {'class': unique[0]['class'], 'mode': unique[0]['mode'], 'multi': unique,
                'polymorphic': any(p.get('polymorphic') for p in unique),
                'evidence': [step for p in unique for step in p['evidence']]}

    def callee_functions(self, unit, call, scope):
        """(unit, FunctionDef, symbol) for every function this call may run; empty means unknown."""
        from repotrace.mapping.project_resolver import Unresolved
        func = call.func
        found = []
        try:
            ref = self.project.reference_value(unit, func, scope, call)
        except Unresolved:
            ref = None
        if ref is not None:
            if ref['kind'] != 'function' or not isinstance(ref.get('node'), ast.FunctionDef):
                raise Unsupported('The callee is not a plain project function.')
            return [(self.project.units[ref['file']], ref['node'], ref['symbol'])]
        if not isinstance(func, ast.Attribute):
            raise Unsupported('The callee is not a supported function reference.')
        base = self.instance_receiver(unit, func.value, scope, call)
        if base is None:
            raise Unsupported('The call receiver has no supported instance type.')
        for class_id in self.runtime_classes(base):
            try:
                symbol, owner, _ = self.find_method(class_id, func.attr)
            except Unsupported:
                continue
            node = owner['bindings'][func.attr][0]
            if isinstance(node, ast.FunctionDef) and all(s[2]['id'] != symbol['id'] for s in found):
                found.append((owner['unit'], node, symbol))
        if not found:
            raise Unsupported('No supported method declaration was found for the call.')
        return found

    def return_receiver(self, unit, function, symbol, depth, seen):
        cache_key = (unit['report']['file'], function.lineno, function.col_offset)
        if cache_key in self.return_cache:
            return self.return_cache[cache_key]
        if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(function)):
            raise Unsupported('A generator returns a generator object, not its return value.')
        scope = self.function_scope(unit, function)
        parts = []
        for statement in self.own_returns(function):
            value = statement.value
            if value is None or (isinstance(value, ast.Constant) and value.value is None):
                continue
            part = self.receiver(unit, value, scope, statement, depth + 1, seen, True)
            parts.append({**part, 'evidence': part['evidence'] + [
                {'kind': 'return_value', 'file': unit['report']['file'], 'span': span(statement),
                 'function': symbol['qualified_name']}]})
        if not parts:
            raise Unsupported('The function has no typed return value.')
        result = self.merge_receivers(parts)
        self.return_cache[cache_key] = result
        return result

    def call_result(self, unit, call, scope, depth, seen):
        if depth >= self.max_depth:
            raise Unsupported('Receiver inference depth limit.')
        parts = []
        for target_unit, function, symbol in self.callee_functions(unit, call, scope):
            key = ('return', symbol['id'])
            if key in seen:
                raise Unsupported('Return values depend on each other.')
            parts.append(self.return_receiver(target_unit, function, symbol, depth + 1, seen + (key,)))
        return self.merge_receivers(parts)

    # ---- implicit special-method calls: operators, protocols and builtins ----

    def instance_receiver(self, unit, expression, scope, at_node):
        try:
            receiver = self.receiver(unit, expression, scope, at_node)
        except Unsupported:
            return None
        return receiver if receiver['mode'] == 'instance' else None

    def dunder_targets(self, receiver, names):
        """Per runtime class, the first of `names` it defines (Python's own fallback order)."""
        targets, seen, truncated = [], set(), False
        for class_id in self.runtime_classes(receiver):
            for name in names:
                try:
                    symbol, owner, unknown = self.find_method(class_id, name)
                except Unsupported:
                    continue
                if symbol['id'] not in seen:
                    seen.add(symbol['id'])
                    if len(targets) >= self.max_candidates:
                        truncated = True
                    else:
                        evidence = list(receiver['evidence']) + [
                            {'kind': 'declared_method', 'file': owner['file'], 'span': symbol['span'],
                             'receiver_class': class_id, 'defined_in_class': owner['id'],
                             'method': name, 'unknown_bases': unknown, 'super_after': None}]
                        targets.append({
                            'target_id': symbol['id'], 'file': owner['file'],
                            'qualified_name': symbol['qualified_name'],
                            'reason': 'Python runs this special method here (operator, protocol or builtin); '
                                      'the receiver class comes from supported static evidence.',
                            'evidence': {'steps': evidence, 'assumptions': [
                                'The operand has the inferred class and ordinary Python special-method dispatch applies.',
                                'Unobserved subclasses, a NotImplemented result, dynamic code or external mutation may change the target.']}})
                break
        return targets, truncated

    def add_implicit(self, unit, node, scope, dunder, targets, truncated, ordinal=0, anchor=None):
        anchor = anchor or node
        function = scope
        while function is not None and function.kind != 'function':
            function = function.parent
        if function is None or not targets:
            return
        symbol = unit['symbols'].get((function.node.lineno, function.node.col_offset))
        if symbol is None:
            return
        path = unit['report']['file']
        s = span(anchor)
        identifier = f"{path}::implicit@{s['line']}:{s['column']}:{s['end_line']}:{s['end_column']}:{dunder}:{ordinal}"
        if identifier in self.implicit_ids:
            return
        self.implicit_ids.add(identifier)
        unit['report']['calls'].append({
            'id': identifier, 'enclosing_symbol_id': symbol['id'], 'context': 'FUNCTION_BODY', 'file': path,
            'span': s, 'expression': ast.unparse(anchor)[:160], 'arguments': [], 'keywords': [],
            'target_id': None, 'resolution': 'POSSIBLE', 'resolution_kind': 'implicit',
            'reason_code': 'IMPLICIT_CANDIDATE',
            'reason': f'Python calls {dunder} here without an explicit call; possible targets based on static receiver evidence.',
            'candidate_targets': targets, 'candidate_limit_reached': truncated,
            'implicit': True, 'implicit_method': dunder})

    def implicit_for(self, unit, node, scope, expression, names, ordinal=0, label=None, anchor=None):
        receiver = self.instance_receiver(unit, expression, scope, node)
        if receiver is None:
            return
        targets, truncated = self.dunder_targets(receiver, names)
        self.add_implicit(unit, node, scope, label or names[0], targets, truncated, ordinal, anchor)

    def implicit_binary(self, unit, node, scope, names, left_expr, right_expr, ordinal=0, forward=None):
        forward, reflected = (forward or (names[0],)), (names[1] if len(names) > 1 and names[1] else None)
        left = self.instance_receiver(unit, left_expr, scope, node)
        right = self.instance_receiver(unit, right_expr, scope, node)
        targets, truncated = [], False
        if left is not None:
            targets, truncated = self.dunder_targets(left, forward)
        if right is not None and reflected and (
                (left is None and isinstance(left_expr, ast.Constant)) or
                (left is not None and not set(self.runtime_classes(left)) & set(self.runtime_classes(right)))):
            more, cut = self.dunder_targets(right, reflected if isinstance(reflected, tuple) else (reflected,))
            seen = {t['target_id'] for t in targets}
            targets += [t for t in more if t['target_id'] not in seen][:max(0, self.max_candidates - len(targets))]
            truncated = truncated or cut
        self.add_implicit(unit, node, scope, forward[0], targets, truncated, ordinal)

    def run_implicit(self):
        for unit in self.project.units.values():
            self.implicit_ids = getattr(self, 'implicit_ids', set())
            for node, scope, context in unit['index'].implicit:
                if context != 'function':
                    continue
                try:
                    self.implicit_node(unit, node, scope)
                except (Unsupported, RecursionError):
                    continue
            for _, (node, scope, context) in sorted(unit['index'].calls.items()):
                if context != 'function' or not isinstance(node.func, ast.Name):
                    continue
                names = IMPLICIT_BUILTINS.get(node.func.id)
                if names is None or not self.builtin_name(unit, scope, node.func.id):
                    continue
                if any(isinstance(a, ast.Starred) for a in node.args):
                    continue
                if node.func.id == 'print':
                    for k, argument in enumerate(node.args):
                        self.implicit_for(unit, node, scope, argument, names, k)
                elif node.args and (node.func.id != 'str' or (len(node.args) == 1 and not node.keywords)):
                    self.implicit_for(unit, node, scope, node.args[0], names)

    def implicit_node(self, unit, node, scope):
        if isinstance(node, ast.BinOp):
            names = IMPLICIT_BINARY.get(type(node.op))
            if names is None:
                return
            if (isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant)
                    and isinstance(node.left.value, str)):
                if '%s' in node.left.value:
                    operands = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
                    for k, operand in enumerate(operands):
                        self.implicit_for(unit, node, scope, operand, ('__str__', '__repr__'), k)
                return
            self.implicit_binary(unit, node, scope, names, node.left, node.right)
        elif isinstance(node, ast.UnaryOp):
            name = IMPLICIT_UNARY.get(type(node.op))
            if name:
                self.implicit_for(unit, node, scope, node.operand, (name,))
        elif isinstance(node, ast.Compare):
            left = node.left
            for k, (operator, right) in enumerate(zip(node.ops, node.comparators)):
                if isinstance(operator, (ast.In, ast.NotIn)):
                    self.implicit_for(unit, node, scope, right, ('__contains__',), k)
                elif type(operator) in IMPLICIT_COMPARE:
                    forward, reflected = IMPLICIT_COMPARE[type(operator)]
                    self.implicit_binary(unit, node, scope, (forward[0], reflected), left, right, k, forward)
                left = right
        elif isinstance(node, ast.Subscript):
            name = {ast.Load: '__getitem__', ast.Store: '__setitem__', ast.Del: '__delitem__'}.get(type(node.ctx))
            if name:
                self.implicit_for(unit, node, scope, node.value, (name,))
        elif isinstance(node, ast.AugAssign):
            names = IMPLICIT_BINARY.get(type(node.op))
            if names and isinstance(node.target, (ast.Name, ast.Attribute)):
                self.implicit_for(unit, node, scope, node.target, (names[2], names[0]), 0, names[2])
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self.implicit_for(unit, node, scope, node.iter,
                              ('__aiter__',) if isinstance(node, ast.AsyncFor) else ('__iter__',), anchor=node.iter)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            enter, leave = (('__aenter__', '__aexit__') if isinstance(node, ast.AsyncWith) else ('__enter__', '__exit__'))
            for k, item in enumerate(node.items):
                self.implicit_for(unit, node, scope, item.context_expr, (enter,), 2 * k, anchor=item.context_expr)
                self.implicit_for(unit, node, scope, item.context_expr, (leave,), 2 * k + 1, anchor=item.context_expr)
        elif isinstance(node, ast.JoinedStr):
            for k, part in enumerate(node.values):
                if isinstance(part, ast.FormattedValue):
                    names = ('__repr__',) if part.conversion == 114 else ('__format__', '__str__', '__repr__')
                    self.implicit_for(unit, node, scope, part.value, names, k)

IMPLICIT_BINARY = {
    ast.Add: ('__add__', '__radd__', '__iadd__'), ast.Sub: ('__sub__', '__rsub__', '__isub__'),
    ast.Mult: ('__mul__', '__rmul__', '__imul__'), ast.MatMult: ('__matmul__', '__rmatmul__', '__imatmul__'),
    ast.Div: ('__truediv__', '__rtruediv__', '__itruediv__'),
    ast.FloorDiv: ('__floordiv__', '__rfloordiv__', '__ifloordiv__'),
    ast.Mod: ('__mod__', '__rmod__', '__imod__'), ast.Pow: ('__pow__', '__rpow__', '__ipow__'),
    ast.LShift: ('__lshift__', '__rlshift__', '__ilshift__'), ast.RShift: ('__rshift__', '__rrshift__', '__irshift__'),
    ast.BitOr: ('__or__', '__ror__', '__ior__'), ast.BitXor: ('__xor__', '__rxor__', '__ixor__'),
    ast.BitAnd: ('__and__', '__rand__', '__iand__')}
IMPLICIT_COMPARE = {
    ast.Eq: (('__eq__',), ('__eq__',)), ast.NotEq: (('__ne__', '__eq__'), ('__ne__', '__eq__')),
    ast.Lt: (('__lt__',), ('__gt__',)), ast.LtE: (('__le__',), ('__ge__',)),
    ast.Gt: (('__gt__',), ('__lt__',)), ast.GtE: (('__ge__',), ('__le__',))}
IMPLICIT_UNARY = {ast.USub: '__neg__', ast.UAdd: '__pos__', ast.Invert: '__invert__'}
IMPLICIT_BUILTINS = {
    'len': ('__len__',), 'str': ('__str__', '__repr__'), 'repr': ('__repr__',),
    'bool': ('__bool__', '__len__'), 'iter': ('__iter__',), 'next': ('__next__',),
    'hash': ('__hash__',), 'format': ('__format__', '__str__', '__repr__'), 'print': ('__str__', '__repr__')}


def resolve_method_candidates(project_index, *, max_depth=16, max_candidates=8, parameter_hints=None):
    """Add bounded POSSIBLE targets to unresolved function-body calls in place."""
    model = MethodCandidates(project_index, max_depth=max_depth, max_candidates=max_candidates)
    model.parameter_hints.update(parameter_hints or {})
    model.run()
