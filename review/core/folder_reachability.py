"""Whether a remembered folder is reachable, without letting the question hang.

The recent-datasets rows say whether each folder can still be opened, and the
only way to know is to ``stat`` it. On a local disk that is microseconds. On a
network share whose server has gone away — a laptop off the lab network, a VPN
that dropped, a NAS that was restarted — macOS keeps the mount and a ``stat``
under it blocks until the SMB client gives up, which takes tens of seconds per
call. The rows were drawn on the GUI thread at construction (the rail head's
dataset menu, Home's card and Home's placeholder: nineteen ``stat`` calls for a
full history), so one dead share could hold the launch for many minutes with no
window on screen.

So the ``stat`` runs on a daemon thread and the GUI thread waits for it only
briefly. :meth:`ReachabilityProbe.check` starts one check per folder and waits
at most :data:`WAIT_BUDGET_S` **in total** for the ones it started: a local
folder answers well inside that, so every row that can be decided at once is
decided at once, exactly as before. A folder that has not answered by the
deadline comes back ``None`` — *not known yet* — and the caller draws it as
such. When its check does finish with an answer different from the one that
was drawn, the probe calls its listener so the surface can repaint.

**One check in flight per folder.** A folder whose check has not returned is
not checked again until it does, so a dead share costs one blocked thread per
remembered folder, never one per repaint, and a repaint never waits on a check
that an earlier call already gave up on. Ten folders is the most the history
holds (``recent_datasets.MAX_RECENTS``).

**Every call re-checks a folder that answers promptly.** A folder that answered
inside the budget is checked again the next time rows are built, as the
synchronous version did, so a drive plugged back in brings its row back without
the app being restarted. A folder that answered *late* is not re-checked until
:data:`SLOW_RECHECK_S` has passed since: its late answer is what triggers the
repaint, and a repaint that re-checked it would draw it as "being checked"
again and be followed by another late answer and another repaint, for as long
as the share stays down.

**Qt-free.** The listener is a plain callable invoked on the *worker* thread;
``core.recent_view`` hands it a queued signal so the repaint lands on the GUI
thread. Keeping Qt out of this module is what lets its timing rules be pinned
in the fast lane with a fake ``isdir`` that blocks on demand.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Sequence

#: The longest one call waits, in total, for the checks it started. Long enough
#: that a local or healthy network folder always answers inside it (they take
#: microseconds to a few milliseconds), short enough that three surfaces built
#: at launch against a dead share cost well under a second between them.
WAIT_BUDGET_S = 0.2

#: How long a folder whose last check outlasted the budget keeps that answer
#: before it is checked again. See "Every call re-checks…" above.
SLOW_RECHECK_S = 60.0


class ReachabilityProbe:
    """Answers "is this folder there?" with ``True``, ``False`` or ``None``.

    ``None`` means the check has not finished — the folder may be on a share
    that is not answering. It is never collapsed into ``False``: a folder that
    is slow to answer is not the same as one that is gone, and the row says so.
    """

    def __init__(self, isdir: Callable[[str], bool] = os.path.isdir, *,
                 slow_recheck_s: float = SLOW_RECHECK_S) -> None:
        self._isdir = isdir
        self._slow_recheck_s = slow_recheck_s
        #: Folders whose last check finished after its budget, with the
        #: ``time.monotonic()`` it finished at.
        self._slow_since: dict[str, float] = {}
        self._lock = threading.Lock()
        #: The last answer each folder's check gave.
        self._known: dict[str, bool] = {}
        #: Folders whose check is still running, with the event it sets.
        self._inflight: dict[str, threading.Event] = {}
        #: What the last :meth:`check` returned for each folder — what is on
        #: screen. A finished check notifies only when it disagrees with this,
        #: so a check that answered inside the budget never causes a repaint.
        self._shown: dict[str, bool | None] = {}
        self._listener: Callable[[], None] | None = None

    def set_listener(self, listener: Callable[[], None] | None) -> None:
        """Called, on the worker thread, when a late answer changes a row."""
        with self._lock:
            self._listener = listener

    def check(self, paths: Sequence[str], *,
              budget_s: float = WAIT_BUDGET_S) -> list[bool | None]:
        """One answer per path, in order, after waiting at most *budget_s*."""
        started: list[threading.Event] = []
        now = time.monotonic()
        deadline = now + budget_s
        with self._lock:
            for path in paths:
                if path in self._inflight:
                    continue        # still blocked from an earlier call
                slow_at = self._slow_since.get(path)
                if slow_at is not None and now - slow_at < self._slow_recheck_s:
                    continue        # answered late recently; keep that answer
                event = threading.Event()
                self._inflight[path] = event
                started.append(event)
                threading.Thread(target=self._run, args=(path, event, deadline),
                                 name="allwell-reachability", daemon=True).start()
        for event in started:
            event.wait(max(0.0, deadline - time.monotonic()))
        with self._lock:
            # Still running means not known — even with an older answer cached.
            # A share that answered an hour ago and is not answering now is
            # exactly the folder a click must not be offered on, because the
            # load would block the window on the same stat.
            answers = [None if path in self._inflight else self._known.get(path)
                       for path in paths]
            for path, answer in zip(paths, answers):
                self._shown[path] = answer
        return answers

    def _run(self, path: str, event: threading.Event, deadline: float) -> None:
        try:
            answer = bool(self._isdir(path))
        except Exception:   # an isdir that raises is a folder that cannot be opened
            answer = False
        finished = time.monotonic()
        with self._lock:
            self._known[path] = answer
            self._inflight.pop(path, None)
            if finished > deadline:
                self._slow_since[path] = finished
            else:
                self._slow_since.pop(path, None)
            listener = self._listener
            stale = path in self._shown and self._shown[path] != answer
        event.set()
        if stale and listener is not None:
            listener()


#: The one probe the app uses. Module-level because the three surfaces that draw
#: the recent list must share in-flight checks — otherwise each would start its
#: own blocked thread against the same dead share.
PROBE = ReachabilityProbe()
