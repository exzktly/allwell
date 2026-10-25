"""The Analyze sidebar's cleanup: what a fresh form shows, and where.

Each test here pins one finding of the sidebar UX review against the real
``AnalyzeTab``, and each is built so the form as it stood before the cleanup
fails it — the assertion names the value, not a property the old form also had:

* a fresh form corrects as the command line does, and can run without a blank well;
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

def test_a_fresh_form_corrects_as_the_command_line_does_and_needs_no_blank_well(tab):
    # The command line's own defaults (pipeline/cli_args.py): no flat-field, a
    # rolling-disk background — neither reads a blank well.
    assert tab._flatfield_cb.value() == "none"
    assert tab._background_cb.value() == "sliding_disk"
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
    for lbl in (tab._layout_lbl, tab._schema_err_lbl):
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


def test_the_footer_is_drawn_apart_from_the_form(tab, qtbot, qapp):
    """Measured in pixels, because the first version passed every geometry
    check here while drawing nothing: the app-wide ``QWidget`` rule filled the
    footer's two rows with the canvas colour, over the footer's own fill and
    its top edge, so the footer was indistinguishable from the form."""
    from ui import theme

    before = qapp.styleSheet()
    qapp.setStyleSheet(theme.qss())
    try:
        _settle(qtbot)
        footer = tab._left_sidebar.footer()
        img = footer.grab().toImage()
        x = footer.width() - 4                    # right of every control in both rows
        assert img.pixelColor(x, 0).name().lower() == theme.Colors.border.lower()
        assert img.pixelColor(x, footer.height() // 2).name().lower() == theme.Colors.panel.lower()
    finally:
        qapp.setStyleSheet(before)


# ── every run problem at once, beside Run (F1) ──────────────────────────────

def _numbers_and_schema_wrong(tab):
    tab._input_edit.setText("")
    tab._schema_picker.setSchema("well:well")             # does not parse
    tab._tf_threads_edit.setText("four")                   # not a number


def test_every_problem_is_collected_with_its_section(tab):
    _numbers_and_schema_wrong(tab)
    got = [(p.section, p.title) for p in tab._run_problems()]
    # In the order a user fixes them; the old chain stopped at the first.
    assert got[:3] == [("Data", "Input Error"),
                       ("Filename schema", "Schema Error"),
                       ("Advanced", "Input Error")]


def test_run_lists_the_problems_under_it_and_opens_the_first_one(tab, qtbot):
    _numbers_and_schema_wrong(tab)
    tab.reveal_section("Advanced")
    body = tab._collapsible_sections["Filename schema"][0]
    body.setVisible(False)                                 # folded again
    assert tab._validate_run_request() is None             # no dialog: the
    lbl = tab._run_problems_lbl                            # autouse guard would raise
    assert not lbl.isHidden() and lbl.objectName() == "Error"
    assert lbl.text().startswith("No input folder selected.")
    assert "(+2 more)" in lbl.text()
    tab._input_edit.setText("/somewhere")                 # fix the first
    tab._go_to_run_problem(tab._run_problems()[0])
    assert body.isVisibleTo(tab)                           # the schema's section opened


def test_the_live_list_is_quiet_until_run_is_pressed(tab, qtbot):
    _numbers_and_schema_wrong(tab)
    tab._live_run_check()
    assert tab._run_problems_lbl.objectName() == "Muted"
    tab._validate_run_request()
    tab._live_run_check()
    assert tab._run_problems_lbl.objectName() == "Error"   # stays red while fixing
    tab._input_edit.setText("/somewhere")
    tab._schema_picker.setSchema("experiment:channel:well:fov:timepoint")
    tab._tf_threads_edit.setText("0")
    tab._live_run_check()
    assert tab._run_problems_lbl.isHidden()


def test_an_invalid_schema_does_not_lock_the_input_folder(tab):
    tab._schema_picker.setSchema("well:well")
    assert tab._input_edit.isEnabled() and tab._browse_btn.isEnabled()


# ── one choice for existing output (E1) ─────────────────────────────────────

@pytest.mark.parametrize("value, force, incremental", [
    ("skip", False, False), ("incremental", False, True), ("force", True, False)])
def test_the_existing_output_choice_is_the_pipeline_s_two_flags(tab, value, force, incremental):
    tab._existing_output_cb.setValue(value)
    opts = tab._collect_run_options()
    assert (opts["force"], opts["incremental"]) == (force, incremental)


def test_a_sidecar_with_both_flags_restores_redo_it(tab):
    """The pipeline ignores --incremental under --force; so does the form."""
    tab._apply_pipeline_info({"schema_version": 2, "execution_options": {
        "force": True, "incremental": True}}, set_input_path=False)
    assert tab._existing_output_cb.value() == "force"


# ── folded sections say what they hold (B4), switch in the header (B3) ─────

def test_a_folded_section_says_what_it_holds(tab, qtbot):
    tab._existing_output_cb.setValue("incremental")
    tab._only_wells_edit.setText("B02")
    assert tab._run_scope_summary() == "Fill in what's missing · only B02"
    assert tab._changed_summary("Advanced") == ""
    tab._tf_threads_edit.setText("8")
    tab._cpu_only_cb.setChecked(True)
    assert tab._changed_summary("Advanced") == "2 changed"
    assert tab._image_correction_summary() == "None · Rolling disk"


def test_auto_threshold_is_switched_from_its_folded_header(tab):
    body, _caret = tab._collapsible_sections["Auto-threshold"]
    assert not body.isVisibleTo(tab)                       # folded
    switch = tab._auto_threshold_cb
    assert not body.isAncestorOf(switch)                   # in the header
    assert switch.isVisibleTo(tab)
    switch.click()
    assert not switch.isChecked() and not body.isVisibleTo(tab)   # did not unfold it


# ── layout (D4, D5) and copy (C1) ───────────────────────────────────────────

def test_a_row_label_sits_above_its_field(tab, qtbot):
    _settle(qtbot)
    row = tab._seg_method_cb.parentWidget().parentWidget()
    label = row.layout().itemAt(0).widget()
    assert label.text() == "Method"
    assert label.geometry().bottom() < tab._seg_method_cb.parentWidget().geometry().top()


def test_dropdowns_take_the_field_width_and_numbers_one_width(tab, qtbot):
    _settle(qtbot)
    combos = (tab._seg_method_cb, tab._flatfield_cb, tab._background_cb)
    assert len({c.width() for c in combos}) == 1           # one right edge
    nums = (tab._tophat_radius_nir_edit, tab._tf_threads_edit,
            tab._stardist_prob_thresh_edit, tab._qc_min_area_edit)
    assert {n.maximumWidth() for n in nums} == {tab._NUM_FIELD_W}


def test_scan_orders_read_as_words(tab):
    labels = {tab._nanowell_scan_cb.itemData(i): tab._nanowell_scan_cb.itemText(i)
              for i in range(tab._nanowell_scan_cb.count())}
    assert labels["top-left-row"] == "Top-left corner, by row"
    assert labels["bottom-right-column-serpentine"] == "Bottom-right corner, by column, serpentine"


def test_the_phase_storage_option_shows_only_with_a_phase_channel(tab):
    box = tab._phase_reference_cb
    assert box.isHidden()
    tab._fluor_rows[0][2].setChecked(True)
    assert not box.isHidden()
    tab._fluor_rows[0][2].setChecked(False)
    assert box.isHidden()
