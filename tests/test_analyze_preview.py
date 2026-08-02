"""The Analyze pane's Preview page.

The page exists to remove a defect rather than add a feature. The same
correction and segmentation settings used to live in two places — the Analyze
form and the Review Playground's own panel — with a button to copy one into the
other, so a carefully tuned preview could describe a run that never happened.

The fix is that **the Preview page owns no parameter controls**: it renders
whatever the form says. That is asserted here directly
(:func:`test_the_preview_page_owns_no_parameter_widget`) rather than left as a
convention, because every other guarantee in this file follows from it.

Builds a real ``AnalyzeTab`` offscreen. No dataset and no pipeline run.
"""

from __future__ import annotations

import io
import zipfile

import numpy as np
import pytest

pytest.importorskip("PySide6")
tifffile = pytest.importorskip("tifffile")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QLineEdit,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def input_dir(tmp_path):
    """The dataset layout Analyze is normally pointed at: TIFs under ``in/``."""
    (tmp_path / "in").mkdir()
    rng = np.random.default_rng(7)
    for well in ("A01", "A02"):
        for chan in ("NIR", "GFP"):
            tifffile.imwrite(tmp_path / "in" / f"exp_{chan}_{well}_1_0.tif",
                             (rng.random((48, 48)) * 900).astype(np.uint16))
    return tmp_path


@pytest.fixture
def tab(qapp, input_dir):
    from analyze_tab import AnalyzeTab
    t = AnalyzeTab()
    t._input_edit.setText(str(input_dir))
    t._right_switch.setCurrentByData("preview")
    return t


# ── the two-page right pane ──────────────────────────────────────────────────

def test_the_right_pane_has_run_and_preview_and_starts_on_run(qapp):
    from analyze_tab import AnalyzeTab
    t = AnalyzeTab()
    assert t._right_stack.pageNames() == ["run", "preview"]
    # Run is what an operator opening Analyze expects to see.
    assert t._right_stack.currentName() == "run"


def test_starting_a_run_brings_the_run_page_forward(tab):
    """Pressing Run from the Preview page must not leave the user watching a
    still image while the pipeline works."""
    assert tab._right_stack.currentName() == "preview"
    tab._show_run_page()
    assert tab._right_stack.currentName() == "run"


# ── the property the design rests on ─────────────────────────────────────────

def test_the_preview_page_owns_no_parameter_widget(tab):
    """Only *view* controls — which frame, and the display LUT.

    A flat-field combo or a threshold box here would be a second copy of a form
    field, and the moment there are two copies they can disagree about what the
    run will do. If this fails, the page has grown a parameter and the guarantee
    is gone.
    """
    page = tab._right_stack.widget(tab._right_stack.pageNames().index("preview"))
    owned = [w for cls in (QLineEdit, QComboBox, QCheckBox)
             for w in page.findChildren(cls)]
    names = set()
    for widget in owned:
        for attr in dir(tab):
            if attr.startswith("_") and getattr(tab, attr, None) is widget:
                names.add(attr)
                break
    assert names == {"_preview_frame_cb", "_preview_lo_edit", "_preview_hi_edit"}


# ── the preview follows the form ─────────────────────────────────────────────

@pytest.fixture
def correction_calls(tab, monkeypatch):
    """Record the kwargs the preview hands to the correction."""
    import services.frame_preview as FP
    calls: list[dict] = []
    real = FP.correct_frame
    monkeypatch.setattr(FP, "correct_frame",
                        lambda arr, **kw: calls.append(kw) or real(arr, **kw))
    return calls


def test_frames_are_found_in_the_input_folder(tab):
    assert tab._preview_frame_cb.count() == 4
    assert tab._preview_canvas._rgb is not None


def test_the_segmentation_channel_leads_the_frame_list(tab):
    """The thresholds are being chosen against the channel StarDist sees, so
    that is the frame to land on."""
    assert "NIR" in tab._preview_frame_cb.itemText(0)


