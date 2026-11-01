"""A spin box is never squeezed narrower than the number in it.

Reported: the nanowell focus threshold on the Nanowells page and on the QC panel clipped its value after two or
three digits. Qt lets a layout shrink a ``QSpinBox`` to its *minimum* size hint, which is narrower than its size
hint and ignores the stylesheet's ``min-width`` floor under the text area, so any row given less width than it asks
for took the shortfall out of the number: the QC panel's rim-focus row asked for about 510 px, and in a 330 px
panel its box drew "75" in a 14 px text area against the 15 px it needs.

``widgets.spin_box.SpinBox`` / ``DoubleSpinBox`` report their size hint as their minimum, every spin box the
application builds is one of them (the guard below), and the QC row wraps onto two lines rather than asking for a
width the panel does not have.
"""

from __future__ import annotations

import ast

import pytest
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QSpinBox, QStyle, QStyleOptionFrame, QWidget)

from tests._repo_scan import parse_source, python_sources, rel
from ui import theme
from widgets.spin_box import DoubleSpinBox, SpinBox

pytestmark = pytest.mark.qt


def _text_area(spin) -> int:
    """The width the style draws the spin box's number in."""
    le = spin.findChild(QLineEdit)
    opt = QStyleOptionFrame()
    le.initStyleOption(opt)
    return le.style().subElementRect(QStyle.SE_LineEditContents, opt, le).width()


def _needs(spin) -> int:
    fm = spin.findChild(QLineEdit).fontMetrics()
    return max(fm.horizontalAdvance(spin.prefix() + spin.textFromValue(v) + spin.suffix())
               for v in (spin.minimum(), spin.maximum(), spin.value()))


def _squeezed(qtbot, qapp, cls):
    """*cls* in a row whose window is as narrow as it can be — every widget at its minimum width, which a layout
    falls back to when the row is short of room and its neighbours cannot give way (the QC row's labels could not).
    Qt clamps a window resized below its minimum to that minimum, so this is the narrowest the row ever gets."""
    qapp.setStyleSheet(theme.qss())
    host = QWidget()
    qtbot.addWidget(host)
    layout = QHBoxLayout(host)
    layout.addWidget(QLabel("rim below", host))
    spin = cls(host)
    spin.setRange(10, 100)
    spin.setValue(100)
    layout.addWidget(spin)
    layout.addWidget(QLabel("of the well's sharpest rims", host))
    host.show()
    host.resize(10, 40)
    qtbot.waitExposed(host)
    qtbot.waitUntil(lambda: host.width() == host.minimumSizeHint().width(), timeout=2000)
    return spin, host                  # the host is held by the caller, or its children go with it


@pytest.mark.parametrize("cls", [SpinBox, DoubleSpinBox])
def test_a_row_short_of_room_does_not_squeeze_the_number(qtbot, qapp, cls):
    spin, _host = _squeezed(qtbot, qapp, cls)
    assert _text_area(spin) >= _needs(spin), f"{_text_area(spin)} px for a number that needs {_needs(spin)} px"


def test_a_plain_qspinbox_in_the_same_row_is_squeezed_which_is_why_the_subclass_exists(qtbot, qapp):
    """The same row with Qt's own spin box: the layout takes the filler's wish out of the number. If this ever
    stops being squeezed, Qt has changed and the subclass can go."""
    spin, _host = _squeezed(qtbot, qapp, QSpinBox)
    assert _text_area(spin) < _needs(spin)


def test_the_qc_panel_s_rim_focus_number_fits_a_narrow_panel(qtbot, qapp):
    """The reported control, built by its own builder, in a panel narrower than its row used to ask for."""
    from review.domain.qc_params import QCParams
    from review.features.qc.nanowell_focus_view import build_row

    qapp.setStyleSheet(theme.qss())
    host = QWidget()
    qtbot.addWidget(host)
    row, widgets = build_row(host, QCParams(), 27, 300)
    host.resize(330, 120)
    row.resize(330, 120)
    host.show()
    qtbot.waitExposed(host)
    spin = widgets["nanowell_focus_fraction"]
    assert _text_area(spin) >= _needs(spin)
    assert row.minimumSizeHint().width() <= 330, "the row still asks for more than a narrow panel has"


def _bare_spin_box_calls() -> list[str]:
    sites = []
    for path in python_sources():
        if rel(path) == "widgets/spin_box.py":
            continue
        for node in ast.walk(parse_source(path)):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
                if name in ("QSpinBox", "QDoubleSpinBox"):
                    sites.append(f"{rel(path)}:{node.lineno}")
    return sites


def test_the_application_builds_no_bare_qt_spin_box():
    """Every spin box the app builds is ``widgets.spin_box.SpinBox`` / ``DoubleSpinBox``: a bare ``QSpinBox`` is
    squeezed by the first row that is short of room."""
    sites = _bare_spin_box_calls()
    assert not sites, "build these with widgets.spin_box.SpinBox / DoubleSpinBox:\n  " + "\n  ".join(sites)

