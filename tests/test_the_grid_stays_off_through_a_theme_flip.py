"""Properties → Grid → **Show grid**, off, survives everything that re-themes.

``apply_export_style_prefs`` learned to turn the grid off. Then every capture
turned it straight back on: ``PlotCard._publication_capture`` flips the card to
the publication theme for the duration of a Save figure, a Copy panel, a Copy
as vector or a gallery capture, ``setPlotTheme`` runs ``apply_axes_style`` over
every axes, and that ended with

    ax.grid(True, color=grid, linewidth=0.7, linestyle="-")

— an unconditional switch-on, in a pass whose business is colour. So the
reported symptom was two symptoms with one cause: the toggle looked dead the
moment the user moved the Screen/Publication control, and **every exported
figure carried the gridlines it was drawn without**. The same line also reset
the grid's dash and opacity, so a file could differ from its own figure in
three ways at once.

The assertions are on the rendered figure, hashed, for the reason the sibling
file (``test_grid_show_turns_the_grid_off.py``) gives: matplotlib's grid state
is not one flag, and reading any single one of them passes against code that
still draws the lines.
"""

from __future__ import annotations

import hashlib
import io

import pytest

pytest.importorskip("matplotlib")

from matplotlib.figure import Figure  # noqa: E402

from well_viewer.core.plot_style import (  # noqa: E402
    DEFAULT_EXPORT_STYLE_PREFS,
    apply_export_style_prefs,
)
from widgets.plot_card import apply_axes_style  # noqa: E402


def _rendered(fig) -> str:
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    FigureCanvasAgg(fig)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=72)
    return hashlib.sha256(buf.getvalue()).hexdigest()


def _figure(*, grid_show: bool, theme: str | None = None,
            then_force_off: bool = False):
    """A styled plot, optionally re-themed the way a capture re-themes it."""
    fig = Figure(figsize=(4, 3))
    ax = fig.add_subplot(1, 1, 1)
    ax.plot([1, 2, 3], [1, 4, 9])
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["grid_show"] = grid_show
    apply_export_style_prefs(fig, prefs)
    if theme is not None:
        apply_axes_style(ax, theme)
    if then_force_off:
        # The control case: this figure has no grid, whatever the theme pass
        # did. Comparing against it is what separates "no gridlines" from
        # "gridlines in a different colour", which a hash of the two themed
        # figures cannot tell apart — the first draft of this measurement
        # reported the pref respected on exactly that mistake.
        ax.grid(False)
    return fig


@pytest.mark.parametrize("theme", ["publication", "screen"])
def test_a_theme_flip_does_not_bring_the_grid_back(theme):
    """What a Save figure or a Copy does to a plot with the grid turned off."""
    assert _rendered(_figure(grid_show=False, theme=theme)) == \
        _rendered(_figure(grid_show=False, theme=theme, then_force_off=True))


@pytest.mark.parametrize("theme", ["publication", "screen"])
def test_a_theme_flip_does_not_take_the_grid_away_either(theme):
    """The other half: a figure whose grid is on keeps it, and keeps it
    distinguishable from one whose grid is off."""
    on = _rendered(_figure(grid_show=True, theme=theme))
    off = _rendered(_figure(grid_show=False, theme=theme))
    assert on != off


def test_the_theme_pass_leaves_the_dash_and_opacity_alone():
    """They are Properties settings too. The switch-on carried
    ``linewidth=0.7, linestyle="-"`` with it, so a captured figure came out
    with a solid grid over a plot showing a dashed one."""
    dashed = _figure(grid_show=True, theme=None)
    dashed_then_themed = _figure(grid_show=True, theme="publication")
    def _styles(fig):
        # A set, not a list: the theme pass changes the tick label size, which
        # changes how many ticks the locator picks, so comparing per-line lists
        # compares tick counts as well and fails for a reason that has nothing
        # to do with the grid's style.
        ax = fig.axes[0]
        return {(ln.get_linestyle(), round(float(ln.get_alpha() or 1.0), 3))
                for ln in (*ax.get_xgridlines(), *ax.get_ygridlines())}

    assert _styles(dashed) == _styles(dashed_then_themed) == {("--", 0.25)}


def test_the_capture_bracket_keeps_the_grid_off(qapp_or_skip):
    """Through the real bracket, on a real card — the seam the user's Save
    figure and Copy actually go through.

    ``_publication_capture`` is what every capture route on the card wraps
    itself in (``_on_copy_axis_clicked``, ``_on_save_axis_clicked``,
    ``captureVectorSvg``, ``_on_save_figure_clicked``), so a test of
    ``apply_axes_style`` alone would be a test of the layer under the defect.

    Measured two ways, because neither alone is enough: the grid switch
    matplotlib itself reads, and the pixels — a figure captured with the grid
    off must not render like the same figure captured with it on.
    """
    from widgets.plot_card import PlotCard

    card = PlotCard(figsize=(3, 2))
    try:
        card.setPlotTheme("screen")
        ax = card.figure.add_subplot(1, 1, 1)
        ax.plot([1, 2, 3], [1, 4, 9])
        prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
        prefs["grid_show"] = False
        apply_export_style_prefs(card.figure, prefs)

        with card._publication_capture():
            switched_on = [a.xaxis._major_tick_kw.get("gridOn")
                           or a.yaxis._major_tick_kw.get("gridOn")
                           for a in card.figure.axes]
            captured_without_grid = _rendered(card.figure)
        assert not any(switched_on), "the capture switched the grid back on"

        prefs["grid_show"] = True
        apply_export_style_prefs(card.figure, prefs)
        with card._publication_capture():
            captured_with_grid = _rendered(card.figure)
        assert captured_without_grid != captured_with_grid
    finally:
        card.deleteLater()


@pytest.fixture
def qapp_or_skip():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
