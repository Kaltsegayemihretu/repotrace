"""Read Python files, link supported imports and collect evidence-backed method candidates."""
import argparse
import configparser
import copy
import re
import json
import os
import platform
from pathlib import Path

from repotrace.reading.analyzer import MAX_FILE_BYTES, analyze_file, write_json_report
from repotrace.mapping.project_resolver import resolve_project_calls
from repotrace.mapping.resolution_summary import summarize_calls


DEFAULT_EXCLUDED_DIRS = frozenset({
    '.git', '.hg', '.svn', '.venv', 'venv', 'env', '.env', '__pycache__',
    'node_modules', '.tox', '.nox', '.mypy_cache', '.pytest_cache',
    '.ruff_cache', 'build', 'dist', '.eggs',
})


def pytest_configuration(root):
    """Read supported root-level pytest configuration without loading project code."""
    root = Path(root)
    found = None
    for name, section in (('pytest.ini', 'pytest'), ('pyproject.toml', None), ('tox.ini', 'pytest'), ('setup.cfg', 'tool:pytest')):
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        if section is None:                                   # pyproject.toml: the [tool.pytest.ini_options] table
            table = re.search(r'^\[tool\.pytest\.ini_options\]\s*$(.*?)(?=^\[|\Z)', text, re.M | re.S)
            if not table:
                continue
            values = {}
            for key in ('pythonpath', 'testpaths', 'python_files', 'python_classes', 'python_functions', 'addopts', 'norecursedirs'):
                m = re.search(rf'^{key}\s*=\s*(\[[^\]]*\]|"[^"]*"|\'[^\']*\')', table.group(1), re.M)
                if m:
                    values[key] = ' '.join(re.findall(r'["\']([^"\']+)["\']', m.group(1)))
        else:
            parser = configparser.ConfigParser(interpolation=None)
            try:
                parser.read_string(text)
            except configparser.Error:
                continue
            if not parser.has_section(section):
                continue
            values = dict(parser[section])
        found = (name, values)
        break                                                 # pytest uses the first configuration file it finds
    return found or (None, {})


def pytest_source_roots(root):
    """Use configured pytest import paths when available."""
    root = Path(root)
    name, values = pytest_configuration(root)
    if not values.get('pythonpath', '').split():
        return None, None
    roots = []
    for entry in values['pythonpath'].split() + [t for t in values.get('testpaths', '').split()
                                                 if (root / t).is_dir() and not (root / t / '__init__.py').exists()]:
        entry = entry.strip().rstrip('/') or '.'
        if (root / entry).is_dir() and entry not in roots:
            roots.append(entry)
    return (roots or None), name


