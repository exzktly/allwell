"""Grouping every plot by a sample variable, through one seam.

**The rule.** ``domain/plot_members.plot_replicate_sets(ctx)`` is the single
answer to "how does this view pool its wells". Normally it returns the user's
own replicate sets; when ``ctx.state.plot.group_by_variable`` names a variable,
it returns one set per value of it instead.

The reason it is one seam and not a per-tab setting is the failure it prevents.
If the Bar tab pooled by dose while Statistics still pooled by the user's
groups, the two would describe the same plate differently and **neither would
say so** — the same class of silent disagreement §7 is about, with the numbers
on screen rather than in a log.

So the guard below is not tidiness: a module that reaches past this seam to
``active_replicate_sets`` with a ctx in hand is a tab that will keep pooling the
old way after the user regroups.

Grouping this way deliberately introduces **no third member-key shape**. Pooling
the wells that share a dose is structurally what a replicate set already is, so
every collector's existing grouped branch is reused. A third shape would have
touched fifty-odd call sites and orphaned every saved per-sample control
reference.
"""

from __future__ import annotations

import ast
from types import SimpleNamespace

import pytest

from tests._repo_scan import parse_source, python_sources
from well_viewer.domain import sample_variables as sv
from well_viewer.domain.plot_members import (
    member_variable_values,
    plot_replicate_sets,
    split_members_by_variable,
    variable_replicate_sets,
    wells_without_variable,
)

#: The two modules allowed to call ``active_replicate_sets`` with a ctx in hand:
#: the one that defines the rule, and the one that implements the substitution.
_SEAM_OWNERS = {"selections_model.py", "plot_members.py"}


def _ctx(*, wells, variables=(), assignments=None, group_by="", selections=(),
         order=None):
    """The state ``plot_members`` reads, and nothing else.

    ``runtime.barplot.current_keys`` is the bar tab's drag order — an attribute
    the domain module looks up rather than imports — so a stub has to supply it
    or every bar-scope answer is empty for the wrong reason.
    """
    vset = sv.VariableSet(variables=tuple(variables),
                          assignments=dict(assignments or {}))
    names = [s.get("name") for s in selections] or sorted(wells)
    return SimpleNamespace(
        state=SimpleNamespace(
            dataset=SimpleNamespace(well_paths={w: f"/{w}.csv" for w in wells}),
            selection=SimpleNamespace(selections=list(selections),
                                      selected_wells=set(), variables=vset),
            plot=SimpleNamespace(group_by_variable=group_by,
                                 bar_pool_replicates=True,
                                 line_order_rsets=[], line_order_wells=[]),
        ),
        runtime=SimpleNamespace(barplot=SimpleNamespace(
            current_keys=lambda: list(order if order is not None else names))),
    )


def _dose(*labels):
    return sv.Variable(
        id="dose", name="Dose", kind=sv.NUMBER, unit="µM",
        values=tuple(sv.VariableValue(f"d{i}", lab, float(lab))
                     for i, lab in enumerate(labels)))


# ── the seam ────────────────────────────────────────────────────────────────

def test_no_tab_reaches_past_the_seam_for_its_grouping():
    """A caller that keeps ``active_replicate_sets`` keeps pooling the old way.

    Matched on the *call*, so an aliased import (`as _active_replicate_sets`,
    which one tab genuinely used) is caught too.
    """
    offenders = []
    for path in python_sources():
        if path.name in _SEAM_OWNERS:
            continue
        tree = parse_source(path)
        aliases = {"active_replicate_sets"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "active_replicate_sets" and alias.asname:
                        aliases.add(alias.asname)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in aliases:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "these pool wells without consulting group_by_variable, so they keep "
        "showing the user's groups after the user has regrouped by a "
        "variable:\n  " + "\n  ".join(offenders)
        + "\nGo through domain.plot_members.plot_replicate_sets(ctx)."
    )


