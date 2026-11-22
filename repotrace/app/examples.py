"""Ready-made examples for the RepoTrace app: a built-in suite plus the real projects we tried.

Each example has comparisons. A comparison names two versions, says in plain words what it shows,
and lists "try this" steps that the page can perform for you. Nothing here runs a project's code:
real projects are only read through Git, and the "edited copy" examples clone a project into a
temporary folder and commit one small, scripted edit there (the original is never touched).
"""
import os
import re
import subprocess
from pathlib import Path

from repotrace.changes.git_snapshot import GitError, resolve_commit

DESKTOP = Path.home() / 'Desktop'
CODEX = Path('/Users/israelhayalsew/.codex/.chatgpt-projects/g-p-6a5c4d518038819194ce0393bc39f22f/output/stress-tests/2026-10-23')
ALLOWED_ACTIONS = {'select', 'click', 'toggle', 'layers', 'open', 'read'}


def step(text, do='read', arg=None):
    return {'text': text, 'do': do, 'arg': arg}


# ------------------------------------------------------------------ the built-in suite

BASE_FILES = {
    'shop/__init__.py': '',
    'shop/decorators.py': ('import functools\n\n\ndef logged(func):\n    @functools.wraps(func)\n    def wrapper(*args, **kwargs):\n'
                           '        return func(*args, **kwargs)\n    return wrapper\n'),
    'shop/pricing.py': ('TAX = 1.15\n\n\ndef calculate_total(price):\n    return price * TAX\n\n\n'
                        'def old_discount(price):\n    return price * 0.9\n'),
    'shop/orders.py': ('from shop.decorators import logged\nfrom shop.pricing import calculate_total\n\n\n@logged\n'
                       'def place_order(price):\n    return calculate_total(price)\n\n\ndef receipt(price):\n'
                       '    return "$%.2f" % place_order(price)\n'),
    'shop/cart.py': ('from shop.pricing import calculate_total\n\n\nclass Cart:\n    def __init__(self):\n        self.items = []\n\n'
                     '    def add(self, price):\n        self.items.append(price)\n\n    def total(self):\n        total = 0\n'
                     '        for price in self.items:\n            total += calculate_total(price)\n        return total\n'),
    'shop/checkout.py': ('from shop.cart import Cart\nfrom shop.orders import receipt\n\n\ndef checkout(prices):\n    cart = Cart()\n'
                         '    for price in prices:\n        cart.add(price)\n    return cart.total()\n\n\n'
                         'def print_summary(cart_like):\n    return cart_like.total()\n\n\ndef final_receipt(price):\n    return receipt(price)\n'),
    'shop/legacy.py': 'import shop.pricing as pricing\n\n\ndef report(price):\n    return pricing.old_discount(price)\n'}

# Each step is (tag, commit message, {file: new text, or None to delete}).
SUITE = [
    ('demo-0', 'The shop as it starts', BASE_FILES),
    ('demo-1', 'calculate_total now rounds', {'shop/pricing.py': BASE_FILES['shop/pricing.py'].replace('return price * TAX', 'return round(price * TAX, 2)')}),
    ('demo-2', 'Add comments and a docstring (no code change)', {'shop/pricing.py': (
        '# Prices and tax.\nTAX = 1.15\n\n\ndef calculate_total(price):\n    """Price plus tax, rounded to cents."""\n    return round(price * TAX, 2)\n\n\n'
        'def old_discount(price):\n    return price * 0.9\n')}),
    ('demo-3', 'Raise the tax rate', {'shop/pricing.py': (
        '# Prices and tax.\nTAX = 1.2\n\n\ndef calculate_total(price):\n    """Price plus tax, rounded to cents."""\n    return round(price * TAX, 2)\n\n\n'
        'def old_discount(price):\n    return price * 0.9\n')}),
    ('demo-4', 'logged() now prints a line', {'shop/decorators.py': BASE_FILES['shop/decorators.py'].replace(
        '        return func(*args, **kwargs)', '        print("calling", func.__name__)\n        return func(*args, **kwargs)')}),
    ('demo-5', 'Remove old_discount (legacy.py still uses it)', {'shop/pricing.py': (
        '# Prices and tax.\nTAX = 1.2\n\n\ndef calculate_total(price):\n    """Price plus tax, rounded to cents."""\n    return round(price * TAX, 2)\n')}),
    ('demo-6', 'Cart.total now rounds', {'shop/cart.py': BASE_FILES['shop/cart.py'].replace('        return total\n', '        return round(total, 2)\n')}),
    ('demo-7', 'Add shop/labels.py (with a typo that stops it being read)', {'shop/labels.py': 'def render_label(text:\n    return text.upper()\n'}),
    ('demo-8', 'Fix the typo in shop/labels.py', {'shop/labels.py': 'def render_label(text):\n    return text.upper()\n'}),
    ('demo-9', 'Require currency, but leave the callers unchanged', {'shop/pricing.py':
        'TAX = 1.2\n\n\ndef calculate_total(price, currency):\n    return round(price * TAX, 2)\n'})]

