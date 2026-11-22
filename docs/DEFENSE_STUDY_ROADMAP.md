# Study roadmap: know RepoTrace well enough to defend it

For Israel (and any teammate who has to answer questions about the code). Goal: be able to explain every part in your own
words and show it working live. Not "memorise 12,000 lines".

Real sizes (counted 17 Nov 2026): reading 585 lines, mapping 2,551, graph 686, changes 1,346, evaluation 1,021,
app 938, cli 298, report 4,193 (of which the old dashboard page is 1,675 and the new screens about 1,400), tests 32 files.
The **engine** (reading + mapping + graph + changes) is about 5,200 lines. That is what a panel will push on hardest.

## The method (use it for every file)
1. **Say what goes in and what comes out** in one sentence. Data in, data out. If you can't, you don't know the file yet.
2. **Run it** on `samples/` or StudentManagement and look at the real output.
3. **Break it on purpose** (change one thing), run again, see what moves.
4. **Explain it out loud** to an empty room, 60 seconds, no notes. Write down where you got stuck; that is your gap.

Do not read `method_resolver.py` (1,115 lines) line by line. Know **what problem it solves, the grades it hands out,
and 3 examples**. Same for `dashboard_template.html`: it is the old screen; know it exists and why it was replaced.

## The one picture you must be able to draw from memory
```
 a folder / two Git versions
   -> reading   one file -> functions, classes, calls
                (analyzer.py, resolver.py)
   -> mapping   all files -> one project map, graded links
                (repository.py, project_resolver.py, method_resolver.py)
   -> graph     questions on the map: who calls X?
                (graph_index.py, impact.py)
   -> changes   two maps -> what changed, what it reaches, which tests to run
                (git_snapshot.py, change_detector.py, change_impact.py, test_selection.py)
   -> report    plain-words data for the screens
                (lab_data.py, story.py, overview.py)
   -> app       local web server + job + screens
                (server.py, report/lab/*.js)
```
Rule to quote: a folder may only use folders above it. `tests/test_architecture.py` enforces it.

## Stage 0: orientation (45 min)
- Read `docs/ARCHITECTURE.md` and `docs/PROJECT_HANDBOOK.md` once, fast.
- Run `python3 -m repotrace --lab`, analyse StudentManagement step-06 -> step-07, click everything once.
- Write the picture above on paper from memory.
- Exit test: explain in 60 seconds what RepoTrace does and the three link grades (sure / maybe / name only).

## Stage 1: reading, one file (90 min). `repotrace/reading/`
- `analyzer.py` (312): uses Python's `ast` to walk one file. Key pieces: `CodeExtractor` (a visitor), `visit_definition`
  (functions/classes), `visit_Call` (each call), `analyze_file` (the entry point, returns the report for one file).
- `resolver.py` (273): scopes. Which name means what inside a function (`Scope`, `ScopeIndex`). Links calls that stay
  inside the same file.
- Do: run `python3 -m repotrace.reading.analyzer` on one small file and read the JSON. Find a function, its calls, its line.
- Exit test: "What is an AST? What does the extractor record for a call? Why does it never run the code?"

## Stage 2: mapping, many files into one map (3 hours, the hardest part). `repotrace/mapping/`
Read in this order:
1. `repository.py` (255): `analyze_repository` walks a folder, calls reading on each file, joins the results,
   `pytest_source_roots` finds the source folders.
2. `project_resolver.py` (410): `ProjectIndex` follows imports across files (`export`, `_import_value`,
   `reference_value`). This is how `from a import b` becomes a link to the right function.
3. `method_resolver.py` (1,115): objects and methods (`self.x()`, `obj.method()`, inheritance). Know the idea:
   find which class an object came from; if exactly one candidate -> sure, several possible -> maybe.
   Why `self.method()` stays **maybe**: a subclass could override it.
4. `argument_flow.py` (295), `library_objects.py` (159), `fixture_flow.py` (198): objects passed as arguments,
   objects from standard library classes, pytest fixtures.
5. `resolution_summary.py` (119): the counts (sure / maybe / not traced / library) you show on screen.
- Do: `python3 -m repotrace map ../StudentManagement --output /tmp/sm.json`, open the JSON, find one `sure` and one
  `maybe` call and write down why each got its grade.
- Exit test: "Why is this link maybe and not sure? What do you do with `print()`? What is a hole in the map and
  what does the tool say about it?"

## Stage 3: graph, questions on the map (90 min). `repotrace/graph/`
- `graph_index.py` (208): `GraphIndex` loads the report, checks it (limits, valid spans), gives `callers_of`, `select`,
  `functions`. It never reads source files.
- `impact.py` (108): `analyze_impact` walks callers outward from a function with depth/size limits.
- `structure.py` (288) and `graph_explore.py` (82): folders/files/classes view used by the explorer.
- Do: `python3 -m repotrace impact /tmp/sm.json --list-functions`, then `--function NAME`; ask what one function reaches, then check one path by hand in the code.
- Exit test: "What does 'affected' mean? Why are there limits? Why does the graph never touch the source?"

## Stage 4: changes, the main feature (3 hours). `repotrace/changes/`
1. `git_snapshot.py` (150): reads a commit's Python files out of Git safely (never checks out into your repo, never
   runs code). `materialize_commit`, `safe_destination` (path safety).
