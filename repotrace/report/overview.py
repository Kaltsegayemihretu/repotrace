"""A first look at a whole project, in plain words: how big it is, what is used most, where things start.

Works from a finished map (a GraphIndex and its Explorer), so it never reads source code itself.
"""

from repotrace.graph.graph_explore import is_test_file

# Where each kind of call ends up, in the words the screen uses.
SURE, MAYBE = ('internal_linked',), ('internal_possible',)
OUTSIDE = ('builtin_reference', 'builtin_object_method', 'external_or_missing')   # Python's own or other people's code


def _top(items, key, limit):
    return sorted(items, key=key)[:limit]


def build_overview(index, explorer, name, limit=8):
    """Numbers and starting points for a project map."""
    functions = {i: s for i, s in index.symbols.items() if s['kind'] != 'CLASS'}
    classes = sum(1 for s in index.symbols.values() if s['kind'] == 'CLASS')
    own = {i: s for i, s in functions.items() if not is_test_file(s['file'])}
    # Only callers in the project's own code count as users; a function that only tests call is not really used.
    used_by = {i: sum(1 for pair in index.callers_of(i) if not is_test_file(index.symbols[pair['source']]['file'])) for i in own}
    uses = {i: len(explorer.forward.get(i, ())) for i in own}
    busiest = _top([i for i in own if used_by[i]], lambda i: (-used_by[i], own[i]['qualified_name']), limit)
    # Functions nothing in the project calls but that call several others: where a program or a task begins.
    starters = _top([i for i in own if not used_by[i] and uses[i] >= 2], lambda i: (-uses[i], own[i]['qualified_name']), limit)
    per_file = {}
    for s in own.values():
        per_file[s['file']] = per_file.get(s['file'], 0) + 1
    biggest = _top(per_file.items(), lambda kv: (-kv[1], kv[0]), 6)

    c = index.coverage
    categories = c.get('categories', {})
    count = lambda names: sum(categories.get(n, 0) for n in names)
    sure, maybe, outside = count(SURE), count(MAYBE), count(OUTSIDE)
    untraced = max(0, c['calls'] - sure - maybe - outside)
    candidates = sure + maybe + untraced            # calls that could have gone to the project's own code
    return {
        'name': name,
        'files': c['files_analyzed'], 'functions': len(functions), 'classes': classes, 'tests': len(functions) - len(own),
        'calls': {'total': c['calls'], 'sure': sure, 'maybe': maybe, 'outside': outside, 'untraced': untraced,
                  'not_found': categories.get('import_not_found', 0),
                  'traced_percent': round(100 * (sure + maybe) / candidates) if candidates else None},
        'busiest': [{**explorer.node(i), 'used_by': used_by[i]} for i in busiest],
        'starters': [{**explorer.node(i), 'uses': uses[i]} for i in starters],
        'biggest_files': [{'file': f, 'functions': n} for f, n in biggest]}


class SnapshotReader:
    """Read function code as it was when the map was made, never from whatever is on disk now."""

    def __init__(self, sources):
        self.lines_by_file = {file: text.splitlines() for file, text in sources.items()}

    def lines(self, path, first, last):
        rows = self.lines_by_file.get(path)
        return None if rows is None else {'first': first, 'lines': rows[max(first, 1) - 1:last]}
