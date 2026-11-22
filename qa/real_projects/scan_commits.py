"""Read-only scan of a Git history for commits that are useful for evaluating RepoTrace (changes to Python source, not docs).
python3 scan_commits.py REPO SRC_PREFIX [--n 400]"""
import subprocess, sys, re
repo, src = sys.argv[1], sys.argv[2]
n = int(sys.argv[sys.argv.index('--n')+1]) if '--n' in sys.argv else 400
def git(*a): return subprocess.run(['git','-C',repo,*a],capture_output=True,text=True,errors='replace').stdout
out = git('log', f'-{n}', '--no-merges', '--format=@@%H|%ad|%s', '--date=short', '--numstat', '--', '*.py')
rows, cur = [], None
for line in out.splitlines():
    if line.startswith('@@'):
        h, d, s = line[2:].split('|', 2); cur = {'h': h, 'd': d, 's': s, 'src': [], 'tests': [], 'add': 0, 'del': 0}; rows.append(cur)
    elif line.strip() and cur:
        a, dl, f = line.split('\t')
        if a == '-': continue
        if not f.endswith('.py'): continue
        isT = bool(re.search(r'(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|conftest\.py$', f))
        (cur['tests'] if isT else cur['src']).append(f) if (isT or f.startswith(src)) else None
        if f.startswith(src) and not isT: cur['add'] += int(a); cur['del'] += int(dl)
good = [r for r in rows if r['src'] and 1 <= len(r['src']) <= 3 and r['add'] + r['del'] <= 120]
print(f'{repo}: {len(rows)} commits touching .py in the last {n}; {len(good)} touch 1-3 source files with <=120 changed source lines')
print('  with test changes too:', sum(1 for r in good if r['tests']), '| source-only:', sum(1 for r in good if not r['tests']))
for r in good[:int(sys.argv[sys.argv.index('--show')+1]) if '--show' in sys.argv else 0]:
    print(f"  {r['h'][:9]} {r['d']} src={len(r['src'])} t={len(r['tests'])} +{r['add']}/-{r['del']}  {r['s'][:75]}")
