import tempfile
import unittest
from pathlib import Path

from repotrace.graph.graph_explore import Explorer
from repotrace.graph.graph_index import GraphIndex
from repotrace.mapping.repository import analyze_repository
from repotrace.report.overview import SnapshotReader, build_overview

PROJECT = {
    'lib.py': 'def helper(x):\n    return x + 1\n\n\ndef other(x):\n    return helper(x)\n',
    'app.py': ('from lib import helper, other\n\n\ndef run(x):\n    seen = []\n    seen.append(other(x))\n    return helper(x)\n\n\n'
               'def main():\n    run(1)\n    run(2)\n    helper(0)\n'),
    'tests/test_app.py': 'from app import run\n\n\ndef test_run():\n    assert run(1)\n',
}


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name, text in PROJECT.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        index = GraphIndex(analyze_repository(self.root), include_possible=True)
        self.overview = build_overview(index, Explorer(index), 'proj')

    def test_counts_separate_tests_from_the_project_own_code(self):
        o = self.overview
        self.assertEqual((o['files'], o['functions'], o['tests']), (3, 5, 1))

    def test_busiest_functions_are_the_most_used_and_tests_do_not_count_as_users(self):
        busiest = {n['name']: n['used_by'] for n in self.overview['busiest']}
        self.assertEqual(list(busiest)[0], 'helper')             # used by other() and run()
        self.assertEqual(busiest, {'helper': 3, 'other': 1, 'run': 1})
        self.assertNotIn('test_run', busiest)                    # the test calls run(), but that is not a user

    def test_starters_are_unused_functions_that_call_several_others(self):
        names = [n['name'] for n in self.overview['starters']]
        self.assertEqual(names, ['main'])                        # main uses run and helper; nothing uses main

    def test_calls_are_split_into_sure_outside_and_not_traced_and_add_up(self):
        c = self.overview['calls']
        self.assertGreaterEqual(c['sure'], 3)
        self.assertGreaterEqual(c['outside'], 1)                 # seen.append is Python's own list method
        self.assertEqual(c['sure'] + c['maybe'] + c['outside'] + c['untraced'], c['total'])
        self.assertIsNotNone(c['traced_percent'])

    def test_snapshot_reader_returns_the_function_lines_from_the_saved_source(self):
        reader = SnapshotReader({'lib.py': PROJECT['lib.py']})
        self.assertEqual(reader.lines('lib.py', 1, 2), {'first': 1, 'lines': ['def helper(x):', '    return x + 1']})
        self.assertIsNone(reader.lines('missing.py', 1, 2))


if __name__ == '__main__':
    unittest.main()
