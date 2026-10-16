"""Concatenate's second option: add dataset 2 to dataset 1 *in place*.

"Create a new dataset" writes a fresh copy of both datasets into a third
folder. For a running experiment that is the wrong shape: each added
timepoint re-copies every earlier one, and at 60 wells × 25 tiles × 3–4
channels (about 12.6 GB per channel per timepoint) twenty timepoints write
7.9–10.5 TB (tracker Q9). Here dataset 2's images are instead appended to
dataset 1's own ``in/<WELL>.zip`` archives, under dataset 2's shifted names,
and nothing of dataset 1 is copied.

Which pairs qualify (:func:`structural_refusal`): **dataset 2 must be raw** —
its images are added and the analysis is left to an incremental Analyze run
on dataset 1, which processes exactly the new timepoint (it subtracts the
FOV-groups already in each well's measurements CSV from those in its
archive). Dataset 1 may be raw or analysed; an analysed dataset 1 keeps its
``out/`` folder untouched, ``pipeline_info.json`` included, exactly as an
analysed + raw "new dataset" merge keeps dataset 1's outputs. Dataset 1's
wells must be zip archives, and an acquisition record dataset 1 has must have
a counterpart in dataset 2, because a record that stopped describing dataset
1's images would stop its next Analyze run.

Everything is refused before a byte is written when a member would collide
(:func:`plan_wells`): a destination name already in the archive — what a
second run of the same append produces against an analysed dataset 1 whose
sidecar has not moved on — or an image whose bytes (CRC and size) are already
in that well — what it produces against a raw dataset 1, whose last timepoint
*has* moved on, so the shifted names are new and only the contents repeat.

The bytes go through :class:`util.zip_append.AppendRun`: every archive is
journalled before it is touched, each new member is copied in its compressed
form (:mod:`datasets.core.zip_passthrough`, so dataset 2's compression is
kept, as everywhere else in Concatenate), and each well is verified by reading
every new member back against dataset 2's CRC and sizes. The acquisition
record is merged last; only then is the run committed. **The run is
all-or-nothing**: the first well that fails — or a cancellation — stops it,
and every archive appended so far is restored byte for byte, with the record,
so dataset 1 is never left holding the new timepoint in some wells only.

**Dataset 2's files are never deleted** (the user's decision): the run log
ends with every source file whose contents were appended and verified, per
well, so they can be removed by hand.

Qt-free; :mod:`~datasets.concatenate.worker` runs it off the UI thread.
"""

from __future__ import annotations

import logging
import threading
import zipfile
import zlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from datasets.concatenate.apply import _merge_acquisition_records
from datasets.concatenate.concatenate_compute import (
    KIND_ANALYSED,
    ConcatError,
    MergePlan,
    in_containers,
    open_pair,
    open_pair_cached,
    plan_pair,
    refuse_same_folder,
    rewrite_member_name,
)
from datasets.concatenate.raw_compute import is_raw_dataset
from datasets.core.concurrency import pick_workers
from datasets.core.containers import WellFraction, copy_container_members
from datasets.core.dataset_paths import DatasetPaths
from datasets.core.member_names import is_os_junk_member
from datasets.core.provenance import record_derivation
from datasets.core.zip_passthrough import copy_member_raw, member_is_raw_copyable
from util.acquisition_record import AcquisitionRecordError, read_record, record_path
from util.image_exts import is_run_input_member
from util.aw_safe import swallowed
from util.zip_append import AppendJournalError, AppendRun, recover

#: How many colliding names a refusal spells out before it says "and N more".
_SHOWN = 5
_READ_CHUNK = 1 << 20


def _null(*_args) -> None:
    return None


def _never() -> bool:
    return False


