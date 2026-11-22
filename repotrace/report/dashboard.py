"""Turn a saved change_impact report into one self-contained HTML page.

The page answers, in order: what changed -> where should I look -> why -> the supporting code.
It is local-first: one file, no server, no network, no external scripts or fonts. Source
snippets are read from the two Git commits named in the report (never the working tree) and
embedded in the page, so treat the HTML like source code when sharing it.
"""
import argparse
import difflib
import json
import os
import re
from pathlib import Path

from repotrace.changes.git_snapshot import GitError, run_git
from repotrace.graph.graph_explore import is_test_file
from repotrace.report.story import build_story

MAX_CANDIDATES = 200
MAX_NAME_SITES = 30
SNIPPET_CONTEXT = 3


class SourceReader:
    """Read files from a commit without touching the working tree; failures become 'unavailable'."""

    def __init__(self, repo):
        self.repo = repo
        self.cache = {}
        self.failed = 0

    def text(self, commit, path):
        if self.repo is None or not commit:
            return None
        key = (commit, path)
        if key not in self.cache:
            try:
                self.cache[key] = run_git(self.repo, 'show', f'{commit}:{path}').decode('utf-8', 'replace')
            except GitError:
                self.cache[key] = None
                self.failed += 1
        return self.cache[key]

    def lines(self, commit, path, first, last):
        text = self.text(commit, path)
        if text is None:
            return None
        rows = text.splitlines()
        return {'first': first, 'lines': rows[max(first, 1) - 1:last]}


def short(identifier):
    return identifier.split('::', 1)[-1].split('@', 1)[0]


def explain_step(step):
    kind = step.get('kind')
    span = step.get('span') or {}
    where = f"{step.get('file', '')}:{span.get('line', '?')}"
    if kind == 'constructor_expression':
        return f"the object is created by {step.get('expression')} at {where}"
    if kind == 'receiver_assignment':
        return f"it is assigned to {step.get('name')} at {where}"
    if kind == 'field_assignment':
        return f"the field {step.get('attribute')} is assigned at {where}"
    if kind == 'observed_argument':
        return (f"{step.get('argument')} is passed as parameter {step.get('parameter')} of "
                f"{short(step.get('called_function', ''))} at {where}")
    if kind == 'return_value':
        return f"it is returned by {step.get('function')} ({where})"
    if kind == 'method_first_parameter':
        return 'it is self inside a method of this class (subclasses may override the method)'
    if kind == 'parameter_type_hint':
        return f"the parameter is annotated {step.get('annotation')} (a hint, not enforced)"
    if kind == 'class_binding':
        return 'the class is referenced by name'
    if kind == 'declared_method':
        return f"{step.get('method')} is defined in {short(step.get('defined_in_class', ''))} ({where})"
    if kind == 'unverified_dynamic_write':
        return step.get('warning', 'a dynamic write could change this')
    return None


# The resolver's own wording is precise but technical; these are the plain-words versions.
PLAIN_LINK = {'import': 'it imports the function by name',
              'local': 'a function defined in the same file'}


def why_text(candidate, side):
    witness = side['witness_call'].get('evidence', side['witness_call'])   # older reports stored it bare
    where = f"{witness['file']}:{witness['span']['line']}"
    if not side['path_has_possible_link'] and witness.get('resolution') == 'LIKELY':
        base = f"The call at {where} goes straight to the changed function"
        kind = witness.get('resolution_kind')
        if kind == 'decorated_function':
            base += ' through its decorator wrapper'
        plain = PLAIN_LINK.get(kind)
        reason = None if plain else witness.get('reason')
        return base + (f" ({plain})." if plain else f". {reason}" if reason else '.'), []
    base = f"A call at {where} may reach it. This is a possibility, not a proven call"
    steps = (witness.get('candidate_evidence') or {}).get('steps', [])
    bullets = [t for t in (explain_step(s) for s in steps) if t]
    return base + (':' if bullets else '.'), bullets[:5]


