"""Two-commit change-to-impact workflow built on the existing analyzer and impact engine.

repo + BASE + HEAD -> pin commits -> read both snapshots from Git objects -> analyze both
with identical settings -> detect changed functions -> query the matching graph(s).
"""
import hashlib
import platform
from time import perf_counter
import tempfile
from pathlib import Path

from repotrace.changes.change_detector import detect_changes
from repotrace.changes.decorator_impact import (_tolerant_index, callers_of_decorated, decorated_by, decorator_bases,
                              decorators_of_change)
from repotrace.changes.git_snapshot import (CONFIG_FILES, GitError, check_repository, materialize_commit, resolve_commit,
                          worktree_is_dirty)
from repotrace.graph.graph_index import GraphIndex
from repotrace.graph.impact import analyze_impact
from repotrace.changes.name_matches import find_name_matches
from repotrace.mapping.repository import analyze_repository, pytest_configuration
from repotrace.changes.signature_review import review_signatures
from repotrace.changes.test_selection import DEFAULT_TEST_LIMIT, TestSymbols, build_test_plan, match_tests, select_tests

SCHEMA_VERSION = '0.2'


def _compact(result):
    """Keep affected callers and traversal facts; shared metadata is reported once."""
    return {'target': result['target'], 'status': result['status'],
            'summary': result['summary'], 'traversal': result['traversal'],
            'affected': result['affected']}


def _snapshot_report(repo, commit, label, workdir, settings):
    folder = Path(workdir) / label
    snapshot = materialize_commit(repo, commit, folder)
    report = analyze_repository(folder, **settings)
    name, config = pytest_configuration(folder)
    report['test_configuration'] = config
    snapshot['test_configuration'] = name
    snapshot['configuration_hashes'] = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
                                        for name in CONFIG_FILES if (folder / name).is_file()}
    return report, snapshot


def _candidates(change, results, after_index, changed_after):
    """Merge before/after callers into one list of places to inspect."""
    merged = {}
    for side, result in results.items():
        for item in result['affected']:
            symbol = item['symbol']
            key = (symbol['file'], symbol['qualified_name'], symbol['kind'])
            entry = merged.setdefault(key, {
                'file': key[0], 'qualified_name': key[1], 'kind': key[2], 'sides': {}})
            entry['sides'][side] = {
                'symbol_id': symbol['id'], 'span': symbol['span'], 'distance': item['distance'],
                'relationship': item['relationship'],
                'path_has_possible_link': item['path_has_possible_link'],
                'witness_call': item['witness_call'],
                'unresolved_outgoing_calls': item['unresolved_outgoing_calls']}
    for entry in merged.values():
        sides = entry['sides']
        entry['distance'] = min(s['distance'] for s in sides.values())
        entry['relationship'] = 'direct' if entry['distance'] == 1 else 'indirect'
        # Uncertain only if every side that found it relied on a candidate link.
        entry['uncertain'] = all(s['path_has_possible_link'] for s in sides.values())
        if set(sides) == {'before'}:
            entry['exists_after'] = bool(after_index and after_index.by_name.get(
                (entry['file'], entry['qualified_name'])))
            if change['change'] == 'deleted':
                # An untouched caller of a deleted function probably still calls the old name.
                entry['may_still_call_removed'] = (
                    entry['distance'] == 1 and
                    (entry['file'], entry['qualified_name']) not in changed_after)
    return sorted(merged.values(), key=lambda e: (e['distance'], e['file'], e['qualified_name']))


def _confident_callers_by_name(after_report, symbols):
    """{final name: {(file, caller)}} for confident calls in the newer version.

    Lets a deleted function that merely moved (callers now reach the same name elsewhere)
    be told apart from one whose callers were forgotten."""
    found = {}
    for file in after_report['files']:
        if file['status'] != 'ok':
            continue
        for call in file['calls']:
            target, caller = symbols.get(call.get('target_id')), symbols.get(call.get('enclosing_symbol_id'))
            if call['resolution'] == 'LIKELY' and target and caller:
                found.setdefault(target['qualified_name'].split('.')[-1], set()).add(
                    (caller['file'], caller['qualified_name']))
    return found