2. `change_detector.py` (320): `detect_changes(before, after)` compares two maps. Added/removed/changed/renamed
   (a move or rename is paired only when the bodies are identical).
3. `change_impact.py` (324): `analyze_change_impact` is the conductor: snapshot both versions, map both, detect changes,
   ask the graph what each change reaches, add leftover-caller concerns.
4. `signature_review.py` (155): changed parameters vs callers. `decorator_impact.py` (107), `name_matches.py` (85).
5. `test_selection.py` (205): the test shortlist. Test roles (test/setup/fixture/helper), evidence chain, both versions,
   `removed_tests`. This is your weakest measured area, so know it best.
- Do: `python3 -m repotrace change ../StudentManagement --base step-06 --head step-07` and read every line of output.
  Then find the answer in `CHANGELOG_TRUTH.md` and check it matches.
- Exit test: "Walk me through step-07. Why does it flag the forgotten caller? Why is a test on the list? Why did it miss
  tests on Queuely?"

## Stage 5: report layer and the data the screens read (2 hours). `repotrace/report/`
- `lab_data.py` (534): **the adapter**. Turns engine reports into the screens' data: `build_graph` (nodes, links,
  strength vs older-version flag), `build_review` (changed, concerns, affected, tests), `questions_for`. Know the
  contract: strength (sure/maybe) is separate from version (older version only).
- `story.py` (170) and `overview.py` (58): the plain-words story and the project overview.
- Exit test: "Why is there an adapter? How do you match the same function across two versions?" (file + qualified name + kind)

## Stage 6: app and front end (2.5 hours). `repotrace/app/`, `repotrace/report/lab/`
- `server.py` (623): local server on your machine only. Routes: `/lab`, `/lab/<allowed files>`, `POST /api/lab` (start
  a job), `/api/job` (progress), `/api/inspect`, `/api/browse`. Security: per-launch token, host/origin check,
  CSP headers, allow-listed files only. `Job.run_lab` runs the analysis on a background thread.
- `lab/picker.js` (94): choose folder and two versions, start the job, show progress.
- `lab/app.js` (200): the screens (start, review, reading view, test shortlist) and the app<->explorer messages.
- `lab/model.js` (100): search ranking, neighbours, navigation history.
- `lab/explore.js` (756) + `explore.html/css`: the Obsidian-style graph page, runs in an iframe, talks to the app
  with `postMessage` (`ready`, `init`, `navigate`, `read`, `review`, `state`, `escape`).
- Do: in the browser dev tools, open the Network tab, run an analysis, and watch `/api/lab` and `/api/job`.
- Exit test: "What happens between pressing Analyse and seeing the map? Why is nothing uploaded? Why an iframe?"

## Stage 7: evaluation and tests (90 min). `repotrace/evaluation/`, `tests/`
- `runtime_recorder.py` (219): runs the project on a **throwaway copy with network blocked** and records real calls.
- `trace_score.py` (379): compares the static map to the recording (the 138/138 numbers come from here).
- `test_score.py` (275): "relevant" (test executes a changed function) vs "failing" (passes before, fails after).
- `tests/test_trust.py`, `tests/test_architecture.py`: the eight trust bugs and the layer rule.
- Exit test: "How did you measure accuracy? What does 'coverage is not accuracy' mean? What can't you claim?"

## Suggested schedule
| Block | Stages |
|---|---|
| Day 1 morning | 0, 1, then 2 (steps 1-2 only) |
| Day 1 afternoon | 2 (steps 3-5), 3 |
| Day 2 morning | 4 |
| Day 2 afternoon | 5, 6 |
| Day 3 morning | 7, then redo the weak "exit tests" |
| Day 3 afternoon | mock defense with a teammate (questions below) |

If you only get one day, do 0, 1, 2 (idea only), 4, and the questions below.

## Questions a panel will ask (answer each in 60 seconds)
1. What problem does it solve and for whom? What do existing tools (Pyright, Sourcegraph, pytest-testmon) not do?
2. How do you find calls without running the code? What can't you find?
3. What are the three grades and why not just yes/no?
4. Why does it never say "safe"?
5. How do you know it is accurate? Show the numbers and say which are measured and which are not.
6. Where does it fail? (Factories, containers, fields set from outside; Queuely 4 of 128, Shopkeep 0 of 72.)
7. Walk through step-07 from the command to the screen.
8. How is user data protected? (Local only, no upload, code never executed, token and origin checks.)
9. Why Python standard library only?
10. What would you do next with more time? (Close the factory/container gaps, untouched real-project score, optional AI.)
11. Your test projects were made by ChatGPT or you. Why trust the numbers? (Say it plainly: small and synthetic; this
    is why we are adding real open-source repos.)
12. How much did AI tools write, and how do you know it is right? (Be honest: tools wrote much of it; 496 tests,
    browser checks and the measured evaluation are how it was verified.)

## Rules for the defense
- Say "measured" or "claimed" every time you give a number.
- If you don't know, say "I'd check `<file>`" and open it. Never invent.
- Have these open before you start: the map for step-07, the shortlist, `docs/ARCHITECTURE.md`, the evaluation table.
