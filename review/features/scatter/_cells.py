"""One well's per-cell scatter cloud — the one definition of a scatter cell.

Shared by :func:`~review.features.scatter.collect.collect_scatter_data` and its
pooled path (``scatter/_pooled.py``), so the cells a large selection samples
from are exactly the cells a small one draws. Its own module so both can import
it at module scope: ``collect`` imports ``_pooled``, and ``_pooled`` needs this.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from domain.tp_match import tp_close_mask
from review.data.data_loading import df_included_mask, resolve_value_series
from review.domain.metric_registry import fluor_floor_judges_column
# The one gate predicate every surface uses, so "inside a gate" means the
# same thing here, in Cell Gating and in the Cell Browser.
from review.domain.gates import compute_gate_inclusion


def well_cloud(df, *, col_x: str, col_y: str, target_tps, fluor_gate_x: float, fluor_gate_y: float,
               gates=None, ratios=None) -> tuple | None:
    """One well's per-cell scatter, filtered: ``(df, x, y, tp, mask, ungated, gated)`` or ``None``.

    *mask* is the cells drawn, *ungated* the cells before the tab's gate filter
    (the gate percentages' denominator) and *gated* whether a gate filter was
    applied at all. ``None`` when the well has nothing to contribute — no rows,
    no timepoint or area column, or no cell before the gate filter.

    See the module docstring.
    """
    if df is None or df.empty:
        return None
    if "timepoint_hours" not in df.columns or "area_px" not in df.columns:
        return None
    # The Gate_Pass layer stays off here: the per-cell scatter is where the
    # gates are drawn, so it shows the full cloud with the boxes over it
    # while every other view filters via df_included_mask. The tab's
    # "Plot only the gated cells" toggle filters through ``gates`` below instead — against
    # the boxes on screen, not against a column another tab's worker wrote.
    mask = df_included_mask(df, include_gate=False).to_numpy(copy=True)
    # Two masks when the tab is filtering by gate geometry: the drawn one,
    # and the one the gate percentages have to be denominated by. "42% of
    # well A fell in this gate" is a statement about the cloud the gate was
    # drawn over — filter first and every gate reads 100.0%, which is what
    # "Plot only the gated cells" used to do to its own labels.
    # ``nanowell_values=None``: both callers hand this function rows already
    # joined with the plotted axes' nanowell columns (``with_nanowell_columns``),
    # and the gates it is given are those same axes' (``gates_for_axes``).
    gate_mask = compute_gate_inclusion(df, gates, ratios, nanowell_values=None) if gates else None
    target_arr = np.asarray(target_tps, dtype=float)
    tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").to_numpy()
    with np.errstate(invalid="ignore"):
        finite = np.isfinite(tp)
        if target_arr.size == 0:
            mask &= False
        else:
            # tp ≈ any selected target within tolerance.
            tp_match = np.zeros_like(tp, dtype=bool)
            for t in target_arr:
                tp_match |= tp_close_mask(tp, t)
            mask &= finite & tp_match
    area = pd.to_numeric(df["area_px"], errors="coerce").to_numpy()
    with np.errstate(invalid="ignore"):
        mask &= np.isfinite(area)
    x = resolve_value_series(df, col_x, ratios).to_numpy()
    y = resolve_value_series(df, col_y, ratios).to_numpy()
    # A channel's floor judges an axis only when it is on and the axis is the
    # column the floor is defined on (the channel's mean intensity) — the
    # metric registry owns that rule, and with it the bypass for smFISH counts
    # and shape features. A floor of 0.0 is "off", not "above zero": applying
    # it dropped every cell at or below zero on a background-subtracted
    # channel, here and nowhere else in the app.
    gate_x_mask = (x > fluor_gate_x) if fluor_floor_judges_column(col_x, fluor_gate_x) else np.ones_like(x, dtype=bool)
    gate_y_mask = (y > fluor_gate_y) if fluor_floor_judges_column(col_y, fluor_gate_y) else np.ones_like(y, dtype=bool)
    mask &= np.isfinite(x) & np.isfinite(y) & gate_x_mask & gate_y_mask
    ungated = mask
    if gate_mask is not None:
        mask = mask & gate_mask
    # Test the *pre-gate* population for emptiness. Returning early on an
    # empty gated mask dropped the whole well, and with it the cells that
    # belong in the gate percentage's denominator — so a set of two wells
    # where one had 100 of 1000 cells inside the gate and the other had
    # none read 10.0% instead of 5.0%. That is the exact number this
    # denominator exists to produce.
    if not ungated.any():
        return None
    return df, x, y, tp, mask, ungated, gate_mask is not None
