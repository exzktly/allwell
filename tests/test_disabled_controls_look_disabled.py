"""A control the app greys out must actually look greyed out.

Reported for the Spatial tab's Timepoint picker at Summary level:
``tab_view.py`` calls ``tp_cb.setEnabled(not is_summary)`` and the box on screen
was indistinguishable from a live one. The call worked — the popup would not
open — but the stylesheet gave ``QComboBox`` no disabled appearance, so the
widget rendered pixel-identical in both states. Measured before the fix:

    LabeledComboBox: identical=True  fg_on=#e6e9ef  fg_off=#e6e9ef

Setting ``color`` in a base QSS rule is what made the omission total. A
stylesheet colour with no ``:disabled`` variant is applied in every state, so
the palette's disabled group never gets a look in — which is also why the
``LabeledComboBox`` custom ``paintEvent``, which reads
``palette().color(foregroundRole())``, painted the enabled colour too.

The assertion is on the rendered pixels rather than on the QSS text or on
``isEnabled()``. ``isEnabled()`` was already ``False`` throughout and told us
nothing; a QSS-text check would pass the day someone expresses the same rule a
different way. What the user sees is the artifact, so that is what is measured.

Only classes the application actually disables somewhere are listed. ``QTextEdit``
/ ``QPlainTextEdit`` share the ``QLineEdit`` base rule but not its ``:disabled``
variant — no code disables either (every ``…_edit`` the app greys is a
``QLineEdit``), so the gap is recorded here rather than papered over with a rule
describing nothing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit,
    QPushButton, QRadioButton, QSpinBox, QToolButton,
)

import theme
from widgets.labeled_combo import LabeledComboBox
from widgets.styled_slider import StyledSlider


def _make_combo():
    cb = QComboBox()
    cb.addItem("4.0h", 4.0)
    return cb


def _make_labeled_combo():
    cb = LabeledComboBox("Timepoint")
    cb.addItem("4.0h", 4.0)
    return cb


#: (name, factory) for every interactive class the app calls setEnabled(False)
#: on. Grouped by the call site that proves each belongs here.
CONTROLS = (
    # spatial/tab_view.py — the Timepoint and Metric pickers at Summary level.
    ("QComboBox", _make_combo),
    ("LabeledComboBox", _make_labeled_combo),
    # analyze_tab_form.py, merge_plates/view.py, analyze_remote_dialog.py.
    ("QLineEdit", lambda: QLineEdit("text")),
    # import_loose/view.py, analyze_tab.py, playground/tab_view.py.
    ("QCheckBox", lambda: QCheckBox("log x")),
    # cell_gating tab_view — the per-layer source radios.
    ("QRadioButton", lambda: QRadioButton("Per-channel")),
    ("QPushButton", lambda: QPushButton("Run")),
    ("QToolButton", lambda: QToolButton()),
    ("QSpinBox", lambda: QSpinBox()),
    ("QDoubleSpinBox", lambda: QDoubleSpinBox()),
    # barplot/tab_view.py:193 — the violin smoothing slider, disabled until the
    # Violin display is picked. Paints its own disabled state (paintEvent reads
    # isEnabled) rather than relying on the stylesheet.
    ("StyledSlider", lambda: StyledSlider()),
)


@pytest.fixture(autouse=True)
def _stylesheet():
    """The rule under test lives in the app stylesheet, so it must be installed.

    Set rather than swapped-and-restored: the fast lane already runs with it
    applied, and every case here reads it the same way.
    """
    QApplication.instance().setStyleSheet(theme.qss())


def _rendered(widget) -> set[str]:
    widget.resize(160, 28)
    image = widget.grab().toImage()
    return {
        image.pixelColor(x, y).name()
        for x in range(0, image.width(), 2)
        for y in range(0, image.height(), 2)
    }


@pytest.mark.parametrize("name,factory", CONTROLS, ids=[c[0] for c in CONTROLS])
def test_a_disabled_control_renders_differently(name, factory, qtbot):
    enabled, disabled = factory(), factory()
    for widget in (enabled, disabled):
        qtbot.addWidget(widget)
        widget.ensurePolished()
    disabled.setEnabled(False)

    assert disabled.isEnabled() is False, "setEnabled did not take."
    assert _rendered(enabled) != _rendered(disabled), (
        f"A disabled {name} renders pixel-identically to an enabled one, so a "
        "control the app has greyed out reads as live. Give it a ':disabled' "
        "rule in theme.qss (or paint the state itself, as StyledSlider does)."
    )


def test_the_combo_foreground_actually_changes(qtbot):
    """The specific mechanism, named.

    The pixel comparison above would also pass on a background-only change.
    This pins the text colour, which is what made the Timepoint picker read as
    live: the label was painted in the enabled foreground.
    """
    cb = _make_labeled_combo()
    qtbot.addWidget(cb)
    cb.ensurePolished()
    live = cb.palette().color(cb.foregroundRole()).name()
    cb.setEnabled(False)
    greyed = cb.palette().color(cb.foregroundRole()).name()
    assert live != greyed, (
        "A disabled combo's foreground is still the enabled colour, so "
        "LabeledComboBox.paintEvent — which reads the palette — paints its "
        "label as though the control were live."
    )
