"""Audit L1 — quitting mid-run must stop the Analyze pipeline runner.

AnalyzeTab is a child widget, so its own closeEvent never fires; the app root's
closeEvent must stop the runner instead, or the local subprocess is orphaned and
a remote run leaks its SLURM allocation + staging + scratch. Tests the extracted
`_stop_analyze_runner` helper (the full AllWellApp.closeEvent needs a live Qt
top-level window and is exercised by the slow full-app suite).
"""
from __future__ import annotations

import threading
import types

from allwell import _stop_analyze_runner


def test_stops_a_running_runner():
    calls = []
    runner = types.SimpleNamespace(is_running=True, stop=lambda: calls.append("stop"))
    _stop_analyze_runner(types.SimpleNamespace(_runner=runner))
    assert calls == ["stop"]


def test_noop_when_not_running():
    calls = []
    runner = types.SimpleNamespace(is_running=False, stop=lambda: calls.append("stop"))
    _stop_analyze_runner(types.SimpleNamespace(_runner=runner))
    assert calls == []


def test_safe_when_pane_or_runner_absent():
    _stop_analyze_runner(None)                       # no analyze pane
    _stop_analyze_runner(types.SimpleNamespace())    # pane without a _runner
    # (no exception = pass)


def test_stop_failure_is_swallowed_not_propagated():
    def _boom():
        raise RuntimeError("stop failed")
    runner = types.SimpleNamespace(is_running=True, stop=_boom)
    # A failing stop() during shutdown must not block the app from closing.
    _stop_analyze_runner(types.SimpleNamespace(_runner=runner))


def test_stop_analyze_runner_also_stops_the_log_poller():
    # Audit L2 — closing must stop _poll_log re-arming its QTimer.
    analyze = types.SimpleNamespace(
        _alive=True,
        _runner=types.SimpleNamespace(is_running=False, stop=lambda: None),
    )
    _stop_analyze_runner(analyze)
    assert analyze._alive is False


# ── a remote run: the close must outlast the scancel ─────────────────────────

class _LiveProc:
    def poll(self):
        return None


class _SlowRemote:
    """A remote whose cancel takes as long as an ssh round trip to a far host."""

    def __init__(self):
        self.cancelled = []

    def cancel(self, proc):
        import time
        time.sleep(0.5)
        self.cancelled.append(proc)


def _remote_runner(monkeypatch):
    import services.pipeline_runner as pr
    monkeypatch.setattr(pr, "signal_subprocess", lambda proc, **k: None)
    r = pr.PipelineRunner()
    r._proc = _LiveProc()
    r._remote_runner = _SlowRemote()
    # The run thread, which ends once it sees the stop, as the real one does.
    r._thread = threading.Thread(target=lambda: r._stop_requested.wait(5),
                                 daemon=True)
    r._thread.start()
    return r


def test_the_close_says_what_it_is_waiting_for_while_it_waits(monkeypatch):
    """The bounded wait blocks the GUI thread for up to 25 s. With nothing on screen the window just froze;
    the notice must be up before the wait starts and come down only after it has finished."""
    from allwell import CLOSE_WAIT_NOTICE

    runner = _remote_runner(monkeypatch)
    seen = []

    def notice(text):
        seen.append(("shown", text, list(runner._remote_runner.cancelled)))
        return lambda: seen.append(("dismissed", list(runner._remote_runner.cancelled)))

    _stop_analyze_runner(types.SimpleNamespace(_runner=runner), notice=notice)
    assert seen == [("shown", CLOSE_WAIT_NOTICE, []), ("dismissed", [runner._proc])], (
        f"the notice did not bracket the wait for the remote cancel: {seen}")


