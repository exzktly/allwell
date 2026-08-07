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
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

import theme
from widgets.icons import make_icon


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

        row = QHBoxLayout(self)
        row.setContentsMargins(theme.Spacing.md, theme.Spacing.sm,
                               theme.Spacing.md, theme.Spacing.sm)
        row.setSpacing(theme.Spacing.sm)

        glyph = QLabel(self)
        glyph.setPixmap(make_icon(icon).pixmap(14, 14))
        row.addWidget(glyph)

        self._label = QLabel(text, self)
        self._label.setWordWrap(True)
        self._label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row.addWidget(self._label, 1)

        self._confirm_btn = QPushButton(confirm_text, self)
        self._confirm_btn.setProperty("variant", "primary")
        self._confirm_btn.clicked.connect(self.confirmed)
        row.addWidget(self._confirm_btn)

        self._cancel_btn = QPushButton(cancel_text, self)
        self._cancel_btn.clicked.connect(self.cancelled)
        row.addWidget(self._cancel_btn)

    def setText(self, text: str) -> None:
        self._label.setText(text)

    def text(self) -> str:
        return self._label.text()

    def setConfirmText(self, text: str) -> None:
        self._confirm_btn.setText(text)
