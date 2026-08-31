"""Segmentation-method redesign (§A): dropdown set, per-method parameter panel,
general Cellpose params + their CLI mapping, and `_segment_cellpose` honoring
model/diameter/thresholds/nucleus-channel. Runs headless (offscreen Qt)."""

from __future__ import annotations

import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import numpy as np
import pytest

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _set_method(tab, value: str) -> None:
    idx = tab._seg_method_cb.findData(value)
    assert idx >= 0, f"method {value!r} not in combo"
    tab._seg_method_cb.setCurrentIndex(idx)


# ── dropdown + per-method parameter panel ────────────────────────────────────

def test_dropdown_lists_five_methods(qapp):
    from analyze.tab import AnalyzeTab
    tab = AnalyzeTab()
    values = [tab._seg_method_cb.itemData(i) for i in range(tab._seg_method_cb.count())]
    assert values == ["stardist_nuclei", "stardist_seeded_watershed_cell",
                      "cellpose", "cellpose_labelfree", "classical_threshold"]
    # friendly labels (not raw CLI values)
    labels = [tab._seg_method_cb.itemText(i) for i in range(tab._seg_method_cb.count())]
    assert any("Label-free" in l for l in labels)


def test_param_panel_swaps_per_method(qapp):
    from analyze.tab import AnalyzeTab
    tab = AnalyzeTab()
    for value in ("stardist_nuclei", "stardist_seeded_watershed_cell", "cellpose",
                  "cellpose_labelfree", "classical_threshold"):
        _set_method(tab, value)
        assert tab._seg_param_stack.currentName() == value

    # The Cellpose page exposes the tuning controls.
    _set_method(tab, "cellpose")
    assert tab._cellpose_model_cb is not None
    assert tab._cellpose_flow_thr_edit is not None
    assert tab._cellpose_cellprob_thr_edit is not None
    assert tab._cellpose_nuclei_channel_edit is not None

    # Label-free hides the Segmentation-channel field (phase takes that slot);
    # general Cellpose shows it. (isHidden reflects setVisible intent even when
    # the top-level window isn't shown, unlike isVisible.)
    _set_method(tab, "cellpose_labelfree")
    assert tab._nuclear_row_widget.isHidden() is True
    _set_method(tab, "cellpose")
    assert tab._nuclear_row_widget.isHidden() is False


def test_param_stack_collapses_to_current_page(qapp):
    # Regression: the param stack must size to the *current* page, not the max
    # over all pages — otherwise the empty StarDist page reserves the tall
    # Cellpose page's height (the gap under channel tokens).
    from PySide6.QtWidgets import QSizePolicy
    from analyze.tab import AnalyzeTab
    tab = AnalyzeTab()
    st = tab._seg_param_stack
    _set_method(tab, "cellpose")
    tall = st.sizeHint().height()
    _set_method(tab, "stardist_nuclei")
    short = st.sizeHint().height()
    assert short < tall                      # collapsed, no reserved gap
    cur = st.currentIndex()
    assert st.widget(cur).sizePolicy().verticalPolicy() == QSizePolicy.Preferred
    assert all(st.widget(i).sizePolicy().verticalPolicy() == QSizePolicy.Ignored
               for i in range(st.count()) if i != cur)


# ── general Cellpose → CLI arg mapping ───────────────────────────────────────

def _base(**over):
    opts = dict(nuclear_token="GFP", fluor_tokens=["GFP", "DAPI"], csv_prefix="m",
                filename_schema="experiment:channel:well:fov:timepoint", filename_sep="_",
                min_nucleus_area_px=50, tf_threads="0", workers="0",
                compress_input_well_folders=True, compress_output_well_folders=True)
    opts.update(over)
    return opts


def test_build_pipeline_args_general_cellpose():
    from services.pipeline_service import build_pipeline_args
    P = Path("/x/p.py")

    a = build_pipeline_args(P, Path("/in"), Path("/out"), _base(
        segmentation_method="cellpose", cellpose_model="cyto2",
        cellpose_diameter=25.0, cellpose_flow_threshold=0.5,
        cellpose_cellprob_threshold=-1.0, cellpose_nuclei_channel="DAPI"))
    assert a[a.index("--cellpose_model") + 1] == "cyto2"
    assert a[a.index("--cellpose_diameter") + 1] == "25.0"
    assert a[a.index("--cellpose_flow_threshold") + 1] == "0.5"
    assert a[a.index("--cellpose_cellprob_threshold") + 1] == "-1.0"
    assert a[a.index("--cellpose_nuclei_channel") + 1] == "DAPI"
    assert "--phase_token" not in a          # general Cellpose is NOT label-free

    # diameter 0 (auto) is omitted; classical emits no cellpose/phase flags
    a2 = build_pipeline_args(P, Path("/in"), Path("/out"),
                             _base(segmentation_method="cellpose", cellpose_diameter=0.0))
    assert "--cellpose_diameter" not in a2 and "--cellpose_model" in a2
    a3 = build_pipeline_args(P, Path("/in"), Path("/out"),
                             _base(segmentation_method="classical_threshold"))
    assert "--phase_token" not in a3 and "--cellpose_model" not in a3


# ── _segment_cellpose honors params (mocked cellpose) ────────────────────────

def test_segment_cellpose_honors_params(monkeypatch):
    pytest.importorskip("scipy")
    import process_microscopy as pm
    # The Cellpose segmenters + model cache live in pipeline.cellpose now
    # (pm re-exports them). Patch the cache where it actually lives.
    import pipeline.cellpose as mc

    calls: dict = {}

    class _FakeModel:
        def __init__(self, model_type=None):
            calls["model_type"] = model_type

        def eval(self, x, **kw):
            calls["eval"] = kw
            return np.zeros(np.asarray(x).shape[:2], dtype=int), None, None

    fake_models = types.SimpleNamespace(CellposeModel=_FakeModel)
    fake_cellpose = types.ModuleType("cellpose")
    fake_cellpose.models = fake_models  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cellpose", fake_cellpose)
    monkeypatch.setitem(sys.modules, "cellpose.models", fake_models)
    monkeypatch.setattr(mc, "_CELLPOSE_MODELS", {}, raising=False)

    img = np.zeros((8, 8), np.float32)
    pm._segment_cellpose(img, model_type="cyto2", diameter=20.0,
                         flow_threshold=0.5, cellprob_threshold=-0.5)
    assert calls["model_type"] == "cyto2"
    assert calls["eval"]["diameter"] == 20.0
    assert calls["eval"]["flow_threshold"] == 0.5
    assert calls["eval"]["cellprob_threshold"] == -0.5
    assert calls["eval"]["channels"] == [0, 0]

    # A nucleus channel → 2-channel stack + channels=[1, 2].
    mc._CELLPOSE_MODELS.clear()
    pm._segment_cellpose(img, model_type="cyto2",
                         nucleus_image=np.ones((8, 8), np.float32))
    assert calls["eval"]["channels"] == [1, 2]
