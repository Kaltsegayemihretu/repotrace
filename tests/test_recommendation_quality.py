"""Controlled comparisons with source-written relevance and evidence expectations."""
import json
import os
from time import perf_counter

from tests.test_change_impact import RepoCase


class RecommendationQualityTests(RepoCase):
    def measure(self, base, head, expected, witnesses, **options):
        started = perf_counter()
        report = self.run_report(base, head, **options)
        elapsed = perf_counter() - started
        plan = report['test_plan']
        actual = {(test['file'], test['name']): test for test in plan['all_tests']}
        correct = 0
        for file, name, change, side, path in witnesses:
            test = actual.get((file, name), {})
            for reason in test.get('reasons', []):
                evidence = reason['evidence'].get(side, {})
                if reason['name'] == change and evidence.get('chain') == path:
                    self.assertTrue(all(call['evidence']['span']['line'] > 0 for call in evidence['calls']))
                    correct += 1
                    break
        if os.environ.get('REPOTRACE_SHOW_QUALITY'):
            print(json.dumps({'case': self._testMethodName, 'expected': len(expected), 'returned': len(actual),
                              'true_positive': len(expected & actual.keys()), 'correct_witnesses': correct,
                              'expected_witnesses': len(witnesses), 'selection_seconds': plan['selection_seconds'],
                              'comparison_seconds': round(elapsed, 3)}))
        self.assertEqual(expected, set(actual))
        self.assertEqual(correct, len(witnesses))
        self.assertTrue(all(test['execution_status'] == 'not executed' for test in actual.values()))
        return report, actual

    def test_helpers_hooks_and_two_revisions(self):
        file = 'tests/test_behavior.py'
        linked = ('from app import f, g\nimport unittest\n\ndef helper():\n    return f() + g()\n\n'
                  'def test_direct():\n    assert f()\n\ndef test_one():\n    assert helper()\n\n'
                  'def test_two():\n    assert helper()\n\n'
                  'class Behavior(unittest.TestCase):\n    def tearDown(self):\n        f()\n'
                  '    def test_hook_a(self):\n        pass\n    def test_hook_b(self):\n        pass\n\n'
                  'def test_unrelated():\n    assert True\n')
        base = self.commit({'app.py': 'def f():\n    return 1\n\ndef g():\n    return 1\n',
                            'tests/__init__.py': '', file: linked,
                            'tests/test_old.py': 'from app import f\n\ndef test_was_here():\n    assert f() > 0\n',
                            'tests/test_head.py': 'from app import g\n\ndef test_current():\n    assert g()\n'})
        head = self.commit({'app.py': 'def f():\n    return 2\n\ndef g():\n    return 2\n',
                            file: linked + '\ndef test_added():\n    assert True\n',
                            'tests/test_new.py': 'from app import f\n\ndef test_now_here():\n    assert f() > 0\n',
                            'tests/test_head.py': 'from app import g\n\ndef test_current():\n    assert g() > 0\n'},
                           remove=('tests/test_old.py',))
        expected = {(file, name) for name in ('test_direct', 'test_one', 'test_two', 'Behavior.test_hook_a',
                                              'Behavior.test_hook_b', 'test_added')}
        expected |= {('tests/test_new.py', 'test_now_here'), ('tests/test_head.py', 'test_current')}
        report, actual = self.measure(base, head, expected, [
            (file, 'test_direct', 'f', 'after', ['test_direct', 'f']),
            (file, 'test_one', 'f', 'before', ['test_one', 'helper', 'f']),
            (file, 'test_two', 'g', 'after', ['test_two', 'helper', 'g']),
            (file, 'Behavior.test_hook_a', 'f', 'after', ['Behavior.test_hook_a', 'Behavior.tearDown', 'f']),
            (file, 'test_added', 'test_added', 'after', ['test_added']),
            ('tests/test_new.py', 'test_now_here', 'f', 'before', ['test_was_here', 'f']),
            ('tests/test_head.py', 'test_current', 'test_current', 'after', ['test_current']),
        ], max_tests=2)
        self.assertEqual(len(report['test_plan']['tests']), 2)
        self.assertEqual(len(actual[(file, 'test_one')]['reasons']), 2)
        self.assertFalse(report['test_plan']['removed_tests'])

    def test_fixture_chains_scopes_shadowing_and_configured_async_collection(self):
        local = 'checks/check_behavior.py'
        root_fixture = ('import pytest\nfrom app import f\n\n@pytest.fixture(name="resource")\n'
                        'def source():\n    return f()\n')
        chain = ''.join(f'\n@pytest.fixture\ndef layer{i}({dep}):\n    return {dep}\n'
                        for i, dep in enumerate(('resource', 'layer0', 'layer1', 'layer2', 'layer3')))
        base = self.commit({
            'app.py': 'def f():\n    return 1\n',
            'pytest.ini': '[pytest]\npython_files = check_*.py\npython_classes = Group*\npython_functions = verify*\ntestpaths = checks\n',
            'conftest.py': root_fixture + chain,
            local: ('import pytest\n\nclass GroupCases:\n'
                    '    @pytest.mark.parametrize("case", [1, 2])\n'
                    '    async def verify_async(self, layer4, case):\n        assert layer4\n\n'
                    'class Helper:\n    def verify_non_test(self, layer4):\n        pass\n\n'
                    '@pytest.mark.parametrize("resource", [0])\ndef verify_parameter(resource):\n    pass\n'),
            'checks/sub/conftest.py': 'import pytest\n\n@pytest.fixture(autouse=True)\ndef clean(resource):\n    pass\n',
            'checks/sub/check_auto.py': 'def verify_auto():\n    pass\n',
            'checks/isolated/conftest.py': 'import pytest\n\n@pytest.fixture\ndef resource():\n    return 0\n',
            'checks/isolated/check_shadow.py': 'def verify_shadow(layer4):\n    pass\n',
            'other/check_excluded.py': 'def verify_excluded(resource):\n    pass\n'})
        head = self.commit({'app.py': 'def f():\n    return 2\n',
                            'checks/sub/conftest.py': 'import pytest\n\n@pytest.fixture(autouse=True)\ndef clean(resource):\n    assert resource\n',
                            'pytest.ini': '[pytest]\npython_files = check_*.py\npython_classes = Group*\npython_functions = verify*\ntestpaths = checks\naddopts = -q\n'})
        auto = ('checks/sub/check_auto.py', 'verify_auto')
        report, actual = self.measure(base, head, {(local, 'GroupCases.verify_async'), auto}, [
            (local, 'GroupCases.verify_async', 'f', 'after',
             ['GroupCases.verify_async', 'layer4', 'layer3', 'layer2', 'layer1', 'layer0', 'source', 'f']),
            (*auto, 'f', 'before', ['verify_auto', 'clean', 'source', 'f']),
            (*auto, 'clean', 'after', ['verify_auto', 'clean']),
        ])
        self.assertIn('::GroupCases::verify_async', actual[(local, 'GroupCases.verify_async')]['run'])
        self.assertTrue(actual[auto]['via']['autouse'])
        advice = report['test_plan']['broader_testing']
        self.assertTrue(any(item.get('scope') == 'checks/sub' for item in advice))
        self.assertTrue(any(item.get('file') == 'pytest.ini' for item in advice))

    def test_head_only_removed_ambiguous_and_missing_evidence(self):
        file = 'tests/test_cases.py'
        base = self.commit({
            'app.py': 'def old():\n    return 1\n',
            file: ('from app import old\n\ndef test_survives():\n    assert True\n\n'
                   'def test_removed():\n    assert old()\n\ndef test_ambiguous():\n    assert old() > 0\n')})
        head = self.commit({
            'app.py': 'def new():\n    return 1\n',
            file: ('from app import new\n\ndef test_survives():\n    assert new()\n\n'
                   'def test_candidate_a():\n    assert old() > 0\n\n'
                   'def test_candidate_b():\n    assert old() > 0\n'),
            'broken.py': 'def unparseable(\n'})
        report, actual = self.measure(base, head, {(file, n) for n in ('test_survives', 'test_candidate_a', 'test_candidate_b')},
                                      [(file, 'test_survives', 'new', 'after', ['test_survives', 'new'])])
        self.assertTrue(any(reason['name'] == 'new' and list(reason['evidence']) == ['after']
                            for reason in actual[(file, 'test_survives')]['reasons']))
        self.assertEqual([t['name'] for t in report['test_plan']['removed_tests']], ['test_removed'])
        self.assertEqual(len(report['test_plan']['unverified_tests'][0]['possible_head_matches']), 2)
        self.assertTrue(any(item.get('file') == 'broken.py' for item in report['test_plan']['broader_testing']))

    def test_unittest_inheritance_uses_project_imports_and_the_inherited_hook(self):
        file = 'tests/test_child.py'
        base = self.commit({
            'app.py': 'def f():\n    return 1\n',
            'tests/__init__.py': '',
            'tests/test_parent.py': 'import unittest\nfrom app import f\n\n'
                                    'class Parent(unittest.TestCase):\n    def tearDown(self):\n        f()\n',
            file: 'from tests.test_parent import Parent\n\n'
                  'class Child(Parent):\n    def test_child(self):\n        pass\n'})
        head = self.commit({'app.py': 'def f():\n    return 2\n'})
        name = 'Child.test_child'
        _, actual = self.measure(base, head, {(file, name)}, [
            (file, name, 'f', 'after', [name, 'Parent.tearDown', 'f'])])
        self.assertEqual(actual[(file, name)]['run'], 'python3 -m unittest tests.test_child.Child.test_child')

    def test_possible_method_link_is_visible_with_the_default_impact_settings(self):
        file = 'tests/test_method.py'
        source = 'class Box:\n    def save(self):\n        return 1\n'
        base = self.commit({'app.py': source,
                            file: 'from app import Box\n\ndef test_uses_box():\n    assert Box().save()\n'})
        head = self.commit({'app.py': source.replace('return 1', 'return 2')})
        _, actual = self.measure(base, head, {(file, 'test_uses_box')}, [
            (file, 'test_uses_box', 'Box.save', 'after', ['test_uses_box', 'Box.save'])])
        self.assertEqual(actual[(file, 'test_uses_box')]['tier'], 'possible')

    def test_call_cycle_traversal_limit_and_no_linked_tests(self):
        file = 'tests/test_cycle.py'
        source = ('def f():\n    return 1\n\ndef a():\n    return b() + f()\n\n'
                  'def b():\n    return a()\n')
        base = self.commit({'app.py': source, file: 'from app import b\n\ndef test_cycle():\n    assert b()\n'})
        head = self.commit({'app.py': source.replace('return 1', 'return 2')})
        self.measure(base, head, {(file, 'test_cycle')}, [
            (file, 'test_cycle', 'f', 'after', ['test_cycle', 'b', 'a', 'f'])])
        limited = self.run_report(base, head, max_depth=1)
        self.assertEqual(limited['status'], 'limited')
        self.assertEqual(limited['test_plan']['all_tests'], [])
        self.assertTrue(any('No linked tests found' in item['reason']
                            for item in limited['test_plan']['broader_testing']))
