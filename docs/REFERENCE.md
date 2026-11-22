# RepoTrace

> **Technical reference.** For a short tour start with the [README](../README.md) and [ARCHITECTURE](ARCHITECTURE.md). Module names below (for example `resolver.py`) are files inside `repotrace/<layer>/`; ARCHITECTURE.md says which layer each one lives in.


See **GUIDE.md** for how to run each project we tried and what every example shows.

## Quick start (no commands to remember)

1. **Double-click `RepoTrace.command`** in Finder (or run `python3 -m repotrace` in a terminal).
   Your browser opens the app. Nothing leaves your computer; it only reads Git history and never runs your code.
2. Click one of the **examples** on the first screen (each has a plain explanation and "Try this" steps), or choose your own project folder.
3. Pick the **Before** and **After** versions (the default is "the last commit") and click **Analyze**.
4. The main view is a **picture**: the changed function on the right, its callers fanning out to the left in
   columns (uses it directly, 2 steps away, ...). Solid line = sure, dashed = maybe, dotted = name only.
   Click any box to see why it is there and the exact line of code. Use **Show: Direct / 2 steps / 3 steps / All**,
   **Hide test code**, and under **More options** the line filters and **Group by file** (a file-level map).
5. Open the **"Explore any function"** tab to search for any function and expand "who calls this" and
   "what this calls", one layer at a time.
6. Use **Save as one file** to keep a copy (the picture and details, without Explore) you can open later without the app.

To stop it, close the terminal window (or press Ctrl+C).


A small analyzer for one file or a project folder. Requires Python 3.9+ and no third-party packages.
It reads source; it never imports or executes the analyzed file.

From this folder:

```sh
python3 -m repotrace.reading.analyzer tests/fixtures/example.py --root . --output report.json --overwrite
python3 -m unittest discover -s tests -t . -v
```

Read any other Python file:

```sh
python3 -m repotrace.reading.analyzer /full/path/to/file.py --output report.json
```

The report contains source and file metadata, definitions and signatures, imports,
call sites, containment edges, and diagnostics. Existing output files are only
replaced when you add `--overwrite`. Outputs must end in `.json`; source files,
symbolic-link outputs and hard-linked outputs are protected.
Exit status is 0 for successful extraction, 1 for analysis errors, and 2 for CLI errors.

## New: read a whole folder

Start with the included four-file example:

```sh
python3 -m repotrace map samples/demo_repository --output repository_report.json
```

On later runs, explicitly permit replacement of that generated report:

```sh
python3 -m repotrace map samples/demo_repository --output repository_report.json --overwrite
```

Expected: **4 files analyzed, 6 symbols, 3 calls: 3 linked and 0 unresolved**.
`users/utils.py` and `payments/utils.py` now have separate identities, despite
sharing filenames and function names. The call in `orders.py` links to
`calculate_total` in `pricing.py` by following its explicit import.
This small example is a correctness check, not a coverage estimate for real projects.

`repository.py` finds files and calls the existing `analyze_file` for each one.
The combined JSON has:

- `summary`: file, symbol and call counts.
- `files`: one full report per Python file, including source and diagnostics.
- `skipped`: excluded directories and links, with reasons.
- `diagnostics`: input and directory-reading problems.
- `settings` and `limitations`: what this scan includes and does not do.
- `project_index`: module names and definitions collected across all analyzed files.

Open `files`, then inspect each entry's `file`, `symbols`, `imports` and `calls`.
Paths are relative to the chosen folder, using `/` separators. These identities
belong to this root/snapshot; they are not globally unique or stable across edits.
Without `--root`, the single-file reader uses a canonical absolute path instead.

Scan another project:

```sh
python3 -m repotrace map /path/to/project --output another_report.json
```

The reader skips common environment/cache/build folders such as `.venv`, `venv`,
`env`, `.git`, `__pycache__`, `node_modules`, `build` and `dist`. The full list is
in the report. `--exclude-dir vendor` adds a directory name to skip at every depth.
Gitignore rules are not interpreted. Tests and other ordinary `.py` files are included.
Symlinks are recorded and not followed. A skipped directory counts as one skipped
entry, not as a count of all files hidden underneath it.

One broken file does not stop the other files. `status` is `partial` when some
files are usable and others fail (or a directory cannot be read), `error` when
there is no usable result after an error, and `ok` when the in-scope scan succeeds.
An empty folder is `ok` with a `NoPythonFiles` diagnostic. Intentionally excluded
folders do not make the result partial. Exit status is 0 for `ok`, 1 for `partial`
or `error`, and 2 for CLI/output errors.

