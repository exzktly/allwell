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


def test_apply_fill_cycles_the_values_along_the_rows(tab):
    variable_id = _dose_tab(tab)
    axis = tab.w("_var_fill_axis")
    axis.setCurrentIndex(axis.findData("row"))
    tab.w("_var_fill_repeat").setValue(2)
    tab.w("_var_fill_apply").click()

    assert _assigned(tab, variable_id) == {
        "A01": "0", "A02": "0", "A03": "1", "A04": "1",
        "B01": "0", "B02": "0", "B03": "1", "B04": "1",
    }


def test_apply_fill_along_the_columns_walks_down_first(tab):
    """Two cell lines down eight rows is the other half of a block design."""
    variable_id = _dose_tab(tab)
    axis = tab.w("_var_fill_axis")
    axis.setCurrentIndex(axis.findData("col"))
    tab.w("_var_fill_repeat").setValue(2)
    tab.w("_var_fill_apply").click()

    assert _assigned(tab, variable_id) == {
        "A01": "0", "B01": "0", "A02": "1", "B02": "1",
        "A03": "0", "B03": "0", "A04": "1", "B04": "1",
    }


@pytest.mark.wells({"A01": "x", "A03": "x", "A04": "x"})
def test_a_fill_closes_gaps_rather_than_counting_them(tab):
    """A plate whose column 2 was never imaged is a plate of three wells here.

    Leaving a slot for the absent well would put every value after it one place
    out, and there is nothing on screen where that well would be for the user to
    notice by.
    """
    variable_id = _dose_tab(tab)
    tab.w("_var_fill_repeat").setValue(1)
    tab.w("_var_fill_apply").click()

    assert _assigned(tab, variable_id) == {"A01": "0", "A03": "1", "A04": "0"}


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
