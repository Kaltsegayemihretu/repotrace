import json
import re
import tempfile
import unittest
from pathlib import Path

from repotrace.cli import change as change_cli
from repotrace.report import dashboard
from tests.test_change_impact import CHAIN, RepoCase


class DashboardTests(RepoCase):
    def view(self, base, head, **kw):
        report = self.run_report(base, head, **kw)
        return report, dashboard.build_view(report, self.repo)

    def test_changed_function_has_diff_candidates_chain_and_code(self):
        base = self.commit(CHAIN)
        head = self.commit({'pricing.py': 'def calculate_total(price):\n    return price * 1.2\n'})
        report, view = self.view(base, head)
        change = view['changes'][0]
        self.assertEqual((change['name'], change['change']), ('calculate_total', 'modified'))
        diff_lines = change['diff'].splitlines()
        self.assertIn('-    return price * 1.15', diff_lines)       # each change on its own line
        self.assertIn('+    return price * 1.2', diff_lines)
        self.assertEqual(view['head']['subject'], 'c')
        self.assertEqual(view['base']['commit'], base)
        names = {c['name']: c for c in change['candidates']}
        self.assertEqual(names['place_order']['relationship'], 'direct')
        self.assertEqual(names['checkout']['chain'], ['checkout', 'place_order', 'calculate_total'])
        site = names['place_order']['call_site']
        self.assertEqual(site['side'], 'after')
        self.assertIn('calculate_total(price)', ''.join(site['code']['lines']))
        self.assertEqual(names['place_order']['tier'], 'supported')
        self.assertTrue(view['source_available'])

    def test_sure_links_are_explained_without_analyzer_jargon(self):
        base = self.commit(CHAIN)
        head = self.commit({'pricing.py': 'def calculate_total(price):\n    return price * 1.2\n'})
        _, view = self.view(base, head)
        why = {c['name']: c['why'] for c in view['changes'][0]['candidates']}
        self.assertIn('imports the function by name', why['place_order'])
        for text in why.values():
            for jargon in ('source roots', 'initialized-module', 'bindings'):
                self.assertNotIn(jargon, text)

    def test_possible_links_explain_their_evidence_in_words(self):
        files = {'lib.py': 'class Dep:\n    def save(self):\n        return 1\n',
                 'use.py': 'from lib import Dep\n\n\ndef go():\n    d = Dep()\n    return d.save()\n'}
        base = self.commit(files)
        head = self.commit({'lib.py': files['lib.py'].replace('return 1', 'return 2')})
        _, view = self.view(base, head, include_possible=True)
        candidate = view['changes'][0]['candidates'][0]
        self.assertEqual(candidate['tier'], 'possible')
        self.assertIn('possibility', candidate['why'])
        self.assertTrue(any('created by' in b for b in candidate['because']))

    def test_deleted_function_uses_base_commit_and_flags_forgotten_callers(self):
        files = {'lib.py': 'def old():\n    return 1\n', 'a.py': 'import lib\n\n\ndef forgot():\n    return lib.old()\n'}
        base = self.commit(files)
        head = self.commit({'lib.py': 'def other():\n    return 2\n'})
        _, view = self.view(base, head)
        change = next(c for c in view['changes'] if c['change'] == 'deleted')
        self.assertIsNone(change['diff'])
        self.assertIn('def old()', change['source']['before'])
        candidate = change['candidates'][0]
        self.assertEqual(candidate['call_site']['side'], 'before')
        self.assertIn('head still references the removed name', candidate['flags'])
        self.assertEqual(change['name_matches']['meaning'], 'still_referenced_in_head')

    def test_without_a_repository_the_page_still_builds_and_says_so(self):
        base = self.commit(CHAIN)
        head = self.commit({'pricing.py': 'def calculate_total(price):\n    return 0\n'})
        report = self.run_report(base, head)
        view = dashboard.build_view(report, None)
        self.assertFalse(view['source_available'])
        self.assertIsNone(view['changes'][0]['diff'])
        self.assertIsNone(view['changes'][0]['candidates'][0]['call_site']['code'])

    def test_html_is_self_contained_and_embeds_data_safely(self):
        base = self.commit({'m.py': 'def f():\n    return "</script><b>x</b>"\n'})
        head = self.commit({'m.py': 'def f():\n    return "</script><img src=x onerror=alert(1)>"\n'})
        _, view = self.view(base, head)
        html = dashboard.render_html(view)
        self.assertNotRegex(html, r'(src|href)=["\']https?://')
        self.assertEqual(html.count('</script>'), 2)         # only the page's own two script blocks
        data = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1)
        embedded = json.loads(data)
        self.assertIn('onerror=alert(1)', embedded['changes'][0]['diff'])    # data survives, markup does not
        self.assertNotIn('__DATA__', html)
        self.assertIn('textContent', html)                  # data is only ever inserted as text

    def test_writing_refuses_wrong_extension_symlinks_and_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'out.html'
            dashboard.write_html('<html></html>', target)
            with self.assertRaises(ValueError):
                dashboard.write_html('x', target)
            dashboard.write_html('y', target, overwrite=True)
            with self.assertRaises(ValueError):
                dashboard.write_html('x', Path(d) / 'out.txt')
            link = Path(d) / 'link.html'
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                dashboard.write_html('x', link, overwrite=True)

    def test_command_line_writes_json_and_dashboard_together(self):
        base = self.commit(CHAIN)
        head = self.commit({'pricing.py': 'def calculate_total(price):\n    return 0\n'})
        with tempfile.TemporaryDirectory() as d:
            code = change_cli.main([str(self.repo), '--base', base, '--head', head, '--dashboard', str(Path(d) / 'd.html'),
                                    '--output', str(Path(d) / 'r.json')])
            self.assertEqual(code, 0)
            self.assertIn('calculate_total', (Path(d) / 'd.html').read_text())
            report = json.loads((Path(d) / 'r.json').read_text())
            again = dashboard.build_view(report, self.repo)
            self.assertEqual(again['changes'][0]['name'], 'calculate_total')

    def test_older_reports_with_bare_witness_calls_still_render(self):
        base = self.commit(CHAIN)
        head = self.commit({'pricing.py': 'def calculate_total(price):\n    return 0\n'})
        report = self.run_report(base, head)
        for change in report['changes']:
            for candidate in change['review_candidates']:
                for side in candidate['sides'].values():
                    side['witness_call'] = side['witness_call']['evidence']
        view = dashboard.build_view(report, self.repo)
        self.assertEqual(view['changes'][0]['candidates'][0]['name'], 'place_order')


