"""The StarDist threshold help text has one definition, reachable from the row.

Two panels expose ``prob_thresh`` and ``nms_thresh`` — the Analyze form and the
Review pane's Playground — and each used to carry its own copy of the
explanation. They drifted: one grew turn-it-this-way guidance the other never
got, so the same two numbers were described differently depending on which tab
you were standing in. The text now lives in ``pipeline.stardist``, which both
already import.

The second guard is the defect that prompted this: the tooltip was attached only
to the narrow entry box, so hovering the *word* "Probability" — the obvious
target, and the larger one — answered nothing.

No app fixture: the row helpers are constructed directly against a bare
``QWidget``, so this stays in the fast lane.
"""

from __future__ import annotations

import pytest

from pipeline.stardist import NMS_THRESH_HELP, PROB_THRESH_HELP


def test_both_thresholds_state_their_range_and_their_default():
    """The two things the report asked for: what it does, and what range.

    Each text must also say how to get back to the pretrained model's own
    threshold — but the two say it differently now, because the two fields start
    differently: Overlap starts blank, and Probability starts at the Analyze
    form's shipped 0.2.
    """
    for text in (PROB_THRESH_HELP, NMS_THRESH_HELP):
        assert "(0-1)" in text
    assert "0.479" in PROB_THRESH_HELP        # the model's own prob default
    assert "Clear the field" in PROB_THRESH_HELP
    assert "0.3" in NMS_THRESH_HELP           # ...and its nms default
    assert "Blank =" in NMS_THRESH_HELP


def test_the_playground_shows_the_pipeline_strings_not_its_own():
    from review.features.image_browser.playground import tab_view
    assert tab_view.PROB_THRESH_HELP is PROB_THRESH_HELP
    assert tab_view.NMS_THRESH_HELP is NMS_THRESH_HELP


def test_the_analyze_form_shows_the_pipeline_strings_not_its_own():
    from analyze.seg_params import _threshold_help
    assert _threshold_help() == (PROB_THRESH_HELP, NMS_THRESH_HELP)


def _label_of(row_or_cell):
    """The row's ``QLabel``, found from either the row or its right-hand cell."""
    from PySide6.QtWidgets import QLabel
    row = row_or_cell if row_or_cell.layout().count() == 2 else row_or_cell.parent()
    return row.findChild(QLabel)


def test_the_playground_row_helper_puts_the_tooltip_on_the_label(qtbot):
    """The actual defect: an explanation that answers only over the entry box is
    one most people never find."""
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    from review.features.image_browser.playground.tab_view import _row

    parent = QWidget()
    QVBoxLayout(parent)
    qtbot.addWidget(parent)
    cell = _row(parent, "Probability", tooltip=PROB_THRESH_HELP)

    row = cell.parent()
    assert row.toolTip() == PROB_THRESH_HELP
    assert _label_of(row).toolTip() == PROB_THRESH_HELP


def test_the_analyze_row_helper_puts_the_tooltip_on_the_label(qtbot):
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    from analyze.form_chrome import AnalyzeFormChromeMixin

    parent = QWidget()
    QVBoxLayout(parent)
    qtbot.addWidget(parent)
    cell = AnalyzeFormChromeMixin._row(AnalyzeFormChromeMixin(), parent,
                                   "Nucleus probability", tooltip=PROB_THRESH_HELP)

    row = cell.parent()
    assert row.toolTip() == PROB_THRESH_HELP
    assert _label_of(row).toolTip() == PROB_THRESH_HELP


@pytest.mark.parametrize("builder", ["playground", "analyze"])
def test_a_row_without_a_tooltip_gains_none(qtbot, builder):
    """The keyword is optional, and an unadorned row must stay unadorned — a
    blanket empty tooltip would swallow one set later by a caller."""
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    parent = QWidget()
    QVBoxLayout(parent)
    qtbot.addWidget(parent)
    if builder == "playground":
        from review.features.image_browser.playground.tab_view import _row
        cell = _row(parent, "Zoom")
    else:
        from analyze.form_chrome import AnalyzeFormChromeMixin
        cell = AnalyzeFormChromeMixin._row(AnalyzeFormChromeMixin(), parent, "Zoom")
    assert cell.parent().toolTip() == ""