def candidate_view(change, candidate, impacts, reader, commits, changed_keys):
    side_name = 'after' if 'after' in candidate['sides'] else 'before'
    side = candidate['sides'][side_name]
    commit = commits[side_name]
    tier = 'possible' if candidate['uncertain'] else 'supported'
    text, bullets = why_text(candidate, side)
    # The chain of callers from this function down to the changed one, from the impact result.
    chain = []
    affected = {a['symbol']['id']: a for a in impacts.get(side_name, {}).get('affected', [])}
    current, target = side['symbol_id'], (change.get(side_name) or change.get('before') or {}).get('id')
    seen = set()
    while current in affected and current not in seen:
        seen.add(current)
        chain.append(short(affected[current]['symbol']['id']))
        current = affected[current]['next_toward_target']
    if chain and target:
        chain.append(short(target))
    flags = []
    key = (candidate['file'], candidate['qualified_name'])
    if key in changed_keys:
        flags.append('also changed in this commit')
    if candidate.get('exists_after') is False:
        flags.append('removed in head')
    if candidate.get('still_references_removed_name'):
        flags.append('head still references the removed name')
    elif candidate.get('may_still_call_removed'):
        flags.append('not updated: may still call the removed function')
    if candidate.get('via_decorator'):
        flags.append('through decorator on ' + ', '.join(candidate['via_decorator'][:2]))
    if candidate['file'].split('/')[0] in {'tests', 'test'} or '/test_' in '/' + candidate['file']:
        flags.append('test code')
    call = side['witness_call'].get('evidence', side['witness_call'])
    line = call['span']['line']
    return {
        'name': candidate['qualified_name'], 'file': candidate['file'], 'line': side['span']['line'],
        'relationship': candidate['relationship'], 'distance': candidate['distance'], 'tier': tier,
        'flags': flags, 'why': text, 'because': bullets, 'chain': chain,
        'call_site': {'file': call['file'], 'line': line, 'side': side_name,
                      'code': reader.lines(commit, call['file'],
                                           line - SNIPPET_CONTEXT, line + SNIPPET_CONTEXT)}}


def function_source(reader, commit, file, brief):
    if not brief:
        return None
    span = brief['span']
    got = reader.lines(commit, file, span['line'], span['end_line'])
    return '\n'.join(got['lines']) if got else None


MAX_GRAPH_NODES = 300
TIER_RANK = {'supported': 0, 'possible': 1, 'name_only': 2}


