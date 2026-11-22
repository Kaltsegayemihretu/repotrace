import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from repotrace.app import server as app
from tests.test_change_impact import CHAIN, RepoCase


class AppCase(RepoCase):
    def setUp(self):
        super().setUp()
        self.recent = Path(self._tmp.name) / 'recent.json'
        self.server, self.state = app.make_server(recent_file=self.recent, browse=lambda: '/picked/folder')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.state.cleanup()

    def call(self, method, path, body=None, headers=None, token=True, host=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.state.port, timeout=30)
        h = {'Host': host or f'127.0.0.1:{self.state.port}'}
        if token:
            h['X-RepoTrace-Token'] = self.state.token
        if body is not None:
            h['Content-Type'] = 'application/json'
        h.update(headers or {})
        conn.request(method, path, json.dumps(body) if body is not None else None, h)
        response = conn.getresponse()
        raw = response.read().decode('utf-8')
        conn.close()
        try:
            return response.status, json.loads(raw), response
        except ValueError:
            return response.status, raw, response

    def wait_for_job(self, job_id, seconds=60):
        end = time.time() + seconds
        while time.time() < end:
            _, data, _ = self.call('GET', f'/api/job?id={job_id}')
            if data['state'] != 'running':
                return data
            time.sleep(0.1)
        self.fail('job did not finish')

    def two_commits(self):
        base = self.commit(CHAIN)
        head = self.commit({'pricing.py': 'def calculate_total(price):\n    return price * 1.2\n'})
        return base, head


class MapTests(AppCase):
    """Explore a whole project: no commits, no second version."""

    def start_map(self, folder, **extra):
        status, data, _ = self.call('POST', '/api/map', {'repo': str(folder), **extra})
        return status, data

    def test_map_a_folder_then_walk_it_like_the_explore_tab(self):
        (self.repo / 'lib.py').write_text('def helper(x):\n    return x + 1\n')
        (self.repo / 'app.py').write_text('from lib import helper\n\n\ndef run(x):\n    return helper(x)\n')
        status, data = self.start_map(self.repo)
        self.assertEqual(status, 200)
        job = self.wait_for_job(data['job'])
        self.assertEqual(job['state'], 'done', job)
        _, view, _ = self.call('GET', f"/api/job?id={data['job']}")
        view = view['view']
        self.assertEqual((view['kind'], view['repository']), ('map', self.repo.name))
        self.assertEqual(view['overview']['functions'], 2)
        self.assertEqual([n['name'] for n in view['overview']['busiest']], ['helper'])
        _, found, _ = self.call('GET', f"/api/functions?job={data['job']}&q=helper&side=after")
        symbol = found['functions'][0]
        self.assertEqual(symbol['callers'], 1)
        _, near, _ = self.call('GET', f"/api/neighbors?job={data['job']}&id={symbol['id']}&direction=callers&side=after")
        self.assertEqual({n['name'] for n in near['nodes']}, {'helper', 'run'})
        _, code, _ = self.call('GET', f"/api/function?job={data['job']}&id={symbol['id']}&side=after")
        self.assertEqual(code['code']['lines'], ['def helper(x):', '    return x + 1'])
        self.assertEqual(self.call('GET', f"/api/export?id={data['job']}&token={self.state.token}")[0], 404)

    def test_a_folder_that_does_not_exist_is_a_clear_error(self):
        status, data = self.start_map(self.repo / 'nope')
        self.assertEqual(status, 400)
        self.assertIn('not found', data['error'])

    def test_a_folder_without_python_says_so(self):
        (self.repo / 'notes.txt').write_text('hello')
        status, data = self.start_map(self.repo)
        job = self.wait_for_job(data['job'])
        self.assertEqual((job['state'], job['error']['kind']), ('error', 'input'))
        self.assertIn('No Python files', job['error']['message'])

    def test_a_src_folder_is_detected_without_asking(self):
        (self.repo / 'src' / 'pkg').mkdir(parents=True)
        (self.repo / 'src' / 'pkg' / '__init__.py').write_text('')
        (self.repo / 'src' / 'pkg' / 'a.py').write_text('def f():\n    return 1\n')
        (self.repo / 'src' / 'pkg' / 'b.py').write_text('from pkg.a import f\n\n\ndef g():\n    return f()\n')
        job = self.wait_for_job(self.start_map(self.repo)[1]['job'])
        self.assertEqual(job['state'], 'done')
        _, data, _ = self.call('GET', f"/api/job?id={job['id']}")
        self.assertEqual(data['view']['settings']['source_roots'], ['src'])
        self.assertEqual([n['name'] for n in data['view']['overview']['busiest']], ['f'])