Files over 2 MiB are reported as failed by default; adjust with
`--max-file-bytes 4194304`. This bounds each input read, not all CPU/memory use.
This is not a sandbox for hostile repositories. It reads the working folder;
it does not compare Git commits or import/execute the target code.

Single-file report schema is now `0.6`; repository wrapper schema is `0.3`.
Impact output schema is `0.2`. The impact reader accepts both repository/file
`0.2/0.5` and `0.3/0.6`; re-run analysis to obtain the new evidence and candidates.

## Follow a call into another file

The repository reader now makes two passes:

1. Collect definitions, imports and calls from all included files.
2. Follow supported imports using the collected definitions and add call links.

For the demo:

```text
orders.py: place_order()
    calls calculate_total()
    import says: from pricing import calculate_total
    definition found: pricing.py: calculate_total()
```

In `repository_report.json`, find the call with `"expression": "calculate_total"`.

| Field | What it tells you |
|---|---|
| `enclosing_symbol_id` | Which function contains the call |
| `target_id` | One supported internal target; `null` means no LIKELY target |
| `target_file` | Destination file for an import link |
| `resolution_kind` | `local` or `import` when linked |
| `resolution` | `LIKELY`, `POSSIBLE` or `UNRESOLVED` |
| `candidate_targets` | Possible internal targets with receiver evidence; only for POSSIBLE calls |
| `import_evidence` | Import statement and its source location |
| `reason` | Explanation of the result |
| `reason_code` | Short code for a project-resolution decision |

The summary separates local links from import links. The demo has two local links
and one import link. The same links are exported as `CALLS` edges for a future graph.

Supported imports include `from pricing import calculate_total`, aliases such as
`from pricing import calculate_total as total`, module calls such as
`import pricing as p; p.calculate_total(...)`, explicit submodule imports, and
relative imports when the package context is known. Supported targets are ordinary
or async module-level function definitions.

This is conservative: overwritten names, conditional imports, decorated functions,
ambiguous modules and broken target files stay unresolved. Explicit imported-module
attribute writes such as `pricing.calculate_total = replacement` invalidate links
to that target. Calls are kept in the report even when they cannot be linked.

`LIKELY` means a potential target under selected source roots and ordinary
initialized-module bindings. It does not prove that execution reaches the call,
that the import succeeds at runtime, or that a change will break its caller.
Circular initialization timing, import hooks, runtime path changes, arbitrary
alias-based mutation, `exec` and `setattr` are not simulated. Built-in and frozen
module checks use the interpreter running the analyzer, not a separately configured
target environment.

### Projects with a src folder

By default the selected project folder is the import base. If the project imports
modules from `src`, declare that:

```sh
python3 -m repotrace map /path/to/project --source-root src --output src_report.json
```

`--source-root` is repeatable and replaces the default `.` root. Each root must be
an existing directory inside the project. These roots control import lookup, not
file discovery. When two files provide the same module name, this first version
reports ambiguity instead of selecting one by search-path order.

Regular packages and namespace-package directories are recognized conservatively.
A namespace entry has no module file; that is intentional. Explicit package
re-export chains are followed, including aliases and supported package initializer
imports. Chains have a depth bound of 32 and cycle detection.

To compare against the previous local-only behavior:

```sh
python3 -m repotrace map samples/demo_repository --local-only --output local_only_report.json
```

That demo result is two links and one unresolved call. The standalone `analyzer.py`
command still has only single-file context.

## Read the code in this order

1. `repository.py` / `analyze_repository`: collects files and coordinates both passes.
2. `analyzer.py` / `analyze_file`: reads, decodes, parses, and reports errors.
3. `CodeExtractor`: collects definitions, imports and calls.
4. `resolver.py`: links supported calls inside a file using their scopes.
5. `project_resolver.py`: follows supported imports between files.
6. `main`: accepts command-line options and writes JSON safely.

## What to inspect first

In report.json, find `place_order` in symbols, then find the call expression
`calculate_total` in calls. Its enclosing_symbol_id points to place_order.
The calculate_total and place_order calls now have target IDs and CALLS edges.
print remains UNRESOLVED because it is not a top-level function defined in this file.
The resolution_summary reports two linked calls and one unresolved call.

resolver.py builds separate name records for the module, classes, functions and
lambdas. Bare calls search their local scope, enclosing function scopes, then the
module. Class namespaces are skipped for bare-name lookup inside methods.
An unrelated function's parameter or local import no longer blocks another call.
Nested functions, recursion, and read-only global/nonlocal declarations are supported.
Writes through global/nonlocal invalidate the affected binding conservatively.
Duplicate bindings, local reassignment, decorated targets and conditional
definitions remain unresolved with specific explanations. Imports remain unresolved
in single-file mode; repository mode can resolve supported imports.
Annotation-only module statements do not replace a binding. Global definitions
created inside another function are not assumed to exist already.

