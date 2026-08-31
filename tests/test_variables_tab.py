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


def _delete_row(tab, row: int = 0):
    """Press the × on rail row *row* — the control that now deletes a variable.

    Through the real button rather than by calling ``remove_variable``: the
    delete moved from one button at the foot of the column onto each row, and
    a test that calls the handler directly would pass against a row whose ×
    was never connected — the failure this tab has shipped before.
    """
    from well_viewer.features.home.variables.editor_view import row_delete_button

    button = row_delete_button(tab.w("_var_list"), row)
    assert button is not None, f"rail row {row} has no delete button"
    button.click()


def _labels(listing):
    # The **painted** text, from the row widget — ``item.text()`` is blanked so
    # the delegate does not draw it under the widget, and a test reading it
    # would be asserting a string that never reaches the screen.
    from well_viewer.features.home.variables.editor_view import row_label_text

    return [row_label_text(listing, i) for i in range(listing.count())]


def _rail_labels(tab):
    return _labels(tab.w("_var_list"))


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
    _delete_row(tab, tab.w("_var_list").currentRow())

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


def test_the_fill_row_names_the_variable_it_will_rewrite(tab):
    """A fill is the one action on this page that rewrites every well at once,
    and ``Give each row the next value`` on a page holding four variables does
    not say which one.

    Asserted on the label's text rather than "the label is not empty", which the
    static placeholder it replaces also satisfies.
    """
    _dose_tab(tab)

    assert tab.w("_var_fill_lead").text() == "the next Dose,"


def test_the_fill_row_follows_a_rename(tab):
    """It is repainted from the live set, so it cannot name the old variable."""
    _dose_tab(tab)
    name = tab.w("_var_name")
    name.setText("Concentration")
    name.editingFinished.emit()

    assert tab.w("_var_fill_lead").text() == "the next Concentration,"


# ── select-then-chip ────────────────────────────────────────────────────────

def _chips(tab):
    layout = tab.w("_var_chips").layout()
    return [layout.itemAt(i).widget() for i in range(layout.count())
            if layout.itemAt(i).widget() is not None]


def test_a_chip_is_offered_per_value_plus_a_no_value(tab):
    _dose_tab(tab)
    assert [c.text() for c in _chips(tab)] == ["0", "1", "No value"]


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
    assert "Select wells" in tab.w("_var_message").text()


def test_the_chips_follow_a_rename(tab):
    """The chip is the label the user reads while assigning; a stale one would
    put the wrong dose on the wells they pick next."""
    _dose_tab(tab)
    tab.w("_var_unit").setText("µM")
    tab.w("_var_unit").editingFinished.emit()
    assert [c.text() for c in _chips(tab)][:2] == ["0 µM", "1 µM"]


# ── am I finished? ──────────────────────────────────────────────────────────

def test_the_shortfall_is_counted_on_the_control_that_selects_it(tab):
    """The count used to be a sentence that also carried up to eight well ids, a
    rendering fact ("they are ringed") and a policy statement about a different
    pane, in one muted run-on line under the plate.

    It is one number, and it belongs on the link that selects exactly those
    wells — which turns "seven are missing" from something to read into
    something to press.
    """
    _dose_tab(tab)
    tab.w("_var_plate").selectedWells = ["A01"]
    _chips(tab)[0].click()

    assert tab.w("_var_sel_missing").text() == "unassigned (7)"
    assert tab.w("_var_sel_missing").isEnabled()


def test_pressing_it_selects_exactly_the_wells_that_are_short(tab):
    """The half that makes the count worth showing. A label that named the wells
    and could not select them left the user hunting a grid of ninety-six by
    eye."""
    _dose_tab(tab)
    tab.w("_var_plate").selectedWells = ["A01"]
    _chips(tab)[0].click()

    tab.w("_var_sel_missing").click()

    assert set(tab.w("_var_plate").selectedWells) == set(tab.wells) - {"A01"}


