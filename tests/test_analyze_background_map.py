"""The Analyze pane's blank-pairing editor, driven the way a user drives it.

Every test here goes through a real ``AnalyzeTab`` and the handler a control is
actually wired to — the Browse-equivalent for this feature is
``_open_background_map_editor``, the button's own slot. Asserting that the
button exists, or calling the editor's helpers one step past the closure the
button reaches, is how the per-sample control editor in Review shipped
unopenable past a lane of thousands of tests (CLAUDE.md §7.6).
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
    tab._bg_map_btn.click()
    tab._background_well_edit.setText("H12")
    tab._bg_map_add_blank()

    tab._bg_map_plate.wellPaintPressed.emit("A01")
    tab._bg_map_plate.wellPaintDragged.emit("A02")
    tab._bg_map_plate.paintDragFinished.emit()

    assert tab._background_map == {"H12": ["A01", "A02"]}
    assert tab._background_map_value() == "H12=A01,A02"
    # …and the form's own summary says so, because that is all a user sees
    # without opening the editor again.
    assert "H12 → 2 well(s)" in tab._bg_map_summary.text()


def test_dragging_back_over_a_claimed_well_removes_it(tab):
    """One gesture, one direction — decided by the well it started on."""
    tab._bg_map_btn.click()
    tab._background_well_edit.setText("H12")
    tab._bg_map_add_blank()
    for well in ("A01", "A02"):
        tab._bg_map_plate.wellPaintPressed.emit(well)
        tab._bg_map_plate.paintDragFinished.emit()
    assert tab._background_map["H12"] == ["A01", "A02"]

    tab._bg_map_plate.wellPaintPressed.emit("A01")   # starts on a claimed well
    tab._bg_map_plate.wellPaintDragged.emit("A02")
    tab._bg_map_plate.paintDragFinished.emit()
    assert tab._background_map["H12"] == []


def test_a_well_belongs_to_one_blank_at_a_time(tab):
    """Painting takes a well from whoever held it, rather than making a map the
    parser would refuse for claiming it twice."""
    tab._bg_map_btn.click()
    tab._background_well_edit.setText("H12, G12")
    tab._bg_map_add_blank()
    tab._bg_map_add_blank()
    assert list(tab._background_map) == ["H12", "G12"]

    tab._bg_map_set_active(0)
    tab._bg_map_plate.wellPaintPressed.emit("A01")
    tab._bg_map_plate.paintDragFinished.emit()
    tab._bg_map_set_active(1)
    tab._bg_map_plate.wellPaintPressed.emit("A01")
    tab._bg_map_plate.paintDragFinished.emit()

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
    tab._bg_map_btn.click()
    tab._background_well_edit.setText("")
    tab._bg_map_split("row")
    assert tab._background_map == {}
    assert "Name the blank well" in tab._bg_map_uncovered.text()


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

    tab._bg_map_btn.click()
    tab._background_well_edit.setText(
        "H01, H02, H03, H04, H05, H06, H07, H08")
    tab._bg_map_split("row")

    mapping = tab._background_map
    assert list(mapping) == ["H01", "H02", "H03", "H04",
                             "H05", "H06", "H07", "H08"]
    # H01 takes row A, and rows A–G are untouched by the hold-out…
    assert mapping["H01"][:3] == ["A01", "A02", "A03"]
    assert len(mapping["H01"]) == 12
    # …while row H keeps only the wells that are not somebody's blank.
    assert mapping["H08"] == ["H09", "H10", "H11", "H12"]

    # No blank appears among the wells any blank corrects — the same statement
    # the parser makes by refusing a map where one does.
    corrected = {w for wells in mapping.values() for w in wells}
    assert not set(mapping) & corrected
    assert parse_background_well_map(tab._background_map_value()) == mapping
    assert "Every well is claimed" in tab._bg_map_uncovered.text()


def test_fewer_blanks_than_blocks_says_so_rather_than_inventing_one(tab):
    tab._bg_map_btn.click()
    tab._background_well_edit.setText("H12, G12")
    tab._bg_map_split("quadrant")
    assert len(tab._background_map) == 2
    assert "2 blank(s) for 4 block(s)" in tab._bg_map_uncovered.text()


def test_the_uncovered_count_is_the_runs_own_refusal_shown_early(tab):
    """What the editor says and what the run does are the same question.

    Asserted against the pipeline's own ``BlankGroups`` rather than a number
    written here, so the two cannot drift into disagreeing about which wells
    would stop the run.
    """
    from pipeline.empty_well import BlankGroups

    tab._bg_map_btn.click()
    tab._background_well_edit.setText("H12")
    tab._bg_map_add_blank()
    tab._bg_map_plate.wellPaintPressed.emit("A01")
    tab._bg_map_plate.paintDragFinished.emit()

    groups = BlankGroups("", tab._background_map_value(),
                         corrections=("empty_well",))
    expected = groups.uncovered(tab._background_map_plate_wells())
    assert expected, "the fixture must leave something uncovered to measure"
    assert f"{len(expected)} well(s) claimed by no blank" in \
        tab._bg_map_uncovered.text()


def test_clearing_the_pairing_returns_the_run_to_pooling(tab):
    tab._bg_map_btn.click()
    tab._background_well_edit.setText("H12")
    tab._bg_map_add_blank()
    tab._bg_map_plate.wellPaintPressed.emit("A01")
    tab._bg_map_plate.paintDragFinished.emit()
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
    error = tab._background_map_coverage_error("H12=A01:D12", "H12, G12")
    assert error and "G12" in error, error

    # Named in both, nothing is refused.
    assert tab._background_map_coverage_error(
        "H12=A01:D12; G12=E01:H12", "H12, G12") is None
