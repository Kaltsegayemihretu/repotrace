"""Weak evidence: unresolved calls whose final name equals a changed function's name.

These sites are NOT call links. The analyzer could not tell what they call, and a shared
name proves nothing about the target. They are listed separately so a developer can
look at places the structural graph could not explain.
"""
import re

FUNCTION_KINDS = ('FUNCTION', 'METHOD', 'NESTED_FUNCTION')
# Names that mostly belong to builtin/library objects (dict.get, list.append, cursor.execute ...).
COMMON_NAMES = frozenset({
    'get', 'set', 'add', 'append', 'extend', 'insert', 'remove', 'pop', 'clear', 'copy', 'update',
    'items', 'keys', 'values', 'index', 'count', 'sort', 'reverse', 'join', 'split', 'strip',
    'lstrip', 'rstrip', 'format', 'replace', 'find', 'lower', 'upper', 'startswith', 'endswith',
    'encode', 'decode', 'read', 'write', 'readline', 'close', 'open', 'flush', 'execute',
    'executemany', 'executescript', 'fetchone', 'fetchall', 'commit', 'rollback', 'cursor',
    'info', 'debug', 'warning', 'error', 'setdefault'})
NOISY_SITE_COUNT = 50
_LAST_NAME = re.compile(r'([A-Za-z_]\w*)\s*$')


def _is_test_path(path):
    parts = path.replace('\\', '/').split('/')
    return any(p in {'tests', 'test'} or p.startswith('test_') or p.endswith('_test.py') for p in parts)


def find_name_matches(report, qualified_name, kind, *, covered_callers=(), limit=20, deleted=False):
    """Name-only matches in one analyzed snapshot report for a (possibly deleted) function.

    covered_callers: (file, qualified_name) pairs already explained by graph links.
    deleted: also count calls classed external_or_missing; a call to a removed project
    function lands in that bucket, which is exactly the dangling reference to find.
    """
    name = qualified_name.rsplit('.', 1)[-1]
    result = {'name': name, 'qualified_name': qualified_name, 'strength': 'name_only',
              'status': 'ok', 'total': 0, 'shown': 0, 'truncated': False, 'noisy_name': False,
              'same_name_definitions': 0, 'sites': []}
    if kind not in FUNCTION_KINDS or kind == 'NESTED_FUNCTION':
        result['status'] = 'skipped_local_function'
        return result
    if name.startswith('__') and name.endswith('__'):
        result['status'] = 'skipped_special_method'
        return result
    covered, owners, definitions = set(covered_callers), {}, 0
    for file in report['files']:
        if file['status'] != 'ok':
            continue
        for symbol in file['symbols']:
            owners[symbol['id']] = symbol
            if symbol['kind'] in FUNCTION_KINDS and symbol['qualified_name'].rsplit('.', 1)[-1] == name \
                    and symbol['qualified_name'] != qualified_name:
                definitions += 1
    result['same_name_definitions'] = definitions
    method = kind == 'METHOD'
    categories = {'unresolved', 'unsupported_context'} | ({'external_or_missing', 'import_not_found'} if deleted else set())
    sites = []
    for file in report['files']:
        if file['status'] != 'ok':
            continue
        for call in file['calls']:
            expression = call.get('expression')
            if (call.get('resolution') != 'UNRESOLVED' or not isinstance(expression, str)
                    or call.get('category') not in categories):
                continue
            last = _LAST_NAME.search(expression)
            if not last or last.group(1) != name:
                continue
            attribute = expression.strip() != name
            if method and not attribute:
                continue
            caller = call.get('enclosing_symbol_id')
            symbol = owners.get(caller, {})
            if (symbol.get('file'), symbol.get('qualified_name')) in covered:
                continue
            sites.append({
                'file': call['file'], 'line': call['span']['line'], 'expression': expression,
                'caller': symbol.get('qualified_name', '<module>'), 'caller_id': caller,
                'call_id': call['id'], 'is_test': _is_test_path(call['file'])})
    sites.sort(key=lambda s: (s['is_test'], s['file'], s['line']))
    result['total'] = len(sites)
    result['noisy_name'] = name in COMMON_NAMES or len(sites) > NOISY_SITE_COUNT
    result['sites'] = sites[:limit]
    result['shown'] = len(result['sites'])
    result['truncated'] = len(sites) > limit
    return result
