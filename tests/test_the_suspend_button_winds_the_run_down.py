"""Pressing Suspend winds the run down, whatever the record does (audit L11).

Suspend does two things in order: it writes a pointer to the output folder so
the app can offer to resume, and it asks the run to stop taking on new
FOV-groups. The order is deliberate — a run that wound down having left nothing
behind to resume it with is the worse failure — but "first" was implemented as
"and only if that worked".

The record write and the wind-down shared one ``try``, so anything escaping the
write returned from the slot. The user pressed the button precisely because they
wanted the work kept, the run carried straight on, and nothing on screen said
so. The escape was not hypothetical: ``suspend_record._write_all`` caught
``OSError`` alone while the record carries a ``spec`` the caller handed in, so
one unserialisable value in it raised a ``TypeError`` from ``json.dump``.

CLAUDE.md §7.6 — these press the button. Nothing in the repo did.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def tab(qtbot, monkeypatch, tmp_path):
    import analyze.form

    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)

    # The real runner, put into the state a running run leaves it in, through
    # the attributes its own properties read: a live subprocess handle makes
    # ``is_running`` true, and ``_last_output_dir`` is what the resolver sets.
    # A stand-in would have to reimplement the teardown the tab performs on
    # close, and would then be testing the stand-in.
    class _LiveProc:
        def poll(self):
            return None

    runner = t._runner
    runner._proc = _LiveProc()
    runner._last_output_dir = tmp_path
    runner.suspended = False

    def _suspend() -> None:
        runner.suspended = True

    runner.suspend = _suspend
    t._suspend_btn.setEnabled(True)
    return t


def test_pressing_suspend_asks_the_run_to_wind_down(tab, tmp_path, monkeypatch):
    monkeypatch.setattr("services.suspend_record.record_path",
                        lambda: tmp_path / "state" / "suspended.json")
    tab._suspend_btn.click()
    assert tab._runner.suspended, "the button did not reach the runner at all"


def test_a_record_that_cannot_be_written_still_winds_the_run_down(
        tab, monkeypatch):
    """The reported shape: the write fails and the run must still stop."""
    import analyze.suspend

    monkeypatch.setattr(analyze.suspend, "save",
                        lambda run: (_ for _ in ()).throw(OSError("disk gone")))
    tab._suspend_btn.click()
    assert tab._runner.suspended, (
        "an exception from the resume record returned out of the slot, so the "
        "run kept going after the user pressed Suspend and nothing said so"
    )


def test_a_form_that_cannot_be_collected_still_winds_the_run_down(tab, monkeypatch):
    """The other way in: the spec is built before the record is ever written."""
    monkeypatch.setattr(type(tab), "_collect_run_spec",
                        lambda self: (_ for _ in ()).throw(ValueError("bad form")))
    tab._suspend_btn.click()
    assert tab._runner.suspended


def test_a_run_that_has_not_resolved_its_output_folder_is_refused(tab, monkeypatch):
    """...but a run with nowhere to resume from is told, not suspended."""
    from PySide6.QtWidgets import QMessageBox

    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda _p, _t, text, *a, **k: warned.append(text)))
    tab._runner._last_output_dir = None
    tab._suspend_btn.click()
    assert not tab._runner.suspended
    assert warned, "the refusal was silent"


def test_a_run_that_is_not_going_is_left_alone(tab):
    tab._runner._proc = None                    # is_running now answers False
    tab._suspend_btn.click()
    assert not tab._runner.suspended


# ── the button at the moments it used to do the wrong thing ─────────────────

_REAL_HANDLER_PIPELINE = '''
import sys, time
sys.path.insert(0, {repo!r})
from pipeline import suspend
suspend.install_handlers()      # the real handler: a second SIGTERM kills
print("Zip mode: 3 wells", flush=True)
for w in ("A01", "A02", "A03"):
    if suspend.is_suspended():
        break
    print(f"Well {{w}} F001 starting", flush=True)
    time.sleep(0.8)
    print(f"Well {{w}} F001 done.", flush=True)
'''


@pytest.fixture
def live_tab(qtbot, monkeypatch, tmp_path):
    """A tab whose runner runs a stand-in with the pipeline's real handler."""
    import sys
    from pathlib import Path

    import analyze.form
    import services.pipeline_runner as pr

    repo = str(Path(__file__).resolve().parent.parent)
    script = tmp_path / "fake.py"
    script.write_text(_REAL_HANDLER_PIPELINE.format(repo=repo))
    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
    monkeypatch.setattr(pr, "build_pipeline_args",
                        lambda p, i, o, opts: [sys.executable, str(script)])
    monkeypatch.setattr("services.suspend_record.record_path",
                        lambda: tmp_path / "state" / "suspended.json")
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    plate = tmp_path / "plate"
    (plate / "in").mkdir(parents=True)
    (plate / "out").mkdir()
    t.plate, t.script = plate, script
    return t


def _start(tab, *, resolve_delay: float = 0.0):
    import time

    from services.run_spec import RunSpec

    def _resolve(opts, log_q, **kw):
        time.sleep(resolve_delay)
        return (tab.plate / "in", tab.plate / "out", None)

    tab._resolve_run_dirs_for_runner = _resolve
    spec = RunSpec.from_json({"opts": {
        "raw": str(tab.plate), "filename_schema": "experiment:channel:well:fov:timepoint",
        "filename_sep": "_", "nuclear_token": "DAPI", "fluor_tokens": ["GFP"]}})
    finished: list = []
    tab.jobFinished.connect(lambda *a: finished.append(a))
    assert tab.start_run(spec, pipeline=tab.script) is None
    return finished


def test_a_second_click_does_not_kill_the_run_it_is_winding_down(live_tab, qtbot):
    finished = _start(live_tab)
    qtbot.waitUntil(lambda: "A01 F001 starting" in live_tab._log.toPlainText(),
                    timeout=10_000)
    live_tab._suspend_btn.click()
    live_tab._suspend_btn.click()
    qtbot.waitUntil(lambda: bool(finished), timeout=20_000)
    log = live_tab._log.toPlainText()
    assert "exited with code -15" not in log, "the second click hard-killed the run"
    assert "[Suspended]" in log
    assert "Already winding down" in log


def test_suspend_while_the_input_is_still_being_prepared_says_so(
        live_tab, qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda _p, _t, text, *a, **k: told.append(text)))
    finished = _start(live_tab, resolve_delay=1.5)
    qtbot.waitUntil(lambda: live_tab._runner.is_busy, timeout=5_000)
    assert not live_tab._runner.is_running
    live_tab._suspend_btn.click()
    assert told and "nothing to suspend yet" in told[0], (
        "Suspend during grouping did nothing and said nothing")
    qtbot.waitUntil(lambda: bool(finished), timeout=20_000)


def test_closing_the_window_while_suspending_leaves_the_run_to_finish():
    """Closing used to Stop — a hard kill of the wells Suspend was keeping."""
    import types

    import allwell

    stopped: list = []
    runner = types.SimpleNamespace(is_running=True, was_suspended=True,
                                   stop=lambda: stopped.append(True))
    allwell._stop_analyze_runner(types.SimpleNamespace(_runner=runner, _alive=True))
    assert stopped == []
    runner.was_suspended = False
    allwell._stop_analyze_runner(types.SimpleNamespace(_runner=runner, _alive=True))
    assert stopped == [True], "an ordinary running run must still be stopped on close"
