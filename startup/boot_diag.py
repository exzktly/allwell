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

* :func:`arm_stall_watchdog` records where the GUI thread *is* when it stops
  answering for :data:`STALL_THRESHOLD_S` seconds, and again at each doubling
  of that, so a launch that took minutes leaves the line it was stuck on rather
  than only the milestone before it. :func:`boot` is its heartbeat until the
  ``QApplication`` exists; :func:`start_stall_heartbeat` takes over from there.

**A log that recorded a crash or a stall is kept.** The boot log is truncated per launch so
it always describes the *current* attempt — which is right for a hang, and was
wrong for a crash: the thing a user does after a crash is restart the app, and
that restart erased the traceback the dialog had just told them to go and read.
A log carrying a crash marker is therefore moved aside first
(:func:`crash_log_paths`), and only logs from launches that ended quietly are
overwritten. A stall report is kept the same way, for the same reason: what a
user does after a launch that took fifteen minutes is quit and try again, and
that relaunch would otherwise erase the only record of where the time went. The dialog also carries the traceback itself now, so the common
case needs no file at all.

Stdlib-only and fully defensive: importing or calling anything here never blocks
or breaks startup. Import it FIRST in ``allwell.py`` — before PySide6 — so the
riskiest imports are already bracketed.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

_LOG_FILE = None          # lazily-opened append handle for the boot log
_LOG_PATH: Path | None = None


def user_state_dir() -> Path:
    """Per-user directory for anything this app keeps between launches.

    Platform-appropriate, with fallbacks: ``%LOCALAPPDATA%\\allwell`` on
    Windows, ``~/.cache/allwell`` elsewhere, the system temp dir if neither is
    writable. The boot log lives here, and so does matplotlib's font cache in a
    frozen build — ``startup.mpl_bootstrap`` used the directory holding its own source
    file, which under PyInstaller is inside ``sys._MEIPASS``, a fresh temp tree
    unpacked per launch. The cache was therefore rebuilt every single launch,
    which is precisely what that module exists to prevent.
    """
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
        _LOG_PATH = user_state_dir() / "allwell-boot.log"
    return _LOG_PATH


#: How many crashed boot logs are kept, newest first.
CRASH_LOG_KEEP = 3

#: Text that means the previous launch left a diagnosis in its log. The first is
#: what :func:`install_excepthook` writes; the rest are ``faulthandler``'s, which
#: is the only record a native abort leaves (it never reaches Python at all).
_CRASH_MARKERS = (
    "FATAL uncaught exception",
    "Fatal Python error",
    "Current thread 0x",
    "Stack (most recent call first)",
)

#: Cap on how much of a previous log is scanned for those markers. A boot log is
#: normally a few KB; a faulthandler dump from a process with many threads is the
#: only thing that makes one big, and its marker is at the top of the dump.
_SCAN_BYTES = 1 << 20


#: What :func:`arm_stall_watchdog` writes at the head of every stall report.
#: Plain text, not ``faulthandler``'s format, so a stall is never reported as a
#: crash — but it is in :data:`_KEEP_MARKERS`, so its log survives a relaunch.
_STALL_MARKER = "STALL: the GUI thread has not answered"

#: Everything that makes a log worth keeping past the next launch.
_KEEP_MARKERS = _CRASH_MARKERS + (_STALL_MARKER,)


def crash_log_paths() -> list[Path]:
    """The kept logs from previous launches that ended in a crash, newest first.

    Only the ones that exist. Named so a caller — the dialog, a bug report, a
    support question — can point at them without knowing the rotation scheme.
    """
    base = log_path()
    kept = [base.with_name(f"{base.name}.{i}") for i in range(1, CRASH_LOG_KEEP + 1)]
    return [p for p in kept if p.exists()]


