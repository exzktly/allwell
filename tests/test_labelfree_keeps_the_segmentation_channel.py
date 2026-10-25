"""Switching to label-free keeps the segmentation channel in the run.

Reported (9.14.16): load a dataset, the scan puts EGFP in "Segmentation
channel" and Bright among the fluorescent channels; choose Label-free and EGFP
is deleted outright. It was, twice over. Label-free hides the "Segmentation
channel" row, and a scan never lists the segmentation channel among the
fluorescent channels as well, so EGFP was on screen nowhere. And a label-free
run overwrites the segmentation slot with the Phase channel
(``pipeline.cli_args.apply_labelfree_preset``), so EGFP was in the run nowhere
either: never segmented, never measured.

Asserted on what the user sees (a shown row reading EGFP) and on what is sent
(the run's ``fluor_tokens``), because the field holding EGFP always kept its
text — reading that text is exactly how this was missed.
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
    t.resize(400, 1400)
    t.show()
    qtbot.waitExposed(t)
    return t


def _scan(tab, tmp_path, channels) -> None:
    for well in ("A01", "A02"):
        for field in range(2):
            for ch in channels:
                (tmp_path / f"{well}_f{field:02d}_t000_{ch}.tif").write_bytes(b"x")
    tab._input_edit.setText(str(tmp_path))
    tab._autofill_from_input_scan(str(tmp_path))


def _method(tab, value) -> None:
    tab._seg_method_cb.setCurrentIndex(tab._seg_method_cb.findData(value))


def _shown_rows(tab) -> list[str]:
    # A row added to a shown layout is shown by Qt on the next event-loop turn.
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    return [e.text().strip() for e, _s, _p, _b in tab._fluor_rows if e.isVisibleTo(tab)]


def _sent_channels(tab) -> list[str]:
    return list(tab._collect_run_spec().opts["fluor_tokens"])


def test_the_reported_plate_keeps_egfp_on_screen_and_in_the_run(tab, tmp_path):
    """The reporter's state, as a scan or a loaded run leaves it: EGFP is the
    segmentation channel and Bright the one fluorescent channel."""
    tab._apply_pipeline_info({"nuclear_token": "EGFP", "fluor_tokens": ["Bright"]},
                             set_input_path=False)
    assert tab._nuclear_token_edit.text() == "EGFP" and _shown_rows(tab) == ["Bright"]

    _method(tab, "cellpose_labelfree")
    assert not tab._nuclear_row_widget.isVisibleTo(tab), "premise: label-free hides the field"
    assert "EGFP" in _shown_rows(tab), "EGFP is shown nowhere on the form"
    assert "EGFP" in _sent_channels(tab), "EGFP is not in the run, so it is never measured"


def test_a_real_scan_that_makes_egfp_the_segmentation_channel(tab, tmp_path):
    """Through the folder scan itself: Brightfield is marked Phase, EGFP takes
    the segmentation slot, and label-free must still measure EGFP."""
    _scan(tab, tmp_path, ("BRIGHTFIELD", "EGFP"))
    assert tab._nuclear_token_edit.text() == "EGFP", "premise: the scan chose EGFP"
    _method(tab, "cellpose_labelfree")
    assert "EGFP" in _shown_rows(tab) and "EGFP" in _sent_channels(tab)


def test_switching_back_takes_the_row_away_and_restores_the_field(tab):
    tab._apply_pipeline_info({"nuclear_token": "EGFP", "fluor_tokens": ["Bright"]},
                             set_input_path=False)
    _method(tab, "cellpose_labelfree")
    _method(tab, "cellpose")
    assert tab._nuclear_row_widget.isVisibleTo(tab)
    assert tab._nuclear_token_edit.text() == "EGFP"
    assert _shown_rows(tab) == ["Bright"], "the moved row stayed, listing EGFP twice"


def test_a_folder_scanned_while_label_free_is_already_chosen(tab, tmp_path):
    _method(tab, "cellpose_labelfree")
    _scan(tab, tmp_path, ("BRIGHTFIELD", "EGFP"))
    assert tab._nuclear_token_edit.text() == "EGFP", "premise: the scan chose EGFP"
    assert "EGFP" in _shown_rows(tab) and "EGFP" in _sent_channels(tab)


def test_a_loaded_job_keeps_exactly_the_channels_it_states(tab):
    """A queued job or a saved run states the channels it ran with; loading it
    while label-free must not add the segmentation channel to them."""
    tab._apply_pipeline_info({"segmentation_method": "cellpose_labelfree", "nuclear_token": "DAPI",
                              "fluor_tokens": ["GFP", "BF"], "phase_token": "BF"},
                             set_input_path=False)
    assert tab._seg_method() == "cellpose_labelfree"
    assert _sent_channels(tab) == ["GFP", "BF"]


def test_the_form_s_default_is_not_made_into_a_channel(tab):
    """With nothing loaded or typed, the field's starting text is a default, not
    a channel any input is known to carry; listing it would ask the run for
    images that may not exist."""
    before = _shown_rows(tab)
    _method(tab, "cellpose_labelfree")
    assert _shown_rows(tab) == before


def test_a_channel_the_user_typed_is_kept(tab, qtbot):
    tab._nuclear_token_edit.clear()
    qtbot.keyClicks(tab._nuclear_token_edit, "DAPI")
    _method(tab, "cellpose_labelfree")
    assert "DAPI" in _shown_rows(tab)


def test_label_free_hides_the_segmentation_channel_s_label_too(tab):
    """Rendered, label-free left the words "Segmentation channel" under the
    method selector with no field beside them: only the field's cell was hidden."""
    from PySide6.QtWidgets import QLabel

    _method(tab, "cellpose_labelfree")
    shown = [w.text() for w in tab.findChildren(QLabel)
             if w.text() == "Segmentation channel" and w.isVisibleTo(tab)]
    assert shown == [], "the label of a hidden field is still drawn"
    _method(tab, "stardist_nuclei")
    assert [w.text() for w in tab.findChildren(QLabel)
            if w.text() == "Segmentation channel" and w.isVisibleTo(tab)] == ["Segmentation channel"]


