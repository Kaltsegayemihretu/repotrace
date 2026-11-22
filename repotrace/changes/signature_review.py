"""Narrow, static call-argument checks for changed Python function parameters.

No project code is imported or executed. Only existing LIKELY target bindings
are used; uncertain calls, methods, decorated targets and argument unpacking
are not promoted into findings. This is a review aid, not a safety verdict.
"""
import inspect

POLICY = (
    'Checks explicit arguments to recorded LIKELY ordinary/nested function targets in HEAD. '
    'A finding means the HEAD call fits the old parameters but not the new ones, under that '
    'target-binding assumption. Methods, decorated targets, uncertain/unresolved calls, '
    '*args/**kwargs expansion, runtime values and return-type compatibility are not checked. '
    'No findings does not mean safe; run the relevant tests.'
)
_VALUE = object()
_KINDS = {name: getattr(inspect.Parameter, name) for name in (
    'POSITIONAL_ONLY', 'POSITIONAL_OR_KEYWORD', 'VAR_POSITIONAL', 'KEYWORD_ONLY', 'VAR_KEYWORD')}


def _signature(symbol):
    """Defaults become inert placeholders: never evaluate their source text."""
    params = []
    for p in symbol['parameters']:
        kind = _KINDS[p['kind']]
        default = inspect.Parameter.empty
        if kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD) and not p['required']:
            default = _VALUE
        params.append(inspect.Parameter(p['name'], kind, default=default))
    return inspect.Signature(params)


def _shape(symbol):
    return [(p['name'], p['kind'], p['required']) for p in symbol['parameters']]


def _binding_error(signature, args, kwargs):
    try:
        signature.bind(*args, **kwargs)
    except TypeError as exc:
        return str(exc)
    return None


def review_signatures(before, after, changes, *, max_calls=100_000, max_findings=100):
    """Return per-target checks plus explicit coverage and truncation information.

    Linear in report size, not changes times calls. The analyzer stores canonical
    ast.unparse expressions, so a leading '*' marks a starred positional argument;
    keyword name None marks ** expansion. We deliberately do not guess their size.
    """
    for name, value in (('max_calls', max_calls), ('max_findings', max_findings)):
        if type(value) is not int or value < 1:
            raise ValueError(name + ' must be a positive integer')
    old = {s['id']: s for f in before['files'] if f['status'] == 'ok' for s in f['symbols']}
    new = {s['id']: s for f in after['files'] if f['status'] == 'ok' for s in f['symbols']}
    targets, signatures = {}, {}
    summary = {'eligible_functions': 0, 'skipped_changed_functions': 0, 'scanned_calls': 0,
               'checked_calls': 0, 'skipped_calls': 0, 'finding_count': 0, 'findings_recorded': 0,
               'limits_reached': []}
    for change in changes:
        if 'before' not in change or 'after' not in change:
            continue
        a, b = old.get(change['before']['id']), new.get(change['after']['id'])
        if not a or not b or 'parameters' not in a or 'parameters' not in b or _shape(a) == _shape(b):
            continue
        entry = {'status': 'checked', 'policy': POLICY, 'checked_calls': 0,
                 'skipped_calls': {}, 'finding_count': 0, 'findings': []}
        targets[b['id']] = entry
        if (a['kind'] not in {'FUNCTION', 'NESTED_FUNCTION'} or
                b['kind'] not in {'FUNCTION', 'NESTED_FUNCTION'} or
                a.get('decorators') or b.get('decorators')):
            entry.update(status='not_supported', reason='Methods and decorated targets are not checked yet.')
            summary['skipped_changed_functions'] += 1
            continue
        try:
            signatures[b['id']] = (_signature(a), _signature(b), a, b)
        except (ValueError, KeyError, TypeError):
            entry.update(status='not_supported', reason='Parameter metadata could not be interpreted.')
            summary['skipped_changed_functions'] += 1
    summary['eligible_functions'] = len(signatures)

    def skip(entry, reason):
        entry['skipped_calls'][reason] = entry['skipped_calls'].get(reason, 0) + 1
        summary['skipped_calls'] += 1

    if signatures:
        stop = False
        for file in after['files']:
            if file['status'] != 'ok':
                continue
            for call in file['calls']:
                if summary['scanned_calls'] >= max_calls:
                    summary['limits_reached'].append('max_calls')
                    stop = True
                    break
                summary['scanned_calls'] += 1
                target = call.get('target_id')
                if target not in signatures:
                    continue
                entry = targets[target]
                if call.get('resolution') != 'LIKELY':
                    skip(entry, 'uncertain_target')
                    continue
                if call.get('context') in {'ANNOTATION', 'TYPE_PARAMETER'}:
                    skip(entry, 'annotation_context')
                    continue
                arguments, keywords = call.get('arguments'), call.get('keywords')
                if (not isinstance(arguments, list) or not isinstance(keywords, list) or
                        any(not isinstance(a, str) for a in arguments)):
                    skip(entry, 'missing_argument_metadata')
                    continue
                if any(a.lstrip().startswith('*') for a in arguments) or any(k.get('name') is None for k in keywords):
                    skip(entry, 'unpacked_arguments')
                    continue
                names = [k['name'] for k in keywords]
                if len(set(names)) != len(names):
                    skip(entry, 'repeated_keyword')
                    continue
                args, kwargs = [_VALUE] * len(arguments), dict.fromkeys(names, _VALUE)
                old_sig, new_sig, old_target, new_target = signatures[target]
                entry['checked_calls'] += 1
                summary['checked_calls'] += 1
                if _binding_error(old_sig, args, kwargs):
                    continue  # not a newly introduced incompatibility with this call shape
                error = _binding_error(new_sig, args, kwargs)
                if not error:
                    continue
                entry['finding_count'] += 1
                summary['finding_count'] += 1
                if summary['findings_recorded'] >= max_findings:
                    if 'max_findings' not in summary['limits_reached']:
                        summary['limits_reached'].append('max_findings')
                    continue
                caller = new.get(call.get('enclosing_symbol_id'), {})
                entry['findings'].append({
                    'kind': 'CALL_ARGUMENT_MISMATCH', 'call_id': call['id'], 'file': call['file'],
                    'span': call['span'], 'caller_id': call.get('enclosing_symbol_id'),
                    'caller': caller.get('qualified_name', '<module>'),
                    'called_expression': call['expression'], 'target_id': target,
                    'target_name': new_target['qualified_name'], 'target_file': new_target['file'],
                    'before_span': old_target['span'], 'after_span': new_target['span'],
                    'reason': error, 'resolution': 'LIKELY',
                    'binding_reason': call.get('reason', ''),
                    'message': 'This call fits the old parameters, but not the new ones.',
                    'action': 'Check this call against the changed function, update its arguments if needed, and run its tests.'})
                summary['findings_recorded'] += 1
            if stop:
                break
    for entry in targets.values():
        entry['truncated'] = ('max_calls' in summary['limits_reached'] or
                              entry['finding_count'] > len(entry['findings']))
        if entry['truncated'] and entry['status'] == 'checked':
            entry['status'] = 'limited'
    return {'by_target': targets, 'summary': summary, 'policy': POLICY}
