"""A divider-placed colour bar's text stays inside the figure (``review.core.margin_fit``).

Reported by the page-render sweep: on a phone the Heat Map's colour bar label ran
off the right edge. The Heat Map's grid and bar are placed by an ``axes_grid1``
divider, which ignores constrained layout, so nothing made the margin the bar's
text needs.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from mpl_toolkits.axes_grid1 import make_axes_locatable

from review.core import margin_fit, plot_style


def _heat_figure():
    fig = Figure(figsize=(4.06, 7.96), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    fig.subplots_adjust(top=0.88, bottom=0.10, left=0.10, right=0.94)
    cax = make_axes_locatable(ax).append_axes("right", size="4%", pad=0.08)
    im = ax.imshow(np.arange(96.0).reshape(8, 12), aspect="equal")
    fig.colorbar(im, cax=cax).set_label("GFP Mean Intensity (mean above threshold)", fontsize=12)
    return fig, ax, cax


def _overhang(fig) -> float:
    renderer = fig.canvas.get_renderer()
    return max(max(0.0, ax.get_tightbbox(renderer).x1 - fig.bbox.width) for ax in fig.axes)


def test_text_hanging_off_the_right_edge_is_brought_inside():
    fig, ax, cax = _heat_figure()
    assert _overhang(fig) > 0, "the fixture must overhang to prove anything"
    margin_fit.inset_for_overhang(fig)
    assert _overhang(fig) <= 0.5
    # The divider's bar went with its grid.
    assert cax.get_tightbbox(fig.canvas.get_renderer()).x1 <= fig.bbox.width


def test_restoring_gives_the_renderers_margins_back():
    fig, ax, _cax = _heat_figure()
    before = ax.get_position(original=True).bounds
    margin_fit.inset_for_overhang(fig)
    assert ax.get_position(original=True).bounds != before
    margin_fit.restore(fig)
    assert ax.get_position(original=True).bounds == before


def test_a_second_fit_does_not_inset_twice():
    fig, ax, _cax = _heat_figure()
    margin_fit.inset_for_overhang(fig)
    once = ax.get_position(original=True).bounds
    margin_fit.inset_for_overhang(fig)
    assert ax.get_position(original=True).bounds == once


def test_a_figure_with_nothing_overhanging_is_left_alone():
    fig = Figure(figsize=(8, 6), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    before = ax.get_position(original=True).bounds
    margin_fit.inset_for_overhang(fig)
    assert ax.get_position(original=True).bounds == before


def test_a_figure_that_declares_tight_layout_keeps_it_through_the_style_pass():
    """The Spatial grid is laid out by the engine at every draw: a one-shot
    ``tight_layout`` from the renderer's own text sizes was stale once the
    Properties pass enlarged them, and each panel's x-label landed on the title
    of the panel below."""
    fig = Figure(figsize=(6, 6), dpi=100)
    FigureCanvasAgg(fig)
    fig.add_subplot(111)
    fig._aw_tight_layout = True
    plot_style._apply_layout_engine(fig, {})
    assert type(fig.get_layout_engine()).__name__ == "TightLayoutEngine"
    fig._aw_tight_layout = False
    plot_style._apply_layout_engine(fig, {})
    assert fig.get_layout_engine() is None


def test_text_hanging_off_the_top_edge_is_brought_inside_too():
    """Held sideways the Heat Map's top column ticks and title are taller than the
    margin the renderer left above the grid."""
    fig = Figure(figsize=(9.1, 4.3), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    fig.subplots_adjust(top=0.88, bottom=0.10, left=0.10, right=0.94)
    make_axes_locatable(ax).append_axes("right", size="4%", pad=0.08)
    ax.imshow(np.arange(96.0).reshape(8, 12), aspect="equal")
    ax.set_xticks(range(12))
    ax.xaxis.tick_top()
    ax.tick_params(labelsize=22)
    ax.set_title("GFP — Mean above threshold — t = 0 h", fontsize=22)
    renderer = fig.canvas.get_renderer()
    assert ax.get_tightbbox(renderer).y1 > fig.bbox.height, "the fixture must overhang to prove anything"
    margin_fit.inset_for_overhang(fig)
    assert ax.get_tightbbox(renderer).y1 <= fig.bbox.height + 0.5


def test_only_a_divider_counts_as_placing_axes_not_a_colour_bar():
    """Every ``fig.colorbar`` axes carries matplotlib's own locator; treating that
    as a divider took constrained layout away from every plot with a colour bar."""
    fig, _ax, _cax = _heat_figure()
    assert margin_fit.divider_placed(fig)
    plain = Figure(figsize=(4, 4))
    FigureCanvasAgg(plain)
    pax = plain.add_subplot(111)
    plain.colorbar(pax.imshow(np.arange(4.0).reshape(2, 2)), ax=pax)
    assert not margin_fit.divider_placed(plain)


def test_tick_labels_are_fitted_again_after_the_inset_narrows_the_grid():
    """The page-render sweep, nanowell on a phone upright: the Heat Map's twelve column numbers were fitted to the
    grid's width, the inset for the colour bar's label then narrowed the grid, and "9 10 11 12" ran together.
    At 21 pt on this fixture the labels clear the grid before the inset and collide after it."""
    from review.core.tick_fit import _collide, _drawn

    fig, ax, _cax = _heat_figure()
    ax.set_xticks(range(12))
    ax.set_xticklabels([str(i + 1) for i in range(12)], fontsize=21)
    fig._aw_margin_fitted = True
    plot_style._fit_ticks(fig)
    renderer = fig.canvas.get_renderer()
    assert not _collide(_drawn(ax.xaxis, renderer, ax.get_xlim()), renderer, fig), (
        "the column numbers overlap once the grid is inset: they were fitted to the grid before the inset")
