"""What the Line tab is drawing, as a :class:`CellScope`.

Line aggregates through ``stats_compute.aggregate_well_cached`` →
``data_loading.aggregate_with_threshold_df``, so two things differ from
Distribution and both change which rows belong in the file:

* **Timepoints resolve through the string column.** The aggregation falls back
  to ``timepoint`` (parsed, else ordinally numbered) for rows whose numeric
  ``timepoint_hours`` is missing, and drops a row it still cannot place *even
  with no timepoint filter set*. A plate whose timepoints are text plots
  normally, so an export that filtered on the numeric column alone would write
  an empty file beside a full figure.
* **"Above threshold" may not mean ``value > threshold``.** When scatter-gate
  thresholding is active the aggregation partitions on the ``Gate_On`` column
  instead, and the marker has to follow — a file whose marker contradicts the
  bar it came from is worse than one without the marker.

The tab draws every selected timepoint, so the scope carries none: an empty
``timepoints`` means "whatever the rows have", which is what the x axis shows.
"""

from __future__ import annotations

from well_viewer.core.channel_state_controller import on_cutoff_for_column
from well_viewer.domain.stats_compute import _thresh_on_col
from well_viewer.features.export.cell_scope_compute import CellScope, scope_from_ctx


def cell_scope(ctx) -> CellScope:
    val_col = ctx.state.channel.active_val_col
    rep_note = (
        "a replicate set's point is the mean of its wells' means with a "
        "cross-well SD (ddof=1); group by well_label to reproduce it"
    )
    return scope_from_ctx(
        ctx,
        plot="line",
        val_col=val_col,
        threshold=float(on_cutoff_for_column(ctx, val_col)),
        timepoints=(),
        include_gate=True,
        tp_fallback=True,
        on_col=_thresh_on_col(ctx),
        required_columns=("area_px",),
        notes=(("drawn statistic",
                "mean of the value column over plot_above_threshold=1, per timepoint"),
               ("replicate sets", rep_note)),
    )
