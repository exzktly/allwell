"""What the Array map, Trajectories and Position views draw, as data (tracker V2, V2a, V4, V6; decision U4).

**Array map positions are lattice positions** (U4). A nanowell is drawn where its address puts it on an ideal
lattice of the well's pitch, east along ``q``, ``r`` 60° counter-clockwise from it on screen (y down), so a
nanowell stays in one place while the time slider moves — the plate may have been put back rotated between
acquisitions, and the measured positions belong to Array QC, the misregistration view. A site has a place
whether or not it was seen.

**Trajectories** are each nanowell's metric over time, coloured by occupancy class, with one median per
occupancy class over the nanowells with a value at each timepoint — "which nanowells went where". **Position
effects** are the metric against ring (distance from the central square in lattice steps), per well, with the
share of each ring's nanowells excluded — an edge-effect check, descriptive only.

Qt-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from review.features.nanowell.collect import MEASURABLE

_SQRT3_2 = math.sqrt(3.0) / 2.0


def lattice_xy(q, r, pitch_um: float) -> tuple[np.ndarray, np.ndarray]:
    """Display positions (µm, y down) of axial addresses on an ideal hex lattice of *pitch_um*.

    ``q`` steps east; ``r`` steps along the direction 60° counter-clockwise from east on screen, which with y
    down is up and to the right — the pipeline's convention (``pipeline_info.json``'s ``nanowell.conventions``).
    The central square ``(0, 0)`` is at the origin.
    """
    q = np.asarray(q, dtype=float)
    r = np.asarray(r, dtype=float)
    return pitch_um * (q + 0.5 * r), -pitch_um * _SQRT3_2 * r


def well_pitch(data, well: str) -> float | None:
    """The well's measured lattice pitch: the median over its timepoints' grid records, or ``None``."""
    pitches = [b.get("pitch_um") for b in (data.grids.get(well) or {}).values() if isinstance(b, dict)]
    pitches = [float(p) for p in pitches if isinstance(p, (int, float)) and math.isfinite(p) and p > 0]
    return float(np.median(pitches)) if pitches else None


@dataclass
class MapModel:
    """One well at one timepoint, every nanowell of its roster at its lattice place."""
    well: str
    timepoint: str
    pitch_um: float
    x: np.ndarray
    y: np.ndarray
    radius_um: float
    values: np.ndarray                    # the metric; NaN where undefined
    state: list                           # the row's state (measured / empty / occluded / … / absent)
    fused: np.ndarray
    sites: list                           # (well, q, r)
    reason: str = ""                      # why the timepoint has no address, when it has none
    counts: dict = field(default_factory=dict)    # state -> number of nanowells


_OCCUPANCY_VALUE = {"0": 0.0, "1": 1.0, "2": 2.0, "3+": 3.0}


def numeric_values(rows: pd.DataFrame, column: str) -> pd.Series:
    """*column* of *rows* as numbers: the occupancy class as its lower bound (``3+`` as 3), NaN where undefined."""
    if column == "occupancy_class":
        return rows[column].map(_OCCUPANCY_VALUE).astype(float)
    return pd.to_numeric(rows[column], errors="coerce")


def map_model(metrics: pd.DataFrame, data, well: str, timepoint: str, column: str) -> MapModel | None:
    """The array map for *well* at *timepoint*, or ``None`` when the well has no addressed nanowell."""
    rows = metrics[(metrics["well"] == well) & (metrics["timepoint"] == timepoint)]
    pitch = well_pitch(data, well)
    if not len(rows) or pitch is None:
        return None
    x, y = lattice_xy(rows["nw_q"].to_numpy(), rows["nw_r"].to_numpy(), pitch)
    radius = rows["radius_um"].dropna()
    values = numeric_values(rows, column).to_numpy(dtype=float)
    states = list(rows["state"].astype(str))
    reason = ""
    if all(s not in MEASURABLE and s not in ("occluded", "clipped") for s in states):
        reason = next((r for r in rows["reason"] if r), "this timepoint has no addressed nanowells")
    counts: dict[str, int] = {}
    for s in states:
        counts[s] = counts.get(s, 0) + 1
    return MapModel(well, timepoint, pitch, x, y, float(radius.median()) if len(radius) else pitch * 0.3,
                    values, states, rows["fused"].eq(True).to_numpy(), list(zip(rows["well"],
                    rows["nw_q"].astype(int), rows["nw_r"].astype(int))), reason, counts)


@dataclass
class TrajectoryPanel:
    well: str
    times: np.ndarray                     # the x positions (hours, or timepoint order when time is unknown)
    labels: list                          # timepoint labels at those positions
    lines: list                           # (site, occupancy, y over the times) — NaN where no value
    class_medians: dict                   # occupancy class -> median at each time (NaN where none)
    n_without_value: int                  # nanowells with no value at any timepoint (not drawn)


def trajectory_panels(metrics: pd.DataFrame, wells, column: str, *, cohort: bool = False) -> list[TrajectoryPanel]:
    """One panel per well: a line per nanowell over time, and the median of each occupancy class. With *cohort*,
    only the nanowells with a value at every timepoint are drawn and pooled (tracker I8); the rest are counted
    among those not drawn."""
    out = []
    for well in wells:
        rows = metrics[metrics["well"] == well]
        if not len(rows):
            continue
        order = rows.drop_duplicates("timepoint").sort_values("tp_order")
        use_time = order["time_h"].notna().all()
        xs = (order["time_h"] if use_time else order["tp_order"]).to_numpy(dtype=float)
        tps = list(order["timepoint"])
        values = numeric_values(rows, column)
        wide = rows.assign(_v=values).pivot_table(index=["nw_q", "nw_r"], columns="timepoint", values="_v",
                                                  aggfunc="first", dropna=False).reindex(columns=tps)
        occupancy = rows.drop_duplicates(["nw_q", "nw_r"]).set_index(["nw_q", "nw_r"])["occupancy_class"]
        lines, empty = [], 0
        for (q, r), series in wide.iterrows():
            y = series.to_numpy(dtype=float)
            if not np.isfinite(y).any() or (cohort and not np.isfinite(y).all()):
                empty += 1
                continue
            occ = occupancy.get((q, r))
            lines.append(((well, int(q), int(r)), None if pd.isna(occ) else str(occ), y))
        medians = {}
        for cls in ("0", "1", "2", "3+"):
            ys = np.array([y for _s, occ, y in lines if occ == cls])
            if len(ys):
                with np.errstate(all="ignore"):
                    medians[cls] = np.array([np.nanmedian(col) if np.isfinite(col).any() else np.nan
                                             for col in ys.T])
        out.append(TrajectoryPanel(well, xs, tps, lines, medians, empty))
    return out


def hex_ring(q, r) -> np.ndarray:
    """Lattice steps from the central nanowell, from the axial address — the pipeline's ``hex_ring``.

    Computed from ``(q, r)`` rather than read from a row's ``nw_ring``, because the rows Review adds for a
    roster nanowell not seen at a timepoint (absent, not located) carry the address and no ring.
    """
    q = np.asarray(q, dtype=float)
    r = np.asarray(r, dtype=float)
    return (np.abs(q) + np.abs(r) + np.abs(q + r)) / 2.0


@dataclass
class PositionSeries:
    well: str
    rings: np.ndarray
    median: np.ndarray
    q1: np.ndarray
    q3: np.ndarray
    n_used: np.ndarray
    excluded_share: np.ndarray            # fraction of the ring's nanowells with no value


def position_series(metrics: pd.DataFrame, wells, timepoint: str,
                    column: str) -> tuple[list[PositionSeries], list[str]]:
    """Per well, the metric's median and quartiles by ring at *timepoint*, and each ring's excluded share —
    every addressed roster nanowell counts towards its ring, seen or not. Also the wells with no addressed
    nanowell at *timepoint*, which have no series and are named rather than dropped."""
    out, missing = [], []
    for well in wells:
        rows = metrics[(metrics["well"] == well) & (metrics["timepoint"] == timepoint)]
        frame = pd.DataFrame({"ring": hex_ring(rows["nw_q"], rows["nw_r"]),
                              "v": numeric_values(rows, column).to_numpy(dtype=float)}).dropna(subset=["ring"])
        if not len(frame):
            missing.append(well)
            continue
        rings = np.array(sorted(frame["ring"].unique()), dtype=float)
        median, q1, q3, used, share = [], [], [], [], []
        for ring in rings:
            v = frame.loc[frame["ring"] == ring, "v"].to_numpy(dtype=float)
            have = v[np.isfinite(v)]
            median.append(float(np.median(have)) if len(have) else np.nan)
            q1.append(float(np.quantile(have, 0.25)) if len(have) else np.nan)
            q3.append(float(np.quantile(have, 0.75)) if len(have) else np.nan)
            used.append(len(have))
            share.append(1.0 - len(have) / len(v))
        out.append(PositionSeries(well, rings, np.array(median), np.array(q1), np.array(q3),
                                  np.array(used, dtype=int), np.array(share)))
    return out, missing
