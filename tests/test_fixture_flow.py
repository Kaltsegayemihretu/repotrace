"""pytest fixtures give test parameters a type only when pytest's own lookup picks one fixture with one known value."""
import tempfile
import unittest
from pathlib import Path

from repotrace.mapping.repository import analyze_repository, pytest_source_roots

SERVICE = 'class Service:\n    def run(self):\n        return 1\n\n\nclass Other:\n    def run(self):\n        return 2\n'


def targets(files, test_file='tests/test_a.py', test='test_it'):
    """The functions the `x.run()` call inside `test` links to (as qualified names)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, text in {'app/__init__.py': '', 'app/service.py': SERVICE, **files}.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        report = analyze_repository(root)
    names = {s['id']: s['qualified_name'] for f in report['files'] for s in f['symbols']}
    calls = [c for f in report['files'] if f['file'] == test_file for c in f['calls']
             if c['enclosing_symbol_id'].split('::')[1].split('@')[0].endswith(test) and c['expression'].endswith('.run')]
    assert calls, 'no run() call found in ' + test
    found = set()
    for c in calls:
        if c.get('target_id'):
            found.add(names[c['target_id']])
        found |= {names[t['target_id']] for t in c.get('candidate_targets', [])}
    return found, report


CONFTEST = 'import pytest\nfrom app.service import Service, Other\n\n\n@pytest.fixture\ndef svc():\n    return Service()\n'


class FixtureFlowTests(unittest.TestCase):
    def test_a_fixture_return_value_reaches_the_test_parameter(self):
        found, report = targets({'tests/conftest.py': CONFTEST, 'tests/test_a.py': 'def test_it(svc):\n    svc.run()\n'})
        self.assertEqual(found, {'Service.run'})
        self.assertEqual(report['project_index']['fixture_flow']['parameters_with_hints'], 1)

    def test_a_yield_fixture_hands_over_the_yielded_value(self):
        conftest = 'import pytest\nfrom app.service import Service\n\n\n@pytest.fixture\ndef svc():\n    s = Service()\n    yield s\n    print("done")\n'
        found, _ = targets({'tests/conftest.py': conftest, 'tests/test_a.py': 'def test_it(svc):\n    svc.run()\n'})
        self.assertEqual(found, {'Service.run'})

    def test_the_nearest_definition_wins(self):
        module = 'import pytest\nfrom app.service import Other\n\n\n@pytest.fixture\ndef svc():\n    return Other()\n\n\ndef test_it(svc):\n    svc.run()\n'
        self.assertEqual(targets({'tests/conftest.py': CONFTEST, 'tests/test_a.py': module})[0], {'Other.run'})
        nested = 'import pytest\nfrom app.service import Other\n\n\n@pytest.fixture\ndef svc():\n    return Other()\n'
        found, _ = targets({'tests/conftest.py': CONFTEST, 'tests/sub/conftest.py': nested,
                            'tests/sub/test_b.py': 'def test_it(svc):\n    svc.run()\n'}, test_file='tests/sub/test_b.py')
        self.assertEqual(found, {'Other.run'}, 'a conftest in the closer folder overrides the outer one')
        self.assertEqual(targets({'tests/conftest.py': CONFTEST, 'tests/sub/conftest.py': nested,
                                  'tests/test_a.py': 'def test_it(svc):\n    svc.run()\n'})[0], {'Service.run'},
                         'but not for tests outside that folder')

    def test_an_override_that_asks_for_its_own_name_gets_the_outer_fixture(self):
        module = 'import pytest\n\n\n@pytest.fixture\ndef svc(svc):\n    return svc\n\n\ndef test_it(svc):\n    svc.run()\n'
        self.assertEqual(targets({'tests/conftest.py': CONFTEST, 'tests/test_a.py': module})[0], {'Service.run'})

    def test_ambiguous_or_unknown_fixtures_stay_unlinked(self):
        cases = {
            'parametrized fixture': 'import pytest\nfrom app.service import Service, Other\n\n\n@pytest.fixture(params=[1, 2])\n'
                                    'def svc(request):\n    return Service()\n\n\ndef test_it(svc):\n    svc.run()\n',
            'two possible types': 'import pytest\nfrom app.service import Service, Other\n\n\n@pytest.fixture\ndef svc():\n'
                                  '    if 1:\n        return Service()\n    return Other()\n\n\ndef test_it(svc):\n    svc.run()\n',
            'filled by parametrize': 'import pytest\nfrom app.service import Service\n\n\n@pytest.fixture\ndef svc():\n    return Service()\n\n\n'
                                     '@pytest.mark.parametrize("svc", [1])\ndef test_it(svc):\n    svc.run()\n',
            'not a project fixture': 'def test_it(svc):\n    svc.run()\n',
            'yields twice': 'import pytest\nfrom app.service import Service\n\n\n@pytest.fixture\ndef svc():\n'
                            '    yield Service()\n    yield Service()\n\n\ndef test_it(svc):\n    svc.run()\n',
        }
        for label, module in cases.items():
            with self.subTest(label):
                self.assertEqual(targets({'tests/test_a.py': module})[0], set())


class SourceRootTests(unittest.TestCase):
    def roots(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, text in files.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(text)
            return pytest_source_roots(root)

    def test_declared_pythonpath_and_non_package_testpaths_are_used(self):
        self.assertEqual(self.roots({'pytest.ini': '[pytest]\npythonpath = src\ntestpaths = tests\n', 'src/x.py': '', 'tests/t.py': ''}),
                         (['src', 'tests'], 'pytest.ini'))
        self.assertEqual(self.roots({'pyproject.toml': '[tool.pytest.ini_options]\npythonpath = ["src"]\n', 'src/x.py': ''}),
                         (['src'], 'pyproject.toml'))

    def test_nothing_is_guessed_without_a_declared_pythonpath(self):
        self.assertEqual(self.roots({'pytest.ini': '[pytest]\ntestpaths = tests\n', 'src/x.py': ''}), (None, None))
        self.assertEqual(self.roots({'src/x.py': ''}), (None, None))

    def test_a_package_test_folder_is_not_added(self):
        self.assertEqual(self.roots({'setup.cfg': '[tool:pytest]\npythonpath = src\ntestpaths = tests\n', 'src/x.py': '',
                                     'tests/__init__.py': ''})[0], ['src'])


if __name__ == '__main__':
    unittest.main()