def build_graph(change, changed_keys):
    """The picture for one changed function: the function at layer 0, its callers at -1, -2, ...

    Layers come from shortest paths over the actual links, so the drawing cannot disagree with
    the data. Decorator-wrapped functions get their own layer; name matches become dotted edges.
    """
    root = (change['file'], change['qualified_name'])
    nodes, ids, edges = {}, {}, {}

    def key_of(k):
        return f'{k[0]}::{k[1]}'

    def add(k, name, file, line, kind, role, **extra):
        nodes.setdefault(k, {'key': key_of(k), 'name': name, 'file': file, 'line': line, 'kind': kind, 'role': role,
                             'test': is_test_file(file), 'changed': k in changed_keys and k != root, **extra})

    def link(src, dst, tier, kind):
        if src != dst and (src, dst) not in edges:
            edges[(src, dst)] = {'source': key_of(src), 'target': key_of(dst), 'tier': tier, 'kind': kind}

    brief = change.get('after') or change.get('before') or {}
    add(root, change['qualified_name'], change['file'], (brief.get('span') or {}).get('line'), change['kind'], 'root')
    for side in ('before', 'after'):
        if change.get(side):
            ids[change[side]['id']] = root
    for index, cand in enumerate(change['review_candidates'][:MAX_CANDIDATES]):
        k = (cand['file'], cand['qualified_name'])
        line = next(iter(cand['sides'].values()))['span']['line']
        add(k, cand['qualified_name'], cand['file'], line, cand['kind'], 'caller', cand=index)
        for side in cand['sides'].values():
            ids[side['symbol_id']] = k
    effect = change.get('decorator_effect') or {}
    for wrapped in effect.get('wrapped_detail', []):
        k = (wrapped['file'], wrapped['name'])
        add(k, wrapped['name'], wrapped['file'], wrapped['line'], wrapped['kind'], 'wrapped')
        ids[wrapped['id']] = k
        link(k, root, 'supported', 'wraps')
    for cand in change['review_candidates'][:MAX_CANDIDATES]:
        k = (cand['file'], cand['qualified_name'])
        for side in cand['sides'].values():
            pair = side['witness_call']
            evidence = pair.get('evidence', pair)
            dst = ids.get(pair.get('target'))
            if dst is not None:
                link(k, dst, 'possible' if evidence.get('resolution') == 'POSSIBLE' else 'supported', 'call')
    matches = change.get('name_matches') or {}
    sites = [(root, s) for s in matches.get('sites', [])[:MAX_NAME_SITES]] if matches.get('status') == 'ok' else []
    for wrapped_name, found in effect.get('name_matches', {}).items():
        target = next((k for k, n in nodes.items() if n['role'] == 'wrapped' and n['name'] == wrapped_name), root)
        sites += [(target, s) for s in found['sites']]
    for target, site in sites:
        k = (site['file'], site['caller'])
        add(k, site['caller'], site['file'], None, 'FUNCTION', 'weak', call_line=site['line'],
            site=next((i for i, s in enumerate(matches.get('sites', [])) if s['file'] == site['file'] and s['line'] == site['line']), None))
        if not any(src == k for (src, _) in edges):
            link(k, target, 'name_only', 'name')
    # Layers: shortest path to the changed function, following the links backwards.
    incoming = {}
    for (src, dst), edge in edges.items():
        incoming.setdefault(dst, []).append((src, edge))
    nodes[root]['layer'], nodes[root]['tier'] = 0, 'supported'
    queue = [root]
    for current in queue:
        for src, edge in sorted(incoming.get(current, []), key=lambda p: (TIER_RANK[p[1]['tier']], p[0])):
            if 'layer' not in nodes[src]:
                weaker = max(edge['tier'], nodes[current]['tier'], key=TIER_RANK.get)
                nodes[src]['layer'], nodes[src]['tier'] = nodes[current]['layer'] - 1, weaker
                queue.append(src)
    for node in nodes.values():
        node.setdefault('layer', -1)
        node.setdefault('tier', 'possible')
    keep = sorted(nodes.values(), key=lambda n: (-n['layer'], TIER_RANK[n['tier']], n['test'], n['file'], n['name']))
    truncated = len(keep) > MAX_GRAPH_NODES
    keep = keep[:MAX_GRAPH_NODES]
    kept = {n['key'] for n in keep}
    return {'root': key_of(root), 'nodes': keep, 'truncated': truncated,
            'edges': [e for e in edges.values() if e['source'] in kept and e['target'] in kept]}


