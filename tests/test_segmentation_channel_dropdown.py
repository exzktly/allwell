"""The Analyze form's Segmentation channel is a dropdown of the channels listed above it — that can still be typed in.

Reported: it was unclear whether the field asks for a new channel name or one already in the Channels list. It is both:
the segmentation channel is normally a separate one (a nuclear stain the list does not hold), so a name can be typed,
and a channel the list does hold can be picked; the form says which of the two the current text is.
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
    t.resize(500, 1400)
    t.show()
    qtbot.waitExposed(t)
    return t


def _options(combo) -> list[str]:
    return [combo.itemText(i) for i in range(combo.count())]


def test_the_dropdown_lists_exactly_the_channels_entered_above_and_follows_them(tab):
    tab._fluor_rows[0][0].setText("GFP")
    tab._fluor_add_row("mCherry")
    assert _options(tab._nuclear_token_edit) == ["GFP", "mCherry"]
    tab._fluor_rows[1][0].setText("Cy5")
    assert _options(tab._nuclear_token_edit) == ["GFP", "Cy5"]
    assert _options(tab._cytoplasm_edit) == ["GFP", "Cy5"]


def test_picking_a_listed_channel_sets_the_segmentation_channel_the_run_uses(tab):
    tab._fluor_rows[0][0].setText("GFP")
    tab._fluor_add_row("mCherry")
    combo = tab._nuclear_token_edit
    combo.setCurrentIndex(1)
    combo.activated.emit(1)
    assert combo.text() == "mCherry"
    assert tab._seg_channel_known, "a picked channel is a real one, as a typed one is"
    assert tab._seg_channel_note.text() == "mCherry is also one of the channels listed above."


def test_a_typed_name_that_no_row_lists_is_kept_and_called_a_separate_channel(tab):
    tab._fluor_rows[0][0].setText("GFP")
    tab._nuclear_token_edit.setText("DAPI")
    assert tab._nuclear_token_edit.text() == "DAPI"
    assert tab._seg_channel_note.text().startswith("DAPI is not one of the channels listed above: it is a separate channel")
    assert tab._seg_channel_note.isVisibleTo(tab)


def test_the_note_is_gone_for_label_free_where_the_field_is_hidden(tab):
    tab._nuclear_token_edit.setText("DAPI")
    tab._seg_method_cb.setCurrentIndex(tab._seg_method_cb.findData("cellpose_labelfree"))
    assert not tab._seg_channel_note.isVisibleTo(tab)


def test_a_typed_prefix_of_a_listed_channel_stays_what_was_typed(tab, qtbot):
    """Typing ``DAPI`` where ``DAPI_405`` is listed must leave ``DAPI``: a nuclear stain the list does not hold is the
    normal case, and Qt's default inline completer used to fill in the listed name, which leaving the field accepted —
    the run then segmented on a channel nobody typed. ``setText`` never reaches the completer, so this types keys."""
    from PySide6.QtCore import Qt

    tab._fluor_rows[0][0].setText("DAPI_405")
    tab._fluor_add_row("GFP")
    combo = tab._nuclear_token_edit
    combo.setText("")
    combo.setFocus()
    qtbot.keyClicks(combo.lineEdit(), "DAPI")
    tab._fluor_rows[0][0].setFocus()
    qtbot.wait(10)
    assert combo.text() == "DAPI"

    combo.setText("")
    combo.setFocus()
    qtbot.keyClicks(combo.lineEdit(), "gfp")
    qtbot.keyClick(combo.lineEdit(), Qt.Key_Tab)
    assert combo.text() == "gfp", "a case-insensitive completion turned the typed name into a listed one"
