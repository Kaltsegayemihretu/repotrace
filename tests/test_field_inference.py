"""Typed object fields, constructor injection and forwarding decorators. Source is parsed, never run."""
import tempfile
import textwrap
import unittest
from pathlib import Path

from repotrace.mapping.repository import analyze_repository

DEP = '''
    class Dep:
        def save(self): pass
    class Other:
        def save(self): pass
'''
DECORATORS = '''
    import functools
    def logged(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            print("calling")
            return func(*args, **kwargs)
        return wrapper
    def role(*roles):
        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                if not roles:
                    raise ValueError
                return func(*args, **kwargs)
            return wrapper
        return decorator
    def swaps_first_argument(func):
        def wrapper(*args, **kwargs):
            return func(object(), *args[1:], **kwargs)
        return wrapper
    def drops_arguments(func):
        def wrapper(*args, **kwargs):
            return func()
        return wrapper
'''


class Helpers(unittest.TestCase):
    def scan(self, *parts, extra=None):
        source = '\n'.join(textwrap.dedent(part) for part in parts)
        files = {'app.py': source, **(extra or {})}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, content in files.items():
                (root / name).write_text(textwrap.dedent(content), encoding='utf-8')
            report = analyze_repository(root)
        self.assertEqual(report['status'], 'ok', report.get('diagnostics'))
        return report

    def calls(self, report, expression, file='app.py'):
        calls = next(f['calls'] for f in report['files'] if f['file'] == file)
        return [c for c in calls if c['expression'] == expression]

    def names(self, report, expression, **kw):
        found = self.calls(report, expression, **kw)
        self.assertTrue(found, expression)
        return {t['qualified_name'] for c in found for t in c.get('candidate_targets', [])}

