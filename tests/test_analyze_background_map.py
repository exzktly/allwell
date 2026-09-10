"""The Analyze pane's blank-pairing editor, driven the way a user drives it.

Every test here goes through a real ``AnalyzeTab`` and the handler a control is
actually wired to — the Browse-equivalent for this feature is
``_open_background_map_editor``, the button's own slot. Asserting that the
button exists, or calling the editor's helpers one step past the closure the
button reaches, is how the per-sample control editor in Review shipped
unopenable past a lane of thousands of tests (CLAUDE.md §7.6).

That rule was written here and then not followed here. The first version of this
file opened the editor through the button and then reached *inside* for
everything after — ``_bg_map_add_blank()``, ``_bg_map_set_active(i)`` — so
thirteen tests passed while the dialog was unusable. A mutation matrix run
against them found that disconnecting the pick control, disconnecting the add
control, deleting the rename, emptying the instruction label, deleting
``setWellColors`` and ``setEnabledWells`` outright, deleting ``setWellRings``,
and painting every swatch black each left all thirteen green.

So two rules for anything added below:

* **Reach the control the way a user reaches it** — find the widget in the
  window and ``.click()`` it, or emit the plate's own paint signals. A helper
  called directly is a test of a function, not of a dialog.
* **Assert at the boundary** (§7.4 #1): what the plate was *told* —
  ``setWellColors`` / ``setWellRings`` / ``setEnabledWells`` /
  ``setWellMarker`` — not the dict those calls are derived from. A test that
  reads ``_background_map`` passes happily while nothing is drawn.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def tab(qapp):
    from analyze.tab import AnalyzeTab
    return AnalyzeTab()


def rows(tab):
    """The blank list as a user reads it: the radio in each row, in order.

    ``findChildren`` and not the layout, because that is what sees the rows a
    rebuild has orphaned but not yet destroyed — which is a real state, and one
    the editor must not leave behind mid-drag.
    """
    from PySide6.QtWidgets import QRadioButton
    return tab._bg_map_rows_host.findChildren(QRadioButton)


def pick(tab, index):
    """Click the row a user would click, rather than setting the index."""
    rows(tab)[index].click()


def drag(tab, *wells):
    """One paint gesture across *wells*, through the plate's own signals."""
    tab._bg_map_plate.wellPaintPressed.emit(wells[0])
    for well in wells[1:]:
        tab._bg_map_plate.wellPaintDragged.emit(well)
    tab._bg_map_plate.paintDragFinished.emit()


class PlateSpy:
    """What the plate was told to draw.

    The editor's entire output is these four calls; every assertion about what
    is on screen belongs here rather than on the dict behind them.
    """

    def __init__(self, plate):
        self.colors: dict = {}
        self.rings: list = []
        self.enabled: list = []
        self.markers: dict = {}
        self._plate = plate
        self._real = {
            "setWellColors": plate.setWellColors,
            "setWellRings": plate.setWellRings,
            "setEnabledWells": plate.setEnabledWells,
            "setWellMarker": plate.setWellMarker,
            "clearWellMarkers": plate.clearWellMarkers,
        }

        def colors(mapping):
            self.colors = dict(mapping)
            return self._real["setWellColors"](mapping)

        def rings(ids):
            self.rings = list(ids)
            return self._real["setWellRings"](ids)

        def enabled(ids):
            self.enabled = list(ids)
            return self._real["setEnabledWells"](ids)

        def marker(well, glyph):
            self.markers[well] = glyph
            return self._real["setWellMarker"](well, glyph)

        def clear_markers():
            self.markers = {}
            return self._real["clearWellMarkers"]()

        plate.setWellColors = colors
        plate.setWellRings = rings
        plate.setEnabledWells = enabled
        plate.setWellMarker = marker
        plate.clearWellMarkers = clear_markers

    def block_of(self, shade):
        return sorted(w for w, c in self.colors.items() if c == shade)


def test_the_button_opens_an_editor_that_is_actually_built(tab):
    """The slot the button is connected to, not the builder beneath it."""
    assert tab._bg_map_window is None
    tab._bg_map_btn.click()
    assert tab._bg_map_window is not None
    assert tab._bg_map_plate is not None
    # Built once and kept: a second click reuses the window rather than
    # stacking another one behind it.
    first = tab._bg_map_window
    tab._bg_map_btn.click()
    assert tab._bg_map_window is first


