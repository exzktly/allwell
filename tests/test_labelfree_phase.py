"""Label-free segmentation + phase ingestion (§A).

Role.PHASE classification, the classical segmenter + cellpose guard, the
Analyze→CLI arg mapping, and the per-channel Phase checkbox behaviour. Runs
headless (offscreen Qt)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import numpy as np
import pytest

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ── image_identity: Role.PHASE ───────────────────────────────────────────────

def test_role_phase_catalog():
    from well_viewer.data.image_identity import Role, ROLE_CATALOG, role_for_kind
    assert Role.PHASE.value == "phase"
    assert Role.PHASE in ROLE_CATALOG
    spec = ROLE_CATALOG[Role.PHASE]
    assert spec.suffix == "_phase" and spec.is_channel_scoped
    assert role_for_kind("phase") is Role.PHASE
    # existing roles unaffected
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
    pytest.importorskip("scipy")
    from process_microscopy import _segment_cellpose, _CELLPOSE_MODEL
    if _CELLPOSE_MODEL is not None:
        pytest.skip("cellpose installed in this env")
    try:
        import cellpose  # noqa: F401
        pytest.skip("cellpose installed in this env")
    except Exception:
        pass
    with pytest.raises(RuntimeError, match="cellpose"):
        _segment_cellpose(np.zeros((16, 16), np.float32))


# ── Analyze → CLI arg mapping ────────────────────────────────────────────────

def _base(**over):
    opts = dict(nuclear_token="NIR", fluor_tokens=["BF", "GFP"], csv_prefix="m",
                filename_schema="experiment:channel:well:fov:timepoint", filename_sep="_",
                min_nucleus_area_px=50, tf_threads="0", workers="0",
                compress_input_well_folders=True, compress_output_well_folders=True)
    opts.update(over)
    return opts


def test_build_pipeline_args_labelfree():
    from services.pipeline_service import build_pipeline_args
    P = Path("/x/process_microscopy.py")

    a = build_pipeline_args(P, Path("/in"), Path("/out"),
                            _base(segmentation_method="cellpose", phase_token="BF",
                                  cellpose_diameter=30.0))
    assert a[a.index("--segmentation_method") + 1] == "cellpose"
    assert a[a.index("--phase_token") + 1] == "BF"
    assert a[a.index("--cellpose_diameter") + 1] == "30.0"

    a2 = build_pipeline_args(P, Path("/in"), Path("/out"),
                             _base(segmentation_method="classical_threshold", phase_token="BF"))
    assert a2[a2.index("--phase_token") + 1] == "BF"
    assert "--cellpose_diameter" not in a2   # classical has no diameter

    a3 = build_pipeline_args(P, Path("/in"), Path("/out"),
                             _base(segmentation_method="stardist_nuclei"))
    assert "--phase_token" not in a3         # stardist ignores phase


# ── Analyze per-channel Phase checkbox ───────────────────────────────────────

def test_analyze_phase_checkbox(qapp):
    from analyze_tab import AnalyzeTab
    tab = AnalyzeTab()

    methods = [tab._seg_method_cb.itemText(i) for i in range(tab._seg_method_cb.count())]
    assert "cellpose" in methods and "classical_threshold" in methods

    while len(tab._fluor_rows) < 2:
        tab._fluor_add_row("BF")
    (e0, s0, p0, _b0) = tab._fluor_rows[0]
    (e1, s1, p1, _b1) = tab._fluor_rows[1]
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

    # Label-free method needs a Phase channel → validation error when none.
    tab._seg_method_cb.setCurrentText("cellpose")
    assert any("Phase" in e for e in tab._segmentation_validation_errors())
    # mark one again → error clears
    p0.setChecked(True)
    assert tab._segmentation_validation_errors() == []
