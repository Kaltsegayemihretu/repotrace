import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from repotrace.cli import change as change_cli
from repotrace.changes.change_impact import analyze_change_impact
from repotrace.changes.git_snapshot import GitError, resolve_commit

GIT_ENV = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}

CHAIN = {
    'pricing.py': 'def calculate_total(price):\n    return price * 1.15\n',
    'orders.py': 'from pricing import calculate_total\n\n\ndef place_order(price):\n    return calculate_total(price)\n',
    'checkout.py': 'from orders import place_order\n\n\ndef checkout(price):\n    return place_order(price)\n',
}
METHODS = {
    'library/__init__.py': 'from .service import LibraryService\n',
    'library/service.py': ('class BaseStore:\n    def save(self, title):\n        return title\n\n\n'
                           'class LibraryService(BaseStore):\n    def checkout(self, title):\n'
                           '        return self.save(title)\n'),
    'operations.py': 'def process_checkout(service, title):\n    return service.checkout(title)\n',
    'main.py': ('from library import LibraryService\nfrom operations import process_checkout\n\n\n'
                'def run():\n    service = LibraryService()\n    return process_checkout(service, "x")\n'),
}


class RepoCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')

    def git(self, *args):
        done = subprocess.run(['git', '-C', str(self.repo), *args], env=GIT_ENV,
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def commit(self, files, message='c', remove=()):
        for name in remove:
            self.git('rm', '-q', name)
        for name, text in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            self.git('add', name)
        self.git('commit', '-q', '--allow-empty', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def run_report(self, base, head, **kw):
        return analyze_change_impact(self.repo, base, head, **kw)

    @staticmethod
    def names(report, change=0, key='review_candidates'):
        return [(c['qualified_name'], c['relationship']) for c in report['changes'][change][key]]


class ChangeImpactTests(RepoCase):
    def test_duplicate_nested_names_only_flag_real_edits(self):
        code = ('def changed():\n    return 1\n\n'
                'def build(flag):\n    if flag:\n        def helper():\n            return 2\n'
                '    else:\n        def helper():\n            return 3\n    return helper\n')
        base = self.commit({'app.py': code})
        head = self.commit({'app.py': code.replace('return 1', 'return 4')})
        self.assertEqual(self.run_report(base, head)['gaps'], [])
        updated = self.commit({'app.py': code.replace('return 2', 'return 5')})
        gaps = self.run_report(head, updated)['gaps']
        self.assertIn(('ambiguous_definition', 'build.helper'),
                      [(gap['type'], gap.get('qualified_name')) for gap in gaps])

    def test_modified_function_finds_direct_and_indirect_callers(self):
        base = self.commit(CHAIN)
        head = self.commit({'pricing.py': 'def calculate_total(price):\n    return price * 1.2\n'})
        report = self.run_report(base, head)
        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['base']['commit'], base)
        self.assertEqual(report['head']['commit'], head)
        self.assertEqual(report['summary']['functions_modified'], 1)
        self.assertEqual(self.names(report), [('place_order', 'direct'), ('checkout', 'indirect')])
        item = report['changes'][0]['review_candidates'][0]
        self.assertEqual(sorted(item['sides']), ['after', 'before'])
        self.assertFalse(item['uncertain'])
        self.assertIn('witness_call', item['sides']['after'])

    def test_deleted_function_uses_old_graph_for_callers(self):
        base = self.commit(CHAIN)
        head = self.commit({'orders.py': 'def place_order(price):\n    return price\n'},
                           remove=['pricing.py'])
        report = self.run_report(base, head)
        deleted = [c for c in report['changes'] if c['change'] == 'deleted']
        self.assertEqual(len(deleted), 1)
        self.assertEqual(deleted[0]['qualified_name'], 'calculate_total')
        self.assertEqual(list(deleted[0]['impact']), ['before'])
        self.assertNotIn('after', deleted[0])
        # place_order existed before and still exists, though its call was removed.
        direct = deleted[0]['review_candidates'][0]
        self.assertEqual((direct['qualified_name'], list(direct['sides'])), ('place_order', ['before']))
        self.assertTrue(direct['exists_after'])

    def test_added_function_has_only_new_side(self):
        base = self.commit(CHAIN)
        head = self.commit({'pricing.py': CHAIN['pricing.py'] + '\n\ndef discount(price):\n    return calculate_total(price)\n'})
        report = self.run_report(base, head)
        added = [c for c in report['changes'] if c['change'] == 'added']
        self.assertEqual([c['qualified_name'] for c in added], ['discount'])
        self.assertEqual(list(added[0]['impact']), ['after'])
        self.assertNotIn('before', added[0])
        self.assertEqual(report['summary']['functions_modified'], 0)

    def test_comment_and_line_shift_are_not_function_changes(self):
        base = self.commit(CHAIN)
        shifted = '# new header\n\n\ndef calculate_total(price):\n    # note\n    return price * 1.15\n'
        head = self.commit({'pricing.py': shifted})
        report = self.run_report(base, head)
        self.assertEqual(report['changes'], [])
        self.assertEqual(report['gaps'], [])
        self.assertEqual([f['change'] for f in report['files']], ['cosmetic'])

    def test_docstring_edit_beside_a_real_change_is_not_a_change(self):
        old = 'def a():\n    "old doc"\n    return 1\n\n\ndef b():\n    return 1\n'
        new = 'def a():\n    "new doc"\n    return 1\n\n\ndef b():\n    return 2\n'
        base, head = self.commit({'m.py': old}), self.commit({'m.py': new})
        report = self.run_report(base, head)
        self.assertEqual([c['qualified_name'] for c in report['changes']], ['b'])
        self.assertEqual(report['files'][0]['docstring_or_comment_only'], ['a'])

    def test_untouched_caller_of_deleted_function_is_flagged(self):
        files = {'lib.py': 'def old():\n    return 1\n\n\ndef keep():\n    return 2\n',
                 'a.py': 'from lib import old\n\n\ndef forgot():\n    return old()\n',
                 'b.py': 'from lib import old\n\n\ndef updated():\n    return old()\n'}
        base = self.commit(files)
        head = self.commit({'lib.py': 'def keep():\n    return 2\n',
                            'b.py': 'from lib import keep\n\n\ndef updated():\n    return keep()\n'})
        report = self.run_report(base, head)
        deleted = [c for c in report['changes'] if c['change'] == 'deleted'][0]
        flags = {c['qualified_name']: c['may_still_call_removed'] for c in deleted['review_candidates']}
        self.assertEqual(flags, {'forgot': True, 'updated': False})

    def test_moved_function_does_not_flag_callers_that_still_reach_it(self):
        files = {'lib.py': 'def fmt():\n    return 1\n',
                 'a.py': 'from lib import fmt\n\n\ndef shows():\n    return fmt()\n'}
        base = self.commit(files)
        head = self.commit({'newhome.py': 'def fmt():\n    return 1\n',
                            'a.py': 'from newhome import fmt\n\n\ndef shows():\n    return fmt()\n'},
                           remove=['lib.py'])
        report = self.run_report(base, head)
        deleted = [c for c in report['changes'] if c['change'] == 'deleted'][0]
        self.assertEqual({c['qualified_name']: c['may_still_call_removed']
                          for c in deleted['review_candidates']}, {'shows': False})

    def test_forgotten_caller_is_still_flagged_when_the_name_exists_elsewhere(self):
        files = {'lib.py': 'def fmt():\n    return 1\n',
                 'a.py': 'from lib import fmt\n\n\ndef forgot():\n    return fmt()\n'}
        base = self.commit(files)
        head = self.commit({'newhome.py': 'def fmt():\n    return 1\n'}, remove=['lib.py'])
        report = self.run_report(base, head)
        deleted = [c for c in report['changes'] if c['change'] == 'deleted'][0]
        self.assertEqual({c['qualified_name']: c['may_still_call_removed']
                          for c in deleted['review_candidates']}, {'forgot': True})

    def test_import_or_constant_change_is_reported_as_gap(self):
        base = self.commit({**CHAIN, 'orders.py': CHAIN['orders.py'] + 'RATE = 1\n'})
        head = self.commit({'orders.py': CHAIN['orders.py'] + 'RATE = 2\n'})
        report = self.run_report(base, head)
        self.assertEqual(report['changes'], [])
        self.assertEqual(report['gaps'][0]['file'], 'orders.py')
        self.assertIn('not computed', report['gaps'][0]['message'])

    def test_broken_file_is_a_gap_not_a_deletion(self):
        base = self.commit(CHAIN)
        head = self.commit({'pricing.py': 'def calculate_total(price:\n    return 1\n'})
        report = self.run_report(base, head)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual([c for c in report['changes'] if c['change'] == 'deleted'], [])
        self.assertEqual(report['gaps'][0]['type'], 'analysis_failed')
        self.assertEqual(report['gaps'][0]['side'], 'after')

    def test_candidate_uncertainty_survives_pipeline(self):
        base = self.commit(METHODS)
        head = self.commit({'library/service.py': METHODS['library/service.py'].replace('return title', 'return title.strip()', 1)})
        plain = self.run_report(base, head)
        self.assertEqual(self.names(plain), [])
        report = self.run_report(base, head, include_possible=True)
        names = self.names(report)
        self.assertEqual(names[0], ('LibraryService.checkout', 'direct'))
        self.assertIn(('run', 'indirect'), names)
        self.assertTrue(all(c['uncertain'] for c in report['changes'][0]['review_candidates']))
        self.assertTrue(report['settings']['include_possible'])

    def test_nested_edit_marks_enclosing_function(self):
        outer = 'def outer():\n    def inner():\n        return {}\n    return inner()\n'
        base = self.commit({'m.py': outer.replace('{}', '1')})
        head = self.commit({'m.py': outer.replace('{}', '2')})
        report = self.run_report(base, head)
        flags = {c['qualified_name']: c.get('includes_nested_change', False) for c in report['changes']}
        self.assertEqual(len(flags), 2)
        self.assertTrue(any(flags.values()))

    def test_source_root_setting_is_recorded_and_used(self):
        base = self.commit({'src/pkg/__init__.py': '', 'src/pkg/a.py': 'def f():\n    return 1\n',
                            'src/pkg/b.py': 'from pkg.a import f\n\n\ndef g():\n    return f()\n'})
        head = self.commit({'src/pkg/a.py': 'def f():\n    return 2\n'})
        report = self.run_report(base, head, source_roots=['src'])
        self.assertEqual(report['settings']['source_roots'], ['src'])
        self.assertEqual(self.names(report), [('g', 'direct')])

    def test_budget_marks_report_limited(self):
        base = self.commit({'m.py': 'def a():\n    return 1\n\n\ndef b():\n    return 1\n'})
        head = self.commit({'m.py': 'def a():\n    return 2\n\n\ndef b():\n    return 2\n'})
        report = self.run_report(base, head, max_changes=1)
        self.assertEqual(report['status'], 'limited')
        self.assertEqual(sum('not_queried' in c for c in report['changes']), 1)

    def test_identical_commits_and_invalid_refs(self):
        base = self.commit(CHAIN)
        report = self.run_report(base, 'HEAD')
        self.assertEqual(report['changes'], [])
        self.assertTrue(any('same commit' in w for w in report['warnings']))
        with self.assertRaises(GitError):
            self.run_report(base, 'no-such-ref')
        with self.assertRaises(GitError):
            resolve_commit(self.repo, '--output=x')
        with self.assertRaises(GitError):
            analyze_change_impact(self.repo.parent, base, base)

    def test_repository_state_is_untouched(self):
        base = self.commit(CHAIN)
        head = self.commit({'pricing.py': 'def calculate_total(price):\n    return 0\n'})
        self.git('checkout', '-q', base)
        (self.repo / 'orders.py').write_text('# dirty edit\n')
        (self.repo / 'scratch.txt').write_text('untracked')
        before = (self.git('status', '--porcelain'), self.git('rev-parse', 'HEAD'),
                  (self.repo / '.git' / 'index').read_bytes(), (self.repo / 'orders.py').read_text())
        report = self.run_report(base, head)
        after = (self.git('status', '--porcelain'), self.git('rev-parse', 'HEAD'),
                 (self.repo / '.git' / 'index').read_bytes(), (self.repo / 'orders.py').read_text())
        self.assertEqual(before, after)
        self.assertTrue(report['worktree_dirty'])
        # The dirty edit is invisible: snapshots come from commits.
        self.assertEqual(self.names(report), [('place_order', 'direct'), ('checkout', 'indirect')])
        self.assertTrue(report['repository'] == str(self.repo.resolve()))
        self.assertNotIn('repotrace-', json.dumps(report))


class ChangeCliTests(RepoCase):
    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = change_cli.main([str(self.repo), *args])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_text_summary_json_output_and_exit_codes(self):
        base = self.commit(CHAIN)
        head = self.commit({'pricing.py': 'def calculate_total(price):\n    return 3\n'})
        code, out, _ = self.call('--base', base, '--head', head)
        self.assertEqual(code, 0)
        self.assertIn('[modified] pricing.py::calculate_total', out)
        self.assertIn('direct', out)
        target = Path(self._tmp.name) / 'out.json'
        code, out, _ = self.call('--base', base, '--head', head, '--output', str(target))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(target.read_text())['report_kind'], 'change_impact')
        code, _, err = self.call('--base', base, '--head', head, '--output', str(target))
        self.assertEqual(code, 2)
        code, _, err = self.call('--base', 'nope', '--head', head)
        self.assertEqual(code, 2)
        self.assertIn('Cannot resolve', err)


