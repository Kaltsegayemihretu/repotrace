# RepoTrace: small checks with useful next actions

Reviewed 29 October 2026. This is an incremental plan, not a promise that every feature below exists.

## The product's job

Help a developer answer: **What might I have forgotten after this change, where is the evidence, and what should I check next?**

A large graph is supporting evidence, not the main result. Keep one review screen. Give each finding a location, a specific explanation, an action, and a limitation. Separate a demonstrated argument mismatch from a possible relationship and from a name-only guess. Do not add a numerical “safe to merge” score.

## Problems worth addressing

The sources below document mechanisms and existing approaches. They are not a survey measuring how frequently all developers encounter these problems. The priorities are a judgment for this project.

| Developer's question | Small useful response | State / boundary |
|---|---|---|
| “I changed a function's inputs. Did I forget a caller?” | Show a call that fits the old parameters but not the new ones, with its exact location. | Implemented in this update for supported ordinary/nested function calls. Not all Python calls. |
| “I removed a function. Does anything still use its old name?” | Keep old caller evidence and current references, distinguishing uncertain name matches. | Already present. Preserve that distinction; a name match alone is not proof. |
| “Which tests should I check first?” | Suggest tests connected to the changed behavior, explain each connection, and say when evidence is absent. | Some test callers already appear. Next: a concise test checklist; later accept optional per-test coverage evidence. Never call an unlinked function untested solely because no test caller was found. |
| “My branch worked, but main changed while I was working.” | Compare against current main and preview integration; distinguish textual conflicts from potentially incompatible behavior. | Planned. Do not merge, reset, push or modify the user's branch automatically. |
| “I changed shared test setup; why did unrelated tests fail?” | Show tests that depend on the changed pytest fixture, including visible autouse fixtures. | Later, a narrowly scoped pytest adapter. Fixture selection depends on scope, overrides and plugins, not just name matching. |
| “Someone crossed a boundary in our design.” | Report a newly introduced import that violates a rule the team explicitly supplied. | Later, optional rules such as “UI must not import database internals.” Do not invent the intended architecture. |

## Why these are grounded choices

- Python provides argument-to-parameter binding rules via [Signature.bind](https://docs.python.org/3/library/inspect.html#inspect.Signature.bind). RepoTrace constructs signatures from AST metadata with inert placeholder values; it does not inspect or import the analyzed functions at runtime.
- [GitHub merge queues](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue) validate changes combined with the latest target branch and queued changes. This supports the need for current integration evidence; RepoTrace should complement existing Git/CI workflows, not pretend to replace them.
- [Detecting Semantic Conflicts with Unit Tests](https://arxiv.org/abs/2310.02395) studies behavioral conflicts that textual merging can miss. It supports treating a clean merge as insufficient evidence of correct behavior, not claiming that our static analysis can detect every such conflict.
- [pytest's fixture documentation](https://docs.pytest.org/en/stable/reference/fixtures.html) explains fixture dependencies and autouse behavior. This is why ordinary function-call edges alone cannot capture all test relationships.
- [Coverage.py measurement contexts](https://coverage.readthedocs.io/en/latest/contexts.html) can record which test executed a line. Imported coverage could strengthen a test recommendation, but execution is not proof that a test asserts the right behavior. Require matching source revision and record the test environment.
- [Import Linter's forbidden contracts](https://import-linter.readthedocs.io/en/stable/contract_types/forbidden/) demonstrate explicit import-boundary rules. Integrating a proven rule engine may be better than building a broad new architecture engine.

## What this update actually adds

`signature_review.py` consumes the already analyzed before/after reports and changed-definition list. It does not run project code or add dependencies. It checks the newer version's calls against both parameter sets. Only a call that fits the old set and fails the new set becomes a finding.

Included: new required parameters, keyword renames/removals, excess positional arguments, positional-only and keyword-only changes. Optional parameters and variadic definitions follow Python binding rules. Types, return values and semantic correctness are not checked.

Excluded: method targets, constructors, decorated targets, uncertain/unresolved targets, annotation contexts and calls with `*args`/`**kwargs` expansion. Existing resolver gaps remain gaps; in particular, unresolved module-level calls are not guessed. A call already incompatible with the old signature is not presented as a newly introduced mismatch.

The check scans at most 100,000 call records and stores at most 100 findings per comparison. Counts and limits are disclosed. The existing changed-function budget also applies. No findings means only “none found within these rules,” never “safe.”

Integration is additive to the existing change-report format: top-level `signature_review` coverage/policy, and a per-change `signature_review` with findings. Each finding records its call ID, file/span, caller, target, binding explanation and suggested action. Commit identities remain on the enclosing report. Existing reports without these optional fields still open. No resolver confidence label is upgraded.

The dashboard adds **Calls to update** inside the changed function's existing panel and text list, plus a summary prompt. Individual call snippets expand on demand. Snippets come from the pinned newer commit, not a potentially modified working copy.

### Try it

Restart RepoTrace. In the built-in example shop choose **A required parameter was added, but callers were forgotten**. The function `calculate_total` now requires `currency`; `place_order` and `Cart.total` still supply only `price`. Open each of the two findings to see the line and missing parameter.

An already running app or an old exported report will not acquire findings automatically; restart and analyze again.

## The next two increments

1. **Test checklist:** reuse existing call paths to list relevant test candidates beside each finding. Show why each test is suggested. Later add opt-in coverage import with revision validation. Do not run arbitrary repository tests automatically.
2. **Branch integration review:** choose branch A and target branch B, pin both commits and their common ancestor, compare each branch's changes, and preview their combination in an isolated environment. First report real Git conflicts. If the preview is clean, apply supported checks to the combined code and show cross-branch dependency interactions as review candidates. If the preview cannot be produced, say so. A branch changing a function while another adds an old-style caller is the first acceptance scenario. Identical starting times, commit timing or shared files alone do not prove a conflict.

After those are useful in real repositories, consider scoped pytest-fixture links and optional architecture rules. RAG can later explain the recorded evidence and answer repository questions with file/line citations. It must not invent missing links or turn uncertainty into certainty.

## Keep the scope calm

- One workflow: choose versions → inspect concrete findings → check affected code/tests.
- Put actionable findings first; keep the graph and uncertainty details available underneath.
- Bound scans and displayed results; disclose skipped analysis and stale evidence.
- Measure warning usefulness on hand-checked examples and real changes, not only the percentage of calls linked.
- Do not add chat, automatic fixes, generated tests, live team tracking and merge automation at the same time.
