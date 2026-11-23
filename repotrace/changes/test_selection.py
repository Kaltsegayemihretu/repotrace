"""A shortlist of tests to run for one changed function, with the evidence chain behind each one.

Only code in a test file can be on the list, and only *tests* are recommended. Everything else found in test files
is reported separately with its role, because running it is not how you check a change:

    test     a test function or test method (pytest `test*`, unittest `test*` methods)
    setup    a hook the test runner calls around tests (`setUp`, `tearDown`, `setup_method`, `setUpModule`, ...)
    fixture  a pytest fixture (`@pytest.fixture`), injected into tests by parameter name
    helper   any other function in a test file

When a setup hook or fixture reaches the change, the tests it runs for are recommended *through* it, and the chain
says so: test -> setUp -> ... -> changed function. Bounds, stated rather than hidden:
  - a class hook counts for that class and supported unittest subclasses that inherit it;
  - a module-level hook (`setUpModule`, `setup_module`, `setup_function`) counts for every test in its file;
  - a fixture counts for tests that name it as a parameter, use it via `usefixtures("name")`, or (if `autouse=True`)
    every test in its scope; its scope is its own file, or the folder of the `conftest.py` that defines it;
    fixtures that request other fixtures are followed with cycle detection.
"Recommended" means the static graph connects the test to the change. It does not mean the test will fail, or even
that running it reaches the changed line; see repotrace/evaluation/test_score.py for how that is measured.
"""
import ast
import posixpath
import re
import shlex
import textwrap
from collections import defaultdict, deque
from fnmatch import fnmatchcase

from repotrace.graph.graph_explore import is_test_file

CLASS_HOOKS = {'setUp', 'tearDown', 'asyncSetUp', 'asyncTearDown', 'setUpClass', 'tearDownClass',
               'setup_method', 'teardown_method', 'setup_class', 'teardown_class', 'setup', 'teardown'}
MODULE_HOOKS = {'setUpModule', 'tearDownModule', 'setup_module', 'teardown_module', 'setup_function', 'teardown_function'}
UNITTEST_HOOKS = {'setUp', 'tearDown', 'asyncSetUp', 'asyncTearDown', 'setUpClass', 'tearDownClass',
                  'setUpModule', 'tearDownModule'}
DEFAULT_TEST_LIMIT = 20
_USEFIXTURES = re.compile(r"usefixtures\(([^)]*)\)")


def _is_fixture(symbol):
    return any(re.match(r'^(pytest\.)?fixture\b', d) for d in symbol.get('decorators') or ())


def _autouse(symbol):
    return any(re.match(r'^(pytest\.)?fixture\b', d) and re.search(r'autouse\s*=\s*True', d) for d in symbol.get('decorators') or ())


def _parent_class(symbol, symbols):
    parent = symbols.get(symbol.get('parent_id'))
    return parent if parent is not None and parent['kind'] == 'CLASS' else None


def _unittest_class(symbol, symbols):
    cls = _parent_class(symbol, symbols)
    return cls is not None and (
        cls['id'] in getattr(symbols, 'unittest_ancestry', {})
        or any(b.rsplit('.', 1)[-1] in {'TestCase', 'IsolatedAsyncioTestCase'} for b in cls.get('bases') or ()))



def _matches(name, patterns):
    return any(fnmatchcase(name, pattern) or name.startswith(pattern) for pattern in patterns)