def test_a_fully_assigned_plate_offers_no_action_that_would_do_nothing(tab):
    """Zero short is said by the rail's tick, and the link greys out rather than
    staying live with nothing to select."""
    _dose_tab(tab)
    tab.w("_var_fill_apply").click()

    assert tab.w("_var_sel_missing").text() == "unassigned"
    assert not tab.w("_var_sel_missing").isEnabled()
    assert _rail_labels(tab) == ["Dose    8/8 ✓"]


def test_the_rail_says_how_much_of_the_plate_each_variable_covers(tab):
    """Every variable's answer at once, which is the question the tab is asked.

    The rail listed a *value count* — a number the user had just typed in and
    could read off the list beside it — so "is this plate finished?" could only
    be answered one variable at a time by selecting each and reading a sentence
    under the plate.
    """
    _dose_tab(tab)
    tab.w("_var_plate").selectedWells = ["A01", "A02"]
    _chips(tab)[0].click()

    assert _rail_labels(tab) == ["Dose    2/8"]


@pytest.mark.wells({})
def test_a_plate_with_no_dataset_says_that_rather_than_showing_zero(tab):
    _dose_tab(tab)
    assert "No dataset is open" in tab.w("_var_fill_scope").text()


def test_a_variable_with_no_values_says_there_is_nothing_to_fill(tab):
    tab.w("_var_add").click()
    assert "nothing to fill" in tab.w("_var_fill_scope").text()


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
    # The **painted** text, from the row widget — ``item.text()`` is blanked so
    # the delegate does not draw it under the widget, and a test reading it
    # would be asserting a string that never reaches the screen.
    from well_viewer.features.home.variables.editor_view import row_label_text

    return [row_label_text(listing, i) for i in range(listing.count())]


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

    # Asserted against the shared constant, not a second copy of the string.
    # The row read "Mixed" while the figure bands read "—mixed—", under a
    # comment in variable_series claiming the two say it identically. Comparing
    # against MIXED_LABEL is what makes that claim testable rather than a hope.
    from well_viewer.domain.variable_series import MIXED_LABEL

    assert f"{MIXED_LABEL} (2 values across 2 wells)" in _group_rows(tab)[0]


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
    """Its colours, rings, chips and sentence all belonged to it.

    The variable is filled here, so deleting it now asks first — this patches
    that one call rather than letting the autouse no-modal-dialogs fixture
    raise, which is the escape ARCHITECTURE names for a test that means to
    exercise a dialog path.
    """
    from PySide6.QtWidgets import QMessageBox

    _dose_tab(tab)
    tab.w("_var_fill_apply").click()
    assert tab.w("_var_chips").layout().count() > 0

    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    seen = _watch_clears(tab, monkeypatch)
    _delete_row(tab, tab.w("_var_list").currentRow())

    assert seen["colors"] and seen["rings"] and seen["markers"], (
        f"the deleted variable's decoration was never cleared: {seen}"
    )
    assert tab.w("_var_chips").layout().count() == 0, (
        "the deleted variable's value chips are still there, and still "
        "connected — pressing one reaches assign_selected and does nothing"
    )
    assert tab.w("_var_selection").text() == ""


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
    assert tab.w("_var_selection").text() == "", (
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
    """"2 columns per value" reads as two columns to any reader, and that is
    what it does — but while the cycling counted wells the two readings differed
    by the width of the plate, and nothing on screen said which was meant. The
    unit follows the axis so the two cannot drift apart."""
    _dose_tab(tab)
    axis = tab.w("_var_fill_axis")
    unit = tab.w("_var_fill_unit")
    tab.w("_var_fill_repeat").setValue(2)

    axis.setCurrentIndex(axis.findData("row"))
    assert unit.text() == "rows per value."

    axis.setCurrentIndex(axis.findData("col"))
    assert unit.text() == "columns per value."


def test_the_unit_is_singular_at_one(tab):
    """The control reads as a sentence now, and "1 rows per value" is the sort
    of thing that makes a reader stop and wonder what they have misunderstood."""
    _dose_tab(tab)
    tab.w("_var_fill_repeat").setValue(1)
    assert tab.w("_var_fill_unit").text() == "row per value."


def test_a_colliding_series_is_drawn_as_a_numbering_not_as_part_labels(tab):
    """The rule this replaces truncated per value, and on the series it was
    built for it mixed two kinds of glyph without marking either.

    ``0, 0.1, 0.3, 1`` came out ``0, 02, 03, 1``: ``1`` is a real dose, ``02``
    is a code standing for ``0.1``, and nothing on the plate or in the key said
    which was which. A reader taking ``03`` at face value read a well as three
    times the dose it holds. Numbering the whole variable is a code that is
    visibly a code.

    Driven through the plate, not the rule — the rule's own cases are in
    ``tests/test_variables_domain_readouts.py``. What is asserted here is that
    the tab draws what the rule says, on the wells it says it about.
    """
    dose = _dose_tab(tab, labels="0, 0.1, 0.3, 1")
    # Along the columns: the fixture is two rows of four, so a fill along rows
    # would land only two of the four values and the numbering could not be
    # told from a truncation that happened to agree on those two.
    axis = tab.w("_var_fill_axis")
    axis.setCurrentIndex(axis.findData("col"))
    tab.w("_var_fill_apply").click()          # every well now holds a dose

    # A second variable to look at, so Dose is available to overlay: the combo
    # lists the *other* variables, so a variable is never its own overlay.
    tab.w("_var_add").click()
    tab.w("_var_name").setText("Line")
    tab.w("_var_name").editingFinished.emit()
    tab.w("_var_value_entry").setText("HeLa, U2OS")
    tab.w("_var_value_add").click()

    marks = {}
    plate = tab.w("_var_plate")
    plate.setWellMarker = lambda w, g: marks.__setitem__(w, g)
    combo = tab.w("_var_overlay")
    combo.setCurrentIndex(combo.findData(dose))

    assert set(marks.values()) == {"1", "2", "3", "4"}, marks
    assert "0" not in marks.values(), (
        "a value's own label is being drawn beside codes for the others")
    assert "1 = 0" in tab.w("_var_overlay_legend").text()


def _select_variable_row(tab, variable_id):
    listing = tab.w("_var_list")
    for row in range(listing.count()):
        if listing.item(row).data(Qt.UserRole) == variable_id:
            listing.setCurrentRow(row)
            return
    raise AssertionError(f"no rail row for {variable_id}")


def test_the_plate_drags_as_a_rubber_band_not_a_paintbrush(tab):
    """§F named the rubber-band configuration and named the Groups plate as the
    precedent; this plate was left on ``paint``.

    It matters because a block design is what this plate exists to describe:
    painting a 4x3 block freehand takes a serpentine drag, and dragging back
    over a cell already crossed de-selects it.
    """
    plate = tab.w("_var_plate")
    assert plate.dragMode() == "rect"


def test_the_overlay_combo_states_its_rule_when_it_has_nothing_to_offer(tab):
    """Disabled rather than hidden is the right call — there *is* a rule the
    user can act on — but nothing said what it was. ``Group by`` is hidden in
    the same situation precisely because an empty one states no rule."""
    _dose_tab(tab)                       # one variable, so nothing to overlay
    combo = tab.w("_var_overlay")

    assert not combo.isEnabled()
    assert "second variable" in combo.toolTip()


#: Everything on the plate side of the tab, which stands or falls together.
#: A roster rather than the three widgets that came to mind: the first version
#: of the empty state hid the plate, the fill row and the chips and left the
#: selection links, the Preview / Apply fill buttons, the overlay combo and the
#: standing policy note live on a tab with nothing to act on — and the test
#: asserted only the three, so it passed.
#:
#: ``isVisibleTo`` rather than ``isHidden``: hiding a parent does not set a
#: child's own hidden flag, so ``isHidden`` answers False for every child of a
#: hidden column and the roster would measure nothing.
_PLATE_SIDE = (
    "_var_plate_column", "_var_plate", "_var_fill_row", "_var_chips",
    "_var_chips_caption", "_var_fill_preview", "_var_fill_apply",
    "_var_overlay", "_var_sel_all", "_var_sel_none", "_var_sel_missing",
    "_var_sel_same", "_var_selection", "_var_showing_caption",
)


def test_with_no_variable_there_is_no_plate_to_stare_at(tab):
    """The opposite call from a greyed control, and right for the same reason.

    ``_clear_plate`` used to blank the labels and leave a full ninety-six well
    grid, a fill row and a chip row on screen, all inert. Greying is for a
    control that cannot work *yet* and states a rule the user can act on; there
    is no plate to show at all until a variable exists, and a greyed grid around
    a two-paragraph explainer reads as software that has broken. The one thing a
    user can do here — ``+ Variable`` — is what is left.
    """
    for name in _PLATE_SIDE:
        assert not tab.w(name).isVisibleTo(tab.frame), (
            f"{name} is live on a tab with no variable to act on")
    assert tab.w("_var_add").isVisibleTo(tab.frame), (
        "the one action available on an empty tab was hidden too")


def test_defining_a_variable_brings_the_plate_back(tab):
    """The half that makes the test above mean something: a tab that hid them
    and never restored them would satisfy it and be useless.

    ``isHidden`` rather than ``isVisible``: a child of a widget that has never
    been shown reports itself invisible whatever its own flag says.
    """
    _dose_tab(tab)

    for name in _PLATE_SIDE:
        assert tab.w(name).isVisibleTo(tab.frame), name


def test_deleting_a_filled_variable_asks_first(tab, monkeypatch):
    """One click destroyed the definition *and* every assignment under it, with
    no confirmation and no undo — and a filled 96-well dose grid is the most
    expensive thing on this page. The Groups tab already confirms a bulk
    replace; this is a bulk delete."""
    from PySide6.QtWidgets import QMessageBox

    _dose_tab(tab)
    tab.w("_var_fill_apply").click()
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.No))

    _delete_row(tab, tab.w("_var_list").currentRow())

    assert asked, "a filled variable was deleted without asking"
    assert _vset(tab).variables, "answering No deleted it anyway"


