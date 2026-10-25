"""Two reported Nanowells defects.

**The Array view showed "a crushed single row".** It fits the region it shows to its axes' shape when it draws, so
a nanowell is round only at that size. Nothing redrew it when the canvas changed size — the window, the site detail
opening beside it, the control band re-flowing — so every nanowell was squashed into an oval, and a draw made while
the canvas was still being laid out (a few pixels tall) left one flattened row of the array stretched over the
view. The view now re-fits once a resize settles (``controller.on_canvas_resized``).

**Position's colours did not match the plate's.** Each well's line took the palette entry of its place in the list
drawn, so a well drawn without the wells before it took their colour. It now takes its plate-position colour, as
the plate selector and every plot tab do (``selections_model.color_for_well``).

Driven through the real builder and window in the minimal shell of ``tests/test_nanowell_page.py``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from matplotlib.colors import to_hex  # noqa: E402

from review.domain.selections_model import color_for_well  # noqa: E402
from review.features.nanowell import controller as nwc  # noqa: E402
from tests.test_nanowell_page import _loaded  # noqa: E402
from tests.test_nanowell_page_views_ui import _choose_view, page  # noqa: E402,F401
from ui.theme import WELL_COLORS_TUPLE  # noqa: E402


def _units_per_px(st):
    """µm per screen pixel across and down the Array view's axes, as drawn now."""
    ax = st.drawn.ax
    box = ax.get_window_extent()
    (x0, x1), (y1, y0) = ax.get_xlim(), ax.get_ylim()
    return abs(x1 - x0) / box.width, abs(y1 - y0) / box.height


def test_the_array_re_fits_when_its_canvas_changes_size_so_a_nanowell_stays_round(page, qtbot):  # noqa: F811
    _loaded(qtbot, page)
    _choose_view(page, "array", "map")
    st = page.state.feature_slice("nanowell")
    across, down = _units_per_px(st)
    assert across == pytest.approx(down, rel=0.02)                              # round when drawn
    canvas = page.widgets.get("_nw_canvas")
    before = canvas.height()
    page.window.resize(page.window.width(), page.window.height() - 300)         # a much shorter page
    qtbot.waitUntil(lambda: canvas.height() < before - 200, timeout=5000)       # the canvas has shrunk
    qtbot.wait(400)                                                             # past the re-fit's settling delay
    across, down = _units_per_px(st)
    assert across == pytest.approx(down, rel=0.02)                              # and still round after it


def test_a_resize_on_another_view_draws_nothing_new(page, qtbot, monkeypatch):  # noqa: F811
    _loaded(qtbot, page)
    calls = []
    monkeypatch.setattr(nwc, "redraw", lambda ctx: calls.append(ctx))
    nwc.on_canvas_resized(page)                                                 # on Distribution
    assert calls == []


def test_position_draws_each_well_in_its_plate_colour(page, qtbot):  # noqa: F811
    page.state.selection.selected_wells = {"B01"}                               # drawn without A01 before it
    _loaded(qtbot, page)
    nwc.set_view(page, "position")
    assert to_hex(color_for_well("B01")) != to_hex(WELL_COLORS_TUPLE[0])        # the two rules disagree here
    lines = [ln for ln in page.widgets.get("_nw_fig").axes[0].get_lines() if ln.get_label() == "B01"]
    assert [to_hex(ln.get_color()) for ln in lines] == [to_hex(color_for_well("B01"))]


def test_a_range_typed_for_the_distribution_does_not_reach_the_array(page, qtbot):  # noqa: F811
    """Reported: limits set in Properties while on Distribution carried over to the Array view and were applied
    to it. The page's views share one figure, so its typed prefs are shared too; the Array, a map in the well frame
    with its own zoom, takes none of them."""
    from review.core import plot_style

    _loaded(qtbot, page)
    fig = page.widgets.get("_nw_fig")
    plot_style._ensure_axis_prefs_store(page)[plot_style.figure_pref_key(page, fig)].update(
        {"y_lim_min": "0", "y_lim_max": "10", "x_lim_min": "0", "x_lim_max": "4", "y_log": True})
    nwc.redraw(page)                                                              # Distribution takes them
    assert fig.axes[0].get_ylim()[1] == 10.0 and fig.axes[0].get_yscale() == "log"   # (a log axis drops the 0)
    _choose_view(page, "array", "map")
    st = page.state.feature_slice("nanowell")
    x0, y0, x1, y1 = st.drawn.region
    ax = st.drawn.ax
    assert ax.get_xlim() == (x0, x1) and ax.get_ylim() == (y1, y0) and ax.get_yscale() == "linear"
    plot_style.apply_export_style_to_current(page, fig, page.widgets.get("_nw_canvas"))   # the sidebar's route
    assert ax.get_xlim() == (x0, x1) and ax.get_ylim() == (y1, y0) and ax.get_yscale() == "linear"


def test_a_re_fit_that_fires_after_the_page_is_closed_draws_nothing(page, qtbot, monkeypatch):  # noqa: F811
    """The re-fit runs a moment after a resize; a page closed in that moment must not be drawn. A redraw there queues
    matplotlib's deferred draw, which then ran on a canvas already deleted — the intermittent teardown error
    ``Internal C++ object (FigureCanvasQTAgg) already deleted`` on the Array tests."""
    _loaded(qtbot, page)
    _choose_view(page, "array", "map")
    st = page.state.feature_slice("nanowell")
    st.drawn.size_px = (1, 1)                         # a fit the axes no longer match: an open page would redraw
    calls = []
    monkeypatch.setattr(nwc, "redraw", lambda ctx: calls.append(ctx))
    nwc.on_canvas_resized(page)
    assert calls == [page], "the fixture does not reach the redraw on an open page"
    calls.clear()
    page.window.close()
    page.widgets.get("_nw_refit_timer").timeout.emit()                      # the pending re-fit firing after it
    assert calls == []
