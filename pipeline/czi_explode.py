"""De-interleave a Zeiss CZI into the per-well ``.zip`` archives the pipeline reads.

A plate scan saved as a single ``.czi`` holds every well, field of view,
timepoint, channel and focal plane of the run in one file. All-Well's pipeline
is filename-token driven and wants the opposite: one 2-D TIFF per
(channel, well, FOV, timepoint), named to the filename schema, grouped into one
archive per well. This module is the bridge, and it is the CZI half of the pair
:mod:`pipeline.ome_explode` is the OME-TIFF half of — same output layout, same
per-well atomic archives, same resume rule, all of it shared through
:mod:`pipeline.explode_common`.

**Z is collapsed by maximum-intensity projection**, per (scene, tile, T, C) —
the standard reduction for fluorescence quantification, and the same one the
OME exploder applies, so a plate converted from either source is measuring the
same thing.

**Where the wells come from is the one real difference from the OME importer.**
A packed OME-TIFF records stage coordinates and leaves the plate layout to be
reconstructed from them, which is why that page asks for a grid. A CZI plate
scan normally names its scenes after the wells they were taken from, so this
importer reads the mapping out of the file and the user confirms it rather than
supplying it. When the file does not name its scenes — a manually defined scene
list, an older ZEN version — the grid is still there, and
:func:`resolve_well_map` says which of the two produced the answer so the page
can show it. It never mixes them: a mapping that is half from the file and half
from a grid is right exactly where a user is most likely to spot-check it.

Depends on numpy, tifffile and :mod:`pipeline.czi_read` plus the ``util``
helpers — no ``review`` / ``widgets`` / Qt, so it stays inside the pipeline
self-sufficiency contract (ARCHITECTURE §6.2).
"""

from __future__ import annotations

import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pipeline.czi_read import CziReader, CziReadError
from pipeline.czi_set import CziSet, ScenePosition, read_czi_set
from pipeline.explode_common import (
    ExplodeCancelled,
    ExplodeResult,
    encode_tiff,
    expected_members,
    pick_workers,
    resolve_positions,
    shortfall_summary,
    stride_note,
    uneven_fields_note,
    subset_note,
    timepoint_plan,
    well_is_complete,
    wells_to_convert,
    worker_groups,
)
from util.filename_token import safe_token
from util.plate_grid import block_well_labels
from util.zip_write import atomic_zip

#: Where the well label of each scene comes from. ``"file"`` reads the names
#: the CZI's own metadata gives its scenes; ``"grid"`` ignores them and lays the
#: acquisition order over a rectangular block of the plate, exactly as the
#: OME-TIFF importer does.
WELL_SOURCES = ("file", "grid")


class CziExplodeCancelled(ExplodeCancelled):
    """Raised when a ``should_abort`` callback stops a conversion mid-run."""


@dataclass(frozen=True)
class WellMap:
    """Which stage positions belong to which well, and where that was decided.

    ``positions[i]`` is the stage positions making up ``wells[i]``, in the
    order they become FOV 0, 1, … of that well. Each carries the file it is in,
    so a plate split into one file per scene and a plate held in a single file
    produce the same structure and the exploder below does not have to know
    which it was given — nor which of the two well sources produced the labels.
    """

    wells: list[str] = field(default_factory=list)
    positions: list[list[ScenePosition]] = field(default_factory=list)
    fovs_per_well: int = 0
    #: True when ``wells`` came from the CZI's own scene names. Reported rather
    #: than inferred from the settings, because the page's readout has to say
    #: which of the two answers the user is looking at — the counts are
    #: identical either way.
    from_file: bool = False

    @property
    def n_wells(self) -> int:
        return len(self.wells)


def _default_experiment(selected: Path, czi_set: CziSet) -> str:
    """The experiment token a conversion uses when the user names none.

    The *folder* for a set of several files, the file's own stem for one. A
    split-scene export is a folder of ``Exp7_A1.czi``, ``Exp7_A2.czi``, … and
    taking the stem of whichever member sorted first would stamp that one
    scene's well into every archive on the plate:
    ``Exp7_A1_DAPI_H11_f0_t0.tif``. The folder is the thing they are all part
    of, which is what the token is for.
    """
    if czi_set.n_files > 1:
        folder = selected if selected.is_dir() else selected.parent
        return folder.name or selected.stem
    return selected.stem


