"""De-interleave a single packed OME-TIFF into loose per-plane 2-D TIFs.

Some acquisition software emits one OME-TIFF holding every timepoint, channel,
Z-plane and stage position (well × field-of-view) of a plate scan, with the
dimension order described in the embedded OME-XML metadata. All-Well's pipeline,
by contrast, is filename-token driven: it wants one 2-D TIFF per
(channel, well, FOV, timepoint), named to the filename schema, which
``WellPlateZipper`` then groups per well.

This module bridges the two by *exploding* the packed file into exactly those
loose TIFs. It is a client-side pre-pass that runs before the normal
``resolve_input_output`` grouping, so nothing downstream — the pipeline, the
``pipeline_info.json`` sidecar, the remote runner, the Review viewer — needs any
OME awareness.

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

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import tifffile

from pipeline.io import _safe_imwrite
from util.plate_grid import position_to_well_fov, serpentine_well_labels

# How a single-image file's flat T-frame sequence splits into (position, timepoint):
#   "single"      — every frame is a distinct position; one timepoint.
#   "pos_in_time" — time on the outside: frame = t*n_positions + p.
#   "time_in_pos" — position on the outside: frame = p*n_timepoints + t.
FRAME_ORDERS = ("single", "pos_in_time", "time_in_pos")


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


def explode_ome_tiff(
    ome_path: Path | str,
    out_in_dir: Path | str,
    *,
    grid_rows: int,
    grid_cols: int,
    origin_well: str = "A01",
    direction: str = "row",
    experiment: str = "ome",
    fovs_per_well: int | None = None,
    frame_order: str = "single",
    log_fn: Callable[[str], None] | None = None,
) -> ExplodeResult:
    """Explode a packed OME-TIFF into loose per-plane 2-D TIFs under *out_in_dir*.

    For every (position, timepoint, channel) the Z stack is max-projected to a
    2-D plane and written as
    ``<experiment>_<channel>_<well>_f<fov>_<timepoint>.tif`` (the default
    filename schema). Each stage position is mapped to a 96-well label via the
    serpentine order and the *grid_rows* × *grid_cols* imaged block; each
    position is one well × FOV.

    Two source structures are handled (see :class:`OmeLayout`). A **multi-image**
    file has one series per position. A **single-image** file packs every
    position into one series' ``T`` axis; *frame_order* then says how that flat
    frame sequence splits into positions × timepoints (see :data:`FRAME_ORDERS`)
    and planes are read one page at a time (the whole array can be far too large
    to load).

    *fovs_per_well* is the fields-of-view per well; ``None`` derives it. Raises
    ``ValueError`` on any inconsistency between the position/frame count and the
    grid, an unknown *frame_order*, or a grid that overflows the 96-well plate.
    """
    ome_path = Path(ome_path)
    out_in_dir = Path(out_in_dir)

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    if frame_order not in FRAME_ORDERS:
        raise ValueError(
            f"Unknown frame order {frame_order!r}; expected one of {FRAME_ORDERS}."
        )

    layout = read_ome_layout(ome_path)
    n_wells = grid_rows * grid_cols
    exp_tok = _safe_token(experiment, "ome")
    # Raises for an out-of-range grid before any file is written.
    well_labels = serpentine_well_labels(
        grid_rows, grid_cols, origin=origin_well, direction=direction
    )
    out_in_dir.mkdir(parents=True, exist_ok=True)

    common = dict(
        out_in_dir=out_in_dir, well_labels=well_labels, exp_tok=exp_tok,
        n_wells=n_wells, fovs_per_well=fovs_per_well, log=_log, name=ome_path.name,
    )
    if layout.single_image:
        return _explode_single_image(
            ome_path, layout, frame_order=frame_order, **common
        )
    return _explode_multi_image(ome_path, layout, **common)


def _explode_multi_image(
    ome_path, layout, *, out_in_dir, well_labels, exp_tok, n_wells,
    fovs_per_well, log, name,
) -> ExplodeResult:
    """One OME ``<Image>`` (series) per stage position; timepoints are its T axis."""
    fovs_per_well = _resolve_positions(
        layout.n_positions, n_wells, fovs_per_well,
        what="stage position(s)", name=name,
    )
    log(
        f"[ome] {name}: {layout.n_positions} position(s) → {n_wells} well(s) × "
        f"{fovs_per_well} FOV(s), {layout.size_c} channel(s), "
        f"{layout.size_t} timepoint(s)"
        f"{f', {layout.size_z} Z-plane(s) max-projected' if layout.size_z > 1 else ''}.\n"
    )
    result = ExplodeResult(
        channels=list(layout.channel_names),
        timepoints=list(layout.timepoint_tokens),
        fovs_per_well=fovs_per_well,
    )
    seen_wells: list[str] = []
    with tifffile.TiffFile(str(ome_path)) as tf:
        for pos, series in enumerate(tf.series):
            well, fov = position_to_well_fov(
                pos, well_labels=well_labels, fovs_per_well=fovs_per_well
            )
            if well not in seen_wells:
                seen_wells.append(well)
            arr = series.asarray()
            axes = str(series.axes).upper()
            for ti in range(layout.size_t):
                tp_tok = layout.timepoint_tokens[ti]
                for ci in range(layout.size_c):
                    plane = _extract_plane(arr, axes, ti, ci)
                    _write_plane(out_in_dir, exp_tok, layout.channel_names[ci],
                                 well, fov, tp_tok, plane)
                    result.images_written += 1
    result.wells = sorted(set(seen_wells))
    return _finish(result, out_in_dir, log)


def _explode_single_image(
    ome_path, layout, *, frame_order, out_in_dir, well_labels, exp_tok, n_wells,
    fovs_per_well, log, name,
) -> ExplodeResult:
    """One ``<Image>`` whose T axis packs every position (× any timepoints).

    ``n_positions = n_wells × fovs_per_well``; ``n_timepoints`` is derived as
    ``n_frames / n_positions``. Planes are read one page at a time — a packed
    plate file's full array is routinely tens to hundreds of GB.
    """
    n_frames = layout.n_frames
    if frame_order == "single":
        # Every frame is a distinct position; one timepoint.
        fovs_per_well = _resolve_positions(
            n_frames, n_wells, fovs_per_well, what="frame(s)", name=name,
        )
        n_positions, n_timepoints = n_frames, 1
    else:
        if fovs_per_well is None:
            raise ValueError(
                f"{name} packs {n_frames} frames into one image with timepoints, "
                f"so FOVs per well is needed to separate positions from "
                f"timepoints (positions = wells × FOVs; timepoints = frames / "
                f"positions)."
            )
        n_positions = n_wells * fovs_per_well
        n_timepoints, rem = divmod(n_frames, n_positions)
        if rem or n_timepoints < 1:
            raise ValueError(
                f"{name} has {n_frames} frame(s), which does not split evenly "
                f"into {n_wells} well(s) × {fovs_per_well} FOV(s) = {n_positions} "
                f"position(s) × a whole number of timepoints. Check the grid and "
                f"FOVs-per-well."
            )

    log(
        f"[ome] {name}: single image, {n_frames} frame(s) ({frame_order}) → "
        f"{n_wells} well(s) × {fovs_per_well} FOV(s) × {n_timepoints} "
        f"timepoint(s), {layout.size_c} channel(s)"
        f"{f', {layout.size_z} Z-plane(s) max-projected' if layout.size_z > 1 else ''}.\n"
    )
    result = ExplodeResult(
        channels=list(layout.channel_names),
        timepoints=[f"t{t}" for t in range(n_timepoints)],
        fovs_per_well=fovs_per_well,
    )
    seen_wells: list[str] = []
    with tifffile.TiffFile(str(ome_path)) as tf:
        series = tf.series[0]
        axes = str(series.axes).upper()
        for frame in range(n_frames):
            if frame_order == "single":
                pos, tp = frame, 0
            elif frame_order == "pos_in_time":
                pos, tp = frame % n_positions, frame // n_positions
            else:  # time_in_pos
                pos, tp = frame // n_timepoints, frame % n_timepoints
            well, fov = position_to_well_fov(
                pos, well_labels=well_labels, fovs_per_well=fovs_per_well
            )
            if well not in seen_wells:
                seen_wells.append(well)
            for ci in range(layout.size_c):
                plane = _packed_plane(series, axes, frame, ci)
                _write_plane(out_in_dir, exp_tok, layout.channel_names[ci],
                             well, fov, f"t{tp}", plane)
                result.images_written += 1
    result.wells = sorted(set(seen_wells))
    return _finish(result, out_in_dir, log)


def _write_plane(out_in_dir, exp_tok, ch_tok, well, fov, tp_tok, plane) -> None:
    name = f"{exp_tok}_{ch_tok}_{well}_f{fov}_{tp_tok}.tif"
    _safe_imwrite(out_in_dir / name, plane)


def _finish(result, out_in_dir, log) -> ExplodeResult:
    log(
        f"[ome] Wrote {result.images_written} loose TIF(s) for "
        f"{len(result.wells)} well(s) into {Path(out_in_dir).name}/.\n"
    )
    return result
