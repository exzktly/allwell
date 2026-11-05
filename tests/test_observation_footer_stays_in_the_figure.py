"""An observations footer wider than its figure wraps inside it, so a capture keeps the plot's shape.

The footer was one unwrapped ``fig.text`` per line. A line wider than the figure ran off the canvas's right edge,
where the screen merely clipped it; a capture crops ``bbox_inches="tight"``, and the tight box grew to take the
whole line in. Saving a Nanowells distribution to Saved Plots then handed back an image as wide as its longest
footer note, with the plot squeezed against the left edge of it. Every footer now goes through
``observation_band.write_footer``, which wraps to the figure's width.
"""

from __future__ import annotations

import ast
import io

import pytest

pytest.importorskip("PySide6")

from tests import _repo_scan  # noqa: E402
from tests.test_plots_draw_nanowell_well_medians import _footer, _shell  # noqa: E402

#: Long enough to be several figure widths at the footer's 7 pt.
LONG_NOTE = "Registration was checked against the reference well and found to drift at the rim; " * 4


def test_saving_a_nanowell_distribution_keeps_the_figure_s_shape_and_the_whole_footer(qtbot, tmp_path, monkeypatch):
    from PIL import Image

    from review.domain import observation
    from review.features.distribution.controller import redraw_distribution

    real = observation.footer_line
    monkeypatch.setattr(observation, "footer_line", lambda *a, **k: real(*a, **k) + " · " + LONG_NOTE)
    ctx = _shell(qtbot, tmp_path, "review.features.distribution.tab_view.build_distribution_tab", "dist_save")
    redraw_distribution(ctx)
    fig = ctx.widgets.get("_distribution_fig")
    assert LONG_NOTE.strip() in _footer(fig), "the probe note did not reach the footer, so nothing is measured"

    card = ctx.widgets.get("_distribution_card")
    saved = []
    card.set_save_handler(lambda png, label, svg=b"", panel=False: saved.append(png) or "")
    card.saveFigureToGallery()
    assert saved and saved[0], "the card's save produced no image"

    width, height = Image.open(io.BytesIO(saved[0])).size
    fig_w, fig_h = fig.get_size_inches()
    # A tight crop is the ink plus matplotlib's 0.1 in pad on each side, and the wrapped footer's ink reaches the
    # figure's right edge, so the image may be up to that pad wider than the figure — no more. Unwrapped, this
    # footer made it 5,516 px for a 2,160 px figure.
    pad_px = 2 * 0.1 * 300
    assert width <= fig_w * 300 + pad_px, f"saved {width} px wide for a {fig_w * 300:.0f} px figure"
    assert width / height == pytest.approx(fig_w / fig_h, rel=0.15)
    # Wrapped, not cut: the footer still holds every word.
    assert LONG_NOTE.strip() in _footer(fig)


def test_every_observations_footer_is_written_by_the_one_helper():
    """A footer written with its own ``fig.text(..., gid=FOOTER_GID)`` would not wrap; the helper is the only site."""
    sites = []
    for path, tree in _repo_scan.parsed_sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                named = isinstance(kw.value, ast.Name) and kw.value.id == "FOOTER_GID"
                spelled = isinstance(kw.value, ast.Constant) and kw.value.value == "observations_footer"
                if kw.arg == "gid" and (named or spelled):
                    sites.append(f"{_repo_scan.rel(path)}:{node.lineno}")
    assert len(sites) == 1 and sites[0].startswith("review/core/observation_band.py:"), (
        "observations footers written outside observation_band.write_footer: " + ", ".join(sites))


# --- The panels clear the footer -------------------------------------------------------------------------------
#
# A footer is written on the figure, outside every axes, so the renderers' fixed bottom margins took no account of
# it: on the Distribution tab the footer's first line was drawn across the x-axis label, and a wrapped footer
# covered it entirely. ``plot_style.keep_panels_clear_of_footer`` moves the panels up until their lowest ink
# sits above the footer.


def _overlap_px(fig) -> float:
    """How far, in pixels, the footer's top reaches above the lowest ink of any visible panel (<= 0: clear)."""
    renderer = fig.canvas.get_renderer()
    footer = next(t for t in fig.texts if t.get_gid() == "observations_footer").get_window_extent(renderer)
    boxes = [ax.get_tightbbox(renderer) for ax in fig.axes if ax.get_visible()]
    return footer.y1 - min(b.y0 for b in boxes if b is not None)


def _bottom_y0(fig) -> float:
    return min(ax.get_position().y0 for ax in fig.axes if ax.get_visible())


def _redraw(which, qtbot, tmp_path):
    if which == "distribution":
        from review.features.distribution.controller import redraw_distribution

        ctx = _shell(qtbot, tmp_path, "review.features.distribution.tab_view.build_distribution_tab", which)
        return ctx, lambda: redraw_distribution(ctx), lambda: ctx.widgets.get("_distribution_fig")
    if which == "bar":
        from review.features.barplot.collect import bar_well_display_label
        from review.features.barplot.controller import ordered_bar_keys, update_bar_tp_menu
        from review.features.barplot.renderer import redraw_bars

        ctx = _shell(qtbot, tmp_path, "review.features.barplot.tab_view.build_bar_plots_tab", which)
        ctx.runtime.barplot.current_keys = lambda: ordered_bar_keys(ctx)
        ctx.runtime.barplot.well_display_label = lambda label: bar_well_display_label(ctx, label)
        update_bar_tp_menu(ctx)
        return ctx, lambda: redraw_bars(ctx), lambda: ctx.runtime.barplot.fig
    from review.core.plot_orchestrator import redraw_app

    ctx = _shell(qtbot, tmp_path, "review.features.lineplot.tab_view.build_line_graphs_tab", which)
    return ctx, lambda: redraw_app(ctx), lambda: ctx.widgets.get("_line_fig")


