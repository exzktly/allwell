"""Phone layout: a plot title is never clipped, and takes at most two lines.

Reported: in phone layout plot titles were cut off at the figure's edges — on
the Bar Plots page right after switching into phone layout ("…an Intensity
(above threshold) ± SEI"), and on Line Graphs and the Scatter Plot, whose
renderers break their own titles across lines. Two defects:

- a figure drawn at the desk and then shown in phone layout kept the desk's
  fit until something redrew it: switching raised its DPI and nothing re-fitted
  the titles (``plot_style._refit_on_resize`` now does, on the canvas's resize);
- the fit skipped any title that already held a line break, so a renderer's
  own two lines, each too wide for a phone, were drawn as they were.

Asked for: never clipped, two lines if needed. ``plot_style._fit_title`` keeps
a title on one line when it fits, else breaks it onto two (at its " — " clause
when it has one), and if two lines still do not fit draws them at the size
that does.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from review.core import plot_style
from review.core.plot_style import _fit_title

LONG = "GFP Mean Intensity (above threshold) ± SEM — t = 0 h"


def _title(text: str, size: float = 18.0):
    fig = Figure(figsize=(4, 3), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_title(text, fontsize=size)
    renderer = fig.canvas.get_renderer()
    return ax.title, renderer


def _widest(title, renderer) -> float:
    prop = title.get_fontproperties()
    return max(renderer.get_text_width_height_descent(line, prop, ismath=False)[0]
               for line in title.get_text().split("\n"))


def _one_line_width(text: str, size: float, renderer, title) -> float:
    prop = title.get_fontproperties().copy()
    prop.set_size(size)
    return renderer.get_text_width_height_descent(text, prop, ismath=False)[0]


def test_a_title_that_fits_is_left_alone():
    title, renderer = _title("Events (N)")
    _fit_title(title, 1000, renderer)
    assert title.get_text() == "Events (N)" and title.get_fontsize() == 18.0


def test_a_title_too_wide_for_one_line_takes_two_at_its_own_size():
    title, renderer = _title(LONG, 10.0)
    room = _one_line_width(LONG, 10.0, renderer, title) * 0.85
    _fit_title(title, room, renderer)
    assert title.get_text() == "GFP Mean Intensity (above threshold) ± SEM\n— t = 0 h"
    assert title.get_fontsize() == 10.0
    assert _widest(title, renderer) <= room


def test_a_title_too_wide_for_two_lines_shrinks_rather_than_takes_a_third():
    """The case that clipped: two lines at full size still overran the axes."""
    title, renderer = _title(LONG, 18.0)
    room = 150.0
    _fit_title(title, room, renderer)
    assert title.get_text().count("\n") == 1, title.get_text()
    assert title.get_fontsize() < 18.0
    assert _widest(title, renderer) <= room + 0.5


def test_without_a_dash_clause_the_break_balances_the_two_lines():
    text = "Scatter: GFP Mean Intensity vs NIR Mean Intensity"
    title, renderer = _title(text, 12.0)
    room = _one_line_width(text, 12.0, renderer, title) * 0.6
    _fit_title(title, room, renderer)
    first, second = title.get_text().split("\n")
    assert f"{first} {second}" == text
    assert _widest(title, renderer) <= room


def test_a_renderers_own_break_that_does_not_fit_is_refitted():
    """Line Graphs and Scatter break their own titles; each line was still
    wider than a phone, and the fit skipped any title holding a break."""
    text = "Scatter: GFP Mean Intensity vs GFP\nMean Intensity (t=0h–48h, all wells)"
    title, renderer = _title(text, 18.0)
    room = 200.0
    _fit_title(title, room, renderer)
    assert title.get_text().count("\n") == 1
    assert _widest(title, renderer) <= room + 0.5
    assert title.get_text().replace("\n", " ") == text.replace("\n", " ")


def test_a_renderers_own_break_that_fits_is_kept():
    text = "GFP Mean Intensity\n(above threshold) ± SEM"
    title, renderer = _title(text, 8.0)
    _fit_title(title, 1000, renderer)
    assert title.get_text() == text and title.get_fontsize() == 8.0


def test_every_fit_starts_from_the_renderers_title_not_the_last_fit():
    """Narrow, then wide again (the phone turned): the title goes back to one
    line at its own size, rather than keeping the narrow fit's break."""
    title, renderer = _title(LONG, 18.0)
    _fit_title(title, 150.0, renderer)
    assert "\n" in title.get_text()
    _fit_title(title, 5000.0, renderer)
    assert title.get_text() == LONG and title.get_fontsize() == 18.0


