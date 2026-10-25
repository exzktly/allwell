"""FooterScrollArea — a QScrollArea with a footer that does not scroll.

The footer sits along the bottom edge of the scroll area, below the viewport,
and the viewport is shortened by the footer's height so nothing the scrolled
content draws is ever hidden under it. The scroll area stays the one widget a
caller holds: hiding it, fixing its width, or reading its ``viewport()`` and
``widget()`` all behave as they do on a plain ``QScrollArea``, which is the
point — a footer bolted on by wrapping the scroll area in another widget would
change what every one of those calls returns.

The footer's height follows its contents. A footer whose layout wraps (a
``FlowLayout`` of buttons) is asked for its height at the area's width, and a
child shown, hidden or resized inside it re-lays the footer on the next event
loop pass.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QScrollArea, QWidget


class FooterScrollArea(QScrollArea):
    """A scroll area with a pinned footer; see the module docstring."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._footer: QWidget | None = None
        self._relayout_pending = False

    def footer(self) -> QWidget | None:
        return self._footer

    def setFooter(self, footer: QWidget) -> None:
        """Pin *footer* under the viewport. The area takes ownership of it."""
        footer.setParent(self)
        footer.installEventFilter(self)
        self._footer = footer
        footer.show()
        self._layout_footer()

    def _live_footer(self) -> QWidget | None:
        """The footer, or ``None`` once its C++ object is gone.

        The footer is a child, so a tree being torn down deletes it before the
        area, and a resize or a queued re-layout can still reach the area
        after that. A ``destroyed`` hook does not cover it: the signal is not
        delivered to a Python slot on an object that is itself mid-teardown.
        """
        import shiboken6

        footer = self._footer
        if footer is not None and not shiboken6.isValid(footer):
            self._footer = footer = None
        return footer

    def footer_height(self) -> int:
        """The height the footer takes at the area's current width."""
        footer = self._live_footer()
        if footer is None or footer.isHidden():
            return 0
        width = max(0, self.width() - 2 * self.frameWidth())
        if footer.hasHeightForWidth() and width > 0:
            return max(footer.heightForWidth(width), footer.minimumSizeHint().height())
        return footer.sizeHint().height()

    def _layout_footer(self) -> None:
        self._relayout_pending = False
        import shiboken6

        if not shiboken6.isValid(self):
            return
        footer = self._live_footer()
        if footer is None:
            return
        height = self.footer_height()
        self.setViewportMargins(0, 0, 0, height)
        frame = self.frameWidth()
        footer.setGeometry(frame, self.height() - frame - height,
                           self.width() - 2 * frame, height)

    def _schedule_layout(self) -> None:
        if not self._relayout_pending:
            self._relayout_pending = True
            QTimer.singleShot(0, self._layout_footer)

    def resizeEvent(self, event) -> None:  # Qt naming
        super().resizeEvent(event)
        self._layout_footer()

    def eventFilter(self, obj, event) -> bool:  # Qt naming
        if obj is self._footer and event.type() in (
                QEvent.LayoutRequest, QEvent.Show, QEvent.Hide):
            self._schedule_layout()
        return super().eventFilter(obj, event)