def test_role(symbol, symbols):
    """Classify a function using the comparison's discovery settings, when available."""
    if symbol['kind'] not in ('FUNCTION', 'METHOD'):
        return None
    config = getattr(symbols, 'config', {})
    file = posixpath.basename(symbol['file'])
    xunit = _unittest_class(symbol, symbols)
    files = config.get('python_files', 'test_*.py *_test.py').split()
    pytest_file = any(fnmatchcase(file, pattern) for pattern in files)
    roots = config.get('testpaths', '').split()
    parts = symbol['file'].split('/')
    paths = ['.'] + ['/'.join(parts[:i]) for i in range(1, len(parts) + 1)]
    in_testpaths = not roots or any(fnmatchcase(path, root.removeprefix('./').rstrip('/') or '.')
                                   for path in paths for root in roots)
    if not pytest_file and file != 'conftest.py' and not is_test_file(symbol['file']):
        return None
    if _is_fixture(symbol):
        return 'fixture'
    cls = _parent_class(symbol, symbols)
    parent = symbols.get(symbol.get('parent_id'))
    if parent is not None and parent['kind'] not in ('CLASS', 'MODULE'):
        return 'helper'
    if symbol['name'] in (CLASS_HOOKS if cls is not None else MODULE_HOOKS):
        return 'setup'
    classes = config.get('python_classes', 'Test').split()
    functions = config.get('python_functions', 'test').split()
    if (xunit and (pytest_file or fnmatchcase(file, 'test*.py')) and symbol['name'].startswith('test')
            or pytest_file and in_testpaths and not xunit and _matches(symbol['name'], functions)
            and (cls is None or _matches(cls['name'], classes)
                 and cls['id'] not in getattr(symbols, 'custom_constructors', ()))):
        return 'test'
    return 'helper'


def run_command(test, symbols):
    """Run a test in the selected head checkout."""
    parts = test['file'][:-3].split('/') + test['qualified_name'].split('.')
    if _unittest_class(test, symbols) and all(part.isidentifier() for part in parts):
        return 'python3 -m unittest ' + '.'.join(parts)
    target = f"{test['file']}::{test['qualified_name'].replace('.', '::')}"
    if parts[0].startswith('-'):
        target = './' + target
    return 'python3 -m pytest ' + shlex.quote(target)


def _fixture_name(symbol):
    for decorator in symbol.get('decorators') or ():
        match = re.search(r"\bname\s*=\s*(['\"])(.*?)\1", decorator)
        if match and re.match(r'^(pytest\.)?fixture\b', decorator):
            return match[2]
    return symbol['name']


def _fixture_requests(symbol):
    names = {p['name'] for p in symbol.get('parameters') or ()}
    for decorator in symbol.get('decorators') or ():
        match = _USEFIXTURES.search(decorator)
        if match:
            names.update(x.strip(" '\"") for x in match[1].split(','))
        if 'parametrize' not in decorator:
            continue
        try:
            mark = ast.parse(decorator, mode='eval').body
            if not isinstance(mark, ast.Call) or not mark.args:
                continue
            parameters = ast.literal_eval(mark.args[0])
            indirect = next((ast.literal_eval(kw.value) for kw in mark.keywords if kw.arg == 'indirect'), False)
        except (SyntaxError, ValueError, TypeError, RecursionError):
            continue
        if indirect is True:
            continue
        if isinstance(parameters, str):
            parameters = [part.strip() for part in parameters.split(',')]
        if isinstance(parameters, (list, tuple)):
            fixtures = indirect if isinstance(indirect, (list, tuple)) else ()
            names.difference_update(name for name in parameters if isinstance(name, str) and name not in fixtures)
    return names


def _fixture_scope(fixture, user):
    if fixture['file'] == user['file']:
        if fixture['kind'] != 'METHOD':
            return (1, 0)
        if fixture.get('parent_id') == user.get('parent_id'):
            return (2, 0)
    folder = posixpath.dirname(fixture['file'])
    if (fixture['kind'] != 'METHOD' and posixpath.basename(fixture['file']) == 'conftest.py'
            and (not folder or user['file'].startswith(folder + '/'))):
        return (0, len(folder))
    return (-1, 0)


