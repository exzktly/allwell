"""The Image Table's nanowell frames: the registry service ``image_table.nanowell_frame``.

The Image Table lays images out side by side — any wells, timepoints and channels on its axes, one crop, one zoom,
one display window per row — but a nanowell well is one array, not a set of fields, and the only picture the run
keeps of it is the phase montage. This module draws the other two things a comparison across wells wants, as frames
the Image Table can lay out like any other channel (``review/data/nanowell_images`` names the tokens):

* **A fluorescence montage** (``MONTAGE <CH>``): the run keeps none, so it is stitched here from that channel's raw
  tiles, each placed where the run's registration put it (``full_montage_compute``, the Array view's own reader),
  in the tiles' own units — the row's LUT, not a stretch, sets how bright it looks, so two wells share one window.
* **A metric map** (``NW: <metric>``): each nanowell's disc, at its measured place and radius, filled with its value
  of the metric — the Array view's map, as an image. The values are the plot tools' own rows
  (``observation_compute.nanowell_observations``): the page's Baseline, rim-focus rule and Cell Gating, so a value on
  the map and the same value on a plot are one number.

**One geometry.** Every frame of a (well, timepoint) — the stored phase montage, any channel's montage and any
metric map — is drawn on the grid record's ``montage_origin_um`` / ``montage_um_per_px`` over the stored montage's
size. So one crop is one region of the well in every row, and a metric map sits pixel for pixel over the montage
beneath it. The frame carries that scale, which is what a scale bar on it must use; the tiles' pixel size would
draw a bar several times too short.

**Nothing drawn as something else.** Outside every tile and every nanowell a frame is NaN, which the Image Table
draws as background. A nanowell with no value of the metric (empty, occluded, out of focus, gated out, …) has its
rim marked in :attr:`NanowellFrame.undefined` and is drawn as an empty ring in the undefined ink — never as
background and never as a filled disc; :attr:`NanowellFrame.missing` counts them by reason, and the note counts the
nanowells with no measured position, which are not drawn.

**Re-painted for a crop.** A metric map is geometry, not pixels, so when a crop would blow its discs up into stair
steps the Image Table asks for just the cropped window at a finer scale (:func:`table_frame`'s *window* and
*oversample*). A montage never is: its pixels are what the run or the tiles hold.

Qt-free. Called from the Image Table's prefetch pool as well as the GUI thread, so the shared page state (the
read-wells cache, the observations memo) is touched under one lock; stitching, the slow part, is not.
"""

from __future__ import annotations

import io
import logging
import math
import threading
import zipfile
from pathlib import Path

import numpy as np

from review.data.nanowell_images import NanowellFrame, metric_label, montage_channel
from review.features.nanowell import full_montage_compute as fmc
from review.features.nanowell.collect import NanowellSchemeError, load_nanowell_data
from review.features.nanowell.image_compute import read_montage, tile_boxes
from review.features.nanowell.observation_compute import nanowell_observations

log = logging.getLogger("review")

_LOCK = threading.Lock()


def _hours(label) -> float:
    from pipeline.nanowell_task import timepoint_hours

    try:
        return timepoint_hours(label)
    except (TypeError, ValueError):
        return float("nan")


def _timepoint(data, well: str, tp) -> str | None:
    """The run's own label of *well*'s timepoint *tp* (a label, or hours as the Image Table's dropdown spells it)."""
    from domain.tp_match import tp_close

    labels = data.timepoints(well)
    for label in labels:
        if str(label) == str(tp):
            return label
    want = _hours(tp)
    if want != want:
        return None
    return next((label for label in labels if _hours(label) == _hours(label) and tp_close(_hours(label), want)), None)


def _data(ctx, well: str):
    """``(NanowellData, "")`` holding *well*, or ``(None, why not)``; the page's read-wells cache is shared."""
    st = ctx.state.feature_slice("nanowell")
    data_dir = getattr(ctx.state.dataset, "data_dir", None)
    if st is None or not data_dir:
        return None, "No dataset is loaded."
    if st.parts_for != str(data_dir):
        st.well_parts, st.parts_for, st.loaded_wells = {}, str(data_dir), frozenset()
    try:
        data = load_nanowell_data(Path(data_dir), wells=[well], parts=st.well_parts)
    except NanowellSchemeError as exc:
        return None, str(exc)
    if data is None:
        return None, "This dataset holds no nanowell tables."
    if well not in data.wells:
        problem = next((p for p in data.problems if str(p).startswith(f"{well}:")), "")
        return None, problem or f"{well} has no nanowell table."
    return data, ""


