"""*Make groups from variable*, pressed in the real app.

Through the real builder, the real combo and the real button, with the
variables committed the way the Variables tab commits them — so the test fails
if the combo is never refreshed when a variable is defined, if the button is
connected to nothing, or if the groups it makes are not ordinary editable
groups that the plots then draw.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def _define_dose(app):
    from review.domain import sample_variables as sv
    from review.features.home.variables.controller import _commit
    dose = sv.Variable(
        id="dose", name="Dose", kind=sv.NUMBER, unit="",
        values=(sv.VariableValue("d1", "1", 1.0), sv.VariableValue("d10", "10", 10.0)))
    # B02 gets no dose. Values cut across rows, so a group per row (what
    # Quick Replicates would make) cannot pass for a group per dose.
    _commit(app._ctx, sv.VariableSet(variables=(dose,), assignments={
        "dose": {"A01": "d1", "B01": "d1", "A02": "d10"}}))


def test_the_button_turns_a_variable_s_values_into_editable_groups(loaded_app, qtbot, monkeypatch):
    from PySide6.QtCore import Qt
    from review.core.selection_controller import sel_add, sel_rename
    from review.domain.plot_members import plot_replicate_sets
    from review.features.home.groups import controller as groups

    app = loaded_app
    ctx = app._ctx
    sel_add(ctx, "Old", ["A01", "A02"], make_current=False)
    combo = ctx.widgets.get("_rep_from_var_combo")
    button = ctx.widgets.get("_rep_from_var_btn")
    assert not button.isEnabled(), "no variable yet: the button has nothing to make"

    _define_dose(app)
    assert [combo.itemText(i) for i in range(combo.count())] == ["Dose"], (
        "defining a variable on the Variables tab must reach this combo")
    assert button.isEnabled()

    asked = []
    monkeypatch.setattr(groups, "_confirm_replace",
                        lambda _ctx, n: asked.append(n) or True)
    shown = []
    monkeypatch.setattr(ctx.notify, "set_status", lambda text, *a, **k: shown.append(text))
    qtbot.mouseClick(button, Qt.LeftButton)

    assert asked == [1], "replacing the user's group must be confirmed first"
    sels = ctx.state.selection.selections
    assert [(s["name"], s["wells"]) for s in sels] == [
        ("1", ["A01", "B01"]), ("10", ["A02"])]
    assert shown[-1] == ("Made 2 groups from Dose. 1 well with no value is in "
                         "no group (B02).")
    # Ordinary groups: the plots pool them, and they can be edited.
    assert [r.name for r in plot_replicate_sets(ctx)] == ["1", "10"]
    sel_rename(ctx, sels[0]["id"], "Low dose")
    assert ctx.state.selection.selections[0]["name"] == "Low dose"
