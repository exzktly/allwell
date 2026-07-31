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
