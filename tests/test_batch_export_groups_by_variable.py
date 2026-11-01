"""Batch Export's export groups can be chosen by sample variable, not only by well.

The group editor's plate map picks wells by position; the "By variable" row
beside it picks every loaded well holding one value of a sample variable and
adds it to (or takes it out of) the active group, and Quick Setup's
"One group per value of…" replaces the groups with one per value.

The fixture is built so the right and wrong answers differ (CLAUDE.md §7.8):

* the variable's values are declared **against** plate order (``U2OS`` before
  ``HeLa`` while HeLa holds the earlier wells), so a group order taken from the
  wells rather than from the variable is caught;
* one replicate set (``Mix``) holds wells of both values, so a rule that pulls
  in a well's whole set — what the plate drag does — exports a U2OS well under
  the HeLa name and is caught;
* one well (``C01``) has no value, so a rule that drops unassigned wells
  without saying so, or files them in a group, is caught;
* one value (``Vero``) is held by no well, so a rule that makes empty groups
  is caught.
"""

from __future__ import annotations

import pytest

from review.domain import sample_variables as sv
from review.domain.batch_models import BarGroup, ReplicateSet
from review.features.batch_export._variable_groups import (
    add_membership,
    groups_per_value,
    membership_for_value,
    remove_membership,
)

LOADED = ["A01", "A02", "A03", "A04", "B01", "B02", "C01"]


def _plate():
    vset, vid = sv.new_variable(sv.VariableSet(), "Cell line")
    for label in ("U2OS", "HeLa", "Vero"):
        vset = sv.add_value(vset, vid, label)
    ids = {v.label: v.id for v in vset.by_id(vid).values}
    vset = sv.assign(vset, vid, ["A01", "A02", "A03", "A04"], ids["HeLa"])
    vset = sv.assign(vset, vid, ["B01", "B02"], ids["U2OS"])
    return vset, vid, ids


def _sets():
    # Fresh objects on every call, as ``plot_replicate_sets_loaded`` builds them.
    return [ReplicateSet("R1", ["A01", "A02"]),
            ReplicateSet("Mix", ["A03", "B01"]),
            ReplicateSet("R2", ["B02", "H12"])]   # H12 is not loaded


def test_a_value_takes_whole_sets_and_splits_a_mixed_one():
    vset, vid, ids = _plate()
    m = membership_for_value(vset, vid, ids["HeLa"], LOADED, _sets())
    assert m.wells == ["A01", "A02", "A03", "A04"]
    assert [r.name for r in m.members] == ["R1"]
    # Mix's A03 holds HeLa, its B01 does not: only A03 joins, and alone.
    assert m.solo_wells == ["A03", "A04"]
    assert m.split_sets == ["Mix"]


def test_a_set_is_whole_over_its_loaded_wells_only():
    vset, vid, ids = _plate()
    m = membership_for_value(vset, vid, ids["U2OS"], LOADED, _sets())
    # R2's H12 is not in the dataset, so B02 alone makes R2 whole.
    assert [r.name for r in m.members] == ["R2"]
    assert m.solo_wells == ["B01"]
    assert m.split_sets == ["Mix"]


def test_no_value_selects_the_unassigned_wells():
    vset, vid, _ids = _plate()
    m = membership_for_value(vset, vid, None, LOADED, _sets())
    assert m.wells == ["C01"]
    assert m.solo_wells == ["C01"]


def test_one_group_per_value_in_the_variables_order():
    vset, vid, _ids = _plate()
    groups, unassigned = groups_per_value(vset, vid, LOADED, _sets())
    # U2OS was declared first, though HeLa holds the earlier wells; Vero holds
    # nothing and makes no group.
    assert [g.name for g in groups] == ["U2OS", "HeLa"]
    assert [g.membership.wells for g in groups] == [
        ["B01", "B02"], ["A01", "A02", "A03", "A04"]]
    assert unassigned == ["C01"]


def test_group_names_carry_the_unit():
    vset, vid = sv.new_variable(sv.VariableSet(), "Dose", kind=sv.NUMBER, unit="µM")
    vset = sv.add_value(vset, vid, "10")
    (value,) = vset.by_id(vid).values
    vset = sv.assign(vset, vid, ["A01"], value.id)
    groups, _ = groups_per_value(vset, vid, ["A01"], [])
    assert [g.name for g in groups] == ["10 µM"]


