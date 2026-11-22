"""Score RepoTrace's test shortlist for a change against what really happens when the tests run.

THIS SCRIPT EXECUTES THE TARGET'S TESTS. Point it only at a project whose code you have reviewed. Each version runs
from a throwaway copy made with `git archive`, with network connections blocked; that is a precaution, not a sandbox.
pytest projects need an interpreter that has pytest (pass `--python`, e.g. a virtual environment); unittest projects
run with any Python.

Two different truths, measured separately because they answer different questions:

  relevant   tests that actually EXECUTE a changed function while they run (at the old version for changed or
             removed functions, at the new version for changed or added ones). This is what the shortlist claims:
             "these tests exercise the code you changed".
  failing    tests that pass at the old version and fail or error at the new one. A relevant test can pass (the change
             was fine), and a failing test can fail for a reason the shortlist never claimed to know.

The whole suite runs once per version, in its normal order, so setUpClass/setUpModule, fixtures of every scope and
cleanups behave as usual. Code is credited to the test that was running at the time; code run by class-, module- or
session-level setup is credited to whichever test triggered it (usually the first in its scope). A run that could not
be completed (discovery or collection errors, a crash, a timeout) is reported as INCOMPLETE, never as an empty result.

`relevant` and `failing` retain the uncapped connected-candidate metrics for comparison with older evaluations.
`shortlist` separately scores the capped run-first list, including omitted candidates and failing tests found.

  python3 -m repotrace.evaluation.test_score REPO BASE HEAD [--python PATH] [--json out.json]
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from repotrace.changes.change_impact import analyze_change_impact
from repotrace.changes.test_selection import build_test_plan

TIMEOUT = 900

# Runs inside the target's copy. Writes {"status", "problems", "tests": {key: {"outcome", "executed"}}}.
RUNNER = r'''
import json, os, sys, threading
mode, root, out = sys.argv[1], sys.argv[2], sys.argv[3]
os.chdir(root)
from repotrace.evaluation.runtime_recorder import block_network
block_network()
state = {'current': None, 'executed': {}, 'outcome': {}, 'problems': []}
RANK = {'skip': 0, 'pass': 1, 'fail': 2, 'error': 3}

def prof(frame, event, arg):
    if event == 'call' and state['current'] is not None:
        path = frame.f_code.co_filename
        if path.startswith(root):
            state['executed'][state['current']].add((os.path.relpath(path, root).replace(os.sep, '/'), frame.f_code.co_firstlineno))

def begin(key):
    state['current'] = key
    state['executed'].setdefault(key, set())

def outcome(key, value):          # a parametrized test is one entry: its worst outcome
    old = state['outcome'].get(key)
    if old is None or RANK[value] > RANK[old]:
        state['outcome'][key] = value

def key_from_file(path, qualname):
    return os.path.relpath(path, root).replace(os.sep, '/')[:-3].replace('/', '.') + '.' + qualname

status = 'ok'
if mode == 'unittest':
    import inspect, unittest
    sys.path.insert(0, root)
    class Result(unittest.TestResult):
        def key(self, test):
            if type(test).__name__ in ('_FailedTest', '_ErrorHolder'):
                return None
            return key_from_file(inspect.getsourcefile(type(test)), type(test).__qualname__ + '.' + test._testMethodName)
        def startTest(self, test):
            super().startTest(test); k = self.key(test)
            if k: begin(k); outcome(k, 'pass')
        def stopTest(self, test):
            super().stopTest(test); state['current'] = None
        def addError(self, test, err):
            super().addError(test, err); k = self.key(test)
            if k: outcome(k, 'error')
            else: state['problems'].append('error outside a test: ' + str(test) + ': ' + self._exc_info_to_string(err, test).strip().splitlines()[-1])
        def addFailure(self, test, err):
            super().addFailure(test, err); k = self.key(test)
            if k: outcome(k, 'fail')
        def addSkip(self, test, reason):
            super().addSkip(test, reason); k = self.key(test)
            if k: state['outcome'][k] = 'skip'
    loader = unittest.TestLoader()
    # Discover from the root and from every folder holding test*.py. A folder inside a package is reached through the
    # topmost folder that is not a package (as `python -m unittest discover` does), because relative imports only work there.
    def topmost(folder):
        while folder != '.' and os.path.exists(os.path.join(folder, '__init__.py')):
            folder = os.path.dirname(folder) or '.'
        return folder
    starts = sorted({'.'} | {topmost(os.path.relpath(d, '.')) for d, _, fs in os.walk('.')
                     if not any(p.startswith('.') for p in os.path.relpath(d, '.').split(os.sep) if p != '.')
                     and any(f.startswith('test') and f.endswith('.py') for f in fs)})
    suite, seen = unittest.TestSuite(), set()
    def flat(s):
        for t in s:
            yield from (flat(t) if isinstance(t, unittest.TestSuite) else [t])
    pending = []
    for start in starts:
        if start != '.' and os.path.abspath(start) not in sys.path:
            sys.path.insert(0, os.path.abspath(start))
        loader.errors = []
        found = loader.discover(start, top_level_dir='.' if start == '.' else start)
        failed = []
        for t in flat(found):
            if type(t).__name__ == '_FailedTest':
                failed.append(t)
                continue
            k = (inspect.getsourcefile(type(t)), type(t).__qualname__, t._testMethodName)
            if k not in seen:
                seen.add(k); suite.addTest(t)
        pending.append((failed, list(loader.errors)))
    # Discovering from a sub-folder can fail for modules that were loaded fine from their package root (relative imports);
    # such a failure is dropped only when a module of the same name was really loaded. Anything else stays a problem.
    loaded = {os.path.splitext(os.path.basename(k[0]))[0] for k in seen if k[0]}
    for failed, errors in pending:
        uncovered = [t for t in failed if t.id().split('.')[-1] not in loaded]
        for t in uncovered:
            state['problems'].append('could not load: ' + t.id() + ': ' + str(getattr(t, '_exception', '')).splitlines()[0][:200])
        if uncovered or not failed:
            for e in errors:
                state['problems'].append('discovery error: ' + e.strip().splitlines()[-1][:200])
    sys.setprofile(prof); threading.setprofile(prof)
    try:
        suite.run(Result())
    finally:
        sys.setprofile(None); threading.setprofile(None)
else:
    import pytest
    def key_of(nodeid):
        parts = nodeid.split('[')[0].split('::')
        return parts[0][:-3].replace('/', '.') + '.' + '.'.join(parts[1:])
    class Plugin:
        def pytest_sessionstart(self, session):
            sys.setprofile(prof); threading.setprofile(prof)
        def pytest_sessionfinish(self, session, exitstatus):
            sys.setprofile(None); threading.setprofile(None)
        def pytest_runtest_logstart(self, nodeid, location):
            begin(key_of(nodeid))
        def pytest_runtest_logfinish(self, nodeid, location):
            state['current'] = None
        def pytest_runtest_logreport(self, report):
            k = key_of(report.nodeid)
            if report.skipped:
                outcome(k, 'skip')
            elif report.failed:
                outcome(k, 'fail' if report.when == 'call' else 'error')
            elif report.when == 'call':
                outcome(k, 'pass')
        def pytest_collectreport(self, report):
            if report.failed:
                state['problems'].append('collection error: ' + report.nodeid + ': ' + str(report.longrepr).strip().splitlines()[-1][:200])
    code = pytest.main(['-q', '-p', 'no:cacheprovider', '-p', 'no:randomly', '--continue-on-collection-errors', '--rootdir', root], plugins=[Plugin()])
    if int(code) not in (0, 1):
        state['problems'].append(f'pytest stopped with exit code {int(code)}')
        status = 'incomplete'
if state['problems']:
    status = 'incomplete'
tests = {k: {'outcome': state['outcome'].get(k, 'error'), 'executed': sorted(v)} for k, v in state['executed'].items()}
if not tests:
    status = 'incomplete'; state['problems'].append('no tests were run')
json.dump({'status': status, 'mode': mode, 'problems': state['problems'], 'tests': tests}, open(out, 'w'))
'''


def snapshot(repo, rev, into):
    archive = subprocess.run(['git', '-C', str(repo), 'archive', rev], capture_output=True, check=True).stdout
    into.mkdir(parents=True)
    subprocess.run(['tar', '-x', '-C', str(into)], input=archive, check=True)
    return into


def test_mode(root):
    """pytest only when the project itself names pytest: a conftest, a config file that mentions it, or test files that import it.
    The mere existence of a tox.ini proves nothing (markdown's tox.ini runs `unittest discover`)."""
    def names_pytest(name):
        path = root / name
        return path.exists() and 'pytest' in path.read_text(errors='ignore')
    uses_pytest = (root / 'conftest.py').exists() or any(names_pytest(n) for n in ('pytest.ini', 'tox.ini', 'setup.cfg', 'pyproject.toml')) or any(
        'import pytest' in p.read_text(errors='ignore') for p in root.rglob('*.py') if 'test' in p.name)
    return 'pytest' if uses_pytest else 'unittest'


def run_tests(root, work, python):
    """Run one version's suite; returns the runner's record, or an INCOMPLETE record saying why."""
    out, mode = work / (root.name + '.json'), test_mode(root)
    env = {'PYTHONPATH': str(Path(__file__).resolve().parents[2]), 'PATH': '/usr/bin:/bin', 'HOME': str(work),
           'PYTHONDONTWRITEBYTECODE': '1'}
    try:
        done = subprocess.run([python, '-c', RUNNER, mode, str(root.resolve()) + '/', str(out)], env=env, cwd=root,
                              capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {'status': 'incomplete', 'mode': mode, 'problems': [f'timed out after {TIMEOUT}s'], 'tests': {}}
    except OSError as exc:
        return {'status': 'incomplete', 'mode': mode, 'problems': [f'the test runner could not start: {exc}'], 'tests': {}}
    if not out.exists():
        tail = (done.stderr or done.stdout).strip().splitlines()[-3:]
        return {'status': 'incomplete', 'mode': mode, 'problems': ['the test runner crashed: ' + ' | '.join(tail)], 'tests': {}}
    return json.loads(out.read_text())


def test_key(file, qualified_name):
    return file[:-3].replace('/', '.') + '.' + qualified_name


def first_lines(root, file, line):
    """The line numbers Python may report as this function's start: the def line, or its first decorator."""
    import ast
    try:
        tree = ast.parse((root / file).read_text(encoding='utf-8'))
    except (OSError, SyntaxError, ValueError):
        return {line}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno == line and node.decorator_list:
            return {line, min(d.lineno for d in node.decorator_list)}
    return {line}


def executes(record, file, lines):
    return any(f == file and line in lines for f, line in record['executed'])


def score(repo, base, head, python=sys.executable, report=None):
    report = report or analyze_change_impact(repo, base, head, include_possible=True)
    with tempfile.TemporaryDirectory(prefix='rt-tests-') as tmp:
        work = Path(tmp)
        roots = {'before': snapshot(repo, base, work / 'before'), 'after': snapshot(repo, head, work / 'after')}
        runs = {side: run_tests(root, work, python) for side, root in roots.items()}
        targets = [(change['qualified_name'], side, change['file'], first_lines(roots[side], change['file'], change[side]['span']['line']))
                   for change in report['changes'] for side in ('before', 'after') if side in change]
    relevant = {}
    for name, side, file, lines in targets:
        for key, rec in runs[side]['tests'].items():
            if executes(rec, file, lines):
                relevant.setdefault(key, set()).add(name)
    before, after = runs['before']['tests'], runs['after']['tests']
    failing = {k for k, rec in after.items() if rec['outcome'] in ('fail', 'error') and before.get(k, {}).get('outcome') == 'pass'}
    suggested, removed = {}, set()
    for change in report['changes']:
        sel = change.get('test_selection', {})
        for t in sel.get('tests', []):
            suggested.setdefault(test_key(t['file'], t['name']), t)
        removed |= {test_key(t['file'], t['name']) for t in sel.get('removed_tests', [])}
    # Only tests that still exist can be suggested to run, so relevance is judged on those.
    relevant_now = {k for k in relevant if k in after}
    rel, sug = relevant_now, set(suggested)
    plan = report.get('test_plan') or build_test_plan(report['changes'])
    first = {test_key(t['file'], t['name']) for t in plan['tests']}
    ratio = lambda a, b: round(len(a) / len(b), 3) if b else None
    complete = all(r['status'] == 'ok' for r in runs.values())
    return {
        'repo': str(repo), 'base': base, 'head': head, 'complete': complete,
        'runs': {side: {'status': r['status'], 'mode': r.get('mode'), 'tests': len(r['tests']), 'problems': r['problems']}
                 for side, r in runs.items()},
        'changed_functions': sorted({t[0] for t in targets}),
        'relevant': {'truth': len(rel), 'suggested': len(sug), 'found': len(rel & sug),
                     'recall': ratio(rel & sug, rel), 'precision': ratio(rel & sug, sug),
                     'missed': sorted(rel - sug), 'suggested_but_not_executed': sorted(sug - rel)},
        'shortlist': {'limit': plan['limit'], 'suggested': len(first), 'found': len(rel & first),
                      'recall': ratio(rel & first, rel), 'precision': ratio(rel & first, first),
                      'omitted_candidates': plan['omitted'], 'failing_found': len(failing & first)},
        'failing': {'truth': len(failing), 'found': len(failing & sug), 'recall': ratio(failing & sug, failing),
                    'missed': sorted(failing - sug), 'failing_but_not_relevant': sorted(failing - rel)},
        'removed_tests_listed': sorted(removed),
    }


def summary(result):
    r, f = result['relevant'], result['failing']
    lines = [f"{result['base']}..{result['head']}: changed {', '.join(result['changed_functions'][:6])}"
             + (' …' if len(result['changed_functions']) > 6 else '')]
    if not result['complete']:
        for side, run in result['runs'].items():
            if run['status'] != 'ok':
                lines.append(f"  INCOMPLETE {side} run ({run['tests']} tests recorded): " + '; '.join(run['problems'][:3]))
    lines.append(f"  relevant (all connected candidates): {r['truth']} | suggested {r['suggested']} | found {r['found']}"
                 f" | recall {r['recall']} | precision {r['precision']}")
    if 'shortlist' in result:
        first = result['shortlist']
        lines.append(f"  run-first shortlist (limit {first['limit']}): {first['suggested']} | found {first['found']}"
                     f" | recall {first['recall']} | precision {first['precision']} | omitted {first['omitted_candidates']}")
    lines.append(f"  failing (pass before, fail after):    {f['truth']} | found {f['found']} | recall {f['recall']}")
    for label, items in (('missed relevant', r['missed']), ('suggested, did not run it', r['suggested_but_not_executed']),
                         ('missed failing', f['missed'])):
        if items:
            lines.append(f'    {label}: ' + ', '.join(i.split('.', 1)[-1] for i in items[:6]) + (' …' if len(items) > 6 else ''))
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('repo'); parser.add_argument('base'); parser.add_argument('head')
    parser.add_argument('--python', default=sys.executable, help='interpreter to run the tests with (needs pytest for pytest projects)')
    parser.add_argument('--json')
    args = parser.parse_args(argv)
    result = score(Path(args.repo), args.base, args.head, args.python)
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2))
    print(summary(result))
    return 0 if result['complete'] else 2


if __name__ == '__main__':
    sys.exit(main())