def _add_decorator_effects(entry, change, decorated, refused_callers, symbols, index, limit, max_depth,
                           max_nodes, max_edges, warnings, after_report=None, max_name_matches=20):
    """A changed decorator affects the callers of every function it wraps."""
    wrapped_ids = []
    for _, wrapped in decorators_of_change(symbols, decorated, change['file'], change['qualified_name']):
        wrapped_ids += [w for w in wrapped if w not in wrapped_ids]
    if not wrapped_ids:
        return
    entry['decorator_effect'] = {
        'wrapped_functions': [symbols[w]['qualified_name'] for w in wrapped_ids if w in symbols],
        'truncated': len(wrapped_ids) > limit, 'classes_not_traced': 0, 'name_matches': {},
        'wrapped_detail': [{'id': w, 'name': symbols[w]['qualified_name'], 'file': symbols[w]['file'],
                            'line': symbols[w]['span']['line'], 'kind': symbols[w]['kind']}
                           for w in wrapped_ids[:limit] if w in symbols]}
    merged = {(c['file'], c['qualified_name']): c for c in entry['review_candidates']}
    for wrapped_id in wrapped_ids[:limit]:
        symbol = symbols.get(wrapped_id)
        if symbol is None:
            continue
        if symbol['kind'] == 'CLASS':
            entry['decorator_effect']['classes_not_traced'] += 1
            continue
        # Direct callers: graph links to the wrapped function, plus calls the analyzer refused
        # to link because the target is decorated.
        direct = {}
        for edge in index.callers_of(wrapped_id):
            direct[edge['source']] = (edge, edge['evidence']['resolution'] == 'POSSIBLE')
        for caller_id, call in refused_callers.get(wrapped_id, []):
            if caller_id in symbols and caller_id not in direct:
                direct[caller_id] = ({'source': caller_id, 'target': wrapped_id, 'call_site_count': 1,
                                      'likely_call_site_count': 1, 'possible_call_site_count': 0,
                                      'evidence': {'call_id': call['id'], 'file': call['file'], 'span': call['span'],
                                                   'resolution': 'LIKELY', 'resolution_kind': 'decorated_function',
                                                   'reason': 'The name resolves to a decorated function; the call runs its wrapper first.'}}, False)
        reached = {}
        for caller_id, (evidence, possible) in direct.items():
            reached[caller_id] = (1, possible, evidence)
        for caller_id, (evidence, possible) in list(direct.items()):
            if caller_id not in index.symbols:
                continue
            result = analyze_impact(index, caller_id, max_depth=max_depth, max_nodes=max_nodes, max_edges=max_edges)
            warnings.update(result['warnings'])
            for item in result['affected']:
                caller = item['symbol']['id']
                distance = item['distance'] + 1
                if caller not in reached or distance < reached[caller][0]:
                    reached[caller] = (distance, possible or item['path_has_possible_link'], item['witness_call'])
        for caller_id, (distance, possible, evidence) in reached.items():
            caller = symbols.get(caller_id)
            if caller is None:
                continue
            key = (caller['file'], caller['qualified_name'])
            side = {'symbol_id': caller_id, 'span': caller['span'], 'distance': distance,
                    'relationship': 'direct' if distance == 1 else 'indirect',
                    'path_has_possible_link': possible, 'witness_call': evidence,
                    'unresolved_outgoing_calls': index.unresolved_by_source.get(caller_id, 0)}
            existing = merged.get(key)
            if existing is None:
                existing = merged[key] = {
                    'file': key[0], 'qualified_name': key[1], 'kind': caller['kind'], 'sides': {'after': side},
                    'distance': distance, 'relationship': side['relationship'], 'uncertain': possible}
                entry['review_candidates'].append(existing)
            elif distance < existing['distance']:
                existing['distance'], existing['relationship'] = distance, side['relationship']
            existing.setdefault('via_decorator', [])
            if symbol['qualified_name'] not in existing['via_decorator']:
                existing['via_decorator'].append(symbol['qualified_name'])
    # Weak evidence, kept apart: unresolved calls that use a wrapped function's name.
    covered = {(c['file'], c['qualified_name']) for c in entry['review_candidates']}
    for wrapped_id in wrapped_ids[:limit]:
        symbol = symbols.get(wrapped_id)
        if symbol is None or symbol['kind'] == 'CLASS' or after_report is None:
            continue
        found = find_name_matches(after_report, symbol['qualified_name'], symbol['kind'],
                                  covered_callers=covered, limit=max_name_matches)
        if found['total']:
            entry['decorator_effect']['name_matches'][symbol['qualified_name']] = found
    entry['review_candidates'].sort(key=lambda e: (e['distance'], e['file'], e['qualified_name']))