def frame_geometry(data, well: str, timepoint: str) -> tuple[tuple[float, float, float, int, int] | None, str]:
    """``((left_um, top_um, um_per_px, rows, columns), "")`` — the frame every nanowell frame of *well* at
    *timepoint* is drawn on — or ``(None, why not)``.

    The origin and scale are the grid record's for the stored montage; the size is that montage's (read from its
    PNG header, not decoded), or, when the run kept none, whatever covers every tile at that scale."""
    block = (data.grids.get(well) or {}).get(timepoint) or {}
    origin, scale = block.get("montage_origin_um"), block.get("montage_um_per_px")
    if not origin or not scale or float(scale) <= 0:
        return None, (f"The grid record has no montage for {well} at {timepoint} "
                      f"({block.get('status') or 'no record'}), so there is no frame to draw it on.")
    left, top, scale = float(origin[0]), float(origin[1]), float(scale)
    shape = _stored_shape(data, well, timepoint)
    if shape is None:
        boxes = [b for b in tile_boxes(data, well, timepoint) if b.width_um and b.height_um]
        if not boxes:
            return None, f"{well} at {timepoint} has neither a stored montage nor tile placements to size a frame."
        right = max(b.left_um + b.width_um for b in boxes)
        bottom = max(b.top_um + b.height_um for b in boxes)
        shape = (max(1, math.ceil((bottom - top) / scale)), max(1, math.ceil((right - left) / scale)))
    return (left, top, scale, int(shape[0]), int(shape[1])), ""


def _stored_shape(data, well: str, timepoint: str) -> tuple[int, int] | None:
    from PIL import Image

    if data.out_dir is None:
        return None
    try:
        with zipfile.ZipFile(Path(data.out_dir) / f"{well}_out.zip") as zf, \
                Image.open(io.BytesIO(zf.read(f"{well}_{timepoint}_montage.png"))) as im:
            width, height = im.size
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    return int(height), int(width)


def table_frame(ctx, well: str, tp, token: str, *, window=None, oversample: int = 1) -> NanowellFrame | None:
    """The nanowell frame *token* names (``nanowell_images``) for *well* at *tp*, or ``None`` when *token* is not
    one. A frame that cannot be drawn is a :class:`NanowellFrame` with no values and a note saying why.

    *window* ``(y0, x0, y1, x1)`` and *oversample* re-paint part of a **metric map** at *oversample* × the frame's
    resolution: the Image Table asks for this when a crop would otherwise blow each disc up into stair steps. A
    disc's geometry is exact, so it can be drawn at any resolution; a montage's pixels are what the run or the tiles
    hold, and are never re-drawn finer than the frame (the call returns ``None`` for one)."""
    channel, label = montage_channel(token), metric_label(token)
    if channel is None and label is None:
        return None
    if window is not None and label is None:
        return None
    with _LOCK:
        data, why = _data(ctx, well)
    if data is None:
        return NanowellFrame(None, note=why)
    timepoint = _timepoint(data, well, tp)
    if timepoint is None:
        return NanowellFrame(None, note=f"{well} has no timepoint {tp}.")
    geometry, why = frame_geometry(data, well, timepoint)
    if geometry is None:
        return NanowellFrame(None, note=why)
    if channel == "":
        return _phase_frame(data, well, timepoint, geometry)
    if channel is not None:
        return channel_frame(data, well, timepoint, channel, geometry)
    if window is not None:
        geometry = window_geometry(geometry, window, oversample)
        if geometry is None:
            return None
    return _metric_frame(ctx, well, timepoint, label, geometry)


def window_geometry(geometry, window, oversample: int) -> tuple | None:
    """*geometry*'s pixels ``window`` ``(y0, x0, y1, x1)`` as a geometry of its own, *oversample* × finer; ``None``
    when the window holds no pixel of the frame."""
    left, top, scale, height, width = geometry
    y0, x0, y1, x1 = (int(v) for v in window)
    y0, y1 = max(0, min(y0, height)), max(0, min(y1, height))
    x0, x1 = max(0, min(x0, width)), max(0, min(x1, width))
    k = max(1, int(oversample))
    if y1 <= y0 or x1 <= x0:
        return None
    return left + x0 * scale, top + y0 * scale, scale / k, (y1 - y0) * k, (x1 - x0) * k


def _phase_frame(data, well: str, timepoint: str, geometry) -> NanowellFrame:
    montage, why = read_montage(data, well, timepoint)
    if montage is None:
        return NanowellFrame(None, geometry[2], note=why)
    return NanowellFrame(montage.image.astype(np.float32), geometry[2])


