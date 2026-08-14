"""A control with no sample at the drawn timepoint must not crash the collector.

The grouped branch resolved the cross-axis stacking *before* it applied
``fc_force_nan``, so when both fold-change axes were on and the control had no
sample at the timepoint being drawn, it reached ``_apply_axis`` with a ``None``
denominator and raised ``TypeError: unsupported operand type(s) for /:
'NoneType' and 'float'``. The per-well branch has always guarded this with an
``if fc_force_nan: … elif …``; only the grouped branch was exposed.

Pre-dates the per-sample-controls work — reproducible on origin/main — but it
is squarely in the way of it, because a per-member mapping makes "some control
is missing here" the ordinary case rather than the exceptional one.

``fc_force_nan`` only ever tracked the *mean* denominator, so the fraction and
count denominators are guarded on themselves too: in ``t0_first`` order each is
stacked separately and any one of them can be ``None`` while the mean resolved.
"""
from __future__ import annotations

import math

import pytest

from well_viewer.features.barplot.collect import collect_bar_items

from tests.conftest import MockApp, make_agg


#: Legacy 3-tuple rather than the typed request object, so this file also runs
#: against a base branch that predates that type (CLAUDE.md §7.4 #4).
FC_BOTH_AXES = (True, "CTRL", True)


@pytest.fixture
def sparse_control_app():
    """CTRL is sampled only at t=0; TREAT reaches t=1. Drawing t=1 leaves the
    control unresolvable at exactly the timepoint being plotted."""
    app = MockApp(
        per_well_pts={
            "C01": [make_agg(0.0, 100.0)],
            "C02": [make_agg(0.0, 100.0)],
            "T01": [make_agg(0.0, 200.0), make_agg(1.0, 400.0)],
            "T02": [make_agg(0.0, 200.0), make_agg(1.0, 400.0)],
        },
        selections=[
            {"name": "CTRL", "wells": ["C01", "C02"], "hidden": False},
            {"name": "TREAT", "wells": ["T01", "T02"], "hidden": False},
        ],
    )
    ctx = getattr(app, "_ctx", app)
    ctx.state.plot.bar_pool_replicates = True
    return ctx


@pytest.mark.parametrize("order", ["t0_first", "ctrl_first"])
def test_a_control_missing_at_the_drawn_timepoint_does_not_crash(
        sparse_control_app, order):
    """The regression. ``t0_first`` raised TypeError; ``ctrl_first`` did not."""
    sparse_control_app.state.plot.fc_order = order
    _ug, items, _b = collect_bar_items(
        sparse_control_app, 1.0, fc_state=FC_BOTH_AXES)
    assert items, "the collector returned nothing at all"


@pytest.mark.parametrize("order", ["t0_first", "ctrl_first"])
def test_the_bars_become_placeholders_rather_than_raw_values(
        sparse_control_app, order):
    """Not crashing is not enough — the shortfall has to be visible.

    A raw value drawn under a fold-change axis is the failure this codebase
    rules out; ``has_mean=False`` is what makes the renderer draw the dashed
    placeholder instead.
    """
    sparse_control_app.state.plot.fc_order = order
    _ug, items, _b = collect_bar_items(
        sparse_control_app, 1.0, fc_state=FC_BOTH_AXES)
    for it in items:
        assert it.has_mean is False, f"{it.key} drew a real bar"
        assert math.isnan(it.mean), f"{it.key} drew {it.mean}"


@pytest.mark.parametrize("order", ["t0_first", "ctrl_first"])
def test_the_missing_timepoint_is_reported(sparse_control_app, order):
    """The status line names the timepoint, so the empty panel is explained."""
    sparse_control_app.state.plot.fc_order = order
    miss: set = set()
    collect_bar_items(sparse_control_app, 1.0,
                      fc_state=FC_BOTH_AXES, miss_sink=miss)
    assert miss == {1.0}


@pytest.mark.parametrize("order", ["t0_first", "ctrl_first"])
def test_a_timepoint_the_control_does_have_still_normalizes(
        sparse_control_app, order):
    """The guard must not suppress the timepoints that are fine.

    At t=0 the control resolves, so TREAT is 200/100 on the pre-baseline axis;
    the assertion is only that a real bar is drawn, since the exact value
    depends on how the two axes stack.
    """
    sparse_control_app.state.plot.fc_order = order
    _ug, items, _b = collect_bar_items(
        sparse_control_app, 0.0, fc_state=FC_BOTH_AXES)
    by_key = {it.key: it for it in items}
    assert by_key["TREAT"].has_mean is True
    assert math.isfinite(by_key["TREAT"].mean)
