# Test recommendations

RepoTrace compares two Git commits from a local Python repository. It reads the committed snapshots and analyzes them without importing project code or running its tests.

```powershell
python -m repotrace --lab
python -m repotrace change . --base HEAD~1 --head HEAD --all-tests
python -m repotrace change . --base HEAD~1 --head HEAD --json
```

In the Lab, select the repository and both versions. **Related tests** shows the ranked recommendations in pages of 20. Expand a reason to see the base and head paths and the file and line for each recorded call. **Changed code** shows the recommendations for each definition. Copy a command and run it from the head checkout in the project's own environment. Replace `python3` with `python` if that is how your environment exposes Python.

## Selection and ranking

- RepoTrace starts from changed definitions and walks callers in both revisions. Shared helpers are regular steps in that walk. Recorded uncertain method and constructor candidates can add possible recommendations. The selector also looks for supported paths so a shorter uncertain path cannot hide a supported one.
- A changed runnable test is itself a recommendation. Hooks and fixtures provide evidence for the tests that use them. They have no individual run command.
- Fixtures are resolved by visibility in the test class, module, and enclosing `conftest.py` directories. The selector follows parameter requests, literal `usefixtures` marks, fixture dependencies, and autouse fixtures, with cycle detection. It can distinguish direct parametrization from literal indirect requests. The command selects the whole parametrized test by definition.
- Identity normally uses the relative file and qualified name. Among otherwise unmatched tests, a unique matching syntax tree with the function name ignored can transfer base evidence to a moved or renamed head test. Multiple matches and unreadable head files are reported as unverified. A prior test with no head counterpart is listed without a command.
- Tests are deduplicated by head identity. Ranking prefers supported evidence, base-only evidence, explicit use over shared setup or autouse, then shorter paths. File and qualified name break ties. `--max-tests` sets the run-first shortlist size. `--all-tests` prints the full ranking. Neither setting changes the analysis budget.

## Report fields

- `changes[].test_selection` holds runnable tests, removed tests, unverified tests, and other relevant test code for a changed definition. Its traversal data shows the limits for supported and possible paths. A change beyond the graph budget can still yield a directly edited test or hook.
- `test_plan.tests` contains the run-first shortlist. `test_plan.all_tests` contains the full discovered ranking, with change indexes and evidence per change. Each runnable entry supplies its head file, line, command, and initial execution status of `not executed`.
- Evidence is keyed by `before` and `after`. It contains the confidence tier, chain, runner hook or fixture details where applicable, and graph call locations. The top-level base and head fields give the commit IDs.
- `test_plan.removed_tests`, `unverified_tests`, and `broader_testing` describe prior tests and follow-up scope. Shared setup, configuration changes, parse failures, unresolved calls, and traversal limits can warrant running a wider suite. If no test is linked, run the relevant component tests or the usual project suite.

## Supported scope and limitations

RepoTrace reads conventional Pytest names and simple root-level `pytest.ini`, `pyproject.toml`, `tox.ini`, and `setup.cfg` options. These include file, class, and function patterns, test paths, and configured import roots. It recognizes standard unittest test classes and hooks, including statically resolved inheritance. Async test execution and parametrization still depend on the project's test runner and plugins.

Imports, dynamic dispatch, plugin-provided or dynamically requested fixtures, inherited Pytest fixtures and dynamically resolved hooks, and customized collection can leave gaps. Test correspondence uses exact syntax evidence and can miss a move that also edits the body. Module and class changes use existing definition and constant-reader analysis where available. Other class initialization, module behavior, and shared configuration changes can call for broader testing. Static evidence establishes a potential relationship. Execution and changed-line coverage need a separate test run.

## Verification

```powershell
$env:REPOTRACE_SHOW_QUALITY = '1'
python -m unittest tests.test_recommendation_quality
Remove-Item Env:REPOTRACE_SHOW_QUALITY
python -m unittest tests.test_test_selection tests.test_recommendations tests.test_change_impact tests.test_lab_data tests.test_repository tests.test_app.LabTests
node --check repotrace/report/lab/app.js
```

Six temporary Git repositories used expected test and evidence sets authored from the fixture source. They yielded 16 of 16 expected tests and 16 total recommendations, for 100% recall and precision on this set. All 14 checked change, revision, and path combinations matched their expected witnesses. Each controlled comparison took 0.29 to 0.39 seconds end to end on the verification machine. Its recommendation-specific work took 0.27 to 1.76 ms. That timer includes discovery, fixture indexing, identity matching, additional graph queries, and ranking. It excludes snapshot analysis and the primary change-impact queries.

On this workspace's two preceding commits, a comparison of 51 changed definitions returned 47 runnable recommendations in 19.84 seconds end to end, including 0.36 seconds of recommendation-specific work. This run measured timing. The temporary repositories supplied the independently labeled test sets.

With the Lab running on port 8773, the browser workflow can be checked in a second PowerShell window. Set `CHROME` to the path of the installed Chrome executable.

```powershell
$base = git rev-parse --short HEAD~1
$head = git rev-parse --short HEAD
node qa/browser/recs_verify.mjs http://127.0.0.1:8773 (Get-Location).Path $base $head
```

Headless Chrome verified the live comparison, expandable test evidence, per-change recommendations, and the next page on a comparison with 30 linked tests. The full unittest run had 12 failures, one error, and three skips. All 13 failures and errors also reproduced in an archive of the pre-feature commit.
