"""Which queued jobs may run at the same time.

The Job Queue runs at most one job per **lane**. There are two lanes:

* ``"remote"`` — an Analyze job with a remote host set. Its pipeline runs on
  the remote machine; this computer stages, uploads and fetches, which costs
  disk and network but little CPU.
* ``"local"`` — every other job: a local Analyze, and every Datasets tool.
  Each of those works this computer's CPU and disk hard, and two at once
  would each run slower than the two in turn.

So a remote Analyze can run alongside one local job — converting the next
plate while the previous one is analysed on the cluster — and never two local
jobs together.

A lane being free is not enough on its own. The queue's order used to be what
kept two jobs off each other's folders: a Validate queued after a conversion
into the same folder ran after it because it was below it. With two jobs in
flight that guarantee has to be stated, and :func:`conflicts` states it: a job
that writes a folder another job reads or writes — the same folder, or one
inside the other — must not run beside it. ``JobQueue.next_to_start`` holds a
candidate to that against every running job *and* every unfinished job ahead
of it, so a job never jumps ahead of an earlier one it shares a folder with.

Qt-free and model-only, so the rule is testable without an event loop.
"""

from __future__ import annotations

import os

from services.job_kinds import planned_output

LOCAL = "local"
REMOTE = "remote"
#: Every lane. ``QueueBridge._advance`` asks each free one for its next job
#: and starts the earliest of those in the queue first.
LANES = (LOCAL, REMOTE)

#: The spec keys each kind reads its input from. Analyze reads ``opts.raw``.
_READ_KEYS = {
    "concatenate": ("dataset1", "dataset2"),
    "merge_plates": ("dataset1", "dataset2"),
    "subset": ("source",),
    "validate": ("dataset",),
    "ome_import": ("source",),
    "czi_import": ("source",),
    "nd2_import": ("source",),
    "import_loose": ("source",),
}


def run_lane(kind: str, spec: dict | None) -> str:
    """The lane a job of *kind* with *spec* runs in."""
    if kind == "analyze" and str((spec or {}).get("remote_name") or "").strip():
        return REMOTE
    return LOCAL


def _norm(path: object) -> str:
    text = str(path or "").strip()
    return os.path.normpath(text) if text else ""


def folders(kind: str, spec: dict | None) -> tuple[set[str], set[str]]:
    """``(reads, writes)``: the paths a job of *kind* reads and writes.

    Writes are what a job changes:

    * a converter, Subset, Merge plates or Concatenate its destination (an
      in-place Concatenate dataset 1, an in-place Zip loose its source);
    * Validate the dataset it checks, since a repair rewrites its metadata;
    * Analyze its dataset root, where it writes ``out/`` — the input folder,
      or the folder above it when the input is a dataset's ``in/``.

    A path not filled in is left out: a blank field shares nothing with
    anything.
    """
    spec = spec or {}
    if kind == "analyze":
        raw = _norm((spec.get("opts") or {}).get("raw"))
        if not raw:
            return set(), set()
        root = os.path.dirname(raw) if os.path.basename(raw) == "in" else raw
        return {raw}, {root}
    reads = {_norm(spec.get(key)) for key in _READ_KEYS.get(kind, ())} - {""}
    written = planned_output(kind, spec)
    writes = {_norm(written)} - {""} if written else set()
    return reads, writes


def _overlap(a: str, b: str) -> bool:
    """Whether *a* and *b* are the same folder or one is inside the other."""
    if a == b:
        return True
    return a.startswith(b.rstrip(os.sep) + os.sep) or b.startswith(a.rstrip(os.sep) + os.sep)


def _any_overlap(xs: set[str], ys: set[str]) -> bool:
    return any(_overlap(x, y) for x in xs for y in ys)


def conflicts(a: tuple[set[str], set[str]], b: tuple[set[str], set[str]]) -> bool:
    """Whether two jobs, given as :func:`folders` results, may not run
    together: one writes where the other reads or writes. Two jobs that only
    read the same folder do not conflict."""
    a_reads, a_writes = a
    b_reads, b_writes = b
    return (_any_overlap(a_writes, b_reads | b_writes)
            or _any_overlap(b_writes, a_reads))
