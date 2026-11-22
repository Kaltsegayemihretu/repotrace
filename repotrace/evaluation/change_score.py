"""Score the commit-comparison workflow against an author's CHANGELOG_TRUTH.md.

Three layers per commit: which functions changed, which are directly affected, which are
indirectly affected. A key function counts as found at the best tier we showed it:
confident link, uncertain link, or name-only match. Reads files; runs only git and the analyzer.
"""
import argparse
import json
import re
from pathlib import Path

from repotrace.changes.change_impact import analyze_change_impact
from repotrace.changes.git_snapshot import GitError

STEP = re.compile(r'^## step-(\d\d)\b')
ENTRY = re.compile(r'`([^`]+)`:\s*(.*)')
NAME = re.compile(r'`([^`]+)`')
SKIP = ('<module>', '<lambda>', '<class>', '<UNPARSEABLE FILE>')


def normalize(name):
    return name.replace('.<locals>', '')


def parse_truth(text):
    """{step: {'changed': {kind: set}, 'direct': set, 'indirect': set, 'unparseable': [files]}}"""
    steps, current, section, kind = {}, None, None, None
    for line in text.splitlines():
        match = STEP.match(line)
        if match:
            current = steps.setdefault(int(match.group(1)), {
                'changed': {'modified': set(), 'added': set(), 'removed': set()},
                'direct': set(), 'indirect': set(), 'unparseable': [], 'module_level': set()})
            section = None
            continue
        if current is None:
            continue
        if line.startswith('**Changed'):
            section = 'changed'
        elif line.startswith('**Affected, direct'):
            section = 'direct'
        elif line.startswith('**Affected, indirect'):
            section = 'indirect'
        elif line.startswith('---') or line.startswith('## '):
            section = None
        elif section == 'changed' and re.match(r'^\s*-\s*(modified|added|removed):', line):
            kind = re.match(r'^\s*-\s*(\w+):', line).group(1)
        elif section and (entry := ENTRY.search(line)):
            file, rest = entry.group(1), entry.group(2)
            for name in NAME.findall(rest):
                if name == '<UNPARSEABLE FILE>':
                    current['unparseable'].append(file)
                elif name == '<module>':
                    current['module_level'].add(file)
                elif any(marker in name for marker in ('<lambda>', '<class>')):
                    continue
                elif section == 'changed':
                    current['changed'][kind or 'modified'].add((file, normalize(name)))
                else:
                    current[section].add((file, normalize(name)))
    return steps


def our_view(report):
    """What the workflow showed, with the best tier per (file, function)."""
    rank = {'confident': 0, 'uncertain': 1, 'name_only': 2}
    shown, changed = {}, {'modified': set(), 'added': set(), 'removed': set()}

    def offer(key, tier):
        if key not in shown or rank[tier] < rank[shown[key]]:
            shown[key] = tier
    for change in report['changes']:
        changed[{'deleted': 'removed'}.get(change['change'], change['change'])].add(
            (change['file'], normalize(change['qualified_name'])))
        for item in change['review_candidates']:
            offer((item['file'], normalize(item['qualified_name'])), 'uncertain' if item['uncertain'] else 'confident')
        for site in change.get('name_matches', {}).get('sites', []):
            offer((site['file'], normalize(site['caller'])), 'name_only')
        for found in change.get('decorator_effect', {}).get('name_matches', {}).values():
            for site in found['sites']:
                offer((site['file'], normalize(site['caller'])), 'name_only')
    return shown, changed


def score_step(truth, report):
    shown, changed = our_view(report)
    key_changed = set().union(*truth['changed'].values())
    our_changed = {k for k in set().union(*changed.values()) if not k[0].startswith('tests/')}  # the key leaves tests out
    result = {
        'changed': {'key': len(key_changed), 'found': len(key_changed & our_changed),
                    'extra': len(our_changed - key_changed)},
        'gaps': sorted({(g['type'], g['file']) for g in report['gaps']}),
        'unparseable_flagged': sorted(f for f in truth['unparseable']
                                      if any(g['type'] == 'analysis_failed' and g['file'] == f for g in report['gaps']))}
    result['unparseable_expected'] = sorted(truth['unparseable'])
    for layer in ('direct', 'indirect'):
        keys = truth[layer]
        tiers = {'confident': 0, 'uncertain': 0, 'name_only': 0, 'missing': 0}
        for key in keys:
            tiers[shown.get(key, 'missing')] += 1
        result[layer] = {'key': len(keys), **tiers}
    key_all = truth['direct'] | truth['indirect']
    ours = {k: t for k, t in shown.items() if not k[0].startswith('tests/')}
    graph = {k for k, t in ours.items() if t != 'name_only'}
    result['graph_candidates_outside_tests'] = {'total': len(graph), 'in_key': len(graph & key_all)}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=Path)
    parser.add_argument('--truth', required=True, type=Path)
    parser.add_argument('--source-root', action='append', dest='roots')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    truth = parse_truth(args.truth.read_text(encoding='utf-8'))
    out = {}
    for mode in (False, True):
        label = 'with_uncertain_links' if mode else 'confident_links_only'
        out[label] = {}
        for step in sorted(truth):
            if step == 1:
                continue
            try:
                report = analyze_change_impact(args.repository, f'step-{step - 1:02d}', f'step-{step:02d}',
                                               source_roots=args.roots, include_possible=mode)
            except GitError as exc:
                out[label][step] = {'error': str(exc)}
                continue
            out[label][step] = score_step(truth[step], report)
    if args.output:
        args.output.write_text(json.dumps(out, indent=1, default=sorted))
    for label, steps in out.items():
        print(f'== {label}')
        for step, r in steps.items():
            if 'error' in r:
                print(step, r['error'])
                continue
            c, d, i = r['changed'], r['direct'], r['indirect']
            print(f"step {step:02d}  changed {c['found']}/{c['key']} (+{c['extra']} extra)  "
                  f"direct {d['key']}: conf {d['confident']} unc {d['uncertain']} name {d['name_only']} miss {d['missing']}  "
                  f"indirect {i['key']}: conf {i['confident']} unc {i['uncertain']} name {i['name_only']} miss {i['missing']}  "
                  f"gaps {[g[0] for g in r['gaps']]}")
    return out


if __name__ == '__main__':
    main()
