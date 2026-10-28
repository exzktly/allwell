"""What a batched remote run's driver stream said about each well, and why the run ended.

``_BatchedRemoteProcess`` hands the plate to a driver batch by batch and learns
how each batch went from one exit code. That code cannot say which wells a
failed batch lost — a batch fails as a whole when a single well in it comes out
partial — and a batch that exits 0 cannot say it ran every well it was sent. The
stream the driver writes can: every well the pipeline finishes prints
``Well <W> — done.``, a well it skips because its output exists prints
``SKIP well <W> …``, and one that lost FOV-groups prints ``Well <W> — PARTIAL``.
:class:`BatchLedger` keeps those, so the end-of-run tally names what is really
missing and a batch whose wells never reported is noticed rather than counted
complete.

The ledger also keeps the batch loop's own failure lines (:func:`fail`). Those
go to the log directly rather than through the stream the runner reads, so they
used to miss the runner's error tail and a failed batched run's headline said
only "…exited with code 1." ``PipelineRunner`` puts ``failure_lines`` in front
of that tail.

Split out of ``services/remote_process.py``, which is line-budgeted; *proc* is
its ``_BatchedRemoteProcess``.
"""

from __future__ import annotations

import re
import threading

from services.remote_wire import _RE_WELL_DONE
from util.well_token import canonical_well_label

_RE_SKIPPED = re.compile(r"\bSKIP well (?P<well>[A-Ha-h]\d{1,2})\b")
_RE_PARTIAL = re.compile(r"\bWell (?P<well>[A-Ha-h]\d{1,2})\s+\S+\s+PARTIAL\b")

_CREATE_LOCK = threading.Lock()


def _label(token: str) -> str:
    return canonical_well_label(token) or token.upper()


class BatchLedger:
    """The wells the driver's stream reported, and the reasons the batch loop gave up.

    Written by the driver's reader thread (:meth:`observe`) and by the batch
    loop (:func:`fail`), read by the batch loop, so every access holds the lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._done: set[str] = set()
        self._skipped: set[str] = set()
        self._partial: set[str] = set()
        self._reasons: list[str] = []

    def observe(self, line: str) -> None:
        """Note a well the pipeline reported finished, skipped or partial."""
        for regex, into in ((_RE_WELL_DONE, self._done), (_RE_SKIPPED, self._skipped),
                            (_RE_PARTIAL, self._partial)):
            m = regex.search(line)
            if m:
                with self._lock:
                    into.add(_label(m.group("well")))

    def unreported(self, wells) -> list[str]:
        """Those of *wells* the pipeline neither finished nor skipped, in the order given."""
        with self._lock:
            accounted = self._done | self._skipped
        return [w for w in (_label(str(w)) for w in wells) if w not in accounted]

    def partial(self, wells) -> list[str]:
        """Those of *wells* the pipeline finished with FOV-groups missing."""
        with self._lock:
            partial = set(self._partial)
        return [w for w in (_label(str(w)) for w in wells) if w in partial]

    def note_reason(self, line: str) -> None:
        with self._lock:
            self._reasons.append(line.strip())

    @property
    def reasons(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._reasons)


def ledger_of(proc) -> BatchLedger:
    """*proc*'s :class:`BatchLedger`, made on first use."""
    with _CREATE_LOCK:
        ledger = getattr(proc, "_ledger", None)
        if ledger is None:
            ledger = BatchLedger()
            proc._ledger = ledger
        return ledger


def fail(proc, message: str) -> None:
    """Log *message*, a reason the batched run is ending or failing, and keep it for the run's headline."""
    proc._log(message)
    ledger_of(proc).note_reason(message)