def test_with_no_variable_chosen_the_seam_returns_the_users_own_groups():
    ctx = _ctx(wells=["A01", "A02"],
               selections=[{"name": "ctrl", "wells": ["A01", "A02"]}])
    assert [r.name for r in plot_replicate_sets(ctx)] == ["ctrl"]


def test_choosing_a_variable_replaces_the_groups_with_its_values():
    ctx = _ctx(wells=["A01", "A02", "A03", "A04"],
               variables=[_dose("0", "10")],
               assignments={"dose": {"A01": "d0", "A02": "d0",
                                     "A03": "d1", "A04": "d1"}},
               group_by="dose",
               selections=[{"name": "ctrl", "wells": ["A01", "A02"]}])
    sets = plot_replicate_sets(ctx)

    assert [r.name for r in sets] == ["0 µM", "10 µM"], (
        "the plot is still pooling by the user's groups"
    )
    assert [sorted(r.wells) for r in sets] == [["A01", "A02"], ["A03", "A04"]]


def test_the_sets_come_out_in_the_variables_own_value_order():
    """The user's order, which is what a legend and a dose axis read."""
    ctx = _ctx(wells=["A01", "A02"], variables=[_dose("10", "0")],
               assignments={"dose": {"A01": "d0", "A02": "d1"}},
               group_by="dose")
    assert [r.name for r in plot_replicate_sets(ctx)] == ["10 µM", "0 µM"]


def test_a_well_with_no_value_joins_no_set(tmp_path):
    """It is left out rather than pooled into a dose it was never given."""
    ctx = _ctx(wells=["A01", "A02", "A03"], variables=[_dose("0")],
               assignments={"dose": {"A01": "d0", "A02": "d0"}},
               group_by="dose")
    sets = plot_replicate_sets(ctx)

    assert [sorted(r.wells) for r in sets] == [["A01", "A02"]]
    assert wells_without_variable(ctx, "dose") == ["A03"], (
        "the wells left out are not reportable, so the shortfall reaches no one"
    )


def test_a_declared_value_no_well_holds_is_not_drawn_empty():
    """An empty bar at that dose reads as a measured zero."""
    ctx = _ctx(wells=["A01"], variables=[_dose("0", "10")],
               assignments={"dose": {"A01": "d0"}}, group_by="dose")
    assert [r.name for r in plot_replicate_sets(ctx)] == ["0 µM"]


def test_a_variable_that_groups_nothing_falls_back_to_the_users_groups():
    """A blank Bar tab three clicks from the Variables page is not an
    explanation. The Variables tab is where that shortfall is reported."""
    ctx = _ctx(wells=["A01", "A02"], variables=[_dose("0")],
               assignments={}, group_by="dose",
               selections=[{"name": "ctrl", "wells": ["A01", "A02"]}])
    assert [r.name for r in plot_replicate_sets(ctx)] == ["ctrl"]


def test_a_deleted_variable_still_selected_falls_back_rather_than_emptying():
    ctx = _ctx(wells=["A01"], variables=[], group_by="gone",
               selections=[{"name": "ctrl", "wells": ["A01"]}])
    assert [r.name for r in plot_replicate_sets(ctx)] == ["ctrl"]


# ── member values ───────────────────────────────────────────────────────────

def test_a_member_takes_a_value_only_when_all_its_wells_agree():
    ctx = _ctx(wells=["A01", "A02", "A03"], variables=[_dose("0", "10")],
               assignments={"dose": {"A01": "d0", "A02": "d1", "A03": "d0"}},
               selections=[{"name": "mixed", "wells": ["A01", "A02"]},
                           {"name": "clean", "wells": ["A03"]}])
    values = member_variable_values(ctx, "bar", "dose")
    assert values["clean"] == "d0"
    assert values["mixed"] is None, (
        "a member whose wells hold two doses was given one of them"
    )


def test_a_member_containing_an_unassigned_well_has_no_value():
    """The rule the whole feature is shaped around, at the plot layer."""
    ctx = _ctx(wells=["A01", "A02"], variables=[_dose("0")],
               assignments={"dose": {"A01": "d0"}},
               selections=[{"name": "ctrl", "wells": ["A01", "A02"]}])
    assert member_variable_values(ctx, "bar", "dose")["ctrl"] is None


