"""The ``"canvas"`` plate role — a plate that *is* its page, not a control on it.

``widgets/plate_geometry`` had one ceiling for every plate that is not Review's
rail, on the argument that an editor plate sits in a page among other controls
and uncapped would grow to the scroll area's width. That argument is right for
the plate it was written about and wrong for the Variables tab's, where the
plate is the surface every gesture on the page operates and carries three
independent marks at once — a value's colour, a ring for an unannotated well,
and a second variable's glyph inside it. At the editor caps that resolves to
about 16.5 px of well with a 10 px glyph in it.
"""

from __future__ import annotations

import pytest

from widgets.plate_geometry import (
    PLATE_CANVAS_HEIGHT, PLATE_CANVAS_WIDTH, PLATE_MAX_HEIGHT, PLATE_MAX_WIDTH,
    apply_plate_geometry,
)
from widgets.well_plate_selector import WellPlateSelector

pytestmark = pytest.mark.qt


def _plate(qtbot, role: str):
    plate = WellPlateSelector()
    qtbot.addWidget(plate)
    apply_plate_geometry(plate, role=role)
    return plate


def test_a_canvas_plate_is_larger_than_an_editor_one(qtbot):
    """Asserted as the numbers, not as "bigger".

    "Bigger" is satisfied by one pixel, and the point of the role is a well the
    user can read a glyph inside.
    """
    canvas = _plate(qtbot, "canvas")
    assert canvas.maximumWidth() == PLATE_CANVAS_WIDTH
    assert canvas.maximumHeight() >= PLATE_CANVAS_HEIGHT
    assert PLATE_CANVAS_WIDTH > PLATE_MAX_WIDTH
    assert PLATE_CANVAS_HEIGHT > PLATE_MAX_HEIGHT


def test_a_canvas_plate_is_still_capped(qtbot):
    """Not the rail's exemption.

    Reusing ``role="rail"`` here would have been one word and would have
    inherited an argument written about Review's rail specifically, plus an
    uncapped width — which is the exact failure the width cap exists to prevent:
    the selector paints its panel across its whole allocated box while the grid
    inside sizes to the smaller of the two dimensions, so extra width buys a
    wide empty card with a small grid floating in it.
    """
    canvas = _plate(qtbot, "canvas")
    rail = _plate(qtbot, "rail")
    assert canvas.maximumWidth() < rail.maximumWidth()
    assert canvas.maximumHeight() < rail.maximumHeight()


def test_a_canvas_plate_behaves_like_an_editor_in_every_other_way(qtbot):
    """The role differs in its caps and in nothing else.

    Its host supplies its own selection actions, so the built-in All / Invert /
    Clear row stays hidden — and the Variables tab makes good on that promise
    with its own ``Select: all / none / unassigned / same value`` links, which
    the ``"editor"`` role's original host never did.
    """
    canvas = _plate(qtbot, "canvas")
    editor = _plate(qtbot, "editor")
    assert canvas.selectionMode() == editor.selectionMode() == "select"
    # The built-in action row: hidden for both, because both roles promise the
    # host supplies its own.
    assert canvas._actions_host.isHidden() == editor._actions_host.isHidden()


def test_an_unknown_role_is_refused_rather_than_silently_treated_as_rail(qtbot):
    plate = WellPlateSelector()
    qtbot.addWidget(plate)
    with pytest.raises(ValueError, match="canvas"):
        apply_plate_geometry(plate, role="enormous")
