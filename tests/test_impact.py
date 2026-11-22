import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from repotrace.graph.graph_index import GraphIndex
from repotrace.graph.impact import analyze_impact, explain_path
from repotrace.cli.impact import load_report
from repotrace.mapping.repository import analyze_repository
from tests import ROOT


def synthetic(count, edges=(), unresolved=()):
    """Small report adapter fixture; does not need AST parsing for graph tests."""
    span = {'line': 1, 'column': 0, 'end_line': 1, 'end_column': 1}
    symbols = [{'id': f'f{i}', 'kind': 'FUNCTION', 'qualified_name': f'f{i}',
                'file': 'app.py', 'span': dict(span), 'source': 'SOURCE_NOT_NEEDED'}
               for i in range(count)]
    calls = [{'id': f'c{i}', 'file': 'app.py', 'enclosing_symbol_id': f'f{source}',
              'target_id': f'f{target}', 'resolution': 'LIKELY', 'resolution_kind': 'local',
              'span': dict(span)} for i, (source, target) in enumerate(edges)]
    calls += [{'id': f'u{i}', 'file': 'app.py', 'enclosing_symbol_id': f'f{source}',
               'target_id': None, 'resolution': 'UNRESOLVED', 'span': dict(span)}
              for i, source in enumerate(unresolved)]
    return {'schema_version': '0.2', 'file_schema_version': '0.5',
            'report_kind': 'repository', 'status': 'ok', 'root': '/example',
            'files': [{'file': 'app.py', 'status': 'ok', 'module_id': 'app.py::module',
                       'symbols': symbols, 'calls': calls, 'edges': [], 'source': 'NOT_NEEDED'}],
            'skipped': [], 'diagnostics': []}


