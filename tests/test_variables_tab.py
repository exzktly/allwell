"""The Variables tab, driven through the controls a user actually presses.

Every rule this tab enforces is in ``domain/sample_variables`` and tested
there. What is tested *here* is the other half, and it is the half this
repository has shipped broken twice: that the controls are connected, that
pressing one reaches the rule, and that the redraw puts the result back on
screen.

``test_home_empty_state`` and the per-sample control editor are the precedents.
Both had tests that built the widget and asserted it was *offered*, and in both
cases the callback behind it was never reached — once because the entry was
never selected, once because the handler raised on its first line. So nothing
below asserts a control exists: each one presses it and reads what came back.

Fast lane. The tab needs no application — a bare ``QWidget`` and a widget
registry are the whole fixture, which is exactly why the semantics were put
where they could be reached this cheaply.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from well_viewer.domain import sample_variables as sv


class _Registry:
    """``ctx.widgets``' two methods, over a dict."""

    def __init__(self):
        self._d = {}

    def register(self, name, widget):
        self._d[name] = widget
        return widget

    def get_or_none(self, name):
        return self._d.get(name)


def _wells(rows="AB", cols=(1, 2, 3, 4)):
    return {f"{r}{c:02d}": f"/data/{r}{c:02d}.csv" for r in rows for c in cols}


@pytest.fixture
def tab(qtbot, request):
    """A built Variables tab, and the ctx behind it.

    The dataset carries 8 wells (A01–A04, B01–B04) unless a test asks
    otherwise, because a fill covers *the wells the dataset holds* and a fixture
    with none would make every fill a no-op that still passed.
    """
    from well_viewer.core.selection_state import SelectionState
    from well_viewer.features.home.variables.tab_view import build_variables_subtab

    marker = request.node.get_closest_marker("wells")
    wells = marker.args[0] if marker else _wells()

    saves = []
    ctx = SimpleNamespace(
        widgets=_Registry(),
        state=SimpleNamespace(selection=SelectionState(),
                              dataset=SimpleNamespace(well_paths=wells)),
        persistence=SimpleNamespace(schedule_save=saves.append),
    )
    frame = QWidget()
    qtbot.addWidget(frame)
    build_variables_subtab(ctx, frame)
    return SimpleNamespace(ctx=ctx, frame=frame, saves=saves, wells=wells,
                           w=ctx.widgets.get_or_none)


def _vset(tab):
    return tab.ctx.state.selection.variables


def _labels(listing):
    return [listing.item(i).text() for i in range(listing.count())]


# ── the buttons do something ────────────────────────────────────────────────

def test_pressing_add_variable_adds_one_and_shows_the_definition(tab):
    """Asserted with ``isHidden``, not ``isVisible``.

    A child of a widget that has never been shown reports itself invisible
    however it was configured — the quirk ``EmptyState._sync_hint_visibility``
    already documents — so ``isVisible()`` here is False for everything and
    passes the "hidden" half of this check for the wrong reason. ``isHidden()``
    is the explicit flag the code sets, and it means what it says whether or not
    an ancestor has been painted.
    """
    assert tab.w("_var_body").isHidden() is True
    assert tab.w("_var_hint").isHidden() is False

    tab.w("_var_add").click()

    assert len(_vset(tab).variables) == 1
    assert tab.w("_var_body").isHidden() is False, (
        "the definition pane stayed hidden, so the variable that was just "
        "added cannot be named"
    )
    assert tab.w("_var_hint").isHidden() is True


def test_the_new_variable_is_selected_and_its_name_is_ready_to_type(tab):
    tab.w("_var_add").click()
    from well_viewer.features.home.variables.controller import selected_variable_id

    assert selected_variable_id(tab.ctx) == _vset(tab).variables[0].id
    assert tab.w("_var_name").text() == "Variable"


def test_typing_a_name_and_leaving_the_field_renames_the_variable(tab):
    tab.w("_var_add").click()
    tab.w("_var_name").setText("Dose")
    tab.w("_var_name").editingFinished.emit()

    assert _vset(tab).variables[0].name == "Dose"
    assert "Dose" in _labels(tab.w("_var_list"))[0]


def test_adding_a_value_puts_it_in_the_list_and_on_the_model(tab):
    tab.w("_var_add").click()
    tab.w("_var_value_entry").setText("HeLa")
    tab.w("_var_value_add").click()

    assert [v.label for v in _vset(tab).variables[0].values] == ["HeLa"]
    assert _labels(tab.w("_var_values")) == ["HeLa"]
    assert tab.w("_var_value_entry").text() == "", "the entry was not cleared"