def test_adding_twice_does_not_add_a_set_twice():
    vset, vid, ids = _plate()
    grp = BarGroup("G")
    m = membership_for_value(vset, vid, ids["HeLa"], LOADED, _sets())
    assert add_membership(grp, m) == 4
    again = membership_for_value(vset, vid, ids["HeLa"], LOADED, _sets())
    assert add_membership(grp, again) == 0
    assert [r.name for r in grp.members] == ["R1"]
    assert grp.solo_wells == ["A03", "A04"]


def test_removing_keeps_a_set_that_also_holds_other_values():
    vset, vid, ids = _plate()
    grp = BarGroup("G", members=[ReplicateSet("R1", ["A01", "A02"]),
                                 ReplicateSet("Mix", ["A03", "B01"])],
                   solo_wells=["A04", "B02"])
    m = membership_for_value(vset, vid, ids["HeLa"], LOADED, _sets())
    removed, kept = remove_membership(grp, m)
    assert [r.name for r in grp.members] == ["Mix"]
    assert grp.solo_wells == ["B02"]
    assert kept == ["Mix"]
    assert removed == 3   # A01, A02 (R1) and A04; A03 stays with Mix


# ── through the real panel ──────────────────────────────────────────────────
@pytest.fixture
def panel(qtbot):
    pytest.importorskip("PySide6")
    from domain.selections_format import make_selection
    from review.features.batch_export import BatchExportPanel
    from tests._minimal_shell import minimal_shell

    ctx, _window = minimal_shell(qtbot)
    ctx.state.dataset.well_paths = {w: f"/x/{w}.zip" for w in LOADED}
    names: set = set()
    ids: set = set()
    ctx.state.selection.selections = [
        make_selection(name=r.name, wells=r.wells, source="rep_set",
                       used_names=names, used_ids=ids, fallback_color_idx=i)
        for i, r in enumerate(_sets())]
    vset, _vid, _ids = _plate()
    ctx.state.selection.variables = vset
    p = BatchExportPanel(ctx, None, use_sidebar_groups=False)
    return p


def _pick(combo, text):
    idx = combo.findText(text)
    assert idx >= 0, [combo.itemText(i) for i in range(combo.count())]
    combo.setCurrentIndex(idx)


def _status(panel):
    return panel._vsel_status_text()


def test_the_add_button_fills_the_active_group(panel, qtbot):
    from PySide6.QtCore import Qt

    panel._grp_add()
    _pick(panel._vsel_var_cb, "Cell line")
    _pick(panel._vsel_val_cb, "HeLa")
    qtbot.mouseClick(panel._vsel_add_btn, Qt.LeftButton)
    grp = panel._groups[0]
    assert [r.name for r in grp.members] == ["R1"]
    assert grp.solo_wells == ["A03", "A04"]
    # Named for the value, not for its wells: the name is the figure's title.
    assert grp.name == "HeLa"
    assert _status(panel) == (
        "Added 4 wells to \u201cHeLa\u201d.\n"
        "\u26a0 1 replicate set split (Mix): its other wells are not HeLa, "
        "so A03 joined as solo wells.")
    # The plate shows the result: the boundary call carries the group colour
    # for exactly the group's wells (CLAUDE.md §7.4 #1).
    sent = {}
    panel._map_plate.setWellColors = lambda mapping: sent.update(mapping)
    panel._refresh_map()
    colour = panel._group_color(0)
    assert sorted(w for w, c in sent.items() if c == colour) == sorted(grp.wells)

    qtbot.mouseClick(panel._vsel_remove_btn, Qt.LeftButton)
    assert grp.wells == []


def test_the_name_follows_the_values_and_survives_a_drag(panel):
    panel._grp_add()
    _pick(panel._vsel_var_cb, "Cell line")
    _pick(panel._vsel_val_cb, "HeLa")
    panel._vsel_add()
    _pick(panel._vsel_val_cb, "U2OS")
    panel._vsel_add()
    grp = panel._groups[0]
    assert grp.name == "HeLa + U2OS"
    # A drag no longer renames it to a list of wells.
    panel._plate_press("C01")
    panel._plate_release()
    assert "C01" in grp.wells
    assert grp.name == "HeLa + U2OS"
    _pick(panel._vsel_val_cb, "HeLa")
    panel._vsel_remove()
    assert grp.name == "U2OS"


