"""Regression tests for the ON cutoff the scatter *aggregate* path feeds counts.

The Wells (aggregate) scatter mode reduces each well through
``group_replicate_stats``, whose mean is the mean of the *above-cutoff*
population. The cutoff it was handed came from ``get_thresh_frac_on`` — a
fluorescence-intensity cutoff that starts at 50.0 for a readout channel. smFISH
spot counts are small integers, so nothing cleared 50, every well's mean came
back undefined, every point was dropped, and the tab drew "No data available."
against a plate whose counts were sitting in the CSVs. The per-cell (Cells) mode
plotted the same column correctly, which is what made the report look like a
scatter bug rather than a units bug.

Counts now carry their own per-channel cutoff, defaulting below the minimum a
tally can take so every cell — including the zero-spot ones, which are a
measurement and not a gap — is in the ON population.

These tests deliberately name no symbol the fix introduces. They seed
``thresh_frac_on`` (a name that exists on both sides of the change) and assert
the *outcome*, so running them against the pre-fix tree produces a plain
assertion failure rather than an ImportError that would measure nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from well_viewer.features.scatter.collect import collect_scatter_agg_data


def _well_df(counts, *, tp_hours=24.0, channel="w3cy5"):
    """One row per cell, in the shape the aggregator's gating chokepoint needs."""
    n = len(counts)
    return pd.DataFrame({
        "Included": [1] * n,
        "area_px": [200.0] * n,
        "timepoint_hours": [tp_hours] * n,
        "timepoint": [f"{int(tp_hours):02d}h"] * n,
        "fov": ["1"] * n,
        "nucleus_id": [str(i) for i in range(n)],
        f"{channel}_smfish_count": counts,
    })


def _ctx(label_to_df, *, thresh_frac_on=None, thresh_count_on=None,
         fluor_channels=()):
    """Minimal ctx for ``collect_scatter_agg_data``.

    Broader than the per-cell collector's stub in ``test_scatter_smfish.py``:
    the aggregate path additionally reads the plot slice, the cell-gating slice
    (through ``get_thresh_frac_on`` → ``_cell_gating_resolve_key``), the channel
    slice's ratio/label maps, and ``state.repo`` (via ``stats_compute._safe_rows``).

    ``fluor_channels`` is left empty by default on purpose: ``get_all_fluor_gates``
    builds its gate dict from it, and every gate it returns is applied against
    that channel's ``_mean_intensity`` column, which a count-only fixture has
    not got.
    """
    cache = dict(label_to_df)
    state = SimpleNamespace(
        dataset=SimpleNamespace(
            well_paths=dict.fromkeys(label_to_df),
            well_labels={},
            cache=cache,
        ),
        channel=SimpleNamespace(
            ratio_index={},
            label_to_channel_key={},
            fluor_channels=list(fluor_channels),
            active_channel="w3cy5",
        ),
        cell_gating=SimpleNamespace(
            fluor_gates={},
            thresh_frac_on=dict(thresh_frac_on or {}),
            thresh_count_on=dict(thresh_count_on or {}),
            use_scatter_thresh=False,
            scatter_gates=[],
            thresh_choices=None,
        ),
        selection=SimpleNamespace(selected_wells=list(label_to_df), selections=[]),
        plot=SimpleNamespace(use_sem=False, use_fov_replicates=False),
        repo=SimpleNamespace(rows_or_none=cache.get),
    )
    # ``aggregate.well`` is the pure-logic MockApp's precomputed-points delegate;
    # None takes the production DataFrame path, which is what we are measuring.
    runtime = SimpleNamespace(aggregate=SimpleNamespace(well=None))
    return SimpleNamespace(state=state, runtime=runtime)


def test_smfish_axes_survive_the_intensity_cutoff():
    """The reported bug: an intensity ON cutoff must not empty a count plot."""
    ctx = _ctx(
        {"A01": _well_df([0, 3, 5, 0, 2, 1])},
        # The readout default. Every spot count is far below it.
        thresh_frac_on={"w3cy5": 50.0},
    )
    out = collect_scatter_agg_data(
        ctx,
        "smFISH Count W3CY5",
        "smFISH Count W3CY5",
        [24.0],
        well_colors=["#000000"],
    )
    assert out, (
        "smFISH count axes were emptied by the channel's fluorescence ON cutoff"
    )


