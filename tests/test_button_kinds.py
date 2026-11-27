"""Each of the four kinds of button draws as the design language says (``ui/buttons.py``).

Measured on a rendered button under the application stylesheet, in every palette: the fill a grab shows just
inside the button's edge, the ink of its label and the weight of its font — not the ``variant`` property, which
a button carries whether or not a rule reads it.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ui import buttons, theme


@pytest.fixture(params=theme.available_themes())
def sheet(qapp, request):
    theme.apply_theme(request.param)
    qapp.setStyleSheet(theme.qss())
    yield theme
    theme.apply_theme("blue")
    qapp.setStyleSheet("")


def _render(qapp, kind, *, enabled=True):
    host = QWidget()
    host.setObjectName("probeHost")
    lay = QVBoxLayout(host)
    b = buttons.button("Export", kind, host)
    b.setEnabled(enabled)
    lay.addWidget(b)
    host.resize(160, 60)
    host.show()
    qapp.processEvents()
    # From a grab of the host, not of the button: a button with no fill grabs as transparent on its own,
    # and what the reader sees through it is the page behind.
    img = host.grab().toImage()
    g = b.geometry()
    fill = img.pixelColor(g.left() + 4, g.center().y()).name().upper()
    ink = b.palette().color(QPalette.ButtonText).name().upper()
    weight = b.font().weight()
    host.close()
    return fill, ink, weight


@pytest.mark.parametrize("kind, fill, ink, weight", [
    (buttons.PRIMARY, "accent", "on_accent", QFont.Weight.DemiBold),
    (buttons.DEFAULT, "control", "text_primary", QFont.Weight.Medium),
    (buttons.QUIET, "canvas", "text_secondary", QFont.Weight.Medium),
    (buttons.DESTRUCTIVE, "control", "danger", QFont.Weight.Medium),
])
def test_each_kind_draws_its_fill_ink_and_weight(qapp, sheet, kind, fill, ink, weight):
    c = sheet.Colors
    got = _render(qapp, kind)
    want = (getattr(c, fill).upper(), getattr(c, ink).upper(), weight)
    assert got == want, f"{kind}: drawn (fill, ink, weight) {got}, the kind is {want}"


@pytest.mark.parametrize("kind", buttons.KINDS)
def test_a_disabled_button_of_any_kind_reads_disabled(qapp, sheet, kind):
    _fill, ink, _weight = _render(qapp, kind, enabled=False)
    assert ink == sheet.Colors.text_disabled.upper()


def test_set_kind_repaints_a_built_button(qapp, sheet):
    host = QWidget()
    b = buttons.default("Run", host)
    host.show()
    qapp.processEvents()
    buttons.set_kind(b, buttons.PRIMARY)
    assert b.palette().color(QPalette.ButtonText).name().upper() == sheet.Colors.on_accent.upper()
    host.close()


def test_a_kind_that_is_not_one_of_the_four_is_refused():
    with pytest.raises(ValueError, match="not a kind of button"):
        buttons.button("Run", "secondary")
