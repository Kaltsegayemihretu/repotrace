"""A shortlist of tests to run for one changed function, with the evidence chain behind each one.

Only code in a test file can be on the list, and only *tests* are recommended. Everything else found in test files
is reported separately with its role, because running it is not how you check a change:

    test     a test function or test method (pytest `test*`, unittest `test*` methods)
    setup    a hook the test runner calls around tests (`setUp`, `tearDown`, `setup_method`, `setUpModule`, ...)
    fixture  a pytest fixture (`@pytest.fixture`), injected into tests by parameter name
    helper   any other function in a test file

When a setup hook or fixture reaches the change, the tests it runs for are recommended *through* it, and the chain
says so: test -> setUp -> ... -> changed function. Bounds, stated rather than hidden:
  - a class-level hook counts for the test methods of that same class only (not subclasses);
  - a module-level hook (`setUpModule`, `setup_module`, `setup_function`) counts for every test in its file;
  - a fixture counts for tests that name it as a parameter, use it via `usefixtures("name")`, or (if `autouse=True`)
    every test in its scope; its scope is its own file, or the folder of the `conftest.py` that defines it;
    fixtures that request other fixtures are followed up to FIXTURE_DEPTH steps.
"Recommended" means the static graph connects the test to the change. It does not mean the test will fail, or even
that running it reaches the changed line; see repotrace/evaluation/test_score.py for how that is measured.
"""
import posixpath
import re

from repotrace.graph.graph_explore import is_test_file

CLASS_HOOKS = {'setUp', 'tearDown', 'asyncSetUp', 'asyncTearDown', 'setUpClass', 'tearDownClass',
               'setup_method', 'teardown_method', 'setup_class', 'teardown_class', 'setup', 'teardown'}
MODULE_HOOKS = {'setUpModule', 'tearDownModule', 'setup_module', 'teardown_module', 'setup_function', 'teardown_function'}
FIXTURE_DEPTH = 3
DEFAULT_TEST_LIMIT = 20
_USEFIXTURES = re.compile(r"usefixtures\(([^)]*)\)")


def _is_fixture(symbol):
    return any(re.match(r'^(pytest\.)?fixture\b', d) for d in symbol.get('decorators') or ())


def _autouse(symbol):
    return any(re.match(r'^(pytest\.)?fixture\b', d) and re.search(r'autouse\s*=\s*True', d) for d in symbol.get('decorators') or ())


def _parent_class(symbol, symbols):
    parent = symbols.get(symbol.get('parent_id'))
    return parent if parent is not None and parent['kind'] == 'CLASS' else None


def test_role(symbol, symbols):
    """'test', 'setup', 'fixture' or 'helper' for a function in a test file; None for code outside test files."""
    if symbol['kind'] not in ('FUNCTION', 'METHOD') or not is_test_file(symbol['file']):
        return None
    if _is_fixture(symbol):
        return 'fixture'
    cls = _parent_class(symbol, symbols)
    if (cls is not None and symbol['name'] in CLASS_HOOKS) or (cls is None and symbol['name'] in MODULE_HOOKS):
        return 'setup'
    nested = symbols.get(symbol.get('parent_id'))
    if symbol['name'].startswith('test') and (nested is None or nested['kind'] in ('CLASS', 'MODULE')):
        return 'test'
    return 'helper'


def run_command(test, symbols):
    """How to run just this test: unittest style for a unittest class, pytest style otherwise."""
    cls = _parent_class(test, symbols)
    if cls is not None and any('TestCase' in b for b in (cls.get('bases') or ())):
        return f"python3 -m unittest {test['file'][:-3].replace('/', '.')}.{test['qualified_name']}"
    return f"python3 -m pytest {test['file']}::{test['qualified_name'].replace('.', '::')}"


def _tests_in(symbols, file=None, folder=None, cls_id=None):
    for s in symbols.values():
        if test_role(s, symbols) != 'test':
            continue
        if cls_id is not None and s.get('parent_id') != cls_id:
            continue
        if file is not None and s['file'] != file:
            continue
        if folder is not None and not (folder == '' or s['file'].startswith(folder + '/')):
            continue
        yield s


def _fixture_users(fixture, symbols):
    """Tests that use this fixture directly or through other fixtures (bounded), with the fixtures in between."""
    folder = posixpath.dirname(fixture['file']) if posixpath.basename(fixture['file']) == 'conftest.py' else None
    in_scope = (lambda s: s['file'] == fixture['file']) if folder is None else \
               (lambda s: folder == '' or s['file'].startswith(folder + '/'))
    found, frontier = {}, [(fixture['name'], [fixture['qualified_name']])]
    for _ in range(FIXTURE_DEPTH):
        nxt = []
        for name, via in frontier:
            for s in symbols.values():
                if s['kind'] not in ('FUNCTION', 'METHOD') or not in_scope(s) or s['qualified_name'] in via:
                    continue
                params = {p['name'] for p in s.get('parameters') or ()}
                marked = any(name in [x.strip(' \'"') for x in m.group(1).split(',')]
                             for d in s.get('decorators') or () for m in [_USEFIXTURES.search(d)] if m)
                if name not in params and not marked:
                    continue
                role = test_role(s, symbols)
                if role == 'test' and s['id'] not in found:
                    found[s['id']] = (s, via)
                elif role == 'fixture':
                    nxt.append((s['name'], [s['qualified_name']] + via))
        frontier = nxt
    if _autouse(fixture):
        for s in symbols.values():
            if in_scope(s) and test_role(s, symbols) == 'test' and s['id'] not in found:
                found[s['id']] = (s, [fixture['qualified_name'] + ' (autouse)'])
    return list(found.values())


