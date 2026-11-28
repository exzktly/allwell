"""The Nanowells figures keep their words inside a narrow or short figure (the surfaces work).

The every-page tour measured the page's figures on six screens and found sentences running past a phone's figure,
keys starting left of it, titles over its top, tick labels running together and the Extremes montage drawn off a
short figure's foot — until it recursed without end on one. Each fix is pinned here on a bare matplotlib figure of a
phone's size, with no app: the tour itself is the slow lane's (``tests/test_every_page_renders.py``).
"""

from __future__ import annotations

import matplotlib

matplotlib.use("agg")

from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from review.core import figure_fit  # noqa: E402
from review.features.nanowell import renderer  # noqa: E402

#: A sentence as long as the Identity view's, with a path that has no spaces in it.
LONG = ("A01 at t_c: the nanowell identity check has not been run on these tables. Run it in a terminal with: "
        "python -m pipeline.nanowell_identity /tmp/pages-nanowell-phone-upright-v49j8qgc/nanowell/ds/out")


def _figure(w_in: float, h_in: float) -> Figure:
    fig = Figure(figsize=(w_in, h_in), dpi=100)
    FigureCanvasAgg(fig)
    return fig


def _inside(fig, text) -> bool:
    fig.canvas.draw()
    box = text.get_window_extent(fig.canvas.get_renderer())
    return box.x0 >= -1.5 and box.x1 <= fig.bbox.width + 1.5 and box.y0 >= -1.5 and box.y1 <= fig.bbox.height + 1.5


def test_a_message_is_broken_to_the_figure_it_is_shown_at_not_the_one_it_was_drawn_at():
    fig = _figure(8.0, 4.0)
    renderer.render_message(fig, LONG, gid="m")
    text = fig.axes[0].texts[0]
    assert _inside(fig, text)
    fig.set_size_inches(3.0, 5.0)                  # the card narrowed after the draw: a phone's sheet opening
    assert _inside(fig, text)
    assert " ".join(text.get_text().split()).replace(" ", "") == LONG.replace(" ", "")      # every word kept


def test_a_fitted_text_is_made_smaller_before_it_is_broken():
    # A figure whose room (92% of its width) is 80% of the text's width at 12 pt, measured in whatever face this
    # machine draws in: a fixed width fitted the app's font and was too narrow for the CI runner's wider DejaVu Sans.
    probe = _figure(10.0, 2.0)
    wide = probe.text(0, 0, "Cells at baseline (earliest):", fontsize=12)
    probe.canvas.draw()
    needed_px = wide.get_window_extent(probe.canvas.get_renderer()).width
    fig = _figure(0.8 * needed_px / 0.92 / 100.0, 2.0)
    ax = fig.add_subplot(1, 1, 1)
    item = renderer._fitted_text_class()(0.5, 0.5, "Cells at baseline (earliest):", ha="center", fontsize=12,
                                         transform=ax.transAxes, smallest=0.5)
    ax._add_text(item)
    fig.canvas.draw()
    assert "\n" not in item.get_text() and item.get_fontsize() < 12
    assert _inside(fig, item)


def test_crowded_tick_labels_are_thinned_and_ones_hidden_on_purpose_are_left_alone():
    fig = _figure(2.0, 2.0)
    left, right = fig.add_subplot(1, 2, 1), fig.add_subplot(1, 2, 2)
    left.set_xlim(0, 20)
    left.set_xticks(range(21))
    right.set_xlim(0, 2)
    right.set_xticks([0, 2])                       # two labels far apart: nothing to thin
    fig.canvas.draw()
    hidden = right.xaxis.get_major_ticks()[1].label1
    hidden.set_visible(False)                      # a renderer's own choice: not the thinning's to undo
    assert figure_fit.thin_crowded_ticks(fig, fig.canvas.get_renderer())
    renderer_ = fig.canvas.get_renderer()
    shown = [lb.get_window_extent(renderer_) for lb in left.xaxis.get_majorticklabels() if lb.get_visible()]
    assert 1 < len(shown) < 21
    assert all(a.x1 <= b.x0 + 1.0 for a, b in zip(shown, shown[1:]))
    assert not hidden.get_visible()
    assert not figure_fit.thin_crowded_ticks(fig, renderer_), "a second pass changed what the first settled"
    # Wider, the labels thinned come back.
    fig.set_size_inches(14.0, 2.0)
    fig.canvas.draw()
    figure_fit.thin_crowded_ticks(fig, fig.canvas.get_renderer())
    assert all(t.label1.get_visible() for t in left.xaxis.get_major_ticks() if 0 <= t.get_loc() <= 20)


def test_the_extremes_montage_on_a_very_short_figure_draws_without_recursing():
    from types import SimpleNamespace

    row = SimpleNamespace(site=("A01", 0, 0), rank=1, value=2.0, values={"t_a": 1.0, "t_c": 2.0})
    model = SimpleNamespace(timepoint="t_c", timepoints=["t_a", "t_c"], column="count_fc", format=None)
    fig = _figure(3.0, 1.0)                        # its caption wraps to more lines than the figure has room for
    crops = {("A01", 0, 0): {"t_a": SimpleNamespace(image=None, note="The grid record has no montage", source="",
                                                     cells=None, outlines=None)}}
    renderer.render_site_rows(fig, model, [("Largest 1", [row])], crops, label="Count fold change", empty="none",
                              title="A01 · the nanowells with the largest count fold change", head="Ranked")
    fig.canvas.draw()
    assert fig.axes, "the montage drew nothing"


