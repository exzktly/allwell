"""A plate does not change size because something beside it did.

Reported against two windows at once — the blank-pairing editor and Home's
Variables canvas, where "an undo button growing" moved the plate — and it was
one defect in the shared widget rather than two in the hosts.

``plate_geometry`` sets ``QSizePolicy.setHeightForWidth(True)`` on the
``WellPlateSelector``, and that widget did not implement ``heightForWidth``: the
aspect lock lived on the ``_PlateGrid`` one layout down, where no host's layout
ever asked, so ``QWidget``'s default answered -1 and the flag did nothing. A box
layout then took the plate's height whenever a sibling wanted more. Measured in
the pairing editor: a status line *below* the plate wrapping to a second line
took it from 210px to 168px on a window whose own size never changed.

Forwarding it is necessary and not sufficient — a layout treats
``heightForWidth`` as a hint — so the widget also holds its own minimum at the
aspect-locked height for its current width. These tests are the geometry, not
the wiring, so they are written against the widget rather than against any host.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from widgets.plate_geometry import PLATE_ALIGNMENT, apply_plate_geometry  # noqa: E402
from widgets.well_plate_selector import WellPlateSelector  # noqa: E402

ROLES = ("rail", "picker", "status", "editor", "canvas")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _column(qapp, role, width=480, height=420):
    """A plate above a note, above something stretchy, in a tight column.

    The stretchy sibling and the tight height are the fixture, not decoration.
    Written with a trailing ``addStretch`` and room to spare, the column simply
    absorbs a growing label into the slack and the plate never moves — so the
    test passed with the fix removed, which is the fixture agreeing with its
    author rather than measuring anything (CLAUDE.md §7.8 rule 2). Both reported
    windows have a stretchy widget under the plate — the pairing editor's blank
    list, the Variables canvas's chips — competing for the same height, and that
    competition is the whole defect.
    """
    host = QWidget()
    layout = QVBoxLayout(host)
    plate = WellPlateSelector(host)
    apply_plate_geometry(plate, role=role)
    layout.addWidget(plate, 0, PLATE_ALIGNMENT)
    note = QLabel("short")
    note.setWordWrap(True)
    layout.addWidget(note)
    filler = QWidget(host)
    filler.setMinimumHeight(80)
    filler.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
    layout.addWidget(filler, 1)
    host.show()
    # Tall enough for the plate this role actually renders at, plus slack for
    # the rest. A column too short to hold the plate at all is a different
    # complaint — there Qt must shrink something and no floor can stop it —
    # while the defect under test is a sibling taking height that was there.
    host.resize(width, max(height, plate.heightForWidth(width) + 200))
    qapp.processEvents()
    return host, layout, plate, note


@pytest.mark.parametrize("role", ROLES)
def test_a_sibling_that_grows_does_not_shrink_the_plate(qapp, role):
    """Every role, because the defect was in the widget they all share."""
    host, layout, plate, note = _column(qapp, role)
    before = (plate.width(), plate.height())

    note.setText("a much longer sentence that will certainly wrap onto two or "
                 "even three lines in a column this wide, the way a status "
                 "message growing under the plate does")
    qapp.processEvents()
    assert (plate.width(), plate.height()) == before, "a wrapping label moved it"

    layout.insertWidget(2, QPushButton("Undo", host))     # a button appears
    qapp.processEvents()
    assert (plate.width(), plate.height()) == before, "a new button moved it"

    note.setText("short")
    qapp.processEvents()
    assert (plate.width(), plate.height()) == before, "shrinking the label moved it"


def test_the_selector_answers_height_for_width_at_all(qapp):
    """The flag was set on a widget that did not implement it.

    Asserted as a real height rather than "not -1": ``QWidget``'s default is -1,
    and a forwarding that returned any positive constant would satisfy the
    weaker claim while breaking the aspect it exists to keep.
    """
    plate = WellPlateSelector()
    apply_plate_geometry(plate, role="editor")
    assert plate.hasHeightForWidth()
    assert plate.sizePolicy().hasHeightForWidth()

    narrow, wide = plate.heightForWidth(240), plate.heightForWidth(400)
    assert narrow > 0 and wide > narrow, (narrow, wide)
    # It tracks the width rather than being a constant: a 12x8 grid grows about
    # two thirds as fast in height as in width.
    assert 0.5 < (wide - narrow) / (400 - 240) < 0.85


def test_a_plate_still_scales_down_in_a_narrow_container(qapp):
    """The floor must track the width, not pin the plate at its ceiling.

    Holding a constant minimum would stop the crushing and cost the property the
    caps were written for: a plate in a container narrower than its cap keeps
    its aspect instead of overflowing.
    """
    seen = []
    for width in (470, 380, 300, 240):
        host, _layout, plate, _note = _column(qapp, "canvas", width=width, height=600)
        assert plate.width() <= width, "the plate overflowed its container"
        seen.append((plate.width(), plate.height()))

    widths = [w for w, _h in seen]
    heights = [h for _w, h in seen]
    assert widths == sorted(widths, reverse=True), widths
    assert heights == sorted(heights, reverse=True), heights
    assert len(set(heights)) > 1, "the height stopped tracking the width"
