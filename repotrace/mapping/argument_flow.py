"""Bounded observations of receiver values passed to ordinary functions.

This supplies POSSIBLE receiver hints, not runtime types. It never changes a
direct call target, executes source, infers from a method name, or assumes every
caller was observed. One witness per observed class is enough for this pass.
"""
import ast
from collections import defaultdict

from repotrace.mapping.method_resolver import Unsupported, span
from repotrace.reading.resolver import lookup, position


def _bind_arguments(function, call, skip_first=False):
    """Bind only an unambiguous, complete explicit argument list.

    skip_first is for constructor calls: the new object is passed as the first
    parameter implicitly, so explicit arguments start at the second one.
    """
    if any(isinstance(value, ast.Starred) for value in call.args):
        return None
    if any(keyword.arg is None for keyword in call.keywords):
        return None
    args = function.args
    full = args.posonlyargs + args.args
    required_full = full[:len(full) - len(args.defaults)]
    positional = full[1:] if skip_first else full
    if skip_first and not full:
        return None
    if len(call.args) > len(positional):
        return None
    bound = dict(zip(positional, call.args))
    named = {arg.arg: arg for arg in positional + args.kwonlyargs if arg in args.args or arg in args.kwonlyargs}
    for keyword in call.keywords:
        arg = named.get(keyword.arg)
        if arg is None or arg in bound:
            return None
        bound[arg] = keyword.value
    required = [arg for arg in required_full if arg in positional]
    required += [arg for arg, default in zip(args.kwonlyargs, args.kw_defaults) if default is None]
    if any(arg not in bound for arg in required):
        return None
    return bound


def _constructor_target(model, unit, call, scope):
    """The __init__ a plain `Class(...)` call would run, or None when that is not provable."""
    try:
        info, _ = model.class_reference(unit, call.func, scope, call)
        for class_id in model.mro(info['id']):
            if '__new__' in model.classes[class_id]['bindings']:
                return None
        symbol, owner, _ = model.find_method(info['id'], '__init__')
    except Unsupported:
        return None
    function = owner['bindings']['__init__'][0]
    if (not isinstance(function, ast.FunctionDef) or function.decorator_list
            or getattr(function, 'type_params', [])):
        return None
    return function, symbol, owner['file']


def _source_parameter(model, unit, expression, scope, at_node, seen=()):
    """Recognize parameter forwarding, including unconditional simple aliases."""
    if not isinstance(expression, ast.Name) or len(seen) >= model.max_depth:
        return None
    bindings, owner, reason = lookup(scope, expression.id, unit['index'].root)
    if reason or not bindings or len(bindings) != 1:
        return None
    binding = bindings[0]
    node = binding['node']
    if node in seen:
        return None
    if binding['kind'] == 'parameter':
        return node
    if binding['kind'] != 'assignment or deletion':
        return None
    statement = model.parents[unit['report']['file']].get(node)
    if not isinstance(statement, (ast.Assign, ast.AnnAssign)) or statement not in owner.direct:
        return None
    if isinstance(statement, ast.Assign) and not all(isinstance(t, ast.Name) for t in statement.targets):
        return None
    if statement.value is None or position(statement)[:2] >= position(at_node)[:2]:
        return None
    return _source_parameter(model, unit, statement.value, owner, statement, seen + (node,))


def _main_guard(node):
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]
    return ((isinstance(left, ast.Name) and left.id == '__name__' and
             isinstance(right, ast.Constant) and right.value == '__main__') or
            (isinstance(right, ast.Name) and right.id == '__name__' and
             isinstance(left, ast.Constant) and left.value == '__main__'))


