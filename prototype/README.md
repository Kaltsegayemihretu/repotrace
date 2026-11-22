# RepoTrace prototype (clickable, real data)

**Two designs, same data.** `lab/` is the guided, step-by-step design (started in ChatGPT, fixed here; see `lab/README.md`) and is the recommended base for the real app. `index.html` is the earlier web-of-connections design. Both read `data.js`.

**Not the final app.** A design proof to approve before the real screen is replaced.
Open `index.html` in Chrome (double-click it). No install, no server, no internet.

It shows real results from a real project, **StudentManagement** (`~/Desktop/StudentManagement`, 21 app files, 11 save points), and covers three tasks:

1. **Find code.** `⌘K` (or `/`), type *where is an email checked?*, Enter. Matches light up on the graph and the panel cites file and line. Word matching only, no AI: this is the baseline an AI answer would have to beat.
2. **Understand connections.** Click a folder → its files → a class → a function. The function's real callers and callees appear on the graph *as functions* (their folders stay as faded context). Arrows go from caller to called. Esc goes back; breadcrumbs at the bottom.
3. **Review a change.** **Review a change** → summary (e.g. *1 to inspect · 1 test to look at · 3 changed · 5 not checked* for the rename in step-07) → one finding open at a time with the exact caller line, the before/after signature, *Focus this relationship*, *View the whole caller*; tests show the line that leads to the change and the command to run them.

The project's own answer key (`CHANGELOG_TRUTH.md`) lets you check RepoTrace: at step-07 it names the same forgotten caller (`EnrollmentService.find_course_or_fail`, line 32), but it lists **1** test to look at where the answer key says **8** tests break. That gap is real and is the engine's, not the screen's.

## Versions always agree
Each comparison carries its own graph of the **after** version, so the graph, the code shown and the evidence come from the same commit. Code the change **removed** is added back from the **before** version: drawn dashed, labelled *removed*, with its old links dotted. The top bar always says which version you are looking at.

## Honest limits
- Data is baked into `data.js` (five graphs). Regenerate: `python3 prototype/make_data.py ../StudentManagement` (or `"../test project 3"` for Queuely).
- "Tests to look at" come from following calls; nothing is run.
- *Conceptual* view is **experimental**: topics are guessed and often wrong.
- Review findings only come from the checks that exist (changed call signatures, leftover uses of removed names). Nothing found ≠ safe; *not checked* lists the gaps.
- Coverage numbers say how much was traced, not whether it is correct.

## Check it automatically
`node prototype/check.mjs OUTDIR` (Chrome + Node 22+) does the three tasks with real key presses, typing and mouse clicks, verifies the results, and fails on any page error. Last run: 10/10, screenshots in `screenshots/`.
Passing it does **not** show people understand the screen: that is what `WALKTHROUGH.md` is for.
