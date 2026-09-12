"""The workstream-H tests, driven through the tab a user actually presses.

``domain/stats_tests`` and ``features/stats/_report`` are covered from lists in
the fast lane. This file covers the half neither of those can see: that
*selecting* a test in the real combo reaches the branch that runs it, that the
pickers it needs appear, and that the report the panel receives is the one the
test produced.

That gap is not hypothetical here. §7.6 in ``CLAUDE.md`` records a per-sample
control editor that shipped unopenable past ~4,950 tests because every test of
it entered one step past the closure that was broken, and §7.8 records a whole
tab that raised on every real construction while ~130 of its own tests stayed
green. A statistical test offered in a combo and wired to nothing would report
"not run" for ever, which looks exactly like a plate that could not support it.
"""

from __future__ import annotations

import pytest

from review.domain import sample_variables as sv
from review.domain.batch_models import BarGroup
from review.features.stats import controller as sc

pytestmark = pytest.mark.slow


def _open_stats(app):
    assert app._notebook.setCurrentByName("Statistics")
    assert app._ctx.widgets.get_or_none("_stats_test_cb") is not None, \
        "the Statistics tab did not build"
    return app._ctx


def _two_groups(app):
    app._app_state.stats.groups = [
        BarGroup("Ctrl", solo_wells=["A01", "A02"]),
        BarGroup("Drug", solo_wells=["B01", "B02"]),
    ]


def _declare(app, name, kind, pairs, assignments):
    """Put a real sample variable on the plate, through the domain module."""
    vset = sv.from_block({
        "schema_version": 1,
        "variables": [{
            "id": name.lower(), "name": name, "kind": kind,
            "values": [{"id": vid, "label": lbl} for vid, lbl in pairs],
        }],
        "assignments": {name.lower(): assignments},
    })
    existing = app._app_state.selection.variables
    merged = sv.VariableSet(
        variables=tuple(existing.variables) + tuple(vset.variables),
        assignments={**existing.assignments, **vset.assignments},
    )
    app._app_state.selection.variables = merged
    return merged


def _run(app, test_label):
    combo = app._ctx.widgets.get_or_none("_stats_test_cb")
    combo.setCurrentText(test_label)
    assert combo.currentText() == test_label, f"{test_label!r} is not offered"
    app._ctx.runtime.stats.run()
    panel = app._ctx.widgets.get_or_none("_stats_result_text")
    assert panel is not None, "the result panel is not registered"
    return panel.toPlainText()


def test_every_test_the_roster_declares_is_offered_by_the_combo(loaded_app):
    """The combo is filled from ``controller.TESTS`` and nothing else. A second
    hand-written list in the view is how a combo comes to offer a test the run
    branch has never heard of."""
    _open_stats(loaded_app)
    combo = loaded_app._ctx.widgets.get_or_none("_stats_test_cb")

    offered = [combo.itemText(i) for i in range(combo.count())]

    assert offered == list(sc.TEST_LABELS)


def test_choosing_two_way_anova_reveals_its_two_factor_pickers(loaded_app):
    """A control that exists is not a control that works: the pickers are
    hidden for the tests that do not need them, so selecting the test has to
    make them appear or the test can only ever decline."""
    ctx = _open_stats(loaded_app)
    combo = ctx.widgets.get_or_none("_stats_test_cb")
    var_a = ctx.widgets.get_or_none("_stats_var_a_cb")
    var_b = ctx.widgets.get_or_none("_stats_var_b_cb")

    combo.setCurrentText("t-test (Welch, unequal var)")
    assert not var_a.isVisibleTo(var_a.parentWidget())
    assert not var_b.isVisibleTo(var_b.parentWidget())

    combo.setCurrentText("Two-way ANOVA")

    assert var_a.isVisibleTo(var_a.parentWidget())
    assert var_b.isVisibleTo(var_b.parentWidget())


def test_choosing_dunnett_reveals_a_control_picker_of_the_tabs_own_groups(loaded_app):
    """The control must be one of the groups being compared — a Dunnett control
    that is not in the comparison is not a control the test can use."""
    app = loaded_app
    ctx = _open_stats(app)
    _two_groups(app)
    combo = ctx.widgets.get_or_none("_stats_test_cb")

    combo.setCurrentText("Dunnett (vs control group)")
    control = ctx.widgets.get_or_none("_stats_control_cb")

    assert control.isVisibleTo(control.parentWidget())
    offered = [control.itemText(i) for i in range(control.count())]
    assert offered == ["Ctrl", "Drug"]


