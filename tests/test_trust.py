"""Regression tests from the independent review of the project map: no invented links, no hidden content,
no stale code, no flattering numbers. Each test reproduces a reported failure."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from repotrace.graph.graph_explore import Explorer
from repotrace.graph.graph_index import GraphIndex
from repotrace.graph.structure import build_structure
from repotrace.mapping.repository import analyze_repository
from repotrace.report.overview import SnapshotReader, build_overview


def project(files, **options):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return tmp, root, analyze_repository(root, **options)


class Fixture(unittest.TestCase):
    def build(self, files, **options):
        tmp, self.root, self.report = project(files, **options)
        self.addCleanup(tmp.cleanup)
        self.s = build_structure(self.report)
        self.name = {e['id']: f"{e['file']}:{e['name']}" for e in self.s['entities']}
        return self.s

    def relations(self, kind):
        return {(self.name[e['source']], self.name[e['target']]) for e in self.s['edges'] if e['kind'] == kind}

    def entity(self, file, name):
        return next(e for e in self.s['entities'] if (e['file'], e['name']) == (file, name))


class InheritanceBinding(Fixture):
    def test_an_external_base_is_never_linked_to_a_same_named_local_class(self):
        self.build({'unrelated.py': 'class Base:\n    pass\n', 'app.py': 'from third_party import Base\n\n\nclass Child(Base):\n    pass\n'})
        self.assertEqual(self.relations('inherits'), set())
        self.assertEqual(self.entity('app.py', 'Child')['base_ids'], [])
        self.assertEqual(self.entity('app.py', 'Child')['bases'], ['Base'])              # still shown, as outside the project

    def test_a_name_that_is_not_imported_or_defined_here_is_not_guessed_from_other_files(self):
        self.build({'other.py': 'class Base:\n    pass\n', 'app.py': 'class Child(Base):\n    pass\n'})
        self.assertEqual(self.relations('inherits'), set())

    def test_a_base_defined_in_the_same_file_is_linked(self):
        self.build({'a.py': 'class Base:\n    pass\n\n\nclass Child(Base):\n    pass\n'})
        self.assertEqual(self.relations('inherits'), {('a.py:Child', 'a.py:Base')})

    def test_the_import_decides_when_two_files_define_the_same_name(self):
        self.build({'one.py': 'class Base:\n    pass\n', 'two.py': 'class Base:\n    pass\n',
                    'app.py': 'from two import Base\n\n\nclass Child(Base):\n    pass\n'})
        self.assertEqual(self.relations('inherits'), {('app.py:Child', 'two.py:Base')})

    def test_aliases_submodules_and_package_reexports_are_followed(self):
        self.build({'pkg/__init__.py': 'from .base import Base\n', 'pkg/base.py': 'class Base:\n    pass\n', 'pkg/other.py': 'class Base:\n    pass\n',
                    'a.py': 'from pkg import Base as B\n\n\nclass A(B):\n    pass\n',
                    'b.py': 'from pkg import other\n\n\nclass B2(other.Base):\n    pass\n',
                    'c.py': 'import pkg.other as o\n\n\nclass C(o.Base):\n    pass\n'})
        self.assertEqual(self.relations('inherits'), {('a.py:A', 'pkg/base.py:Base'), ('b.py:B2', 'pkg/other.py:Base'), ('c.py:C', 'pkg/other.py:Base')})

    def test_a_name_both_imported_and_defined_is_left_unlinked_as_ambiguous(self):
        self.build({'one.py': 'class Base:\n    pass\n', 'app.py': 'from one import Base\n\n\nclass Base:\n    pass\n\n\nclass Child(Base):\n    pass\n'})
        self.assertEqual(self.relations('inherits'), set())


class OwnershipBinding(Fixture):
    def test_a_module_qualified_constructor_names_the_right_class(self):
        self.build({'real.py': 'class Item:\n    pass\n',
                    'app.py': 'import real\n\n\nclass Item:\n    pass\n\n\nclass Cart:\n    def __init__(self):\n        self.item = real.Item()\n'})
        self.assertEqual(self.relations('holds'), {('app.py:Cart', 'real.py:Item')})

    def test_aliased_constructors_are_followed(self):
        self.build({'real.py': 'class Item:\n    pass\n',
                    'a.py': 'import real as r\n\n\nclass A:\n    def __init__(self):\n        self.x = r.Item()\n',
                    'b.py': 'from real import Item as I\n\n\nclass B:\n    def __init__(self):\n        self.x = I()\n'})
        self.assertEqual(self.relations('holds'), {('a.py:A', 'real.py:Item'), ('b.py:B', 'real.py:Item')})

    def test_fields_and_ownership_of_an_inner_class_stay_with_the_inner_class(self):
        self.build({'a.py': 'class Part:\n    pass\n\n\nclass Outer:\n    class Inner:\n        def __init__(self):\n            self.secret = Part()\n'})
        self.assertEqual(self.entity('a.py', 'Outer')['fields'], [])
        self.assertEqual(self.entity('a.py', 'Outer.Inner')['fields'], ['secret'])
        self.assertEqual(self.relations('holds'), {('a.py:Outer.Inner', 'a.py:Part')})

    def test_creating_an_unimported_class_is_not_guessed(self):
        self.build({'other.py': 'class Item:\n    pass\n', 'a.py': 'class A:\n    def __init__(self):\n        self.x = Item()\n'})
        self.assertEqual(self.relations('holds'), set())


class CompleteInventory(Fixture):
    def test_every_analyzed_file_is_listed_even_when_it_has_no_classes_or_functions(self):
        s = self.build({'__init__.py': 'from .main import outer\n', 'config.py': 'LIMIT = 10\n', 'main.py': 'def outer():\n    return 1\n'})
        self.assertEqual({f['file'] for f in s['files']}, {'__init__.py', 'config.py', 'main.py'})

    def test_nested_functions_are_listed_under_their_file(self):
        self.build({'main.py': 'def outer():\n    def inner():\n        return 1\n    return inner()\n'})
        self.assertEqual([m['name'] for m in self.entity('main.py', 'main')['methods']], ['outer', 'outer.inner'])

    def test_no_function_is_dropped_however_many_there_are(self):
        self.build({'large.py': '\n'.join(f'def f{i}():\n    return {i}\n' for i in range(205))})
        e = self.entity('large.py', 'large')
        self.assertEqual((len(e['methods']), e['method_count']), (205, 205))

    def test_files_that_could_not_be_read_are_listed_with_the_reason(self):
        s = self.build({'good.py': 'def f():\n    return 1\n', 'bad.py': 'def broken(:\n    pass\n'})
        self.assertEqual([(u['file'], 'invalid syntax' in u['message']) for u in s['unreadable']], [('bad.py', True)])


class HonestCoverage(Fixture):
    def overview(self, files, **options):
        self.build(files, **options)
        index = GraphIndex(self.report, include_possible=True)
        return build_overview(index, Explorer(index), 'fixture')['calls']

    def test_an_import_that_cannot_be_found_is_not_counted_as_outside_the_project(self):
        calls = self.overview({'src/pkg/lib.py': 'def helper():\n    return 1\n',
                               'app.py': 'from pkg.lib import helper\n\n\ndef local():\n    return 1\n\n\ndef run():\n    local()\n    helper()\n'}, source_roots=['.'])
        self.assertEqual((calls['sure'], calls['outside'], calls['untraced'], calls['not_found']), (1, 0, 1, 1))
        self.assertEqual(calls['traced_percent'], 50)                           # it used to claim 100

    def test_standard_library_calls_are_counted_as_outside_and_do_not_lower_the_percentage(self):
        calls = self.overview({'app.py': 'import os\n\n\ndef local():\n    return 1\n\n\ndef run():\n    local()\n    os.getcwd()\n'})
        self.assertEqual((calls['sure'], calls['outside'], calls['untraced'], calls['not_found']), (1, 1, 0, 0))
        self.assertEqual(calls['traced_percent'], 100)

    def test_a_library_that_is_not_installed_here_is_not_confirmed_as_outside(self):
        calls = self.overview({'app.py': 'import surely_not_installed_xyz\n\n\ndef run():\n    surely_not_installed_xyz.go()\n'})
        self.assertEqual((calls['outside'], calls['not_found']), (0, 1))


class SnapshotSource(Fixture):
    def test_code_comes_from_the_map_snapshot_not_from_whatever_is_on_disk_now(self):
        self.build({'a.py': 'def old_name():\n    return 1\n'})
        reader = SnapshotReader({f['file']: f['source'] for f in self.report['files']})
        (self.root / 'a.py').write_text('def replacement():\n    return "new"\n')
        self.assertEqual(reader.lines('a.py', 1, 2), {'first': 1, 'lines': ['def old_name():', '    return 1']})
        self.assertIsNone(reader.lines('missing.py', 1, 2))

    def test_the_report_carries_a_hash_of_each_file_so_a_change_can_be_noticed(self):
        self.build({'a.py': 'def f():\n    return 1\n'})
        f = self.report['files'][0]
        self.assertEqual(f['content_sha256'], hashlib.sha256((self.root / 'a.py').read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
