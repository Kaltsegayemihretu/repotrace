# RepoTrace project handbook

For teammates joining the project. It records what we decided, why, what was measured, what is still weak and what comes
next, so you do not need the chat history. Written 15 Nov 2026 from the working sessions. Where a number appears, the
sentence says whether it was **measured** or only **claimed**.

## 1. What RepoTrace is, in four sentences
RepoTrace reads a Python project **without running it** and builds a map of which function uses which. Every link is
graded **sure** (the call is written in the code), **maybe** (one step depends on an assumption) or **name only**
(another call has the same name; the weakest). Given two Git versions it shows what a change could affect, which line to
check, and which existing tests to run first. It never says a change is "safe"; a quiet result means "nothing found".

Local, standard library only, no network. Code: `repotrace/` (layers explained in `docs/ARCHITECTURE.md`).

## 2. Where we are (15 Nov 2026)
| Area | State |
| --- | --- |
| Engine (reading, mapping, graph, changes) | Working. 497 automatic tests pass (`python3 -m unittest discover -s tests -t .`, about a minute). |
| Test shortlist for a change | New. Finds the tests connected to a change, through `setUp` hooks and pytest fixtures, using both Git versions. |
| Real app, classic screen (`python3 -m repotrace`) | Works; the earlier outline-and-details screen. |
| Real app, new guided screens (`python3 -m repotrace --lab`) | **Live (16 Nov).** Pick any folder and two versions; guided start, map with code, change review, test shortlist, and **Explore the project**, which is the Obsidian-style connected graph from the first prototype (its own page, ported line for line and running inside the app; ringed with the change in a review). Verified in a real browser on four projects. Not yet the default screen. |
| Accuracy evaluation | Scripts exist (`repotrace/evaluation/`); numbers in section 6. |
| Not built | RAG/LLM answers, branch-merge preview, architecture-rule checking, live loading in the new design, IDE panel. |

Deadline: about 4 weeks from 26 Oct 2026 for the senior project; the supervisor will not help much.

## 3. How to run everything
```
git clone https://github.com/israelhayal/repotrace.git && cd repotrace
python3 -m repotrace --lab                             # the real app, new guided screens (live analysis of any folder)
python3 -m repotrace                                   # the real app, classic screen
python3 -m repotrace change REPO --base HEAD~1 --head HEAD   # what did my last commit affect?
python3 -m unittest discover -s tests -t .             # 497 checks
qa/browser/run_lab.sh OUT PROJECT BASE HEAD            # real-browser check of the new screens on a project
open prototype/lab/index.html                          # the guided prototype (recommended design)
open prototype/index.html                              # the earlier web-of-connections prototype
node prototype/lab/check.mjs /tmp/out                  # browser checks of the guided prototype (Chrome + Node 22+)
```
Needs Python 3.9+ and Git. The prototypes need only a browser and read the saved file `prototype/data.js`.
Regenerate it from a project with `python3 prototype/make_data.py ../StudentManagement`.

## 4. The direction, and the decisions behind it
1. **Core promise (reset on 27 Oct).** A simple developer tool that assesses changes, reports impact accurately and
   resolves calls. UI polish and extra features come second to accuracy and the test shortlist.
2. **UI direction (set on 9 Nov).** The first dashboards were judged "not even 10%" of the intended tool. Wanted:
   a developer-tool feel, exploring by touching nodes (like Obsidian), seeing the project **physically** (files),
   **logically** (recorded relationships) and **conceptually** (guessed topics, labelled as a guess), plus guidance on
   which tests to run. Exploring comes first, then reviewing a change. Website first, IDE side panel later.
3. **Approval gate.** The real UI is replaced only after a clickable prototype of three tasks is approved:
   find the relevant code, understand its connections, review a change and get a useful next action.
4. **Two prototypes now exist.** `prototype/lab/` is a guided, one-level-at-a-time design (started in ChatGPT, fixed
   here; see `prototype/lab/README.md`). `prototype/index.html` is a web of connections with three views. **Recommended
   base for the real app: the Lab design.** Open question for the team: do we also want the web view?
