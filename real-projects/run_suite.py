"""Run one project's own test command on a throwaway copy of one commit, network blocked.
python3 run_suite.py REPO COMMIT VENV -- CMD...   (prints a one-line result; exit 0 always)"""
import os, subprocess, sys, tarfile, tempfile, io, re, time, shutil
repo, commit, venv = sys.argv[1:4]; repo, venv = os.path.abspath(repo), os.path.abspath(venv); cmd = sys.argv[sys.argv.index('--') + 1:]
work = tempfile.mkdtemp(prefix='suite_')
data = subprocess.run(['git', '-C', repo, 'archive', '--format=tar', commit], capture_output=True).stdout
tarfile.open(fileobj=io.BytesIO(data)).extractall(work, filter='data')
env = dict(os.environ, PYTHONPATH=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'netblock') + os.pathsep + os.path.join(work, 'src') + os.pathsep + work,
           PATH=os.path.join(venv, 'bin') + os.pathsep + os.environ['PATH'], PYTHONDONTWRITEBYTECODE='1', VIRTUAL_ENV=venv)
t = time.time()
try:
    r = subprocess.run([os.path.join(venv, 'bin', cmd[0]) if os.path.exists(os.path.join(venv, 'bin', cmd[0])) else cmd[0], *cmd[1:]], cwd=work, env=env, capture_output=True, text=True, timeout=900)
    tail = (r.stdout + r.stderr).strip().splitlines()[-4:]
    print(f'{repo.rsplit("/",1)[-1]}@{commit[:9]} exit={r.returncode} {time.time()-t:.0f}s | ' + ' / '.join(tail)[:330])
except subprocess.TimeoutExpired:
    print(f'{repo}@{commit[:9]} TIMEOUT')
shutil.rmtree(work, ignore_errors=True)
