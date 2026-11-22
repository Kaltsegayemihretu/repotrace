# RepoTrace on markdown, and where we stand (18 Nov 2026)

Second project we did not write: **Python-Markdown** (github.com/Python-Markdown/markdown), cloned at `2c96e36ba4750cf18583d62e2fcecff49e077419`:
36 source files, 38 test files, 1,672 graph nodes. Its own 1,223 tests pass in a clean Python 3.12.10 environment. Method and scripts are the same as
for tinydb (`docs/EVALUATION_tinydb.md`, `qa/real_projects/`); raw results are in `qa/real_projects/results/markdown/`. Everything below is **measured**
unless it says "my read".

**Status:** markdown is a *development* project, not a blind one: I had already looked at it (a 758-test suggestion on one commit) before scoring.
The blind set (`click`, `itsdangerous`) is still untouched.

## 1. Coverage (static)
4,727 calls (tests included): 149 sure, 2,021 maybe, 767 to Python itself or libraries, 1,790 could be project code but not traced, 6 import not found.
**55% of the calls that could go to the project's own code were traced** (tinydb: 29%; StudentManagement: 85%).

## 2. Call accuracy against what really ran
Recorded: the whole suite, 1,223 tests, 4,677,366 real calls, 3,949 distinct edges, network blocked.

| Arrow type | Judged | Right |
|---|---|---|
| Sure | 132 (17 never ran) | **132 (100%)** |
| Maybe | 1,980 (41 never ran) | **1,980 (100%)** |
| Name only, name unique in the project | 247 | 211 (85.4%) |
| Name only, name shared by several functions | 940 | 165 (17.6%) |
| Name only, common library-style name | 81 | 17 (21.0%) |

| Recall: real project-to-project calls that had an arrow | Real calls | Any arrow | Sure or maybe |
|---|---|---|---|
| markdown's own code (source only) | 788 | **89.1%** | **50.1%** |
| everything, tests included | 2,733 | 92.5% | 77.7% |

Read the "any arrow" column with care: 307 of the 788 source calls were found only as **name-only** matches, the weakest evidence we have (right 18% to 85% of the time
depending on the name). Strong evidence (sure or maybe) covers 50.1%. Main reasons for the 86 missed source calls: implicit calls such as operators,
properties and iteration (23), unresolved calls (12), "not an established class binding" (6), a receiver with no supported declaration (6), resolved to a different target than ran (7), others.

## 3. Test shortlist against real test runs
30 commit pairs (the newest commits touching 1 to 3 source files and at most 120 source lines, excluding release, docs and typo commits), real tests run
at both versions. All 30 runs complete. 29 pairs had at least one test that really executes the changed code.

| | Result |
|---|---|
| Relevant tests (really execute a changed function) | 11,620 over 29 pairs |
| Suggested by RepoTrace | 2,921 |
| Suggested and really relevant | **2,747 (94.0%)** |
| Relevant tests found | **2,747 of 11,620 (23.6%)** |
| Pairs where it suggested anything | **7 of 29** |
| Pairs where it found nothing | **22 of 29** |
| Tests that went from pass to fail | **0 in 30 pairs** |

The 23.6% is misleading on its own. It comes from **4 pairs** where one changed class constructor (`Delimiter.__init__` and similar) is reached by almost every
test, so RepoTrace suggested 686 to 758 of the project's 1,223 tests, between 56% and 62% of the whole suite. That is not a shortlist. In 3 other pairs it
suggested 1, 2 and 12 tests, all right. In the other 22 pairs it suggested nothing while 22 to 1,014 tests really ran the change.

