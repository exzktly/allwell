"""Shared well-container (zip/folder) IO for the Datasets-pane features.

Every feature that copies or re-packs a well's archives (Concatenate, Subset,
Merge plates) shares this rather than re-implementing zip/folder IO.

:func:`copy_container_members` is the entry point all three use. It publishes
one destination archive from one or more source containers, deciding per
member what it is called in the destination and whether it belongs there at
all — which is the whole of what these tools do to a container. None of them
alters image bytes, so it moves bytes rather than reconstructing them, in
three tiers:

1. **Whole-file copy.** A single zip source with every member kept under its
   own name is copied with :func:`shutil.copy2` and never opened for reading.
   This is a wells-only Subset, and it is the case a user would otherwise do
   by hand in Finder — so it must cost what Finder costs.
2. **Raw member passthrough.** Otherwise each kept member's *already
   compressed* bytes are streamed into the destination by
   :mod:`datasets.core.zip_passthrough` — no inflate, no deflate, no CRC
   recomputation. A dropped member is never read at all, so a filter that
   keeps a quarter of an archive costs about a quarter of it.
3. **Fallback.** A folder source, or a member the passthrough is not entitled
   to handle, goes through :func:`read_members`/:func:`write_zip`'s ordinary
   decompress-and-recompress, one member at a time.

The plan pass that chooses between them reads member *lists* only. For a zip
that is the central directory, a small read at the end of the file, so
planning costs a couple of seeks however large the archive is and however slow
the volume holding it.

Tiers 2 and 3 publish through :func:`util.zip_write.atomic_zip`, the same
staged-then-verified-then-renamed writer the pipeline's own archives use, so a
member that fails part-way through a well can never leave a complete-looking
destination archive that is quietly missing images.

:func:`read_members` and :func:`write_zip` remain the tier-3 primitives and
the shape a caller reaches for when it genuinely needs a member's decoded
bytes in hand.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path

from util.aw_safe import swallow
from util.zip_write import atomic_zip
from datasets.core.dataset_paths import DatasetPaths
from datasets.core.zip_passthrough import copy_member_raw, member_is_raw_copyable

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

# Decides one member's fate: given the source container it came from and its
# name inside that container, return the name it takes in the destination, or
# ``None`` to leave it out. Identity for a pure filter (Subset), a rewritten
# name for a tool that renames as it copies (Concatenate's timepoint offset,
# Merge plates' well-token remap).
MemberRename = Callable[[Path, str], "str | None"]

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
        steps so a large container doesn't flood the UI thread.

        Rounded *down*, never to nearest. A dashboard reads the full step as
        "this well is finished" — the converters draw their ✓ from it — so a
        well 999 planes into 1,000 must not round up to 100 while its last
        plane, or its archive's publish, is still outstanding. Only a fill of
        exactly 1.0 reaches the last step, and every way of finishing a well
        (``(n, n)`` on the last slice, :meth:`skip`, :meth:`finish`) produces
        exactly that.
        """
        filled = (min(index, self._slices) + max(0.0, min(1.0, within))) / self._slices
        step = int(min(1.0, filled) * _FRACTION_STEPS)
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


def container_dest(src: Path, dest_dir: Path, well: str) -> Path:
    """Where *src* belongs in *dest_dir*, named for *well* rather than for itself.

    A dataset is read back by well token: ``in/<WELL>.zip`` for a zipped well,
    ``in/<WELL>/`` for a folder-mode one. Every path that rewrites a container
    builds that name from the well; the copy-it-whole paths built it from
    ``src.name`` instead, so a source plate written with unpadded tokens landed
    as ``in/A1.zip`` beside an ``out/A01_out.zip`` produced by the same run, and
    nothing downstream matched the two up. Merge Plates was half-swept on its
    own: its QC branch already named the destination for the well while its
    input branch did not.

    The extension follows the source, because a folder-mode well stays a folder.
    """
    return dest_dir / (well if src.is_dir() else f"{well}.zip")


