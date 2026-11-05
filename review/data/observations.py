"""Observations for the plot tools: what a plot's points are on this dataset, and the rows behind them.

Cells come from the row cache, as they always have. Nanowells come from whichever feature answers the
``observations.nanowell`` registry service (the Nanowells feature, ``review/domain/observation.py``) — asked, never
imported, so a build without that feature simply has no nanowell observations, and every caller here decides what
that means instead of meeting an ``ImportError``.

Imports no feature.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from review.domain.observation import (
    CELLS, NANOWELL_CELL_PREFIX, NANOWELL_SERVICE, NANOWELLS, ObservationMetric, ObservationTable,
    is_nanowell_cell_column,
)

if TYPE_CHECKING:
    import pandas as pd


def is_nanowell_dataset(ctx) -> bool:
    """Whether the loaded dataset was written by a nanowell run."""
    from review.domain.qc_params import is_nanowell_run

    return is_nanowell_run(getattr(getattr(ctx.state, "dataset", None), "pipeline_info", None))


def nanowell_views_available(ctx) -> bool:
    """Whether this build has a supplier of nanowell observations (the Nanowells feature)."""
    features = getattr(ctx, "features", None)
    return features is not None and features.service(NANOWELL_SERVICE) is not None


def available_observations(ctx) -> tuple[str, ...]:
    """The observations a plot may draw on this dataset in this build: cells, and nanowells when both hold."""
    if is_nanowell_dataset(ctx) and nanowell_views_available(ctx):
        return (CELLS, NANOWELLS)
    return (CELLS,)


def nanowell_observations(ctx, wells) -> ObservationTable | None:
    """The nanowell rows of *wells*, or ``None`` when this build or this dataset has none."""
    if not is_nanowell_dataset(ctx):
        return None
    return ctx.features.query("observations.nanowell", ctx, list(wells), default=None)   # NANOWELL_SERVICE


def nanowell_metric_specs(ctx, wells) -> tuple | None:
    """The metrics :func:`nanowell_observations` would offer for *wells*, with no rows read or computed, or ``None``
    when this build or this dataset has none. For a picker's entries: computing the whole table to name its metrics
    held up every nanowell plate's load."""
    if not is_nanowell_dataset(ctx):
        return None
    return ctx.features.query("observations.nanowell_metrics", ctx, list(wells), default=None)  # NANOWELL_METRICS_SERVICE


def nanowell_metric_for_token(ctx, token) -> ObservationMetric | None:
    """The ``ObservationMetric`` an Image Table ``NW: <label>`` token names (``data/nanowell_images``), matched by
    label case-insensitively, or ``None`` — not a metric token, or not a metric of this dataset or build."""
    from review.data.nanowell_images import metric_label

    label = metric_label(token)
    if label is None:
        return None
    wells = list(getattr(getattr(ctx.state, "dataset", None), "well_paths", {}) or {})
    specs = nanowell_metric_specs(ctx, wells) or ()
    return next((m for m in specs if m.label.lower() == label.lower() or m.key.lower() == label.lower()), None)


def fold_change_scale(metric) -> str:
    """How a nanowell metric's values sit around "no change": ``"ratio"`` for a fold change (1 is no change, and
    ×2 and ×½ are the same distance from it, so it is drawn on a log scale centred at 1), ``"log"`` for one already
    on a log scale (0 is no change), ``""`` for anything that is not a fold change."""
    if metric is None or getattr(metric, "kind", "") != "fc":
        return ""
    return "log" if str(metric.key).startswith("log") else "ratio"


#: :func:`occupied_nanowells_by_well`'s answer per well file version: ``(path, fingerprint) -> count``, or ``None``
#: for a table with no nanowell address columns.
_OCCUPIED: dict[tuple[str, str], int | None] = {}


def _count_occupied(rows) -> int | None:
    if rows is None or "nw_q" not in rows.columns or "nw_r" not in rows.columns:
        return None
    return int(len(rows[["nw_q", "nw_r"]].dropna().drop_duplicates()))