## 4. What the runs taught us about our own tools (fixed)
Using the scorer on an outside project found three bugs in our tooling, each now covered by a test:
1. the "Check a change" page crashed on nested changed functions (tinydb);
2. the scorer took any `tox.ini` as proof of pytest (markdown's `tox.ini` runs `unittest`);
3. the scorer's unittest discovery failed on nested test packages with relative imports and on a library module named `test_tools.py`, and reported the run as incomplete.

## 5. Scorecard across projects (what we have measured)
| | Our practice projects (made by us or ChatGPT) | tinydb (real, small) | markdown (real, medium) |
|---|---|---|---|
| Sure arrows right where the code ran | 138 of 138 (Shopkeep), 156 of 156 (Ledgerly) | 150 of 151 | 132 of 132 |
| Maybe arrows right where the code ran | 128 of 128 (Shopkeep), 399 of 400 (Ledgerly) | 207 of 207 | 1,980 of 1,980 |
| Real source calls found (any arrow) | 32.7% (Shopkeep), 44.8% (Ledgerly) | 63.3% | 89.1% (50.1% with sure or maybe) |
| Calls traced (static, of those that could be project code) | 85% (StudentManagement), about 35% (Queuely) | 29% | 55% |
| Test shortlist: relevant tests found | 78 of 78 (StudentManagement), 45 of 157 (Ledgerly), 4 of 128 (Queuely), 0 of 72 (Shopkeep) | 119 of 1,243 (9.6%) | 2,747 of 11,620 (23.6%, from 4 floods) |
| Test shortlist: suggestions that were right | 94% (StudentManagement), 98% (Ledgerly) | 100% | 94.0% |
| Commit pairs with any suggestion | n/a (single hand-picked commits) | 9 of 25 | 7 of 29 |
The two columns on the left come from the earlier handbook figures and a different method (hand-tagged steps, different projects), so compare with care.
Across the 54 real commit pairs with at least one relevant test (tinydb and markdown), **38 got no suggestion at all (70%)**.

## 6. Where we stand (my read, not a measurement)
| Area | Verdict | Why |
|---|---|---|
| What it says is true | **Satisfied** | Sure and maybe arrows were right 99 to 100% on three different projects. It does not invent links. |
| The map is complete | **Not satisfied** | 29% and 55% traced on the two real projects; 2 of 12 real file connections drawn on tinydb. Pluggable designs defeat it. |
| The test shortlist (the headline feature) | **Not satisfied** | Right when it speaks, but silent on 70% of real commits and useless (60% of the suite) on a few others. |
| "Tests that will break" | **Unproven** | 0 pass-to-fail cases in 60 real commit pairs; only our own practice project ever showed one. |
| The screens | Satisfied for small projects, unproven for large | Work end to end on tinydb and StudentManagement; large-project behaviour not exercised beyond the checks. |
| The evidence base | **Thin** | Two real projects, both now development sets; the blind run has not happened. |
| Honesty of the reporting | Satisfied | Weak numbers are reported as weak; three tool bugs were found and fixed by using it on real code. |

Plain summary: a trustworthy tool that is much less helpful than it looks. When it draws a line or names a test it is almost always right, but on
real projects it draws a minority of the real connections and names the right tests in under a third of real commits.

## 7. What would change the verdict, in order
1. **Cap and rank the shortlist** (do not list 700 tests as a shortlist): cheap, and removes the worst output.
2. **Score `click` once, blind**, and report it whatever it says.
3. Pick one blocker, by data: untyped parameters (457 refused calls over 8 projects) or computed call targets (482). Each is hard and moves numbers a few points.
4. Say the limits plainly in the report: static analysis cannot see plug-in style designs, and a quiet result is not "safe".

## 8. Follow-up: a bounded run-first list (18 Nov 2026)

Implemented a comparison-wide default budget of **20 tests**, deduplicated across changed functions.
The guided review and CLI show the number selected, total connected candidates, and number omitted.
The CLI accepts `--max-tests`; complete per-change evidence remains in the JSON report, alongside `test_plan`.
Ranking uses evidence strength, a path seen only before the change, explicit use before shared setup/autouse,
then shorter paths, with file/name as stable tie breakers. This is a heuristic, not a measured failure predictor.

**Measured follow-up on one development-set pair**, `0bfd8c3e..a198bb39`: reran the full suite at both versions
using the existing clean Python 3.12 environment and the network-blocking scorer. Both runs completed with
1,187 test identities each. Raw result: `qa/real_projects/results/markdown/shortlist_score.json`.

| | All connected candidates | Run-first list |
|---|---|---|
| Tests listed | 758 | 20 |
| Listed tests that executed changed code | 720 (95.0%) | 20 (100%) |
| Relevant tests found, out of 1,038 | 69.4% | 1.9% |
| Connected candidates outside the list | 0 | 738 |

This improves the size and clarity of the output. It **does not improve call coverage, cure empty results, or
establish failure prediction**. The original 30-pair measurements above remain unchanged; the capped list is
scored separately so its smaller recall cannot be hidden behind the full candidate count. This single rerun
does not establish that the ranking is optimal or that 20 is the right budget for every project.

Why the larger problem remains: test selection needs a complete path from a test to changed code. One unresolved
receiver, callback, factory return, or dynamically selected method can sever that path even when its other links
are correct. Tinydb's forwarding through `__getattr__` is a diagnosed example. Markdown has many unsupported
receiver/target patterns and implicit calls (sections 1–2); shared constructors explain the opposite problem,
where a single reachable change attracts most of the suite. Some misses are limitations of the current resolver,
so they can be improved; others depend on runtime choices that source text alone cannot settle reliably.
Passing tests on ordinary historical commits also provide no pass-to-fail examples with which to judge failure
prediction. The next evidence step remains an untouched blind evaluation; the next coverage step needs a
bounded resolver improvement measured on these development projects.

Validation: all 507 RepoTrace tests passed with the optional pytest interpreter configured. The live Chrome
check on this Markdown pair passed all guided-screen checks, including exactly 20 test cards and working copy
commands, with no page errors. This verifies this development-project workflow, not arbitrary large projects.

## 9. Follow-up: recognize the standard-library Generic base (18 Nov 2026)

The registry class inherits from `typing.Generic[T]`. The resolver previously treated that as an unknown base,
so it refused the registry's own methods as well. A ten-line change in `mapping/method_resolver.py` recognizes
this exact imported standard-library base. It reuses the existing known-base machinery and reserves the names
that `Generic` supplies. It does not unwrap arbitrary `SomeClass[T]` expressions or infer the type of stored items.
Python documents this base form in its [generic-class reference](https://docs.python.org/3.12/library/typing.html#user-defined-generic-types).

**Measured against the same source snapshot and the previously recorded full-suite trace** (the tests were not
rerun to produce this trace):

| Markdown measure | Before | After |
|---|---|---|
| Maybe arrows | 2,021 | 2,076 |
| Maybe arrows right where their line ran | 1,980 / 1,980 | 2,035 / 2,035 |
| Real source calls covered by sure or maybe | 395 / 788 (50.1%) | 447 / 788 (56.7%) |
| Real source calls covered by any tier | 702 / 788 (89.1%) | 711 / 788 (90.2%) |

All **55 added links** were observed in the recording: 52 in source code and 3 in tests. Of the source links,
43 were previously only name matches and 9 were previously missed. No existing link was removed or downgraded.
The new links cover registry operations such as registration, iteration and lookup, not dispatch to each stored
processor's `run` method. Every addition remains **Maybe**; no new Sure links were introduced.

Reanalysis of tinydb, cachetools, more-itertools and python-dotenv found no link changes. Tinydb's trace score
is unchanged. Static reanalysis of Markdown's same 30 commit pairs found unchanged connected-test counts and
**no previously missed relevant tests newly connected**. This is a measured map improvement, not evidence that
the headline test-selection problem is solved. The blind projects remain untouched.

Raw results: `qa/real_projects/results/markdown/generic_base_comparison.json`,
`qa/real_projects/results/markdown/generic_base_trace_score.json`, and
`qa/real_projects/results/tinydb/generic_base_trace_score.json`.

All **513 tests pass**, including architecture and evidence-contract checks. Six regression tests cover imported
aliases, inheritance, mutation, shadowing, unknown bases and reserved names. The folder layout, public graph
schema, candidate limits, dependencies and static-only analysis policy are unchanged by this resolver change.

An earlier experiment to propagate arguments into known instance methods was removed after it recovered zero
calls on the same five development projects. It is not part of the retained implementation.
