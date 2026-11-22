# RepoTrace: current state and the path to a clear, useful product

Team handoff — 15 November 2026

This document is meant to stand on its own. It explains the project, what is implemented, what is missing, the recommended experience, and the order of work. Recommendations below are a proposed delivery baseline, not claims that those features already exist. There is no allocation of work to particular people.

## 1. Read this first

RepoTrace helps developers find relevant Python code, follow its connections, and decide what to inspect after a change.

The useful outcome is: **“I know where to look, why it matters, and which tests to consider.”**

A graph is one way to explain that answer. Counting changed functions is not the answer. Listing every reachable caller is not automatically useful either: someone still has to decide what those callers mean.

The project already has a substantial engine. The immediate problem is finishing the connection between that engine and an understandable interface. More analysis features will not, by themselves, solve the confusion users have reported.

Finish one complete journey first:

> Open a project → find something relevant → read its code → follow one connection → inspect a change → get a small, explained test shortlist.

The user should not need Israel beside them explaining the controls.

## 2. What this review actually checked

Reviewed the current Desktop repository at `/Users/israelhayalsew/Desktop/repotrace-mvp`. Its latest commit was `787e692` at the start and `0ca42d5` at the final check; the intervening commit changed only documentation, not the engine or UI reviewed here. Read the architecture and handbook, implementation files, prototype code, and four saved screenshots of the guided prototype. Also inspected the earlier prototype's documentation and the separate publishing copy.

Fresh verification:

- The unit test run completed: **484 discovered, 482 passed, 2 skipped, no failures or errors** after allowing the tests to open a local server. The initial restricted run failed 25 server tests because localhost binding was blocked, not because those assertions failed.
- A fresh StudentManagement `step-06 → step-07` analysis completed with status `ok`.
- It produced **12 distinct suggested tests** across three changed definitions.
- All three engine change records contained `test_selection`; none of the corresponding `build_view()` dashboard records contained it.
- No target-project test suite was rerun for this comparison. Twelve suggestions is not a new measurement of twelve relevant or failing tests.

Browser control was unavailable. The visual observations below come from the saved screenshots and current HTML/JavaScript; they are not a new live usability test. Existing browser-check claims in the repository were not independently rerun. This is a focused implementation and product review, not a proof that every line is correct.

The working tree initially contained changes to the handbook and new vision-document files; those were committed separately during this review and the final working tree was clean. This review did not replace those files or modify the app.

## 3. Where the actual files are

Open `/Users/israelhayalsew/Desktop/repotrace-mvp` in your editor.

| Location inside that project | What it contains |
|---|---|
| `repotrace/` | The working Python engine and local app |
| `repotrace/report/dashboard_template.html` | The current real dashboard interface |
| `repotrace/report/dashboard.py` | Converts engine results into data for that dashboard |
| `repotrace/app/server.py` | Opens projects, starts analyses, and serves results to the browser |
| `prototype/lab/index.html` | The newer guided prototype; recommended design starting point |
| `prototype/lab/app.js`, `map.js`, `model.js` | Guided screens, map interactions, and browser-side display/search logic |
| `prototype/lab/style.css`, `map.css` | Guided prototype appearance |
| `prototype/index.html` | Earlier whole-project graph prototype |
| `prototype/data.js`, `prototype/lab/data.js` | Saved prototype data files; verify the loaded path when regenerating |
| `prototype/make_data.py` | Builds saved prototype data from analysis results |
| `tests/` | Automated engine, report, and local app checks |
| `docs/PROJECT_HANDBOOK.md` | Existing project history, measurements, and decisions |

An earlier separately published copy lives in the ChatGPT workspace at `output/prototypes/repotrace-investigation/dist/`. The Desktop `prototype/lab/` copy contains later changes documented in its README. Do not assume the published website and current Desktop prototype are identical.

Run the real app from the project root:

```sh
python3 -m repotrace
```

Open the guided prototype on Mac:

```sh
open prototype/lab/index.html
```

The prototype uses saved analysis. Opening it does not analyse a new repository. The real app can analyse local folders, but has the older interface.

StudentManagement is a separate example repository: `https://github.com/israelhayal/StudentManagement`. Its 11 tagged steps include intentionally broken intermediate versions. It is useful for development and demonstrations, but is no longer an unseen evaluation project.

