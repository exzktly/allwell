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


def _drawn_header_labels(plate):
    """Every column number and row letter the plate paints, as ``(text, rect,
    advance, line height)`` in the font the painter held when it drew it.

    Asserted at the painter — the boundary — not through a helper the fix adds:
    a plate paints its labels from ``paintEvent`` and nothing else says what
    reached the screen."""
    import widgets.well_plate_selector as wps
    from PySide6.QtGui import QFontMetricsF, QPainter

    drawn = []

    class _Spy(QPainter):
        def drawText(self, *args):  # Qt naming
            if len(args) == 3 and isinstance(args[2], str):
                metrics = QFontMetricsF(self.font())
                drawn.append((args[2], args[0], metrics.horizontalAdvance(args[2]), metrics.height()))
            return super().drawText(*args)

    real = wps.QPainter
    wps.QPainter = _Spy
    try:
        plate.grab()  # the plate paints itself into a pixmap, through the module's QPainter
    finally:
        wps.QPainter = real
    return [d for d in drawn if d[0].isdigit() or d[0] in "ABCDEFGH"]


def _own_metrics(plate):
    """The grid's own font, measured the way the spy measures the painted one."""
    from PySide6.QtGui import QFontMetricsF
    return QFontMetricsF(plate._grid.font())


def test_the_column_numbers_and_row_letters_fit_a_shrunk_plate(qapp):
    """A plate fitted small (Analyze's phone Progress view) kept drawing its labels at the widget's font: "10 11 12"
    ran together and the letters A–H stood on one another. Each label must fit the pitch it is centred in.

    Fixture: the plate is made small enough that the widget's own font is wider than a column — at the default size
    "12" is ~12 px and the pitch here is ~11 px — so a plate that draws at its own font fails."""
    plate = WellPlateSelector()
    # Capped the way Analyze's fit caps it (analyze/phone_view.py): a plate has a floor of its own, so a bare
    # resize() leaves it roomier than this.
    plate.setMaximumSize(150, plate.heightForWidth(150))
    plate.resize(150, plate.heightForWidth(150))
    plate.show()
    qapp.processEvents()
    _lab, cell, _ox, _oy = plate._grid._metrics()
    own = _own_metrics(plate)
    assert own.horizontalAdvance("12") > cell, "fixture too roomy: the plate's own font already fits a column"

    labels = _drawn_header_labels(plate)
    assert len(labels) == 12 + 8, f"expected 12 column numbers and 8 row letters, drew {len(labels)}"
    for text, rect, advance, height in labels:
        if text.isdigit():
            assert advance <= rect.width() + 0.5, f"column {text!r} is {advance:.1f}px wide in a {rect.width():.1f}px column"
        else:
            assert height <= rect.height() + 0.5, f"row {text!r} is {height:.1f}px tall in a {rect.height():.1f}px row"


def test_a_roomy_plate_keeps_its_labels_at_the_widget_font(qapp):
    """The shrink is for a plate that needs it: at full size the labels are drawn at the widget's own size."""
    plate = WellPlateSelector()
    plate.resize(520, 360)
    plate.show()
    qapp.processEvents()
    labels = _drawn_header_labels(plate)
    own = _own_metrics(plate)
    assert {round(a, 1) for text, _r, a, _h in labels if text == "12"} == {round(own.horizontalAdvance("12"), 1)}
