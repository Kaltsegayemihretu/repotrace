"""The data behind the guided screens: exact identities, every reason kept, link strength apart from link version."""
import json
import unittest

from repotrace.changes.change_impact import analyze_change_impact
from repotrace.report import lab_data
from repotrace.report.dashboard import build_view
from tests.test_change_impact import RepoCase


class LabDataCase(RepoCase):
    def review(self, base, head):
        keep = {}
        report = analyze_change_impact(self.repo, base, head, include_possible=True, keep=keep)
        view = build_view(report, self.repo)
        review, graph = lab_data.build_review(report, view, keep['reports'], self.repo, 'proj')
        return review, graph

    @staticmethod
    def q(graph, index):
        return None if index is None else graph['nodes'][index]['q']


class IdentityTests(LabDataCase):
    def test_same_named_functions_never_receive_each_others_findings(self):
        files = {'a.py': 'def helper(x):\n    return x\n', 'b.py': 'def helper(x):\n    return x\n',
                 'use_a.py': 'from a import helper\n\n\ndef run_a():\n    return helper(1)\n',
                 'use_b.py': 'from b import helper\n\n\ndef run_b():\n    return helper(1)\n'}
        base = self.commit(files)
        head = self.commit({'a.py': 'def helper(x, mode):\n    return x\n'})
        review, graph = self.review(base, head)
        found = [c for c in review['concerns'] if c['type'] == 'signature']
        self.assertEqual([(self.q(graph, c['node']), c['file']) for c in found], [('run_a', 'use_a.py')])
        change = review['changed'][found[0]['changed']]
        self.assertEqual((change['file'], change['name']), ('a.py', 'helper'))
        self.assertEqual(graph['nodes'][found[0]['target']]['file'], 'a.py', 'the target is a.helper, not b.helper')

    def test_every_node_carries_its_exact_symbol_id(self):
        base = self.commit({'a.py': 'class C:\n    def m(self):\n        return 1\n\n\ndef f():\n    return C().m()\n'})
        head = self.commit({'a.py': 'class C:\n    def m(self):\n        return 2\n\n\ndef f():\n    return C().m()\n'})
        _, graph = self.review(base, head)
        ids = [n['id'] for n in graph['nodes'] if n['k'] in ('fn', 'class')]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all('::' in i for i in ids))

    def test_a_changed_nested_function_is_shown_as_the_definition_that_holds_it(self):
        """Found on tinydb: helpers defined inside a function had no node, and the screen crashed on them."""
        base = self.commit({'a.py': 'def outer():\n    def inner(x):\n        return x\n    return inner(1)\n'})
        head = self.commit({'a.py': 'def outer():\n    def inner(x):\n        return x + 1\n    return inner(1)\n'})
        review, graph = self.review(base, head)
        names = {c['name']: c for c in review['changed']}
        self.assertIn('outer.inner', names)
        node = names['outer.inner']['node']
        self.assertIsNotNone(node, 'a nested function must point at the definition that contains it')
        self.assertEqual(self.q(graph, node), 'outer')
        self.assertTrue(all(c['node'] is not None for c in review['changed']))


