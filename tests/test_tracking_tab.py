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


def test_tracking_subtabs(viewer_app, monkeypatch):
    app = _build_tab(viewer_app)
    from well_viewer.features.tracking import tab_view
    df = pd.DataFrame({
        "well": ["A01"] * 5, "fov": ["F001"] * 5,
        "timepoint": ["0", "0", "24h", "24h", "0"], "nucleus_id": [1, 2, 1, 2, 3],
        "shape_centroid_row": [10, 50, 12, 52, 80],
        "shape_centroid_col": [10, 50, 12, 52, 80],
        "track_id": [1, 2, 1, 2, 3], "track_parent_id": [-1, 1, -1, 1, -1],
        "tree_id": [1, 1, 1, 1, 3],
    })
    monkeypatch.setattr(tab_view, "_active_well", lambda _a: "A01")
    monkeypatch.setattr(app, "_get_rows", lambda w: df, raising=False)

    # Lineage view summarises tracks + division links.
    tab_view._refresh_lineage(app)
    assert "track(s)" in app._tracking_lineage_summary.text()
    assert "division" in app._tracking_lineage_summary.text()

    # Editor operates on the active well's df (relabel via the cached editor).
    ed = tab_view._editor_for(app, "A01")
    assert ed.relabel(1, 9) == 2
    assert set(df[df["nucleus_id"] == 1]["track_id"]) == {9}

    # Pruning preview reports what the rules would drop (1-frame track 3).
    app._tracking_prune_min_frames.setText("2")
    app._tracking_prune_min_disp.setText("0")
    tab_view._prune(app, apply_now=False)
    assert "Would drop" in app._tracking_prune_status.text()


def test_tracking_analytics_subtab(viewer_app, monkeypatch, tmp_path):
    app = _build_tab(viewer_app)
    from well_viewer.features.tracking import tab_view
    df = pd.DataFrame({
        "well": ["A01"] * 4, "fov": ["F001"] * 4,
        "timepoint": ["0", "1", "2", "3"], "nucleus_id": [1, 1, 1, 1],
        "shape_centroid_row": [10, 12, 14, 16], "shape_centroid_col": [10, 10, 10, 10],
        "track_id": [1, 1, 1, 1], "track_parent_id": [-1] * 4, "tree_id": [1] * 4,
    })
    monkeypatch.setattr(tab_view, "_active_well", lambda _a: "A01")
    monkeypatch.setattr(app, "_get_rows", lambda w: df, raising=False)

    tab_view._refresh_analytics(app)
    assert "track(s)" in app._tracking_analytics_summary.text()
    assert "directionality" in app._tracking_analytics_summary.text()

    # Export via the handler (file dialog stubbed to a temp path).
    out = tmp_path / "A01_motility.csv"
    monkeypatch.setattr(tab_view.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "CSV (*.csv)")))
    tab_view._export(app, "motility")
    assert out.exists()
    assert "Exported" in app._tracking_analytics_summary.text()
