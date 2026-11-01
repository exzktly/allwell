"""A number typed into an entry field is applied once, when it is finished.

Qt's spin boxes emit ``valueChanged`` for every keystroke by default, and a
``QLineEdit`` emits ``textChanged`` the same way. Wired to a recompute, typing
"200" into the Distribution tab's Bins box drew a 2-bin histogram, then a
20-bin one, then the 200 asked for; the Spatial tab's ε box ran DBSCAN once per
digit; the Properties axis limits pushed "-", "-0" and "-0." to the plot on the
way to "-0.5".

``widgets.spin_box.SpinBox`` / ``DoubleSpinBox`` — the only spin boxes the
application builds — now commit typed text on Enter or focus-out, and the
Properties panel applies a bound ``QLineEdit`` on ``editingFinished``. These
tests type into the real widgets, keystroke by keystroke, and count what came
out. The arrow and programmatic paths are pinned too, because a fix that made
the arrows wait for Enter would be a different bug.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit

from widgets.spin_box import DoubleSpinBox, SpinBox


def _type(qtbot, edit, text: str) -> None:
    edit.setFocus()
    edit.selectAll()
    qtbot.keyClicks(edit, text)


@pytest.mark.parametrize(("cls", "typed", "expected"), [
    (SpinBox, "200", 200),
    (DoubleSpinBox, "12.5", 12.5),
])
def test_typing_a_value_emits_once_on_enter(qtbot, cls, typed, expected):
    box = cls()
    qtbot.addWidget(box)
    box.setRange(0, 1000)
    box.setValue(7)
    seen: list = []
    box.valueChanged.connect(seen.append)

    _type(qtbot, box.lineEdit(), typed)
    assert seen == [], f"{cls.__name__} emitted {seen} while the number was still being typed"

    qtbot.keyClick(box.lineEdit(), Qt.Key_Return)
    assert seen == [expected]
    assert box.value() == expected


def test_typing_then_leaving_the_box_commits_the_value(qtbot):
    box = SpinBox()
    qtbot.addWidget(box)
    box.setRange(0, 1000)
    seen: list = []
    box.valueChanged.connect(seen.append)
    _type(qtbot, box.lineEdit(), "42")
    assert seen == []
    box.interpretText()  # what focus-out runs; offscreen focus changes are not delivered reliably
    assert seen == [42]


@pytest.mark.parametrize("cls", [SpinBox, DoubleSpinBox])
def test_arrows_and_set_value_still_apply_at_once(qtbot, cls):
    box = cls()
    qtbot.addWidget(box)
    box.setRange(0, 100)
    box.setValue(5)
    seen: list = []
    box.valueChanged.connect(seen.append)
    box.stepUp()
    box.setValue(50)
    assert seen == [6, 50]


def test_a_properties_text_field_applies_when_finished(qtbot):
    """Drive the panel's real binding code with a stand-in for the rest of the panel."""
    from review.features.export.sidebar_view import ExportStyleSidebar

    calls: list[str] = []
    panel = SimpleNamespace(_getters={}, _setters={})
    edit = QLineEdit()
    qtbot.addWidget(edit)
    panel._on_fields_changed = lambda: calls.append(edit.text())
    ExportStyleSidebar._bind_getter_setter(panel, "y_lim_min", edit)

    _type(qtbot, edit, "-0.5")
    assert calls == [], f"the axis limit was applied while being typed: {calls}"
    qtbot.keyClick(edit, Qt.Key_Return)
    assert calls == ["-0.5"]

    # A programmatic set (a profile, a restore, a test) still applies at once,
    # as it did when the binding rode on textChanged.
    panel._setters["y_lim_min"]("3")
    assert calls == ["-0.5", "3"]
    assert panel._getters["y_lim_min"]() == "3"