## 4. Explain the engine without technical language

Imagine a project has these functions:

```text
checkout → place_order → calculate_total
```

The arrows mean one function calls another. They do not show everything that happens at runtime or the order of all operations.

RepoTrace reads source text and records functions, classes, imports, and calls. It then tries to connect each call to its definition. If `calculate_total` changes, it follows the connections backwards to find `place_order`, then `checkout`.

Those are places worth reviewing. They are not automatically broken. A change to a comment, a harmless refactor, and a new required parameter can have very different consequences.

RepoTrace adds value when it gives a specific reason, such as:

> “This call still passes one argument, but the changed function now requires two. Inspect this line.”

Or:

> “This test is connected to the changed function through its setup code. Consider running it.”

### The internal stages

| Stage | Plain explanation | Code |
|---|---|---|
| Read | Understand the structure of one Python file | `reading/` |
| Connect | Work out which definitions calls may refer to across files | `mapping/` |
| Navigate | Find callers, callees, and paths through the recorded connections | `graph/` |
| Compare | Read two Git versions and find changed definitions and review candidates | `changes/` |
| Explain | Prepare readable findings, source snippets, and display data | `report/` |
| Present | Let someone choose a project and interact with the result | `app/`, `cli/` |
| Evaluate | Separately measure findings against controlled executions or answer keys | `evaluation/` |

The graph is currently represented by reports and in-memory indexes. A graph database is not required to complete this MVP.

## 5. What exists, and how far it goes

| Capability | Current status | Boundary |
|---|---|---|
| Read Python files and folders | Implemented | Files with unsupported syntax, unreadable content, or excluded paths must remain visible as gaps |
| Extract definitions, imports, calls and metadata | Implemented | Extraction alone does not identify every runtime target |
| Link cross-file calls | Implemented | Ordinary imports and several object patterns work; dynamic patterns can remain uncertain or unresolved |
| Recognise objects and method calls | Implemented for supported patterns | Subclass overrides, replacement objects, factories and containers limit certainty |
| Explore callers and callees | Implemented | Traverses recorded relationships, not all possible real execution paths |
| Compare two Git versions | Implemented | A comparison is not a merge preview |
| Find changed, added and removed definitions | Implemented | Failed-file analysis must not be interpreted as deletion |
| Suggest rename/move pairs | Implemented for matching bodies | Matching text does not prove identical runtime meaning in a different context |
| Detect supported argument mismatches | Implemented | Methods, constructors, decorated targets and unpacked arguments are excluded from this check |
| Find remaining uses of removed names | Implemented with evidence distinctions | A matching name alone does not identify its runtime target |
| Suggest related tests | Implemented in the engine and saved prototype | Full shortlist is missing from the real dashboard's view data |
| Follow unittest setup and pytest fixtures | Implemented in bounded forms | Not full pytest collection/plugin behaviour or all factory patterns |
| Follow a fixture's returned object | Implemented for supported returns/yields | Parametrized or ambiguous fixtures can remain unresolved |
| History hints | Implemented in the prototype generator | Not yet a complete production history feature |
| Search by ordinary words | Implemented in the prototype | Not semantic retrieval or an AI answer |
| Conceptual/topic view | Experimental in the older prototype | Guessed topics can be poor; not verified architecture |
| Architecture rules for users' repositories | Not implemented | The existing architecture test checks RepoTrace's own folder rules |
| RAG and model explanations | Not implemented | Existing natural-language search prompts do not make search AI |
| Two-branch integration/conflict preview | Not implemented | Comparing commits does not simulate merging them |
| IDE extension | Not implemented | Local browser app remains the delivery target |

## 6. What the measurements mean

Do not use one percentage to describe the entire product.

- **How much was traced:** proportion of calls the analyzer could connect under a clearly stated denominator.
- **How often recorded links were right:** accuracy among relationships that the evaluation could check.
- **Relevant tests found:** how many tests that executed changed code appeared in the shortlist.
- **Useful suggestions:** how many suggested tests actually executed changed code in the measured runs.
- **Failing tests found:** how many tests passed before and failed after the change, and were suggested.

These are different measurements. Runtime measurements only observe the executed cases. They cannot prove correctness on every unexecuted path.

