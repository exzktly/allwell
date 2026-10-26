"""ContextBanner — a strip that says a page is being used for something else.

A page normally does one thing, and its own controls say what. Occasionally a
page is borrowed: the Job Queue sends the user to a tool's page to fill in or
correct a queued job, and while that is happening the page's own Run button is
not what the user came for. Without a strip saying so, the page looks exactly
as it always does and the only way to finish is to guess.

The banner carries the sentence, a confirm button and a cancel, and nothing
else — it is not a dialog and it does not block the page beneath it. The page
keeps working normally throughout, which is the point: the user is editing it.

API
---
* ``ContextBanner(text, confirm_text, parent=None, *, icon="info", cancel_text="Cancel")``
* ``setText(text)`` — restate the context without rebuilding.
* ``confirmed`` / ``cancelled`` — the two ways out.

Host-agnostic by design: it knows nothing about queues, jobs or datasets, and
whoever shows it decides what confirming means.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from ui import theme
from widgets.icons import make_icon


#: The narrowest the sentence may be beside the buttons before they move under
#: it. Named rather than written inline (ARCHITECTURE §8).
_MIN_TEXT_W = 260
#: The glyph's column: the 14 px icon the strip starts with.
_GLYPH_W = 14


class ContextBanner(QWidget):
    """One row: glyph, message, confirm, cancel."""

    confirmed = Signal()
    cancelled = Signal()

    def __init__(
        self,
        text: str = "",
        confirm_text: str = "Save",
        parent: QWidget | None = None,
        *,
        icon: str = "info",
        cancel_text: str = "Cancel",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ContextBanner")
        # Without this a QWidget subclass ignores its own background rule, so
        # the strip would read as ordinary page chrome rather than as a mode.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        # A grid rather than a row, so the buttons can drop under the
        # sentence when the host is narrow. Beside it in a 320 px column (the
        # Analyze form's) the sentence got about 60 px and needed 255 px of
        # height in a strip that stayed one line tall, so it was cut off.
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(theme.Spacing.md, theme.Spacing.sm,
                                      theme.Spacing.md, theme.Spacing.sm)
        self._grid.setHorizontalSpacing(theme.Spacing.sm)
        self._grid.setVerticalSpacing(theme.Spacing.xs)

        glyph = QLabel(self)
        glyph.setPixmap(make_icon(icon).pixmap(_GLYPH_W, _GLYPH_W))
        self._grid.addWidget(glyph, 0, 0, Qt.AlignTop)

        self._label = QLabel(text, self)
        self._label.setWordWrap(True)
        self._label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._grid.addWidget(self._label, 0, 1)
        self._grid.setColumnStretch(1, 1)

        self._buttons = QWidget(self)
        buttons = QHBoxLayout(self._buttons)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(theme.Spacing.sm)
        self._confirm_btn = QPushButton(confirm_text, self._buttons)
        self._confirm_btn.setProperty("variant", "primary")
        self._confirm_btn.clicked.connect(self.confirmed)
        buttons.addWidget(self._confirm_btn)
        self._cancel_btn = QPushButton(cancel_text, self._buttons)
        self._cancel_btn.clicked.connect(self.cancelled)
        buttons.addWidget(self._cancel_btn)
        self._stacked = False
        self._grid.addWidget(self._buttons, 0, 2)

    def is_stacked(self) -> bool:
        """Whether the buttons sit under the sentence (a narrow host)."""
        return self._stacked

    def _fit(self) -> None:
        """Put the buttons beside the sentence or under it, and size the strip
        to the sentence as it actually wraps at this width.

        The height is worked out here rather than asked of the grid: the
        grid's height-for-width answer did not follow the label's real width,
        and left a wide strip sized for the narrow one it had been.
        """
        margins = self._grid.contentsMargins()
        gap = self._grid.horizontalSpacing()
        inner = self.width() - margins.left() - margins.right() - _GLYPH_W - gap
        buttons = self._buttons.sizeHint()
        stacked = inner - buttons.width() - gap < _MIN_TEXT_W
        if stacked != self._stacked:
            self._stacked = stacked
            self._grid.removeWidget(self._buttons)
            if stacked:
                self._grid.addWidget(self._buttons, 1, 1, Qt.AlignLeft)
            else:
                self._grid.addWidget(self._buttons, 0, 2)
        text_w = max(1, inner if stacked else inner - buttons.width() - gap)
        text_h = self._label.heightForWidth(text_w)
        body = (text_h + self._grid.verticalSpacing() + buttons.height() if stacked
                else max(text_h, buttons.height()))
        height = body + margins.top() + margins.bottom()
        if height != self.height():
            self.setFixedHeight(height)
        # Moving the buttons only schedules a relayout; apply it now, so the
        # strip is never drawn for a frame with the sentence squeezed beside
        # buttons that have already moved under it.
        self._grid.activate()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def setText(self, text: str) -> None:
        self._label.setText(text)
        self._fit()

    def text(self) -> str:
        return self._label.text()

    def setConfirmText(self, text: str) -> None:
        self._confirm_btn.setText(text)
