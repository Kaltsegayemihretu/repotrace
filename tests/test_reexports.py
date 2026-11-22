"""Repository-agnostic import/export fixtures; never import analyzed source."""
import ast
import copy
import tempfile
import unittest
from pathlib import Path

from repotrace.mapping.project_resolver import ProjectIndex, Unresolved, resolve_project_calls
from repotrace.mapping.repository import analyze_repository


class ReexportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def scan(self, files, **kwargs):
        for name, source in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding='utf-8')
        report = analyze_repository(self.root, **kwargs)
        self.assertEqual(report['status'], 'ok', report.get('diagnostics'))
        return report

    def call(self, report, expression=None, file='app.py'):
        return next(c for f in report['files'] if f['file'] == file
                    for c in f['calls'] if expression is None or c['expression'] == expression)

    def test_explicit_function_reexport_chain(self):
        report = self.scan({'storage.py': 'def save(): pass\n',
                            'domain.py': 'from storage import save as persist\n',
                            'api.py': 'from domain import persist as store\n',
                            'app.py': 'from api import store as action\ndef run(): action()\n'})
        call = self.call(report)
        self.assertEqual(call['target_file'], 'storage.py')
        self.assertEqual(call['resolution'], 'LIKELY')
        self.assertEqual([e['file'] for e in call['import_chain']], ['app.py', 'api.py', 'domain.py'])
        self.assertEqual([e['bound_name'] for e in call['import_chain']], ['action', 'store', 'persist'])
        self.assertEqual(call['import_evidence']['file'], 'app.py')
        edge = next(e for f in report['files'] for e in f['edges'] if e.get('call_id') == call['id'])
        self.assertEqual(edge['import_chain'], call['import_chain'])

    def test_package_export_attribute(self):
        report = self.scan({'pkg/__init__.py': 'from .helpers import save\n',
                            'pkg/helpers.py': 'def save(): pass\n',
                            'app.py': 'import pkg\ndef run(): pkg.save()\n'})
        self.assertEqual(self.call(report)['target_file'], 'pkg/helpers.py')

    def test_exported_module_alias(self):
        report = self.scan({'storage.py': 'def save(): pass\n',
                            'api.py': 'import storage as backend\n',
                            'app.py': 'from api import backend as db\ndef run(): db.save()\n'})
        self.assertEqual(self.call(report)['target_file'], 'storage.py')
        self.assertEqual(len(self.call(report)['import_chain']), 2)

    def test_module_export_from_attribute(self):
        report = self.scan({'storage.py': 'def save(): pass\n',
                            'api.py': 'import storage as backend\n',
                            'app.py': 'import api\ndef run(): api.backend.save()\n'})
        self.assertEqual(self.call(report)['target_file'], 'storage.py')

    def test_unaliased_dotted_module_reexport(self):
        report = self.scan({'pkg/storage.py': 'def save(): pass\n',
                            'api.py': 'import pkg.storage\n',
                            'app.py': 'import api\ndef run(): api.pkg.storage.save()\n'})
        self.assertEqual(self.call(report)['target_file'], 'pkg/storage.py')

    def test_package_exposes_child_module(self):
        report = self.scan({'pkg/__init__.py': 'from . import storage\n',
                            'pkg/storage.py': 'def save(): pass\n',
                            'app.py': 'import pkg\ndef run(): pkg.storage.save()\n'})
        self.assertEqual(self.call(report)['target_file'], 'pkg/storage.py')

    def test_importing_explicit_child_with_matching_package_binding(self):
        report = self.scan({'pkg/__init__.py': 'from . import storage\n',
                            'pkg/storage.py': 'def save(): pass\n',
                            'app.py': 'import pkg.storage\ndef run(): pkg.storage.save()\n'})
        self.assertEqual(self.call(report)['target_file'], 'pkg/storage.py')

    def test_matching_package_child_alias(self):
        report = self.scan({'pkg/__init__.py': 'import pkg.storage as storage\n',
                            'pkg/storage.py': 'def save(): pass\n',
                            'app.py': 'from pkg.storage import save\ndef run(): save()\n'})
        self.assertEqual(self.call(report)['target_file'], 'pkg/storage.py')

    def test_imported_child_alias_and_parent_relative_reexport(self):
        report = self.scan({'pkg/__init__.py': 'from .domain import save\n',
                            'pkg/domain/__init__.py': 'from ..storage import save\n',
                            'pkg/storage.py': 'def save(): pass\n',
                            'app.py': 'from pkg import save\ndef run(): save()\n'})
        self.assertEqual(self.call(report)['target_file'], 'pkg/storage.py')
        self.assertEqual(len(self.call(report)['import_chain']), 3)

    def test_reexport_cycle_explained(self):
        report = self.scan({'a.py': 'from b import f\n', 'b.py': 'from a import f\n',
                            'app.py': 'from a import f\ndef run(): f()\n'})
        self.assertIsNone(self.call(report)['target_id'])
        self.assertEqual(self.call(report)['reason_code'], 'REEXPORT_CYCLE')

    def test_reexport_depth_is_bounded(self):
        files = {f'm{i}.py': f'from m{i + 1} import f\n' for i in range(36)}
        files.update({'m36.py': 'def f(): pass\n', 'app.py': 'from m0 import f\ndef run(): f()\n'})
        report = self.scan(files)
        self.assertEqual(self.call(report)['reason_code'], 'REEXPORT_DEPTH_LIMIT')
        self.assertIsNone(self.call(report)['target_id'])

    def test_bound_is_configurable(self):
        report = self.scan({'a.py': 'from b import f\n', 'b.py': 'def f(): pass\n',
                            'app.py': 'from a import f\ndef run(): f()\n'})
        index = ProjectIndex(report['files'], ['.'], max_reexport_depth=1)
        with self.assertRaises(Unresolved) as result:
            index.export('a', 'f')
        self.assertEqual(result.exception.code, 'REEXPORT_DEPTH_LIMIT')

    def test_conditional_reexport_stays_unknown(self):
        report = self.scan({'storage.py': 'def save(): pass\n',
                            'api.py': 'if enabled:\n    from storage import save\n',
                            'app.py': 'from api import save\ndef run(): save()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'CONDITIONAL_EXPORT')

    def test_reassigned_export_stays_unknown(self):
        report = self.scan({'storage.py': 'def save(): pass\n',
                            'api.py': 'from storage import save\nsave = replacement\n',
                            'app.py': 'from api import save\ndef run(): save()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'AMBIGUOUS_EXPORT')

    def test_wildcard_export_stays_unknown(self):
        report = self.scan({'storage.py': 'def save(): pass\n',
                            'api.py': 'from storage import *\n',
                            'app.py': 'from api import save\ndef run(): save()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'WILDCARD_NAMESPACE')

    def test_decorated_reexport_stays_unknown(self):
        report = self.scan({'storage.py': '@decorator\ndef save(): pass\n',
                            'api.py': 'from storage import save\n',
                            'app.py': 'from api import save\ndef run(): save()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'UNSUPPORTED_TARGET')

    def test_dynamic_export_stays_unknown(self):
        report = self.scan({'storage.py': 'def __getattr__(name): pass\n',
                            'api.py': 'from storage import save\n',
                            'app.py': 'from api import save\ndef run(): save()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'DYNAMIC_EXPORT')

    def test_external_export_stays_unknown(self):
        report = self.scan({'api.py': 'from unavailable import save\n',
                            'app.py': 'from api import save\ndef run(): save()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'MODULE_NOT_FOUND')

    def test_unknown_sibling_module_not_assumed_loaded(self):
        report = self.scan({'pkg/one.py': 'def f(): pass\n', 'pkg/two.py': 'def f(): pass\n',
                            'app.py': 'import pkg.one\ndef run(): pkg.two.f()\n'})
        self.assertIsNone(self.call(report)['target_id'])

    def test_matching_import_does_not_hide_package_reassignment(self):
        report = self.scan({'pkg/__init__.py': 'from . import storage\nstorage = other\n',
                            'pkg/storage.py': 'def save(): pass\n',
                            'app.py': 'import pkg.storage\ndef run(): pkg.storage.save()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'PACKAGE_ATTRIBUTE_SHADOWED')

    def test_export_attribute_mutation_invalidates_alias_target(self):
        report = self.scan({'storage.py': 'def save(): pass\ndef local(): save()\n',
                            'api.py': 'from storage import save as store\n',
                            'patch.py': 'import api\napi.store = other\n',
                            'app.py': 'from api import store\ndef run(): store()\n'})
        self.assertIsNone(self.call(report)['target_id'])
        self.assertEqual(self.call(report)['reason_code'], 'ATTRIBUTE_MUTATION')
        self.assertIsNone(self.call(report, file='storage.py')['target_id'])

    def test_replacing_module_alias_invalidates_dependent_call(self):
        report = self.scan({'storage.py': 'def save(): pass\n',
                            'api.py': 'import storage as backend\n',
                            'patch.py': 'import api\napi.backend = other\n',
                            'app.py': 'import api\ndef run(): api.backend.save()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'ATTRIBUTE_MUTATION')
        self.assertIsNone(self.call(report)['target_id'])

    def test_replacing_package_child_invalidates_dependent_call(self):
        report = self.scan({'pkg/storage.py': 'def save(): pass\n',
                            'patch.py': 'import pkg.storage\npkg.storage = other\n',
                            'app.py': 'from pkg.storage import save\ndef run(): save()\n'})
        # A from-import still depends on that child's ordinary package binding.
        self.assertIsNone(self.call(report)['target_id'])
        self.assertEqual(self.call(report)['reason_code'], 'ATTRIBUTE_MUTATION')

    def test_replacing_parent_on_reexport_path_invalidates_call(self):
        report = self.scan({'pkg/inner/storage.py': 'def save(): pass\n',
                            'api.py': 'from pkg.inner.storage import save as persist\n',
                            'patch.py': 'import pkg.inner.storage\npkg.inner = other\n',
                            'app.py': 'from api import persist\ndef run(): persist()\n'})
        self.assertIsNone(self.call(report)['target_id'])
        self.assertEqual(self.call(report)['reason_code'], 'ATTRIBUTE_MUTATION')

    def test_deleting_exported_module_alias_invalidates_call(self):
        report = self.scan({'storage.py': 'def save(): pass\n',
                            'api.py': 'import storage as backend\n',
                            'patch.py': 'import api\ndel api.backend\n',
                            'app.py': 'from api import backend\ndef run(): backend.save()\n'})
        self.assertIsNone(self.call(report)['target_id'])
        self.assertEqual(self.call(report)['reason_code'], 'ATTRIBUTE_MUTATION')

    def test_deferred_global_reexport_is_not_a_static_export(self):
        report = self.scan({'storage.py': 'def save(): pass\n',
                            'api.py': 'def install():\n    global save\n    from storage import save\n',
                            'app.py': 'from api import save\ndef run(): save()\n'})
        self.assertIsNone(self.call(report)['target_id'])
        self.assertEqual(self.call(report)['reason_code'], 'CONDITIONAL_EXPORT')

    def test_async_definition_can_be_reexported(self):
        report = self.scan({'storage.py': 'async def save(): pass\n',
                            'api.py': 'from storage import save\n',
                            'app.py': 'from api import save\nasync def run(): await save()\n'})
        self.assertEqual(self.call(report)['target_file'], 'storage.py')
        self.assertEqual(self.call(report)['resolution'], 'LIKELY')

    def test_class_value_reference_is_not_function_edge(self):
        report = self.scan({'models.py': 'class User: pass\n',
                            'api.py': 'from models import User as Person\n',
                            'app.py': 'import api\ndef run(): api.Person()\n'})
        self.assertIsNone(self.call(report)['target_id'])
        index = ProjectIndex(report['files'], ['.'])
        unit = index.units['app.py']
        node, scope, _ = next(iter(unit['index'].calls.values()))
        ref = index.reference_value(unit, node.func, scope, node)
        self.assertEqual(ref['kind'], 'class')
        self.assertEqual(ref['file'], 'models.py')
        self.assertIsInstance(ref['node'], ast.ClassDef)
        self.assertEqual(ref['symbol']['qualified_name'], 'User')
        self.assertEqual(len(ref['import_chain']), 2)

    def test_local_class_reference_for_method_pass(self):
        report = self.scan({'app.py': 'class User: pass\ndef run(): User()\n'})
        index = ProjectIndex(report['files'], ['.'])
        unit = index.units['app.py']
        node, scope, _ = next(iter(unit['index'].calls.values()))
        ref = index.reference_value(unit, node.func, scope, node)
        self.assertEqual(ref['kind'], 'class')
        self.assertIs(ref['owner_scope'], unit['index'].root)

    def test_reassigned_class_reexport_is_not_a_known_class(self):
        report = self.scan({'models.py': 'class User: pass\n',
                            'api.py': 'from models import User\nUser = replacement\n',
                            'app.py': 'from api import User\ndef run(): User()\n'})
        index = ProjectIndex(report['files'], ['.'])
        unit = index.units['app.py']
        node, scope, _ = next(iter(unit['index'].calls.values()))
        with self.assertRaises(Unresolved) as result:
            index.reference_value(unit, node.func, scope, node)
        self.assertEqual(result.exception.code, 'AMBIGUOUS_EXPORT')

    def test_conditional_class_reference_rejected(self):
        report = self.scan({'models.py': 'if enabled:\n    class User: pass\n',
                            'app.py': 'from models import User\ndef run(): User()\n'})
        self.assertEqual(self.call(report)['reason_code'], 'CONDITIONAL_EXPORT')

    def test_reexports_with_duplicate_roots_are_not_guessed(self):
        report = self.scan({'one/storage.py': 'def save(): pass\n',
                            'two/storage.py': 'def save(): pass\n',
                            'one/api.py': 'from storage import save\n',
                            'one/app.py': 'from api import save\ndef run(): save()\n'},
                           source_roots=['one', 'two'])
        self.assertEqual(self.call(report, file='one/app.py')['reason_code'], 'AMBIGUOUS_MODULE')

    def test_pass_repetition_retains_evidence(self):
        report = self.scan({'storage.py': 'def save(): pass\n',
                            'api.py': 'from storage import save\n',
                            'app.py': 'from api import save\ndef run(): save()\n'})
        original = copy.deepcopy(report)
        resolve_project_calls(report)
        self.assertEqual(report, original)


if __name__ == '__main__':
    unittest.main()
