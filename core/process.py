"""Whether a local process is still running — for folders that name their owner.

The desktop app's short-lived working folders (``review.data.cell_pool``'s
spill directories, Batch Export's staging) write an ``owner.pid`` file naming
the process that made them. Anything that wants to remove such a folder — the
spill sweep at start-up, the storage button's cache clear — asks this first, so a
folder in use by a running app is never deleted from under it.

Stdlib-only. In ``core/`` rather than ``util/`` because only the desktop app
uses it: everything in ``util/`` is shipped to a remote worker, and this is not
for one.
"""

from __future__ import annotations

import os

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


def owner_alive(folder) -> bool | None:
    """Whether *folder*'s :data:`OWNER_FILE` names a running process.

    ``None`` when it has no readable owner file, so the caller applies its own
    rule (usually an age) instead of guessing.
    """
    path = os.path.join(os.fspath(folder), OWNER_FILE)
    try:
        with open(path, encoding="ascii") as handle:
            text = handle.read().strip()
    except OSError:
        return None
    if not text.isdigit():
        return True
    return pid_alive(int(text))
