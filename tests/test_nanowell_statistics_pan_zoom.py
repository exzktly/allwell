"""The Nanowells Statistics figures keep a pan or zoom made with the figure card's toolbar.

Reported: "the plots on this statistics page do not respond to pan/zoom controls". They did respond — each drag
moved the axes — but the press that began it was also read by the page as a click on empty space, which cleared the
nanowell selection and drew the figure again from scratch, so the pan was gone the moment the events settled. The
same press reached Trajectories and Position. Driven through the page's real card toolbar and its canvas callbacks.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from tests.test_nanowell_rare_responders_page import _choose, _open, page  # noqa: E402,F401


def _card_toolbar(page):  # noqa: F811
    from widgets.plot_card import PlotCard

    canvas = page.widgets.get("_nw_fig").canvas
    return next(w for w in page.window.findChildren(PlotCard) if getattr(w, "canvas", None) is canvas).toolbar.nav


def _drag(canvas, ax, dx=80, dy=40):
    from matplotlib.backend_bases import MouseEvent

    box = ax.get_window_extent()
    x, y = box.x0 + box.width / 2, box.y0 + box.height / 2
    for name, px, py in (("button_press_event", x, y), ("motion_notify_event", x + dx, y + dy),
                         ("button_release_event", x + dx, y + dy)):
        canvas.callbacks.process(name, MouseEvent(name, canvas, px, py, button=1))


@pytest.mark.parametrize("test, tool", [("welch", "pan"), ("rare", "pan"), ("welch", "zoom"), ("rare", "zoom")])
def test_a_pan_or_zoom_on_the_statistics_figure_stays(page, qtbot, test, tool):  # noqa: F811
    st = _open(qtbot, page)
    _choose(page, "_nw_stats_test_cb", test)
    if test == "rare":
        _choose(page, "_nw_control_cb", "ctrl")
        qtbot.waitUntil(lambda: st.rare_footer is not None, timeout=30000)
    fig = page.widgets.get("_nw_fig")
    qtbot.wait(300)
    ax = next(a for a in fig.axes if a.axison)
    before = ax.get_xlim(), ax.get_ylim()
    nav = _card_toolbar(page)
    getattr(nav, tool)()
    _drag(fig.canvas, ax)
    moved = ax.get_xlim(), ax.get_ylim()
    assert moved != before
    qtbot.wait(500)                                      # let whatever the press queued run
    now = next(a for a in fig.axes if a.axison)
    assert now is ax and (now.get_xlim(), now.get_ylim()) == moved
    getattr(nav, tool)()                                 # the tool off again


def test_an_empty_space_click_with_nothing_selected_does_not_redraw(page, qtbot, monkeypatch):  # noqa: F811
    from review.features.nanowell import controller as nwc

    _open(qtbot, page)
    _choose(page, "_nw_stats_test_cb", "welch")
    qtbot.wait(300)
    fig = page.widgets.get("_nw_fig")
    ax = next(a for a in fig.axes if a.axison)
    drawn = []
    monkeypatch.setattr(nwc, "redraw", lambda ctx: drawn.append(ctx))
    from matplotlib.backend_bases import MouseEvent

    box = ax.get_window_extent()
    for name in ("button_press_event", "button_release_event"):
        fig.canvas.callbacks.process(name, MouseEvent(name, fig.canvas, box.x0 + 5, box.y1 - 5, button=1))
    assert drawn == []
