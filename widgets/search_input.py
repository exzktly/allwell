"""SearchInput — a QLineEdit with a leading search glyph and a trailing hint.

Matches the mockup's property-panel search field: a search icon inset on the
left (via ``QLineEdit.addAction``) and a small ``⌘K``-style hint chip on the
right. Recolors its icon on focus. Token-styled; margins are font relative.

API additions over ``QLineEdit``:
* ``SearchInput(parent=None, *, placeholder="Search…", hint="⌘K")``
* ``setHintText(text)`` — pass ``""`` to hide the hint.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QLineEdit

from ui import theme
from widgets import icons


class SearchInput(QLineEdit):
    def __init__(self, parent=None, *, placeholder: str = "Search…",
                 hint: str = "⌘K") -> None:
        super().__init__(parent)
        self.setObjectName("SearchInput")
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)

        self._icon_px = max(13, round(self.fontMetrics().height() * 0.9))
        self._search_action = QAction(self)
        self.addAction(self._search_action, QLineEdit.LeadingPosition)
        self._refresh_icon(active=False)

        # Trailing hint chip (a child label, right-aligned over the text margin).
        self._hint = QLabel(hint, self)
        self._hint.setObjectName("SearchInputHint")
        self._hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._hint.setVisible(bool(hint))

        self.setStyleSheet(self._build_qss())
        self.textChanged.connect(lambda _t: self._reposition_hint())
        self._reposition_hint()

    # ── API ──────────────────────────────────────────────────────────────
    def setHintText(self, text: str) -> None:
        self._hint.setText(text or "")
        self._hint.setVisible(bool(text))
        self._reposition_hint()

    # ── internals ────────────────────────────────────────────────────────
    def _refresh_icon(self, *, active: bool) -> None:
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        color = "text_secondary" if active else "text_muted"
        self._search_action.setIcon(
            icons.make_icon("search", self._icon_px,
                            normal=color, active="text_secondary", dpr=dpr or 1.0)
        )

    def _hint_width(self) -> int:
        # ``isVisibleTo(self)``, not ``isVisible()``. The latter is False while
        # *any* ancestor is hidden — which is exactly during construction and
        # for as long as the popover holding this input is closed, the two
        # moments the hint is first laid out. Measured on the real LutSelector:
        # the "32 / 32" chip opened at QRect(0,0,56,15) with a right margin of
        # 0, corrected to QRect(202,7,49,15) only once the user typed, and
        # regressed on every re-open.
        if not self._hint.isVisibleTo(self) or not self._hint.text():
            return 0
        return self.fontMetrics().horizontalAdvance(self._hint.text()) + theme.Spacing.md

    def _reposition_hint(self) -> None:
        # Keep room on the right so typed text never slides under the hint.
        right_margin = self._hint_width()
        m = self.textMargins()
        self.setTextMargins(m.left(), m.top(), right_margin, m.bottom())
        if self._hint.isVisibleTo(self):
            self._hint.adjustSize()
            x = self.rect().right() - self._hint.width() - theme.Spacing.sm
            y = (self.rect().height() - self._hint.height()) // 2
            self._hint.move(max(0, x), max(0, y))
            self._hint.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_hint()

    def focusInEvent(self, event):
        self._refresh_icon(active=True)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._refresh_icon(active=False)
        super().focusOutEvent(event)

    def changeEvent(self, event):
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._icon_px = max(13, round(self.fontMetrics().height() * 0.9))
            self._refresh_icon(active=self.hasFocus())
            self._reposition_hint()
        super().changeEvent(event)

    def _build_qss(self) -> str:
        c, t, r = theme.Colors, theme.Typography, theme.Radii
        return f"""
        QLineEdit#SearchInput {{
            background-color: {c.panel_elevated};
            border: 1px solid {c.border};
            border-radius: {r.sm}px;
            color: {c.text_primary};
            padding: 5px 8px;
            font-size: {t.body_size}px;
            selection-background-color: {c.accent};
            selection-color: {c.accent_fg};
        }}
        QLineEdit#SearchInput:hover {{ border-color: {c.border_strong}; }}
        QLineEdit#SearchInput:focus {{ border-color: {c.accent}; }}
        QLabel#SearchInputHint {{
            color: {c.text_muted};
            background-color: {c.panel};
            border: 1px solid {c.border_subtle};
            border-radius: {r.xs}px;
            padding: 0 4px;
            font-size: {t.caption_size}px;
        }}
        """


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget as _QW

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("SearchInput — demo")
    pad = theme.Spacing.lg
    lay = QVBoxLayout(root)
    lay.setContentsMargins(pad, pad, pad, pad)
    lay.setSpacing(theme.Spacing.md)

    title = QLabel("SearchInput")
    title.setObjectName("Title")
    lay.addWidget(title)

    s1 = SearchInput(placeholder="Search properties…")
    lay.addWidget(s1)
    s2 = SearchInput(placeholder="Filter wells…", hint="")
    lay.addWidget(s2)

    echo = QLabel("(type above)")
    echo.setObjectName("Secondary")
    lay.addWidget(echo)
    s1.textChanged.connect(lambda v: echo.setText(f"query → {v!r}"))
    lay.addStretch(1)

    root.resize(360, 180)
    root.show()
    _sys.exit(app.exec())
