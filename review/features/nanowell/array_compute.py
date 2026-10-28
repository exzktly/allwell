"""The Array view as data: one well at one timepoint, and every element the view can draw over it.

The Nanowells page used to show one well's array three ways, each with its own code: the **Array map** (the
nanowells at their lattice places, coloured by the metric), **Array QC** (the run's montage, the tiles by how
they were placed, the nanowells marked by state, the grid record) and the **Full montage** window (the raw tiles
at full resolution, the nanowells with their addresses, the anchor). Each drew the same nanowells differently —
the map in grey styles, Array QC only its occluded and clipped ones, the window in the state palette — and only
the map could be clicked, only the window zoomed. They are now one view whose elements are switched on and off
(``state.ArrayLayers``), drawn by one renderer (``renderer.render_array``) from one scene, built here.

**Two frames.** A nanowell is drawn either where the run *measured* it, in the well frame (µm, y down) the
montage, the tiles and the anchor are in, or where its address puts it on an ideal *lattice* of the well's pitch
(``views_compute.lattice_xy``; decision U4), where it keeps its place from one timepoint to the next even when the
plate was put back rotated. The image, the tiles and the anchor have no place on the lattice, so in that frame
they are not drawn, and the figure says so rather than drawing them somewhere wrong.

Qt-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from review.features.nanowell import full_montage_compute as fmc
from review.features.nanowell import views_compute as vc
from review.features.nanowell.collect import MEASURABLE, cell_positions
from review.features.nanowell.image_compute import tile_boxes

#: The states with a nanowell seen at its place, whatever its number: a timepoint with none of them has no
#: addresses, and the figure says why.
_SEEN = MEASURABLE | {"occluded", "clipped"}


@dataclass
class ArraySites:
    """The nanowells drawn, one entry each, in the scene's frame."""
    x: np.ndarray
    y: np.ndarray
    radius: np.ndarray                  # µm, per nanowell
    values: np.ndarray                  # the metric; NaN where undefined
    state: list
    fused: np.ndarray
    sites: list                         # (well, q, r)
    labels: list                        # ``q,r`` (``util.nanowell_address.coordinate_text``), "" without an address

    def __len__(self) -> int:
        return len(self.x)


@dataclass
class ArrayScene:
    """One well at one timepoint: the nanowells in :attr:`frame`, and — in the well frame — the tiles, the anchor
    and what the grid record says."""
    well: str
    timepoint: str
    frame: str                          # "well" (measured positions) or "lattice"
    sites: ArraySites
    roster: int                         # nanowells of the roster at this timepoint
    unplaced: int                       # of those, not drawn: no measured position (well frame only)
    counts: dict                        # state -> nanowells of the roster
    pitch_um: float | None
    reason: str = ""                    # why the timepoint has no addressed nanowells, when it has none
    no_places: str = ""                 # why no nanowell has a place in this frame, when none has
    tiles: list = field(default_factory=list)          # image_compute.TileBox, well frame
    status: str = ""                    # the grid record's status at this timepoint
    anchor: tuple | None = None         # the anchor set by hand, well-frame µm
    anchor_text: str = ""               # how the timepoint was addressed and what its anchor does
    anchorable: bool = False            # an anchor can change this timepoint's result (``ANCHORABLE``)
    record: list = field(default_factory=list)         # (text, kind): the grid record, kind head / flag / plain
    cells: tuple | None = None          # (x, y) of each counted cell drawn, in the scene's frame; None = not read
    cells_note: str = ""                # what the Cells layer drew, and what it could not

    def centre(self) -> int | None:
        """The index of the central nanowell, address ``(0, 0)``, among those drawn."""
        for k, (_w, q, r) in enumerate(self.sites.sites):
            if q == 0 and r == 0:
                return k
        return None

    def extent(self) -> tuple[float, float, float, float] | None:
        """``(left, top, right, bottom)`` of the nanowells and tiles drawn, or ``None`` when there are none."""
        xs, ys = [], []
        if len(self.sites):
            r = self.sites.radius
            xs += [float(np.min(self.sites.x - r)), float(np.max(self.sites.x + r))]
            ys += [float(np.min(self.sites.y - r)), float(np.max(self.sites.y + r))]
        for box in self.tiles if self.frame == "well" else ():
            xs += [box.left_um, box.left_um + (box.width_um or 0.0)]
            ys += [box.top_um, box.top_um + (box.height_um or 0.0)]
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)