The existing handbook reports the following historical test-selection results. They were not rerun in this review:

| Project and evaluation status | Relevant tests found | Usefulness of suggestions |
|---|---|---|
| StudentManagement, development project | 78/78 | About 94% |
| Ledgerly, first measured before tuning, now a development project | 45/157, about 29% | 45/46, about 98% |
| Queuely, before fixture work | 4/128 | 4/5 |
| Shopkeep, before fixture work | 0/72 | No suggestions to score |

The handbook says fixture work did not produce a measured improvement on those projects because other object-flow gaps blocked the paths first. Do not present older scores as a fresh benchmark of the current tree.

Plain conclusion: **the shortlist can be useful when it finds a path, but on some projects it misses most relevant tests.** Completing the test shortlist in the real interface is valuable; presenting it as an exhaustive test plan would be wrong.

Keep machine-readable evaluation reports, repository commit IDs, analyzer commit ID, environment, skipped tests, and limitations with the final report. A number copied into a handbook is not a reproducible evaluation package by itself.

## 7. Why the current experience still feels difficult

The saved guided screens have worthwhile strengths: restrained colours, stable local maps, source beside connections, version context, and a clear “Show me that line” action. Preserve those.

The main difficulty is the amount of interpretation demanded from a first-time user.

### A. Too many equivalent ways to begin

The start screen contains a large search field, two large cards, navigation links, sample questions, a map action, and a separate file list. They are individually reasonable, but together the user must first learn which route is intended.

**Change:** keep two clear entry tasks: **Find code** and **Review a change**. Put **Browse map** beside search as an alternative within Find code. Keep the map readily available, without making it a prerequisite.

### B. A result often sends the user into another exploration task

Search leads into a map, which requires a node selection, which reveals a narrow source panel. Someone who asked where email is checked wanted a useful code location first.

**Change:** return a small list of locations, with file, function, and a short matching excerpt. Selecting a result opens readable source immediately. “What uses this?” opens its local connections in the same workspace.

### C. The graph gets more room than the code

In the saved function screen, the map is wide while the source panel is narrow. Long method names wrap and source lines require horizontal navigation.

**Change:** default the function workspace to a readable code/details pane and a compact connections pane. Offer an obvious **Expand map** control. Selecting another node updates the same workspace; it must not feel like entering another application.

### D. Review information is spread across tabs and repeated explanations

“What to check”, “Changed code”, “Related tests”, and “Limits & history” are useful categories, but the user must assemble them into a plan.

**Change:** each finding should carry its own context, code evidence, suggested action, and related tests. Keep the whole comparison's tests and diff available as secondary views.

### E. The test list reads like an analysis dump

Long qualified names, path badges, chains, source links, and full commands repeat for every test. The first screen can show only a small part of the list.

**Change:** show a compact test row with name, file, a one-sentence reason, and **Copy command**. Expand **Why this test?** to reveal the full chain. Deduplicate tests without discarding their relationships to multiple changed functions.

### F. Some wording promises more than the evidence

The current prototype says “the change cut their path” and “that often means the change broke the connection” for links found only in the old version. Losing a static path can also result from a valid rename, a move, or a pattern the resolver no longer follows.

**Change:** say **“Connected in the old version; no path found in the new version.”** A concrete leftover reference or argument mismatch can justify stronger attention. Missing analysis alone cannot prove breakage or a probability of breakage.

The prototype also contains hard-coded StudentManagement evaluation wording in its test panel. Remove it from general project screens or supply it through clearly labelled, project-specific evaluation metadata.

### G. There is no clear point at which the review is finished

Users can keep clicking indefinitely. They need to know what they accomplished.

**Change:** allow a finding to be marked **Reviewed**, with an optional note. Keep that decision tied to the exact comparison. “Reviewed” is a user's acknowledgement, not a claim that a defect was fixed or tests passed. A new analysis must not silently inherit an outdated acknowledgement.

## 8. The recommended experience

The feeling should be: **calm, orienting, useful, and reversible.** Every screen should answer: where am I, what am I looking at, and what useful thing can I do next?

### Starting a project

Show the project name and one sentence explaining the tool. Before a project is loaded, the main action is **Open Python project**, with **Try example** underneath.