def test_changing_the_forms_background_method_changes_the_preview(tab, correction_calls):
    tab._background_cb.setCurrentText("sliding_disk")
    tab._tophat_radius_nir_edit.setText("17")
    tab.refresh_preview()
    assert correction_calls[-1]["background_method"] == "sliding_disk"
    assert correction_calls[-1]["radius"] == 17

    tab._background_cb.setCurrentText("robust_mesh")
    tab._mesh_ratio_edit.setText("4.5")
    tab.refresh_preview()
    assert correction_calls[-1]["background_method"] == "robust_mesh"
    assert correction_calls[-1]["mesh_ratio"] == 4.5


def test_changing_the_forms_flatfield_changes_the_preview(tab, correction_calls):
    tab._flatfield_cb.setCurrentText("gaussian")
    tab.refresh_preview()
    assert correction_calls[-1]["flatfield_mode"] == "gaussian"


def test_the_forms_thresholds_reach_segmentation(tab, monkeypatch):
    import services.frame_preview as FP
    seen = {}

    def _fake(arr, *, prob_thresh, nms_thresh, tile_px=0):
        seen["thresholds"] = (prob_thresh, nms_thresh)
        return np.zeros(np.shape(arr), dtype=np.int32)
    monkeypatch.setattr(FP, "segment_frame", _fake)

    tab._stardist_prob_thresh_edit.setText("0.25")
    tab._stardist_nms_thresh_edit.setText("")      # blank = the model's own
    tab._segment_preview()
    _wait(tab, lambda: "thresholds" in seen)
    assert seen["thresholds"] == (0.25, None)


def _wait(tab, done, limit: int = 400) -> None:
    app = QApplication.instance()
    for _ in range(limit):
        app.processEvents()
        if done():
            return
    raise AssertionError("the off-thread segmentation never came back")


# ── things the preview must say rather than hide ─────────────────────────────

def test_a_reference_well_correction_is_named_not_silently_skipped(tab):
    """empty_well estimates its field from a whole reference well, which one
    frame cannot do. The run WILL apply it — so the preview has to say that it
    itself did not, rather than showing an uncorrected frame as corrected."""
    tab._flatfield_cb.setCurrentText("empty_well")
    tab.refresh_preview()
    status = tab._preview_status.text()
    assert "not applied in this preview" in status
    assert "flat-field empty_well" in status
    assert "The run itself will apply it" in status


def test_an_ordinary_correction_carries_no_warning(tab):
    tab._flatfield_cb.setCurrentText("gaussian")
    tab._background_cb.setCurrentText("sliding_disk")
    tab.refresh_preview()
    assert "not applied in this preview" not in tab._preview_status.text()


def test_labels_go_stale_when_a_setting_changes(tab, monkeypatch):
    import services.frame_preview as FP

    def _fake(arr, *, prob_thresh, nms_thresh, tile_px=0):
        labels = np.zeros(np.shape(arr), dtype=np.int32)
        labels[5:15, 5:15] = 1
        labels[25:35, 25:35] = 2
        return labels
    monkeypatch.setattr(FP, "segment_frame", _fake)

    tab._segment_preview()
    _wait(tab, lambda: tab._preview_labels is not None)
    assert "2 nuclei" in tab._preview_status.text()

    tab._tophat_radius_nir_edit.setText("31")     # a different corrected frame
    tab.refresh_preview()
    assert "stale" in tab._preview_status.text()


def test_an_empty_input_folder_says_so(qapp, tmp_path):
    from analyze_tab import AnalyzeTab
    t = AnalyzeTab()
    t._input_edit.setText(str(tmp_path))
    t._right_switch.setCurrentByData("preview")
    assert t._preview_frame_cb.count() == 0
    assert "No images found" in t._preview_status.text()


def test_a_per_well_zip_input_previews_too(qapp, tmp_path):
    rng = np.random.default_rng(3)
    with zipfile.ZipFile(tmp_path / "C04.zip", "w") as zf:
        for chan in ("NIR", "GFP"):
            buf = io.BytesIO()
            tifffile.imwrite(buf, (rng.random((32, 32)) * 900).astype(np.uint16))
            zf.writestr(f"exp_{chan}_C04_1_0.tif", buf.getvalue())

    from analyze_tab import AnalyzeTab
    t = AnalyzeTab()
    t._input_edit.setText(str(tmp_path))
    t._right_switch.setCurrentByData("preview")
    assert t._preview_frame_cb.count() == 2
    assert t._preview_canvas._rgb is not None