def _empty_sites() -> ArraySites:
    nothing = np.zeros(0)
    return ArraySites(nothing, nothing, nothing, nothing, [], np.zeros(0, dtype=bool), [], [])


def _labels(qs, rs) -> list[str]:
    from util.nanowell_address import coordinate_text

    return [coordinate_text(q, r) or "" for q, r in zip(qs, rs)]


def array_scene(metrics, data, well: str, timepoint: str, column: str, *, positions: str = "measured",
                anchors: bool = True, cells: bool = False) -> ArrayScene:
    """The Array view of *well* at *timepoint*, coloured by *column*, with its nanowells at their *positions*
    (``"measured"`` or ``"lattice"``). *anchors* off skips reading the anchors file (a batch figure, which draws
    no anchor); *cells* on reads the well's counted cells for the Cells layer (:func:`cells_into`)."""
    rows = metrics[(metrics["well"] == well) & (metrics["timepoint"] == timepoint)]
    states = [str(s) for s in rows["state"]]
    counts: dict[str, int] = {}
    for s in states:
        counts[s] = counts.get(s, 0) + 1
    reason = ""
    if len(rows) and all(s not in _SEEN for s in states):
        reason = next((r for r in rows["reason"] if r), "this timepoint has no addressed nanowells")
    pitch = vc.well_pitch(data, well)
    frame = "lattice" if positions == "lattice" else "well"
    scene = ArrayScene(well, timepoint, frame, _empty_sites(), len(rows), 0, counts, pitch, reason)
    if frame == "lattice":
        model = vc.map_model(metrics, data, well, timepoint, column)
        if model is None:
            scene.no_places = ("no addressed nanowells and no measured pitch to place them on" if len(rows)
                               else "no addressed nanowells")
        else:
            n = len(model.x)
            scene.sites = ArraySites(model.x, model.y, np.full(n, model.radius_um), model.values, list(model.state),
                                     np.asarray(model.fused, dtype=bool), list(model.sites),
                                     _labels([s[1] for s in model.sites], [s[2] for s in model.sites]))
    else:
        placed = rows[rows["well_x_um"].notna() & rows["well_y_um"].notna()]
        scene.unplaced = len(rows) - len(placed)
        if len(placed):
            radius = placed["radius_um"].astype(float)
            fallback = float(radius.median()) if radius.notna().any() else (pitch or 0.0) * 0.3
            scene.sites = ArraySites(
                placed["well_x_um"].to_numpy(dtype=float), placed["well_y_um"].to_numpy(dtype=float),
                radius.fillna(fallback).to_numpy(dtype=float),
                vc.numeric_values(placed, column).to_numpy(dtype=float), [str(s) for s in placed["state"]],
                placed["fused"].eq(True).to_numpy(),
                # Built as a list, not with ``Series.map``: pandas reads a ``None`` back as NaN and every address
                # as a float, and a NaN address would pass the hit map's "has an address" test.
                [(w, _int(q), _int(r)) for w, q, r in zip(placed["well"], placed["nw_q"], placed["nw_r"])],
                _labels(placed["nw_q"], placed["nw_r"]))
        else:
            scene.no_places = ("no nanowell with a measured position at this timepoint" if len(rows)
                               else "no addressed nanowells")
        scene.tiles = tile_boxes(data, well, timepoint)
    block = (data.grids.get(well) or {}).get(timepoint) or {}
    scene.status = str(block.get("status") or "")
    scene.record = record_lines(data.grids.get(well) or {}, timepoint)
    if frame == "well" and anchors:
        _anchor_into(scene, data, block)
    if cells:
        cells_into(scene, rows, data)
    return scene