def test_the_distribution_s_footer_is_not_drawn_across_its_axis_label(qtbot, tmp_path):
    """The reported case, as drawn: on main the two-line footer reached 20 px into the x-axis label."""
    ctx, redraw, fig_of = _redraw("distribution", qtbot, tmp_path)
    redraw()
    fig = fig_of()
    fig.canvas.draw()
    assert _overlap_px(fig) <= 0, f"the footer reaches {_overlap_px(fig):.1f} px into the panel's ink"


@pytest.mark.parametrize("which", ["distribution", "bar", "line"])
def test_a_long_footer_pushes_the_panels_up_and_redraws_do_not_ratchet(which, qtbot, tmp_path, monkeypatch):
    from review.domain import observation

    real = observation.footer_line
    # Long enough to need more room than Bar's and Line's own bottom margins already leave (a short footer fits).
    monkeypatch.setattr(observation, "footer_line", lambda *a, **k: real(*a, **k) + " · " + LONG_NOTE * 3)
    ctx, redraw, fig_of = _redraw(which, qtbot, tmp_path)
    redraw()
    fig = fig_of()
    fig.canvas.draw()
    assert _overlap_px(fig) <= 0, f"{which}: the footer reaches {_overlap_px(fig):.1f} px into the panels' ink"
    first = _bottom_y0(fig)
    redraw()
    redraw()
    assert fig_of() is fig
    assert _bottom_y0(fig) == pytest.approx(first), f"{which}: each redraw moved the panels again"


def _figure_with_footer(text, *, by_hand=True):
    import matplotlib

    matplotlib.use("Agg", force=False)
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from mpl_toolkits.axes_grid1 import make_axes_locatable

    fig = Figure(figsize=(7.2, 5.4), dpi=100)
    FigureCanvasAgg(fig)
    top, bottom = fig.subplots(2, 1)
    fig.subplots_adjust(bottom=0.12)
    bottom.set_xlabel("Cell count")
    if by_hand:
        bottom.set_position([0.15, 0.12, 0.6, 0.3])      # a panel the renderer placed by hand, off the grid
    cax = make_axes_locatable(top).append_axes("right", size="4%", pad=0.08)
    fig.text(0.01, 0.002, text, fontsize=7, wrap=True, gid="observations_footer")
    return fig, top, bottom, cax


def test_panels_placed_by_hand_keep_their_arrangement_and_get_it_back_when_the_footer_goes():
    from review.core.plot_style import keep_panels_clear_of_footer

    fig, top, bottom, _cax = _figure_with_footer("Observations: nanowells\n" + LONG_NOTE)
    # Original positions: the top panel's colour bar is appended by a divider, which narrows the drawn
    # (active) position at draw time from the original one, and the original is what moves.
    before = {ax: ax.get_position(original=True).bounds for ax in (top, bottom)}
    keep_panels_clear_of_footer(fig)
    assert _overlap_px(fig) <= 0
    # Squeezed upward together, not snapped to the grid: the hand-placed panel keeps its left edge and width,
    # and the top panel's top edge does not move.
    assert bottom.get_position().x0 == pytest.approx(0.15) and bottom.get_position().width == pytest.approx(0.6)
    assert top.get_position(original=True).y1 == pytest.approx(before[top][1] + before[top][3])
    assert bottom.get_position().y0 > before[bottom][1]
    fig.texts[0].remove()
    keep_panels_clear_of_footer(fig)
    for ax, bounds in before.items():
        assert ax.get_position(original=True).bounds == pytest.approx(bounds), "the panels did not get their place back"


def test_under_a_layout_engine_the_footer_s_band_is_kept_out_of_its_rectangle():
    from review.core.plot_style import keep_panels_clear_of_footer

    fig, *_ = _figure_with_footer("Observations: nanowells\n" + LONG_NOTE, by_hand=False)
    fig.set_layout_engine("constrained")
    keep_panels_clear_of_footer(fig)
    rect = fig.get_layout_engine().get()["rect"]
    assert rect[1] > 0.0
    fig.canvas.draw()
    assert _overlap_px(fig) <= 0


def test_a_figure_on_a_bare_canvas_is_cleared_too():
    """Batch Export draws on a bare ``FigureCanvasBase``, which has no renderer of its own; its figures are the ones
    that are written to disk, so they must be measured and cleared like the tabs' are."""
    from matplotlib.backend_bases import FigureCanvasBase
    from matplotlib.figure import Figure

    from review.core.observation_band import write_footer

    fig = Figure(figsize=(7.2, 5.4), dpi=100)
    FigureCanvasBase(fig)
    ax = fig.add_subplot()
    fig.subplots_adjust(bottom=0.12)
    ax.set_xlabel("Cell count", fontsize=14)
    write_footer(fig, "Observations: nanowells\n" + LONG_NOTE, None)
    renderer = fig._get_renderer()
    footer = fig.texts[0].get_window_extent(renderer)
    assert footer.y1 <= ax.get_tightbbox(renderer).y0