if __name__ == '__main__':
    unittest.main()


class NameMatchTests(RepoCase):
    LIB = ('class Store:\n    def save(self, item):\n        return item\n\n\n'
           'def helper(x):\n    return x\n')

    def report(self, before, after, **kw):
        base = self.commit(before)
        head = self.commit(after)
        return self.run_report(base, head, **kw)

    def matches(self, report, qualified_name):
        change = next(c for c in report['changes'] if c['qualified_name'] == qualified_name)
        return change['name_matches']

    def test_untyped_receiver_call_is_listed_as_name_only_evidence(self):
        before = {'lib.py': self.LIB, 'use.py': 'def go(thing):\n    return thing.save(1)\n'}
        after = {'lib.py': self.LIB.replace('return item', 'return item + 1')}
        report = self.report(before, after)
        found = self.matches(report, 'Store.save')
        self.assertEqual((found['status'], found['total'], found['strength']), ('ok', 1, 'name_only'))
        self.assertEqual(found['sites'][0]['caller'], 'go')
        # It is not a graph link and does not appear among review candidates.
        self.assertEqual(report['changes'][0]['review_candidates'], [])
        self.assertEqual(report['summary']['name_match_sites'], 1)

    def test_method_needs_attribute_form_but_function_accepts_both(self):
        use = ('from lib import helper\nimport lib\n\n\n'
               'def a(save):\n    return save(1)\n\n\ndef b(o):\n    return o.helper(1)\n')
        before = {'lib.py': self.LIB, 'use.py': use}
        after = {'lib.py': self.LIB.replace('return item', 'return item + 1').replace('return x', 'return x + 1')}
        report = self.report(before, after)
        self.assertEqual(self.matches(report, 'Store.save')['total'], 0)   # bare save() cannot be a method call
        self.assertEqual({s['caller'] for s in self.matches(report, 'helper')['sites']}, {'b'})

    def test_calls_already_explained_by_the_graph_are_not_repeated(self):
        before = {'lib.py': self.LIB, 'use.py': 'from lib import helper\n\n\ndef go(v):\n    return helper(v)\n'}
        after = {'lib.py': self.LIB.replace('return x', 'return x + 1')}
        report = self.report(before, after)
        self.assertEqual([c['qualified_name'] for c in report['changes'][0]['review_candidates']], ['go'])
        self.assertEqual(self.matches(report, 'helper')['total'], 0)

    def test_deleted_function_reports_references_that_remain_in_head(self):
        before = {'lib.py': self.LIB, 'a.py': 'import lib\n\n\ndef forgot(v):\n    return lib.helper(v)\n',
                  'b.py': 'import lib\n\n\ndef fixed(v):\n    return lib.helper(v)\n'}
        after = {'lib.py': 'class Store:\n    def save(self, item):\n        return item\n',
                 'b.py': 'def fixed(v):\n    return v\n'}
        report = self.report(before, after)
        change = next(c for c in report['changes'] if c['change'] == 'deleted')
        self.assertEqual(change['name_matches']['meaning'], 'still_referenced_in_head')
        self.assertEqual([s['caller'] for s in change['name_matches']['sites']], ['forgot'])
        flags = {c['qualified_name']: c['still_references_removed_name'] for c in change['review_candidates']}
        self.assertEqual(flags, {'forgot': True, 'fixed': False})

    def test_common_names_are_flagged_noisy_and_special_methods_are_skipped(self):
        lib = 'class Box:\n    def get(self):\n        return 1\n\n    def __repr__(self):\n        return "b"\n'
        use = 'def go(d):\n    return d.get("x")\n'
        report = self.report({'lib.py': lib, 'use.py': use},
                             {'lib.py': lib.replace('return 1', 'return 2').replace('"b"', '"c"')})
        self.assertTrue(self.matches(report, 'Box.get')['noisy_name'])
        self.assertEqual(self.matches(report, 'Box.__repr__')['status'], 'skipped_special_method')

    def test_limit_truncates_but_keeps_the_true_total(self):
        calls = ''.join(f'def f{i}(o):\n    return o.save({i})\n\n\n' for i in range(5))
        before = {'lib.py': self.LIB, 'use.py': calls}
        after = {'lib.py': self.LIB.replace('return item', 'return item + 1')}
        base = self.commit(before)
        head = self.commit(after)
        report = self.run_report(base, head, max_name_matches=2)
        found = self.matches(report, 'Store.save')
        self.assertEqual((found['total'], found['shown'], found['truncated']), (5, 2, True))
        none = self.run_report(base, head, max_name_matches=0)
        self.assertEqual(self.matches(none, 'Store.save')['sites'], [])
        self.assertEqual(self.matches(none, 'Store.save')['total'], 5)

    def test_test_files_are_marked_and_sorted_last(self):
        before = {'lib.py': self.LIB, 'tests/test_x.py': 'def test_a(o):\n    o.save(1)\n',
                  'zz.py': 'def go(o):\n    o.save(1)\n'}
        after = {'lib.py': self.LIB.replace('return item', 'return item + 1')}
        sites = self.matches(self.report(before, after), 'Store.save')['sites']
        self.assertEqual([(s['file'], s['is_test']) for s in sites], [('zz.py', False), ('tests/test_x.py', True)])


