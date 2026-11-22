"""Scope-aware, conservative resolution of bare calls within one Python file."""
import ast
import symtable
from collections import Counter, defaultdict


class Declarations(ast.NodeVisitor):
    def __init__(self):
        self.globals, self.nonlocals = set(), set()

    def visit_Global(self, node):
        self.globals.update(node.names)

    def visit_Nonlocal(self, node):
        self.nonlocals.update(node.names)

    def visit_FunctionDef(self, node):
        pass

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef
    visit_Lambda = visit_FunctionDef


def position(node):
    return node.lineno, node.col_offset, node.end_lineno, node.end_col_offset


class Scope:
    def __init__(self, node, parent, kind):
        self.node = node
        self.parent, self.kind = parent, kind
        self.generic_scope = bool(getattr(node, 'type_params', []))
        self.bindings = defaultdict(list)
        self.wildcard = False
        body = node.body if isinstance(getattr(node, 'body', None), list) else []
        self.direct = set(body)
        declarations = Declarations()
        for statement in body:
            declarations.visit(statement)
        self.globals, self.nonlocals = declarations.globals, declarations.nonlocals


class ScopeIndex(ast.NodeVisitor):
    def __init__(self, tree):
        self.root = Scope(tree, None, 'module')
        self.current = self.root
        self.all_scopes = [self.root]
        self.calls = {}
        self.context = 'module'
        self.nonlocal_writes = []
        self.attribute_writes = []
        self.implicit = []   # operators and protocol uses that Python turns into special-method calls

    def bind(self, name, kind, node):
        record = {'kind': kind, 'node': node}
        if name in self.current.globals:
            self.root.bindings[name].append(record)
        elif name in self.current.nonlocals:
            self.nonlocal_writes.append((self.current, name, record))
        else:
            self.current.bindings[name].append(record)

    def visit_context(self, nodes, context):
        old = self.context
        self.context = context
        try:
            for node in nodes:
                if isinstance(node, ast.AST):
                    self.visit(node)
        finally:
            self.context = old

    def signature(self, args):
        self.visit_context(args.defaults + args.kw_defaults, 'default')
        arguments = args.posonlyargs + args.args + args.kwonlyargs
        arguments += [a for a in (args.vararg, args.kwarg) if a]
        self.visit_context([a.annotation for a in arguments], 'annotation')

    def enter(self, node, kind, body):
        old = self.current
        self.current = Scope(node, old, kind)
        self.all_scopes.append(self.current)
        if kind == 'function':
            args = node.args
            for arg in args.posonlyargs + args.args + args.kwonlyargs + [
                    a for a in (args.vararg, args.kwarg) if a]:
                self.bind(arg.arg, 'parameter', arg)
        try:
            self.visit_context(body, 'function' if kind == 'function' else 'class')
        finally:
            self.current = old

    def visit_FunctionDef(self, node):
        kind = 'function' if node in self.current.direct else 'conditional definition'
        self.bind(node.name, kind, node)
        self.visit_context(node.decorator_list, 'decorator')
        self.visit_context(getattr(node, 'type_params', []), 'type parameter')
        self.signature(node.args)
        self.visit_context([node.returns], 'annotation')
        self.enter(node, 'function', node.body)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.bind(node.name, 'class', node)
        self.visit_context(node.decorator_list + node.bases + node.keywords +
                           getattr(node, 'type_params', []), 'class header')
        self.enter(node, 'class', node.body)

    def visit_Lambda(self, node):
        self.signature(node.args)
        self.enter(node, 'function', [node.body])

    def visit_Call(self, node):
        self.calls[position(node)] = node, self.current, self.context
        self.generic_visit(node)

    def record_implicit(self, node):
        self.implicit.append((node, self.current, self.context))
        self.generic_visit(node)

    visit_BinOp = visit_UnaryOp = visit_Compare = visit_Subscript = record_implicit
    visit_AugAssign = visit_For = visit_AsyncFor = visit_With = visit_AsyncWith = record_implicit
    visit_JoinedStr = record_implicit

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bind(node.id, 'assignment or deletion', node)

    def visit_Attribute(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.attribute_writes.append((node, self.current))
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            self.bind(alias.asname or alias.name.split('.')[0], 'import', node)

    def visit_ImportFrom(self, node):
        for alias in node.names:
            if alias.name == '*':
                self.current.wildcard = True
            else:
                self.bind(alias.asname or alias.name, 'import', node)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.bind(node.name, 'exception variable', node)
        self.generic_visit(node)

    def visit_MatchAs(self, node):
        if node.name:
            self.bind(node.name, 'pattern capture', node)
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node):
        if node.rest:
            self.bind(node.rest, 'pattern capture', node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if node.value is not None or self.current.kind == 'function':
            self.visit(node.target)
        self.visit_context([node.annotation], 'annotation')
        if node.value is not None:
            self.visit(node.value)

    def visit_ListComp(self, node):
        # Comprehension calls stay unresolved; targets must not leak outward.
        # Walrus bindings do affect the containing scope.
        for item in ast.walk(node):
            if isinstance(item, ast.NamedExpr):
                self.visit(item.target)
            if isinstance(item, ast.Call):
                self.calls[position(item)] = item, self.current, 'comprehension'

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def finish_nonlocal_writes(self):
        for scope, name, record in self.nonlocal_writes:
            owner = scope.parent
            while owner and owner.kind != 'module':
                if owner.kind != 'class' and name in owner.bindings:
                    owner.bindings[name].append(record)
                    break
                owner = owner.parent


def lookup(scope, name, root):
    """Search lexical function scopes, skipping class namespaces."""
    if name in scope.globals:
        scope = root
    elif name in scope.nonlocals:
        scope = scope.parent
    while scope:
        if scope.generic_scope:
            return None, scope, 'Generic type-parameter scope lookup is not implemented.'
        if scope.kind == 'class':
            scope = scope.parent
            continue
        if scope.wildcard:
            return None, scope, 'Wildcard import makes this namespace uncertain.'
        if name in scope.bindings:
            return scope.bindings[name], scope, None
        if name in scope.globals and scope is not root:
            scope = root
        else:
            scope = scope.parent
    return None, None, 'No definition found in the visible function or module scopes.'


def resolve_local_calls(tree, source, report):
    symtable.symtable(source, report['file'], 'exec')
    index = ScopeIndex(tree)
    index.visit(tree)
    index.finish_nonlocal_writes()
    positions = Counter(position(node) for node in ast.walk(tree) if isinstance(node, ast.Call))
    symbols = {(s['span']['line'], s['span']['column']): s for s in report['symbols']}
    report['edges'] = [e for e in report['edges'] if e['kind'] != 'CALLS']
    for call in report['calls']:
        call.update(target_id=None, resolution='UNRESOLVED', resolution_kind=None)
        span = call['span']
        key = (span['line'], span['column'], span['end_line'], span['end_column'])
        if positions[key] > 1:
            call['reason'] = 'Multiple calls share this source span; target left unresolved.'
            continue
        node, scope, context = index.calls[(span['line'], span['column'],
                                           span['end_line'], span['end_column'])]
        reason, target = None, None
        if context != 'function':
            reason = f'Calls in {context} context are not resolved in this pass.'
        elif not isinstance(node.func, ast.Name):
            reason = 'Attribute calls and computed targets need later resolution.'
        else:
            name = node.func.id
            bindings, owner, reason = lookup(scope, name, index.root)
            if bindings:
                if len(bindings) != 1:
                    reason = f"'{name}' has multiple definitions or reassignments in its visible scope."
                else:
                    binding = bindings[0]
                    definition = binding['node']
                    if binding['kind'] != 'function':
                        reason = f"'{name}' refers to a {binding['kind']}; its callable target is unknown."
                    elif owner is index.root and definition not in owner.direct:
                        reason = 'This global definition is installed from another scope.'
                    elif definition.decorator_list:
                        reason = f"'{name}' has decorators that may replace the function."
                    elif getattr(definition, 'type_params', []):
                        reason = 'Generic function scope handling is not implemented.'
                    elif owner is scope and (definition.lineno, definition.col_offset) > (node.lineno, node.col_offset):
                        reason = f"Local function '{name}' is defined after this call."
                    else:
                        target = symbols[(definition.lineno, definition.col_offset)]['id']
                        reason = f"'{name}' resolves to one undecorated function in its visible scope."
        call['reason'] = reason
        if target:
            call.update(target_id=target, resolution='LIKELY', resolution_kind='local')
            report['edges'].append({
                'kind': 'CALLS', 'source': call['enclosing_symbol_id'], 'target': target,
                'call_id': call['id'], 'resolution': 'LIKELY',
                'evidence': {'file': report['file'], **span}, 'reason': reason,
            })
    report['resolution_summary'] = {
        'total_calls': len(report['calls']),
        'linked_calls': sum(c['target_id'] is not None for c in report['calls']),
        'unresolved_calls': sum(c['target_id'] is None for c in report['calls']),
    }
