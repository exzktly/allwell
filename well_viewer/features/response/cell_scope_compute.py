"""What the Response tab is drawing, as a :class:`CellScope`.

The cells behind a response curve are the same ones the Line tab exports —
both aggregate through ``stats_compute.compute_rep_stats``, so the drawn
statistic and the "above threshold" partition are identical, and the notes say
so in the same words.

One thing differs, and it is the reason this is not simply Line's scope reused.
**Response draws only the ticked timepoints.** Line draws every selected one and
so carries none, meaning "whatever the rows have"; a Response export that did
the same would write twelve timepoints beside a figure showing one, which is
precisely the mismatch a cell-scope exists to prevent — the file has to describe
the figure it was saved from.
"""

from __future__ import annotations

from well_viewer.core.channel_state_controller import on_cutoff_for_column
from well_viewer.domain.stats_compute import _thresh_on_col
from well_viewer.features.export.cell_scope_compute import CellScope, scope_from_ctx
from well_viewer.features.response.controller import (
    response_val_col, timepoints_for,
)


def cell_scope(ctx) -> CellScope:
    # The tab's own column, not the shared one. This module's docstring says the
    # file has to describe the figure it was saved from, and the Channel and
    # Property became tab-local: left reading the band, "Export cell data" on a
    # Response figure of RFP fractions wrote GFP mean intensity at GFP's cutoff.
    val_col = response_val_col(ctx) or ctx.state.channel.active_val_col
    rep_note = (
        "a member's point is the mean of its wells' means with a cross-well SD "
        "(ddof=1); group by well_label to reproduce it"
    )
    x_note = (
        "one point per member per timepoint, placed on x by the member's value "
        "of the chosen Number variable; members whose wells disagree, or that "
        "hold an unfilled well, are named on the figure and are not in it"
    )
    return scope_from_ctx(
        ctx,
        plot="response",
        val_col=val_col,
        threshold=float(on_cutoff_for_column(ctx, val_col)),
        timepoints=tuple(timepoints_for(ctx)),
        include_gate=True,
        tp_fallback=True,
        on_col=_thresh_on_col(ctx),
        required_columns=("area_px",),
        notes=(("drawn statistic",
                "mean of the value column over plot_above_threshold=1, per timepoint"),
               ("replicate sets", rep_note),
               ("x axis", x_note)),
    )