def channel_frame(data, well: str, timepoint: str, channel: str, geometry) -> NanowellFrame:
    """*channel*'s montage of *well* at *timepoint*, stitched from its raw tiles onto *geometry*."""
    left, top, scale, rows, cols = geometry
    spelled = next((c for c in data.image_channels if c.lower() == channel.lower()), None)
    if spelled is None:
        read = ", ".join(data.image_channels) or "none"
        return NanowellFrame(None, scale, note=f"The run read no {channel.upper()} images (fluorescence channels: {read}).")
    source, why = fmc.montage_source(data, well, timepoint, spelled)
    if source is None:
        return NanowellFrame(None, scale, note=why)
    store = fmc.TileStore(source)
    level = fmc.level_for(scale / source.pixel_size_um)
    names = [t.tile for t in source.tiles]
    store.load(names, level=level if level < fmc.KEEP_FROM_LEVEL else None)
    region = (left, top, left + cols * scale, top + rows * scale)
    values, out = store.values(region, cols, rows)
    if out.wants:                       # a fine level evicted under the byte budget: read what is still wanted
        for tile, wanted in sorted(out.wants):
            store.load([tile], level=wanted)
        values, out = store.values(region, cols, rows)
    notes = []
    if source.reason and "have no image" in source.reason:
        notes.append(source.reason.strip())
    if store.failed:
        notes.append(f"{len(store.failed)} tile(s) could not be read and are not drawn: {', '.join(sorted(store.failed))}.")
    if out.wants:
        notes.append(f"{len(out.wants)} tile(s) are drawn from a coarser level than the frame's scale.")
    if not out.covered.any():
        return NanowellFrame(None, scale, note=" ".join(notes) or f"No {spelled} tile falls inside the frame.")
    return NanowellFrame(values, scale, note=" ".join(notes))


def _metric_frame(ctx, well: str, timepoint: str, label: str, geometry) -> NanowellFrame:
    with _LOCK:
        table = nanowell_observations(ctx, [well])
    if table is None:
        return NanowellFrame(None, geometry[2], note="This dataset holds no nanowell tables.")
    spec = next((m for m in table.metrics if m.label.lower() == label.lower() or m.key.lower() == label.lower()), None)
    if spec is None:
        return NanowellFrame(None, geometry[2], note=f"No nanowell metric is called {label!r} on this dataset.")
    frame = table.frame
    rows = frame[(frame["well"] == well) & (frame["timepoint"].astype(str) == str(timepoint))]
    why = table.why_missing(spec.key).loc[rows.index] if table.why_missing is not None else None
    return metric_map(rows, spec.key, spec.label, geometry, why)


def metric_map(rows, column: str, label: str, geometry, why=None) -> NanowellFrame:
    """*rows*' nanowells as discs on *geometry*, each filled with its *column* value (see the module docstring);
    *why* (aligned to *rows*) names the reason each undefined value is undefined, counted in
    :attr:`NanowellFrame.missing`.

    A nanowell with no value is drawn as its rim alone — an empty ring in the undefined ink — so it differs from a
    value by its shape and not only by a colour a colormap might also hold."""
    import pandas as pd

    left, top, scale, height, width = geometry
    values = np.full((height, width), np.nan, dtype=np.float32)
    undefined = np.zeros((height, width), dtype=bool)
    xs = pd.to_numeric(rows.get("well_x_um"), errors="coerce") if len(rows) else pd.Series(dtype=float)
    ys = pd.to_numeric(rows.get("well_y_um"), errors="coerce") if len(rows) else pd.Series(dtype=float)
    radii = pd.to_numeric(rows.get("radius_um"), errors="coerce") if len(rows) else pd.Series(dtype=float)
    fallback = float(radii.median()) if radii.notna().any() else 0.0
    vals = pd.to_numeric(rows[column], errors="coerce") if column in rows else pd.Series(np.nan, index=rows.index)
    unplaced, reasons, drawn = 0, {}, 0
    for idx in rows.index:
        x, y = xs.get(idx), ys.get(idx)
        if x is None or y is None or not (np.isfinite(x) and np.isfinite(y)):
            unplaced += 1
            continue
        radius = radii.get(idx)
        radius = fallback if radius is None or not np.isfinite(radius) else float(radius)
        cx, cy, r = (x - left) / scale, (y - top) / scale, max(radius / scale, 0.75)
        if cx + r < 0 or cy + r < 0 or cx - r > width or cy - r > height:
            continue                     # outside a cropped window: neither drawn nor counted
        y0, y1 = max(0, int(math.floor(cy - r))), min(height, int(math.ceil(cy + r)) + 1)
        x0, x1 = max(0, int(math.floor(cx - r))), min(width, int(math.ceil(cx + r)) + 1)
        if y1 <= y0 or x1 <= x0:
            continue
        gy, gx = np.mgrid[y0:y1, x0:x1]
        d2 = (gx + 0.5 - cx) ** 2 + (gy + 0.5 - cy) ** 2
        disc = d2 <= r * r
        value = vals.get(idx)
        drawn += 1
        if value is not None and np.isfinite(value):
            values[y0:y1, x0:x1][disc] = float(value)
            undefined[y0:y1, x0:x1][disc] = False
        else:
            rim = max(1.0, r * 0.18)
            ring = disc & (d2 >= (r - rim) ** 2)
            undefined[y0:y1, x0:x1][ring] = True
            text = str(why.get(idx) or "") if why is not None else ""
            reasons[text or "no value"] = reasons.get(text or "no value", 0) + 1
    note = f"{unplaced} nanowell(s) have no measured position and are not drawn." if unplaced else ""
    if not drawn:
        return NanowellFrame(None, scale, note=note or "No nanowell to draw at this timepoint.")
    return NanowellFrame(values, scale, undefined, note, missing=reasons, total=drawn)
