"""Full-app smoke test for the Multivariate tab.

Boots the real ``ReviewApp`` with a shape-feature dataset, opens the
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
    from review.features.multivariate import controller as mvctl
    from review.features.multivariate.collect import available_feature_columns

    # Select every loaded well so the matrix has plenty of cells.
    app._set_selected_wells(set(app._app_state.dataset.well_paths))

    # Open the tab — lazy build + on_activate wiring.
    assert app._notebook.setCurrentByName("Multivariate")
    assert hasattr(app, "_mv_canvas"), "tab did not build"

    # Pick a mixed feature set (intensity + shape) discovered from the dataset.
    fams = available_feature_columns(app._ctx)
    cols = fams.get("Intensity", [])[:2] + fams.get("Shape", [])[:1]
    assert len(cols) >= 2, f"expected >=2 discoverable features, got {fams}"
    mvctl.mv_set_features(app._ctx, cols)

    st = app._app_state.feature_slice("multivariate")
    assert st.n_cells > 0

    for mode in ("correlation", "dimred", "clustering", "facet"):
        mvctl.mv_set_mode(app._ctx, mode)
        assert not st.last_error, f"{mode}: {st.last_error}"
        assert st.last_result.get("kind") == mode

    # SPLOM view path.
    mvctl.mv_set_mode(app._ctx, "correlation")
    mvctl.mv_set_corr_view(app._ctx, "splom")
    assert not st.last_error
    assert st.last_result.get("view") == "splom"

    # Clustering colour-by on the dim-reduction view.
    mvctl.mv_set_mode(app._ctx, "dimred")
    mvctl.mv_set_color_by(app._ctx, "cluster")
    assert not st.last_error
    assert "clusters" in st.last_result


@pytest.mark.slow
def test_multivariate_dimred_pca_exposes_loadings(loaded_app_shape):
    """PCA dim-red must attach loadings (feature weights per PC) to the result so
    the renderer can draw the loadings panel and the export can write them."""
    app = loaded_app_shape
    from review.features.multivariate import controller as mvctl
    from review.features.multivariate.collect import available_feature_columns

    app._set_selected_wells(set(app._app_state.dataset.well_paths))
    assert app._notebook.setCurrentByName("Multivariate")
    fams = available_feature_columns(app._ctx)
    cols = fams.get("Intensity", [])[:2] + fams.get("Shape", [])[:1]
    assert len(cols) >= 2
    mvctl.mv_set_features(app._ctx, cols)
    mvctl.mv_set_mode(app._ctx, "dimred")
    mvctl.mv_set_dimred_method(app._ctx, "pca")

    st = app._app_state.feature_slice("multivariate")
    assert not st.last_error
    ld = st.last_result.get("loadings")
    assert ld is not None
    assert ld["components"].shape == (2, len(cols))
    assert len(ld["feature_labels"]) == len(cols)


@pytest.mark.slow
def test_multivariate_clustering_exposes_feature_profile(loaded_app_shape):
    """Clustering must attach a per-cluster feature profile (the interpretable
    'what is each cluster' table) so the renderer + export can surface it."""
    app = loaded_app_shape
    from review.features.multivariate import controller as mvctl
    from review.features.multivariate.collect import available_feature_columns

    app._set_selected_wells(set(app._app_state.dataset.well_paths))
    assert app._notebook.setCurrentByName("Multivariate")
    fams = available_feature_columns(app._ctx)
    cols = fams.get("Intensity", [])[:2] + fams.get("Shape", [])[:1]
    mvctl.mv_set_features(app._ctx, cols)
    mvctl.mv_set_mode(app._ctx, "clustering")

    st = app._app_state.feature_slice("multivariate")
    assert not st.last_error
    prof = st.last_result.get("profile")
    assert prof is not None
    assert prof["matrix"].shape == (st.cluster_count, len(cols))
    assert len(prof["feature_labels"]) == len(cols)


@pytest.mark.slow
def test_multivariate_tab_flips_off_empty_state_with_dataset(loaded_app_shape):
    """The lazily-built tab must recognise the already-open dataset.

    This used to assert every registered stack sat on page 1. That stopped being
    the right question once the placeholder learned to say *which* nothing it is
    showing: ``_load_path`` clears the well selection, so a selection-gated stack
    correctly sits on page 0 here saying "No wells selected." What the tab must
    never do — the thing this test has always been about — is ask for a dataset
    that is already loaded, so that is what it checks now.
    """
    from PySide6.QtWidgets import QLabel

    app = loaded_app_shape
    assert app._app_state.dataset.well_paths          # dataset is loaded
    assert app._notebook.setCurrentByName("Multivariate")

    stacks = getattr(app, "_plot_empty_stacks", []) or []
    assert stacks
    shown = [
        lbl.text()
        for stack in stacks if stack.currentIndex() == 0
        for lbl in stack.widget(0).findChildren(QLabel) if lbl.text()
    ]
    assert "Load a directory." not in shown, shown


@pytest.mark.slow
def test_multivariate_k_spinbox_has_room_for_value(loaded_app_shape):
    """Regression: the cluster ``k`` spinbox sits in a crowded horizontal row and
    used to be squeezed until its value clipped. Pin a minimum width that leaves
    room for two digits + the up/down arrows."""
    app = loaded_app_shape
    assert app._notebook.setCurrentByName("Multivariate")
    kspin = app._mv_kspin
    assert kspin.minimumWidth() >= 64
    # The natural size the widget asks for must fit inside what it's guaranteed.
    assert kspin.sizeHint().width() <= kspin.minimumWidth() + 40


@pytest.mark.slow
def test_multivariate_empty_selection_shows_message(loaded_app_shape):
    app = loaded_app_shape
    from review.features.multivariate import controller as mvctl

    app._set_selected_wells(set())
    assert app._notebook.setCurrentByName("Multivariate")
    mvctl.mv_set_features(app._ctx, [])
    st = app._app_state.feature_slice("multivariate")
    # No features and/or no cells → a friendly error, not a crash.
    assert st.last_error
    assert not st.last_result
