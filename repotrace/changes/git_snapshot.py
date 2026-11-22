"""Read Python files of an existing Git commit into a temporary folder.

Only the Git object database is read: the user's branch, index and working files are
never changed, and no code from the repository is imported or executed.
"""
import os
import subprocess
from pathlib import Path, PurePosixPath

GIT_TIMEOUT_SECONDS = 120
BLOB_MODES = {'100644', '100755'}
BATCH_SIZE = 200


class GitError(ValueError):
    """A Git command failed or a reference could not be used."""


def run_git(repo, *args, input_bytes=None, timeout=GIT_TIMEOUT_SECONDS):
    # GIT_OPTIONAL_LOCKS=0 stops read commands (like status) from refreshing the index.
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0',
           'LC_ALL': 'C'}
    try:
        done = subprocess.run(['git', '-C', str(repo), *args], input=input_bytes,
                              capture_output=True, timeout=timeout, env=env)
    except FileNotFoundError:
        raise GitError('Git is not installed or not on PATH.')
    except subprocess.TimeoutExpired:
        raise GitError(f'Git timed out after {timeout} seconds: git {args[0]}')
    if done.returncode != 0:
        message = done.stderr.decode('utf-8', 'replace').strip() or f'exit {done.returncode}'
        raise GitError(f'git {args[0]} failed: {message}')
    return done.stdout


def check_repository(repo):
    path = Path(repo)
    if not path.is_dir():
        raise GitError('Choose an existing local repository folder.')
    inside = run_git(path, 'rev-parse', '--is-inside-work-tree').decode().strip()
    if inside != 'true':
        raise GitError('The folder is not a Git working tree.')
    return path.resolve()


def resolve_commit(repo, ref):
    """Pin a branch/tag/hash to one full commit ID, so later moves do not matter."""
    if not isinstance(ref, str) or not ref.strip() or ref.startswith('-'):
        raise GitError(f'Invalid Git reference: {ref!r}')
    try:
        out = run_git(repo, 'rev-parse', '--verify', '--quiet', '--end-of-options',
                      f'{ref}^{{commit}}')
    except GitError:
        raise GitError(f'Cannot resolve {ref!r} to a commit in this repository.')
    return out.decode().strip()


def worktree_is_dirty(repo):
    """Informational only: snapshots come from commits, never from the working tree."""
    return bool(run_git(repo, 'status', '--porcelain', '--untracked-files=no').strip())


# Root-level test configuration, read as text so the analysis can follow the project's declared import paths.
CONFIG_FILES = frozenset({'pytest.ini', 'pyproject.toml', 'tox.ini', 'setup.cfg'})


def list_python_blobs(repo, commit):
    """Return ([(path, blob_sha)], skipped) for .py entries of one commit, plus root-level pytest configuration."""
    raw = run_git(repo, 'ls-tree', '-r', '-z', '--full-tree', commit)
    found, skipped = [], []
    for entry in raw.split(b'\0'):
        if not entry:
            continue
        meta, _, path_bytes = entry.partition(b'\t')
        mode, kind, sha = meta.decode().split(' ')
        path = os.fsdecode(path_bytes)
        if not path.endswith('.py') and path not in CONFIG_FILES:
            continue
        if mode in BLOB_MODES and kind == 'blob':
            found.append((path, sha))
        else:
            reason = 'symbolic_link' if mode == '120000' else 'submodule' if mode == '160000' else 'not_regular_file'
            skipped.append({'path': path, 'reason': reason})
    return found, skipped


def read_blobs(repo, shas):
    """Yield (sha, bytes) using batched cat-file calls; chunking bounds memory."""
    for start in range(0, len(shas), BATCH_SIZE):
        chunk = shas[start:start + BATCH_SIZE]
        data = run_git(repo, 'cat-file', '--batch',
                       input_bytes=''.join(f'{sha}\n' for sha in chunk).encode())
        position = 0
        for sha in chunk:
            end = data.index(b'\n', position)
            header = data[position:end].split()
            if len(header) != 3 or header[1] != b'blob':
                raise GitError(f'Missing Git object: {sha}')
            size = int(header[2])
            yield sha, data[end + 1:end + 1 + size]
            position = end + 1 + size + 1


def safe_destination(root, relative):
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {'', '.', '..', '.git'} for part in pure.parts):
        return None
    target = root.joinpath(*pure.parts)
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return target


def materialize_commit(repo, commit, destination):
    """Write the commit's regular .py files under destination, keeping relative paths.

    Returns a record of what was written and what was skipped. Anything skipped is a
    coverage gap, never evidence that the file was absent.
    """
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    blobs, skipped = list_python_blobs(repo, commit)
    seen_lower, writable = set(), []
    for path, sha in blobs:
        target = safe_destination(root, path)
        key = path.lower()
        if target is None:
            skipped.append({'path': path, 'reason': 'unsafe_path'})
        elif key in seen_lower:
            # Case-insensitive file systems cannot hold both names.
            skipped.append({'path': path, 'reason': 'case_collision'})
        else:
            seen_lower.add(key)
            writable.append((path, sha, target))
    by_sha = {}
    for path, sha, target in writable:
        by_sha.setdefault(sha, []).append((path, target))
    written = 0
    for sha, content in read_blobs(repo, list(by_sha)):
        for path, target in by_sha[sha]:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                written += path.endswith('.py')
            except OSError as exc:
                skipped.append({'path': path, 'reason': f'write_failed: {type(exc).__name__}'})
    skipped.sort(key=lambda item: item['path'])
    return {'commit': commit, 'python_files_written': written, 'skipped': skipped}
