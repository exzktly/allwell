"""A failure the runner reports must be recorded as a failure.

`_enqueue_error` does three things: it sets `_had_error`, and it latches
whether a Stop or a Suspend was in flight at that instant. `_journal_outcome`
reads all three to decide what the run journal records, and the resume banner
reads the journal.

One site built its `("error", …)` tuple by hand — the branch for a remote run
that finished and then failed local verification. So the run was journalled
`ok`: the index held it as complete, the banner never offered it, and the
remote files it had explicitly preserved for retry were never fetched. The
message said "preserved for retry" and nothing could retry it.

This is a syntactic rule because the alternative — noticing in review that a
new tuple duplicates the helper — is what already failed once.
"""
from __future__ import annotations

import ast
import inspect

from services import pipeline_runner


def _error_tuples(tree: ast.AST) -> list[int]:
    """Line numbers where a two-element tuple starting with "error" is built."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Tuple) or not node.elts:
            continue
        first = node.elts[0]
        if isinstance(first, ast.Constant) and first.value == "error":
            out.append(node.lineno)
    return out


def test_the_scan_can_see_the_module():
    """Fail closed: a guard reading nothing passes for ever."""
    src = inspect.getsource(pipeline_runner)
    assert "_enqueue_error" in src and len(src) > 10_000, len(src)


def test_no_error_tuple_is_built_outside_the_one_seam():
    src = inspect.getsource(pipeline_runner)
    tree = ast.parse(src)

    allowed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_enqueue_error":
            allowed = {n for n in range(node.lineno, (node.end_lineno or node.lineno) + 1)}
            break
    assert allowed, "_enqueue_error is gone; this guard names a seam that no longer exists"

    offenders = [ln for ln in _error_tuples(tree) if ln not in allowed]
    assert not offenders, (
        "these build an ('error', …) event without going through "
        "_enqueue_error, so `_had_error` is never set and the run journals "
        "`ok` for a run that failed — the resume banner then never offers it. "
        f"Lines (relative to the module source): {offenders}")


def test_the_verification_failure_goes_through_the_seam():
    """The specific site, asserted as a call rather than as nearby text.

    The first version of this test looked for `_enqueue_error` in the 400
    characters before the message. That window also contains the *previous*
    branch's legitimate call, so it passed against the unfixed code and
    measured nothing — §7.3's opening warning, made while writing the guard
    for it.
    """
    src = inspect.getsource(pipeline_runner)
    tree = ast.parse(src)
    MESSAGE = "local verification failed"

    def _mentions(node: ast.AST) -> bool:
        return any(isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and MESSAGE in n.value for n in ast.walk(node))

    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and _mentions(n)]
    assert calls, f"the {MESSAGE!r} branch is gone; this test names a site that no longer exists"

    names = {n.func.attr for n in calls if isinstance(n.func, ast.Attribute)}
    assert "_enqueue_error" in names, (
        f"the {MESSAGE!r} failure is reported by {sorted(names)}, not by "
        f"_enqueue_error, so `_had_error` is never set and the run is "
        f"journalled `ok`")
