"""The recorder is tested on a tiny fixture project written here, never on outside code."""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests import ROOT

SCRIPT = ROOT / 'repotrace' / 'evaluation' / 'runtime_recorder.py'

CORE = '''
import functools
import socket
import threading
import asyncio


def logged(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper


def leaf():
    return 1


@logged
def decorated():
    return leaf()


class Service:
    def run(self):
        return self.helper()

    def helper(self):
        return leaf()


def with_callback(items):
    return sorted(items, key=lambda x: leaf())


def comprehension():
    return [leaf() for _ in range(2)]


def threaded():
    worker = threading.Thread(target=leaf)
    worker.start()
    worker.join()


async def coroutine():
    return leaf()


def try_network():
    try:
        socket.create_connection(("127.0.0.1", 9))
    except OSError as exc:
        return "blocked" if "blocked" in str(exc) else "other"


def never_called():
    return leaf() + 100


def main():
    decorated()
    Service().run()
    with_callback([2, 1])
    comprehension()
    threaded()
    asyncio.run(coroutine())
    return try_network()
'''


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / 'proj'
        (self.root / 'pkg').mkdir(parents=True)
        (self.root / 'pkg' / '__init__.py').write_text('')
        (self.root / 'pkg' / 'core.py').write_text(textwrap.dedent(CORE))
        (self.root / 'run.py').write_text('from pkg.core import main\nprint(main())\n')
        self.scratch = Path(self._tmp.name) / 'scratch'
        self.output = Path(self._tmp.name) / 'trace.json'

    def record(self, *extra, target=('run.py',)):
        command = [sys.executable, '-B', str(SCRIPT), '--root', str(self.root), '--cwd', str(self.scratch),
                   '--output', str(self.output), *extra, '--', *[str(self.root / t) for t in target]]
        done = subprocess.run(command, capture_output=True, text=True, timeout=60)
        return done, json.loads(self.output.read_text())

    def pairs(self, trace):
        return {((e['caller'] or {}).get('name'), e['callee']['name'], e['through_library'])
                for e in trace['edges']}

    def test_wrappers_are_collapsed_by_default_and_kept_on_request(self):
        _, trace = self.record()
        pairs = self.pairs(trace)
        self.assertIn(('main', 'decorated', False), pairs)
        self.assertNotIn(('main', 'wrapper', False), pairs)
        self.assertNotIn(('wrapper', 'decorated', False), pairs)
        edge = next(e for e in trace['edges'] if e['callee']['name'] == 'decorated')
        self.assertTrue(edge['via_wrapper'])
        source = textwrap.dedent(CORE).splitlines()
        self.assertEqual(source[edge['caller']['line'] - 1].strip(), 'def main():')
        _, raw = self.record('--keep-wrappers')
        self.assertIn(('main', 'wrapper', False), self.pairs(raw))
        self.assertIn(('wrapper', 'decorated', False), self.pairs(raw))

    def test_line_recording_lists_executed_lines_only(self):
        _, trace = self.record('--lines')
        lines = set(trace['executed_lines']['pkg/core.py'])
        source = textwrap.dedent(CORE).splitlines()
        by_text = {text.strip(): number for number, text in enumerate(source, 1)}
        self.assertIn(by_text['return leaf()'], lines)
        self.assertNotIn(by_text['return leaf() + 100'], lines)
        _, plain = self.record()
        self.assertEqual(plain['executed_lines'], {})

    def test_edges_for_every_call_shape(self):
        done, trace = self.record()
        self.assertEqual(done.returncode, 0, done.stderr)
        pairs = self.pairs(trace)
        self.assertIn(('main', 'decorated', False), pairs)      # wrapper frame collapsed
        self.assertIn(('decorated', 'leaf', False), pairs)
        self.assertIn(('run', 'helper', False), pairs)
        self.assertIn(('helper', 'leaf', False), pairs)
        self.assertIn(('with_callback', '<lambda>', False), pairs)   # callback from C code
        self.assertIn(('<lambda>', 'leaf', False), pairs)
        self.assertIn(('comprehension', 'leaf', False), pairs)       # comprehension frame collapsed
        self.assertIn((None, 'leaf', True), pairs)                  # thread: only library frames above
        self.assertIn(('main', 'coroutine', True), pairs)           # asyncio sits between
        self.assertNotIn(('<listcomp>', 'leaf', False), pairs)

    def test_call_lines_and_counts(self):
        _, trace = self.record()
        edge = next(e for e in trace['edges'] if e['callee']['name'] == 'helper')
        source = textwrap.dedent(CORE).splitlines()
        self.assertIn('self.helper()', source[edge['call_line'] - 1])
        leaf_from_comp = next(e for e in trace['edges']
                              if e['caller'] and e['caller']['name'] == 'comprehension' and e['callee']['name'] == 'leaf')
        self.assertEqual(leaf_from_comp['count'], 2)

    def test_network_is_blocked_by_default_and_files_stay_in_scratch(self):
        done, trace = self.record()
        self.assertEqual(done.stdout.splitlines()[0], 'blocked')
        self.assertTrue(trace['network_blocked'])
        self.assertEqual(list(self.root.rglob('*.json')), [])

    def test_failing_target_still_saves_a_partial_trace(self):
        (self.root / 'boom.py').write_text('from pkg.core import leaf\nleaf()\nraise RuntimeError("boom")\n')
        done, trace = self.record(target=('boom.py',))
        self.assertEqual(trace['exit_code'], 1)
        self.assertIn('RuntimeError: boom', trace['error'])
        self.assertTrue(any(e['callee']['name'] == 'leaf' for e in trace['edges']))

    def test_code_outside_the_root_is_not_recorded(self):
        _, trace = self.record()
        files = {e['callee']['file'] for e in trace['edges']}
        self.assertEqual(files, {'pkg/core.py'})

    def test_module_mode_passes_extra_arguments(self):
        (self.root / 'argtool.py').write_text(
            'import sys\nfrom pkg.core import leaf\nleaf()\nprint("ARGS", sys.argv[1:])\n')
        command = [sys.executable, '-B', str(SCRIPT), '--root', str(self.root), '--path', '.',
                   '--cwd', str(self.scratch), '--output', str(self.output), '-m', 'argtool',
                   'one', '-p', 'two']
        done = subprocess.run(command, capture_output=True, text=True, timeout=60)
        self.assertIn("ARGS ['one', '-p', 'two']", done.stdout)

    def test_module_mode_and_exit_code(self):
        (self.root / 'tool.py').write_text('import sys\nfrom pkg.core import leaf\nleaf()\nsys.exit(3)\n')
        command = [sys.executable, '-B', str(SCRIPT), '--root', str(self.root), '--path', '.',
                   '--cwd', str(self.scratch), '--output', str(self.output), '-m', 'tool']
        done = subprocess.run(command, capture_output=True, text=True, timeout=60)
        trace = json.loads(self.output.read_text())
        self.assertEqual((done.returncode, trace['exit_code']), (3, 3))


if __name__ == '__main__':
    unittest.main()