class DecoratorChangeTests(RepoCase):
    LIB = ('import functools\n\n\ndef logged(func):\n    @functools.wraps(func)\n    def wrapper(*args, **kwargs):\n'
           '        return func(*args, **kwargs)\n    return wrapper\n\n\n'
           'def role(*roles):\n    def decorator(func):\n        @functools.wraps(func)\n        def wrapper(*args, **kwargs):\n'
           '            return func(*args, **kwargs)\n        return wrapper\n    return decorator\n\n\n'
           '@logged\ndef save(x):\n    return x\n\n\n@role("a")\ndef erase(x):\n    return x\n\n\ndef plain(x):\n    return x\n')
    USE = ('from lib import save, erase, plain\n\n\ndef go():\n    return save(1)\n\n\n'
           'def wipe():\n    return erase(1)\n\n\ndef other():\n    return plain(1)\n')
    TOP = 'from use import go\n\n\ndef main():\n    return go()\n'

    def run_change(self, old, new):
        base = self.commit({'lib.py': old, 'use.py': self.USE, 'top.py': self.TOP})
        head = self.commit({'lib.py': new})
        return self.run_report(base, head)

    def names(self, report, qualified_name):
        change = next(c for c in report['changes'] if c['qualified_name'] == qualified_name)
        return {c['qualified_name']: c for c in change['review_candidates']}, change

    def test_changing_a_wrapper_affects_callers_of_everything_it_wraps(self):
        report = self.run_change(self.LIB, self.LIB.replace(
            'def wrapper(*args, **kwargs):\n        return func', 'def wrapper(*args, **kwargs):\n        print("x")\n        return func', 1))
        found, change = self.names(report, 'logged.wrapper')
        self.assertEqual(change['decorator_effect']['wrapped_functions'], ['save'])
        self.assertEqual({k: v['relationship'] for k, v in found.items()}, {'go': 'direct', 'main': 'indirect'})
        self.assertEqual(found['go']['via_decorator'], ['save'])
        self.assertNotIn('other', found)           # plain() is not decorated
        self.assertNotIn('wipe', found)            # erase() is wrapped by a different decorator

    def test_changing_a_decorator_factory_inner_wrapper_is_followed(self):
        old = self.LIB
        new = self.LIB.replace('        def wrapper(*args, **kwargs):\n            return func',
                               '        def wrapper(*args, **kwargs):\n            print("y")\n            return func')
        found, change = self.names(self.run_change(old, new), 'role.decorator.wrapper')
        self.assertEqual(change['decorator_effect']['wrapped_functions'], ['erase'])
        self.assertEqual(set(found), {'wipe'})

    def test_changing_the_decorator_function_itself_counts(self):
        new = self.LIB.replace('def logged(func):\n', 'def logged(func):\n    print("decorating")\n')
        found, change = self.names(self.run_change(self.LIB, new), 'logged')
        self.assertEqual(set(found), {'go', 'main'})

    def test_unexplained_calls_to_wrapped_methods_are_listed_as_weak_evidence(self):
        lib = ('import functools\n\n\ndef logged(func):\n    @functools.wraps(func)\n    def wrapper(*args, **kwargs):\n'
               '        return func(*args, **kwargs)\n    return wrapper\n\n\nclass Store:\n    @logged\n'
               '    def put(self, item):\n        return item\n')
        use = 'def go(thing):\n    return thing.put(1)\n'
        base = self.commit({'lib.py': lib, 'use.py': use})
        head = self.commit({'lib.py': lib.replace('return func(*args, **kwargs)', 'print(1)\n        return func(*args, **kwargs)')})
        report = self.run_report(base, head)
        change = next(c for c in report['changes'] if c['qualified_name'] == 'logged.wrapper')
        weak = change['decorator_effect']['name_matches']['Store.put']
        self.assertEqual([s['caller'] for s in weak['sites']], ['go'])
        self.assertEqual(report['summary']['decorator_name_match_sites'], 2)  # logged and logged.wrapper both report it

    def test_ordinary_function_change_adds_no_decorator_effect(self):
        new = self.LIB.replace('def plain(x):\n    return x', 'def plain(x):\n    return x + 1')
        found, change = self.names(self.run_change(self.LIB, new), 'plain')
        self.assertNotIn('decorator_effect', change)
        self.assertEqual(set(found), {'other'})

    def test_decorator_that_wraps_nothing_gives_no_effect(self):
        lib = self.LIB.replace('@logged\n', '')
        new = lib.replace('def logged(func):\n', 'def logged(func):\n    print("z")\n')
        _, change = self.names(self.run_change(lib, new), 'logged')
        self.assertNotIn('decorator_effect', change)


