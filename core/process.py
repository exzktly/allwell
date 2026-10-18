"""Whether a local process is still running — for folders that name their owner.

The desktop app's short-lived working folders (``review.data.cell_pool``'s
spill directories, Batch Export's staging) write an ``owner.pid`` file naming
the process that made them. Anything that wants to remove such a folder — the
spill sweep at start-up, the storage button's cache clear — asks this first, so a
folder in use by a running app is never deleted from under it.

**The owner is a process on a host.** The folders live under the app's own
folder, which on macOS and Linux is in the home folder — and a lab's Linux home
folder is often one network share mounted on several machines. A PID means
nothing on another host: process 12345 being absent here says nothing about
process 12345 on the machine that wrote it. So the file holds ``<pid>@<host>``
(:func:`owner_text`), and a folder written on another host is always treated as
in use. A bare ``<pid>`` (no host) is read as this host's.

Stdlib-only. In ``core/`` rather than ``util/`` because only the desktop app
uses it: everything in ``util/`` is shipped to a remote worker, and this is not
for one.
"""

from __future__ import annotations

import os
import socket

#: The file a working folder names its owning process in.
OWNER_FILE = "owner.pid"


def pid_alive(pid: int) -> bool:
    """Whether a process *pid* exists. Errs towards ``True``: a directory is swept only when its owner is gone."""
    if pid <= 0:
        return True
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.GetLastError() != 87                      # ERROR_INVALID_PARAMETER: no such process
        try:
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return not ok or code.value == 259                      # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True                                                 # it exists, owned by someone else
    return True


def _host() -> str:
    return socket.gethostname() or "?"


def owner_text(pid: int | None = None) -> str:
    """What a working folder's :data:`OWNER_FILE` holds: ``<pid>@<host>``."""
    return f"{os.getpid() if pid is None else pid}@{_host()}\n"


def write_owner(folder) -> None:
    """Name this process as *folder*'s owner."""
    with open(os.path.join(os.fspath(folder), OWNER_FILE), "w", encoding="utf-8") as handle:
        handle.write(owner_text())


def owner_alive(folder) -> bool | None:
    """Whether *folder*'s :data:`OWNER_FILE` names a running process.

    ``True`` for an owner on another host (it cannot be checked from here) and
    for anything unreadable as ``<pid>[@<host>]``; ``None`` when there is no
    readable owner file, so the caller applies its own rule (usually an age)
    instead of guessing.
    """
    path = os.path.join(os.fspath(folder), OWNER_FILE)
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read().strip()
    except (OSError, UnicodeDecodeError):
        return None
    pid, _, host = text.partition("@")
    if not pid.isdigit():
        return True
    if host and host != _host():
        return True
    return pid_alive(int(pid))