Try the scope demonstration:

```sh
python3 -m repotrace.reading.analyzer samples/scope_example.py --root . --output scope_report.json --overwrite
```

Expected: first calls save; second's save is an unknown parameter;
outer.inner calls outer.helper; outer calls outer.inner. Three linked, one unresolved.
Comprehension calls are deliberately unresolved, but their loop variables no longer
block ordinary calls outside the comprehension. Full execution-order analysis,
generic type-parameter scopes remain unsupported. Repository mode adds method
candidates separately; single-file mode still has only local name resolution.
LIKELY refers to the target under ordinary static bindings, not guaranteed execution
or breakage. External monkey-patching and dynamic namespace mutation remain unknown.

`required` describes the declared parameter, so self has no default even though
Python supplies it automatically for bound method calls. AST columns are UTF-8
byte offsets. IDs distinguish occurrences within one snapshot; they are not stable
identities across commits. Source is retained, including comments and assignments,
but these are not separate summary records yet.

## Next milestones

The first project-wide definition index, explicit-import resolver, and manual
function impact query are implemented. Next: compare two Git snapshots and feed
the changed functions into the same impact query. Git comparison is not implemented yet.

Wildcard imports, general dynamic dispatch, calls outside supported function
contexts, comprehension bindings, annotation evaluation and hostile-input resource
isolation remain future work. Method and constructor candidates are available in
repository mode with the limits described below.

Run `python3 -B -m unittest discover -s tests -t . -v` to check successful links and cases that must remain
unresolved. If the project resolution pass fails with a handled analysis error,
its partial changes are rolled back and usable local results remain available.

