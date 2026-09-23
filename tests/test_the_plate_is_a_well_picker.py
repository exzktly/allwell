"""The converter's plate can be used to choose which wells convert.

Picking a subset on the plate was wired up and then could not be used. The
plate belongs to ``PlateProgress``, which builds it as a **status map** —
``apply_plate_geometry(role="status")`` makes it report-only and the
constructor turns off drag-select and the row/column headers — because for
most of its life that is what it is. Turning the selection *mode* on over the
top left exactly one way to pick: clicking wells one at a time.

On a 96-well plate, converting all but three wells was ninety-three clicks.
Nothing said the plate could be clicked at all, the selection drew as a thin
inset rim so that it would not fight the run's progress colours, and the box
around it is captioned "Convert Progress".

So this asserts the affordances rather than the API underneath them: a
control reachable only through a method call is not a control (CLAUDE.md
§7.6). Each of these was off, and each is what a person actually uses.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication  # noqa: E402

from datasets.core.converter_dashboard import ConverterDashboard  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def board(qapp):
    dash = ConverterDashboard()
    dash.set_wells([f"{row}{col:02d}" for row in "ABCDEFGH"
                    for col in range(1, 13)])
    yield dash
    dash.deleteLater()


# ── the ways a person picks wells, all of which were off ────────────────────

def test_the_plate_offers_every_way_of_picking(board):
    """Four settings, not one. The selection *mode* was the only one turned
    on, which left clicking wells one at a time as the whole interface."""
    plate = board._plate

    assert plate._grid._mode == "select"
    assert not plate._grid._report_only, (
        "report-only drops the corner select-all and the pointing-hand cursor")
    assert plate._grid._drag_select, "no way to sweep a block of wells"
    assert plate._grid._rc_selectable, "no way to take a row or a column"


def test_a_run_takes_all_of_them_back(board):
    """While a run is in flight the plate is the progress display, and a
    selection changed mid-run would describe a conversion other than the one
    being reported on."""
    board.set_selection_enabled(False)
    plate = board._plate

    assert plate._grid._mode == "passive"
    assert plate._grid._report_only
    assert not plate._grid._drag_select
    assert not plate._grid._rc_selectable


def test_picking_is_restored_when_the_run_ends(board):
    board.set_selection_enabled(False)
    board.set_selection_enabled(True)

    assert board._plate._grid._drag_select
    assert not board._plate._grid._report_only


# ── and the page says what the plate is for ─────────────────────────────────

def test_the_plate_says_it_converts_everything_until_you_pick(board):
    """A plate with nothing picked and a plate with everything picked convert
    the same wells and look completely different. The sentence is what makes
    the empty one legible."""
    assert "all 96 well(s)" in board._pick_note.text()
    assert "click" in board._pick_note.text().lower()


def test_the_count_names_what_converts_and_what_is_ignored(board):
    """"Ignored" in those words, because that is the half a person is
    choosing when they pick three wells out of ninety-six."""
    board.set_selected_wells(["A01", "A02", "B01"])

    note = board._pick_note.text()
    assert "3 of 96 well(s)" in note, note
    assert "93 ignored" in note, note


def test_the_note_follows_the_well_set(board):
    """The total it quotes is the source's, which changes when the source
    does — a note still saying 96 under a 12-well plate is worse than none."""
    board.set_wells(["A01", "A02", "A03"])

    assert "all 3 well(s)" in board._pick_note.text()


# ── the two buttons, which are the plate's own row made visible ─────────────

def test_convert_all_clears_the_selection(board):
    """Clearing is how "convert everything" is said, which is the opposite of
    what a button labelled "None" would suggest — hence the wording."""
    board.set_selected_wells(["A01", "A02"])
    assert board.selected_wells() == ["A01", "A02"]

    board._pick_all.click()

    assert board.selected_wells() == []
    assert "all 96 well(s)" in board._pick_note.text()


def test_invert_is_how_all_but_these_is_said(board):
    """The one thing an include-only model cannot express in fewer clicks
    than there are wells: skip three of ninety-six."""
    board.set_selected_wells(["A01", "B02", "C03"])

    board._pick_invert.click()

    picked = board.selected_wells()
    assert len(picked) == 93
    assert "A01" not in picked and "B02" not in picked and "C03" not in picked
    assert "A02" in picked
    assert "93 of 96 well(s)" in board._pick_note.text()


def test_inverting_nothing_picks_every_well(board):
    """The same conversion either way, but it says so on the plate rather
    than leaving a button that appears to have done nothing."""
    assert board.selected_wells() == []

    board._pick_invert.click()

    assert len(board.selected_wells()) == 96


def test_picking_emits_what_the_page_listens_for(board):
    """The page connects ``wellsSelected`` to rebuild its readout and its
    run parameters; a selection the signal does not report is a selection the
    conversion never hears about."""
    seen: list[list[str]] = []
    board.wellsSelected.connect(seen.append)

    board._plate.setSelectedWellIds(["H12"])

    assert seen and seen[-1] == ["H12"]


def test_the_buttons_go_dead_while_a_run_is_in_flight(board):
    board.set_selection_enabled(False)

    assert not board._pick_row.isEnabled()
    assert board._pick_note.text() == "", (
        "a note about picking, beside controls that cannot be used")
