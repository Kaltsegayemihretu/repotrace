# RepoTrace on a project we did not write: tinydb (18 Nov 2026)

First full evaluation on an outside project. **tinydb** is a small document database (github.com/msiemens/tinydb), cloned
at commit `edff96aeb2616402e9489d46e90a592c05565621`: 12 source files, 9 test files, 408 symbols. Its own 228 tests pass
in a clean Python 3.12.10 environment. Every number below is **measured** by the scripts in `qa/real_projects/`, raw results
are in `qa/real_projects/results/tinydb/`. Where something is a guess, it says so.

**Status of tinydb as a test set:** scored once, untuned, before any change to the engine. From now on it is a
*development* set (the failures below were diagnosed on it). The blind set (`click`, `itsdangerous`) has not been cloned or touched.

## 1. How much of the code the map could trace (static, nothing run)
| Calls in the project (1,555, tests included) | Count |
|---|---|
| Sure (the code text shows where it goes) | 151 |
| Maybe (depends on an assumption) | 207 |
| Go to Python itself or installed libraries (counted, not drawn) | 336 |
| Could be the project's own code but not traced | 861 |
| Import not found | 31 |

**29% of the calls that could go to the project's own code were traced** (358 of 1,250). StudentManagement: 85 + 198 of 334 (85%); the
difference is mostly the tests: tinydb's tests call almost everything through one object (section 5).
Coverage is not accuracy; section 2 measures accuracy.

## 2. Call accuracy against what really ran
Method: run tinydb's whole test suite under `runtime_recorder` (228 tests, 21,938 real calls, 2,052 distinct caller-to-callee
edges, network blocked, throwaway copy), then compare every arrow of the static map with what ran at that line.
Only arrows whose line ran can be judged.

**Precision (of the arrows we drew, how many were right where the code ran):**
| Arrow type | Judged | Right |
|---|---|---|
| Sure | 151 | **150 (99.3%)** |
| Maybe | 207 | **207 (100%)** |
| Name only, name unique in the project | 174 | 174 (100%) |
| Name only, name shared by several functions | 101 | 50 (49.5%) |
| Name only, common library-style name (`get`, `update`) | 938 | 254 (27.1%) |

One sure arrow counted as wrong: `tests/test_tinydb.py:87`, a call to the local generator function `generator`. At that line the recorder saw
`Table.insert_multiple` run through `TinyDB.__getattr__`, not the generator. My reading (a guess, not checked): a generator function runs its body only
when it is iterated, later and from inside `insert_multiple`, so the recorder never sees a call at that line. If so, the sure arrow is right and
the count is conservative. Treat 99.3% as a floor for this project, not an exact figure.

**Recall (of the real project-to-project calls, how many had an arrow):**
| Scope | Real calls | Any arrow | Sure or maybe |
|---|---|---|---|
| tinydb's own code (source only) | 210 | **63.3%** | 37.6% |
| everything, tests included | 1,864 | 45.9% | 19.0% |

Why the source-only misses happened (77 calls): unresolved receiver (28), "receiver has no supported callable declaration" (14),
implicit calls such as operators and properties (9), unknown base classes (`TableBase`, `abc.MutableMapping`, `Generic[K, V]`: 9 together),
resolved to a different target than what ran (3).
For comparison, on the earlier practice projects real calls found were 32.7% (Shopkeep) and 44.8% (Ledgerly). Different projects, so only a rough guide.

## 3. Test shortlist against real test runs
Method: 30 commit pairs from tinydb's history (the newest commits that change 1 to 3 source files and at most 120 source lines, excluding
release, docs, typo and version commits). For each pair the real tests were run at both versions
(`repotrace.evaluation.test_score`). A test is **relevant** if it really executes a changed function.

| | Result |
|---|---|
| Pairs scored (all runs complete) | 30 |
| Pairs with a changed function and at least one relevant test | 25 |
| Relevant tests in those pairs | 1,243 (a parametrized fixture runs every test twice, `memory` and `json`) |
| Tests RepoTrace suggested | 119 |
| Suggested tests that really execute the change | **119 of 119 (100%)** |
| Relevant tests RepoTrace found | **119 of 1,243 (9.6%)** |
| Pairs where it suggested anything | 9 of 25 |
| Pairs with recall 0 / between 1% and 10% / 100% | 16 / 6 / 3 |
| Tests that went from pass to fail | **0 in 30 pairs: this part cannot be measured on tinydb** |

Plain reading: when it names a test it is right, but on tinydb it finds about one relevant test in ten. The same pattern as Queuely and Shopkeep.

## 4. What was not measured
- **Change detection.** `change_score` needs the author's answer key (`CHANGELOG_TRUTH.md`); tinydb has none. Only the changed-function lists in section 3 exist, unchecked against a key.
- **Failing tests** (no pass-to-fail case in the 30 pairs).
- A single project, a single workload (its own test suite), and one commit of it. Code the tests never run cannot be judged.