BUILTIN_COMPARISONS = [
    {'id': 'function-changed', 'title': 'A function changed', 'aspect': 'The basics', 'base': 'demo-0', 'head': 'demo-1',
     'shows': ('calculate_total now rounds its result. RepoTrace finds the code that calls it. place_order and Cart.total call it '
               'directly, and those two links are Sure (the code text shows the call). checkout reaches it through Cart.total '
               'using an object whose type RepoTrace can only infer, so that link is Maybe (dashed).'),
     'steps': [step('Look at the picture: the changed function is on the right, the code that calls it fans out to the left.'),
               step('Click the box place_order to see why it is listed and the exact line.', 'click', 'place_order'),
               step('Click checkout: its line is dashed because the link rests on an assumption.', 'click', 'checkout'),
               step('Show only the direct callers by choosing "Direct".', 'layers', 1)]},
    {'id': 'comments-only', 'title': 'Only comments changed', 'aspect': 'Avoiding false alarms', 'base': 'demo-1', 'head': 'demo-2',
     'shows': ('Only a comment and a docstring were added. RepoTrace ignores comments, docstrings and blank lines, so it reports no '
               'changed function and sends you nowhere. A plain text diff would have flagged the file.'),
     'steps': [step('Read the summary line: no function changed.'), step('Open "What we could not see" to see what it says about ignored edits.', 'open', 'limits')]},
    {'id': 'value-changed', 'title': 'A value changed (not a function)', 'aspect': 'Changes that hide inside functions', 'base': 'demo-2', 'head': 'demo-3',
     'shows': ('Only the number TAX changed; no function text did. But calculate_total reads TAX, so its behaviour changed. RepoTrace '
               'lists calculate_total as changed "because it reads a changed value", then shows its callers as usual.'),
     'steps': [step('Open calculate_total and read the note: it uses a value that changed.', 'select', 'calculate_total'),
               step('Click place_order to see a caller of the affected function.', 'click', 'place_order')]},
    {'id': 'decorator-changed', 'title': 'A decorator changed', 'aspect': 'Changes that reach many functions', 'base': 'demo-3', 'head': 'demo-4',
     'shows': ('logged() wraps place_order, so every call to place_order now runs the new code first. RepoTrace finds the functions the '
               'decorator wraps (their own column) and lists the places that call them.'),
     'steps': [step('Find the column "Functions it wraps".'), step('Click place_order, the wrapped function.', 'click', 'place_order'),
               step('Open "More options" and turn on "Group by file" for the high-level view.', 'toggle', 'group')]},
    {'id': 'caller-forgotten', 'title': 'A function was removed, but a caller was forgotten', 'aspect': 'Catching mistakes', 'base': 'demo-4', 'head': 'demo-5',
     'shows': ('old_discount was deleted, but legacy.py still calls it. RepoTrace lists the caller from the older version and flags it: '
               '"head still references the removed name". This is a bug waiting to happen.'),
     'steps': [step('Open old_discount.', 'select', 'old_discount'), step('Click the box "report" and read the red flag.', 'click', 'report')]},
    {'id': 'possible-and-name', 'title': 'Maybe links and name-only links', 'aspect': 'How sure RepoTrace is', 'base': 'demo-5', 'head': 'demo-6',
     'shows': ('Cart.total changed. checkout calls it on an object it created itself, so RepoTrace can infer the type: a Maybe link '
               '(dashed). print_summary calls cart_like.total() on a parameter of unknown type, so it can only be matched by name: a '
               'Name-only link (dotted), the weakest evidence.'),
     'steps': [step('Click checkout (dashed = maybe).', 'click', 'checkout'), step('Click print_summary (dotted = name only).', 'click', 'print_summary'),
               step('Open "More options" and switch off the "Maybe" line filter to see only the strongest evidence.', 'toggle', 'possible')]},
    {'id': 'unreadable-file', 'title': 'A file that cannot be read', 'aspect': 'Honesty about gaps', 'base': 'demo-6', 'head': 'demo-7',
     'shows': ('A new file has a typo, so Python cannot parse it. RepoTrace does not crash and does not pretend: the analysis is marked '
               '"partly complete" and "What we could not see" lists the file. A file that fails to parse is never treated as deleted or safe.'),
     'steps': [step('Open "Limits" and find the unreadable file.', 'open', 'limits')]},
    {'id': 'file-repaired', 'title': 'The unreadable file is repaired', 'aspect': 'Honesty about gaps', 'base': 'demo-7', 'head': 'demo-8',
     'shows': ('The file parses now, but RepoTrace has no readable older version to compare with, so render_label is listed as new with a '
               'warning: "the file could not be read before, so this may not really be new".'),
     'steps': [step('Open render_label and read the note.', 'select', 'render_label')]},
    {'id': 'parameters-changed', 'title': 'A required parameter was added, but callers were forgotten',
     'aspect': 'Catching mistakes', 'base': 'demo-8', 'head': 'demo-9',
     'shows': ('calculate_total now requires currency. place_order and Cart.total still supply only price. '
               'Calls to update shows both locations and why their arguments no longer fit. '
               'This assumes each call runs the function RepoTrace traced it to; it does not cover every runtime behavior.'),
     'steps': [step('Select calculate_total and open each entry under Calls to update.', 'select', 'calculate_total'),
               step('Read What this parameter check covers. Uncertain targets and *args/**kwargs are not guessed.')]}]


