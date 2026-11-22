"""Test shortlists: only real tests are recommended, setup hooks and fixtures pass their evidence on to the tests they
run for, and objects built in setUp are followed only when nothing else could supply or replace them."""
import unittest

from repotrace.changes.test_selection import build_test_plan, test_role
from tests.test_change_impact import RepoCase

SERVICE = ('class Store:\n    def save(self, x):\n        return x\n\n\n'
           'class Service:\n    def __init__(self):\n        self.store = Store()\n\n'
           '    def add(self, x):\n        return self.store.save(x)\n')


class TestPlanTests(unittest.TestCase):
    @staticmethod
    def make_entry(name, **kw):
        return {'file': 'tests/test_app.py', 'name': name, 'tier': 'supported',
                'seen_in': ['before', 'after'], 'via': None, 'distance': 1, **kw}

    def test_global_budget_deduplicates_and_preserves_all_original_evidence(self):
        shared = self.make_entry('test_shared')
        changes = [{'test_selection': {'tests': [shared] + [self.make_entry(f'test_{i:03}') for i in range(700)]}},
                   {'test_selection': {'tests': [shared, self.make_entry('test_second')]}}]
        plan = build_test_plan(changes)
        self.assertEqual((plan['selected'], plan['total_candidates'], plan['omitted']), (20, 702, 682))
        self.assertTrue(plan['truncated'])
        self.assertEqual(len(changes[0]['test_selection']['tests']), 701)
        full = build_test_plan(changes, 1000)
        self.assertEqual(next(t for t in full['tests'] if t['name'] == 'test_shared')['change_indexes'], [0, 1])
        self.assertNotIn('change_indexes', shared)

    def test_evidence_then_lost_path_then_explicit_use_then_distance(self):
        tests = [self.make_entry('possible', tier='possible', seen_in=['before']),
                 self.make_entry('setup', via={'kind': 'setup'}),
                 self.make_entry('far', distance=4),
                 self.make_entry('lost', distance=9, seen_in=['before']),
                 self.make_entry('direct'),
                 self.make_entry('autouse', via={'kind': 'fixture', 'autouse': True})]
        changes = [{'test_selection': {'tests': tests}}]
        plan = build_test_plan(changes, 4)
        self.assertEqual([t['name'] for t in plan['tests']], ['lost', 'direct', 'far', 'autouse'])
        tests.reverse()
        self.assertEqual(build_test_plan(changes, 4), plan)

    def test_empty_and_removed_tests_never_get_a_fallback_recommendation(self):
        plan = build_test_plan([{'test_selection': {'tests': [], 'removed_tests': [self.make_entry('old')]}}])
        self.assertEqual(plan['tests'], [])
        self.assertEqual(plan['omitted'], 0)
        self.assertFalse(plan['truncated'])
        self.assertIn('does not mean safe', plan['warning'])
        for limit in (0, -1, True, 2.5):
            with self.assertRaises(ValueError):
                build_test_plan([], limit)


def shortlist(report, name):
    change = next(c for c in report['changes'] if c['qualified_name'] == name)
    return change['test_selection']


def tests_of(report, name):
    return {t['name']: t for t in shortlist(report, name)['tests']}


