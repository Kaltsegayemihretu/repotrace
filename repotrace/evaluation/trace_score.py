"""Compare the analyzer's call arrows with what really ran (traces from runtime_recorder).

Reads only JSON files and source text; it executes nothing.

Two questions are answered separately:
  PRECISION  Of the arrows we drew whose line really ran, how many pointed at what ran?
  RECALL     Of the project-to-project calls that really happened, how many did we have an arrow for?
"""
import argparse
import ast
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

FUNCTION_KINDS = ('FUNCTION', 'METHOD', 'NESTED_FUNCTION')
TAG = re.compile(r'\[P(\d\d)\]')
LAST_NAME = re.compile(r'([A-Za-z_]\w*)\s*$')
HARDEST_FIRST = ['impossible', 'dynamic', 'needs type inference', 'direct']


class Static:
    """The analyzer's report, indexed for lookups from runtime positions."""

    def __init__(self, report):
        self.symbols = {}
        self.by_file = defaultdict(list)
        self.calls = defaultdict(list)      # caller symbol id -> call records
        for file in report['files']:
            if file['status'] != 'ok':
                continue
            for symbol in file['symbols']:
                self.symbols[symbol['id']] = symbol
                self.by_file[file['file']].append(symbol)
            for call in file['calls']:
                self.calls[call['enclosing_symbol_id']].append(call)

    def symbol_for(self, file, name, line):
        """The one analyzed function that matches a runtime code object, else None."""
        if name == '<module>':
            module = f'{file}::module'
            return module if file in self.by_file else None
        found = []
        for symbol in self.by_file.get(file, []):
            if symbol['kind'] == 'CLASS' and symbol['name'] == name and line in (
                    symbol['span']['line'], symbol.get('start_line_including_decorators')):
                found.append(symbol)
            elif name == '<lambda>':
                if symbol['kind'] == 'LAMBDA' and symbol['span']['line'] == line:
                    found.append(symbol)
            elif (symbol['kind'] in FUNCTION_KINDS and symbol['name'] == name
                  and line in (symbol['span']['line'], symbol.get('start_line_including_decorators'))):
                found.append(symbol)
        return found[0]['id'] if len(found) == 1 else None


def merge_traces(traces):
    edges, executed, unmapped_note = {}, defaultdict(set), Counter()
    for trace in traces:
        for file, lines in trace.get('executed_lines', {}).items():
            executed[file].update(lines)
        for edge in trace['edges']:
            key = (json.dumps(edge['caller'], sort_keys=True), edge['call_line'],
                   json.dumps(edge['callee'], sort_keys=True))
            slot = edges.setdefault(key, {**edge, 'count': 0})
            slot['count'] += edge['count']
            slot['through_library'] = slot['through_library'] and edge['through_library']
            slot['via_wrapper'] = slot.get('via_wrapper', False) or edge.get('via_wrapper', False)
    return list(edges.values()), executed


def in_range(call, line):
    return call['span']['line'] <= line <= call['span']['end_line']


def name_only_match(call, callee):
    expression = call.get('expression')
    if (call['resolution'] != 'UNRESOLVED' or not isinstance(expression, str)
            or call.get('category') not in {'unresolved', 'unsupported_context'}):
        return False
    last = LAST_NAME.search(expression)
    if not last or last.group(1) != callee['name']:
        return False
    return callee['kind'] != 'METHOD' or expression.strip() != callee['name']


def tier_of(calls, callee_id, callee):
    """Highest tier at which any of these call records reaches the callee."""
    for call in calls:
        if call['resolution'] == 'LIKELY' and call['target_id'] == callee_id:
            return 'confident'
    for call in calls:
        if call['resolution'] == 'POSSIBLE' and any(c['target_id'] == callee_id for c in call.get('candidate_targets', [])):
            return 'uncertain'
    for call in calls:
        if name_only_match(call, callee):
            return 'name_only'
    return None


def why_missed(calls, callee):
    if not calls:
        return 'no call recorded on that line (implicit: operator, property, iteration, context manager or class creation)'
    for call in calls:
        if call['resolution'] == 'UNRESOLVED':
            reason = call.get('method_resolution_reason') or call.get('reason_code') or call.get('category')
            return f"{call.get('category', 'unresolved')}: {reason}"[:110]
    return 'resolved to different targets than what ran'


