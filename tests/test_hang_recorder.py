"""A window that stops responding writes every thread's stack to the boot log (``startup.boot_diag.install_hang_recorder``).

Reported: a nanowell dataset on a USB hard disk "just sits on loading and fails to progress at all" at
``Loading 7/61``, and nothing on disk said where it was. Each case runs in its own process, because faulthandler's
watchdog and the boot log handle are process-wide state no test should leave armed behind it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

REPO = Path(__file__).resolve().parent.parent

SCRIPT = textwrap.dedent("""
    import os, sys, time
    sys.path.insert(0, {repo!r})
    from startup import boot_diag
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    boot_diag.install_hang_recorder(app)

    def pump(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    def _stuck_reading_a_well():
        time.sleep({stall})

    pump(0.5)
    _stuck_reading_a_well()
    pump(1.5)
    print(boot_diag.log_path(), flush=True)
    os._exit(0)
""")


def _run(tmp_path, *, stall: float, limit: str) -> str:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", ALLWELL_STATE_DIR=str(tmp_path),
               ALLWELL_HANG_DUMP_S=limit)
    done = subprocess.run([sys.executable, "-c", SCRIPT.format(repo=str(REPO), stall=stall)],
                          env=env, capture_output=True, text=True, timeout=60, check=True)
    log = Path(done.stdout.strip().splitlines()[-1])
    assert log.is_file(), done.stderr
    return log.read_text(encoding="utf-8", errors="replace")


def test_a_stalled_window_leaves_the_stuck_line_in_the_boot_log(tmp_path):
    text = _run(tmp_path, stall=2.5, limit="1")
    assert "Timeout (0:00:01)!" in text, text
    assert "_stuck_reading_a_well" in text, text      # the stack names the function it was stuck in
    assert "the window did not respond for 3 s" in text or "the window did not respond for 2 s" in text, text


def test_a_responsive_window_writes_nothing(tmp_path):
    text = _run(tmp_path, stall=0.2, limit="1")
    assert "Timeout" not in text and "did not respond" not in text, text


def test_switched_off_it_writes_nothing(tmp_path):
    text = _run(tmp_path, stall=2.5, limit="0")
    assert "Timeout" not in text, text


def test_a_stall_it_recovered_from_is_not_kept_as_a_crash(tmp_path):
    """The dump's thread headers must not read as a crash marker, or the next launch keeps and reports the log."""
    from startup import boot_diag

    text = _run(tmp_path, stall=2.5, limit="1")
    assert "Timeout" in text
    log = tmp_path / "copy.log"
    log.write_text(text)
    assert not boot_diag._looks_like_a_crash(log)