def test_split_puts_the_undefined_members_in_their_own_list():
    """Two returns rather than a ``None`` key, because the caller must draw one
    and *say how many are in* the other."""
    ctx = _ctx(wells=["A01", "A02", "A03"], variables=[_dose("0", "10")],
               assignments={"dose": {"A01": "d0", "A02": "d1"}},
               selections=[{"name": "a", "wells": ["A01"]},
                           {"name": "b", "wells": ["A02"]},
                           {"name": "short", "wells": ["A03"]}])
    by_value, undefined = split_members_by_variable(ctx, "bar", "dose")

    assert by_value == {"d0": ["a"], "d1": ["b"]}
    assert undefined == ["short"]


def test_split_orders_the_values_the_way_the_user_listed_them():
    ctx = _ctx(wells=["A01", "A02"], variables=[_dose("10", "0")],
               assignments={"dose": {"A01": "d0", "A02": "d1"}},
               selections=[{"name": "a", "wells": ["A01"]},
                           {"name": "b", "wells": ["A02"]}])
    by_value, _ = split_members_by_variable(ctx, "bar", "dose")
    assert list(by_value) == ["d0", "d1"]


def test_asking_for_a_variable_that_does_not_exist_answers_nothing():
    ctx = _ctx(wells=["A01"], selections=[{"name": "a", "wells": ["A01"]}])
    assert member_variable_values(ctx, "bar", "nope") == {}
    assert variable_replicate_sets(ctx, "nope") == []
    by_value, undefined = split_members_by_variable(ctx, "bar", "nope")
    assert by_value == {} and undefined == ["a"]


@pytest.mark.parametrize("scope", ["bar", "line"])
def test_the_values_answer_in_whatever_key_space_the_plot_is_in(scope):
    """Well tokens per-well, set names grouped — the caller need not know."""
    ctx = _ctx(wells=["A01", "A02"], variables=[_dose("0")],
               assignments={"dose": {"A01": "d0", "A02": "d0"}},
               selections=[{"name": "ctrl", "wells": ["A01", "A02"]}])
    assert set(member_variable_values(ctx, scope, "dose")) == {"ctrl"}

    ctx.state.selection.selections = []
    ctx.state.selection.selected_wells = {"A01", "A02"}
    ctx.runtime.barplot.current_keys = lambda: ["A01", "A02"]
    assert set(member_variable_values(ctx, scope, "dose")) == {"A01", "A02"}


# ── the band control ────────────────────────────────────────────────────────
#
# §7.6 again: a control that is offered is not a control that works. These press
# the combo rather than checking it exists, and they assert on what reaches the
# state and the bus, not on what the handler computed.

class _Registry:
    def __init__(self, **widgets):
        self._d = dict(widgets)

    def get_or_none(self, name):
        return self._d.get(name)


def _band_ctx(qtbot, **kw):
    from PySide6.QtWidgets import QWidget

    from widgets.labeled_combo import LabeledComboBox

    ctx = _ctx(**kw)
    combo = LabeledComboBox("Group by")
    group = QWidget()
    qtbot.addWidget(combo)
    qtbot.addWidget(group)
    ctx.widgets = _Registry(_plotting_groupby_cb=combo, _plotting_groupby_grp=group)
    emitted = []
    ctx.bus = SimpleNamespace(plot_options_changed=SimpleNamespace(
        emit=lambda *a: emitted.append(a)))
    ctx.notify = SimpleNamespace(set_status=lambda text: emitted.append(("status", text)))
    return ctx, combo, group, emitted


def test_the_combo_lists_the_plates_variables(qtbot):
    from well_viewer.core.group_by_controller import (
        NO_VARIABLE_LABEL, refresh_group_by_combo,
    )

    ctx, combo, group, _ = _band_ctx(qtbot, wells=["A01"], variables=[_dose("0")])
    refresh_group_by_combo(ctx)

    assert [combo.itemText(i) for i in range(combo.count())] == [
        NO_VARIABLE_LABEL, "Dose"]
    assert group.isHidden() is False


