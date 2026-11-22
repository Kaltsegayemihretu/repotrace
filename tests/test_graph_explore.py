import tempfile
import textwrap
import unittest
from pathlib import Path

from repotrace.graph.graph_explore import Explorer, is_test_file
from repotrace.graph.graph_index import GraphIndex
from repotrace.mapping.repository import analyze_repository

FILES = {
    'chain.py': '''
        def d():
            return 1
        def c():
            return d()
        def b():
            return c()
        def a():
            return b()
        def e():
            return c()
        def loop1():
            return loop2()
        def loop2():
            return loop1()
        class Box:
            def go(self):
                return 1
        def make():
            return Box()
        def use():
            x = make()
            return x.go()
    ''',
    'tests/test_chain.py': '''
        from chain import a
        def test_a():
            a()
    '''}


class ExplorerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        for name, text in FILES.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(textwrap.dedent(text))
        report = analyze_repository(root)
        cls.explorer = Explorer(GraphIndex(report, include_possible=True))
        cls.ids = {n['name']: n['id'] for n in cls.explorer.search('')} or {}
        cls.by_name = {s['qualified_name']: i for i, s in cls.explorer.index.symbols.items() if s['file'] == 'chain.py'}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def names(self, result, layer=None):
        return {n['name'] for n in result['nodes'] if layer is None or n['layer'] == layer}

    def test_callers_are_layered_and_negative(self):
        result = self.explorer.neighbors(self.by_name['d'], 'callers', depth=3)
        self.assertEqual(self.names(result, 0), {'d'})
        self.assertEqual(self.names(result, -1), {'c'})
        self.assertEqual(self.names(result, -2), {'b', 'e'})
        self.assertIn('a', self.names(result, -3))
        self.assertTrue(all(e['tier'] == 'supported' for e in result['edges'] if e['target'] == self.by_name['d']))

    def test_depth_limits_the_layers(self):
        result = self.explorer.neighbors(self.by_name['d'], 'callers', depth=1)
        self.assertEqual(self.names(result), {'d', 'c'})

    def test_callees_are_positive(self):
        result = self.explorer.neighbors(self.by_name['a'], 'callees', depth=3)
        self.assertEqual({n['name']: n['layer'] for n in result['nodes']}, {'a': 0, 'b': 1, 'c': 2, 'd': 3})

    def test_both_directions_share_one_root(self):
        result = self.explorer.neighbors(self.by_name['c'], 'both', depth=1)
        self.assertEqual({n['name']: n['layer'] for n in result['nodes']}, {'c': 0, 'b': -1, 'e': -1, 'd': 1})
        self.assertEqual(len(result['edges']), 3)

    def test_cycles_terminate(self):
        result = self.explorer.neighbors(self.by_name['loop1'], 'both', depth=5)
        self.assertEqual(self.names(result), {'loop1', 'loop2'})

    def test_possible_links_are_labelled_possible(self):
        result = self.explorer.neighbors(self.by_name['Box.go'], 'callers', depth=1)
        self.assertEqual([e['tier'] for e in result['edges']], ['possible'])

    def test_node_budget_is_enforced_and_reported(self):
        result = self.explorer.neighbors(self.by_name['d'], 'callers', depth=3, limit=3)
        self.assertEqual(len(result['nodes']), 3)
        self.assertTrue(result['truncated'])
        self.assertTrue({(e['source'], e['target']) for e in result['edges']} <=
                        {(a, b) for a in {n['id'] for n in result['nodes']} for b in {n['id'] for n in result['nodes']}})

    def test_search_ranks_exact_names_first_and_tests_last_and_skips_classes(self):
        found = [n['name'] for n in self.explorer.search('a')]
        self.assertEqual(found[0], 'a')
        self.assertNotIn('Box', [n['name'] for n in self.explorer.search('box')])
        self.assertEqual(self.explorer.search('  '), [])
        tests = [n for n in self.explorer.search('test_a') if n['test']]
        self.assertTrue(tests)

    def test_node_details_and_unknown_ids(self):
        node = self.explorer.node(self.by_name['c'])
        self.assertEqual((node['callers'], node['callees'], node['test']), (2, 1, False))
        with self.assertRaises(KeyError):
            self.explorer.neighbors('nope::x@1:0')

    def test_test_file_detection(self):
        self.assertTrue(is_test_file('tests/test_x.py'))
        self.assertTrue(is_test_file('pkg/test_x.py'))
        self.assertFalse(is_test_file('pkg/contest.py'))


if __name__ == '__main__':
    unittest.main()
