"""The Analyze sidebar's cleanup: what a fresh form shows, and where.

Each test here pins one finding of the sidebar UX review against the real
``AnalyzeTab``, and each is built so the form as it stood before the cleanup
fails it — the assertion names the value, not a property the old form also had:

* a fresh form corrects nothing, so it can run without a blank well;
* the Background well rows are hidden, not greyed, while nothing reads them,
  and say they are required while a correction does and the field is empty;
* Label-free mode hides the whole "Segmentation channel" row, label included;
* the method-settings block is exactly as tall as the chosen method's page;
* a form error is drawn in the error style, and an empty status line is gone;
* Run sits in a footer pinned under the scrolling form, with Suspend and Stop
  shown only while a run is going;
* the correction and Cellpose-model combos show labels and record ids.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def tab(qtbot, monkeypatch):
    import analyze.run_footer

    monkeypatch.setattr(analyze.run_footer, "load_remotes", lambda: {})
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    t.resize(1280, 860)
    t.show()
    qtbot.waitExposed(t)
    return t


def _method(tab, value: str) -> None:
    cb = tab._seg_method_cb
    cb.setCurrentIndex(cb.findData(value))


def _settle(qtbot) -> None:
    qtbot.wait(20)


# ── defaults ────────────────────────────────────────────────────────────────

def test_a_fresh_form_corrects_nothing_and_needs_no_blank_well(tab):
    assert tab._flatfield_cb.value() == "none"
    assert tab._background_cb.value() == "none"
    # The old default (flat-field from a blank well) returned "Empty-well
    # flat-field correction requires a Background well." here.
    assert tab._background_well_validation_error(check_input=False) is None


def test_the_blank_well_rows_appear_only_when_a_correction_reads_them(tab, qtbot):
    rows = (tab._background_well_row, *tab._bg_map_rows)
    needed = tab._background_well_needed_lbl
    assert all(r.isHidden() for r in rows) and needed.isHidden()

    tab._background_cb.setValue("empty_well")
    assert not any(r.isHidden() for r in rows)
    assert not needed.isHidden() and needed.objectName() == "Error"

    tab._background_well_edit.setText("H12")
    assert needed.isHidden()
    tab._background_well_edit.setText("")
    assert not needed.isHidden()

    tab._background_cb.setValue("robust_mesh")
    assert all(r.isHidden() for r in rows) and needed.isHidden()


# ── labels and values ───────────────────────────────────────────────────────

@pytest.mark.parametrize("attr, value, label", [
    ("_flatfield_cb", "empty_well", "From blank well(s)"),
    ("_flatfield_cb", "basic", "Whole plate (BaSiC)"),
    ("_background_cb", "robust_mesh", "Robust local mesh"),
    ("_background_cb", "sliding_disk", "Rolling disk"),
    ("_cellpose_model_cb", "cyto3", "cyto3 (needs Cellpose 3)"),
])
def test_a_combo_shows_a_label_and_records_the_id(tab, attr, value, label):
    combo = getattr(tab, attr)
    assert combo.setValue(value)
    assert combo.currentText() == label
    key = {"_flatfield_cb": "flatfield", "_background_cb": "background",
           "_cellpose_model_cb": "cellpose_model"}[attr]
    assert tab._collect_run_options()[key] == value


def test_a_sidecar_restores_a_combo_by_its_recorded_id(tab):
    tab._apply_pipeline_info({"schema_version": 2, "execution_options": {
        "flatfield": "gaussian", "background": "top_hat",
        "cellpose_model": "nuclei"}}, set_input_path=False)
    assert tab._flatfield_cb.currentText() == "Self (Gaussian blur)"
    assert tab._background_cb.currentText() == "Top-hat"
    assert tab._cellpose_model_cb.value() == "nuclei"


# ── rows that hide whole ────────────────────────────────────────────────────

def test_label_free_hides_the_segmentation_channel_label_too(tab, qtbot):
    from PySide6.QtWidgets import QLabel

    def label_shown() -> bool:
        return any(lbl.text() == "Segmentation channel" and lbl.isVisible()
                   for lbl in tab.findChildren(QLabel))

    tab.reveal_section("Channels & segmentation")
    _settle(qtbot)
    assert label_shown()
    _method(tab, "cellpose_labelfree")
    _settle(qtbot)
    # Before, only the field's cell was hidden and the label floated alone.
    assert not label_shown()


@pytest.mark.parametrize("method", [
    "stardist_seeded_watershed_cell", "cellpose_labelfree", "cellpose"])
def test_the_method_block_is_as_tall_as_the_chosen_page(tab, qtbot, method):
    _method(tab, method)
    _settle(qtbot)
    stack = tab._seg_param_stack
    page = stack.currentWidget()
    want = page.heightForWidth(stack.width()) if page.hasHeightForWidth() \
        else page.sizeHint().height()
    # Before, watershed and label-free were given the Cellpose page's 126 px,
    # the spare spread as gaps around their rows.
    assert stack.height() == want


# ── status lines ────────────────────────────────────────────────────────────

def test_a_segmentation_problem_is_drawn_as_an_error(tab):
    lbl = tab._segmentation_hint_lbl
    assert lbl.objectName() == "Muted" and lbl.text().startswith("Best default")
    _method(tab, "stardist_seeded_watershed_cell")      # no cytoplasm channel yet
    assert lbl.objectName() == "Error"
    assert lbl.text() == "Watershed mode requires a cytoplasm channel."
    tab._cytoplasm_edit.setText("GFP")
    assert lbl.objectName() == "Muted"


def test_an_empty_status_line_takes_no_room(tab, tmp_path):
    for lbl in (tab._layout_lbl, tab._folder_lock_lbl, tab._schema_err_lbl):
        assert lbl.isHidden(), lbl.text()
    (tmp_path / "in").mkdir()
    tab._input_edit.setText(str(tmp_path / "in"))
    assert not tab._layout_lbl.isHidden()
    assert tab._layout_lbl.objectName() == "Error"
    assert "empty" in tab._layout_lbl.text()


# ── the run footer ──────────────────────────────────────────────────────────

def test_run_is_on_screen_without_scrolling(tab, qtbot):
    _settle(qtbot)
    scroll = tab._left_sidebar
    assert not scroll.widget().isAncestorOf(tab._run_btn)
    assert scroll.footer().isAncestorOf(tab._run_btn)
    assert scroll.footer().isAncestorOf(tab._remote_cb)
    top = tab._run_btn.mapTo(scroll, tab._run_btn.rect().topLeft()).y()
    assert 0 < top and top + tab._run_btn.height() <= scroll.height()
    # And the form above it is not drawn under it.
    assert scroll.viewport().geometry().bottom() < scroll.footer().geometry().top()


def test_suspend_and_stop_are_shown_only_while_running(tab):
    def shown():
        return (not tab._run_btn.isHidden(), not tab._suspend_btn.isHidden(),
                not tab._stop_btn.isHidden())

    assert shown() == (True, False, False)
    tab._show_run_buttons(running=True)
    assert shown() == (False, True, True)
    assert tab._stop_btn.isEnabled() and not tab._run_btn.isEnabled()
    tab._show_run_buttons(running=False)
    assert shown() == (True, False, False)
    assert tab._run_btn.isEnabled() and not tab._stop_btn.isEnabled()


def test_remote_options_show_only_for_a_remote(tab):
    section = tab._remote_params_widget
    assert section is tab._section_widgets["Remote options"]
    assert section.isHidden()
    tab._remote_cb.addItem("gpu-box", userData="gpu-box")
    tab._remote_cb.setCurrentIndex(tab._remote_cb.count() - 1)
    assert not section.isHidden()
