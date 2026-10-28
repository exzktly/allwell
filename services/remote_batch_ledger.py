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


def batch_wells(batch_zips) -> list[str]:
    """The well labels of a batch's archives."""
    return [_label(z.stem) for z in batch_zips]


def unreported_after_success(proc, index: int, batch_zips) -> int:
    """For a batch that exited 0: ``0`` when every well it was sent reported finished or skipped, else ``1``, having
    said which did not.

    An exit of 0 says the pipeline ran over the input directory it found, not that it found the batch it was sent:
    a batch swapped in before its upload finished — a stale ``ready`` flag left by an earlier run — processes what
    had arrived and exits 0. Its missing wells are then absent from the run's verification too, which builds its
    list from the wells that announced themselves. A suspend is the one honest reason for silence: the wells it
    reached part way and the ones it never started say so in their own words, and the resume runs them.
    """
    if proc._suspending.is_set():
        return 0
    missing = ledger_of(proc).unreported(batch_wells(batch_zips))
    if not missing:
        return 0
    fail(proc, f"[remote] ERROR batch {index} exited 0, but {len(missing)} of its {len(batch_zips)} well(s) never "
               f"reported finished — {', '.join(missing[:8])}{' …' if len(missing) > 8 else ''}; their output is "
               f"missing\n")
    return 1


def failed_batches_line(proc, failed: dict[int, int], batches, n_batches: int) -> str:
    """The end-of-run tally of the batches that failed, naming what each one actually lost.

    A batch fails as a whole when one well in it does — a single partial well is enough — so "the wells of a failed
    batch are missing" claimed far more than was true. Each batch is described from what its wells reported.
    """
    ledger = ledger_of(proc)
    parts = []
    for index in sorted(failed):
        wells = batch_wells(batches[index - 1])
        unfinished, partial = ledger.unreported(wells), ledger.partial(wells)
        what = []
        if unfinished:
            what.append(f"{', '.join(unfinished)} not finished")
        if partial:
            what.append(f"{', '.join(partial)} partial")
        if index in getattr(proc, "_over_budget", ()):
            what.insert(0, "exceeded its time budget")
        if not what:
            what.append("every well reported finished")
        rc = proc._batch_rc.get(index, failed[index])
        parts.append(f"batch {index} (exit {rc}): {'; '.join(what)}")
    return (f"[remote] ERROR {len(failed)} of {n_batches} batch(es) failed — {' | '.join(parts)}. Every other well "
            f"completed.\n")
