"""A Properties click either changes what the user sees or says in the log why it did not.

Reported: on a nanowell dataset, Plotting -> Scatter, clicking the Properties
(sliders) icon did nothing at all — no panel, nothing in the log drawer, and
repeated clicks never opened it. It could not be reproduced offscreen, so what
is pinned here is the two ways the click path could do that without a trace:

* The toggle closed any panel Qt called ``isVisible()``. That is true of a
  panel whose every ancestor is shown even when a layout has left it with no
  pixel on screen, so the click "closed" a panel the user could not see and the
  next one re-opened it in the same unseen place. A panel is now closed only
  when it has a visible region; otherwise the click re-mounts it.
* Every way out of the click path that does not open the panel — an unknown
  key, a figure not built yet, an exception from a slot (which reaches only
  stderr, and a double-clicked app has none) — now logs at WARNING, which the
  log drawer shows. ``core.safe.swallow`` logs at DEBUG by default, which it
  does not.
"""

from __future__ import annotations

import logging
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget  # noqa: E402

from review.features.export import style  # noqa: E402

_LOGGER = "review.features.export.style"


class _Sidebar(QWidget):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.closed = 0

    def _close_dock(self) -> None:
        self.closed += 1
        self.hide()


class _Win(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._scatter_fig = object()
        self._scatter_canvas = None
        self._export_style_sidebars = {}
        self.status = []

    def _set_status(self, msg: str) -> None:
        self.status.append(msg)


@pytest.fixture
def win(qtbot):
    w = _Win()
    qtbot.addWidget(w)
    w.resize(400, 300)
    w.show()
    qtbot.waitExposed(w)
    return w


@pytest.fixture
def launches(monkeypatch):
    calls = []

    def _fake_launch(win, fig, default_name, **_kw):
        calls.append(default_name)
        return None

    monkeypatch.setattr(style, "launch_export_editor", _fake_launch)
    return calls


def _mount(win, *, size) -> _Sidebar:
    sb = _Sidebar(win)
    sb.setGeometry(0, 0, *size)
    sb.show()
    win._export_style_sidebars[id(win._scatter_fig)] = sb
    return sb


def test_a_panel_the_user_can_see_is_closed_by_the_click(win, launches):
    sb = _mount(win, size=(120, 120))
    assert sb.isVisible() and not sb.visibleRegion().isEmpty()

    style.open_export_style_panel(win, "scatter_cells")

    assert sb.closed == 1
    assert launches == []


def test_a_panel_visible_to_qt_but_with_no_pixel_on_screen_is_reopened_not_closed(win, launches):
    # Shown, every ancestor shown, and nothing of it drawn: what the old toggle closed.
    sb = _mount(win, size=(0, 0))
    assert sb.isVisible()
    assert sb.visibleRegion().isEmpty()

    style.open_export_style_panel(win, "scatter_cells")

    assert sb.closed == 0
    assert launches == ["scatter_figure"]


def test_a_deleted_panel_is_rebuilt_rather_than_raising_into_the_slot(win, launches):
    sb = _mount(win, size=(120, 120))
    from shiboken6 import delete
    delete(sb)

    style.open_export_style_panel(win, "scatter_cells")

    assert launches == ["scatter_figure"]


def test_a_figure_not_built_yet_is_reported_in_the_log_as_well_as_the_status_line(win, launches, caplog):
    win._scatter_fig = None
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        style.open_export_style_panel(win, "scatter_cells")

    assert win.status == ["Plot is not ready yet."]
    assert [r.getMessage() for r in caplog.records if r.name == _LOGGER] == [
        "Properties panel for 'scatter_cells' not opened: Plot is not ready yet."]
    assert launches == []


def test_an_unknown_key_is_reported_in_the_log(win, launches, caplog):
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        style.open_export_style_panel(win, "no_such_plot")

    assert [r.getMessage() for r in caplog.records if r.name == _LOGGER] == [
        "Properties panel for 'no_such_plot' not opened: Unknown export panel: no_such_plot"]


def test_an_exception_in_the_click_path_reaches_the_log_drawer_and_not_only_stderr(win, monkeypatch, caplog):
    def _boom(*_a, **_kw):
        raise RuntimeError("the panel could not be built")

    monkeypatch.setattr(style, "launch_export_editor", _boom)
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        style.open_export_style_panel(win, "scatter_cells")   # must not raise into the slot

    warned = [r for r in caplog.records if r.name == _LOGGER and r.levelno >= logging.WARNING]
    assert len(warned) == 1
    assert "open the Properties panel for 'scatter_cells'" in warned[0].getMessage()
    assert str(warned[0].exc_info[1]) == "the panel could not be built"
