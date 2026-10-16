"""The bar chart's per-cell columns over more cells than they can draw (tracker F3d).

The beeswarm and violin modes (``renderer.draw_beeswarm`` / ``draw_violin``) draw
one column per well or pooled replicate set at one timepoint. Each column used to
concatenate every well's whole frame (``renderer._pooled_rows``) and then draw
every above-threshold cell: a beeswarm point for each, and a KDE evaluated
against all of them. A column pooling a replicate set of 1.1M-cell wells is
millions of points, and its frame alone is gigabytes.

:func:`column_cells` gives a column its cells well by well, from narrow reads:

* a column whose wells' rows fit under :data:`BAR_CELL_MAX_POINTS` returns every
  above-threshold value, as before — the same values in the same order, so the
  figure is unchanged;
* a larger one pools them (``review/data/cell_pool.py``) and returns a sample of
  :data:`BAR_CELL_MAX_POINTS` to draw, while :class:`ColumnCells` answers the
  column's fraction, mean and median over **every** cell.

The fold-change scaling the renderers apply to a column's values is affine (a
subtraction or a division by a constant, ``fold_change.apply_cell_scale``), so a
sampled column's mean is taken over every scaled value a chunk at a time, and its
median is the scaled median of every raw value. Both agree with numpy over the
whole column to rounding (a relative 1e-12 in ``tests/test_bar_cells_draw_a_stated_sample.py``),
not bit for bit: the mean is summed in chunks, not pairwise.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from domain.text_columns import timepoint_text
from domain.tp_match import tp_close_mask
from review.data.cell_pool import CellPool, cells_upper_bound
from review.data.data_loading import df_included_mask, get_rows, parse_timepoint_hours, resolve_value_series
from review.data.narrow_rows import narrow_rows, value_inputs
from review.domain import fold_change as _fc

#: The most cells a column draws. A beeswarm of 5,000 points per column already
#: packs into a solid shape, and a violin's KDE over 5,000 values on its 200-point
#: grid is a million kernel evaluations.
BAR_CELL_MAX_POINTS = 5_000

#: Above this many rows a column's cells are pooled in DuckDB, under its memory
#: limit; at or below it, in numpy. A numpy pool keeps six arrays per row (the
#: value and its sequence, group, well, row and timepoint), measured at about
#: 38 MiB per million rows, so the renderers hold one column's pool at a time
#: (:func:`release_previous`).
BAR_CELL_DUCKDB_ROWS = 1_000_000


@dataclass
class ColumnCells:
    """One column's cells at one timepoint. See the module docstring."""

    #: Included cells with a finite value at the timepoint — the fraction's denominator.
    n_total: int
    #: Of those, the ones above the threshold — the fraction's numerator.
    n_above: int
    #: The above-threshold values the column draws: every one, or a sample.
    vals: np.ndarray
    #: Every above-threshold value, when :attr:`vals` is a sample; the caller closes it.
    pool: CellPool | None = None

    @property
    def sampled(self) -> bool:
        return self.pool is not None and self.vals.size < self.n_above

    def mean(self, spec=None) -> float:
        """The mean of every above-threshold value, scaled by *spec*, ignoring non-finite results."""
        if self.pool is None:
            vals = _scaled(self.vals, spec)
            return float(vals.mean()) if vals.size else float("nan")
        total, count = 0.0, 0
        for chunk in self.pool.scan(["v"]):
            vals = _scaled(chunk["v"], spec)
            total += float(vals.sum())
            count += int(vals.size)
        return total / count if count else float("nan")

    def median(self, spec=None) -> float:
        """The median of every above-threshold value, scaled by *spec*."""
        if self.pool is None:
            vals = _scaled(self.vals, spec)
            return float(np.median(vals)) if vals.size else float("nan")
        if self.n_above == 0:
            return float("nan")
        raw = self.pool.quantiles("v", [0.5])
        scaled = _scaled(raw, spec)
        return float(scaled[0]) if scaled.size else float("nan")

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()


def _scaled(vals, spec) -> np.ndarray:
    vals = np.asarray(vals, dtype=float)
    if spec is None:
        return vals
    out = _fc.apply_cell_scale(vals, spec)
    return out[np.isfinite(out)]


def _timepoint_hours(df) -> np.ndarray:
    """Numeric timepoint-in-hours per row, falling back to the text column.

    ``timepoint_hours`` is the parsed column and is present on anything the
    current pipeline wrote, but older CSVs — and any row whose parse failed —
    carry only the text ``timepoint``. The beeswarm renderer had this fallback
    and the violin renderer did not: it required ``timepoint_hours`` outright
    and drew nothing without it, so the same well showed a populated beeswarm
    and an empty violin, one toggle apart, with nothing saying why.
    """
    if "timepoint_hours" in df.columns:
        tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").to_numpy(dtype=float)
    else:
        tp = np.full(len(df), np.nan)
    need = ~np.isfinite(tp)
    if need.any() and "timepoint" in df.columns:
        fallback = timepoint_text(df.loc[need]).map(parse_timepoint_hours)
        tp[need] = fallback.fillna(np.nan).to_numpy(dtype=float)
    return tp


