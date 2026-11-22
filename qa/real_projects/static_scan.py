"""For each repo, run RepoTrace's change report (static: reads Git objects, never runs the project) on recent commits that touch
1-3 source files, and count how many contain real function-level changes.  python3 static_scan.py REPO SRC_PREFIX SOURCE_ROOT [N]"""
import json, re, subprocess, sys, tempfile, os, time
repo, src, root = os.path.abspath(sys.argv[1]), sys.argv[2], sys.argv[3]
N = int(sys.argv[4]) if len(sys.argv) > 4 else 15
from pathlib import Path
R = str(Path(__file__).resolve().parents[2])
def git(*a): return subprocess.run(['git', '-C', repo, *a], capture_output=True, text=True, errors='replace').stdout
rows, cur = [], None
for line in git('log', '-200', '--no-merges', '--format=@@%H|%P|%s', '--numstat', '--', '*.py').splitlines():
    if line.startswith('@@'):
        h, p, s = line[2:].split('|', 2); cur = {'h': h, 'p': p.split()[0] if p else None, 's': s, 'src': [], 'tests': 0, 'ch': 0}; rows.append(cur)
    elif '\t' in line and cur:
        a, d, f = line.split('\t')
        if a == '-' or not f.endswith('.py'): continue
        if re.search(r'(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|conftest\.py$', f): cur['tests'] += 1
        elif f.startswith(src): cur['src'].append(f); cur['ch'] += int(a) + int(d)
cands = [r for r in rows if r['p'] and 1 <= len(r['src']) <= 3 and r['ch'] <= 120][:N]
res = []
for c in cands:
    out = tempfile.mktemp(suffix='.json'); t = time.time()
    cmd = [sys.executable, '-B', '-m', 'repotrace', 'change', repo, '--base', c['p'], '--head', c['h'], '--source-root', root, '--output', out, '--overwrite', '--include-possible']
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=R, env=dict(os.environ, PYTHONPATH=R), timeout=300)
    row = {'commit': c['h'][:9], 'subject': c['s'][:70], 'src_files': len(c['src']), 'test_files_changed': c['tests'], 'seconds': round(time.time() - t, 1)}
    try:
        j = json.load(open(out)); s = j['summary']
        row.update(status=j['status'], modified=s['functions_modified'], added=s['functions_added'], deleted=s['functions_deleted'], gaps=s['gaps'],
                   tests_suggested=sum(len((ch.get('test_selection') or {}).get('tests', [])) for ch in j['changes']),
                   renamed_or_signature=len(j.get('signature_review') or []))
    except Exception as e:
        row.update(status='no report', error=(r.stderr or r.stdout)[-160:].strip().replace('\n', ' '))
    res.append(row)
json.dump(res, open(f'scan_{os.path.basename(repo)}.json', 'w'), indent=1)
ok = [r for r in res if r.get('status') == 'ok']
real = [r for r in ok if r['modified'] + r['added'] + r['deleted'] > 0]
print(f"{os.path.basename(repo)}: {len(res)} commits scanned | report ok {len(ok)} | with function changes {len(real)} | with tests suggested {sum(1 for r in real if r['tests_suggested'])} | not ok: {[ (r['commit'], r.get('status'), r.get('error','')[:60]) for r in res if r.get('status')!='ok'][:3]}")