# ── the parameter area's height ─────────────────────────────────────────────
#
# Under the app's own stylesheet, because the defect is made of wrapped text:
# without it the fonts are smaller, no page's text wraps at this width, and the
# stack lays out correctly with or without the fix.


@pytest.fixture
def styled_tab(qtbot, monkeypatch, qapp):
    from ui import theme

    before = qapp.styleSheet()
    qapp.setStyleSheet(theme.qss())
    try:
        import analyze.run_footer

        monkeypatch.setattr(analyze.run_footer, "load_remotes", lambda: {})
        from analyze.tab import AnalyzeTab

        t = AnalyzeTab()
        qtbot.addWidget(t)
        t.resize(400, 1600)
        t.show()
        qtbot.waitExposed(t)
        yield t
    finally:
        qapp.setStyleSheet(before)   # app-wide: left applied, it changes later tests' layout


def _gap_above_and_below(tab) -> tuple[int, int]:
    """Pixels between "+ Add channel" and the parameter area, and between the
    area and the row after it — laid out, as drawn."""
    from PySide6.QtWidgets import QApplication

    # Settled, as drawn (see the nanowell test below): two turns for the stack
    # to refit to its page and for the section to lay out around it.
    for _ in range(3):
        QApplication.processEvents()
    stack = tab._seg_param_stack
    lay = stack.parentWidget().layout()
    i = lay.indexOf(stack)
    before = lay.itemAt(i - 1).widget()
    after = next(lay.itemAt(j).widget() for j in range(i + 1, lay.count())
                 if lay.itemAt(j).widget() is not None and lay.itemAt(j).widget().isVisibleTo(tab))
    return (stack.geometry().top() - before.geometry().bottom() - 1,
            after.geometry().top() - stack.geometry().bottom() - 1)


def test_label_free_leaves_no_gap_below_add_channel(styled_tab):
    """Reported: a tall blank gap below "+ Add channel" with Label-free chosen.
    The stack was laid out at its tallest page's wrapped-text height (Cellpose's)
    with Label-free's short page centred in it: 45 px blank above and below.
    Visiting Cellpose first, since that is the page whose height it kept."""
    tab = styled_tab
    stack = tab._seg_param_stack
    spacing = stack.parentWidget().layout().spacing()
    _method(tab, "cellpose")
    _method(tab, "cellpose_labelfree")
    assert _gap_above_and_below(tab) == (spacing, spacing)
    # And no blank inside the area either: it is as tall as the page showing
    # needs, not as tall as Cellpose's. (With the area vertically Preferred, a
    # stale height shows up here rather than between the widgets.)
    needed = stack.currentWidget().heightForWidth(stack.width())
    assert stack.height() == needed, f"the area is {stack.height()} px for a {needed} px page"


def test_nanowell_mode_s_rows_are_not_clipped(styled_tab, qtbot):
    """The label-free page grows in nanowell mode, and its wrapped heading needs
    more height at the sidebar's width than its plain size hint: the stack must
    give it that, not cut it off."""
    tab = styled_tab
    tab._nanowell_cb.setChecked(True)
    # Read once the layout has settled, which is what is drawn: the stack refits
    # to its page on the page's own layout request, a turn after the rows are
    # added. Read after a single turn, it measured 193 px of a 209 px page under
    # CI's wider DejaVu Sans — a frame that is never painted.
    qtbot.wait(100)
    stack = tab._seg_param_stack
    needed = stack.currentWidget().heightForWidth(stack.width())
    assert stack.height() >= needed, f"the page needs {needed} px and was given {stack.height()}"
