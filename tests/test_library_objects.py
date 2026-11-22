import tempfile
import unittest
from pathlib import Path

from repotrace.mapping.repository import analyze_repository


class LibraryObjectTests(unittest.TestCase):
    def calls(self, source):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'app.py').write_text(source, encoding='utf-8')
            report = analyze_repository(Path(directory))
        self.assertEqual(report['status'], 'ok', report.get('diagnostics'))
        return {c['expression']: c for c in report['files'][0]['calls']}

    def categories(self, source):
        """{expression: category} for every call in one analyzed file."""
        return {expr: call['category'] for expr, call in self.calls(source).items()}

    def test_names_assigned_only_builtin_values(self):
        got = self.categories('def run(x):\n    seen = []\n    seen.append(x)\n'
                              '    d = dict()\n    d.get(1)\n    name = "a"\n    name.strip()\n')
        self.assertEqual(got['seen.append'], 'builtin_object_method')
        self.assertEqual(got['d.get'], 'builtin_object_method')
        self.assertEqual(got['name.strip'], 'builtin_object_method')

    def test_literals_and_chains(self):
        got = self.categories('def run(parts):\n    return ", ".join(parts).split(",").copy()\n')
        self.assertEqual(got["', '.join"], 'builtin_object_method')
        self.assertEqual(got["', '.join(parts).split"], 'builtin_object_method')

    def test_open_result(self):
        got = self.categories('def run(p):\n    return open(p).read()\n')
        self.assertEqual(got['open(p).read'], 'builtin_object_method')

    def test_reference_names_the_builtin_method(self):
        call = self.calls('def run(x):\n    items = []\n    items.append(x)\n')['items.append']
        self.assertEqual(call['external_reference']['qualified_name'], 'builtins.list.append')
        self.assertIsNone(call['target_id'])

    def test_name_reused_for_another_type_stays_unclear(self):
        got = self.categories('class Box:\n    def append(self, v): pass\n'
                              'def run(x):\n    items = []\n    items = Box()\n    items.append(x)\n')
        self.assertNotEqual(got['items.append'], 'builtin_object_method')

    def test_parameter_stays_unclear(self):
        got = self.categories('def run(items):\n    items.append(1)\n')
        self.assertNotEqual(got['items.append'], 'builtin_object_method')

    def test_shadowed_constructor_stays_unclear(self):
        got = self.categories('def run(list):\n    items = list()\n    items.append(1)\n')
        self.assertNotEqual(got['items.append'], 'builtin_object_method')

    def test_method_the_type_does_not_have_stays_unclear(self):
        got = self.categories('def run():\n    items = []\n    items.push(1)\n')
        self.assertNotEqual(got['items.push'], 'builtin_object_method')

    def test_loop_variable_and_augmented_assignment_stay_unclear(self):
        got = self.categories('def run(rows):\n    out = []\n    out += rows\n    out.append(1)\n'
                              '    for r in rows:\n        r.go()\n')
        self.assertNotEqual(got['out.append'], 'builtin_object_method')
        self.assertNotEqual(got['r.go'], 'builtin_object_method')

    def test_comprehension_variable_is_not_the_outer_name(self):
        got = self.categories('def run(rows):\n    x = []\n    return [x.go() for x in rows]\n')
        self.assertNotEqual(got['x.go'], 'builtin_object_method')

    def test_project_class_with_same_method_name_is_not_called_builtin(self):
        got = self.categories('class Box:\n    def append(self, v): pass\n'
                              'def run():\n    b = Box()\n    b.append(1)\n')
        self.assertEqual(got['b.append'], 'internal_possible')

    def test_self_field_that_is_always_a_list(self):
        got = self.categories('class Log:\n    def __init__(self):\n        self.items = []\n'
                              '    def add(self, x):\n        self.items.append(x)\n')
        self.assertEqual(got['self.items.append'], 'builtin_object_method')

    def test_field_with_one_unknown_write_stays_unclear(self):
        got = self.categories('class Log:\n    def __init__(self, items):\n        self.items = []\n'
                              '    def reset(self, items):\n        self.items = items\n'
                              '    def add(self, x):\n        self.items.append(x)\n')
        self.assertNotEqual(got['self.items.append'], 'builtin_object_method')

    def test_field_assigned_from_outside_stays_unclear(self):
        got = self.categories('class Log:\n    def __init__(self):\n        self.items = []\n'
                              '    def add(self, x):\n        self.items.append(x)\n'
                              'def swap(log, other):\n    log.items = other\n')
        self.assertNotEqual(got['self.items.append'], 'builtin_object_method')

    def test_same_name_as_a_method_stays_unclear(self):
        got = self.categories('class Log:\n    def __init__(self):\n        self.items = []\n'
                              '    def add(self, x):\n        self.items.append(x)\n'
                              'class Other:\n    def items(self): pass\n')
        self.assertNotEqual(got['self.items.append'], 'builtin_object_method')

    def test_field_with_literal_setattr_stays_unclear(self):
        got = self.categories('class Log:\n    def __init__(self):\n        self.items = []\n'
                              '    def add(self, x):\n        self.items.append(x)\n'
                              '    def set(self, v):\n        setattr(self, "items", v)\n')
        self.assertNotEqual(got['self.items.append'], 'builtin_object_method')

    def test_field_method_the_type_lacks_stays_unclear(self):
        got = self.categories('class Log:\n    def __init__(self):\n        self.items = []\n'
                              '    def add(self, x):\n        self.items.push(x)\n')
        self.assertNotEqual(got['self.items.push'], 'builtin_object_method')


if __name__ == '__main__':
    unittest.main()
