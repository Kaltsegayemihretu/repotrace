"""Import-resolution regression tests. Fixtures are read, never imported."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import FrozenImporter
from pathlib import Path
from unittest.mock import patch

from repotrace.reading.analyzer import analyze_file
from repotrace.mapping.project_resolver import resolve_project_calls
from repotrace.mapping.repository import analyze_repository
from tests import ROOT


class ProjectResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, source):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')
        return path

    def scan(self, files, **kwargs):
        for name, source in files.items():
            self.write(name, source)
        report = analyze_repository(self.root, **kwargs)
        ids = {f['module_id'] for f in report['files']}
        ids.update(s['id'] for f in report['files'] for s in f['symbols'])
        for file in report['files']:
            for edge in file['edges']:
                self.assertIn(edge['source'], ids)
                self.assertIn(edge['target'], ids)
            for call in file['calls']:
                edges = [e for e in file['edges'] if e.get('call_id') == call['id']]
                self.assertEqual(len(edges), int(call['target_id'] is not None))
        return report

    def call(self, report, file='app.py', expression=None):
        calls = next(f['calls'] for f in report['files'] if f['file'] == file)
        return next(c for c in calls if expression is None or c['expression'] == expression)

    def test_direct_import_and_evidence(self):
        report = self.scan({'lib.py': 'def f(): pass\n',
                            'app.py': 'from lib import f\ndef run(): f()\n'})
        call = self.call(report)
        self.assertEqual(call['target_file'], 'lib.py')
        self.assertEqual(call['resolution'], 'LIKELY')
        self.assertEqual(call['import_evidence']['statement'], 'from lib import f')
        self.assertEqual(call['import_evidence']['span']['line'], 1)
        self.assertEqual(report['summary']['import_linked_calls'], 1)
        self.assertEqual(len(report['project_index']['definitions']), 2)

    def test_import_forms(self):
        cases = [
            ('from lib import f as save', 'save'),
            ('import lib', 'lib.f'), ('import lib as m', 'm.f'),
            ('import pkg.lib', 'pkg.lib.f'), ('import pkg.lib as m', 'm.f'),
            ('from pkg import lib', 'lib.f'), ('from pkg import lib as m', 'm.f')]
        for statement, expr in cases:
            with self.subTest(statement=statement):
                report = self.scan({'lib.py': 'def f(): pass\n', 'pkg/lib.py': 'def f(): pass\n',
                                    'app.py': f'{statement}\ndef run(): {expr}()\n'})
                self.assertIsNotNone(self.call(report)['target_id'])

    def test_relative_imports(self):
        for statement, expr in [('from .lib import f', 'f'),
                                ('from . import lib', 'lib.f'),
                                ('from ..pkg.lib import f', 'f')]:
            with self.subTest(statement=statement):
                report = self.scan({'outer/pkg/lib.py': 'def f(): pass\n',
                                    'outer/pkg/app.py': f'{statement}\ndef run(): {expr}()\n'})
                self.assertEqual(self.call(report, 'outer/pkg/app.py')['target_file'], 'outer/pkg/lib.py')

    def test_package_init_definition(self):
        report = self.scan({'pkg/__init__.py': 'def f(): pass\n',
                            'app.py': 'from pkg import f\ndef run(): f()\n'})
        self.assertEqual(self.call(report)['target_file'], 'pkg/__init__.py')

    def test_relative_import_in_init(self):
        report = self.scan({'pkg/__init__.py': 'from .lib import f\ndef run(): f()\n',
                            'pkg/lib.py': 'def f(): pass\n'})
        self.assertIsNotNone(self.call(report, 'pkg/__init__.py')['target_id'])

    def test_explicit_src_root(self):
        files = {'src/lib.py': 'def f(): pass\n',
                 'src/app.py': 'from lib import f\ndef run(): f()\n'}
        self.assertIsNone(self.call(self.scan(files), 'src/app.py')['target_id'])
        report = self.scan(files, source_roots=['src'])
        self.assertEqual(self.call(report, 'src/app.py')['target_file'], 'src/lib.py')

    def test_ambiguous_roots(self):
        report = self.scan({'one/lib.py': 'def f(): pass\n', 'two/lib.py': 'def f(): pass\n',
                            'one/app.py': 'from lib import f\ndef run(): f()\n'},
                           source_roots=['one', 'two'])
        self.assertEqual(self.call(report, 'one/app.py')['reason_code'], 'AMBIGUOUS_MODULE')

    def test_file_package_collision(self):
        report = self.scan({'lib.py': 'def f(): pass\n', 'lib/__init__.py': 'def f(): pass\n',
                            'app.py': 'from lib import f\ndef run(): f()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'AMBIGUOUS_MODULE')

    def test_parent_must_be_package(self):
        report = self.scan({'pkg.py': '', 'pkg/lib.py': 'def f(): pass\n',
                            'app.py': 'from pkg.lib import f\ndef run(): f()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'PARENT_NOT_PACKAGE')

    def test_regular_package_blocks_other_root_child(self):
        report = self.scan({'one/pkg/__init__.py': '', 'two/pkg/lib.py': 'def f(): pass\n',
                            'one/app.py': 'from pkg.lib import f\ndef run(): f()\n'},
                           source_roots=['one', 'two'])
        self.assertEqual(self.call(report, 'one/app.py')['reason_code'], 'PACKAGE_PATH_CONFLICT')

    def test_shadowed_imports(self):
        for body in ['def run(f): f()', 'def run():\n    f = 1\n    f()',
                     'f = 1\ndef run(): f()']:
            with self.subTest(body=body):
                report = self.scan({'lib.py': 'def f(): pass\n',
                                    'app.py': 'from lib import f\n' + body + '\n'})
                self.assertIsNone(self.call(report)['target_id'])

    def test_conditional_import(self):
        report = self.scan({'lib.py': 'def f(): pass\n',
                            'app.py': 'if enabled:\n    from lib import f\ndef run(): f()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'CONDITIONAL_IMPORT')

    def test_local_import_order(self):
        for body, linked in [('from lib import f; f()', True),
                             ('f(); from lib import f', False)]:
            with self.subTest(body=body):
                report = self.scan({'lib.py': 'def f(): pass\n', 'app.py': f'def run(): {body}\n'})
                self.assertEqual(self.call(report)['target_id'] is not None, linked)

    def test_closure_import(self):
        report = self.scan({'lib.py': 'def f(): pass\n',
                            'app.py': 'def outer():\n    from lib import f\n    def inner(): f()\n'})
        self.assertIsNotNone(self.call(report)['target_id'])

    def test_method_skips_class_namespace(self):
        report = self.scan({'lib.py': 'def f(): pass\n',
                            'app.py': 'from lib import f\nclass A:\n    f = 1\n    def run(self): f()\n'})
        self.assertIsNotNone(self.call(report)['target_id'])

    def test_unknown_exports(self):
        sources = ['if enabled:\n    def f(): pass\n', '@decorate\ndef f(): pass\n',
                   'def f(): pass\nf = other\n', 'from elsewhere import f\n',
                   'from elsewhere import *\ndef f(): pass\n', 'class f: pass\n']
        for source in sources:
            with self.subTest(source=source):
                report = self.scan({'lib.py': source, 'app.py': 'from lib import f\ndef run(): f()\n'})
                self.assertIsNone(self.call(report)['target_id'])

    def test_reexport_cycle(self):
        report = self.scan({'a.py': 'from b import f\n', 'b.py': 'from a import f\n',
                            'app.py': 'from a import f\ndef run(): f()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'REEXPORT_CYCLE')

    def test_direct_function_import_cycle(self):
        report = self.scan({'a.py': 'from b import g\ndef f(): g()\n',
                            'b.py': 'from a import f\ndef g(): f()\n'})
        self.assertEqual(report['summary']['import_linked_calls'], 2)

    def test_package_import_does_not_establish_child(self):
        report = self.scan({'pkg/lib.py': 'def f(): pass\n',
                            'app.py': 'import pkg\ndef run(): pkg.lib.f()\n'})
        self.assertIsNone(self.call(report)['target_id'])

    def test_package_child_shadow(self):
        report = self.scan({'pkg/__init__.py': 'lib = object()\n', 'pkg/lib.py': 'def f(): pass\n',
                            'app.py': 'import pkg.lib\ndef run(): pkg.lib.f()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'PACKAGE_ATTRIBUTE_SHADOWED')

    def test_dynamic_package_path(self):
        report = self.scan({'pkg/__init__.py': '__path__ = []\n', 'pkg/lib.py': 'def f(): pass\n',
                            'app.py': 'from pkg.lib import f\ndef run(): f()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'DYNAMIC_PACKAGE_PATH')

    def test_broken_module(self):
        report = self.scan({'lib.py': 'def broken(', 'app.py': 'from lib import f\ndef run(): f()\n'})
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(self.call(report)['reason_code'], 'MODULE_ANALYSIS_FAILED')

    def test_external_import(self):
        report = self.scan({'app.py': 'from missing_library import f\ndef run(): f()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'MODULE_NOT_FOUND')

    def test_builtin_module(self):
        report = self.scan({'sys.py': 'def f(): pass\n', 'app.py': 'from sys import f\ndef run(): f()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'BUILTIN_MODULE')

    @unittest.skipUnless(FrozenImporter.find_spec('os'), 'os is not frozen in this interpreter')
    def test_frozen_module(self):
        report = self.scan({'os.py': 'def f(): pass\n', 'app.py': 'from os import f\ndef run(): f()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'FROZEN_MODULE')

    def test_object_methods_and_computed_targets_stay_unknown(self):
        report = self.scan({'lib.py': 'class Service:\n    def save(self): pass\n',
                            'app.py': 'from lib import Service\ndef run(user, factory):\n'
                            '    user.save()\n    factory()()\n    Service()\n'})
        self.assertEqual(report['summary']['linked_calls'], 0)

    def test_excluded_target_stays_unknown(self):
        report = self.scan({'vendor/lib.py': 'def f(): pass\n',
                            'app.py': 'from vendor.lib import f\ndef run(): f()\n'},
                           exclude_dirs=['vendor'])
        self.assertEqual(self.call(report)['reason_code'], 'MODULE_NOT_FOUND')

    def test_relative_outside_package(self):
        report = self.scan({'lib.py': 'def f(): pass\n', 'app.py': 'from .lib import f\ndef run(): f()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'RELATIVE_IMPORT_OUTSIDE_PACKAGE')

    def test_module_getattr(self):
        report = self.scan({'lib.py': 'def __getattr__(name): pass\n',
                            'app.py': 'from lib import f\ndef run(): f()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'DYNAMIC_EXPORT')

    def test_attribute_mutation_blocks_local_and_imported_callers(self):
        report = self.scan({'lib.py': 'def f(): pass\ndef local(): f()\n',
                            'patcher.py': 'import lib\nlib.f = replacement\n',
                            'app.py': 'from lib import f\ndef run(): f()\n'})
        for file in ['app.py', 'lib.py']:
            self.assertEqual(self.call(report, file)['reason_code'], 'ATTRIBUTE_MUTATION')
        self.assertEqual(report['summary']['linked_calls'], 0)

    def test_annotation_only_does_not_replace_module_binding(self):
        report = self.scan({'lib.py': 'def f(): pass\nf: object\n',
                            'app.py': 'from lib import f\nf: object\ndef run(): f()\n'})
        self.assertIsNotNone(self.call(report)['target_id'])

    def test_function_annotation_shadows(self):
        report = self.scan({'lib.py': 'def f(): pass\n',
                            'app.py': 'from lib import f\ndef run():\n    f: object\n    f()\n'})
        self.assertIsNone(self.call(report)['target_id'])

    def test_deferred_global_definition(self):
        report = self.scan({'lib.py': 'def install():\n    global f\n    def f(): pass\n'
                            'def run(): f()\n', 'app.py': 'from lib import f\ndef run(): f()\n'})
        self.assertEqual(report['summary']['linked_calls'], 0)

    def test_deferred_global_import(self):
        report = self.scan({'lib.py': 'def f(): pass\n', 'app.py':
                            'def install():\n    global f\n    from lib import f\ndef run(): f()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'CONDITIONAL_IMPORT')

    def test_global_reassignment(self):
        report = self.scan({'lib.py': 'def f(): pass\n',
                            'app.py': 'from lib import f\ndef change():\n    global f\n    f = other\n'
                            'def run(): f()\n'})
        self.assertIsNone(self.call(report)['target_id'])

    def test_async_and_lambda(self):
        report = self.scan({'lib.py': 'async def f(): pass\n',
                            'app.py': 'from lib import f\nasync def run(): await f()\ng = lambda: f()\n'})
        self.assertEqual(report['summary']['import_linked_calls'], 2)

    def test_module_level_calls_recorded_unresolved(self):
        report = self.scan({'lib.py': 'def f(): pass\n', 'app.py': 'from lib import f\nf()\n'})
        self.assertIsNone(self.call(report)['target_id'])

    def test_no_execution(self):
        marker = self.root / 'NEVER_CREATE'
        report = self.scan({'lib.py': f'open({str(marker)!r}, "w").close()\ndef f(): pass\n',
                            'app.py': 'from lib import f\ndef run(): f()\n'})
        self.assertFalse(marker.exists())
        self.assertIsNotNone(self.call(report)['target_id'])

    def test_single_file_has_no_project_context(self):
        self.write('lib.py', 'def f(): pass\n')
        path = self.write('app.py', 'from lib import f\ndef run(): f()\n')
        self.assertIsNone(analyze_file(path)['calls'][0]['target_id'])

    def test_repeat_pass_is_stable(self):
        report = self.scan({'lib.py': 'def f(): pass\n', 'app.py': 'from lib import f\ndef run(): f()\n'})
        original = copy.deepcopy(report)
        resolve_project_calls(report)
        self.assertEqual(report, original)

    def test_invalid_source_roots(self):
        for roots in [[], 'src', ['missing'], ['..'], ['/'], [None]]:
            with self.subTest(roots=roots):
                self.assertEqual(analyze_repository(self.root, source_roots=roots)['status'], 'error')

    def test_failed_project_pass_rolls_back_partial_mutation(self):
        self.write('app.py', 'def f(): pass\ndef run(): f()\n')
        baseline = analyze_repository(self.root, resolve_imports=False)
        def fail(report):
            report['files'][0]['calls'][0]['target_id'] = 'invented'
            report['files'][0]['edges'].clear()
            raise ValueError('test failure')
        with patch('repotrace.mapping.repository.resolve_project_calls', side_effect=fail):
            report = analyze_repository(self.root)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['files'], baseline['files'])

    def test_cli_source_root_and_local_only(self):
        self.write('src/lib.py', 'def f(): pass\n')
        self.write('src/app.py', 'from lib import f\ndef run(): f()\n')
        command = [sys.executable, '-B', '-m', 'repotrace.mapping.repository',
                   str(self.root), '--source-root', 'src']
        for extra, expected in [([], 1), (['--local-only'], 0)]:
            process = subprocess.run(command + extra, capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout)['summary']['linked_calls'], expected)


if __name__ == '__main__':
    unittest.main()
