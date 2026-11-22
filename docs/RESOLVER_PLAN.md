# Resolver improvement plan (18 Nov 2026)

Goal: more real calls covered by **sure or maybe** arrows, with precision unchanged. Every step is kept only if the benchmark below says so.

## The evidence the plan is built on
Five development projects now have a full recording of their own test suites (tinydb, markdown, cachetools, python-dotenv,
more-itertools; all suites pass while recorded). `qa/real_projects/miss_census.py` lists every real source-to-source call the map does not
cover with sure or maybe, and why. 892 such calls, by family (upper bounds: a fixed family can still be blocked by another link):

| Family | tinydb | markdown | cachetools | dotenv | more-itertools | Total |
|---|---|---|---|---|---|---|
| F1 class with an unreadable or builtin base (`TableBase`, `HTMLParser`, `MutableMapping`, `dict`, `Protocol`) | 31 | 91 | 108 | 0 | 4 | **234** (+ part of 67 "no supported callable declaration") |
| F4 implicit calls (operators, property reads, `next()`/`iter()` on project objects, `with`) | 9 | 13 | 119 | 3 | 6 + 37 | **187** |
| F2 local receiver assigned in a branch, a loop, or more than once | 0 | 90 | 7 | 0 | 0 | **97** |
| F3 parameter with no type evidence (`md: Markdown` imported under `if TYPE_CHECKING`, unannotated overrides) | 8 | about 58 | 0 | 1 | 0 | **about 67** |
| F6 dynamic: callables passed in, registries, `getattr` | 15 | 10 | 27 | 1 | 3 | 56 (out of scope) |
| F5 calls at module, class or decorator level | 1 | 11 | 0 | 16 | 1 | 29 |
| F8/F9/F7 field typing, `super()`, decorated methods | 0 | 11 | 30 | 0 | 2 | 43 |

## Step 0: one benchmark command (half a day)
`qa/real_projects/resolver_bench.sh`: maps the five projects, scores each against its recording, prints sure and maybe precision, strong recall,
any-tier recall and the family table, and compares with a saved baseline. **A change is kept only if precision does not drop, strong recall rises,
and all unit tests pass.** Before/after numbers go into the evaluation report every time.

## Step 1: readable bases (F1, the largest family; 2 days)
1a. Treat builtin and standard-library abstract bases like the `Generic` fix: `object`, `dict`, `list`, `str`, `Exception` family,
`collections.abc.*`, `typing.Protocol`. Refine the reserved-name rule: a name is refused only if the base sets it on instances, or if no
project class earlier in the method order defines it (an own override such as `LRUCache.get` comes first in Python's lookup).
1b. A class with an unreadable base (`TinyDB(TableBase)`, markdown's `HTMLParser` loaded through importlib): a method defined in the class itself,
or in its first listed base when that is a readable project class, is still found first. Keep it **maybe**, with the assumption written into the
evidence ("the unreadable base does not intercept attribute lookup"). Constructors of such classes stay refused.

## Step 2: parameter types (F3; 1.5 days)
2a. Annotations whose class is imported under `if TYPE_CHECKING:` (that is what such imports are for), including `X | None` and `Optional[X]`.
2b. An unannotated override takes the annotation of the same parameter in the method it overrides (`extendMarkdown(self, md)` overriding `Extension.extendMarkdown(self, md: Markdown)`).
Both stay maybe with the existing "type hints are not enforced" warning.

## Step 3: locals assigned in several places (F2; 1.5 days)
When a local is assigned in branches or loops and **every** assignment is typed, offer the union as maybe candidates. Any untyped, augmented,
loop-target, `with`, `del`, `global` or `nonlocal` binding refuses, as today.

## Step 4: implicit calls (F4; 1.5 days)
Extend the existing implicit-call support to builtins that dispatch to project dunders (`next`, `iter`, `len`, `str`, `repr`, `bool`, `hash`) and to
reads of `@property` attributes on a typed receiver.

## Step 5, only if time: module-level calls (F5)
Bare calls at module level to functions defined earlier in the same module or imported (python-dotenv's 16).

## Out of scope
F6, callables passed in, registries and `getattr`. Say so in the report: source text alone cannot decide them reliably.

## Timeline and guardrails
- Steps 0-4 by about 27 Nov. Then the resolver is frozen and `click` is scored once, blind (still untouched), and reported as it comes out.
- One step per commit, each with tests for the new case and for refusals (unknown names, shadowing, mutation). The layer rule and the
  "never sure without new evidence" rule stay.
- The five projects are development sets now; their gains are not proof. Only the blind project counts as evidence.
- Expect less than the ceilings: earlier fixes (class attributes, argument propagation) moved almost nothing because another link blocked the path.