def test_a_hand_given_name_is_kept(panel, monkeypatch):
    from review.features.batch_export import base_panel

    panel._grp_add()
    _pick(panel._vsel_var_cb, "Cell line")
    _pick(panel._vsel_val_cb, "HeLa")
    panel._vsel_add()
    assert panel._groups[0].name == "HeLa"
    monkeypatch.setattr(base_panel, "ask_name_dialog", lambda *a, **k: "Mine")
    panel._grp_rename(0)
    # A group the value had named, then renamed by hand, keeps the hand name.
    _pick(panel._vsel_val_cb, "U2OS")
    panel._vsel_add()
    assert panel._groups[0].name == "Mine"


def test_add_with_no_group_makes_one_named_for_the_value(panel):
    assert panel._groups == []
    _pick(panel._vsel_var_cb, "Cell line")
    _pick(panel._vsel_val_cb, "U2OS")
    panel._vsel_add()
    assert [g.name for g in panel._groups] == ["U2OS"]
    assert panel._groups[0].wells == ["B02", "H12", "B01"]


def test_removal_names_the_set_it_kept(panel):
    from review.features.batch_export._common import BarGroup

    panel._groups.append(BarGroup("G", members=[ReplicateSet("Mix", ["A03", "B01"])]))
    panel._active_grp = 0
    _pick(panel._vsel_var_cb, "Cell line")
    _pick(panel._vsel_val_cb, "HeLa")
    panel._vsel_remove()
    assert [r.name for r in panel._groups[0].members] == ["Mix"]
    assert "Kept Mix" in _status(panel)
    assert "\u2212 button" in _status(panel)


def test_quick_setup_makes_one_group_per_value(panel):
    menu = panel._vsel_quick_menu
    menu.aboutToShow.emit()
    (action,) = [a for a in menu.actions() if a.text() == "Cell line"]
    action.trigger()
    assert [g.name for g in panel._groups] == ["U2OS", "HeLa"]
    assert panel._groups[1].wells == ["A01", "A02", "A03", "A04"]
    assert _status(panel) == (
        "2 groups, one per Cell line.\n"
        "\u26a0 1 well with no Cell line left out: C01.\n"
        "\u26a0 1 replicate set split across groups (Mix): their wells hold "
        "different values.")
    # The groups persist like hand-made ones.
    doc = panel._ctx.runtime.batch_export.export_groups_doc
    assert [g["name"] for g in doc["groups"]] == ["U2OS", "HeLa"]


@pytest.mark.parametrize("answer, expected", [("No", ["Mine"]), ("Yes", ["U2OS", "HeLa"])])
def test_quick_setup_asks_before_replacing_groups(panel, monkeypatch, answer, expected):
    from PySide6.QtWidgets import QMessageBox

    from review.features.batch_export._common import BarGroup

    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: asked.append(a) or getattr(QMessageBox, answer)))
    panel._groups.append(BarGroup("Mine", solo_wells=["D01"]))
    panel._active_grp = 0
    panel._quick_by_variable(panel._vsel_var_cb.currentData())
    assert len(asked) == 1
    assert [g.name for g in panel._groups] == expected


def test_a_status_about_old_variables_is_cleared(panel):
    panel._vsel_add()
    assert _status(panel)
    panel._ctx.state.selection.variables = sv.VariableSet()
    panel._vsel_refresh_variables()
    assert _status(panel) == ""


def test_the_row_says_where_to_define_variables_when_there_are_none(panel):
    panel._ctx.state.selection.variables = sv.VariableSet()
    panel._vsel_refresh_variables()
    assert not panel._vsel_controls.isVisibleTo(panel)
    assert not panel._vsel_buttons.isVisibleTo(panel)
    assert panel._vsel_empty.isVisibleTo(panel)
    assert "Home \u2192 Sample Map \u2192 Variables" in panel._vsel_empty.text()
