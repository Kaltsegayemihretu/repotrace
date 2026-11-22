"""The evaluation runner must keep normal test setup and must never report a broken run as an empty, clean one.
These tests EXECUTE tiny throwaway test suites written below."""
import os
import tempfile
import unittest
from pathlib import Path

from repotrace.evaluation.test_score import run_tests, test_mode
from repotrace.evaluation.test_score import score
from tests.test_change_impact import RepoCase

PYTEST = os.environ.get('REPOTRACE_PYTEST_PYTHON')     # an interpreter with pytest installed, if available


class ShortlistScoreTests(RepoCase):
    def test_scores_capped_list_separately_from_all_connected_candidates(self):
        base = self.commit({'app.py': 'def f():\n    return 1\n',
                            'tests/test_app.py': 'import unittest\nfrom app import f\n\nclass T(unittest.TestCase):\n' +
                            ''.join(f'    def test_{i}(self):\n        self.assertEqual(f(), 1)\n' for i in range(3))})
        head = self.commit({'app.py': 'def f():\n    return 1 + 0\n'})
        report = self.run_report(base, head, max_tests=1)
        result = score(self.repo, base, head, report=report)
        self.assertTrue(result['complete'])
        self.assertEqual(result['relevant']['suggested'], 3)
        self.assertEqual(result['relevant']['found'], 3)
        self.assertEqual(result['shortlist']['suggested'], 1)
        self.assertEqual(result['shortlist']['found'], 1)
        self.assertEqual(result['shortlist']['omitted_candidates'], 2)
        self.assertEqual(result['shortlist']['recall'], 0.333)


def project(files):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name) / 'proj'
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return tmp, root


class TestModeTests(unittest.TestCase):
    """Found on markdown: a tox.ini that runs `unittest discover` was taken as proof of pytest."""
    def mode(self, files):
        tmp, root = project(files)
        self.addCleanup(tmp.cleanup)
        return test_mode(root)

    def test_a_tox_ini_that_never_names_pytest_does_not_make_it_a_pytest_project(self):
        self.assertEqual(self.mode({'tox.ini': '[testenv]\ncommands = python -m unittest discover tests\n', 'tests/test_a.py': 'import unittest\n'}), 'unittest')

    def test_pytest_named_anywhere_it_is_configured_makes_it_pytest(self):
        self.assertEqual(self.mode({'tox.ini': '[testenv]\ndeps = pytest\ncommands = pytest\n'}), 'pytest')
        self.assertEqual(self.mode({'conftest.py': ''}), 'pytest')
        self.assertEqual(self.mode({'tests/test_a.py': 'import pytest\n'}), 'pytest')


