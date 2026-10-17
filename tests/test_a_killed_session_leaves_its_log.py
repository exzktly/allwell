"""A session the system kills still leaves a record, and the next launch says where.

The report: All-Well died while opening a large dataset, very likely killed by
macOS for running out of memory, and no boot log was found. A process the
operating system kills runs no Python and no ``faulthandler``, so the log carries
no crash marker — and the boot log is truncated on every launch unless it holds
one. So the one file that knew which phase the load was in was overwritten by
the relaunch that followed, every time.

What is pinned here:

* a log that reached the event loop and never recorded a clean exit is kept by
  the next launch, beside the crashes, with a note saying what its silence means;
* a session that exited cleanly, one still running, and a launch that never got
  as far as the event loop are overwritten as before;
* the next launch announces the kept file in the application log, by path;
* ``allwell.main`` writes the clean-exit line only after the event loop returns;
* a dataset load writes each phase to the log, so a killed load names its phase;
* ``user_state_dir`` passes over a directory it cannot write, instead of
  returning it and then losing the log to a silent ``open`` failure.
"""

from __future__ import annotations

import ast
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from startup import boot_diag
from tests._repo_scan import parse_source

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    """Point boot_diag at a temp directory and reset its per-process state."""
    monkeypatch.setattr(boot_diag, "user_state_dir", lambda: tmp_path)
    monkeypatch.setattr(boot_diag, "_LOG_PATH", None, raising=False)
    monkeypatch.setattr(boot_diag, "_LOG_FILE", None, raising=False)
    monkeypatch.setattr(boot_diag, "_PREVIOUS_ENDING", None, raising=False)
    yield tmp_path
    handle = boot_diag._LOG_FILE
    if handle:
        handle.close()


def _dead_pid() -> int:
    """The pid of a process that has certainly exited."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _a_session(pid: int, *, reached_loop: bool = True) -> None:
    """Write what ``allwell.py`` writes for a session, up to a load phase."""
    boot_diag.boot(f"start pid={pid} python=3.11 platform=darwin")
    boot_diag.boot("PySide6 imported OK")
    if reached_loop:
        boot_diag.boot("entering Qt event loop (startup complete)")
        boot_diag.boot("dataset load: Measuring channel ranges")


def _relaunch() -> None:
    """What the next start of the app does: reopen the log from scratch."""
    handle = boot_diag._LOG_FILE
    if handle:
        handle.close()
    boot_diag._LOG_FILE = None
    boot_diag.boot("start pid=1 python=3.11 platform=darwin")


def _kept(logdir: Path) -> str:
    live = boot_diag.log_path()
    return "\n".join(p.read_text(errors="replace") for p in sorted(logdir.iterdir())
                     if p.is_file() and p != live)


def test_a_killed_session_s_log_survives_the_next_launch(logdir):
    _a_session(_dead_pid())

    _relaunch()

    kept = _kept(logdir)
    assert "dataset load: Measuring channel ranges" in kept, (
        "the relaunch overwrote the only record of where a killed session was")
    assert "never shut down cleanly" in kept, "the kept log does not say what its silence means"
    assert boot_diag.log_path().with_name("allwell-boot.log.1").exists()
    assert boot_diag.previous_session_ending() == (
        boot_diag.log_path().with_name("allwell-boot.log.1"), "killed")


def test_a_clean_exit_is_overwritten(logdir):
    _a_session(_dead_pid())
    boot_diag.mark_clean_exit()

    _relaunch()

    assert "Measuring channel ranges" not in _kept(logdir)
    assert boot_diag.previous_session_ending() is None


def test_a_session_still_running_is_not_called_an_ending(logdir):
    """Starting a second copy of the app must not report the first as dead."""
    _a_session(os.getpid())

    _relaunch()

    assert "Measuring channel ranges" not in _kept(logdir)
    assert boot_diag.previous_session_ending() is None


def test_a_launch_that_never_reached_the_event_loop_is_overwritten(logdir):
    """Every process that merely imports ``allwell`` — a test, a tool — writes
    the first milestones and stops; keeping those would ring false alarms."""
    _a_session(_dead_pid(), reached_loop=False)

    _relaunch()

    assert "PySide6 imported OK" not in _kept(logdir)
    assert boot_diag.previous_session_ending() is None


def test_the_next_launch_names_the_kept_log_in_the_application_log(logdir, caplog):
    _a_session(_dead_pid())
    _relaunch()

    with caplog.at_level(logging.WARNING, logger="allwell.boot"):
        boot_diag.announce_previous_ending()

    kept = str(boot_diag.log_path().with_name("allwell-boot.log.1"))
    said = [r.getMessage() for r in caplog.records if r.name == "allwell.boot"]
    assert len(said) == 1 and kept in said[0], said
    assert "out of memory" in said[0]


def test_a_crash_is_announced_as_a_crash(logdir, caplog):
    boot_diag.boot("FATAL uncaught exception:\nMemoryError: unable to allocate")
    _relaunch()

    with caplog.at_level(logging.WARNING, logger="allwell.boot"):
        boot_diag.announce_previous_ending()

    said = [r.getMessage() for r in caplog.records if r.name == "allwell.boot"]
    assert len(said) == 1 and "crashed" in said[0], said


def test_main_marks_a_clean_exit_only_after_the_event_loop_returns():
    """The order is the feature: written before ``exec`` returns, every killed
    session would look clean; never written, every clean one would look killed."""
    tree = parse_source(_ROOT / "allwell.py")
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [n for n in ast.walk(main) if isinstance(n, ast.Call)]

    def line_of(attr: str) -> int:
        found = [c.lineno for c in calls
                 if isinstance(c.func, ast.Attribute) and c.func.attr == attr]
        assert found, f"main() never calls {attr}"
        return min(found)

    assert line_of("exec") < line_of("mark_clean_exit") < line_of("exit")
    assert line_of("announce_previous_ending") < line_of("exec")


def test_a_load_phase_is_written_to_the_boot_log(logdir, monkeypatch):
    from types import SimpleNamespace

    from review.core.load_progress import LoadProgress

    monkeypatch.setattr(LoadProgress, "_paint", lambda self, text: None)
    progress = LoadProgress(SimpleNamespace(), 4)

    progress.phase("Measuring channel ranges")

    log = boot_diag.log_path()
    written = log.read_text() if log.exists() else ""
    assert "dataset load: Measuring channel ranges" in written, (
        "a load phase left nothing in the boot log, so a load killed for memory "
        "would leave no record of how far it got")


def test_an_unwritable_state_dir_is_passed_over(tmp_path, monkeypatch):
    """``mkdir(exist_ok=True)`` succeeds on a directory that cannot be written,
    and taking it left the log's ``open`` to fail with nowhere else tried."""
    home = tmp_path / "home"
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(os, "name", "posix")
    import tempfile
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    blocked = home / ".cache" / "allwell"
    real_access = os.access
    monkeypatch.setattr(boot_diag.os, "access",
                        lambda p, mode: False if Path(p) == blocked else real_access(p, mode))

    assert boot_diag.user_state_dir() == temp / "allwell"
