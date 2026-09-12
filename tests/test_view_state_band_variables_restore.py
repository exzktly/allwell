"""Regression: Group by / Colour by / Split by must survive a save and reopen.

The three sample-variable band settings were declared on ``PlotState`` and never
captured in the view-state snapshot. ``bar_pool_replicates`` — the setting beside
them that changes what a bar means in the same way — *was* captured, which is
what made the gap invisible: the block looked complete.

**Group by is the one that matters most.** Every plot pools through
``plot_replicate_sets``, so grouping by a variable substitutes the member set on
every page. A figure saved grouped by dose and restored ungrouped comes back with
different members, different means and a different n, under the same title and
with nothing on it saying so. That is §7's forbidden substitution reached through
a gap in persistence rather than through a bug in the plot, and it applies to
Saved Plots and to an ordinary reopen alike, since both replay this snapshot.

Driven through the real ``snapshot`` → ``restore`` round-trip on a loaded app,
because the seam under test *is* that pair: a test of either half alone cannot
see a key that one writes and the other ignores.

The three settings are given three **different** ids on purpose. A restore that
read one key and copied it into all three fields would satisfy any assertion
that only checked "grouping came back", and this one names each field's own
value.
"""
from __future__ import annotations

import pytest

from review.persistence import view_state

pytestmark = pytest.mark.qt

VALUES = {
    "group_by_variable": "var-dose",
    "colour_by_variable": "var-cell-line",
    "split_by_variable": "var-timepoint",
}


def test_the_three_band_variables_round_trip(loaded_app):
    app = loaded_app
    plot = app._app_state.plot

    for field, value in VALUES.items():
        setattr(plot, field, value)
    snap = view_state.snapshot(app._ctx)

    for field, value in VALUES.items():
        assert snap[field] == value, field

    # A fresh open starts with none of them set.
    for field in VALUES:
        setattr(plot, field, "")
    view_state.restore(app._ctx, snap)

    for field, value in VALUES.items():
        assert getattr(plot, field) == value, field


def test_no_grouping_round_trips_as_no_grouping(loaded_app):
    """The other direction, and not a formality: a restore that only ever
    *assigned* a non-empty value would leave a stale grouping in place when the
    saved figure had none — the same wrong-members defect with the sign
    flipped."""
    app = loaded_app
    plot = app._app_state.plot

    for field in VALUES:
        setattr(plot, field, "")
    snap = view_state.snapshot(app._ctx)

    for field, value in VALUES.items():
        setattr(plot, field, value)
    view_state.restore(app._ctx, snap)

    for field in VALUES:
        assert getattr(plot, field) == "", field
