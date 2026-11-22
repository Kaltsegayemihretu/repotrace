import unittest

from repotrace.report import dashboard
from tests.test_change_impact import RepoCase

BODY = ('def fmt(seconds):\n    minutes = seconds // 60\n    rest = seconds % 60\n'
        '    return "%d:%02d" % (minutes, rest)\n')


class StoryTests(RepoCase):
    def story(self, base, head):
        return dashboard.build_view(self.run_report(base, head, include_possible=True), self.repo)['story']

    def test_a_moved_function_is_one_item_and_nothing_is_left_behind(self):
        base = self.commit({'lib.py': BODY, 'a.py': 'from lib import fmt\n\n\ndef shows(n):\n    return fmt(n)\n'})
        head = self.commit({'newhome.py': BODY, 'a.py': 'from newhome import fmt\n\n\ndef shows(n):\n    return fmt(n)\n'}, remove=['lib.py'])
        story = self.story(base, head)
        self.assertEqual([p['how'] for p in story['pairs']], ['moved'])
        self.assertEqual(story['headline'], '1 change in this update')
        self.assertIn('was moved from `lib.py` to `newhome.py`', story['items'][0])
        self.assertEqual(story['check']['level'], 'ok')

    def test_a_rename_with_updated_callers_counts_them_as_edited_to_match(self):
        base = self.commit({'lib.py': BODY, 'a.py': 'from lib import fmt\n\n\ndef shows(n):\n    return fmt(n)\n'})
        head = self.commit({'lib.py': BODY.replace('fmt', 'clock_text'),
                            'a.py': 'from lib import clock_text\n\n\ndef shows(n):\n    return clock_text(n)\n'})
        story = self.story(base, head)
        self.assertEqual([p['how'] for p in story['pairs']], ['renamed'])
        self.assertIn('`fmt` was renamed to `clock_text`', story['items'][0])
        self.assertTrue(any('edited to match' in item for item in story['items']))
        self.assertEqual(len(story['primary']), 1)

    def test_a_caller_left_on_the_old_name_is_pointed_out(self):
        base = self.commit({'lib.py': BODY, 'a.py': 'from lib import fmt\n\n\ndef forgot(n):\n    return fmt(n)\n'})
        head = self.commit({'newhome.py': BODY}, remove=['lib.py'])
        story = self.story(base, head)
        self.assertEqual(story['check']['level'], 'look')
        self.assertIn('forgot', story['check']['text'])

    def test_different_code_is_not_called_a_rename(self):
        base = self.commit({'lib.py': BODY})
        head = self.commit({'lib.py': BODY.replace('fmt', 'other').replace('// 60', '// 3600')})
        story = self.story(base, head)
        self.assertEqual(story['pairs'], [])
        self.assertTrue(any('was deleted' in i for i in story['items']))
        self.assertTrue(any('is new' in i for i in story['items']))

    def test_tiny_bodies_are_never_paired(self):
        base = self.commit({'lib.py': 'def one():\n    pass\n'})
        head = self.commit({'lib.py': 'def two():\n    pass\n'})
        self.assertEqual(self.story(base, head)['pairs'], [])

    def test_two_identical_new_functions_are_ambiguous_and_not_paired(self):
        base = self.commit({'lib.py': BODY})
        head = self.commit({'lib.py': BODY.replace('fmt', 'copy_one') + '\n\n' + BODY.replace('fmt', 'copy_two')})
        self.assertEqual(self.story(base, head)['pairs'], [])

    def test_a_plain_edit_says_how_many_places_use_it_and_names_the_tests(self):
        base = self.commit({'lib.py': 'def f(x):\n    return x\n', 'tests/test_f.py': 'from lib import f\n\n\ndef test_f():\n    assert f(1) == 1\n'})
        head = self.commit({'lib.py': 'def f(x):\n    return x + 0\n'})
        story = self.story(base, head)
        self.assertIn('`f` was edited. 1 place uses it directly.', story['items'][0])
        self.assertIn('test_f', story['check']['text'])

    def test_nothing_changed(self):
        base = self.commit({'lib.py': BODY})
        head = self.commit({'lib.py': '# a comment\n' + BODY})
        story = self.story(base, head)
        self.assertEqual((story['headline'], story['items'], story['check']), ('No function changed', [], None))


if __name__ == '__main__':
    unittest.main()
