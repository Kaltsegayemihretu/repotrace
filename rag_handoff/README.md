# RAG starter package: a small repo, two commits, the Git diff and the dependency graph

Everything here was produced by RepoTrace from a real small Python project (StudentManagement, 21 files, 160 functions,
classes and methods). Nothing was typed in by hand; `build_dataset.py` regenerates every file in `data/`.

## 1. What is in this folder

| File | What it is |
|---|---|
| `StudentManagement.bundle` | The test repository with its **full Git history** (11 commits, tags `step-01` to `step-11`). One file; clone it (see section 3). |
| `data/commits.txt` | The history, one line per commit: full id, short id, date, message. |
| `data/diff.patch` | The full unified diff between the two commits. |
| `data/diff_name_status.txt` | Changed files and the kind of change (`M` modified, `A` added, `D` deleted, `R` renamed). |
| `data/diff_numstat.txt` | Lines added and removed per file. |
| `data/diff_hunks.json` | Every changed **line range**, linked to the function that contains it, before and after. |
| `data/graph_step-06.json` | The dependency graph of the project **at the base commit**. |
| `data/graph_step-07.json` | The dependency graph of the project **at the head commit**. |
| `data/change_report.json` | RepoTrace's own change report: what changed, name-only leftovers, gaps. |
| `build_dataset.py` | Regenerates all of `data/`. Python standard library only. |

## 2. The two commits

| | Tag | Commit id | Message |
|---|---|---|---|
| **Base (before)** | `step-06` | `f33909e2a086ec9cdbb4a006a2c6d65fbe6ad418` | Step 06: add count_records to the parent BaseRepository |
| **Head (after)** | `step-07` | `7e2496f59a4968f96ecc16a61123c80f310c7264` | Step 07: rename read_course_by_id to fetch_course_by_id (one caller forgotten) |

`graph_step-06.json` describes the code at `f33909e`. `graph_step-07.json` describes the code at `7e2496f`.
Each graph covers the **whole project at that commit**, not only the changed files.

## 3. Commands

Needs **Python 3.9 or newer** and **Git**. There is **nothing to install**: no `pip install`, no third-party packages.
On Windows use `python` where this page says `python3`.

```
# 1. Get RepoTrace (the analyser and graph generator) and go into it
git clone https://github.com/israelhayal/repotrace.git
cd repotrace

# 2. Rebuild every file in rag_handoff/data (clones the bundled test repo into a temp folder, analyses both commits)
python3 rag_handoff/build_dataset.py

# 3. Optional: look at the test repository itself, with its history
git clone rag_handoff/StudentManagement.bundle StudentManagement
git -C StudentManagement log --oneline
```

Use the same pipeline on any other Git project:
```
python3 rag_handoff/build_dataset.py --repo PATH_TO_REPO --base OLD_REF --head NEW_REF --project MyProject --out my_data
```

The individual tools, if you want to call them yourself:
```
# Git comparison (same data as diff_*.txt):
git -C StudentManagement diff --name-status step-06 step-07
git -C StudentManagement diff --numstat     step-06 step-07
git -C StudentManagement diff -U0           step-06 step-07      # exact changed line ranges

# RepoTrace change report: what changed and what it can affect
python3 -m repotrace change StudentManagement --base step-06 --head step-07 --output change.json

# RepoTrace map of one folder (the raw graph, before build_dataset.py reshapes it):
git -C StudentManagement worktree add ../step07 step-07        # a plain checkout of that commit in a new folder
python3 -m repotrace map ../step07 --output map_step-07.json
```

## 4. The graph: what it means

**Nodes are files, classes, functions and methods.** They are not individual lines or statements.
**Edges go from `source` to `target`.**

| Edge `type` | Direction | Meaning | `confidence` |
|---|---|---|---|
| `CONTAINS` | parent -> child | a file holds a class or function; a class holds a method | none |
| `CALLS` | **caller -> callee** | the code of `source` contains a call that goes to `target` | `sure` or `maybe` |
| `IMPORTS` | importing file -> imported file | the file imports a module that is part of this project | none |

- `sure`: the code text shows where the call goes (a plain call to a function in scope, or an explicit import).
- `maybe`: it depends on an assumption. Example: `self.repository.read_course_by_id(...)` is shown as a call to
  `CourseRepository.read_course_by_id` because the object looks like a `CourseRepository`, but a subclass or another object
  could be used at run time. A call with several possible targets gets **one `maybe` edge per candidate**.
- Calls that have **no edge**: calls to Python itself or installed libraries (`print`, `list.append`, ...) and calls the analysis
  could not trace. They are **counted** in `meta.counts.calls_not_drawn`, not drawn. A missing edge is a hole in the map,
  not proof that nothing depends on it.
- There is one `CALLS` edge per call site. The same two functions can have two edges if one calls the other twice; the line is in `evidence.line`.

**To find what a change can affect, walk `CALLS` edges backwards** (target -> source) from the changed function: its callers.

