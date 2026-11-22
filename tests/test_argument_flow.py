"""Observed object arguments must remain bounded, explainable candidates."""
import tempfile
import textwrap
import unittest
from pathlib import Path

from repotrace.mapping.argument_flow import enrich_parameter_hints
from repotrace.mapping.method_resolver import MethodCandidates
from repotrace.mapping.project_resolver import ProjectIndex, resolve_project_calls
from repotrace.mapping.repository import analyze_repository
from repotrace.graph.graph_index import GraphIndex


class ArgumentFlowTests(unittest.TestCase):
    def scan(self, source=None, files=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in (files or {'app.py': source}).items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(textwrap.dedent(value))
            report = analyze_repository(root)
        self.assertEqual(report['status'], 'ok', report['diagnostics'])
        GraphIndex(report, include_possible=True)
        return report

    def call(self, report, expression):
        return next(c for f in report['files'] for c in f['calls'] if c['expression'] == expression)

    def names(self, report, expression='service.save'):
        return {c['qualified_name'] for c in self.call(report, expression).get('candidate_targets', [])}

    def test_constructor_argument_and_evidence(self):
        report = self.scan('''
            class Service:
                def save(self): pass
            def consume(service): service.save()
            def start(): consume(Service())
        ''')
        self.assertEqual(self.names(report), {'Service.save'})
        call = self.call(report, 'service.save')
        self.assertIsNone(call['target_id'])
        self.assertEqual(call['resolution'], 'POSSIBLE')
        steps = call['candidate_targets'][0]['evidence']['steps']
        self.assertIn('constructor_expression', [s['kind'] for s in steps])
        self.assertIn('observed_argument', [s['kind'] for s in steps])

    def test_keyword_forwarding_across_files(self):
        report = self.scan(files={
            'model.py': 'class Service:\n    def save(self): pass\n',
            'consumer.py': 'def consume(service): service.save()\n',
            'main.py': '''
                from model import Service
                from consumer import consume
                def forward(value):
                    alias = value
                    consume(service=alias)
                def start(): forward(Service())
            '''})
        self.assertEqual(self.names(report), {'Service.save'})

    def test_main_guard_constructor(self):
        report = self.scan('''
            class Service:
                def save(self): pass
            def consume(service): service.save()
            if __name__ == '__main__':
                app = Service()
                try:
                    consume(app)
                finally:
                    pass
        ''')
        self.assertEqual(self.names(report), {'Service.save'})

    def test_conflict_propagates_downstream(self):
        report = self.scan('''
            class First:
                def save(self): pass
            class Second:
                def save(self): pass
            def consume(service): service.save()
            def forward(value): consume(value)
            def start():
                forward(First())
                forward(Second())
        ''')
        self.assertEqual(self.names(report), set())
        self.assertEqual(report['project_index']['argument_flow']['conflicting_parameters'], 2)

    def test_no_method_name_guess(self):
        report = self.scan('''
            class Service:
                def save(self): pass
            def consume(service): service.save()
        ''')
        self.assertEqual(self.names(report), set())

    def test_write_through_observed_parameter_blocks_target(self):
        report = self.scan('''
            class Service:
                def save(self): pass
            def consume(service):
                service.save = replacement
                service.save()
            def start(): consume(Service())
        ''')
        self.assertEqual(self.names(report), set())

    def test_unsupported_argument_shapes(self):
        for actual in ['*items', '**items', 'Service(), Service()', 'wrong=Service()']:
            with self.subTest(actual=actual):
                report = self.scan('class Service:\n    def save(self): pass\n'
                                   'def consume(service): service.save()\n'
                                   f'def start(): consume({actual})\n')
                self.assertEqual(self.names(report), set())

    def test_guard_alternate_branch_or_reassignment_not_inferred(self):
        for tail in ['else:\n    consume(app)', '    app = other\n    consume(app)']:
            with self.subTest(tail=tail):
                report = self.scan('class Service:\n    def save(self): pass\n'
                                   'def consume(service): service.save()\n'
                                   "if __name__ == '__main__':\n    app = Service()\n" + tail + '\n')
                self.assertEqual(self.names(report), set())

    def test_bound_exhaustion_drops_incomplete_hints(self):
        report = self.scan('''
            class Service:
                def save(self): pass
            def consume(service): service.save()
            def forward(value): consume(value)
            def start(): forward(Service())
        ''')
        model = MethodCandidates(ProjectIndex(report['files'], ['.']))
        stats = enrich_parameter_hints(model, max_rounds=1)
        self.assertIn('max_rounds', stats['limits_reached'])
        self.assertFalse(model.parameter_hints)

    def test_guarded_receiver_mutation(self):
        for mutation in ['app.save = replacement', 'setattr(app, "save", replacement)']:
            with self.subTest(mutation=mutation):
                report = self.scan('class Service:\n    def save(self): pass\n'
                                   'def consume(service): service.save()\n'
                                   "if __name__ == '__main__':\n    app = Service()\n"
                                   f'    {mutation}\n    consume(app)\n')
                self.assertFalse(self.names(report))

    def test_repeated_project_resolution_is_stable(self):
        report = self.scan('''
            class Service:
                def save(self): pass
            def consume(service): service.save()
            def start(): consume(Service())
        ''')
        import copy
        before = copy.deepcopy(report)
        resolve_project_calls(report)
        self.assertEqual(report, before)


if __name__ == '__main__':
    unittest.main()
