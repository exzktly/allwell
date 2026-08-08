"""Every main page draws its first row at the same height.

The complaint this guards against: switching pages moved the top of the page.
The shell chrome was never the cause — the centre stack starts at the same y in
all three modes — but each page body added its own offset on top of it. A 12 px
inset on the page root here, a bespoke toolbar row there, a control band whose
height came from whichever control the page happened to put in it, and a 1 px
pane border around the bodies nested in a ``QTabWidget``: the first row of a
page's own chrome landed anywhere from level with the stack to 52 px below it.

The contract (``widgets/page_body.py``): a main page body is a ``ControlBand``
flush against the top of the page, one row tall, then the page's content. The
band is present even on a page with nothing to put in it.

What this asserts, against a real window with every page and sub-tab built:

* every page's band starts exactly at the centre stack's top, in all three
  modes and inside the nested sub-tab widgets;
* no page root re-introduces a top inset;
* no band is shorter than one row.

A band that *wraps* is taller, and so is one holding a control taller than a row
(the ``SegmentedControl`` on Multivariate, Spatial and Scatter). Both move only
the content below the band, never its top edge, so the height assertion is a
floor rather than an equality.

Slow lane: this builds the whole application window once (the fixture is
module-scoped so the ~2000-QObject construction is paid a single time).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QStackedWidget, QTabWidget, QWidget

from widgets.control_band import ControlBand

pytestmark = pytest.mark.slow

#: The window the measurements are taken in. Fixed so a band that wraps here
#: wraps for everyone reading a failure message.
_W, _H = 1640, 980


def _pump(qapp, n: int = 15) -> None:
    for _ in range(n):
        qapp.processEvents()


def _top(win, widget: QWidget) -> int:
    """*widget*'s top edge in window coordinates."""
    return widget.mapTo(win, QPoint(0, 0)).y()


def _bands(widget: QWidget) -> list[ControlBand]:
    return [b for b in widget.findChildren(ControlBand) if b.isVisible()]


@pytest.fixture(scope="module")
def shell(qapp, tmp_path_factory):
    """A live ``AllWellApp`` with the mini dataset loaded and every page built."""
    from tests.fixtures.mini_dataset import build_mini_dataset
    from allwell import AllWellApp

    data = build_mini_dataset(tmp_path_factory.mktemp("alignment") / "mini")
    win = AllWellApp(None)
    win.resize(_W, _H)
    win.show()
    _pump(qapp)
    win._review._load_path(data)
    _pump(qapp, 25)

    build_pending = win._review._ctx.runtime.centre.build_pending
    for name in win._review._notebook.pageNames():
        if name and build_pending is not None:
            build_pending(name)
    _pump(qapp, 25)
    yield win
    win.close()


def _review_pages(shell) -> list[tuple[str, QWidget]]:
    nb = shell._review._notebook
    return [(name, nb._by_name[name]) for name in nb.pageNames() if name]


def test_every_review_page_starts_its_band_at_the_stack_top(shell, qapp):
    nb = shell._review._notebook
    stack_top = _top(shell, nb)
    offenders = []
    for name, page in _review_pages(shell):
        nb.setCurrentByName(name)
        _pump(qapp)
        bands = _bands(page)
        if not bands:
            offenders.append(f"{name}: no control band (page reserves no row)")
            continue
        top = min(_top(shell, b) for b in bands)
        if top != stack_top:
            offenders.append(f"{name}: band top {top}, stack top {stack_top}")
    assert not offenders, "pages whose first row is not level:\n  " + "\n  ".join(offenders)


def test_nested_sub_tab_bodies_fill_their_host(shell, qapp):
    """A sub-tab's body starts at the top of the widget hosting it.

    Image Browser and Sample Definitions nest their leaves in a ``QTabWidget``
    whose own bar is hidden (it is mirrored into the mode band), so the pane
    border that bar would have sat on has nothing left to close — and its top
    edge used to push every nested body a pixel below where an un-nested page's
    body starts. A leaf that carries its own band must also start it flush
    against the body's top, exactly as a top-level page does.
    """
    nb = shell._review._notebook
    offenders = []
    for name, page in _review_pages(shell):
        nb.setCurrentByName(name)
        _pump(qapp)
        for tabs in page.findChildren(QTabWidget):
            host_top = _top(shell, tabs)
            for i in range(tabs.count()):
                tabs.setCurrentIndex(i)
                _pump(qapp)
                leaf = f"{name} / {tabs.tabText(i)}"
                body = tabs.widget(i)
                if _top(shell, body) != host_top:
                    offenders.append(
                        f"{leaf}: body top {_top(shell, body)}, host top {host_top}"
                    )
                for band in _bands(body):
                    if _top(shell, band) != _top(shell, body):
                        offenders.append(
                            f"{leaf}: band top {_top(shell, band)}, "
                            f"body top {_top(shell, body)}"
                        )
    assert not offenders, "nested bodies not level:\n  " + "\n  ".join(offenders)


def test_no_page_root_re_introduces_a_top_inset(shell, qapp):
    """The page root's top margin is what a band flush at the top costs.

    Every page that used to sit 12 px low did so by setting one here.
    """
    offenders = []
    for name, page in _review_pages(shell):
        layout = page.layout()
        if layout is None:
            continue
        top = layout.contentsMargins().top()
        if top:
            offenders.append(f"{name}: page root top margin {top}")
    assert not offenders, "pages with a top inset:\n  " + "\n  ".join(offenders)


def test_no_band_is_shorter_than_one_row(shell, qapp):
    nb = shell._review._notebook
    offenders = []
    for name, page in _review_pages(shell):
        nb.setCurrentByName(name)
        _pump(qapp)
        for band in _bands(page):
            if band.height() < ControlBand.band_height():
                offenders.append(
                    f"{name}: band {band.height()}px, one row is "
                    f"{ControlBand.band_height()}px"
                )
    assert not offenders, "bands under one row:\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize("mode,index", [("review", 0), ("analyze", 1), ("datasets", 2)])
def test_each_mode_starts_its_band_at_the_stack_top(shell, qapp, mode, index):
    """Switching Review / Analyze / Datasets must not move the first row either."""
    stack: QStackedWidget = shell._review._central_pane_stack
    stack.setCurrentIndex(index)
    _pump(qapp)
    pane = stack.widget(index)
    bands = _bands(pane)
    assert bands, f"{mode} pane reserves no control-band row"
    assert min(_top(shell, b) for b in bands) == _top(shell, stack), (
        f"{mode} pane's first row is not level with the centre stack's top"
    )
    stack.setCurrentIndex(0)
    _pump(qapp)