def analyze_repository(folder, *, exclude_dirs=(), max_file_bytes=MAX_FILE_BYTES,
                       source_roots=None, resolve_imports=True):
    """Find files, reuse the one-file analyzer, then summarize its reports."""
    report = {
        'schema_version': '0.3', 'report_kind': 'repository', 'status': 'error',
        'root': str(Path(folder).absolute()), 'analyzer_python': platform.python_version(),
        'file_schema_version': '0.6', 'files': [], 'skipped': [], 'diagnostics': [],
        'summary': {},
        'limitations': [
            'Supported local and explicit-import function targets are potential links, not proof of execution.',
            'Import lookup assumes selected source roots and ordinary initialized-module bindings.',
            'Runtime import hooks, sys.path changes and circular initialization timing are not simulated.',
            'Explicit re-export chains are followed with cycle/depth guards. Wildcard imports are not expanded.',
            'Method/constructor candidates are POSSIBLE, not runtime guarantees; runtime type inference is incomplete.',
            'linked_calls counts only LIKELY internal targets; unresolved_calls includes POSSIBLE candidates for compatibility.',
            'Built-in/external references do not count as resolved internal calls. Missing modules may be external or misconfigured.',
            'Only supported function-body calls are linked; other call sites remain recorded.',
            'Explicit imported-module attribute writes invalidate targets; arbitrary alias mutation is not modeled.',
            'This is a working-folder scan, not a Git revision comparison.',
            'IDs are meaningful within this root and snapshot, not globally or across revisions.',
            'Excluded directories are recorded once; their contents are not enumerated.',
            'Symbolic links are not followed. Gitignore rules are not interpreted.',
            'Tests are read as source, never executed. Pytest-specific discovery comes later.',
            'Input size is bounded, but this is not a sandbox for hostile repositories.',
        ],
    }
    try:
        root = Path(folder).resolve()
        report['root'] = str(root)
        if not root.is_dir():
            raise ValueError('Choose an existing project folder, not a file.')
        if isinstance(exclude_dirs, str):
            raise ValueError('exclude_dirs must be a collection of directory names.')
        extras = set(exclude_dirs)
        if any(not isinstance(name, str) or name in {'', '.', '..'} or
               '/' in name or '\\' in name for name in extras):
            raise ValueError('Extra exclusions must be directory names, not paths.')
        if not isinstance(max_file_bytes, int) or max_file_bytes < 1:
            raise ValueError('max_file_bytes must be a positive integer.')
        excluded = DEFAULT_EXCLUDED_DIRS | extras
        detected_from = None
        if source_roots is None:
            source_roots, detected_from = pytest_source_roots(root)
        roots = ['.'] if source_roots is None else source_roots
        if not isinstance(roots, (list, tuple, set)) or not roots:
            raise ValueError('source_roots must be a non-empty collection of relative directories.')
        normalized = set()
        for name in roots:
            if not isinstance(name, str) or not name or Path(name).is_absolute():
                raise ValueError('Source roots must be relative directory names.')
            candidate = (root / name).resolve()
            relative_root = candidate.relative_to(root).as_posix()
            if not candidate.is_dir():
                raise ValueError(f'Source root does not exist: {name}')
            normalized.add(relative_root)
        report['settings'] = {'excluded_directory_names': sorted(excluded),
                              'max_file_bytes': max_file_bytes, 'follow_symlinks': False,
                              'source_roots': sorted(normalized), 'resolve_imports': resolve_imports}
        if detected_from:
            report['settings']['source_roots_from'] = f'{detected_from} (pytest pythonpath and testpaths)'
    except (OSError, ValueError, RuntimeError) as exc:
        report['diagnostics'].append({'stage': 'input', 'type': type(exc).__name__,
                                      'message': str(exc)})
        return finish_report(report)

    def relative(path):
        return Path(path).relative_to(root).as_posix()

    def walk_error(error):
        path = Path(error.filename) if error.filename else root
        report['diagnostics'].append({'stage': 'discovery', 'type': type(error).__name__,
                                      'path': relative(path), 'message': str(error)})

    paths = []
    for directory, dirs, filenames in os.walk(root, topdown=True, followlinks=False,
                                             onerror=walk_error):
        kept = []
        for name in sorted(dirs):
            path = Path(directory) / name
            reason = ('symbolic_link' if path.is_symlink() else
                      'excluded_directory' if name in excluded else None)
            if reason:
                report['skipped'].append({'path': relative(path), 'kind': 'directory',
                                           'reason': reason})
            else:
                kept.append(name)
        dirs[:] = kept
        for name in sorted(filenames):
            path = Path(directory) / name
            # Record symlinks explicitly, including broken links; never follow them.
            if path.is_symlink():
                report['skipped'].append({'path': relative(path), 'kind': 'entry',
                                           'reason': 'symbolic_link'})
            elif path.suffix == '.py':
                if path.is_file():
                    paths.append(path)
                else:
                    report['skipped'].append({'path': relative(path), 'kind': 'entry',
                                               'reason': 'not_regular_file'})

    for path in sorted(paths, key=relative):
        report['files'].append(analyze_file(path, root=root, max_bytes=max_file_bytes))
    if resolve_imports:
        # Roll back all partial project-pass changes if this pass cannot finish.
        original_files = copy.deepcopy(report['files'])
        try:
            resolve_project_calls(report)
        except (SyntaxError, ValueError, RecursionError) as exc:
            report['files'] = original_files
            report.pop('project_index', None)
            report['diagnostics'].append({'stage': 'project_resolution',
                                          'type': type(exc).__name__, 'message': str(exc)})
    report['skipped'].sort(key=lambda entry: entry['path'])
    if not paths and not report['diagnostics']:
        report['diagnostics'].append({'stage': 'discovery', 'type': 'NoPythonFiles',
                                      'severity': 'info',
                                      'message': 'No in-scope Python files were found.'})
    report['status'] = 'ok'
    return finish_report(report)


