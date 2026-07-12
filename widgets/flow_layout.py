"""FlowLayout — a QLayout that reflows its children onto multiple lines.

A left-to-right layout that packs widgets like words in a paragraph: when the
next widget would overflow the available width it wraps to a new line instead
of clipping off the right edge. This is the standard Qt "Flow Layout" example
adapted to PySide6.

Use it for control rows (toolbars) that carry more widgets than fit at a narrow
width — e.g. the plotting / image-review "TabCtrl" rows, which are clipped when
Split View halves the pane width. Swapping a ``QHBoxLayout`` for a ``FlowLayout``
keeps every control reachable (the row grows taller instead of hiding widgets).

No All-Well coupling — a plain reusable widget (belongs in ``widgets/``).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QMargins, QObject, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLayout, QSizePolicy, QStyle, QWidget,
)


class _FlowHeightSync(QObject):
    """Keeps a FlowLayout host's ``minimumHeight`` equal to the height its rows
    actually need at the current width.

    A ``FlowLayout`` reports ``heightForWidth`` so a box layout can grow the row
    when it wraps — but that only works if the enclosing context *applies* it.
    Nested inside a ``QStackedWidget`` page (as every centre tab is), some
    platforms size the row from its one-line ``minimumSize`` instead, so a
    wrapped second line has no reserved height and is clipped behind the widget
    below (the reported "row of buttons hidden by the output panel"). Pinning the
    minimum height on every resize makes the row keep its wrapped content
    regardless of whether the parent honours ``heightForWidth``.
    """

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 — Qt naming
        if isinstance(event, QEvent) and event.type() in (
            QEvent.Resize, QEvent.LayoutRequest,
        ):
            lay = obj.layout()
            if lay is not None and lay.hasHeightForWidth():
                need = lay.heightForWidth(obj.width())
                if need > 0 and obj.minimumHeight() != need:
                    obj.setMinimumHeight(need)
        return False


def sync_flow_height(widget: QWidget) -> None:
    """Make *widget* (a host of a :class:`FlowLayout`) always reserve the height
    its wrapped rows need. See :class:`_FlowHeightSync` for why the layout's own
    ``heightForWidth`` isn't enough inside a stacked page."""
    watcher = _FlowHeightSync(widget)
    widget.installEventFilter(watcher)
    widget._flow_height_sync = watcher  # keep a reference alive


def flow_group(parent: QWidget | None, *items, spacing: int = 4) -> QWidget:
    """Bundle a label and its control(s) into one un-splittable widget.

    A ``FlowLayout`` wraps per *item*, and a label added as its own item can wrap
    away from the widget it names ("Layout:" landing on a different line than its
    combo). Grouping the label + control(s) into a single container widget — added
    to the FlowLayout as ONE item — keeps them together: the whole group is placed
    or wrapped atomically.

    Each positional *item* is either a ``str`` (wrapped in a ``QLabel``) or an
    existing ``QWidget`` (used as-is; it is reparented into the group — safe for a
    widget already stored in a registry, which keeps its own reference). Build and
    register the control first, then pass it here.

    Note: the FlowLayout sees only the container, so its wrap/visibility logic keys
    off the group, not the inner widgets — hide the group container, not an inner
    control, if a call site needs to hide one.
    """
    box = QWidget(parent)
    hl = QHBoxLayout(box)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(spacing)
    for it in items:
        hl.addWidget(QLabel(it, box) if isinstance(it, str) else it)
    return box


