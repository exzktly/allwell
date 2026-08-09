"""The aggregate scatter says *why* it has nothing to draw.

Split from ``test_scatter_agg_count_cutoff.py`` on purpose. That file is the
regression proof for the reported bug and names no symbol the fix introduces, so
it fails honestly against the pre-fix tree. These tests cannot: they import
``agg_empty_message`` and pass ``cut_sink``, both new, so on the old tree they
would raise ImportError / TypeError and measure nothing at all. They are
behaviour tests for the message, not a proof that the bug existed.
"""

from __future__ import annotations

from tests.test_scatter_agg_count_cutoff import _ctx, _well_df
from well_viewer.features.scatter.collect import collect_scatter_agg_data
from well_viewer.features.scatter._messages import agg_empty_message


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
    msg = agg_empty_message(_ctx({}), {})
    assert "No data at the selected timepoints" in msg
    assert "cut by" not in msg


def test_nothing_is_recorded_when_the_plot_succeeds():
    ctx = _ctx({"A01": _well_df([0, 3, 5, 0, 2, 1])})
    assert _cut_sink_for(ctx) == {}
