"""Explode a Nikon ND2 plate scan into the per-well archives the pipeline reads.

The third of the packed-file exploders, and structurally the same as the other
two: for every (well, field, kept timepoint, channel) the Z stack is
max-projected to a 2-D plane and stored as
``<experiment>_<channel>_<well>_f<fov>_<timepoint>.tif`` inside that well's
archive. Everything the three do identically lives in
:mod:`pipeline.explode_common` — the member names, the resume check, the
worker-count policy, the strided well split, the timepoint plan, the
in-memory TIFF encode — so what is here is only what reading an ND2 makes
different.

Which is mostly one thing: **an ND2 says how many fields each well has**. A
CZI has a mosaic-tile axis inside each scene and an OME-TIFF has a flat list
of stage positions that a grid has to be laid over, but a Nikon plate scan
writes the well into each position's own name, and several positions naming
one well *are* that well's fields of view. So the well source defaults to the
file here as it does for CZI, and the grid is the fallback for a file whose
positions are unnamed — see
:meth:`pipeline.nd2_read.Nd2Layout.wells_from_positions`.
"""

from __future__ import annotations

import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pipeline.explode_common import (
    ExplodeCancelled,
    ExplodeResult,
    encode_tiff,
    expected_members,
    pick_workers,
    resolve_positions,
    stride_note,
    timepoint_plan,
    well_is_complete,
    worker_groups,
)
from pipeline.nd2_read import Nd2Layout, Nd2Reader, read_nd2_layout
from util.filename_token import safe_token
from util.plate_grid import block_well_labels
from util.zip_write import atomic_zip

#: Where the well each image belongs to is decided. ``"file"`` reads it from
#: each stage position's own name, ``"grid"`` ignores those names and lays the
#: acquisition order over a rectangular block of the plate.
WELL_SOURCES = ("file", "grid")


class Nd2ExplodeCancelled(ExplodeCancelled):
    """Raised when a ``should_abort`` callback stops a conversion mid-run."""


@dataclass
class WellMap:
    """Which stage positions belong to which well, and where that was decided.

    ``positions[i]`` is the stage-position indices making up ``wells[i]``, in
    the order they become FOV 0, 1, … of that well. ``from_file`` is carried
    rather than inferred from the settings because the counts in a readout are
    identical either way and only one of the two is a mapping the user is
    entitled to trust without checking.
    """

    wells: list[str] = field(default_factory=list)
    positions: list[list[int]] = field(default_factory=list)
    fovs_per_well: int = 1
    from_file: bool = False

    @property
    def n_wells(self) -> int:
        return len(self.wells)


def resolve_well_map(
    layout: Nd2Layout,
    *,
    well_source: str = "file",
    grid_rows: int = 0,
    grid_cols: int = 0,
    origin: str = "A01",
    direction: str = "row",
    serpentine: bool = True,
    fovs_per_well: "int | None" = None,
    name: str = "the file",
) -> WellMap:
    """Decide which stage positions make up which well.

    With ``well_source="file"`` the positions' own names decide, which is what
    a Nikon plate scan records and the only mapping that needs no confirming.
    A file that does not name every position, or that names them in a way that
    cannot lay over a plate, raises and points at the grid: a mapping half from
    the file and half from a grid is right exactly where a user is most likely
    to spot-check it.
    """
    if well_source not in WELL_SOURCES:
        raise ValueError(
            f"Unknown well source {well_source!r}; expected one of {WELL_SOURCES}."
        )
    n_positions = layout.n_positions
    if not n_positions:
        raise ValueError(f"{name} holds no stage positions to convert.")

    if well_source == "file":
        wells, fovs = layout.wells_from_positions()
        if not wells:
            named = sum(1 for label in layout.position_wells if label)
            raise ValueError(
                f"{name} does not name a well for every one of its "
                f"{n_positions} stage position(s) in a way that lays over a "
                f"plate — {named} of them carry a name, and the wells they "
                f"name have to take the same number of fields each and must "
                f"not repeat. Switch the well source to the plate grid and "
                f"set the imaged rows and columns, which lays the acquisition "
                f"order over the plate instead."
            )
        if fovs_per_well is not None and fovs_per_well != fovs:
            raise ValueError(
                f"{name} names its wells with {fovs} field(s) of view each, "
                f"but FOVs per well is set to {fovs_per_well}. Clear the field "
                f"to use what the file says, or switch to the plate grid."
            )
        groups = [list(range(i * fovs, (i + 1) * fovs)) for i in range(len(wells))]
        return WellMap(wells=wells, positions=groups, fovs_per_well=fovs,
                       from_file=True)

    labels = block_well_labels(grid_rows, grid_cols, origin=origin,
                               direction=direction, serpentine=serpentine)
    if not labels:
        raise ValueError(
            f"Enter the imaged well-grid rows and columns to lay {name}'s "
            f"{n_positions} stage position(s) over the plate."
        )
    fovs = resolve_positions(n_positions, len(labels), fovs_per_well,
                             what="stage position(s)", name=name)
    groups = [list(range(i * fovs, (i + 1) * fovs)) for i in range(len(labels))]
    return WellMap(wells=list(labels), positions=groups, fovs_per_well=fovs,
                   from_file=False)