if __name__ == '__main__':
    unittest.main()


class GraphTests(RepoCase):
    def graph(self, base, head, name, **kw):
        report = self.run_report(base, head, **kw)
        view = dashboard.build_view(report, self.repo)
        change = next(c for c in view['changes'] if c['name'] == name)
        return change['graph'], change

    def test_layers_follow_the_links_and_edges_point_toward_the_changed_function(self):
        base = self.commit(CHAIN)
        head = self.commit({'pricing.py': 'def calculate_total(price):\n    return price * 1.2\n'})
        graph, change = self.graph(base, head, 'calculate_total')
        layers = {n['name']: n['layer'] for n in graph['nodes']}
        self.assertEqual(layers, {'calculate_total': 0, 'place_order': -1, 'checkout': -2})
        edges = {(e['source'].split('::')[1], e['target'].split('::')[1], e['tier']) for e in graph['edges']}
        self.assertEqual(edges, {('place_order', 'calculate_total', 'supported'), ('checkout', 'place_order', 'supported')})
        self.assertEqual(graph['root'], 'pricing.py::calculate_total')
        node = next(n for n in graph['nodes'] if n['name'] == 'checkout')
        self.assertEqual(change['candidates'][node['cand']]['name'], 'checkout')    # the node links to its card

    def test_possible_links_make_dashed_edges_and_weaker_nodes(self):
        files = {'lib.py': 'class Dep:\n    def save(self):\n        return 1\n',
                 'use.py': 'from lib import Dep\n\n\ndef go():\n    d = Dep()\n    return d.save()\n\n\ndef top():\n    return go()\n'}
        base = self.commit(files)
        head = self.commit({'lib.py': files['lib.py'].replace('return 1', 'return 2')})
        graph, _ = self.graph(base, head, 'Dep.save', include_possible=True)
        tiers = {n['name']: n['tier'] for n in graph['nodes']}
        self.assertEqual(tiers['go'], 'possible')
        self.assertEqual(tiers['top'], 'possible')           # one doubtful hop makes the whole path doubtful
        self.assertEqual({e['tier'] for e in graph['edges'] if e['source'].endswith('::go')}, {'possible'})

    def test_decorator_wrapped_functions_get_their_own_layer(self):
        lib = ('import functools\n\n\ndef logged(func):\n    @functools.wraps(func)\n    def wrapper(*args, **kwargs):\n'
               '        return func(*args, **kwargs)\n    return wrapper\n\n\n@logged\ndef save(x):\n    return x\n')
        use = 'from lib import save\n\n\ndef go():\n    return save(1)\n\n\ndef main():\n    return go()\n'
        base = self.commit({'lib.py': lib, 'use.py': use})
        head = self.commit({'lib.py': lib.replace('return func(*args, **kwargs)', 'print(1)\n        return func(*args, **kwargs)')})
        graph, _ = self.graph(base, head, 'logged')
        layers = {n['name']: n['layer'] for n in graph['nodes']}
        self.assertEqual((layers['logged'], layers['save'], layers['go'], layers['main']), (0, -1, -2, -3))
        self.assertIn(('save', 'logged', 'wraps'), {(e['source'].split('::')[1], e['target'].split('::')[1], e['kind']) for e in graph['edges']})

    def test_name_matches_become_dotted_edges_to_the_changed_function(self):
        files = {'lib.py': 'class Store:\n    def put(self, x):\n        return x\n', 'use.py': 'def go(thing):\n    return thing.put(1)\n'}
        base = self.commit(files)
        head = self.commit({'lib.py': files['lib.py'].replace('return x', 'return x + 1')})
        graph, change = self.graph(base, head, 'Store.put')
        weak = next(n for n in graph['nodes'] if n['name'] == 'go')
        self.assertEqual((weak['role'], weak['tier'], weak['layer']), ('weak', 'name_only', -1))
        self.assertEqual(change['name_matches']['sites'][weak['site']]['caller'], 'go')
        self.assertEqual({e['kind'] for e in graph['edges']}, {'name'})

    def test_deleted_functions_graph_uses_the_older_callers(self):
        files = {'lib.py': 'def old():\n    return 1\n', 'a.py': 'import lib\n\n\ndef forgot():\n    return lib.old()\n'}
        base = self.commit(files)
        head = self.commit({'lib.py': 'def other():\n    return 2\n'})
        graph, _ = self.graph(base, head, 'old')
        self.assertEqual({n['name']: n['layer'] for n in graph['nodes']}, {'old': 0, 'forgot': -1})

    def test_changed_callers_and_tests_are_marked_and_the_graph_is_bounded(self):
        base = self.commit({**CHAIN, 'tests/test_p.py': 'from pricing import calculate_total\n\n\ndef test_it():\n    calculate_total(1)\n'})
        head = self.commit({'pricing.py': 'def calculate_total(price):\n    return 0\n', 'orders.py': CHAIN['orders.py'].replace('calculate_total(price)', 'calculate_total(price) + 1')})
        graph, _ = self.graph(base, head, 'calculate_total')
        flags = {n['name']: (n['changed'], n['test']) for n in graph['nodes']}
        self.assertEqual(flags['place_order'], (True, False))
        self.assertEqual(flags['test_it'], (False, True))
        old = dashboard.MAX_GRAPH_NODES
        dashboard.MAX_GRAPH_NODES = 2
        try:
            small, _ = self.graph(base, head, 'calculate_total')
        finally:
            dashboard.MAX_GRAPH_NODES = old
        self.assertTrue(small['truncated'])
        self.assertEqual(len(small['nodes']), 2)