class ImpactTests(unittest.TestCase):
    def test_chain(self):
        index = GraphIndex(synthetic(3, [(1, 0), (2, 1)]))
        result = analyze_impact(index, 'f0')
        self.assertEqual([a['distance'] for a in result['affected']], [1, 2])
        self.assertEqual(result['summary']['direct_callers'], 1)
        self.assertEqual(result['summary']['indirect_callers'], 1)
        self.assertEqual(explain_path(result, 'f2'), ['f2', 'f1', 'f0'])
        self.assertEqual(result['summary']['affected_files'], ['app.py'])

    def test_reverse_direction_only(self):
        result = analyze_impact(GraphIndex(synthetic(3, [(0, 1), (1, 2)])), 'f0')
        self.assertEqual(result['affected'], [])

    def test_cycle_and_self_call(self):
        result = analyze_impact(GraphIndex(synthetic(3, [(0, 0), (1, 0), (0, 1), (2, 1)])), 'f0')
        self.assertEqual({a['symbol']['id'] for a in result['affected']}, {'f1', 'f2'})
        self.assertEqual(explain_path(result, 'f2'), ['f2', 'f1', 'f0'])

    def test_diamond_shortest_path_is_deterministic(self):
        edges = [(2, 0), (1, 0), (3, 2), (3, 1), (4, 3), (4, 0)]
        one = analyze_impact(GraphIndex(synthetic(5, edges)), 'f0')
        two = analyze_impact(GraphIndex(synthetic(5, list(reversed(edges)))), 'f0')
        self.assertEqual(explain_path(one, 'f3'), ['f3', 'f1', 'f0'])
        self.assertEqual(explain_path(two, 'f3'), ['f3', 'f1', 'f0'])
        self.assertEqual(explain_path(one, 'f4'), ['f4', 'f0'])

    def test_multiple_calls_grouped_not_multiple_callers(self):
        index = GraphIndex(synthetic(2, [(1, 0), (1, 0)]))
        result = analyze_impact(index, 'f0')
        self.assertEqual(result['summary']['affected_functions'], 1)
        self.assertEqual(result['affected'][0]['witness_call']['call_site_count'], 2)
        self.assertEqual(result['affected'][0]['witness_call']['evidence']['call_id'], 'c0')
        self.assertEqual(index.coverage['linked_calls'], 2)

    def test_unknown_calls_not_guessed(self):
        result = analyze_impact(GraphIndex(synthetic(2, unresolved=[1])), 'f0')
        self.assertEqual(result['affected'], [])
        self.assertEqual(result['coverage']['unresolved_calls'], 1)
        self.assertTrue(any('Unresolved' in w for w in result['warnings']))

    def test_unresolved_count_on_known_caller(self):
        result = analyze_impact(GraphIndex(synthetic(2, [(1, 0)], [1])), 'f0')
        self.assertEqual(result['affected'][0]['unresolved_outgoing_calls'], 1)

    def test_depth_bound(self):
        index = GraphIndex(synthetic(3, [(1, 0), (2, 1)]))
        result = analyze_impact(index, 'f0', max_depth=1)
        self.assertEqual(result['summary']['affected_functions'], 1)
        self.assertEqual(result['traversal']['limits_reached'], ['max_depth'])
        self.assertEqual(result['status'], 'limited')
        zero = analyze_impact(index, 'f0', max_depth=0)
        self.assertEqual(zero['affected'], [])
        self.assertFalse(zero['traversal']['complete_for_recorded_graph'])

    def test_depth_at_leaf_can_be_complete(self):
        result = analyze_impact(GraphIndex(synthetic(2, [(1, 0)])), 'f0', max_depth=1)
        self.assertEqual(result['status'], 'ok')

    def test_node_bound(self):
        result = analyze_impact(GraphIndex(synthetic(4, [(1, 0), (2, 0), (3, 0)])),
                                'f0', max_nodes=2)
        self.assertEqual(result['summary']['affected_functions'], 2)
        self.assertEqual(result['traversal']['limits_reached'], ['max_nodes'])

    def test_edge_bound(self):
        result = analyze_impact(GraphIndex(synthetic(3, [(1, 0), (2, 0)])), 'f0', max_edges=1)
        self.assertEqual(result['traversal']['examined_call_pairs'], 1)
        self.assertEqual(result['traversal']['limits_reached'], ['max_edges'])

    def test_exact_bound_need_not_truncate(self):
        result = analyze_impact(GraphIndex(synthetic(2, [(1, 0)])), 'f0', max_nodes=1, max_edges=1)
        self.assertEqual(result['status'], 'ok')

    def test_index_and_input_not_mutated_by_query(self):
        report = synthetic(3, [(1, 0), (2, 1)])
        original = copy.deepcopy(report)
        index = GraphIndex(report)
        result = analyze_impact(index, 'f0')
        result['affected'][0]['symbol']['qualified_name'] = 'changed'
        result['affected'][0]['witness_call']['evidence']['span']['line'] = 500
        self.assertEqual(report, original)
        self.assertEqual(analyze_impact(index, 'f0')['affected'][0]['symbol']['qualified_name'], 'f1')
        self.assertEqual(index.callers_of('f0')[0]['evidence']['span']['line'], 1)
        self.assertNotIn('source', index.symbols['f0'])

    def test_partial_source_coverage(self):
        report = synthetic(1)
        report['status'] = 'partial'
        report['files'].append({'file': 'broken.py', 'status': 'error'})
        report['skipped'].append({'path': '.venv'})
        result = analyze_impact(GraphIndex(report), 'f0')
        self.assertEqual(result['source_report_status'], 'partial')
        self.assertEqual(result['coverage']['files_failed'], 1)
        self.assertTrue(any('partial' in w for w in result['warnings']))
        self.assertTrue(result['traversal']['complete_for_recorded_graph'])

    def test_selection_and_duplicate_names(self):
        report = synthetic(2)
        report['files'][0]['symbols'][1]['qualified_name'] = 'f0'
        index = GraphIndex(report)
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            index.select(file='app.py', qualified_name='f0')
        self.assertEqual(index.select(symbol_id='f1'), 'f1')
        with self.assertRaises(ValueError):
            index.select(symbol_id='missing')
        with self.assertRaises(ValueError):
            index.select(symbol_id='f1', file='app.py')
        self.assertEqual(len(index.functions('app.py')), 2)
        self.assertEqual(index.functions('other.py'), [])

    def test_class_selection_rejected(self):
        report = synthetic(1)
        report['files'][0]['symbols'][0]['kind'] = 'CLASS'
        index = GraphIndex(report)
        self.assertEqual(index.functions(), [])
        with self.assertRaises(ValueError):
            index.select(symbol_id='f0')

    def test_invalid_limits(self):
        index = GraphIndex(synthetic(1))
        for kwargs in [{'max_depth': -1}, {'max_nodes': 0}, {'max_edges': -1},
                       {'max_depth': True}, {'max_nodes': 1.5}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                analyze_impact(index, 'f0', **kwargs)

    def test_index_size_limits(self):
        with self.assertRaisesRegex(ValueError, 'Symbol limit'):
            GraphIndex(synthetic(2), max_symbols=1)
        with self.assertRaisesRegex(ValueError, 'Call limit'):
            GraphIndex(synthetic(2, [(1, 0), (1, 0)]), max_calls=1)

    def test_invalid_schema_and_error_report(self):
        for key, value in [('schema_version', '0.1'), ('file_schema_version', 'unknown'),
                           ('report_kind', 'single_file'), ('status', 'error'), ('files', None)]:
            report = synthetic(1)
            report[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                GraphIndex(report)

    def test_invalid_graph_rejected(self):
        mutations = [
            lambda f: f['symbols'].append(copy.deepcopy(f['symbols'][0])),
            lambda f: f['calls'].append(copy.deepcopy(f['calls'][0])),
            lambda f: f['calls'][0].update(target_id='missing'),
            lambda f: f['calls'][0].update(enclosing_symbol_id='missing'),
            lambda f: f['calls'][0].update(resolution='UNRESOLVED'),
            lambda f: f['calls'][0].update(resolution_kind='unverified'),
            lambda f: f['calls'][0].update(file='wrong.py'),
            lambda f: f['symbols'][0].update(span={}),
        ]
        for mutate in mutations:
            report = synthetic(2, [(1, 0)])
            mutate(report['files'][0])
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                GraphIndex(report)

    def test_containment_is_not_dependency(self):
        report = synthetic(2)
        report['files'][0]['edges'] = [{'kind': 'CONTAINS', 'source': 'f1', 'target': 'f0'}]
        self.assertEqual(analyze_impact(GraphIndex(report), 'f0')['affected'], [])

    def test_long_chain_no_recursion_or_expanded_paths(self):
        count = 5000
        index = GraphIndex(synthetic(count, [(i, i - 1) for i in range(1, count)]))
        result = analyze_impact(index, 'f0', max_depth=count, max_nodes=count, max_edges=count)
        self.assertEqual(len(result['affected']), count - 1)
        self.assertEqual(result['traversal']['examined_call_pairs'], count - 1)
        self.assertEqual(len(explain_path(result, f'f{count - 1}')), count)
        self.assertTrue(all('path' not in a for a in result['affected']))

    def test_wide_graph_bound(self):
        index = GraphIndex(synthetic(5001, [(i, 0) for i in range(1, 5001)]))
        result = analyze_impact(index, 'f0', max_nodes=25)
        self.assertEqual(len(result['affected']), 25)
        self.assertLessEqual(result['traversal']['examined_call_pairs'], 26)

    def test_demo_integration(self):
        report = analyze_repository(ROOT / 'samples' / 'demo_impact')
        index = GraphIndex(report)
        target = index.select(file='pricing.py', qualified_name='calculate_total')
        result = analyze_impact(index, target)
        self.assertEqual(result['summary']['direct_callers'], 1)
        self.assertEqual(result['summary']['indirect_callers'], 1)
        self.assertEqual([a['symbol']['qualified_name'] for a in result['affected']],
                         ['place_order', 'checkout'])
        self.assertEqual(result['affected'][0]['witness_call']['evidence']['import_evidence']['file'],
                         'orders.py')

    def test_path_validation(self):
        result = analyze_impact(GraphIndex(synthetic(2, [(1, 0)])), 'f0')
        self.assertEqual(explain_path(result, 'f0'), ['f0'])
        with self.assertRaises(ValueError):
            explain_path(result, 'missing')
        result['affected'][0]['next_toward_target'] = 'f1'
        with self.assertRaises(ValueError):
            explain_path(result, 'f1')


class ImpactCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / 'repository.json'
        self.input.write_text(json.dumps(synthetic(3, [(1, 0), (2, 1)])))
        self.script = 'repotrace.cli.impact'

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-B', '-m', self.script, str(self.input), *args],
                              capture_output=True, text=True, cwd=ROOT)

    def test_selection_and_output(self):
        result = self.run_cli('--file', 'app.py', '--function', 'f0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['summary']['indirect_callers'], 1)
        output = self.root / 'impact.json'
        self.assertEqual(self.run_cli('--symbol', 'f0', '--output', str(output)).returncode, 0)
        self.assertEqual(self.run_cli('--symbol', 'f0', '--output', str(output)).returncode, 2)
        self.assertEqual(self.run_cli('--symbol', 'f0', '--output', str(output), '--overwrite').returncode, 0)

    def test_list_functions(self):
        result = self.run_cli('--list-functions', '--file', 'app.py')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)['functions']), 3)

    def test_limits_and_invalid_selector(self):
        result = self.run_cli('--symbol', 'f0', '--max-depth', '1')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['status'], 'limited')
        self.assertEqual(self.run_cli('--symbol', 'missing').returncode, 2)
        self.assertEqual(self.run_cli('--symbol', 'f0', '--max-report-bytes', '10').returncode, 2)
        self.assertEqual(self.run_cli('--symbol', 'f0', '--max-index-symbols', '1').returncode, 2)

    def test_input_cannot_be_overwritten(self):
        original = self.input.read_bytes()
        result = self.run_cli('--symbol', 'f0', '--output', str(self.input), '--overwrite')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.input.read_bytes(), original)
        linked = self.root / 'linked.json'
        linked.symlink_to(self.input)
        self.assertEqual(self.run_cli('--symbol', 'f0', '--output', str(linked), '--overwrite').returncode, 2)

    def test_malformed_and_oversize_input(self):
        self.input.write_text('{bad json')
        self.assertEqual(self.run_cli('--symbol', 'f0').returncode, 2)
        with self.assertRaises(ValueError):
            load_report(self.input, max_bytes=2)
        self.input.write_text('{}')
        self.assertEqual(self.run_cli('--symbol', 'f0').returncode, 2)


if __name__ == '__main__':
    unittest.main()
