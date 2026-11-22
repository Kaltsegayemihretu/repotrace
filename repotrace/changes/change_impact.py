"""Two-commit change-to-impact workflow built on the existing analyzer and impact engine.

repo + BASE + HEAD -> pin commits -> read both snapshots from Git objects -> analyze both
with identical settings -> detect changed functions -> query the matching graph(s).
"""
import platform
import tempfile
from pathlib import Path

from repotrace.changes.change_detector import detect_changes
from repotrace.changes.decorator_impact import (_tolerant_index, callers_of_decorated, decorated_by, decorator_bases,
                              decorators_of_change)
from repotrace.changes.git_snapshot import (GitError, check_repository, materialize_commit, resolve_commit,
                          worktree_is_dirty)
from repotrace.graph.graph_index import GraphIndex
from repotrace.graph.impact import analyze_impact
from repotrace.changes.name_matches import find_name_matches
from repotrace.mapping.repository import analyze_repository
from repotrace.changes.signature_review import review_signatures
from repotrace.changes.test_selection import DEFAULT_TEST_LIMIT, build_test_plan, select_tests

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
                'diagnostics': analyzed['diagnostics']}
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
    after_symbols = {s['id']: s for f in reports['after']['files'] if f['status'] == 'ok' for s in f['symbols']}
    before_symbols = {s['id']: s for f in reports['before']['files'] if f['status'] == 'ok' for s in f['symbols']}
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
        entry['test_selection'] = select_tests(change, results, {'before': before_symbols, 'after': after_symbols})
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
    for change in definitions[max_changes:]:
        report['changes'].append({**change, 'impact': {}, 'review_candidates': [],
                                  'not_queried': 'max_changes budget reached'})
    report['warnings'] += sorted(warnings)
    report['test_plan'] = build_test_plan(report['changes'], max_tests)
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
