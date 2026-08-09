"""End-to-end: smFISH spot counts, from the CSVs on disk to the plots.

The suite had thorough unit cover for the smFISH *worker* — how counts are
computed and written back into the per-well CSVs — and none at all for what
happens to those counts afterwards. That gap is exactly where the reported bug
lived: the counts were on disk, the smFISH tab was happy, and every per-well
plot silently drew nothing, because each aggregate path fed the count column the
channel's *fluorescence* ON cutoff (ThreshFracOn, 50.0 by default) and no spot
tally clears 50.

So this drives a real ``WellViewerApp`` over a real dataset directory whose CSVs
carry a ``mcherry_smfish_count`` column, and asserts the numbers the plots
actually collect. The count channel deliberately also has an intensity column,
because the fix is that one channel now carries *two* independent cutoffs — a
dataset without both could not tell that fix apart from one that merely stopped
consulting either.

The fixture's tallies are a closed-form function of (well, cell), so every
expected value below is exact. Per well, cells 1–6 carry ``well_index + 0 … +5``:

    A01 → 0 1 2 3 4 5   (mean 2.5)      A02 → 1 2 3 4 5 6   (mean 3.5)
    B01 → 2 3 4 5 6 7   (mean 4.5)      B02 → 3 4 5 6 7 8   (mean 5.5)

A01 spans zero. That cell is the one an ON cutoff of 0 quietly drops and an
intensity cutoff of 50 drops along with every other cell, so the well that makes
both failures visible is the one most of these tests use.
"""

from __future__ import annotations

import json

import pytest

from tests.fixtures.mini_dataset import SMFISH_CHANNEL
# Only names that exist on both sides of the fix are imported at module scope.
# The ones the fix introduces are imported inside the tests that need them, so
# this file still *collects* against the pre-fix tree and the first test below
# fails there with a plain assertion rather than an ImportError at import time —
# which would look like a proof and measure nothing.
from well_viewer.core.channel_state_controller import (
    get_thresh_frac_on,
    set_active_channel,
    set_active_metric,
    set_thresh_frac_on,
)

COUNT_COL = f"{SMFISH_CHANNEL}_smfish_count"
COUNT_STAT = f"smFISH Count {SMFISH_CHANNEL.upper()}"
TP = 0.0


def _select(ctx, *wells):
    """Put the app on the smFISH channel + count Property for *wells*."""
    ctx.state.selection.selected_wells = set(wells)
    set_active_channel(ctx, SMFISH_CHANNEL)
    set_active_metric(ctx, "smfish_count")
    return ctx


def _agg_x(ctx, wells=("A01",)):
    """The X value the Wells-mode scatter collects, or None when it drew nothing."""
    from well_viewer.features.scatter.collect import collect_scatter_agg_data
    out = collect_scatter_agg_data(
        ctx, COUNT_STAT, COUNT_STAT, [TP], well_colors=["#000000"] * len(wells))
    if not out:
        return None
    (point,) = out.values()
    return point["x"][0]


# ── the reported bug ─────────────────────────────────────────────────────────

def test_wells_mode_draws_smfish_counts(loaded_app_smfish):
    """The report: Wells mode said "no data available" though the data exists.

    Nothing is configured here beyond picking the metric — the channel's
    ThreshFracOn sits at its 50.0 default, which is the state every user starts
    in and the state that emptied the plot.
    """
    ctx = _select(loaded_app_smfish._ctx, "A01")
    assert get_thresh_frac_on(ctx, SMFISH_CHANNEL) == 50.0, "fixture drifted"
    assert _agg_x(ctx) == 2.5


