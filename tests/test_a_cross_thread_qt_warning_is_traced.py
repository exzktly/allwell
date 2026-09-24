"""A Qt object touched from the wrong thread leaves its culprit in the boot log.

Reported: clicking Stop on a remote run crashed the app with "timers cannot be
stopped from another thread". Qt only *warns* for that, and the crash after it
dumps the stack of whichever thread it happened on — so neither the console
nor the faulthandler dump said which code had reached across. Two harnesses
driving Stop on the real Analyze pane (local, and remote over FakeRemote) did
not reproduce it, so the next occurrence has to explain itself:
``startup.boot_diag.install_qt_cross_thread_trace`` writes the warning, the
thread it came from and every thread's Python stack to the boot log.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _run(tmp_path, install: bool) -> str:
    code = textwrap.dedent(f"""
        import sys, threading
        sys.path.insert(0, {str(_REPO)!r})
        from startup import boot_diag
        boot_diag.boot("start")
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        if {install!r}:
            boot_diag.install_qt_cross_thread_trace()
        timer = QTimer()
        timer.start(10_000)

        def the_culprit():
            timer.stop()          # a GUI-thread timer, stopped from a worker

        t = threading.Thread(target=the_culprit, name="aw-culprit")
        t.start(); t.join()
        print("LOG", boot_diag.log_path())
    """)
    env = {**os.environ, "HOME": str(tmp_path), "QT_QPA_PLATFORM": "offscreen"}
    env.pop("LOCALAPPDATA", None)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=60, env=env)
    assert "LOG" in out.stdout, out.stderr[-2000:]
    log = Path(out.stdout.split("LOG", 1)[1].strip())
    return log.read_text(), out.stderr


def test_the_boot_log_names_the_thread_and_the_code(tmp_path):
    text, stderr = _run(tmp_path, install=True)
    assert "Timers cannot be stopped from another thread" in stderr, (
        "Qt's own output must still reach the console")
    assert "Qt cross-thread warning on thread 'aw-culprit'" in text, text[-1500:]
    assert "the_culprit" in text, "the offending function's stack was not written"


def test_a_warning_is_not_mistaken_for_a_crash(tmp_path):
    """The next launch keeps and reports a log that reads like a crash; a
    warning dumped in faulthandler's format would be reported as one."""
    from startup.boot_diag import _CRASH_MARKERS

    text, _ = _run(tmp_path, install=True)
    assert not any(m in text for m in _CRASH_MARKERS), text[-1500:]


def test_without_it_the_log_says_nothing(tmp_path):
    """The control, so the first test measures the handler and not Qt."""
    text, _ = _run(tmp_path, install=False)
    assert "aw-culprit" not in text
