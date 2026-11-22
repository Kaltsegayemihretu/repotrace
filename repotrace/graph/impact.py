"""Bounded reverse-call traversal, independent of AST extraction and CLI I/O."""
from collections import deque
from copy import deepcopy

from repotrace.graph.graph_index import require


def analyze_impact(index, target_id, *, max_depth=20, max_nodes=1000, max_edges=100_000):
    """Return one shortest witness chain per caller, stored as shared next links.

    max_nodes counts affected callers, excluding the selected target.
    max_edges counts inspected caller pairs, including already-visited nodes.
    Limits bound this query, not the earlier JSON load or index construction.
    """
    require(type(max_depth) is int and max_depth >= 0, 'max_depth must be a nonnegative integer.')
    for value in (max_nodes, max_edges):
        require(type(value) is int and value > 0, 'max_nodes and max_edges must be positive integers.')
    index.select(symbol_id=target_id)
    distances = {target_id: 0}
    possible_path = {target_id: False}
    queue = deque([target_id])
    affected = []
    boundaries = set()
    examined = 0
    stop = False
    while queue and not stop:
        current = queue.popleft()
        callers = index.callers_of(current)
        if distances[current] >= max_depth:
            if callers:
                # Conservative boundary: some of these callers may already be visited.
                boundaries.add('max_depth')
            continue
        for edge in callers:
            if examined >= max_edges:
                boundaries.add('max_edges')
                stop = True
                break
            examined += 1
            caller = edge['source']
            if caller in distances:
                continue
            if len(affected) >= max_nodes:
                boundaries.add('max_nodes')
                stop = True
                break
            distance = distances[current] + 1
            distances[caller] = distance
            possible_path[caller] = possible_path[current] or edge['evidence']['resolution'] == 'POSSIBLE'
            queue.append(caller)
            affected.append({
                'symbol': deepcopy(index.symbols[caller]), 'distance': distance,
                'relationship': 'direct' if distance == 1 else 'indirect',
                'next_toward_target': current,
                'path_has_possible_link': possible_path[caller],
                'witness_call': deepcopy(edge),
                'unresolved_outgoing_calls': index.unresolved_by_source.get(caller, 0)})
    warnings = [
        'Potential review/test candidates, not proof of breakage or runtime execution.',
        'Only selected recorded call links are followed; data dependencies and other relationship types are excluded.',
        'No caller found does not prove that a function is unused or safe to change.',
        'One shortest witness per caller is retained, not all paths or all call sites.',
        'IDs and this result belong to the analyzed snapshot; regenerate after source changes.']
    if index.coverage['unresolved_calls']:
        warnings.append('Unresolved calls may hide additional callers, including uncertain candidates and external or dynamic targets.')
    if index.coverage['candidate_limited_calls']:
        warnings.append('Some call sites reached the candidate limit; additional possible targets were omitted before this query.')
    if index.include_possible:
        warnings.append('Candidate mode is enabled: POSSIBLE links are hypotheses, not proven dispatch targets. Each witness chain marks whether it uses a candidate.')
    elif index.coverage['possible_calls']:
        warnings.append('Method/constructor candidates are excluded in this mode. Use include_possible / --include-possible to review them separately.')
    if index.source_status == 'partial' or index.coverage['files_failed']:
        warnings.append('Source analysis is partial; failed files or diagnostics may leave gaps.')
    if index.coverage['skipped_entries']:
        warnings.append('Excluded files/directories were not fully analyzed.')
    if boundaries:
        warnings.append('A traversal limit was reached; the result may omit additional callers.')
    files = sorted({a['symbol']['file'] for a in affected})
    return {
        'schema_version': '0.2', 'report_kind': 'impact',
        'resolution_policy': 'likely_and_possible' if index.include_possible else 'likely_only',
        'status': 'limited' if boundaries else 'ok',
        'repository_root': index.root, 'source_report_status': index.source_status,
        'target': deepcopy(index.symbols[target_id]), 'affected': affected,
        'summary': {'affected_functions': len(affected),
                    'direct_callers': sum(a['distance'] == 1 for a in affected),
                    'indirect_callers': sum(a['distance'] > 1 for a in affected),
                    'witnesses_with_possible_links': sum(a['path_has_possible_link'] for a in affected),
                    'affected_files': files},
        'traversal': {'max_depth': max_depth, 'max_nodes': max_nodes, 'max_edges': max_edges,
                      'examined_call_pairs': examined, 'limits_reached': sorted(boundaries),
                      'complete_for_recorded_graph': not boundaries},
        'coverage': deepcopy(index.coverage), 'warnings': warnings}


def explain_path(result, caller_id):
    """Expand a single witness on demand; do not expand all paths for large graphs."""
    target = result['target']['id']
    records = {a['symbol']['id']: a for a in result['affected']}
    require(caller_id == target or caller_id in records, 'Caller is not in this impact result.')
    path, seen = [], set()
    current = caller_id
    while current != target:
        require(current not in seen and current in records, 'Broken witness chain.')
        seen.add(current)
        path.append(current)
        current = records[current]['next_toward_target']
    return path + [target]
