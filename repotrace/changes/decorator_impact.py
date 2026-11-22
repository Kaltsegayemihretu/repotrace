"""Which functions does a decorator wrap? A changed decorator changes everything it wraps.

Calls to a decorated function run the decorator's wrapper first, so callers of the wrapped
function are affected when the decorator (or its inner wrapper) changes. This is a
deterministic lookup over decorator names that the project resolver can follow; it does not
guess at decorators it cannot resolve.
"""
import ast
from collections import defaultdict


def decorator_bases(report):
    """Last name segments used in decorator expressions, for a cheap pre-check."""
    names = set()
    for file in report['files']:
        if file['status'] != 'ok':
            continue
        for symbol in file['symbols']:
            for text in symbol.get('decorators') or []:
                names.add(text.split('(')[0].rsplit('.', 1)[-1].strip())
    return names


def _tolerant_index(report):
    """A project index that may name a decorated function as a target (the main analyzer refuses)."""
    from repotrace.mapping.project_resolver import ProjectIndex, Unresolved

    class TolerantIndex(ProjectIndex):
        def _definition_value(self, unit, binding, owner, module_name=None):
            node = binding['node']
            if binding['kind'] not in {'function', 'class'}:
                raise Unresolved('EXPORT_NOT_FUNCTION', 'This binding is not a supported function or class definition.')
            if node not in owner.direct:
                raise Unresolved('CONDITIONAL_EXPORT', 'The target is not an unconditional definition in this scope.')
            return {'kind': binding['kind'], 'module': module_name, 'file': unit['report']['file'],
                    'node': node, 'owner_scope': owner,
                    'symbol': unit['symbols'][(node.lineno, node.col_offset)],
                    'import_chain': [], 'binding_path': []}
    index = TolerantIndex(report['files'], report['settings']['source_roots'])
    index.mutations()
    return index


def callers_of_decorated(report, index=None):
    """{decorated function id: [(caller id, call record)]} for calls the analyzer refused to link."""
    from repotrace.mapping.project_resolver import Unresolved
    index = index or _tolerant_index(report)
    result = defaultdict(list)
    for unit in index.units.values():
        for call in unit['report']['calls']:
            if call.get('reason_code') != 'UNSUPPORTED_TARGET' or call.get('target_id') is not None:
                continue
            span = call['span']
            entry = unit['index'].calls.get((span['line'], span['column'], span['end_line'], span['end_column']))
            if entry is None or unit['positions'][(span['line'], span['column'], span['end_line'], span['end_column'])] != 1:
                continue
            node, scope, _ = entry
            try:
                ref = index.reference_value(unit, node.func, scope, node)
            except Unresolved:
                continue
            if ref['kind'] == 'function':
                result[ref['symbol']['id']].append((call['enclosing_symbol_id'], call))
    return dict(result)


def decorated_by(report, index=None):
    """{decorator symbol id: [decorated symbol ids]} and a count of decorators not followed."""
    from repotrace.mapping.project_resolver import Unresolved
    index = index or _tolerant_index(report)
    result, unresolved = defaultdict(list), 0
    for unit in index.units.values():
        tree = ast.parse(unit['report']['source'])
        root = unit['index'].root

        def visit(body):
            nonlocal unresolved
            for node in body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                symbol = unit['symbols'].get((node.lineno, node.col_offset))
                for decorator in node.decorator_list:
                    target = decorator.func if isinstance(decorator, ast.Call) else decorator
                    try:
                        ref = index.reference_value(unit, target, root, node, enforce_order=False)
                    except Unresolved:
                        unresolved += 1
                        continue
                    if symbol is not None and ref['kind'] in {'function', 'class'}:
                        result[ref['symbol']['id']].append(symbol['id'])
                if isinstance(node, ast.ClassDef):
                    visit(node.body)
        visit(tree.body)
    return dict(result), unresolved


def decorators_of_change(symbols, decorated, file, qualified_name):
    """Decorator symbols the changed function belongs to: itself or an enclosing definition."""
    found = []
    for decorator_id, wrapped in decorated.items():
        symbol = symbols.get(decorator_id)
        if symbol is None or symbol['file'] != file:
            continue
        base = symbol['qualified_name']
        if qualified_name == base or qualified_name.startswith(base + '.'):
            found.append((symbol, wrapped))
    return found