def _guarded_receiver(model, unit, expression, scope, call):
    """Narrow support for a constructor assignment before use in a main guard.

    Only a single module binding, assigned directly in the guard body, is
    accepted. The later call must be in that body, optionally inside try/except.
    Loops, nested conditions, alternate branches and closure uses are excluded.
    """
    root = unit['index'].root
    if scope is not root or not isinstance(expression, ast.Name):
        raise Unsupported('No supported guarded receiver assignment.')
    if root.wildcard or '__name__' in root.bindings:
        raise Unsupported('The entry-point guard binding is uncertain.')
    bindings = root.bindings.get(expression.id, [])
    if len(bindings) != 1 or bindings[0]['kind'] != 'assignment or deletion':
        raise Unsupported('The guarded receiver is reassigned or unavailable.')
    # The regular receiver pass cannot infer this guarded variable when scanning
    # writes. Suppress this observation if an explicit write could change it.
    for write, write_scope in unit['index'].attribute_writes:
        if isinstance(write.value, ast.Name) and write.value.id == expression.id and write_scope is root:
            raise Unsupported('The guarded receiver has an explicit attribute mutation.')
    for write, write_scope, _ in unit['index'].calls.values():
        if (write_scope is root and isinstance(write.func, ast.Name)
                and write.func.id in {'setattr', 'delattr'} and write.args
                and isinstance(write.args[0], ast.Name) and write.args[0].id == expression.id):
            raise Unsupported('The guarded receiver has an explicit reflective mutation.')
    parents = model.parents[unit['report']['file']]
    statement = parents.get(bindings[0]['node'])
    if isinstance(statement, ast.Assign):
        if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            raise Unsupported('Only one simple guarded constructor binding is supported.')
    elif not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
        raise Unsupported('No simple guarded assignment.')
    if not isinstance(statement.value, ast.Call):
        raise Unsupported('The guarded value must be a constructor expression.')
    guard = parents.get(statement)
    if not _main_guard(guard) or guard not in root.direct or statement not in guard.body:
        raise Unsupported('The assignment is not directly in a module entry-point guard.')
    branch = call
    allowed = (ast.Expr, ast.Try, ast.ExceptHandler)
    while parents.get(branch) is not guard:
        branch = parents.get(branch)
        if branch is None or not isinstance(branch, allowed):
            raise Unsupported('This use does not have the supported guarded execution order.')
    if branch not in guard.body or guard.body.index(statement) >= guard.body.index(branch):
        raise Unsupported('The guarded constructor assignment is not before this call.')
    receiver = model.receiver(unit, statement.value, root, statement)
    return {**receiver, 'evidence': receiver['evidence'] + [{
        'kind': 'guarded_constructor_assignment', 'file': unit['report']['file'],
        'span': span(statement), 'name': expression.id, 'guard_span': span(guard),
        'assumption': 'This observation applies only when this entry-point guard body is entered.'}]}