def _well_values(df, val_col: str, ratios, col_tp: float, threshold: float) -> tuple[int, np.ndarray]:
    """``(n_total, above-threshold values)`` for one well's frame — the renderers' own filter."""
    if df is None or df.empty:
        return 0, np.empty(0, dtype=float)
    mask = df_included_mask(df).to_numpy(copy=True)
    tp = _timepoint_hours(df)
    with np.errstate(invalid="ignore"):
        mask &= np.isfinite(tp) & tp_close_mask(tp, col_tp)
    v = resolve_value_series(df, val_col, ratios).to_numpy()
    mask &= np.isfinite(v)
    v_sel = v[mask]
    return int(v_sel.size), v_sel[v_sel > threshold]


def column_cells(ctx, lbl: str, pool_groups, col_tp: float, threshold: float) -> ColumnCells:
    """Column *lbl*'s cells at *col_tp*: every one under the cap, a stated sample above it."""
    wells = list(pool_groups[lbl]) if pool_groups is not None and lbl in pool_groups else [lbl]
    val_col = ctx.state.channel.active_val_col
    ratios = ctx.state.channel.ratio_index
    inputs = value_inputs(val_col, ratios)
    want = None if inputs is None else inputs | {"timepoint_hours"}
    rows = cells_upper_bound(ctx, wells)
    # A column is pooled once it may hold more cells than it draws, but only a
    # very large one is worth a DuckDB connection and a spill directory: an
    # ordinary well of tens of thousands of cells pools in numpy, which costs
    # nothing to open. Measured on 96 columns of 20,000 cells: 4.8 s and 574 MB
    # with a DuckDB pool each.
    if rows <= BAR_CELL_MAX_POINTS:
        pool = None
    else:
        pool = CellPool(["v"], backend="numpy" if rows <= BAR_CELL_DUCKDB_ROWS else None)
    n_total, parts = 0, []
    try:
        for well in wells:
            df = narrow_rows(ctx, well, want) if want is not None else get_rows(ctx, well)
            n, above = _well_values(df, val_col, ratios, col_tp, threshold)
            n_total += n
            if pool is not None:
                pool.add(lbl, well, np.arange(above.size), np.zeros(above.size), {"v": above})
            else:
                parts.append(above)
        if pool is None:
            vals = np.concatenate(parts) if parts else np.empty(0, dtype=float)
            return ColumnCells(n_total=n_total, n_above=int(vals.size), vals=vals)
        n_above = pool.count(lbl)
        drawn = pool.sample(BAR_CELL_MAX_POINTS, per_group=True).get(lbl)
    except BaseException:
        if pool is not None:
            pool.close()
        raise
    vals = drawn.values["v"] if drawn is not None else np.empty(0, dtype=float)
    return ColumnCells(n_total=n_total, n_above=n_above, vals=vals, pool=pool)


def sampled_note(columns) -> str:
    """The sentence a figure with a sampled column carries, or ``""``."""
    sampled = [c for c in columns if c.sampled]
    if not sampled:
        return ""
    return (f"Columns over {BAR_CELL_MAX_POINTS:,} cells draw a sample of {BAR_CELL_MAX_POINTS:,} "
            f"({len(sampled)} of {len(columns)}); fractions, means and medians use every cell")


#: Tag on the sampled-figure note, so a test (and a redraw) can find it.
SAMPLED_NOTE_TAG = "_aw_bar_cells_sampled_note"


def release_previous(columns) -> None:
    """Close the last column's pool before the next column is read.

    A column's pool is only read inside its own iteration (its mean or median),
    so holding every column's until the figure is finished would cost the sum of
    them: 96 columns of just under a million cells would be about 3.6 GiB of
    numpy. :func:`finish_columns` still closes the last, and a closed pool's
    column still says it was sampled.
    """
    if columns:
        columns[-1].close()


def finish_columns(ax, columns) -> None:
    """Say on *ax* which columns were drawn from a sample, then release their pools."""
    try:
        text = sampled_note(columns)
        if text and ax is not None:
            from review.core.plot_style import follows_theme, tokens_for
            note = follows_theme(ax.text(0.01, 0.01, text, transform=ax.transAxes, ha="left", va="bottom",
                                         fontsize=7, color=tokens_for(ax)[2], zorder=6, clip_on=False), muted=True)
            setattr(note, SAMPLED_NOTE_TAG, True)
    finally:
        for column in columns:
            column.close()