def _occupied_in(ctx, well: str, path) -> int | None:
    """One well's count, without reading the whole frame when it is not held.

    The well's own held frame when the row cache has it; else the two address columns from its Parquet cache;
    else (no current cache) the whole frame, as before.
    """
    from pathlib import Path

    from review.data.data_loading import get_rows
    from review.data.parquet_cache import cached_column_names, read_cached

    cache = getattr(ctx.state.dataset, "cache", None)
    held = cache.get(well) if cache is not None else None
    if held is not None:
        return _count_occupied(held)
    names = cached_column_names(Path(path))
    if names is not None:
        if "nw_q" not in names or "nw_r" not in names:
            return None
        frame = read_cached(Path(path), columns=["nw_q", "nw_r"])
        if frame is not None:
            return _count_occupied(frame)
    return _count_occupied(get_rows(ctx, well))


def occupied_nanowells_by_well(ctx, wells) -> dict[str, int]:
    """Per well, how many distinct nanowell addresses its cell rows name — the nanowells holding a cell.

    Read from the cell table alone, so it needs no nanowell feature: it is what a build without one can still say
    about a nanowell plate's cell plots (``observation.pooled_cells_line``).

    **Counted once per file version.** The count is of every cell row, not the included ones, so it is a property
    of the CSV alone: it is remembered per well under the file's path and fingerprint
    (``parquet_cache.source_fingerprint``: size, modification time, parser version), and a rewritten CSV is
    counted again. It was read on every redraw of every cell plot that carries the footer — on a plate too large
    for the row cache, a second whole-frame read of every well on each visit to Distribution, on top of the one
    the plot itself made. A well that has to be counted reads only its two address columns.
    """
    from review.data.parquet_cache import source_fingerprint

    out: dict[str, int] = {}
    paths = ctx.state.dataset.well_paths
    for well in wells:
        path = paths.get(well)
        if path is None:
            continue
        fingerprint = source_fingerprint(path)
        key = (str(path), fingerprint) if fingerprint is not None else None
        if key is not None and key in _OCCUPIED:
            count = _OCCUPIED[key]
        else:
            count = _occupied_in(ctx, well, path)
            if key is not None:
                _OCCUPIED[key] = count
        if count is not None:
            out[well] = count
    return out


def observation_groups(ctx) -> list[tuple[str, tuple[str, ...], str]]:
    """``(legend label, wells, colour)`` per plotted member: the replicate sets when any are active, else each
    selected well — the members every plot draws, in their order (as ``scatter.collect.scatter_groups``)."""
    from review.domain.plot_members import plot_replicate_sets
    from review.domain.plot_order import apply_order
    from review.domain.selections_model import (
        color_for_replicate_set, color_for_well, replicate_display_label, well_display_label,
    )
    from review.domain.viewer_state import parse_rc

    paths = ctx.state.dataset.well_paths
    rsets = plot_replicate_sets(ctx)
    if rsets:
        rsets = apply_order(rsets, list(ctx.state.plot.line_order_rsets or []), key=lambda r: getattr(r, "name", ""))
        return [(replicate_display_label(ctx, r), tuple(w for w in r.wells if w in paths),
                 color_for_replicate_set(r, ctx.state.selection.selections)) for r in rsets]
    selected = sorted((w for w in ctx.state.selection.selected_wells if w in paths), key=parse_rc)
    selected = apply_order(selected, list(ctx.state.plot.line_order_wells or []), key=lambda x: x)
    return [(well_display_label(ctx, w), (w,), color_for_well(w)) for w in selected]


#: The labels of the nanowell metrics last joined onto cell rows, so an axis can name a ``nw::`` column without the
#: Nanowells feature (labels only; every value comes from the table itself).
_NANOWELL_LABELS: dict[str, str] = {}


def nanowell_cell_label(column: str) -> str:
    """The axis label of a ``nw::<key>`` column: ``Nanowell ▸ <metric label>``."""
    key = column[len(NANOWELL_CELL_PREFIX):]
    return f"Nanowell ▸ {_NANOWELL_LABELS.get(key, key)}"


def nanowell_cell_column(key: str) -> str:
    return f"{NANOWELL_CELL_PREFIX}{key}"


