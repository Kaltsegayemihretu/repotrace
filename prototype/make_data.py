"""Bake real RepoTrace analysis of one project into prototype/data.js for the clickable prototype.

    python3 prototype/make_data.py "../StudentManagement"   (or "../test project 3")

The prototype is a throwaway design sketch: it reads this file and nothing else, runs no analysis itself,
and does not touch the real app. Everything it shows (links, tests, findings, history hints) comes from the
real engine on a real project; only the layout and screens are new.
"""
import json
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from repotrace.changes.change_impact import analyze_change_impact          # noqa: E402
from repotrace.graph.graph_explore import Explorer, is_test_file            # noqa: E402
from repotrace.graph.graph_index import GraphIndex                          # noqa: E402
from repotrace.graph.structure import build_structure                       # noqa: E402
from repotrace.mapping.repository import analyze_repository                 # noqa: E402
from repotrace.report.dashboard import build_view                           # noqa: E402
from repotrace.report.overview import build_overview                        # noqa: E402

STOP = set('the a an of to in and or for is are be this that it with as on by from at self none true false return if else not def class init main conftest usually'.split())
CODE_LINES, CODE_CHARS = 30, 1600


def words(text):
    """Split identifiers and prose into lowercase words: `retryPolicy` and `retry_policy` both give retry, policy."""
    text = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text or '')
    return [w for w in re.findall(r'[a-z][a-z0-9]+', text.lower().replace('_', ' ')) if w not in STOP and len(w) > 2]


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, check=True).stdout


def build_graph(folder):
    report = analyze_repository(Path(folder))
    index = GraphIndex(report, include_possible=True)
    explorer = Explorer(index)
    files = [f for f in report['files'] if f['status'] == 'ok']
    symbols = {s['id']: s for f in files for s in f['symbols']}
    structure = build_structure(report)

    nodes, by_key = [], {}

    def add(kind, name, qname, parent, file, line, **extra):
        node = {'i': len(nodes), 'k': kind, 'n': name, 'q': qname, 'p': parent, 'file': file, 'line': line,
                'test': is_test_file(file) if file else False, **extra}
        nodes.append(node)
        return node['i']

    dirs = {}
    for f in sorted(files, key=lambda f: f['file']):
        folder_name = str(Path(f['file']).parent)
        folder_name = '(top)' if folder_name == '.' else folder_name
        if folder_name not in dirs:
            dirs[folder_name] = add('dir', folder_name, folder_name, None, folder_name if folder_name != '(top)' else '', 0)
            nodes[dirs[folder_name]]['test'] = all(is_test_file(x['file']) for x in files if (str(Path(x['file']).parent) or '(top)') == folder_name)
    file_idx = {}
    for f in sorted(files, key=lambda f: f['file']):
        folder_name = str(Path(f['file']).parent)
        folder_name = '(top)' if folder_name == '.' else folder_name
        file_idx[f['file']] = add('file', Path(f['file']).name, f['file'], dirs[folder_name], f['file'], 1, lines=f.get('line_count', 0))
    class_idx, fn_idx = {}, {}
    for s in sorted(symbols.values(), key=lambda s: (s['file'], s['span']['line'])):
        doc = (s.get('docstring') or '').strip().split('\n')[0][:160]
        code = '\n'.join((s.get('source') or '').split('\n')[:CODE_LINES])[:CODE_CHARS]
        top = s['parent_id'].endswith('::module')
        if s['kind'] == 'CLASS' and top:
            class_idx[s['id']] = add('class', s['name'], s['qualified_name'], file_idx[s['file']], s['file'], s['span']['line'], doc=doc, code=code)
        elif s['kind'] == 'FUNCTION' and top:
            fn_idx[s['id']] = add('fn', s['name'], s['qualified_name'], file_idx[s['file']], s['file'], s['span']['line'], doc=doc, code=code,
                                  unres=index.unresolved_by_source.get(s['id'], 0))
    for s in sorted(symbols.values(), key=lambda s: (s['file'], s['span']['line'])):
        if s['kind'] == 'METHOD' and s['parent_id'] in class_idx:
            doc = (s.get('docstring') or '').strip().split('\n')[0][:160]
            code = '\n'.join((s.get('source') or '').split('\n')[:CODE_LINES])[:CODE_CHARS]
            fn_idx[s['id']] = add('fn', s['name'], s['qualified_name'], class_idx[s['parent_id']], s['file'], s['span']['line'], doc=doc, code=code,
                                  unres=index.unresolved_by_source.get(s['id'], 0))

    def owner(sid):
        s = symbols.get(sid)
        while s is not None:
            if s['id'] in fn_idx:
                return fn_idx[s['id']]
            if s['id'] in class_idx:
                return class_idx[s['id']]
            s = symbols.get(s['parent_id'])
        return None

    calls = Counter()
    for pairs in index.reverse.values():
        for pair in pairs:
            a, b = owner(pair['source']), owner(pair['target'])
            if a is not None and b is not None and a != b:
                tier = 1 if pair['evidence']['resolution'] == 'POSSIBLE' else 0
                calls[(a, b, tier)] += pair['call_site_count']
    id_of_entity = {e['id']: class_idx.get(e['id']) for e in structure['entities']}
    links = [[a, b, tier, n] for (a, b, tier), n in sorted(calls.items())]
    rel = []
    for e in structure['edges']:
        a, b = id_of_entity.get(e['source']), id_of_entity.get(e['target'])
        if a is not None and b is not None and e['kind'] in ('inherits', 'holds'):
            rel.append([a, b, 0 if e['kind'] == 'inherits' else 1])
    overview = build_overview(index, explorer, Path(folder).name)
    return report, nodes, links, rel, overview, {(n['file'], n['q']): n['i'] for n in nodes if n['k'] in ('fn', 'class')}


