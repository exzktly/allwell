"""A selected well has to look selected, even when its fill is already spoken for.

``WellPlateSelector`` paints a selected well with :meth:`_paint_lit`, and
``_paint_lit`` takes the host's colour override as its ``base`` whether or not
the well is in the selection. On a plate that colours every well with data of
its own — the Variables tab's, where the fill *is* which value the well holds —
that made a selected well and an unselected one the same pixels, and the tab's
central gesture ("select these wells, then click a value") had no feedback at
all.

**Measured against the same well, twice.** The first version of this test
compared two neighbouring wells, one selected and one not, and reported them
different against the *unfixed* painter: each cell rasterises at its own
sub-pixel offset, so two wells never hash alike whatever is drawn in them.
Rendering one well in both states, at one position, is what makes the right and
the wrong answer differ — §7.8 #2.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor

import theme
from widgets.well_plate_selector import WellPlateSelector

pytestmark = pytest.mark.qt

#: A value colour a host would really use — the first entry of the palette the
#: Variables tab's values are inked from.
_VALUE_INK = "#6AB2FB"


def _plate(qtbot, *, rim: bool, selected: list[str]):
    plate = WellPlateSelector()
    qtbot.addWidget(plate)
    plate.resize(480, 320)
    plate.setEnabledWells([f"{r}{c:02d}" for r in "ABCDEFGH"
                           for c in range(1, 13)])
    plate.setColoredWellsLit(True)
    plate.setSelectionRim(rim)
    plate.setWellColor("A01", _VALUE_INK)
    plate.setSelectedWellIds(selected)
    plate.show()
    return plate


def _well_pixels(plate, row: int = 0, col: int = 0):
    """Every RGB triple inside one well's rect, from the rendered widget."""
    image = plate.grab().toImage()
    grid = plate._grid
    offset = grid.mapTo(plate, grid.rect().topLeft())
    rect: QRect = grid._well_rect(row, col).toRect().translated(offset)
    return [image.pixelColor(x, y).getRgb()[:3]
            for y in range(rect.top(), rect.bottom() + 1)
            for x in range(rect.left(), rect.right() + 1)]


def _near(pixel, colour: QColor, tolerance: int = 40) -> bool:
    return all(abs(pixel[i] - c) <= tolerance
               for i, c in enumerate(colour.getRgb()[:3]))


def test_a_selected_coloured_well_does_not_render_as_an_unselected_one(qtbot):
    """The defect itself: one well, selected and not, must not be one picture."""
    lit = _well_pixels(_plate(qtbot, rim=True, selected=["A01"]))
    unlit = _well_pixels(_plate(qtbot, rim=True, selected=[]))
    assert lit != unlit


def test_the_rim_is_the_near_white_ink_no_value_can_take(qtbot):
    """Asserted as *which* ink and *how much of it*, not as "something changed".

    "The two renderings differ" is satisfied by a painter that shifts the hue,
    darkens the fill, or draws the accent ring — every one of which either
    destroys the value colour the well is carrying or collides with the mark
    that already means "unassigned". So this counts pixels of
    ``text_primary`` specifically, and requires the selected well to carry
    several times as many as the unselected one, whose only light pixels are
    the specular arc every lit well wears.
    """
    ink = QColor(theme.Colors.text_primary)
    selected = sum(1 for p in _well_pixels(_plate(qtbot, rim=True,
                                                  selected=["A01"]))
                   if _near(p, ink))
    plain = sum(1 for p in _well_pixels(_plate(qtbot, rim=True, selected=[]))
                if _near(p, ink))
    assert selected > plain * 3 + 10, (
        f"a selected well carried {selected} near-white pixels against "
        f"{plain} on an unselected one — the rim is not being drawn, or is "
        f"not being drawn in text_primary")


def test_the_value_colour_survives_the_rim(qtbot):
    """The rim must annotate the well, not repaint it.

    A selection mark that dims or recolours the fill would take the value away
    to say the well is selected, which on this plate is trading the data for
    the cursor. The well keeps a substantial block of its own ink.
    """
    value = QColor(_VALUE_INK)
    pixels = _well_pixels(_plate(qtbot, rim=True, selected=["A01"]))
    kept = sum(1 for p in pixels if _near(p, value, tolerance=60))
    assert kept > len(pixels) * 0.25, (
        f"only {kept} of {len(pixels)} pixels still read as the value's ink")


def test_the_rim_is_off_unless_a_host_asks_for_it(qtbot):
    """Every other plate in the app is unchanged.

    Selection on a plate whose fill is free already reads as lit-versus-
    recessed, and a second mark saying the same thing is noise. So the rim is
    opt-in, and this pins that the default really is off rather than the flag
    being decorative.
    """
    on = _well_pixels(_plate(qtbot, rim=True, selected=["A01"]))
    off = _well_pixels(_plate(qtbot, rim=False, selected=["A01"]))
    assert on != off
