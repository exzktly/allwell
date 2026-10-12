"""A launch that takes minutes must leave the line it was stuck on.

The boot log's milestones say which *step* a slow launch was in — "building
main window (AllWellApp) …" and nothing after it, for fifteen minutes. That
names a step of several thousand lines. ``boot_diag.arm_stall_watchdog`` samples
the GUI thread's stack whenever its heartbeat goes quiet, so the same log names
the call instead; and the log that holds such a report survives the relaunch a
user makes after a launch that slow, the way a crashed log does.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402

from startup import boot_diag  # noqa: E402


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    """Point boot_diag at a temp directory and reset its per-process state."""
    monkeypatch.setattr(boot_diag, "user_state_dir", lambda: tmp_path)
    monkeypatch.setattr(boot_diag, "_LOG_PATH", None, raising=False)
    monkeypatch.setattr(boot_diag, "_LOG_FILE", None, raising=False)
    yield tmp_path
    handle = boot_diag._LOG_FILE
    if handle:
        handle.close()


def _stuck_in_a_stat_on_a_dead_share(seconds: float) -> None:
    """Named so the report can be checked for *where* it says the thread was."""
    time.sleep(seconds)


def _watching(threshold_s: float):
    """Run the watchdog loop on a thread the test owns, and can stop."""
    stop = threading.Event()
    thread = threading.Thread(target=boot_diag._watch,
                              args=(threshold_s, 0.02, stop), daemon=True)
    thread.start()
    return stop, thread


def test_a_stall_records_the_line_the_gui_thread_was_stuck_on(logdir):
    boot_diag.boot("building main window (AllWellApp) …")
    stop, thread = _watching(0.15)
    try:
        _stuck_in_a_stat_on_a_dead_share(0.6)
        boot_diag.boot("main window built; showing")
        time.sleep(0.1)
    finally:
        stop.set()
        thread.join(2.0)

    log = boot_diag.log_path().read_text()
    assert boot_diag._STALL_MARKER in log, "a 0.6 s stall was not reported"
    report = log.split(boot_diag._STALL_MARKER, 1)[1]
    assert "_stuck_in_a_stat_on_a_dead_share" in report, (
        "the report does not name the function the GUI thread was stuck in")
    # Reported again at the doubling, so the log shows it stayed put.
    assert log.count(boot_diag._STALL_MARKER) >= 2
    assert "GUI thread answered again" in log


def test_a_responsive_gui_thread_writes_no_report(logdir):
    """A report on every launch is the noise that gets real ones ignored."""
    stop, thread = _watching(0.3)
    try:
        for _ in range(10):
            boot_diag.boot("tick")
            time.sleep(0.05)
    finally:
        stop.set()
        thread.join(2.0)
    assert boot_diag._STALL_MARKER not in boot_diag.log_path().read_text()


def test_a_milestone_from_another_thread_is_not_a_heartbeat(logdir, monkeypatch):
    """The Qt message handler logs from whichever thread raised the warning. If
    that counted, a worker logging while the GUI thread is stuck would hide the
    very stall it is stuck in."""
    monkeypatch.setattr(boot_diag, "_LAST_BEAT", 0.0)
    worker = threading.Thread(target=boot_diag.boot, args=("from a worker",))
    worker.start()
    worker.join()
    assert boot_diag._LAST_BEAT == 0.0

    boot_diag.boot("from the GUI thread")
    assert boot_diag._LAST_BEAT > 0.0


def test_a_stalled_launch_s_log_survives_the_relaunch(logdir):
    """What a user does after a fifteen-minute launch is quit and try again."""
    boot_diag.boot("building main window (AllWellApp) …")
    with open(boot_diag.log_path(), "a") as fh:
        fh.write(f"{boot_diag._STALL_MARKER} for 900s. It is here:\n"
                 '    File "review/core/recent_view.py", line 1, in entry_rows\n')

    handle = boot_diag._LOG_FILE
    handle.close()
    boot_diag._LOG_FILE = None
    boot_diag.boot("starting")

    kept = [p for p in logdir.iterdir() if p != boot_diag.log_path()]
    assert any("for 900s" in p.read_text() for p in kept), (
        "the relaunch erased the only record of where the launch spent its time")


def test_the_event_loop_keeps_the_heartbeat_going(qtbot, monkeypatch):
    """After the window is up, the beat comes from a timer on the event loop —
    so an idle app is not a stalled one, and a blocked loop stops beating."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    boot_diag.start_stall_heartbeat(app)
    try:
        monkeypatch.setattr(boot_diag, "_LAST_BEAT", 0.0)
        qtbot.wait(1200)
        assert boot_diag._LAST_BEAT > 0.0, "the event loop ran and nothing beat"
    finally:
        # Destroyed now rather than with ``deleteLater``: the test process
        # never runs ``app.exec()``, so a deferred delete is never processed and
        # a timer left on the application warns from Qt's teardown at exit.
        from shiboken6 import delete
        for timer in app.findChildren(QTimer, "allwell-stall-heartbeat"):
            timer.stop()
            delete(timer)


def test_the_app_arms_the_watchdog_before_qt_and_beats_once_qt_exists():
    """Read from ``allwell.py``'s syntax tree, because running ``main`` builds the
    whole window. Everything above passes with the watchdog never armed, which
    is the one edit that would make every slow launch silent again.

    Armed at module scope, beside ``arm_faulthandler``: the slow steps include
    importing PySide6 and building the window, both before ``main``'s
    ``QApplication`` exists. The heartbeat starts inside ``main`` after it.
    """
    import ast

    from tests._repo_scan import REPO_ROOT, parse_source

    tree = parse_source(REPO_ROOT / "allwell.py")

    def calls(nodes):
        return [n.func.attr for stmt in nodes for n in ast.walk(stmt)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]

    top_level = [s for s in tree.body if not isinstance(s, (ast.FunctionDef, ast.ClassDef))]
    assert "arm_stall_watchdog" in calls(top_level), (
        "allwell.py no longer arms the stall watchdog at import")

    main = next(s for s in tree.body if isinstance(s, ast.FunctionDef) and s.name == "main")
    in_main = calls(main.body)
    assert "start_stall_heartbeat" in in_main, (
        "main() never starts the heartbeat, so the first idle second after the "
        "window opens reads as a stall")
