"""The Heat Map keeps square cells only where they fill their panel.

Reported by the page-render sweep: held upright, a phone drew a 12 x 8 plate as
a strip a third of the panel's height, centred in empty space. Square cells are
right at the desk; where they would leave most of the panel empty the grid
stretches to it.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

from matplotlib.figure import Figure

from review.features.heatmap.controller import _grid_aspect


def _panel(width_in: float, height_in: float):
    fig = Figure(figsize=(width_in, height_in))
    ax = fig.add_subplot(111)
    fig.subplots_adjust(top=0.88, bottom=0.10, left=0.10, right=0.94)
    return fig, ax


def test_a_wide_panel_keeps_square_cells():
    fig, ax = _panel(8.0, 6.0)               # the desk's own figure
    assert _grid_aspect(fig, ax, rows=8, cols=12) == "equal"


def test_a_tall_narrow_panel_stretches_the_grid_to_it():
    fig, ax = _panel(4.06, 7.96)             # a phone held upright
    assert _grid_aspect(fig, ax, rows=8, cols=12) == "auto"


def test_a_grid_that_is_itself_tall_stays_square_in_a_tall_panel():
    fig, ax = _panel(4.06, 7.96)
    assert _grid_aspect(fig, ax, rows=16, cols=6) == "equal"


def test_an_empty_grid_is_left_square():
    fig, ax = _panel(4.06, 7.96)
    assert _grid_aspect(fig, ax, rows=0, cols=12) == "equal"
