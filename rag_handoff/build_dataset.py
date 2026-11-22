"""Builds the RAG starter dataset from one Git repository and two commits. Python 3.9+, standard library only.

    python3 rag_handoff/build_dataset.py                       # uses the bundled StudentManagement, step-06 -> step-07
    python3 rag_handoff/build_dataset.py --repo PATH --base A --head B --out OUTDIR

Needs: Git on PATH, and RepoTrace (this repository). RepoTrace has no third-party dependencies, so there is nothing to install.
It never runs the analysed code: it reads source text with Python's `ast` module and reads Git objects.

Writes into OUTDIR (default rag_handoff/data):
    commits.txt                 the whole history, one line per commit (full id, short id, date, subject)
    diff.patch                  the full unified diff between the two commits
    diff_name_status.txt        which files changed and how (A added, M modified, D deleted, R renamed)
    diff_numstat.txt            lines added / removed per file
    diff_hunks.json             every changed line range, linked to the function(s) that contain it, before and after
    graph_<base>.json           the dependency graph of the project at the base commit
    graph_<head>.json           the dependency graph of the project at the head commit
    change_report.json         RepoTrace's own change report (what changed, what it can affect, tests to run first)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import io
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                                    # the RepoTrace repository


def git(repo, *args, check=True):
    r = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if check and r.returncode:
        sys.exit(f'git {" ".join(args)} failed: {r.stderr.strip()}')
    return r.stdout


def repotrace(*args):
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    r = subprocess.run([sys.executable, '-B', '-m', 'repotrace', *args], capture_output=True, text=True, encoding='utf-8', errors='replace', env=env, cwd=str(ROOT))
    if r.returncode:
        sys.exit(f'repotrace {" ".join(args)} failed:\n{r.stdout}\n{r.stderr}')
    return r.stdout


def export_commit(repo, commit, destination):
    data = subprocess.run(['git', '-C', str(repo), 'archive', '--format=tar', commit], capture_output=True)
    if data.returncode:
        sys.exit(f'git archive {commit} failed: {data.stderr.decode(errors="replace")}')
    with tarfile.open(fileobj=io.BytesIO(data.stdout)) as tar:
        for member in tar.getmembers():                # refuse anything that would leave the folder
            target = (Path(destination) / member.name).resolve()
            if not str(target).startswith(str(Path(destination).resolve())):
                sys.exit(f'unsafe path in archive: {member.name}')
        tar.extractall(destination)


def is_test_file(path):
    parts = path.lower().split('/')
    name = parts[-1]
    return 'tests' in parts[:-1] or 'test' in parts[:-1] or name.startswith('test_') or name.endswith('_test.py') or name == 'conftest.py'


def module_to_file(files):
    """dotted module name -> file path, from what the engine reported for each file."""
    table = {}
    for f in files:
        for name in f.get('module_names') or []:
            table.setdefault(name, f['file'])
    return table


def build_graph(map_report, project, tag, commit):
    files = map_report['files']
    nodes, ids = [], set()
    for f in files:                                     # one node per file
        nodes.append({'id': f['file'], 'type': 'file', 'name': f['file'].split('/')[-1], 'qualified_name': f['file'], 'file': f['file'],
                      'start_line': 1, 'end_line': f.get('line_count'), 'parent': None, 'is_test': is_test_file(f['file']),
                      'docstring': f.get('docstring'), 'signature': None, 'source': None})
        ids.add(f['file'])
    file_of_module = {f['module_id']: f['file'] for f in files}
    kind_name = {'CLASS': 'class', 'FUNCTION': 'function', 'METHOD': 'method'}
    for f in files:
        for s in f['symbols']:
            parent = file_of_module.get(s['parent_id'], s['parent_id'])
            sig = ', '.join(p['name'] for p in s.get('parameters', []))
            nodes.append({'id': s['id'], 'type': kind_name.get(s['kind'], s['kind'].lower()), 'name': s['name'], 'qualified_name': s['qualified_name'],
                          'file': s['file'], 'start_line': s.get('start_line_including_decorators') or s['span']['line'], 'end_line': s['span']['end_line'],
                          'parent': parent, 'is_test': is_test_file(s['file']), 'docstring': s.get('docstring'),
                          'signature': f"{s['name']}({sig})" if s['kind'] != 'CLASS' else s['name'],
                          'source': s['source'] if s['kind'] != 'CLASS' else None})   # a class's text is its methods; they are separate nodes
            ids.add(s['id'])
    edges, dropped = [], 0

    def add(source, target, kind, confidence=None, **evidence):
        nonlocal dropped
        if source not in ids or target not in ids:
            dropped += 1
            return
        edges.append({'source': source, 'target': target, 'type': kind, 'confidence': confidence, 'evidence': evidence or None})

    for n in nodes:                                     # CONTAINS: parent -> child
        if n['parent'] and n['type'] != 'file':
            add(n['parent'], n['id'], 'CONTAINS')
    mods = module_to_file(files)
    for f in files:                                     # IMPORTS: file -> file, only for modules that are part of this project
        seen = {}
        for imp in f['imports']:
            base = imp.get('module') or ''
            if imp.get('level'):                        # relative import: resolve against this file's package
                pkg = f['file'].split('/')[:-1]
                pkg = pkg[:len(pkg) - (imp['level'] - 1)] if imp['level'] > 1 else pkg
                base = '.'.join(pkg + ([base] if base else []))
            names = [n['name'] for n in imp.get('names', [])] if imp['kind'] == 'ImportFrom' else []
            for dotted in ([base + '.' + n for n in names] if names else []) + [base]:
                target = mods.get(dotted)
                if target and target != f['file']:
                    seen.setdefault(target, imp['span']['line'])
                    break
        for target, line in seen.items():
            add(f['file'], target, 'IMPORTS', None, line=line)
    unresolved = {'builtin_or_library': 0, 'not_traced': 0}
    for f in files:                                     # CALLS: caller -> callee, graded
        for c in f['calls']:
            caller = file_of_module.get(c['enclosing_symbol_id'], c['enclosing_symbol_id'])
            ev = {'file': c['file'], 'line': c['span']['line'], 'expression': c['expression'], 'how': c.get('resolution_kind')}
            if c['resolution'] == 'LIKELY' and c.get('target_id'):
                add(caller, c['target_id'], 'CALLS', 'sure', **ev)
            elif c['resolution'] == 'POSSIBLE':
                for cand in c.get('candidate_targets', []):
                    add(caller, cand['target_id'], 'CALLS', 'maybe', **ev)
            else:
                unresolved['builtin_or_library' if c.get('category', '').startswith(('builtin', 'external')) else 'not_traced'] += 1
    counts = {'nodes': len(nodes), 'edges': len(edges), 'edges_by_type': {}, 'calls_by_confidence': {}, 'calls_not_drawn': unresolved, 'edges_dropped_unknown_endpoint': dropped}
    for e in edges:
        counts['edges_by_type'][e['type']] = counts['edges_by_type'].get(e['type'], 0) + 1
        if e['type'] == 'CALLS':
            counts['calls_by_confidence'][e['confidence']] = counts['calls_by_confidence'].get(e['confidence'], 0) + 1
    meta = {'project': project, 'commit': commit, 'tag_or_ref': tag, 'node_granularity': 'file, class, function and method nodes (not statements)',
            'edge_direction': 'source -> target. CONTAINS: parent to child. CALLS: caller to callee. IMPORTS: importing file to imported file. '
                              'To find what a change can affect, walk CALLS edges BACKWARDS from the changed function (its callers).',
            'confidence': "CALLS edges: 'sure' = the code text shows where the call goes; 'maybe' = depends on an assumption (e.g. self.method() could be overridden). "
                          'Calls to Python itself or installed libraries, and calls the analysis could not trace, are counted in counts.calls_not_drawn and have no edge.',
            'generated_by': 'RepoTrace map + rag_handoff/build_dataset.py', 'counts': counts}
    return {'meta': meta, 'nodes': nodes, 'edges': edges}


HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


def innermost(graph, file, first, last):
    """functions/methods (else classes) in `file` whose lines overlap [first, last], smallest first-level only."""
    hit = [n for n in graph['nodes'] if n['file'] == file and n['type'] != 'file' and n['start_line'] <= last and (n['end_line'] or 0) >= first]
    leaves = [n for n in hit if not any(o is not n and n['start_line'] <= o['start_line'] and o['end_line'] <= n['end_line'] for o in hit)]
    return [n['id'] for n in leaves]


def diff_hunks(repo, base, head, graph_old, graph_new):
    status = {}
    for line in git(repo, 'diff', '--name-status', '-M', base, head).splitlines():
        parts = line.split('\t')
        status[parts[-1]] = {'status': parts[0][0], 'old_path': parts[1] if parts[0][0] == 'R' else parts[-1]}
    out, current = [], None
    for line in git(repo, 'diff', '-U0', '--no-color', '-M', base, head).splitlines():
        if line.startswith('+++ '):
            path = line[4:].removeprefix('b/') if line != '+++ /dev/null' else None
            current = None
            if path:
                current = {'file': path, 'change': status.get(path, {}).get('status', 'M'), 'old_file': status.get(path, {}).get('old_path', path), 'hunks': []}
                out.append(current)
        elif line.startswith('--- ') and line == '--- /dev/null':
            continue
        else:
            m = HUNK.match(line)
            if m and current is not None:
                a, b, c, d = int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 1)
                current['hunks'].append({'old_lines': [a, a + b - 1] if b else None, 'new_lines': [c, c + d - 1] if d else None,
                                         'old_symbols': innermost(graph_old, current['old_file'], a, a + b - 1) if b else [],
                                         'new_symbols': innermost(graph_new, current['file'], c, c + d - 1) if d else []})
    for entry in status.items():                         # deleted files have no '+++ b/...' line
        path, info = entry
        if info['status'] == 'D' and not any(o['old_file'] == path for o in out):
            out.append({'file': None, 'change': 'D', 'old_file': path, 'hunks': []})
    return out


def clean(value, replacements):
    if isinstance(value, str):
        for old, new in replacements:
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [clean(v, replacements) for v in value]
    if isinstance(value, dict):
        return {k: clean(v, replacements) for k, v in value.items()}
    return value


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--repo', help='a Git repository; default: the bundled StudentManagement (cloned into a temporary folder)')
    ap.add_argument('--base', default='step-06'); ap.add_argument('--head', default='step-07')
    ap.add_argument('--project', default='StudentManagement'); ap.add_argument('--out', default=str(HERE / 'data'))
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='repotrace_rag_'))
    repo = Path(a.repo).resolve() if a.repo else None
    if repo is None:
        repo = work / a.project
        subprocess.run(['git', 'clone', '-q', str(HERE / 'StudentManagement.bundle'), str(repo)], check=True)
    commits = {ref: git(repo, 'rev-parse', ref).strip() for ref in (a.base, a.head)}
    (out / 'commits.txt').write_text(git(repo, 'log', '--format=%H %h %ad %s', '--date=short', a.head), encoding='utf-8')
    (out / 'diff.patch').write_text(git(repo, 'diff', '--no-color', '-M', a.base, a.head), encoding='utf-8')
    (out / 'diff_name_status.txt').write_text(git(repo, 'diff', '--name-status', '-M', a.base, a.head), encoding='utf-8')
    (out / 'diff_numstat.txt').write_text(git(repo, 'diff', '--numstat', '-M', a.base, a.head), encoding='utf-8')
    graphs = {}
    for ref, commit in commits.items():
        snap = work / ('snap_' + ref.replace('/', '_')); snap.mkdir()
        export_commit(repo, commit, snap)
        report = work / (ref.replace('/', '_') + '_map.json')
        repotrace('map', str(snap), '--output', str(report), '--overwrite')
        graphs[ref] = build_graph(json.loads(report.read_text(encoding='utf-8')), a.project, ref, commit)
        (out / f'graph_{ref}.json').write_text(json.dumps(graphs[ref], indent=1) + '\n', encoding='utf-8')
    hunks = {'base': {'ref': a.base, 'commit': commits[a.base]}, 'head': {'ref': a.head, 'commit': commits[a.head]},
             'files': diff_hunks(repo, a.base, a.head, graphs[a.base], graphs[a.head])}
    (out / 'diff_hunks.json').write_text(json.dumps(hunks, indent=1) + '\n', encoding='utf-8')
    change = work / 'change.json'
    repotrace('change', str(repo), '--base', a.base, '--head', a.head, '--output', str(change), '--overwrite')
    cleaned = clean(json.loads(change.read_text(encoding='utf-8')), [(str(repo), a.project), (str(work), '<tmp>'), (str(Path.home()), '~')])
    (out / 'change_report.json').write_text(json.dumps(cleaned, indent=1) + '\n', encoding='utf-8')
    for name in sorted(p.name for p in out.iterdir()):
        print('wrote', out / name)


if __name__ == '__main__':
    main()