def test_deleting_an_unfilled_variable_asks_nothing(tab, monkeypatch):
    """Nothing to lose, so no prompt. A dialog here is the kind users learn to
    click through — which is how the prompt that matters gets clicked through
    too."""
    from PySide6.QtWidgets import QMessageBox

    _dose_tab(tab)                       # defined, never filled
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.Yes))

    _delete_row(tab, tab.w("_var_list").currentRow())

    assert not asked
    assert not _vset(tab).variables


def test_a_fill_says_how_many_wells_it_changed(tab):
    """A fill overwrites every existing assignment for the variable and used to
    report nothing, so re-filling a finished plate looked identical to filling an
    empty one."""
    _dose_tab(tab)

    tab.w("_var_fill_apply").click()

    assert "Filled 8 well(s) with Dose." == tab.w("_var_message").text()


def test_a_fill_that_changed_nothing_says_that_rather_than_going_quiet(tab):
    """The half the previous fix left out.

    The old confirmation was written only when the count was non-zero, so a user
    re-running an identical fill to check it had taken got the same screen as one
    whose button was broken.
    """
    _dose_tab(tab)
    tab.w("_var_fill_apply").click()

    tab.w("_var_fill_apply").click()

    assert "Nothing changed" in tab.w("_var_message").text()


# ── the selection is a thing the tab knows about ────────────────────────────