class UnittestSetupTests(RepoCase):
    def change_store(self, test_file):
        base = self.commit({'app/__init__.py': '', 'app/service.py': SERVICE, 'tests/__init__.py': '', 'tests/test_service.py': test_file})
        head = self.commit({'app/service.py': SERVICE.replace('return x\n', 'return [x]\n', 1)})
        return self.run_report(base, head, include_possible=True)

    def test_an_object_built_in_setUp_links_the_test_to_the_change(self):
        report = self.change_store(
            'import unittest\nfrom app.service import Service\n\n\nclass TestService(unittest.TestCase):\n'
            '    def setUp(self):\n        self.service = Service()\n\n'
            '    def test_add(self):\n        self.assertEqual(self.service.add(1), [1])\n')
        tests = tests_of(report, 'Store.save')
        self.assertIn('TestService.test_add', tests)
        t = tests['TestService.test_add']
        self.assertEqual(t['chain'], ['TestService.test_add', 'Service.add', 'Store.save'])
        self.assertEqual(t['tier'], 'possible', 'a call through self is never graded sure: a subclass could override it')
        self.assertEqual(t['run'], 'python3 -m unittest tests.test_service.TestService.test_add')

    def test_setUp_is_not_recommended_but_its_tests_are_through_it(self):
        report = self.change_store(
            'import unittest\nfrom app.service import Service\n\n\nclass TestService(unittest.TestCase):\n'
            '    def setUp(self):\n        Service().add(0)\n\n'
            '    def test_one(self):\n        pass\n\n    def test_two(self):\n        pass\n\n\n'
            'class TestOther(unittest.TestCase):\n    def test_unrelated(self):\n        pass\n')
        sel = shortlist(report, 'Store.save')
        self.assertEqual([(o['name'], o['role']) for o in sel['other_test_code']], [('TestService.setUp', 'setup')])
        names = {t['name']: t for t in sel['tests']}
        self.assertEqual(set(names), {'TestService.test_one', 'TestService.test_two'}, 'only tests of the same class')
        self.assertEqual(names['TestService.test_one']['via'], {'kind': 'setup', 'name': 'TestService.setUp'})
        self.assertEqual(names['TestService.test_one']['chain'][:2], ['TestService.test_one', 'TestService.setUp'])

    def test_a_field_reassigned_to_something_unknown_stays_unlinked(self):
        report = self.change_store(
            'import unittest\nfrom app.service import Service\n\n\ndef make():\n    return object()\n\n\n'
            'class TestService(unittest.TestCase):\n    def setUp(self):\n        self.service = Service()\n\n'
            '    def test_swapped(self):\n        self.service = make()\n        self.service.add(1)\n')
        self.assertNotIn('TestService.test_swapped', tests_of(report, 'Store.save'))

    def test_a_name_that_TestCase_itself_defines_is_never_resolved(self):
        # `id` is a TestCase method and `_outcome` an attribute TestCase.__init__ sets: either could be what runs.
        for attr in ('id', '_outcome'):
            with self.subTest(attr=attr):
                report = self.change_store(
                    'import unittest\nfrom app.service import Service\n\n\nclass TestService(unittest.TestCase):\n'
                    f'    def setUp(self):\n        self.{attr} = Service()\n\n'
                    f'    def test_add(self):\n        self.{attr}.add(1)\n')
                self.assertNotIn('TestService.test_add', tests_of(report, 'Store.save'))

    def test_a_subclass_override_keeps_the_link_uncertain(self):
        report = self.change_store(
            'import unittest\nfrom app.service import Service\n\n\nclass Base(unittest.TestCase):\n'
            '    def make(self):\n        return Service()\n\n    def test_add(self):\n        self.make().add(1)\n\n\n'
            'class Fake:\n    def add(self, x):\n        return x\n\n\n'
            'class TestFake(Base):\n    def make(self):\n        return Fake()\n')
        tests = tests_of(report, 'Store.save')
        if 'Base.test_add' in tests:                      # if linked at all, only as a maybe
            self.assertEqual(tests['Base.test_add']['tier'], 'possible')

    def test_a_helper_is_reported_separately_and_its_callers_are_recommended(self):
        report = self.change_store(
            'import unittest\nfrom app.service import Service\n\n\ndef add_one():\n    return Service().add(1)\n\n\n'
            'class TestService(unittest.TestCase):\n    def test_add(self):\n        add_one()\n')
        sel = shortlist(report, 'Store.save')
        self.assertIn(('add_one', 'helper'), [(o['name'], o['role']) for o in sel['other_test_code']])
        self.assertEqual(tests_of(report, 'Store.save')['TestService.test_add']['chain'], ['TestService.test_add', 'add_one', 'Service.add', 'Store.save'])


class PytestFixtureTests(RepoCase):
    def test_fixtures_pass_evidence_to_the_tests_that_use_them(self):
        base = self.commit({
            'app/__init__.py': '', 'app/service.py': SERVICE, 'tests/__init__.py': '',
            'tests/conftest.py': ('import pytest\nfrom app.service import Service\n\n\n'
                                  '@pytest.fixture\ndef service():\n    s = Service()\n    s.add(0)\n    return s\n\n\n'
                                  '@pytest.fixture\ndef loaded(service):\n    return service\n\n\n'
                                  '@pytest.fixture(autouse=True)\ndef reset():\n    Service().add(-1)\n'),
            'tests/test_a.py': ('def test_direct(service):\n    pass\n\n\ndef test_nested(loaded):\n    pass\n\n\n'
                                'def test_plain():\n    pass\n'),
            'other/test_far.py': 'def test_elsewhere():\n    pass\n'})
        head = self.commit({'app/service.py': SERVICE.replace('return x\n', 'return [x]\n', 1)})
        sel = shortlist(self.run_report(base, head, include_possible=True), 'Store.save')
        roles = {o['name']: o['role'] for o in sel['other_test_code']}
        self.assertEqual(roles, {'service': 'fixture', 'reset': 'fixture'})
        tests = {t['name']: t for t in sel['tests']}
        self.assertEqual(set(tests), {'test_direct', 'test_nested', 'test_plain'}, 'conftest scope is its folder; autouse covers all of it')
        self.assertEqual(tests['test_nested']['chain'][:3], ['test_nested', 'loaded', 'service'])
        self.assertEqual(tests['test_direct']['run'], 'python3 -m pytest tests/test_a.py::test_direct')


