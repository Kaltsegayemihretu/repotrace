import tempfile
import unittest
from pathlib import Path

from repotrace.reading.analyzer import analyze_file


class AnalyzerTests(unittest.TestCase):
    def analyze(self, source):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            path.write_bytes(source if isinstance(source, bytes) else source.encode("utf-8"))
            return analyze_file(path)

    def test_sample(self):
        report = analyze_file(Path(__file__).with_name("fixtures") / "example.py")
        self.assertEqual(report["status"], "ok")
        self.assertEqual([s["qualified_name"] for s in report["symbols"]],
                         ["calculate_total", "place_order", "Checkout", "Checkout.submit"])
        self.assertEqual(len(report["calls"]), 3)
        self.assertEqual(report["resolution_summary"]["linked_calls"], 2)
        calls = {c["expression"]: c for c in report["calls"]}
        self.assertEqual(calls["calculate_total"]["target_id"], report["symbols"][0]["id"])
        self.assertEqual(calls["place_order"]["target_id"], report["symbols"][1]["id"])
        self.assertIsNone(calls["print"]["target_id"])
        ids = {s["id"] for s in report["symbols"]} | {report["module_id"]}
        self.assertTrue(all(e["source"] in ids and e["target"] in ids for e in report["edges"]))

    def test_parameters(self):
        report = self.analyze("def f(a, /, b=None, *args: str, c, d=3, **kwargs): pass")
        params = report["symbols"][0]["parameters"]
        self.assertEqual([p["kind"] for p in params], ["POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD",
                         "VAR_POSITIONAL", "KEYWORD_ONLY", "KEYWORD_ONLY", "VAR_KEYWORD"])
        self.assertEqual(params[1]["default"], "None")
        self.assertFalse(params[1]["required"])
        self.assertTrue(params[3]["required"])

    def test_resolution_rejects_competing_bindings(self):
        examples = [
            'def caller(target): target()',
            'def caller():\n    target()\n    target = other',
            'target = other\ndef caller(): target()',
            'from other import target\ndef caller(): target()',
            'def caller():\n    return [target() for target in callbacks]',
            'def caller():\n    try: pass\n    except Exception as target: target()',
            'def target(): pass\ndef caller(): target()',
        ]
        for example in examples:
            with self.subTest(example=example):
                report = self.analyze('def target(): pass\n' + example)
                self.assertEqual(report['status'], 'ok')
                self.assertFalse(any(e['kind'] == 'CALLS' for e in report['edges']))

    def test_recursion_and_forward_reference(self):
        report = self.analyze('def caller(): target()\ndef target(): target()\n')
        self.assertEqual(report['resolution_summary']['linked_calls'], 2)

    def assert_targets(self, source, expected):
        report = self.analyze(source)
        self.assertEqual(report['status'], 'ok', report['diagnostics'])
        names = {s['id']: s['qualified_name'] for s in report['symbols']}
        actual = [(names.get(c['enclosing_symbol_id']), c['expression'],
                   names.get(c['target_id'])) for c in report['calls']]
        self.assertEqual(actual, expected)
        self.assertTrue(all(c['reason'] for c in report['calls']))
        return report

    def test_unrelated_parameter_does_not_block(self):
        report = self.assert_targets(
            'def save(): pass\ndef first(): save()\ndef second(save): save()\n',
            [('first', 'save', 'save'), ('second', 'save', None)])
        self.assertIn('parameter', report['calls'][1]['reason'])

    def test_local_assignment_only_blocks_its_scope(self):
        self.assert_targets(
            'def save(): pass\ndef first(): save()\ndef second():\n    save()\n    save = other\n',
            [('first', 'save', 'save'), ('second', 'save', None)])

    def test_nested_functions_and_closures(self):
        self.assert_targets(
            'def outer():\n    def helper(): helper()\n    def inner(): helper()\n    inner()\n',
            [('outer.helper', 'helper', 'outer.helper'),
             ('outer.inner', 'helper', 'outer.helper'), ('outer', 'inner', 'outer.inner')])

    def test_enclosing_parameter_shadows_module(self):
        self.assert_targets(
            'def save(): pass\ndef outer(save):\n    def inner(): save()\n',
            [('outer.inner', 'save', None)])

    def test_class_namespace_skipped(self):
        self.assert_targets(
            'def save(): pass\nclass C:\n    def save(self): pass\n    def run(self): save()\n',
            [('C.run', 'save', 'save')])

    def test_class_inside_function_preserves_closure(self):
        self.assert_targets(
            'def outer():\n    def helper(): pass\n    class C:\n        helper = other\n        def run(self): helper()\n',
            [('outer.C.run', 'helper', 'outer.helper')])

    def test_local_import_only_blocks_its_scope(self):
        self.assert_targets(
            'def save(): pass\ndef first(): save()\ndef second():\n    from other import save\n    save()\n',
            [('first', 'save', 'save'), ('second', 'save', None)])

    def test_comprehension_binding_does_not_leak(self):
        self.assert_targets(
            'def save(): pass\ndef first():\n    [save() for save in callbacks]\n    save()\n',
            [('first', 'save', None), ('first', 'save', 'save')])

    def test_global_read_and_write(self):
        self.assert_targets('def save(): pass\ndef f():\n    global save\n    save()\n',
                            [('f', 'save', 'save')])
        self.assert_targets('def save(): pass\ndef f(): save()\ndef replace():\n    global save\n    save = other\n',
                            [('f', 'save', None)])

    def test_nonlocal_read_and_write(self):
        self.assert_targets(
            'def outer():\n    def save(): pass\n    def inner():\n        nonlocal save\n        save()\n',
            [('outer.inner', 'save', 'outer.save')])
        self.assert_targets(
            'def outer():\n    def save(): pass\n    def inner():\n        nonlocal save\n        save = other\n    save()\n',
            [('outer', 'save', None)])

    def test_local_forward_call_and_conditional_definition(self):
        self.assert_targets('def outer():\n    helper()\n    def helper(): pass\n',
                            [('outer', 'helper', None)])
        self.assert_targets('def outer():\n    if enabled:\n        def helper(): pass\n    helper()\n',
                            [('outer', 'helper', None)])

    def test_duplicate_names_in_separate_functions(self):
        self.assert_targets(
            'def first():\n    def helper(): pass\n    helper()\ndef second():\n    def helper(): pass\n    helper()\n',
            [('first', 'helper', 'first.helper'), ('second', 'helper', 'second.helper')])

    def test_async_and_lambda(self):
        report = self.analyze('async def save(): pass\nasync def run():\n    await save()\n    callback = lambda: save()\n')
        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['resolution_summary']['linked_calls'], 2)

    def test_unsupported_targets_are_unresolved(self):
        for code in [
            '@wrap\ndef target(): pass\ndef caller(): target()',
            'if enabled:\n    def target(): pass\ndef caller(): target()',
            'from other import *\ndef target(): pass\ndef caller(): target()',
            'def target(): pass\ndef caller(): obj.target()',
        ]:
            with self.subTest(code=code):
                report = self.analyze(code)
                self.assertEqual(report['status'], 'ok')
                self.assertEqual(report['resolution_summary']['linked_calls'], 0)

    def test_definition_time_calls_and_lambda(self):
        report = self.analyze("@decorate()\ndef f(x=build()):\n    return lambda: later()\n")
        calls = {c["expression"]: c for c in report["calls"]}
        self.assertEqual(calls["decorate"]["context"], "DECORATOR")
        self.assertEqual(calls["build"]["enclosing_symbol_id"], report["module_id"])
        self.assertEqual(calls["later"]["context"], "LAMBDA_BODY")
        self.assertEqual(report["symbols"][0]["start_line_including_decorators"], 1)

    def test_nested_and_repeated_definitions(self):
        report = self.analyze("if True:\n    def f(): pass\nelse:\n    def f(): pass\ndef outer():\n    def inner(): pass\n")
        self.assertEqual(len({s["id"] for s in report["symbols"]}), 4)
        self.assertEqual(report["symbols"][-1]["qualified_name"], "outer.inner")

    def test_errors_encoding_and_no_execution(self):
        self.assertEqual(self.analyze("def broken(")["status"], "error")
        self.assertEqual(self.analyze("raise RuntimeError('do not run')")["status"], "ok")
        report = self.analyze(b"# coding: latin-1\n# caf\xe9\nx = 1\n")
        self.assertEqual(report["status"], "ok")
        self.assertIn("café", report["source"])
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(analyze_file(Path(directory) / "missing.py")["status"], "error")


if __name__ == "__main__":
    unittest.main()