def test_the_control_is_hidden_when_the_plate_declares_no_variables(qtbot):
    """The band's rule is that it only carries controls that do something, and
    a Group by offering nothing but the status quo is not one."""
    from well_viewer.core.group_by_controller import refresh_group_by_combo

    ctx, _combo, group, _ = _band_ctx(qtbot, wells=["A01"])
    refresh_group_by_combo(ctx)
    assert group.isHidden() is True


def test_choosing_a_variable_reaches_the_state_and_asks_for_a_redraw(qtbot):
    """Every tab pools through one seam, so every tab's picture changed —
    redrawing one here would leave the others on the previous grouping."""
    from well_viewer.core.group_by_controller import (
        refresh_group_by_combo, set_group_by_variable,
    )

    ctx, combo, _group, emitted = _band_ctx(
        qtbot, wells=["A01"], variables=[_dose("0")],
        assignments={"dose": {"A01": "d0"}})
    refresh_group_by_combo(ctx)
    set_group_by_variable(ctx, combo.itemData(1))

    assert ctx.state.plot.group_by_variable == "dose"
    assert emitted, "nothing asked for a redraw, so the plots keep the old grouping"


def test_a_deleted_variable_clears_the_state_as_well_as_the_combo(qtbot):
    """Otherwise the next redraw reads a variable the combo no longer offers,
    and the control and the plot describe different plots."""
    from well_viewer.core.group_by_controller import refresh_group_by_combo

    ctx, combo, _group, _ = _band_ctx(qtbot, wells=["A01"], group_by="gone")
    refresh_group_by_combo(ctx)

    assert ctx.state.plot.group_by_variable == ""
    assert combo.currentIndex() == 0


def test_the_shortfall_names_the_wells_the_grouping_drops(qtbot):
    """A plate that silently loses eight wells looks exactly like a plate that
    had eighty-eight — the numbers move and nothing accounts for it."""
    from well_viewer.core.group_by_controller import group_by_shortfall

    ctx, _c, _g, _e = _band_ctx(
        qtbot, wells=["A01", "A02", "A03"], variables=[_dose("0")],
        assignments={"dose": {"A01": "d0"}}, group_by="dose")
    message = group_by_shortfall(ctx)

    assert "2 well(s) have no value" in message
    assert "A02" in message and "A03" in message


def test_a_fully_assigned_plate_reports_no_shortfall(qtbot):
    from well_viewer.core.group_by_controller import group_by_shortfall

    ctx, _c, _g, _e = _band_ctx(
        qtbot, wells=["A01"], variables=[_dose("0")],
        assignments={"dose": {"A01": "d0"}}, group_by="dose")
    assert group_by_shortfall(ctx) == ""


def test_choosing_a_variable_says_what_it_left_out(qtbot):
    from well_viewer.core.group_by_controller import set_group_by_variable

    ctx, _c, _g, emitted = _band_ctx(
        qtbot, wells=["A01", "A02"], variables=[_dose("0")],
        assignments={"dose": {"A01": "d0"}})
    set_group_by_variable(ctx, "dose")

    statuses = [a[1] for a in emitted if a and a[0] == "status"]
    assert any("no value" in s for s in statuses), (
        "the dropped well reached no surface the user reads"
    )


def test_the_real_plot_state_always_carries_the_setting():
    """The seam tolerates a context with no ``plot`` slice, because the headless
    export and CSV paths build one with only what they read. That tolerance
    must not be able to hide a *real* context that stopped honouring the
    setting, so the field's presence on the shipped state is pinned here.
    """
    from well_viewer.core.plot_state import PlotState

    assert hasattr(PlotState(), "group_by_variable"), (
        "PlotState lost group_by_variable, so plot_replicate_sets now reads "
        "the fallback on every real context and no plot ever groups by a "
        "variable — silently, because the seam tolerates the absence"
    )
