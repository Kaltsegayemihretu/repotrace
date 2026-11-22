"""Compare two repository reports and list changed function definitions.

Matching key: relative file + qualified name + kind, only when unique on that side.
A syntax-hash difference means the function's syntax changed, not that behavior did.
Failed files are coverage gaps, never additions or deletions.
"""
import ast
import textwrap
from collections import Counter, defaultdict
from pathlib import PurePosixPath

FUNCTION_KINDS = ('FUNCTION', 'METHOD', 'NESTED_FUNCTION')

POLICY = {
    'matching_key': 'file + qualified_name + kind, only where unique on both sides',
    'renames_and_moves': 'reported as one deleted and one added function (not matched)',
    'comparison': 'syntax without positions, comments or docstrings; those edits are listed as cosmetic',
    'lambdas': 'not matched across commits',
    'constants': 'a changed module-level constant marks the functions that read it as changed',
    'newly_parseable_file': 'functions in a file that did not parse before are listed as added, flagged',
}


def _files(report):
    return {item['file']: item for item in report['files']}


def _import_signature(file):
    return sorted(repr((i.get('kind'), i.get('module'), i.get('level'),
                        tuple((n.get('name'), n.get('alias')) for n in i.get('names', []))))
                  for i in file.get('imports', []))


def _definitions(file):
    """Group function symbols by key; also return the id->symbol map for ancestry."""
    groups = defaultdict(list)
    for symbol in file['symbols']:
        if symbol['kind'] in FUNCTION_KINDS:
            groups[(symbol['qualified_name'], symbol['kind'])].append(symbol)
    return groups


def _ancestors(file, symbol_id):
    parents = {s['id']: s.get('parent_id') for s in file['symbols']}
    found, current = [], parents.get(symbol_id)
    while current in parents:
        found.append(current)
        current = parents[current]
    return found


def _strip_docstrings(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return tree


def _fingerprint(source):
    """Syntax without positions, comments or docstrings; None if it cannot be parsed."""
    try:
        return ast.dump(_strip_docstrings(ast.parse(textwrap.dedent(source))))
    except (SyntaxError, ValueError, TypeError, RecursionError):
        return None


def _same_code(old, new):
    a, b = _fingerprint(old), _fingerprint(new)
    return a is not None and a == b


def _module_facts(source):
    """(module-level constants {name: value syntax}, other module/class-level code) of a file."""
    tree = _strip_docstrings(ast.parse(source))
    constants, counts, residual = {}, Counter(), []
    for statement in tree.body:
        target = value = None
        if (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)):
            target, value = statement.targets[0].id, statement.value
        elif (isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
              and statement.value is not None):
            target, value = statement.target.id, statement.value
        if target is not None:
            counts[target] += 1
            constants[target] = ast.dump(value)
        elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Import, ast.ImportFrom)):
            continue
        elif isinstance(statement, ast.ClassDef):
            header = ast.ClassDef(name=statement.name, bases=statement.bases, keywords=statement.keywords,
                                  decorator_list=statement.decorator_list,
                                  body=[s for s in statement.body
                                        if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))])
            residual.append(ast.dump(header))
        else:
            residual.append(ast.dump(statement))
    for name, count in counts.items():
        if count > 1:       # assigned several times: the final value is not one simple constant
            constants.pop(name)
            residual.append('repeated:' + name)
    return constants, tuple(residual)


def _readers(source, bare_names, attribute_reads):
    """Positions of functions that read a module-level name (and do not rebind it locally)."""
    tree = ast.parse(source)
    found = set()
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        arguments = function.args
        bound = {a.arg for a in arguments.posonlyargs + arguments.args + arguments.kwonlyargs}
        bound |= {a.arg for a in (arguments.vararg, arguments.kwarg) if a is not None}
        reads = False
        for node in ast.walk(function):
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
                bound.add(node.id)
        for node in ast.walk(function):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in bare_names \
                    and node.id not in bound:
                reads = True
            elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                  and (node.value.id, node.attr) in attribute_reads and node.value.id not in bound):
                reads = True
        if reads:
            found.add((function.lineno, function.col_offset))
    return found


def _module_tail(path):
    pure = PurePosixPath(path)
    return pure.parent.name if pure.stem == '__init__' else pure.stem