def cells_into(scene: ArrayScene, rows, data) -> None:
    """The Cells layer: a dot at each counted cell's centre, from the run's cell table (the run keeps no outlines).

    In the well frame a dot is where the run measured the cell. On the lattice it keeps its offset from its
    nanowell's measured centre, added to that nanowell's lattice place, so it sits in the same spot of the same
    nanowell; a cell whose nanowell has no address or no measured centre has no place there and is counted, not
    drawn. What was drawn and what was not is :attr:`ArrayScene.cells_note`."""
    found = cell_positions(data, scene.well, scene.timepoint)
    if found is None:
        scene.cells = (np.zeros(0), np.zeros(0))
        scene.cells_note = (f"Cells: {scene.well}'s cell table holds no cell positions, so no cell is drawn."
                            if scene.well in data.cell_tables else
                            f"Cells: {scene.well} has no cell table, so no cell is drawn (a run that counted no "
                            "cell, or one that could not count).")
        return
    if found.empty:
        scene.cells = (np.zeros(0), np.zeros(0))
        scene.cells_note = f"Cells: the run counted no cell in {scene.well} at {scene.timepoint}."
        return
    x, y = found["well_x_um"].to_numpy(dtype=float), found["well_y_um"].to_numpy(dtype=float)
    unplaced = 0
    if scene.frame == "lattice":
        centres = {(_int(q), _int(r)): (cx, cy) for q, r, cx, cy in
                   zip(rows["nw_q"], rows["nw_r"], rows["well_x_um"], rows["well_y_um"])
                   if np.isfinite(cx) and np.isfinite(cy)}
        places = {(q, r): (scene.sites.x[k], scene.sites.y[k]) for k, (_w, q, r) in enumerate(scene.sites.sites)}
        keep, lx, ly = [], [], []
        for k, (q, r) in enumerate(zip(found["nw_q"], found["nw_r"])):
            site = (_int(q), _int(r))
            if site in centres and site in places:
                keep.append(k)
                lx.append(places[site][0] + x[k] - centres[site][0])
                ly.append(places[site][1] + y[k] - centres[site][1])
        unplaced = len(found) - len(keep)
        x, y = np.asarray(lx, dtype=float), np.asarray(ly, dtype=float)
    scene.cells = (x, y)
    note = (f"Cells: a dot at the centre of each of the {len(x):,} cells the run counted here, from its cell table; "
            "the run keeps no cell outlines.")
    if unplaced:
        note += f" {unplaced:,} more have no place on the lattice (their nanowell has no address or measured centre)."
    scene.cells_note = note


def _int(value):
    """An address component as an integer, or ``None`` for a nanowell without an address."""
    return int(value) if value is not None and not (isinstance(value, float) and math.isnan(value)) else None


def _anchor_into(scene: ArrayScene, data, block: dict) -> None:
    anchor, problem = fmc.read_well_anchor(data, scene.well, scene.timepoint)
    scene.anchor = anchor
    scene.anchorable = fmc.anchors_file(data) is not None and scene.status in fmc.ANCHORABLE
    scene.anchor_text = fmc.status_text(scene.well, scene.timepoint, scene.status, str(block.get("reason") or ""),
                                        block.get("origin_method"), anchor, problem)


# ── the grid record, beside the figure ──────────────────────────────────────
#: Lines of the grid record written beside the figure at most.
RECORD_LINES = 24


