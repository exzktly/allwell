"""Home's Overview in phone layout: one column, nothing reached by scrolling sideways.

Reported: "On mobile, Home view should be arranged vertically so no horizontal
scrolling is required." Measured before the change, the Overview at a 400 px
phone width needed 821 px: the plate and notes side by side, the stat tiles in
one row, and a next-step sentence that did not wrap (773 px on its own).

The main assertion is on the page as laid out — its minimum width against the
viewport it has — because "no horizontal scrolling" is a statement about
widths, and a test that only checked each row's direction would pass with a
long folder path still holding the page wide.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QBoxLayout, QScrollArea, QVBoxLayout, QWidget

from core import phone_layout

#: A phone held upright, in desktop pixels at a unit of 1.
PHONE_WIDTH = 400

#: A dataset name with no spaces, longer than the phone is wide in one line.
LONG_NAME = "2026-10-21_HeLa_DAPI_GFP_mCherry_timecourse_plate07"

#: A Windows path — the reporter is on Windows — whose backslashes Qt's line
#: breaking will not break at.
WIN_PATH = (r"C:\Users\researcher\OneDrive_University\Lab_Microscopy"
            r"\2026_October_Screens\2026-10-21_plate07")


@pytest.fixture
def phone():
    phone_layout.set_on(True)
    yield
    phone_layout.set_on(False)


def _overview(shell_ctx, qtbot, width=PHONE_WIDTH):
    """The Overview as the app builds it, shown at *width*."""
    from review.features.home.tab_view import build_home_overview

    ctx, _window = shell_ctx
    frame = QWidget()
    QVBoxLayout(frame)
    qtbot.addWidget(frame)
    build_home_overview(ctx, frame)
    # Show the page behind the "no dataset" placeholder, which is the stack
    # flip a load makes (``refresh_empty_state_pages``, which needs the full
    # app). Until then the page is hidden and has no width to measure.
    stack = ctx.widgets.get("_home_empty_stack")
    stack.setCurrentWidget(frame.findChild(QScrollArea, "HomeScroll"))
    frame.resize(width, 900)
    frame.show()
    qtbot.waitExposed(frame)
    scroll = frame.findChild(QScrollArea, "HomeScroll")
    # The frame has no parent, so Python owns it: held here for the test's
    # lifetime, or the page is freed as soon as this returns.
    scroll._test_frame = frame
    return ctx, scroll


def _min_width(w: QWidget) -> int:
    return max(w.minimumWidth(), w.minimumSizeHint().width())


def _too_wide(scroll: QScrollArea) -> list[str]:
    page = scroll.widget()
    room = scroll.viewport().width()
    return [f"{type(w).__name__} {w.objectName()!r} {_min_width(w)}px "
            f"{(getattr(w, 'text', lambda: '')() or '')[:40]!r}"
            for w in page.findChildren(QWidget)
            if w.isVisibleTo(page) and _min_width(w) > room]


def _with_recents(monkeypatch, tmp_path) -> None:
    """Recent datasets whose rows are wider than the phone: a long name in a
    deep folder. The folders exist, so the rows are enabled and drawn."""
    from core import app_settings as app_settings_module
    from review.core.recent_datasets import record_open
    from tests.test_home_empty_state import _Settings

    deep = tmp_path / "OneDrive_University_Lab_Microscopy" / "2026_October_Screens"
    settings = _Settings()
    for i in range(3):
        folder = deep / f"{LONG_NAME}_{i}"
        folder.mkdir(parents=True)
        record_open(settings, str(folder), folder.name, float(i))
    monkeypatch.setattr(app_settings_module, "app_settings", lambda: settings)


def test_the_overview_needs_no_sideways_scroll_on_a_phone(
        shell_ctx, qtbot, phone, monkeypatch, tmp_path):
    from review.features.home import collect

    _with_recents(monkeypatch, tmp_path)
    # The masthead's path as a Windows machine shows it.
    identity = collect.identity
    monkeypatch.setattr(collect, "identity",
                        lambda ctx: {**identity(ctx), "path": WIN_PATH})
    ctx, scroll = _overview(shell_ctx, qtbot)
    from review.features.home.controller import refresh_home
    refresh_home(ctx)

    assert ctx.widgets.get("_home_path").text().startswith("C:"), (
        "premise: the masthead shows the Windows path")
    assert ctx.widgets.get("_home_recents").layout().count() == 3, (
        "premise: the recents card drew its rows")
    stack = ctx.widgets.get("_home_empty_stack")
    assert stack.width() <= PHONE_WIDTH, (
        f"the page's stack is {stack.width()} px wide on a {PHONE_WIDTH} px "
        "phone — held open by one of its pages")
    assert _min_width(scroll.widget()) <= scroll.viewport().width(), (
        "the Overview is wider than the phone, so part of it is reached by "
        "scrolling sideways:\n  " + "\n  ".join(_too_wide(scroll)))
    qtbot.wait(50)    # and still, once Qt has run everything it queued
    assert _min_width(scroll.widget()) <= scroll.viewport().width(), (
        "the page grew wider than the phone after the repaint settled:\n  "
        + "\n  ".join(_too_wide(scroll)))
    assert scroll.horizontalScrollBar().maximum() == 0, (
        f"the page scrolls {scroll.horizontalScrollBar().maximum()} px sideways:"
        "\n  " + "\n  ".join(_too_wide(scroll)))


def test_every_side_by_side_row_reads_top_to_bottom_on_a_phone(
        shell_ctx, qtbot, phone):
    from review.features.home._phone import SIDE_BY_SIDE

    _ctx, scroll = _overview(shell_ctx, qtbot)
    rows = scroll.widget().findChildren(QBoxLayout, SIDE_BY_SIDE)
    # Masthead, next step, plate + notes, plate + legend, health + receipt.
    assert len(rows) == 5, f"expected the five side-by-side rows, found {len(rows)}"
    assert [r.direction() for r in rows] == [QBoxLayout.TopToBottom] * 5


def test_the_tiles_are_one_row_at_the_desk_and_one_column_on_a_phone(
        shell_ctx, qtbot):
    from review.features.home._phone import apply

    ctx, scroll = _overview(shell_ctx, qtbot, width=1400)
    grid = ctx.widgets.get("_home_tiles").layout()
    n = grid.count()
    assert n > 1, "premise: the dataset has more than one tile"
    assert (grid.rowCount(), grid.columnCount()) == (1, n)

    phone_layout.set_on(True)
    try:
        apply(ctx)
        grid = ctx.widgets.get("_home_tiles").layout()
        cells = [grid.getItemPosition(i)[:2] for i in range(grid.count())]
        assert cells == [(i, 0) for i in range(n)]
    finally:
        phone_layout.set_on(False)


def test_leaving_phone_layout_gives_the_desk_back(shell_ctx, qtbot, phone):
    """Every row side by side again and the tiles in one row: the page must not
    stay a column on a wide screen after the phone is put down."""
    from review.features.home._phone import SIDE_BY_SIDE, apply

    ctx, scroll = _overview(shell_ctx, qtbot)
    phone_layout.set_on(False)
    apply(ctx)
    rows = scroll.widget().findChildren(QBoxLayout, SIDE_BY_SIDE)
    assert [r.direction() for r in rows] == [QBoxLayout.LeftToRight] * 5
    grid = ctx.widgets.get("_home_tiles").layout()
    cells = [grid.getItemPosition(i)[:2] for i in range(grid.count())]
    assert cells == [(0, i) for i in range(grid.count())]
    phone_layout.set_on(True)   # the fixture turns it off again


def test_turning_phone_layout_on_reaches_a_built_overview(shell_ctx, qtbot):
    """Through the listener the page registers, not by calling ``apply``: the
    shell turns phone layout on with the Overview already built."""
    from review.features.home._phone import SIDE_BY_SIDE

    _ctx, scroll = _overview(shell_ctx, qtbot)
    rows = scroll.widget().findChildren(QBoxLayout, SIDE_BY_SIDE)
    assert [r.direction() for r in rows] == [QBoxLayout.LeftToRight] * 5
    phone_layout.set_on(True)
    try:
        qtbot.waitUntil(
            lambda: [r.direction() for r in rows] == [QBoxLayout.TopToBottom] * 5,
            timeout=2000)
    finally:
        phone_layout.set_on(False)
