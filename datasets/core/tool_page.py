"""The skeleton every Datasets tool page shares (design language §6, *Datasets*).

A tool page is a column no wider than ``theme.Metrics.tool_page_w`` (about 720 px in the mock-up), left on the
canvas: the tool's title, one line of help, its cards (``widgets.card.Card`` — *Folders*, *What to copy*, …), then a
foot row — the reason the primary action cannot run yet, "Add to queue", and the primary action last
(``ui.page_chrome.ActionRow``). Nine pages built their own heading, rule and blurb by hand, each slightly
differently; :func:`tool_page` is that opening, once.

The page scrolls, so a tall dashboard or log under the foot row never crushes what is above it (each tool's plate
map sizes its cells from the room it is given).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ui import buttons, text_roles, theme
from ui.page_chrome import ActionRow
from widgets.card import Card


def tool_page(page: QWidget, title: str, help_line: str, *, rich: bool = False,
              help_tip: str = "") -> tuple[QScrollArea, QVBoxLayout]:
    """Lay *page* out as a tool page titled *title* with *help_line* under it; return its scroll area and the
    column's layout, for the caller to add its cards, foot row and status below the help. *rich* reads the help
    line as rich text (a page that names a file in ``<code>``); *help_tip* is the help line's tooltip, for the
    detail a single line leaves out."""
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(0, 0, 0, 0)
    scroll = QScrollArea(page)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    page_layout.addWidget(scroll)

    content = QWidget(scroll)
    scroll.setWidget(content)
    row = QHBoxLayout(content)
    row.setContentsMargins(theme.Spacing.lg, theme.Spacing.lg, theme.Spacing.lg, theme.Spacing.lg)
    row.setSpacing(0)
    column = QWidget(content)
    column.setObjectName("ToolPageColumn")
    column.setMaximumWidth(theme.Metrics.tool_page_w)
    row.addWidget(column, 1)
    row.addStretch(0)
    outer = QVBoxLayout(column)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(theme.Spacing.md)

    head = QVBoxLayout()
    head.setSpacing(theme.Spacing.xs)
    head.addWidget(text_roles.page_title(title, column))
    help_label = text_roles.help_text(help_line, column)
    if rich:
        help_label.setTextFormat(Qt.RichText)
    if help_tip:
        help_label.setToolTip(help_tip)
    head.addWidget(help_label)
    outer.addLayout(head)
    return scroll, outer


def foot_row(page: QWidget, primary_text: str, *, on_run, on_queue=None,
             queue_tip: str = "Add this run to the Job Queue instead of running it now.",
             before: tuple[QWidget, ...] = (), after: tuple[QWidget, ...] = ()) -> ActionRow:
    """The page's foot: the reason the primary action cannot run, "Add to queue" when *on_queue* is given, and the
    primary action last. Sets ``page._run_btn`` and ``page._queue_btn`` (``None`` without a queue), the names the
    tools' controllers and tests reach for; both start disabled, as the tools' readiness checks expect. *before* sit
    left of "Add to queue" (a setting of how the run goes, such as OME-TIFF's Workers); *after* sit between it and
    the primary (a tool's second action, such as Validate's Apply repairs)."""
    page._run_btn = buttons.primary(primary_text, page)
    page._run_btn.setEnabled(False)
    page._run_btn.clicked.connect(on_run)
    others = tuple(before)
    page._queue_btn = None
    if on_queue is not None:
        page._queue_btn = buttons.default("Add to queue", page)
        page._queue_btn.setToolTip(queue_tip)
        page._queue_btn.setEnabled(False)
        page._queue_btn.clicked.connect(on_queue)
        others += (page._queue_btn,)
    others += tuple(after)
    return ActionRow(page._run_btn, page, others=others)


def into_card(outer: QVBoxLayout, title: str, items) -> Card:
    """Move *items* — widgets, or layouts, laid out in *outer* — into a ``widgets.card.Card`` titled *title*, put
    where the first of them was. Each widget keeps whether it was hidden: reparenting a widget hides it, and a
    page decides elsewhere which of its rows show (a hint shown only once a source is chosen)."""
    items = list(items)
    first = items[0]
    at = outer.indexOf(first)
    if at < 0:
        raise ValueError(f"{first!r} is not laid out in this column")
    card = Card(title, outer.parentWidget())
    for item in items:
        # Each keeps its stretch and alignment (a plate map laid out centred stays centred) and whether it was
        # hidden.
        index = outer.indexOf(item)
        stretch = outer.stretch(index)
        align = outer.itemAt(index).alignment()
        if isinstance(item, QWidget):
            hidden = item.isHidden()
            outer.removeWidget(item)
            card.body().addWidget(item, stretch, align)
            item.setHidden(hidden)
        else:
            outer.removeItem(item)
            card.body().addLayout(item, stretch)
    outer.insertWidget(at, card)
    watcher = _ShownWhileAnyIs(card, [w for w in items if isinstance(w, QWidget)])
    watcher.sync()
    return card


class _ShownWhileAnyIs(QObject):
    """Shows a card while any of its rows is shown, and hides it while none is: a card of rows the page reveals
    only later (OME-TIFF's channel roles, once a file is read) is otherwise an empty titled box."""

    def __init__(self, card: Card, rows: list[QWidget]) -> None:
        super().__init__(card)
        self._card, self._rows = card, rows
        for row in rows:
            row.installEventFilter(self)

    def sync(self) -> None:
        self._card.setHidden(bool(self._rows) and all(row.isHidden() for row in self._rows))

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 — Qt naming
        if ev.type() in (QEvent.ShowToParent, QEvent.HideToParent):
            self.sync()
        return False


def cards(outer: QVBoxLayout, *groups: tuple[str, object, object]) -> list[Card]:
    """Group runs of *outer*'s items into cards: each group is ``(title, first, last)``, *first* and *last* a widget
    in the column or an index into it, and everything from one to the other inclusive goes into a card titled
    *title*. The runs are read before anything moves, so indexes count the column as the page built it."""
    def at(x) -> int:
        return x if isinstance(x, int) else outer.indexOf(x)

    spans = [(title, at(first), at(last)) for title, first, last in groups]
    made = []
    for title, a, b in sorted(spans, key=lambda g: g[1], reverse=True):     # bottom first: earlier indexes hold
        if a < 0 or b < a:
            raise ValueError(f"{title!r}: no run of the column from {a} to {b}")
        items = []
        for i in range(a, b + 1):
            item = outer.itemAt(i)
            items.append(item.widget() if item.widget() is not None else item.layout())
        made.append(into_card(outer, title, [x for x in items if x is not None]))
    return made[::-1]