def test_painting_a_well_assigns_it_to_the_active_blank(tab):
    """The plate's own paint signals, through the handlers they are wired to."""
    tab._background_well_edit.setText("H12")
    tab._bg_map_btn.click()
    drag(tab, "A01", "A02")

    assert tab._background_map == {"H12": ["A01", "A02"]}
    assert tab._background_map_value() == "H12=A01,A02"
    # …and the form's own summary says so, because that is all a user sees
    # without opening the editor again.
    assert "H12 → 2 well(s)" in tab._bg_map_summary.text()


def test_dragging_back_over_a_claimed_well_removes_it(tab):
    """One gesture, one direction — decided by the well it started on."""
    tab._background_well_edit.setText("H12")
    tab._bg_map_btn.click()
    for well in ("A01", "A02"):
        drag(tab, well)
    assert tab._background_map["H12"] == ["A01", "A02"]

    drag(tab, "A01", "A02")          # starts on a claimed well
    assert tab._background_map["H12"] == []


def test_a_well_belongs_to_one_blank_at_a_time(tab):
    """Painting takes a well from whoever held it, rather than making a map the
    parser would refuse for claiming it twice."""
    tab._background_well_edit.setText("H12, G12")
    tab._bg_map_btn.click()
    assert list(tab._background_map) == ["H12", "G12"]

    pick(tab, 0)
    drag(tab, "A01")
    pick(tab, 1)
    drag(tab, "A01")

    assert tab._background_map == {"H12": [], "G12": ["A01"]}
    # And the value it produces parses, which is the real contract.
    from util.well_token import parse_background_well_map
    assert parse_background_well_map(tab._background_map_value()) == {"G12": ["A01"]}


def test_a_split_will_not_turn_a_sample_well_into_a_reference(tab):
    """The bug the first draft shipped, pinned.

    Blanks came off the end of the plate when the field named none, so a
    row-split took H12, H11, H10… — wells inside row H's own block. Row H's
    blank was then one of the wells another blank corrected, and the map the
    editor produced was one its own parser refuses.
    """
    tab._background_well_edit.setText("")
    tab._bg_map_btn.click()
    tab._bg_map_split("row")
    assert tab._background_map == {}
    assert "Name the blank well" in tab._bg_map_status.text()


def test_a_split_claims_the_plate_and_holds_every_blank_out_of_it(tab):
    """Every blank held out of every block, not just out of its own.

    The blanks are deliberately all in row H, so seven of the eight land in a
    block that is not theirs. A fixture with one blank per row — the obvious
    one, and the one this test first used — cannot see the difference: there
    "exclude this block's blank" and "exclude every blank" are the same answer,
    and the version that shipped a row-H blank inside row H's own block passed
    it.
    """
    from util.well_token import parse_background_well_map

    tab._background_well_edit.setText(
        "H01, H02, H03, H04, H05, H06, H07, H08")
    tab._bg_map_btn.click()
    tab._bg_map_split("row")

    mapping = tab._background_map
    assert list(mapping) == ["H01", "H02", "H03", "H04",
                             "H05", "H06", "H07", "H08"]
    # H01 sits in row H, so row H is the block it gets — and row H keeps only
    # the wells that are not somebody's blank. The seven blanks with nowhere of
    # their own take the remaining rows in order, so every row has an owner.
    assert mapping["H01"] == ["H09", "H10", "H11", "H12"]
    assert mapping["H02"][:3] == ["A01", "A02", "A03"]
    assert len(mapping["H02"]) == 12
    assert sorted({w[0] for wells in mapping.values() for w in wells}) == list(
        "ABCDEFGH")

    # No blank appears among the wells any blank corrects — the same statement
    # the parser makes by refusing a map where one does.
    corrected = {w for wells in mapping.values() for w in wells}
    assert not set(mapping) & corrected
    assert parse_background_well_map(tab._background_map_value()) == mapping
    assert "Every well is claimed" in tab._bg_map_uncovered.text()


def test_fewer_blanks_than_blocks_says_so_rather_than_inventing_one(tab):
    tab._background_well_edit.setText("H12, G12")
    tab._bg_map_btn.click()
    tab._bg_map_split("quadrant")
    assert len(tab._background_map) == 2
    assert "2 blank(s) for 4 block(s)" in tab._bg_map_status.text()
    # …and the coverage line keeps its own job, which is the count.
    assert "claimed by no blank" in tab._bg_map_uncovered.text()