def test_the_paired_picker_says_pair_by_and_the_anova_one_says_factor(loaded_app):
    """One widget serving two tests has to say which it is serving. Left
    reading `Pair by` under Two-way ANOVA it would name the wrong thing."""
    ctx = _open_stats(loaded_app)
    combo = ctx.widgets.get_or_none("_stats_test_cb")
    var_a = ctx.widgets.get_or_none("_stats_var_a_cb")

    combo.setCurrentText("Paired t-test")
    assert "Pair by" in var_a.label()

    combo.setCurrentText("Two-way ANOVA")
    assert "Factor" in var_a.label()


# ── the runs themselves ─────────────────────────────────────────────────────

def test_the_omnibus_reaches_the_panel_on_an_ordinary_run(loaded_app_multifov):
    """H2 is not behind a combo entry — it is printed above every pairwise run
    with three or more groups, which is exactly when it is worth having.

    On the **multi-FOV** dataset with FOV mode on, so each single-well group has
    three replicates. The plain mini dataset has four wells and one FOV each, so
    three groups cannot each have two replicates by any route and the run is
    declined before it ever reaches the omnibus — a fixture that could not tell
    a working omnibus from a missing one.
    """
    app = loaded_app_multifov
    _open_stats(app)
    app._app_state.plot.use_fov_replicates = True
    app._app_state.stats.groups = [
        BarGroup("A", solo_wells=["A01"]),
        BarGroup("B", solo_wells=["A02"]),
        BarGroup("C", solo_wells=["B01"]),
    ]

    text = _run(app, "t-test (Welch, unequal var)")

    assert "Omnibus:" in text
    assert "One-way ANOVA" in text


def test_the_effect_size_reaches_the_panel_on_an_ordinary_run(loaded_app):
    """H1 likewise. With three replicate wells a p-value says very little, and
    a reader given only the p will quote only the p."""
    app = loaded_app
    _open_stats(app)
    _two_groups(app)

    text = _run(app, "t-test (Welch, unequal var)")

    # The **value**, not the label. Asserting `"g = " in text` passes against
    # `effect_size(a, a)` — every g zero, every interval [0, 0] — which is a
    # mutation that silently reports no effect anywhere.
    import re
    match = re.search(r"g = (-?[\d.]+(?:e-?\d+)?)", text)
    assert match, text
    assert abs(float(match.group(1))) > 0.0, "every effect size came back zero"
    assert "95% CI" in text
    interval = re.search(r"95% CI (-?[\d.]+(?:e-?\d+)?), (-?[\d.]+)", text)
    assert interval, text
    assert float(interval.group(1)) < float(interval.group(2)), \
        "the interval has no width"


def test_dunnett_runs_against_the_chosen_control_and_names_it(loaded_app):
    app = loaded_app
    ctx = _open_stats(app)
    _two_groups(app)
    ctx.widgets.get_or_none("_stats_test_cb").setCurrentText(
        "Dunnett (vs control group)")
    control = ctx.widgets.get_or_none("_stats_control_cb")
    control.setCurrentIndex(control.findData("Ctrl"))

    text = _run(app, "Dunnett (vs control group)")

    assert "Dunnett" in text
    assert "Drug  vs  Ctrl" in text


def test_a_paired_test_without_a_variable_declines_in_words(loaded_app):
    """It must not pair by list order, and it must say that is why. §7's rule:
    a degraded answer must not be indistinguishable from a good one."""
    app = loaded_app
    _open_stats(app)
    _two_groups(app)

    text = _run(app, "Paired t-test")

    assert "not run" in text
    assert "Home → Variables" in text
    assert "list order" in text


