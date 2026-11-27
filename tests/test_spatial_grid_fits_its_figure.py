"""The Spatial page's grid of wells follows the figure's shape (``spatial.renderer._grid_shape``).

A square grid on a phone held upright drew four wells two abreast in a strip too narrow for them, and the right
column's axis labels ran past the figure's edge (every-page tour, tracker row S31).
"""

from __future__ import annotations

from review.features.spatial.renderer import _grid_shape


def test_a_tall_narrow_figure_stacks_the_wells():
    assert _grid_shape(4, aspect=406 / 700, width_in=4.06) == (4, 1)
    assert _grid_shape(4, aspect=406 / 700) == (4, 1)      # taller than wide: one column whatever its inches


def test_a_square_figure_keeps_a_square_grid():
    assert _grid_shape(4, aspect=1.0) == (2, 2)
    assert _grid_shape(9) == (3, 3)


def test_a_wide_figure_lays_the_wells_out_in_a_row():
    assert _grid_shape(3, aspect=3.0) == (1, 3)
    assert _grid_shape(1, aspect=0.3) == (1, 1)


def test_the_grid_follows_the_figure_when_it_is_resized_after_rendering():
    import matplotlib

    matplotlib.use("agg")
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from review.features.spatial import renderer

    fig = Figure(figsize=(8.0, 6.0), dpi=100)
    FigureCanvasAgg(fig)
    axes = [fig.add_subplot(2, 2, i + 1) for i in range(4)]
    fig._aw_spatial_axes, fig._aw_spatial_shape = axes, (2, 2)
    renderer._regrid_as_drawn(fig)
    fig.set_size_inches(4.0, 7.0)                   # a phone held upright, after the render
    fig.canvas.draw()
    assert fig._aw_spatial_shape == (4, 1)
    assert [ax.get_subplotspec().colspan.start for ax in axes] == [0, 0, 0, 0]
