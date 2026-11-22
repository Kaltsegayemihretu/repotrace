# Evaluation on projects we did not write

Scripts used for `docs/EVALUATION_tinydb.md`. They **run the target project's own tests** in throwaway copies (`git archive`), with outgoing
network connections refused by `netblock/sitecustomize.py` (a precaution, not a sandbox). Use them only on projects you chose.

| Script | What it does |
|---|---|
| `scan_commits.py REPO SRC_PREFIX` | read-only: counts commits that touch 1-3 source files |
| `static_scan.py REPO SRC_PREFIX SOURCE_ROOT [N]` | runs RepoTrace's change report on recent commits (reads Git only) and counts real function changes |
| `run_suite.py REPO COMMIT VENV -- CMD...` | runs the project's own test command on one commit in a throwaway copy, network blocked |
| `eval_tests.py REPO SRC_PREFIX VENV_PYTHON OUT.json [N]` | scores the test shortlist on N commit pairs against real test runs |

`results/tinydb/` holds the raw output of the tinydb run. The test environment needs Python 3.11+ for most projects (create it with `python3.12 -m venv`).
