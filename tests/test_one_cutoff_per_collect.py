"""Every panel of one figure is cut against the same ON cutoff.

A replicate set's Mean, Fraction and Events panels are produced by three
functions — ``compute_rep_stats``, ``compute_rep_n_above_stats`` and
``compute_rep_n_above_total`` — and they have to describe the same population,
because they sit side by side in one figure and one exported CSV row.

They did not. ``compute_rep_stats`` took the cutoff its caller passed, while the
two events producers ignored theirs and re-derived from
``ctx.state.channel.active_channel``. On the Line and Bar tabs those agree by
coincidence: the tab resolves its own threshold with the identical expression. A
**batch export of a channel other than the one the tab is showing** does not,
because ``base_panel._app_val_col_scope`` swaps ``active_val_col`` for the
duration of the render and leaves ``active_channel`` alone. So the panel's cutoff
for the exported channel reached the Mean and Fraction panels while the Events
panel beside them kept using the tab's.

Reproduced before the fix, and the numbers are the reason this file exists:
exporting ``mcherry`` (ON cutoff 5.0) while the tab showed ``gfp`` (ON cutoff
50.0) put both into a single collect. Nothing raised; the figure drew; the Events
panel simply counted a different population from the two panels next to it.

The assertion is on **what the producers were asked to compute with**, not on the
numbers that came back. Two cutoffs can produce equal counts on a particular
plate — a fixture where every cell clears both — and a test reading only the
outputs would pass on exactly the data that hides the bug.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from well_viewer.core.app_runtime import AppRuntime
from well_viewer.core.app_state import AppState
from well_viewer.core.event_bus import EventBus
from well_viewer.core.widget_registry import WidgetRegistry
from well_viewer.domain import stats_compute as sc
from well_viewer.domain.batch_models import ReplicateSet
from well_viewer.viewer_context import ViewerContext

WELLS = ["A01", "A02"]
TAB_CHANNEL = "gfp"
EXPORT_CHANNEL = "mcherry"
TAB_CUTOFF = 50.0
EXPORT_CUTOFF = 5.0


def _rows() -> pd.DataFrame:
    n = 12
    return pd.DataFrame({
        "well": "A01",
        "fov": ["1"] * n,
        "timepoint": ["0h"] * n,
        "timepoint_hours": 0.0,
        "nucleus_id": np.arange(1, n + 1),
        "area_px": 200.0,
        "gfp_mean_intensity": np.linspace(10.0, 120.0, n),
        "mcherry_mean_intensity": np.linspace(1.0, 60.0, n),
        "Included": 1,
    })


@pytest.fixture
def ctx(tmp_path):
    from well_viewer.features.registry import attach_feature_state

    state = AppState()
    attach_feature_state(state)
    state.channel.active_channel = TAB_CHANNEL
    state.channel.active_val_col = f"{TAB_CHANNEL}_mean_intensity"
    state.cell_gating.thresh_frac_on = {
        TAB_CHANNEL: TAB_CUTOFF,
        EXPORT_CHANNEL: EXPORT_CUTOFF,
    }
    state.dataset.data_dir = tmp_path
    state.dataset.well_paths = {w: tmp_path / f"{w}.csv" for w in WELLS}
    for w in WELLS:
        state.dataset.cache[w] = _rows()
    store: dict = {}
    return ViewerContext(
        state=state, bus=EventBus(),
        widgets=WidgetRegistry(None, store=store),
        runtime=AppRuntime(None, store=store),
    )


@pytest.fixture
def thresholds_asked_for(monkeypatch):
    """Record the cutoff each producer actually computes with."""
    seen: list[float] = []
    original = sc.group_replicate_stats

    def spy(ctx, wells, target_t, **kw):
        seen.append(kw.get("threshold"))
        return original(ctx, wells, target_t, **kw)

    monkeypatch.setattr(sc, "group_replicate_stats", spy)
    return seen


def _collect_as_a_batch_export_would(ctx, panel_cutoff: float):
    """Drive the three producers the way an off-screen export drives them.

    ``_app_val_col_scope`` swaps only the value column, which is the whole
    point: the channel the cutoff is keyed on stays as the tab left it.
    """
    rset = ReplicateSet("G1", list(WELLS))
    chan = ctx.state.channel
    saved = chan.active_val_col
    chan.active_val_col = f"{EXPORT_CHANNEL}_mean_intensity"
    try:
        sc.compute_rep_stats(ctx, rset, 0.0, panel_cutoff, False)
        sc.compute_rep_n_above_stats(ctx, rset, 0.0, False, panel_cutoff)
        sc.compute_rep_n_above_total(ctx, rset, 0.0, panel_cutoff)
    finally:
        chan.active_val_col = saved


def test_a_batch_export_of_another_channel_uses_one_cutoff(
        ctx, thresholds_asked_for):
    """The defect, driven through the collector the export actually calls.

    This goes through ``collect_line_series`` rather than calling the three
    producers directly, because the fix is that the *callers* pass their cutoff
    down — a test that called the producers itself would be asserting its own
    argument list. Driving the collector is also what lets this run against the
    unfixed code, where the producers take no cutoff at all: there, the
    divergence appears without the test having to mention it.
    """
    from well_viewer.features.lineplot.controller import collect_line_series

    ctx.state.selection.selections = [
        {"name": "G1", "wells": list(WELLS), "color": "#4C72B0", "hidden": False},
    ]
    chan = ctx.state.channel
    saved = chan.active_val_col
    # What ``base_panel._app_val_col_scope`` does: swap the value column and
    # leave the channel the cutoffs are keyed on exactly as the tab left it.
    chan.active_val_col = f"{EXPORT_CHANNEL}_mean_intensity"
    try:
        collect_line_series(
            ctx, threshold=EXPORT_CUTOFF, use_sem=False,
            val_col=f"{EXPORT_CHANNEL}_mean_intensity", include_cdf=False,
        )
    finally:
        chan.active_val_col = saved

    distinct = set(thresholds_asked_for)
    assert distinct, "precondition: the collector reached the producers at all"
    assert distinct == {EXPORT_CUTOFF}, (
        "the panels of one exported figure were cut against different ON "
        f"cutoffs: {sorted(distinct)}. The Mean and Fraction panels and the "
        "Events panel beside them are describing different populations."
    )


def test_the_producers_take_the_cutoff_they_are_handed(
        ctx, thresholds_asked_for):
    """The same property one level down, asserted directly.

    Kept separate from the collector-driven case above because it cannot run
    against the unfixed code — the producers there accept no cutoff argument, so
    it would fail with a TypeError, which measures nothing.
    """
    _collect_as_a_batch_export_would(ctx, EXPORT_CUTOFF)
    assert set(thresholds_asked_for) == {EXPORT_CUTOFF}


def test_the_tab_path_is_unchanged(ctx, thresholds_asked_for):
    """The common case must keep behaving exactly as it did.

    On the tab the active channel and the requested column agree, so passing the
    cutoff explicitly and re-deriving it give the same number. This pins that the
    fix did not move the tab's numbers while repairing the export's.
    """
    rset = ReplicateSet("G1", list(WELLS))
    sc.compute_rep_stats(ctx, rset, 0.0, TAB_CUTOFF, False)
    sc.compute_rep_n_above_stats(ctx, rset, 0.0, False, TAB_CUTOFF)
    sc.compute_rep_n_above_total(ctx, rset, 0.0, TAB_CUTOFF)
    assert set(thresholds_asked_for) == {TAB_CUTOFF}


def test_omitting_the_cutoff_still_falls_back_to_the_active_channel(
        ctx, thresholds_asked_for):
    """The default is kept so no existing caller changed behaviour.

    A caller that does not name a cutoff gets the active channel's, which is
    what both producers always did. The fix widens the contract; it does not
    replace it.
    """
    rset = ReplicateSet("G1", list(WELLS))
    sc.compute_rep_n_above_stats(ctx, rset, 0.0, False)
    assert set(thresholds_asked_for) == {TAB_CUTOFF}


def test_the_two_cutoffs_are_genuinely_different_on_this_fixture():
    """Guard against a fixture that could not tell the two apart.

    If the export and tab cutoffs ever became equal here, every assertion above
    would pass without measuring anything.
    """
    assert TAB_CUTOFF != EXPORT_CUTOFF


def test_the_cutoffs_select_different_populations(ctx):
    """And that the difference is observable in the data, not just in the key.

    A cutoff difference that no cell straddles would make the whole class of bug
    invisible on this fixture. Some cells must sit between the two.
    """
    values = _rows()[f"{EXPORT_CHANNEL}_mean_intensity"].to_numpy()
    straddling = ((values > EXPORT_CUTOFF) & (values <= TAB_CUTOFF)).sum()
    assert straddling > 0, (
        "no cell lies between the two cutoffs, so this fixture cannot "
        "distinguish them"
    )