def test_the_uncovered_count_is_the_runs_own_refusal_shown_early(tab):
    """What the editor says and what the run does are the same question.

    Asserted against the pipeline's own ``BlankGroups`` rather than a number
    written here, so the two cannot drift into disagreeing about which wells
    would stop the run.
    """
    from pipeline.empty_well import BlankGroups

    tab._background_well_edit.setText("H12")
    tab._bg_map_btn.click()
    drag(tab, "A01")

    groups = BlankGroups("", tab._background_map_value(),
                         corrections=("empty_well",))
    expected = groups.uncovered(tab._background_map_plate_wells())
    assert expected, "the fixture must leave something uncovered to measure"
    assert f"{len(expected)} well(s) claimed by no blank" in \
        tab._bg_map_uncovered.text()


def test_clearing_the_pairing_returns_the_run_to_pooling(tab):
    tab._background_well_edit.setText("H12")
    tab._bg_map_btn.click()
    drag(tab, "A01")
    assert tab._background_map_value()

    tab._bg_map_clear()
    assert tab._background_map_value() == ""
    assert "Pooled" in tab._bg_map_summary.text()
    assert tab._collect_run_options()["background_well_map"] == ""


def test_the_map_survives_the_sidecar_round_trip(tab):
    """Form → run options → sidecar shape → form, the loop a queued job makes."""
    tab._set_background_map("H12=A01,A02; G12=B01")
    assert tab._background_map_value() == "H12=A01,A02; G12=B01"

    fresh = type(tab)()
    fresh._apply_pipeline_info({
        "qc_options": {"background_well_map": tab._background_map_value()}})
    assert fresh._background_map == tab._background_map

    # An absent key means "this run predates pairing", never "clear the map".
    fresh._apply_pipeline_info({"qc_options": {"background_well": "H12"}})
    assert fresh._background_map == tab._background_map


def test_an_unreadable_map_leaves_the_form_pooled_and_says_so(tab):
    """A hand-edited sidecar can hold a map the parser refuses; a form that
    silently kept half of it would be worse than one that kept none."""
    tab._set_background_map("H12=A01; G12=A01")     # A01 claimed twice
    assert tab._background_map == {}
    assert "Pooled" in tab._bg_map_summary.text()


def test_a_blank_with_no_wells_is_reported_as_pooled_not_as_paired(tab):
    """The labels describe the run, and a blank with no wells is dropped en route.

    ``format_background_well_map`` omits a blank that claims nothing, so a map
    of nothing but empty blanks is a non-empty dict on the tab and an empty
    string to the run. Both readouts used to read the dict: the editor said
    "Every well is claimed — this pairing will run" and the form's summary said
    "H12 → 0 well(s)", for a run that pools. Asserting the text rather than "it
    changed", because a wrong message also differs from the right one.
    """
    tab._bg_map_btn.click()
    tab._background_map = {"H12": []}
    tab._refresh_background_map_summary()
    tab._bg_map_refresh_uncovered()

    assert tab._background_map_value() == "", "the run is handed no pairing"
    summary = tab._bg_map_summary.text()
    uncovered = tab._bg_map_uncovered.text()
    assert "Pooled" in summary, summary
    assert "0 well(s)" not in summary, summary
    assert "will run" not in uncovered, uncovered
    assert "pool" in uncovered.lower(), uncovered

    # And once it claims a well, both say a pairing is in force.
    tab._background_map = {"H12": ["A01"]}
    tab._refresh_background_map_summary()
    tab._bg_map_refresh_uncovered()
    assert "H12 → 1 well(s)" == tab._bg_map_summary.text()
    assert "Pooled" not in tab._bg_map_uncovered.text()


def test_a_background_well_the_map_does_not_use_is_refused_in_the_form(tab):
    """The run's own refusal, previewed rather than left to `SystemExit`.

    A blank typed into Background well that the map never uses is held out of the
    batches and uploaded as a reference by ``blank_wells_for_run``, and treated
    as a sample needing a blank by the pairing plan. The form asks the same
    object the pipeline builds, so it cannot answer differently.
    """
    tab._background_well_edit.setText("H12, G12")
    # A map that covers the whole plate, so the *only* thing left to refuse is
    # the stray blank. Written with a block that leaves nothing uncovered
    # because the uncovered-well message lists G12 too: asserting "G12 appears"
    # passed with this fix reverted, which is how the first version of this test
    # measured nothing at all.
    error = tab._background_map_coverage_error("H12=A01:H12", "H12, G12")
    assert error is not None
    assert "does not use as a blank" in error, error
    assert "G12" in error.split(",")[0], error

    # Named in both, nothing is refused.
    assert tab._background_map_coverage_error(
        "H12=A01:D12; G12=E01:H12", "H12, G12") is None
    # And a map that covers nothing extra still refuses on coverage, not on this.
    coverage = tab._background_map_coverage_error("H12=A01:D12; G12=E01:H12", "")
    assert coverage is None or "does not use as a blank" not in coverage


