"""Multivariate tab config persistence — ``multivariate`` section of
``persistence.json``.

Stores only the user's configuration (selected features, mode, method, cluster
count, colour-by, facet axes, timepoints, standardize flag) — never the
transient computed result. Fully tolerant of the feature being disabled/removed:
it reads/writes through ``AppState.feature_slice`` and no-ops when the slice is
absent, and imports no feature module.
"""

from __future__ import annotations

from well_viewer.persistence import _doc


def save_to_data_dir(app) -> None:
    if not app._app_state.dataset.data_dir:
        return
    st = app._app_state.feature_slice("multivariate")
    if st is None:
        return
    _doc.set_section(app, "multivariate", st.to_config())


def load_from_data_dir(app) -> None:
    if not app._app_state.dataset.data_dir:
        return
    st = app._app_state.feature_slice("multivariate")
    if st is None:
        return
    data = _doc.get_section(app, "multivariate")
    if isinstance(data, dict):
        st.apply_config(data)
