"""NoticeBadge — a small "!" pip pinned to the top-right corner of a host widget.

The notification-style marker for a control whose content has changed while
nobody was looking at it: the status bar's application-log button raises one
when a line arrives with the log drawer shut, and clears it when the drawer is
opened.

The badge carries a *tone* as well as its presence, and the tone only ever
escalates until :meth:`clear` — ``accent`` for something new, ``warn`` once a
warning has arrived, ``danger`` once an error has. An error that arrived
between two routine lines must not be repainted back to the routine colour by
the line that came after it, because the badge is then the only sign the error
happened at all.

API
---
* ``NoticeBadge(host)`` — a child of *host*, hidden until first raised.
* ``notify(tone="accent")`` — show the badge, escalating its tone.
* ``clear()`` — hide it and forget the tone.
* ``tone()`` — the current tone, or ``None`` while hidden.
* ``changed`` — emitted whenever the tone changes, raised or cleared. A
  widget mirroring the badge listens to this rather than to Show / Hide,
  which never arrive while the badge's own host is hidden.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QLabel, QWidget

from ui import theme

#: Lowest to highest. A tone can replace only a tone earlier in this tuple.
TONES = ("accent", "warn", "danger")


class NoticeBadge(QLabel):
    changed = Signal()

    def __init__(self, host: QWidget) -> None:
        super().__init__("!", host)
        self.setObjectName("NoticeBadge")
        self.setAlignment(Qt.AlignCenter)
        # The host stays clickable through the badge sitting on top of it.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._tone: str | None = None
        font = self.font()
        font.setPixelSize(max(1, round(theme.Typography.caption_size * 0.8)))
        font.setBold(True)
        self.setFont(font)
        # Derived from the font, so the pip scales with the UI rather than
        # carrying a pixel size of its own.
        self._diameter = self.fontMetrics().height()
        self.setFixedSize(self._diameter, self._diameter)
        host.installEventFilter(self)
        self.hide()

    def tone(self) -> str | None:
        return self._tone

    def notify(self, tone: str = "accent") -> None:
        """Show the badge; *tone* replaces the current one only if it ranks higher."""
        if tone not in TONES:
            tone = "accent"
        if self._tone is not None and TONES.index(tone) <= TONES.index(self._tone):
            if not self.isVisible():
                self.show()
            return
        self._tone = tone
        self._paint_tone()
        self.changed.emit()

    def _paint_tone(self) -> None:
        tone = self._tone
        self.setStyleSheet(
            f"#NoticeBadge {{ background-color: {getattr(theme.Colors, tone)}; "
            f"color: {theme.Colors.accent_fg}; "
            f"border-radius: {self._diameter // 2}px; }}"
        )
        self._place()
        self.show()
        self.raise_()

    def clear(self) -> None:
        was, self._tone = self._tone, None
        self.hide()
        if was is not None:
            self.changed.emit()

    def setInset(self, px: int) -> None:
        """Sit *px* inside the host's top-right corner rather than on it —
        for a host at the screen's edge, where a corner pip is half off it."""
        self._inset = max(0, int(px))
        self._place()

    def _aw_rescale(self) -> None:
        """Re-measure the pip off its (rescaled) font after a UI scale."""
        self._diameter = self.fontMetrics().height()
        self.setFixedSize(self._diameter, self._diameter)
        if self._tone is not None:
            self._paint_tone()
        self._place()

    def _place(self) -> None:
        host = self.parentWidget()
        inset = getattr(self, "_inset", 0)
        if host is not None:
            self.move(max(0, host.width() - self._diameter - inset), inset)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() in (
                QEvent.Resize, QEvent.Show):
            self._place()
        return False
