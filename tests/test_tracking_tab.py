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


def test_track_plots_subtab(viewer_app, monkeypatch):
    app = _build_tab(viewer_app)
    from well_viewer.features.tracking import tab_view

    def _df(well, base):
        rows = []
        for tp, a, b in (("0", base, base + 50), ("24h", base + 10, base + 60)):
            rows.append(dict(well=well, fov="F001", timepoint=tp, nucleus_id=1,
                             shape_centroid_row=1, shape_centroid_col=1,
                             gfp_mean_intensity=a, track_id=1, track_parent_id=-1, tree_id=1))
            rows.append(dict(well=well, fov="F001", timepoint=tp, nucleus_id=2,
                             shape_centroid_row=2, shape_centroid_col=2,
                             gfp_mean_intensity=b, track_id=2, track_parent_id=-1, tree_id=2))
        return pd.DataFrame(rows)

    dfs = {"A01": _df("A01", 100), "A02": _df("A02", 300)}
    monkeypatch.setattr(app, "_get_rows", lambda w: dfs[w], raising=False)
    app._app_state.dataset.well_paths = {"A01": "a.csv", "A02": "b.csv"}
    app._app_state.selection.selected_wells = {"A01", "A02"}

    tab_view._refresh_track_plots(app)
    # metric combo populated from the data; gfp metric available
    metrics = [app._tracking_tp_metric_cb.itemText(i)
               for i in range(app._tracking_tp_metric_cb.count())]
    assert "gfp_mean_intensity" in metrics
    # both wells plotted; two distinct legend colours (one per well) + averages
    ax = app._tracking_tp_ax
    labels = [t.get_text() for t in ax.get_legend().get_texts()]
    assert any("A01" in l for l in labels) and any("A02" in l for l in labels)
    line_colors = {ln.get_color() for ln in ax.get_lines() if ln.get_xdata() is not None}
    assert len(line_colors) >= 2          # at least one colour per well
    assert "2 well(s)" in app._tracking_tp_summary.text()
    assert "4 track(s)" in app._tracking_tp_summary.text()

    # toggling the average off still plots (individual tracks only)
    app._tracking_tp_avg_cb.setChecked(False)
    tab_view._refresh_track_plots(app)
    assert "individual tracks only" in app._tracking_tp_summary.text()