def _constant_readers(old_files, new_files, file_name, constant):
    """Unchanged-looking functions that read `constant`: in its own file and in importing files."""
    readers = []
    tail = _module_tail(file_name)
    for other_name, other in sorted(new_files.items()):
        if other['status'] != 'ok':
            continue
        bare, attributes = set(), set()
        if other_name == file_name:
            bare.add(constant)
        else:
            try:
                tree = ast.parse(other.get('source', ''))
            except (SyntaxError, ValueError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module_tail = (node.module or '').rsplit('.', 1)[-1]
                    for alias in node.names:
                        if module_tail == tail and alias.name == constant:
                            bare.add(alias.asname or alias.name)
                        elif alias.name == tail and (not node.module or module_tail != tail):
                            attributes.add((alias.asname or alias.name, constant))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.asname and alias.name.rsplit('.', 1)[-1] == tail:
                            attributes.add((alias.asname, constant))
            if not bare and not attributes:
                continue
        try:
            positions = _readers(other.get('source', ''), bare, attributes)
        except (SyntaxError, ValueError):
            continue
        old = old_files.get(other_name)
        if old is None or old['status'] != 'ok':
            continue
        old_groups = _definitions(old)
        for symbol in other['symbols']:
            if symbol['kind'] not in FUNCTION_KINDS or (symbol['span']['line'], symbol['span']['column']) not in positions:
                continue
            key = (symbol['qualified_name'], symbol['kind'])
            news = _definitions(other).get(key, [])
            olds = old_groups.get(key, [])
            if len(news) == 1 and len(olds) == 1:
                readers.append({'file': other_name, 'qualified_name': key[0], 'kind': key[1],
                                'before': olds[0], 'after': news[0]})
    return readers


def _brief(symbol):
    return {'id': symbol['id'], 'span': symbol['span'], 'syntax_sha256': symbol.get('syntax_sha256')}


def detect_changes(before, after):
    """Return file changes, definition changes and coverage gaps between two reports."""
    old_files, new_files = _files(before), _files(after)
    changes, gaps, files, constants, constant_hits = [], [], [], [], []
    for name in sorted(set(old_files) | set(new_files)):
        old, new = old_files.get(name), new_files.get(name)
        old_ok = old is not None and old['status'] == 'ok'
        new_ok = new is not None and new['status'] == 'ok'
        if old is None and new is None:
            continue
        if (old is not None and not old_ok) or (new is not None and not new_ok):
            side = 'both' if (old and not old_ok and new and not new_ok) else \
                   'before' if old is not None and not old_ok else 'after'
            gaps.append({'file': name, 'type': 'analysis_failed', 'side': side,
                         'message': 'Definitions in this file cannot be compared; '
                                    'a failed parse is not an addition or deletion.'})
            files.append({'file': name, 'change': 'unanalyzed', 'side': side})
            if old is not None and not old_ok and new_ok:
                # The file parses now but did not before: its functions cannot be compared, so they
                # are listed as new and flagged. They may have existed in the unparseable version.
                for (qualified_name, kind), news in sorted(_definitions(new).items()):
                    if len(news) == 1:
                        changes.append({'file': name, 'qualified_name': qualified_name, 'kind': kind,
                                        'change': 'added', 'after': _brief(news[0]),
                                        'before_file_did_not_parse': True})
            continue
        if old is None:
            files.append({'file': name, 'change': 'added'})
        elif new is None:
            files.append({'file': name, 'change': 'removed'})
        elif old['content_sha256'] == new['content_sha256']:
            continue
        elif _same_code(old.get('source', ''), new.get('source', '')):
            # Only comments, blank lines or docstrings differ: nothing to analyze.
            files.append({'file': name, 'change': 'cosmetic'})
            continue
        else:
            files.append({'file': name, 'change': 'modified',
                          'imports_changed': _import_signature(old) != _import_signature(new)})
        old_groups = _definitions(old) if old_ok else {}
        new_groups = _definitions(new) if new_ok else {}
        file_changes = []
        for key in sorted(set(old_groups) | set(new_groups)):
            qualified_name, kind = key
            olds, news = old_groups.get(key, []), new_groups.get(key, [])
            entry = {'file': name, 'qualified_name': qualified_name, 'kind': kind}
            if len(olds) > 1 or len(news) > 1:
                gaps.append({'file': name, 'type': 'ambiguous_definition',
                             'qualified_name': qualified_name,
                             'message': 'Several definitions share this name on one side; '
                                        'not matched across commits.'})
                continue
            if olds and news:
                if olds[0].get('syntax_sha256') == news[0].get('syntax_sha256'):
                    continue
                if _same_code(olds[0].get('source', ''), news[0].get('source', '')):
                    files[-1].setdefault('docstring_or_comment_only', []).append(qualified_name)
                    continue
                entry.update(change='modified', before=_brief(olds[0]), after=_brief(news[0]))
            elif news:
                entry.update(change='added', after=_brief(news[0]))
            else:
                entry.update(change='deleted', before=_brief(olds[0]))
            file_changes.append(entry)
        # A nested edit also changes the hash of every enclosing function: flag the overlap.
        modified_after = {c['after']['id'] for c in file_changes if c['change'] == 'modified'}
        for entry in file_changes:
            if entry['change'] == 'modified':
                inner = any(entry['after']['id'] in _ancestors(new, other)
                            for other in modified_after if other != entry['after']['id'])
                if inner:
                    entry['includes_nested_change'] = True
        changes.extend(file_changes)
        constants_changed, residual_changed = [], False
        if old is not None and new is not None and files[-1]['change'] == 'modified':
            try:
                old_constants, old_residual = _module_facts(old.get('source', ''))
                new_constants, new_residual = _module_facts(new.get('source', ''))
            except (SyntaxError, ValueError, RecursionError):
                old_constants = new_constants = {}
                old_residual, new_residual = ('?',), ('!',)
            residual_changed = old_residual != new_residual
            for constant in sorted(old_constants):
                if old_constants[constant] != new_constants.get(constant):
                    readers = _constant_readers(old_files, new_files, name, constant)
                    constants_changed.append({'file': name, 'name': constant,
                                              'change': 'removed' if constant not in new_constants else 'modified',
                                              'readers': [f"{r['file']}::{r['qualified_name']}" for r in readers]})
                    for reader in readers:
                        constant_hits.append((constant, name, reader))
            files[-1]['constants_changed'] = [c['name'] for c in constants_changed]
        constants.extend(constants_changed)
        explained = any(c['readers'] for c in constants_changed)
        if old is not None and new is not None:
            imports_changed = bool(files and files[-1].get('imports_changed'))
            for c in constants_changed:
                if not c['readers']:
                    gaps.append({'file': name, 'type': 'constant_without_readers',
                                 'message': f"Constant {c['name']} changed but no function that reads it "
                                            f"was found; impact is not computed."})
            if not file_changes and not explained:
                if residual_changed or imports_changed or not constants_changed:
                    gaps.append({'file': name, 'type': 'contextual_change_only',
                                 'message': 'File content changed but no function definition did '
                                            '(imports, constants, class bases or module code). '
                                            'Impact of this change is not computed.'})
            else:
                if imports_changed:
                    gaps.append({'file': name, 'type': 'imports_changed',
                                 'message': 'Imports changed in this file; callers may depend on them.'})
                if residual_changed:
                    gaps.append({'file': name, 'type': 'module_level_code_changed',
                                 'message': 'Module-level or class-level code (not a function) also changed '
                                            'in this file; its effect is not computed.'})
    # Functions that read a changed constant count as changed, even though their text did not.
    index = {(c['file'], c['qualified_name'], c['kind']): c for c in changes}
    for constant, constant_file, reader in constant_hits:
        key = (reader['file'], reader['qualified_name'], reader['kind'])
        existing = index.get(key)
        if existing is not None:
            existing.setdefault('also_reads_changed_constants', []).append(constant)
            continue
        entry = {'file': reader['file'], 'qualified_name': reader['qualified_name'], 'kind': reader['kind'],
                 'change': 'modified', 'before': _brief(reader['before']), 'after': _brief(reader['after']),
                 'reason': 'reads_changed_constant', 'constants': [constant], 'constant_file': constant_file}
        changes.append(entry)
        index[key] = entry
    changes.sort(key=lambda c: (c['file'], c['qualified_name'], c['kind']))
    return {'files': files, 'definitions': changes, 'gaps': gaps, 'constants': constants,
            'policy': dict(POLICY)}
