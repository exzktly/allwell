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
* :func:`install_excepthook` routes any uncaught Python exception — on the main
  thread or a background one — to the log, to the application log, and
  (best-effort, main thread only) a GUI message box, so a headless/pythonw
  launch still surfaces the error.

**A log that recorded a crash is kept.** The boot log is truncated per launch so
it always describes the *current* attempt — which is right for a hang, and was
wrong for a crash: the thing a user does after a crash is restart the app, and
that restart erased the traceback the dialog had just told them to go and read.
A log carrying a crash marker is therefore moved aside first
(:func:`crash_log_paths`), and only logs from launches that ended quietly are
overwritten. The dialog also carries the traceback itself now, so the common
case needs no file at all.

**So is a session that was killed.** A process the system ends — macOS's
out-of-memory killer above all — runs no Python and no ``faulthandler``, so it
writes no marker. It does leave a log that reached the event loop and never
recorded :func:`mark_clean_exit`, and that log is kept the same way, with a
note saying what its silence means. The next launch then says so in the
application log (:func:`announce_previous_ending`), naming the file, and a
dataset load writes each of its phases here, so the last line of a load that
was killed names the phase it was in.

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


def user_state_dir() -> Path:
    """Per-user directory for anything this app keeps between launches.

    :func:`util.state_dir.user_state_dir`, the one folder every local log and
    cache shares (``~/.cache/allwell`` first, on every platform), so the boot log
    always sits beside the remote-bootstrap logs. Kept under this name because
    ``startup.mpl_bootstrap`` and the tests reach it here. matplotlib's font
    cache lives in it in a frozen build — ``startup.mpl_bootstrap`` used the
    directory holding its own source file, which under PyInstaller is inside
    ``sys._MEIPASS``, a fresh temp tree unpacked per launch, so the cache was
    rebuilt every launch.
    """
    from util.state_dir import user_state_dir as _shared
    return _shared()


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


def crash_log_paths() -> list[Path]:
    """The kept logs from previous launches that ended in a crash, newest first.

    Only the ones that exist. Named so a caller — the dialog, a bug report, a
    support question — can point at them without knowing the rotation scheme.
    """
    base = log_path()
    kept = [base.with_name(f"{base.name}.{i}") for i in range(1, CRASH_LOG_KEEP + 1)]
    return [p for p in kept if p.exists()]


#: The milestone ``allwell.main`` writes when the window is up and the event
#: loop is about to run — the point from which a session is a *session*, and an
#: ending without :data:`_CLEAN_EXIT` means something stopped it.
_SESSION_LIVE = "entering Qt event loop"

#: The line :func:`mark_clean_exit` writes once the event loop has returned.
_CLEAN_EXIT = "[boot] clean exit"

#: How ``allwell.py`` records the launching process in its first milestone.
_START_PID = "[boot] start pid="

#: What the previous launch's log was kept as, and why — set by the rotation
#: when this process first opens its own log, read by
#: :func:`previous_session_ending`. ``None`` when the previous launch ended
#: quietly (or there was none).
_PREVIOUS_ENDING: tuple[Path, str] | None = None


