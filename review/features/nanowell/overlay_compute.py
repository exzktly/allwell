"""What Segmentation draws over a nanowell run's tile: its cells as points and its nanowells' rims
(NANOWELL_OBSERVATIONS_TRACKER #5).

A nanowell run writes no label map — its cells are counted per nanowell and recorded as positions in the well frame
(``well_x_um``, ``well_y_um``), not as outlines — so Segmentation, which draws outlines from a label map, had nothing
to draw and drew nothing at all. It now draws the tile without outlines (its own, generic, fallback) and asks this
module, through the registry service ``segmentation.nanowell_overlay``, for what can honestly be drawn: each counted
cell as a point, and each nanowell the tile owns as a circle in its state's colour. Points look like points; nothing
here pretends to be a segmentation.

A tile's pixel is its well-frame position less the tile's placement in the grid record, over the pixel size (the
same rule the page's crops use, ``image_compute``). Qt-free.
"""

from __future__ import annotations

from domain.tp_match import tp_close
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from review.features.nanowell.collect import NanowellSchemeError, load_nanowell_data


@dataclass
class NanowellOverlay:
    #: ``(column, row)`` pixel positions of the counted cells in the tile (N × 2).
    points: np.ndarray
    #: ``(column, row, radius_px, state)`` of each nanowell the tile owns at the timepoint.
    rims: list = field(default_factory=list)
    #: Why the overlay is empty, ``""`` when it is not.
    note: str = ""


def _hours(label) -> float:
    from pipeline.nanowell_task import timepoint_hours

    return timepoint_hours(label)


def _same_timepoint(label, tp) -> bool:
    if str(label) == str(tp):
        return True
    try:
        h = _hours(label)
        return h == h and tp_close(h, float(tp))
    except (TypeError, ValueError):
        return False


def nanowell_overlay(ctx, well: str, fov: str, tp) -> NanowellOverlay | None:
    """The overlay of *well*'s tile *fov* at timepoint *tp*, or ``None`` when the dataset has no nanowell tables."""
    from review.data.data_loading import get_rows

    st = ctx.state.feature_slice("nanowell")
    data_dir = getattr(ctx.state.dataset, "data_dir", None)
    if st is None or not data_dir:
        return None
    if st.parts_for != str(data_dir):
        st.well_parts, st.parts_for, st.loaded_wells = {}, str(data_dir), frozenset()
    try:
        data = load_nanowell_data(Path(data_dir), wells=[well], parts=st.well_parts)
    except NanowellSchemeError:
        return None
    if data is None or well not in data.wells:
        return None
    label = next((t for t in data.timepoints(well) if _same_timepoint(t, tp)), None)
    block = (data.grids.get(well) or {}).get(label) or {}
    tile = next((t for t in block.get("tiles") or [] if str(t.get("tile")) == str(fov)), None)
    px = ((data.acquisition or {}).get(label) or {}).get("pixel_size_um") or {}
    um = px.get("x")
    if label is None or tile is None or not um:
        why = ("the grid record does not place this tile" if tile is None else "no pixel size is recorded")
        return NanowellOverlay(np.empty((0, 2)), [], f"Nanowell overlay unavailable: {why}.")
    left, top = float(tile["left_um"]), float(tile["top_um"])
    cells = get_rows(ctx, well)
    points = np.empty((0, 2))
    if cells is not None and {"well_x_um", "well_y_um", "timepoint", "fov"} <= set(cells.columns):
        mine = cells[(cells["fov"].astype(str) == str(fov)) & cells["timepoint"].map(lambda t: _same_timepoint(t, tp))]
        xy = mine[["well_x_um", "well_y_um"]].dropna().to_numpy(float)
        points = np.column_stack([(xy[:, 0] - left) / um, (xy[:, 1] - top) / um]) if len(xy) else points
    sites = data.sites
    rims = []
    if len(sites):
        owned = sites[(sites["well"] == well) & (sites["timepoint"] == label) & (sites["owner"].astype(str) == str(fov))]
        for _, row in owned.iterrows():
            if row.get("well_x_um") is None or row.get("radius_um") is None:
                continue
            rims.append(((float(row["well_x_um"]) - left) / um, (float(row["well_y_um"]) - top) / um,
                         float(row["radius_um"]) / um, str(row.get("state") or "")))
    return NanowellOverlay(points, rims)
