"""The design language's four measured checks (``tests/_layout_checks.py``) find what they are for, on built widgets.

They run on every page of the every-page render test, which takes minutes; these build the case each check is for
— and the case it must pass — in a window of a few widgets.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QComboBox, QVBoxLayout, QWidget

from tests import _layout_checks as lc
from ui import buttons


def _window(qtbot, *children, width=900):
    win = QWidget()
    qtbot.addWidget(win)
    lay = QVBoxLayout(win)
    for c in children:
        lay.addWidget(c)
    win.resize(width, 400)
    win.show()
    for _ in range(3):
        QApplication.processEvents()
    return win


def test_one_primary_finds_a_second_primary_button(qtbot):
    win = _window(qtbot, buttons.primary("Run"), buttons.primary("Export"), buttons.default("Clear"))
    assert [f.check for f in lc.one_primary(win)] == ["primary"]
    assert lc.one_primary(_window(qtbot, buttons.primary("Run"), buttons.default("Clear"))) == []


def test_band_is_one_row_finds_a_wrapped_band(qtbot):
    from widgets.control_band import ControlBand

    band = ControlBand()
    for i in range(12):
        band.add_scope("", buttons.default(f"Control number {i}"))
    assert [f.check for f in lc.band_is_one_row(_window(qtbot, band, width=400))] == ["band rows"]
    one = ControlBand()
    one.add_scope("", buttons.default("Channel"))
    assert lc.band_is_one_row(_window(qtbot, one)) == []


def test_labels_outside_controls_finds_a_painted_prefix(qtbot):
    from widgets.labeled_combo import LabeledComboBox

    combo = LabeledComboBox("Channel")
    combo.addItems(["GFP"])
    assert [f.check for f in lc.labels_outside_controls(_window(qtbot, combo))] == ["label inside"]
    plain = QComboBox()
    plain.addItems(["GFP"])
    assert lc.labels_outside_controls(_window(qtbot, plain)) == []


def test_no_empty_band_finds_a_strip_with_nothing_in_it(qtbot):
    from widgets.control_band import ControlBand

    assert [f.check for f in lc.no_empty_band(_window(qtbot, ControlBand()))] == ["empty band"]
    full = ControlBand()
    full.add_scope("", buttons.default("Channel"))
    assert lc.no_empty_band(_window(qtbot, full)) == []