After loading, show **Find code** and **Review a change**. A short status says when analysis happened and whether any files were missed. Advanced roots and exclusions belong behind settings, except when needed to explain a failed analysis.

Do not show a wall of totals, confidence percentages, or three graph lenses on first use.

### Find code

1. Enter a name, file, or question.
2. See a few useful starting points with source excerpts.
3. Open one and read its code.
4. Choose **Used by** or **Uses** to follow a relationship.
5. Back returns to the exact prior place, including query, selection, scroll, and map position.

Search must describe what it actually does. Before RAG exists, “Matching code” is appropriate; “AI answer” is not.

### Browse the map

Keep the requested map experience. Start with folders or files at a manageable level. Clicking goes deeper; breadcrumbs go back up. A function opens a local view of its callers and callees. A visible sentence explains that arrows mean calls, not an execution timeline.

Use stable positions and readable labels. Show a limited set initially with an explicit count and **Show more**. Never hide a relevant finding merely because the graph limit was reached. Provide an equivalent list for keyboard use and for people who find graphs difficult.

Physical browsing and logical connections can be part of one journey: folders locate code; function connections explain its relationships. Leave experimental conceptual grouping out of the default workflow.

### Review a change

Use plain labels **Before** and **After**, with human-readable commit subjects and exact IDs available. The first useful output should resemble:

```text
Review: step-06 → step-07

1 place to inspect
The course lookup has a new name. This line still uses the old name.

enrollment_service.py:32
[highlighted source line]

Next: check whether this call should use fetch_course_by_id.

[View related tests]  [Show connection]  [Mark reviewed]

12 suggested tests · not run
Some connections could not be checked [Details]
```

This wording is an example specific to StudentManagement. General screens must generate the same structure from actual evidence, without hard-coded project stories.

Put concrete supported findings before broad affected-code lists. Group related findings by changed definition or issue; keep uncertain references distinguishable. A graph path by itself is “related code”, not a defect.

### Tests and empty results

For a suggested test, show the test name, where it lives, why it is relevant, and an exact copyable command. Keep **Not run** visible. A command must match the test framework and current version. If that cannot be established, offer the location without inventing a command.

If nothing is selected, say **“No related tests found in the recorded connections”** and provide a sensible next step, such as browsing tests or running the project's usual suite. Do not say “no tests needed”.

If no findings exist, say **“No issues found by these checks”**, show what was checked and any material gaps, and allow inspection of changed code. An unreadable file is a partial result, not a clean result.

## 9. What must be done as soon as possible

### Priority 1 — One real workflow, not three separate products

Use the guided Lab design as the basis. Preserve the existing real engine and server. Connect one live comparison all the way through source, evidence, connections, and tests before expanding other screens.

The crucial change is in the presentation boundary: `dashboard.py` currently omits `test_selection`. The engine already creates it in `changes/change_impact.py`; the prototype generator consumes it separately. Make the real UI consume the same authoritative result.

**Finished when:** a user opens StudentManagement in the real app, compares step-06 and step-07, sees the actual leftover caller and the 12 distinct suggested tests, opens the relevant source version, and copies a command. Then the same workflow succeeds on a second supported project with no StudentManagement-specific text or IDs.

### Priority 2 — Simplify the path before polishing appearance

Reduce competing start actions, let search open readable source, and bring related tests into each finding's flow. Keep a map button and a full-map option. Use one navigation model rather than independent page histories.

**Finished when:** new users can find code and explain one connection without a tutorial from a team member.

### Priority 3 — Preserve evidence while adapting the data

The prototype generator deduplicates tests by file/name and keeps the first changed-target association. That can hide why a test relates to another changed function. Merge associations instead of discarding them.

Carry actual symbol IDs through the display model. In particular, the prototype's signature-finding association uses the last component of a name; two different classes/files can share that name. The production adapter should join by recorded identity, not a short-name guess.

Maintain separate fields for link strength and source version. “Old version only” describes time, not confidence. Do not collapse those concepts into a single display tier.

**Finished when:** one test connected to two changes appears once in the comparison list and under both applicable changes, with both reasons preserved. Two functions with the same name never receive each other's findings. All source locations identify their version.

### Priority 4 — Make testing repeatable for the team