def test_cells_mode_and_wells_mode_agree_on_the_same_counts(loaded_app_smfish):
    """The asymmetry that made this look like a scatter bug rather than a units bug.

    Per-cell mode always plotted these counts correctly; per-well mode dropped
    every point. They now describe the same population, so the per-well mean is
    the mean of the per-cell values.
    """
    from well_viewer.features.scatter.collect import collect_scatter_data
    ctx = _select(loaded_app_smfish._ctx, "A01")
    cells = collect_scatter_data(
        ctx, COUNT_COL, COUNT_COL, [TP], well_colors=["#000000"])
    per_cell = sorted(cells["A01"]["x"])
    assert per_cell == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert _agg_x(ctx) == sum(per_cell) / len(per_cell)


# ── the two cutoffs are independent ──────────────────────────────────────────

def test_the_intensity_cutoff_no_longer_touches_the_counts(loaded_app_smfish):
    """Raising ThreshFracOn far above every spot tally must change nothing.

    This is the defect stated as an assertion: before the fix, any cutoff above
    the tally range emptied the plot, and 50.0 already was.
    """
    ctx = _select(loaded_app_smfish._ctx, "A01")
    set_thresh_frac_on(ctx, 500.0, SMFISH_CHANNEL)
    assert get_thresh_frac_on(ctx, SMFISH_CHANNEL) == 500.0
    assert _agg_x(ctx) == 2.5


def test_the_count_cutoff_does_cut(loaded_app_smfish):
    """And the new cutoff is a real control, not a hardcoded "keep everything".

    Above 2 spots, A01 keeps cells carrying 3, 4 and 5 — mean 4.0.
    """
    from well_viewer.core.channel_state_controller import (
        get_thresh_count_on, set_thresh_count_on,
    )
    ctx = _select(loaded_app_smfish._ctx, "A01")
    set_thresh_count_on(ctx, 2.0, SMFISH_CHANNEL)
    assert get_thresh_count_on(ctx, SMFISH_CHANNEL) == 2.0
    assert _agg_x(ctx) == 4.0


def test_zero_spot_cells_count_at_the_default(loaded_app_smfish):
    """-1 vs 0 is the difference between a measurement and a dropped one.

    A cutoff of 0 keeps only 1–5 (mean 3.0). The default keeps the zero-spot
    cell too, and the mean drops to 2.5 — the honest number.
    """
    from well_viewer.core.channel_state_controller import set_thresh_count_on
    ctx = _select(loaded_app_smfish._ctx, "A01")
    assert _agg_x(ctx) == 2.5
    set_thresh_count_on(ctx, 0.0, SMFISH_CHANNEL)
    assert _agg_x(ctx) == 3.0


# ── the same numbers reach the other aggregate plots ─────────────────────────

def test_bar_plot_collects_the_same_population(loaded_app_smfish):
    """Bar/line were wrong the same way and are swept the same way.

    At the default every cell is on, so the fraction panel reads 1.00 — correct
    for "no cut", and the visible consequence documented in the manual.
    """
    from well_viewer.core.channel_state_controller import set_thresh_count_on
    from well_viewer.features.barplot.collect import collect_bar_items
    ctx = _select(loaded_app_smfish._ctx, "A01", "A02")
    _use_groups, items, _band = collect_bar_items(ctx, TP)
    by_well = {it.key: it for it in items}
    assert by_well["A01"].mean == pytest.approx(2.5)
    assert by_well["A02"].mean == pytest.approx(3.5)
    assert by_well["A01"].frac == pytest.approx(1.0)

    set_thresh_count_on(ctx, 2.0, SMFISH_CHANNEL)
    _use_groups, items, _band = collect_bar_items(ctx, TP)
    by_well = {it.key: it for it in items}
    assert by_well["A01"].mean == pytest.approx(4.0)
    assert by_well["A01"].frac == pytest.approx(0.5)      # 3 of 6 carry > 2


def test_the_resolver_picks_the_cutoff_from_the_column(loaded_app_smfish):
    """One channel, two metrics, two cutoffs — resolved by what is being measured."""
    from well_viewer.core.channel_state_controller import (
        on_cutoff_for_column, set_thresh_count_on,
    )
    ctx = _select(loaded_app_smfish._ctx, "A01")
    set_thresh_frac_on(ctx, 500.0, SMFISH_CHANNEL)
    set_thresh_count_on(ctx, 2.0, SMFISH_CHANNEL)
    assert on_cutoff_for_column(ctx, COUNT_COL) == 2.0
    assert on_cutoff_for_column(ctx, f"{SMFISH_CHANNEL}_mean_intensity") == 500.0