def test_the_tab_says_what_is_selected_and_what_those_wells_hold(tab):
    """Fourteen controls were connected and none of them was the plate.

    Dragging a box changed nothing on screen, so a user could not tell a
    selection had been made; and because selecting triggered no redraw, the
    "select wells on the plate first" instruction a chip wrote into the status
    label stayed up after they had complied.

    Asserted as the sentence, not as "the label is not empty" — a label that
    said "some wells are selected" would satisfy the weaker version and tell the
    user nothing they came for.
    """
    variable_id = _dose_tab(tab)
    plate = tab.w("_var_plate")
    plate.selectedWells = ["A01", "A02"]
    _chips(tab)[0].click()                       # A01, A02 -> "0"
    plate.selectedWells = ["A01", "A02", "A03"]
    plate.selectionChanged.emit(["A01", "A02", "A03"])

    assert tab.w("_var_selection").text() == (
        "3 well(s) selected — 2 hold 0, 1 hold no value.")
    assert variable_id


def test_an_empty_selection_says_how_to_make_one(tab):
    _dose_tab(tab)
    tab.w("_var_plate").selectedWells = []
    tab.w("_var_plate").selectionChanged.emit([])

    assert "No wells selected" in tab.w("_var_selection").text()


def test_select_all_takes_the_dataset_not_ninety_six(tab):
    """``role="canvas"`` hides the plate's own All / Invert / Clear row on the
    promise that the host supplies its own — and that row's ``All`` means all
    ninety-six, which on a plate imaged in half is wrong."""
    _dose_tab(tab)
    tab.w("_var_sel_all").click()

    assert set(tab.w("_var_plate").selectedWells) == set(tab.wells)