def _read_for_scan(path: Path) -> str | None:
    """The text of *path* to search for markers, or ``None`` on any error."""
    try:
        size = path.stat().st_size
        with open(path, encoding="utf-8", errors="replace") as fh:
            if size > _SCAN_BYTES:
                # Read both ends. The excepthook's marker lands wherever the
                # crash happened, and a faulthandler dump appends at the end.
                head = fh.read(_SCAN_BYTES // 2)
                fh.seek(max(0, size - _SCAN_BYTES // 2))
                return head + fh.read()
            return fh.read()
    except OSError:
        return None


def _looks_like_a_crash(path: Path) -> bool:
    """Whether *path* holds a traceback or a native dump. False on any error.

    False is the safe direction: it means the log is treated as an ordinary
    launch record and overwritten, which is what every version before this did.
    """
    text = _read_for_scan(path)
    return text is not None and any(marker in text for marker in _CRASH_MARKERS)


def _pid_alive(pid: int) -> bool:
    """Whether process *pid* still exists. False when it cannot be told.

    POSIX only: on Windows ``os.kill(pid, 0)`` does not probe, it calls
    ``TerminateProcess`` — so there the answer is always False, which at worst
    keeps a still-running instance's log as if it had ended.
    """
    if os.name == "nt" or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True          # it exists; it is just not ours to signal
    except OSError:
        return False
    return True


def _ended_uncleanly(text: str) -> bool:
    """Whether a log describes a session that was stopped from outside.

    A process the operating system kills — macOS's out-of-memory killer, a
    Force Quit, a power cut — runs no Python and no ``faulthandler``, so it
    leaves no crash marker at all. What it leaves is a session that reached the
    event loop and never wrote :data:`_CLEAN_EXIT`. Its last milestone is then
    the only record of where it was, and before this check the next launch
    overwrote it. A log whose process is still running (a second copy of the
    app being started) is not an ending.
    """
    if _SESSION_LIVE not in text or _CLEAN_EXIT in text:
        return False
    at = text.find(_START_PID)
    if at >= 0:
        rest = text[at + len(_START_PID):].split(None, 1)
        if rest and rest[0].isdigit() and _pid_alive(int(rest[0])):
            return False
    return True


#: Appended to a kept log that ended without a marker, so whoever opens the file
#: is told what its silence means.
_UNCLEAN_NOTE = (
    "{stamp} [boot] (written by the next launch) this session never shut down "
    "cleanly and recorded no traceback, so the process was stopped from outside "
    "it: killed by the system for using too much memory, Force Quit, or a power "
    "loss. The line above this one is the last thing it reached.\n"
)


def _rotate_if_it_recorded_a_crash() -> None:
    """Move a crashed log aside so this launch's truncation cannot erase it.

    A crash is either a marker in the log (a traceback or a native dump) or a
    session that ended without a clean exit (:func:`_ended_uncleanly`). Ordinary
    launches are overwritten as before, so a user who restarts the app a dozen
    times does not push their one crash out of the ring. Never raises — a
    rotation that fails must not stop the app from starting.
    """
    global _PREVIOUS_ENDING
    base = log_path()
    try:
        if not base.exists():
            return
        text = _read_for_scan(base)
        if text is None:
            return
        if any(marker in text for marker in _CRASH_MARKERS):
            kind = "crash"
        elif _ended_uncleanly(text):
            kind = "killed"
        else:
            return
        for i in range(CRASH_LOG_KEEP, 1, -1):
            older = base.with_name(f"{base.name}.{i}")
            newer = base.with_name(f"{base.name}.{i - 1}")
            if newer.exists():
                os.replace(newer, older)
        kept = base.with_name(f"{base.name}.1")
        os.replace(base, kept)
        _PREVIOUS_ENDING = (kept, kind)
        if kind == "killed":
            with open(kept, "a", encoding="utf-8") as fh:
                fh.write(_UNCLEAN_NOTE.format(stamp=time.strftime("%H:%M:%S")))
    except OSError:
        pass


def previous_session_ending() -> tuple[Path, str] | None:
    """How the previous launch ended, when it did not end cleanly.

    ``(kept log, "crash" | "killed")``, or ``None`` when it ended quietly. Only
    meaningful once this process has opened its own log — which ``allwell.py``
    does at import, before anything else.
    """
    return _PREVIOUS_ENDING


def describe_previous_ending() -> str | None:
    """One sentence for the application log about the previous launch, or None.

    Said at the next start, because a session the system killed had no chance
    to say anything itself — and the file holding its last milestone sits in a
    hidden directory nobody finds without being told where.
    """
    ending = previous_session_ending()
    if ending is None:
        return None
    kept, kind = ending
    how = ("crashed" if kind == "crash" else
           "was stopped without shutting down (most often the system ending it "
           "for running out of memory)")
    return (f"The previous All-Well session {how}. Its log was kept at {kept} — "
            f"the last lines say what it was doing; please attach the file to a "
            f"bug report.")


def announce_previous_ending() -> None:
    """Put :func:`describe_previous_ending` in the application log, as a warning.

    ``allwell.main`` calls this once the window — and with it the log drawer's
    handler — exists, so the sentence lands in the drawer and raises its badge.
    Logging reports a failing handler itself rather than raising.
    """
    text = describe_previous_ending()
    if text:
        import logging
        logging.getLogger("allwell.boot").warning("%s", text)


def mark_clean_exit() -> None:
    """Record that the session ended on its own terms. Never raises.

    ``allwell.main`` calls this once the Qt event loop has returned. A log that
    reached the event loop and lacks this line is kept by the next launch as an
    unclean ending (:func:`_ended_uncleanly`).
    """
    boot("clean exit")


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

    Call once the ``QApplication`` exists. Qt's warnings, criticals and fatals
    are also forwarded to the ``allwell.qt`` logger, so they appear in the
    application log beside the app's own. Installs a Qt message handler that,
    for a cross-thread warning, writes the message, the thread that raised it
    and every thread's Python stack to the boot log — the evidence a report
    like "clicking Stop crashes with *timers cannot be stopped from another
    thread*" needs, and which no reading of the code had produced. Every
    message is still passed on to the previous handler, or to stderr as Qt's
    default handler would print it.
    """
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:
        return
    import logging
    import threading

    # Qt's own warnings reach the application log too, beside every other
    # warning the app raises. Debug and info chatter stay on stderr only.
    to_level = {
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }
    qt_log = logging.getLogger("allwell.qt")
    # The log handler marshals through a queued Qt signal, and that can itself
    # raise a Qt warning — at teardown, say — which would re-enter here.
    forwarding = threading.local()
    dumps = [0]
    previous = None

    def _handler(mode, context, message):
        try:
            level = to_level.get(mode)
            if level is not None and not getattr(forwarding, "active", False):
                forwarding.active = True
                try:
                    qt_log.log(level, "%s", message)
                finally:
                    forwarding.active = False
        except Exception:
            pass
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
        # And to the application log, where every other error lands. After the
        # two records above, which must not depend on logging working.
        try:
            import logging
            logging.getLogger("allwell.uncaught").error(
                "Uncaught exception:\n%s", text.rstrip())
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
    _install_thread_excepthook()


def _install_thread_excepthook() -> None:
    """Route an exception that kills a background thread to the app log.

    ``sys.excepthook`` never sees these: Python hands them to
    ``threading.excepthook``, whose default prints to stderr — which a
    double-clicked app does not have. The default still runs afterwards.
    """
    import logging
    import threading
    import traceback

    previous = threading.excepthook

    def _thread_hook(args):
        try:
            if args.exc_type is not SystemExit:
                text = "".join(traceback.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback))
                name = args.thread.name if args.thread is not None else "?"
                boot(f"uncaught exception on thread {name!r}:\n{text}")
                logging.getLogger("allwell.uncaught").error(
                    "Uncaught exception on thread %r:\n%s", name, text.rstrip())
        finally:
            previous(args)

    threading.excepthook = _thread_hook
