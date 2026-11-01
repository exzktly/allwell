"""``subprocess.run`` whose timeout holds on Windows too.

On Windows, ``subprocess.run(..., capture_output=True, timeout=…)`` kills a child that timed out and then calls
``communicate()`` with **no** timeout, to collect the output its reader threads had gathered. Those threads read until
the pipes close, and a process the child started — ssh's ProxyJump hop, a ProxyCommand, a shell's background job —
inherits the pipes and keeps them open after the child is gone. The call then never returns, and the thread that made
it is parked for good without a word. A heartbeat thread went silent that way on a Windows 365 machine, and the
granted node it was keeping released itself under a running step ten minutes later.

:func:`run_bounded` takes ``subprocess.run``'s arguments and returns at the timeout on every platform. On Windows, after
the kill, the output is waited for :data:`AFTER_KILL_S` and then abandoned; elsewhere the call is ``subprocess.run``'s
own (see :data:`WINDOWS`). The pipes are not closed — on Windows, closing one
blocks on the lock that a reader thread still stuck in ``read()`` holds — and close themselves when the process
holding them ends.

``tests/test_subprocess_timeouts_return_on_windows.py`` fails on any ``subprocess.run`` / ``check_output`` that
captures output under a timeout; ``tests/test_a_control_command_returns_at_its_timeout_on_windows.py`` drives the
Windows branch. Stdlib-only, because ``pipeline/`` and the remote worker import it.
"""

from __future__ import annotations

import os
import subprocess

from util.aw_safe import swallowed

#: How long a timed-out child's output is waited for once it has been killed.
AFTER_KILL_S = 2.0

#: Whether ``subprocess.run``'s Windows branch is the one this process would take. Off Windows, ``run`` already
#: returns at its timeout (it waits for the killed child, not for its pipes), so :func:`run_bounded` hands the call to
#: it unchanged: every test double that stands in for ``subprocess.run`` keeps intercepting. A test sets this to reach
#: the Windows path.
WINDOWS = os.name == "nt"


def run_bounded(cmd, **kw) -> subprocess.CompletedProcess:
    """``subprocess.run(cmd, **kw)``, except that a timeout raises ``TimeoutExpired`` at the timeout on Windows too.
    Everything else is ``subprocess.run``'s: the result, the ``CalledProcessError`` under ``check``, and every other
    argument, passed through as given."""
    if not WINDOWS:
        return subprocess.run(cmd, **kw)
    timeout = kw.pop("timeout", None)
    check = kw.pop("check", False)
    input = kw.pop("input", None)
    if kw.pop("capture_output", False):
        if kw.get("stdout") is not None or kw.get("stderr") is not None:
            raise ValueError("stdout and stderr arguments may not be used with capture_output.")
        kw["stdout"] = kw["stderr"] = subprocess.PIPE
    if input is not None:
        if kw.get("stdin") is not None:
            raise ValueError("stdin and input arguments may not both be used.")
        kw["stdin"] = subprocess.PIPE
    # Not ``with Popen(...)``: its exit closes the pipes, which is the block this module exists to avoid.
    proc = subprocess.Popen(cmd, **kw)
    try:
        out, err = proc.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        try:
            exc.stdout, exc.stderr = proc.communicate(timeout=AFTER_KILL_S)
        except subprocess.TimeoutExpired:
            swallowed("a timed-out subprocess's pipes were held open after it was killed")
            proc.wait()                     # the child itself is dead; only the pipes outlive it
        raise
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    code = proc.returncode
    if check and code:
        raise subprocess.CalledProcessError(code, proc.args, out, err)
    return subprocess.CompletedProcess(proc.args, code, out, err)
