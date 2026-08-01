"""Bare-`QApplication` widget tests for `widgets.labeled_combo.LabeledComboBox`.

Same pattern as `tests/test_path_field.py` — never the `viewer_app`/`loaded_app`
full-app fixtures (CLAUDE.md §2's per-test RSS leak).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication  # noqa: E402

from widgets.labeled_combo import LabeledComboBox  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_current_text_and_signals_are_unaffected_by_the_label(qapp):
    combo = LabeledComboBox("Channel")
    combo.addItems(["RFP", "GFP"])

    seen = []
    combo.currentIndexChanged.connect(seen.append)
    combo.setCurrentIndex(1)

    # The label is a paint-time prefix only — the combo's own value API
    # still reports the plain item text, so existing wiring that reads
    # currentText()/currentIndex() is unaffected.
    assert combo.currentText() == "GFP"
    assert combo.currentIndex() == 1
    assert seen == [1]


def test_label_accessor_and_setter(qapp):
    combo = LabeledComboBox("Channel")
    assert combo.label() == "Channel"
    combo.setLabel("Property")
    assert combo.label() == "Property"


def test_size_hint_grows_to_fit_the_label_prefix(qapp):
    labeled = LabeledComboBox("Channel")
    labeled.addItems(["RFP"])
    bare = LabeledComboBox("")
    bare.addItems(["RFP"])

    assert labeled.sizeHint().width() > bare.sizeHint().width()


def test_size_hint_tracks_the_current_item_not_the_widest_one(qapp):
    """Regression: plain QComboBox sizes to the widest item across the whole
    list (AdjustToContentsOnFirstShow measures every item up front), so a
    combo showing "Property: N" sat as wide as its longest possible entry
    ("Property: Fraction Threshold On") even when a short one was selected —
    wasted row space on every shorter selection. LabeledComboBox measures
    only what it's actually painting."""
    combo = LabeledComboBox("Property")
    combo.addItems(["N", "Fraction Threshold On"])

    combo.setCurrentIndex(0)
    short_width = combo.sizeHint().width()
    combo.setCurrentIndex(1)
    long_width = combo.sizeHint().width()

    assert short_width < long_width
    # The short hint must track "Property: N", not fall back to the widest
    # item — leave real headroom below the long hint, not just any delta.
    assert short_width < long_width - 50


def test_size_hint_shrinks_back_when_selection_returns_to_a_short_item(qapp):
    combo = LabeledComboBox("Property")
    combo.addItems(["N", "Fraction Threshold On"])
    combo.setCurrentIndex(0)
    baseline = combo.sizeHint().width()
    combo.setCurrentIndex(1)
    combo.setCurrentIndex(0)
    assert combo.sizeHint().width() == baseline


def test_paint_event_requests_relayout_when_the_hint_changes(qapp):
    """The combo's ideal width can change from a route with signals blocked
    (a common "seed without re-triggering the handler" pattern) — Qt still
    schedules a repaint on any state change regardless of blockSignals, so
    paintEvent is where a stale hint gets corrected. updateGeometry() calls
    are the observable signal here; QWidget doesn't expose "am I stale"."""
    from unittest.mock import patch

    combo = LabeledComboBox("Property")
    combo.addItems(["N", "Fraction Threshold On"])
    combo.resize(combo.sizeHint())
    combo.show()
    qapp.processEvents()

    blocked = combo.blockSignals(True)
    try:
        combo.setCurrentIndex(1)
    finally:
        combo.blockSignals(blocked)

    with patch.object(combo, "updateGeometry") as mock_update:
        combo.repaint()  # force a synchronous, real paintEvent
        assert mock_update.called


def test_painted_text_falls_back_to_the_bare_label_with_no_current_text():
    """Before a caller populates the combo (or an empty dataset leaves it
    with none), currentText() is "" — the box must still read "Timepoint",
    not collapse to a blank sliver now that its width tracks painted content
    rather than a fixed/minimum floor."""
    combo = LabeledComboBox("Timepoint")
    assert combo.count() == 0
    assert combo.currentText() == ""
    assert combo._painted_text() == "Timepoint"
    assert combo.sizeHint().width() > 0


def test_painted_text_is_empty_for_an_unlabeled_empty_combo():
    combo = LabeledComboBox("")
    assert combo._painted_text() == ""


def test_paint_event_draws_the_same_text_size_hint_measured(qapp):
    """Regression: paintEvent used to compute its own prefix condition
    separately from sizeHint's — the two agreed whenever there was a current
    item, but diverged for an empty combo (sizeHint fell back to the bare
    label; paintEvent's own check painted nothing at all, a blank sliver).
    paintEvent must call the one source of truth, not a second copy of the
    same logic."""
    from unittest.mock import patch

    combo = LabeledComboBox("Timepoint")  # no items — the divergent case
    combo.resize(combo.sizeHint())
    combo.show()
    qapp.processEvents()

    with patch.object(combo, "_painted_text", wraps=combo._painted_text) as spy:
        combo.repaint()
        assert spy.called
