"""Compare two commits of a local Git repository and list functions to inspect."""
import argparse
import json
from pathlib import Path

from repotrace.reading.analyzer import write_json_report
from repotrace.changes.change_impact import analyze_change_impact
from repotrace.changes.git_snapshot import GitError
from repotrace.changes.test_selection import DEFAULT_TEST_LIMIT


def summarize(report, show_noisy=False):
    s = report['summary']
    lines = [f"{report['base']['ref']} ({report['base']['commit'][:10]}) -> "
             f"{report['head']['ref']} ({report['head']['commit'][:10]}): status {report['status']}",
             f"Functions: {s['functions_modified']} modified, {s['functions_added']} added, "
             f"{s['functions_deleted']} deleted; {s['gaps']} gap(s)."]
    for change in report['changes']:
        notes = []
        if change.get('reason') == 'reads_changed_constant':
            notes.append('reads changed constant ' + ', '.join(change['constants']))
        elif change.get('also_reads_changed_constants'):
            notes.append('also reads changed constant ' + ', '.join(change['also_reads_changed_constants']))
        if change.get('before_file_did_not_parse'):
            notes.append('file did not parse before, so this may not be new')
        lines.append(f"\n[{change['change']}] {change['file']}::{change['qualified_name']}"
                     + (f"  ({'; '.join(notes)})" if notes else ''))
        for item in change['review_candidates']:
            flags = ', '.join(f for f in (
                'uncertain' if item['uncertain'] else 'supported',
                'also changed' if (item['file'], item['qualified_name']) in
                {(c['file'], c['qualified_name']) for c in report['changes']} else '',
                'removed in head' if item.get('exists_after') is False else '',
                'via decorator: ' + ', '.join(item['via_decorator'][:2]) if item.get('via_decorator') else '',
                'HEAD STILL REFERENCES THE REMOVED NAME' if item.get('still_references_removed_name') else
                'NOT UPDATED: may still call the removed function' if item.get('may_still_call_removed') else '') if f)
            lines.append(f"    {item['relationship']:8} {item['file']}::{item['qualified_name']} "
                         f"({'/'.join(sorted(item['sides']))}; {flags})")
        effect = change.get('decorator_effect')
        if effect:
            more = ' (list cut)' if effect['truncated'] else ''
            lines.append(f"    DECORATOR CHANGE: wraps {len(effect['wrapped_functions'])} function(s){more}; "
                         f"the callers of those are listed above, marked 'via decorator'.")
            for wrapped, found in effect.get('name_matches', {}).items():
                lines.append(f"      weak: {found['total']} unexplained call site(s) named '{found['name']}' "
                             f"(wrapped function {wrapped}), e.g. "
                             + ', '.join(f"{s['file']}:{s['line']}" for s in found['sites'][:3]))
        match = change.get('name_matches')
        if match and match['status'] == 'ok' and match['total']:
            title = ('STILL REFERENCED IN HEAD (name only)' if match.get('meaning') else
                     'Name matches (weak: the analyzer could not tell what these call)')
            if match['noisy_name'] and not show_noisy and not match.get('meaning'):
                lines.append(f"    Name matches hidden: '{match['name']}' is a very common name "
                             f"({match['total']} unexplained calls, mostly other objects). "
                             f"Use --show-noisy-matches to list them.")
                continue
            note = ' - very common name, most are probably other objects' if match['noisy_name'] else ''
            lines.append(f"    {title}: {match['total']} site(s){note}")
            for site in match['sites'][:5]:
                lines.append(f"        {site['file']}:{site['line']}  {site['expression']}  in {site['caller']}")
            if match['total'] > 5:
                lines.append(f"        ... {match['total'] - 5} more (see --output JSON)")
    plan = report.get('test_plan')
    if plan is not None:
        lines.append(f"\nTests to run first: {plan['selected']} of {plan['total_candidates']} connected tests "
                     f"({plan['omitted']} outside the shortlist).")
        lines.append('  ' + plan['ranking'])
        for test in plan['tests']:
            lines.append('  ' + test['run'])
        lines.append('  ' + plan['warning'])
    for gap in report['gaps']:
        lines.append(f"\nGap [{gap['type']}] {gap['file']}: {gap['message']}")
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=Path, help='Local Git repository folder')
    parser.add_argument('--base', required=True, help='Older branch/tag/commit')
    parser.add_argument('--head', required=True, help='Newer branch/tag/commit')
    parser.add_argument('--source-root', action='append', dest='source_roots',
                        help='Relative import root; repeatable; default is the repository root')
    parser.add_argument('--exclude-dir', action='append', default=[], dest='exclude_dirs')
    parser.add_argument('--include-possible', action='store_true',
                        help='Also follow uncertain method/constructor candidates (clearly flagged)')
    parser.add_argument('--max-changes', type=int, default=500)
    parser.add_argument('--max-depth', type=int, default=20)
    parser.add_argument('--max-tests', type=int, default=DEFAULT_TEST_LIMIT,
                        help='Maximum tests in the comparison-wide run-first list (default: 20)')
    parser.add_argument('--max-name-matches', type=int, default=20,
                        help='Name-only match sites kept per changed function (0 = none)')
    parser.add_argument('--output', type=Path, help='Save the JSON report (.json)')
    parser.add_argument('--overwrite', action='store_true')
    parser.add_argument('--dashboard', type=Path, help='Also write a self-contained HTML dashboard (.html)')
    parser.add_argument('--json', action='store_true', help='Print JSON instead of the text summary')
    parser.add_argument('--show-noisy-matches', action='store_true',
                        help='List name-only matches even for very common method names')
    args = parser.parse_args(argv)
    try:
        report = analyze_change_impact(
            args.repository, args.base, args.head, source_roots=args.source_roots,
            exclude_dirs=args.exclude_dirs, include_possible=args.include_possible,
            max_changes=args.max_changes, max_depth=args.max_depth,
            max_name_matches=args.max_name_matches, max_tests=args.max_tests)
        if args.output:
            write_json_report(report, args.output, overwrite=args.overwrite)
            print(f'Saved change_impact report: {args.output}')
        if args.dashboard:
            from repotrace.report.dashboard import build_view, render_html, write_html
            write_html(render_html(build_view(report, args.repository)), args.dashboard, args.overwrite)
            print(f'Saved dashboard: {args.dashboard}')
        if args.json and not args.output:
            print(json.dumps(report, indent=2))
        else:
            print(summarize(report, args.show_noisy_matches))
        return 0 if report['status'] == 'ok' else 1
    except (GitError, OSError, ValueError, RecursionError) as exc:
        parser.exit(2, f'Cannot build change-impact report: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