def test_pressing_enter_in_the_entry_adds_the_value(tab):
    """The keystroke a user actually makes. Wiring only the button would leave
    Enter doing nothing, which reads as the field being broken."""
    tab.w("_var_add").click()
    tab.w("_var_value_entry").setText("U2OS")
    tab.w("_var_value_entry").returnPressed.emit()

    assert [v.label for v in _vset(tab).variables[0].values] == ["U2OS"]


def test_a_comma_separated_list_becomes_one_value_each(tab):
    """How a dose series is written down. Retyping the commas is the difference
    between this taking ten seconds and taking a minute."""
    tab.w("_var_add").click()
    tab.w("_var_value_entry").setText("0, 0.1, 0.3, 1, 3, 10")
    tab.w("_var_value_add").click()

    assert [v.label for v in _vset(tab).variables[0].values] == [
        "0", "0.1", "0.3", "1", "3", "10"]


def test_switching_the_type_to_number_reparses_and_enables_the_unit(tab):
    tab.w("_var_add").click()
    tab.w("_var_value_entry").setText("0, 1, 10")
    tab.w("_var_value_add").click()
    assert tab.w("_var_unit").isEnabled() is False

    combo = tab.w("_var_kind")
    combo.setCurrentIndex(combo.findData(sv.NUMBER))

    assert [v.number for v in _vset(tab).variables[0].values] == [0.0, 1.0, 10.0]
    assert tab.w("_var_unit").isEnabled() is True, (
        "the unit field stayed disabled on a Number variable, so a dose series "
        "cannot be given its units"
    )


def test_setting_a_unit_reaches_the_model(tab):
    tab.w("_var_add").click()
    combo = tab.w("_var_kind")
    combo.setCurrentIndex(combo.findData(sv.NUMBER))
    tab.w("_var_unit").setText("µM")
    tab.w("_var_unit").editingFinished.emit()

    assert _vset(tab).variables[0].unit == "µM"


def test_the_move_buttons_reorder_the_values(tab):
    tab.w("_var_add").click()
    tab.w("_var_value_entry").setText("a, b, c")
    tab.w("_var_value_add").click()
    values = tab.w("_var_values")
    values.setCurrentRow(2)

    tab.w("_var_value_up").click()
    assert [v.label for v in _vset(tab).variables[0].values] == ["a", "c", "b"]
    assert _labels(tab.w("_var_values")) == ["a", "c", "b"], (
        "the model moved and the list did not, so the two disagree on screen"
    )


def test_remove_takes_the_selected_value(tab):
    tab.w("_var_add").click()
    tab.w("_var_value_entry").setText("a, b")
    tab.w("_var_value_add").click()
    tab.w("_var_values").setCurrentRow(0)
    tab.w("_var_value_remove").click()

    assert [v.label for v in _vset(tab).variables[0].values] == ["b"]


def test_remove_takes_the_selected_variable(tab):
    tab.w("_var_add").click()
    tab.w("_var_add").click()
    tab.w("_var_remove").click()

    assert len(_vset(tab).variables) == 1


def test_editing_a_row_in_place_renames_the_value_rather_than_replacing_it(tab):
    """The reason values carry ids: the wells holding it must follow.

    Reading the row back by its *new* text would make this a delete-and-add,
    and every well pointing at the old value would be orphaned — on a filled
    plate, silently.
    """
    tab.w("_var_add").click()
    tab.w("_var_value_entry").setText("1uM")
    tab.w("_var_value_add").click()

    variable_id = _vset(tab).variables[0].id
    value_id = _vset(tab).variables[0].values[0].id
    tab.ctx.state.selection.variables = sv.assign(
        _vset(tab), variable_id, ["A01", "A02"], value_id)

    item = tab.w("_var_values").item(0)
    item.setText("1 µM")

    assert _vset(tab).variables[0].values[0].id == value_id, (
        "the rename minted a new id, so every well holding the old one is now "
        "unassigned"
    )
    assert _vset(tab).variables[0].values[0].label == "1 µM"
    assert _vset(tab).value_id_for(variable_id, "A01") == value_id


# ── what the tab says about itself ──────────────────────────────────────────

def test_a_variable_with_no_values_says_what_it_needs(tab):
    tab.w("_var_add").click()
    assert "at least two values" in tab.w("_var_note").text()


