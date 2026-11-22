"""Operators, protocols and builtins that run special methods. Source is parsed, never run."""
import tempfile
import textwrap
import unittest
from pathlib import Path

from repotrace.mapping.repository import analyze_repository

MONEY = '''
class Money:
    def __init__(self, v):
        self.v = v
    def __add__(self, other): return Money(self.v + other.v)
    def __radd__(self, other): return self
    def __sub__(self, other): return self
    def __neg__(self): return self
    def __eq__(self, other): return True
    def __lt__(self, other): return True
    def __gt__(self, other): return True
    def __len__(self): return 1
    def __str__(self): return "m"
    def __repr__(self): return "m!"
    def __getitem__(self, i): return i
    def __setitem__(self, i, v): pass
    def __contains__(self, x): return True
    def __iter__(self): return iter([])
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __iadd__(self, other): return self
    def __hash__(self): return 1
    def __format__(self, spec): return "f"
class Other:
    def __radd__(self, other): return self
    def __lt__(self, other): return True
'''


class ImplicitCallTests(unittest.TestCase):
    def scan(self, *parts):
        source = '\n'.join(textwrap.dedent(p) for p in parts)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'app.py').write_text(source, encoding='utf-8')
            report = analyze_repository(directory)
        self.assertEqual(report['status'], 'ok', report.get('diagnostics'))
        return report

    def implicit(self, report):
        calls = next(f['calls'] for f in report['files'] if f['file'] == 'app.py')
        return [c for c in calls if c.get('implicit')]

    def found(self, report):
        """{(expression, method): {target names}}"""
        return {(c['expression'], c['implicit_method']): {t['qualified_name'] for t in c['candidate_targets']}
                for c in self.implicit(report)}

    def body(self, code):
        return MONEY + '\ndef use(a, b):\n' + textwrap.indent(textwrap.dedent(code), '    ') + '\nuse(Money(1), Money(2))\n'

    def test_binary_and_comparison_operators(self):
        found = self.found(self.scan(self.body('''
            x = a + b
            y = a - b
            z = a == b
            w = a < b
            return x, y, z, w
        ''')))
        self.assertEqual(found[('a + b', '__add__')], {'Money.__add__'})
        self.assertEqual(found[('a - b', '__sub__')], {'Money.__sub__'})
        self.assertEqual(found[('a == b', '__eq__')], {'Money.__eq__'})
        self.assertEqual(found[('a < b', '__lt__')], {'Money.__lt__'})

    def test_unary_subscript_in_for_with_and_augmented_assignment(self):
        found = self.found(self.scan(self.body('''
            n = -a
            i = a[0]
            a[1] = 2
            has = 3 in a
            for item in a:
                pass
            with a:
                pass
            return n, i, has
        ''')))
        self.assertEqual(found[('-a', '__neg__')], {'Money.__neg__'})
        self.assertEqual(found[('a[0]', '__getitem__')], {'Money.__getitem__'})
        self.assertEqual(found[('a[1]', '__setitem__')], {'Money.__setitem__'})
        self.assertEqual(found[('3 in a', '__contains__')], {'Money.__contains__'})
        self.assertIn(('a', '__iter__'), {(k[0], k[1]) for k in found})      # anchored on the iterable only
        enters = {m for (_, m) in found}
        self.assertTrue({'__enter__', '__exit__'} <= enters)

    def test_augmented_assignment_on_a_rebound_name_stays_unresolved(self):
        report = self.scan(self.body('''
            a += b
            return a
        '''))
        self.assertNotIn('__iadd__', {c['implicit_method'] for c in self.implicit(report)})

    def test_builtins_fstrings_and_percent_format(self):
        found = self.found(self.scan(self.body('''
            n = len(a)
            s = str(a)
            r = repr(a)
            h = hash(a)
            print(a, b)
            f = f"{a} and {a!r}"
            p = "%s / %s" % (a, b)
            return n, s, r, h, f, p
        ''')))
        self.assertEqual(found[('len(a)', '__len__')], {'Money.__len__'})
        self.assertEqual(found[('str(a)', '__str__')], {'Money.__str__'})
        self.assertEqual(found[('repr(a)', '__repr__')], {'Money.__repr__'})
        self.assertEqual(found[('hash(a)', '__hash__')], {'Money.__hash__'})
        self.assertEqual(found[('print(a, b)', '__str__')], {'Money.__str__'})
        methods = {(e, m) for (e, m) in found}
        self.assertTrue(any(m == '__format__' for _, m in methods))
        self.assertTrue(any(e.startswith("'%s / %s' %") and m == '__str__' for e, m in methods))

    def test_fallback_order_str_to_repr_and_bool_to_len(self):
        report = self.scan('''
            class OnlyRepr:
                def __repr__(self): return "r"
            class OnlyLen:
                def __len__(self): return 1
            def use():
                a = OnlyRepr()
                b = OnlyLen()
                return str(a), bool(b)
            use()
        ''')
        found = self.found(report)
        self.assertEqual(found[('str(a)', '__str__')], {'OnlyRepr.__repr__'})
        self.assertEqual(found[('bool(b)', '__bool__')], {'OnlyLen.__len__'})

    def test_reflected_operator_when_left_is_a_literal_or_a_different_class(self):
        found = self.found(self.scan(self.body('''
            x = 0 + a
            return x
        ''')))
        self.assertEqual(found[('0 + a', '__add__')], {'Money.__radd__'})
        found = self.found(self.scan(MONEY, '''
            def use(a, o):
                return a + o
            use(Money(1), Other())
        '''))
        self.assertEqual(found[('a + o', '__add__')], {'Money.__add__', 'Other.__radd__'})

    def test_same_class_operands_do_not_add_the_reflected_method(self):
        found = self.found(self.scan(self.body('return a + b')))
        self.assertEqual(found[('a + b', '__add__')], {'Money.__add__'})

    def test_untyped_operands_and_missing_methods_stay_unresolved(self):
        report = self.scan(MONEY, '''
            def use(a, b, c):
                x = c + b          # nothing known about either side
                y = len(c)
                z = a * a          # Money has no __mul__
                return x, y, z
            use(Money(1), Money(2), 5)
        ''')
        names = {(c['expression'], c['implicit_method']) for c in self.implicit(report)}
        self.assertNotIn(('c + b', '__add__'), names)
        self.assertNotIn(('len(c)', '__len__'), names)
        self.assertNotIn(('a * a', '__mul__'), names)

    def test_shadowed_builtin_and_class_level_code_are_ignored(self):
        report = self.scan(MONEY, '''
            def len(x):
                return 0
            def use(a):
                return len(a)
            use(Money(1))
            total = Money(1) + Money(2)
        ''')
        self.assertEqual(self.implicit(report), [])

    def test_polymorphic_self_lists_subclass_overrides(self):
        found = self.found(self.scan('''
            class Base:
                def __eq__(self, other): return True
                def same(self, other): return self == other
            class Child(Base):
                def __eq__(self, other): return False
        '''))
        self.assertEqual(found[('self == other', '__eq__')], {'Base.__eq__', 'Child.__eq__'})

    def test_records_are_valid_graph_input_and_counted(self):
        from repotrace.graph.graph_index import GraphIndex
        report = self.scan(self.body('return a + b, len(a)'))
        self.assertEqual(report['summary']['implicit_calls'], 2)
        index = GraphIndex(report, include_possible=True)
        self.assertEqual(index.coverage['possible_calls'], 2 + sum(
            c['resolution'] == 'POSSIBLE' and not c.get('implicit') for f in report['files'] for c in f['calls']))
        ids = [c['id'] for f in report['files'] for c in f['calls']]
        self.assertEqual(len(ids), len(set(ids)))
        for call in self.implicit(report):
            self.assertEqual(call['category'], 'internal_possible')
            self.assertEqual(call['resolution'], 'POSSIBLE')
            self.assertIsNone(call['target_id'])


