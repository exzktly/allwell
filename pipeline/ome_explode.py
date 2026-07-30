"""De-interleave a single packed OME-TIFF into per-well ``.zip`` archives.

Some acquisition software emits one OME-TIFF holding every timepoint, channel,
Z-plane and stage position (well × field-of-view) of a plate scan, with the
dimension order described in the embedded OME-XML metadata. All-Well's pipeline,
by contrast, is filename-token driven: it wants one 2-D TIFF per
(channel, well, FOV, timepoint), named to the filename schema, grouped per well.

This module bridges the two by *exploding* the packed file directly into the
per-well ``<WELL>.zip`` archives the pipeline reads — the same layout
``resolve_input_output`` accepts as ready input (no ``WellPlateZipper`` pass).
Wells are converted in parallel across a thread pool, each written as one atomic
archive (``util.zip_write.atomic_zip``), which also collapses the thousands of
per-file fsyncs a loose-TIF explode would cost into two per well.

Two axes get special handling:

* **Z** is collapsed by **maximum-intensity projection** per (position, T, C) —
  the standard reduction for fluorescence quantification.
* the **stage-position** axis (one OME ``Image`` / ``tifffile`` series per
  position) is mapped to a 96-well label via :mod:`util.plate_grid`, using the
  serpentine acquisition order and the imaged well-grid dimensions the user
  supplies. Each position is one well × FOV; FOVs-per-well is derived as
  ``n_positions / (grid_rows * grid_cols)``.

Channel names and timepoint labels come from the OME-XML when present, falling
back to positional tokens (``c0``, ``t0``) otherwise.

Depends only on numpy + tifffile (like :mod:`pipeline.io`) plus the stdlib and
the ``util`` helpers — no ``well_viewer`` / ``widgets`` / Qt imports, so it stays
inside the pipeline self-sufficiency contract (ARCHITECTURE §6.2).
"""

from __future__ import annotations

import io
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import tifffile

from util.plate_grid import serpentine_well_labels
from util.zip_write import atomic_zip

# How a single-image file's flat T-frame sequence splits into (position, timepoint):
#   "single"      — every frame is a distinct position; one timepoint.
#   "pos_in_time" — time on the outside: frame = t*n_positions + p.
#   "time_in_pos" — position on the outside: frame = p*n_timepoints + t.
FRAME_ORDERS = ("single", "pos_in_time", "time_in_pos")


class OmeExplodeCancelled(Exception):
    """Raised when a ``should_abort`` callback stops an explode mid-run."""


@dataclass
class OmeLayout:
    """The dimensions and metadata labels read from a packed OME-TIFF.

    Two structures are represented:

    * **Multi-image** — the plate scan is stored as several OME ``<Image>``
      elements (one ``tifffile`` series each), one per stage position. Then
      ``single_image`` is False, ``n_positions`` is the series count, and
      ``size_t`` is the timepoints *per* position.
    * **Single-image packed** — one ``<Image>`` whose ``T`` axis packs every
      stage position (and any real timepoints) into one flat frame sequence,
      because OME allows only X/Y/Z/C/T in an Image (no position dimension).
      Then ``single_image`` is True and ``n_frames`` is that ``SizeT`` frame
      count; how it splits into positions × timepoints is decided at explode time
      from the user's grid and frame-order.
    """

    n_positions: int
    size_t: int
    size_c: int
    size_z: int
    channel_names: list[str]      # length == size_c
    timepoint_tokens: list[str]   # length == size_t (multi-image only)
    axes_per_series: list[str]    # tifffile axes string per series
    single_image: bool = False
    n_frames: int = 0             # single-image: the T-axis frame count


@dataclass
class ExplodeResult:
    """A summary of an explode run, for logging and a pre-run preview."""

    wells: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    timepoints: list[str] = field(default_factory=list)
    fovs_per_well: int = 0
    images_written: int = 0


def _safe_token(value: object, fallback: str) -> str:
    """Filename-safe token: keep alphanumerics only, else *fallback*.

    The exploded filenames are split on the schema separator (``_``) downstream,
    so a channel name like ``"DAPI 405"`` or ``"GFP_2"`` must not carry spaces or
    separators into a token slot. Strips everything but ``[0-9A-Za-z]``.
    """
    tok = re.sub(r"[^0-9A-Za-z]+", "", str(value if value is not None else ""))
    return tok or fallback