def test_select_none_clears_a_selection_that_would_otherwise_accumulate(tab):
    """A rubber-band selection adds across gestures and the corner circle is
    eleven pixels wide, so without this there was no way back from a wrong
    one."""
    _dose_tab(tab)
    tab.w("_var_sel_all").click()
    tab.w("_var_sel_none").click()

    assert list(tab.w("_var_plate").selectedWells) == []


def test_select_same_value_finds_the_rest_of_the_wells_holding_it(tab):
    """The route to "four of these got the wrong dose": pick one, press this.

    The fixture gives ``1`` to three wells and ``0`` to one, and selects the
    single ``0`` — so a rule that grew the selection to *everything*, or that
    returned it unchanged, produces a different answer from the right one.
    """
    variable_id = _dose_tab(tab)
    plate = tab.w("_var_plate")
    plate.selectedWells = ["A01", "A02", "A03"]
    _chips(tab)[1].click()                       # -> "1"
    plate.selectedWells = ["B01"]
    _chips(tab)[0].click()                       # -> "0"

    plate.selectedWells = ["A02"]
    tab.w("_var_sel_same").click()

    assert set(plate.selectedWells) == {"A01", "A02", "A03"}
    assert _assigned(tab, variable_id)["B01"] == "0"


def test_select_same_with_nothing_picked_says_so(tab):
    _dose_tab(tab)
    tab.w("_var_plate").selectedWells = []
    tab.w("_var_sel_same").click()

    assert "at least one well" in tab.w("_var_message").text()


def test_a_group_row_selects_the_wells_it_is_complaining_about(tab):
    """The list knows exactly which wells are inconsistent and could only print
    them into a sentence for the user to find by eye on the plate above it."""
    _dose_tab(tab)
    tab.ctx.state.selection.selections = [
        {"name": "Ctrl", "wells": ["A01", "A02", "B04"]}]
    from well_viewer.features.home.variables.controller import refresh_variables
    refresh_variables(tab.ctx)

    listing = tab.w("_var_groups")
    listing.itemDoubleClicked.emit(listing.item(0))

    assert set(tab.w("_var_plate").selectedWells) == {"A01", "A02", "B04"}


# ── the fill honours the selection, and says so ─────────────────────────────

def test_a_fill_with_wells_selected_touches_only_those(tab):
    """The most destructive button on the page read the dataset and ignored the
    plate, so a user who had carefully picked twenty-four wells pressed it and
    rewrote all ninety-six."""
    variable_id = _dose_tab(tab)
    plate = tab.w("_var_plate")
    plate.selectedWells = ["A01", "A02"]
    plate.selectionChanged.emit(["A01", "A02"])

    tab.w("_var_fill_apply").click()

    assert set(_assigned(tab, variable_id)) == {"A01", "A02"}


