"""Layout defects a person finds by looking, found by measuring a live window.

``tests/test_every_page_renders.py`` renders every page of the full app, with data,
at a desktop and at a phone's two shapes, and runs :func:`check_window` on each. The
checks are the defects a manual sweep found, written as measurements:

* ``text`` — a label or button whose text is wider (or, wrapped, taller) than the room it
  was given, so the screen shows it cut off;
* ``scroll`` — a scroll area whose content is wider than its viewport with no horizontal
  scroll bar, so the rest cannot be reached;
* ``window`` — a widget that extends past the window's right edge outside any scroll area;
* ``figure`` — matplotlib text (a title, an axis label, a note) that falls outside its figure;
* ``overlap`` — matplotlib text that overlaps other text (a legend over a panel title);
* ``sliver`` — a scrollable area squeezed to a fraction of the window;
* ``table`` — an item view that needs a sideways scroll to show its columns;
* ``menu`` — the phone's page menu naming a page that is not the one on screen.

Each tolerance is calibrated so the **desktop** is clean on a data-filled app: a check that
fires at the desk is a false alarm or a desk defect, and either way gets looked at.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QAbstractScrollArea, QLabel, QScrollArea, QWidget,
)

#: Pixels of slack before a measurement counts: font rounding, margins, a frame.
TEXT_SLACK_PX = 6
WRAPPED_SLACK_PX = 8
SCROLL_SLACK_PX = 8
#: A scrollable area shorter than this fraction of the window is a sliver.
SLIVER_FRACTION = 0.30


class Finding(NamedTuple):
    check: str
    widget: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.widget}: {self.detail}"


def _plain(text: str) -> str:
    """The text as drawn: markup removed and ``&&`` read as ``&``, a single ``&`` dropped."""
    text = re.sub(r"<[^>]+>", "", text or "")
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&")


def _name(w: QWidget) -> str:
    return f"{type(w).__name__}#{w.objectName()}" if w.objectName() else type(w).__name__


def visible_widgets(window: QWidget) -> list[QWidget]:
    out = []
    for w in window.findChildren(QWidget):
        try:
            if w.isVisibleTo(window) and not w.isWindow():
                out.append(w)
        except RuntimeError:
            continue            # deleted while the list was walked
    return out


def _widest_line(fm: QFontMetrics, text: str) -> int:
    return max((fm.horizontalAdvance(line) for line in text.split("\n")), default=0)


def clipped_text(window: QWidget) -> list[Finding]:
    found = []
    for w in visible_widgets(window):
        try:
            if isinstance(w, QLabel) and w.text() and not w.pixmap():
                text = _plain(w.text())
                if not text.strip():
                    continue
                fm, room = QFontMetrics(w.font()), w.contentsRect()
                if w.wordWrap():
                    need = fm.boundingRect(0, 0, max(room.width(), 1), 10_000, int(Qt.TextWordWrap), text).height()
                    if need > room.height() + WRAPPED_SLACK_PX:
                        found.append(Finding("text", _name(w), f"wrapped text needs {need}px, has {room.height()}px: {text[:40]!r}"))
                elif room.width() > 0 and _widest_line(fm, text) > room.width() + TEXT_SLACK_PX:
                    found.append(Finding("text", _name(w), f"text {_widest_line(fm, text)}px wide in {room.width()}px: {text[:40]!r}"))
            elif isinstance(w, QAbstractButton) and w.text() and w.width() > 0:
                text = _plain(w.text())
                need = _widest_line(QFontMetrics(w.font()), text) + 8
                if need > w.width() + TEXT_SLACK_PX:
                    found.append(Finding("text", _name(w), f"button text needs {need}px, has {w.width()}px: {text[:40]!r}"))
        except RuntimeError:
            continue
    return found


def clipped_scroll_content(window: QWidget) -> list[Finding]:
    found = []
    for w in visible_widgets(window):
        try:
            if isinstance(w, QScrollArea) and w.widget() is not None:
                inner, viewport = w.widget(), w.viewport()
                if inner.width() > viewport.width() + SCROLL_SLACK_PX and not w.horizontalScrollBar().isVisibleTo(window):
                    found.append(Finding("scroll", _name(w), f"content {inner.width()}px in a {viewport.width()}px viewport, no horizontal scroll bar"))
        except RuntimeError:
            continue
    return found


def off_window(window: QWidget) -> list[Finding]:
    found, width = [], window.width()
    for w in visible_widgets(window):
        try:
            parent, in_scroll_area = w.parentWidget(), False
            while parent is not None and parent is not window:
                if isinstance(parent, QAbstractScrollArea):
                    in_scroll_area = True
                    break
                parent = parent.parentWidget()
            if in_scroll_area or w.width() <= 4:
                continue
            right = QRect(w.mapTo(window, QPoint(0, 0)), w.size()).right()
            if right > width + 2:
                found.append(Finding("window", _name(w), f"right edge {right}px past a {width}px window"))
        except RuntimeError:
            continue
    return found


def _figure_texts(fig) -> list:
    """The text a reader reads off a figure: titles, axis labels, notes, legend entries. Tick labels are
    left out: matplotlib keeps labels for ticks outside the axis limits, and draws nothing there."""
    texts = list(fig.texts)
    for ax in fig.axes:
        texts += [ax.title, ax._left_title, ax._right_title, ax.xaxis.label, ax.yaxis.label] + list(ax.texts)
        legend = ax.get_legend()
        if legend is not None:
            texts += list(legend.get_texts())
    return [t for t in texts if t.get_visible() and t.get_text().strip()]


def _canvases(window: QWidget):
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

    return [c for c in window.findChildren(FigureCanvasQTAgg) if c.isVisibleTo(window)]


def figure_text_outside(window: QWidget) -> list[Finding]:
    found = []
    for canvas in _canvases(window):
        try:
            canvas.draw()
            renderer, fig = canvas.get_renderer(), canvas.figure
            width, height = fig.bbox.width, fig.bbox.height
            for t in _figure_texts(fig):
                e = t.get_window_extent(renderer)
                if e.x1 > width + 1.5 or e.x0 < -1.5 or e.y1 > height + 1.5 or e.y0 < -1.5:
                    found.append(Finding("figure", "FigureCanvas", f"{t.get_text()[:40]!r} spans {e.x0:.0f}..{e.x1:.0f} of {width:.0f}"))
        except Exception as exc:                      # a figure that cannot be measured is a finding
            found.append(Finding("figure", "FigureCanvas", f"could not be measured: {exc}"))
    return found


def figure_text_overlap(window: QWidget) -> list[Finding]:
    found = []
    for canvas in _canvases(window):
        try:
            canvas.draw()
            renderer = canvas.get_renderer()
            legend_entries = {id(t) for ax in canvas.figure.axes if ax.get_legend() is not None
                              for t in ax.get_legend().get_texts()}
            # A legend's own entries sit inside its box by design: the box stands for them.
            boxes = [(t.get_text()[:30], t.get_window_extent(renderer))
                     for t in _figure_texts(canvas.figure) if id(t) not in legend_entries]
            for ax in canvas.figure.axes:
                legend = ax.get_legend()
                if legend is not None:
                    boxes.append(("legend", legend.get_window_extent(renderer)))
            for i, (a_text, a) in enumerate(boxes):
                for b_text, b in boxes[i + 1:]:
                    if min(a.x1, b.x1) - max(a.x0, b.x0) > 2 and min(a.y1, b.y1) - max(a.y0, b.y0) > 2:
                        found.append(Finding("overlap", "FigureCanvas", f"{a_text!r} overlaps {b_text!r}"))
        except Exception as exc:
            found.append(Finding("overlap", "FigureCanvas", f"could not be measured: {exc}"))
    return found


def scroll_slivers(window: QWidget) -> list[Finding]:
    found, limit = [], window.height() * SLIVER_FRACTION
    for w in visible_widgets(window):
        try:
            if isinstance(w, QScrollArea) and w.widget() is not None:
                viewport = w.viewport()
                if w.widget().height() > viewport.height() + 8 and 0 < viewport.height() < limit:
                    found.append(Finding("sliver", _name(w), f"a scrollable area {viewport.height()}px tall in a {window.height()}px window"))
        except RuntimeError:
            continue
    return found


def wide_tables(window: QWidget) -> list[Finding]:
    found = []
    for w in visible_widgets(window):
        try:
            if isinstance(w, QAbstractItemView):
                bar = w.horizontalScrollBar()
                if bar.isVisibleTo(window) and bar.maximum() > SCROLL_SLACK_PX:
                    found.append(Finding("table", _name(w), f"needs {bar.maximum()}px of sideways scroll to show its columns"))
        except RuntimeError:
            continue
    return found


def stale_page_menu(window: QWidget, shell) -> list[Finding]:
    """In Review, the phone's page button names the page whose tab is current."""
    try:
        button = shell.page_button
        bar = shell._current_page_bar()
        if bar is None or not button.isVisibleTo(window) or window._review.currentMode() != "review":
            return []
        shown, current = _plain(button.text()).replace("▾", "").strip(), bar.tabText(bar.currentIndex())
        return [] if shown == current else [Finding("menu", "page button", f"says {shown!r} while {current!r} is on screen")]
    except (RuntimeError, AttributeError):
        return []


CHECKS = (clipped_text, clipped_scroll_content, off_window, figure_text_outside, figure_text_overlap,
          scroll_slivers, wide_tables)


def check_window(window: QWidget, shell=None) -> list[Finding]:
    """Every finding on *window* as it stands. *shell* is the phone shell, or ``None`` at the desk."""
    found = [f for check in CHECKS for f in check(window)]
    if shell is not None:
        found += stale_page_menu(window, shell)
    return found