5. **RAG (an AI answering questions about code).** Decided: retrieval first (search finds the code and cites file and
   line), then at most one narrow, evaluated AI explanation, cloud only with explicit consent, and the core must work
   with no AI. Not built. Whether the approved proposal requires RAG is **still to confirm with the supervisor**.
6. **Honesty rules (do not break these).** Never claim "safe". Keep **measured** and **claimed** separate. A missing
   link is a hole in the map, not proof of no dependency. Never turn a **maybe** into **sure** without new evidence:
   `self.method()` can be overridden by a subclass, so it stays maybe.
7. **Library and built-in calls** are counted and classified but not drawn, so "unresolved" means "might be your code".
8. **Layer rule.** Each folder may only use folders above it in the pipeline (`tests/test_architecture.py` enforces it).

## 5. What was built, in order
- Engine: reads one file with `ast`, joins files into one map, grades links, compares two Git versions, finds callers
  of changed code, checks changed signatures, pairs a moved or renamed function only when the bodies are identical.
- Layered restructure and GitHub (31 Oct), "Explore the project" map (2 Nov), outline + details screen (4 Nov).
- Fixes after an outside review of 6 Nov: eight real trust bugs (invented links, stale source, omitted files, coverage
  hiding missing imports and more), each now covered by a test in `tests/test_trust.py`.
- Prototype design work (9 to 13 Nov): both prototypes, with every claim on screen built from the data.
- Test shortlist and evaluation (11 to 13 Nov): see section 6.
- **Engine to screens, live (16 Nov).** `repotrace/report/lab_data.py` turns engine reports into the data the guided screens read: joins by exact symbol id inside a version (and by file plus qualified name across versions), keeps every reason a test is connected to a change, and keeps link strength (sure or maybe) apart from link version (shown version or older only). The server serves the screens (`/lab`) and a job (`/api/lab`); `repotrace/report/lab/` holds the screens, shared with `prototype/lab`. Checked in a real browser on StudentManagement, Queuely, Ledgerly and Shopkeep.

## 6. Measured results (and their limits)
Rule: score a project **once**, untuned, before fixing anything on it; report the number as it comes out.

**Practice projects** (all written by another ChatGPT chat or by Israel, each with an answer key
`CHANGELOG_TRUTH.md` and 11 tagged steps):
| Project | Layout | Used as |
| --- | --- | --- |
| Shopkeep (`Test Project`) | `src/`, pytest | development set |
| Ledgerly (`test project 2`) | flat, pytest | blind once, now spent as a development set |
| Queuely (`test project 3`) | flat, pytest | development set |
| StudentManagement | flat, unittest | development set, used by the prototypes |
These folders are **not in this repository**. Ask Israel; they are small private repos.

**Arrows** (static map against a recorded run): confident arrows 138/138 and 156/156 right where run; uncertain
128/128 and 399/400; real calls found 32.7% on Shopkeep and 44.8% on Ledgerly. Coverage is not accuracy: of the calls
that could go to a project's own code, about 25% are sure, 59% maybe and 15% not traced on StudentManagement; Queuely 35%
traced, Shopkeep about a third.

**Test shortlist** ("relevant" = the test really executes a changed function, "failing" = passes before, fails after).
Measured by `repotrace/evaluation/test_score.py`, which runs the real tests:
| Project | Relevant tests found | Suggestions that were right | Notes |
| --- | --- | --- | --- |
| StudentManagement (development) | 78 of 78 | about 94% | the 8 tests that really break at step-07 were all found |
| Ledgerly (measured once, untuned) | 45 of 157 (29%) | 45 of 46 (98%) | fresh pytest project |
| Queuely, before fixture work | 4 of 128 | 4 of 5 | objects come from a dependency container |
| Shopkeep, before fixture work | 0 of 72 | none suggested | parametrized fixture and a factory that sets fields from outside |
Plain reading: when it suggests a test it is almost always right; on larger pytest projects it still misses most.
The fixture work (`repotrace/mapping/fixture_flow.py`) is correct and tested but added **no measured gain** on those
projects, because other patterns block them first. Failing-test truth exists only on StudentManagement.