def test_a_paired_test_pairs_on_the_variable_the_user_declared(loaded_app_multifov):
    """The end-to-end shape of H4: a batch axis on the plate becomes the
    correspondence the data model otherwise cannot express.

    **Asserts what the resolution produced, not the header.** The first version
    checked only ``"Paired by Batch"``, ``"never on list order"`` and
    ``"Ctrl  vs  Drug"`` — all three of which ``paired_lines`` emits *before* it
    knows whether anything computed — so blanking the whole of
    ``collect_group_values_by_variable`` left it green and the sample-resolution
    layer H4 is made of was covered by nothing at all.

    The **pair count** is what a header cannot fake. This fixture's two batches
    differ by a perfectly constant amount — the mini datasets are synthetic and
    linear, so every within-batch difference is identical and the test correctly
    declines for having no variation between pairs — but it declines *having
    paired two replicates*, and a blanked resolution declines at zero with a
    different reason. `tests/test_stats_tests.py` covers the arithmetic on data
    that can vary; this covers the wiring that feeds it.
    """
    app = loaded_app_multifov
    ctx = _open_stats(app)
    app._app_state.plot.use_fov_replicates = True
    _two_groups(app)
    _declare(app, "Batch", "text", [("r1", "day1"), ("r2", "day2")],
             {"A01": "r1", "A02": "r2", "B01": "r1", "B02": "r2"})
    ctx.widgets.get_or_none("_stats_test_cb").setCurrentText("Paired t-test")
    var_a = ctx.widgets.get_or_none("_stats_var_a_cb")
    var_a.setCurrentIndex(var_a.findData("batch"))

    text = _run(app, "Paired t-test")

    assert "Paired by Batch" in text
    assert "Ctrl  vs  Drug" in text
    # The part a header cannot fake: the two replicates it actually paired.
    assert "n = 2 pairs" in text, text
    assert "same amount" in text, text
    assert "too few values of the pairing variable" not in text


def test_blanking_the_pairing_resolution_is_visible_in_the_panel(loaded_app_multifov):
    """The mutation the previous version of the test above could not see.

    With no value for the pairing variable on any well, nothing can pair — and
    the panel has to say so rather than printing a header and stopping.
    """
    app = loaded_app_multifov
    ctx = _open_stats(app)
    app._app_state.plot.use_fov_replicates = True
    _two_groups(app)
    _declare(app, "Batch", "text", [("r1", "day1"), ("r2", "day2")], {})
    ctx.widgets.get_or_none("_stats_test_cb").setCurrentText("Paired t-test")
    var_a = ctx.widgets.get_or_none("_stats_var_a_cb")
    var_a.setCurrentIndex(var_a.findData("batch"))

    text = _run(app, "Paired t-test")

    assert "Not computed (n = 0 pairs)" in text, text
    assert "too few values of the pairing variable" in text


def test_two_way_anova_runs_over_two_declared_variables(loaded_app_multifov):
    """On the **multi-FOV** dataset with FOV mode on.

    The plain mini dataset has four wells, and a 2x2 design over four wells puts
    exactly one replicate in each cell — which the ANOVA correctly declines for
    having no residual degrees of freedom. A fixture that cannot support the
    test cannot tell a working implementation from a broken one; three FOVs a
    well gives each cell three replicates and eight residual df.
    """
    app = loaded_app_multifov
    ctx = _open_stats(app)
    app._app_state.plot.use_fov_replicates = True
    _declare(app, "Dose", "number", [("d0", "0"), ("d1", "10")],
             {"A01": "d0", "A02": "d1", "B01": "d0", "B02": "d1"})
    _declare(app, "Line", "text", [("l0", "HeLa"), ("l1", "U2OS")],
             {"A01": "l0", "A02": "l0", "B01": "l1", "B02": "l1"})
    # Two groups so the run's own "at least 2 groups" gate is satisfied; the
    # ANOVA itself reads the wells, not the groups.
    app._app_state.stats.groups = [
        BarGroup("g1", solo_wells=["A01", "A02"]),
        BarGroup("g2", solo_wells=["B01", "B02"]),
    ]
    ctx.widgets.get_or_none("_stats_test_cb").setCurrentText("Two-way ANOVA")
    var_a = ctx.widgets.get_or_none("_stats_var_a_cb")
    var_b = ctx.widgets.get_or_none("_stats_var_b_cb")
    var_a.setCurrentIndex(var_a.findData("dose"))
    var_b.setCurrentIndex(var_b.findData("line"))

    text = _run(app, "Two-way ANOVA")

    assert "Type II sums of squares" in text
    assert "Dose x Line" in text
    assert "Residual" in text
    assert "not run" not in text


def test_two_way_with_one_variable_chosen_twice_is_declined(loaded_app):
    app = loaded_app
    ctx = _open_stats(app)
    _two_groups(app)
    _declare(app, "Dose", "number", [("d0", "0"), ("d1", "10")],
             {"A01": "d0", "A02": "d1", "B01": "d0", "B02": "d1"})
    ctx.widgets.get_or_none("_stats_test_cb").setCurrentText("Two-way ANOVA")
    for name in ("_stats_var_a_cb", "_stats_var_b_cb"):
        combo = ctx.widgets.get_or_none(name)
        combo.setCurrentIndex(combo.findData("dose"))

    text = _run(app, "Two-way ANOVA")

    assert "not run" in text
    assert "two different variables" in text


