"""A plain-words account of a change: what happened, what to check, in a few sentences.

Works only from what the analysis already found. A rename or a move is claimed only when a deleted
and a new function are the same code apart from their name, so nothing is guessed.
"""
import ast
import re
import textwrap

MIN_NODES = 12          # smaller bodies (such as `pass` or one return) match too many unrelated functions
MAX_ITEMS = 6
FLAG_OLD_NAME = {'head still references the removed name', 'not updated: may still call the removed function'}


def _shape(source):
    """The code of one definition with its name blanked out, or None if it cannot be compared."""
    if not source:
        return None
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return None
    if len(tree.body) != 1 or not isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return None
    node = tree.body[0]
    if sum(1 for _ in ast.walk(node)) < MIN_NODES:
        return None
    node.name = '_'
    return ast.dump(node, annotate_fields=False, include_attributes=False)


def _simple(name):
    return name.split('.')[-1]


def find_pairs(changes):
    """[{'from': id, 'to': id, 'how': 'moved' | 'renamed' | 'renamed and moved'}]"""
    added = {}
    for c in changes:
        if c['change'] == 'added':
            shape = _shape(c['source']['after'])
            if shape:
                added.setdefault(shape, []).append(c)
    pairs, used = [], set()
    for d in changes:
        if d['change'] != 'deleted':
            continue
        options = [a for a in added.get(_shape(d['source']['before']) or '', []) if a['id'] not in used]
        if len(options) > 1:
            options = [a for a in options if _simple(a['name']) == _simple(d['name'])]
        if len(options) != 1:
            continue
        a = options[0]
        used.add(a['id'])
        same_name, same_file = _simple(a['name']) == _simple(d['name']), a['file'] == d['file']
        if same_name and same_file:
            continue
        pairs.append({'from': d['id'], 'to': a['id'],
                      'how': 'moved' if same_name else 'renamed' if same_file else 'renamed and moved'})
    return pairs


def _changed_lines(diff):
    return '\n'.join(line[1:] for line in (diff or '').splitlines()
                     if line[:1] in '+-' and not line.startswith(('+++', '---')))


def _names(items, limit=3):
    shown = ', '.join(f'`{n}`' for n in items[:limit])
    return shown + (f' and {len(items) - limit} more' if len(items) > limit else '')


def _plural(n, one, many=None):
    return f'{n} {one if n == 1 else many or one + "s"}'


def _use_text(c):
    direct = [x for x in c['candidates'] if x['distance'] == 1]
    indirect = len(c['candidates']) - len(direct)
    if not c['candidates']:
        return 'RepoTrace found nothing in the project that uses it.'
    text = _plural(len(direct), 'place') + ' use' + ('s' if len(direct) == 1 else '') + ' it directly'
    return text + (f' and {indirect} more use it through other code.' if indirect else '.')


