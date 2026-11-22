"""Evidence-based method candidates; fixtures are parsed, never executed."""
import tempfile
import textwrap
import unittest
from pathlib import Path

from repotrace.mapping.method_resolver import MethodCandidates, resolve_method_candidates
from repotrace.mapping.project_resolver import ProjectIndex
from repotrace.mapping.repository import analyze_repository


class MethodCandidateTests(unittest.TestCase):
    def scan(self, source=None, files=None, **limits):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, content in (files or {'app.py': source}).items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(textwrap.dedent(content), encoding='utf-8')
            report = analyze_repository(root, resolve_imports=False)
            self.assertEqual(report['status'], 'ok', report.get('diagnostics'))
            index = ProjectIndex(report['files'], ['.'])
            index.mutations()
            resolve_method_candidates(index, **limits)
            symbols = {symbol['id']: symbol for f in report['files'] for symbol in f['symbols']}
            for f in report['files']:
                for call in f['calls']:
                    if call.get('candidate_targets'):
                        self.assertEqual(call['resolution'], 'POSSIBLE')
                        self.assertIsNone(call['target_id'])
                        self.assertLessEqual(len(call['candidate_targets']), limits.get('max_candidates', 8))
                        for candidate in call['candidate_targets']:
                            symbol = symbols[candidate['target_id']]
                            self.assertNotEqual(symbol['kind'], 'CLASS')
                            self.assertEqual(candidate['qualified_name'], symbol['qualified_name'])
                            self.assertIsInstance(candidate['evidence'], dict)
                            self.assertTrue(candidate['evidence']['steps'])
            return report

    def call(self, report, expression, file='app.py', occurrence=0):
        calls = next(f['calls'] for f in report['files'] if f['file'] == file)
        return [c for c in calls if c['expression'] == expression][occurrence]

    def names(self, report, expression, **kwargs):
        return {c['qualified_name'] for c in self.call(report, expression, **kwargs).get('candidate_targets', [])}

    def test_self_and_positional_only_receiver(self):
        report = self.scan('''
            class Service:
                def save(self): pass
                def run(this, /): this.save()
        ''')
        self.assertEqual(self.names(report, 'this.save'), {'Service.save'})

    def test_async_method(self):
        report = self.scan('''
            class Service:
                async def save(self): pass
                async def run(self): await self.save()
        ''')
        self.assertEqual(self.names(report, 'self.save'), {'Service.save'})

    def test_constructor_variable_and_alias(self):
        report = self.scan('''
            class Service:
                def save(self): pass
            def run():
                service = Service()
                alias = service
                alias.save()
        ''')
        self.assertEqual(self.names(report, 'alias.save'), {'Service.save'})
        steps = self.call(report, 'alias.save')['candidate_targets'][0]['evidence']['steps']
        self.assertEqual([s['kind'] for s in steps].count('receiver_assignment'), 2)

    def test_typing_generic_base_retains_possible_project_methods(self):
        for imports, base in [('from typing import Generic, TypeVar', 'Generic[T]'),
                              ('from typing import Generic as G, TypeVar', 'G[T]'),
                              ('import typing\nfrom typing import TypeVar', 'typing.Generic[T]'),
                              ('import typing as ty\nfrom typing import TypeVar', 'ty.Generic[T]')]:
            with self.subTest(base=base):
                report = self.scan(imports + '\nT = TypeVar("T")\n'
                                   f'class Service({base}):\n    def save(self): pass\n'
                                   'def run(): Service().save()\n')
                self.assertEqual(self.names(report, 'Service().save'), {'Service.save'})
                call = self.call(report, 'Service().save')
                self.assertEqual(call['resolution'], 'POSSIBLE')
                self.assertIsNone(call['target_id'])

    def test_generic_base_still_checks_inheritance_and_mutations(self):
        common = ('from typing import Generic, TypeVar\nT = TypeVar("T")\n'
                  'class Base(Generic[T]):\n    def save(self): pass\n')
        report = self.scan(common + 'class Child(Base): pass\ndef run(): Child().save()\n')
        self.assertEqual(self.names(report, 'Child().save'), {'Base.save'})
        for extra in ('class Child(Base, Unknown): pass\n',
                      'class Child(Base):\n    def __getattribute__(self, name): return other\n',
                      'class Child(Base): pass\nChild.save = replacement\n'):
            with self.subTest(extra=extra):
                report = self.scan(common + extra + 'def run(): Child().save()\n')
                self.assertFalse(self.names(report, 'Child().save'))

    def test_generic_name_must_come_from_the_standard_library(self):
        for imports in ('from elsewhere import Generic\n', 'Generic = unknown\n',
                        'from typing import Generic\nGeneric = unknown\n',
                        'if condition:\n    from typing import Generic\n'):
            with self.subTest(imports=imports):
                report = self.scan(imports + 'class Service(Generic[T]):\n    def save(self): pass\n'
                                   'def run(): Service().save()\n')
                self.assertFalse(self.names(report, 'Service().save'))
        report = self.scan(files={'typing.py': 'class Generic: pass\n',
                                  'app.py': 'from typing import Generic\n'
                                            'class Service(Generic[T]):\n    def save(self): pass\n'
                                            'def run(): Service().save()\n'})
        self.assertFalse(self.names(report, 'Service().save'))

    def test_nested_class_header_respects_shadowed_generic_alias(self):
        report = self.scan('''
            from typing import Generic
            class Outer:
                Generic = unknown
                class Service(Generic[T]):
                    def save(self): pass
                    def run(self): self.save()
        ''')
        self.assertFalse(self.names(report, 'self.save'))

    def test_arbitrary_subscripted_base_is_not_treated_as_typing_metadata(self):
        report = self.scan('''
            class Custom:
                def __class_getitem__(cls, item): return Other
            class Service(Custom[T]):
                def save(self): pass
            def run(): Service().save()
        ''')
        self.assertFalse(self.names(report, 'Service().save'))

    def test_names_supplied_by_generic_remain_reserved(self):
        report = self.scan('''
            from typing import Generic, TypeVar
            T = TypeVar('T')
            class Service(Generic[T]):
                def __class_getitem__(cls, item): pass
            def run(): Service().__class_getitem__(T)
        ''')
        self.assertFalse(self.names(report, 'Service().__class_getitem__'))

    def test_direct_constructor_expression(self):
        report = self.scan('''
            class Service:
                def save(self): pass
            def run(): Service().save()
        ''')
        self.assertEqual(self.names(report, 'Service().save'), {'Service.save'})

    def test_constructor_links_init_not_class(self):
        report = self.scan('''
            class Service:
                def __init__(self): pass
            def run(): Service()
        ''')
        call = self.call(report, 'Service')
        self.assertEqual(self.names(report, 'Service'), {'Service.__init__'})
        self.assertEqual(call['resolution_kind'], 'constructor')

    def test_callable_instance(self):
        report = self.scan('''
            class Service:
                def __call__(self): pass
            def run():
                instance = Service()
                instance()
        ''')
        self.assertEqual(self.names(report, 'instance'), {'Service.__call__'})
        self.assertEqual(self.call(report, 'instance')['resolution_kind'], 'callable_instance')

    def test_imported_class_and_reexport(self):
        report = self.scan(files={
            'pkg/service.py': 'class Service:\n    def save(self): pass\n',
            'pkg/__init__.py': 'from .service import Service\n',
            'app.py': 'import pkg as public\ndef run(): public.Service().save()\n'})
        self.assertEqual(self.names(report, 'public.Service().save'), {'Service.save'})
        candidate = self.call(report, 'public.Service().save')['candidate_targets'][0]
        self.assertEqual(candidate['file'], 'pkg/service.py')

    def test_class_and_static_methods(self):
        report = self.scan('''
            class Service:
                @classmethod
                def save(cls): pass
                @staticmethod
                def helper(): pass
                @classmethod
                def run(cls):
                    cls.save()
                    cls.helper()
            def run(): Service.helper()
        ''')
        for expression, name in [('cls.save', 'Service.save'), ('cls.helper', 'Service.helper'),
                                 ('Service.helper', 'Service.helper')]:
            self.assertEqual(self.names(report, expression), {name})

    def test_static_method_parameter_not_self(self):
        report = self.scan('''
            class Service:
                def save(self): pass
                @staticmethod
                def run(self): self.save()
        ''')
        self.assertFalse(self.names(report, 'self.save'))

    def test_untyped_parameter_not_guessed_from_name(self):
        report = self.scan('''
            class Application:
                def save(self): pass
            def run(application): application.save()
        ''')
        self.assertFalse(self.names(report, 'application.save'))

    def test_annotation_is_only_possible(self):
        report = self.scan('''
            class Service:
                def save(self): pass
            def run(service: Service): service.save()
        ''')
        self.assertEqual(self.names(report, 'service.save'), {'Service.save'})
        evidence = self.call(report, 'service.save')['candidate_targets'][0]['evidence']['steps']
        self.assertIn('parameter_type_hint', {e['kind'] for e in evidence})

    def test_annotation_without_value_not_constructor(self):
        report = self.scan('''
            class Service:
                def save(self): pass
            def run():
                service: Service
                service.save()
        ''')
        self.assertFalse(self.names(report, 'service.save'))

    def test_inheritance_and_override_candidates(self):
        report = self.scan('''
            class Base:
                def save(self): pass
                def run(self): self.save()
            class Child(Base):
                def save(self): pass
        ''')
        self.assertEqual(self.names(report, 'self.save'), {'Base.save', 'Child.save'})

    def test_constructed_type_avoids_other_subclasses(self):
        report = self.scan('''
            class Base:
                def save(self): pass
            class Child(Base):
                def save(self): pass
            def run(): Base().save()
        ''')
        self.assertEqual(self.names(report, 'Base().save'), {'Base.save'})

    def test_super_and_diamond_order(self):
        report = self.scan('''
            class A:
                def save(self): pass
            class B(A):
                def save(self): super().save()
            class C(A):
                def save(self): pass
            class D(B, C): pass
        ''')
        self.assertEqual(self.names(report, 'super().save'), {'A.save', 'C.save'})

    def test_unknown_base_blocks_candidate(self):
        report = self.scan('''
            from external import Unknown
            class Service(Unknown):
                def save(self): pass
                def run(self): self.save()
        ''')
        self.assertFalse(self.names(report, 'self.save'))

    def test_custom_metaclass_or_class_decorator_blocks(self):
        for header in ['class Service(metaclass=Meta):', '@decorate\nclass Service:']:
            with self.subTest(header=header):
                report = self.scan(header + '\n    def save(self): pass\n    def run(self): self.save()\n')
                self.assertFalse(self.names(report, 'self.save'))

    def test_custom_getattribute_in_base_blocks(self):
        report = self.scan('''
            class Base:
                def __getattribute__(self, name): return other
            class Service(Base):
                def save(self): pass
                def run(self): self.save()
        ''')
        self.assertFalse(self.names(report, 'self.save'))

    def test_custom_new_does_not_establish_constructed_type(self):
        report = self.scan('''
            class Service:
                def __new__(cls): return other
                def save(self): pass
            def run(): Service().save()
        ''')
        self.assertFalse(self.names(report, 'Service().save'))

    ABSTRACT = '''
        import abc
        class Base(abc.ABC):
            @abc.abstractmethod
            def apply(self): pass
            def run(self): return self.apply()
        class One(Base):
            def apply(self): pass
        class Two(Base):
            def apply(self): pass
    '''

    def test_abstract_base_lists_every_override(self):
        report = self.scan(self.ABSTRACT)
        self.assertEqual(self.names(report, 'self.apply'), {'Base.apply', 'One.apply', 'Two.apply'})

    def test_from_import_abc_forms_are_supported(self):
        report = self.scan(self.ABSTRACT.replace('import abc', 'from abc import ABC, abstractmethod')
                           .replace('abc.ABC', 'ABC').replace('abc.abstractmethod', 'abstractmethod'))
        self.assertEqual(self.names(report, 'self.apply'), {'Base.apply', 'One.apply', 'Two.apply'})

    def test_abc_lookalikes_do_not_unblock(self):
        shadowed = self.scan(files={'app.py': self.ABSTRACT, 'abc.py': 'class ABC: pass\n'})
        # The project's own abc.py is a normal base; its abstractmethod is NOT the stdlib one.
        self.assertNotIn('Base.apply', self.names(shadowed, 'self.apply'))
        rebound = self.scan(self.ABSTRACT.replace('import abc', 'import abc\n        abc = object()'))
        self.assertEqual(self.names(rebound, 'self.apply'), set())
        foreign = self.scan(self.ABSTRACT.replace('import abc', 'import other as abc'))
        self.assertEqual(self.names(foreign, 'self.apply'), set())

    REGISTRY = '''
        REGISTRY = {}
        def register(name):
            def decorator(cls):
                REGISTRY[name] = cls
                cls.label = name
                return cls
            return decorator
        class Base:
            def run(self): return self.apply()
            def apply(self): pass
        @register("one")
        class One(Base):
            def apply(self): pass
    '''

    def test_identity_class_decorator_keeps_subclass_candidates(self):
        report = self.scan(self.REGISTRY)
        self.assertEqual(self.names(report, 'self.apply'), {'Base.apply', 'One.apply'})

    def test_class_decorator_that_may_replace_the_class_still_blocks(self):
        for body in ['return cls', 'cls = Other\n                return cls', 'return Other',
                     'cls.apply = None\n                return cls', 'setattr(cls, "apply", None)\n                return cls',
                     'print(cls)']:
            with self.subTest(body=body):
                source = self.REGISTRY.replace('REGISTRY[name] = cls\n                cls.label = name\n                return cls', body)
                names = self.names(self.scan(source), 'self.apply')
                self.assertNotIn('One.apply', names if body != 'return cls' else set())

    def test_decorator_written_attribute_cannot_hide_a_method(self):
        source = self.REGISTRY.replace('cls.label = name', 'cls.apply = name')
        self.assertEqual(self.names(self.scan(source), 'self.apply'), {'Base.apply'})

    def test_property_and_custom_method_decorator_block(self):
        for decorator in ['property', 'decorate']:
            with self.subTest(decorator=decorator):
                report = self.scan(f'class Service:\n    @{decorator}\n    def save(self): pass\n'
                                   '    def run(self): self.save()\n')
                self.assertFalse(self.names(report, 'self.save'))

    def test_shadowed_staticmethod_decorator_blocks(self):
        report = self.scan('''
            staticmethod = wrapper
            class Service:
                @staticmethod
                def save(): pass
            def run(): Service.save()
        ''')
        self.assertFalse(self.names(report, 'Service.save'))

    def test_receiver_reassignment_blocks(self):
        for body in [
                'service = Service()\nservice = other\nservice.save()',
                'service = Service()\ndel service\nservice.save()',
                'service.save()\nservice = Service()',
                'if enabled:\n    service = Service()\nservice.save()']:
            with self.subTest(body=body):
                report = self.scan('class Service:\n    def save(self): pass\ndef run():\n' +
                                   textwrap.indent(body, '    ') + '\n')
                self.assertFalse(self.names(report, 'service.save'))

    def test_self_reassignment_blocks(self):
        report = self.scan('''
            class Service:
                def save(self): pass
                def run(self):
                    self = other
                    self.save()
        ''')
        self.assertFalse(self.names(report, 'self.save'))

    def test_shadowed_class_blocks(self):
        report = self.scan('''
            class Service:
                def save(self): pass
            def run(Service): Service().save()
        ''')
        self.assertFalse(self.names(report, 'Service().save'))

    def test_instance_attribute_write_blocks(self):
        report = self.scan('''
            class Service:
                def __init__(self): self.save = replacement
                def save(self): pass
                def run(self): self.save()
        ''')
        self.assertFalse(self.names(report, 'self.save'))

    def test_class_mutation_and_dynamic_setattr_block(self):
        mutations = ['Service.save = replacement', 'del Service.save',
                     'setattr(Service, "save", replacement)', 'setattr(Service, name, replacement)',
                     'Service.__getattribute__ = replacement', 'Service.__bases__ = (Other,)']
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                report = self.scan('class Service:\n    def save(self): pass\n    def run(self): self.save()\n' +
                                   mutation + '\n')
                self.assertFalse(self.names(report, 'self.save'))

    def test_competing_method_definitions_block(self):
        report = self.scan('''
            class Service:
                def save(self): pass
                save = replacement
                def run(self): self.save()
        ''')
        self.assertFalse(self.names(report, 'self.save'))

    def test_conditional_method_definition_blocks(self):
        report = self.scan('''
            class Service:
                if enabled:
                    def save(self): pass
                def run(self): self.save()
        ''')
        self.assertFalse(self.names(report, 'self.save'))

    def test_nested_function_can_capture_self(self):
        report = self.scan('''
            class Service:
                def save(self): pass
                def run(self):
                    def inner(): self.save()
        ''')
        self.assertEqual(self.names(report, 'self.save'), {'Service.save'})

    def test_zero_argument_super_in_nested_function_not_assumed(self):
        report = self.scan('''
            class Base:
                def save(self): pass
            class Service(Base):
                def run(self):
                    def inner(): super().save()
        ''')
        self.assertFalse(self.names(report, 'super().save'))

    def test_module_level_calls_stay_unresolved(self):
        report = self.scan('''
            class Service:
                def save(self): pass
            Service().save()
        ''')
        self.assertFalse(self.names(report, 'Service().save'))

    def test_method_candidate_limit_is_reported(self):
        source = 'class Base:\n    def save(self): pass\n    def run(self): self.save()\n'
        source += ''.join(f'class Child{i}(Base):\n    def save(self): pass\n' for i in range(10))
        report = self.scan(source, max_candidates=3)
        call = self.call(report, 'self.save')
        self.assertEqual(len(call['candidate_targets']), 3)
        self.assertTrue(call['candidate_limit_reached'])

    def test_inference_depth_is_bounded(self):
        source = 'class Base:\n    def save(self): pass\n'
        source += ''.join(f'class Child{i}({"Base" if i == 0 else "Child" + str(i - 1)}): pass\n'
                          for i in range(6))
        source += 'def run(): Child5().save()\n'
        report = self.scan(source, max_depth=3)
        self.assertFalse(self.names(report, 'Child5().save'))

    def test_parameter_hint_hook_is_evidence_based(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'app.py').write_text('class Service:\n    def save(self): pass\n'
                                       'def run(app): app.save()\n', encoding='utf-8')
            report = analyze_repository(root, resolve_imports=False)
            index = ProjectIndex(report['files'], ['.'])
            model = MethodCandidates(index)
            unit = index.units['app.py']
            node, scope, _ = next(v for v in unit['index'].calls.values())
            arg = scope.bindings['app'][0]['node']
            class_id = next(iter(model.classes))
            model.parameter_hints[arg] = {
                'class': class_id, 'mode': 'instance', 'polymorphic': False,
                'evidence': [{'kind': 'observed_argument', 'file': 'app.py', 'warning': 'Not every caller is known.'}]}
            model.run()
            self.assertEqual(self.names(report, 'app.save'), {'Service.save'})

    def test_nested_class_header_must_not_skip_class_binding(self):
        report = self.scan('''
            class Outer:
                object = Unknown
                class Service(object):
                    def save(self): pass
                    def run(self): self.save()
        ''')
        self.assertFalse(self.names(report, 'self.save'))

    def test_implicit_class_receiver_in_new(self):
        report = self.scan('''
            class Service:
                def __new__(cls): return cls()
                def __call__(self): pass
        ''')
        self.assertEqual(self.names(report, 'cls'), {'Service.__new__'})

    def test_class_name_alias(self):
        report = self.scan('''
            class Service:
                def save(self): pass
            def run():
                Alias = Service
                Alias.save()
        ''')
        self.assertEqual(self.names(report, 'Alias.save'), {'Service.save'})

    def test_fixture_is_never_executed(self):
        report = self.scan('''
            raise RuntimeError("The analyzer must not execute this fixture")
            class Service:
                def save(self): pass
            def run(): Service().save()
        ''')
        self.assertEqual(self.names(report, 'Service().save'), {'Service.save'})


if __name__ == '__main__':
    unittest.main()