Keep the core standard-library installation simple. Provide a separate documented development environment for pytest-based evaluation. The current default run skips a pytest evaluation check unless `REPOTRACE_PYTEST_PYTHON` points to an appropriate interpreter.

**Finished when:** a teammate can reproduce the full intended test/evaluation setup without guessing which temporary environment was used. Record executed, skipped, and failed counts separately.

### Priority 5 — Improve one important resolver gap at a time

First inspect missed-test paths and categorise their first blocker. Likely candidates from the handbook are factory returns, fields assigned outside a constructor, dependency-container objects, tuple returns, and parametrized fixtures. Do not attempt all at once.

Choose one narrowly defined pattern that appears frequently and can be handled conservatively. Add both a positive example and an ambiguous/reassigned example that must remain unknown. Keep inferred links uncertain where assumptions remain.

**Finished when:** the change improves a measured useful outcome without adding incorrect confident links. A higher raw link count alone is not success.

### Priority 6 — Test comprehension, not just whether buttons work

Watch five people independently attempt the three tasks in section 13. Fix the most common first obstacle, repeat with different users, and record the results honestly.

**Finished when:** the agreed comprehension targets are met, or remaining problems are explicitly documented for the final demo and report.

## 10. How to integrate without tangling the code

Keep this flow:

```text
Python analysis → versioned result → display adapter → browser workspace
```

The adapter translates results into usable display data; it must not invent new call targets. The browser can filter, group, search, and draw. The engine owns call resolution, change findings, and test selection.

Existing server routes already cover starting a map/comparison, checking a job, searching functions, fetching neighbours, and fetching source. Extend or adapt these rather than introducing a second analyzer inside the prototype.

A production display contract should include:

| Record | Required meaning |
|---|---|
| Analysis | Unique run ID, schema version, project, selected roots, status, timestamps, settings and gaps |
| Snapshot | Exact commit or identified working-tree snapshot; source and links belong to it |
| Definition | Symbol ID, file, qualified name, kind, span and snapshot |
| Connection | Caller/target IDs, evidence location, confidence/assumptions and version |
| Finding | Stable ID within the analysis, type, changed target IDs, evidence, explanation and next action |
| Suggested test | Test identity, all changed-target associations, evidence chains, versions, framework/command when known, not-run state |
| Review acknowledgement | Finding ID, analysis ID, user acknowledgement and optional note; separate from analysis truth |

Use an adapter test to ensure engine records survive into UI data. Test navigation and empty/error states in the browser. Do not substitute screenshot appearance for data correctness, or passing unit tests for user comprehension.

For bigger projects: load local neighbours and source on demand, paginate lists, disclose limits, keep result ordering stable, and avoid redrawing every function at once. Measure time and memory on a named medium repository before promising large-repository support. Cancellation and cached/reusable analysis are useful later improvements; their availability must be verified rather than implied.

## 11. The other layers we discussed: exact scope and order

### Retrieval and RAG

Purpose: answer questions such as “Where is authentication implemented?” with useful locations and a short explanation grounded in code.

Build retrieval first: index functions, classes, docstrings, paths and suitable source chunks; retrieve candidate locations; expand a small number of recorded graph neighbours; retain file/line and snapshot citations. Evaluate against a small manually labelled question set, including questions with no answer.

If the approved proposal requires RAG, reserve a narrow required slice after the live workflow. Add a model to explain retrieved evidence, cite every substantive code claim, and abstain when evidence is insufficient. Model text must not create call edges, convert uncertainty into certainty, or label a change safe. Cloud use requires an explicit code-sharing choice; the non-AI workflow remains usable.

The chat history says RAG is included in the proposal. The original approved proposal was not independently re-read in this review. Treat it as an outstanding deliverable until checked against that document; do not silently remove it based on an older handbook's uncertainty. If required, cut optional history/conceptual work before expanding its scope.

### Architecture conformance

Purpose: check a rule the team actually wrote, for example “UI modules must not import storage internals.”

First version: a small explicit configuration, import-based checks, file/line evidence, and optionally “new violation in this comparison”. Unknown imports remain gaps. Do not infer intended architecture from folder names or an AI summary. RepoTrace's existing internal layer test is not this user-facing feature.