# ── which pairs can be appended ──────────────────────────────────────────────
def structural_refusal(ds1: DatasetPaths, ds2: DatasetPaths, kind: str) -> str:
    """Why this opened pair can't be appended in place, or ``""``.

    Reads folder listings and the two acquisition records only, so the page
    can ask it whenever a dataset field changes.
    """
    if kind == KIND_ANALYSED or not is_raw_dataset(ds2.root):
        return ("Adding in place needs a raw dataset 2 (converted images, not yet analysed): "
                "its images are appended to dataset 1's archives and an incremental Analyze run "
                "then processes the new timepoint. Dataset 2 is analysed, so create a new "
                "dataset instead.")
    first, second = in_containers(ds1), in_containers(ds2)
    folders = sorted(well for well in second if well in first and first[well].is_dir())
    if folders:
        return (f"Dataset 1 keeps well {folders[0]}'s images in a folder rather than a zip "
                f"archive ({len(folders)} well(s) like that), and adding in place appends to "
                "archives. Create a new dataset instead.")
    try:
        record1, record2 = read_record(ds1.root), read_record(ds2.root)
    except AcquisitionRecordError as exc:
        return f"An acquisition record can't be read, so it can't be carried: {exc}"
    if record1 is not None and record2 is None:
        return ("Dataset 1 has an acquisition record and dataset 2 has none, so after adding in "
                "place the record would no longer describe dataset 1's images and Analyze would "
                "refuse it. Convert dataset 2 with an importer that writes a record, or create a "
                "new dataset (which then carries no record).")
    return ""


def in_place_refusal(ds1_root: Path | str, ds2_root: Path | str) -> str:
    """The page's question: why "Add to dataset 1 in place" is unavailable, or ``""``.

    Opens the pair through the same remembered reading the gap preview uses,
    so asking costs nothing more on a keystroke. A pair that can't be
    concatenated at all answers with that reason.
    """
    try:
        ds1, ds2, _info1, _info2, kind = open_pair_cached(ds1_root, ds2_root)
        refuse_same_folder(ds1, ds2)
    except ConcatError as exc:
        return str(exc)
    return structural_refusal(ds1, ds2, kind)


def build_append_plan(ds1_root: Path | str, ds2_root: Path | str, *,
                      gap_hours: float | None = None) -> MergePlan:
    """Validate the pair and plan dataset 2's append into dataset 1 itself.

    The same planning as a new-dataset merge (:func:`plan_pair`), with
    dataset 1 as the destination and ``in_place`` set. Reads only.
    """
    ds1, ds2, info1, info2, kind = open_pair(ds1_root, ds2_root)
    refuse_same_folder(ds1, ds2)
    reason = structural_refusal(ds1, ds2, kind)
    if reason:
        raise ConcatError(reason)
    return plan_pair(ds1, ds2, info1, info2, kind, dest=ds1, gap_hours=gap_hours, in_place=True)


def recover_dataset(root: Path | str, log: Callable[[str], None] = _null) -> None:
    """Undo an in-place append a crash left unfinished in *root*, logging what was done.

    Raises :class:`ConcatError` when an append is still running there, or the
    undo itself could not complete; the dataset must not be read in either case.
    """
    in_dir = Path(root) / "in"
    try:
        result = recover(in_dir)
    except AppendJournalError as exc:
        raise ConcatError(f"{Path(root).name}: {exc}") from exc
    for line in result.lines:
        log(f"Recovery ({Path(root).name}): {line}")
    if result.blocked:
        raise ConcatError(result.blocked)


# ── the per-well plan ────────────────────────────────────────────────────────
@dataclass
class WellAppend:
    """What one well receives: dataset 2's members, and the names they take."""

    well: str
    target: Path                       # dataset 1's archive (created when absent)
    source: Path                       # dataset 2's container, a zip or a folder
    members: list[tuple[str, str]] = field(default_factory=list)   # (source name, destination name)
    #: Destination name → dataset 2's (CRC, size), as planned: what verification expects.
    expected: dict[str, tuple[int, int]] = field(default_factory=dict)
    junk_skipped: int = 0
    unshifted: list[str] = field(default_factory=list)

    @property
    def creates(self) -> bool:
        return not self.target.exists()


def _file_content(path: Path) -> tuple[int, int]:
    """(CRC-32, size) of a loose file, read in chunks."""
    crc, size = 0, 0
    with open(path, "rb") as handle:
        while chunk := handle.read(_READ_CHUNK):
            crc = zlib.crc32(chunk, crc)
            size += len(chunk)
    return crc & 0xFFFFFFFF, size