class _ReaderPool:
    """The :class:`~pipeline.czi_read.CziReader` handles one worker thread holds.

    Opened lazily, one per file that thread's wells actually read from, and all
    closed together on exit. Lazily because a split-scene plate has one file
    per well: opening every file in every thread would be ninety-six handles
    per thread to read one well, and closing them only at the end of the run
    would hold all of them open at once.

    Not shared between threads. A reader owns a file position, so two threads
    reading through one would interleave seeks — which is the same reason
    ``explode_ome_tiff`` gives every worker its own ``TiffFile``.
    """

    def __init__(self, layouts: "dict[Path, object]") -> None:
        self._layouts = layouts
        self._open: dict[Path, CziReader] = {}

    def __enter__(self) -> "_ReaderPool":
        return self

    def __exit__(self, *exc_info) -> None:
        for reader in self._open.values():
            reader.__exit__(*exc_info)
        self._open.clear()

    def for_path(self, path: Path) -> CziReader:
        reader = self._open.get(path)
        if reader is None:
            reader = CziReader(path, self._layouts.get(path))
            reader.__enter__()
            self._open[path] = reader
        return reader


def resolve_well_map(
    source: CziSet,
    *,
    well_source: str = "file",
    grid_rows: int = 0,
    grid_cols: int = 0,
    origin: str = "A01",
    direction: str = "row",
    serpentine: bool = True,
    fovs_per_well: int | None = None,
    name: str = "This file",
) -> WellMap:
    """Resolve *layout*'s scenes and tiles onto well labels.

    Two sources, and only two: the scene names in the file, or a rectangular
    block of the plate. There is deliberately no third — the OME-TIFF importer
    additionally accepts an explicit click-defined visit order because its page
    has an editor that produces one, and a parameter with no producer is a
    branch the application never takes however well it is written (CLAUDE.md
    §7.7). Adding it back alongside such an editor is four lines.

    Raises ``ValueError`` — before anything is written — when the settings
    cannot produce a whole mapping: a grid that does not divide the positions,
    a grid that runs off the plate, a FOVs-per-well that contradicts the file,
    or ``well_source="file"`` on a file that does not name its scenes.
    """
    if well_source not in WELL_SOURCES:
        raise ValueError(
            f"Unknown well source {well_source!r}; expected one of {WELL_SOURCES}."
        )
    if source.problem:
        raise ValueError(source.problem)
    positions = list(source.positions)
    if not positions:
        raise ValueError(f"{name} holds no image tiles to convert.")

    if well_source == "file":
        groups = source.scene_well_groups()
        if not groups:
            raise ValueError(_why_the_file_cannot_name_the_wells(source, name))
        tiles = source.fovs_per_scene
        if tiles <= 0:
            raise ValueError(
                f"{name}'s scenes hold different numbers of tiles, so there is "
                f"no single fields-of-view-per-well for the plate."
            )
        # Every scene in the set holds exactly ``tiles`` tiles — ragged scenes
        # are refused inside a file by ``CziLayout`` and across a set by
        # ``_MUST_AGREE`` — so scene *i* owns ``positions[i * tiles:]``, and a
        # well's fields are its scenes' tiles laid end to end in scene order.
        wells = [well for well, _scenes in groups]
        grouped: list[list[ScenePosition]] = [
            [position
             for scene in scenes
             for position in positions[scene * tiles:(scene + 1) * tiles]]
            for _well, scenes in groups
        ]
        # The nominal count is the largest, so that a plate where one well was
        # imaged at fewer positions is described by its plate rather than by
        # its smallest well. Every well keeps its own count in ``positions``.
        fovs = max(len(block) for block in grouped)
        if fovs_per_well is not None and fovs_per_well != fovs:
            raise ValueError(
                f"{name} holds {fovs} field(s) per well, but FOVs per well is "
                f"set to {fovs_per_well}. Clear it to use the file's own "
                f"count, or switch the well source to the plate grid."
            )
        return WellMap(wells=wells, positions=grouped, fovs_per_well=fovs,
                       from_file=True)

    if grid_rows <= 0 or grid_cols <= 0:
        raise ValueError(
            "Enter the imaged well-grid dimensions (rows × columns) as "
            "positive whole numbers."
        )
    wells = block_well_labels(grid_rows, grid_cols, origin=origin or "A01",
                              direction=direction, serpentine=serpentine)
    fovs = resolve_positions(len(positions), len(wells), fovs_per_well,
                             what="stage position(s)", name=name)
    return WellMap(
        wells=wells,
        positions=[positions[i * fovs:(i + 1) * fovs] for i in range(len(wells))],
        fovs_per_well=fovs,
        from_file=False,
    )