def test_zero_spot_cells_are_in_the_mean():
    """Zero spots is a measurement, not a gap: 11/6, not 11/4."""
    ctx = _ctx(
        {"A01": _well_df([0, 3, 5, 0, 2, 1])},
        thresh_frac_on={"w3cy5": 50.0},
    )
    out = collect_scatter_agg_data(
        ctx, "smFISH Count W3CY5", "smFISH Count W3CY5", [24.0],
        well_colors=["#000000"],
    )
    (point,) = out.values()
    assert point["x"] == [11 / 6]


def test_a_deliberate_count_cutoff_still_cuts():
    """The feature stays real — the fix is a separate cutoff, not a hardcoded -1.

    With the count cutoff at 2, the ON population is the cells carrying more
    than two spots (3 and 5), so the mean is 4.0.
    """
    ctx = _ctx(
        {"A01": _well_df([0, 3, 5, 0, 2, 1])},
        thresh_count_on={"w3cy5": 2.0},
    )
    out = collect_scatter_agg_data(
        ctx, "smFISH Count W3CY5", "smFISH Count W3CY5", [24.0],
        well_colors=["#000000"],
    )
    (point,) = out.values()
    assert point["x"] == [4.0]


def test_intensity_axes_still_honour_their_own_cutoff():
    """Counter-test: the intensity path must keep the cutoff it always had."""
    df = pd.DataFrame({
        "Included": [1] * 4,
        "area_px": [200.0] * 4,
        "timepoint_hours": [24.0] * 4,
        "timepoint": ["24h"] * 4,
        "fov": ["1"] * 4,
        "nucleus_id": [str(i) for i in range(4)],
        "w3cy5_mean_intensity": [10.0, 100.0, 1000.0, 2000.0],
    })
    ctx = _ctx({"A01": df}, thresh_frac_on={"w3cy5": 500.0})
    out = collect_scatter_agg_data(
        ctx,
        "Mean Fluorescence W3CY5 (Mean Intensity)",
        "Mean Fluorescence W3CY5 (Mean Intensity)",
        [24.0],
        well_colors=["#000000"],
    )
    (point,) = out.values()
    # Only the two cells above the 500 cutoff contribute.
    assert point["x"] == [1500.0]


# ── the empty-plot message ───────────────────────────────────────────────────

def _cut_sink_for(ctx, stat="smFISH Count W3CY5"):
    sink: dict = {}
    collect_scatter_agg_data(ctx, stat, stat, [24.0],
                             well_colors=["#000000"], cut_sink=sink)
    return sink


def test_cut_cells_are_reported_not_blamed_on_the_selection():
    """The message must name the cutoff, not send the user back to the picker.

    Wells were selected and timepoints ticked; the cells existed and were cut.
    Telling them to "select wells/groups and timepoints" is the failure this
    replaced.
    """
    from well_viewer.features.scatter.collect import agg_empty_message

    ctx = _ctx({"A01": _well_df([0, 3, 5, 0, 2, 1])},
               thresh_count_on={"w3cy5": 500.0})
    sink = _cut_sink_for(ctx)
    assert sink, "an all-cut axis must record why it produced nothing"
    assert sink["X"]["n_total"] == 6 and sink["X"]["n_above"] == 0

    msg = agg_empty_message(ctx, sink)
    assert "cut by the ON cutoff" in msg
    assert "W3CY5" in msg and "kept 0 of 6 cells" in msg
    assert "ThreshCountOn" in msg
    assert "select wells" not in msg.lower()


def test_no_cells_at_all_reads_as_a_timepoint_problem():
    """The other branch: nothing was cut because there was nothing there."""
    from well_viewer.features.scatter.collect import agg_empty_message

    msg = agg_empty_message(_ctx({}), {})
    assert "No data at the selected timepoints" in msg
    assert "cut by" not in msg


def test_nothing_is_recorded_when_the_plot_succeeds():
    ctx = _ctx({"A01": _well_df([0, 3, 5, 0, 2, 1])})
    assert _cut_sink_for(ctx) == {}
