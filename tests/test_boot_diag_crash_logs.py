"""A crash's own record must survive the restart that follows the crash.

`boot_diag` truncates the boot log once per launch, so the log always describes
the current attempt. That is right for a hang — the last line is the step that
never returned — and it was wrong for a crash, because the thing a user does
after a crash is relaunch, and relaunching erased the traceback the dialog had
just told them to go and read. A `MemoryError` opening a dataset was reported
with the log already gone for exactly that reason, and it is the only evidence
that would have named the allocation.

So a log carrying a crash marker is moved aside before the new one is opened,
and logs from launches that ended quietly are still overwritten — otherwise a
dozen ordinary restarts would push the one crash out of the ring.

The dialog carries the traceback itself as well, which is the half that needs no
file at all.
"""

from __future__ import annotations

import sys
import traceback

import pytest

from startup import boot_diag


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    """Point boot_diag at a temp directory and reset its per-process state.

    `_LOG_PATH` and `_LOG_FILE` are module globals that a real process sets once;
    the tests here stand in for several launches, so each 'launch' resets them.
    """
    monkeypatch.setattr(boot_diag, "user_state_dir", lambda: tmp_path)
    monkeypatch.setattr(boot_diag, "_LOG_PATH", None, raising=False)
    monkeypatch.setattr(boot_diag, "_LOG_FILE", None, raising=False)
    return tmp_path


def _relaunch() -> None:
    """What the next start of the app does to the log: reopen it from scratch."""
    handle = boot_diag._LOG_FILE
    if handle:
        handle.close()
    boot_diag._LOG_FILE = None
    boot_diag.boot("starting")


def _kept(logdir) -> str:
    """Everything still readable in the log directory bar the live log.

    Deliberately a directory scan rather than a call to ``crash_log_paths``:
    what these tests measure is whether the record *survives*, which is a fact
    about the filesystem and not about any particular rotation scheme. It also
    means they run — and fail honestly — against a branch that has neither the
    helper nor the constants (ARCHITECTURE §15 on proving a guard).
    """
    live = boot_diag.log_path()
    return "\n".join(
        p.read_text(errors="replace")
        for p in sorted(logdir.iterdir())
        if p.is_file() and p != live
    )


#: The cap, read from the module when it defines one. The fallback lets the
#: whole file measure real behaviour on a branch that predates the constant.
def _keep() -> int:
    return int(getattr(boot_diag, "CRASH_LOG_KEEP", 3))


def test_a_log_holding_a_traceback_survives_the_next_launch(logdir):
    boot_diag.boot("FATAL uncaught exception:\nMemoryError: unable to allocate")

    _relaunch()

    assert "MemoryError: unable to allocate" in _kept(logdir), (
        "the crashed log was erased by the relaunch — which is the one thing a "
        "user always does after a crash, and the traceback is the only evidence "
        "that names the failing allocation"
    )
    # And the live log is the new launch's, not the old one's.
    assert "MemoryError" not in boot_diag.log_path().read_text()


def test_a_native_dump_counts_as_a_crash(logdir):
    """faulthandler's output is the only record an abort leaves — it never
    reaches Python, so the excepthook's marker is not in the file."""
    boot_diag.boot("importing PySide6")
    with open(boot_diag.log_path(), "a") as fh:
        fh.write("Fatal Python error: Segmentation fault\n\n"
                 "Current thread 0x00007f0a (most recent call first):\n"
                 '  File "allwell.py", line 1, in <module>\n')

    _relaunch()

    assert "Segmentation fault" in _kept(logdir)


def test_an_ordinary_launch_is_still_overwritten(logdir):
    """Otherwise a dozen clean restarts push the one crash out of the ring."""
    boot_diag.boot("app ready")

    _relaunch()

    assert "app ready" not in _kept(logdir)
    assert "app ready" not in boot_diag.log_path().read_text()


def test_crashes_rotate_newest_first_and_stop_at_the_cap(logdir):
    keep = _keep()
    for n in range(keep + 2):
        boot_diag.boot(f"FATAL uncaught exception:\nRuntimeError: crash {n}")
        _relaunch()

    kept = _kept(logdir)
    assert f"crash {keep + 1}" in kept, "the newest crash was not kept"
    assert "crash 0" not in kept, "the oldest crash was not dropped at the cap"

    paths = getattr(boot_diag, "crash_log_paths", None)
    if paths is not None:                      # the module's own view of them
        found = paths()
        assert len(found) == keep
        assert f"crash {keep + 1}" in found[0].read_text(), "not newest-first"


def test_a_huge_previous_log_is_still_recognised(logdir):
    """The marker sits at the top of a big faulthandler dump, so a scan that
    only read the tail would miss it and overwrite the file."""
    boot_diag.boot("FATAL uncaught exception:\nMemoryError: the one we want")
    with open(boot_diag.log_path(), "a") as fh:
        fh.write("x" * (int(getattr(boot_diag, "_SCAN_BYTES", 1 << 20)) * 2))

    _relaunch()

    assert "MemoryError: the one we want" in _kept(logdir)


# ── the dialog ──────────────────────────────────────────────────────────────


class _FakeBox:
    """Stands in for QMessageBox, recording what the hook put in it."""

    Critical = 3
    last: _FakeBox | None = None

    def __init__(self, icon, title, text):
        self.icon, self.title, self.text = icon, title, text
        self.informative = ""
        self.detailed = ""
        self.shown = False
        _FakeBox.last = self

    def setInformativeText(self, s):
        self.informative = s

    def setDetailedText(self, s):
        self.detailed = s

    def exec(self):
        self.shown = True


def _fire_hook(monkeypatch):
    """Run the installed excepthook over a real MemoryError, with the dialog
    stubbed. Returns the traceback text the hook should have shown."""
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    # A real QApplication, because the hook is gated on one existing and
    # pytest-qt's own teardown calls processEvents on whatever it finds.
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(QtWidgets, "QMessageBox", _FakeBox)
    _FakeBox.last = None

    before = sys.excepthook
    try:
        boot_diag.install_excepthook()
        try:
            raise MemoryError("Unable to allocate 4.00 TiB for an array")
        except MemoryError:
            exc_type, exc, tb = sys.exc_info()
            sys.excepthook(exc_type, exc, tb)
            return "".join(traceback.format_exception(exc_type, exc, tb))
    finally:
        sys.excepthook = before


def test_the_dialog_carries_the_traceback_not_only_its_path(logdir, monkeypatch):
    """The user relaunches before finding a file in a hidden directory. The
    frames have to be in front of them, copyable, while the dialog is open."""
    text = _fire_hook(monkeypatch)

    box = _FakeBox.last
    assert box is not None and box.shown
    assert box.detailed == text
    assert "MemoryError" in box.text
    assert str(boot_diag.log_path()) in box.informative


def test_the_dialog_does_not_claim_the_app_failed_to_start(logdir, monkeypatch):
    """This hook fires for a crash at any point in a session. A dataset that
    died an hour in did not fail to start, and that sentence is the first thing
    the reader sees."""
    _fire_hook(monkeypatch)

    box = _FakeBox.last
    blob = f"{box.title}\n{box.text}\n{box.informative}".lower()
    assert "failed to start" not in blob
