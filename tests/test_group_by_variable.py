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


from tests._repo_scan import parse_source, python_sources
from review.domain import sample_variables as sv
from review.domain.plot_members import (
    plot_replicate_sets,
    variable_replicate_sets,
    wells_without_variable,
)

#: Both raw pooling functions. ``active_replicate_sets`` is the visible sets and
#: ``replicate_sets_loaded`` the same list including hidden ones — a distinction
#: that does not exist for a variable's values, which is precisely why the
#: second one was missed: the first sweep converted every
#: ``active_replicate_sets`` site and left four ``replicate_sets_loaded`` sites
#: pooling by the user's groups while their neighbours regrouped. The Heat Map
#: averaged the whole plate into every dose cell and labelled them all with the
#: first group's name. §7.5 #5: half-swept is worse than unswept, because one
#: figure then holds two definitions of a word and neither looks wrong.
_RAW_POOLERS = {"active_replicate_sets", "replicate_sets_loaded"}

#: The functions — not the files — allowed to call them. A file-level exemption
#: is what let ``plot_members.member_replicate_sets`` keep the old pooling while
#: living beside the substitution that replaced it.
_SEAM_FUNCTIONS = {
    "plot_replicate_sets", "plot_replicate_sets_loaded",
    # ``wells_the_grouping_adds`` exists to *compare* the two poolings — it
    # reports the wells the variable grouping draws that the user's own
    # selection does not hold — so naming the replaced one is its whole job,
    # and it pools nothing itself. It lives in ``plot_members`` beside the
    # seam for that reason.
    "wells_the_grouping_adds",
    # ``groups_the_variable_replaced`` is its sibling and the other half of the
    # comparison: it answers "which of the user's own groups did the
    # substitution displace", which is what lets a readout say "your 4 groups
    # are not pooling" and be right about both halves. It pools nothing itself,
    # and it lives here rather than in the controller that prints the sentence
    # for the reason §7.5 #10 gives — a controller naming the replaced pooling
    # would hold a second definition of "the user's groups".
    "groups_the_variable_replaced",
    # ``selections_model`` defines them; its own module is exempt entirely.
}
_SEAM_MODULE = "selections_model.py"


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
        if path.name == _SEAM_MODULE:
            continue
        tree = parse_source(path)
        aliases = set(_RAW_POOLERS)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in _RAW_POOLERS and alias.asname:
                        aliases.add(alias.asname)
        # Which function each call sits inside, so the exemption is per
        # function rather than per file.
        enclosing = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    enclosing.setdefault(id(inner), node.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in aliases:
                continue
            if enclosing.get(id(node)) in _SEAM_FUNCTIONS:
                continue
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


def test_asking_for_a_variable_that_does_not_exist_answers_nothing():
    ctx = _ctx(wells=["A01"], selections=[{"name": "a", "wells": ["A01"]}])
    assert variable_replicate_sets(ctx, "nope") == []
    assert wells_without_variable(ctx, "nope") == []


# ``member_variable_values`` / ``split_members_by_variable`` were removed with
# their tests: nothing in the application called them, and they were seams for
# row 32's Colour-by. The rule they expressed — a member takes a value only when
# every one of its wells has the same one — is pinned by
# ``test_sample_variables.py``'s roll-up tests and by
# ``test_response_collect.py``, which drives it per member through a real
# collector.


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
    from review.core.group_by_controller import (
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
    from review.core.group_by_controller import refresh_group_by_combo

    ctx, _combo, group, _ = _band_ctx(qtbot, wells=["A01"])
    refresh_group_by_combo(ctx)
    assert group.isHidden() is True


def test_choosing_a_variable_reaches_the_state_and_asks_for_a_redraw(qtbot):
    """Every tab pools through one seam, so every tab's picture changed —
    redrawing one here would leave the others on the previous grouping."""
    from review.core.group_by_controller import (
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
    from review.core.group_by_controller import refresh_group_by_combo

    ctx, combo, _group, _ = _band_ctx(qtbot, wells=["A01"], group_by="gone")
    refresh_group_by_combo(ctx)

    assert ctx.state.plot.group_by_variable == ""
    assert combo.currentIndex() == 0


def test_the_shortfall_names_the_wells_the_grouping_drops(qtbot):
    """A plate that silently loses eight wells looks exactly like a plate that
    had eighty-eight — the numbers move and nothing accounts for it."""
    from review.core.group_by_controller import group_by_shortfall

    ctx, _c, _g, _e = _band_ctx(
        qtbot, wells=["A01", "A02", "A03"], variables=[_dose("0")],
        assignments={"dose": {"A01": "d0"}}, group_by="dose")
    message = group_by_shortfall(ctx)

    assert "2 well(s) have no value" in message
    assert "A02" in message and "A03" in message


def test_a_fully_assigned_plate_reports_no_shortfall(qtbot):
    from review.core.group_by_controller import group_by_shortfall

    ctx, _c, _g, _e = _band_ctx(
        qtbot, wells=["A01"], variables=[_dose("0")],
        assignments={"dose": {"A01": "d0"}}, group_by="dose")
    assert group_by_shortfall(ctx) == ""


def test_choosing_a_variable_says_what_it_left_out(qtbot):
    """The sentence exists and names the shortfall — read from the function
    the figure title draws, not from the status line.

    It used to go to ``notify.set_status``, which writes the **persistent**
    status bar: a redraw that has nothing to report has no reason to write
    anything, so the sentence outlived its condition. Fill in the missing wells
    and the figure's title clears while the bar goes on naming them,
    contradicting the plot above it with no redraw able to correct it. That is
    the defect the bar tab's significance note was moved off the status line
    for, and G's review gate found the same one in the Response tab.

    The title is cleared by the same redraw that resolves the condition, and it
    travels with a saved figure, which the status line never did.
    """
    from review.core.group_by_controller import (
        group_by_shortfall, set_group_by_variable,
    )

    ctx, _c, _g, emitted = _band_ctx(
        qtbot, wells=["A01", "A02"], variables=[_dose("0")],
        assignments={"dose": {"A01": "d0"}})
    set_group_by_variable(ctx, "dose")

    shortfall = group_by_shortfall(ctx)
    assert "no value" in shortfall and "A02" in shortfall, shortfall

    statuses = [a[1] for a in emitted if a and a[0] == "status"]
    assert not any("no value" in s for s in statuses), (
        "the shortfall was pushed to the persistent status bar, where it "
        "outlives the condition that produced it"
    )


def test_the_real_plot_state_always_carries_the_setting():
    """The seam tolerates a context with no ``plot`` slice, because the headless
    export and CSV paths build one with only what they read. That tolerance
    must not be able to hide a *real* context that stopped honouring the
    setting, so the field's presence on the shipped state is pinned here.
    """
    from review.core.plot_state import PlotState

    assert hasattr(PlotState(), "group_by_variable"), (
        "PlotState lost group_by_variable, so plot_replicate_sets now reads "
        "the fallback on every real context and no plot ever groups by a "
        "variable — silently, because the seam tolerates the absence"
    )


def test_a_bare_number_value_keeps_its_label_on_the_axis(qtbot):
    """``replicate_display_label`` swaps an auto-generated-looking group name
    for its member wells — right for a group nobody named, wrong for a dose the
    user typed. ``Group by: Dose`` on values entered before a unit was drawing
    x ticks reading ``A01,A03``. §7.1: a domain predicate outside its domain.
    """
    from review.domain.selections_model import replicate_display_label

    ctx = _ctx(wells=["A01", "A03"],
               variables=[_dose("10")],
               assignments={"dose": {"A01": "d0", "A03": "d0"}},
               group_by="dose")
    ctx.state.dataset.well_labels = {}
    rset = plot_replicate_sets(ctx)[0]

    assert rset.name == "10 µM"
    assert replicate_display_label(ctx, rset) == "10 µM"

    # And with no unit typed yet, which is the reachable case.
    ctx.state.selection.variables = sv.VariableSet(
        variables=(sv.Variable(id="dose", name="Dose", kind=sv.NUMBER, unit="",
                               values=(sv.VariableValue("d0", "10", 10.0),)),),
        assignments={"dose": {"A01": "d0", "A03": "d0"}})
    bare = plot_replicate_sets(ctx)[0]
    assert replicate_display_label(ctx, bare) == "10", (
        "a dose of 10 was replaced by the tokens of the wells that received it"
    )


def test_a_users_own_group_still_gets_the_auto_name_substitution(qtbot):
    """The heuristic is not removed, only kept out of the domain it was not
    written for: a replicate set literally named "1" still reads as its wells."""
    from review.domain.batch_models import ReplicateSet
    from review.domain.selections_model import replicate_display_label

    ctx = _ctx(wells=["A01", "A03"])
    ctx.state.dataset.well_labels = {}
    assert replicate_display_label(
        ctx, ReplicateSet("1", ["A01", "A03"])) == "A01,A03"