class UnittestRunnerTests(unittest.TestCase):
    def run_suite(self, files, python=None):
        tmp, root = project(files)
        self.addCleanup(tmp.cleanup)
        import sys
        return run_tests(root, Path(tmp.name), python or sys.executable)

    def test_nested_test_packages_with_relative_imports_are_complete_not_incomplete(self):
        """Found on markdown: discovering from a sub-folder cannot resolve `from ..x import y`, which is fine when the package root loads it."""
        result = self.run_suite({'pkg/__init__.py': '', 'pkg/core.py': 'def value():\n    return 1\n',
                                 'tests/__init__.py': '', 'tests/helpers.py': 'VALUE = 1\n', 'tests/sub/__init__.py': '',
                                 'tests/sub/test_x.py': 'import unittest\nfrom ..helpers import VALUE\nfrom pkg.core import value\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(value(), VALUE)\n'})
        self.assertEqual(result['status'], 'ok', result['problems'])
        self.assertEqual(result['problems'], [])
        self.assertEqual(len(result['tests']), 1)

    def test_a_library_module_named_like_a_test_is_not_reported_as_a_broken_test(self):
        """markdown ships markdown/test_tools.py (a helper for extension authors, not a test); it must not make the run incomplete."""
        result = self.run_suite({'pkg/__init__.py': '', 'pkg/core.py': 'def value():\n    return 1\n',
                                 'pkg/test_tools.py': 'from .core import value\n\n\ndef helper():\n    return value()\n',
                                 'tests/test_a.py': 'import unittest\nfrom pkg.test_tools import helper\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(helper(), 1)\n'})
        self.assertEqual(result['status'], 'ok', result['problems'])
        self.assertEqual(len(result['tests']), 1)

    def test_a_module_that_really_cannot_be_imported_is_still_a_problem(self):
        result = self.run_suite({'tests/test_bad.py': 'import unittest\nimport module_that_does_not_exist\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n',
                                 'tests/test_ok.py': 'import unittest\n\n\nclass U(unittest.TestCase):\n    def test_b(self):\n        pass\n'})
        self.assertEqual(result['status'], 'incomplete')
        self.assertTrue(any('test_bad' in p for p in result['problems']), result['problems'])

    def test_class_and_module_setup_run_and_code_is_credited_to_the_test(self):
        out = self.run_suite({
            'app.py': 'def work():\n    return 1\n',
            'tests/test_a.py': ('import unittest\nimport app\nSTATE = []\n\n\ndef setUpModule():\n    STATE.append("module")\n\n\n'
                                'class T(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls):\n        STATE.append("class")\n\n'
                                '    def test_setup_ran(self):\n        self.assertEqual(STATE, ["module", "class"])\n        app.work()\n\n'
                                '    def test_fails(self):\n        self.assertEqual(1, 2)\n')})
        self.assertEqual(out['status'], 'ok', out['problems'])
        tests = out['tests']
        self.assertEqual(tests['tests.test_a.T.test_setup_ran']['outcome'], 'pass')
        self.assertEqual(tests['tests.test_a.T.test_fails']['outcome'], 'fail')
        self.assertIn(['app.py', 1], tests['tests.test_a.T.test_setup_ran']['executed'])
        self.assertNotIn(['app.py', 1], tests['tests.test_a.T.test_fails']['executed'])

    def test_an_import_error_makes_the_run_incomplete(self):
        out = self.run_suite({'tests/test_a.py': 'import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n',
                              'tests/test_b.py': 'import does_not_exist\n'})
        self.assertEqual(out['status'], 'incomplete')
        self.assertTrue(any('test_b' in p for p in out['problems']), out['problems'])
        self.assertIn('tests.test_a.T.test_ok', out['tests'], 'what did run is still kept')

    def test_no_tests_at_all_is_incomplete_not_empty(self):
        out = self.run_suite({'app.py': 'x = 1\n'})
        self.assertEqual(out['status'], 'incomplete')

    def test_a_crashing_runner_is_reported(self):
        out = self.run_suite({'tests/test_a.py': 'pass\n'}, python='/usr/bin/false')
        self.assertEqual(out['status'], 'incomplete')
        self.assertTrue(any('crashed' in p for p in out['problems']))
        out = self.run_suite({'tests/test_a.py': 'pass\n'}, python='/no/such/python')
        self.assertEqual(out['status'], 'incomplete')
        self.assertTrue(any('could not start' in p for p in out['problems']))


@unittest.skipUnless(PYTEST, 'set REPOTRACE_PYTEST_PYTHON to an interpreter that has pytest')
class PytestRunnerTests(unittest.TestCase):
    def test_fixtures_run_and_collection_errors_are_reported(self):
        tmp, root = project({
            'pytest.ini': '[pytest]\ntestpaths = tests\n', 'app.py': 'def work():\n    return 1\n',
            'tests/conftest.py': 'import pytest\nimport app\n\n\n@pytest.fixture\ndef ready():\n    app.work()\n    return True\n',
            'tests/test_a.py': 'import pytest\n\n\n@pytest.mark.parametrize("n", [1, 2])\ndef test_uses(ready, n):\n    assert ready\n',
            'tests/test_b.py': 'import does_not_exist\n'})
        self.addCleanup(tmp.cleanup)
        out = run_tests(root, Path(tmp.name), PYTEST)
        self.assertEqual(out['mode'], 'pytest')
        self.assertEqual(out['status'], 'incomplete')
        self.assertTrue(any('collection error' in p for p in out['problems']), out['problems'])
        self.assertEqual(out['tests']['tests.test_a.test_uses']['outcome'], 'pass', 'two parameter sets, one entry')
        self.assertIn(['app.py', 1], out['tests']['tests.test_a.test_uses']['executed'], 'fixture code counts for the test')


if __name__ == '__main__':
    unittest.main()