def publish_container(src: Path, dest: Path) -> None:
    """Copy *src* to *dest* whole, through a staging name and a rename.

    The one way a Datasets tool puts a container at its destination without
    opening it — a zip copied byte-for-byte, or a folder-mode well copied as a
    tree. Copying straight onto *dest* leaves a truncated ``<WELL>.zip``, or a
    half-populated ``<WELL>/``, if the run is killed or the volume fills: a well
    whose images read as partly vanished, produced by the one path that was
    supposed to be safest because it moves the file whole.

    Every caller goes through here rather than each staging for itself, because
    the last time this rule was applied it was applied to one of five sites —
    and the one that got the fix was the *rare* one. ``_copy_container_as_is``
    in Merge Plates and Concatenate runs for every well the tool does not remap,
    which is all of dataset 1's, always.

    The staging name carries the repo-wide ``__aw_tmp_`` prefix so an
    interrupted run's leftovers are visible to the sweeps that clean them up.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.with_name(f"__aw_tmp_{dest.name}.pid{os.getpid()}")
    try:
        if staging.is_dir():
            shutil.rmtree(staging, ignore_errors=True)
        elif staging.exists():
            staging.unlink()
        if src.is_dir():
            shutil.copytree(src, staging)
            # ``os.replace`` onto a non-empty directory fails, and a retried run
            # may find one there from the attempt before it.
            if dest.is_dir():
                shutil.rmtree(dest, ignore_errors=True)
        else:
            shutil.copy2(src, staging)
        os.replace(str(staging), str(dest))
    except BaseException:
        if staging.is_dir():
            shutil.rmtree(staging, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                staging.unlink(missing_ok=True)
        raise


def _close_progress(progress: MemberProgress | None) -> None:
    """Report a pass with no member-level steps as finished.

    A container that is copied whole, or whose filter kept nothing, still owns
    its slice of the caller's :class:`WellFraction`; without this the well's
    dial would stop short of full on a well that is in fact done.
    """
    if progress is not None:
        progress(1, 1)


def _plan_members(
    sources: Sequence[Path], rename: MemberRename, stack: contextlib.ExitStack,
) -> tuple[list[tuple[zipfile.ZipFile | None, object, str]], set[str], set[str], int]:
    """Decide every member's destination name before any bytes move.

    Returns ``(planned, written, dropped, offered)``. *planned* pairs each kept
    member with the open archive it comes from (``None`` for a folder source,
    whose entry is a :class:`~pathlib.Path`); *offered* counts every member
    across every source, so a caller can tell "kept all of them" from "kept
    most of them" without a second walk.

    First-write-wins on a name collision, matching :func:`write_zip`: callers
    that list the base dataset's members first can rely on a same-named member
    from elsewhere never displacing it, and *dropped* names it so a caller can
    tell a genuine collision from data that never made it in.
    """
    planned: list[tuple[zipfile.ZipFile | None, object, str]] = []
    written: set[str] = set()
    dropped: set[str] = set()
    offered = 0
    for container in sources:
        if container.is_dir():
            archive: zipfile.ZipFile | None = None
            entries: list[tuple[str, object]] = [
                (path.relative_to(container).as_posix(), path)
                for path in sorted(container.rglob("*")) if path.is_file()
            ]
        else:
            archive = stack.enter_context(zipfile.ZipFile(container))
            entries = [
                (info.filename, info) for info in archive.infolist() if not info.is_dir()
            ]
        for name, entry in entries:
            offered += 1
            dest_name = rename(container, name)
            if dest_name is None:
                continue
            if dest_name in written:
                dropped.add(dest_name)
                continue
            written.add(dest_name)
            planned.append((archive, entry, dest_name))
    return planned, written, dropped, offered


def copy_container_members(
    dest: Path,
    sources: Sequence[Path],
    rename: MemberRename,
    *,
    progress: MemberProgress | None = None,
) -> tuple[frozenset[str], frozenset[str]]:
    """Publish *dest* from *sources*, keeping and naming members by *rename*.

    *sources* are well containers (zips, or folders of files) in priority
    order — a later source's member never displaces an earlier one's of the
    same name. Returns ``(written, dropped)`` exactly as :func:`write_zip`
    does: *written* is what actually landed, so a caller can verify a CSV
    row's ``filename`` is really there, and *dropped* is the names lost to a
    collision, which is real data loss and must not be read as success. A
    member *rename* declined is not "dropped" — it was never asked for.

    **Nothing is created when nothing is kept.** A zero-member zip is
    structurally valid but macOS Archive Utility refuses to expand it and
    All-Well reads it as a well whose images have vanished, so a filter that
    matches nothing leaves no file behind rather than an empty one — the same
    rule :func:`util.zip_write.atomic_zip` enforces for the pipeline's own
    archives.

    *progress* is called ``progress(done, total)`` once per member written,
    and once as ``(1, 1)`` for a pass that has no member-level steps (a
    whole-file copy, or a filter that kept nothing). Either way the pass
    closes out its slice of the caller's :class:`WellFraction`, so one
    container is one slice whichever tier serves it.

    See the module docstring for the three tiers and why they exist.
    """
    sources = [Path(source) for source in sources]
    with contextlib.ExitStack() as stack:
        planned, written, dropped, offered = _plan_members(sources, rename, stack)

        if not planned:
            _close_progress(progress)
            return frozenset(), frozenset(dropped)

        # Tier 1 — one zip source, every member kept under its own name.
        if (
            len(sources) == 1
            and sources[0].is_file()
            and len(planned) == offered
            and not dropped
            and all(dest_name == entry.filename for _archive, entry, dest_name in planned)
        ):
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Staged and renamed, exactly like the tier-2/3 path below. Copying
            # straight onto *dest* would leave a truncated ``<WELL>.zip`` if the
            # copy is interrupted — a well whose images read as partly vanished,
            # from the one tier that was supposed to be the safest because it
            # moves the file whole. The comment below says nothing here is
            # written straight to the destination; this is what makes that true.
            publish_container(sources[0], dest)
            _close_progress(progress)
            return frozenset(written), frozenset()

        # Published through ``atomic_zip``, not written straight to *dest*.
        # A plain ``with ZipFile(dest, "w")`` writes a central directory for
        # whatever landed before an exception, so a member that fails part-way
        # through a well leaves a structurally valid archive holding fewer
        # images than it was asked for, with nothing on it saying so. The tools
        # do write an in-progress marker and clear the destination on the next
        # attempt, but nothing stops a user opening that half-written folder in
        # Review first, and a truncated archive that reads clean is exactly the
        # silent substitution this codebase refuses to make. Staging and
        # renaming also means the destination never exists in a partial state
        # on disk at all.
        #
        # ``durable=False`` keeps the staging file, the member-list check and
        # the atomic rename, and drops only the fsyncs. Nothing a reader can
        # observe changes; what is given up is durability across a power loss,
        # which costs the bulk of the write and buys little here — the source
        # dataset is still on disk beside the destination, so a lost copy is
        # reproducible by re-running, and the tool's in-progress marker already
        # tells the next attempt to clear and redo.
        total = len(planned)
        with atomic_zip(
            dest, compression=zipfile.ZIP_DEFLATED, compresslevel=1,
            durable=False,
        ) as archive:
            for done, (source_archive, entry, dest_name) in enumerate(planned, start=1):
                if source_archive is None:
                    # Tier 3 — a folder source has no compressed form to pass
                    # through, so its file is read and deflated like any other.
                    archive.writestr(dest_name, entry.read_bytes())
                elif member_is_raw_copyable(entry):
                    copy_member_raw(source_archive, entry, archive, dest_name)  # tier 2
                else:
                    archive.writestr(dest_name, source_archive.read(entry.filename))
                if progress is not None:
                    progress(done, total)

    return frozenset(written), frozenset(dropped)


def restamp_overrides(entries: list[dict], out_dir: Path) -> int:
    """Re-stamp every entry's ``seg_fp`` to its destination ``<WELL>_out.zip``'s
    mtime. Returns how many entries could **not** be stamped.

    Every tool that carries ``cell_overrides.json`` across to a destination has
    to re-fingerprint it: a repack changes the archive's mtime even though the
    segmentation itself is untouched, and the fingerprint exists to catch a real
    re-segmentation, so the source dataset's stamp is meaningless against the
    file the destination actually holds.

    The stamp used to be assigned *inside* the ``swallow`` that guards the
    ``stat`` — so a well with no ``<WELL>_out.zip`` in the destination (a run
    made without QC images, or a well whose archive was tidied away) silently
    kept the **source** dataset's fingerprint. Validate then compares that
    against the destination archive, finds it different, and prunes the entry:
    the user's manual cell exclusions vanish, with every step reporting
    success. A fingerprint that could not be computed is not a fingerprint, so
    the key is removed instead — an entry with no ``seg_fp`` is the legacy,
    unfingerprinted shape both readers already understand and keep
    (``review.persistence.cell_overrides.load_overrides``,
    ``datasets.validate.apply._prune_stale_overrides``).

    The count is returned rather than logged here so each caller can say it in
    its own voice, in the summary its dashboard shows: an override carried over
    without a fingerprint survives, but it will no longer be dropped when the
    well really is re-segmented, and that is a fact about the artifact the run
    just produced.
    """
    unstamped = 0
    for entry in entries:
        well = str(entry.get("well", "")).strip()
        zip_path = out_dir / f"{well}_out.zip"
        stamp: str | None = None
        with swallow(f"stat {zip_path}"):
            stamp = f"{zip_path.stat().st_mtime_ns}"
        if stamp is None:
            entry.pop("seg_fp", None)
            unstamped += 1
        else:
            entry["seg_fp"] = stamp
    return unstamped


def unstamped_note(unstamped: int) -> str:
    """The one sentence a tool adds to its cell-override log line when
    :func:`restamp_overrides` could not fingerprint every entry, or ``""``."""
    if not unstamped:
        return ""
    return (
        f" {unstamped} of them could not be fingerprinted against a destination "
        "<WELL>_out.zip (none was written for that well), so they are carried "
        "without one and will not be dropped if that well is re-segmented."
    )


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