def with_nanowell_columns(ctx, df, well: str, columns):
    """*df* (one well's cell rows) with each ``nw::<key>`` of *columns* filled from the cell's own nanowell.

    A cell names its nanowell by ``(timepoint, nw_q, nw_r)``; the value is that nanowell's at the cell's timepoint,
    as the Nanowells feature computes it. A cell with no address, or in a build or dataset without nanowell
    observations, gets NaN — no value, never a borrowed one. *df* itself is not changed.
    """
    wanted = [c for c in columns if is_nanowell_cell_column(c)]
    if df is None or not wanted:
        return df
    # Asked for the plotted members' wells together, not one well at a time: one table, one memo entry, shared
    # by every well of the plot and by the pickers listing the metrics, instead of a read and a gating tally per
    # well per redraw that evicts its own memo on any plot of more than a few wells.
    plotted = [w for _l, ws, _c in observation_groups(ctx) for w in ws]
    table = nanowell_observations(ctx, sorted(set(plotted) | {well}) if well in plotted else [well])
    if table is not None:
        _NANOWELL_LABELS.update({m.key: m.label for m in table.metrics})
    return join_nanowell_columns(df, table.frame if table is not None else None, wanted, well=well)


#: The columns a nanowell row and a cell row share, which name the cell's own nanowell at its timepoint.
_ADDRESS = ("timepoint", "nw_q", "nw_r")


def join_nanowell_columns(df, frame, columns, *, well: str | None = None):
    """A copy of *df* (cell rows) with each ``nw::<key>`` of *columns* filled from *frame* (nanowell rows, the
    ``ObservationTable.frame`` shape: ``well``, ``timepoint``, ``nw_q``, ``nw_r`` and one column per metric key).

    Each cell takes its own nanowell's value at its timepoint. The well is *well* when given (every row of *df* is
    that well's), else each row's own ``well`` column. A cell with no address, a nanowell the frame does not hold,
    or no frame at all gives NaN: no value, never a borrowed one. Qt-free and ctx-free, so a gating pass can run it
    off the GUI thread on a frame computed before the pass began.
    """
    import numpy as np
    import pandas as pd

    wanted = [c for c in columns if is_nanowell_cell_column(c)]
    if df is None or not wanted:
        return df
    out = df.copy()
    for column in wanted:
        out[column] = np.nan
    if frame is None or frame.empty or not set(_ADDRESS) <= set(df.columns):
        return out
    if well is None and "well" not in df.columns:
        return out
    keys = [c[len(NANOWELL_CELL_PREFIX):] for c in wanted]
    present = [k for k in keys if k in frame.columns]
    sites = frame[["well", *_ADDRESS, *present]].copy()
    if well is not None:
        sites = sites[sites["well"] == well]
    left = out[list(_ADDRESS)].copy()
    left["well"] = well if well is not None else out["well"].astype(str)
    for side in (sites, left):
        side["timepoint"] = side["timepoint"].astype(str)
        for col in ("nw_q", "nw_r"):
            side[col] = pd.to_numeric(side[col], errors="coerce").astype(float)
    sites["well"] = sites["well"].astype(str)
    # One row per nanowell and timepoint, so a duplicate in the table can never duplicate a cell.
    sites = sites.drop_duplicates(["well", *_ADDRESS])
    merged = left.reset_index(drop=True).merge(sites, on=["well", *_ADDRESS], how="left")
    for column, key in zip(wanted, keys):
        if key in merged.columns:
            out[column] = merged[key].to_numpy(dtype=float)
    return out


def nanowell_gate_values(ctx, gates) -> pd.DataFrame | None:
    """The nanowell rows a gate drawn on a nanowell metric is applied to cells with, or ``None`` when no gate in
    *gates* has an ``nw::`` axis (or the build or dataset has no nanowell observations).

    Every well of the dataset, counted **without** the scatter-gate layer (``scatter_gates=False``): a nanowell's
    count is the cells the analysis includes, and the scatter gates are one of the layers that decide that, so a
    gate on "Nanowell ▸ count" judged against the gated count would move the count it was judging. Every other
    layer (curation, QC, the fluorescence gates) still applies, as it does on the plot. Computed on the calling
    thread, which must be the GUI thread: the Nanowells feature's memo is not shared across threads.
    """
    columns = {g.get(k) for g in gates or () for k in ("x_col", "y_col")}
    keys = sorted(c[len(NANOWELL_CELL_PREFIX):] for c in columns if is_nanowell_cell_column(c))
    if not keys or not is_nanowell_dataset(ctx):
        return None
    wells = sorted(getattr(ctx.state.dataset, "well_paths", {}) or {})
    table = ctx.features.query("observations.nanowell", ctx, wells, default=None, scatter_gates=False)
    if table is None:
        return None
    # Every metric, not only the gates' own: a pass that starts a moment after this was taken may apply a gate
    # drawn in between.
    return table.frame.copy()