def difficulty_by_pattern(patterns_text):
    result = defaultdict(set)
    for line in patterns_text.splitlines():
        if line.startswith('| P') and line.count('|') >= 4:
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            text = cells[-1].lower()
            level = next((word for word in HARDEST_FIRST if word in text), 'unlabelled')
            result[cells[0]].add(level)
    return {key: next((w for w in HARDEST_FIRST if w in levels), 'unlabelled') for key, levels in result.items()}


def score(static_report, traces, root=None, patterns_text=''):
    static = Static(static_report)
    edges, executed = merge_traces(traces)
    root = Path(root) if root else None
    difficulty = difficulty_by_pattern(patterns_text)
    line_cache = {}

    def source_line(file, number):
        if root is None:
            return ''
        if file not in line_cache:
            try:
                line_cache[file] = (root / file).read_text(encoding='utf-8').splitlines()
            except OSError:
                line_cache[file] = []
        lines = line_cache[file]
        return lines[number - 1] if 0 < number <= len(lines) else ''

    parents_cache = {}

    def conditional_call(call):
        """True when this call may be skipped even though its line ran (a or f(), x if c else f())."""
        if root is None:
            return False
        file = call['file']
        if file not in parents_cache:
            try:
                tree = ast.parse((root / file).read_text(encoding='utf-8'))
            except (OSError, SyntaxError, ValueError):
                tree = None
            parents = {}
            if tree is not None:
                for parent in ast.walk(tree):
                    for child in ast.iter_child_nodes(parent):
                        parents[child] = parent
            parents_cache[file] = parents
        parents = parents_cache[file]
        s = call['span']
        node = next((n for n in parents if isinstance(n, ast.Call) and n.lineno == s['line']
                     and n.col_offset == s['column'] and n.end_lineno == s['end_line']
                     and n.end_col_offset == s['end_column']), None)
        while node is not None and node in parents:
            parent = parents[node]
            if isinstance(parent, ast.BoolOp) and node is not parent.values[0]:
                return True
            if isinstance(parent, ast.IfExp) and node is not parent.test:
                return True
            if isinstance(parent, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                return True
            if isinstance(parent, ast.stmt):
                return False
            node = parent
        return False

    # ---- recall: every real project-to-project call
    recall = {'scopes': {}}
    skipped = Counter()
    rows = []
    for edge in edges:
        if edge['caller'] is None:
            skipped['entered_from_outside (test runner, thread start)'] += 1
            continue
        if edge['through_library']:
            skipped['called back through library code (callbacks, asyncio, sorted(key=...))'] += 1
            continue
        caller_id = static.symbol_for(edge['caller']['file'], edge['caller']['name'], edge['caller']['line'])
        callee_id = static.symbol_for(edge['callee']['file'], edge['callee']['name'], edge['callee']['line'])
        if caller_id is None or callee_id is None:
            skipped['function not found in analysis (generated code, ambiguous lambda)'] += 1
            continue
        callee = static.symbols[callee_id]
        if callee['kind'] == 'CLASS':
            skipped['class body running at import (not a function call)'] += 1
            continue
        on_line = [c for c in static.calls.get(caller_id, []) if in_range(c, edge['call_line'])]
        tier = tier_of(on_line, callee_id, callee)
        loose = False
        if tier is None:
            tier = tier_of(static.calls.get(caller_id, []), callee_id, callee)
            loose = tier is not None
        text = source_line(edge['caller']['file'], edge['call_line'])
        rows.append({'caller': caller_id, 'callee': callee_id, 'line': edge['call_line'],
                     'file': edge['caller']['file'], 'tier': tier or 'missed', 'other_line': loose,
                     'count': edge['count'], 'why': None if tier else why_missed(on_line, callee),
                     'tags': TAG.findall(text), 'callee_file': edge['callee']['file']})

    def tally(selected):
        counts = Counter(r['tier'] for r in selected)
        calls = Counter()
        for r in selected:
            calls[r['tier']] += r['count']
        total = len(selected)
        return {'distinct_calls': total, 'by_tier': dict(counts), 'call_weighted': dict(calls),
                'covered_any_tier': total - counts.get('missed', 0),
                'covered_percent': round(100 * (total - counts.get('missed', 0)) / total, 1) if total else None,
                'confident_or_uncertain_percent': round(100 * (counts.get('confident', 0) + counts.get('uncertain', 0)) / total, 1) if total else None}

    def in_source(r):
        return not r['file'].startswith('tests/') and not r['callee_file'].startswith('tests/')
    recall['scopes']['all_project_code'] = tally(rows)
    recall['scopes']['source_only'] = tally([r for r in rows if in_source(r)])
    recall['not_scored'] = dict(skipped)
    recall['missed_reasons'] = Counter(r['why'] for r in rows if r['tier'] == 'missed' and in_source(r)).most_common(12)
    by_pattern = defaultdict(Counter)
    by_level = defaultdict(Counter)
    for r in rows:
        for tag in r['tags']:
            by_pattern[f'P{tag}'][r['tier']] += 1
            by_level[difficulty.get(f'P{tag}', 'unlabelled')][r['tier']] += 1
    recall['by_pattern'] = {k: dict(v) for k, v in sorted(by_pattern.items())}
    recall['by_difficulty'] = {k: dict(v) for k, v in by_level.items()}
    recall['missed_examples'] = [
        {'file': r['file'], 'line': r['line'], 'caller': r['caller'].split('::')[-1], 'callee': r['callee'].split('::')[-1],
         'why': r['why']} for r in rows if r['tier'] == 'missed' and in_source(r)][:15]

    # ---- precision: every arrow we drew
    runtime_by_caller = defaultdict(list)
    for edge in edges:
        if edge['caller'] is None:
            continue
        caller_id = static.symbol_for(edge['caller']['file'], edge['caller']['name'], edge['caller']['line'])
        callee_id = static.symbol_for(edge['callee']['file'], edge['callee']['name'], edge['callee']['line'])
        if caller_id and callee_id:
            runtime_by_caller[caller_id].append((edge['call_line'], callee_id))
    ran_anywhere = {callee for pairs in runtime_by_caller.values() for _, callee in pairs}
    precision = {}
    for resolution in ('LIKELY', 'POSSIBLE'):
        counts, sets = Counter(), []
        for caller_id, calls in static.calls.items():
            for call in calls:
                if call['resolution'] != resolution:
                    continue
                targets = ({call['target_id']} if resolution == 'LIKELY'
                           else {c['target_id'] for c in call.get('candidate_targets', [])})
                span = call['span']
                file = call['file']
                ran = any(span['line'] <= n <= span['end_line'] for n in executed.get(file, ()))
                if not ran:
                    counts['not_run (cannot judge)'] += 1
                    continue
                real = {callee for line, callee in runtime_by_caller.get(caller_id, []) if span['line'] <= line <= span['end_line']}
                real_any = {callee for _, callee in runtime_by_caller.get(caller_id, [])}
                if real & targets:
                    counts['right (called at that line)'] += 1
                elif real_any & targets:
                    counts['right (called from that function, other line)'] += 1
                elif any(static.symbols[t].get('is_async') and t in ran_anywhere for t in targets):
                    counts['cannot judge (async function ran; the trace cannot say who started it)'] += 1
                elif conditional_call(call):
                    counts['cannot judge (call may be skipped on a line that ran, e.g. a or f())'] += 1
                else:
                    counts['not seen (line ran, target not called)'] += 1
                    sets.append({'file': file, 'line': span['line'], 'expression': call.get('expression'),
                                 'drawn_targets': sorted(t.split('::')[-1] for t in targets)[:4],
                                 'what_ran_there': sorted(c.split('::')[-1] for c in real)[:4]})
                if resolution == 'POSSIBLE' and (real & targets or real_any & targets):
                    counts['_candidates_total'] += len(targets)
                    counts['_candidates_seen'] += len(targets & (real | real_any))
        judged = sum(v for k, v in counts.items() if k.startswith(('right', 'not seen')))
        right = sum(v for k, v in counts.items() if k.startswith('right'))
        precision[resolution] = {
            'arrows_total': sum(v for k, v in counts.items() if not k.startswith('_')),
            'counts': {k: v for k, v in counts.items() if not k.startswith('_')},
            'judged': judged, 'right': right,
            'percent_right': round(100 * right / judged, 1) if judged else None,
            'not_seen_examples': sets[:12]}
        if resolution == 'POSSIBLE' and counts.get('_candidates_total'):
            precision[resolution]['candidates_per_right_site_seen_percent'] = round(
                100 * counts['_candidates_seen'] / counts['_candidates_total'], 1)
    precision['NAME_ONLY'] = name_only_precision(static, executed, runtime_by_caller, conditional_call)
    return {'schema_version': '0.1', 'report_kind': 'trace_score', 'recall': recall, 'precision': precision,
            'runtime_edges_total': len(edges),
            'limitations': [
                'Only code that ran is judged; unrun arrows are counted as not judgeable.',
                'Runtime sees project functions only: a library callee at a drawn line looks like "not seen".',
                'A recorded run is not every possible run; it is evidence from the runs given.']}


def name_only_precision(static, executed, runtime_by_caller, conditional_call):
    """For each project function, how many name-only sites that ran really called it."""
    from repotrace.changes.name_matches import COMMON_NAMES
    functions = [s for s in static.symbols.values() if s['kind'] in ('FUNCTION', 'METHOD')
                 and not (s['name'].startswith('__') and s['name'].endswith('__'))]
    names = Counter(s['name'] for s in functions)
    unresolved = [(caller, call) for caller, calls in static.calls.items() for call in calls
                  if call['resolution'] == 'UNRESOLVED']
    by_group = defaultdict(lambda: Counter())
    for function in functions:
        group = ('common library-style name' if function['name'] in COMMON_NAMES else
                 'name shared by several project functions' if names[function['name']] > 1 else
                 'name unique in the project')
        for caller, call in unresolved:
            if not name_only_match(call, function):
                continue
            span = call['span']
            if not any(span['line'] <= n <= span['end_line'] for n in executed.get(call['file'], ())):
                by_group[group]['sites_not_run'] += 1
                continue
            if conditional_call(call):
                by_group[group]['sites_cannot_judge'] += 1
                continue
            hit = any(callee == function['id'] and span['line'] <= line <= span['end_line']
                      for line, callee in runtime_by_caller.get(caller, []))
            by_group[group]['sites_that_ran'] += 1
            by_group[group]['real_calls' if hit else 'other_objects'] += 1
    result = {}
    for group, c in by_group.items():
        ran = c['sites_that_ran']
        result[group] = {**dict(c), 'percent_real': round(100 * c['real_calls'] / ran, 1) if ran else None}
    return result


def text_summary(result):
    out = []
    p = result['precision']
    out.append('PRECISION - of the arrows we drew, how many were right where the code ran')
    for key, label in (('LIKELY', 'confident'), ('POSSIBLE', 'uncertain')):
        d = p[key]
        out.append(f"  {label:10} {d['arrows_total']} arrows; {d['judged']} judgeable; right {d['right']} "
                   f"({d['percent_right']}%)" + ('' if d['percent_right'] is not None else ''))
        for name, n in d['counts'].items():
            out.append(f"      {n:4}  {name}")
    out.append('  name-only sites (weak tier) that ran: how often they were the real call')
    for group, d in result['precision']['NAME_ONLY'].items():
        out.append(f"      {group}: {d.get('real_calls', 0)} of {d.get('sites_that_ran', 0)} ({d['percent_real']}%)")
    out.append('')
    r = result['recall']
    out.append('RECALL - of the real project-to-project calls, how many had an arrow')
    for scope, d in r['scopes'].items():
        out.append(f"  {scope}: {d['distinct_calls']} distinct calls; any arrow {d['covered_percent']}% "
                   f"(confident+uncertain {d['confident_or_uncertain_percent']}%)  {d['by_tier']}")
    out.append('  not scored: ' + '; '.join(f'{v} {k}' for k, v in r['not_scored'].items()))
    out.append('  by difficulty label from PATTERNS.md: ' + json.dumps(r['by_difficulty']))
    out.append('  why the misses (source only):')
    for reason, n in r['missed_reasons']:
        out.append(f'      {n:4}  {reason}')
    return '\n'.join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path, help='Repository report of the SAME source tree that ran')
    parser.add_argument('--trace', required=True, action='append', type=Path)
    parser.add_argument('--root', type=Path, help='The source tree (to read call lines and PATTERNS.md)')
    parser.add_argument('--patterns', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text())
    traces = [json.loads(p.read_text()) for p in args.trace]
    patterns = args.patterns.read_text(encoding='utf-8') if args.patterns else ''
    result = score(report, traces, args.root, patterns)
    if args.output:
        args.output.write_text(json.dumps(result, indent=1))
    print(text_summary(result))


if __name__ == '__main__':
    main()
