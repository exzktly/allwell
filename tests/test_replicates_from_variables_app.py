"""*Make replicates from variables*, pressed in the real app.

Through the real builder and the real button, with the variables committed the
way the Variables tab commits them — so the test fails if the button is never
enabled when a variable is defined, if it is connected to nothing, or if the
groups it makes are not ordinary editable groups that the plots then draw.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def _define_line_and_dose(app):
    from review.domain import sample_variables as sv
    from review.features.home.variables.controller import _commit
    line = sv.Variable(
        id="line", name="Cell line", kind=sv.TEXT,
        values=(sv.VariableValue("hela", "HeLa", None),
                sv.VariableValue("u2os", "U2OS", None)))
    dose = sv.Variable(
        id="dose", name="Dose", kind=sv.NUMBER, unit="",
        values=(sv.VariableValue("d1", "1", 1.0), sv.VariableValue("d10", "10", 10.0)))
    # A01 and B01 are one condition; A02 is the same dose in another cell
    # line, so grouping by Dose alone would wrongly pool it with them.
    # B02 has no values at all.
    _commit(app._ctx, sv.VariableSet(variables=(line, dose), assignments={
        "line": {"A01": "hela", "B01": "hela", "A02": "u2os"},
        "dose": {"A01": "d1", "B01": "d1", "A02": "d1"}}))


def test_the_button_makes_wells_with_identical_values_replicates(loaded_app, qtbot, monkeypatch):
    from PySide6.QtCore import Qt
    from review.core.selection_controller import sel_add, sel_rename
    from review.domain.plot_members import plot_replicate_sets
    from review.features.home.groups import controller as groups

    app = loaded_app
    ctx = app._ctx
    sel_add(ctx, "Old", ["A01", "A02"], make_current=False)
    button = ctx.widgets.get("_rep_from_var_btn")
    assert not button.isEnabled(), "no variable yet: the button has nothing to make"

    _define_line_and_dose(app)
    assert button.isEnabled(), "defining a variable on the Variables tab must reach this button"

    asked = []
    monkeypatch.setattr(groups, "_confirm_replace",
                        lambda _ctx, n: asked.append(n) or True)
    shown = []
    monkeypatch.setattr(ctx.notify, "set_status", lambda text, *a, **k: shown.append(text))
    qtbot.mouseClick(button, Qt.LeftButton)

    assert asked == [1], "replacing the user's group must be confirmed first"
    sels = ctx.state.selection.selections
    assert [(s["name"], s["wells"]) for s in sels] == [
        ("HeLa · 1", ["A01", "B01"]), ("U2OS · 1", ["A02"])]
    assert shown[-1].startswith("Made 2 replicate groups from the variables.")
    assert "(B02)" in shown[-1]
    # Ordinary groups: the plots pool them, and they can be edited.
    assert [r.name for r in plot_replicate_sets(ctx)] == ["HeLa · 1", "U2OS · 1"]
    sel_rename(ctx, sels[0]["id"], "Control")
    assert ctx.state.selection.selections[0]["name"] == "Control"
