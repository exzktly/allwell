"""A failure the run reports must be recorded as a failure.

Three pieces of per-run bookkeeping ride on a failure: `_had_error`, and the
latched answer to "was a Stop or a Suspend in flight at that instant".
`_journal_outcome` reads all three to decide what the run journal records, the
resume banner reads the journal, and the dashboard reads the latch to choose
between an amber "Cancelled" and a red failure with the raw log forced open.

Two writers got it wrong, in the two ways available.

Inside the runner, one site built its `("error", …)` tuple by hand — the branch
for a remote run that finished and then failed local verification. So the run
was journalled `ok`: the index held it as complete, the banner never offered it,
and the remote files it had explicitly preserved for retry were never fetched.
The message said "preserved for retry" and nothing could retry it. The AST rule
below is what keeps that from coming back, and it is syntactic because the
alternative — noticing in review that a new tuple duplicates the helper — is
what already failed once.

Outside it, `log_q` is public and the Analyze pane's input resolver is handed
the queue directly, so its own failures never passed through the runner at all.
No rule inside `pipeline_runner.py` can see that writer, and the bookkeeping
does not belong to any one writer anyway — so it moved to the queue, and the
tests at the bottom of this file drive it from outside the class exactly as
`analyze_input._resolve_run_dirs_for_runner` does.
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


# ── the latch fires for a writer the runner does not own ─────────────────────

def _runner():
    from services.pipeline_runner import PipelineRunner
    return PipelineRunner()


def test_a_stop_during_grouping_is_not_painted_as_a_failure():
    """The reported bug: red dashboard and a forced-open log for a user's Stop.

    `analyze_input._resolve_run_dirs_for_runner` reports an input failure by
    putting straight onto the public queue, so pressing Stop while a plate was
    grouping produced a genuine `("error", …)` — the zipper killed by signal —
    that had never been through the latch. The GUI read `(False, False)`, chose
    the failure branch, and headlined it with a signal exit code, while the
    journal recorded `stopped`.
    """
    r = _runner()
    r._stop_requested.set()
    r.log_q.put(("error", "Input error: WellPlateZipper exited with code -15.\n"))
    assert r.cancel_state_at_failure == (True, False), (
        "the GUI would read this as a failure rather than the user's own Stop, "
        "paint the dashboard red and force the raw log pane open"
    )


def test_a_suspend_during_grouping_is_latched_the_same_way():
    r = _runner()
    r._suspend_requested.set()
    r.log_q.put(("error", "Input error: cancelled\n"))
    assert r.cancel_state_at_failure == (False, True)


def test_an_input_failure_with_no_stop_is_journalled_as_a_failure():
    """A run that never started must not be recorded as one that succeeded.

    `_had_error` was set in `_enqueue_error` only, so an input folder that no
    longer exists — a real failure, no Stop in play — journalled `ok`. The index
    then held it complete and it was never offered for resume.
    """
    r = _runner()
    r.log_q.put(("error", "Input error: no such folder\n"))
    assert r._journal_outcome() == "error", (
        "a run that failed during input resolution was journalled as a "
        "successful run, so nothing will ever offer it for resume"
    )


def test_an_ordinary_line_latches_nothing():
    r = _runner()
    r._stop_requested.set()
    r.log_q.put(("line", "error: this is log output, not an event\n"))
    r.log_q.put(("zipper_well", "A01"))
    assert r.cancel_state_at_failure == (False, False)
    assert r._journal_outcome() == "stopped"


def test_put_nowait_latches_too():
    """CPython routes it through `put`; assert that rather than trusting it."""
    r = _runner()
    r._stop_requested.set()
    r.log_q.put_nowait(("error", "boom\n"))
    assert r.cancel_state_at_failure == (True, False)
    assert r._had_error is True