def make_builtin_suite(parent):
    """Create the tagged example repository and return its path."""
    repo = Path(parent) / 'example-shop'
    repo.mkdir(parents=True)
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'Example', 'GIT_AUTHOR_EMAIL': 'example@example.com',
           'GIT_COMMITTER_NAME': 'Example', 'GIT_COMMITTER_EMAIL': 'example@example.com',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}

    def git(*args):
        subprocess.run(['git', '-C', str(repo), *args], env=env, check=True, capture_output=True)
    git('init', '-q', '-b', 'main')
    for tag, message, files in SUITE:
        for name, text in files.items():
            path = repo / name
            if text is None:
                path.unlink(missing_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        git('add', '-A')
        git('commit', '-q', '-m', message)
        git('tag', tag)
    return repo


# ------------------------------------------------------------------ real projects we tried

def tag_range(a, b):
    return f'step-{a:02d}', f'step-{b:02d}'


def cmp(id_, title, aspect, base, head, shows, steps, roots=None):
    return {'id': id_, 'title': title, 'aspect': aspect, 'base': base, 'head': head, 'shows': shows, 'steps': steps,
            'source_roots': roots or []}


REAL_PROJECTS = [
    {'id': 'shopkeep', 'title': 'Shopkeep', 'kind': 'existing', 'paths': [DESKTOP / 'Test Project'],
     'blurb': 'A small shop-management app (about 3,000 lines) written as a test bed, with 11 tagged commits. The answer key says what really changed.',
     'comparisons': [
         cmp('rounding', 'A helper used in many places', 'The basics', *tag_range(1, 2),
             'round_money changed. Many functions use it, some directly (Sure) and others through objects whose type is inferred (Maybe).',
             [step('Open round_money and count the columns to its left.', 'select', 'round_money'), step('Choose "Direct" to see only the direct callers.', 'layers', 1)], ['src']),
         cmp('comments', 'Only comments changed', 'Avoiding false alarms', *tag_range(2, 3),
             'Docstrings and comments changed in two files. RepoTrace reports no changed function.', [step('Read the summary line.')], ['src']),
         cmp('constant', 'A value changed', 'Changes that hide inside functions', *tag_range(3, 4),
             'DEFAULT_REORDER_LEVEL changed. Two functions read it, so both are listed as changed because they read a changed value.',
             [step('Open create_product and read the note.', 'select', 'InventoryService.create_product')], ['src']),
         cmp('forgotten', 'A function removed, a caller forgotten', 'Catching mistakes', *tag_range(5, 6),
             'month_label was removed. One caller was updated; scripts/export_report.py still calls it. RepoTrace flags exactly that caller.',
             [step('Open month_label.', 'select', 'month_label'), step('Click the box "run" and read the red flag.', 'click', 'run')], ['src']),
         cmp('override', 'A method changed in one subclass', 'How sure RepoTrace is', *tag_range(8, 9),
             'BulkDiscount.apply changed. It is called through a base class, so which subclass runs depends on the data: the link is Maybe, never Sure.',
             [step('Click the dashed link.', 'read')], ['src']),
         cmp('broken', 'A file that cannot be read', 'Honesty about gaps', *tag_range(9, 10),
             'A new file does not parse. The analysis is marked partly complete and "What we could not see" names the file.', [step('Open "What we could not see".', 'open', 'limits')], ['src']),
         cmp('repaired', 'The file is repaired', 'Honesty about gaps', *tag_range(10, 11),
             'The file parses now; its functions are listed as new with a warning that they may not really be new.', [step('Read the note on the new function.')], ['src'])]},
    {'id': 'ledgerly', 'title': 'Ledgerly', 'kind': 'existing', 'paths': [DESKTOP / 'test project 2'],
     'blurb': 'A personal-finance ledger (about 3,000 lines) with operator overloading, decorators and string-based dispatch, with 11 tagged commits.',
     'comparisons': [
         cmp('quantize', 'A helper used almost everywhere', 'The basics', *tag_range(1, 2),
             'Money.quantize runs inside every Money value, so nearly everything is affected. RepoTrace shows the layers; many links are Maybe because they go through objects.',
             [step('Choose Direct, then 2 steps, then All to watch the circle widen.', 'layers', 1)]),
         cmp('constant', 'A value changed', 'Changes that hide inside functions', *tag_range(3, 4),
             'MAX_PAYEE_LEN changed; three functions read it and are listed as changed.', [step('Open one of the changed functions and read the note.')]),
         cmp('parameter', 'A parameter was added to a method', 'How sure RepoTrace is', *tag_range(4, 5),
             'Money.format got a new optional parameter. Many callers cannot be traced exactly, so they appear as name-only links, the weakest evidence.',
             [step('Look for dotted lines (name only).')]),
         cmp('rename', 'A class was renamed', 'The basics', *tag_range(5, 6),
             'RuleEngine became RuleSet. Renames are shown as removed plus added, and every use that changed is listed.',
             [step('Use the filter at the top left to show only "removed".')]),
         cmp('dynamic', 'What RepoTrace cannot see', 'Honesty about gaps', *tag_range(7, 8),
             'Evaluator.visit_Not is only reached through getattr(self, "visit_" + name). Reading the code cannot see that, and RepoTrace says so rather than guessing.',
             [step('Open "What we could not see" and read how far to trust the result.', 'open', 'limits')]),
         cmp('decorator', 'A decorator changed', 'Changes that reach many functions', *tag_range(8, 9),
             'logged() now counts calls. It wraps three functions, so their callers are affected even though their code did not change.',
             [step('Find the column "Functions it wraps".'), step('Group by file for the high-level view.', 'toggle', 'group')])]},
    {'id': 'queuely', 'title': 'Queuely', 'kind': 'existing', 'paths': [DESKTOP / 'test project 3'],
     'blurb': 'A background-job queue with a small web-style API, decorators, a dependency container and plugins, with 11 tagged commits and an author answer key.',
     'comparisons': [
         cmp('clamp', 'A small helper used in many places', 'The basics', *tag_range(1, 2),
             'clamp() was rewritten. Four functions call it directly (Sure); the callers of those come after, some only as Maybe.',
             [step('Open clamp and count the columns to its left.', 'select', 'clamp'), step('Choose "Direct" to see only the direct callers.', 'layers', 1)]),
         cmp('comments', 'Only comments changed', 'Avoiding false alarms', *tag_range(2, 3),
             'Comments, docstrings and blank lines changed in two files. RepoTrace reports no changed function.', [step('Read the summary line.')]),
         cmp('constant', 'A value changed', 'Changes that hide inside functions', *tag_range(3, 4),
             'DEFAULT_MAX_ATTEMPTS changed from 3 to 4. Three functions read it, so each is listed as changed because it reads a changed value.',
             [step('Open parse_job_payload and read the note.', 'select', 'parse_job_payload')]),
         cmp('decorator', 'A decorator changed', 'Changes that reach many functions', *tag_range(5, 6),
             'The @task decorator changed. Functions it wraps (such as fib) have callers that are affected even though their own code did not change.',
             [step('Find the column "Functions it wraps".')]),
         cmp('moved', 'A function moved, another renamed', 'Catching mistakes without false alarms', *tag_range(8, 9),
             'In this update the function format_duration was moved to a different file, and short was renamed to short_id. Moves and renames are risky: any code that still uses the old name or place would break. RepoTrace shows each one as a single item and checks for leftovers.',
             [step('Read the "What happened" box at the top. It says it all in a few sentences.'), step('Open format_duration: the boxes on the left are the places that use it.', 'select', 'format_duration')]),
         cmp('broken', 'A file that cannot be read', 'Honesty about gaps', *tag_range(9, 10),
             'A new plugin file does not parse. The analysis is marked partly complete and "What we could not see" names the file.', [step('Open "What we could not see".', 'open', 'limits')]),
         cmp('repaired', 'The file is repaired', 'Honesty about gaps', *tag_range(10, 11),
             'The file parses now; its functions are listed as new with a warning that they may not really be new.', [step('Read the note on the new function.')])]},
]

# Projects with one commit only: clone to a temporary folder and commit a small scripted edit there.
EDIT_PROJECTS = [
    {'id': 'libtrack', 'title': 'LibTrack (with one edit)', 'kind': 'edited-copy', 'paths': [CODEX / 'libtrack'],
     'blurb': 'A small library-management app. It has no history, so we copy it to a temporary folder and edit one helper there. Your copy is never touched.',
     'edits': [{'file': 'utils.py', 'find': 'def clear_screen():', 'replace': 'def clear_screen():\n    pass  # example edit', 'message': 'Edit clear_screen', 'tag': 'try-1'}],
     'comparisons': [cmp('helper', 'A small helper used from many menus', 'The basics', 'HEAD~1', 'HEAD',
                         'clear_screen is called from many menu functions. RepoTrace lists them all as Sure callers, then the places that call those.',
                         [step('Group by file to see which files depend on it.', 'toggle', 'group')])]},
    {'id': 'flask', 'title': 'Flask (with two edits)', 'kind': 'edited-copy', 'paths': [CODEX / 'flask'],
     'blurb': 'The well-known Flask web framework (about 18,000 lines), copied to a temporary folder with two small edits. A realistic, much larger project.',
     'edits': [{'file': 'src/flask/helpers.py', 'find': '"0", "false", "no"}', 'replace': '"0", "false", "no", "off"}', 'message': 'Edit get_debug_flag', 'tag': 'try-1'},
               {'file': 'src/flask/sansio/scaffold.py', 'find': '        return self._method_route("GET", rule, options)',
                'replace': '        options = dict(options)\n        return self._method_route("GET", rule, options)', 'message': 'Edit Scaffold.get', 'tag': 'try-2'}],
     'comparisons': [
         cmp('debug-flag', 'A function in a big framework', 'The basics', 'HEAD~2', 'HEAD~1',
             'get_debug_flag has a handful of direct callers in a real framework. Open the "Explore its map" button on the first screen to follow them outward.',
             [step('Click a box to see why it is listed.')], ['src']),
         cmp('common-name', 'A very common method name', 'How sure RepoTrace is', 'HEAD~1', 'HEAD',
             'Scaffold.get shares its name with dict.get and many others. RepoTrace hides name-only links for names that common, because most would be noise, and tells you so.',
             [step('Read the note about the hidden name-only links.')], ['src'])]}]

ALL_EXAMPLES = [{'id': 'builtin', 'title': 'Example shop (built in)', 'kind': 'builtin', 'paths': [],
                 'blurb': 'A tiny shop with a tagged history. Every comparison is built to show one feature clearly. Start here.',
                 'comparisons': [{**c, 'source_roots': []} for c in BUILTIN_COMPARISONS]}] + REAL_PROJECTS + EDIT_PROJECTS


def project_path(example):
    for candidate in example.get('paths', []):
        if (Path(candidate) / '.git').exists():
            return Path(candidate)
    return None


def _resolves(repo, ref):
    try:
        resolve_commit(repo, ref)
        return True
    except GitError:
        return False


def edit_applicable(source, edits):
    """True if every scripted edit's search text exists in the project."""
    for edit in edits:
        try:
            if edit['find'] not in (Path(source) / edit['file']).read_text(encoding='utf-8'):
                return False
        except OSError:
            return False
    return True


def catalog():
    """What the first screen offers: only examples that can actually run on this computer."""
    out = []
    for example in ALL_EXAMPLES:
        entry = {k: example[k] for k in ('id', 'title', 'kind', 'blurb')}
        if example['kind'] == 'builtin':
            entry['comparisons'] = example['comparisons']
        else:
            source = project_path(example)
            if source is None:
                continue
            if example['kind'] == 'existing':
                keep = [c for c in example['comparisons'] if _resolves(source, c['base']) and _resolves(source, c['head'])]
            else:
                keep = example['comparisons'] if edit_applicable(source, example['edits']) else []
            if not keep:
                continue
            entry['comparisons'] = keep
        out.append(entry)
    return out


def prepare(example_id, workdir):
    """Make the example's repository ready and return its path (a temporary copy where needed)."""
    example = next((e for e in ALL_EXAMPLES if e['id'] == example_id), None)
    if example is None:
        raise KeyError(example_id)
    if example['kind'] == 'builtin':
        return make_builtin_suite(Path(workdir) / example_id)
    source = project_path(example)
    if source is None:
        raise GitError('This example project is not on this computer.')
    if example['kind'] == 'existing':
        return source
    dest = Path(workdir) / example_id
    dest.mkdir(parents=True)
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'Example', 'GIT_AUTHOR_EMAIL': 'example@example.com',
           'GIT_COMMITTER_NAME': 'Example', 'GIT_COMMITTER_EMAIL': 'example@example.com',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}
    clone = dest / 'copy'
    subprocess.run(['git', 'clone', '-q', '--no-hardlinks', str(source), str(clone)], env=env, check=True, capture_output=True)
    for edit in example['edits']:
        path = clone / edit['file']
        text = path.read_text(encoding='utf-8')
        if edit['find'] not in text:
            raise GitError('The example edit does not apply to this version of the project.')
        path.write_text(text.replace(edit['find'], edit['replace'], 1), encoding='utf-8')
        subprocess.run(['git', '-C', str(clone), 'commit', '-q', '-a', '-m', edit['message']], env=env, check=True, capture_output=True)
        subprocess.run(['git', '-C', str(clone), 'tag', edit['tag']], env=env, check=True, capture_output=True)
    return clone
