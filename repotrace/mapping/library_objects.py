"""Recognize method calls on Python's own objects: `seen.append(x)`, `', '.join(parts)`.

Label only. This never creates a link and never runs the analyzed code. A call is named
only when the receiver is provably a plain built-in value: a literal, an unshadowed
constructor such as `list()`, or a name whose every assignment is one of those.
Anything less certain is left alone, so "could not trace" keeps meaning "really unclear".
"""
import ast
import builtins

from repotrace.reading.resolver import lookup

TYPES = {'str': str, 'bytes': bytes, 'bytearray': bytearray, 'list': list, 'dict': dict,
         'set': set, 'frozenset': frozenset, 'tuple': tuple}
FILE = 'file'   # what open() returns; method names are checked against a text file
STR_TO_STR = {'strip', 'lstrip', 'rstrip', 'lower', 'upper', 'title', 'capitalize', 'casefold',
              'replace', 'format', 'join', 'center', 'ljust', 'rjust', 'zfill'}
STR_TO_LIST = {'split', 'rsplit', 'splitlines'}
MAX_DEPTH = 4


def _file_methods():
    import io
    return io.TextIOWrapper


def _has_method(kind, name):
    owner = _file_methods() if kind == FILE else TYPES[kind]
    return callable(getattr(owner, name, None))


class LibraryObjects:
    def __init__(self, unit):
        self.unit = unit
        self.root = unit['index'].root
        self.fields = None   # attribute name -> set of kinds, shared across the project
        self.parents = {}
        for parent in ast.walk(self.root.node):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent

    def _unshadowed(self, name, scope):
        bindings, owner, _ = lookup(scope, name, self.root)
        return (bindings is None and owner is None and '__builtins__' not in self.root.bindings
                and callable(vars(builtins).get(name)))

    def kind_of(self, expr, scope, depth=0, seen=()):
        """Built-in type name of an expression, or None when it is not certain."""
        if depth > MAX_DEPTH:
            return None
        if isinstance(expr, ast.Constant):
            return {str: 'str', bytes: 'bytes'}.get(type(expr.value))
        if isinstance(expr, ast.JoinedStr):
            return 'str'
        if isinstance(expr, (ast.List, ast.ListComp)):
            return 'list'
        if isinstance(expr, (ast.Dict, ast.DictComp)):
            return 'dict'
        if isinstance(expr, (ast.Set, ast.SetComp)):
            return 'set'
        if isinstance(expr, ast.Tuple):
            return 'tuple'
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Mod):
            return 'str' if self.kind_of(expr.left, scope, depth + 1, seen) == 'str' else None
        if isinstance(expr, ast.Call):
            func = expr.func
            if isinstance(func, ast.Name):
                if func.id in TYPES and self._unshadowed(func.id, scope):
                    return func.id
                if func.id == 'open' and self._unshadowed('open', scope):
                    return FILE
            elif isinstance(func, ast.Attribute) and self.kind_of(func.value, scope, depth + 1, seen) == 'str':
                if func.attr in STR_TO_STR:
                    return 'str'
                if func.attr in STR_TO_LIST:
                    return 'list'
            return None
        if isinstance(expr, ast.Name):
            return self._name_kind(expr, scope, depth, seen)
        return None

    def _name_kind(self, name, scope, depth, seen):
        bindings, owner, reason = lookup(scope, name.id, self.root)
        if reason or not bindings or (name.id, id(owner)) in seen:
            return None
        kinds = set()
        for binding in bindings:
            if binding['kind'] != 'assignment or deletion':
                return None
            kinds.add(self.write_kind(binding['node'], owner, depth + 1, seen + ((name.id, id(owner)),)))
            if None in kinds:
                return None
        return kinds.pop() if len(kinds) == 1 else None

    def write_kind(self, target, scope, depth=0, seen=()):
        """Built-in type assigned to a Name/Attribute store target, or None for anything else."""
        if not isinstance(getattr(target, 'ctx', None), ast.Store):
            return None
        statement = self.parents.get(target)
        if isinstance(statement, ast.Assign) and target in statement.targets:
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.target is target and statement.value is not None:
            value = statement.value
        else:
            return None
        return self.kind_of(value, scope, depth, seen)

    def field_kind(self, expr, scope):
        """Built-in type of `self.<field>` when every write to that field name is the same built-in value."""
        if (self.fields is None or not isinstance(expr, ast.Attribute) or not isinstance(expr.value, ast.Name)
                or expr.value.id != 'self'):
            return None
        bindings, _, reason = lookup(scope, 'self', self.root)
        if reason or not bindings or any(b['kind'] != 'parameter' for b in bindings):
            return None
        kinds = self.fields.get(expr.attr)
        return next(iter(kinds)) if kinds and len(kinds) == 1 and None not in kinds else None

    def method_call(self, call, scope):
        """('builtins.list.append', basis) when `call` is a method call on a certain built-in value."""
        func = call.func
        if not isinstance(func, ast.Attribute):
            return None
        kind = self.kind_of(func.value, scope) or self.field_kind(func.value, scope)
        if kind is None or not _has_method(kind, func.attr):
            return None
        qualified = 'io.TextIOWrapper.' + func.attr if kind == FILE else f'builtins.{kind}.{func.attr}'
        return qualified, (f'The receiver is always a built-in {"file" if kind == FILE else kind} '
                           'value in the analyzed scopes; runtime namespace mutation is not modeled.')


def attach_field_kinds(objects):
    """Give every file's helper the project-wide map: attribute name -> kinds of everything assigned to it.

    One unclear write anywhere (a method of that name, a loop target, an augmented assignment, an
    unknown value) puts None in the set, which blocks the label for every class.
    """
    fields = {}

    def note(name, kind):
        fields.setdefault(name, set()).add(kind)

    for helper in objects.values():
        index = helper.unit['index']
        for node, scope in index.attribute_writes:
            note(node.attr, helper.write_kind(node, scope))
        for scope in index.all_scopes:
            if scope.kind == 'class':
                for name, bindings in scope.bindings.items():
                    for binding in bindings:
                        node = binding['node']
                        note(name, helper.write_kind(node, scope) if binding['kind'] == 'assignment or deletion' else None)
        for call, _, _ in index.calls.values():
            if isinstance(call.func, ast.Name) and call.func.id in {'setattr', 'delattr'} and len(call.args) >= 2:
                name = call.args[1]
                if isinstance(name, ast.Constant) and isinstance(name.value, str):
                    note(name.value, None)
    for helper in objects.values():
        helper.fields = fields
