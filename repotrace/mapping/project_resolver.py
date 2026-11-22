"""Conservative explicit-import resolution. Never imports or executes target code."""
import ast
import sys
from collections import Counter, defaultdict
from importlib.machinery import FrozenImporter
from pathlib import PurePosixPath

from repotrace.reading.resolver import ScopeIndex, lookup, position


class Unresolved(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def span(node):
    return dict(zip(('line', 'column', 'end_line', 'end_column'), position(node)))


class ProjectIndex:
    def __init__(self, files, roots, max_reexport_depth=32):
        # Bound each lookup, not the whole repository. A pathological import
        # chain must not consume Python's recursion limit or hang a scan.
        if max_reexport_depth < 1:
            raise ValueError('max_reexport_depth must be positive')
        self.max_reexport_depth = max_reexport_depth
        self.attribute_mutations = defaultdict(list)
        self.modules = defaultdict(list)
        self.namespaces = set()
        self.names = defaultdict(set)
        self.units = {}
        for report in files:
            path = PurePosixPath(report['file'])
            for root in roots:
                try:
                    relative = path.relative_to(PurePosixPath(root))
                except ValueError:
                    continue
                parts = list(relative.with_suffix('').parts)
                package = parts[-1] == '__init__'
                if package:
                    parts.pop()
                if not parts or not all(p.isidentifier() for p in parts):
                    continue
                name = '.'.join(parts)
                self.modules[name].append({
                    'file': report['file'], 'package': package, 'status': report['status']})
                self.names[report['file']].add(name)
                self.namespaces.update('.'.join(parts[:i]) for i in range(1, len(parts)))
            if report['status'] != 'ok':
                continue
            tree = ast.parse(report['source'], filename=report['file'], type_comments=True)
            index = ScopeIndex(tree)
            index.visit(tree)
            index.finish_nonlocal_writes()
            self.units[report['file']] = {
                'report': report, 'index': index,
                'positions': Counter(position(n) for n in ast.walk(tree) if isinstance(n, ast.Call)),
                'symbols': {(s['span']['line'], s['span']['column']): s for s in report['symbols']},
            }

    def module(self, name):
        top = name.split('.')[0]
        if top in sys.builtin_module_names:
            raise Unresolved('BUILTIN_MODULE', 'A project file cannot establish this built-in module target.')
        # Query the interpreter's built-in frozen finder only; never run path hooks
        # or import a project package to discover it.
        if FrozenImporter.find_spec(top) is not None:
            raise Unresolved('FROZEN_MODULE', 'This interpreter provides a frozen module before project source lookup.')
        parts = name.split('.')
        parent_directory = None
        result = None
        for end in range(1, len(parts) + 1):
            current = '.'.join(parts[:end])
            entries = self.modules.get(current, [])
            if len(entries) > 1:
                raise Unresolved('AMBIGUOUS_MODULE', f"Multiple files provide '{current}'.")
            if entries:
                result = entries[0]
                if result['status'] != 'ok':
                    raise Unresolved('MODULE_ANALYSIS_FAILED', f"'{current}' could not be analyzed.")
                if parent_directory is not None:
                    try:
                        PurePosixPath(result['file']).relative_to(parent_directory)
                    except ValueError:
                        raise Unresolved('PACKAGE_PATH_CONFLICT', 'The child is outside its regular parent package.')
                if end < len(parts) and not result['package']:
                    raise Unresolved('PARENT_NOT_PACKAGE', f"'{current}' is not a package.")
                if result['package']:
                    bindings = self.units[result['file']]['index'].root.bindings
                    if '__path__' in bindings:
                        raise Unresolved('DYNAMIC_PACKAGE_PATH', 'The package modifies its import path.')
                    if end < len(parts) and parts[end] in bindings and not self._binds_child_module(
                            self.units[result['file']], current, parts[end]):
                        raise Unresolved('PACKAGE_ATTRIBUTE_SHADOWED', 'The package explicitly binds this child name.')
                    parent_directory = PurePosixPath(result['file']).parent
            elif current in self.namespaces:
                result = {'package': True, 'namespace': True}
            else:
                raise Unresolved('MODULE_NOT_FOUND', f"'{current}' is external, excluded, or missing from the selected roots.")
        return result

    def _binds_child_module(self, unit, module_name, name):
        """Recognize an explicit binding to this exact child, without recursion.

        In a package, ``from . import child`` is a legitimate module binding,
        not a competing assignment. Other aliases/replacements stay uncertain.
        """
        root = unit['index'].root
        bindings = root.bindings.get(name, [])
        if root.wildcard or len(bindings) != 1:
            return False
        binding = bindings[0]
        node = binding['node']
        if binding['kind'] != 'import' or node not in root.direct:
            return False
        if isinstance(node, ast.Import):
            return any(a.asname == name and a.name == module_name + '.' + name
                       for a in node.names)
        try:
            base = self.absolute_from(unit, node)
        except Unresolved:
            return False
        return base == module_name and any(
            a.name == name and (a.asname or a.name) == name for a in node.names)

    @staticmethod
    def _merge_reference(ref, previous=None, imports=(), bindings=()):
        """Return fresh metadata; never mutate a reusable reference in place."""
        result = dict(ref)
        previous = previous or {}
        result['import_chain'] = (list(previous.get('import_chain', [])) +
                                  list(imports) + list(ref.get('import_chain', [])))
        result['binding_path'] = (list(previous.get('binding_path', [])) +
                                 list(bindings) + list(ref.get('binding_path', [])))
        return result

    @staticmethod
    def _import_evidence(unit, node, name):
        return {'file': unit['report']['file'], 'span': span(node),
                'statement': ast.unparse(node), 'bound_name': name}

    def _definition_value(self, unit, binding, owner, module_name=None):
        node = binding['node']
        if binding['kind'] not in {'function', 'class'}:
            raise Unresolved('EXPORT_NOT_FUNCTION', 'This binding is not a supported function or class definition.')
        if node not in owner.direct:
            raise Unresolved('CONDITIONAL_EXPORT', 'The target is not an unconditional definition in this scope.')
        if node.decorator_list or getattr(node, 'type_params', []):
            raise Unresolved('UNSUPPORTED_TARGET', 'Decorated or generic targets are not resolved.')
        return {'kind': binding['kind'], 'module': module_name,
                'file': unit['report']['file'], 'node': node, 'owner_scope': owner,
                'symbol': unit['symbols'][(node.lineno, node.col_offset)],
                'import_chain': [], 'binding_path': []}

    def export(self, module_name, name, allow_submodule=False, _trail=()):
        key = (module_name, name)
        if key in _trail:
            raise Unresolved('REEXPORT_CYCLE', 'The explicit import/export chain returns to the same binding.')
        if len(_trail) >= self.max_reexport_depth:
            raise Unresolved('REEXPORT_DEPTH_LIMIT', 'The explicit import/export chain exceeds the configured lookup bound.')
        trail = _trail + (key,)
        module = self.module(module_name)
        if not module.get('namespace'):
            unit = self.units[module['file']]
            root = unit['index'].root
            if root.wildcard:
                raise Unresolved('WILDCARD_NAMESPACE', 'A wildcard import makes exports uncertain.')
            bindings = root.bindings.get(name, [])
            if bindings:
                if len(bindings) != 1:
                    raise Unresolved('AMBIGUOUS_EXPORT', 'The target name has competing bindings.')
                binding = bindings[0]
                node = binding['node']
                if binding['kind'] == 'conditional definition' or (
                        binding['kind'] in {'function', 'class', 'import'} and node not in root.direct):
                    raise Unresolved('CONDITIONAL_EXPORT', 'The target is not an unconditional module-level definition.')
                if binding['kind'] == 'import':
                    # ``from . import child`` in this package means loading a
                    # child module, not recursively looking up its own alias.
                    if module['package'] and self._binds_child_module(unit, module_name, name):
                        child = module_name + '.' + name
                        self.module(child)
                        ref = {'kind': 'module', 'module': child}
                        ref = self._merge_reference(ref, imports=[self._import_evidence(unit, node, name)])
                    else:
                        ref = self._import_value(unit, name, node, trail)
                else:
                    ref = self._definition_value(unit, binding, root, module_name)
                return self._merge_reference(ref, bindings=[key])
            if '__getattr__' in root.bindings:
                raise Unresolved('DYNAMIC_EXPORT', 'The module may create this attribute dynamically.')
        if allow_submodule and module['package']:
            child = module_name + '.' + name
            self.module(child)
            return {'kind': 'module', 'module': child, 'import_chain': [], 'binding_path': [key]}
        raise Unresolved('EXPORT_NOT_FOUND', f"No supported export '{name}' exists in '{module_name}'.")

    def absolute_from(self, unit, node):
        if not node.level:
            return node.module or ''
        names = self.names[unit['report']['file']]
        if len(names) != 1:
            raise Unresolved('AMBIGUOUS_PACKAGE_CONTEXT', 'A relative import needs one known package context.')
        parts = next(iter(names)).split('.')
        if PurePosixPath(unit['report']['file']).name != '__init__.py':
            parts.pop()
        if node.level > len(parts):
            raise Unresolved('RELATIVE_IMPORT_OUTSIDE_PACKAGE', 'The import goes beyond the known package root.')
        parts = parts[:len(parts) - node.level + 1]
        return '.'.join(parts + (node.module.split('.') if node.module else []))

    def _import_value(self, unit, name, node, trail=()):
        evidence = self._import_evidence(unit, node, name)
        if isinstance(node, ast.Import):
            alias = next(a for a in node.names if (a.asname or a.name.split('.')[0]) == name)
            self.module(alias.name)
            # Unaliased 'import pkg.child' binds pkg, but establishes the exact
            # child path as loaded. It does not establish arbitrary siblings.
            module_name = alias.name if alias.asname else alias.name.split('.')[0]
            parts = alias.name.split('.')
            ref = {'kind': 'module', 'module': module_name,
                   'loaded_modules': (alias.name,), 'import_chain': [],
                   'binding_path': [('.'.join(parts[:i]), parts[i]) for i in range(1, len(parts))]}
        else:
            alias = next(a for a in node.names if (a.asname or a.name) == name)
            module_name = self.absolute_from(unit, node)
            ref = self.export(module_name, alias.name, allow_submodule=True, _trail=trail)
            # Keep the whole package path as evidence, not only its final
            # export. An observed write to pkg.child makes an ordinary-path
            # assumption uncertain even for ``from pkg.child import f``.
            # We do not simulate sys.modules or import/patch execution order.
            parts = module_name.split('.')
            ref = self._merge_reference(
                ref, bindings=[('.'.join(parts[:i]), parts[i]) for i in range(1, len(parts))])
        return self._merge_reference(ref, imports=[evidence])

    def reference_value(self, unit, expression, scope, at_node, enforce_order=True):
        """Find a lexical module/function/class value, never execute the code.

        A class reference is evidence for a separate method candidate pass. It
        is NOT proof that calling a class executes one particular function.
        """
        suffix = []
        while isinstance(expression, ast.Attribute):
            suffix.insert(0, expression.attr)
            expression = expression.value
        if not isinstance(expression, ast.Name):
            raise Unresolved('COMPUTED_TARGET', 'The callable is a computed expression.')
        name = expression.id
        bindings, owner, reason = lookup(scope, name, unit['index'].root)
        if reason:
            raise Unresolved('UNKNOWN_BINDING', reason)
        if len(bindings) != 1:
            raise Unresolved('AMBIGUOUS_BINDING', f"'{name}' has competing bindings.")
        binding = bindings[0]
        node = binding['node']
        if binding['kind'] == 'import' and node not in owner.direct:
            raise Unresolved('CONDITIONAL_IMPORT', 'The import is conditional or installed from another scope.')
        if enforce_order and owner is scope and position(node)[:2] > position(at_node)[:2]:
            raise Unresolved('IMPORT_AFTER_CALL' if binding['kind'] == 'import' else 'DEFINITION_AFTER_CALL',
                             'The local binding appears after this expression.')
        if binding['kind'] == 'import':
            ref = self._import_value(unit, name, node)
        elif binding['kind'] in {'class', 'function'}:
            ref = self._definition_value(unit, binding, owner)
        else:
            raise Unresolved('NOT_AN_IMPORT', f"'{name}' is not a known imported target or definition.")
        for attribute in suffix:
            if ref['kind'] != 'module':
                raise Unresolved('OBJECT_ATTRIBUTE', 'Object or function attributes need later resolution.')
            loaded = ref.get('loaded_modules', ())
            child = ref['module'] + '.' + attribute
            allow_submodule = any(m == child or m.startswith(child + '.') for m in loaded)
            exported = self.export(ref['module'], attribute, allow_submodule=allow_submodule)
            exported['loaded_modules'] = tuple(set(exported.get('loaded_modules', ())) | set(loaded))
            exported['access'] = (ref['module'], attribute)
            ref = self._merge_reference(exported, previous=ref)
        for key in ref.get('binding_path', ()):
            if key in self.attribute_mutations:
                raise Unresolved('ATTRIBUTE_MUTATION', 'An explicit attribute write may replace a binding in this import/export path.')
        if binding['kind'] == 'import':
            ref['import'] = self._import_evidence(unit, node, name)
        return ref

    def reference(self, unit, expression, scope, at_node, enforce_order=True):
        ref = self.reference_value(unit, expression, scope, at_node, enforce_order)
        if not ref.get('import_chain'):
            raise Unresolved('NOT_AN_IMPORT', 'The expression does not follow an imported binding.')
        if ref['kind'] != 'function':
            raise Unresolved('MODULE_NOT_CALLABLE' if ref['kind'] == 'module' else 'CLASS_CONSTRUCTION',
                             'The expression identifies a module or class, not a direct function target.')
        return ref

    def mutations(self):
        changed = defaultdict(list)
        attributes = defaultdict(list)
        # Collect first, then apply: iteration order must not hide later writes.
        self.attribute_mutations.clear()
        for unit in self.units.values():
            for node, scope in unit['index'].attribute_writes:
                try:
                    ref = self.reference_value(unit, node, scope, node, enforce_order=False)
                except Unresolved:
                    continue
                evidence = {'file': unit['report']['file'], 'span': span(node)}
                if 'symbol' in ref:
                    changed[ref['symbol']['id']].append(evidence)
                if 'access' in ref:
                    attributes[ref['access']].append(evidence)
        self.attribute_mutations = attributes
        return changed

    def public_data(self):
        modules = []
        for name in sorted(set(self.modules) | self.namespaces):
            entries = self.modules.get(name, [])
            status = ('namespace' if not entries else 'ambiguous' if len(entries) > 1 else
                      'available' if entries[0]['status'] == 'ok' else 'unavailable')
            modules.append({'name': name, 'status': status, 'files': sorted(e['file'] for e in entries)})
        definitions = [
            {'symbol_id': s['id'], 'file': path, 'modules': sorted(self.names[path]),
             'qualified_name': s['qualified_name'], 'kind': s['kind'], 'span': s['span']}
            for path, unit in sorted(self.units.items()) for s in unit['report']['symbols']]
        return {'modules': modules, 'definitions': definitions}


def resolve_project_calls(report):
    from repotrace.mapping.method_resolver import MethodCandidates
    from repotrace.mapping.argument_flow import enrich_parameter_hints
    from repotrace.mapping.fixture_flow import add_fixture_hints
    from repotrace.mapping.resolution_summary import classify_project_calls

    index = ProjectIndex(report['files'], report['settings']['source_roots'])
    mutations = index.mutations()
    for path, unit in sorted(index.units.items()):
        file_report = unit['report']
        file_report['module_names'] = sorted(index.names[path])
        file_report['resolution_scope'] = 'project'
        for call in file_report['calls']:
            if call.get('resolution_kind') == 'import' or call.get('resolution') == 'POSSIBLE':
                call.update(target_id=None, resolution='UNRESOLVED', resolution_kind=None)
            call.pop('candidate_targets', None)
            call.pop('candidate_limit_reached', None)
            call.pop('method_resolution_reason', None)
            call.pop('import_evidence', None)
            call.pop('import_chain', None)
            call.pop('target_file', None)
            if call['target_id'] is None:
                s = call['span']
                key = (s['line'], s['column'], s['end_line'], s['end_column'])
                if unit['positions'][key] != 1:
                    continue
                node, scope, context = unit['index'].calls[key]
                if context != 'function':
                    continue
                try:
                    ref = index.reference(unit, node.func, scope, node)
                except Unresolved as exc:
                    if exc.code != 'NOT_AN_IMPORT' or isinstance(node.func, ast.Attribute):
                        call.update(reason=str(exc), reason_code=exc.code)
                else:
                    call.update(target_id=ref['symbol']['id'], target_file=ref['file'],
                                resolution='LIKELY', resolution_kind='import',
                                reason_code='EXPLICIT_IMPORT', import_evidence=ref['import'],
                                import_chain=ref['import_chain'],
                                reason='Explicit import identifies this function under the selected source roots and ordinary initialized-module bindings.')
            if call['target_id'] in mutations:
                evidence = mutations[call['target_id']]
                call.update(target_id=None, resolution='UNRESOLVED', resolution_kind=None,
                            reason_code='ATTRIBUTE_MUTATION', mutation_evidence=evidence,
                            reason='An explicit imported-module attribute write may replace this target.')
                call.pop('target_file', None)
                call.pop('import_evidence', None)
                call.pop('import_chain', None)
    model = MethodCandidates(index)
    # pytest fixtures first: a test that passes a fixture value on to a helper then informs the helper's parameter too.
    fixture_flow = add_fixture_hints(model)
    argument_flow = enrich_parameter_hints(model)
    # Newly observed parameter types can expose writes through those parameters.
    model.scan_mutations()
    model.run()
    model.run_implicit()
    classify_project_calls(index)
    for path, unit in sorted(index.units.items()):
        file_report = unit['report']
        file_report['edges'] = [e for e in file_report['edges'] if e['kind'] not in {'CALLS', 'POSSIBLE_CALLS'}]
        for call in file_report['calls']:
            for candidate in call.get('candidate_targets', []):
                file_report['edges'].append({
                    'kind': 'POSSIBLE_CALLS', 'source': call['enclosing_symbol_id'],
                    'target': candidate['target_id'], 'call_id': call['id'],
                    'resolution': 'POSSIBLE', 'resolution_kind': call.get('resolution_kind'),
                    'reason': candidate['reason'], 'evidence': {'file': path, **call['span']},
                    'candidate_evidence': candidate['evidence']})
            if call['target_id'] is None:
                continue
            edge = {'kind': 'CALLS', 'source': call['enclosing_symbol_id'],
                    'target': call['target_id'], 'call_id': call['id'],
                    'resolution': call['resolution'], 'resolution_kind': call['resolution_kind'],
                    'reason': call['reason'], 'evidence': {'file': path, **call['span']}}
            if 'import_evidence' in call:
                edge['import_evidence'] = call['import_evidence']
            if 'import_chain' in call:
                edge['import_chain'] = call['import_chain']
            file_report['edges'].append(edge)
    report['project_index'] = index.public_data()
    report['project_index']['argument_flow'] = argument_flow
    report['project_index']['fixture_flow'] = fixture_flow
