"""A plate annotated only on the Variables tab says so on its figures.

**The defect.** Three tabs answer "what is this well" — Replicates, Variables
and Well Labels — and only one of them was ever read by a tick label. A user
who filled a dose grid across the plate got ``A01 A02 A03`` on every x axis and
in every legend, and was then told on Home that there were "no sample labels
yet", over figures whose x axis was the very thing they had spent the morning
filling in. The annotation was read for pooling, for colour and for splitting,
and never once for the only part of it the user could see.

**The fix, and where it is.** ``selections_model.well_display_label_and_source``
is the one place a well's figure name is decided, and it now answers in three
rungs: a label typed on Well Labels, then the values the well holds on the
Variables tab, then the token. Every tick label, legend entry, image caption
and exported ``well_name`` column already went through that function or through
its map form ``figure_names_map``, so the whole app moved together.

Every assertion here is on the **string a figure draws**, not on a property of
it (§7.8 #1): "the label is not the token" is satisfied by a label composed
from the wrong variable, in the wrong order, or without its unit.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from well_viewer.domain import sample_variables as sv
from well_viewer.domain.selections_model import (
    LABEL_FROM_CUSTOM,
    LABEL_FROM_TOKEN,
    LABEL_FROM_VARIABLES,
    figure_names_map,
    well_display_label,
    well_display_label_and_source,
)

WELLS = ("A01", "A02", "A03", "B01", "B02", "B03")


def _plate(*, unit: str = "µM", labels=None, wells=WELLS, dose_only=False):
    """Two variables over six wells: a cell line per row, a dose per column.

    Deliberately **not** one variable per well in well order: with a single
    axis the composed name and the well token would rise and fall together, so
    a fixture like that cannot tell a name composed from the right variable
    from one composed from the wrong one (§7.8 #2).
    """
    dose = sv.Variable(id="v_dose", name="Dose", kind=sv.NUMBER, unit=unit, values=(
        sv.VariableValue("d0", "0", 0.0),
        sv.VariableValue("d1", "1", 1.0),
        sv.VariableValue("d10", "10", 10.0),
    ))
    line = sv.Variable(id="v_line", name="Cell line", values=(
        sv.VariableValue("c_hela", "HeLa"),
        sv.VariableValue("c_u2os", "U2OS"),
    ))
    by_column = {"01": "d0", "02": "d1", "03": "d10"}
    assignments = {"v_dose": {w: by_column[w[1:]] for w in WELLS}}
    variables = (dose,)
    if not dose_only:
        variables = (line, dose)
        assignments["v_line"] = {w: ("c_hela" if w[0] == "A" else "c_u2os")
                                 for w in WELLS}
    return SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(well_labels=dict(labels or {}),
                                well_paths=dict.fromkeys(wells, "x")),
        selection=SimpleNamespace(variables=sv.VariableSet(
            variables=variables, assignments=assignments)),
    ))


# ── the domain rule ─────────────────────────────────────────────────────────

def test_a_well_is_named_by_the_values_it_holds():
    """The exact string, in the variables' own order, units included."""
    ctx = _plate()
    vset = ctx.state.selection.variables
    assert sv.label_for_well(vset, "A03") == "HeLa · 10 µM"
    assert sv.label_for_well(vset, "B01") == "U2OS · 0 µM"


def test_a_partly_filled_well_carries_the_well_it_came_from():
    """``HeLa`` beside ``HeLa · 10 µM`` reads as a well that was given no dose.

    The wells suffix is what tells "this well has no dose" from "this well's
    dose is nothing", which is the distinction the whole module is built on.
    """
    ctx = _plate()
    vset = sv.assign(ctx.state.selection.variables, "v_dose", ["A01"], None)
    assert sv.label_for_well(vset, "A01") == "HeLa (A01)"


def test_a_well_no_variable_reaches_is_named_by_nothing():
    """``""``, not the token: the caller decides what to fall back to, and a
    function that returned ``A01`` here could not be told apart from one that
    had resolved a variable whose value happened to read ``A01``."""
    assert sv.label_for_well(sv.VariableSet(), "A01") == ""


# ── the figure name, and which rung answered ────────────────────────────────

def test_a_tick_label_is_the_wells_variables_not_its_token():
    """The reported defect. Reverting the variables rung puts ``A03`` back."""
    ctx = _plate()
    assert well_display_label(ctx, "A03") == "HeLa · 10 µM"
    assert well_display_label_and_source(ctx, "A03") == (
        "HeLa · 10 µM", LABEL_FROM_VARIABLES)


def test_a_typed_label_still_wins_over_the_variables():
    """Well Labels is the explicit override — the one of the three the user
    wrote for this well alone — so a plate carrying both draws the typed one."""
    ctx = _plate(labels={"A03": "shCtrl"})
    assert well_display_label_and_source(ctx, "A03") == ("shCtrl", LABEL_FROM_CUSTOM)
    # …and only for the well it was typed on.
    assert well_display_label(ctx, "B03") == "U2OS · 10 µM"


def test_a_blank_typed_label_falls_through_rather_than_blanking_the_tick():
    ctx = _plate(labels={"A03": "   "})
    assert well_display_label(ctx, "A03") == "HeLa · 10 µM"


def test_a_plate_with_no_variables_keeps_its_tokens():
    ctx = _plate()
    ctx.state.selection.variables = sv.VariableSet()
    assert well_display_label_and_source(ctx, "A03") == ("A03", LABEL_FROM_TOKEN)


def test_a_prefixed_measurement_label_resolves_through_its_well_token():
    """``extract_well_token`` is the first thing the seam does, so a label the
    loader built from a file name is named like the well it holds. A ``_tp``
    key is **not** one of these — nothing in ``A03_tp24`` ends in a well token
    — and its callers strip the suffix before they ask."""
    ctx = _plate()
    assert well_display_label(ctx, "measurements_a3") == "HeLa · 10 µM"


# ── the caller, not the seam (§7.7 #1) ──────────────────────────────────────

def test_the_bar_tabs_own_member_builder_draws_the_names():
    """``batch_members`` is what puts text under a bar. The seam being right
    is not the claim — the claim is that the bars are labelled."""
    from well_viewer.domain.batch_models import BarGroup
    from well_viewer.features.barplot.collect import batch_members

    ctx = _plate()
    ctx.runtime = SimpleNamespace(barplot=SimpleNamespace(current_keys=[]))
    group = BarGroup("g", solo_wells=["A01", "A03", "B03"])
    displays = [m[1] for m in batch_members(ctx, group, pool_replicates=True)]
    assert displays == ["HeLa · 0 µM", "HeLa · 10 µM", "U2OS · 10 µM"]


def test_a_unitless_numeric_dose_survives_the_bar_tabs_tick_guard():
    """The guard rewrites a numeric label into the well token, and a `Dose` of
    ``10`` declared without a unit is exactly the label this feature exists to
    put on the axis. It applies to a hand-typed label only."""
    from well_viewer.features.barplot.collect import bar_well_display_label

    ctx = _plate(unit="", dose_only=True)
    assert bar_well_display_label(ctx, "A03") == "10"

    typed = _plate(unit="", dose_only=True, labels={"A03": "3"})
    assert bar_well_display_label(typed, "A03") == "A03"


# ── the figure and the rows behind it agree ─────────────────────────────────

def test_the_exported_well_name_is_what_the_figure_drew():
    """One word, one definition. The map form and the per-well form are the
    same answer for every loaded well, so a CSV cannot call a well ``A01``
    while the figure it came from calls it ``HeLa · 10 µM``."""
    ctx = _plate(labels={"A01": "shCtrl"})
    names = figure_names_map(ctx)
    for well in WELLS:
        assert names.get(well, well) == well_display_label(ctx, well), well


def test_a_typed_label_for_an_absent_well_is_kept_in_the_map():
    """``well_name_for``'s strict mode consults the map to tell a well token it
    does not recognise from a group name; dropping the label of a well this
    run did not cover would blank the column for the rest of the plate."""
    ctx = _plate(labels={"H12": "Blank"}, wells=("A01",))
    assert figure_names_map(ctx)["H12"] == "Blank"


def test_the_map_names_no_well_the_plate_does_not_hold():
    ctx = _plate(wells=("A01", "A02"))
    assert set(figure_names_map(ctx)) == {"A01", "A02"}


# ── Home stops calling the plate unannotated ────────────────────────────────

def _home_ctx(ctx, **plate):
    ctx.state.selection.selections = []
    ctx.state.selection.plot_groups = []
    ctx.state.selection.notes_text = ""
    ctx.state.channel = SimpleNamespace(ratio_metrics=[])
    ctx.state.dataset.data_dir = None
    ctx.state.dataset.pipeline_info = {
        "run_check": {"wells": {w: {"health": "complete"}
                                for w in ctx.state.dataset.well_paths}}}
    for key, value in plate.items():
        setattr(ctx.state.dataset, key, value)
    return ctx


def test_home_counts_the_wells_the_figures_name():
    from well_viewer.features.home.collect import wells_named_on_figures

    ctx = _home_ctx(_plate())
    assert wells_named_on_figures(ctx) == len(WELLS)

    bare = _home_ctx(_plate())
    bare.state.selection.variables = sv.VariableSet()
    assert wells_named_on_figures(bare) == 0

    typed = _home_ctx(_plate(labels={"A01": "shCtrl"}))
    typed.state.selection.variables = sv.VariableSet()
    assert wells_named_on_figures(typed) == 1


@pytest.mark.parametrize("named", [True, False])
def test_home_only_asks_for_labels_when_nothing_names_the_wells(named):
    """The reported sentence. It was printed over figures already reading
    ``HeLa · 10 µM``, because it counted the Well Labels tab rather than the
    plate."""
    from well_viewer.features.home.collect import next_step

    ctx = _home_ctx(_plate())
    if not named:
        ctx.state.selection.variables = sv.VariableSet()
    step = next_step(ctx)
    assert ("No sample labels yet" in step["text"]) is (not named)


def test_home_does_not_ask_for_replicate_sets_when_a_variable_can_pool():
    """The rung below it, and the same mistake: a plate with a dose variable
    pools by dose, so "No replicate sets yet" is a nudge to redo the work."""
    from well_viewer.features.home.collect import next_step

    assert "replicate" not in next_step(_home_ctx(_plate()))["text"].lower()

    bare = _home_ctx(_plate(labels={w: f"s{w}" for w in WELLS}))
    bare.state.selection.variables = sv.VariableSet()
    assert "No replicate sets yet" in next_step(bare)["text"]


# ── the Spatial summary's own axis ──────────────────────────────────────────

def test_the_spatial_summary_axis_uses_the_same_names():
    """Driven through ``render_spatial``, the tab's real entry point, and read
    off the axis (§7.4 #1). Asserting ``_shown_wells`` alone passed with the
    renderer no longer calling it — the helper was right and the figure was
    unchanged, which is the whole failure mode this file is about."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    from well_viewer.features.spatial import state as sp_state
    from well_viewer.features.spatial.renderer import render_spatial

    ctx = _plate(labels={"A01": "shCtrl"})
    fig = plt.figure()
    try:
        summaries = np.empty((2, 1), dtype=object)
        summaries[0, 0] = SimpleNamespace(nnd_median=1.0)
        summaries[1, 0] = SimpleNamespace(nnd_median=2.0)
        st = SimpleNamespace(
            last_error="", viz=sp_state.VIZ_SUMMARY_PANEL,
            summary_display=sp_state.SUMMARY_DISPLAY_HEATMAP, frames=[],
            last_result={"wells": ["A01", "B03"], "timepoints": [0.0],
                         "summaries": summaries,
                         "summary_metric_key": "nnd_median",
                         "summary_display": sp_state.SUMMARY_DISPLAY_HEATMAP})
        ctx.state.feature_slice = lambda name: st if name == "spatial" else None
        ctx.state.plot = SimpleNamespace()
        ctx.widgets = SimpleNamespace(
            get_or_none=lambda key: fig if key == "_sp_fig" else None)
        render_spatial(ctx)
        ax = fig.axes[0]
        fig.canvas.draw()
        drawn = [t.get_text() for t in ax.get_yticklabels() if t.get_text()]
        assert drawn == ["shCtrl", "U2OS · 10 µM"], drawn
    finally:
        plt.close(fig)
