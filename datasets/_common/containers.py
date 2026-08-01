"""Shared well-container (zip/folder) IO for the Datasets-pane features.

Lifted from ``datasets.concatenate.apply``: reading a well's raw-input or
QC-output container as a flat list of ``(member_name, bytes)``, and writing
one back out as a fresh deflated zip with a collision-safe, first-write-wins
policy. Every feature that copies or re-packs a well's archives (Concatenate,
Subset, Merge plates) shares this rather than re-implementing zip/folder IO.
"""

from __future__ import annotations

import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path

from datasets._common.dataset_paths import DatasetPaths

TIMEPOINT_COLUMN = "timepoint"
TIMEPOINT_HOURS_COLUMN = "timepoint_hours"
FILENAME_COLUMN = "filename"
OVERRIDES_FILENAME = "cell_overrides.json"
OVERRIDES_SCHEMA_VERSION = 2

# A member-level progress sink: ``progress(done, total)``, called once per
# member. Both container helpers know their total up front (``infolist()`` /
# ``len(members)``), which is what makes a real fraction — rather than a bare
# "started"/"finished" pair — available to a caller that wants one.
MemberProgress = Callable[[int, int], None]

# Denominator the fused per-well fraction reports against. It is a fixed,
# coarse scale rather than a raw member count on purpose: a well's copy is
# several passes with different member counts (see :class:`WellFraction`), and
# the number that reaches a dashboard dial only has to be a fraction. Coarse
# also means bounded UI traffic — a well emits at most ``_FRACTION_STEPS + 1``
# updates however many thousands of files it holds, instead of one queued
# signal and one plate repaint per member.
_FRACTION_STEPS = 100


class WellFraction:
    """One well's single 0 → 100% progress dial, fused across several passes.

    Copying one well is never a single pass over its files: the tools read a
    source container and write the destination one, and most of them do that
    again for the well's ``<WELL>_out.zip`` QC archive. Each pass knows only
    its own ``(done, total)``, so forwarding those straight to a dashboard
    would drive the well's dial from empty to full once per pass — on screen
    that reads as the well restarting two or four times, not as progress.

    This fuses them. The passes are declared up front as *slices* of equal
    weight; each pass reports into its own slice via the callback
    :meth:`slice_progress` hands out, and the dial reports the total filled so
    far. The result is one monotonic sweep from 0 to 100% per well. A pass
    that turns out to have nothing to do — a well with no QC archive, a filter
    that kept nothing, a container copied byte-for-byte rather than re-packed
    — still owns its slice, so :meth:`skip` closes it out; otherwise a well
    that genuinely finished would sit for ever at three-quarters full.
    """

    def __init__(self, report: Callable[[str, int, int], None], well: str, slices: int) -> None:
        self._report = report
        self._well = well
        self._slices = max(1, int(slices))
        self._index = 0
        self._last_step = -1

    def _emit(self, index: int, within: float) -> None:
        """Report slice *index* as *within* (0..1) full, deduplicated to whole
        steps so a large container doesn't flood the UI thread."""
        filled = (min(index, self._slices) + max(0.0, min(1.0, within))) / self._slices
        step = int(round(min(1.0, filled) * _FRACTION_STEPS))
        if step <= self._last_step:
            return
        self._last_step = step
        self._report(self._well, step, _FRACTION_STEPS)

    def slice_progress(self) -> MemberProgress:
        """Claim the next slice and return the ``progress(done, total)``
        callback to hand to :func:`read_members` / :func:`write_zip`."""
        index = self._index
        self._index += 1

        def _progress(done: int, total: int) -> None:
            if total <= 0:
                return
            self._emit(index, done / total)

        return _progress

    def skip(self, slices: int = 1) -> None:
        """Close out *slices* passes that had no work to do."""
        for _ in range(max(0, int(slices))):
            index = self._index
            self._index += 1
            self._emit(index, 1.0)

    def finish(self) -> None:
        """Report the well as fully copied, whatever slices remain unclaimed."""
        self._index = self._slices
        self._emit(self._slices, 0.0)


def read_members(
    container: Path, *, progress: MemberProgress | None = None,
) -> list[tuple[str, bytes]]:
    """Read a well container's files as ``(member_name, bytes)``.

    Works for both input layouts: a ``.zip`` (its entries) or a folder (its
    files by POSIX-relative name). Directory entries are skipped.

    *progress*, when given, is called ``progress(done, total)`` once per
    member read, with the count of members known up front from the archive's
    ``infolist()`` (or the folder walk). Reading a plate's worth of images is
    the slow half of every Datasets copy, so this is what lets a caller show a
    well's real sub-progress instead of a single "copying" event that sits
    unchanged until the whole well is done.
    """
    if container.is_dir():
        files = [path for path in sorted(container.rglob("*")) if path.is_file()]
        members: list[tuple[str, bytes]] = []
        for done, path in enumerate(files, start=1):
            members.append((path.relative_to(container).as_posix(), path.read_bytes()))
            if progress is not None:
                progress(done, len(files))
        return members
    with zipfile.ZipFile(container) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        members = []
        for done, info in enumerate(infos, start=1):
            members.append((info.filename, archive.read(info.filename)))
            if progress is not None:
                progress(done, len(infos))
        return members


def write_zip(
    dest: Path, members: list[tuple[str, bytes]], *, progress: MemberProgress | None = None,
) -> tuple[frozenset[str], frozenset[str]]:
    """Write ``(name, bytes)`` members into a fresh deflated zip at *dest*.

    A later member whose name duplicates an earlier one is skipped rather than
    overwriting it — callers that always list dataset 1's (or the base
    dataset's) members first can rely on a same-named member from elsewhere
    never silently clobbering it. Returns ``(written, dropped)`` — *written*
    is the set of names actually written (so callers can verify a CSV row's
    ``filename`` value actually landed in the archive); *dropped* is the set
    of names that were requested but skipped due to a collision, so a caller
    can tell a genuine duplicate-write apart from data that never made it in
    at all.

    ``compresslevel=1``: microscopy image formats are already
    information-dense, so re-deflating at the default level 6 spends a lot of
    CPU for negligible size reduction — this is a pure container-level merge,
    not a size-optimization pass.

    *progress*, when given, is called ``progress(done, total)`` once per
    member offered — including a member skipped as a collision, which still
    costs the caller a step through the list — with *total* being
    ``len(members)``, known before the first write. It exists for the same
    reason :func:`read_members`' does: so a well can render as a live dial
    rather than as a single event that only resolves once the whole well is
    written.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    written: set[str] = set()
    dropped: set[str] = set()
    total = len(members)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for done, (name, data) in enumerate(members, start=1):
            if name in written:
                dropped.add(name)
            else:
                written.add(name)
                archive.writestr(name, data)
            if progress is not None:
                progress(done, total)
    return frozenset(written), frozenset(dropped)


def clear_destination(dest: DatasetPaths) -> None:
    """Remove a destination's ``in``/``out`` content (a failed prior attempt).

    Every Datasets feature rebuilds a destination from its source(s) fresh on
    each ``execute_plan``/apply call — nothing is incremental — so the only
    correct way to resume into a destination that already has leftovers is to
    clear them first; otherwise a partially-written file (or a well from an
    earlier, differently-scoped attempt) would linger in the "finished"
    output.
    """
    for folder in (dest.in_dir, dest.out_dir):
        if folder.exists():
            shutil.rmtree(folder)
