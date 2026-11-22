import tempfile
import unittest
from pathlib import Path

from repotrace.graph.structure import build_structure, folder_of
from repotrace.mapping.repository import analyze_repository

PROJECT = {
    'core/base.py': 'class Base:\n    """The root of all items.\n\n    More detail."""\n    kind = "base"\n\n    def run(self):\n        return 1\n',
    'core/other.py': 'class Base:\n    pass\n',                      # a second class with the same name
    'shop/items.py': ('from core.base import Base\nimport core.other as other\n\n\n'
                      'class Item(Base):\n    def __init__(self, price):\n        self.price = price\n        self.tax: float = 0.1\n\n'
                      '    def total(self):\n        return self.price\n\n\n'
                      'class Odd(other.Base, Exception):\n    pass\n'),
    'shop/cart.py': ('from shop.items import Item\n\n\nclass Cart:\n    def __init__(self):\n        self.items = []\n        self.first = Item(1)\n\n'
                     '    def add(self, price):\n        self.items.append(Item(price))\n        return Item(price).total()\n'),
    'shop/helpers.py': 'from shop.cart import Cart\n\n\ndef make():\n    return Cart()\n\n\ndef unused():\n    return 1\n',
    'tests/test_cart.py': 'from shop.cart import Cart\n\n\ndef test_cart():\n    Cart().add(1)\n',
}


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name, text in PROJECT.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        self.s = build_structure(analyze_repository(root))
        self.by_name = {(e['file'], e['name']): e for e in self.s['entities']}
        self.name = {e['id']: f"{e['file']}:{e['name']}" for e in self.s['entities']}

    def relations(self, kind):
        return {(self.name[e['source']], self.name[e['target']]) for e in self.s['edges'] if e['kind'] == kind}

    def test_inheritance_follows_the_import_even_when_two_classes_share_a_name(self):
        inherits = self.relations('inherits')
        self.assertIn(('shop/items.py:Item', 'core/base.py:Base'), inherits)
        self.assertIn(('shop/items.py:Odd', 'core/other.py:Base'), inherits)    # other.Base, not core.base.Base
        self.assertEqual(sum(1 for a, b in inherits if a.endswith(':Odd')), 1)  # Exception is not part of the project

    def test_a_class_lists_its_fields_and_methods_in_order(self):
        item = self.by_name[('shop/items.py', 'Item')]
        self.assertEqual(item['fields'], ['price', 'tax'])
        self.assertEqual([m['name'] for m in item['methods']], ['__init__', 'total'])
        self.assertEqual(self.by_name[('core/base.py', 'Base')]['fields'], ['kind'])

    def test_a_class_carries_the_first_line_of_its_docstring(self):
        self.assertEqual(self.by_name[('core/base.py', 'Base')]['doc'], 'The root of all items.')
        self.assertEqual(self.by_name[('shop/cart.py', 'Cart')]['doc'], '')

    def test_holds_is_only_claimed_when_the_class_creates_the_other_itself(self):
        self.assertEqual(self.relations('holds'), {('shop/cart.py:Cart', 'shop/items.py:Item')})

    def test_uses_counts_calls_between_boxes_and_never_points_at_itself(self):
        uses = self.relations('uses')
        self.assertIn(('shop/cart.py:Cart', 'shop/items.py:Item'), uses)
        self.assertIn(('shop/helpers.py:helpers', 'shop/cart.py:Cart'), uses)  # a plain function uses a class
        self.assertTrue(all(a != b for a, b in uses))
        cart_item = next(e for e in self.s['edges'] if e['kind'] == 'uses' and self.name[e['source']] == 'shop/cart.py:Cart'
                         and self.name[e['target']] == 'shop/items.py:Item')
        # Item(1), Item(price), Item(price) and Item(price).total(): creating an object is always only a "maybe"
        self.assertEqual((cart_item['sure'], cart_item['maybe']), (0, 4))

    def test_files_with_plain_functions_become_module_boxes(self):
        helpers = self.by_name[('shop/helpers.py', 'helpers')]
        self.assertEqual((helpers['kind'], [m['name'] for m in helpers['methods']]), ('module', ['make', 'unused']))
        self.assertNotIn(('shop/items.py', 'items'), self.by_name)               # that file only has classes

    def test_tests_are_marked_so_the_screen_can_hide_them(self):
        self.assertTrue(self.by_name[('tests/test_cart.py', 'test_cart')]['test'])
        self.assertFalse(self.by_name[('shop/cart.py', 'Cart')]['test'])

    def test_folders_count_what_is_inside_and_link_when_code_crosses_folders(self):
        folders = {f['id']: f for f in self.s['folders']}
        self.assertEqual(set(folders), {'core', 'shop', 'tests'})
        self.assertEqual((folders['shop']['classes'], folders['shop']['files']), (3, 3))
        self.assertTrue(folders['tests']['test'])
        link = next(e for e in self.s['folder_edges'] if (e['source'], e['target']) == ('shop', 'core'))
        self.assertGreaterEqual(link['inherits'], 2)

    def test_folder_names(self):
        self.assertEqual(folder_of('a/b/c.py'), 'a/b')
        self.assertEqual(folder_of('a/b/c.py', 1), 'a')
        self.assertEqual(folder_of('c.py'), '.')


if __name__ == '__main__':
    unittest.main()
