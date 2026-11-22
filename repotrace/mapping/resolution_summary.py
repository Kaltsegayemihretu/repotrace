"""Explain coverage without treating external references or guesses as resolved edges."""
import ast
import builtins
import importlib.util
import re
import sysconfig
from collections import Counter

from repotrace.mapping.library_objects import LibraryObjects, attach_field_kinds
from repotrace.reading.resolver import lookup


_OUTSIDE_FOLDERS = None
_installed_cache = {}


def _outside_folders():
    global _OUTSIDE_FOLDERS
    if _OUTSIDE_FOLDERS is None:
        paths = sysconfig.get_paths()
        _OUTSIDE_FOLDERS = tuple(p for p in {paths.get(k) for k in ('stdlib', 'platstdlib', 'purelib', 'platlib')} if p)
    return _OUTSIDE_FOLDERS


def confirmed_outside(top):
    """True when `top` is Python's own or an installed library: found in the standard library or site-packages.

    Only the top-level name is looked up, so no module is imported. Anything found elsewhere (such as next to the
    analyzer) is not confirmed, because it could be the project's own code under a different source root."""
    if not top:
        return False
    if top not in _installed_cache:
        try:
            spec = importlib.util.find_spec(top)
        except (ImportError, ValueError, AttributeError):
            spec = None
        places = [spec.origin] if spec and spec.origin else []
        places += list(getattr(spec, 'submodule_search_locations', None) or []) if spec else []
        _installed_cache[top] = bool(spec) and (
            any(p in ('built-in', 'frozen') for p in places)
            or any(str(p).startswith(_outside_folders()) or 'site-packages' in str(p) or 'dist-packages' in str(p) for p in places))
    return _installed_cache[top]


def _import_tops(report):
    """{name used in this file: top-level module it came from, or None for a relative import}."""
    tops = {}
    for imp in report.get('imports', []):
        for n in imp.get('names', []):
            if imp['kind'] == 'Import':
                tops[n.get('alias') or n['name'].split('.')[0]] = n['name'].split('.')[0]
            else:
                tops[n.get('alias') or n['name']] = None if imp.get('level') else (imp.get('module') or '').split('.')[0]
    return tops


def summarize_calls(calls):
    categories = Counter(c.get('category', 'unresolved') for c in calls)
    linked = sum(c.get('target_id') is not None for c in calls)
    possible = sum(c.get('resolution') == 'POSSIBLE' for c in calls)
    return {
        'total_calls': len(calls), 'linked_calls': linked,
        # Backward-compatible meaning: no single LIKELY internal target.
        'unresolved_calls': len(calls) - linked,
        'possible_calls': possible, 'unknown_target_calls': len(calls) - linked - possible,
        'categories': dict(sorted(categories.items())),
    }


def classify_project_calls(index):
    """Attach explanations only. Never creates a target or executes target code."""
    objects = {path: LibraryObjects(unit) for path, unit in index.units.items()}
    attach_field_kinds(objects)
    for unit in index.units.values():
        report = unit['report']
        for call in report['calls']:
            call.pop('external_reference', None)
            if call['target_id'] is not None:
                call['category'] = 'internal_linked'
                continue
            if call.get('candidate_targets'):
                call['category'] = 'internal_possible'
                continue
            s = call['span']
            key = (s['line'], s['column'], s['end_line'], s['end_column'])
            entry = unit['index'].calls.get(key)
            if not entry:
                call['category'] = 'unresolved'
                continue
            node, scope, context = entry
            if isinstance(node.func, ast.Name):
                name = node.func.id
                bindings, owner, _ = lookup(scope, name, unit['index'].root)
                if (bindings is None and owner is None and '__builtins__' not in unit['index'].root.bindings
                        and callable(vars(builtins).get(name))):
                    call['category'] = 'builtin_reference'
                    call['external_reference'] = {
                        'qualified_name': 'builtins.' + name,
                        'basis': 'Unshadowed builtin name in the analyzed scopes; runtime namespace mutation is not modeled.'}
                    continue
            elif isinstance(node.func, ast.Attribute) and context in {'function', 'module'}:
                found = objects[unit['report']['file']].method_call(node, scope)
                if found:
                    call['category'] = 'builtin_object_method'
                    call['external_reference'] = {'qualified_name': found[0], 'basis': found[1]}
                    continue
            if call.get('reason_code') in {'MODULE_NOT_FOUND', 'BUILTIN_MODULE', 'FROZEN_MODULE'}:
                head = re.match(r'[A-Za-z_]\w*', call['expression']) if isinstance(call.get('expression'), str) else None
                top = unit.setdefault('import_tops', _import_tops(report)).get(head.group(0)) if head else None
                if call['reason_code'] != 'MODULE_NOT_FOUND' or confirmed_outside(top):
                    call['category'] = 'external_or_missing'              # Python's own or an installed library
                    call['external_reference'] = {'qualified_name': top or '', 'basis': 'Found in the standard library or an installed package.'}
                else:
                    call['category'] = 'import_not_found'                 # a library not installed here, or the project's own code in another folder
            elif context != 'function':
                call['category'] = 'unsupported_context'
            else:
                call['category'] = 'unresolved'
        report['resolution_summary'] = summarize_calls(report['calls'])
