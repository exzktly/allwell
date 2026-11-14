"""A log(x+1) axis in a base just above 1 is ticked at a readable number of powers.

Found in review: the scatter's log(x+1) axis placed a fixed tick at every power of the user's base up to ten million.
The base field takes any number above 1, so base 1.01 built 1,621 ticks and took 4.8 s per draw, and 1.001 built
16,128 and took 50 s — on every redraw of the per-cell scatter, the nanowell scatter and Batch Export's scatter
panels. The powers are now thinned to a bounded count, keeping the bases a user picks from the list (2, 3, e, 10)
ticked at every power as before.

The wrong implementation this rejects is the old one: every power, whatever the base — it fails the count.
"""

from __future__ import annotations

import math

import pytest


def _major_ticks(base: float) -> list[float]:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    from review.core.plot_style import apply_axis_log_scale

    ax = Figure().add_subplot()
    ax._aw_log_plus1 = True
    apply_axis_log_scale(ax, "x", True, base)
    return list(ax.xaxis.get_major_locator().locs)


@pytest.mark.parametrize("base", [1.5, 1.01, 1.001])
def test_a_base_near_one_gets_a_bounded_number_of_ticks(base):
    ticks = _major_ticks(base)
    assert len(ticks) <= 25, f"base {base}: {len(ticks)} ticks"
    # Still reaching the axis's full range, so a large value is not left past the last tick.
    assert max(ticks) >= 1_000_000, f"base {base}: highest tick {max(ticks):g}"


@pytest.mark.parametrize("base, power", [(2.0, 8.0), (3.0, 27.0), (math.e, math.e ** 3), (10.0, 1000.0)])
def test_the_listed_bases_keep_every_power(base, power):
    ticks = _major_ticks(base)
    assert any(math.isclose(t, power) for t in ticks), f"base {base}: no tick at {power:g}"
