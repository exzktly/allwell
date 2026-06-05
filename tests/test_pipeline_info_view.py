"""Pipeline Info sub-tab — reads pipeline_info.json and surfaces the recorded
background method (the run's receipt)."""

from __future__ import annotations

import json
import types

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _stub_app(data_dir):
    ds = types.SimpleNamespace(data_dir=data_dir, pipeline_info={})
    return types.SimpleNamespace(_app_state=types.SimpleNamespace(dataset=ds))


def _rows(tree):
    out = []
    root = tree.invisibleRootItem()

    def walk(it):
        for i in range(it.childCount()):
            c = it.child(i)
            out.append((c.text(0), c.text(1)))
            walk(c)
    walk(root)
    return out


def test_pipeline_info_shows_background_method(qapp, tmp_path):
    (tmp_path / "pipeline_info.json").write_text(json.dumps({
        "schema_version": 2, "nuclear_token": "nir",
        "fluor_tokens": ["gfp", "mcherry"],
        "segmentation_method": "stardist_nuclei", "min_nucleus_area_px": 50,
        "shape_features": ["area"],
        "qc_options": {"flatfield": "none", "background": "sliding_disk",
                       "qc_remove_debris": True},
        "available_timepoints": ["24h"], "available_fovs": ["F001", "F002"],
    }))
    from well_viewer.features.grouping.pipeline_info_view import build_pipeline_info_subtab
    app = _stub_app(tmp_path)
    parent = QWidget()
    build_pipeline_info_subtab(app, parent)
    rows = _rows(app._pipeline_info_tree)
    assert ("Background method", "sliding_disk") in rows
    assert ("Fluorescent", "gfp, mcherry") in rows
    assert ("Method", "stardist_nuclei") in rows
    # the same call wired a refresh hook the sub-tab switch uses
    assert callable(app._pipeline_info_refresh)


def test_pipeline_info_reports_top_hat_run(qapp, tmp_path):
    # the whole point: a run that used the square method says so, plainly
    (tmp_path / "pipeline_info.json").write_text(json.dumps({
        "qc_options": {"background": "top_hat"}}))
    from well_viewer.features.grouping.pipeline_info_view import build_pipeline_info_subtab
    app = _stub_app(tmp_path)
    parent = QWidget()
    build_pipeline_info_subtab(app, parent)
    assert ("Background method", "top_hat") in _rows(app._pipeline_info_tree)


def test_pipeline_info_no_dataset(qapp):
    from well_viewer.features.grouping.pipeline_info_view import build_pipeline_info_subtab
    app = _stub_app(None)
    parent = QWidget()
    build_pipeline_info_subtab(app, parent)          # must not raise
    rows = _rows(app._pipeline_info_tree)
    assert any("no dataset" in k.lower() for k, _ in rows)
