"""Print a repository report as plain arrows: who calls whom, and how sure we are.

  A -> B    confident link (one supported target found in the project)
  A ~> B    uncertain link (one of a few possible targets)
Builtin, library, dynamic and unsupported calls are not drawn; they are only counted.
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

LEFT_OUT = [
    ('builtin_reference', "Python's own functions (print, len, ...)"),
    ('builtin_object_method', "methods on Python's own objects (list.append, str.join, ...)"),
    ('external_or_missing', 'Python\'s standard library and installed libraries'),
    ('import_not_found', 'imports we could not find (a library not installed here, or your own code in another folder)'),
    ('unsupported_context', 'places we do not analyze (e.g. decorators)'),
    ('unresolved', 'dynamic or unclear: callbacks, objects of unknown type, ...'),
]


def short(symbol_id):
    """'models.py::LibraryManager.login@84:4' -> 'models.py: LibraryManager.login'."""
    name = symbol_id.split('@')[0]
    return name.replace('::', ': ', 1)


def uncertain_why(call, candidate):
    steps = candidate['evidence'].get('steps', [])
    kinds = [s['kind'] for s in steps]
    for step in steps:
        if step['kind'] in ('constructor_expression', 'class_reference'):
            made = step.get('expression') or step.get('name') or '?'
            return f"object looks like a {made} (line {step['span']['line']}); " \
                   'the exact method is not guaranteed'
    return 'guessed from ' + (kinds[0].replace('_', ' ') if kinds else 'indirect evidence')


def collect(report):
    sure, maybe, counts, left = defaultdict(Counter), defaultdict(dict), Counter(), defaultdict(Counter)
    for file in report['files']:
        if file['status'] != 'ok':
            continue
        for call in file['calls']:
            category = call.get('category', 'unresolved')
            counts[category] += 1
            caller = call['enclosing_symbol_id']
            if call['resolution'] == 'LIKELY':
                sure[caller][call['target_id']] += 1
            elif call['resolution'] == 'POSSIBLE':
                for cand in call['candidate_targets']:
                    maybe[caller].setdefault(cand['target_id'], (
                        len(call['candidate_targets']), uncertain_why(call, cand)))
            else:
                left[category][call['expression'] if isinstance(call['expression'], str) else '?'] += 1
    return sure, maybe, counts, left


def render(report, tree_limit=60):
    sure, maybe, counts, left = collect(report)
    out, total = [], sum(counts.values())
    out += [f'CALLS IN THE PROJECT: {total}', '']
    out += ['1) CONFIDENT LINKS  (A -> B)   '
            f'[{counts["internal_linked"]} calls]', '']
    for caller in sorted(sure):
        for target, n in sorted(sure[caller].items()):
            out.append(f'  {short(caller)}  ->  {short(target)}' + (f'   (x{n})' if n > 1 else ''))
    out += ['', f'2) UNCERTAIN LINKS  (A ~> B)   [{counts["internal_possible"]} calls]', '']
    for caller in sorted(maybe):
        for target, (options, why) in sorted(maybe[caller].items()):
            pick = '' if options == 1 else f' (1 of {options} options)'
            out += [f'  {short(caller)}  ~>  {short(target)}{pick}', f'        why: {why}']
    # Chains: follow arrows from functions nobody (known) calls, so indirect calls show up.
    edges = defaultdict(list)
    for caller, targets in sure.items():
        edges[caller] += [(t, False) for t in targets]
    for caller, targets in maybe.items():
        edges[caller] += [(t, True) for t in targets]
    called = {t for pairs in edges.values() for t, _ in pairs}
    roots = sorted(set(edges) - called)
    out += ['', '3) CHAINS  (indirect calls: follow the arrows downward)', '']
    shown, lines = set(), []

    def walk(node, depth, trail):
        for target, unsure in sorted(edges.get(node, [])):
            arrow = '~>' if unsure else '->'
            lines.append('  ' + '    ' * depth + f'{arrow} {short(target)}' +
                         ('   (cycle)' if target in trail else
                          '   (shown above)' if target in shown and edges.get(target) else ''))
            if target not in trail and not (target in shown):
                shown.add(target)
                walk(target, depth + 1, trail | {target})
    for root in roots:
        lines.append(f'  {short(root)}')
        shown.add(root)
        walk(root, 1, {root})
    out += lines[:tree_limit] + ([f'  ... {len(lines) - tree_limit} more lines'] if len(lines) > tree_limit else [])
    out += ['', 'LEFT OUT OF THE ARROWS (counted, not drawn; they do not point at your own functions)', '']
    for category, meaning in LEFT_OUT:
        top = ', '.join(f'{name}' for name, _ in left[category].most_common(4))
        out.append(f'  {counts[category]:4}  {meaning}' + (f'   e.g. {top}' if top else ''))
    out += ['', 'Note: "confident" means supported by the code text, not proven at run time.',
            'Unresolved calls may hide more links, so a missing arrow does not mean safe.']
    return '\n'.join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path, help='Saved repository report JSON')
    parser.add_argument('--max-chain-lines', type=int, default=60)
    args = parser.parse_args(argv)
    print(render(json.loads(args.report.read_text()), args.max_chain_lines))


if __name__ == '__main__':
    main()
