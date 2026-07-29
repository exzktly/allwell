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


@dataclass
class OmeLayout:
    """The dimensions and metadata labels read from a packed OME-TIFF."""

    n_positions: int
    size_t: int
    size_c: int
    size_z: int
    channel_names: list[str]      # length == size_c
    timepoint_tokens: list[str]   # length == size_t
    axes_per_series: list[str]    # tifffile axes string per position


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

    One ``tifffile`` series == one OME ``Image`` == one stage position. Sizes are
    taken from the first series (a plate scan is dimensionally uniform across
    positions).
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

    channel_names, timepoint_tokens = _parse_ome_labels(ome_xml, size_c, size_t)
    return OmeLayout(
        n_positions=len(series),
        size_t=size_t,
        size_c=size_c,
        size_z=size_z,
        channel_names=channel_names,
        timepoint_tokens=timepoint_tokens,
        axes_per_series=axes_per_series,
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


def explode_ome_tiff(
    ome_path: Path | str,
    out_in_dir: Path | str,
    *,
    grid_rows: int,
    grid_cols: int,
    origin_well: str = "A01",
    direction: str = "row",
    experiment: str = "ome",
    log_fn: Callable[[str], None] | None = None,
) -> ExplodeResult:
    """Explode a packed OME-TIFF into loose per-plane 2-D TIFs under *out_in_dir*.

    For every (position, timepoint, channel) the Z stack is max-projected to a
    2-D plane and written as
    ``<experiment>_<channel>_<well>_f<fov>_<timepoint>.tif`` (the default
    filename schema). The position axis is mapped to a 96-well label via the
    serpentine order and the *grid_rows* × *grid_cols* imaged block; each
    position is one well × FOV.

    Raises ``ValueError`` if the number of positions is not an exact multiple of
    ``grid_rows * grid_cols`` (so FOVs-per-well is not integral), or if the grid
    overflows the 96-well plate from *origin_well*.
    """
    ome_path = Path(ome_path)
    out_in_dir = Path(out_in_dir)

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    layout = read_ome_layout(ome_path)
    n_wells = grid_rows * grid_cols
    if layout.n_positions % n_wells != 0:
        raise ValueError(
            f"{ome_path.name} has {layout.n_positions} stage position(s), which "
            f"is not divisible by the {grid_rows}×{grid_cols} = {n_wells} well "
            f"grid. Fields-of-view per well must be a whole number — check the "
            f"grid dimensions."
        )
    fovs_per_well = layout.n_positions // n_wells

    # Raises for an out-of-range grid before any file is written.
    well_labels = serpentine_well_labels(
        grid_rows, grid_cols, origin=origin_well, direction=direction
    )
    exp_tok = _safe_token(experiment, "ome")

    out_in_dir.mkdir(parents=True, exist_ok=True)
    _log(
        f"[ome] {ome_path.name}: {layout.n_positions} position(s) → "
        f"{n_wells} well(s) × {fovs_per_well} FOV(s), "
        f"{layout.size_c} channel(s), {layout.size_t} timepoint(s)"
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
                    ch_tok = layout.channel_names[ci]
                    plane = _extract_plane(arr, axes, ti, ci)
                    name = f"{exp_tok}_{ch_tok}_{well}_f{fov}_{tp_tok}.tif"
                    _safe_imwrite(out_in_dir / name, plane)
                    result.images_written += 1

    result.wells = sorted(set(seen_wells))
    _log(
        f"[ome] Wrote {result.images_written} loose TIF(s) for "
        f"{len(result.wells)} well(s) into {out_in_dir.name}/.\n"
    )
    return result
