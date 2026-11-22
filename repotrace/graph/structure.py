"""The shape of a whole repository: boxes (classes and files of functions) and how they relate.

Built only from a saved map (the repository report), so it needs no source files and runs nothing.

Boxes ("entities"):  a class, or a module (a file's plain functions).
Relations:           inherits  (this class is a kind of that one)
                     holds     (this class creates one of that kind and keeps it in a field)
                     uses      (code in this box calls code in that one; graded sure / maybe)

A relation is only drawn when the code names its target through something RepoTrace can follow: a class
defined in the same file, or a name that an import in that file brings in. A name that is imported from
outside the project, or not imported at all, is never matched to a same-named class elsewhere.
"""
import ast
import re
import textwrap
from collections import defaultdict
from pathlib import PurePosixPath

from repotrace.graph.graph_explore import is_test_file

MAX_FOLDERS = 40
MAX_FIELDS = 40           # fields listed per class; the rest are counted
CLASS_REASONS = {'UNSUPPORTED_TARGET', 'CLASS_CONSTRUCTION', 'OBJECT_ATTRIBUTE'}   # calls that name a class
NAME_ONLY = re.compile(r'^[A-Za-z_][A-Za-z_0-9.]*$')
MAX_HOPS = 3              # how many `from .x import Name` re-exports are followed


def _first_line(text):
    """The first sentence-ish line of a docstring, short enough for a subtitle."""
    line = next((l.strip() for l in (text or '').strip().splitlines() if l.strip()), '')
    return line if len(line) <= 140 else line[:137] + '...'


def folder_of(file, depth=None):
    parts = list(PurePosixPath(file).parent.parts)
    parts = parts[:depth] if depth else parts
    return '/'.join(parts) or '.'


def _class_node(source):
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return None
    return next((n for n in tree.body if isinstance(n, ast.ClassDef)), None)


def _own_nodes(cls):
    """Everything inside the class body that belongs to this class: nested classes are not entered."""
    stack = list(reversed(cls.body))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, ast.ClassDef):
            stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _self_attribute(target):
    return target.attr if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self' else None


def _fields_and_holds(class_source):
    """([field names in order of first appearance], {field: the constructor expression assigned to it})."""
    cls = _class_node(class_source)
    if cls is None:
        return [], {}
    fields, held = [], {}

    def add(name):
        if name and not (name.startswith('__') and name.endswith('__')) and name not in fields:
            fields.append(name)

    for node in _own_nodes(cls):
        targets, value = [], None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets, value = [node.target], getattr(node, 'value', None)
        for t in targets:
            for part in (t.elts if isinstance(t, ast.Tuple) else [t]):
                name = _self_attribute(part)
                add(name)
                if name and isinstance(value, ast.Call) and not isinstance(node, ast.AugAssign):
                    held.setdefault(name, ast.unparse(value.func))
    for node in cls.body:                                    # declared in the class body: `name = ...` or `name: type`
        if isinstance(node, ast.Assign):
            for t in node.targets:
                add(t.id if isinstance(t, ast.Name) else None)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            add(node.target.id)
    return fields, held