def concepts(nodes, links):
    """Group files into topics by who calls whom, then name each group by its most distinctive words.

    This is an interpretation, not a fact; the screen says so."""
    files = [n for n in nodes if n['k'] == 'file' and not n['test']]
    fid = {n['i']: n['i'] for n in files}

    def file_of(i):
        while nodes[i]['k'] != 'file':
            i = nodes[i]['p']
        return i
    weight = defaultdict(lambda: defaultdict(float))
    for a, b, tier, n in links:
        fa, fb = file_of(a), file_of(b)
        if fa != fb and fa in fid and fb in fid:
            weight[fa][fb] += n * (1 if tier == 0 else 0.5)
            weight[fb][fa] += n * (1 if tier == 0 else 0.5)
    label = {i: i for i in fid}
    for _ in range(12):
        changed = False
        for i in sorted(fid):
            votes = Counter()
            for j, w in weight[i].items():
                votes[label[j]] += w
            if votes:
                best = max(votes.items(), key=lambda kv: (kv[1], -kv[0]))[0]
                if best != label[i]:
                    label[i], changed = best, True
        if not changed:
            break
    groups = defaultdict(list)
    for i, lab in label.items():
        groups[lab].append(i)
    # Fold small groups into the group they belong with: first by who they call, then by the folder they sit in.
    folder_of = lambda i: nodes[nodes[i]['p']]['q']

    def regroup(min_size, max_groups):
        while True:
            groups = defaultdict(list)
            for i, lab in label.items():
                groups[lab].append(i)
            small = sorted((l for l, m in groups.items() if len(m) < min_size), key=lambda l: (len(groups[l]), l))
            if not small and len(groups) <= max_groups:
                return
            lab = small[0] if small else min(groups, key=lambda l: (len(groups[l]), l))
            if len(groups) <= 1:
                return
            votes = Counter()
            for m in groups[lab]:
                for j, w in weight[m].items():
                    if label[j] != lab:
                        votes[label[j]] += w
                for j in fid:                           # same folder counts too, so files with no links still land somewhere sensible
                    if label[j] != lab and folder_of(j) == folder_of(m):
                        votes[label[j]] += 0.5
            target = max(votes.items(), key=lambda kv: (kv[1], -kv[0]))[0] if votes else max((l for l in groups if l != lab), key=lambda l: len(groups[l]))
            for m in groups[lab]:
                label[m] = target
    regroup(3, 12)
    groups = defaultdict(list)
    for i, lab in label.items():
        groups[lab].append(i)
    tokens = {lab: Counter() for lab in groups}
    for lab, members in groups.items():
        for n in nodes:
            top = n
            while top['k'] != 'file' and top['p'] is not None:
                top = nodes[top['p']]
            if top['i'] in members:
                tokens[lab].update(words(n['n'] + ' ' + (n.get('doc') or '')))
    df = Counter(w for c in tokens.values() for w in c)
    names, ordered = [], sorted(groups, key=lambda l: (-len(groups[l]), l))
    where = {}
    for k, lab in enumerate(ordered):
        score = {w: c * (1 / df[w]) for w, c in tokens[lab].items()}
        top = [w for w, _ in sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
        names.append(' · '.join(top) or 'misc')
        for m in groups[lab]:
            where[m] = k
    mapping = {}
    for n in nodes:
        top = n
        while top['k'] != 'file' and top['p'] is not None:
            top = nodes[top['p']]
        if top['k'] == 'file':
            mapping[n['i']] = where.get(top['i'], -1)
    for n in nodes:
        if n['k'] == 'dir':
            kids = Counter(mapping.get(c['i'], -1) for c in nodes if c['p'] == n['i'])
            mapping[n['i']] = kids.most_common(1)[0][0] if kids else -1
    # tests belong to the topic they mostly exercise
    for n in nodes:
        if n['k'] == 'file' and n['test']:
            votes = Counter(mapping.get(b, -1) for a, b, t, c in links if file_of_safe(nodes, a) == n['i'])
            mapping[n['i']] = votes.most_common(1)[0][0] if votes else -1
    for n in nodes:
        if n['k'] in ('class', 'fn') and n['test']:
            top = n
            while top['k'] != 'file':
                top = nodes[top['p']]
            mapping[n['i']] = mapping.get(top['i'], -1)
    return names, [mapping.get(n['i'], -1) for n in nodes]


def file_of_safe(nodes, i):
    while nodes[i]['k'] != 'file':
        i = nodes[i]['p']
    return i


def history(repo, window=60, bulk=20):
    """File-level co-change from Git: which .py files were edited in the same commits. Hints, not facts."""
    out = git(repo, 'log', f'-n{window}', '--name-only', '--format=@@%H').split('@@')[1:]
    commits = []
    for block in out:
        lines = [l for l in block.strip().split('\n')[1:] if l.endswith('.py')]
        if lines and len(lines) <= bulk:
            commits.append(set(lines))
    count, pair = Counter(), defaultdict(Counter)
    for files in commits:
        for a in files:
            count[a] += 1
            for b in files:
                if a != b:
                    pair[a][b] += 1
    return commits, count, pair


def snapshot(repo, rev):
    """The project exactly as it was at one commit, unpacked into a temporary folder."""
    out = tempfile.mkdtemp(prefix=f'rt-{rev}-')
    archive = subprocess.run(['git', '-C', str(repo), 'archive', rev], capture_output=True, check=True).stdout
    subprocess.run(['tar', '-x', '-C', out], input=archive, check=True)
    return Path(out)


def graph_of(folder, version):
    report, nodes, links, rel, overview, index = build_graph(folder)
    names, concept_of = concepts(nodes, links)
    return {'version': version, 'nodes': nodes, 'links': links, 'rel': rel, 'concepts': names, 'concept_of': concept_of,
            'calls': overview['calls'], 'files': overview['files'], 'functions': overview['functions'], '_index': index}


def lines_of(folder, file):
    try:
        return (Path(folder) / file).read_text(encoding='utf-8').split('\n')
    except OSError:
        return []


def signature(folder, file, line):
    """The def line(s) of a function as written in that version, decorators skipped."""
    rows, out = lines_of(folder, file), []
    for row in rows[max(line, 1) - 1:max(line, 1) + 25]:
        if not out and row.strip().startswith('@'):
            continue
        out.append(row.strip() if not out else '    ' + row.strip())
        if row.rstrip().endswith(':'):
            break
    return '\n'.join(out) or None


def snippet(folder, file, line, around=2):
    rows = lines_of(folder, file)
    return [[k + 1, rows[k]] for k in range(max(0, line - 1 - around), min(len(rows), line + around))]


def add_removed(g, base_g, report, base_dir):
    """Code deleted by the change still matters to a reviewer: add it back, marked as the before-version, with its old links."""
    nodes, index, base_nodes, base_index = g['nodes'], g['_index'], base_g['nodes'], base_g['_index']
    find = lambda kind, q: next((n['i'] for n in nodes if n['k'] == kind and n['q'] == q), None)
    for ch in report['changes']:
        if ch['change'] != 'deleted':
            continue
        file, q = ch['file'], ch['qualified_name']
        bi = base_index.get((file, q))
        if bi is None or (file, q) in index:
            continue
        parent = index.get((file, q.rsplit('.', 1)[0])) if '.' in q else None
        if parent is None:
            parent = find('file', file)
        if parent is None:
            folder_name = str(Path(file).parent)
            folder_name = '(top)' if folder_name == '.' else folder_name
            d = find('dir', folder_name)
            if d is None:
                d = len(nodes)
                nodes.append({'i': d, 'k': 'dir', 'n': folder_name, 'q': folder_name, 'p': None, 'file': folder_name, 'line': 0, 'test': False, 'gone': True})
                g['concept_of'].append(-1)
            parent = len(nodes)
            nodes.append({'i': parent, 'k': 'file', 'n': Path(file).name, 'q': file, 'p': d, 'file': file, 'line': 1, 'test': is_test_file(file), 'gone': True})
            g['concept_of'].append(-1)
        b = base_nodes[bi]
        new = {**b, 'i': len(nodes), 'p': parent, 'gone': True}
        nodes.append(new)
        g['concept_of'].append(g['concept_of'][parent] if parent < len(g['concept_of']) else -1)
        index[(file, q)] = new['i']
        head_of = lambda j: index.get((base_nodes[j]['file'], base_nodes[j]['q']))
        for a, bb, tier, n in base_g['links']:
            if bb == bi and head_of(a) is not None and head_of(a) != new['i']:
                g['links'].append([head_of(a), new['i'], 2, n])
            if a == bi and head_of(bb) is not None and head_of(bb) != new['i']:
                g['links'].append([new['i'], head_of(bb), 2, n])


def review(repo, base, head, label, example, hist):
    base_dir, head_dir = snapshot(repo, base), snapshot(repo, head)
    report = analyze_change_impact(repo, base, head, include_possible=True)
    view = build_view(report, repo)
    g, base_g = graph_of(head_dir, f'{head} (after the change)'), graph_of(base_dir, base)
    add_removed(g, base_g, report, base_dir)
    nodes, code_index = g['nodes'], g['_index']
    node_of = lambda file, q: code_index.get((file, q))
    spans = {(c['file'], c['qualified_name']): c for c in report['changes']}
    changed, affected, concerns, tests, candidates = [], {}, [], [], []
    for c in view['changes']:
        n = node_of(c['file'], c['name'])
        sp = spans.get((c['file'], c['name'])) or {}
        before = signature(base_dir, c['file'], sp['before']['span']['line']) if sp.get('before') else None
        after = signature(head_dir, c['file'], sp['after']['span']['line']) if sp.get('after') else None
        changed.append({'node': n, 'name': c['name'], 'file': c['file'], 'change': c['change'], 'pair': c.get('pair'), 'diff': (c.get('diff') or '')[:2400],
                        'before_sig': before, 'after_sig': after, 'line': (sp.get('after') or sp.get('before') or {'span': {'line': c.get('line') or 1}})['span']['line']})
        for cand in c['candidates']:
            cn = node_of(cand['file'], cand['name'])
            is_test = 'test code' in cand['flags']
            entry = {'node': cn, 'name': cand['name'], 'file': cand['file'], 'line': cand['line'], 'dist': cand['distance'], 'tier': 0 if cand['tier'] == 'supported' else 1,
                     'test': is_test, 'flags': cand['flags'], 'for': c['name'], 'for_node': n}
            if cn is not None and (cn not in affected or affected[cn]['dist'] > entry['dist']):
                affected[cn] = entry
            if is_test:
                tests.append({**entry, 'reason': ('calls ' + c['name'] + ' directly' if cand['distance'] == 1 else f"reaches {c['name']} through {cand['distance'] - 1} other function" + ('s' if cand['distance'] > 2 else '')) + (' (sure)' if entry['tier'] == 0 else ' (maybe: depends on an assumption)')})
            else:
                candidates.append(entry)
            if any(f.startswith(('head still references', 'not updated')) for f in cand['flags']):
                rows, short = lines_of(head_dir, cand['file']), c['name'].split('.')[-1]
                use = next((k + 1 for k in range(cand['line'], min(len(rows), cand['line'] + 80)) if re.search(r'\b' + re.escape(short) + r'\b', rows[k])), cand['line'])
                cand = {**cand, 'line': use}
                concerns.append({'type': 'leftover', 'title': f"{cand['name']} may still use the removed {c['name']}", 'node': cn, 'target': n, 'target_name': c['name'], 'changed': len(changed) - 1, 'file': cand['file'], 'line': cand['line'],
                                 'what': f"{cand['name']} used {c['name']}, which no longer exists in the new version.",
                                 'why': 'If it still calls the old name it will fail when it runs.',
                                 'snippet': snippet(head_dir, cand['file'], cand['line']), 'check': f"Open {cand['name']} and update or confirm its use of {c['name']}."})
        if c.get('decorator_effect'):
            e = c['decorator_effect']
            concerns.append({'type': 'decorator', 'title': f"Decorator {c['name']} changed: it wraps {len(e['wrapped'])} functions", 'node': n, 'target': n, 'target_name': c['name'], 'file': c['file'], 'line': c.get('line') or 1,
                             'what': f"{c['name']} wraps {', '.join(e['wrapped'][:5])}.", 'why': 'Every call to a wrapped function now runs the changed code first.',
                             'snippet': snippet(head_dir, c['file'], c.get('line') or 1), 'check': 'Run the tests for the wrapped functions and their callers.'})
    by_name = {ch['name'].split('.')[-1]: k for k, ch in enumerate(changed)}
    for ch in report['changes']:
        for f in (ch.get('signature_review') or {}).get('findings', [])[:12]:
            cn = node_of(f['file'], f['caller'])
            k = by_name.get(f['called_expression'].split('.')[-1])
            concerns.append({'type': 'signature', 'title': f"{f['caller']} → {f['called_expression']}() no longer fits", 'node': cn, 'target': changed[k]['node'] if k is not None else None,
                             'target_name': f['called_expression'], 'changed': k, 'file': f['file'], 'line': f['span']['line'],
                             'what': f"The call fits the old parameters but not the new ones: {f['reason']}.",
                             'why': 'This call would raise an error when it runs.', 'snippet': snippet(head_dir, f['file'], f['span']['line']), 'check': f['action']})
    seen, ordered = set(), []
    for c in sorted(candidates, key=lambda c: (c['dist'], c['tier'], c['file'], c['name'])):
        if (c['file'], c['name']) not in seen:
            seen.add((c['file'], c['name']))
            ordered.append(c)
    # Tests come from the engine's test shortlist (changes/test_selection.py): both versions' graphs, real tests only,
    # setup hooks and fixtures credited to the tests they run for.
    def call_line(file, from_line, name):
        """First line from `from_line` inside that function body that calls `name`."""
        rows, short = lines_of(head_dir, file), name.split('.')[-1]
        if not rows or not from_line:
            return None
        indent = len(rows[from_line - 1]) - len(rows[from_line - 1].lstrip())
        for k in range(from_line, min(len(rows), from_line + 80)):
            row = rows[k]
            if row.strip() and (len(row) - len(row.lstrip())) <= indent and not row.lstrip().startswith(('#', '@', ')')):
                break
            if re.search(r'\b' + re.escape(short) + r'\s*\(', row):
                return k + 1
        return None
    tests_out, removed_tests, tseen = [], [], set()
    for ch in report['changes']:
        sel = ch.get('test_selection') or {}
        target = node_of(ch['file'], ch['qualified_name'])
        for t in sel.get('tests', []):
            if (t['file'], t['name']) in tseen:
                continue
            tseen.add((t['file'], t['name']))
            via, chain = t.get('via'), t['chain']
            if via and via['kind'] == 'setup':                       # the evidence is in the hook, not the test body
                hook = node_of(t['file'], via['name'])
                where = (t['file'], nodes[hook]['line'] if hook is not None else None, chain[2] if len(chain) > 2 else '')
            else:
                where = (t['file'], t['line'], chain[1] if len(chain) > 1 else '')
            line = call_line(*where) if not (via and via['kind'] == 'fixture') else None
            tests_out.append({'node': node_of(t['file'], t['name']), 'name': t['name'], 'file': t['file'], 'line': t['line'],
                              'tier': 0 if t['tier'] == 'supported' else 1, 'dist': t['distance'], 'for': ch['qualified_name'], 'for_node': target,
                              'chain': chain, 'seen_in': t['seen_in'], 'via': via, 'run': t['run'],
                              'call_line': line, 'snippet': snippet(head_dir, where[0], line) if line else None})
        for t in sel.get('removed_tests', []):
            if (t['file'], t['name']) not in tseen:
                tseen.add((t['file'], t['name']))
                removed_tests.append({'name': t['name'], 'file': t['file'], 'chain': t['chain'], 'for': ch['qualified_name']})
    tests_out.sort(key=lambda t: (t['tier'], t['dist'], t['file'], t['name']))
    untraced_in_tests = sum(n.get('unres') or 0 for n in nodes if n['k'] == 'fn' and n['test'] and not n.get('gone'))
    commits, count, pair = hist
    hints = []
    for f in sorted({c['file'] for c in changed}):
        if count.get(f):
            others = [{'file': b, 'together': n, 'of': count[f]} for b, n in pair[f].most_common(3)]
            hints.append({'file': f, 'changed_in': count[f], 'with': others})
    gaps = [g_['message'] for g_ in report['gaps']][:4] + report['warnings'][:3]
    unres = sum(nodes[ch['node']].get('unres') or 0 for ch in changed if ch['node'] is not None)
    if unres:
        gaps.append(f"{unres} calls inside the changed functions could not be traced.")
    gaps.append(f"{untraced_in_tests} calls inside tests could not be followed (for example objects handed over by parametrized fixtures, "
                "built by a factory or dependency container, or unpacked from a tuple), so tests that reach the change that way are missing from the list.")
    gaps.append('Signature checks cover explicit calls to plain functions. Methods, decorated targets, constructors and calls using *args/**kwargs are not checked. No findings does not mean safe.')
    g.pop('_index')
    return g, {'id': f'{base}..{head}', 'graph': f'{base}..{head}', 'label': label, 'example': example, 'base': base, 'head': head, 'story': view['story'],
               'changed': changed, 'affected': list(affected.values()), 'concerns': concerns[:14], 'candidates': ordered[:10], 'tests': tests_out, 'removed_tests': removed_tests,
               'history': hints, 'history_window': len(commits), 'gaps': [x for x in gaps if x]}


# What to show for each practice project: the comparisons to review, an optional demo edit, and sample questions.
PROJECTS = {
    'StudentManagement': {
        'edit': None,
        'reviews': [('step-06', 'step-07', 'Rename: read_course_by_id → fetch_course_by_id (one caller forgotten)'),
                    ('step-04', 'step-05', 'A phone field added to Student'),
                    ('step-07', 'step-08', 'A function moved from a service to a repository'),
                    ('step-10', 'step-11', 'An unused function deleted')],
        'questions': ['where is an email checked?', 'how are students saved?', 'what happens when a course is full?', 'how does a student enroll in a course?']},
    'test project 3': {
        'edit': ('step-11', 'queuely/util/validation.py', 'def clamp(value, low, high):', 'def clamp(value, low, high, mode):', 'Example edit: clamp() gets a new required parameter'),
        'reviews': [('step-01', 'step-02', 'Real commit: clamp() rewritten'), ('step-08', 'step-09', 'Real commit: one function moved, another renamed'),
                    ('step-05', 'step-06', 'Real commit: the @task decorator changed')],
        'questions': ['where are retries handled?', 'how are jobs stored?', 'what happens when a job fails?', 'where is authentication checked?']},
}


def main():
    folder = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT.parent / 'StudentManagement'
    cfg = PROJECTS.get(folder.name, {'edit': None, 'reviews': [], 'questions': []})
    sha = git(folder, 'rev-parse', '--short', 'HEAD').strip()
    dirty = any(l.endswith('.py') for l in git(folder, 'status', '--porcelain').split('\n'))
    main_g = graph_of(folder, f"working folder (commit {sha}{', plus unsaved edits' if dirty else ''})")
    main_g.pop('_index')
    hist = history(folder)
    jobs = []
    if cfg['edit']:                       # a demo edit, so there is something for the checks to find
        at, path, old, new, label = cfg['edit']
        tmp = tempfile.mkdtemp(prefix='rt-proto-')
        subprocess.run(['git', 'clone', '-q', str(folder), tmp], check=True)
        git(tmp, 'config', 'user.name', 'proto'), git(tmp, 'config', 'user.email', 'proto@example.com')
        git(tmp, 'checkout', '-q', at)
        f = Path(tmp) / path
        f.write_text(f.read_text().replace(old, new))
        git(tmp, 'commit', '-q', '-am', label)
        git(tmp, 'tag', 'edit-1')
        jobs.append((tmp, at, 'edit-1', label, True))
    jobs += [(folder, base, head, label, False) for base, head, label in cfg['reviews']]
    graphs, reviews = {'main': main_g}, []
    for repo, base, head, label, example in jobs:
        g, r = review(repo, base, head, label, example, hist)
        graphs[r['graph']], _ = g, reviews.append(r)
    data = {'project': {'name': folder.name, 'made_at': time.strftime('%Y-%m-%d %H:%M')}, 'graphs': graphs, 'reviews': reviews, 'questions': cfg['questions']}
    out = Path(__file__).with_name('data.js')
    out.write_text('window.PROTO = ' + json.dumps(data, separators=(',', ':')) + ';\n', encoding='utf-8')
    print(f'{out.name}: {out.stat().st_size // 1024} KB | graphs {[(k, len(g["nodes"]), g["version"]) for k, g in graphs.items()]}')
    for r in reviews:
        print(' ', r['id'], 'concerns', [c['title'] for c in r['concerns']], 'tests', [t['name'] for t in r['tests']][:4], 'removed nodes', sum(1 for n in graphs[r['graph']]['nodes'] if n.get('gone')),
              'changed', [(c['name'], c['change']) for c in r['changed']])


if __name__ == '__main__':
    main()