def build_view(report, repo=None):
    reader = SourceReader(repo)
    commits = {'before': report['base']['commit'], 'after': report['head']['commit']}
    changed_keys = {(c['file'], c['qualified_name']) for c in report['changes'] if c['change'] != 'deleted'}
    changes = []
    for index, change in enumerate(report['changes']):
        entry = {
            'id': index, 'file': change['file'], 'name': change['qualified_name'], 'kind': change['kind'],
            'change': change['change'], 'reason': change.get('reason'), 'constants': change.get('constants'),
            'also_reads_constants': change.get('also_reads_changed_constants'),
            'before_file_did_not_parse': bool(change.get('before_file_did_not_parse')),
            'includes_nested_change': bool(change.get('includes_nested_change')),
            'not_queried': change.get('not_queried'),
            'line': ((change.get('after') or change.get('before') or {}).get('span') or {}).get('line')}
        before = function_source(reader, commits['before'], change['file'], change.get('before'))
        after = function_source(reader, commits['after'], change['file'], change.get('after'))
        entry['source'] = {'before': before, 'after': after}
        review = change.get('signature_review')
        entry['signature_review'] = ({**review, 'findings': [
            {**f, 'code': reader.lines(commits['after'], f['file'],
                                      f['span']['line'] - SNIPPET_CONTEXT,
                                      f['span']['end_line'] + SNIPPET_CONTEXT)}
            for f in review.get('findings', [])]} if review else None)
        # Every line must end in a newline, or the last '-' and '+' lines run together.
        entry['diff'] = ''.join(difflib.unified_diff(
            (before + '\n').splitlines(True), (after + '\n').splitlines(True),
            fromfile='older version', tofile='newer version', n=2)) if before and after and before != after else None
        impacts = change.get('impact', {})
        entry['candidates'] = [candidate_view(change, c, impacts, reader, commits, changed_keys)
                               for c in change['review_candidates'][:MAX_CANDIDATES]]
        entry['candidates_total'] = len(change['review_candidates'])
        entry['graph'] = build_graph(change, changed_keys)
        entry['unresolved_callers'] = {side: i.get('summary', {}) for side, i in impacts.items()}
        matches = change.get('name_matches') or {}
        entry['name_matches'] = {
            'status': matches.get('status'), 'total': matches.get('total', 0), 'noisy': matches.get('noisy_name', False),
            'meaning': matches.get('meaning'), 'same_name_definitions': matches.get('same_name_definitions', 0),
            'sites': [{'file': s['file'], 'line': s['line'], 'expression': s['expression'], 'caller': s['caller'],
                       'is_test': s['is_test'],
                       'code': reader.lines(commits['after'], s['file'], s['line'] - SNIPPET_CONTEXT, s['line'] + SNIPPET_CONTEXT)}
                      for s in matches.get('sites', [])[:MAX_NAME_SITES]]} if matches else None
        effect = change.get('decorator_effect')
        entry['decorator_effect'] = {
            'wrapped': effect['wrapped_functions'], 'truncated': effect['truncated'],
            'weak': {name: {'total': m['total'], 'sites': [{'file': s['file'], 'line': s['line'], 'caller': s['caller']}
                                                         for s in m['sites'][:5]]}
                     for name, m in effect.get('name_matches', {}).items()}} if effect else None
        changes.append(entry)
    def commit_subject(commit):
        try:
            return run_git(repo, 'show', '-s', '--format=%s', commit).decode('utf-8', 'replace').strip()[:100]
        except (GitError, TypeError):
            return ''
    return {
        'repository': os.path.basename(report['repository'].rstrip('/')),
        'base': {**report['base'], 'subject': commit_subject(report['base']['commit']) if repo else ''},
        'head': {**report['head'], 'subject': commit_subject(report['head']['commit']) if repo else ''},
        'status': report['status'], 'summary': report['summary'], 'settings': report['settings'],
        'warnings': report['warnings'], 'gaps': report['gaps'], 'files': report['files'],
        'constants': report.get('constants', []), 'policy': report['policy'],
        'coverage': report.get('coverage', {}), 'worktree_dirty': report.get('worktree_dirty'),
        'snapshots': {k: {'status': v['status'], 'python_files_written': v['python_files_written'],
                          'skipped': len(v['skipped_from_git'])} for k, v in report['snapshots'].items()},
        'source_available': repo is not None and reader.failed == 0, 'changes': changes, 'story': build_story(changes)}


def embed(view):
    # ensure_ascii escapes everything non-ASCII; the replacements keep '</script>' out of the page.
    return json.dumps(view, ensure_ascii=True).replace('</', '<\\/').replace('<!--', '<\\!--')


def render_html(view):
    template = Path(__file__).with_name('dashboard_template.html').read_text(encoding='utf-8')
    title = f"{view['repository']}: {view['base']['commit'][:8]} to {view['head']['commit'][:8]}"
    return template.replace('__TITLE__', re.sub(r'[<>&"]', '', title)).replace('__DATA__', embed(view))


def write_html(html, output, overwrite=False):
    output = Path(output)
    if output.suffix.lower() != '.html':
        raise ValueError('Choose an output filename ending in .html.')
    if output.is_symlink():
        raise ValueError('The output must not be a symbolic link.')
    if output.exists() and not overwrite:
        raise ValueError('The output exists; pass --overwrite to replace it.')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path, help='A change_impact JSON report')
    parser.add_argument('--repo', type=Path, help='The Git repository, for code snippets and diffs')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding='utf-8'))
        if report.get('report_kind') != 'change_impact':
            raise ValueError('Expected a change_impact report.')
        view = build_view(report, args.repo)
        write_html(render_html(view), args.output, args.overwrite)
    except (OSError, ValueError, KeyError, GitError) as exc:
        parser.exit(2, f'Cannot build the dashboard: {exc}\n')
    print(f'Saved dashboard: {args.output}'
          + ('' if view['source_available'] else '  (source snippets unavailable; pass --repo)'))


if __name__ == '__main__':
    main()