if __name__ == '__main__':
    unittest.main()


class ReturnTypeTests(unittest.TestCase):
    scan = ImplicitCallTests.scan
    implicit = ImplicitCallTests.implicit
    found = ImplicitCallTests.found

    def methods(self, report, expression):
        calls = next(f['calls'] for f in report['files'] if f['file'] == 'app.py')
        return {t['qualified_name'] for c in calls if c['expression'] == expression
                for t in c.get('candidate_targets', [])}

    def test_function_and_method_return_values_type_the_result(self):
        report = self.scan('''
            class Money:
                def format(self): return "m"
                def doubled(self): return Money()
                def same(self): return self
            def make(): return Money()
            def use(m):
                a = make()
                b = a.doubled()
                c = b.same()
                return a.format(), b.format(), c.format(), m.format()
            use(Money())
        ''')
        calls = {c['expression']: c for f in report['files'] for c in f['calls']}
        for expression in ('a.format', 'b.format', 'c.format'):
            self.assertEqual({t['qualified_name'] for t in calls[expression]['candidate_targets']}, {'Money.format'})
        steps = calls['a.format']['candidate_targets'][0]['evidence']['steps']
        self.assertIn('return_value', [s['kind'] for s in steps])

    def test_factory_returning_different_classes_lists_all_of_them(self):
        report = self.scan('''
            class A:
                def go(self): return 1
            class B:
                def go(self): return 2
            def make(kind):
                if kind == "a":
                    return A()
                return B()
            def use():
                x = make("a")
                return x.go()
            use()
        ''')
        self.assertEqual(self.methods(report, 'x.go'), {'A.go', 'B.go'})

    def test_untyped_returns_generators_async_and_cycles_stay_unresolved(self):
        report = self.scan('''
            class A:
                def go(self): return 1
            def untyped(v): return v
            def mixed(flag):
                if flag:
                    return A()
                return untyped(flag)
            def gen():
                yield A()
            async def coro():
                return A()
            def loop_a(): return loop_b()
            def loop_b(): return loop_a()
            def use():
                return (untyped(1).go(), mixed(1).go(), gen().go(), coro().go(), loop_a().go())
            use()
        ''')
        for expression in ('untyped(1).go', 'mixed(1).go', 'gen().go', 'coro().go', 'loop_a().go'):
            self.assertEqual(self.methods(report, expression), set(), expression)

    def test_none_returns_do_not_block_a_typed_result(self):
        report = self.scan('''
            class A:
                def go(self): return 1
            def maybe(flag):
                if flag:
                    return A()
                return None
            def use():
                x = maybe(1)
                return x.go()
            use()
        ''')
        self.assertEqual(self.methods(report, 'x.go'), {'A.go'})

    def test_operator_on_a_call_result_is_found(self):
        report = self.scan('''
            class Money:
                def __add__(self, other): return self
                def total(self): return Money()
            def use(m):
                return m.total() + m.total()
            use(Money())
        ''')
        self.assertEqual(self.found(report)[('m.total() + m.total()', '__add__')], {'Money.__add__'})

    def test_with_statement_record_is_anchored_on_the_context_expression(self):
        report = self.scan('''
            class Ctx:
                def __enter__(self): return self
                def __exit__(self, *a): return False
            def make(): return Ctx()
            def use():
                with make() as c:
                    return 1
            use()
        ''')
        records = self.implicit(report)
        self.assertEqual({(r['expression'], r['implicit_method']) for r in records},
                         {('make()', '__enter__'), ('make()', '__exit__')})
        self.assertTrue(all(r['span']['line'] == r['span']['end_line'] for r in records))
