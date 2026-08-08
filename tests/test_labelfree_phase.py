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


def test_analyze_form_fits_narrow_sidebar(qapp):
    """Regression: long-label checkboxes (a QCheckBox reports its full text as
    its minimum width) used to pin the form ~822 px wide, so the scroll area
    could never shrink to its RAIL_W-px viewport — every word-wrap hint was
    laid out wide and then clipped instead of wrapping. The offending
    checkboxes are now WrapCheckBox, so the form's minimum width fits the
    sidebar."""
    from PySide6.QtWidgets import QScrollArea
    from analyze_tab import AnalyzeTab
    from well_viewer.core.app_shell_view import RAIL_W
    from widgets.wrap_checkbox import WrapCheckBox

    tab = AnalyzeTab()
    form = next(sa.widget() for sa in tab.findChildren(QScrollArea))
    # The form must be able to shrink to (≤) the scroll area's RAIL_W viewport.
    assert form.minimumSizeHint().width() <= RAIL_W + 2, form.minimumSizeHint().width()
    # the long-label checkboxes wrap rather than pinning the panel wide
    for cb in (tab._qc_remove_debris_cb,
               tab._dry_run_cleanup_cb):
        assert isinstance(cb, WrapCheckBox)
        # API still behaves like a checkbox
        cb.setChecked(True); assert cb.isChecked()
        cb.setChecked(False); assert not cb.isChecked()


def test_qc_debris_toggle_hides_its_labels(qapp):
    """Regression: 'Remove debris' off must hide the size/solidity LABELS too,
    not just their input cells. ``_row()`` returns the right-hand cell (the label
    lives in the cell's parent), so toggling the cells left 'Min / Max area (px)'
    / 'Min solidity (0-1)' stranded/centered with no fields when debris was off —
    the toggle must target the parent rows (like _bg_radius_rows et al.)."""
    from PySide6.QtWidgets import QLabel
    from analyze_tab import AnalyzeTab

    tab = AnalyzeTab()
    labels = [l for l in tab.findChildren(QLabel)
              if l.text() in ("Min / Max area (px)", "Min solidity (0-1)")]
    assert {l.text() for l in labels} == {"Min / Max area (px)", "Min solidity (0-1)"}
    # Each debris label's containing row is one of the toggled rows (so hiding the
    # row hides the label, not just the input cell beside it).
    for lbl in labels:
        assert lbl.parentWidget() in tab._qc_debris_rows

    tab._qc_remove_debris_cb.setChecked(False)
    assert all(row.isHidden() for row in tab._qc_debris_rows)
    tab._qc_remove_debris_cb.setChecked(True)
    assert not any(row.isHidden() for row in tab._qc_debris_rows)


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


def test_phase_channel_cannot_also_be_the_segmentation_channel(qapp):
    """A Phase channel named as the segmentation input is refused by every
    routine but the label-free one.

    The reported failure: the folder-scan auto-fill put a DIC channel in the
    Segmentation channel field, the operator marked that channel Phase in the
    rows, and ``stardist_nuclei`` then ran StarDist over an uncorrected
    brightfield image. The run completed and wrote a full-looking set of CSVs,
    so nothing about the output said the segmentation input was wrong.
    """
    from analyze_tab import AnalyzeTab
    tab = AnalyzeTab()
    while len(tab._fluor_rows) < 2:
        tab._fluor_add_row("GFP")
    (e0, _s0, p0, _b0) = tab._fluor_rows[0]
    (e1, _s1, _p1, _b1) = tab._fluor_rows[1]
    e0.setText("DIC")
    e1.setText("GFP")
    p0.setChecked(True)

    _set_method(tab, "stardist_nuclei")
    tab._nuclear_token_edit.setText("RFP")
    assert tab._segmentation_validation_errors() == []

    # The collision, including a case-only difference.
    tab._nuclear_token_edit.setText("DIC")
    assert any("Phase" in e and "segmentation channel" in e
               for e in tab._segmentation_validation_errors())
    tab._nuclear_token_edit.setText("dic")
    assert tab._segmentation_validation_errors() != []

    # The label-free preset segments the phase channel by design; the field is
    # hidden there and its value must not be read as a collision.
    _set_method(tab, "cellpose_labelfree")
    assert tab._segmentation_validation_errors() == []


def test_phase_channel_cannot_also_be_the_cytoplasm_channel(qapp):
    from analyze_tab import AnalyzeTab
    tab = AnalyzeTab()
    while len(tab._fluor_rows) < 2:
        tab._fluor_add_row("GFP")
    (e0, _s0, p0, _b0) = tab._fluor_rows[0]
    tab._fluor_rows[1][0].setText("GFP")
    e0.setText("BF")
    p0.setChecked(True)
    _set_method(tab, "stardist_seeded_watershed_cell")
    tab._nuclear_token_edit.setText("DAPI")
    tab._cytoplasm_edit.setText("BF")
    assert any("cytoplasm channel" in e for e in tab._segmentation_validation_errors())
    tab._cytoplasm_edit.setText("GFP")
    assert tab._segmentation_validation_errors() == []


def test_set_fluor_rows_restores_a_lowercased_phase_token(qapp):
    """The sidecar records ``phase_token`` lower-cased while the channel list
    keeps the filenames' casing, and a phase token can arrive naming a channel
    that is not in the list at all. Neither may lose the designation."""
    from analyze_tab import AnalyzeTab
    tab = AnalyzeTab()

    tab._set_fluor_rows(["GFP", "DIC"], smfish=[], phase="dic")
    assert tab._fluor_tokens_list() == ["GFP", "DIC"]
    assert tab._phase_token() == "DIC"

    # Not in the list → it gets a row of its own rather than being dropped.
    tab._set_fluor_rows(["GFP", "RFP"], smfish=[], phase="BF")
    assert tab._fluor_tokens_list() == ["GFP", "RFP", "BF"]
    assert tab._phase_token() == "BF"