def enrich_parameter_hints(model, *, max_rounds=16, max_observations=50000,
                           max_parameters=10000, max_evidence=12):
    """Add bounded observed argument hints to ``model.parameter_hints``.

    Conflicting observed classes/modes suppress the hint, including downstream
    forwarding. If bounds prevent completing the pass, no new hints are issued.
    Existing caller-supplied hints are preserved. All new hints retain a single
    witness path and remain possible observations, not exhaustive caller types.
    """
    if min(max_rounds, max_observations, max_parameters, max_evidence) < 1:
        raise ValueError('Argument-flow bounds must be positive.')
    original = dict(model.parameter_hints)
    generated_before = {arg for arg, hint in original.items() if hint.get('observed_argument_flow')}
    for arg in generated_before:
        model.parameter_hints.pop(arg, None)
    states, dependencies = defaultdict(dict), []
    tracked, limits = set(), []
    observations = 0
    mutated_functions = model.project.mutations()

    def keep_evidence(steps, observation):
        # Keep the original receiver proof and the final argument call location.
        # Truncation is disclosed rather than pretending all intermediate calls
        # are retained. This list is a witness, not every observed call site.
        result = list(steps) + [observation]
        if len(result) > max_evidence:
            result = result[:max(0, max_evidence - 1)] + [
                {**observation, 'earlier_evidence_truncated': True}]
        return result

    def offer(arg, receiver, observation):
        key = receiver['class'], receiver['mode']
        existing = states[arg].get(key)
        value = {**receiver, 'evidence': keep_evidence(receiver.get('evidence', []), observation),
                 'observed_argument_flow': True,
                 'observation_warning': 'Only observed call sites are represented; other callers may pass other types.'}
        if existing is None:
            # Two distinct types are sufficient to represent a conflict. They
            # propagate downstream so an early observation cannot hide a later
            # competing type, without growing unbounded type sets.
            if len(states[arg]) >= 2:
                return False
            states[arg][key] = value
            return True
        if receiver.get('polymorphic') and not existing.get('polymorphic'):
            existing['polymorphic'] = True
            return True
        return False

    for path, unit in sorted(model.project.units.items()):
        records = {(c['span']['line'], c['span']['column'], c['span']['end_line'],
                    c['span']['end_column']): c for c in unit['report']['calls']}
        for key, (call, scope, context) in sorted(unit['index'].calls.items()):
            if context not in {'function', 'module'} or unit['positions'][key] != 1:
                continue
            record = records.get(key, {})
            if record.get('reason_code') == 'ATTRIBUTE_MUTATION':
                continue
            try:
                ref = model.reference(unit, call.func, scope, call)
            except Unsupported:
                continue
            constructor = ref['kind'] == 'class'
            if constructor:
                found = _constructor_target(model, unit, call, scope)
                if found is None:
                    continue
                function, ref = found[0], {'symbol': found[1], 'file': found[2], 'kind': 'function'}
            else:
                function = ref.get('node')
                if (ref['kind'] != 'function' or not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
                        or function.decorator_list or getattr(function, 'type_params', [])):
                    continue
            if ref['symbol']['id'] in mutated_functions:
                continue
            if record.get('target_id') is not None and record['target_id'] != ref['symbol']['id']:
                continue
            if not constructor and isinstance(model.parents[ref['file']].get(function), ast.ClassDef):
                continue
            bound = _bind_arguments(function, call, skip_first=constructor)
            if bound is None:
                continue
            for parameter, actual in bound.items():
                if observations >= max_observations:
                    limits.append('max_observations')
                    break
                observations += 1
                source_parameter = _source_parameter(model, unit, actual, scope, call)
                tracked.add(parameter)
                if source_parameter is not None:
                    tracked.add(source_parameter)
                if len(tracked) > max_parameters:
                    limits.append('max_parameters')
                    break
                evidence = {
                    'kind': 'observed_argument', 'file': path, 'span': span(call),
                    'called_function': ref['symbol']['id'], 'parameter': parameter.arg,
                    'parameter_file': ref['file'], 'parameter_span': span(parameter),
                    'argument': ast.unparse(actual)[:500],
                    'warning': 'This is one observed argument value, not a guarantee for every caller.'}
                if source_parameter is not None:
                    dependencies.append((source_parameter, parameter, evidence))
                try:
                    receiver = model.receiver(unit, actual, scope, call)
                except Unsupported:
                    try:
                        receiver = _guarded_receiver(model, unit, actual, scope, call)
                    except Unsupported:
                        continue
                offer(parameter, receiver, evidence)
            if limits:
                break
        if limits:
            break

    rounds = 0
    if not limits:
        for rounds in range(1, max_rounds + 1):
            changed = False
            # Snapshot each round, so transfer distance does not depend on file
            # ordering. At most two class/mode records live at each parameter.
            previous = {arg: list(values.values()) for arg, values in states.items()}
            for source, target, evidence in dependencies:
                for receiver in previous.get(source, []):
                    changed = offer(target, receiver, evidence) or changed
            if not changed:
                break
        else:
            limits.append('max_rounds')
    hints = {arg: next(iter(values.values())) for arg, values in states.items() if len(values) == 1}
    if not limits:
        for arg, hint in hints.items():
            if arg not in model.parameter_hints:
                model.parameter_hints[arg] = hint
    return {
        'kind': 'observed_argument_flow', 'observations': observations,
        'parameters_with_hints': len(hints) if not limits else 0,
        'conflicting_parameters': sum(len(values) > 1 for values in states.values()),
        'forwarding_steps': len(dependencies), 'rounds': rounds,
        'limits_reached': sorted(set(limits)),
        'settings': {'max_rounds': max_rounds, 'max_observations': max_observations,
                     'max_parameters': max_parameters, 'max_evidence': max_evidence},
        'warning': 'Hints are observed possible receiver types, not a complete inventory of runtime callers.'}