def test_a_number_variable_names_the_values_that_are_not_numbers(tab):
    """A vehicle row is a real plate, and it is kept — but a response curve
    cannot put it on a numeric axis, and the place to say so is where the value
    is being typed, not in an empty plot later."""
    tab.w("_var_add").click()
    tab.w("_var_value_entry").setText("0, vehicle, 10")
    tab.w("_var_value_add").click()
    combo = tab.w("_var_kind")
    combo.setCurrentIndex(combo.findData(sv.NUMBER))

    note = tab.w("_var_note").text()
    assert "vehicle" in note and "not numbers" in note


def test_a_complete_variable_says_nothing(tab):
    tab.w("_var_add").click()
    tab.w("_var_value_entry").setText("HeLa, U2OS")
    tab.w("_var_value_add").click()
    assert tab.w("_var_note").text() == ""


# ── persistence and identity ────────────────────────────────────────────────

def test_every_edit_schedules_a_save(tab):
    tab.w("_var_add").click()
    tab.w("_var_value_entry").setText("HeLa")
    tab.w("_var_value_add").click()

    assert tab.saves.count("sample_variables") >= 2, (
        "an edit did not reach the autosave, so it is lost on the next load"
    )


def test_the_rail_reads_ids_not_names(tab):
    """A row looked up by its text is a row lost the moment it is renamed."""
    tab.w("_var_add").click()
    item = tab.w("_var_list").item(0)
    assert item.data(Qt.UserRole) == _vset(tab).variables[0].id


def test_wiring_twice_does_not_double_every_button(tab):
    """The builder may run again on a rebuild; a second connection would add two
    variables per click and rename twice per edit."""
    from well_viewer.features.home.variables.controller import wire_variables

    wire_variables(tab.ctx)
    tab.w("_var_add").click()
    assert len(_vset(tab).variables) == 1


# ── the fill row ────────────────────────────────────────────────────────────

def _dose_tab(tab, labels="0, 1"):
    """A tab with one Number variable carrying *labels*, selected."""
    tab.w("_var_add").click()
    tab.w("_var_name").setText("Dose")
    tab.w("_var_name").editingFinished.emit()
    tab.w("_var_value_entry").setText(labels)
    tab.w("_var_value_add").click()
    combo = tab.w("_var_kind")
    combo.setCurrentIndex(combo.findData(sv.NUMBER))
    return _vset(tab).variables[0].id


def _assigned(tab, variable_id):
    """``{well: label}`` for what the plate now holds."""
    vset = _vset(tab)
    variable = vset.by_id(variable_id)
    return {w: variable.value_by_id(vid).label
            for w, vid in vset.assignments.get(variable_id, {}).items()}


@pytest.mark.wells({f"{r}{c:02d}": f"/d/{r}{c:02d}.csv"
                    for r in "ABCD" for c in (1, 2, 3, 4)})
def test_apply_fill_cycles_the_values_along_the_rows(tab):
    """Four rows and four columns, so "repeat 2" means two *rows* and cannot be
    confused with two wells.

    The default 2x4 fixture could not tell those apart — with two rows of four,
    "two rows" and "two wells" land differently but the assertion was written
    from the implementation, so the striped plate the old code produced read as
    correct. §7.8 rule 2: build the fixture so the right and wrong answers
    differ, or no assertion can measure anything.
    """
    variable_id = _dose_tab(tab)
    axis = tab.w("_var_fill_axis")
    axis.setCurrentIndex(axis.findData("row"))
    tab.w("_var_fill_repeat").setValue(2)
    tab.w("_var_fill_apply").click()

    assigned = _assigned(tab, variable_id)
    for col in (1, 2, 3, 4):
        assert assigned[f"A{col:02d}"] == "0" and assigned[f"B{col:02d}"] == "0"
        assert assigned[f"C{col:02d}"] == "1" and assigned[f"D{col:02d}"] == "1"


@pytest.mark.wells({f"{r}{c:02d}": f"/d/{r}{c:02d}.csv"
                    for r in "ABCD" for c in (1, 2, 3, 4)})
def test_apply_fill_along_the_columns_walks_down_first(tab):
    """The other half of a block design: two columns per dose, whole columns."""
    variable_id = _dose_tab(tab)
    axis = tab.w("_var_fill_axis")
    axis.setCurrentIndex(axis.findData("col"))
    tab.w("_var_fill_repeat").setValue(2)
    tab.w("_var_fill_apply").click()

    assigned = _assigned(tab, variable_id)
    for row in "ABCD":
        assert assigned[f"{row}01"] == "0" and assigned[f"{row}02"] == "0"
        assert assigned[f"{row}03"] == "1" and assigned[f"{row}04"] == "1"