def _source_members(source: Path, *, halt: Callable[[], bool] = _never,
                    log: Callable[[str], None] = _null) -> list[tuple[str, tuple[int, int] | None]]:
    """``(name, (crc, size))`` of every file in *source*.

    A zip's come from its central directory. A folder's are read: a folder has
    no stored checksum, and without one a second run of the same append would
    pass the content check and add the timepoint again under new names. OS
    debris is not read (``None``); it is never appended.
    """
    if source.is_dir():
        files = [path for path in sorted(source.rglob("*")) if path.is_file()]
        # Reading is the cost of a folder well (every byte, before anything is
        # written), so it is announced and can be stopped between files.
        log(f"  Reading dataset 2's folder well {source.name} ({len(files)} file(s)) to checksum "
            "it before anything is written…")
        out = []
        for path in files:
            if halt():
                raise ConcatError("Cancelled — nothing was appended.")
            name = path.relative_to(source).as_posix()
            out.append((name, None if is_os_junk_member(name) else _file_content(path)))
        return out
    with zipfile.ZipFile(source) as archive:
        return [(i.filename, (i.CRC, i.file_size)) for i in archive.infolist() if not i.is_dir()]


def _plan_one(plan: MergePlan, well: str, source: Path, target: Path,
              problems: list[str], *, halt: Callable[[], bool] = _never,
              log: Callable[[str], None] = _null) -> WellAppend:
    item = WellAppend(well=well, target=target, source=source)
    names: set[str] = set()
    contents: dict[tuple[int, int], str] = {}
    if target.exists():
        with zipfile.ZipFile(target) as archive:
            for info in archive.infolist():
                names.add(info.filename)
                if not info.is_dir() and info.file_size:
                    contents.setdefault((info.CRC, info.file_size), info.filename)
    for name, content in _source_members(source, halt=halt, log=log):
        if is_os_junk_member(name):
            item.junk_skipped += 1
            continue
        dest, rewrote = rewrite_member_name(
            name, schema=plan.schema, channels=plan.channels,
            offset_hours=plan.offset_hours, tp_format=plan.tp_format)
        if not rewrote and is_run_input_member(name):
            item.unshifted.append(f"{well}/{name}")
        if dest in names:
            problems.append(f"{well}: {dest!r} is already in {target.name}")
        elif content is not None and content in contents:
            problems.append(f"{well}: {name!r} has the same bytes as {contents[content]!r}, "
                            f"already in {target.name}")
        else:
            names.add(dest)
            item.members.append((name, dest))
            if content is not None:
                item.expected[dest] = content
    return item


def plan_wells(plan: MergePlan, *, log: Callable[[str], None] = _null,
               is_cancelled: Callable[[], bool] = _never) -> list[WellAppend]:
    """Every well dataset 2 adds to, or :class:`ConcatError` naming what would collide.

    Nothing is written. A refusal names up to :data:`_SHOWN` collisions and
    counts the rest, so a re-run of an append that already happened is
    recognisable at a glance.
    """
    first, second = in_containers(plan.ds1), in_containers(plan.ds2)
    problems: list[str] = []
    items = []
    for well, source in sorted(second.items()):
        if is_cancelled():
            raise ConcatError("Cancelled — nothing was appended.")
        items.append(_plan_one(plan, well, source,
                               first.get(well) or plan.ds1.in_dir / f"{well}.zip", problems,
                               halt=is_cancelled, log=log))
    if problems:
        more = f" (and {len(problems) - _SHOWN} more)" if len(problems) > _SHOWN else ""
        raise ConcatError(
            f"Nothing was appended: {len(problems)} of dataset 2's file(s) are already in "
            f"dataset 1 — has this dataset already been added? "
            + "; ".join(problems[:_SHOWN]) + more + ".")
    items = [item for item in items if item.members]
    if not items:
        raise ConcatError("Dataset 2 has no images to add to dataset 1.")
    return items


# ── writing one well ─────────────────────────────────────────────────────────
class _Stopped(Exception):
    """Another well failed, or the run was cancelled: stop copying this one."""


def _write_members(zf: zipfile.ZipFile, item: WellAppend, progress,
                   halt: Callable[[], bool]) -> None:
    total = len(item.members)
    if item.source.is_dir():
        for done, (name, dest) in enumerate(item.members, start=1):
            if halt():
                raise _Stopped()
            # A folder's file has no compressed form to pass through; stored,
            # as the converters write well archives.
            zf.write(item.source / name, arcname=dest, compress_type=zipfile.ZIP_STORED)
            progress(done, total)
        return
    with zipfile.ZipFile(item.source) as src:
        for done, (name, dest) in enumerate(item.members, start=1):
            if halt():
                raise _Stopped()
            info = src.getinfo(name)
            if member_is_raw_copyable(info):
                copy_member_raw(src, info, zf, dest)
            else:
                zf.writestr(dest, src.read(name))
            progress(done, total)


