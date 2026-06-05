"""Label-free segmentation + phase ingestion (§A) — the Cellpose label-free preset.

Role.PHASE classification, the classical/cellpose segmenter guards, and the
label-free preset's phase requirement + arg mapping. The *general* Cellpose
method, the dropdown set, and the per-method parameter panel are covered in
``test_segmentation_methods.py``. Runs headless (offscreen Qt)."""

from __future__ import annotations

import importlib.util
import os

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


# ── image_identity: Role.PHASE ───────────────────────────────────────────────

def test_role_phase_catalog():
    from well_viewer.data.image_identity import Role, ROLE_CATALOG, role_for_kind
    assert Role.PHASE.value == "phase"
    assert Role.PHASE in ROLE_CATALOG
    spec = ROLE_CATALOG[Role.PHASE]
    assert spec.suffix == "_phase" and spec.is_channel_scoped
    assert role_for_kind("phase") is Role.PHASE
    assert role_for_kind("smfish") is Role.SMFISH


# ── segmenters ───────────────────────────────────────────────────────────────

def test_segment_classical_splits_objects():
    pytest.importorskip("scipy")
    pytest.importorskip("skimage")
    from process_microscopy import _segment_classical
    img = np.zeros((80, 80), np.float32)
    yy, xx = np.ogrid[:80, :80]
    img[(yy - 25) ** 2 + (xx - 25) ** 2 <= 100] = 200.0
    img[(yy - 55) ** 2 + (xx - 55) ** 2 <= 100] = 200.0
    labels = _segment_classical(img)
    assert len(set(np.unique(labels)) - {0}) == 2
    assert _segment_classical(np.zeros((10, 10), np.float32)).max() == 0


def test_segment_cellpose_guarded():
    if importlib.util.find_spec("cellpose") is not None:
        pytest.skip("cellpose installed in this env")
    from process_microscopy import _segment_cellpose
    with pytest.raises(RuntimeError, match="[Cc]ellpose"):
        _segment_cellpose(np.zeros((16, 16), np.float32))


# ── Analyze → CLI arg mapping (the label-free preset) ────────────────────────

def _base(**over):
    opts = dict(nuclear_token="NIR", fluor_tokens=["BF", "GFP"], csv_prefix="m",
                filename_schema="experiment:channel:well:fov:timepoint", filename_sep="_",
                min_nucleus_area_px=50, tf_threads="0", workers="0",
                compress_input_well_folders=True, compress_output_well_folders=True)
    opts.update(over)
    return opts


def test_build_pipeline_args_labelfree_preset():
    from services.pipeline_service import build_pipeline_args
    P = Path("/x/process_microscopy.py")

    a = build_pipeline_args(P, Path("/in"), Path("/out"),
                            _base(segmentation_method="cellpose_labelfree", phase_token="BF"))
    assert a[a.index("--segmentation_method") + 1] == "cellpose_labelfree"
    assert a[a.index("--phase_token") + 1] == "BF"
    # The brightfield preset is applied in main(); the GUI doesn't push tuning flags.
    assert "--cellpose_model" not in a

    # stardist ignores phase entirely
    a2 = build_pipeline_args(P, Path("/in"), Path("/out"),
                             _base(segmentation_method="stardist_nuclei"))
    assert "--phase_token" not in a2


# ── per-channel Phase checkbox + label-free validation ───────────────────────

def test_analyze_phase_checkbox(qapp):
    from analyze_tab import AnalyzeTab
    tab = AnalyzeTab()
    values = [tab._seg_method_cb.itemData(i) for i in range(tab._seg_method_cb.count())]
    assert "cellpose_labelfree" in values

    while len(tab._fluor_rows) < 2:
        tab._fluor_add_row("BF")
    (e0, _s0, p0, _b0) = tab._fluor_rows[0]
    (e1, s1, p1, _b1) = tab._fluor_rows[1]
    # Layout regression: the smFISH/Phase checkboxes are pinned to their natural
    # width so a narrow sidebar can't shrink them into overlapping text.
    for (_e, _sm, _ph, _b) in tab._fluor_rows:
        assert _sm.minimumWidth() > 0 and _ph.minimumWidth() > 0
        assert _sm.minimumWidth() >= _sm.sizeHint().width() - 1
    e0.setText("BF")
    e1.setText("GFP")

    p0.setChecked(True)
    assert tab._phase_token() == "BF"
    # Phase is mutually exclusive across rows.
    p1.setChecked(True)
    assert not p0.isChecked()
    assert tab._phase_token() == "GFP"
    # A channel can't be both Phase and smFISH.
    s1.setChecked(True)
    assert not p1.isChecked()
    assert tab._phase_token() == ""

    # The label-free preset requires a Phase channel; general Cellpose does not.
    _set_method(tab, "cellpose_labelfree")
    assert any("Phase" in e for e in tab._segmentation_validation_errors())
    _set_method(tab, "cellpose")
    assert tab._segmentation_validation_errors() == []
    _set_method(tab, "cellpose_labelfree")
    p0.setChecked(True)
    assert tab._segmentation_validation_errors() == []