class TestSymbols(dict):
    """Index discovery and fixture relationships once per snapshot."""
    def __init__(self, report):
        super().__init__((s['id'], s) for f in report['files'] if f['status'] == 'ok' for s in f['symbols'])
        self.config = report.get('test_configuration', {})
        self.unittest_ancestry = report.get('project_index', {}).get('unittest_ancestry', {})
        self.custom_constructors = {s.get('parent_id') for s in self.values()
                                    if s['kind'] == 'METHOD' and s['name'] in ('__init__', '__new__')}
        self.tests = {(s['file'], s['qualified_name']): s for s in self.values() if test_role(s, self) == 'test'}
        self.present = {(s['file'], s['qualified_name']) for s in self.values()
                        if s['kind'] in ('FUNCTION', 'METHOD')}
        self.by_file = defaultdict(list)
        self.by_class = defaultdict(list)
        self.inherited_hook_users = defaultdict(list)
        self.fixture_users = defaultdict(list)
        fixtures = defaultdict(list)
        for symbol in self.values():
            if test_role(symbol, self) == 'fixture':
                fixtures[_fixture_name(symbol)].append(symbol)
        for test in self.tests.values():
            self.by_file[test['file']].append(test)
            self.by_class[test.get('parent_id')].append(test)
        known_hooks = {(s.get('parent_id'), s['name']): s['id']
                       for s in self.values() if s['kind'] == 'METHOD' and s['name'] in UNITTEST_HOOKS}
        for class_id, ancestry in self.unittest_ancestry.items():
            for name in sorted(UNITTEST_HOOKS & CLASS_HOOKS):
                hook = next((known_hooks[(member, name)] for member in ancestry if (member, name) in known_hooks), None)
                if hook is not None and (class_id, name) not in known_hooks:
                    self.inherited_hook_users[hook].extend(self.by_class[class_id])
        auto_ids = {fixture['id'] for group in fixtures.values() for fixture in group if _autouse(fixture)}
        auto = {name for name, group in fixtures.items() if any(f['id'] in auto_ids for f in group)}
        fixture_requests = {fixture['id']: _fixture_requests(fixture)
                            for group in fixtures.values() for fixture in group}
        for test in self.tests.values():
            if _unittest_class(test, self):
                continue
            cls = _parent_class(test, self)
            explicit = _fixture_requests(test)
            if cls is not None:
                explicit.update(_fixture_requests(cls))
            def resolve(name, previous=None):
                # A fixture can request the next outer fixture with its own name.
                choices = [(_fixture_scope(f, test), f) for f in fixtures.get(name, ())]
                ceiling = _fixture_scope(previous, test) if previous is not None else (3, 0)
                choices = [(scope, f) for scope, f in choices if (-1, 0) < scope < ceiling]
                return max(choices, key=lambda pair: (pair[0], pair[1]['span']['line']))[1] if choices else None

            seen = set()
            indirect = deque()
            for name in sorted(explicit | auto):
                fixture = resolve(name)
                if fixture is not None and (name in explicit or fixture['id'] in auto_ids):
                    indirect.append((fixture, [], name not in explicit))
            while indirect:
                fixture, path, autouse = indirect.popleft()
                key = (fixture['id'], autouse)
                if key in seen:
                    continue
                seen.add(key)
                self.fixture_users[fixture['id']].append((test, path, autouse))
                for name in sorted(fixture_requests[fixture['id']]):
                    previous = fixture if name == _fixture_name(fixture) else None
                    dependency = resolve(name, previous)
                    if dependency is not None:
                        indirect.append((dependency, path + [fixture], autouse))


def _test_signature(test):
    try:
        node, = ast.parse(textwrap.dedent(test.get('source', ''))).body
        node.name = '_'
        return ast.dump(node)
    except (SyntaxError, ValueError):
        return None


def match_tests(before, after):
    """Match a moved or renamed test only when its syntax uniquely identifies it on both sides."""
    groups = []
    for current, other in ((before, after), (after, before)):
        by_signature = defaultdict(list)
        for key, test in current.tests.items():
            if key not in other.tests:
                signature = _test_signature(test)
                if signature is not None:
                    by_signature[signature].append((key, test))
        groups.append(by_signature)
    matched, ambiguous = {}, {}
    for signature, older in groups[0].items():
        newer = groups[1].get(signature, [])
        if len(older) == len(newer) == 1:
            matched[older[0][0]] = newer[0][1]
        elif newer:
            for key, _ in older:
                ambiguous[key] = [list(candidate) for candidate, _ in newer]
    return matched, ambiguous


def _chain(entry, affected_by_id, target_name):
    names, calls, current = [], [], entry
    while current is not None:
        names.append(current['symbol']['qualified_name'])
        calls.append(current['witness_call'])
        current = affected_by_id.get(current['next_toward_target'])
    return names + [target_name], calls


def _evidence_rank(evidence):
    via = evidence['via'] or {}
    return (evidence['tier'] != 'supported', via.get('kind') == 'setup' or bool(via.get('autouse')),
            evidence['distance'])


