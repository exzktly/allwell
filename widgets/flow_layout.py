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

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidget


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
            metric = (style.PM_LayoutHorizontalSpacing if horizontal
                      else style.PM_LayoutVerticalSpacing)
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
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            widget = item.widget()
            if widget is not None and not widget.isVisible():
                continue
            space_x = self.horizontalSpacing()
            if space_x < 0:
                space_x = 0
            space_y = self.verticalSpacing()
            if space_y < 0:
                space_y = 0
            hint = item.sizeHint()
            next_x = x + hint.width() + space_x
            if next_x - space_x > effective.right() and line_height > 0:
                # Wrap to the next line.
                x = effective.x()
                y = y + line_height + space_y
                next_x = x + hint.width() + space_x
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()