def _why_the_file_cannot_name_the_wells(source: CziSet, name: str) -> str:
    """Which of the two conditions ``scene_well_groups`` refused on, and how.

    It refuses unless **every** scene names a well: either none of them does,
    or some do and some do not. One sentence covered both, and it quoted the
    count of scenes that *did* resolve whatever had actually failed, so the
    message a user got was built out of the check that passed.

    Scenes naming the *same* well is not one of the causes and never was a
    fault — several scenes in one well are that well's fields of view, which
    is what ``CziSet.scene_well_groups`` reads them as. The sentence for it
    said a well "would be written from more than one scene and only the last
    would survive", which described a converter that no longer exists.

    Every sentence names the scenes rather than the source, so that the verb
    does not have to agree with a subject that is one file here and six
    hundred there.
    """
    labels = list(source.scene_wells)
    where = (f"{name}'s {source.n_files} file(s)" if source.n_files > 1
             else name)
    advice = ("Switch the well source to the plate grid and set the imaged "
              "rows and columns, which lays the acquisition order over the "
              "plate instead.")
    if not labels or not any(labels):
        return (f"No well is named for any of the {len(labels)} scene(s) in "
                f"{where}. {advice}")

    unnamed = [index for index, label in enumerate(labels) if not label]
    shown = ", ".join(str(index) for index in unnamed[:3])
    more = "" if len(unnamed) <= 3 else f", and {len(unnamed) - 3} more"
    return (f"{len(unnamed)} of the {len(labels)} scene(s) in {where} "
            f"name no well (scene {shown}{more}); the other "
            f"{len(labels) - len(unnamed)} do. A mapping where some "
            f"scenes land on their real wells and the rest land wherever "
            f"the grid put them is wrong exactly where it would be "
            f"spot-checked. {advice}")