Shape of the file:
```json
{
  "meta":  { "project": "StudentManagement", "commit": "7e2496f5...", "tag_or_ref": "step-07",
             "node_granularity": "...", "edge_direction": "...", "confidence": "...", "counts": { "nodes": 181, "edges": 483, "...": "..." } },
  "nodes": [ { "id": "studentapp/storage/course_repository.py::CourseRepository.fetch_course_by_id@25:4",
               "type": "method",                       // file | class | function | method
               "name": "fetch_course_by_id", "qualified_name": "CourseRepository.fetch_course_by_id",
               "file": "studentapp/storage/course_repository.py", "start_line": 25, "end_line": 29,
               "parent": "studentapp/storage/course_repository.py::CourseRepository@8:0",
               "is_test": false, "docstring": null, "signature": "fetch_course_by_id(self, course_id)",
               "source": "    def fetch_course_by_id(self, course_id):\n        ..." } ],
  "edges": [ { "source": "studentapp/services/course_service.py::CourseService.find_course_by_id@34:4",
               "target": "studentapp/storage/course_repository.py::CourseRepository.fetch_course_by_id@25:4",
               "type": "CALLS", "confidence": "maybe",
               "evidence": { "file": "studentapp/services/course_service.py", "line": 35,
                             "expression": "self.repository.fetch_course_by_id", "how": "method" } } ]
}
```
- A node `id` is `file::qualified_name@line:column` at that commit. **Ids change when lines move**, so match the same
  function across two commits by `file` + `qualified_name` + `type`, not by `id`.
- `source` holds the text of each function or method (what you would chunk and embed). Classes have `source: null`;
  their text is their methods, which are separate nodes. File nodes have no source; read the file at that commit.
- Counts at step-07: 181 nodes (21 files, 15 classes, 145 functions and methods), 483 edges (160 CONTAINS, 41 IMPORTS, 282 CALLS: 85 sure, 197 maybe).
  Another 120 calls are not drawn (68 Python or library calls, 52 not traced).

## 5. The Git comparison output

`data/diff_hunks.json` links the diff to the graph. One entry per changed file:
```json
{ "file": "studentapp/storage/course_repository.py", "change": "M", "old_file": "studentapp/storage/course_repository.py",
  "hunks": [ { "old_lines": [25, 25], "new_lines": [25, 25],
               "old_symbols": ["studentapp/storage/course_repository.py::CourseRepository.read_course_by_id@25:4"],
               "new_symbols": ["studentapp/storage/course_repository.py::CourseRepository.fetch_course_by_id@25:4"] } ] }
```
`old_symbols` index into `graph_step-06.json`; `new_symbols` index into `graph_step-07.json`.
The whole change is 2 lines in 2 files:
```
M  studentapp/services/course_service.py        1 added, 1 removed
M  studentapp/storage/course_repository.py      1 added, 1 removed
```

## 6. One known example (the answer key says so)

The commit renames a method and **forgets one caller**.

- **Changed code:** `CourseRepository.read_course_by_id` is renamed to `CourseRepository.fetch_course_by_id`
  (`studentapp/storage/course_repository.py`, line 25). One caller is updated: `CourseService.find_course_by_id`
  (`studentapp/services/course_service.py`, line 35).
- **Related code that was forgotten:** `EnrollmentService.find_course_or_fail`
  (`studentapp/services/enrollment_service.py`, lines 31-35) still calls the **old name** on line 32:
  `course = self.course_repository.read_course_by_id(course_id)`.
- **What the graphs show:**
  - In `graph_step-06.json` there is a `CALLS` edge `find_course_or_fail -> read_course_by_id` (`maybe`, line 32).
  - In `graph_step-07.json` that edge is gone, because the target no longer exists. The call is now one more call that is
    "not traced" (51 at step-06, 52 at step-07), and `find_course_or_fail` has no outgoing `CALLS` edge at all.
  - The function that disappeared is the one the forgotten caller needs, so running that code would fail.
- **What RepoTrace reports** (`change_report.json`): for the deleted `read_course_by_id`,
  `STILL REFERENCED IN HEAD (name only)`: `studentapp/services/enrollment_service.py:32 self.course_repository.read_course_by_id`.
- **Why this is a good test question:** "I renamed `read_course_by_id`. What did I forget?" The right answer needs both the
  diff (a name vanished) and the old graph (who called it), and it should give file and line.

## 7. Notes for building the retrieval side

- A good unit to embed is one function or method node (its `source` plus `qualified_name`, `file`, `start_line`, `docstring`).
  Keep `file` and `start_line` as metadata so every answer can cite `file:line`.
- Use the graph for the second step: after retrieving a function, add its callers and callees from the `CALLS` edges, and say whether each link is `sure` or `maybe`.
- Keep the diff separate: "what changed" comes from `diff_hunks.json`, "what could be affected" from walking `CALLS` backwards in the **base** graph (callers that existed before the change) and checking them in the **head** graph.
- Do not let an answer say a change is "safe". No edge means "nothing found", not "nothing there".

## 8. Limits to know about

- This is **one** small, made-up project and **one** commit pair. It is enough to start building, not enough to measure quality.
- Only about a quarter of calls are `sure` on this project; most are `maybe`. Edges are static analysis, not proof of what runs.
- The graph does not include dynamic behaviour (`getattr`, plugins, decorators that change targets). It never runs the code.
- Run `python3 rag_handoff/build_dataset.py --repo ...` on a second repository before trusting any number from one example.