### History hints

Purpose: surface files often changed together as secondary review clues. The prototype already computes bounded file co-change counts and excludes large bulk commits.

Before integration, pin the history window to the selected review context. The current generator uses `git log` from the repository's current HEAD and reuses that history for saved comparisons, so an older comparison can be influenced by later history. Such retrospective evidence must be labelled; it must not be evaluated as a prediction made at the old commit.

Display sample size, selected window and counts. Hide an empty section cleanly. Co-change is correlation, not a dependency edge or a requirement to edit another file.

### Branch integration preview

Purpose: address the teammate scenario where two branches independently change interacting code. Commit timing itself is not the problem; incompatible edits or assumptions are.

First version: choose both branches, pin their commits and common ancestor, preview integration in an isolated temporary context, report textual Git conflicts, and, if a combined snapshot can be produced, run supported checks on it. Show a cross-branch case such as one branch adding a required parameter while another adds an old-style call.

Never label a clean textual merge behaviourally safe. Do not automatically merge, reset, push or edit the user's branches. This is a separate increment after the core; do not pretend the existing two-commit comparison already handles it.

### Optional runtime evidence

Purpose: supplement the static map with paths actually observed by trusted tests. A missing observed path does not prove it cannot happen.

Start with importing revision-matched per-test coverage or trace data, not automatic execution of arbitrary repositories. Keep “inferred from source” and “observed in this run” distinguishable. Evaluation scripts already execute controlled targets; that does not make runtime analysis part of the normal product. Their Python network blocking is a precaution, not a security sandbox.

### IDE extension and conceptual view

An IDE extension is a later delivery surface using the same analysis contracts. Do not build a second resolver. A conceptual view can later group retrieved code into provisional topics, with evidence and user correction. Neither needs to delay the core browser MVP.

## 12. A concrete ten-working-day delivery sequence

These are working-day targets, not a guarantee. A day is complete when its output works. If a dependency slips, cut optional work; do not declare an unfinished gate complete.

| Day | Strict scope | Exit evidence |
|---|---|---|
| 1 | Agree the two entry tasks and a compact finding/test layout; reconcile current docs | One annotated flow and a checked list of current capabilities |
| 2 | Add the engine-to-display contract for tests, findings, versions and gaps | Adapter checks preserve test associations and exact identities |
| 3 | Connect the Lab workflow to one live local analysis | Open project → real results; no saved demo IDs required |
| 4 | Finish live source, local map, back navigation and finding-specific tests | The three main tasks work on two projects |
| 5 | Handle missing files, no findings, no tests, stale results, long names and keyboard use | Repeatable checks and no misleading empty state |
| 6 | Observe first-time users and fix the common first obstacle | Written task observations; visible fixes tied to them |
| 7 | Improve at most one high-value resolver pattern; package reproducible evaluation | Positive and ambiguity checks plus measured before/after effect |
| 8 | If required, finish the narrow cited RAG slice; otherwise spend on core defects | Question set results including abstentions; core works without model |
| 9 | Evaluate an untouched project, rerun usability tasks and finish report evidence | Saved metrics, environment, screenshots, limitations and requirement traceability |
| 10 | Fix blocking defects, freeze, and rehearse the demo on a teammate's machine | Reproducible launch, reviewed scope list and backup demo data |

This schedule does not include production branch preview, general architecture conformance, an IDE extension, arbitrary test execution, automated fixes, or a full conceptual engine. Those remain named follow-ups unless the approved requirements make a narrow slice compulsory. Required scope must displace optional work rather than be quietly added to the same ten days.

## 13. Acceptance checks: how the team knows it is useful

### First-time user tasks

Give the tasks without showing the answer or walking through buttons:

1. “Find where an email is validated and show one place that uses it.”
2. “Open this function and tell me what one arrow means. Follow it and return.”
3. “Review step-06 to step-07. Tell me what you would inspect first, why, and which test you would consider running.”

Ask afterward: “Did RepoTrace run those tests?” and “Does no finding mean the change is safe?”

Proposed usability targets, not current results:

- At least four of five participants choose an appropriate starting action within 30 seconds without coaching.
- At least four of five locate the code and explain one relationship within three minutes.
- At least four of five explain one review item and a sensible next action within five minutes.
- Every participant understands that suggested tests were not run and an empty review is not a safety guarantee.

