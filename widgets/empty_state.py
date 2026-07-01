"""EmptyState — a centered icon + message placeholder for "nothing here yet".

Used where a figure / list / tab has no data: a large muted glyph above a
one-line tip (and an optional secondary line). Token-styled; the icon size
derives from the font (DPI-aware).

API
---
* ``EmptyState(text="", icon="image", parent=None, *, hint="")``
* ``setText(text)`` / ``setHint(text)`` / ``setIconName(name)``
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

import theme  # noqa: E402
from widgets import icons  # noqa: E402


class EmptyState(QWidget):
    def __init__(self, text: str = "", icon: str = "image", parent=None,
                 *, hint: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("EmptyState")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._icon_name = icon

        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.Spacing.lg, theme.Spacing.lg,
                                 theme.Spacing.lg, theme.Spacing.lg)
        # Anchor stack at the top with a single bottom stretch — when the
        # host card is short, content grows downward into the stretch
        # instead of overflowing past the card's bottom edge (a vertical
        # centering layout with two stretches squeezes the wrapped hint
        # off-screen when the card is constrained).

        self._glyph = QLabel(self)
        self._glyph.setAlignment(Qt.AlignCenter)
        self._text = QLabel(text, self)
        self._text.setObjectName("EmptyStateText")
        self._text.setAlignment(Qt.AlignCenter)
        self._text.setWordWrap(True)
        self._hint = QLabel(hint, self)
        self._hint.setObjectName("EmptyStateHint")
        self._hint.setAlignment(Qt.AlignCenter)
        self._hint.setWordWrap(True)
        self._hint.setVisible(bool(hint))

        outer.addSpacing(theme.Spacing.lg)
        for w, sp in ((self._glyph, theme.Spacing.md),
                      (self._text, theme.Spacing.xs),
                      (self._hint, 0)):
            outer.addWidget(w, 0, Qt.AlignHCenter)
            if sp:
                outer.addSpacing(sp)
        outer.addStretch(1)
        # Heightful hint: word-wrapped labels report a 1-line sizeHint until
        # the layout has computed the actual width, which makes the bottom
        # stretch oversize and the second wrapped line slip off the card.
        # Reserve two-line minimum height up-front.
        self._hint.setMinimumHeight(self.fontMetrics().height() * 2)

        self.setStyleSheet(self._build_qss())
        self._refresh_glyph()

    # ── API ──────────────────────────────────────────────────────────────
    def setText(self, text: str) -> None:
        self._text.setText(text or "")

    def setHint(self, text: str) -> None:
        self._hint.setText(text or "")
        self._hint.setVisible(bool(text))

    def setIconName(self, name: str) -> None:
        self._icon_name = name
        self._refresh_glyph()

    # ── internals ────────────────────────────────────────────────────────
    def _icon_px(self) -> int:
        return max(32, round(self.fontMetrics().height() * 3.0))

    def _refresh_glyph(self) -> None:
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        px = self._icon_px()
        # Without an explicit fixed size, QLabel with a scaled-pixmap can
        # report a sizeHint larger than the pixmap's logical pixels, which
        # makes the next layout row (the text) overlap the picture above.
        # Pin the label's footprint to the pixmap's logical size and turn
        # off scaledContents so the pixmap renders 1:1.
        self._glyph.setFixedSize(px, px)
        self._glyph.setScaledContents(False)
        self._glyph.setPixmap(
            icons.make_pixmap(self._icon_name, "text_faint", px, dpr or 1.0)
        )

    def showEvent(self, event):  # noqa: N802
        self._refresh_glyph()
        super().showEvent(event)

    def _build_qss(self) -> str:
        c, t = theme.Colors, theme.Typography
        return f"""
        #EmptyState {{ background: transparent; }}
        QLabel#EmptyStateText {{
            color: {c.text_secondary};
            font-size: {t.h3_size}px;
            background: transparent;
        }}
        QLabel#EmptyStateHint {{
            color: {c.text_muted};
            font-size: {t.small_size}px;
            background: transparent;
        }}
        """


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    es = EmptyState(
        "No wells or well groups selected",
        icon="grid",
        hint="Select wells on the plate, or define a group, to plot.",
    )
    es.setWindowTitle("EmptyState — demo")
    es.resize(480, 360)
    es.show()
    _sys.exit(app.exec())
