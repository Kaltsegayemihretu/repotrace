"""The examples teach by claiming things; these tests check every claim against the real analysis."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from repotrace.app import examples
from repotrace.changes.change_impact import analyze_change_impact
from repotrace.changes.git_snapshot import GitError

GIT_ENV = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e.com', 'GIT_COMMITTER_NAME': 't',
           'GIT_COMMITTER_EMAIL': 't@e.com', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}


class BuiltinSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.repo = examples.make_builtin_suite(cls._tmp.name)
        cls.reports = {c['id']: analyze_change_impact(cls.repo, c['base'], c['head'], include_possible=True)
                       for c in examples.BUILTIN_COMPARISONS}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def change(self, comparison, name):
        return next(c for c in self.reports[comparison]['changes'] if c['qualified_name'] == name)

    def callers(self, change):
        return {c['qualified_name']: ('possible' if c['uncertain'] else 'supported', c['relationship']) for c in change['review_candidates']}

    def test_every_comparison_resolves_and_has_the_fields_the_page_needs(self):
        ids = [c['id'] for c in examples.BUILTIN_COMPARISONS]
        self.assertEqual(len(ids), len(set(ids)))
        for c in examples.BUILTIN_COMPARISONS:
            for key in ('title', 'aspect', 'base', 'head', 'shows', 'steps'):
                self.assertTrue(c[key], (c['id'], key))
            for s in c['steps']:
                self.assertIn(s['do'], examples.ALLOWED_ACTIONS)
        self.assertEqual(set(self.reports), set(ids))

    def test_a_function_changed(self):
        got = self.callers(self.change('function-changed', 'calculate_total'))
        self.assertEqual(got, {'place_order': ('supported', 'direct'), 'Cart.total': ('supported', 'direct'), 'checkout': ('possible', 'indirect')})

    def test_comments_only_reports_nothing(self):
        r = self.reports['comments-only']
        self.assertEqual(r['changes'], [])
        self.assertEqual([f['change'] for f in r['files']], ['cosmetic'])

    def test_parameter_change_has_two_concrete_call_findings(self):
        c = self.change('parameters-changed', 'calculate_total')
        review = c['signature_review']
        self.assertEqual(review['finding_count'], 2)
        self.assertEqual({f['caller'] for f in review['findings']}, {'place_order', 'Cart.total'})
        self.assertTrue(all('currency' in f['reason'] for f in review['findings']))

    def test_a_value_changed_marks_the_reader(self):
        change = self.change('value-changed', 'calculate_total')
        self.assertEqual((change['reason'], change['constants']), ('reads_changed_constant', ['TAX']))
        self.assertIn('place_order', self.callers(change))

    def test_a_decorator_changed_lists_wrapped_functions_and_their_callers(self):
        change = self.change('decorator-changed', 'logged')
        self.assertEqual(change['decorator_effect']['wrapped_functions'], ['place_order'])
        self.assertEqual(self.callers(change), {'receipt': ('supported', 'direct'), 'final_receipt': ('supported', 'indirect')})

    def test_a_forgotten_caller_is_flagged(self):
        change = self.change('caller-forgotten', 'old_discount')
        self.assertEqual(change['change'], 'deleted')
        flagged = {c['qualified_name']: c['still_references_removed_name'] for c in change['review_candidates']}
        self.assertEqual(flagged, {'report': True})

    def test_possible_links_and_name_matches(self):
        change = self.change('possible-and-name', 'Cart.total')
        self.assertEqual(self.callers(change), {'checkout': ('possible', 'direct')})
        self.assertEqual([s['caller'] for s in change['name_matches']['sites']], ['print_summary'])

    def test_an_unreadable_file_is_a_gap_not_a_deletion(self):
        r = self.reports['unreadable-file']
        self.assertEqual(r['status'], 'partial')
        self.assertEqual([(g['type'], g['file']) for g in r['gaps']], [('analysis_failed', 'shop/labels.py')])
        self.assertEqual(r['changes'], [])

    def test_a_repaired_file_lists_its_function_as_possibly_new(self):
        change = self.change('file-repaired', 'render_label')
        self.assertEqual((change['change'], change['before_file_did_not_parse']), ('added', True))


class CatalogTests(unittest.TestCase):
    def test_catalog_shape_and_unique_ids(self):
        catalog = examples.catalog()
        self.assertEqual(catalog[0]['id'], 'builtin')
        ids = [e['id'] for e in catalog]
        self.assertEqual(len(ids), len(set(ids)))
        for entry in catalog:
            self.assertTrue(entry['comparisons'])
            seen = [c['id'] for c in entry['comparisons']]
            self.assertEqual(len(seen), len(set(seen)))

    def test_unavailable_projects_are_left_out_not_shown_broken(self):
        saved = examples.ALL_EXAMPLES
        examples.ALL_EXAMPLES = [saved[0], {'id': 'ghost', 'title': 'x', 'kind': 'existing', 'blurb': '', 'paths': [Path('/no/such')], 'comparisons': saved[1]['comparisons']}]
        try:
            self.assertEqual([e['id'] for e in examples.catalog()], ['builtin'])
        finally:
            examples.ALL_EXAMPLES = saved

    @unittest.skipUnless((examples.DESKTOP / 'Test Project' / '.git').exists(), 'Shopkeep is not on this computer')
    def test_real_projects_that_exist_resolve_every_comparison(self):
        entry = next(e for e in examples.catalog() if e['id'] == 'shopkeep')
        self.assertEqual(len(entry['comparisons']), len(next(p for p in examples.REAL_PROJECTS if p['id'] == 'shopkeep')['comparisons']))


class EditedCopyTests(unittest.TestCase):
    def make_source(self, root):
        src = Path(root) / 'proj'
        src.mkdir()
        (src / 'utils.py').write_text('def helper():\n    return 1\n')
        (src / 'main.py').write_text('from utils import helper\n\n\ndef run():\n    return helper()\n')
        for args in (['init', '-q', '-b', 'main'], ['add', '.'], ['commit', '-q', '-m', 'one']):
            subprocess.run(['git', '-C', str(src), *args], env=GIT_ENV, check=True, capture_output=True)
        return src

    def spec(self, source, find='def helper():'):
        return {'id': 'tiny', 'title': 'Tiny', 'kind': 'edited-copy', 'blurb': '', 'paths': [source],
                'edits': [{'file': 'utils.py', 'find': find, 'replace': 'def helper():\n    pass', 'message': 'edit', 'tag': 'try-1'}],
                'comparisons': [examples.cmp('h', 'T', 'The basics', 'HEAD~1', 'HEAD', 's', [])]}

    def test_the_edit_is_made_in_a_copy_and_the_original_is_untouched(self):
        with tempfile.TemporaryDirectory() as root:
            source = self.make_source(root)
            saved = examples.ALL_EXAMPLES
            examples.ALL_EXAMPLES = [self.spec(source)]
            try:
                copy = examples.prepare('tiny', Path(root) / 'work')
                report = analyze_change_impact(copy, 'HEAD~1', 'HEAD')
            finally:
                examples.ALL_EXAMPLES = saved
            self.assertEqual([c['qualified_name'] for c in report['changes']], ['helper'])
            self.assertEqual([c['qualified_name'] for c in report['changes'][0]['review_candidates']], ['run'])
            self.assertEqual((source / 'utils.py').read_text(), 'def helper():\n    return 1\n')
            self.assertEqual(subprocess.run(['git', '-C', str(source), 'status', '--porcelain'], capture_output=True, text=True).stdout, '')

    def test_an_edit_that_no_longer_applies_is_detected_before_it_is_offered(self):
        with tempfile.TemporaryDirectory() as root:
            source = self.make_source(root)
            self.assertTrue(examples.edit_applicable(source, self.spec(source)['edits']))
            self.assertFalse(examples.edit_applicable(source, self.spec(source, find='def gone():')['edits']))
            saved = examples.ALL_EXAMPLES
            examples.ALL_EXAMPLES = [self.spec(source, find='def gone():')]
            try:
                with self.assertRaises(GitError):
                    examples.prepare('tiny', Path(root) / 'work')
            finally:
                examples.ALL_EXAMPLES = saved

    def test_unknown_example_ids(self):
        with self.assertRaises(KeyError):
            examples.prepare('nope', tempfile.gettempdir())


if __name__ == '__main__':
    unittest.main()
