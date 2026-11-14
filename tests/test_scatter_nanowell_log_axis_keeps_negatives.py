"""A log(x+1) axis on the nanowell scatter never hides a value below zero.

Found in review: the Observations: Nanowells scatter on the Cells page is drawn on log(x+1) axes unless the user turns
them off, and its axis menus offer signed metrics — ``log2_count_fc`` is negative for every nanowell whose count
fell. The transform clips a negative value to zero, so every shrinking nanowell was stacked on the zero line with
nothing on the figure saying so, and a gate drawn over that corner stored a bound that left them out.

So an axis whose drawn values include anything below zero is drawn linear, its label drops "log", and the footer
says why. Driven through the real Scatter builder on the nanowell fixture (CLAUDE.md §7.8 #3), asserting what the axes
draw (§7.4 #1). The wrong implementation this rejects — log(x+1) on whatever is drawn — draws ``"function"``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from tests.test_scatter_nanowells_obeys_properties import scatter  # noqa: E402,F401  (fixture)


def _choose(ctx, combo_name: str, key: str) -> None:
    combo = ctx.widgets.get(combo_name)
    index = next(i for i in range(combo.count()) if combo.itemData(i) == key)
    combo.setCurrentIndex(index)


def _shrink_some(monkeypatch):
    """The fixture's plate only grows; give the drawn fold changes the shrinking nanowells a real plate has, by
    moving every x down by one, so the values below zero are the drawn ones and nothing else changes."""
    from review.features.scatter import _observations
    from review.features.scatter.observation_compute import nanowell_scatter

    def shifted(*args, **kwargs):
        drawn = nanowell_scatter(*args, **kwargs)
        for series in drawn.series:
            series.x = series.x - 1.0
        return drawn

    monkeypatch.setattr(_observations, "nanowell_scatter", shifted)


def test_an_axis_with_values_below_zero_is_drawn_linear_and_says_so(scatter, monkeypatch):  # noqa: F811
    import numpy as np

    from review.features.scatter._observations import _combo_names
    from review.features.scatter.controller import redraw_scatter_orchestrate

    ctx, _window = scatter
    _shrink_some(monkeypatch)
    redraw_scatter_orchestrate(ctx)
    x_name, y_name = _combo_names("cells")
    _choose(ctx, x_name, "log2_count_fc")
    redraw_scatter_orchestrate(ctx)
    ax = ctx.widgets.get("_ax_scatter")
    xs = np.concatenate([np.asarray(c.get_offsets())[:, 0] for c in ax.collections if len(c.get_offsets())])
    assert (xs < 0).any(), "fixture: the fold change must include a shrinking nanowell for this test to measure"
    assert ax.get_xscale() == "linear", f"x drawn {ax.get_xscale()} with {int((xs < 0).sum())} values below 0"
    assert "(log" not in ax.get_xlabel(), ax.get_xlabel()
    footer = " ".join(t.get_text() for t in ax.figure.texts)
    assert f"X axis drawn linear, not log(x+1): {int((xs < 0).sum())} value(s) below 0" in footer
    # The other axis, whose values are all non-negative, keeps the log(x+1) the user asked for.
    assert ax.get_yscale() == "function"