@pytest.mark.wells({f"{r}{c:02d}": f"/d/{r}{c:02d}.csv"
                    for r in "ABCD" for c in (1, 2, 3, 4)
                    if not (r == "C" and c == 2)})
def test_a_missing_well_costs_only_itself(tab):
    """C02 was never imaged, which is an ordinary failed acquisition.

    It used to cost more than itself. With the wells flattened into one
    sequence the gap was closed, so every well after it moved one place along
    the cycle and one well was handed the wrong dose — carrying a value, so no
    ring, no count and no mention: a plate that looks like a clean block design
    with one cell quietly wrong. §7's cardinal case.

    This replaces a test that asserted the old behaviour. It was not loosened —
    it was pinning the defect, so it is rewritten around what the user should
    get.
    """
    variable_id = _dose_tab(tab)
    axis = tab.w("_var_fill_axis")
    axis.setCurrentIndex(axis.findData("col"))
    tab.w("_var_fill_repeat").setValue(2)
    tab.w("_var_fill_apply").click()

    assigned = _assigned(tab, variable_id)
    assert "C02" not in assigned
    for row in "ABCD":
        for col, expected in ((1, "0"), (3, "1"), (4, "1")):
            assert assigned[f"{row}{col:02d}"] == expected, f"{row}{col:02d}"
    for row in "ABD":
        assert assigned[f"{row}02"] == "0", f"{row}02 shifted by the gap"


def test_the_fill_row_says_what_it_covers(tab):
    _dose_tab(tab)
    assert "8 well" in tab.w("_var_fill_scope").text()


# ── select-then-chip ────────────────────────────────────────────────────────

def _chips(tab):
    layout = tab.w("_var_chips").layout()
    return [layout.itemAt(i).widget() for i in range(layout.count())
            if layout.itemAt(i).widget() is not None]


def test_a_chip_is_offered_per_value_plus_a_clear(tab):
    _dose_tab(tab)
    assert [c.text() for c in _chips(tab)] == ["0", "1", "Clear"]


def test_selecting_wells_then_clicking_a_chip_assigns_them(tab):
    """The two-step this page already teaches with "select cells, then Add
    Prefix". Asserted by pressing the chip, not by checking it exists."""
    variable_id = _dose_tab(tab)
    tab.w("_var_plate").selectedWells = ["A01", "A02"]
    _chips(tab)[1].click()          # the "1" chip

    assert _assigned(tab, variable_id) == {"A01": "1", "A02": "1"}


def test_the_clear_chip_unassigns_the_selection(tab):
    variable_id = _dose_tab(tab)
    tab.w("_var_fill_apply").click()
    tab.w("_var_plate").selectedWells = ["A01"]
    _chips(tab)[-1].click()

    assert "A01" not in _assigned(tab, variable_id)
    assert "A02" in _assigned(tab, variable_id), "it cleared more than the selection"


def test_a_chip_with_nothing_selected_says_so_rather_than_doing_nothing(tab):
    """A chip that appears inert is indistinguishable from one that is broken."""
    variable_id = _dose_tab(tab)
    tab.w("_var_plate").selectedWells = []
    _chips(tab)[0].click()

    assert _assigned(tab, variable_id) == {}
    assert "Select wells" in tab.w("_var_readout").text()


def test_the_chips_follow_a_rename(tab):
    """The chip is the label the user reads while assigning; a stale one would
    put the wrong dose on the wells they pick next."""
    _dose_tab(tab)
    tab.w("_var_unit").setText("µM")
    tab.w("_var_unit").editingFinished.emit()
    assert [c.text() for c in _chips(tab)][:2] == ["0 µM", "1 µM"]


# ── the plate readout ───────────────────────────────────────────────────────

def test_unassigned_wells_are_ringed_and_counted(tab):
    """Three layers, none of them colour alone: the fill says which value, the
    ring says this well has none, and the line counts them."""
    _dose_tab(tab)
    tab.w("_var_plate").selectedWells = ["A01"]
    _chips(tab)[0].click()

    readout = tab.w("_var_readout").text()
    assert "7 of 8" in readout and "no Dose" in readout
    assert "ringed" in readout