def record_lines(grid_blocks: dict, timepoint: str) -> list[tuple[str, str]]:
    """The grid record of *timepoint* in full, then one line for every other timepoint: ``(text, kind)``, kind
    ``head`` for the timepoint drawn, ``flag`` for a line that warns, ``plain`` otherwise."""
    from util.nanowell_anchors import origin_phrase

    block = grid_blocks.get(timepoint) or {}
    head = [f"{timepoint}: {block.get('status', 'no record')}"
            + (f" — origin from {origin_phrase(block.get('origin_method'))}"
               f" ({block.get('origin_agreement') or 'single source'})" if block.get("origin_method") else ""),
            f"pitch {_num(block.get('pitch_um'), 1)} µm · angle {_num(block.get('angle_deg'), 2)}° · "
            f"overlap {_overlap(block.get('overlap_um'))} · {block.get('n_detected', '?')} detected of "
            f"{block.get('roster_size', '?')}"]
    flags = frame_lines(block)
    rest = [template_line(block.get("template"))]
    rest += [f"{tp}: {b.get('status', '?')}" + (f" — {b.get('reason')}" if b.get("reason") else "")
             + (" — template suspect" if (b.get("template") or {}).get("suspect") else "")
             + (f" — {FRAME_WARNING}" if b.get("consistency") == "roster_mismatch" else "")
             for tp, b in grid_blocks.items() if tp != timepoint and isinstance(b, dict)]
    out = [(text, "head") for text in head] + [(text, "flag") for text in flags]
    out += [(text, "flag" if "template suspect" in text or "floor moved" in text or FRAME_WARNING in text
             else "plain") for text in rest if text]
    return out[:RECORD_LINES]


#: What a timepoint whose addresses disagree with its well's other timepoints' is said to be, wherever it is listed.
FRAME_WARNING = "its addresses may name neighbouring nanowells"


def frame_lines(block: dict) -> list[str]:
    """Warnings about whether *block*'s addresses name the same nanowells as its well's other timepoints': its
    addresses moved onto another timepoint's frame (said, so that a timepoint whose own origin landed a site off,
    or an inferred array whose outline was cut short, is not mistaken for one that anchored there itself), or a ``roster_mismatch`` the run could not settle (its rows are then joined with the other
    timepoints' by an address that may name a neighbour, and Review has nothing else to tell them apart by)."""
    out = []
    shift = block.get("origin_shift")
    if shift and any(shift):
        out.append(f"addresses moved by ({int(shift[0])}, {int(shift[1])}) lattice steps onto "
                   f"{block.get('origin_registered_to') or 'another timepoint'}'s, so that they name the same "
                   "nanowells as its (see the notes for why)")
    if block.get("consistency") == "roster_mismatch":
        detail = str(block.get("consistency_detail") or "").strip()
        out.append(f"roster mismatch — {FRAME_WARNING}" + (f" ({detail})" if detail else ""))
    return out


def template_line(template) -> str:
    """The grid record's empty-nanowell template, for a status line; a template flagged by the run says so."""
    if not isinstance(template, dict) or not template.get("source"):
        return ""
    line = f"template: {template['source']}, {template.get('n_sites', '?')} of {template.get('candidates', '?')} sites"
    drift = template.get("drift_vs_earliest")
    if template.get("suspect") and isinstance(drift, (int, float)):
        earliest = template.get("earliest_timepoint") or "the earliest timepoint"
        line += (f" — its floor moved {math.exp(drift):.1f}-fold against {earliest}'s: most nanowells may hold "
                 "cells, so counts and confluence here may be biased")
    return line


def _overlap(value) -> str:
    """The grid record's tile overlap — ``[x, y]`` µm as the pipeline writes it, or one number — for a status line."""
    parts = value if isinstance(value, (list, tuple)) else [value]
    shown = [_num(v, 1) for v in parts]
    if all(v == "?" for v in shown):
        return "?"
    return (shown[0] if len(set(shown)) == 1 else " × ".join(shown)) + " µm"


def _num(value, places: int) -> str:
    """A grid record's number for the status lines, or ``?`` where the record has none."""
    return f"{value:.{places}f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "?"


# ── where the view looks ─────────────────────────────────────────────────────
def one_to_one(region, width_px: int, height_px: int, um_per_px: float) -> tuple[float, float, float, float]:
    """*region* re-scaled about its centre to one image pixel (*um_per_px*) per figure pixel."""
    left, top, right, bottom = region
    cx, cy = (left + right) / 2, (top + bottom) / 2
    half_w, half_h = width_px * um_per_px / 2, height_px * um_per_px / 2
    return cx - half_w, cy - half_h, cx + half_w, cy + half_h


def padded(extent, pad: float) -> tuple[float, float, float, float]:
    left, top, right, bottom = extent
    return left - pad, top - pad, right + pad, bottom + pad
