"""Review Tracking tab (§D / PR5b): build, viewer, and run-guard wiring."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytestmark = pytest.mark.qt
pd = pytest.importorskip("pandas")


def _build_tab(app):
    """Force the deferred Tracking page to build, then return app."""
    assert app._notebook.setCurrentByName("Tracking")
    if "Tracking" in getattr(app, "_centre_pending_builders", {}):
        app._centre_build_pending("Tracking")
    return app


def test_tracking_tab_builds(viewer_app):
    app = _build_tab(viewer_app)
    assert app._tracking_engine_cb is not None
    assert app._tracking_run_btn is not None
    assert app._tracking_viewer_canvas is not None
    engines = [app._tracking_engine_cb.itemData(i)
               for i in range(app._tracking_engine_cb.count())]
    assert "greedy" in engines


def test_tracking_viewer_empty_then_tracks(viewer_app, monkeypatch):
    app = _build_tab(viewer_app)
    from well_viewer.features.tracking import tab_view

    # No data → empty-state.
    tab_view._refresh_viewer(app)
    assert "No tracks" in app._tracking_viewer_summary.text()

    # Inject a tracked well → the viewer summarises the track count.
    df = pd.DataFrame({
        "well": ["A01"] * 4, "fov": ["F001"] * 4,
        "timepoint": ["0", "0", "24h", "24h"], "nucleus_id": [1, 2, 1, 2],
        "shape_centroid_row": [10, 50, 12, 52],
        "shape_centroid_col": [10, 50, 12, 52],
        "track_id": [1, 2, 1, 2],
    })
    monkeypatch.setattr(tab_view, "_active_well", lambda _app: "A01")
    monkeypatch.setattr(app, "_get_rows", lambda w: df, raising=False)
    tab_view._refresh_viewer(app)
    assert "2 track(s)" in app._tracking_viewer_summary.text()


def test_run_tracking_requires_dataset(viewer_app):
    app = _build_tab(viewer_app)
    from well_viewer.features.tracking import tab_view
    app._app_state.dataset.data_dir = None
    tab_view._run_tracking(app)
    assert "dataset" in app._tracking_status_lbl.text().lower()
