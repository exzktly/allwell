"""Multivariate tab config persistence — ``multivariate`` section of
``persistence.json``.

Stores only the user's configuration (selected features, mode, method, cluster
count, colour-by, facet axes, timepoints, standardize flag) — never the
transient computed result. Fully tolerant of the feature being disabled/removed:
it reads/writes through ``AppState.feature_slice`` and no-ops when the slice is
absent, and imports no feature module.
"""

from __future__ import annotations

from review.persistence import _doc


def save_to_data_dir(ctx) -> None:
    if not ctx.state.dataset.data_dir:
        return
    st = ctx.state.feature_slice("multivariate")
    if st is None:
        return
    _doc.set_section(ctx, "multivariate", st.to_config())


def load_from_data_dir(ctx) -> None:
    if not ctx.state.dataset.data_dir:
        return
    st = ctx.state.feature_slice("multivariate")
    if st is None:
        return
    data = _doc.get_section(ctx, "multivariate")
    if isinstance(data, dict):
        st.apply_config(data)
    else:
        # No saved configuration for this plate means the tab starts fresh, not
        # that it keeps the previous plate's axes and channel picks — those name
        # columns this dataset need not have.
        from review.persistence.reset_scopes import restore_slice_defaults
        restore_slice_defaults(st, "multivariate")