class LabTests(AppCase):
    """The guided screens served by the real app: the page, its files, and the analysis job behind them."""

    def lab_job(self, body):
        status, data, _ = self.call('POST', '/api/lab', body)
        self.assertEqual(status, 200, data)
        return self.wait_for_job(data['job'])

    def test_the_page_and_its_files_are_served_safely(self):
        status, page, response = self.call('GET', '/lab', token=False)
        self.assertEqual(status, 200)
        self.assertIn(f'name="rt-token" content="{self.state.token}"', page)
        self.assertIn("script-src 'self'", response.getheader('Content-Security-Policy'))
        self.assertNotIn('unsafe-inline', response.getheader('Content-Security-Policy').split('style-src')[0], 'no inline scripts')
        for name, kind in (('app.js', 'javascript'), ('picker.js', 'javascript'), ('explore.js', 'javascript'), ('explore.html', 'html'), ('style.css', 'css'), ('explore.css', 'css'), ('icon.svg', 'svg')):
            status, body, response = self.call('GET', f'/lab/{name}', token=False)
            self.assertEqual(status, 200, name)
            self.assertIn(kind, response.getheader('Content-Type'))
        for bad in ('/lab/../server.py', '/lab/index.html', '/lab/data.js', '/lab/%2e%2e/app/server.py'):
            self.assertEqual(self.call('GET', bad, token=False)[0], 404, bad)
        self.assertEqual(self.call('GET', '/lab', token=False, host='evil.example:80')[0], 403, 'a foreign Host header is refused')
        _, _, shell = self.call('GET', '/lab', token=False)
        self.assertIn("frame-src 'self'", shell.getheader('Content-Security-Policy'))
        _, _, frame = self.call('GET', '/lab/explore.html', token=False)
        self.assertIn("frame-ancestors 'self'", frame.getheader('Content-Security-Policy'), 'the explorer may be framed only by this app')
        self.assertNotIn('connect-src', frame.getheader('Content-Security-Policy'), 'the explorer never talks to the network')
        for gone in ('map.js', 'web.js', 'web.css', 'map.css'):
            self.assertEqual(self.call('GET', f'/lab/{gone}', token=False)[0], 404, gone + ' was removed')

    def test_a_comparison_comes_back_as_exactly_what_the_screens_read(self):
        base, head = self.two_commits()
        job = self.lab_job({'repo': str(self.repo), 'base': base, 'head': head})
        self.assertEqual(job['state'], 'done', job.get('error'))
        data = job['view']['data']
        self.assertEqual(data['project']['mode'], 'live')
        self.assertEqual(set(data['graphs']), {'main', data['reviews'][0]['graph']})
        review = data['reviews'][0]
        self.assertEqual({'changed', 'concerns', 'tests', 'removed_tests', 'gaps', 'history', 'label', 'base', 'head'} - set(review), set())
        self.assertEqual([c['name'] for c in review['changed']], ['calculate_total'])
        self.assertEqual(review['base'], base[:7], 'a commit id is shown short')
        graph = data['graphs'][review['graph']]
        self.assertTrue(all(len(l) == 5 for l in graph['links']))
        self.assertTrue(data['questions'])

    def test_exploring_needs_no_second_version_and_no_git(self):
        plain = Path(self._tmp.name) / 'plain'
        plain.mkdir()
        (plain / 'a.py').write_text('def helper(x):\n    return x\n\n\ndef run():\n    return helper(1)\n')
        job = self.lab_job({'repo': str(plain)})
        self.assertEqual(job['state'], 'done', job.get('error'))
        data = job['view']['data']
        self.assertEqual(data['reviews'], [])
        self.assertEqual(data['graphs']['main']['version'], 'working folder')
        self.assertEqual({n['q'] for n in data['graphs']['main']['nodes'] if n['k'] == 'fn'}, {'helper', 'run'})

    def test_bad_requests_get_clear_answers(self):
        base, head = self.two_commits()
        self.assertEqual(self.call('POST', '/api/lab', {'repo': '/no/such/folder'})[0], 400)
        status, data, _ = self.call('POST', '/api/lab', {'repo': str(self.repo), 'base': base})
        self.assertEqual(status, 400)
        self.assertIn('both versions', data['error'])
        self.assertEqual(self.call('POST', '/api/lab', {'repo': str(self.repo)}, token=False)[0], 401)
        empty = Path(self._tmp.name) / 'empty'
        empty.mkdir()
        job = self.lab_job({'repo': str(empty)})
        self.assertEqual(job['state'], 'error')
        self.assertIn('Python', job['error']['message'])

    def test_the_lab_job_never_changes_the_repository(self):
        base, head = self.two_commits()
        before = (self.git('status', '--porcelain'), self.git('rev-parse', 'HEAD'))
        self.lab_job({'repo': str(self.repo), 'base': base, 'head': head})
        self.assertEqual((self.git('status', '--porcelain'), self.git('rev-parse', 'HEAD')), before)