# ── the workflow the manual describes, driven end to end ─────────────────

def test_the_manual_s_first_instruction_is_possible(tab):
    """"Pick a blank in the list" — so there has to be one, on a cold open.

    The reported defect, and the whole of it. The list was empty on open and the
    editor never read the Background well field, so there was nothing to pick,
    every drag was a silent no-op, and the only way to get a row was a button the
    manual does not mention. Asserted through the real controls: name the blanks
    where the manual says to, open the editor, and click a row.
    """
    tab._background_well_edit.setText("H12, G12")
    tab._bg_map_btn.click()

    assert [r.text().split()[0] for r in rows(tab)] == ["H12", "G12"]
    pick(tab, 1)
    drag(tab, "A01", "A02", "A03")
    assert tab._background_map_value() == "G12=A01,A02,A03"


def test_a_blank_typed_while_the_editor_is_open_appears_in_it(tab):
    """The list is a projection of the field, not a snapshot of it.

    Typing a blank with the window up did nothing, and neither did closing and
    reopening — the field was read in two places, both of them buttons.
    """
    tab._background_well_edit.setText("H12")
    tab._bg_map_btn.click()
    assert [r.text().split()[0] for r in rows(tab)] == ["H12"]

    tab._background_well_edit.setText("H12, G12, F12")
    assert [r.text().split()[0] for r in rows(tab)] == ["H12", "G12", "F12"]

    # …and a blank taken out of the field stops being one.
    tab._background_well_edit.setText("H12, F12")
    assert [r.text().split()[0] for r in rows(tab)] == ["H12", "F12"]


def test_nothing_in_the_editor_can_invent_a_blank(tab):
    """A sample well must never become the reference.

    ``+ Add blank`` took wells off the end of the plate when the field named
    none, so H12 — a sample — was held out of the results and used to correct the
    other ninety-five, with the editor reporting that the pairing would run.
    The control is gone; this pins that no *surviving* control resurrects it.
    """
    tab._bg_map_btn.click()                       # field empty, as a first user
    assert rows(tab) == []
    assert tab._background_map == {}

    drag(tab, "A01", "A02")
    assert tab._background_map == {}, "a drag invented a blank"
    assert tab._background_map_value() == ""
    # And it says why, rather than doing nothing silently.
    assert "Background well" in tab._bg_map_status.text()

    tab._bg_map_split("row")
    assert tab._background_map == {}, "a split invented a blank"


def test_the_plate_is_actually_told_what_to_draw(tab):
    """Assert at the boundary: deleting the painting left thirteen tests green.

    Every earlier test read ``_background_map``, which is the derivation behind
    the screen. This one reads what the plate was handed.
    """
    tab._background_well_edit.setText("H12, G12")
    tab._bg_map_btn.click()
    spy = PlateSpy(tab._bg_map_plate)

    pick(tab, 0)
    drag(tab, "A01", "A02")
    pick(tab, 1)
    drag(tab, "B01", "B02")

    first, second = tab._bg_map_color(0), tab._bg_map_color(1)
    assert first != second
    # The blank is drawn in its own block's colour…
    assert spy.block_of(first) == ["A01", "A02", "H12"]
    assert spy.block_of(second) == ["B01", "B02", "G12"]
    # …and marked, so the reference is not mistaken for one of its samples.
    assert set(spy.markers) == {"H12", "G12"}
    assert all(g for g in spy.markers.values())
    # The ring says which blank is being edited, which is a different question.
    assert spy.rings == ["B01", "B02", "G12"]
    assert len(spy.enabled) == 96


def test_picking_a_row_changes_which_blank_a_drag_paints_into(tab):
    """The row is the picker.

    It used to be an 18-px ``●`` whose meaning was in a tooltip, beside a combo
    of 96 wells that looked like the picker and was really a rename — picking
    from it replaced the user's blank with a sample well.
    """
    tab._background_well_edit.setText("H12, G12")
    tab._bg_map_btn.click()

    pick(tab, 1)
    drag(tab, "C05")
    assert tab._background_map["G12"] == ["C05"]
    assert tab._background_map["H12"] == []

    pick(tab, 0)
    drag(tab, "C06")
    assert tab._background_map["H12"] == ["C06"]
    # Nothing in the row can rename a blank any more: the field owns that.
    assert [r.text().split()[0] for r in rows(tab)] == ["H12", "G12"]