def _chain(entry, affected_by_id, target_name):
    names, seen, current = [entry['symbol']['qualified_name']], set(), entry
    while current is not None and current.get('next_toward_target') and current['symbol']['id'] not in seen:
        seen.add(current['symbol']['id'])
        current = affected_by_id.get(current['next_toward_target'])
        if current is not None:
            names.append(current['symbol']['qualified_name'])
    return names + [target_name]


def _evidence_on(change, result, symbols):
    """{test symbol id: evidence} and [other test code] found through one version's graph."""
    affected = result.get('affected', [])
    by_id = {a['symbol']['id']: a for a in affected}
    found, other = {}, []

    def offer(test, chain, tier, distance, via):
        # Prefer sure over maybe, then evidence the test asked for (its own code, a fixture it names) over an autouse
        # fixture that runs for every test, then the shorter chain.
        rank = lambda e: (e['tier'] != 'supported', bool(e['via'] and e['via'].get('autouse')), e['distance'])
        entry = {'tier': tier, 'distance': distance, 'via': via, 'chain': chain}
        if test['id'] not in found or rank(entry) < rank(found[test['id']][1]):
            found[test['id']] = (test, entry)

    for a in affected:
        s = symbols.get(a['symbol']['id'])
        role = None if s is None else test_role(s, symbols)
        if role is None:
            continue
        tier = 'possible' if a.get('path_has_possible_link') else 'supported'
        chain = _chain(a, by_id, change['qualified_name'])
        if role == 'test':
            offer(s, chain, tier, a['distance'], None)
            continue
        other.append({'name': s['qualified_name'], 'file': s['file'], 'line': s['span']['line'],
                      'role': role, 'tier': tier, 'distance': a['distance']})
        if role == 'setup':
            cls = _parent_class(s, symbols)
            users = list(_tests_in(symbols, cls_id=cls['id'])) if cls is not None else list(_tests_in(symbols, file=s['file']))
            for test in users:
                offer(test, [test['qualified_name']] + chain, tier, a['distance'] + 1, {'kind': 'setup', 'name': s['qualified_name']})
        elif role == 'fixture':
            for test, via in _fixture_users(s, symbols):
                auto = via[-1].endswith(' (autouse)')
                offer(test, [test['qualified_name']] + via[:-1] + chain, tier, a['distance'] + len(via),
                      {'kind': 'fixture', 'name': s['qualified_name'], **({'autouse': True} if auto else {})})
    return found, other


def select_tests(change, results, symbols_by_side):
    """Shortlist for one change, from BOTH versions' graphs.

    `results` is {side: compact impact result}; `symbols_by_side` is {side: {id: symbol}}. A test linked to the change
    in either version counts: a link seen only in the old version (the change broke what RepoTrace can follow, or the
    function was removed) is often the most important one to run. Each test says which version(s) support its link.
    Tests that exist only in the old version are listed apart, as `removed_tests`: there is nothing to run.
    """
    out = {'tests': [], 'removed_tests': [], 'other_test_code': []}
    after = symbols_by_side.get('after', {})
    tests_after = {(s['file'], s['qualified_name']): s for s in after.values() if test_role(s, after) == 'test'}
    merged = {}
    for side in ('after', 'before'):
        result = results.get(side)
        if not result:
            continue
        symbols = symbols_by_side[side]
        found, other = _evidence_on(change, result, symbols)
        for test, entry in found.values():
            key = (test['file'], test['qualified_name'])
            m = merged.setdefault(key, {'name': test['qualified_name'], 'file': test['file'], 'evidence': {}})
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
        if now is None:
            m['line'] = None
            out['removed_tests'].append(m)
        else:
            m['line'] = now['span']['line']
            m['run'] = run_command(now, after)
            out['tests'].append(m)
    order = lambda t: (t['tier'] != 'supported', t['distance'], t['file'], t['name'])
    out['tests'].sort(key=order)
    out['removed_tests'].sort(key=order)
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
            if old is None:
                merged[key] = {**test, 'change_indexes': [idx]}
            else:
                indexes = old['change_indexes'] + [idx]
                if rank(test) < rank(old):
                    merged[key] = {**test, 'change_indexes': indexes}
                else:
                    old['change_indexes'] = indexes
    ranked = sorted(merged.values(), key=rank)
    selected = ranked[:limit]
    return {'tests': selected, 'limit': limit, 'total_candidates': len(ranked),
            'selected': len(selected), 'omitted': max(0, len(ranked) - limit),
            'truncated': len(ranked) > limit,
            'ranking': 'Evidence strength, lost path, explicit use before shared setup/autouse, shortest path, file/name.',
            'warning': 'Run these first, not exclusively. Unlisted tests may still matter; an empty list does not mean safe.'}
