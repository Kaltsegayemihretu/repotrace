"""The folder structure is a promise, so it is checked: code may only depend on layers below it.

Layers, lowest first (see docs/ARCHITECTURE.md):
    reading -> mapping -> graph -> changes -> report -> app / cli -> evaluation

Every .py file under a layer counts, including __init__.py files and nested subfolders, and relative imports
(`from ..report import x`) are resolved before they are judged.
"""
import ast
import tempfile
import unittest
from pathlib import Path

from tests import ROOT

PACKAGE = ROOT / 'repotrace'
RANK = {'reading': 1, 'mapping': 2, 'graph': 3, 'changes': 4, 'report': 5, 'app': 6, 'cli': 6, 'evaluation': 7}


def layer_imports(path, package=PACKAGE):
    """The layers this file imports from, e.g. {'reading', 'graph'}: absolute and relative imports, also inside functions."""
    here = list(path.relative_to(package.parent).with_suffix('').parts)[:-1]      # the package this file lives in
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = here[:len(here) - (node.level - 1)] if node.level > 1 else here
                module = '.'.join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ''
            names = [module] + [f'{module}.{a.name}' for a in node.names]
        for name in names:
            parts = name.split('.')
            if parts[0] == package.name and len(parts) > 1 and parts[1] in RANK:
                found.add(parts[1])
    return found


def modules(package=PACKAGE):
    """(layer, path) for every .py file under a layer, however deep."""
    for path in sorted(package.rglob('*.py')):
        parts = path.relative_to(package).parts
        if len(parts) >= 2 and parts[0] in RANK:
            yield parts[0], path


def violations(package=PACKAGE):
    problems = []
    for layer, path in modules(package):
        for used in layer_imports(path, package) - {layer}:
            if RANK[used] >= RANK[layer]:
                problems.append(f'{path.relative_to(package)} imports {used}')
    return problems


class ArchitectureTests(unittest.TestCase):
    def test_every_module_lives_in_a_known_layer(self):
        on_disk = {p.name for p in PACKAGE.iterdir() if p.is_dir() and not p.name.startswith(('_', '.'))}
        self.assertEqual(on_disk, set(RANK), 'a folder was added or removed: update RANK and docs/ARCHITECTURE.md')
        for layer in RANK:
            self.assertTrue((PACKAGE / layer / '__init__.py').exists(), layer)

    def test_code_only_depends_on_layers_below_it(self):
        self.assertEqual(violations(), [], 'a lower layer must never use a higher one (app and cli must not use each other)')

    def test_nothing_depends_on_evaluation(self):
        users = [f'{layer}/{p.name}' for layer, p in modules() if layer != 'evaluation' and 'evaluation' in layer_imports(p)]
        self.assertEqual(users, [], 'evaluation measures the tool; the tool must not need it')

    def test_the_package_never_imports_tests(self):
        for layer, path in modules():
            self.assertNotIn('from tests', path.read_text(encoding='utf-8'), f'{layer}/{path.name}')

    def test_the_page_template_ships_with_the_report_layer(self):
        self.assertTrue((PACKAGE / 'report' / 'dashboard_template.html').exists())


class TheGuardCatchesWhatItPromises(unittest.TestCase):
    """The check must actually fail on each way of breaking the rule."""

    def broken(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        package = Path(tmp.name) / 'repotrace'
        for layer in RANK:
            (package / layer).mkdir(parents=True)
            (package / layer / '__init__.py').write_text('')
        for name, text in files.items():
            (package / name).parent.mkdir(parents=True, exist_ok=True)
            (package / name).write_text(text)
        return violations(package)

    def test_an_absolute_import_upwards_is_caught(self):
        self.assertEqual(self.broken({'reading/a.py': 'from repotrace.report import x\n'}), ['reading/a.py imports report'])

    def test_a_relative_import_upwards_is_caught(self):
        self.assertEqual(self.broken({'reading/a.py': 'from ..report import x\n'}), ['reading/a.py imports report'])
        self.assertEqual(self.broken({'reading/a.py': 'from .. import report\n'}), ['reading/a.py imports report'])

    def test_a_nested_subfolder_is_checked_too(self):
        self.assertEqual(self.broken({'mapping/deep/er/b.py': 'import repotrace.cli.change\n'}), ['mapping/deep/er/b.py imports cli'])
        self.assertEqual(self.broken({'mapping/deep/er/b.py': 'from ....changes import y\n'}), ['mapping/deep/er/b.py imports changes'])

    def test_a_layer_init_file_is_checked_too(self):
        self.assertEqual(self.broken({'graph/__init__.py': 'from repotrace.app import server\n'}), ['graph/__init__.py imports app'])

    def test_app_and_cli_may_not_use_each_other(self):
        self.assertEqual(self.broken({'app/a.py': 'from repotrace.cli import change\n'}), ['app/a.py imports cli'])

    def test_importing_the_same_layer_or_lower_is_fine(self):
        self.assertEqual(self.broken({'report/a.py': 'from . import b\nfrom ..graph import c\nfrom repotrace.reading import d\n'}), [])


if __name__ == '__main__':
    unittest.main()
