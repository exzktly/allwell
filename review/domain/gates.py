"""Whether a cell is inside a drawn gate: the one predicate every surface uses.

A gate is a rectangle the user drew on the per-cell scatter, stamped with the
two metric columns it was drawn over (``x_col`` / ``y_col``). Three places ask
which cells it holds: the Scatter tab (its *Apply gates* filter and the CSV
export's ``plot_in_gate`` marker), Cell Gating's pass (``Gate_Pass`` and the
thresholding layer's ``Gate_On``), and the Cell Browser's gate source. They ask
here, so a cell cannot be inside a gate on one page and outside it on another.

The predicate lives below the features because two of its three users are not
the Scatter tab, and one of them — Cell Gating's projector — is applied to every
well the app loads whether or not the Scatter tab is in the build.

**Nanowell axes.** A gate drawn on a nanowell metric names ``nw::<key>``
columns that no cell table holds; each cell reads its own nanowell's value at
its timepoint. :func:`compute_gate_inclusion` therefore takes the nanowell rows
as a required keyword: a caller states either the rows to join or ``None``
because its rows already carry the columns. Leaving the argument out is a
``TypeError`` rather than a gate that silently counts nothing.
"""

from __future__ import annotations

import numpy as np


def norm_gate(g: dict[str, float]) -> dict[str, float]:
    """A copy of *g* with ``x0 <= x1`` and ``y0 <= y1`` (a drag may flip them)."""
    x0, x1 = sorted((g["x0"], g["x1"]))
    y0, y1 = sorted((g["y0"], g["y1"]))
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def in_gate(xs: np.ndarray, ys: np.ndarray, gate: dict[str, float]) -> np.ndarray:
    """Which points lie inside a normalised *gate*; the edges are inclusive."""
    return (
        (xs >= gate["x0"]) & (xs <= gate["x1"])
        & (ys >= gate["y0"]) & (ys <= gate["y1"])
    )


def compute_gate_inclusion(df, gates, ratios=None, *, nanowell_values) -> np.ndarray:
    """Boolean array over *df*'s rows: ``True`` for a cell inside ANY of *gates*.

    Each gate's values are resolved through
    :func:`review.data.data_loading.resolve_value_series`, so ratio and shape
    columns work exactly as on the plot. A gate missing its columns, or whose
    columns are absent from *df*, resolves to all-NaN and contributes nothing
    (NaN comparisons are False). With no usable gates the mask is all-``False``;
    the caller decides what "no gates" means.

    *nanowell_values* (``observations.nanowell_gate_values``) are the nanowell
    rows a gate on an ``nw::`` column reads, joined onto a copy of *df* by each
    cell's nanowell and timepoint (``join_nanowell_columns``). Pass ``None`` only
    when *df* already carries those columns or no gate names one.
    """
    from review.data.data_loading import resolve_value_series
    from review.data.observations import join_nanowell_columns

    n = len(df)
    inside = np.zeros(n, dtype=bool)
    if nanowell_values is not None:
        wanted = [c for g in gates or [] for c in (g.get("x_col"), g.get("y_col"))
                  if c and c not in df.columns]
        df = join_nanowell_columns(df, nanowell_values, wanted)
    for gate in gates or []:
        x_col = gate.get("x_col")
        y_col = gate.get("y_col")
        if not x_col or not y_col:
            continue
        xs = resolve_value_series(df, x_col, ratios).to_numpy()
        ys = resolve_value_series(df, y_col, ratios).to_numpy()
        inside |= in_gate(xs, ys, norm_gate(gate))
    return inside