class ConstantAndRepairedFileTests(RepoCase):
    TEXT = ('LIMIT = 40\nOTHER = "x"\n\n\ndef truncate(value, width=None):\n    return value[:width or LIMIT]\n\n\n'
            'def shout(value):\n    return value.upper() + OTHER\n\n\ndef shadow(LIMIT):\n    return LIMIT\n')
    USE = 'from text import truncate, LIMIT\nimport text as t\n\n\ndef caller(v):\n    return truncate(v)\n\n\n' \
          'def far(v):\n    return v[:LIMIT]\n\n\ndef viamodule(v):\n    return v[:t.LIMIT]\n'

    def names(self, report):
        return {c['qualified_name']: c for c in report['changes']}

    def test_functions_reading_a_changed_constant_count_as_changed(self):
        base = self.commit({'text.py': self.TEXT, 'use.py': self.USE})
        head = self.commit({'text.py': self.TEXT.replace('LIMIT = 40', 'LIMIT = 48')})
        report = self.run_report(base, head, include_possible=True)
        changes = self.names(report)
        self.assertEqual(set(changes), {'truncate', 'far', 'viamodule'})     # not shout, not shadow
        self.assertEqual(changes['truncate']['reason'], 'reads_changed_constant')
        self.assertEqual(changes['far']['constants'], ['LIMIT'])
        self.assertEqual(report['constants'][0]['name'], 'LIMIT')
        self.assertEqual(sorted(report['constants'][0]['readers']), ['text.py::truncate', 'use.py::far', 'use.py::viamodule'])
        self.assertEqual([c['qualified_name'] for c in changes['truncate']['review_candidates']], ['caller'])
        self.assertEqual(report['gaps'], [])
        self.assertEqual(report['summary']['functions_via_constants'], 3)

    def test_constant_with_no_readers_stays_a_gap(self):
        base = self.commit({'text.py': 'UNUSED = 1\n\n\ndef f():\n    return 1\n'})
        head = self.commit({'text.py': 'UNUSED = 2\n\n\ndef f():\n    return 1\n'})
        report = self.run_report(base, head)
        self.assertEqual(report['changes'], [])
        self.assertEqual([g['type'] for g in report['gaps']], ['constant_without_readers'])

    def test_a_function_that_also_changed_is_marked_not_duplicated(self):
        base = self.commit({'text.py': self.TEXT})
        head = self.commit({'text.py': self.TEXT.replace('LIMIT = 40', 'LIMIT = 48').replace(
            'width or LIMIT]', 'width or LIMIT][:99]')})
        report = self.run_report(base, head)
        truncates = [c for c in report['changes'] if c['qualified_name'] == 'truncate']
        self.assertEqual(len(truncates), 1)
        self.assertEqual(truncates[0]['also_reads_changed_constants'], ['LIMIT'])
        self.assertNotIn('reason', truncates[0])

    def test_other_module_level_code_changing_beside_functions_is_reported(self):
        base = self.commit({'text.py': self.TEXT + 'registry = []\nregistry.append(1)\n'})
        head = self.commit({'text.py': self.TEXT.replace('return value.upper()', 'return value.lower()')
                            + 'registry = []\nregistry.append(2)\n'})
        self.assertEqual([g['type'] for g in self.run_report(base, head)['gaps']], ['module_level_code_changed'])

    def test_a_file_that_did_not_parse_before_lists_its_functions_as_added_but_flagged(self):
        base = self.commit({'pivot.py': 'def pivot(report, columns:\n    return 1\n', 'keep.py': 'def keep():\n    return 1\n'})
        head = self.commit({'pivot.py': 'def pivot(report, columns=()):\n    return 1\n'})
        report = self.run_report(base, head)
        self.assertEqual([(c['qualified_name'], c['change'], c['before_file_did_not_parse']) for c in report['changes']],
                         [('pivot', 'added', True)])
        self.assertEqual([g['type'] for g in report['gaps']], ['analysis_failed'])
        self.assertEqual(report['status'], 'partial')

    def test_a_file_that_stops_parsing_never_lists_deletions(self):
        keep = {'keep.py': 'def keep():\n    return 1\n'}
        base = self.commit({'pivot.py': 'def pivot(report, columns=()):\n    return 1\n', **keep})
        head = self.commit({'pivot.py': 'def pivot(report, columns:\n    return 1\n'})
        report = self.run_report(base, head)
        self.assertEqual(report['changes'], [])
        self.assertEqual(report['gaps'][0]['side'], 'after')
