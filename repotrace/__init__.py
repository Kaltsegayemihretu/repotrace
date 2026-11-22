"""RepoTrace: a map of a Python project, and what a change to it could affect.

The code is split into layers, each one built on the ones before it:
reading -> mapping -> graph -> changes -> report -> app / cli (evaluation sits beside them).
See docs/ARCHITECTURE.md for what each layer does and the one rule that keeps them tidy.
"""
