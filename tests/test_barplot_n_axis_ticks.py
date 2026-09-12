"""The events-above-threshold panel picks integer ticks only for a plain count.

``render_bar_items`` carried a comment saying it defaults to integer ticks
because N is a count of events, and then passed ``MaxNLocator(integer=False)``
unconditionally, so a count of 3 events could be labelled 3.0 and a panel of
small counts could pick 0.5 steps. ``draw_per_well_n_bars`` in the renderer —
the other path that draws the same panel — already decided this correctly with
``integer=not per_fov_spread``. The two disagreeing meant the same value read
differently depending on which path drew it.

Two things make the bar something other than a count, and both have to be
asked: Aggregate-FOVs mode, where it is a per-FOV *mean* of counts, and fold
change, where it is a ratio or a difference of them. The first version of this
fix copied ``draw_per_well_n_bars``' rule wholesale — which asks only about
per-FOV mode, correctly, because nothing it draws is fold-changed — and so
rounded a fold change of 1.35 to ticks of 0, 1, 2.
"""

from __future__ import annotations

import pytest

pytest.importorskip("matplotlib")

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

from review.features.barplot.collect import BarItem  # noqa: E402
from review.features.barplot.controller import render_bar_items  # noqa: E402


def _items():
    return [
        BarItem(key="A01", display="A01", color="#4488cc", mean=1.0, spread=0.1,
                has_mean=True, frac=0.5, frac_spread=0.05, has_frac=True,
                n_above=3.0, n_above_spread=0.0),
        BarItem(key="A02", display="A02", color="#cc8844", mean=2.0, spread=0.2,
                has_mean=True, frac=0.7, frac_spread=0.05, has_frac=True,
                n_above=5.0, n_above_spread=0.0),
    ]


def _render(**kwargs):
    fig, (ax_mean, ax_frac, ax_n) = plt.subplots(3, 1)
    try:
        render_bar_items(
            ax_mean=ax_mean, ax_frac=ax_frac, ax_n=ax_n,
            use_groups=False, items=_items(), threshold=0.5,
            warn_color="#cc4444", border_color="#333333",
            placeholder_color="#888888", disabled_well_color="#666666",
            err_bar_color="#222222", **kwargs,
        )
        loc = ax_n.yaxis.get_major_locator()
        assert isinstance(loc, MaxNLocator), loc
        return loc
    finally:
        plt.close(fig)


def test_a_count_of_events_gets_integer_ticks():
    loc = _render(n_axis_integer=True)
    assert loc._integer is True


def test_a_per_fov_mean_gets_fractional_ticks():
    loc = _render(n_axis_integer=False)
    assert loc._integer is False


def test_the_default_with_no_context_is_a_count():
    """``ctx=None`` (the export panels' call shape) must not silently go float."""
    loc = _render()
    assert loc._integer is True


def test_a_fold_changed_count_gets_fractional_ticks():
    """The second fact. A fold change is a ratio or a difference of counts, so
    integer ticks round it away: 1.35 came back as ticks of 0, 1, 2."""
    loc = _render(fc_active=True)
    assert loc._integer is False


def test_fold_change_overrides_an_explicit_integer_request():
    """The batch panel passes ``n_axis_integer`` from the FOV toggle alone, so
    the fold-change half has to be applied after the caller's choice."""
    loc = _render(n_axis_integer=True, fc_active=True)
    assert loc._integer is False


def test_the_other_path_asks_only_about_per_fov_mode():
    """``draw_per_well_n_bars`` is right to ask one question where this one asks
    two: its own docstring records that nothing it draws is fold-changed, and
    it is never handed an ``fc_active``. An earlier version of this file
    asserted the two rules were identical, which is false — the panels draw
    different quantities."""
    import inspect

    from review.features.barplot import renderer

    src = inspect.getsource(renderer.draw_per_well_n_bars)
    assert "MaxNLocator(integer=not per_fov_spread)" in src
    assert "fc_active" not in src, (
        "draw_per_well_n_bars now sees fold-change state, so its tick rule has "
        "to grow the second question too"
    )
