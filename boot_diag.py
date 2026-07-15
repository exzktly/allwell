"""Startup diagnostics — make a silent launch failure observable.

On Windows especially, ``python allwell.py`` can fail to launch and write
*nothing* to the terminal: a native crash during ``import PySide6`` (missing
MSVC runtime / mismatched Qt DLLs) or a Qt platform-plugin abort dies at the C
level with no Python traceback, and a cold-cache matplotlib font scan can *hang*
for minutes with no output at all. In every one of those cases the console is
useless, so this module leaves a trail on disk instead:

* :func:`boot` appends a timestamped milestone to a persistent boot log (and to
  stderr), flushed every time — so after a hang the log's *last* line pinpoints
  exactly which startup step never returned.
* :func:`arm_faulthandler` points ``faulthandler`` at that same log, so a native
  segfault/abort dumps a C+Python stack there even though the terminal shows
  nothing.
* :func:`install_excepthook` routes any uncaught Python exception to the log and
  (best-effort) a GUI message box, so a headless/pythonw launch still surfaces
  the error.

Stdlib-only and fully defensive: importing or calling anything here never blocks
or breaks startup. Import it FIRST in ``allwell.py`` — before PySide6 — so the
riskiest imports are already bracketed.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_LOG_FILE = None          # lazily-opened append handle for the boot log
_LOG_PATH: Path | None = None


def _log_dir() -> Path:
    """Per-user log directory, platform-appropriate, with fallbacks. Prefers
    ``%LOCALAPPDATA%\\allwell`` on Windows and ``~/.cache/allwell`` elsewhere;
    falls back to the system temp dir if neither is writable."""
    candidates = []
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "allwell")
    candidates.append(Path(os.path.expanduser("~/.cache")) / "allwell")
    import tempfile
    candidates.append(Path(tempfile.gettempdir()) / "allwell")
    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d
        except OSError:
            continue
    return Path(tempfile.gettempdir())


def log_path() -> Path:
    """Absolute path to the boot log (stable for the process lifetime)."""
    global _LOG_PATH
    if _LOG_PATH is None:
        _LOG_PATH = _log_dir() / "allwell-boot.log"
    return _LOG_PATH


def _handle():
    """Lazily open (truncating) the boot-log append handle, or None on failure.

    Truncated once per process so the log always reflects the *current* launch
    attempt rather than growing without bound across runs."""
    global _LOG_FILE
    if _LOG_FILE is None:
        try:
            _LOG_FILE = open(log_path(), "w", encoding="utf-8", buffering=1)
        except OSError:
            _LOG_FILE = False  # sentinel: don't retry every call
    return _LOG_FILE or None


def boot(msg: str) -> None:
    """Record a startup milestone to stderr and the boot log, flushed.

    A hang leaves its last-reached milestone as the final log line; a crash
    leaves the milestone just before the faulthandler dump. Never raises."""
    line = f"{time.strftime('%H:%M:%S')} [boot] {msg}\n"
    try:
        if sys.stderr is not None:
            sys.stderr.write(line)
            sys.stderr.flush()
    except Exception:
        pass
    h = _handle()
    if h is not None:
        try:
            h.write(line)
            h.flush()
        except Exception:
            pass


def arm_faulthandler() -> None:
    """Enable ``faulthandler`` writing to the boot log so a native crash
    (segfault / Qt ``abort()``) leaves a stack trace on disk even when the
    console shows nothing. Never raises."""
    try:
        import faulthandler
        h = _handle()
        # Prefer the log file (survives a windowed/no-console launch); fall back
        # to stderr when it's a real stream.
        if h is not None:
            faulthandler.enable(file=h, all_threads=True)
        elif sys.stderr is not None and getattr(sys.stderr, "fileno", None):
            faulthandler.enable()
    except Exception:
        pass  # diagnostics-only; never block startup


def install_excepthook() -> None:
    """Route uncaught exceptions to the boot log + a best-effort GUI dialog, so
    a failure is never silent even without a usable console. Never raises."""
    import traceback

    def _hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        # Log to disk + stderr FIRST — this is the guaranteed record, done before
        # touching Qt (which may itself be the broken component).
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        boot("FATAL uncaught exception:\n" + text)
        try:
            sys.__excepthook__(exc_type, exc, tb)
        except Exception:
            pass
        # Best-effort GUI dialog — ONLY when a QApplication already exists. Never
        # create one here: if the failure IS Qt (missing platform plugin / DLL),
        # constructing QApplication would re-crash or hang, and a modal box with
        # no event loop / no display would block the process forever.
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance()
            if app is not None:
                QMessageBox.critical(
                    None,
                    "AllWell failed to start",
                    f"{exc_type.__name__}: {exc}\n\nDetails written to:\n{log_path()}",
                )
        except Exception:
            pass

    try:
        sys.excepthook = _hook
    except Exception:
        pass
