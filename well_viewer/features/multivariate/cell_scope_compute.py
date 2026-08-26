"""What the Multivariate tab is drawing, as a :class:`CellScope`.

This tab already had a per-cell export — the feature matrix — but it is the
matrix, not the cells: only the ticked feature columns, with NaN rows dropped
and, for t-SNE and UMAP, a subsample. ``Save cell data…`` is the whole row for
every cell in scope, with a marker saying what the analysis did with it.

``plot_in_matrix`` is the honest one: every ticked feature finite, which is the
condition ``collect_feature_matrix`` drops a row on.

**The projection subsample is recorded in the scope block, not as a marker.**
For t-SNE and UMAP the tab draws a seeded ``default_rng(0).choice`` over the
collector's row order (``controller.py:37-48``), so which cells were drawn is
reproducible only by reproducing that order, and the embedding coordinates are
not reproducible at all — UMAP is not even in the pinned test stack. A
``plot_in_projection`` column would therefore have to be read back off the last
draw, and a marker that can only sometimes be filled is worse than a sentence
saying the file holds the whole matrix while the figure drew a sample of it.
The note fires off ``sampled_note``, which is the key ``_compute_for_mode``
actually writes (``controller.py:240``).
"""

from __future__ import annotations

import numpy as np

from well_viewer.features.export.cell_scope_compute import CellScope, scope_from_ctx


def _in_matrix_marker(feature_cols: list[str]):
    """Rows whose every ticked feature is finite — the matrix's own row filter."""
    def _marker(df, scope):
        from well_viewer.data.data_loading import resolve_value_series
        keep = np.ones(len(df), dtype=bool)
        for col in feature_cols:
            try:
                values = resolve_value_series(df, col, scope.ratios).to_numpy()
            except (KeyError, ValueError, TypeError):
                return np.zeros(len(df), dtype=bool)
            with np.errstate(invalid="ignore"):
                keep &= np.isfinite(values)
        return keep
    return _marker


def cell_scope(ctx) -> CellScope | None:
    st = ctx.state.feature_slice("multivariate")
    if st is None:
        return None
    feature_cols = list(st.feature_cols or [])
    if not feature_cols:
        return None

    timepoints = tuple(float(t) for t in (st.timepoints or []))
    result = st.last_result if isinstance(st.last_result, dict) else {}
    subsampled = bool(result.get("sampled_note"))

    notes = [
        ("feature columns", ", ".join(feature_cols)),
        ("drawn statistic",
         "the analysis matrix is these feature columns over plot_in_matrix=1"),
    ]
    if subsampled:
        notes.append((
            "projection subsample",
            str(result.get("sampled_note"))
            + " — this file holds the whole matrix, and which cells the "
              "projection sampled is not reproducible from it",
        ))

    return scope_from_ctx(
        ctx,
        plot="multivariate",
        timepoints=timepoints,
        include_gate=True,
        tp_fallback=False,
        markers=(("plot_in_matrix", _in_matrix_marker(feature_cols)),),
        drawn_subset="plot_in_matrix=1",
        notes=tuple(notes),
    )
