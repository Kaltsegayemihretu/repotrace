# Small cross-file resolver example

Read these files with the analyzer; you do not need to run them.

- `users/utils.py` and `payments/utils.py` deliberately share the same filename
  and function name. Their paths keep their IDs separate.
- Each `prepare_*` function calls its own local `normalize`: two linked calls.
- `orders.py` imports `calculate_total` from `pricing.py`. The resolver follows
  that explicit import and links the call to the definition in `pricing.py`.

Expected result: 4 Python files, 6 symbols, 3 calls, 3 linked, 0 unresolved.
With `--local-only`, the earlier result remains: 2 linked and 1 unresolved.
Links describe potential targets under ordinary bindings, not guaranteed execution.
