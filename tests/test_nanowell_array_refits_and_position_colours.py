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
