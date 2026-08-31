"""Audit U8 — the segmentation hint moves with the fluorescent-channel rows.

The hint label under "Segmentation method" shows either the routine's one-line
description or, when the form is not yet runnable, the reasons why. Label-free
segmentation needs exactly one channel marked "Phase"; remove that row, or clear
its token, and the reason list is non-empty.

``_fluor_remove_row`` and each row's ``textChanged`` called
``_refresh_schema_preview`` and nothing else, so the label went on reading
"Use when there's no stain at all: Cellpose (cyto3) on the channel marked
'Phase' — label-free." with no Phase channel left to segment. The run was
correctly refused at Run; only the live feedback lied, which is the part the
user reads while setting the form up.

Both routes are driven through the real widgets — the row's ✕ button and the
token field — because both are where the missing call was.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def tab(qtbot, monkeypatch):
    import analyze.form

    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    return t


def _use_label_free(tab) -> None:
    values = [tab._seg_method_cb.itemData(i) for i in range(tab._seg_method_cb.count())]
    tab._seg_method_cb.setCurrentIndex(values.index("cellpose_labelfree"))


_HAPPY_PATH = "Use when there's no stain at all"


def test_removing_the_phase_row_updates_the_hint(tab):
    """The ✕ button on the row that carries the Phase mark."""
    tab._fluor_add_row("BF")
    edit, _smfish, phase, remove_btn = tab._fluor_rows[-1]
    phase.setChecked(True)
    _use_label_free(tab)
    assert _HAPPY_PATH in tab._segmentation_hint_lbl.text(), (
        "fixture is wrong: the hint should start on the happy path")

    remove_btn.click()

    assert tab._segmentation_validation_errors(), "fixture is wrong: no error to show"
    assert _HAPPY_PATH not in tab._segmentation_hint_lbl.text(), (
        "the hint still promises label-free segmentation after the Phase "
        "channel was removed")
    assert "Phase" in tab._segmentation_hint_lbl.text()


def test_clearing_the_phase_token_updates_the_hint(tab):
    """The same row, emptied rather than removed — ``_phase_token`` ignores a
    checked row whose token is blank, so this is the identical end state."""
    tab._fluor_add_row("BF")
    edit, _smfish, phase, _btn = tab._fluor_rows[-1]
    phase.setChecked(True)
    _use_label_free(tab)
    assert _HAPPY_PATH in tab._segmentation_hint_lbl.text()

    edit.setText("")

    assert tab._segmentation_validation_errors()
    assert _HAPPY_PATH not in tab._segmentation_hint_lbl.text(), (
        "the hint still promises label-free segmentation after the Phase "
        "channel's token was cleared")


def test_re_marking_a_phase_channel_brings_the_hint_back(tab):
    """Not a one-way door: the hint has to recover too."""
    tab._fluor_add_row("BF")
    edit, _smfish, phase, _btn = tab._fluor_rows[-1]
    phase.setChecked(True)
    _use_label_free(tab)
    edit.setText("")
    assert _HAPPY_PATH not in tab._segmentation_hint_lbl.text()
    edit.setText("BF")
    assert _HAPPY_PATH in tab._segmentation_hint_lbl.text()


def test_the_schema_preview_still_follows_the_rows(tab):
    """The call that was already there stays there."""
    tab._fluor_add_row("mCherry")
    assert "mCherry" in tab._schema_preview_lbl.text()
    edit, _s, _p, remove_btn = tab._fluor_rows[-1]
    remove_btn.click()
    assert "mCherry" not in tab._schema_preview_lbl.text()