class BothVersionsTests(RepoCase):
    TEST = 'from app import total\n\n\ndef test_total():\n    assert total(2) == 2\n'

    def test_a_link_only_the_old_version_shows_still_recommends_the_test(self):
        base = self.commit({'app.py': 'def total(x):\n    return x\n', 'tests/test_app.py': self.TEST})
        # After the change `total` is produced at run time, so the new graph cannot follow the test's call.
        head = self.commit({'app.py': 'def _make():\n    def total(x):\n        return x\n    return total\n\n\ntotal = _make()\n'})
        report = self.run_report(base, head, include_possible=True)
        sel = shortlist(report, 'total')
        self.assertEqual([(t['name'], t['seen_in']) for t in sel['tests']], [('test_total', ['before'])])
        self.assertEqual(sel['tests'][0]['run'], 'python3 -m pytest tests/test_app.py::test_total')

    def test_old_callers_of_a_removed_function_are_recommended(self):
        base = self.commit({'app.py': 'def total(x):\n    return x\n', 'tests/test_app.py': self.TEST})
        head = self.commit({'app.py': 'def grand_total(x):\n    return x\n'})
        sel = shortlist(self.run_report(base, head, include_possible=True), 'total')
        self.assertEqual([(t['name'], t['seen_in']) for t in sel['tests']], [('test_total', ['before'])])
        self.assertEqual(sel['removed_tests'], [])

    def test_a_test_deleted_in_the_same_change_is_listed_apart(self):
        base = self.commit({'app.py': 'def total(x):\n    return x\n', 'tests/test_app.py': self.TEST})
        head = self.commit({'app.py': 'def total(x):\n    return x + 0\n',
                            'tests/test_app.py': 'def test_other():\n    pass\n'})
        sel = shortlist(self.run_report(base, head, include_possible=True), 'total')
        self.assertEqual(sel['tests'], [])
        self.assertEqual([(t['name'], t['seen_in'], t['line']) for t in sel['removed_tests']], [('test_total', ['before'], None)])
        self.assertNotIn('run', sel['removed_tests'][0])

    def test_a_link_in_both_versions_says_so(self):
        base = self.commit({'app.py': 'def total(x):\n    return x\n', 'tests/test_app.py': self.TEST})
        head = self.commit({'app.py': 'def total(x):\n    return x + 0\n'})
        sel = shortlist(self.run_report(base, head, include_possible=True), 'total')
        self.assertEqual([(t['name'], t['seen_in'], t['tier']) for t in sel['tests']], [('test_total', ['before', 'after'], 'supported')])


class RoleTests(unittest.TestCase):
    def sym(self, name, file='tests/test_x.py', kind='FUNCTION', parent=None, decorators=()):
        return {'id': file + '::' + name, 'name': name.split('.')[-1], 'qualified_name': name, 'kind': kind, 'file': file,
                'parent_id': parent, 'decorators': list(decorators)}

    def test_roles(self):
        cls = {**self.sym('TestX', kind='CLASS'), 'id': 'C'}
        symbols = {'C': cls}
        cases = [(self.sym('test_a'), 'test'), (self.sym('TestX.test_b', kind='METHOD', parent='C'), 'test'),
                 (self.sym('TestX.setUp', kind='METHOD', parent='C'), 'setup'), (self.sym('setUpModule'), 'setup'),
                 (self.sym('db', decorators=['pytest.fixture(scope="module")']), 'fixture'),
                 (self.sym('make_user'), 'helper'), (self.sym('test_a', file='app/core.py'), None)]
        for symbol, role in cases:
            symbols[symbol['id']] = symbol
            self.assertEqual(test_role(symbol, symbols), role, symbol['qualified_name'])
        inner = self.sym('test_a.test_inner', parent='tests/test_x.py::test_a')
        self.assertEqual(test_role(inner, symbols), 'helper', 'a nested function is not collected as a test')


if __name__ == '__main__':
    unittest.main()
