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
`analyze.input._resolve_run_dirs_for_runner` does.
"""
from __future__ import annotations

import ast
import inspect
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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

    `analyze.input._resolve_run_dirs_for_runner` reports an input failure by
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


def test_a_failure_on_the_public_queue_reaches_the_record(tmp_path, monkeypatch):
    """At the boundary (§7.4 #1): what the record says, not what a flag says.

    The first version of this asserted `_journal_outcome() == "error"`, which
    is the value in the middle of the mechanism rather than the end of it. The
    claim is about the record on disk and about the resume banner that reads
    it, so that is what is measured here — through the sequence the app runs
    (§7.7 #1): open the journal, report the failure on the public queue the way
    a writer outside the runner does, close in the `finally`.
    """
    from services import run_journal as J

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    out = tmp_path / "out"
    out.mkdir()

    r = _runner()
    r._journal_spec = {}
    r._open_journal(tmp_path / "in", out, ["process_microscopy.py"], {})
    assert r.journal_token is not None, "the journal did not open; nothing below measures anything"

    # Exactly what `analyze.input._resolve_run_dirs_for_runner` does: the queue
    # is public and this writer never passes through `_enqueue_error`.
    r.log_q.put(("error", "Input error: no such folder\n"))
    r._close_journal()

    rec = J.read_journal(out)
    assert rec.outcome == "error", (
        f"a run that failed was recorded as {rec.outcome!r}, so the index "
        f"holds it complete and the banner will never offer it for resume")
    assert [e for e in J.unfinished_runs() if Path(e.get("output_dir", "")) == out], (
        "the failed run is not offered for resume")


def test_a_failure_before_the_journal_opens_leaves_the_previous_record_alone(
        tmp_path, monkeypatch):
    """The other half of the same claim, stated honestly.

    An input failure is reported *during resolution*, and the journal has no
    home until the output directory is resolved — so this run writes no record
    at all, rather than the `ok` the old wording claimed. That is the right
    outcome (there is nothing to write into), but it has a sharp edge: the
    directory may already hold the previous run's record, still open because
    the index is holding it for resume. A run that never started must not
    close it.

    That last assertion is a pin rather than a proof: no path today reaches
    ``_close_journal`` with the previous run's directory, and it is here so that
    a future one cannot arrive unnoticed. What bites in this test is the latch —
    disarming it makes ``_had_error`` read False for a failure the user will be
    shown.
    """
    from services import run_journal as J
    from services.pipeline_runner import PipelineRunner

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    out = tmp_path / "out"
    out.mkdir()
    in_dir = tmp_path / "in"
    in_dir.mkdir()

    # A previous run that died: open, never closed. This is what the banner
    # offers, and what the failed run below must not touch.
    J.open_run(out, input_dir=str(in_dir), mode="local", spec={})
    before = (out / J.JOURNAL_DIRNAME / "run.json").read_text()

    def _resolver(opts, log_q, proc_hook=None):
        log_q.put(("error", "Input error: no such folder\n"))
        return None

    r = PipelineRunner()
    r.start(Path("process_microscopy.py"),
            {"input_dir": str(in_dir), "output_dir": str(out),
             "filename_schema": "experiment:channel:well:fov:timepoint",
             "filename_sep": "_"},
            resolve_dirs=_resolver)
    for _ in range(400):
        if not r.is_busy:
            break
        time.sleep(0.02)
    assert not r.is_busy, "the run never finished"

    assert r._had_error is True, (
        "the dashboard would paint this failure as an ordinary end of run")
    assert r.journal_token is None and r.last_output_dir is None, (
        "the failed run opened a journal for a run that never started")
    assert (out / J.JOURNAL_DIRNAME / "run.json").read_text() == before, (
        "a run that failed before it started rewrote the previous run's "
        "record, which the index is holding open so it can be offered "
        "for resume")
    assert [e for e in J.unfinished_runs() if Path(e.get("output_dir", "")) == out], (
        "the previous run's resume offer was lost")


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