## 6b. First evaluation on a project we did not write (18 Nov 2026)
tinydb, scored with the same scripts. Full report: `docs/EVALUATION_tinydb.md`. In short, **measured**: sure arrows right 150 of 151, maybe arrows 207 of 207,
real calls in tinydb's own code found 63.3% (any arrow) and 37.6% (sure or maybe); the test shortlist is right every time it speaks (119 of 119) but finds
only 9.6% of the tests that really run the change (119 of 1,243 over 30 commit pairs). Main cause: `TinyDB.__getattr__` forwards calls dynamically.

## 7. Known weak spots (be honest about these in the report)
1. Objects built by factories, dependency containers or set from outside their class are not followed (the main reason
   Queuely and Shopkeep find few tests). Next candidates: fields set on a freshly built object, tuple unpacking of
   factory returns, parametrized fixtures whose branches share a class.
2. A suggested test can pass: a graph path exists but an earlier validation error stops the test before it reaches the
   changed line (example: `test_create_enrollment_rejects_missing_student`).
3. `mapping/` has a 4-file import loop (lazy imports). Left alone on purpose.
4. The conceptual (topic) view is rough; one topic held 33 of 72 files. It must stay labelled experimental.
5. The new design is not connected to live analysis; it shows saved data.
6. pytest 8.3.3 for measuring pytest projects lived only in a throwaway environment, not in the repo.

## 8. Plan for the remaining weeks
1. **Now:** teammates run the tester's pack (the shared document) and send notes back. Anything two people get stuck on
   gets fixed first.
2. Decide the design (Lab, web view, or both), then build it into the real app so it works on any folder.
3. Engine: close the factory/container/fixture gaps above, re-measure on an **untouched** project (Ledgerly is spent).
4. Optional, if the supervisor requires it: the narrow retrieval-first AI step.
5. Final weeks: evaluation write-up, user tests on a project nobody tuned on, docs and freeze. Reference roadmap:
   `docs/ROADMAP.md` (M0 to M4 priority, M5 to M7 later).

## 9. How we work
- Plain words; honest numbers; say what is not proven. Lead with the result.
- Verify UI in a real browser (`qa/browser/run.sh`, `prototype/lab/check.mjs`), never from reading code.
- Tests must pass before any push: `python3 -m unittest discover -s tests -t .`
- Never run a project's code except on a throwaway copy with network blocked (that is what the evaluation scripts do).
- Open for your attention: nothing in this repo should contain passwords or personal data; keep it that way.

## 10. Outside reviews so far (summaries)
- 6 Nov review of the project map: 8 findings, all reproduced and fixed.
- 9 and 10 Nov reviews of the prototype: keep the design direction; fix version mismatches, function-level focus,
  evidence in the review panel, confidence wording; keep the topic view experimental; test with real people.
- 12 Nov review of the test shortlist: do not call `self.method()` certain; separate setUp hooks from tests; "relevant"
  and "failing" are different measurements; use both Git versions; measure pytest before improving it. All done.
- 13 Nov: the Lab design was built outside and reviewed in depth; fixes listed in `prototype/lab/README.md`.

## 11. Files worth reading first
`docs/ARCHITECTURE.md` (why the folders), `docs/GUIDE.md` (how to run each example), `docs/REFERENCE.md` (details),
`docs/ROADMAP.md`, `prototype/README.md`, `prototype/WALKTHROUGH.md`, `prototype/lab/README.md`,
`repotrace/changes/test_selection.py`, `repotrace/mapping/fixture_flow.py`, `repotrace/evaluation/test_score.py`.
