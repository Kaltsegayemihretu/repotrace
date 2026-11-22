import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repotrace.reading.analyzer import CodeExtractor, analyze_file, write_json_report
from repotrace.mapping.repository import analyze_repository
from repotrace.reading.resolver import resolve_local_calls
from tests import ROOT


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()

    def write(self, name, source='x = 1\n'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source if isinstance(source, bytes) else source.encode('utf-8'))
        return path

    def assert_graph_valid(self, report):
        files = [f for f in report['files'] if f['status'] == 'ok']
        ids = [f['module_id'] for f in files] + [s['id'] for f in files for s in f['symbols']]
        calls = [c['id'] for f in files for c in f['calls']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(calls), len(set(calls)))
        for f in files:
            for edge in f['edges']:
                self.assertIn(edge['source'], ids)
                self.assertIn(edge['target'], ids)
                if edge['kind'] == 'CALLS':
                    self.assertIn(edge['call_id'], calls)
            for call in f['calls']:
                self.assertIn(call['enclosing_symbol_id'], ids)
                if call['target_id']:
                    self.assertIn(call['target_id'], ids)

    def test_same_filename_and_symbols_are_separate(self):
        for name in ['payments/utils.py', 'users/utils.py']:
            self.write(name, 'def save(): pass\ndef run(): save()\n')
        report = analyze_repository(self.root)
        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['summary']['linked_calls'], 2)
        self.assertEqual([f['file'] for f in report['files']],
                         ['payments/utils.py', 'users/utils.py'])
        for item in report['files']:
            self.assertTrue(item['calls'][0]['target_id'].startswith(item['file'] + '::'))
            self.assertEqual(item['file_key_kind'], 'repository_relative')
        self.assert_graph_valid(report)

    def test_imports_remain_unresolved_across_files(self):
        self.write('pricing.py', 'def calculate_total(x): return x * 2\n')
        self.write('orders.py', 'from pricing import calculate_total\ndef order(): calculate_total(50)\n')
        report = analyze_repository(self.root, resolve_imports=False)
        self.assertEqual(report['summary']['calls'], 1)
        self.assertEqual(report['summary']['linked_calls'], 0)
        self.assertEqual(report['files'][0]['imports'][0]['module'], 'pricing')
        self.assertEqual(report['files'][0]['calls'][0]['resolution'], 'UNRESOLVED')
        self.assert_graph_valid(report)

    def test_file_errors_do_not_stop_other_files(self):
        self.write('good.py', 'def good(): pass\n')
        self.write('bad.py', 'def broken(')
        report = analyze_repository(self.root)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['summary']['files_analyzed'], 1)
        self.assertEqual(report['summary']['files_failed'], 1)
        self.assertEqual(report['summary']['symbols'], 1)
        self.assertTrue(report['files'][0]['diagnostics'])

    def test_all_files_failed(self):
        self.write('bad.py', 'def broken(')
        self.assertEqual(analyze_repository(self.root)['status'], 'error')

    def test_default_and_custom_exclusions(self):
        for name in ['.git/a.py', '.venv/b.py', 'node_modules/c.py', 'vendor/d.py',
                     'nested/build/e.py', 'src/app.py', 'tests/test_app.py', '.tools/check.py']:
            self.write(name)
        self.write('notes.txt')
        report = analyze_repository(self.root, exclude_dirs=['vendor'])
        self.assertEqual([f['file'] for f in report['files']],
                         ['.tools/check.py', 'src/app.py', 'tests/test_app.py'])
        self.assertEqual(report['summary']['skipped_entries'], 5)

    def test_empty_and_invalid_folders(self):
        empty = analyze_repository(self.root)
        self.assertEqual(empty['status'], 'ok')
        self.assertEqual(empty['summary']['files_discovered'], 0)
        self.assertEqual(empty['diagnostics'][0]['type'], 'NoPythonFiles')
        self.assertEqual(analyze_repository(self.root / 'missing')['status'], 'error')
        self.assertEqual(analyze_repository(self.write('file.py'))['status'], 'error')

    def test_deterministic_order_and_data(self):
        for name in ['z.py', 'b/c.py', 'a.py']:
            self.write(name)
        first = analyze_repository(self.root)
        self.assertEqual(first, analyze_repository(self.root))
        self.assertEqual([f['file'] for f in first['files']], ['a.py', 'b/c.py', 'z.py'])

    def test_symlinks_are_not_followed(self):
        self.write('safe.py')
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / 'external.py'
            external.write_text('def external(): pass\n')
            (self.root / 'linked.py').symlink_to(external)
            (self.root / 'outside').symlink_to(outside, target_is_directory=True)
            (self.root / 'loop').symlink_to(self.root, target_is_directory=True)
            (self.root / 'broken.py').symlink_to(self.root / 'absent')
            report = analyze_repository(self.root)
        self.assertEqual([f['file'] for f in report['files']], ['safe.py'])
        self.assertEqual(len(report['skipped']), 4)
        self.assertTrue(all(s['reason'] == 'symbolic_link' for s in report['skipped']))

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX only')
    def test_fifo_is_skipped_without_opening(self):
        os.mkfifo(self.root / 'pipe.py')
        report = analyze_repository(self.root)
        self.assertEqual(report['summary']['files_discovered'], 0)
        self.assertEqual(report['skipped'][0]['reason'], 'not_regular_file')

    def test_directory_read_failure_is_visible(self):
        self.write('good.py')
        def fake_walk(root, **kwargs):
            kwargs['onerror'](PermissionError(13, 'Permission denied', str(root / 'locked')))
            yield str(root), [], ['good.py']
        with patch('repotrace.mapping.repository.os.walk', side_effect=fake_walk):
            report = analyze_repository(self.root)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['diagnostics'][0]['path'], 'locked')
        self.assertEqual(report['summary']['files_analyzed'], 1)

    def test_no_execution_and_encoding(self):
        marker = self.root / 'SHOULD_NOT_EXIST'
        self.write('danger.py', f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
        self.write('encoded.py', b'# coding: latin-1\r\n# caf\xe9\r\nx = 1\r\n')
        report = analyze_repository(self.root)
        self.assertEqual(report['status'], 'ok')
        self.assertFalse(marker.exists())
        self.assertIn('café', report['files'][1]['source'])

    def test_per_file_size_limit(self):
        self.write('small.py', 'x=1\n')
        self.write('large.py', '#' * 21)
        report = analyze_repository(self.root, max_file_bytes=20)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['summary']['files_failed'], 1)
        self.assertIn('20-byte', report['files'][0]['diagnostics'][0]['message'])

    def test_invalid_settings(self):
        for settings in [{'max_file_bytes': 0}, {'exclude_dirs': ['../outside']},
                         {'exclude_dirs': 'venv'}]:
            with self.subTest(settings=settings):
                report = analyze_repository(self.root, **settings)
                self.assertEqual(report['status'], 'error')
                self.assertEqual(report['diagnostics'][0]['stage'], 'input')

    def test_relative_ids_survive_project_relocation(self):
        self.write('one/pkg/mod.py', 'def f(): pass\n')
        self.write('two/pkg/mod.py', 'def f(): pass\n')
        one = analyze_repository(self.root / 'one')['files'][0]
        two = analyze_repository(self.root / 'two')['files'][0]
        self.assertEqual(one, two)

    def test_single_file_root_and_absolute_fallback(self):
        first = self.write('one/utils.py', 'def f(): pass\n')
        second = self.write('two/utils.py', 'def f(): pass\n')
        report = analyze_file(first, root=self.root)
        self.assertEqual(report['file'], 'one/utils.py')
        self.assertTrue(report['symbols'][0]['id'].startswith('one/utils.py::'))
        self.assertNotEqual(analyze_file(first)['module_id'], analyze_file(second)['module_id'])
        outside = analyze_file(first, root=self.root / 'two')
        self.assertEqual(outside['status'], 'error')
        self.assertNotIn('source', outside)

    def test_same_line_and_fstring_call_ids(self):
        self.write('app.py', 'def f(): pass\ndef g(): pass\ndef run():\n    f(); f()\n    return f"{f()} {g()}"\n')
        report = analyze_repository(self.root)
        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['summary']['calls'], 4)
        self.assert_graph_valid(report)

    def test_identical_ast_spans_are_unique_and_unresolved(self):
        source = 'def f(): pass\ndef g(): pass\ndef run():\n    f(); g()\n'
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        for attr in ('lineno', 'col_offset', 'end_lineno', 'end_col_offset'):
            setattr(calls[1], attr, getattr(calls[0], attr))
        extractor = CodeExtractor(source, 'same_span.py')
        extractor.visit(tree)
        report = {'file': 'same_span.py', 'symbols': extractor.symbols,
                  'calls': extractor.calls, 'edges': extractor.edges}
        resolve_local_calls(tree, source, report)
        self.assertEqual(len({c['id'] for c in report['calls']}), 2)
        self.assertTrue(all(c['target_id'] is None for c in report['calls']))
        self.assertTrue(all('share this source span' in c['reason'] for c in report['calls']))

    def test_failed_resolution_leaves_no_partial_graph(self):
        path = self.write('app.py', 'def f(): pass\ndef run(): f()\n')
        with patch('repotrace.reading.analyzer.resolve_local_calls', side_effect=ValueError('test failure')):
            report = analyze_file(path, root=self.root)
        self.assertEqual(report['status'], 'error')
        for key in ['symbols', 'calls', 'imports', 'edges']:
            self.assertEqual(report[key], [])

    def test_explicit_report_overwrite(self):
        output = self.root / 'report.json'
        write_json_report({'first': True}, output)
        with self.assertRaises(FileExistsError):
            write_json_report({'second': True}, output)
        self.assertEqual(json.loads(output.read_text()), {'first': True})
        write_json_report({'second': True}, output, overwrite=True)
        self.assertEqual(json.loads(output.read_text()), {'second': True})

    def test_report_protects_source_and_links(self):
        source = self.write('app.py', 'original = True\n')
        with self.assertRaises(ValueError):
            write_json_report({}, source, overwrite=True)
        linked = self.root / 'linked.json'
        linked.symlink_to(source)
        with self.assertRaises(ValueError):
            write_json_report({}, linked, overwrite=True)
        hard = self.root / 'hard.json'
        os.link(source, hard)
        with self.assertRaises(ValueError):
            write_json_report({}, hard, overwrite=True)
        self.assertEqual(source.read_text(), 'original = True\n')

    def test_cli_and_exit_codes(self):
        self.write('app.py', 'def f(): pass\n')
        script = 'repotrace.mapping.repository'
        output = self.root / 'report.json'
        def run(*extra):
            return subprocess.run([sys.executable, '-B', '-m', script, str(self.root),
                                   '--output', str(output), *extra], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(run().returncode, 0)
        self.assertEqual(json.loads(output.read_text())['summary']['files_analyzed'], 1)
        self.assertEqual(run().returncode, 2)
        self.write('broken.py', 'def broken(')
        self.assertEqual(run('--overwrite').returncode, 1)
        self.assertEqual(json.loads(output.read_text())['status'], 'partial')

    def test_demo(self):
        report = analyze_repository(ROOT / 'samples' / 'demo_repository')
        self.assertEqual(report['status'], 'ok')
        for key, expected in [('files_analyzed', 4), ('symbols', 6), ('calls', 3),
                              ('linked_calls', 3), ('unresolved_calls', 0)]:
            self.assertEqual(report['summary'][key], expected)
        self.assert_graph_valid(report)


if __name__ == '__main__':
    unittest.main()