def _expected(item: WellAppend) -> dict[str, tuple[int, int]]:
    """Destination name → (CRC, size) of dataset 2's original.

    A folder's were read while planning; a zip's are its central directory's.
    """
    if item.source.is_dir():
        return dict(item.expected)
    with zipfile.ZipFile(item.source) as src:
        return {dest: (src.getinfo(name).CRC, src.getinfo(name).file_size)
                for name, dest in item.members}


def verify_well(item: WellAppend) -> None:
    """Every new member is in dataset 1's archive with dataset 2's CRC and size, and reads back.

    Reading each member to its end makes :mod:`zipfile` check the CRC of the
    bytes actually on disk, so a member that was written short or garbled is
    caught here rather than by the next Analyze run.
    """
    expected = _expected(item)
    with zipfile.ZipFile(item.target) as archive:
        landed = {i.filename: i for i in archive.infolist()}
        for dest, (crc, size) in expected.items():
            info = landed.get(dest)
            if info is None:
                raise ConcatError(f"Verification failed: well {item.well}'s {dest!r} is not in "
                                  f"{item.target.name} after the append.")
            if (info.CRC, info.file_size) != (crc, size):
                raise ConcatError(f"Verification failed: well {item.well}'s {dest!r} holds "
                                  "different bytes from dataset 2's original.")
            with archive.open(info) as handle:
                while handle.read(_READ_CHUNK):
                    pass


def _append_well(run: AppendRun, item: WellAppend, *, halt: Callable[[], bool],
                 progress: WellFraction) -> None:
    slice_progress = progress.slice_progress()
    if item.creates:
        rename = dict(item.members)
        # Durable like the appends beside it: committed as part of the run, so
        # it must be on disk when the run journal goes.
        run.create(item.target, lambda: copy_container_members(
            item.target, [item.source], lambda _c, name: rename.get(name),
            progress=slice_progress, durable=True))
    else:
        run.append(item.target, lambda zf: _write_members(zf, item, slice_progress, halt))
    verify_well(item)
    progress.finish()


# ── the run ──────────────────────────────────────────────────────────────────
def _source_report(plan: MergePlan, items: list[WellAppend]) -> list[str]:
    """The end of the log: every source file appended and verified, per well."""
    lines = ["", "Dataset 2's files were left where they are. Every file below was appended to "
             "dataset 1 and verified, so you can delete them by hand:"]
    for item in sorted(items, key=lambda i: i.well):
        if item.source.is_dir():
            lines.append(f"  {item.well}: {item.source}{'/'} ({len(item.members)} file(s))")
            lines += [f"      {item.source / name}" for name, _dest in item.members]
        else:
            junk = (f"; {item.junk_skipped} operating-system file(s) in it were not needed"
                    if item.junk_skipped else "")
            lines.append(f"  {item.well}: {item.source} ({len(item.members)} file(s){junk})")
    record = record_path(plan.ds2.root)
    if record.exists():
        lines.append(f"  acquisition record: {record} (merged into dataset 1's)")
    return lines


def _preamble(plan: MergePlan, items: list[WellAppend], emit) -> None:
    emit(f"Adding dataset 2 to dataset 1 in place ({plan.ds1.root}). Dataset 1's last "
         f"timepoint is {plan.ds1_last_hours:g}h; dataset 2 follows {plan.gap_hours:g}h later "
         f"({plan.gap_source}) → dataset 2 shifted by {plan.offset_hours:g}h.")
    for note in (plan.gap_warning, plan.scale_warning, plan.format_warning):
        if note:
            emit(f"Note: {note}")
    created = sum(1 for item in items if item.creates)
    emit(f"Wells: {len(items) - created} appended to, {created} new archive(s); dataset 1's "
         "existing images are not moved or copied.")