class FlowLayout(QLayout):
    """Left-to-right layout that wraps to the next line on overflow.

    ``h_spacing`` / ``v_spacing`` default to ``-1`` (derive from the style /
    parent spacing), matching the Qt example. Pass explicit pixel values for a
    tighter or looser grid.
    """

    def __init__(self, parent: QWidget | None = None,
                 margin: int = 0, h_spacing: int = -1, v_spacing: int = -1) -> None:
        super().__init__(parent)
        self._items: list = []
        self._h_space = h_spacing
        self._v_space = v_spacing
        self.setContentsMargins(QMargins(margin, margin, margin, margin))

    # ── item bookkeeping ────────────────────────────────────────────────────
    def __del__(self) -> None:  # pragma: no cover - defensive cleanup
        while self._items:
            self._items.pop()

    def addItem(self, item) -> None:  # noqa: N802 (Qt override)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    # ── QBoxLayout drop-in convenience ───────────────────────────────────────
    # These let a toolbar row swap ``QHBoxLayout`` → ``FlowLayout`` without
    # rewriting its call sites. A flow layout left-packs and wraps, so a stretch
    # spacer is meaningless (no-op); fixed spacing and nested layouts flow as
    # ordinary items.
    def addStretch(self, stretch: int = 0) -> None:  # noqa: N802
        return None

    def addSpacing(self, size: int) -> None:  # noqa: N802
        from PySide6.QtWidgets import QSizePolicy, QSpacerItem
        self.addItem(QSpacerItem(int(size), 0,
                                 QSizePolicy.Fixed, QSizePolicy.Minimum))

    def addLayout(self, layout, stretch: int = 0) -> None:  # noqa: N802
        self.addItem(layout)

    def addWidget(self, widget, *args) -> None:  # noqa: N802
        # Tolerate the QBoxLayout ``addWidget(w, stretch, alignment)`` signature —
        # flow layouts ignore stretch/alignment, but accepting the extra args lets
        # a toolbar swap in without editing every call site.
        super().addWidget(widget)

    def insertWidget(self, index: int, widget, *args) -> None:  # noqa: N802
        super().addWidget(widget)  # appended; move it to *index*
        if 0 <= index < len(self._items) - 1:
            self._items.insert(index, self._items.pop())

    def setSpacing(self, spacing: int) -> None:  # noqa: N802
        self._h_space = int(spacing)
        self._v_space = int(spacing)

    # ── spacing ─────────────────────────────────────────────────────────────
    def horizontalSpacing(self) -> int:  # noqa: N802
        if self._h_space >= 0:
            return self._h_space
        return self._smart_spacing(QSizePolicy.PushButton, horizontal=True)

    def verticalSpacing(self) -> int:  # noqa: N802
        if self._v_space >= 0:
            return self._v_space
        return self._smart_spacing(QSizePolicy.PushButton, horizontal=False)

    def _smart_spacing(self, ctrl_type, *, horizontal: bool) -> int:
        parent = self.parent()
        if parent is None:
            return -1
        if parent.isWidgetType():
            style = parent.style()
            # PySide6 exposes PixelMetric as a scoped enum on the QStyle class,
            # not as attributes on the style *instance* — ``style.PM_*`` raises
            # AttributeError. (Only reached when no explicit spacing was passed.)
            metric = (QStyle.PixelMetric.PM_LayoutHorizontalSpacing if horizontal
                      else QStyle.PixelMetric.PM_LayoutVerticalSpacing)
            return style.pixelMetric(metric, None, parent)
        return parent.spacing()

    # ── geometry ────────────────────────────────────────────────────────────
    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(),
                      margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(),
                                  -margins.right(), -margins.bottom())
        space_x = max(0, self.horizontalSpacing())
        space_y = max(0, self.verticalSpacing())

        x = effective.x()
        y = effective.y()
        line_height = 0
        # Buffer the current line's placements as (item, QRect) so the whole line
        # can be flushed *vertically centred* once its final height is known —
        # otherwise short controls (labels/checkboxes) top-align against tall
        # buttons and the row reads ragged.
        line: list = []

        def _flush() -> None:
            if test_only:
                return
            for it, r in line:
                # Centre each item within the line's height.
                cy = r.y() + (line_height - r.height()) // 2
                it.setGeometry(QRect(QPoint(r.x(), cy), r.size()))

        for item in self._items:
            widget = item.widget()
            if widget is not None and not widget.isVisible():
                continue
            # Never pack a widget into less than it needs (defends against a
            # widget whose sizeHint under-reports its painted width — the cause
            # of the Composite/Crop overlap).
            size = item.sizeHint().expandedTo(item.minimumSize())
            next_x = x + size.width() + space_x
            if next_x - space_x > effective.right() and line_height > 0:
                # Wrap: flush the finished line centred, then start a new one.
                _flush()
                line = []
                x = effective.x()
                y = y + line_height + space_y
                next_x = x + size.width() + space_x
                line_height = 0
            line.append((item, QRect(QPoint(x, y), size)))
            x = next_x
            line_height = max(line_height, size.height())
        _flush()
        return y + line_height - rect.y() + margins.bottom()