def _localname(tag: str) -> str:
    """Strip an XML namespace (``{ns}Local`` → ``Local``)."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_ome_labels(
    ome_xml: str | None, size_c: int, size_t: int
) -> tuple[list[str], list[str]]:
    """Return ``(channel_names, timepoint_tokens)`` parsed from OME-XML.

    Channel names come from the first ``Image``'s ``Channel`` elements (channels
    are the same across positions in a plate scan). Timepoint tokens come from
    the per-plane ``DeltaT`` when present — rendered as ``"<hours>h"`` so the
    app's timepoint parser reads them chronologically — otherwise positional
    ``t0``, ``t1``, …. Any parse failure falls back cleanly to positional tokens
    for both, so a malformed or absent metadata block never aborts the explode.
    """
    channel_names = [f"c{i}" for i in range(size_c)]
    timepoint_tokens = [f"t{i}" for i in range(size_t)]
    if not ome_xml:
        return channel_names, timepoint_tokens

    try:
        root = ET.fromstring(ome_xml)
    except ET.ParseError:
        return channel_names, timepoint_tokens

    image = next((el for el in root.iter() if _localname(el.tag) == "Image"), None)
    if image is None:
        return channel_names, timepoint_tokens

    pixels = next(
        (el for el in image.iter() if _localname(el.tag) == "Pixels"), None
    )
    if pixels is None:
        return channel_names, timepoint_tokens

    # Channel names, in channel order.
    names: list[str] = []
    for el in pixels:
        if _localname(el.tag) == "Channel":
            names.append(el.get("Name") or "")
    if names:
        channel_names = [
            _safe_token(names[i] if i < len(names) else "", f"c{i}")
            for i in range(size_c)
        ]

    # Timepoints from DeltaT (seconds by default) on the T-index planes.
    delta_by_t: dict[int, float] = {}
    for el in pixels:
        if _localname(el.tag) != "Plane":
            continue
        the_t = el.get("TheT")
        delta = el.get("DeltaT")
        if the_t is None or delta is None:
            continue
        try:
            ti = int(the_t)
            secs = float(delta)
        except ValueError:
            continue
        unit = (el.get("DeltaTUnit") or "s").lower()
        hours = secs / 60.0 if unit in ("min", "minute", "m") else secs / 3600.0
        delta_by_t.setdefault(ti, hours)
    if delta_by_t and len(delta_by_t) >= size_t:
        timepoint_tokens = [
            f"{delta_by_t[i]:g}h" if i in delta_by_t else f"t{i}"
            for i in range(size_t)
        ]

    return channel_names, timepoint_tokens


def read_ome_layout(ome_path: Path | str) -> OmeLayout:
    """Open *ome_path* and read its position/T/C/Z sizes and metadata labels.

    A multi-image file (several OME ``<Image>`` elements → several ``tifffile``
    series) has one series per stage position, and ``size_t`` is the timepoints
    per position. A single-image file has one series whose ``T`` axis packs every
    stage position into a flat frame sequence — ``single_image`` is True and
    ``n_frames`` is that ``SizeT`` (the split into positions × timepoints is
    decided at explode time). Sizes are taken from the first series (a plate scan
    is dimensionally uniform).
    """
    with tifffile.TiffFile(str(ome_path)) as tf:
        series = list(tf.series)
        if not series:
            raise ValueError(f"{Path(ome_path).name} contains no image series.")
        axes_per_series = [str(s.axes).upper() for s in series]
        axes0 = axes_per_series[0]
        shape0 = series[0].shape

        def _size(ax: str) -> int:
            return int(shape0[axes0.index(ax)]) if ax in axes0 else 1

        size_t, size_c, size_z = _size("T"), _size("C"), _size("Z")
        ome_xml = tf.ome_metadata

    single_image = len(series) == 1
    # For a single-image file the T axis is the frame sequence, not timepoints, so
    # don't manufacture size_t timepoint tokens (there could be thousands).
    channel_names, timepoint_tokens = _parse_ome_labels(
        ome_xml, size_c, 0 if single_image else size_t
    )
    return OmeLayout(
        n_positions=len(series),
        size_t=0 if single_image else size_t,
        size_c=size_c,
        size_z=size_z,
        channel_names=channel_names,
        timepoint_tokens=timepoint_tokens,
        axes_per_series=axes_per_series,
        single_image=single_image,
        n_frames=size_t if single_image else 0,
    )


def _extract_plane(arr: np.ndarray, axes: str, t: int, c: int) -> np.ndarray:
    """Return the 2-D (Y, X) plane at timepoint *t*, channel *c*, Z max-projected.

    Indexes the T and C axes (when present), keeps Y/X/Z, and takes index 0 of
    any other axis (e.g. a sample axis). Z is collapsed by maximum projection;
    any residual leading axes are defensively reduced to leave a 2-D result.
    """
    axes = axes.upper()
    sl: list = [slice(None)] * arr.ndim
    for pos, ax in enumerate(axes):
        if ax == "T":
            sl[pos] = min(t, arr.shape[pos] - 1)
        elif ax == "C":
            sl[pos] = min(c, arr.shape[pos] - 1)
        elif ax in ("Z", "Y", "X"):
            continue
        else:
            sl[pos] = 0
    sub = arr[tuple(sl)]

    remaining = [ax for pos, ax in enumerate(axes) if isinstance(sl[pos], slice)]
    if "Z" in remaining:
        sub = np.max(sub, axis=remaining.index("Z"))
        remaining.pop(remaining.index("Z"))
    sub = np.asarray(sub)
    while sub.ndim > 2:
        sub = sub[0]
    return sub


def _packed_plane(series, axes: str, frame: int, c: int) -> np.ndarray:
    """Read one 2-D plane (T=*frame*, C=*c*, Z max-projected) from a series' pages.

    A single-image packed file can be far too large to ``asarray`` whole, so the
    plane is read a page at a time. Pages are ordered by the series' leading axes
    (everything but Y/X), first axis slowest — so the flat page index is the
    mixed-radix ravel of the (T, C, Z) indices against those axis sizes, honouring
    whatever order the file's ``DimensionOrder`` produced.
    """
    axes = axes.upper()
    leading = [a for a in axes if a not in "YX"]
    sizes = {a: int(series.shape[axes.index(a)]) for a in leading}

    def _page_index(idx: dict[str, int]) -> int:
        pi = 0
        for a in leading:
            pi = pi * sizes[a] + idx.get(a, 0)
        return pi

    nz = sizes.get("Z", 1)
    if "Z" in leading and nz > 1:
        stack = [
            np.asarray(series.pages[_page_index({"T": frame, "C": c, "Z": z})].asarray())
            for z in range(nz)
        ]
        return np.max(np.stack(stack), axis=0)
    plane = series.pages[_page_index({"T": frame, "C": c, "Z": 0})].asarray()
    plane = np.asarray(plane)
    while plane.ndim > 2:
        plane = plane[0]
    return plane


def _resolve_positions(
    total: int, n_wells: int, fovs_per_well: int | None, *, what: str, name: str,
) -> int:
    """Return the validated FOVs-per-well for *total* positions/frames.

    ``None`` derives it (``total`` must be an exact multiple of *n_wells*); a
    given value is cross-checked against ``n_wells * fovs_per_well == total``.
    """
    if fovs_per_well is None:
        if total % n_wells != 0:
            raise ValueError(
                f"{name} has {total} {what}, which is not divisible by the "
                f"well grid ({n_wells} well(s)). Fields-of-view per well must be "
                f"a whole number — check the grid dimensions."
            )
        return total // n_wells
    if n_wells * fovs_per_well != total:
        raise ValueError(
            f"{name} has {total} {what}, but the {n_wells} well(s) × "
            f"{fovs_per_well} FOV(s) per well = {n_wells * fovs_per_well}. "
            f"Check the grid dimensions and the FOVs-per-well value."
        )
    return fovs_per_well


def _pick_workers(workers: int, n_wells: int) -> int:
    """Resolve the worker-thread count (0 = auto), capped at the well count."""
    if n_wells <= 0:
        return 1
    if workers and workers > 0:
        return max(1, min(workers, n_wells))
    return max(1, min(8, os.cpu_count() or 4, n_wells))


def _resolve_single_image(
    layout, n_wells: int, fovs_per_well: "int | None", frame_order: str, name: str,
) -> "tuple[int, int, int]":
    """Return ``(n_positions, n_timepoints, fovs_per_well)`` for a packed file."""
    n_frames = layout.n_frames
    if frame_order == "single":
        fovs = _resolve_positions(n_frames, n_wells, fovs_per_well,
                                  what="frame(s)", name=name)
        return n_frames, 1, fovs
    if fovs_per_well is None:
        raise ValueError(
            f"{name} packs {n_frames} frames into one image with timepoints, so "
            f"FOVs per well is needed to separate positions from timepoints "
            f"(positions = wells x FOVs; timepoints = frames / positions)."
        )
    n_positions = n_wells * fovs_per_well
    n_timepoints, rem = divmod(n_frames, n_positions)
    if rem or n_timepoints < 1:
        raise ValueError(
            f"{name} has {n_frames} frame(s), which does not split evenly into "
            f"{n_wells} well(s) x {fovs_per_well} FOV(s) = {n_positions} "
            f"position(s) x a whole number of timepoints. Check the grid and "
            f"FOVs-per-well."
        )
    return n_positions, n_timepoints, fovs_per_well


def _frame_index(pos: int, t: int, n_positions: int, n_timepoints: int,
                 frame_order: str) -> int:
    """Map a (position, timepoint) to the packed T-axis frame index."""
    if frame_order == "pos_in_time":
        return t * n_positions + pos
    if frame_order == "time_in_pos":
        return pos * n_timepoints + t
    return pos  # "single" -- one timepoint, frame == position


def _encode_tiff(plane: np.ndarray) -> bytes:
    """Encode a 2-D plane as a single-page TIFF in memory (no temp file)."""
    buf = io.BytesIO()
    tifffile.imwrite(buf, plane)
    return buf.getvalue()


def explode_ome_tiff(
    ome_path: "Path | str",
    out_in_dir: "Path | str",
    *,
    grid_rows: int,
    grid_cols: int,
    origin_well: str = "A01",
    direction: str = "row",
    experiment: str = "ome",
    fovs_per_well: "int | None" = None,
    frame_order: str = "single",
    workers: int = 0,
    log_fn: "Callable[[str], None] | None" = None,
    progress_fn: "Callable[[int, int], None] | None" = None,
    should_abort: "Callable[[], bool] | None" = None,
) -> ExplodeResult:
    """Explode a packed OME-TIFF into per-well ``<WELL>.zip`` archives in *out_in_dir*.

    For every (position, timepoint, channel) the Z stack is max-projected to a
    2-D plane and stored as a member named
    ``<experiment>_<channel>_<well>_f<fov>_<timepoint>.tif`` inside that well's
    archive (the default filename schema -- the layout the pipeline reads
    directly, so no ``WellPlateZipper`` pass is needed). Each stage position maps
    to a 96-well label via the serpentine order and the *grid_rows* x *grid_cols*
    imaged block; each position is one well x FOV.

    Two source structures are handled (see :class:`OmeLayout`). A **multi-image**
    file has one series per position. A **single-image** file packs every position
    into one series' ``T`` axis; *frame_order* says how that flat frame sequence
    splits into positions x timepoints (see :data:`FRAME_ORDERS`), and planes are
    read one page at a time (the whole array can be far too large to load).

    Wells are converted in parallel: *workers* threads (0 = auto), each with its
    own read handle, each owning a disjoint set of wells and writing that well's
    archive alone (no locking on the zip handles). *fovs_per_well* is the
    fields-of-view per well; ``None`` derives it.

    *progress_fn* receives ``(planes_written, total_planes)`` as it proceeds.
    *should_abort*, polled per plane, raises :class:`OmeExplodeCancelled` when it
    returns True -- each in-flight well's staged archive is discarded, so no
    partial ``<WELL>.zip`` is left (a re-run overwrites cleanly). Raises
    ``ValueError`` on any grid / FOVs / frame-order inconsistency, or a grid that
    overflows the plate.
    """
    ome_path = Path(ome_path)
    out_in_dir = Path(out_in_dir)

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    def _abort() -> bool:
        return bool(should_abort and should_abort())

    if frame_order not in FRAME_ORDERS:
        raise ValueError(
            f"Unknown frame order {frame_order!r}; expected one of {FRAME_ORDERS}."
        )

    layout = read_ome_layout(ome_path)
    n_wells = grid_rows * grid_cols
    exp_tok = _safe_token(experiment, "ome")
    # Raises for an out-of-range grid before any archive is written.
    well_labels = serpentine_well_labels(
        grid_rows, grid_cols, origin=origin_well, direction=direction
    )

    if layout.single_image:
        n_positions, n_timepoints, fovs = _resolve_single_image(
            layout, n_wells, fovs_per_well, frame_order, ome_path.name)
        tp_tokens = [f"t{t}" for t in range(n_timepoints)]
        head = f"single image, {layout.n_frames} frame(s) ({frame_order})"
    else:
        fovs = _resolve_positions(layout.n_positions, n_wells, fovs_per_well,
                                  what="stage position(s)", name=ome_path.name)
        n_positions, n_timepoints = layout.n_positions, layout.size_t
        tp_tokens = list(layout.timepoint_tokens)
        head = f"{layout.n_positions} position(s)"

    out_in_dir.mkdir(parents=True, exist_ok=True)
    total = n_positions * n_timepoints * layout.size_c
    n_workers = _pick_workers(workers, n_wells)
    _log(
        f"[ome] {ome_path.name}: {head} -> {n_wells} well(s) x {fovs} FOV(s) x "
        f"{n_timepoints} timepoint(s), {layout.size_c} channel(s)"
        f"{f', {layout.size_z} Z-plane(s) max-projected' if layout.size_z > 1 else ''}"
        f" -- {n_workers} worker(s).\n"
    )

    lock = threading.Lock()
    done = 0

    def _bump() -> None:
        nonlocal done
        with lock:
            done += 1
            d = done
        if progress_fn:
            progress_fn(d, total)

    if progress_fn:
        progress_fn(0, total)

    def _convert_wells(well_indices: "list[int]"):
        written = 0
        wells: list[str] = []
        read_s = write_s = 0.0
        with tifffile.TiffFile(str(ome_path)) as tf:
            series0 = tf.series[0]
            axes0 = str(series0.axes).upper()
            for i in well_indices:
                if _abort():
                    raise OmeExplodeCancelled()
                label = well_labels[i]
                base = i * fovs
                with atomic_zip(out_in_dir / f"{label}.zip",
                                compression=zipfile.ZIP_STORED) as zf:
                    for local_fov in range(fovs):
                        pos = base + local_fov
                        arr = axes = None
                        if not layout.single_image:
                            r0 = time.perf_counter()
                            arr = tf.series[pos].asarray()
                            axes = str(tf.series[pos].axes).upper()
                            read_s += time.perf_counter() - r0
                        for ti in range(n_timepoints):
                            for c in range(layout.size_c):
                                if _abort():
                                    raise OmeExplodeCancelled()
                                r0 = time.perf_counter()
                                if layout.single_image:
                                    frame = _frame_index(pos, ti, n_positions,
                                                         n_timepoints, frame_order)
                                    plane = _packed_plane(series0, axes0, frame, c)
                                else:
                                    plane = _extract_plane(arr, axes, ti, c)
                                read_s += time.perf_counter() - r0
                                w0 = time.perf_counter()
                                member = (f"{exp_tok}_{layout.channel_names[c]}_"
                                          f"{label}_f{local_fov}_{tp_tokens[ti]}.tif")
                                zf.writestr(member, _encode_tiff(plane))
                                write_s += time.perf_counter() - w0
                                written += 1
                                _bump()
                wells.append(label)
        return written, wells, read_s, write_s

    groups = [g for g in (list(range(i, n_wells, n_workers))
                          for i in range(n_workers)) if g]
    result = ExplodeResult(
        channels=list(layout.channel_names),
        timepoints=tp_tokens,
        fovs_per_well=fovs,
    )
    all_wells: list[str] = []
    read_total = write_total = 0.0
    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futures = [ex.submit(_convert_wells, g) for g in groups]
        for fut in as_completed(futures):
            w, wells, rs, ws = fut.result()
            result.images_written += w
            all_wells.extend(wells)
            read_total += rs
            write_total += ws
    result.wells = sorted(set(all_wells))

    wall = time.perf_counter() - t_start
    rate = result.images_written / wall if wall > 0 else 0.0
    _log(
        f"[ome] Wrote {result.images_written} image(s) for "
        f"{len(result.wells)} well(s) into {out_in_dir.name}/ in {wall:.1f}s "
        f"({rate:.0f} planes/s; read {read_total:.1f}s, write {write_total:.1f}s "
        f"summed across {n_workers} worker(s)).\n"
    )
    return result
