"""Run RepoTrace's test-shortlist scorer on many commit pairs of one repo (it EXECUTES the repo's tests, in throwaway copies, network blocked).
python3 eval_tests.py REPO SRC_PREFIX VENV_PYTHON OUT.json [N]"""
import json, os, re, subprocess, sys, tempfile
repo, src, py, out = os.path.abspath(sys.argv[1]), sys.argv[2], os.path.abspath(sys.argv[3]), sys.argv[4]
N = int(sys.argv[5]) if len(sys.argv) > 5 else 30
R = '/Users/israelhayalsew/Desktop/repotrace-mvp'
log = subprocess.run(['git', '-C', repo, 'log', '-120', '--no-merges', '--format=@@%H|%P|%s', '--numstat', '--', '*.py'], capture_output=True, text=True, errors='replace').stdout
rows, cur = [], None
for line in log.splitlines():
    if line.startswith('@@'):
        h, p, s = line[2:].split('|', 2); cur = {'h': h, 'p': (p.split() or [None])[0], 's': s, 'src': [], 'ch': 0}; rows.append(cur)
    elif '\t' in line and cur:
        a, d, f = line.split('\t')
        if a != '-' and f.endswith('.py') and f.startswith(src) and not re.search(r'(^|/)(tests?|testing)/|(^|/)test_', f): cur['src'].append(f); cur['ch'] += int(a) + int(d)
picked = [r for r in rows if r['p'] and 1 <= len(r['src']) <= 3 and r['ch'] <= 120 and not re.search(r'\b(release|bump|version|docs?|typo|changelog)\b', r['s'], re.I)][:N]
results = []
for c in picked:
    tmp = tempfile.mktemp(suffix='.json')
    cmd = [sys.executable, '-B', '-m', 'repotrace.evaluation.test_score', repo, c['p'], c['h'], '--python', py, '--json', tmp]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=R, env=dict(os.environ, PYTHONPATH=R), timeout=1500)
        j = json.load(open(tmp)); j['commit'] = c['h']; j['subject'] = c['s']; j['stdout'] = r.stdout[-400:]
    except Exception as e:
        j = {'commit': c['h'], 'subject': c['s'], 'complete': False, 'error': str(e)[:200]}
    results.append(j); json.dump(results, open(out, 'w'), indent=1)
    rel = j.get('relevant') or {}
    print(c['h'][:9], 'complete' if j.get('complete') else 'INCOMPLETE', f"changed={len(j.get('changed_functions', []))} relevant={rel.get('truth')} suggested={rel.get('suggested')} found={rel.get('found')} failing={(j.get('failing') or {}).get('truth')}", '|', c['s'][:50], flush=True)