class _Resolver:
    """Find the project class that a written name refers to, through the same file's definitions and its imports."""

    def __init__(self, files):
        self.by_file = defaultdict(dict)                      # file -> {class name: class id} (top-level classes)
        self.module_file = {}                                 # dotted module name -> file
        self.imports = {}                                     # file -> {local name: (module, original name or None)}
        for f in files:
            for m in f.get('module_names', []):
                self.module_file[m] = f['file']
            for s in f['symbols']:
                if s['kind'] == 'CLASS' and s['parent_id'].endswith('::module'):
                    self.by_file[f['file']][s['name']] = s['id']
        for f in files:
            local = {}
            for imp in f.get('imports', []):
                module = imp.get('module') or ''
                if imp.get('level'):
                    base = (f.get('module_names') or [''])[0].split('.')
                    package = base if f['file'].endswith('__init__.py') else base[:-1]
                    package = package[:len(package) - (imp['level'] - 1)] if imp['level'] > 1 else package
                    module = '.'.join(package + ([module] if module else []))
                for n in imp.get('names', []):
                    if imp['kind'] == 'ImportFrom':
                        local[n.get('alias') or n['name']] = (module, n['name'])
                    else:
                        local[n.get('alias') or n['name'].split('.')[0]] = (n['name'], None)
            self.imports[f['file']] = local

    def _in_file(self, file, name, hops=0):
        """The class `name` in `file`: defined there, or re-exported from another file by an import."""
        if file is None:
            return None
        if name in self.by_file[file]:
            return self.by_file[file][name]
        if hops < MAX_HOPS and name in self.imports.get(file, {}):
            module, original = self.imports[file][name]
            if original is not None:
                return self._in_file(self.module_file.get(module), original, hops + 1)
        return None

    def resolve(self, file, written):
        """The class id, or None when the name is not bound to a project class by this file's own code."""
        written = written.split('[')[0].strip()
        head, _, tail = written.partition('.')
        local, here = self.imports.get(file, {}), self.by_file[file]
        if head in local and head in here:
            return None                                       # defined and imported under one name: ambiguous, so not guessed
        if head in local:
            module, original = local[head]
            if original is None:                              # import pkg / import pkg.mod as m
                package = module + ('.' + tail.rsplit('.', 1)[0] if '.' in tail else '')
                return self._in_file(self.module_file.get(package), tail.rsplit('.', 1)[-1]) if tail else None
            if tail:                                          # from pkg import mod   ->  mod.Class
                return self._in_file(self.module_file.get(module + '.' + original), tail.rsplit('.', 1)[-1]) if '.' not in tail else None
            return self._in_file(self.module_file.get(module), original)
        return here.get(head) if not tail else None

    def resolve_prefix(self, file, expression):
        """`Item`, `Item.create` and `real.Item.create` all name the class Item: try the longest prefix first."""
        parts = expression.split('.')
        for k in range(len(parts), 0, -1):
            found = self.resolve(file, '.'.join(parts[:k]))
            if found:
                return found
        return None


def _enclosing_function_chain_is_module_level(symbol, symbols):
    """True for a function nested only in other functions up to its module (not inside a class)."""
    parent = symbols.get(symbol['parent_id'])
    while parent is not None:
        if parent['kind'] == 'CLASS' or parent['kind'] == 'METHOD':
            return False
        parent = symbols.get(parent['parent_id'])
    return True