def finish_report(report):
    """Count only successful files as usable analysis data."""
    good = [item for item in report['files'] if item['status'] == 'ok']
    failed = len(report['files']) - len(good)
    discovery_failed = any(d.get('severity') != 'info' for d in report['diagnostics'])
    if failed or discovery_failed:
        report['status'] = 'partial' if good else 'error'
    calls = [call for item in good for call in item['calls']]
    report['summary'] = {
        'files_discovered': len(report['files']), 'files_analyzed': len(good),
        'files_failed': failed, 'skipped_entries': len(report['skipped']),
        'symbols': sum(len(item['symbols']) for item in good),
        'imports': sum(len(item['imports']) for item in good),
        'calls': len(calls), 'linked_calls': sum(c['target_id'] is not None for c in calls),
        'unresolved_calls': sum(c['target_id'] is None for c in calls),
        'possible_calls': sum(c.get('resolution') == 'POSSIBLE' for c in calls),
        'implicit_calls': sum(bool(c.get('implicit')) for c in calls),
        'candidate_limited_calls': sum(c.get('candidate_limit_reached', False) for c in calls),
        'unknown_target_calls': sum(c['target_id'] is None and c.get('resolution') != 'POSSIBLE' for c in calls),
        'call_categories': summarize_calls(calls)['categories'],
        'local_linked_calls': sum(c['target_id'] is not None and c.get('resolution_kind') == 'local' for c in calls),
        'import_linked_calls': sum(c['target_id'] is not None and c.get('resolution_kind') == 'import' for c in calls),
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', help='Project folder to read; Git is not required yet')
    parser.add_argument('--output', type=Path, help='Save one combined .json report')
    parser.add_argument('--overwrite', action='store_true', help='Replace an existing JSON report')
    parser.add_argument('--exclude-dir', action='append', default=[],
                        help='Additional directory name to skip; can be repeated')
    parser.add_argument('--max-file-bytes', type=int, default=MAX_FILE_BYTES)
    parser.add_argument('--source-root', action='append',
                        help='Import base relative to project, e.g. src. Repeatable; replaces default ".".')
    parser.add_argument('--local-only', action='store_true', help='Disable project imports and method-candidate passes')
    args = parser.parse_args()
    if args.max_file_bytes < 1:
        parser.error('--max-file-bytes must be positive.')
    report = analyze_repository(args.folder, exclude_dirs=args.exclude_dir,
                                max_file_bytes=args.max_file_bytes, source_roots=args.source_root,
                                resolve_imports=not args.local_only)
    if args.output:
        sources = [Path(report['root']) / item['file'] for item in report['files']]
        try:
            write_json_report(report, args.output, overwrite=args.overwrite,
                              protected_paths=sources)
        except (OSError, ValueError) as exc:
            parser.exit(2, f'Cannot write report: {exc}\nUse --overwrite only to replace an existing JSON report.\n')
        summary = report['summary']
        print(f"Repository analysis {report['status']}: {args.output}")
        print(f"Files: {summary['files_analyzed']} analyzed, {summary['files_failed']} failed; "
              f"calls: {summary['linked_calls']} linked, {summary['possible_calls']} possible, "
              f"{summary['unknown_target_calls']} without internal target.")
    else:
        print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report['status'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