class TestReasonTests(LabDataCase):
    def test_shortlist_is_capped_across_changes_and_reports_omissions(self):
        base = self.commit({'app.py': 'def f():\n    return 1\n\ndef g():\n    return 1\n',
                            'tests/test_app.py': 'from app import f, g\n' + ''.join(
                                f'\ndef test_{i:02}():\n    assert {"f" if i < 15 else "g"}() == 1\n'
                                for i in range(30))})
        head = self.commit({'app.py': 'def f():\n    return 2\n\ndef g():\n    return 2\n'})
        review, _ = self.review(base, head)
        self.assertEqual(len(review['tests']), 20)
        self.assertEqual(review['test_plan']['total_candidates'], 30)
        self.assertEqual(review['test_plan']['omitted'], 10)

    def test_a_test_connected_to_two_changes_appears_once_with_both_reasons(self):
        base = self.commit({'app.py': 'def f(x):\n    return x\n\n\ndef g(x):\n    return x\n',
                            'tests/test_app.py': 'from app import f, g\n\n\ndef test_both():\n    assert f(1) == g(1)\n'})
        head = self.commit({'app.py': 'def f(x):\n    return x + 0\n\n\ndef g(x):\n    return x + 0\n'})
        review, graph = self.review(base, head)
        self.assertEqual([t['name'] for t in review['tests']], ['test_both'])
        test = review['tests'][0]
        self.assertEqual({r['for'] for r in test['reasons']}, {'f', 'g'})
        self.assertEqual({self.q(graph, r['target']) for r in test['reasons']}, {'f', 'g'}, 'each reason points at its own changed function')
        self.assertEqual(test['run'], 'python3 -m pytest tests/test_app.py::test_both')
        self.assertEqual(test['call_line'], 5, 'the line inside the test that starts the path')
        self.assertIn([5, '    assert f(1) == g(1)'], test['snippet'])


class StrengthAndVersionTests(LabDataCase):
    def test_a_removed_function_keeps_its_old_links_marked_as_the_older_version(self):
        base = self.commit({'a.py': 'def old(x):\n    return x\n\n\ndef keep():\n    return old(1)\n'})
        head = self.commit({'a.py': 'def keep():\n    return 1\n'})
        review, graph = self.review(base, head)
        gone = [n for n in graph['nodes'] if n.get('gone')]
        self.assertEqual([n['q'] for n in gone], ['old'])
        old = [l for l in graph['links'] if l[4] == 1]
        self.assertEqual([(self.q(graph, a), self.q(graph, b)) for a, b, *_ in old], [('keep', 'old')])
        a, b, strength, count, ver = old[0]
        self.assertEqual((strength, ver), (0, 1), 'sure in the older version: strength and version are separate fields')
        self.assertTrue(all(l[4] == 0 for l in graph['links'] if l not in old), 'links in the shown version say so')
        self.assertTrue(all(len(l) == 5 for l in graph['links']))

    def test_the_old_function_text_is_the_old_version_text(self):
        base = self.commit({'a.py': 'def old(x):\n    return x * 2\n'})
        head = self.commit({'a.py': 'def other():\n    return 1\n'})
        review, graph = self.review(base, head)
        change = next(c for c in review['changed'] if c['name'] == 'old')
        self.assertEqual(change['before_sig'], 'def old(x):')
        self.assertIsNone(change['after_sig'])
        self.assertIn('x * 2', graph['nodes'][change['node']]['code'])


class PayloadTests(LabDataCase):
    def test_questions_come_from_the_project_not_from_a_script(self):
        base = self.commit({'a.py': 'def parse_order(x):\n    return x\n\n\ndef run():\n    return parse_order(1)\n\n\ndef again():\n    return parse_order(2)\n'})
        head = self.commit({'a.py': 'def parse_order(x):\n    return x + 0\n\n\ndef run():\n    return parse_order(1)\n\n\ndef again():\n    return parse_order(2)\n'})
        review, graph = self.review(base, head)
        data = lab_data.payload('proj', graph, [review], {review['graph']: graph})
        self.assertEqual(data['questions'][0], 'where is parse order used?')
        json.dumps(data)                                   # the whole payload is plain JSON
        self.assertEqual(set(data['graphs']), {'main', review['graph']})

    def test_nothing_is_read_from_disk_after_the_analysis(self):
        base = self.commit({'a.py': 'def f():\n    return 1\n'})
        head = self.commit({'a.py': 'def f():\n    return 2\n'})
        keep = {}
        report = analyze_change_impact(self.repo, base, head, include_possible=True, keep=keep)
        view = build_view(report, self.repo)
        for f in list(self.repo.glob('*.py')):
            f.unlink()
        review, graph = lab_data.build_review(report, view, keep['reports'], None, 'proj')
        self.assertIn('return 2', graph['nodes'][review['changed'][0]['node']]['code'])


if __name__ == '__main__':
    unittest.main()
