"""A desk plot's title that would run past the figure's edge is fitted; one that fits is left alone.

The per-cell Scatter's title — "Scatter: GFP Mean Intensity vs MCHERRY Mean
Intensity (t=0h–24h, 2 tps) · highlighting …" — was cut off at the figure's
right edge at the desk, losing the timepoints and then the highlighted set.
Only phone layout fitted titles. ``plot_style.fit_clipped_titles`` now fits, at
the desk, exactly the titles the figure cannot hold.
"""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402

from review.core.plot_style import fit_clipped_titles  # noqa: E402

LONG = ("Scatter: GFP Mean Intensity vs MCHERRY Mean Intensity (t=0h–24h, 2 tps) "
        "· highlighting Rounded cells")


def _fig(title, width_in=6.0):
    fig = Figure(figsize=(width_in, 4), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_title(title, fontsize=14)
    return fig, ax


def _inside(fig, ax):
    fig.canvas.draw()
    box = ax.title.get_window_extent(fig.canvas.get_renderer())
    return box.x0 >= 0 and box.x1 <= fig.bbox.width


def test_a_title_the_figure_cannot_hold_is_fitted_inside_it():
    fig, ax = _fig(LONG)
    assert not _inside(fig, ax)                     # the defect, measured
    fit_clipped_titles(fig)
    assert _inside(fig, ax)
    assert ax.title.get_text().replace("\n", " ") == LONG   # every word kept


def test_a_title_that_fits_the_figure_is_left_as_written_even_wider_than_its_axes():
    fig, ax = _fig("A title wider than its narrow axes", width_in=6.0)
    ax.set_position([0.45, 0.1, 0.1, 0.8])          # narrow axes, centred
    before = (ax.title.get_text(), ax.title.get_fontsize())
    fit_clipped_titles(fig)
    assert (ax.title.get_text(), ax.title.get_fontsize()) == before


def _wholly_inside(fig, ax):
    fig.canvas.draw()
    box = ax.title.get_window_extent(fig.canvas.get_renderer())
    return box.x0 >= 0 and box.x1 <= fig.bbox.width and box.y1 <= fig.bbox.height


def test_a_broken_title_under_a_one_line_top_margin_is_not_pushed_off_the_top():
    """Found in review: breaking a title onto two lines grows it upward, and a
    figure whose top margin was set for one line (``top=0.96`` — the Bar
    figure's) then lost its first line past the top. The fit must leave the
    whole title inside the figure; breaking alone (the old fit) fails this."""
    fig = Figure(figsize=(6.0, 11.0), dpi=100)
    FigureCanvasAgg(fig)
    axes = [fig.add_subplot(3, 1, i) for i in (1, 2, 3)]
    fig.subplots_adjust(top=0.96)
    axes[0].set_title("GFP Mean Intensity (above threshold) ± SEM  —  t = 0 h", fontsize=18)
    fit_clipped_titles(fig)
    assert _wholly_inside(fig, axes[0]), axes[0].title.get_window_extent(fig.canvas.get_renderer())
    assert axes[0].title.get_text().replace("\n", " ").split() == \
        "GFP Mean Intensity (above threshold) ± SEM — t = 0 h".split()


def test_the_bar_tab_title_stays_inside_at_a_six_inch_canvas():
    """The same, through the Bar tab's renderer and its own style step."""
    from tests.test_bar_two_stage_ticks import _ctx, _drawn

    ctx = _ctx(relative=False)
    ctx.state.plot.fc_vs_control_on = False
    fig = _drawn(ctx, 6.0)
    assert _wholly_inside(fig, fig.axes[0]), fig.axes[0].title.get_window_extent(fig.canvas.get_renderer())


def test_a_refit_after_a_widening_restores_the_one_line_title():
    fig, ax = _fig(LONG)
    fit_clipped_titles(fig)
    assert "\n" in ax.title.get_text()
    fig.set_size_inches(14, 4)
    fit_clipped_titles(fig)
    assert ax.title.get_text() == LONG