# ── the control the user actually touches ────────────────────────────────────

def test_the_quick_edit_writes_the_cutoff_it_is_showing(loaded_app_smfish):
    """The box follows the Property, and so does the write.

    Retargeting only the display would leave a stepper captioned "ON spots"
    writing into ThreshFracOn — destroying the intensity cutoff and leaving the
    count one untouched.
    """
    from well_viewer.core.channel_state_controller import (
        get_thresh_count_on, quick_edit_cutoff_is_count, set_on_cutoff,
    )
    ctx = _select(loaded_app_smfish._ctx, "A01")
    set_thresh_frac_on(ctx, 500.0, SMFISH_CHANNEL)
    assert quick_edit_cutoff_is_count(ctx) is True

    set_on_cutoff(ctx, 3.0)

    assert get_thresh_count_on(ctx, SMFISH_CHANNEL) == 3.0
    assert get_thresh_frac_on(ctx, SMFISH_CHANNEL) == 500.0, (
        "the intensity cutoff was overwritten by a spot count"
    )
    stepper = ctx.widgets.get_or_none("_plotting_thresh_frac_stepper")
    if stepper is not None:
        assert stepper.value() == 3.0
        # What the box actually reads: the unit belongs in the field being
        # typed into, not only in a caption beside it.
        assert stepper._edit.text() == "3 spots"
    caption = ctx.widgets.get_or_none("_plotting_thresh_frac_lbl")
    if caption is not None:
        assert caption.text() == "ON spots"

    # And back the other way: an intensity Property edits the intensity cutoff.
    set_active_metric(ctx, "mean_intensity")
    assert quick_edit_cutoff_is_count(ctx) is False
    set_on_cutoff(ctx, 250.0)
    assert get_thresh_frac_on(ctx, SMFISH_CHANNEL) == 250.0
    assert get_thresh_count_on(ctx, SMFISH_CHANNEL) == 3.0
    if caption is not None:
        assert caption.text() == "ON threshold"


def test_only_smfish_channels_get_a_count_field(loaded_app_smfish):
    """A channel with no spot-count column has no count cutoff to edit."""
    from well_viewer.core.channel_state_controller import quick_edit_cutoff_is_count
    ctx = _select(loaded_app_smfish._ctx, "A01")
    edits = ctx.runtime.cell_gating.thresh_count_edits or {}
    assert SMFISH_CHANNEL in edits
    assert "gfp" not in edits
    # ...and the quick-edit will not retarget onto one, whatever the Property.
    assert quick_edit_cutoff_is_count(ctx, "gfp") is False


# ── it survives the round trip to disk ───────────────────────────────────────

def test_the_count_cutoff_persists_and_reloads(loaded_app_smfish, mini_dataset_smfish):
    """Written into the sidecar beside ThreshFracOn, and read back on open."""
    from well_viewer.core.channel_state_controller import (
        get_thresh_count_on, set_thresh_count_on,
    )
    ctx = _select(loaded_app_smfish._ctx, "A01")
    set_thresh_count_on(ctx, 2.0, SMFISH_CHANNEL)

    block = json.loads(
        (mini_dataset_smfish / "pipeline_info.json").read_text())["cell_gating"]
    assert block["thresh_count_on"] == {SMFISH_CHANNEL: 2.0}

    loaded_app_smfish._load_path(mini_dataset_smfish)
    reloaded = loaded_app_smfish._ctx
    assert get_thresh_count_on(reloaded, SMFISH_CHANNEL) == 2.0
    assert _select(reloaded, "A01") is reloaded
    assert _agg_x(reloaded) == 4.0, "the reloaded cutoff did not reach the plot"
