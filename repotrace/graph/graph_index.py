"""Adapt a repository report into a reusable, source-free reverse call index.

Call records are authoritative; CONTAINS and other edge types are never traversed.
This is the storage boundary: later database adapters can expose the same symbol
lookup and callers_of() operations without changing the impact traversal.
"""
from collections import defaultdict
from copy import deepcopy


FUNCTION_KINDS = frozenset({'FUNCTION', 'METHOD', 'NESTED_FUNCTION', 'LAMBDA'})


def require(condition, message):
    if not condition:
        raise ValueError(message)


def valid_span(value):
    return (isinstance(value, dict) and
            all(type(value.get(k)) is int for k in ('line', 'column', 'end_line', 'end_column')) and
            value['line'] >= 1 and value['column'] >= 0 and value['end_column'] >= 0 and
            (value['end_line'], value['end_column']) >= (value['line'], value['column']))


class GraphIndex:
    """Build once, query many times. No AST parsing or target-code execution."""

    def __init__(self, report, *, max_symbols=200_000, max_calls=1_000_000,
                 include_possible=False, max_candidates_per_call=8):
        for value in (max_symbols, max_calls):
            require(type(value) is int and value > 0, 'Index limits must be positive integers.')
        require(isinstance(report, dict), 'Expected a repository report object.')
        schema = (report.get('schema_version'), report.get('file_schema_version'))
        require(report.get('report_kind') == 'repository' and schema in {('0.2', '0.5'), ('0.3', '0.6')},
                'Expected repository/file schema 0.2/0.5 or 0.3/0.6. Regenerate older reports.')
        require(type(include_possible) is bool, 'include_possible must be a boolean.')
        require(type(max_candidates_per_call) is int and max_candidates_per_call > 0,
                'Candidate limit must be a positive integer.')
        self.include_possible = include_possible
        require(report.get('status') in {'ok', 'partial'}, 'No usable repository analysis is available.')
        files = report.get('files')
        require(isinstance(files, list), 'Repository files must be a list.')
        self.symbols = {}
        self.by_name = defaultdict(list)
        self.reverse = defaultdict(dict)
        self.root = report.get('root')
        self.source_status = report['status']
        self.unresolved_by_source = defaultdict(int)
        modules, file_names, good = {}, set(), []
        failed = 0
        for file in files:
            require(isinstance(file, dict) and isinstance(file.get('file'), str), 'Invalid file record.')
            name = file['file']
            require(name not in file_names, f'Duplicate file: {name}')
            file_names.add(name)
            require(file.get('status') in {'ok', 'error'}, f'Invalid file status: {name}')
            if file['status'] != 'ok':
                failed += 1
                continue
            good.append(file)
            module_id = file.get('module_id')
            require(isinstance(module_id, str) and module_id not in modules, 'Invalid or duplicate module ID.')
            modules[module_id] = name
            require(isinstance(file.get('symbols'), list), f'Missing symbols: {name}')
            for symbol in file['symbols']:
                require(isinstance(symbol, dict), 'Invalid symbol record.')
                identifier = symbol.get('id')
                require(isinstance(identifier, str) and identifier and identifier not in self.symbols,
                        'Invalid or duplicate symbol ID.')
                require(symbol.get('file') == name and valid_span(symbol.get('span')), 'Invalid symbol location.')
                require(isinstance(symbol.get('qualified_name'), str) and
                        symbol.get('kind') in FUNCTION_KINDS | {'CLASS'}, 'Unsupported symbol record.')
                require(len(self.symbols) < max_symbols, 'Symbol limit exceeded; increase max_symbols explicitly.')
                self.symbols[identifier] = {k: deepcopy(symbol[k]) for k in
                                           ('id', 'file', 'qualified_name', 'kind', 'span')}
                self.by_name[(name, symbol['qualified_name'])].append(identifier)
        require(not set(modules).intersection(self.symbols), 'Module and symbol IDs must be distinct.')
        known = set(modules) | set(self.symbols)
        locations = {**modules, **{key: s['file'] for key, s in self.symbols.items()}}
        call_ids, linked, unresolved, possible, candidate_limits = set(), 0, 0, 0, 0
        categories = defaultdict(int)

        def add_pair(caller, target, evidence):
            pair = self.reverse[target].get(caller)
            candidate = evidence['resolution'] == 'POSSIBLE'
            if pair is None:
                self.reverse[target][caller] = {
                    'source': caller, 'target': target, 'call_site_count': 1,
                    'likely_call_site_count': int(not candidate),
                    'possible_call_site_count': int(candidate), 'evidence': evidence}
            else:
                pair['call_site_count'] += 1
                pair['possible_call_site_count' if candidate else 'likely_call_site_count'] += 1
                # A supported link takes priority over a speculative one for this pair.
                rank = lambda e: (e['resolution'] == 'POSSIBLE', e['call_id'])
                if rank(evidence) < rank(pair['evidence']):
                    pair['evidence'] = evidence
        for file in good:
            require(isinstance(file.get('calls'), list), 'Missing call records.')
            for call in file['calls']:
                require(isinstance(call, dict), 'Invalid call record.')
                identifier = call.get('id')
                require(isinstance(identifier, str) and identifier not in call_ids, 'Invalid or duplicate call ID.')
                require(len(call_ids) < max_calls, 'Call limit exceeded; increase max_calls explicitly.')
                call_ids.add(identifier)
                caller = call.get('enclosing_symbol_id')
                require(isinstance(caller, str) and caller in known and locations[caller] == file['file'],
                        'A call has an invalid caller.')
                require(call.get('file') == file['file'] and valid_span(call.get('span')),
                        'A call has an invalid source location.')
                require('target_id' in call, 'A call is missing its target field.')
                target = call['target_id']
                category = call.get('category', 'unclassified')
                require(isinstance(category, str), 'Call category must be a string.')
                categories[category] += 1
                if target is None:
                    state = call.get('resolution')
                    require(state in {'UNRESOLVED', 'POSSIBLE'}, 'Unlinked call must be UNRESOLVED or POSSIBLE.')
                    unresolved += 1
                    self.unresolved_by_source[caller] += 1
                    candidates = call.get('candidate_targets', [])
                    require(isinstance(candidates, list), 'Candidate targets must be a list.')
                    if state == 'UNRESOLVED':
                        require(not candidates, 'UNRESOLVED call cannot carry active candidates.')
                        continue
                    require(schema == ('0.3', '0.6'), 'Candidates require repository schema 0.3.')
                    require(0 < len(candidates) <= max_candidates_per_call, 'Invalid candidate count.')
                    truncated = call.get('candidate_limit_reached', False)
                    require(type(truncated) is bool, 'Candidate truncation flag must be a boolean.')
                    candidate_limits += int(truncated)
                    require(caller in self.symbols and self.symbols[caller]['kind'] in FUNCTION_KINDS,
                            'Only function-context candidates are supported.')
                    seen_targets = set()
                    for candidate in candidates:
                        require(isinstance(candidate, dict), 'Invalid candidate record.')
                        candidate_id = candidate.get('target_id')
                        require(isinstance(candidate_id, str) and candidate_id in self.symbols and
                                candidate_id not in seen_targets, 'Invalid or duplicate candidate target.')
                        seen_targets.add(candidate_id)
                        target_symbol = self.symbols[candidate_id]
                        require(target_symbol['kind'] in FUNCTION_KINDS and
                                candidate.get('file') == target_symbol['file'] and
                                candidate.get('qualified_name') == target_symbol['qualified_name'],
                                'Candidate does not match its definition.')
                        require(isinstance(candidate.get('reason'), str) and
                                isinstance(candidate.get('evidence'), dict), 'Candidate needs reasoning and evidence.')
                        if include_possible:
                            evidence = {'call_id': identifier, 'file': file['file'],
                                        'span': deepcopy(call['span']), 'resolution': 'POSSIBLE',
                                        'resolution_kind': call.get('resolution_kind', 'method'),
                                        'reason': candidate['reason'],
                                        'candidate_evidence': deepcopy(candidate['evidence'])}
                            add_pair(caller, candidate_id, evidence)
                    possible += 1
                    continue
                require(not call.get('candidate_targets'), 'LIKELY calls cannot carry competing candidates.')
                require(isinstance(target, str) and target in self.symbols, 'A call has a missing target definition.')
                require(self.symbols[target]['kind'] in FUNCTION_KINDS and
                        caller in self.symbols and self.symbols[caller]['kind'] in FUNCTION_KINDS,
                        'Only function-to-function links are supported by this impact schema.')
                require(call.get('resolution') == 'LIKELY' and call.get('resolution_kind') in {'local', 'import'},
                        'Unsupported call resolution; update the graph adapter before using this report.')
                linked += 1
                evidence = {'call_id': identifier, 'file': file['file'],
                            'span': deepcopy(call['span']), 'resolution': call['resolution'],
                            'resolution_kind': call['resolution_kind'],
                            'reason': call.get('reason', '')}
                if 'import_evidence' in call:
                    evidence['import_evidence'] = deepcopy(call['import_evidence'])
                if 'import_chain' in call:
                    evidence['import_chain'] = deepcopy(call['import_chain'])
                add_pair(caller, target, evidence)
        self.reverse = {target: tuple(pairs[caller] for caller in sorted(pairs))
                        for target, pairs in self.reverse.items()}
        self.coverage = {
            'files_analyzed': len(good), 'files_failed': failed,
            'skipped_entries': len(report.get('skipped', [])),
            'diagnostic_count': len(report.get('diagnostics', [])),
            'symbols': len(self.symbols), 'calls': len(call_ids),
            'linked_calls': linked, 'unresolved_calls': unresolved,
            'possible_calls': possible, 'unknown_calls': unresolved - possible,
            'candidate_limited_calls': candidate_limits,
            'categories': dict(sorted(categories.items())),
            'include_possible': include_possible,
            'distinct_call_pairs': sum(len(pairs) for pairs in self.reverse.values())}

    def callers_of(self, symbol_id):
        """Sorted incoming call pairs, grouped by caller; no full-graph scan."""
        return self.reverse.get(symbol_id, ())

    def select(self, *, symbol_id=None, file=None, qualified_name=None):
        if symbol_id is not None:
            require(file is None and qualified_name is None, 'Choose an ID OR file plus qualified name.')
            candidates = [symbol_id] if symbol_id in self.symbols else []
        else:
            require(isinstance(file, str) and isinstance(qualified_name, str),
                    'Select by exact symbol ID or file plus qualified function name.')
            candidates = self.by_name.get((file, qualified_name), [])
        require(bool(candidates), 'Function not found. Use --list-functions to inspect available IDs.')
        require(len(candidates) == 1, 'Name is ambiguous; select an exact symbol ID with --symbol.')
        chosen = self.symbols[candidates[0]]
        require(chosen['kind'] in FUNCTION_KINDS, 'Choose a function or method, not a class.')
        return chosen['id']

    def functions(self, file=None):
        return [deepcopy(s) for _, s in sorted(self.symbols.items())
                if s['kind'] in FUNCTION_KINDS and (file is None or s['file'] == file)]