Semantic references: [Python's import system](https://docs.python.org/3/reference/import.html)
and [execution model](https://docs.python.org/3/reference/executionmodel.html).

## New: what might be affected if I change this function?

Start with the separate three-file impact demo:

```sh
python3 -m repotrace map samples/demo_impact --output impact_repository_report.json
python3 -m repotrace impact impact_repository_report.json --file pricing.py --function calculate_total --output impact_report.json
```

If those output files already exist, add `--overwrite` to the respective command.
The original folder-reading demo and its expected counts have not changed.

Expected result:

```text
Selected: pricing.py / calculate_total
Direct caller: orders.py / place_order
Indirect caller: checkout.py / checkout

Reason: checkout → place_order → calculate_total
```

These are functions worth reviewing or testing, not functions proven to break.
The selected function itself is not counted among affected callers, even when it
calls itself. `affected_files` lists caller files, not the selected file unless it
also contains an affected caller.

Flow: saved repository report → reverse call index → selected function → callers
and their callers → impact JSON. Impact analysis reads the saved report; it does
not reread source files. Regenerate the repository report after source changes.

### Select the right function

Use the exact report-relative file path and qualified name (e.g. `UserService.save`).
If a name is ambiguous, the command asks you to choose an exact ID:

```sh
python3 -m repotrace impact impact_repository_report.json --list-functions
python3 -m repotrace impact impact_repository_report.json --list-functions --file orders.py
python3 -m repotrace impact impact_repository_report.json --symbol "pricing.py::calculate_total@1:0"
```

IDs are snapshot-local. Never reuse a saved ID blindly after a new analysis.
Classes are not selectable for this function-impact feature. Methods may be selected;
use `--include-possible` to review candidate callers. Unknown calls still leave gaps.

### Read the result

- `target`: the selected function.
- `affected`: each caller, its file, and its distance from the selected function.
- `relationship`: `direct` for distance 1; `indirect` for longer chains.
- `next_toward_target`: the next function on one shortest explanation chain.
- `witness_call`: a call location supporting that step, its resolution, and import
  evidence when available. `call_site_count` counts repeated calls for this pair.
- `coverage`: whole-report counts of linked/unresolved calls, failed files and
  skipped entries. These are not accuracy percentages.
- `traversal`: query limits, inspected call pairs, and whether the recorded graph
  was fully traversed within those limits.
- `warnings`: uncertainty, missing coverage and any truncation.

Only call relationships are traversed. Imports alone, class containment, shared data,
inheritance and Git co-change are not treated as impact dependencies here.
A result with no callers does not mean a function is unused or safe to change.

### Keeping larger results manageable

```sh
python3 -m repotrace impact impact_repository_report.json --file pricing.py --function calculate_total --max-depth 10 --max-nodes 500 --max-edges 20000
```

Defaults: depth 20, 1,000 affected functions, and 100,000 inspected caller pairs.
Depth 0 returns no callers. A boundary produces `status: "limited"` and names the
limit reached. Depth boundaries are conservative: they may include callers already
visited, so a warning can occur even when no additional distinct caller would be found.

`status: "ok"` means the query finished over the recorded graph, not that every real
dependency is known. Partial source analysis is shown separately in
`source_report_status` and warnings. CLI exit codes: 0 for a completed query/list,
1 for a limit-bound result, 2 for invalid input/output or analysis errors.

JSON input is limited to 64 MiB by default (`--max-report-bytes`). Index limits are
200,000 symbols and 1,000,000 call records (`--max-index-symbols`, `--max-index-calls`).
The report is loaded into memory before indexing. These guards do not make the
program a hostile-input sandbox or establish production-scale capacity.

## Extension boundaries

### Methods, constructors and observed object arguments

Repository analysis now collects **POSSIBLE** targets for ordinary `self` / `cls`
calls, constructor variables and simple aliases, imported/re-exported classes,
simple class annotations, built-in static/class methods, known inheritance and
zero-argument `super()`. Construction can point to `__init__` / `__new__`; callable
instances can point to `__call__`. These represent potential functions involved
in dispatch; they do not prove a particular invocation succeeds.

`argument_flow.py` also follows observed object arguments into ordinary functions:

```python
def process(service):
    service.save()

def start():
    process(Service())
```

The constructor and argument location provide evidence for a possible
`Service.save` target. Forwarding through other functions is bounded. Conflicting
observed classes suppress a hint. Unknown callers may still pass other types, so
this never upgrades a method call to LIKELY. A narrow module `__main__` guard
pattern is supported as an observation, while module calls themselves remain
outside the function-to-function impact graph.

Each candidate has a target ID, reason and `evidence.steps` showing the observed
receiver, argument passing and method declaration. Type hints are suggestions;
Python does not enforce them at runtime. Matching a method name alone is never
used to infer an object type.

Try the original small management example (no third-party packages needed):

```sh
python3 -m repotrace map samples/demo_management --output management_report.json
python3 -m repotrace impact management_report.json --file library/service.py --function BaseStore.save --include-possible
```

Expected candidate review chain:
`run → process_checkout → LibraryService.checkout → BaseStore.save`.
The unknown `plugin.run()` remains unresolved. Without `--include-possible`, the
impact query follows only LIKELY links and does not discover this method chain.
Add `--overwrite` only when replacing an existing generated report.

Default limits: 8 candidates per call, method inference depth 16, argument-flow
rounds 16, 50,000 argument observations, 10,000 tracked parameters and 12 evidence
steps per witness. The argument pass drops new hints if its computation limits
are reached. Its statistics/limits appear under `project_index.argument_flow`.
Candidate truncation appears on calls and in impact coverage/warnings. These
bounds limit inference; they are not a complete CPU/memory sandbox.

Conservative exclusions include unknown/external base classes, decorated classes,
custom metaclasses/attribute interception, properties/custom method decorators,
competing receiver bindings and detected method mutation. Arbitrary reflection,
`__dict__` edits, factories returning objects, object-field type propagation,
string/union type hints and dynamic dependency injection are not generally modeled.

### Reading the new counts and integrating other modules

- `linked_calls`: calls with one LIKELY internal target.
- `possible_calls`: call sites with one or more candidate targets, counted once.
- `unknown_target_calls`: calls with neither of those internal results.
- Legacy `unresolved_calls`: all calls without a LIKELY target, including POSSIBLE.
- `call_categories`: separates internal results, unshadowed builtin references,
  external-or-missing modules, unsupported contexts and other unresolved calls.

The first three counts add up to total calls. A builtin or missing-module category
does not establish an internal graph target. Percentages of all calls measure
coverage of recorded internal links, not correctness or recall.

Existing Git integration can keep `GraphIndex(report)` for the LIKELY-only graph.
Candidate exploration must explicitly use `GraphIndex(report, include_possible=True)`.
Use call records as the source of truth: `CALLS` edges duplicate LIKELY records and
`POSSIBLE_CALLS` duplicate candidate records. Do not silently merge those edge types.
Impact output states `resolution_policy`, and each chosen witness chain has
`path_has_possible_link`. Only one shortest witness is retained, not all alternative
paths; that flag describes the chosen path. IDs remain snapshot-specific, so Git
change matching must map definitions between snapshots before querying each graph.

The added modules have small boundaries: `method_resolver.py` suggests method
targets, `argument_flow.py` supplies observed parameter types, and
`resolution_summary.py` classifies/counts results. Neither the dashboard nor Git
comparison needs to reimplement these rules.

| File | Responsibility | Change it when… |
|---|---|---|
| `analyzer.py`, `resolver.py`, `project_resolver.py` | Extract and resolve code relationships | Improving static-analysis coverage |
| `graph_index.py` | Validate report schema and build symbol/caller lookups | Adding a new report schema or a database-backed graph adapter |
| `impact.py` | Bounded backward traversal and explanation chains | Adding impact rules or query options |
| `impact_cli.py` | Read JSON, accept selections, save output | Changing command-line behavior |
| `tests/test_impact.py` | Graph, integration, CLI and size-regression tests | Extending any of the above |

The adapter deliberately uses validated `calls` records as its source of truth,
not the report's redundant edge list. It rejects dangling targets and unsupported
schema versions instead of silently producing misleading results.

Build the index once and reuse it for multiple queries:

```python
from graph_index import GraphIndex
from impact import analyze_impact, explain_path
from impact_cli import load_report

report = load_report("impact_repository_report.json")
index = GraphIndex(report)
del report  # The index retains graph metadata, not source text.
target = index.select(file="pricing.py", qualified_name="calculate_total")
result = analyze_impact(index, target)
caller = result["affected"][-1]["symbol"]["id"]
print(explain_path(result, caller))
```

The traversal uses a queue and a visited set, not recursive calls. A reverse index
avoids scanning every edge again for each function. Repeated call sites are grouped,
and each caller stores one shared next-step link, not every possible expanded path.
Expand an individual explanation only when requested. Building the index visits all
symbols/calls and sorts incoming caller groups; query work is bounded by the selected
limits. Memory still grows with the input graph and result size.

The test suite includes cycles, diamonds, duplicate call sites, ambiguous selections,
partial reports, invalid graphs, query limits, a 5,000-node chain and a 5,001-node
fan-out. These are regression checks, not a benchmark of large real repositories.

Later Git/UI/API layers can call `analyze_impact` without changing AST extraction.
Database storage, incremental indexing, analysis caching and batch/multi-target
impact are future additions, not implemented features.

## Two-commit change impact (Git workflow)

```sh
python3 -m repotrace change /path/to/repo --base main --head my-branch
python3 -m repotrace change /path/to/repo --base HEAD~1 --head HEAD --include-possible --output change.json
```

`git_snapshot.py` reads each commit straight from Git objects into a temporary folder (branch, index and
working files are never touched; nothing is executed). `change_detector.py` matches functions by
file + qualified name + kind using the syntax hash. `change_impact.py` runs the normal analyzer on both
sides with identical settings, queries the old graph for deleted/modified functions and the new graph
for added/modified ones, and writes a `change_impact` report (schema 0.1). Renames count as delete + add.
Contextual-only edits (imports, constants), failed files and ambiguous names appear under `gaps`
instead of being treated as safe. Exit codes: 0 ok, 1 partial/limited/error report, 2 invalid input.

## Tools added after the first Git workflow

| Tool | What it does |
|---|---|
| `python3 -m repotrace change REPO --base A --head B [--include-possible] [--output r.json] [--dashboard d.html]` | Two-commit change-to-impact report. Evidence tiers: supported links, possible links, name-only matches; plus gaps. Also handles changed decorators, changed constants, and files that did not parse before. |
| `qa/browser/run.sh [light|dark] [outdir]` | Drives the real app in headless Chrome (needs Chrome + Node 22+) and checks every screen; writes screenshots. |
| `python3 -m repotrace.report.dashboard r.json --repo REPO --output d.html` | One self-contained HTML page (no server, no network): what changed, where to look, why, the code. Embeds source snippets from the two commits; treat it like source code when sharing. |
| `python3 -m repotrace arrows report.json` | Plain-text arrows for one repository report. |
| `python3 -m repotrace.evaluation.runtime_recorder` | Evaluation tool. EXECUTES the target's code to record real caller-to-callee pairs. Use on a disposable copy; the network is blocked by default. |
| `python3 -m repotrace.evaluation.trace_score` / `change_score` | Score the analyzer against recorded runs / against an author's CHANGELOG_TRUTH.md. |

The resolver also infers: abstract-base overrides, typed fields and constructor injection, forwarding
decorators, return types, and implicit special-method calls (operators, `len()`, `with`, f-strings).
All of these produce POSSIBLE links only; LIKELY links are unchanged.
