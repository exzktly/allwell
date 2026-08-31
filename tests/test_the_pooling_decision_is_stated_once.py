"""What the plots are pooling by is stated where the choice has its effect.

``plot_replicate_sets`` already decides this for every tab at once, and the
band's combo already spells the *setting*. What was missing is the
*consequence*: a user who has painted four groups and then picks
``Group by: Dose`` has had those four stop pooling anything — in the Bar tab, in
Statistics, in Batch Export — and the only thing that mentioned it was a
shortfall sentence about the wells the grouping left out.

Two properties, and the second is the one that rots. The sentence has to be
**right**, which means asserting the exact string rather than that a sentence
exists — "a readout was produced" is satisfied by one naming the wrong pooling.
And it has to be **derived from the seam**, because a second computation of
"what are we grouping by" is a second answer, whose failure mode is a sentence
confidently describing a figure that is not on screen.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QLabel

from well_viewer.core.group_by_controller import (
    GROUPING_READOUTS, grouping_summary, refresh_grouping_readouts,
)
from well_viewer.domain import sample_variables as sv


class _Registry:
    def __init__(self):
        self._d = {}

    def register(self, name, widget):
        self._d[name] = widget
        return widget

    def get_or_none(self, name):
        return self._d.get(name)


def _dose(*labels):
    return sv.Variable(
        id="dose", name="Dose", kind=sv.NUMBER, unit="µM",
        values=tuple(sv.VariableValue(f"d{i}", lab, float(lab))
                     for i, lab in enumerate(labels)))


def _group(name, wells, hidden=False):
    return {"id": name, "name": name, "wells": list(wells), "hidden": hidden,
            "color": "#5B9BF8", "replicates": None, "source": "user"}


def _ctx(*, wells=("A01", "A02", "A03", "A04"), variables=(), assignments=None,
         group_by="", selections=(), selected=()):
    vset = sv.VariableSet(variables=tuple(variables),
                          assignments=dict(assignments or {}))
    return SimpleNamespace(
        widgets=_Registry(),
        state=SimpleNamespace(
            dataset=SimpleNamespace(well_paths={w: f"/{w}.csv" for w in wells}),
            selection=SimpleNamespace(selections=[dict(s) for s in selections],
                                      selected_wells=set(selected),
                                      variables=vset),
            plot=SimpleNamespace(group_by_variable=group_by,
                                 bar_pool_replicates=True,
                                 line_order_rsets=[], line_order_wells=[]),
        ),
        runtime=SimpleNamespace(barplot=SimpleNamespace(current_keys=list)),
    )


# ── the sentence says what was actually decided ─────────────────────────────

def test_with_no_variable_it_names_the_users_own_groups():
    ctx = _ctx(selections=[_group("Ctrl", ["A01", "A02"]),
                           _group("Drug", ["A03", "A04"])])
    assert grouping_summary(ctx) == "Plots are grouped by your 2 groups."


def test_with_neither_it_says_the_plots_are_per_well():
    assert grouping_summary(_ctx()) == "Plots draw one member per selected well."


def test_a_variable_grouping_says_the_groups_have_stopped_pooling():
    """The whole point. Not "grouped by Dose" — *and your groups are not*."""
    ctx = _ctx(variables=[_dose("0", "10")],
               assignments={"dose": {"A01": "d0", "A02": "d0",
                                     "A03": "d1", "A04": "d1"}},
               group_by="dose",
               selections=[_group("Ctrl", ["A01", "A02"]),
                           _group("Drug", ["A03", "A04"])])
    assert grouping_summary(ctx) == (
        "Plots are grouped by Dose (2 values); your 2 groups are not pooling.")


def test_a_variable_grouping_with_no_groups_to_displace_says_only_what_it_is():
    ctx = _ctx(variables=[_dose("0", "10")],
               assignments={"dose": {"A01": "d0", "A03": "d1"}},
               group_by="dose")
    assert grouping_summary(ctx) == "Plots are grouped by Dose (2 values)."


def test_a_variable_that_groups_nothing_reports_the_fallback_not_the_setting():
    """The seam hands the question back to the user's groups rather than
    emptying every plot. A sentence reporting the *setting* here would describe
    bars that are not drawn — which is the failure the shortfall clause beside
    it already documents."""
    ctx = _ctx(variables=[_dose("0", "10")], assignments={},
               group_by="dose",
               selections=[_group("Ctrl", ["A01", "A02"])])
    assert grouping_summary(ctx) == (
        "Dose groups no wells, so plots are grouped by your 1 group.")


def test_a_hidden_group_is_not_counted():
    """It is not drawn, so it is not what the plots are grouped by."""
    ctx = _ctx(selections=[_group("Ctrl", ["A01", "A02"]),
                           _group("Old", ["A03"], hidden=True)])
    assert grouping_summary(ctx) == "Plots are grouped by your 1 group."


def test_a_hidden_group_is_not_counted_as_displaced_either():
    """The other branch, and the one the first hidden-group case cannot reach.

    With no variable chosen the count comes from the seam's own answer, which
    already excludes hidden sets — so that case passes whatever
    ``groups_the_variable_replaced`` does. Only a variable *in force* runs the
    displaced count, and a hidden group is not drawn, so nothing displaced it.
    """
    ctx = _ctx(variables=[_dose("0", "10")],
               assignments={"dose": {"A01": "d0", "A02": "d0",
                                     "A03": "d1", "A04": "d1"}},
               group_by="dose",
               selections=[_group("Ctrl", ["A01", "A02"]),
                           _group("Drug", ["A03"]),
                           _group("Old", ["A04"], hidden=True)])
    assert grouping_summary(ctx) == (
        "Plots are grouped by Dose (2 values); your 2 groups are not pooling.")


def test_with_no_dataset_it_says_nothing():
    """A page with no plate open has no pooling to report, and a sentence about
    a plate that is not there is worse than silence."""
    assert grouping_summary(_ctx(wells=())) == ""


# ── it is derived from the seam, not recomputed ─────────────────────────────

def test_the_summary_follows_the_seam_rather_than_the_setting():
    """Mutating what the seam returns must move the sentence.

    The property that cannot be got from reading the code: a summary built from
    ``state.plot.group_by_variable`` alone would agree with the combo and
    disagree with the figure in exactly the fallback case above.
    """
    import well_viewer.core.group_by_controller as gbc
    from well_viewer.domain.batch_models import ReplicateSet

    ctx = _ctx(variables=[_dose("0", "10")],
               assignments={"dose": {"A01": "d0", "A03": "d1"}},
               group_by="dose")
    seen = {}

    def fake_pool(inner_ctx):
        seen["called"] = True
        return [ReplicateSet("only", ["A01"])]

    import well_viewer.domain.plot_members as pm
    original = pm.plot_replicate_sets
    pm.plot_replicate_sets = fake_pool
    try:
        assert gbc.grouping_summary(ctx) == "Plots are grouped by Dose (1 value)."
    finally:
        pm.plot_replicate_sets = original
    assert seen.get("called"), "the summary never asked the seam"


# ── both surfaces say it, and neither says it when there is nothing to say ──

def test_both_readouts_are_painted_from_the_one_function(qtbot):
    """Two labels, one sentence. Painted at the widget, not returned — a
    readout the user never sees is the defect this row exists to fix, one
    layer up."""
    ctx = _ctx(selections=[_group("Ctrl", ["A01", "A02"])])
    labels = {}
    for key in GROUPING_READOUTS:
        label = QLabel("")
        qtbot.addWidget(label)
        labels[key] = ctx.widgets.register(key, label)

    refresh_grouping_readouts(ctx)
    expected = "Plots are grouped by your 1 group."
    for key, label in labels.items():
        assert label.text() == expected, key
        assert not label.isHidden(), key


def test_the_readouts_hide_themselves_when_there_is_nothing_to_report(qtbot):
    ctx = _ctx(wells=())
    labels = []
    for key in GROUPING_READOUTS:
        label = QLabel("stale")
        qtbot.addWidget(label)
        label.show()
        labels.append(ctx.widgets.register(key, label))

    refresh_grouping_readouts(ctx)
    for label in labels:
        assert label.text() == ""
        assert label.isHidden()


def test_the_roster_names_both_surfaces():
    """Two hand-written repaints are the pair that grows a difference; the
    roster is what stops the band and Home describing different figures."""
    assert set(GROUPING_READOUTS) == {
        "_plotting_grouping_note", "_sample_map_grouping_note"}


@pytest.mark.parametrize("key", GROUPING_READOUTS)
def test_each_surface_actually_builds_its_label(key, qtbot):
    """The roster naming a widget nobody builds is a readout that never appears.

    Built through each host's real constructor, per §7.8 #3.
    """
    from types import SimpleNamespace as NS

    from PySide6.QtWidgets import QWidget

    if key == "_sample_map_grouping_note":
        from tests.test_one_plate_on_the_sample_map import _ctx as sample_ctx
        from well_viewer.features.home.sample_map.tab_view import (
            build_plate_map_subtab,
        )
        host_ctx = sample_ctx()
        frame = QWidget()
        qtbot.addWidget(frame)
        build_plate_map_subtab(host_ctx, frame)
        assert host_ctx.widgets.get_or_none(key) is not None
        return

    # The band's label is built by ``centre_view``; assert the construction site
    # names it rather than building the whole shell for one widget.
    import ast

    from tests._repo_scan import REPO_ROOT, parse_source

    tree = parse_source(REPO_ROOT / "well_viewer" / "core" / "centre_view.py")
    registered = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert key in registered, f"centre_view never registers {key}"
    _ = NS
