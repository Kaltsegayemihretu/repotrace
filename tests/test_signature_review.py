"""Compatibility findings must be concrete, bounded, and honest about gaps."""
import copy
import unittest

from repotrace.report.dashboard import build_view, render_html
from repotrace.mapping.repository import analyze_repository
from repotrace.changes.change_detector import detect_changes
from repotrace.changes.signature_review import review_signatures
from tests.test_change_impact import RepoCase


class SignatureReviewTests(RepoCase):
    def compare(self, old, new, call='total(10)', new_call=None):
        def files(params, expression):
            return {'pricing.py': f'def total({params}):\n    return 1\n',
                    'orders.py': f'from pricing import total\n\ndef order():\n    return {expression}\n'}
        base = self.commit(files(old, call))
        head = self.commit(files(new, call if new_call is None else new_call))
        return self.run_report(base, head)

    def findings(self, report):
        return [f for c in report['changes'] for f in c.get('signature_review', {}).get('findings', [])]

    def test_added_required_parameter_in_untouched_caller(self):
        report = self.compare('price', 'price, currency')
        finding, = self.findings(report)
        self.assertEqual(finding['caller'], 'order')
        self.assertEqual(finding['file'], 'orders.py')
        self.assertEqual(finding['span']['line'], 4)
        self.assertIn('currency', finding['reason'])
        self.assertEqual(report['signature_review']['summary']['checked_calls'], 1)
        self.assertEqual(finding['resolution'], 'LIKELY')

    def test_updated_caller_does_not_warn(self):
        self.assertFalse(self.findings(self.compare('price', 'price, currency', new_call='total(10, "USD")')))

    def test_optional_addition_does_not_warn(self):
        self.assertFalse(self.findings(self.compare('price', 'price, currency="USD"')))

    def test_keyword_renamed(self):
        self.assertEqual(len(self.findings(self.compare('price', 'amount', 'total(price=10)'))), 1)

    def test_extra_positional_after_parameter_removed(self):
        self.assertEqual(len(self.findings(self.compare('price, currency', 'price', 'total(10, "USD")'))), 1)

    def test_keyword_only_and_positional_only(self):
        cases = [('price', 'price, *, currency', 'total(10)'),
                 ('price', 'price, /', 'total(price=10)'),
                 ('price', '*, price', 'total(10)')]
        for old, new, call in cases:
            with self.subTest(new=new):
                self.assertEqual(len(self.findings(self.compare(old, new, call))), 1)

    def test_varargs_and_kwargs_in_definition_respect_binding(self):
        self.assertFalse(self.findings(self.compare('price, currency', 'price, *args', 'total(10, "USD")')))
        self.assertFalse(self.findings(self.compare('price, currency', 'price, **kwargs', 'total(10, currency="USD")')))

    def test_unpacked_arguments_are_skipped_even_if_literal(self):
        for call in ['total(*[10])', 'total(**{"price": 10})']:
            with self.subTest(call=call):
                report = self.compare('price', 'price, currency', call)
                self.assertFalse(self.findings(report))
                self.assertEqual(report['signature_review']['summary']['skipped_calls'], 1)

    def test_preexisting_invalid_call_does_not_become_new_finding(self):
        self.assertFalse(self.findings(self.compare('price', 'price, currency', 'total()')))

    def test_defaults_and_annotations_are_not_executed_or_type_checked(self):
        report = self.compare('price', 'price: int, currency=explode()')
        self.assertFalse(self.findings(report))
        self.assertEqual(report['signature_review']['summary']['checked_calls'], 1)

    def test_no_binding_change_does_not_run_call_check(self):
        report = self.compare('price: int=1', 'price: str=2')
        self.assertEqual(report['signature_review']['summary']['eligible_functions'], 0)

    def test_nested_and_async_functions(self):
        for prefix in ['async ', '']:
            base = self.commit({'nested.py': f'{prefix}def outer():\n    def inner(x):\n        return x\n    return inner(1)\n'})
            head = self.commit({'nested.py': f'{prefix}def outer():\n    def inner(x, y):\n        return x\n    return inner(1)\n'})
            report = self.run_report(base, head)
            self.assertEqual([f['target_name'] for f in self.findings(report)], ['outer.inner'])

    def test_async_target_is_checked_without_execution(self):
        base = self.commit({'async_case.py': 'async def load(x):\n    return x\nasync def main():\n    return await load(1)\n'})
        head = self.commit({'async_case.py': 'async def load(x, y):\n    return x\nasync def main():\n    return await load(1)\n'})
        finding, = self.findings(self.run_report(base, head))
        self.assertEqual(finding['target_name'], 'load')

    def test_argument_warning_does_not_hide_removed_name_warning(self):
        base = self.commit({'mixed.py': 'def old(x):\n    return x\ndef total(x):\n    return x\ndef caller():\n    old(1)\n    total(1)\n'})
        head = self.commit({'mixed.py': 'def total(x, y):\n    return x\ndef caller():\n    old(1)\n    total(1)\n'})
        view = build_view(self.run_report(base, head), self.repo)
        self.assertIn('old name', view['story']['check']['text'])
        self.assertIn('Calls to update', view['story']['check']['text'])

    def test_methods_and_decorators_are_explicitly_not_supported(self):
        for old, new in [
            ('class C:\n    def f(self, x):\n        pass\n', 'class C:\n    def f(self, x, y):\n        pass\n'),
            ('@wrapper\ndef f(x):\n    pass\n', '@wrapper\ndef f(x, y):\n    pass\n')]:
            base = self.commit({'special.py': old})
            head = self.commit({'special.py': new})
            report = self.run_report(base, head)
            self.assertFalse(self.findings(report))
            review, = [c['signature_review'] for c in report['changes'] if c.get('signature_review')]
            self.assertEqual(review['status'], 'not_supported')

    def snapshots(self):
        self.commit({'p.py': 'def f(x):\n    pass\ndef g():\n    f(1)\n    f(2)\n'})
        before = analyze_repository(self.repo)
        self.commit({'p.py': 'def f(x, y):\n    pass\ndef g():\n    f(1)\n    f(2)\n'})
        after = analyze_repository(self.repo)
        return before, after, detect_changes(before, after)['definitions']

    def test_uncertain_target_not_promoted(self):
        before, after, changes = self.snapshots()
        for call in after['files'][0]['calls']:
            call['resolution'] = 'POSSIBLE'
        result = review_signatures(before, after, changes)
        self.assertEqual(result['summary']['finding_count'], 0)
        self.assertEqual(result['summary']['skipped_calls'], 2)

    def test_finding_limit_and_call_budget_disclosed(self):
        args = self.snapshots()
        result = review_signatures(*args, max_findings=1)
        self.assertEqual(result['summary']['finding_count'], 2)
        self.assertEqual(result['summary']['findings_recorded'], 1)
        self.assertEqual(result['summary']['limits_reached'], ['max_findings'])
        self.assertTrue(next(iter(result['by_target'].values()))['truncated'])
        limited = review_signatures(*args, max_calls=1)
        self.assertEqual(limited['summary']['checked_calls'], 1)
        self.assertIn('max_calls', limited['summary']['limits_reached'])
        for value in [0, -1, True, '3']:
            with self.assertRaises(ValueError):
                review_signatures(*args, max_calls=value)

    def test_unresolved_module_call_is_not_guessed(self):
        base = self.commit({'m.py': 'def f(x):\n    pass\nf(1)\n'})
        head = self.commit({'m.py': 'def f(x, y):\n    pass\nf(1)\n'})
        report = self.run_report(base, head)
        self.assertFalse(self.findings(report))
        self.assertIn('uncertain/unresolved', report['signature_review']['policy'])

    def test_pinned_source_in_view_and_older_report_compatibility(self):
        report = self.compare('price', 'price, currency')
        (self.repo / 'orders.py').write_text('WORKTREE NOT THE ANALYZED SOURCE')
        view = build_view(report, self.repo)
        f = view['changes'][0]['signature_review']['findings'][0]
        self.assertIn('total(10)', '\n'.join(f['code']['lines']))
        self.assertNotIn('WORKTREE', str(view))
        self.assertEqual(view['story']['check']['level'], 'look')
        self.assertIn('Calls to update', view['story']['check']['text'])
        self.assertIn('Calls to update', render_html(view))
        legacy = copy.deepcopy(report)
        legacy.pop('signature_review')
        for c in legacy['changes']:
            c.pop('signature_review', None)
        self.assertIsNone(build_view(legacy, self.repo)['changes'][0]['signature_review'])


if __name__ == '__main__':
    unittest.main()
