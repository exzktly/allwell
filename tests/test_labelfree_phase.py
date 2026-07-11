"""Phase-channel ingestion (§A): Role.PHASE classification, the per-channel Phase
checkbox (mutual exclusion + smFISH exclusion), and that a phase token is passed
through to the pipeline. The Cellpose/classical/label-free segmenters were
removed with the StarDist-only strip; the surviving Phase behaviour (a channel
that skips background correction) is covered here. Runs headless (offscreen Qt).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_analyze_form_fits_narrow_sidebar(qapp):
    """Regression: long-label checkboxes (a QCheckBox reports its full text as
    its minimum width) used to pin the form ~822 px wide, so the scroll area
    could never shrink to its 438 px viewport. The offending checkboxes are now
    WrapCheckBox, so the form's minimum width fits the sidebar."""
    from PySide6.QtWidgets import QScrollArea
    from analyze_tab import AnalyzeTab
    from widgets.wrap_checkbox import WrapCheckBox

    tab = AnalyzeTab()
    form = next(sa.widget() for sa in tab.findChildren(QScrollArea))
    assert form.minimumSizeHint().width() <= 440, form.minimumSizeHint().width()
    for cb in (tab._qc_remove_debris_cb, tab._dry_run_cleanup_cb):
        assert isinstance(cb, WrapCheckBox)
        cb.setChecked(True); assert cb.isChecked()
        cb.setChecked(False); assert not cb.isChecked()


# ── image_identity: Role.PHASE ───────────────────────────────────────────────

def test_role_phase_catalog():
    from well_viewer.data.image_identity import Role, ROLE_CATALOG, role_for_kind
    assert Role.PHASE.value == "phase"
    assert Role.PHASE in ROLE_CATALOG
    spec = ROLE_CATALOG[Role.PHASE]
    assert spec.suffix == "_phase" and spec.is_channel_scoped
    assert role_for_kind("phase") is Role.PHASE
    assert role_for_kind("smfish") is Role.SMFISH


# ── phase token → CLI ────────────────────────────────────────────────────────

def test_phase_token_passed_through(qapp):
    from pathlib import Path
    from services.pipeline_service import build_pipeline_args
    opts = dict(nuclear_token="NIR", fluor_tokens=["BF", "GFP"], csv_prefix="m",
                filename_schema="experiment:channel:well:fov:timepoint", filename_sep="_",
                min_nucleus_area_px=50, tf_threads="0", workers="0",
                segmentation_method="stardist_nuclei", phase_token="BF",
                compress_input_well_folders=True, compress_output_well_folders=True)
    a = build_pipeline_args(Path("/x/process_microscopy.py"), Path("/in"), Path("/out"), opts)
    assert a[a.index("--phase_token") + 1] == "BF"


# ── per-channel Phase checkbox ───────────────────────────────────────────────

def test_analyze_phase_checkbox(qapp):
    from analyze_tab import AnalyzeTab
    tab = AnalyzeTab()

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
