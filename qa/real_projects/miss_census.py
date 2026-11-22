"""Miss census: every real source-to-source call (from a runtime_recorder trace) that the static map does NOT cover with a
sure or maybe arrow, grouped by the shape of the call and the resolver's stated reason. Reads JSON and source only.

    python3 qa/real_projects/miss_census.py NAME MAP.json TRACE.json SRC_ROOT [OUT.json]
"""
import ast, json, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from repotrace.evaluation.trace_score import Static, merge_traces, in_range, tier_of, why_missed   # noqa: E402

name, map_path, trace_path, src = sys.argv[1:5]
out_path = sys.argv[5] if len(sys.argv) > 5 else None
report = json.load(open(map_path))
static = Static(report)
edges, _ = merge_traces([json.load(open(trace_path))])
is_test = lambda f: f.startswith(('tests/', 'test/')) or '/tests/' in f or f.split('/')[-1].startswith('test_') or f.endswith('conftest.py')
trees = {}

def node_at(file, call):
    if file not in trees:
        try:
            trees[file] = ast.parse((Path(src) / file).read_text(encoding='utf-8'))
        except Exception:
            trees[file] = None
    t, s = trees[file], call['span']
    if t is None:
        return None
    return next((n for n in ast.walk(t) if isinstance(n, ast.Call) and n.lineno == s['line'] and n.col_offset == s['column']), None)

def shape(call, caller):
    """A plain-words shape for the callee expression."""
    expr = call.get('expression') or ''
    params = {p['name'] for p in (static.symbols.get(caller, {}).get('parameters') or [])}
    node = node_at(call['file'], call)
    func = node.func if node is not None else None
    if isinstance(func, ast.Name):
        return 'bare name: f()' if func.id not in params else 'parameter called: p()'
    if isinstance(func, ast.Attribute):
        base = func.value
        if isinstance(base, ast.Name):
            if base.id in ('self', 'cls'):
                return f'{base.id}.method()'
            if base.id in params:
                return 'parameter.method()'
            return 'name.method() (local, global or module)'
        if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) and base.value.id in ('self', 'cls'):
            return 'self.field.method()'
        if isinstance(base, ast.Call):
            return 'call().method()'
        if isinstance(base, ast.Subscript):
            return 'x[k].method()'
        return 'other attribute chain'
    if isinstance(func, (ast.Subscript, ast.Call)):
        return 'computed callee: x[k]() or f()()'
    return 'unknown shape' if func is None else type(func).__name__

rows = []
for edge in edges:
    if edge['caller'] is None or edge['through_library']:
        continue
    caller = static.symbol_for(edge['caller']['file'], edge['caller']['name'], edge['caller']['line'])
    callee = static.symbol_for(edge['callee']['file'], edge['callee']['name'], edge['callee']['line'])
    if caller is None or callee is None or static.symbols[callee]['kind'] == 'CLASS':
        continue
    if is_test(edge['caller']['file']) or is_test(edge['callee']['file']):
        continue
    on_line = [c for c in static.calls.get(caller, []) if in_range(c, edge['call_line'])]
    tier = tier_of(on_line, callee, static.symbols[callee]) or 'missed'
    if tier in ('confident', 'uncertain'):
        rows.append({'tier': tier}); continue
    named = [c for c in on_line if (c.get('expression') or '').split('.')[-1] == static.symbols[callee]['name']] or on_line
    call = named[0] if named else None
    callee_sym = static.symbols[callee]
    decorators = ' '.join(d if isinstance(d, str) else json.dumps(d) for d in (callee_sym.get('decorators') or []))
    rows.append({'tier': tier, 'count': edge['count'], 'context': call.get('context') if call else None,
                 'property': 'property' in decorators or '.setter' in decorators,
                 'shape': shape(call, caller) if call else 'no call on that line (implicit: operator, property, iteration, with, class creation)',
                 'reason': (call.get('method_resolution_reason') or call.get('reason_code') or call.get('category')) if call else 'implicit',
                 'where': f"{edge['caller']['file']}:{edge['call_line']}", 'expr': call.get('expression') if call else None,
                 'callee': static.symbols[callee]['qualified_name']})

def bucket(r):
    reason, shp = (r['reason'] or ''), r['shape']
    if shp.startswith('no call on that line'):
        return 'implicit: property read' if r['property'] else 'implicit: operator, iteration, with'
    if r['context'] and r['context'] not in ('FUNCTION_BODY', 'LAMBDA_BODY'):
        return f"call outside a function body ({r['context']})"
    if reason.startswith('Unknown base classes'):
        return 'class has an unreadable or builtin base: ' + reason.split(': ', 1)[-1][:40]
    if reason.startswith('The standard-library base'):
        return 'name reserved by a standard-library base'
    if 'unconditional simple receiver' in reason or 'reassigned or ambiguous' in reason or 'Destructuring' in reason:
        return 'local receiver assigned conditionally, in a loop, or more than once'
    if 'parameter has no supported receiver evidence' in reason or shp.startswith('parameter.') or (
            "is not a known imported target" in reason and shp == 'parameter.method()'):
        return 'parameter with no type evidence'
    if 'field' in reason.lower() or 'attribute may also be assigned' in reason or 'class attribute or method has the same name' in reason:
        return 'field typing refused'
    if shp == 'call().method()' and (r['expr'] or '').startswith('super()'):
        return 'super() call'
    if shp in ('parameter called: p()', 'computed callee: x[k]() or f()()') or 'computed expression' in reason:
        return 'dynamic: callable passed in or computed'
    if 'no supported callable declaration' in reason:
        return 'receiver typed, but the target is not a plain method (property, attribute, inherited from unknown)'
    return 'other: ' + reason[:60]

for r in rows:
    if r['tier'] not in ('confident', 'uncertain'):
        r['bucket'] = bucket(r)
weak = [r for r in rows if r['tier'] not in ('confident', 'uncertain')]
summary = {'project': name, 'real_source_calls': len(rows), 'strong': len(rows) - len(weak),
           'name_only': sum(r['tier'] == 'name_only' for r in weak), 'missed': sum(r['tier'] == 'missed' for r in weak),
           'by_shape': Counter(r['shape'] for r in weak).most_common(),
           'by_bucket': Counter(r['bucket'] for r in weak).most_common(),
           'bucket_examples': defaultdict(list),
           'by_reason': Counter((r['reason'] or '?')[:90] for r in weak).most_common(12),
           'examples': defaultdict(list)}
for r in weak:
    if len(summary['examples'][r['shape']]) < 4:
        summary['examples'][r['shape']].append(f"{r['where']}  {r['expr']}  ->  {r['callee']}")
for r in weak:
    if len(summary['bucket_examples'][r['bucket']]) < 3:
        summary['bucket_examples'][r['bucket']].append(f"{r['where']}  {r['expr']}  ->  {r['callee']}")
print(f"{name}: {summary['real_source_calls']} real source calls; strong {summary['strong']}; name-only {summary['name_only']}; missed {summary['missed']}")
for s, n in summary['by_bucket']:
    print(f'  {n:4}  {s}')
summary['weak_rows'] = weak
if out_path:
    json.dump(summary, open(out_path, 'w'), indent=1)