def build_structure(report):
    all_files = report['files']
    files = [f for f in all_files if f['status'] == 'ok']
    symbols = {s['id']: s for f in files for s in f['symbols']}
    resolver = _Resolver(files)
    entity_of = {}                                           # symbol id -> id of the box that contains it

    def box_for(symbol_id):
        if symbol_id in entity_of:
            return entity_of[symbol_id]
        s, trail, found = symbols.get(symbol_id), [], None
        while s is not None and found is None:
            trail.append(s['id'])
            if s['kind'] == 'CLASS':
                found = s['id']
            elif s['parent_id'].endswith('::module'):
                found = 'module:' + s['file']
            else:
                s = symbols.get(s['parent_id'])
        for t in trail:
            entity_of[t] = found
        return found

    entities, methods, functions = {}, defaultdict(list), defaultdict(list)
    for s in symbols.values():
        item = {'name': s['name'], 'id': s['id'], 'line': s['span']['line']}
        if s['kind'] == 'METHOD' and symbols.get(s['parent_id'], {}).get('kind') == 'CLASS':
            methods[s['parent_id']].append(item)
        elif s['kind'] == 'FUNCTION' and s['parent_id'].endswith('::module'):
            functions[s['file']].append(item)
        elif s['kind'] == 'NESTED_FUNCTION' and _enclosing_function_chain_is_module_level(s, symbols):
            functions[s['file']].append({**item, 'name': s['qualified_name']})
    held_by_class = {}
    for s in symbols.values():
        if s['kind'] != 'CLASS':
            continue
        ms = sorted(methods[s['id']], key=lambda m: m['line'])
        fields, held = _fields_and_holds(s.get('source') or '')
        held_by_class[s['id']] = held
        entities[s['id']] = {
            'id': s['id'], 'kind': 'class', 'name': s['qualified_name'], 'file': s['file'], 'line': s['span']['line'],
            'test': is_test_file(s['file']), 'bases': list(s.get('bases') or []), 'base_ids': [],
            'fields': fields[:MAX_FIELDS], 'field_count': len(fields), 'doc': _first_line(s.get('docstring')),
            'methods': ms, 'method_count': len(ms), 'decorators': list(s.get('decorators') or [])}
    for file, fns in functions.items():
        fns.sort(key=lambda m: m['line'])
        stem = PurePosixPath(file)
        entities['module:' + file] = {
            'id': 'module:' + file, 'kind': 'module', 'name': stem.stem if stem.name != '__init__.py' else stem.parent.name or file,
            'file': file, 'line': 1, 'test': is_test_file(file), 'bases': [], 'base_ids': [], 'fields': [], 'field_count': 0, 'doc': '',
            'methods': fns, 'method_count': len(fns), 'decorators': []}

    edges = {}                                               # (kind, source, target) -> counts

    def edge(kind, a, b, sure=0, maybe=0):
        if a and b and a != b and a in entities and b in entities:
            e = edges.setdefault((kind, a, b), {'kind': kind, 'source': a, 'target': b, 'sure': 0, 'maybe': 0})
            e['sure'] += sure
            e['maybe'] += maybe

    for e in list(entities.values()):
        if e['kind'] != 'class':
            continue
        for written in e['bases']:
            base = resolver.resolve(e['file'], written)
            if base and base != e['id']:
                e['base_ids'].append(base)
                edge('inherits', e['id'], base, sure=1)
        for expression in held_by_class[e['id']].values():
            edge('holds', e['id'], resolver.resolve_prefix(e['file'], expression), sure=1)
    for f in files:
        for call in f['calls']:
            src = box_for(call.get('enclosing_symbol_id'))
            if call.get('target_id'):
                edge('uses', src, box_for(call['target_id']), sure=1)
            elif call.get('candidate_targets'):
                for t in {box_for(c['target_id']) for c in call['candidate_targets']}:
                    edge('uses', src, t, maybe=1)
            elif call.get('reason_code') in CLASS_REASONS and isinstance(call.get('expression'), str) and NAME_ONLY.match(call['expression']):
                # `Item(...)` or `Item.create(...)`: the class is named right there. Like every constructor call it is graded
                # "maybe", because Python lets a class return something else.
                edge('uses', src, resolver.resolve_prefix(call['file'], call['expression']), maybe=1)

    # Folders: the directory of each file, merged upwards if there would be too many boxes.
    files_by_folder = defaultdict(list)
    depth = None
    names = [f['file'] for f in all_files]
    while len({folder_of(x, depth) for x in names}) > MAX_FOLDERS and (depth or 99) > 1:
        depth = max(1, (depth or max(len(PurePosixPath(x).parent.parts) for x in names)) - 1)
    for e in entities.values():
        e['folder'] = folder_of(e['file'], depth)
    for x in names:
        files_by_folder[folder_of(x, depth)].append(x)
    folders = {}
    for name, members in files_by_folder.items():
        inside = [e for e in entities.values() if e['folder'] == name]
        folders[name] = {'id': name, 'name': name if name != '.' else '(top of the project)', 'files': len(members),
                         'classes': sum(1 for e in inside if e['kind'] == 'class'),
                         'functions': sum(e['method_count'] for e in inside if e['kind'] == 'module'),
                         'test': all(is_test_file(m) for m in members)}
    folder_edges = {}
    for (kind, a, b), e in edges.items():
        fa, fb = entities[a]['folder'], entities[b]['folder']
        if fa != fb:
            fe = folder_edges.setdefault((fa, fb), {'source': fa, 'target': fb, 'uses': 0, 'inherits': 0, 'holds': 0})
            fe['uses' if kind == 'uses' else kind] += e['sure'] + e['maybe']
    inventory = [{'file': f['file'], 'folder': folder_of(f['file'], depth), 'test': is_test_file(f['file']), 'lines': f.get('line_count', 0)}
                 for f in sorted(files, key=lambda f: f['file'])]
    unreadable = [{'file': f['file'], 'folder': folder_of(f['file'], depth), 'test': is_test_file(f['file']),
                   'message': ((f.get('diagnostics') or [{}])[0].get('message') or 'This file could not be read.')}
                  for f in sorted(all_files, key=lambda f: f['file']) if f['status'] != 'ok']
    return {'files': inventory, 'unreadable': unreadable,
            'folders': sorted(folders.values(), key=lambda x: x['id']),
            'folder_edges': sorted(folder_edges.values(), key=lambda x: (x['source'], x['target'])),
            'entities': sorted(entities.values(), key=lambda x: (x['folder'], x['file'], x['line'])),
            'edges': sorted(edges.values(), key=lambda x: (x['kind'], x['source'], x['target']))}