def build_story(changes):
    """Order and describe the changes for a reader who is new to all of this."""
    by_id = {c['id']: c for c in changes}
    pairs = find_pairs(changes)
    paired_from, paired_to = {p['from']: p for p in pairs}, {p['to']: p for p in pairs}
    for p in pairs:
        by_id[p['from']]['pair'] = {'how': p['how'], 'other': by_id[p['to']]['name'], 'other_file': by_id[p['to']]['file'], 'role': 'old'}
        by_id[p['to']]['pair'] = {'how': p['how'], 'other': by_id[p['from']]['name'], 'other_file': by_id[p['from']]['file'], 'role': 'new'}
    renamed_names = {_simple(by_id[p['from']]['name']) for p in pairs} | {_simple(by_id[p['to']]['name']) for p in pairs}
    pattern = re.compile(r'\b(' + '|'.join(map(re.escape, sorted(renamed_names))) + r')\b') if renamed_names else None

    primary, secondary, items = [], [], []
    follow_ups = 0
    for c in changes:
        if c['id'] in paired_to:
            secondary.append(c['id'])
        elif pattern and c['change'] == 'modified' and pattern.search(_changed_lines(c['diff'])):
            secondary.append(c['id'])
            follow_ups += 1
        else:
            primary.append(c['id'])

    for c in (by_id[i] for i in primary):
        name = f"`{c['name']}`"
        if c['id'] in paired_from:
            p, other = paired_from[c['id']], by_id[paired_from[c['id']]['to']]
            if p['how'] == 'moved':
                head = f"{name} was moved from `{c['file']}` to `{other['file']}`."
            elif p['how'] == 'renamed':
                head = f"{name} was renamed to `{other['name']}`."
            else:
                head = f"{name} was renamed to `{other['name']}` and moved to `{other['file']}`."
            used = len(c['candidates'])
            items.append(head + (f" {_plural(used, 'place')} used it." if used else ''))
        elif c['change'] == 'added':
            items.append(f'{name} is new.')
        elif c['change'] == 'deleted':
            used = len(c['candidates'])
            items.append(f'{name} was deleted.' + (f" {_plural(used, 'place')} used it." if used else ''))
        elif c.get('reason') == 'reads_changed_constant':
            items.append(f"{name} was not edited, but it uses {_names(c.get('constants') or ['a value'])}, and that value changed.")
        elif c.get('decorator_effect'):
            n = len(c['decorator_effect']['wrapped'])
            items.append(f'{name} is a decorator (a wrapper written with @). It wraps {_plural(n, "function")}, so they all behave differently now.')
        else:
            items.append(f'{name} was edited. ' + _use_text(c))
    if len(items) > MAX_ITEMS:
        items = items[:MAX_ITEMS] + [f'...and {len(items) - MAX_ITEMS} more changes (use the buttons below).']
    if follow_ups:
        items.append(f"{_plural(follow_ups, 'other function')} {'was' if follow_ups == 1 else 'were'} edited to match.")

    # What to do next.
    leftovers = []
    for c in changes:
        if c['change'] == 'deleted' or c['id'] in paired_from:
            for x in c['candidates']:
                if FLAG_OLD_NAME & set(x['flags']):
                    leftovers.append(x['name'])
            m = c.get('name_matches') or {}
            if m.get('meaning') == 'still_referenced_in_head':
                leftovers += [s['caller'] for s in m.get('sites', [])]
    leftovers = list(dict.fromkeys(leftovers))
    removed_something = any(c['change'] == 'deleted' for c in changes)
    tests = list(dict.fromkeys(x['name'] for c in changes for x in c['candidates']
                               if 'test code' in x['flags'] and x['distance'] == 1))
    if leftovers:
        check = {'level': 'look', 'text': f'Look at these first. They may still use the old name: {_names(leftovers, 4)}.'}
    elif removed_something:
        check = {'level': 'ok', 'text': 'RepoTrace found nothing that still uses the old name. It cannot see every kind of call, so run your tests as well.'}
    elif changes:
        check = {'level': 'info', 'text': 'Check the places that use the changed code (the boxes in the picture), and run the tests that cover them.'}
    else:
        check = None
    mismatches = sum((c.get('signature_review') or {}).get('finding_count', 0) for c in changes)
    if mismatches:
        verb = 'matches' if mismatches == 1 else 'match'
        text = (f"{_plural(mismatches, 'call')} no longer {verb} the updated function inputs, based on the calls RepoTrace could trace. "
                'Open the changed function and review Calls to update; run the relevant tests.')
        check = {'level': 'look', 'text': (check['text'] + ' ' if leftovers and check else '') + text}
    if tests and check:
        check['text'] += f' Tests that use it: {_names(tests, 3)}.'

    n = len(primary)
    return {'headline': 'No function changed' if not changes else _plural(n, 'change') + ' in this update',
            'items': items, 'check': check, 'primary': primary, 'secondary': secondary, 'pairs': pairs}