def explode_czi(
    czi_path: "Path | str",
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
    files: "list[Path] | None" = None,
    workers: int = 0,
    log_fn: "Callable[[str], None] | None" = None,
    progress_fn: "Callable[[int, int], None] | None" = None,
    well_progress_fn: "Callable[[str, int, int], None] | None" = None,
    should_abort: "Callable[[], bool] | None" = None,
    well_subset: "tuple[str, ...]" = (),
    resume: bool = False,
) -> ExplodeResult:
    """Explode *czi_path* into per-well ``<WELL>.zip`` archives in *out_in_dir*.

    *czi_path* is a single ``.czi`` or a folder of them; *files* names the
    exact set to convert when a caller has already decided (the converter
    page, whose tick-box widens one file to its folder). One file per scene is
    ZEN's *Split Scenes* export and is as ordinary an input as a single file
    holding the whole plate — see :mod:`pipeline.czi_set`.

    *timepoint_stride* converts every N-th timepoint instead of all of them,
    counting from the first and keeping the source's own tokens — see
    :func:`pipeline.explode_common.timepoint_plan`. The default of 1 converts
    everything.

    For every (scene, tile, kept timepoint, channel) the Z stack is max-projected to
    a 2-D plane and stored as a member named
    ``<experiment>_<channel>_<well>_f<fov>_<timepoint>.tif`` inside that well's
    archive — the default filename schema, which is the layout the pipeline
    reads directly, so no ``well_plate_zipper`` pass is needed afterwards.
    A blank *experiment* takes the token from the source filename.

    Wells are converted in parallel across *workers* threads (0 = auto), each
    with its own read handle and its own disjoint set of wells, so no two
    threads touch one archive and none of them contend on a file position.

    *progress_fn* receives ``(planes_written, total_planes)``; *well_progress_fn*
    receives ``(well, planes_written_for_that_well, planes_per_well)``, once per
    plane and deliberately unthrottled — how often is often enough to repaint is
    a question about a UI, so the quantising belongs to the caller that owns one
    (``datasets.czi_import.apply``).

    *should_abort* is polled per plane and raises :class:`CziExplodeCancelled`;
    each in-flight well's staged archive is discarded, so no partial
    ``<WELL>.zip`` is ever published. Raises ``ValueError`` for an unsound
    source or a mapping that cannot be resolved — both before anything is
    written.
    """
    czi_path = Path(czi_path)
    out_in_dir = Path(out_in_dir)

    def _log(message: str) -> None:
        if log_fn:
            log_fn(message)

    def _abort() -> bool:
        return bool(should_abort and should_abort())

    czi_set = read_czi_set(czi_path, files=files)
    # Refuse an unsound source before a single archive is written. Everything
    # this catches would otherwise reach the dataset as images that look like
    # data: a well short of a timepoint, two channels under one filename, a
    # dimension whose planes overwrite each other, or — across a split-scene
    # set — two files that disagree about what a channel token means.
    if czi_set.problem:
        raise ValueError(czi_set.problem)
    layout = czi_set.layout

    well_map = resolve_well_map(
        czi_set, well_source=well_source, grid_rows=grid_rows,
        grid_cols=grid_cols, origin=origin_well, direction=direction,
        serpentine=serpentine, fovs_per_well=fovs_per_well,
        name=czi_path.name,
    )
    # Blank means the file names itself. A constant default ("czi") would
    # give every plate converted through this function the same experiment
    # token, so two of them concatenated later would be indistinguishable
    # — and the source filename is right there.
    # A folder names the plate; a file names itself. Taking the stem of a
    # split-scene member would put that one scene's well into every archive's
    # filenames — ``Exp7_A1_DAPI_H11_f0_t0.tif``.
    exp_tok = safe_token(experiment or _default_experiment(czi_path, czi_set),
                         "czi")
    # Index *and* token together: the index reads the plane and the token
    # names it, and a stride that filtered one list without the other would
    # write the right image under the wrong timepoint.
    # The set's tokens, not the layout's: elapsed hours where the files'
    # acquisition stamps supported them, positional otherwise. Resolved once
    # for the plate in ``czi_set``, so the page's readout and the archives
    # cannot be named two different ways.
    tp_plan = timepoint_plan(czi_set.timepoint_tokens, timepoint_stride)
    tp_tokens = [token for _index, token in tp_plan]
    n_kept_t = len(tp_plan)
    fovs = well_map.fovs_per_well
    # Per well, because a CZI plate names one scene per imaged position and an
    # acquisition need not visit every well the same number of times. ``fovs``
    # is the plate's nominal count — the largest — and everything that counts
    # planes counts them from here instead.
    field_counts = [len(block) for block in well_map.positions]
    n_wells = well_map.n_wells
    selected = wells_to_convert(well_map.wells, well_subset)
    if czi_set.timepoint_note:
        _log(f"[czi] {czi_set.timepoint_note}\n")

    out_in_dir.mkdir(parents=True, exist_ok=True)
    # Counted over the wells this run writes, not over the plate: a subset run
    # denominated by the whole plate reports a complete conversion as a
    # fraction of one.
    n_selected = len(selected)
    planes_in = {index: field_counts[index] * n_kept_t * layout.size_c
                 for index in selected}
    total = sum(planes_in.values())
    n_workers = pick_workers(workers, n_selected)
    source = "the file's scene names" if well_map.from_file else "the plate grid"
    held_in = (f"{czi_set.n_files} file(s)" if czi_set.n_files > 1
               else czi_path.name)
    _log(
        f"[czi] {held_in}: {czi_set.n_scenes} scene(s) -> "
        f"{subset_note(n_selected, n_wells)} x {fovs} FOV(s) x "
        f"{n_kept_t} timepoint(s)"
        f"{stride_note(timepoint_stride, layout.size_t, tp_tokens)}, "
        f"{layout.size_c} channel(s)"
        f"{f', {layout.size_z} Z-plane(s) max-projected' if layout.size_z > 1 else ''}"
        f" -- wells from {source}, {n_workers} worker(s).\n"
    )
    uneven = uneven_fields_note(well_map.wells, field_counts, fovs)
    if uneven:
        _log(f"[czi] {uneven}\n")

    # Wells a previous attempt already finished. Each archive is published
    # atomically, so one that exists holding every member these settings call
    # for is complete and correct. Off by default: leaving wells alone is only
    # right once the caller has established that this folder holds *this*
    # conversion's interrupted output.
    resumable: set[str] = set()
    if resume:
        resumable = {
            well_map.wells[index] for index in selected
            if well_is_complete(
                out_in_dir / f"{well_map.wells[index]}.zip",
                expected_members(exp_tok, layout.channel_names,
                                 well_map.wells[index], field_counts[index],
                                 tp_tokens))
        }
        if resumable:
            _log(f"[czi] Resuming: {len(resumable)} of {n_selected} well(s) are "
                 f"already converted and will be left alone.\n")

    lock = threading.Lock()
    done = 0

    def _bump(well: "str | None" = None, well_done: int = 0,
              well_total: int = 0) -> None:
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
            well_progress_fn(well, well_done, well_total)

    if progress_fn:
        progress_fn(0, total)

    def _convert_wells(well_indices: "list[int]"):
        written = 0
        converted: list[str] = []
        resumed: list[str] = []
        missing: dict[str, int] = {}
        read_s = write_s = 0.0
        # One reader per file this thread actually touches, opened on first
        # use and closed together at the end. A split-scene plate has one file
        # per well, so opening every file per thread would be N handles for
        # one well's worth of reading; a single-file plate opens exactly one,
        # as it always did.
        with _ReaderPool(dict(zip(czi_set.files, czi_set.layouts))) as readers:
            for index in well_indices:
                if _abort():
                    raise CziExplodeCancelled()
                label = well_map.wells[index]
                planes_per_well = max(1, planes_in[index])
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
                        reader = readers.for_path(position.path)
                        for t_index, tp_token in tp_plan:
                            for channel in range(layout.size_c):
                                if _abort():
                                    raise CziExplodeCancelled()
                                started = time.perf_counter()
                                try:
                                    plane = reader.read_projection(
                                        position.scene, position.tile,
                                        t_index, channel)
                                except CziReadError:
                                    # A coordinate the file holds no data for.
                                    # Skipped, never substituted: a zero-filled
                                    # image is indistinguishable from a real
                                    # dark field, which is the silent
                                    # substitution CLAUDE.md §7 is about. The
                                    # well still converts, and comes back
                                    # counted so that nothing reports it as
                                    # whole.
                                    read_s += time.perf_counter() - started
                                    missing[label] = missing.get(label, 0) + 1
                                    _log(
                                        f"  {label}: no image data for field "
                                        f"{fov}, {tp_token}, "
                                        f"{layout.channel_names[channel]} — "
                                        f"skipped")
                                    well_written += 1
                                    _bump(label, well_written,
                                          planes_per_well)
                                    continue
                                read_s += time.perf_counter() - started
                                started = time.perf_counter()
                                member = (
                                    f"{exp_tok}_{layout.channel_names[channel]}_"
                                    f"{label}_f{fov}_{tp_token}.tif")
                                archive.writestr(member, encode_tiff(plane))
                                write_s += time.perf_counter() - started
                                written += 1
                                well_written += 1
                                _bump(label, well_written, planes_per_well)
                converted.append(label)
        return written, converted, read_s, write_s, resumed, missing

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
                   for group in worker_groups(selected, n_workers)]
        for future in as_completed(futures):
            (count, wells, read_time, write_time, resumed,
             short) = future.result()
            result.images_missing.update(short)
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
        f"[czi] Wrote {result.images_written} image(s) for "
        f"{len(result.wells)} well(s){kept} into {out_in_dir.name}/ in "
        f"{wall:.1f}s ({rate:.0f} planes/s; read {read_total:.1f}s, write "
        f"{write_total:.1f}s summed across {n_workers} worker(s)).\n"
    )
    if result.images_missing:
        # Last, and as its own line, because it is the one thing in this log a
        # reader must not scroll past: these wells are in the dataset and are
        # not whole. A closing line that reported only the total written would
        # read exactly like a complete plate (CLAUDE.md §7).
        _log(shortfall_summary(result.images_missing))
    return result
