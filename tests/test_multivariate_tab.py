"""Full-app smoke test for the Multivariate tab.

Boots the real ``WellViewerApp`` with a shape-feature dataset, opens the
Multivariate tab (lazy build + activation), selects a feature set, and drives
every one of the four analysis modes end-to-end — asserting each produces a
result with no error. This exercises the Qt tab builder, the collect→compute→
render pipeline, and the registry wiring together.
"""

from __future__ import annotations

import pytest


@pytest.mark.slow
def test_multivariate_tab_runs_all_modes(loaded_app_shape):
    app = loaded_app_shape
    from well_viewer.features.multivariate import controller as mvctl
    from well_viewer.features.multivariate.collect import available_feature_columns

    # Select every loaded well so the matrix has plenty of cells.
    app._set_selected_wells(set(app._app_state.dataset.well_paths))

    # Open the tab — lazy build + on_activate wiring.
    assert app._notebook.setCurrentByName("Multivariate")
    assert hasattr(app, "_mv_canvas"), "tab did not build"

    # Pick a mixed feature set (intensity + shape) discovered from the dataset.
    fams = available_feature_columns(app)
    cols = fams.get("Intensity", [])[:2] + fams.get("Shape", [])[:1]
    assert len(cols) >= 2, f"expected >=2 discoverable features, got {fams}"
    mvctl.mv_set_features(app, cols)

    st = app._app_state.feature_slice("multivariate")
    assert st.n_cells > 0

    for mode in ("correlation", "dimred", "clustering", "facet"):
        mvctl.mv_set_mode(app, mode)
        assert not st.last_error, f"{mode}: {st.last_error}"
        assert st.last_result.get("kind") == mode

    # SPLOM view path.
    mvctl.mv_set_mode(app, "correlation")
    mvctl.mv_set_corr_view(app, "splom")
    assert not st.last_error
    assert st.last_result.get("view") == "splom"

    # Clustering colour-by on the dim-reduction view.
    mvctl.mv_set_mode(app, "dimred")
    mvctl.mv_set_color_by(app, "cluster")
    assert not st.last_error
    assert "clusters" in st.last_result


@pytest.mark.slow
def test_multivariate_empty_selection_shows_message(loaded_app_shape):
    app = loaded_app_shape
    from well_viewer.features.multivariate import controller as mvctl

    app._set_selected_wells(set())
    assert app._notebook.setCurrentByName("Multivariate")
    mvctl.mv_set_features(app, [])
    st = app._app_state.feature_slice("multivariate")
    # No features and/or no cells → a friendly error, not a crash.
    assert st.last_error
    assert not st.last_result