def _looks_like_a_crash(path: Path, markers=_CRASH_MARKERS) -> bool:
    """Whether *path* holds a traceback or a native dump. False on any error.

    False is the safe direction: it means the log is treated as an ordinary
    launch record and overwritten, which is what every version before this did.
    *markers* widens the question; rotation passes :data:`_KEEP_MARKERS`.
    """
    try:
        size = path.stat().st_size
        with open(path, encoding="utf-8", errors="replace") as fh:
            if size > _SCAN_BYTES:
                # Read both ends. The excepthook's marker lands wherever the
                # crash happened, and a faulthandler dump appends at the end.
                head = fh.read(_SCAN_BYTES // 2)
                fh.seek(max(0, size - _SCAN_BYTES // 2))
                text = head + fh.read()
            else:
                text = fh.read()
    except OSError:
        return False
    return any(marker in text for marker in markers)


def _rotate_if_it_recorded_a_crash() -> None:
    """Move a crashed log aside so this launch's truncation cannot erase it.

    Ordinary launches are overwritten as before, so a user who restarts the app
    a dozen times does not push their one crash out of the ring. Never raises —
    a rotation that fails must not stop the app from starting.
    """
    base = log_path()
    try:
        if not base.exists() or not _looks_like_a_crash(base, _KEEP_MARKERS):
            return
        for i in range(CRASH_LOG_KEEP, 1, -1):
            older = base.with_name(f"{base.name}.{i}")
            newer = base.with_name(f"{base.name}.{i - 1}")
            if newer.exists():
                os.replace(newer, older)
        os.replace(base, base.with_name(f"{base.name}.1"))
    except OSError:
        pass


def _handle():
    """Lazily open (truncating) the boot-log append handle, or None on failure.

    Truncated once per process so the log always reflects the *current* launch
    attempt rather than growing without bound across runs — except when the
    previous launch crashed, in which case its log is kept first."""
    global _LOG_FILE
    if _LOG_FILE is None:
        _rotate_if_it_recorded_a_crash()
        try:
            _LOG_FILE = open(log_path(), "w", encoding="utf-8", buffering=1)
        except OSError:
            _LOG_FILE = False  # sentinel: don't retry every call
    return _LOG_FILE or None


def boot(msg: str) -> None:
    """Record a startup milestone to stderr and the boot log, flushed.

    A hang leaves its last-reached milestone as the final log line; a crash
    leaves the milestone just before the faulthandler dump. Never raises.

    A milestone from the main thread is also a heartbeat for
    :func:`arm_stall_watchdog`. One from any other thread — the Qt message
    handler, the watchdog's own "answered again" — is not, or a worker logging
    while the GUI thread is stuck would hide the stall it is stuck in."""
    if threading.current_thread() is threading.main_thread():
        beat()
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


#: Qt's words for an object touched from a thread that does not own it — the
#: "Timers cannot be stopped from another thread" family. Qt only *warns* for
#: these, and the native crash that usually follows leaves a stack of the
#: thread it happened on, not of the one that did the damage.
_CROSS_THREAD_MARKERS = ("from another thread", "different thread",
                         "in a different thread")

#: How many cross-thread warnings get a full stack dump. The first names the
#: culprit; a flood would bury it and grow the log without bound.
_CROSS_THREAD_DUMPS = 5


def _all_thread_stacks() -> str:
    """Every live thread's Python stack, formatted by hand.

    Not ``faulthandler.dump_traceback``: its header lines are what
    :func:`_looks_like_a_crash` keys on, so a warning dumped in its format would
    make the next launch keep and report this log as a crash that never
    happened.
    """
    import threading
    import traceback

    names = {t.ident: t.name for t in threading.enumerate()}
    parts = []
    for ident, frame in sys._current_frames().items():
        parts.append(f"  -- thread {names.get(ident, '?')} ({ident})\n")
        parts.extend("    " + line for line in
                     "".join(traceback.format_stack(frame)).splitlines(True))
    return "".join(parts)


def install_qt_cross_thread_trace() -> None:
    """Record who touched a Qt object from the wrong thread. Never raises.

    Call once the ``QApplication`` exists. Installs a Qt message handler that,
    for a cross-thread warning, writes the message, the thread that raised it
    and every thread's Python stack to the boot log — the evidence a report
    like "clicking Stop crashes with *timers cannot be stopped from another
    thread*" needs, and which no reading of the code had produced. Every
    message is still passed on to the previous handler, or to stderr as Qt's
    default handler would print it.
    """
    try:
        from PySide6.QtCore import qInstallMessageHandler
    except Exception:
        return
    dumps = [0]
    previous = None

    def _handler(mode, context, message):
        try:
            if (dumps[0] < _CROSS_THREAD_DUMPS
                    and any(m in str(message) for m in _CROSS_THREAD_MARKERS)):
                import threading
                dumps[0] += 1
                boot(f"Qt cross-thread warning on thread "
                     f"{threading.current_thread().name!r}: {message}\n"
                     f"{_all_thread_stacks()}")
        except Exception:
            pass
        if previous is not None:
            previous(mode, context, message)
        elif sys.stderr is not None:
            try:
                sys.stderr.write(f"{message}\n")
            except Exception:
                pass

    try:
        previous = qInstallMessageHandler(_handler)
    except Exception:
        pass


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
        #
        # The traceback goes IN the dialog, not just its path. This hook fires
        # for a crash at any point in a session, not only during start-up, and
        # the one thing the reader needs is the last few frames — sending them
        # to find a file in a hidden directory loses that record the moment they
        # relaunch. The path is still named, for the native dumps that only ever
        # reach the file.
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance()
            if app is not None:
                box = QMessageBox(QMessageBox.Critical, "AllWell — unexpected error",
                                  f"{exc_type.__name__}: {exc}")
                box.setInformativeText(
                    "The full traceback is under “Show Details”, and is also "
                    f"written to:\n{log_path()}\n\nPlease include it in a bug "
                    "report — it names the exact line that failed."
                )
                box.setDetailedText(text)
                box.exec()
        except Exception:
            pass

    try:
        sys.excepthook = _hook
    except Exception:
        pass


#: How long the GUI thread may go without a heartbeat before it is recorded as
#: stalled. A healthy launch builds its window in well under this (five to ten
#: seconds from a source checkout), so a report means something real, and the
#: launches it exists for — minutes long — cross it many times over.
STALL_THRESHOLD_S = 15.0

#: How many stall reports one process writes. Each report doubles the wait
#: before the next one for the same stall (15 s, 30 s, 60 s, …), so a single
#: fifteen-minute stall takes six; the cap only bounds a session that stalls
#: over and over.
_STALL_DUMPS = 12

#: ``time.monotonic()`` of the GUI thread's last sign of life. Monotonic, so a
#: laptop that slept is not reported as a stall: neither macOS's nor Linux's
#: monotonic clock advances during suspend.
_LAST_BEAT = time.monotonic()
_WATCHDOG_ARMED = False


def beat() -> None:
    """The GUI thread is alive. Called by :func:`boot` and by the heartbeat timer."""
    global _LAST_BEAT
    _LAST_BEAT = time.monotonic()


def _main_thread_stack() -> str:
    """The main thread's Python stack, formatted by hand (see :func:`_all_thread_stacks`)."""
    import traceback

    frame = sys._current_frames().get(threading.main_thread().ident)
    if frame is None:
        return "  (the main thread has no Python frame)\n"
    return "".join("    " + line for line in
                   "".join(traceback.format_stack(frame)).splitlines(True))


def _watch(threshold_s: float, poll_s: float, stop=None) -> None:
    """The watchdog loop. Runs on its own daemon thread for the process's life.

    *stop* is a ``threading.Event`` that ends it; the app passes none. A test
    that runs this loop must, or the thread outlives it and goes on writing
    stall reports into later tests' logs.
    """
    dumps = 0
    next_report = threshold_s
    stalled_at = None
    while dumps < _STALL_DUMPS and not (stop is not None and stop.is_set()):
        time.sleep(poll_s)
        last = _LAST_BEAT
        gap = time.monotonic() - last
        if stalled_at is not None and last != stalled_at:
            boot(f"GUI thread answered again after "
                 f"{time.monotonic() - stalled_at:.0f}s")
            stalled_at, next_report = None, threshold_s
            continue
        if gap < next_report:
            continue
        stalled_at = last
        dumps += 1
        next_report *= 2
        # ``boot`` would count as a heartbeat, so this writes the log directly.
        h = _handle()
        if h is None:
            continue
        try:
            h.write(f"{time.strftime('%H:%M:%S')} [boot] {_STALL_MARKER} for "
                    f"{gap:.0f}s. It is here:\n{_main_thread_stack()}")
            h.flush()
        except Exception:
            pass


def arm_stall_watchdog(threshold_s: float = STALL_THRESHOLD_S,
                       poll_s: float = 1.0) -> None:
    """Record where the GUI thread is whenever it stops answering. Never raises.

    The milestones :func:`boot` writes say which *step* a slow launch was in;
    when one step took fifteen minutes, that is not enough to say why. This
    samples the main thread's stack from a daemon thread once the heartbeat has
    been silent for *threshold_s*, then again at each doubling, so the log
    shows both where it was stuck and whether it stayed in one place. Idempotent.
    """
    global _WATCHDOG_ARMED
    if _WATCHDOG_ARMED:
        return
    try:
        threading.Thread(target=_watch, args=(threshold_s, poll_s),
                         name="allwell-stall-watchdog", daemon=True).start()
        _WATCHDOG_ARMED = True
    except Exception:
        pass


def start_stall_heartbeat(app) -> None:
    """Beat once a second from *app*'s event loop. Never raises.

    Call once the ``QApplication`` exists. A timer and not a call in each
    handler: a timer fires only when the event loop is free to run it, which is
    exactly the property being watched. Modal dialogs run a nested loop, so the
    timer keeps beating behind them and an open dialog is not a stall.
    """
    try:
        from PySide6.QtCore import QTimer
        timer = QTimer(app)
        timer.setObjectName("allwell-stall-heartbeat")
        timer.timeout.connect(beat)
        timer.start(1000)
    except Exception:
        pass