def test_no_notice_when_there_is_nothing_to_wait_for():
    seen = []
    idle = types.SimpleNamespace(is_running=False, stop=lambda: None, wait_for_teardown=lambda: None)
    _stop_analyze_runner(types.SimpleNamespace(_runner=idle), notice=seen.append)
    suspended = types.SimpleNamespace(is_busy=True, was_suspended=True, stop=lambda: None,
                                      wait_for_teardown=lambda: None)
    _stop_analyze_runner(types.SimpleNamespace(_runner=suspended), notice=seen.append)
    assert seen == []


def test_a_failing_notice_does_not_skip_the_wait():
    waited = []

    def boom(_text):
        raise RuntimeError("no screen")

    runner = types.SimpleNamespace(is_busy=True, stop=lambda: None,
                                   wait_for_teardown=lambda: waited.append(True))
    _stop_analyze_runner(types.SimpleNamespace(_runner=runner), notice=boom)
    assert waited == [True]


def test_the_close_notice_is_painted_opaque_with_the_wait_cursor(qapp):
    """`AllWellApp._show_close_notice`, the notice the real close passes: a toast held at full opacity (its fade
    would not run while the thread is blocked) and the wait cursor, both undone by the dismisser."""
    from PySide6.QtWidgets import QApplication, QWidget

    from allwell import AllWellApp
    from widgets.toast import Toast

    host = QWidget()
    try:
        host.show()
        dismiss = AllWellApp._show_close_notice(host, "waiting")
        toasts = [w for w in QApplication.topLevelWidgets() if isinstance(w, Toast) and w.isVisible()]
        assert toasts and toasts[-1].windowOpacity() == 1.0, "the notice was not on screen at full opacity"
        assert QApplication.overrideCursor() is not None, "no wait cursor while the close blocks"
        dismiss()
        assert QApplication.overrideCursor() is None
        assert not toasts[-1].isVisible()
    finally:
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        host.close()


def test_the_real_close_passes_the_notice():
    """Building `AllWellApp` is a slow-lane cost, so its `closeEvent` is read rather than run: the call that
    stops the runner must hand it the window's notice, or every test above measures a seam the app never uses."""
    import ast
    import inspect
    import textwrap

    from allwell import AllWellApp

    tree = ast.parse(textwrap.dedent(inspect.getsource(AllWellApp.closeEvent)))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "_stop_analyze_runner"]
    assert calls, "closeEvent no longer stops the Analyze runner"
    assert any(kw.arg == "notice" and ast.unparse(kw.value) == "self._show_close_notice"
               for c in calls for kw in c.keywords), "closeEvent stops the runner without the on-screen notice"


def test_closing_waits_for_the_remote_cancel_to_reach_the_host(monkeypatch):
    """The Stop's ssh `kill` + `scancel` run on a daemon thread, which the
    interpreter ends at exit: returning before it finished left the SLURM job
    holding its node after the app had gone."""
    runner = _remote_runner(monkeypatch)
    _stop_analyze_runner(types.SimpleNamespace(_runner=runner))
    assert runner._remote_runner.cancelled == [runner._proc], (
        "the app closed before the remote job was cancelled")


class _StopEvent(threading.Event):
    """The runner's stop flag, which also interrupts the stand-in upload."""

    def __init__(self, upload: threading.Event):
        super().__init__()
        self._upload = upload

    def set(self):
        super().set()
        self._upload.set()


def test_closing_during_the_upload_still_stops_the_run(monkeypatch):
    """No pipeline subprocess exists while the input uploads, so a close keyed
    on `is_running` stopped nothing and the run thread was killed mid-upload."""
    import services.pipeline_runner as pr
    r = pr.PipelineRunner()
    release = threading.Event()     # the upload, which a stop interrupts
    r._stop_requested = _StopEvent(release)
    r._thread = threading.Thread(
        target=lambda: release.wait(5) or r._stop_requested.wait(5), daemon=True)
    r._thread.start()
    assert r.is_busy and not r.is_running
    try:
        _stop_analyze_runner(types.SimpleNamespace(_runner=r))
        assert r._stop_requested.is_set(), "a close during the upload did not stop it"
    finally:
        release.set()