def test_the_scope_line_follows_the_selection_rather_than_the_button(tab):
    """The label and the button have to describe one action.

    This was live in the rebuilt tab and caught in a render: ``_fill_result``
    narrowed a fill to the selection while the scope label was repainted only by
    a full redraw, so with four wells picked the page read "Covers all 96
    well(s)" over a button that would have filled four.
    """
    _dose_tab(tab)
    assert "all 8 well(s)" in tab.w("_var_fill_scope").text()

    plate = tab.w("_var_plate")
    plate.selectedWells = ["A01", "A02"]
    plate.selectionChanged.emit(["A01", "A02"])

    assert "selected 2 well(s)" in tab.w("_var_fill_scope").text()


def test_the_scope_line_says_what_it_will_overwrite(tab):
    """It read ``over the 96 well(s) in this dataset`` — a true statement about
    the dataset that says nothing about the assignments about to be replaced."""
    _dose_tab(tab)
    tab.w("_var_fill_apply").click()

    assert "replaces the Dose on 8 of them" in tab.w("_var_fill_scope").text()


def test_preview_paints_the_result_and_stores_nothing(tab):
    """Free, because ``fill`` is pure: the preview is the returned set painted.

    Asserted on both halves. "It stored nothing" alone is satisfied by a button
    that does nothing at all, and "it painted" alone by one that committed.
    """
    variable_id = _dose_tab(tab)
    painted = {}
    tab.w("_var_plate").setWellColor = lambda w, c: painted.__setitem__(w, c)

    saves_before = len(tab.saves)
    tab.w("_var_fill_preview").click()

    assert painted, "Preview painted nothing"
    assert _assigned(tab, variable_id) == {}, "Preview committed the fill"
    assert len(tab.saves) == saves_before, "Preview scheduled a save"
    assert "Preview" in tab.w("_var_fill_scope").text()


# ── undo ────────────────────────────────────────────────────────────────────

def test_undo_puts_back_what_a_fill_replaced(tab):
    variable_id = _dose_tab(tab)
    tab.w("_var_plate").selectedWells = ["A01"]
    _chips(tab)[1].click()                       # A01 -> "1"
    before = _assigned(tab, variable_id)

    tab.w("_var_fill_apply").click()
    assert _assigned(tab, variable_id) != before

    tab.w("_var_undo").click()

    assert _assigned(tab, variable_id) == before


def test_undo_puts_back_a_deleted_variable(tab, monkeypatch):
    """The most expensive click on the page, and the one the confirmation
    dialog can only warn about."""
    from PySide6.QtWidgets import QMessageBox

    _dose_tab(tab)
    tab.w("_var_fill_apply").click()
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    _delete_row(tab, tab.w("_var_list").currentRow())
    assert not _vset(tab).variables

    tab.w("_var_undo").click()

    assert [v.name for v in _vset(tab).variables] == ["Dose"]


def test_undo_is_dead_until_there_is_something_to_undo(tab):
    """A live button that does nothing is the failure this tab's own code
    names."""
    assert not tab.w("_var_undo").isEnabled()
    assert "Nothing to undo" in tab.w("_var_undo").toolTip()

    _dose_tab(tab)

    assert tab.w("_var_undo").isEnabled()


def test_undo_is_one_step_and_says_it_ran(tab):
    """One deep on purpose: the expensive mistake here is a single button, not a
    session's worth of edits, and a second press must not walk further back."""
    variable_id = _dose_tab(tab)
    tab.w("_var_fill_apply").click()
    tab.w("_var_undo").click()
    assert tab.w("_var_message").text() == "Undone."
    after_one = _assigned(tab, variable_id)

    tab.w("_var_undo").click()

    assert _assigned(tab, variable_id) == after_one
    assert not tab.w("_var_undo").isEnabled()