def analyze_change_impact(repo, base, head, *, source_roots=None, exclude_dirs=(),
                          include_possible=False, max_changes=500, max_name_matches=20, max_decorated=50, max_depth=20,
                          max_nodes=1000, max_edges=100_000, max_tests=DEFAULT_TEST_LIMIT, progress=None, keep=None):
    """Return a commit-tagged change_impact report. Raises GitError for unusable input.

    progress(stage, detail) is called as the work moves along; it never changes the result.
    """
    tell = progress or (lambda stage, detail='': None)
    if type(max_changes) is not int or max_changes < 1:
        raise GitError('max_changes must be a positive integer.')
    if type(max_name_matches) is not int or max_name_matches < 0:
        raise GitError('max_name_matches must be a nonnegative integer.')
    if type(max_tests) is not int or max_tests < 1:
        raise GitError('max_tests must be a positive integer.')
    tell('check', 'Checking the repository and the two commits')
    repo = check_repository(repo)
    base_commit, head_commit = resolve_commit(repo, base), resolve_commit(repo, head)
    settings = {'source_roots': list(source_roots) if source_roots else None,
                'exclude_dirs': tuple(exclude_dirs)}
    report = {
        'schema_version': SCHEMA_VERSION, 'report_kind': 'change_impact', 'status': 'ok',
        'repository': str(repo), 'worktree_dirty': worktree_is_dirty(repo),
        'base': {'ref': base, 'commit': base_commit},
        'head': {'ref': head, 'commit': head_commit},
        'settings': {'source_roots': settings['source_roots'] or ['.'],
                     'excluded_directory_names': sorted(settings['exclude_dirs']),
                     'include_possible': include_possible, 'max_changes': max_changes,
                     'max_name_matches': max_name_matches,
                     'max_tests': max_tests,
                     'max_depth': max_depth, 'max_nodes': max_nodes, 'max_edges': max_edges},
        'analyzer_python': platform.python_version(),
        'snapshots': {}, 'files': [], 'changes': [], 'gaps': [], 'warnings': [],
        'policy': {}, 'summary': {}}
    if base_commit == head_commit:
        report['warnings'].append('BASE and HEAD are the same commit; there is nothing to compare.')
    with tempfile.TemporaryDirectory(prefix='repotrace-') as workdir:
        reports = {}
        for label, commit in (('before', base_commit), ('after', head_commit)):
            tell('analyze_' + label, f"Reading and analyzing the {'older' if label == 'before' else 'newer'} commit ({commit[:8]})")
            analyzed, snapshot = _snapshot_report(repo, commit, label, workdir, settings)
            reports[label] = analyzed
            report['snapshots'][label] = {
                'commit': commit, 'status': analyzed['status'],
                'python_files_written': snapshot['python_files_written'],
                'skipped_from_git': snapshot['skipped'], 'summary': analyzed['summary'],
                'diagnostics': analyzed['diagnostics'], 'test_configuration': snapshot['test_configuration'],
                'configuration_hashes': snapshot['configuration_hashes']}
    if any(r['status'] == 'error' for r in reports.values()):
        report['status'] = 'error'
        report['warnings'].append('A snapshot could not be analyzed; no change-impact is available.')
        return report
    tell('compare', 'Comparing the two versions function by function')
    detected = detect_changes(reports['before'], reports['after'])
    report['files'], report['gaps'], report['policy'] = (
        detected['files'], detected['gaps'], detected['policy'])
    report['constants'] = detected['constants']
    indexes = {label: GraphIndex(reports[label], include_possible=include_possible)
               for label in reports}
    report['coverage'] = {label: idx.coverage for label, idx in indexes.items()}
    if keep is not None:       # callers that want to explore the graph later (the app) ask for the indexes and the per-version reports
        keep['indexes'] = indexes
        keep['reports'] = reports
    warnings, limited = set(), False
    definitions = detected['definitions']
    signature_checks = review_signatures(reports['before'], reports['after'], definitions[:max_changes])
    report['signature_review'] = {k: v for k, v in signature_checks.items() if k != 'by_target'}
    if signature_checks['summary']['limits_reached']:
        limited = True
        report['warnings'].append('Call-parameter review reached a limit; see signature_review for coverage.')
    if len(definitions) > max_changes:
        limited = True
        report['warnings'].append(
            f'{len(definitions)} changed functions found; only the first {max_changes} were queried.')
    changed_after = {(c['file'], c['qualified_name']) for c in definitions
                     if c['change'] != 'deleted'}
    selection_started = perf_counter()
    symbols_by_side = {side: TestSymbols(source) for side, source in reports.items()}
    before_symbols, after_symbols = symbols_by_side['before'], symbols_by_side['after']
    correspondence = match_tests(before_symbols, after_symbols)
    unavailable = {f['file'] for f in reports['after']['files'] if f['status'] != 'ok'}
    unavailable.update(row['path'] for row in report['snapshots']['after']['skipped_from_git'])
    alternative_indexes = {
        side: GraphIndex(reports[side], include_possible=not include_possible)
        for side, index in indexes.items() if index.coverage['possible_calls']}
    selection_seconds = perf_counter() - selection_started
    decorator_names, decorated, refused_callers = decorator_bases(reports['after']), None, {}
    callers_by_name = None
    for position, change in enumerate(definitions[:max_changes], 1):
        tell('impact', f"Finding what could be affected ({position} of {min(len(definitions), max_changes)})")
        results = {}
        for side in ('before', 'after'):
            if side in change:
                result = analyze_impact(indexes[side], change[side]['id'], max_depth=max_depth,
                                        max_nodes=max_nodes, max_edges=max_edges)
                warnings.update(result['warnings'])
                limited = limited or result['status'] == 'limited'
                results[side] = _compact(result)
        entry = dict(change)
        review = signature_checks['by_target'].get(change.get('after', {}).get('id'))
        if review is not None:
            entry['signature_review'] = review
        entry['impact'] = results
        entry['review_candidates'] = _candidates(change, results, indexes['after'], changed_after)
        selection_started = perf_counter()
        alternatives = {}
        for side, index in alternative_indexes.items():
            if side in results:
                alternative = analyze_impact(index, change[side]['id'], max_depth=max_depth,
                                             max_nodes=max_nodes, max_edges=max_edges)
                alternatives[side] = _compact(alternative)
                limited = limited or alternative['status'] == 'limited'
                if alternative['status'] == 'limited':
                    warnings.add('A test recommendation traversal reached a limit.')
        entry['test_selection'] = select_tests(change, results, symbols_by_side, correspondence, unavailable, alternatives)
        entry['test_selection']['traversal'] = {
            side: {'supported': (alternatives.get(side, result) if include_possible else result)['traversal'],
                   'including_possible': (result if include_possible else alternatives.get(side, result))['traversal']}
            for side, result in results.items()}
        selection_seconds += perf_counter() - selection_started
        if change['change'] == 'deleted':
            if callers_by_name is None:
                callers_by_name = _confident_callers_by_name(reports['after'], after_symbols)
            moved = callers_by_name.get(change['qualified_name'].split('.')[-1], set())
            for candidate in entry['review_candidates']:
                if candidate.get('may_still_call_removed') and (candidate['file'], candidate['qualified_name']) in moved:
                    candidate['may_still_call_removed'] = False
                    candidate['reaches_same_name_in_head'] = True
        if change['change'] != 'deleted' and change['qualified_name'].split('.')[0] in decorator_names:
            if decorated is None:
                try:
                    tolerant = _tolerant_index(reports['after'])
                    refused_callers = callers_of_decorated(reports['after'], tolerant)
                    decorated, not_followed = decorated_by(reports['after'], tolerant)
                    if not_followed:
                        report['warnings'].append(
                            f'{not_followed} decorator(s) could not be followed to a project definition.')
                except (SyntaxError, ValueError, RecursionError):
                    decorated = {}
            _add_decorator_effects(entry, change, decorated, refused_callers, after_symbols, indexes['after'],
                                   max_decorated, max_depth, max_nodes, max_edges, warnings,
                                   reports['after'], max_name_matches)
        # Weak, separate evidence: unresolved calls in HEAD that use this function's name.
        # For a deleted function these are places that still refer to a name that no longer exists.
        deleted = change['change'] == 'deleted'
        covered = set() if deleted else {(c['file'], c['qualified_name'])
                                         for c in entry['review_candidates']}
        entry['name_matches'] = find_name_matches(
            reports['after'], change['qualified_name'], change['kind'],
            covered_callers=covered, limit=max_name_matches,
            deleted=deleted)
        if deleted:
            entry['name_matches']['meaning'] = 'still_referenced_in_head'
            referenced = {(s['file'], s['caller']) for s in entry['name_matches']['sites']}
            for candidate in entry['review_candidates']:
                candidate['still_references_removed_name'] = (
                    (candidate['file'], candidate['qualified_name']) in referenced)
        report['changes'].append(entry)
    selection_started = perf_counter()
    for change in definitions[max_changes:]:
        # Directly changed tests and hooks can still be selected without a graph query.
        targets = {side: {'target': change[side]} for side in symbols_by_side if side in change}
        report['changes'].append({
            **change, 'impact': {}, 'review_candidates': [],
            'test_selection': select_tests(change, targets, symbols_by_side, correspondence, unavailable),
            'not_queried': 'max_changes budget reached'})
    selection_seconds += perf_counter() - selection_started
    report['warnings'] += sorted(warnings)
    selection_started = perf_counter()
    plan = report['test_plan'] = build_test_plan(report['changes'], max_tests)
    for category in ('removed_tests', 'unverified_tests'):
        entries = {}
        for idx, change in enumerate(report['changes']):
            for test in change.get('test_selection', {}).get(category, []):
                key = (test['file'], test['name'])
                item = entries.setdefault(key, {**test, 'reasons': []})
                item['reasons'].append({'change_index': idx, 'evidence': test['evidence']})
        plan[category] = list(entries.values())
    advice = plan['broader_testing'] = []
    configs = {side: snap['configuration_hashes'] for side, snap in report['snapshots'].items()}
    for path in sorted(CONFIG_FILES):
        if configs['before'].get(path) != configs['after'].get(path):
            advice.append({'file': path, 'scope': '.', 'reason': 'Configuration changed. Run the project test suite using the head configuration.'})
    for file in report['files']:
        if Path(file['file']).name == 'conftest.py' and file['change'] != 'cosmetic':
            advice.append({'file': file['file'], 'scope': str(Path(file['file']).parent.as_posix()),
                           'reason': 'Shared test setup changed. Run the affected directory suite.'})
    for gap in report['gaps']:
        advice.append({'file': gap['file'], 'scope': '.', 'reason': gap['message'] + ' Run the project test suite.'})
    if limited or any(cov['unresolved_calls'] or cov['candidate_limited_calls'] for cov in report['coverage'].values()):
        advice.append({'scope': '.', 'reason': 'Unresolved or bounded graph traversal can omit relevant tests. Run the project test suite.'})
    for side, symbols in symbols_by_side.items():
        for key in ('addopts', 'norecursedirs'):
            if symbols.config.get(key):
                advice.append({'scope': '.', 'reason': f'Pytest {key} in {side} may alter collection. Verify test targets in the project environment.'})
    if any(snap['skipped_from_git'] for snap in report['snapshots'].values()):
        advice.append({'scope': '.', 'reason': 'Some snapshot paths could not be read. Run the project test suite.'})
    if plan['unverified_tests']:
        advice.append({'scope': '.', 'reason': 'Some prior tests could not be matched to a runnable head test. Confirm collection and run the project test suite.'})
    for change in report['changes']:
        selection = change.get('test_selection')
        if selection is not None and not selection['tests']:
            selection['broader_testing'] = 'No linked runnable tests found for this change. Run the relevant component tests or the project suite.'
    if not plan['all_tests']:
        advice.append({'scope': '.', 'reason': 'No linked tests found. Run the usual project test suite.'})
    plan['selection_seconds'] = round(selection_seconds + perf_counter() - selection_started, 6)
    counts = {kind: sum(c['change'] == kind for c in definitions)
              for kind in ('modified', 'added', 'deleted')}
    failed = any(g['type'] == 'analysis_failed' for g in report['gaps'])
    partial = failed or any(r['status'] == 'partial' for r in reports.values())
    report['status'] = 'limited' if limited else 'partial' if partial else 'ok'
    report['summary'] = {
        'files_changed': len(report['files']), 'functions_modified': counts['modified'],
        'functions_added': counts['added'], 'functions_deleted': counts['deleted'],
        'functions_via_constants': sum(c.get('reason') == 'reads_changed_constant' for c in definitions),
        'review_candidates': sum(len(c['review_candidates']) for c in report['changes']),
        'name_match_sites': sum(m.get('total', 0) for m in (c.get('name_matches', {}) for c in report['changes'])
                                if not m.get('noisy_name')),
        'decorator_name_match_sites': sum(m['total'] for c in report['changes']
                                          for m in c.get('decorator_effect', {}).get('name_matches', {}).values()),
        'name_match_sites_noisy_names': sum(m.get('total', 0) for m in (c.get('name_matches', {}) for c in report['changes'])
                                            if m.get('noisy_name')),
        'gaps': len(report['gaps'])}
    report['warnings'].append(
        'Review suggestions are potential places to inspect, not proof of breakage or safety.')
    if report['gaps']:
        report['warnings'].append(
            'Gaps list changes that were not turned into impact results; do not read an empty result as safe.')
    return report