class FieldInferenceTests(Helpers):
    def test_field_assigned_from_constructor(self):
        report = self.scan(DEP, '''
            class Service:
                def __init__(self):
                    self.dep = Dep()
                def run(self):
                    self.dep.save()
        ''')
        self.assertEqual(self.names(report, 'self.dep.save'), {'Dep.save'})
        call = self.calls(report, 'self.dep.save')[0]
        self.assertEqual(call['resolution'], 'POSSIBLE')
        steps = call['candidate_targets'][0]['evidence']['steps']
        self.assertIn('field_assignment', [s['kind'] for s in steps])

    def test_none_default_is_allowed_but_unknown_assignment_blocks(self):
        report = self.scan(DEP, '''
            class Service:
                def __init__(self):
                    self.dep = None
                def start(self):
                    self.dep = Dep()
                def run(self):
                    self.dep.save()
        ''')
        self.assertEqual(self.names(report, 'self.dep.save'), {'Dep.save'})
        report = self.scan(DEP, '''
            class Service:
                def __init__(self, thing):
                    self.dep = Dep()
                    self.dep = thing
                def run(self):
                    self.dep.save()
        ''')
        self.assertEqual(self.names(report, 'self.dep.save'), set())

    def test_two_typed_assignments_list_both_classes(self):
        report = self.scan(DEP, '''
            class Service:
                def __init__(self, fast):
                    self.dep = Dep()
                def swap(self):
                    self.dep = Other()
                def run(self):
                    self.dep.save()
        ''')
        self.assertEqual(self.names(report, 'self.dep.save'), {'Dep.save', 'Other.save'})

    def test_type_annotation_types_the_parameter(self):
        report = self.scan(DEP, '''
            class Service:
                def __init__(self, dep: Dep):
                    self.dep = dep
                def run(self):
                    self.dep.save()
        ''')
        self.assertEqual(self.names(report, 'self.dep.save'), {'Dep.save'})

    def test_constructor_injection_from_observed_call(self):
        report = self.scan(DEP, '''
            class Service:
                def __init__(self, dep, label="x"):
                    self.dep = dep
                def run(self):
                    self.dep.save()
            def build():
                return Service(Dep(), label="y")
        ''')
        self.assertEqual(self.names(report, 'self.dep.save'), {'Dep.save'})
        step = next(s for s in self.calls(report, 'self.dep.save')[0]['candidate_targets'][0]['evidence']['steps']
                    if s['kind'] == 'observed_argument')
        self.assertEqual(step['parameter'], 'dep')

    def test_conflicting_constructor_arguments_stay_unresolved(self):
        report = self.scan(DEP, '''
            class Service:
                def __init__(self, dep):
                    self.dep = dep
                def run(self):
                    self.dep.save()
            def build():
                return Service(Dep())
            def build_other():
                return Service(Other())
        ''')
        self.assertEqual(self.names(report, 'self.dep.save'), set())

    def test_constructor_argument_binding_skips_self_and_rejects_bad_shapes(self):
        for call in ['Service(Dep(), 1, 2)', 'Service(*items)', 'Service(**items)', 'Service()']:
            with self.subTest(call=call):
                report = self.scan(DEP, f'''
                    class Service:
                        def __init__(self, dep):
                            self.dep = dep
                        def run(self):
                            self.dep.save()
                    def build(items):
                        return {call}
                ''')
                self.assertEqual(self.names(report, 'self.dep.save'), set())

    def test_foreign_write_on_a_related_or_unknown_object_blocks(self):
        service = '''
            class Service:
                def __init__(self):
                    self.dep = Dep()
                def run(self):
                    self.dep.save()
        '''
        for target in ['svc = Service()\nsvc.dep = Other()', 'def patch(obj):\n    obj.dep = Other()']:
            with self.subTest(target=target):
                report = self.scan(DEP, service, target)
                self.assertEqual(self.names(report, 'self.dep.save'), set())

    def test_foreign_write_on_an_unrelated_known_class_does_not_block(self):
        report = self.scan(DEP, '''
            class Service:
                def __init__(self):
                    self.dep = Dep()
                def run(self):
                    self.dep.save()
            class Holder:
                pass
            holder = Holder()
            holder.dep = 5
        ''')
        self.assertEqual(self.names(report, 'self.dep.save'), {'Dep.save'})

    def test_computed_setattr_is_disclosed_when_untyped_and_blocks_when_related(self):
        source = '''
            class Service:
                def __init__(self):
                    self.dep = Dep()
                def run(self):
                    self.dep.save()
            def update(obj, key):
                setattr(obj, key, 1)
        '''
        # Untyped object: the link stays, with the doubt written into its evidence.
        report = self.scan(DEP, source)
        self.assertEqual(self.names(report, 'self.dep.save'), {'Dep.save'})
        target = self.calls(report, 'self.dep.save')[0]['candidate_targets'][0]['evidence']
        self.assertIn('unverified_dynamic_write', [s['kind'] for s in target['steps']] or [])
        # Literal name on an untyped object blocks.
        literal = self.scan(DEP, '''
            class Service:
                def __init__(self):
                    self.dep = Dep()
                def run(self):
                    self.dep.save()
            def update(obj):
                setattr(obj, "dep", 1)
        ''')
        self.assertEqual(self.names(literal, 'self.dep.save'), set())

    def test_computed_setattr_on_a_known_related_object_blocks(self):
        report = self.scan(DEP, '''
            class Service:
                def __init__(self):
                    self.dep = Dep()
                def run(self):
                    self.dep.save()
            def update(key):
                svc = Service()
                setattr(svc, key, 1)
        ''')
        self.assertEqual(self.names(report, 'self.dep.save'), set())

    def test_same_named_class_attribute_or_method_blocks(self):
        for member in ['dep = None', 'def dep(self): pass', 'dep = Other()']:
            with self.subTest(member=member):
                report = self.scan(DEP, f'''
                    class Service:
                        {member}
                        def __init__(self):
                            self.dep = Dep()
                        def run(self):
                            self.dep.save()
                ''')
                self.assertEqual(self.names(report, 'self.dep.save'), set())

    def test_unsupported_assignment_forms_block(self):
        for line in ['self.dep += Dep()', 'self.dep, other = Dep(), 1', 'for self.dep in [Dep()]: pass',
                     'del self.dep']:
            with self.subTest(line=line):
                report = self.scan(DEP, f'''
                    class Service:
                        def __init__(self):
                            self.dep = Dep()
                        def change(self):
                            {line}
                        def run(self):
                            self.dep.save()
                ''')
                self.assertEqual(self.names(report, 'self.dep.save'), set())

    def test_subclass_assignment_adds_its_class(self):
        report = self.scan(DEP, '''
            class Base:
                def __init__(self):
                    self.dep = Dep()
                def run(self):
                    self.dep.save()
            class Child(Base):
                def __init__(self):
                    self.dep = Other()
        ''')
        self.assertEqual(self.names(report, 'self.dep.save'), {'Dep.save', 'Other.save'})

    def test_field_cycle_does_not_hang_or_resolve(self):
        report = self.scan(DEP, '''
            class Service:
                def __init__(self):
                    self.a = self.b
                    self.b = self.a
                def run(self):
                    self.a.save()
        ''')
        self.assertEqual(self.names(report, 'self.a.save'), set())

    def test_chained_fields(self):
        report = self.scan(DEP, '''
            class Inner:
                def __init__(self):
                    self.dep = Dep()
            class Outer:
                def __init__(self):
                    self.inner = Inner()
                def run(self):
                    self.inner.dep.save()
        ''')
        self.assertEqual(self.names(report, 'self.inner.dep.save'), {'Dep.save'})


class ForwardingDecoratorTests(Helpers):
    def test_self_is_typed_inside_a_forwarding_decorated_method(self):
        report = self.scan(DECORATORS, DEP, '''
            class Service:
                @logged
                @role("a", "b")
                def run(self):
                    self.helper()
                def helper(self): pass
        ''')
        self.assertEqual(self.names(report, 'self.helper'), {'Service.helper'})

    def test_calls_to_a_decorated_method_reach_the_original(self):
        report = self.scan(DECORATORS, '''
            class Service:
                @logged
                def save(self): pass
                def run(self):
                    self.save()
        ''')
        self.assertEqual(self.names(report, 'self.save'), {'Service.save'})

    def test_non_forwarding_decorators_still_block(self):
        for decorator in ['swaps_first_argument', 'drops_arguments', 'unknown_decorator', 'property']:
            with self.subTest(decorator=decorator):
                report = self.scan(DECORATORS, f'''
                    class Service:
                        @{decorator}
                        def save(self): pass
                        def run(self):
                            self.save()
                ''')
                self.assertEqual(self.names(report, 'self.save'), set())

    def test_a_decorator_name_shadowed_in_the_class_blocks(self):
        report = self.scan(DECORATORS, '''
            class Service:
                def logged(func):
                    return 5
                @logged
                def save(self): pass
                def run(self):
                    self.save()
        ''')
        self.assertEqual(self.names(report, 'self.save'), set())


if __name__ == '__main__':
    unittest.main()
