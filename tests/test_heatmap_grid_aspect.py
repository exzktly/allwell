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


# ── the cells' text fits the cell it is in ───────────────────────────────────

import numpy as np                                                  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg         # noqa: E402

from review.features.heatmap.controller import fit_to_panel         # noqa: E402


def _grid(width_in: float, height_in: float, cols: int = 12, rows: int = 8):
    fig = Figure(figsize=(width_in, height_in), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    fig.subplots_adjust(top=0.88, bottom=0.10, left=0.10, right=0.94)
    ax.imshow(np.full((rows, cols), 3000.0), aspect="equal")
    for c in range(cols):
        ax.text(c, 0, "3000", ha="center", va="center", fontsize=8)
        ax.text(c, 0.32, "A01", ha="center", va="center", fontsize=6)
    return fig, ax


def _row_overlap(fig, ax) -> bool:
    renderer = fig.canvas.get_renderer()
    ax.apply_aspect()
    boxes = [t.get_window_extent(renderer) for t in ax.texts if t.get_text() == "3000"]
    return any(a.x1 - b.x0 > 1.0 for a, b in zip(boxes, boxes[1:]))


def test_cell_text_wider_than_its_cell_is_shrunk_until_it_fits():
    """'3000' in neighbouring cells twenty pixels wide ran together on a phone
    (the images dataset's heat map)."""
    fig, ax = _grid(2.6, 5.0)
    assert _row_overlap(fig, ax), "the fixture must overlap to prove anything"
    fit_to_panel(fig, ax)
    assert not _row_overlap(fig, ax)
    sizes = {t.get_text(): t.get_fontsize() for t in ax.texts}
    assert sizes["3000"] < 8.0 and sizes["A01"] < 6.0
    # Together: the name line keeps its proportion to the value line.
    assert abs(sizes["A01"] / sizes["3000"] - 6.0 / 8.0) < 1e-6


def test_cell_text_that_already_fits_keeps_its_size():
    fig, ax = _grid(12.0, 6.0)
    fit_to_panel(fig, ax)
    assert {t.get_text(): t.get_fontsize() for t in ax.texts} == {"3000": 8.0, "A01": 6.0}


def test_a_grid_that_gets_more_room_gets_its_text_size_back():
    fig, ax = _grid(2.6, 5.0)
    fit_to_panel(fig, ax)
    shrunk = ax.texts[0].get_fontsize()
    fig.set_size_inches(12.0, 6.0)
    fit_to_panel(fig, ax)
    assert shrunk < 8.0 and ax.texts[0].get_fontsize() == 8.0


def test_fit_decides_the_cell_shape_from_the_panel_as_it_now_is():
    """The grid was drawn when the figure was desk-sized; the aspect must follow
    the size it ended up."""
    fig, ax = _grid(8.0, 6.0)
    fit_to_panel(fig, ax)
    assert ax.get_aspect() == 1.0
    fig.set_size_inches(4.06, 7.96)
    fit_to_panel(fig, ax)
    assert ax.get_aspect() == "auto"