def append_in_place(
    plan: MergePlan,
    log: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[str, str], None] | None = None,
    workers: int = 0,
    sub_progress: Callable[[str, int, int], None] | None = None,
) -> str:
    """Append dataset 2 to dataset 1 as *plan* says; returns the summary line.

    The callbacks are :func:`datasets.concatenate.apply.execute_plan`'s. Raises
    :class:`ConcatError` with every archive restored on any failure or
    cancellation; the log then lists what was restored.
    """
    if not plan.in_place:
        raise ValueError("append_in_place needs a plan from build_append_plan")
    emit = log or _null
    cancelled = is_cancelled or _never
    report = progress or _null
    items = plan_wells(plan, log=emit, is_cancelled=cancelled)
    _preamble(plan, items, emit)
    lock, stop = threading.Lock(), threading.Event()

    def one(item: WellAppend) -> None:
        if stop.is_set() or cancelled():
            stop.set()
            raise _Stopped()
        with lock:
            emit(f"  {item.well}…")
            report(item.well, "merging")
        _append_well(run, item, halt=lambda: stop.is_set() or cancelled(),
                     progress=WellFraction(sub_progress or _null, item.well, 1))

    run = AppendRun(plan.ds1.in_dir, preserve=[record_path(plan.ds1.root)])
    try:
        with run:
            with ThreadPoolExecutor(max_workers=pick_workers(workers, len(items))) as pool:
                futures = {pool.submit(one, item): item for item in items}
                for future in as_completed(futures):
                    try:
                        future.result()
                    except BaseException:
                        stop.set()
                        raise
                    report(futures[future].well, "merged")
            emit("Every appended image read back with dataset 2's checksum.")
            _merge_acquisition_records(plan, emit)
            run.commit()
    except Exception as exc:
        _report_rollback(run, emit)
        stopped = isinstance(exc, _Stopped) or cancelled()
        # A failed undo is checked first: "restored exactly" is only ever said
        # of an undo that completed, whatever ended the run.
        if run.rollback_error:
            cause = "it was cancelled" if stopped else f"it failed ({exc})"
            raise ConcatError(f"Adding in place stopped because {cause}, and undoing it did not "
                              f"complete: {run.rollback_error} The journals were kept; opening "
                              "dataset 1 again retries the undo.") from exc
        if stopped:
            raise ConcatError("Cancelled — dataset 1 was restored exactly as it was; nothing "
                              "was appended.") from exc
        raise ConcatError(f"Adding in place failed: {exc} Dataset 1 was restored exactly as it "
                          "was; nothing was appended.") from exc
    return _finish(plan, items, emit)


def _report_rollback(run: AppendRun, emit) -> None:
    if run.rollback_lines:
        emit("Undoing the append:")
        for line in run.rollback_lines:
            emit(f"  {line}")


def _record_provenance(plan: MergePlan) -> bool:
    """Append dataset 1's ``derived_from`` line; ``False`` when it did not reach disk."""
    try:
        return record_derivation(plan.ds1, tool="concatenate", sources=(plan.ds2,),
                                 mode="in_place", offset_hours=plan.offset_hours,
                                 gap_hours=plan.gap_hours, gap_source=plan.gap_source)
    except (OSError, ValueError):
        swallowed("recording the in-place append in the provenance log", level=logging.WARNING)
        return False


def _finish(plan: MergePlan, items: list[WellAppend], emit) -> str:
    unshifted = [name for item in items for name in item.unshifted]
    if unshifted:
        shown = ", ".join(unshifted[:5]) + (", …" if len(unshifted) > 5 else "")
        emit(f"Note: {len(unshifted)} dataset 2 image(s) have no timepoint in their file name, so "
             f"they were added unshifted: {shown}.")
    analysed = not is_raw_dataset(plan.ds1.root)
    if analysed and not _record_provenance(plan):
        # The append is committed: a history line that could not be written
        # must not turn it into a reported failure (a retry would be refused
        # as a duplicate).
        emit("Note: the dataset's provenance log could not be written, so this addition is "
             "not in its history; the images themselves were added and verified.")
    for line in _source_report(plan, items):
        emit(line)
    images = sum(len(item.members) for item in items)
    next_step = (" Run Analyze on it with Incremental ticked: only the new timepoint is processed."
                 if analysed else " Analyze it to process every timepoint.")
    return (f"Added {images} file(s) from dataset 2 to {len(items)} well(s) of {plan.ds1.root} in "
            f"place (dataset 2 shifted +{plan.offset_hours:g}h).{next_step}")