def test_a_fully_assigned_plate_says_so(tab):
    _dose_tab(tab)
    tab.w("_var_fill_apply").click()
    assert tab.w("_var_readout").text() == "All 8 well(s) have a Dose."


def test_the_readout_names_the_wells_that_are_short(tab):
    """Naming them is the difference between a count the user can act on and one
    they have to hunt for."""
    _dose_tab(tab)
    tab.w("_var_plate").selectedWells = [w for w in tab.wells if w != "B04"]
    _chips(tab)[0].click()

    assert "B04" in tab.w("_var_readout").text()


@pytest.mark.wells({})
def test_a_plate_with_no_dataset_says_that_rather_than_showing_zero(tab):
    _dose_tab(tab)
    assert "No dataset is open" in tab.w("_var_readout").text()


def test_a_variable_with_no_values_says_there_is_nothing_to_fill(tab):
    tab.w("_var_add").click()
    assert "nothing to fill" in tab.w("_var_readout").text()


def test_the_plate_only_enables_the_wells_the_dataset_holds(tab):
    """Cycling values over wells that are not there would put every value after
    the gap on the wrong well."""
    _dose_tab(tab)
    plate = tab.w("_var_plate")
    plate.selectedWells = ["A01", "H12"]
    assert "H12" not in (plate.selectedWells or []), (
        "a well the dataset does not hold was selectable, so a fill can land "
        "on it"
    )


# ── what actually reaches the plate ─────────────────────────────────────────
#
# §7.4 #1: assert the outbound call, not the value the controller computed. The
# readout test above was written first and passed against a controller that had
# stopped ringing anything at all — it only ever checked that the *sentence*
# said "ringed". A test that reads an internal value passes happily while the
# screen stays wrong, which is exactly the defect this rule exists for.

def _plate_calls(tab, monkeypatch):
    """Record what the controller sends the plate on the next repaint."""
    plate = tab.w("_var_plate")
    seen = {"rings": None, "colors": {}}

    def rings(ids):
        seen["rings"] = sorted(ids)

    def color(well, value):
        seen["colors"][well] = value

    monkeypatch.setattr(plate, "setWellRings", rings)
    monkeypatch.setattr(plate, "setWellColor", color)
    return seen


def test_the_unassigned_wells_reach_the_plate_as_rings(tab, monkeypatch):
    variable_id = _dose_tab(tab)
    tab.w("_var_plate").selectedWells = ["A01", "A02"]

    seen = _plate_calls(tab, monkeypatch)
    _chips(tab)[0].click()

    assert seen["rings"] is not None, "the plate was never told which wells are short"
    assert seen["rings"] == sorted(w for w in tab.wells if w not in ("A01", "A02")), (
        "the rings the plate was given are not the unassigned wells, so the "
        "readout's count and the picture disagree"
    )
    assert _assigned(tab, variable_id).keys() == {"A01", "A02"}


def test_each_value_reaches_the_plate_as_its_own_colour(tab, monkeypatch):
    _dose_tab(tab)
    seen = _plate_calls(tab, monkeypatch)
    tab.w("_var_fill_apply").click()

    assert set(seen["colors"]) == set(tab.wells), "not every well was coloured"
    distinct = set(seen["colors"].values())
    assert len(distinct) == 2, (
        f"two values produced {len(distinct)} colour(s) on the plate, so the "
        "fill is not readable"
    )


def test_a_fully_assigned_plate_is_told_it_has_no_rings(tab, monkeypatch):
    """Clearing matters as much as setting: rings left behind from the previous
    variable would mark wells that are perfectly well annotated."""
    _dose_tab(tab)
    seen = _plate_calls(tab, monkeypatch)
    tab.w("_var_fill_apply").click()
    assert seen["rings"] == []


# ── a second variable, as a glyph ───────────────────────────────────────────

def _two_variables(tab):
    """`Dose` (0, 1) filled along rows, and `Cell line` (HeLa, U2OS)."""
    dose = _dose_tab(tab)
    tab.w("_var_fill_apply").click()

    tab.w("_var_add").click()
    tab.w("_var_name").setText("Cell line")
    tab.w("_var_name").editingFinished.emit()
    tab.w("_var_value_entry").setText("HeLa, U2OS")
    tab.w("_var_value_add").click()
    line = _vset(tab).variables[1].id
    axis = tab.w("_var_fill_axis")
    axis.setCurrentIndex(axis.findData("col"))
    # Two columns per cell line over the fixture's four, so both values land.
    # This read 4 while the repeat counted wells; under the corrected
    # line-counting semantics 4 columns at 4-per-value is one block and the
    # plate came out entirely HeLa.
    tab.w("_var_fill_repeat").setValue(2)
    tab.w("_var_fill_apply").click()
    return dose, line