def test_a_new_title_from_the_renderer_is_fitted_afresh():
    title, renderer = _title(LONG, 18.0)
    _fit_title(title, 150.0, renderer)
    title.set_text("Fraction")              # the next redraw wrote a new title
    title.set_fontsize(18.0)
    _fit_title(title, 5000.0, renderer)
    assert title.get_text() == "Fraction" and title.get_fontsize() == 18.0


# ── the figure drawn at the desk, then shown in phone layout ────────────────

@pytest.fixture
def phone():
    from core import phone_layout
    phone_layout.set_on(True)
    yield
    phone_layout.set_on(False)


def test_a_desk_figure_is_refitted_when_its_canvas_resizes_in_phone_layout(qtbot, monkeypatch):
    """The reported case: a bar plot drawn at the desk, then phone layout
    raised its DPI (a canvas resize) and nothing re-fitted its titles. And
    leaving phone layout gives the title its own text back."""
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QResizeEvent
    from PySide6.QtWidgets import QApplication

    from core import phone_layout

    monkeypatch.setattr(plot_style, "figure_prefs", lambda _ctx, _fig: {})
    fig = Figure(figsize=(8, 3), dpi=100)
    canvas = FigureCanvasQTAgg(fig)
    qtbot.addWidget(canvas)
    canvas.resize(800, 300)
    ax = fig.add_subplot(111)
    ax.set_title(LONG, fontsize=18)
    plot_style._refit_on_resize(object(), fig, canvas)

    def resize_to(w: int) -> None:
        canvas.resize(w, 300)
        QApplication.sendEvent(canvas, QResizeEvent(QSize(w, 300), QSize(w, 300)))
        canvas.draw()

    phone_layout.set_on(True)
    try:
        resize_to(260)
        renderer = fig.canvas.get_renderer()
        bb = ax.title.get_window_extent(renderer)
        assert ax.title.get_text().count("\n") == 1, ax.title.get_text()
        assert bb.x0 >= -0.5 and bb.x1 <= fig.bbox.width + 0.5, \
            f"title spans {bb.x0:.0f}–{bb.x1:.0f} of a {fig.bbox.width:.0f} px figure"
    finally:
        phone_layout.set_on(False)
    resize_to(800)
    assert ax.title.get_text() == LONG and ax.title.get_fontsize() == 18


def test_the_title_fits_the_axes_the_layout_settles_on(phone):
    """Constrained layout widens the left margin for phone-sized tick labels
    and y-label after the title was first fitted, so a title fitted to the
    axes' earlier width overran the figure's edge — by about 6 px on the
    iPhone Duo's inner screen, measured in the real window. The fit runs again
    against the laid-out axes."""
    fig = Figure(figsize=(4.4, 4), dpi=144)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.bar(["A01", "A02"], [100000, 120000])
    ax.set_ylabel("GFP Mean Intensity (a.u.)", fontsize=16)
    ax.tick_params(labelsize=16)
    ax.set_title(LONG, fontsize=18)
    plot_style._apply_layout_engine(fig, {})
    fig.canvas.draw()
    bb = ax.title.get_window_extent(fig.canvas.get_renderer())
    assert ax.title.get_text().count("\n") <= 1
    assert bb.x0 >= -0.5 and bb.x1 <= fig.bbox.width + 0.5, \
        f"title spans {bb.x0:.1f}–{bb.x1:.1f} of a {fig.bbox.width:.0f} px figure"


def test_a_shrunk_title_fits_even_where_text_does_not_scale_with_its_size():
    """CI failed where this machine passed: the two lines, shrunk in
    proportion to their overrun, still came out 1.4 px wider than the room —
    hinting rounds glyph advances, by different amounts on different FreeType
    builds, so text width is not exactly proportional to size. A renderer
    whose text carries a few pixels that do not scale reproduces that here."""
    title, real = _title(LONG, 18.0)

    class _Hinted:
        def get_text_width_height_descent(self, s, prop, ismath=False):
            w, h, d = real.get_text_width_height_descent(s, prop, ismath=ismath)
            return w + 4.0, h, d

    hinted = _Hinted()
    room = 150.0
    _fit_title(title, room, hinted)
    prop = title.get_fontproperties()
    widest = max(hinted.get_text_width_height_descent(line, prop)[0]
                 for line in title.get_text().split("\n"))
    assert title.get_text().count("\n") == 1
    assert widest <= room, f"the shrunk title is {widest:.1f} px in a {room:.0f} px room"