def test_a_dataset_swap_drops_the_undo_with_the_variables_it_belonged_to(tab):
    """Carried across, it would sit under a live Undo offering the previous
    plate's doses over the current one's numbers."""
    from well_viewer.persistence.sample_variables import load_from_data_dir

    _dose_tab(tab)
    tab.w("_var_fill_apply").click()
    assert tab.ctx.state.selection.variables_undo is not None

    tab.ctx.state.dataset.data_dir = "/somewhere/else"
    tab.ctx.state.dataset.pipeline_info = {}
    load_from_data_dir(tab.ctx)

    assert tab.ctx.state.selection.variables_undo is None


# ── a refused edit says it was refused ──────────────────────────────────────

def test_a_duplicate_value_says_it_was_not_added(tab):
    """``add_value`` drops it, which is the right rule delivered in silence: the
    entry cleared itself and nothing appeared, so a user who typed a dose they
    already had saw the same screen as one whose button was broken."""
    _dose_tab(tab)
    tab.w("_var_value_entry").setText("1")
    tab.w("_var_value_add").click()

    assert [v.label for v in _vset(tab).variables[0].values] == ["0", "1"]
    assert "Already there" in tab.w("_var_message").text()


def test_the_ones_that_were_new_are_still_added(tab):
    """A comma list part-refused must not be refused whole."""
    _dose_tab(tab)
    tab.w("_var_value_entry").setText("1, 3")
    tab.w("_var_value_add").click()

    assert [v.label for v in _vset(tab).variables[0].values] == ["0", "1", "3"]
    assert "1" in tab.w("_var_message").text()


def test_a_rename_onto_another_label_says_why_the_row_snapped_back(tab):
    _dose_tab(tab)
    values = tab.w("_var_values")
    values.item(0).setText("1")               # already taken by the second row

    assert [v.label for v in _vset(tab).variables[0].values] == ["0", "1"]
    assert "already has a value" in tab.w("_var_message").text()


def test_a_variable_renamed_onto_a_taken_name_says_it_was_numbered(tab):
    """``unique_name`` turns a second ``Dose`` into ``Dose 2`` — renaming beats
    refusing — but the field was repainted from the model and the user's word was
    rewritten with nothing said about it."""
    _dose_tab(tab)
    tab.w("_var_add").click()
    tab.w("_var_name").setText("Dose")
    tab.w("_var_name").editingFinished.emit()

    assert [v.name for v in _vset(tab).variables] == ["Dose", "Dose 2"]
    assert "already called" in tab.w("_var_message").text()


# ── the buttons that could not act were never greyed ────────────────────────

def test_the_value_buttons_are_dead_with_no_value_selected(tab):
    tab.w("_var_add").click()                 # a variable with no values at all

    for name in ("_var_value_remove", "_var_value_up", "_var_value_down"):
        assert not tab.w(name).isEnabled(), name


def test_the_move_buttons_are_dead_at_the_end_they_cannot_move_towards(tab):
    """The clamp is real and was invisible: pressing ▲ on the first row did
    nothing and said nothing."""
    _dose_tab(tab, labels="0, 1, 3")
    values = tab.w("_var_values")

    values.setCurrentRow(0)
    assert not tab.w("_var_value_up").isEnabled()
    assert tab.w("_var_value_down").isEnabled()

    values.setCurrentRow(2)
    assert tab.w("_var_value_up").isEnabled()
    assert not tab.w("_var_value_down").isEnabled()


# ── the header says whose marks are on the plate ────────────────────────────

def test_the_header_names_the_variable_the_plate_is_showing(tab):
    """One rail selection decides the definition on screen, the chips' target,
    the plate's colours and what a fill rewrites — and the answer was written
    down nowhere but the rail's own highlight."""
    _dose_tab(tab)
    tab.w("_var_unit").setText("µM")
    tab.w("_var_unit").editingFinished.emit()

    assert tab.w("_var_showing").text() == "Dose (µM) as colour"