class SecurityTests(AppCase):
    def test_page_needs_a_correct_host_and_carries_a_token_and_csp(self):
        status, html, response = self.call('GET', '/', token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.state.token, html)
        self.assertIn("default-src 'none'", response.getheader('Content-Security-Policy'))
        self.assertEqual(self.call('GET', '/', token=False, host='evil.example.com')[0], 403)
        self.assertEqual(self.call('GET', '/', token=False, host=f'127.0.0.1.evil.com:{self.state.port}')[0], 403)

    def test_api_requires_the_token(self):
        self.assertEqual(self.call('GET', '/api/state', token=False)[0], 401)
        self.assertEqual(self.call('GET', '/api/state', headers={'X-RepoTrace-Token': 'wrong'})[0], 401)
        self.assertEqual(self.call('GET', '/api/state')[0], 200)
        self.assertEqual(self.call('POST', '/api/inspect', {'repo': str(self.repo)}, token=False)[0], 401)

    def test_foreign_origins_and_non_json_posts_are_refused(self):
        status, _, _ = self.call('POST', '/api/inspect', {'repo': str(self.repo)}, headers={'Origin': 'https://evil.example.com'})
        self.assertEqual(status, 403)
        conn = http.client.HTTPConnection('127.0.0.1', self.state.port, timeout=10)
        conn.request('POST', '/api/inspect', 'repo=x', {'Host': f'127.0.0.1:{self.state.port}',
                     'X-RepoTrace-Token': self.state.token, 'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual(conn.getresponse().status, 400)

    def test_oversized_bodies_and_unknown_paths(self):
        status, data, _ = self.call('POST', '/api/inspect', {'repo': 'x' * 200_000})
        self.assertEqual(status, 400)
        self.assertEqual(self.call('GET', '/nope')[0], 404)
        self.assertEqual(self.call('GET', '/api/job?id=missing')[0], 404)

    def test_export_needs_the_token_in_the_link(self):
        base, head = self.two_commits()
        _, data, _ = self.call('POST', '/api/analyze', {'repo': str(self.repo), 'base': base, 'head': head})
        self.wait_for_job(data['job'])
        status, _, _ = self.call('GET', f"/api/export?id={data['job']}", token=False)
        self.assertEqual(status, 401)
        status, html, response = self.call('GET', f"/api/export?id={data['job']}&token={self.state.token}", token=False)
        self.assertEqual(status, 200)
        self.assertIn('attachment', response.getheader('Content-Disposition'))
        self.assertIn('calculate_total', html)
        self.assertNotIn('<meta name="rt-live"', html)       # the exported file is a standalone viewer, not the live app


class WorkflowTests(AppCase):
    def test_inspect_lists_commits_branches_tags_and_suggestions(self):
        base, head = self.two_commits()
        self.git('tag', 'v1', base)
        self.git('branch', 'feature', base)
        status, info, _ = self.call('POST', '/api/inspect', {'repo': str(self.repo)})
        self.assertEqual(status, 200)
        self.assertEqual(info['head'], head)
        self.assertEqual([c['commit'] for c in info['commits']], [head, base])
        self.assertIn('feature', {b['name'] for b in info['branches']})
        self.assertEqual(info['tags'][0]['name'], 'v1')
        self.assertEqual(info['tags'][0]['commit'], base)
        self.assertEqual(info['source_roots']['roots'], ['.'])
        self.assertFalse(info['dirty'])
        self.assertEqual(json.loads(self.recent.read_text())[0]['name'], 'repo')

    def test_src_layout_is_detected_and_explained(self):
        self.commit({'src/pkg/__init__.py': '', 'src/pkg/a.py': 'def f():\n    return 1\n', 'tests/test_a.py': 'def test():\n    pass\n'})
        _, info, _ = self.call('POST', '/api/inspect', {'repo': str(self.repo)})
        self.assertEqual(info['source_roots']['roots'], ['src'])
        self.assertIn('src', info['source_roots']['reason'])

    def test_analyze_runs_in_the_background_and_reports_progress_then_a_view(self):
        base, head = self.two_commits()
        status, data, _ = self.call('POST', '/api/analyze', {'repo': str(self.repo), 'base': base, 'head': head,
                                                             'include_possible': True, 'source_roots': []})
        self.assertEqual(status, 200)
        result = self.wait_for_job(data['job'])
        self.assertEqual(result['state'], 'done')
        self.assertEqual(result['seen'][0], 'check')
        self.assertIn('compare', result['seen'])
        names = {c['name'] for c in result['view']['changes']}
        self.assertEqual(names, {'calculate_total'})
        self.assertEqual({c['name'] for c in result['view']['changes'][0]['candidates']}, {'place_order', 'checkout'})

    def test_friendly_errors_for_a_non_repo_a_bad_commit_and_missing_choices(self):
        status, data, _ = self.call('POST', '/api/inspect', {'repo': self._tmp.name})
        self.assertEqual(status, 400)
        self.assertIn('not a Git project', data['error'])
        status, data, _ = self.call('POST', '/api/inspect', {'repo': '/no/such/folder'})
        self.assertEqual(status, 400)
        base, head = self.two_commits()
        _, started, _ = self.call('POST', '/api/analyze', {'repo': str(self.repo), 'base': 'nope', 'head': head})
        failed = self.wait_for_job(started['job'])
        self.assertEqual(failed['state'], 'error')
        self.assertIn("Cannot resolve 'nope'", failed['error']['message'])
        self.assertIn('Check the spelling', failed['error']['message'])
        status, data, _ = self.call('POST', '/api/analyze', {'repo': str(self.repo), 'base': '', 'head': head})
        self.assertEqual(status, 400)
        self.assertIn('Choose a project and both commits', data['error'])

    def test_repository_with_no_commits_is_explained(self):
        status, data, _ = self.call('POST', '/api/inspect', {'repo': str(self.repo)})
        self.assertEqual(status, 400)
        self.assertIn('no commits', data['error'])

    def test_demo_project_works_end_to_end_and_is_removed_on_cleanup(self):
        status, data, _ = self.call('POST', '/api/demo', {})
        self.assertEqual(status, 200)
        demo = Path(data['repo'])
        self.assertTrue((demo / '.git').is_dir())
        _, info, _ = self.call('POST', '/api/inspect', {'repo': data['repo']})
        base, head = info['commits'][1]['commit'], info['commits'][0]['commit']
        _, started, _ = self.call('POST', '/api/analyze', {'repo': data['repo'], 'base': base, 'head': head})
        result = self.wait_for_job(started['job'])
        view = result['view']
        changes = {(c['name'], c['change']) for c in view['changes']}
        self.assertEqual(changes, {('calculate_total', 'modified'), ('old_discount', 'deleted')})
        forgotten = next(c for c in view['changes'] if c['name'] == 'old_discount')
        self.assertTrue(any('head still references' in f for cand in forgotten['candidates'] for f in cand['flags']))
        self.stop_demo_check = str(demo)

    def test_recents_skip_the_example_and_forget_folders_that_no_longer_exist(self):
        _, demo, _ = self.call('POST', '/api/demo', {})
        self.call('POST', '/api/inspect', {'repo': demo['repo']})
        self.assertEqual(self.state.recent(), [])
        self.commit(CHAIN)
        self.call('POST', '/api/inspect', {'repo': str(self.repo)})
        self.assertEqual([r['name'] for r in self.state.recent()], ['repo'])
        self.recent.write_text(json.dumps([{'path': '/gone/forever', 'name': 'gone'}] + json.loads(self.recent.read_text())))
        self.assertEqual([r['name'] for r in self.state.recent()], ['repo'])

    def finished_job(self):
        base, head = self.two_commits()
        _, started, _ = self.call('POST', '/api/analyze', {'repo': str(self.repo), 'base': base, 'head': head})
        return self.wait_for_job(started['job'])

    def test_explore_search_neighbors_and_function_details(self):
        result = self.finished_job()
        job = result['id']
        _, found, _ = self.call('GET', f'/api/functions?job={job}&q=place')
        self.assertEqual([f['name'] for f in found['functions']], ['place_order'])
        node = found['functions'][0]
        _, around, _ = self.call('GET', f"/api/neighbors?job={job}&id={node['id']}&direction=both&depth=1")
        self.assertEqual({(n['name'], n['layer']) for n in around['nodes']}, {('place_order', 0), ('checkout', -1), ('calculate_total', 1)})
        _, deeper, _ = self.call('GET', f"/api/neighbors?job={job}&id={node['id']}&direction=callers&depth=3")
        self.assertEqual({n['name'] for n in deeper['nodes']}, {'place_order', 'checkout'})
        _, detail, _ = self.call('GET', f"/api/function?job={job}&id={node['id']}")
        self.assertIn('def place_order', '\n'.join(detail['code']['lines']))
        self.assertEqual((detail['node']['callers'], detail['node']['callees']), (1, 1))
        _, older, _ = self.call('GET', f'/api/functions?job={job}&q=calculate&side=before')
        self.assertEqual(older['functions'][0]['name'], 'calculate_total')

    def test_explore_rejects_bad_requests_and_needs_the_token(self):
        job = self.finished_job()['id']
        self.assertEqual(self.call('GET', f'/api/functions?job={job}&q=x', token=False)[0], 401)
        self.assertEqual(self.call('GET', '/api/functions?job=nope&q=x')[0], 404)
        self.assertEqual(self.call('GET', f'/api/neighbors?job={job}&id=nope::x@1:0')[0], 404)
        self.assertEqual(self.call('GET', f'/api/neighbors?job={job}&id=x&direction=sideways')[0], 400)
        self.assertEqual(self.call('GET', f'/api/functions?job={job}&q=x&side=middle')[0], 404)

    def test_gallery_lists_the_builtin_suite_and_opens_a_comparison_end_to_end(self):
        _, data, _ = self.call('GET', '/api/examples')
        builtin = data['examples'][0]
        self.assertEqual(builtin['id'], 'builtin')
        self.assertEqual(len(builtin['comparisons']), 9)
        self.assertIn('parameters-changed', {c['id'] for c in builtin['comparisons']})
        status, opened, _ = self.call('POST', '/api/example', {'id': 'builtin', 'comparison': 'caller-forgotten'})
        self.assertEqual(status, 200)
        self.assertIn('legacy.py', opened['example']['shows'])
        self.assertTrue(opened['example']['steps'])
        _, started, _ = self.call('POST', '/api/analyze', {'repo': opened['repo'], 'base': opened['base'], 'head': opened['head'], 'source_roots': opened['source_roots']})
        view = self.wait_for_job(started['job'])['view']
        self.assertEqual([(c['name'], c['change']) for c in view['changes']], [('old_discount', 'deleted')])
        # the same repository serves every comparison; examples are never added to the recent list
        self.call('POST', '/api/inspect', {'repo': opened['repo']})
        self.assertEqual(self.state.recent(), [])
        self.assertEqual(self.call('POST', '/api/example', {'id': 'builtin', 'comparison': 'comments-only'})[1]['repo'], opened['repo'])

    def test_unknown_examples_are_refused_and_the_gallery_needs_the_token(self):
        self.assertEqual(self.call('POST', '/api/example', {'id': 'builtin', 'comparison': 'nope'})[0], 404)
        self.assertEqual(self.call('POST', '/api/example', {'id': 'ghost', 'comparison': 'x'})[0], 404)
        self.assertEqual(self.call('GET', '/api/examples', token=False)[0], 401)

    def test_example_folders_are_removed_when_the_app_stops(self):
        _, opened, _ = self.call('POST', '/api/example', {'id': 'builtin', 'comparison': 'function-changed'})
        folder = Path(opened['repo'])
        self.assertTrue(folder.exists())
        self.state.cleanup()
        self.assertFalse(folder.exists())

    def test_browse_returns_the_chosen_folder(self):
        self.assertEqual(self.call('POST', '/api/browse', {})[1], {'path': '/picked/folder'})

    def test_the_app_never_changes_the_repository(self):
        base, head = self.two_commits()
        before = (self.git('status', '--porcelain'), self.git('rev-parse', 'HEAD'), (self.repo / '.git' / 'index').read_bytes())
        self.call('POST', '/api/inspect', {'repo': str(self.repo)})
        _, started, _ = self.call('POST', '/api/analyze', {'repo': str(self.repo), 'base': base, 'head': head})
        self.wait_for_job(started['job'])
        after = (self.git('status', '--porcelain'), self.git('rev-parse', 'HEAD'), (self.repo / '.git' / 'index').read_bytes())
        self.assertEqual(before, after)

    def test_progress_callback_never_changes_the_report(self):
        from repotrace.changes.change_impact import analyze_change_impact
        base, head = self.two_commits()
        quiet = analyze_change_impact(self.repo, base, head)
        events = []
        loud = analyze_change_impact(self.repo, base, head, progress=lambda s, d='': events.append(s))
        self.assertEqual(quiet['changes'], loud['changes'])
        self.assertEqual(events[0], 'check')
        self.assertIn('impact', events)

    def test_friendly_messages(self):
        self.assertIn('not a Git project', app.friendly(Exception('The folder is not a Git working tree.')))
        self.assertIn('Check the spelling', app.friendly(Exception("Cannot resolve 'x' to a commit in this repository.")))
        self.assertIn('no commits', app.friendly(Exception("git rev-parse failed: ambiguous argument 'HEAD'")))


if __name__ == '__main__':
    unittest.main()