def test_the_position_view_on_a_phone_keeps_its_key_inside_and_its_labels_on_the_figure():
    import numpy as np

    series = [SimpleNamespaceSeries(w) for w in ("A01", "A02", "B01")]
    fig = _figure(4.06, 6.0)
    renderer.render_position(fig, series, label="Cell count", timepoint="t_a")
    fig.canvas.draw()
    texts = [t for ax in fig.axes for t in (ax.title, ax._left_title, ax.xaxis.label, ax.yaxis.label)
             if t.get_text().strip()]
    texts += [t for ax in fig.axes if ax.get_legend() for t in ax.get_legend().get_texts()]
    assert texts and all(_inside(fig, t) for t in texts)
    # The key inside the plot: the plot keeps most of the width (its key outside on the right took over half of it,
    # and the phone's layout then drew the plot a sliver).
    assert np.isfinite(fig.axes[0].get_position().width) and fig.axes[0].get_position().width > 0.7
    legend = fig.axes[0].get_legend()
    box = legend.get_window_extent() if legend is not None else None
    assert box is not None and fig.axes[0].bbox.contains((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)


class SimpleNamespaceSeries:
    """One well's Position series, as ``position_compute`` gives it, with made-up numbers."""

    def __init__(self, well: str):
        import numpy as np

        self.well, self.name = well, ""
        self.rings = np.array([0, 1, 2, 3])
        self.median = np.array([2.0, 4.0, 3.0, 1.0])
        self.q1, self.q3 = self.median - 0.5, self.median + 0.5
        self.excluded_share = np.array([0.0, 0.5, 0.0, 0.0])


def test_crowded_labels_on_the_right_are_thinned_too():
    """A colour bar writes its numbers on its right (each tick's second label): reading the first alone left them
    running together on a phone on its side."""
    fig = _figure(2.0, 0.8)
    bar = fig.add_axes((0.4, 0.1, 0.1, 0.8))
    bar.yaxis.tick_right()
    bar.set_ylim(0, 30)
    bar.set_yticks(range(31))
    fig.canvas.draw()
    figure_fit.thin_crowded_ticks(fig, fig.canvas.get_renderer())
    r = fig.canvas.get_renderer()
    shown = sorted((t.label2.get_window_extent(r) for t in bar.yaxis.get_major_ticks() if t.label2.get_visible()),
                   key=lambda b: b.y0)
    assert 1 < len(shown) < 31
    assert all(a.y1 <= b.y0 + 1.0 for a, b in zip(shown, shown[1:]))


def test_a_y_label_left_of_the_figure_moves_the_margin_until_it_is_on():
    fig = _figure(3.0, 2.0)
    ax = fig.add_subplot(1, 1, 1)
    fig.subplots_adjust(left=0.01)
    ax.set_ylabel("Cell count", fontsize=14)
    fig.canvas.draw()
    assert figure_fit.keep_labels_on_figure(fig, fig.canvas.get_renderer())
    fig.canvas.draw()
    assert ax.yaxis.label.get_window_extent(fig.canvas.get_renderer()).x0 >= -0.5
    assert not figure_fit.keep_labels_on_figure(fig, fig.canvas.get_renderer()), "it did not settle"


def test_a_short_figure_keeps_room_for_its_plot_above_the_footer():
    """On a phone on its side a footer allowed 38% of the height left the Position and Array axes a few pixels
    tall: on a short figure the footer is cut to what leaves the plot its room, and says how much was left out."""
    tall, short = _figure(9.0, 8.0), _figure(9.0, 3.3)
    lines = [f"line {k}" for k in range(20)]
    assert renderer._footer_share(tall) == renderer._FOOTER_MAX_SHARE
    assert renderer._footer_share(short) < renderer._FOOTER_MAX_SHARE
    kept = renderer._footer_that_fits(short, lines)
    assert len(kept) * renderer._line_step(short) <= renderer._footer_share(short) + 1e-9
    assert kept[-1].startswith("… ") and "more lines" in kept[-1]
    plot_pt = (1.0 - renderer._footer_share(short)) * short.get_figheight() * 72.0
    assert plot_pt >= renderer._PLOT_ROOM_PT - 1e-6


def test_a_figure_with_hand_placed_axes_keeps_its_margin():
    """Moving the subplots on a figure that also has hand-placed axes (a colour bar) would move them into it."""
    fig = _figure(3.0, 2.0)
    ax = fig.add_subplot(1, 1, 1)
    fig.add_axes((0.9, 0.1, 0.05, 0.8))
    fig.subplots_adjust(left=0.01)
    ax.set_ylabel("Cell count", fontsize=14)
    fig.canvas.draw()
    assert not figure_fit.keep_labels_on_figure(fig, fig.canvas.get_renderer())
    assert fig.subplotpars.left == 0.01