def test_the_header_names_the_overlay_too(tab):
    dose = _dose_tab(tab)
    tab.w("_var_add").click()
    tab.w("_var_name").setText("Line")
    tab.w("_var_name").editingFinished.emit()
    combo = tab.w("_var_overlay")
    combo.setCurrentIndex(combo.findData(dose))

    assert tab.w("_var_showing").text() == "Line as colour   ·   Dose as letters"


def test_the_plate_wears_a_rim_on_the_wells_you_picked(tab):
    """The tab has spent the fill on data, so without this a selected assigned
    well and an unselected one are the same pixels — which is the tab's central
    gesture having no feedback at all.

    Asserted at the plate, on the flag the painter reads, because a controller
    that computed the right thing and never told the widget is exactly the bug
    this repo has shipped before.
    """
    assert tab.w("_var_plate").selectionRim() is True


def test_a_value_row_wears_the_ink_its_wells_will_take(tab):
    """The definition list was the one surface on the tab with no colour in it.

    The plate and the chips were inked and the list was plain text, so "the
    third row of my list" and "the yellow wells" could only be connected by
    counting. It also makes the reorder buttons honest: the ink is indexed by
    position, so moving a value changes its colour here, on the plate and in
    every plot.

    Asserted as the swatch matching ``value_colors`` for that value, not as
    "the row has an icon" — a row wearing the wrong colour would satisfy the
    weaker version and be worse than a row wearing none.
    """
    from well_viewer.domain.variable_series import value_colors

    _dose_tab(tab, labels="0, 1, 3")
    variable = _vset(tab).variables[0]
    colours = value_colors(variable)
    values = tab.w("_var_values")

    for row, value in enumerate(variable.values):
        icon = values.item(row).icon()
        assert not icon.isNull(), f"row {row} carries no swatch"
        assert _dominant_colour(icon) == _rgb(colours[value.id]), (
            f"row {row} wears an ink that is not its value's")


def _rgb(colour):
    from PySide6.QtGui import QColor

    return QColor(colour).getRgb()[:3]


def _dominant_colour(icon):
    """The commonest opaque colour in an icon, which for a filled square is its
    fill."""
    from collections import Counter

    image = icon.pixmap(11, 11).toImage()
    counts = Counter(
        image.pixelColor(x, y).getRgb()[:3]
        for y in range(image.height()) for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 200)
    return counts.most_common(1)[0][0] if counts else None


def test_the_chip_row_wraps_rather_than_running_off_the_page(tab):
    """A twelve-value dose series is one row of a plate and an ordinary thing to
    define. In a ``QHBoxLayout`` the last chips were pushed off the right edge
    with nothing to scroll and no sign they existed."""
    from widgets.flow_layout import FlowLayout

    _dose_tab(tab, labels="0, 0.1, 0.3, 1, 3, 10, 30, 100, 300, 1000")
    assert isinstance(tab.w("_var_chips").layout(), FlowLayout)
    assert len(_chips(tab)) == 11          # ten values plus No value


def test_the_row_x_deletes_its_own_row_not_the_selected_one(tab):
    """The one property the × was introduced for, and it had no guard.

    Every other delete test selects the row it then presses, so the clicked row
    and the selected row are always the same and a × bound to the *selection*
    passes them all — a reviewer proved it by reverting the binding and
    watching 96 tests stay green. Here they differ on purpose.
    """
    from well_viewer.features.home.variables.editor_view import row_delete_button

    tab.w("_var_add").click()
    tab.w("_var_add").click()
    listing = tab.w("_var_list")
    assert listing.count() == 2, _rail_labels(tab)
    first = listing.item(0).data(Qt.UserRole)

    listing.setCurrentRow(0)                 # selected: row 0
    row_delete_button(listing, 1).click()    # pressed:  row 1

    kept = [v.id for v in _vset(tab).variables]
    remaining = kept
    assert remaining == [first], (
        "pressing row 1's × removed the selected row (0) instead of the row "
        f"it sits on: kept {remaining}, expected {[first]}")