def explode_nd2(
    nd2_path: "Path | str",
    out_in_dir: "Path | str",
    *,
    well_source: str = "file",
    grid_rows: int = 0,
    grid_cols: int = 0,
    origin_well: str = "A01",
    direction: str = "row",
    serpentine: bool = True,
    experiment: str = "",
    fovs_per_well: "int | None" = None,
    timepoint_stride: int = 1,
    workers: int = 0,
    log_fn: "Callable[[str], None] | None" = None,
    progress_fn: "Callable[[int, int], None] | None" = None,
    well_progress_fn: "Callable[[str, int, int], None] | None" = None,
    should_abort: "Callable[[], bool] | None" = None,
    resume: bool = False,
) -> ExplodeResult:
    """Explode *nd2_path* into per-well ``<WELL>.zip`` archives in *out_in_dir*.

    *timepoint_stride* converts every N-th timepoint instead of all of them,
    counting from the first and keeping the source's own tokens — see
    :func:`pipeline.explode_common.timepoint_plan`. The default of 1 converts
    everything.

    Wells are converted in parallel across *workers* threads (0 = auto), each
    with its own read handle and its own disjoint set of wells, so no two
    threads touch one archive and none of them contend on a file position.

    *progress_fn* receives ``(planes_written, total_planes)``; *well_progress_fn*
    receives ``(well, planes_written_for_that_well, planes_per_well)``, once
    per plane and deliberately unthrottled — how often is often enough to
    repaint is a question about a UI, so the quantising belongs to the caller
    that owns one (``datasets.nd2_import.apply``).

    *should_abort* is polled per plane and raises :class:`Nd2ExplodeCancelled`;
    each in-flight well's staged archive is discarded, so no partial
    ``<WELL>.zip`` is ever published. Raises ``ValueError`` for an unsound
    source or a mapping that cannot be resolved — both before anything is
    written.
    """
    nd2_path = Path(nd2_path)
    out_in_dir = Path(out_in_dir)

    def _log(message: str) -> None:
        if log_fn:
            log_fn(message)

    def _abort() -> bool:
        return bool(should_abort and should_abort())

    layout = read_nd2_layout(nd2_path, read_times=True)
    # Refuse an unsound source before a single archive is written: everything
    # this catches would otherwise reach the dataset as images that look like
    # data.
    unsound = layout.validation_report(nd2_path.name)
    if unsound:
        raise ValueError(unsound)

    well_map = resolve_well_map(
        layout, well_source=well_source, grid_rows=grid_rows,
        grid_cols=grid_cols, origin=origin_well, direction=direction,
        serpentine=serpentine, fovs_per_well=fovs_per_well,
        name=nd2_path.name,
    )
    # Blank means the file names itself. A constant default would give every
    # plate converted through this function the same experiment token, so two
    # of them concatenated later would be indistinguishable.
    exp_tok = safe_token(experiment or nd2_path.stem, "nd2")

    tokens, timing_note = timepoint_tokens(layout)
    # Index *and* token together: the index reads the plane and the token names
    # it, so a stride that filtered one list without the other would write the
    # right image under the wrong timepoint's name.
    tp_plan = timepoint_plan(tokens, timepoint_stride)
    tp_tokens = [token for _index, token in tp_plan]
    n_kept_t = len(tp_plan)
    fovs = well_map.fovs_per_well
    n_wells = well_map.n_wells

    out_in_dir.mkdir(parents=True, exist_ok=True)
    total = n_wells * fovs * n_kept_t * layout.size_c
    n_workers = pick_workers(workers, n_wells)
    source = "the file's position names" if well_map.from_file else "the plate grid"
    _log(
        f"[nd2] {nd2_path.name}: {layout.n_positions} position(s) -> {n_wells} "
        f"well(s) x {fovs} FOV(s) x {n_kept_t} timepoint(s)"
        f"{stride_note(timepoint_stride, layout.size_t, tp_tokens)}, "
        f"{layout.size_c} channel(s)"
        f"{f', {layout.size_z} Z-plane(s) max-projected' if layout.size_z > 1 else ''}"
        f" -- wells from {source}, {n_workers} worker(s).\n"
    )
    if timing_note:
        _log(f"[nd2] {timing_note}\n")

    # Wells a previous attempt already finished. Each archive is published
    # atomically, so one that exists holding every member these settings call
    # for is complete and correct. Off by default: leaving wells alone is only
    # right once the caller has established that this folder holds *this*
    # conversion's interrupted output.
    resumable: set[str] = set()
    if resume:
        resumable = {
            label for label in well_map.wells
            if well_is_complete(
                out_in_dir / f"{label}.zip",
                expected_members(exp_tok, layout.channel_names, label, fovs,
                                 tp_tokens))
        }
        if resumable:
            _log(f"[nd2] Resuming: {len(resumable)} of {n_wells} well(s) are "
                 f"already converted and will be left alone.\n")

    lock = threading.Lock()
    done = 0
    planes_per_well = max(1, fovs * n_kept_t * layout.size_c)

    def _bump(well: "str | None" = None, well_done: int = 0) -> None:
        nonlocal done
        with lock:
            done += 1
            current = done
        if progress_fn:
            progress_fn(current, total)
        # Outside the lock: each call names its own well, so interleaving is
        # harmless, and taking the lock per plane would serialise the very
        # parallelism this reports on.
        if well_progress_fn and well is not None:
            well_progress_fn(well, well_done, planes_per_well)

    if progress_fn:
        progress_fn(0, total)

    def _convert_wells(well_indices: "list[int]"):
        written = 0
        converted: list[str] = []
        resumed: list[str] = []
        read_s = write_s = 0.0
        with Nd2Reader(nd2_path, layout) as reader:
            for index in well_indices:
                if _abort():
                    raise Nd2ExplodeCancelled()
                label = well_map.wells[index]
                if label in resumable:
                    # Reported full rather than silently absent: the plate map
                    # this drives would otherwise show a finished well as one
                    # that never ran.
                    resumed.append(label)
                    if well_progress_fn:
                        well_progress_fn(label, planes_per_well, planes_per_well)
                    for _ in range(planes_per_well):
                        _bump()
                    continue
                well_written = 0
                with atomic_zip(out_in_dir / f"{label}.zip",
                                compression=zipfile.ZIP_STORED) as archive:
                    for fov, position in enumerate(well_map.positions[index]):
                        for t_index, tp_token in tp_plan:
                            for channel in range(layout.size_c):
                                if _abort():
                                    raise Nd2ExplodeCancelled()
                                started = time.perf_counter()
                                plane = reader.read_projection(
                                    position, t_index, channel)
                                read_s += time.perf_counter() - started
                                started = time.perf_counter()
                                member = (
                                    f"{exp_tok}_{layout.channel_names[channel]}_"
                                    f"{label}_f{fov}_{tp_token}.tif")
                                archive.writestr(member, encode_tiff(plane))
                                write_s += time.perf_counter() - started
                                written += 1
                                well_written += 1
                                _bump(label, well_written)
                converted.append(label)
        return written, converted, read_s, write_s, resumed

    result = ExplodeResult(
        channels=list(layout.channel_names),
        timepoints=list(tp_tokens),
        fovs_per_well=fovs,
    )
    all_wells: list[str] = []
    all_resumed: list[str] = []
    read_total = write_total = 0.0
    started_at = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = [pool.submit(_convert_wells, group)
                   for group in worker_groups(n_wells, n_workers)]
        for future in as_completed(futures):
            count, wells, read_time, write_time, resumed = future.result()
            result.images_written += count
            all_wells.extend(wells)
            all_resumed.extend(resumed)
            read_total += read_time
            write_total += write_time
    result.wells = sorted(set(all_wells) | set(all_resumed))
    result.wells_resumed = sorted(set(all_resumed))

    wall = time.perf_counter() - started_at
    rate = result.images_written / wall if wall > 0 else 0.0
    kept = (f" ({len(result.wells_resumed)} well(s) kept from the previous run)"
            if result.wells_resumed else "")
    _log(
        f"[nd2] Wrote {result.images_written} image(s) for "
        f"{len(result.wells)} well(s){kept} into {out_in_dir.name}/ in "
        f"{wall:.1f}s ({rate:.0f} planes/s; read {read_total:.1f}s, write "
        f"{write_total:.1f}s summed across {n_workers} worker(s)).\n"
    )
    return result


def timepoint_tokens(layout: Nd2Layout) -> tuple[list[str], str]:
    """``(tokens, note)`` — elapsed hours where the file supports them.

    The same decision the other two converters make, through the same shared
    renderer, so one acquisition exported as an ND2 and as a CZI converts to
    the same timepoint names. Every route to the positional fallback is a
    *degradation* — the acquisition recorded when each plane was taken and the
    archives will not carry it — so each comes back with a note the run log
    prints and the page shows before the run.
    """
    from pipeline.explode_common import elapsed_hour_tokens

    positional = list(layout.timepoint_tokens)
    scan = layout.time_scan
    if scan is None:
        return positional, ""
    resolved = elapsed_hour_tokens(scan.hours_by_index, layout.size_t,
                                   source_reasons=scan.reasons)
    if resolved.tokens is not None:
        return resolved.tokens, (
            f"{resolved.note[:1].upper()}{resolved.note[1:]}."
            if resolved.note else "")
    if not resolved.reasons:
        return positional, ""
    return positional, (
        f"Timepoints are named by position (t0, t1, …) rather than by elapsed "
        f"time — {'; '.join(resolved.reasons)}.")
