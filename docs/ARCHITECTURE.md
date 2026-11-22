# How RepoTrace is built

This page explains the folders and the reasons behind them, so a new teammate can find their way in about ten minutes.

## The one-sentence idea
RepoTrace reads a Python project **without running it**, draws a **map** of which function uses which (and how sure it is), and can show **which part of the map a code change could affect**.

## The pipeline, in plain words
Think of an assembly line. Each station only uses what the stations before it made.

```
 code on disk / in Git
        |
   1. reading     read one file; find its functions and calls
        |
   2. mapping     join all files into one map of the project
        |
   3. graph       ask questions of the map (who calls this? what does it reach?)
        |
   4. changes     compare two versions of the map (which functions changed?)
        |
   5. report      explain the answer in plain words and as a web page
        |
   6. app / cli   the doors people walk through (browser app, terminal commands)

   evaluation     stands to the side: measures how accurate the map is
```

## The folders

| Folder | What it does | Main files |
|---|---|---|
| `repotrace/reading/` | Reads **one file**. Lists its functions and calls, and links the calls that stay inside that file. | `analyzer.py`, `resolver.py` |
| `repotrace/mapping/` | Joins **all files** into one project map. This is where call-guessing happens (imports, objects, methods). Every link gets a grade: *Sure*, *Maybe*, *Name only*. | `repository.py`, `project_resolver.py`, `method_resolver.py`, `argument_flow.py`, `library_objects.py`, `resolution_summary.py` |
| `repotrace/graph/` | Answers questions about a finished map. It never reads source files. | `graph_index.py`, `impact.py`, `graph_explore.py`, `structure.py` (the classes, files and folders of the project, and how they relate) |
| `repotrace/changes/` | Reads two versions straight out of Git, finds which functions changed, and asks the graph what each change reaches. | `git_snapshot.py`, `change_detector.py`, `change_impact.py`, `decorator_impact.py`, `name_matches.py`, `signature_review.py` |
| `repotrace/report/` | Turns results into words and a page: the "What happened" story, the whole-project overview (busiest functions, where things start, how much was traced), and the single-file web page. | `story.py`, `overview.py`, `dashboard.py`, `dashboard_template.html` |
| `repotrace/app/` | The local web app (a small server on your own computer) and its built-in examples. | `server.py`, `examples.py` |
| `repotrace/cli/` | Terminal commands. | `change.py`, `impact.py`, `arrows.py` |
| `repotrace/evaluation/` | Measures accuracy by recording what really runs and comparing. Developer tool; users never need it. | `runtime_recorder.py`, `trace_score.py`, `change_score.py` |
| `tests/` | Automatic checks (459 of them). `test_architecture.py` guards the rule below. | |
| `samples/` | Small example projects and sample reports, used by tests and docs. | |
| `qa/browser/` | A script that drives the real app in Chrome and checks every screen. | |
| `docs/` | This page, the guide, the technical reference, the roadmap. | |

## The one rule
> **A folder may only use folders above it in the pipeline, never below.**

So `reading` can never use `report`, `graph` can never use `changes`, and `app` and `cli` never use each other. `tests/test_architecture.py` fails the build if anyone breaks this, so the structure can't quietly rot.

**Why it matters:** you can change the web page without any risk to the accuracy of the map, and you can test the map without a browser or Git.

## Decisions and the reasons

1. **Never run the analysed code.** The tool only reads text. Running unknown code can be dangerous and slow, and "what happened when I ran it" misses code paths that didn't run. *Cost:* Python decides some things at run time, so some calls can't be seen. The tool says so instead of guessing.
2. **Grade every link.** *Sure* (the code text shows it), *Maybe* (depends on an assumption), *Name only* (just the name matches). A single "yes/no" would hide how much we really know.
3. **Never say "safe".** A quiet result means "nothing found", not "nothing there". The screen always offers *What we could not see*.
4. **Library and built-in calls are counted, not drawn.** `print`, `list.append`, other people's libraries aren't your code, so they'd clutter the map. They are still counted, so you can see how much was left out.
5. **The map is a plain JSON report.** `graph` reads that file and nothing else. Any tool (or a future database) can produce the same format, and we can save, share and test maps.
6. **Python standard library only.** No packages to install. A teammate can clone and run it.
7. **A local web page, not a hosted service.** The app runs only on your own computer, needs a secret per-launch token, and never uploads code.
8. **Measure accuracy against reality.** `evaluation/` records real runs and scores the map. We keep *practice* projects separate from *blind* ones, so we can't tune the tool to its own exam.
9. **Detect a rename or move only when it is certain.** `report/story.py` calls something "renamed" or "moved" only when the deleted and new functions are identical apart from the name.
10. **The map comes first, the comparison is a lens.** "Explore the project map" maps the folder as it is on disk (no Git needed). "What a change affects" is the same map built twice and compared. Both use the same graph screen.
11. **One screen to explore, and pictures only of one thing at a time.** The project map is an outline on the left and details on the right. A picture of a whole repository turns into a tangle past about 30 boxes, so pictures only ever show the thing you picked and its direct neighbours. `graph/structure.py` supplies the classes, files, folders and three kinds of relation: *is a kind of*, *has a* (only when the class itself creates the other, so we never guess) and *uses* (grouped calls, graded sure / maybe). Creating an object is always graded *maybe*, like everywhere else. A name is matched to a class only through the same file's definitions or an import in that file; a name imported from outside the project, or not imported at all, is never matched to a same-named class elsewhere. The map keeps its own copy of the source, so the code shown is the code that was analyzed (the page says so when a file has changed since).
12. **One front door.** `python3 -m repotrace` opens the app. Subcommands (`change`, `map`, `impact`, `arrows`) cover the terminal.

## Known weak spot (honest)
Coverage is reported honestly: calls into Python's standard library and installed libraries are counted as *outside*, but an import RepoTrace cannot find (a library not installed here, or your own code in a folder it was not told about) is counted as *not traced*, so a wrong source folder lowers the number instead of hiding.

The four files in `mapping/` (`project_resolver`, `method_resolver`, `argument_flow`, `resolution_summary`) use each other in a loop, held together by imports done inside functions. It works and is tested, but a cleaner design would break the loop. We left it alone on purpose: moving folders should not change behavior.

## Where to start reading
- *I want to understand the map:* `reading/analyzer.py`, then `mapping/repository.py`.
- *I want to understand a change report:* `changes/change_impact.py`.
- *I want to change what the screen says:* `report/story.py` and `report/dashboard_template.html`.
- *I want to know if it's accurate:* `evaluation/` and `docs/ROADMAP.md`.