Record first hesitation, wrong turns, assistance, completion time, and the participant's own explanation. A small study is formative feedback, not proof that all developers will understand the tool. Compare against doing the same kind of task with normal editor/Git tools where feasible; avoid reusing an already memorised answer.

### Product and technical gates

- Works on a second supported repository without changing project-specific UI code.
- Every finding opens the evidence from the correct source version.
- Duplicate names do not mix targets; one test can retain reasons for several changes.
- The displayed shortlist agrees with the engine, including deleted/renamed test handling.
- Unknown method targets remain unknown or explicitly uncertain.
- A partial analysis remains visibly partial.
- A graph has an equivalent navigable list; keyboard focus and Back work.
- The app reads projects without changing them; normal analysis does not execute target code.
- Test commands are valid for the stated environment or clearly unavailable.
- Measured findings and test-selection results include denominators and skipped/incomplete runs.
- A runtime-observed result, static hypothesis, name match and user acknowledgement never look like the same thing.
- Required proposal items map to implementation evidence or an explicit remaining limitation.

## 14. Working rules for teammates and their coding assistants

Use this document, the current code, and existing tests together. The code takes precedence over an old chat description of what was implemented. A proposed feature is not “done” because it appears in this document.

Before a change, state the user problem, the smallest behavioural change, the owning layer, and the acceptance example. Keep evidence fields intact. Add a focused regression check for behaviour that could mislead a user. Avoid unrelated restructures while connecting the UI.

Use a single approved design and result contract. Do not let separate assistants invent different confidence labels, test-selection rules, or duplicate analysis pipelines. A change to those contracts needs corresponding engine, adapter and display updates.

Use supported imports between layers. The existing architecture test guards RepoTrace's own dependencies. The known lazy-import cycle in mapping is technical debt, but a full resolver rewrite is not a prerequisite for shipping the current workflow.

Keep the README, handbook and roadmap consistent. They currently contain stale counts and states: the architecture document says 459 tests, other docs say 484; the older roadmap calls fixture/test-shortlist work future work that now exists; the prototype README still quotes the earlier one-test rename result; the handbook still describes practice repositories collectively as private although StudentManagement is public. Update these after the next verified build, not from memory.

Do not casually replace user work or deploy a design simply because it passes code tests. A team-approved experience and evidence from first-time users are separate from engine correctness.

## 15. The final demo should prove this

First show a developer locating unfamiliar code and following a connection. Then show a real change with one understandable review item, exact evidence, and related tests. Finally show a genuine limitation and how the UI communicates it without leaving the user stuck.

If required, add one repository question answered from cited evidence. Explain where the answer comes from, and show a question the system declines to answer.

The successful ending is a developer saying: **“I know where to look next, and I understand why.”**

## Evidence index

- Current checkout identity: `git log`, `git status`, 15 Nov 2026.
- Fresh verification: `python3 -B -m unittest discover -s tests -t .`; 484 run, two skipped.
- Fresh rename analysis: `analyze_change_impact(StudentManagement, 'step-06', 'step-07', include_possible=True)` and `build_view()`; 12 unique tests in engine output, no `test_selection` fields in dashboard change records.
- Pipeline and limits: `repotrace/reading/`, `mapping/`, `graph/`, `changes/`; `docs/ARCHITECTURE.md`.
- Test roles, evidence and command generation: `repotrace/changes/test_selection.py`.
- Fixture values and exclusions: `repotrace/mapping/fixture_flow.py`.
- Normal app routes and source versions: `repotrace/app/server.py`.
- Display omission: `repotrace/report/dashboard.py`, especially `build_view()`.
- Generator associations and history window: `prototype/make_data.py`, especially `review()` and `history()`.
- Current wording and paths: `prototype/lab/app.js`, `map.js`, `model.js`.
- Visual review: saved `prototype/lab/screenshots/01-start.png`, `04-function-view.png`, `05-review-overview.png`, `06-review-tests.png`.
- Historical numbers: `docs/PROJECT_HANDBOOK.md`; not remeasured here.
- Evaluation execution boundary: `repotrace/evaluation/test_score.py` and `runtime_recorder.py`.