def test_a_drag_that_takes_wells_from_another_blank_says_so_and_undoes(tab):
    """One sweep moved four wells and left only a count going to zero.

    The steal is the right data rule — one well, one blank — so it stays; what
    it needed was a voice and a way back.
    """
    tab._background_well_edit.setText("H12, G12")
    tab._bg_map_btn.click()
    pick(tab, 0)
    drag(tab, "A01", "A02", "A03", "A04")
    assert tab._background_map_value() == "H12=A01,A02,A03,A04"

    pick(tab, 1)
    drag(tab, "A05", "A04", "A03", "A02", "A01")
    assert tab._background_map["H12"] == []
    assert "4 well(s) from H12" in tab._bg_map_status.text()

    tab._bg_map_undo_btn.click()
    assert tab._background_map_value() == "H12=A01,A02,A03,A04"


def test_a_split_gives_each_blank_the_block_it_sits_in(tab):
    """Blocks went out by list order, so the blank in row H corrected row A.

    Asserted on which rows each blank got, not on "they differ" — the broken
    version also produced two different blocks.
    """
    tab._background_well_edit.setText("H12, G12")
    tab._bg_map_btn.click()
    tab._bg_map_split("row")

    assert {w[0] for w in tab._background_map["H12"]} == {"H"}
    assert {w[0] for w in tab._background_map["G12"]} == {"G"}
    # A blank is never one of its own samples.
    assert "H12" not in tab._background_map["H12"]
    assert "G12" not in tab._background_map["G12"]


def test_the_even_split_covers_the_plate_with_the_blanks_you_have(tab):
    """Two blanks and "one blank per row" is eight blocks and 70 unclaimed wells.

    That is never a runnable map, and it was the only thing on offer. The even
    split makes exactly as many blocks as there are blanks.
    """
    tab._background_well_edit.setText("H12, G12")
    tab._bg_map_btn.click()
    tab._bg_map_split("even")

    assert "Every well is claimed" in tab._bg_map_uncovered.text()
    covered = [w for wells in tab._background_map.values() for w in wells]
    assert len(covered) == len(set(covered)) == 94       # 96 less the two blanks


def test_the_split_menu_says_what_each_option_needs(tab):
    """A split that cannot fill the plate is visible before it is chosen."""
    tab._background_well_edit.setText("H12, G12")
    tab._bg_map_btn.click()
    tab._bg_map_rebuild_split_menu()
    labels = [a.text() for a in tab._bg_map_split_menu.actions()]

    assert any("8 block(s), you have 2 blank(s)" in t for t in labels)
    assert any("Even split" in t and "2 block(s)" in t for t in labels)


def test_the_coverage_count_survives_a_split_that_cannot_fill_the_plate(tab):
    """One label did two jobs and lost the number when it was largest."""
    tab._background_well_edit.setText("H12, G12")
    tab._bg_map_btn.click()
    tab._bg_map_split("row")

    assert "claimed by no blank" in tab._bg_map_uncovered.text()
    assert "6 block(s) went to nobody" in tab._bg_map_status.text()

    # And a transient refusal does not outlive its cause.
    tab._background_well_edit.setText("")
    tab._bg_map_split("row")
    assert "Name the blank well(s)" in tab._bg_map_status.text()
    tab._background_well_edit.setText("H12, G12")
    tab._bg_map_btn.click()
    assert "Name the blank well(s)" not in tab._bg_map_status.text()


def test_restoring_a_map_refreshes_the_open_editor_and_keeps_its_blanks(tab):
    """A restore left the editor painting into a blank its rows did not name."""
    tab._background_well_edit.setText("H12")
    tab._bg_map_btn.click()
    tab._set_background_map("G12=B01:B12; F12=C01:C12")

    assert [r.text().split()[0] for r in rows(tab)] == ["H12", "G12", "F12"]
    # The field now names the restored blanks, so the next keystroke in it
    # cannot drop them.
    assert "G12" in tab._background_well_edit.text()
    assert "F12" in tab._background_well_edit.text()
    tab._background_well_edit.setText(tab._background_well_edit.text() + " ")
    assert len(tab._background_map.get("G12", [])) == 12


def test_the_blank_list_is_tall_enough_to_read(tab):
    """The list was 70px — two and a half of the eight rows a split makes."""
    from PySide6.QtWidgets import QScrollArea

    tab._background_well_edit.setText("H12, G12")
    tab._bg_map_btn.click()
    scroll = tab._bg_map_window.findChild(QScrollArea)
    assert scroll.minimumHeight() >= 4 * 24