## 5. Why recall is low here (diagnosed, not yet fixed)
`TinyDB.__getattr__` forwards every unknown attribute to the default table:
```python
def __getattr__(self, name):
    return getattr(self.table(self.default_table_name), name)
```
The tests use a pytest fixture `db` (parametrized over two storages) and call `db.insert_multiple(...)`. The method lives on `Table`, so a static
reader finds **0 callers** for `Table.insert_multiple`, and no test is connected to changes in it. This is dynamic dispatch through `__getattr__`,
a known limit of static analysis. It is the largest single cause of misses here.
Ranked candidates, by evidence only: (1) classes that define `__getattr__` forwarding to another object: offer the forwarded class's methods as **maybe** links, clearly labelled; (2) known standard-library bases (`abc.MutableMapping`, `Generic`) so their classes still resolve; (3) a cap and ranking on suggestions, because on `markdown` one changed constructor suggests 758 tests.

## 5b. First fix attempt: class stored in a class attribute (18 Nov 2026, not merged)
Rule tried: when a class body says `table_class = Table` and code calls `self.table_class(...)`, treat it as a constructor call to `Table`
(a "maybe" link, refused if anything could reassign the attribute; subclasses that override it add their own candidates).
It worked in isolation: `self.query_cache_class(...)` in tinydb resolved to `LRUCache`. **Measured effect on call counts, before and after, on 9 projects:**
one extra "maybe" call on markdown, **no change on tinydb**, StudentManagement, Shopkeep, Ledgerly, Queuely, more-itertools, cachetools or python-dotenv.
Reason: the classes it points to have bases the resolver deliberately refuses (`TinyDB(TableBase)` where `TableBase = with_typehint(Table)` is a type-hint
trick that is `object` at run time; `LRUCache(abc.MutableMapping, Generic[K, V])`). The rule is correct but something else blocks it first,
the same lesson as the pytest fixture work. Not merged to keep the resolver simple; kept on the local branch `experiment/class-attribute-alias`.

What actually blocks calls, counted over non-test code of 8 projects (3,412 attribute calls the resolver refused, by its own stated reason):
| Reason | Calls |
|---|---|
| The callable is a computed expression (for example `a.b().c()`) | 482 |
| A parameter has no supported receiver evidence (`def f(storage): storage.read()`) | 457 |
| The receiver has no supported callable declaration | 304 |
| The receiver is reassigned or ambiguous | 244 |
| Only unconditional simple receiver assignments are inferred | 231 |
| Unknown base classes (not readable) | 155 |
| This attribute may also be assigned through another object | 135 |
Markdown alone is 1,461 of the 3,412, so these totals lean on it. Parameters without evidence and computed expressions are the biggest targets; no single rule fixes more than about one in seven.

## 6. A bug the website check found (fixed)
Running the guided screens on tinydb crashed the "Check a change" page. Three helper functions defined inside a test had no node in the graph and the screen
assumed every changed function had one. Fixed in `repotrace/report/lab_data.py` (a nested function is shown as the definition that holds it) and guarded in `app.js`;
test: `tests/test_lab_data.py::IdentityTests::test_a_changed_nested_function_is_shown_as_the_definition_that_holds_it`.
A page that fails to draw now shows a message instead of leaving the previous page on screen.

## 7. Repeat it
Needs Python 3.11 or newer for the test environment (tinydb needs 3.10+), Git, internet once for `pip install`.
```
git clone https://github.com/msiemens/tinydb.git repos/tinydb && git -C repos/tinydb checkout edff96aeb2616402e9489d46e90a592c05565621
python3.12 -m venv venvs/tinydb && venvs/tinydb/bin/pip install pytest pytest-cov pyyaml

# 1. map (static) and 2. recording of what really runs
mkdir -p eval/scratch && git -C repos/tinydb archive HEAD --prefix=src_copy/ | tar -x -C eval
python3 -m repotrace map $PWD/eval/src_copy --output $PWD/eval/map.json
PYTHONPATH=/path/to/repotrace venvs/tinydb/bin/python -m repotrace.evaluation.runtime_recorder --root $PWD/eval/src_copy --path . \
    --cwd $PWD/eval/scratch --lines --output trace.json -m pytest -- -q -p no:cacheprovider -o addopts= $PWD/eval/src_copy/tests
python3 -m repotrace.evaluation.trace_score --report eval/map.json --trace eval/scratch/trace.json --root eval/src_copy   # trace.json is written inside --cwd

# 3. test shortlist over 30 commit pairs (runs tinydb's tests in throwaway copies, network blocked)
python3 qa/real_projects/eval_tests.py repos/tinydb tinydb/ venvs/tinydb/bin/python results.json 30
```
The scripts run the project's own tests. Use throwaway copies of projects you have chosen yourself; the network block is a precaution, not a sandbox.

## 8. See it in the website
```
python3 -m repotrace --lab
```
Your browser opens. Type the path of your tinydb clone (for example `~/Desktop/real-projects/repos/tinydb`), press Enter, pick **Newer version**
`770486f fix: freeze unhashable args in Query.test...` and **Older version** `e70f9b1 fix: correct Table.update transform type hints`, press **Analyse**.
In this pair 82 tests are connected to the change. Compare with section 3 for the same pair: the scorer found 82 relevant tests and all 82 were suggested.
