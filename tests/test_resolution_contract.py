import ast
import copy
import unittest

from repotrace.graph.graph_index import GraphIndex
from repotrace.graph.impact import analyze_impact
from repotrace.mapping.project_resolver import ProjectIndex
from repotrace.mapping.resolution_summary import classify_project_calls
from tests.test_impact import synthetic
from tests import test_project_resolver as fixtures


def candidate_report():
    report = synthetic(3, [(2, 1)])
    report.update(schema_version='0.3', file_schema_version='0.6')
    report['files'][0]['calls'].append({
        'id': 'candidate-call', 'file': 'app.py', 'enclosing_symbol_id': 'f1',
        'target_id': None, 'resolution': 'POSSIBLE', 'resolution_kind': 'method',
        'span': {'line': 2, 'column': 0, 'end_line': 2, 'end_column': 5},
        'candidate_targets': [{'target_id': 'f0', 'file': 'app.py', 'qualified_name': 'f0',
                               'reason': 'Receiver has an explicit constructor origin.',
                               'evidence': {'receiver_kind': 'constructed_instance'}}]})
    return report


class CandidateContractTests(unittest.TestCase):
    def test_candidates_opt_in_and_counts(self):
        report = candidate_report()
        normal = GraphIndex(report)
        self.assertEqual(normal.coverage['linked_calls'], 1)
        self.assertEqual(normal.coverage['possible_calls'], 1)
        self.assertEqual(analyze_impact(normal, 'f0')['affected'], [])
        result = analyze_impact(GraphIndex(report, include_possible=True), 'f0')
        self.assertEqual(result['summary']['affected_functions'], 2)
        self.assertTrue(all(a['path_has_possible_link'] for a in result['affected']))
        self.assertEqual(result['resolution_policy'], 'likely_and_possible')
        self.assertEqual(result['affected'][0]['witness_call']['evidence']['resolution'], 'POSSIBLE')

    def test_likely_evidence_wins_same_pair(self):
        report = candidate_report()
        certain = copy.deepcopy(report['files'][0]['calls'][0])
        certain.update(id='certain', enclosing_symbol_id='f1', target_id='f0')
        report['files'][0]['calls'].append(certain)
        result = analyze_impact(GraphIndex(report, include_possible=True), 'f0')
        self.assertFalse(any(a['path_has_possible_link'] for a in result['affected']))
        edge = result['affected'][0]['witness_call']
        self.assertEqual(edge['likely_call_site_count'], 1)
        self.assertEqual(edge['possible_call_site_count'], 1)

    def test_invalid_candidates_rejected_even_when_excluded(self):
        for mutation in [lambda c: c.update(candidate_targets=[]),
                         lambda c: c['candidate_targets'][0].update(target_id='missing'),
                         lambda c: c['candidate_targets'][0].update(file='elsewhere.py'),
                         lambda c: c['candidate_targets'][0].update(evidence=None),
                         lambda c: c.update(resolution='UNRESOLVED')]:
            report = candidate_report()
            mutation(report['files'][0]['calls'][-1])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                GraphIndex(report)

    def test_candidate_limit_and_legacy_version(self):
        report = candidate_report()
        report.update(schema_version='0.2', file_schema_version='0.5')
        with self.assertRaises(ValueError):
            GraphIndex(report)
        report = candidate_report()
        candidate = report['files'][0]['calls'][-1]['candidate_targets'][0]
        report['files'][0]['calls'][-1]['candidate_targets'] = [candidate]*9
        with self.assertRaises(ValueError):
            GraphIndex(report)

    def test_old_reports_still_work(self):
        result = analyze_impact(GraphIndex(synthetic(2, [(1, 0)])), 'f0')
        self.assertEqual(result['summary']['affected_functions'], 1)
        self.assertEqual(result['resolution_policy'], 'likely_only')

    def test_candidate_truncation_remains_visible(self):
        report = candidate_report()
        report['files'][0]['calls'][-1]['candidate_limit_reached'] = True
        result = analyze_impact(GraphIndex(report, include_possible=True), 'f0')
        self.assertEqual(result['coverage']['candidate_limited_calls'], 1)
        self.assertTrue(any('candidate limit' in w for w in result['warnings']))
        self.assertTrue(result['traversal']['complete_for_recorded_graph'])
        report['files'][0]['calls'][-1]['candidate_limit_reached'] = 'true'
        with self.assertRaises(ValueError):
            GraphIndex(report)


class ClassificationTests(unittest.TestCase):
    def test_builtin_reference_not_internal_resolution(self):
        fixture = fixtures.ProjectResolverTests()
        fixture.setUp()
        try:
            report = fixture.scan({'app.py': 'def run(value):\n    print(value)\n    len(value)\n'})
            index = ProjectIndex(report['files'], ['.'])
            classify_project_calls(index)
            calls = report['files'][0]['calls']
            self.assertTrue(all(c['category'] == 'builtin_reference' for c in calls))
            self.assertTrue(all(c['target_id'] is None for c in calls))
        finally:
            fixture.doCleanups()

    def test_shadowed_builtin_stays_unknown(self):
        fixture = fixtures.ProjectResolverTests()
        fixture.setUp()
        try:
            report = fixture.scan({'app.py': 'def run(print): print(1)\n'})
            index = ProjectIndex(report['files'], ['.'])
            classify_project_calls(index)
            self.assertEqual(report['files'][0]['calls'][0]['category'], 'unresolved')
        finally:
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
