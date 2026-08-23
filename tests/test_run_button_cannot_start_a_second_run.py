"""Run stays disabled while a run is going, and the runner refuses a second one.

`_refresh_schema_preview` treated "the schema is valid" as sufficient to enable
Run — `self._run_btn.setEnabled(True)`, unconditional. It is reached from four
controls that stay live during a run: the segmentation-channel edit, every
fluorescent row's edit, add/remove row, and the schema picker. So a user setting
up their next plate while the current one ran re-armed the button by typing, and
pressing it started a second `process_microscopy` into the same output
directory.

`PipelineRunner.start` had no re-entrancy guard either, and that is the half
that matters: it overwrites `self._thread` and `self._proc`, so `stop()` can
only ever signal whichever process was assigned last, the first run becomes
unreachable, both write the same output directory, and both fire `finished` —
which makes a queue bridge start the next job twice.

These tests press the button rather than reading the flag (CLAUDE.md §7.6 #1):
the defect was reachable only by driving the real controls, and a test that
asserted `_running` would have passed throughout.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def _tab(qtbot):
    """A real AnalyzeTab with a valid schema, built offscreen."""
    from analyze_tab import AnalyzeTab

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    return tab


def test_typing_in_a_live_control_does_not_re_arm_run_mid_run(qtbot):
    """The reported shape: change a channel token while a plate is running."""
    tab = _tab(qtbot)
    assert tab._run_btn.isEnabled(), "the fixture's schema should be valid"

    tab._set_running_ui_state()
    assert not tab._run_btn.isEnabled(), "Run should be disabled during a run"

    # Each of the four controls that stay live and reach _refresh_schema_preview.
    tab._nuclear_token_edit.setText("DAPI")
    assert not tab._run_btn.isEnabled(), (
        "editing the segmentation channel re-enabled Run during a run")

    tab._fluor_add_row("mCherry")
    assert not tab._run_btn.isEnabled(), (
        "adding a channel row re-enabled Run during a run")

    if tab._fluor_rows:
        tab._fluor_rows[0][0].setText("GFP2")   # (edit, smfish_cb, phase_cb, remove_btn)
        assert not tab._run_btn.isEnabled(), (
            "editing a channel token re-enabled Run during a run")

    tab._refresh_schema_preview()
    assert not tab._run_btn.isEnabled(), (
        "a plain schema refresh re-enabled Run during a run")


def test_run_comes_back_when_the_run_ends(qtbot):
    """The gate must not be a one-way door."""
    tab = _tab(qtbot)
    tab._set_running_ui_state()
    assert not tab._run_btn.isEnabled()

    tab._running = False
    tab._refresh_schema_preview()
    assert tab._run_btn.isEnabled(), (
        "Run stayed disabled after the run ended")


def test_an_invalid_schema_still_disables_run(qtbot):
    """The original rule survives: a broken schema blocks Run on its own."""
    tab = _tab(qtbot)
    tab._schema_picker.schema_edit.setText("not:a:real:schema:at:all:here")
    tab._refresh_schema_preview()
    assert not tab._run_btn.isEnabled(), (
        "an invalid schema left Run enabled")


def test_the_runner_refuses_a_second_start_while_its_thread_lives():
    """The half the GUI cannot enforce, and the one that loses the first run."""
    import threading

    from services.pipeline_runner import PipelineRunner

    runner = PipelineRunner()
    release = threading.Event()

    def _resolve(_opts, _log_q, **_kw):
        release.wait(timeout=5)
        return None                     # abandon the run; we only need the thread

    runner.start(__import__("pathlib").Path("pm.py"), {}, resolve_dirs=_resolve)
    try:
        with pytest.raises(RuntimeError) as caught:
            runner.start(__import__("pathlib").Path("pm.py"), {},
                         resolve_dirs=_resolve)
        assert "already running" in str(caught.value), caught.value
    finally:
        release.set()
        if runner._thread is not None:
            runner._thread.join(timeout=5)


def test_the_runner_accepts_a_second_start_once_the_first_has_finished():
    """A queue runs job after job on one runner; the guard must not block that."""
    import pathlib
    import threading

    from services.pipeline_runner import PipelineRunner

    runner = PipelineRunner()
    calls: list[int] = []

    def _resolve(_opts, _log_q, **_kw):
        calls.append(1)
        return None

    runner.start(pathlib.Path("pm.py"), {}, resolve_dirs=_resolve)
    assert runner._thread is not None
    runner._thread.join(timeout=5)
    assert isinstance(runner._thread, threading.Thread)

    runner.start(pathlib.Path("pm.py"), {}, resolve_dirs=_resolve)
    runner._thread.join(timeout=5)
    assert len(calls) == 2, (
        f"the guard blocked a legitimate sequential run: {len(calls)} of 2")
