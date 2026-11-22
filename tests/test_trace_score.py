"""Scoring is tested on a tiny fixture: real analyzer, real recorder, nothing outside this file."""
import copy
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from repotrace.mapping.repository import analyze_repository
from repotrace.evaluation.trace_score import difficulty_by_pattern, score
from tests import ROOT

RECORDER = ROOT / 'repotrace' / 'evaluation' / 'runtime_recorder.py'
APP = '''
def leaf():
    return 1


def good():
    return leaf()


def unused():
    return leaf()


class Flag:
    def __bool__(self):
        return True


class Box:
    def __init__(self):
        self.v = 1

    def get(self):
        return self.v

    def __len__(self):
        return 1


def use(thing):
    b = Box()
    n = len(b)
    flag = Flag()
    if flag:
        n = 2
    return b.get(), thing.get()


def main():
    good()
    use(Box())
'''


class ScoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name) / 'proj'
        root.mkdir()
        (root / 'app.py').write_text(textwrap.dedent(APP))
        (root / 'run.py').write_text('from app import main\nmain()\n')
        trace = Path(cls._tmp.name) / 'trace.json'
        subprocess.run([sys.executable, '-B', str(RECORDER), '--root', str(root), '--path', '.',
                        '--cwd', str(Path(cls._tmp.name) / 'scratch'), '--lines', '--output', str(trace),
                        str(root / 'run.py')], check=True, capture_output=True, timeout=60)
        cls.root, cls.trace = root, json.loads(trace.read_text())
        cls.report = analyze_repository(root)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_recall_tiers_and_missed_reason(self):
        result = score(self.report, [self.trace], self.root)
        tiers = result['recall']['scopes']['all_project_code']['by_tier']
        self.assertGreaterEqual(tiers.get('confident', 0), 3)     # main->good, main->use, good->leaf
        self.assertGreaterEqual(tiers.get('uncertain', 0), 3)     # Box(), b.get(), and len(b) -> Box.__len__
        self.assertGreaterEqual(tiers.get('missed', 0), 1)        # `if flag:` runs Flag.__bool__ through truthiness
        reasons = ' '.join(reason for reason, _ in result['recall']['missed_reasons'])
        self.assertIn('no call recorded', reasons)

    def test_name_only_tier_for_untyped_receiver(self):
        report = copy.deepcopy(self.report)
        for file in report['files']:
            for call in file['calls']:
                if call['expression'] in ('b.get', 'thing.get'):
                    call.update(resolution='UNRESOLVED', target_id=None, candidate_targets=[], category='unresolved')
        tiers = score(report, [self.trace], self.root)['recall']['scopes']['all_project_code']['by_tier']
        self.assertGreaterEqual(tiers.get('name_only', 0), 1)

    def test_precision_counts_right_and_unrun_arrows(self):
        result = score(self.report, [self.trace], self.root)['precision']['LIKELY']
        self.assertEqual(result['percent_right'], 100.0)
        self.assertEqual(result['counts']['not_run (cannot judge)'], 1)    # unused() never ran

    def test_a_wrong_confident_arrow_is_caught(self):
        report = copy.deepcopy(self.report)
        unused = next(s['id'] for f in report['files'] for s in f['symbols'] if s['name'] == 'unused')
        for file in report['files']:
            for call in file['calls']:
                if call['enclosing_symbol_id'].endswith('::good@6:0'):
                    call['target_id'] = unused
        result = score(report, [self.trace], self.root)['precision']['LIKELY']
        self.assertEqual(result['counts']['not seen (line ran, target not called)'], 1)
        self.assertLess(result['percent_right'], 100.0)

    def test_pattern_difficulty_parsing(self):
        text = ('| P09 | a.py::f | x | direct |\n| P09 | b.py::g | y | depends on runtime. needs type inference |\n'
                '| P24 | c.py::h | z | dynamic |\n')
        self.assertEqual(difficulty_by_pattern(text), {'P09': 'needs type inference', 'P24': 'dynamic'})


if __name__ == '__main__':
    unittest.main()