def _markers(tab, monkeypatch):
    plate = tab.w("_var_plate")
    seen = {}
    monkeypatch.setattr(plate, "setWellMarker",
                        lambda well, glyph: seen.__setitem__(well, glyph))
    return seen


def test_the_overlay_combo_lists_the_other_variables_only(tab):
    _two_variables(tab)
    combo = tab.w("_var_overlay")
    assert [combo.itemText(i) for i in range(combo.count())] == ["Nothing", "Dose"]


def test_with_one_variable_the_overlay_is_disabled(tab):
    """A control whose every option is a no-op invites a click and answers with
    silence."""
    _dose_tab(tab)
    assert tab.w("_var_overlay").isEnabled() is False


def test_choosing_an_overlay_puts_a_glyph_on_every_annotated_well(tab, monkeypatch):
    """Two factors readable in one look, rather than by switching back and forth."""
    _two_variables(tab)
    combo = tab.w("_var_overlay")
    dose_id = _vset(tab).variables[0].id

    seen = _markers(tab, monkeypatch)
    combo.setCurrentIndex(combo.findData(dose_id))

    assert set(seen) == set(tab.wells), "not every well got a glyph"
    assert set(seen.values()) == {"0", "1"}, (
        "a numeric variable's short labels were not used as their own glyphs"
    )


def test_a_text_overlay_uses_the_values_initial(tab, monkeypatch):
    _two_variables(tab)
    # Select Dose in the rail so Cell line is the overlay.
    tab.w("_var_list").setCurrentRow(0)
    combo = tab.w("_var_overlay")
    line_id = _vset(tab).variables[1].id

    seen = _markers(tab, monkeypatch)
    combo.setCurrentIndex(combo.findData(line_id))
    assert set(seen.values()) == {"H", "U"}


def test_the_overlay_legend_says_what_the_glyphs_mean(tab):
    _two_variables(tab)
    tab.w("_var_list").setCurrentRow(0)
    combo = tab.w("_var_overlay")
    combo.setCurrentIndex(combo.findData(_vset(tab).variables[1].id))

    legend = tab.w("_var_overlay_legend").text()
    assert "H = HeLa" in legend and "U = U2OS" in legend


def test_choosing_nothing_clears_the_glyphs(tab, monkeypatch):
    """A glyph left behind from a variable no longer shown annotates the plate
    with a fact nothing on screen explains."""
    _two_variables(tab)
    tab.w("_var_list").setCurrentRow(0)
    combo = tab.w("_var_overlay")
    combo.setCurrentIndex(combo.findData(_vset(tab).variables[1].id))
    assert tab.w("_var_overlay_legend").text() != "", (
        "the overlay was never on, so turning it off proves nothing"
    )

    plate = tab.w("_var_plate")
    cleared = []
    monkeypatch.setattr(plate, "clearWellMarkers", lambda: cleared.append(1))
    monkeypatch.setattr(plate, "setWellMarker", lambda *a: None)

    combo.setCurrentIndex(0)
    assert cleared, "the markers were never cleared"
    assert tab.w("_var_overlay_legend").text() == ""


# ── the tooltip ─────────────────────────────────────────────────────────────

def test_the_tooltip_gives_the_whole_row_for_a_well(tab):
    from well_viewer.core.ui_helpers import well_plate_tooltip_provider

    _two_variables(tab)
    tab.ctx.state.dataset.well_labels = {"A01": "shCtrl"}
    tip = well_plate_tooltip_provider(tab.ctx)("A01")

    assert tip.startswith("A01 — shCtrl")
    assert "Dose: 0" in tip and "Cell line: HeLa" in tip


def test_the_tooltip_names_a_variable_the_well_has_no_value_for(tab):
    """Omitting the clause is indistinguishable from a plate where no such
    variable exists — and an unassigned well is a fact the user needs to see."""
    from well_viewer.core.ui_helpers import well_plate_tooltip_provider

    _dose_tab(tab)
    tab.ctx.state.dataset.well_labels = {}
    assert "Dose: —" in well_plate_tooltip_provider(tab.ctx)("A01")