def test_an_alternative_test_stores_no_pairwise_records_of_its_own(loaded_app):
    """The Bar plot's brackets are all-pairs records, and a Dunnett run makes
    none — so it must neither add one nor disturb what a real pairwise run
    stored. ``StatsState.runs`` keeps earlier runs on purpose: a comparison
    under GFP/t=24/mean stays true however the tab's selectors move, which is
    why ``invalidate_stats_run`` is a documented no-op. This pins the property
    that is actually available — every stored record still names the test that
    produced it, so no bracket can be attributed to the Dunnett run.
    """
    app = loaded_app
    ctx = _open_stats(app)
    _two_groups(app)
    _run(app, "t-test (Welch, unequal var)")
    before = {k: list(v) for k, v in app._app_state.stats.runs.items()}
    assert before, "the pairwise run stored nothing to compare against"

    control = ctx.widgets.get_or_none("_stats_control_cb")
    ctx.widgets.get_or_none("_stats_test_cb").setCurrentText(
        "Dunnett (vs control group)")
    control.setCurrentIndex(control.findData("Ctrl"))
    _run(app, "Dunnett (vs control group)")

    after = app._app_state.stats.runs
    assert {k: list(v) for k, v in after.items()} == before
    named = {r.get("test") for records in after.values() for r in records}
    assert "Dunnett (vs control group)" not in named


def test_a_built_tab_really_has_the_slices_the_pickers_tolerate_missing(loaded_app):
    """``refresh_stats_test_inputs`` runs while the panel is being built, and
    several callers construct a context holding only the slices they read — so
    it tolerates a missing ``stats`` or ``selection`` slice as "nothing to offer
    yet".

    That tolerance must not be able to hide a *built tab* whose slices have
    quietly gone away, which is the shape ``plot_members`` guards the same way.

    Pinned against a real loaded app rather than a bare ``AppState``: the stats
    slice is a **feature** slice and does not exist on a fresh state at all, so
    the tolerance is covering a case that genuinely occurs rather than a
    hypothetical one. Asserting it on `AppState()` was this test's first
    version, and it failed against code that is correct.
    """
    app = loaded_app
    _open_stats(app)
    state = app._app_state

    assert getattr(state.stats, "groups", None) is not None
    assert getattr(state.selection, "variables", None) is not None
    # And the pickers really did fill from them rather than from a stub.
    assert app._ctx.widgets.get_or_none("_stats_var_a_cb") is not None


def test_the_control_picker_follows_groups_defined_after_the_test_was_chosen(
        loaded_app):
    """The order a user actually works in.

    Choosing Dunnett first and defining the groups after left the combo holding
    the groups as they were at that moment — empty — so "pick a control group"
    named an action there was no way to perform. Every other test in this file
    sets the groups *before* selecting the test, which is the one ordering where
    a stale picker and a live one look identical.
    """
    app = loaded_app
    ctx = _open_stats(app)
    app._app_state.stats.groups = []
    ctx.widgets.get_or_none("_stats_test_cb").setCurrentText(
        "Dunnett (vs control group)")
    control = ctx.widgets.get_or_none("_stats_control_cb")
    assert control.count() == 0

    _two_groups(app)
    from review.features.stats.controller import stats_refresh_group_list
    stats_refresh_group_list(ctx)

    assert [control.itemText(i) for i in range(control.count())] == ["Ctrl", "Drug"]


def test_a_variable_declared_after_arriving_reaches_the_factor_pickers(loaded_app):
    """The variables live on a different page entirely, so the list can change
    without this tab hearing about it. Arriving is when that is fixed."""
    app = loaded_app
    ctx = _open_stats(app)
    ctx.widgets.get_or_none("_stats_test_cb").setCurrentText("Two-way ANOVA")
    var_a = ctx.widgets.get_or_none("_stats_var_a_cb")
    assert var_a.count() == 0

    _declare(app, "Dose", "number", [("d0", "0"), ("d1", "10")],
             {"A01": "d0", "A02": "d1"})
    from review.features.stats.controller import refresh_stats_test_inputs
    refresh_stats_test_inputs(ctx)

    assert [var_a.itemText(i) for i in range(var_a.count())] == ["Dose"]