def _evidence_on(change, result, symbols):
    """{test symbol id: evidence} and [other test code] found through one version's graph."""
    affected = result.get('affected', [])
    by_id = {a['symbol']['id']: a for a in affected}
    found, other = {}, []

    def offer(test, chain, tier, distance, via, calls=(), setup_location=None):
        # Prefer sure over maybe, then evidence the test asked for (its own code, a fixture it names) over an autouse
        # fixture that runs for every test, then the shorter chain.
        entry = {'tier': tier, 'distance': distance, 'via': via, 'chain': chain, 'calls': list(calls),
                 'file': test['file'], 'line': test['span']['line']}
        if setup_location is not None:
            entry['setup_location'] = setup_location
        if test['id'] not in found or _evidence_rank(entry) < _evidence_rank(found[test['id']][1]):
            found[test['id']] = (test, entry)

    target = symbols.get(result['target']['id'])
    candidates = list(affected)
    if target is not None:
        candidates.append({'symbol': target, 'distance': 0})
    for a in candidates:
        s = symbols.get(a['symbol']['id'])
        role = None if s is None else test_role(s, symbols)
        if role is None:
            continue
        chain, calls = (_chain(a, by_id, change['qualified_name']) if a['distance']
                        else ([s['qualified_name']], []))
        tier = 'possible' if a.get('path_has_possible_link') else 'supported'
        if role == 'test':
            offer(s, chain, tier, a['distance'], None if calls else {'kind': 'changed_test'}, calls)
            continue
        other.append({'name': s['qualified_name'], 'file': s['file'], 'line': s['span']['line'],
                      'role': role, 'tier': tier, 'distance': a['distance']})
        if role == 'setup':
            cls = _parent_class(s, symbols)
            users = (symbols.by_class[cls['id']] + symbols.inherited_hook_users[s['id']]
                     if cls is not None else symbols.by_file[s['file']])
            for test in users:
                xunit = _unittest_class(test, symbols)
                if ((s['name'] in UNITTEST_HOOKS) != xunit
                        and s['name'] not in ('setup_module', 'teardown_module')):
                    continue
                if s['name'] in ('setup_function', 'teardown_function') and test['kind'] != 'FUNCTION':
                    continue
                offer(test, [test['qualified_name']] + chain, tier, a['distance'] + 1,
                      {'kind': 'setup', 'name': s['qualified_name']}, calls,
                      setup_location={'file': s['file'], 'line': s['span']['line']})
        elif role == 'fixture':
            for test, path, auto in symbols.fixture_users[s['id']]:
                steps = [{'name': f['qualified_name'], 'file': f['file'], 'line': f['span']['line']}
                         for f in [*path, s]]
                offer(test, [test['qualified_name']] + [f['name'] for f in steps[:-1]] + chain,
                      tier, a['distance'] + len(steps),
                      {'kind': 'fixture', 'name': s['qualified_name'], 'steps': steps, 'autouse': auto}, calls)
    return found, other