def test_a_plate_with_no_variables_keeps_the_tooltip_it_always_had(tab):
    from well_viewer.core.ui_helpers import well_plate_tooltip_provider

    tab.ctx.state.dataset.well_labels = {"A01": "shCtrl"}
    assert well_plate_tooltip_provider(tab.ctx)("A01") == "A01 — shCtrl"


# ── group roll-up: the rule that must not be softened ───────────────────────

def _with_groups(tab, groups):
    tab.ctx.state.selection.selections = [
        {"id": f"g{i}", "name": name, "wells": wells}
        for i, (name, wells) in enumerate(groups)
    ]


def _group_rows(tab):
    listing = tab.w("_var_groups")
    return [listing.item(i).text() for i in range(listing.count())]


def test_a_group_whose_wells_agree_shows_that_value(tab):
    _dose_tab(tab)
    _with_groups(tab, [("ctrl", ["A01", "A02"])])
    tab.w("_var_plate").selectedWells = ["A01", "A02"]
    _chips(tab)[0].click()

    assert _group_rows(tab) == ["ctrl — 0 (2 wells)"]


def test_a_group_whose_wells_disagree_reads_mixed(tab):
    _dose_tab(tab)
    _with_groups(tab, [("ctrl", ["A01", "A02"])])
    tab.w("_var_plate").selectedWells = ["A01"]
    _chips(tab)[0].click()
    tab.w("_var_plate").selectedWells = ["A02"]
    _chips(tab)[1].click()

    assert "Mixed (2 values across 2 wells)" in _group_rows(tab)[0]


def test_a_group_holding_one_unassigned_well_has_no_value_and_says_which(tab):
    """The rule this whole feature is built around.

    A well added to a group after the fill does not inherit a value from its
    neighbours. Seven wells saying 1 µM and one saying nothing is not a 1 µM
    group — a member built from it would carry a dose those wells were never
    given, in a bar indistinguishable from a correct one.
    """
    _dose_tab(tab)
    _with_groups(tab, [("ctrl", ["A01", "A02", "A03"])])
    tab.w("_var_plate").selectedWells = ["A01", "A02"]
    _chips(tab)[0].click()

    row = _group_rows(tab)[0]
    assert "no value" in row, f"the group resolved to a value it was not given: {row}"
    assert "1 of 3 wells unassigned" in row
    assert "A03" in row, "the short well is counted but not named"


def test_a_group_of_wells_this_dataset_does_not_hold_is_not_listed(tab):
    """A group saved against a fuller plate, opened on a subset. Its wells are
    not here, so it has nothing to roll up and a row reading "no value" would
    report a shortfall that is not one."""
    _dose_tab(tab)
    _with_groups(tab, [("elsewhere", ["H11", "H12"])])
    assert _group_rows(tab) == []


def test_the_group_list_is_hidden_when_there_are_none(tab):
    """This tab's whole point is that you need no groups at all."""
    _dose_tab(tab)
    assert tab.w("_var_groups").isHidden() is True
    assert tab.w("_var_groups_title").isHidden() is True


def test_a_group_summary_says_the_same_thing_whatever_order_its_wells_are_in(tab):
    """Two sentences for one plate, one of them with a wrong count, was the
    shape this fixed — a group of two doses plus a blank read `Mixed (3 values)`
    when the blank sat last and "1 of 3 unassigned" when it sat first."""
    from well_viewer.features.home.variables.controller import _group_summary

    variable_id = _dose_tab(tab)
    tab.w("_var_plate").selectedWells = ["A01"]
    _chips(tab)[0].click()
    tab.w("_var_plate").selectedWells = ["A02"]
    _chips(tab)[1].click()

    vset = _vset(tab)
    variable = vset.by_id(variable_id)
    first = _group_summary(vset, variable, ["A01", "A02", "A03"])
    second = _group_summary(vset, variable, ["A03", "A01", "A02"])

    assert first == second
    assert "no value" in first and "1 of 3" in first


# ── what happens when the variable goes away ────────────────────────────────
#
# The review gate found all three of these live: a dataset swap, a reset, and
# deleting the last variable each replaced the *state* and left the *screen*
# describing the plate before. That is the bug ``persistence/ratios.py``
# records, one layer up — the half nobody fixed because the state half looked
# like the whole job.

