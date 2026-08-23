"""The events-above-threshold panel picks integer ticks unless the bar is a mean.

``render_bar_items`` carried a comment saying it defaults to integer ticks
because N is a count of events, and then passed ``MaxNLocator(integer=False)``
unconditionally, so a count of 3 events could be labelled 3.0 and a panel of
small counts could pick 0.5 steps. ``draw_per_well_n_bars`` in the renderer —
the other path that draws the same panel — already decided this correctly with
``integer=not per_fov_spread``. The two disagreeing meant the same value read
differently depending on which path drew it.

Aggregate-FOVs mode is the one case where the bar is a per-FOV *mean* rather
than a count, and fractional ticks are right there.
"""

from __future__ import annotations

import pytest

pytest.importorskip("matplotlib")

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

from well_viewer.features.barplot.collect import BarItem  # noqa: E402
from well_viewer.features.barplot.controller import render_bar_items  # noqa: E402


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


def test_the_two_paths_that_draw_this_panel_agree():
    """``draw_per_well_n_bars`` keys the same decision off the same fact."""
    import inspect

    from well_viewer.features.barplot import renderer

    src = inspect.getsource(renderer.draw_per_well_n_bars)
    assert "MaxNLocator(integer=not per_fov_spread)" in src, (
        "the renderer's own N panel stopped keying integer ticks off "
        "per-FOV mode; render_bar_items still does, so the two now disagree"
    )