def select_tests(change, results, symbols_by_side, correspondence, unavailable, alternative_results=None):
    """Shortlist for one change, from BOTH versions' graphs.

    `results` is {side: compact impact result}; `symbols_by_side` is {side: {id: symbol}}. A test linked to the change
    in either version counts: a link seen only in the old version (the change broke what RepoTrace can follow, or the
    function was removed) is often the most important one to run. Each test says which version(s) support its link.
    Tests that exist only in the old version are listed apart, as `removed_tests`: there is nothing to run.
    """
    out = {'tests': [], 'removed_tests': [], 'unverified_tests': [], 'other_test_code': []}
    after = symbols_by_side.get('after', {})
    tests_after = after.tests
    moved, ambiguous = correspondence
    merged = {}
    for side in ('after', 'before'):
        result = results.get(side)
        if not result:
            continue
        symbols = symbols_by_side[side]
        found, other = _evidence_on(change, result, symbols)
        alternative = (alternative_results or {}).get(side)
        if alternative:
            additional, other_additional = _evidence_on(change, alternative, symbols)
            other.extend(other_additional)
            for test_id, candidate in additional.items():
                if test_id not in found or _evidence_rank(candidate[1]) < _evidence_rank(found[test_id][1]):
                    found[test_id] = candidate
        for test, entry in found.values():
            key = (test['file'], test['qualified_name'])
            replacement = moved.get(key) if side == 'before' else None
            previous = key
            if replacement is not None:
                key = (replacement['file'], replacement['qualified_name'])
                entry['previous_identity'] = list(previous)
            m = merged.setdefault(key, {'name': key[1], 'file': key[0], 'evidence': {}})
            m.setdefault('ids', {})[side] = test['id']                  # exact symbol id inside each version
            m['evidence'][side] = entry
        for o in other:
            o['version'] = side
            if not any((x['file'], x['name'], x['role']) == (o['file'], o['name'], o['role']) for x in out['other_test_code']):
                out['other_test_code'].append(o)
    for key, m in merged.items():
        best_side = min(m['evidence'], key=lambda s: (m['evidence'][s]['tier'] != 'supported', m['evidence'][s]['distance'], s != 'after'))
        best = m['evidence'][best_side]
        m.update({'tier': best['tier'], 'distance': best['distance'], 'via': best['via'], 'chain': best['chain'],
                  'seen_in': [s for s in ('before', 'after') if s in m['evidence']]})
        now = tests_after.get(key)
        m['execution_status'] = 'not executed'
        if now is None:
            m['line'] = None
            if key in ambiguous:
                m['possible_head_matches'] = ambiguous[key]
            category = 'unverified_tests' if key in ambiguous or key in after.present or key[0] in unavailable else 'removed_tests'
            out[category].append(m)
        else:
            m['line'] = now['span']['line']
            m['ids']['after'] = now['id']
            m['run'] = run_command(now, after)
            out['tests'].append(m)
    order = lambda t: (t['tier'] != 'supported', t['distance'], t['file'], t['name'])
    out['tests'].sort(key=order)
    out['removed_tests'].sort(key=order)
    out['unverified_tests'].sort(key=order)
    out['other_test_code'].sort(key=lambda t: (t['role'], t['file'], t['name'], t['version']))
    return out


def build_test_plan(changes, limit=DEFAULT_TEST_LIMIT):
    """Rank runnable tests across the whole comparison; keep per-change evidence intact.

    This is a run-first budget, not a claim that the remaining tests can be skipped.
    Rank by evidence strength, a lost path, explicit use over shared setup/autouse,
    then path length. File and name make ties reproducible. No failure probability
    or runtime saving is inferred from these signals.
    """
    if type(limit) is not int or limit < 1:
        raise ValueError('test limit must be a positive integer.')

    def rank(test):
        via = test.get('via') or {}
        shared = via.get('kind') == 'setup' or bool(via.get('autouse'))
        return (test['tier'] != 'supported', test['seen_in'] != ['before'],
                shared, test['distance'], test['file'], test['name'])

    merged = {}
    for idx, change in enumerate(changes):
        for test in (change.get('test_selection') or {}).get('tests', []):
            key = (test['file'], test['name'])
            old = merged.get(key)
            reason = {'change_index': idx, 'file': change.get('file'), 'name': change.get('qualified_name'),
                      'evidence': test.get('evidence', {})}
            if old is None:
                merged[key] = {**test, 'change_indexes': [idx], 'reasons': [reason]}
            else:
                indexes = old['change_indexes'] + [idx]
                reasons = old['reasons'] + [reason]
                if rank(test) < rank(old):
                    merged[key] = {**test, 'change_indexes': indexes, 'reasons': reasons}
                else:
                    old['change_indexes'] = indexes
                    old['reasons'] = reasons
    ranked = sorted(merged.values(), key=rank)
    selected = ranked[:limit]
    return {'tests': selected, 'all_tests': ranked, 'limit': limit, 'total_candidates': len(ranked),
            'selected': len(selected), 'omitted': max(0, len(ranked) - limit),
            'truncated': len(ranked) > limit,
            'ranking': 'Evidence strength, lost path, explicit use before shared setup/autouse, shortest path, file/name.',
            'warning': 'Run these first, not exclusively. Unlisted tests may still matter; an empty list does not mean safe.'}