def _watch_clears(tab, monkeypatch):
    """Record the plate-clearing calls a repaint makes.

    At the boundary rather than on the widget's internals: what matters is that
    the controller *told* the plate to drop the previous variable's decoration,
    which is the call that was missing.
    """
    plate = tab.w("_var_plate")
    seen = {"colors": 0, "rings": 0, "markers": 0}
    for name, key in (("clearWellColors", "colors"),
                      ("clearWellRings", "rings"),
                      ("clearWellMarkers", "markers")):
        monkeypatch.setattr(plate, name,
                            lambda _k=key: seen.__setitem__(_k, seen[_k] + 1))
    return seen


def test_removing_the_last_variable_blanks_the_plate_it_described(tab, monkeypatch):
    """Its colours, rings, chips and sentence all belonged to it."""
    _dose_tab(tab)
    tab.w("_var_fill_apply").click()
    assert tab.w("_var_chips").layout().count() > 0

    seen = _watch_clears(tab, monkeypatch)
    tab.w("_var_remove").click()

    assert seen["colors"] and seen["rings"] and seen["markers"], (
        f"the deleted variable's decoration was never cleared: {seen}"
    )
    assert tab.w("_var_chips").layout().count() == 0, (
        "the deleted variable's value chips are still there, and still "
        "connected — pressing one reaches assign_selected and does nothing"
    )
    assert tab.w("_var_readout").text() == ""


def test_a_dataset_swap_repaints_rather_than_leaving_the_last_plate(tab,
                                                                    monkeypatch):
    """The state half was right and the screen half was not: a new plate of one
    well kept showing four ringed wells from the plate before."""
    from well_viewer.features.home.variables.controller import refresh_variables

    _dose_tab(tab)
    tab.w("_var_fill_apply").click()

    # What ``persistence.sample_variables.load_from_data_dir`` does on a plate
    # that declares none, followed by what the load controller now calls.
    seen = _watch_clears(tab, monkeypatch)
    tab.ctx.state.selection.variables = sv.VariableSet()
    tab.ctx.state.dataset.well_paths = {"B01": "/B01.csv"}
    refresh_variables(tab.ctx)

    assert seen["colors"] and seen["rings"], f"nothing was cleared: {seen}"
    assert tab.w("_var_chips").layout().count() == 0
    assert tab.w("_var_readout").text() == "", (
        "the readout still names wells from the dataset that was closed"
    )


def test_the_variables_tab_is_reachable_through_the_feature_service():
    """``core/`` must not import the feature to repaint it, and the load
    controller and the reset scope both need to. A service that is not
    registered makes both of those silent no-ops."""
    from well_viewer.features.registry import build_feature_registry

    reg = build_feature_registry()
    assert reg.service("variables.refresh") is not None


def test_the_repeat_control_names_what_it_counts(tab):
    """"repeat each 4" beside "along Rows" reads as four rows to any reader, and
    that is what it does — but while the cycling counted wells the two readings
    differed by the width of the plate, and nothing on screen said which was
    meant. The suffix follows the axis so the two cannot drift apart."""
    _dose_tab(tab)
    axis = tab.w("_var_fill_axis")
    repeat = tab.w("_var_fill_repeat")

    axis.setCurrentIndex(axis.findData("row"))
    assert repeat.suffix().strip() == "rows"

    axis.setCurrentIndex(axis.findData("col"))
    assert repeat.suffix().strip() == "columns"


def test_colliding_overlay_glyphs_are_broken_apart(tab):
    """Truncation collides, and a collision here is the second factor of a
    two-factor plate silently reading as one value where it is three.

    ``0``, ``0.1`` and ``0.3`` all shorten to ``0``; ``HeLa`` and ``HEK293``
    both give ``H``. Nothing detected it, so three doses painted the same mark
    and the key read ``0 = 0, 0 = 0.1, 0 = 0.3`` — a key naming one glyph three
    times, which is worse than no key. The fixture uses a dose series that
    collides on purpose: with two well-separated values any implementation
    passes.
    """
    import well_viewer.domain.sample_variables as sv
    from well_viewer.features.home.variables.controller import overlay_glyphs

    vset = sv.from_block({
        "schema_version": 1,
        "variables": [{
            "id": "d", "name": "Dose", "kind": "number",
            "values": [{"id": f"v{i}", "label": lbl}
                       for i, lbl in enumerate(["0", "0.1", "0.3", "1"])],
        }],
        "assignments": {},
    })
    variable = vset.by_id("d")

    glyphs = overlay_glyphs(variable)

    assert len(set(glyphs.values())) == len(glyphs), glyphs
    assert glyphs[variable.values[0].id] == "0", "an uncollided glyph was changed"
    assert all(g for g in glyphs.values())
