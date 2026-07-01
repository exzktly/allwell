"""SelectionChip — small pill-shaped count chip.

Used in two places from the mockup:

* **Plate header** (``accent`` variant) — ``"2 / 96"`` with a leading check
  icon, accent text on accent-dim background.
* **Rail saved-list h6** (``muted`` variant) — ``"[3]"`` with no icon, faint
  text, transparent bg.

Token-styled; never carries internal layout other than (icon · text). Pure
display widget — emits no signals.

API
---
* ``SelectionChip(text="", *, icon=None, variant="accent", parent=None)``
* ``setText(text)``
* ``setIconName(name | None)``
* ``setVariant("accent" | "muted")``
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget,
)

import theme  # noqa: E402
from widgets import icons  # noqa: E402

_VARIANTS = ("accent", "muted")


class SelectionChip(QFrame):
    def __init__(self, text: str = "", *, icon: str | None = None,
                 variant: str = "accent", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SelectionChip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self._variant = variant if variant in _VARIANTS else "accent"
        self._icon_name = icon

        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.Spacing.sm, 2, theme.Spacing.sm, 2)
        lay.setSpacing(theme.Spacing.xs)

        self._glyph = QLabel(self)
        self._glyph.setVisible(False)
        self._label = QLabel(text, self)
        self._label.setObjectName("SelectionChipText")
        self._label.setAlignment(Qt.AlignCenter)

        lay.addWidget(self._glyph, 0, Qt.AlignVCenter)
        lay.addWidget(self._label, 0, Qt.AlignVCenter)

        self.setStyleSheet(self._build_qss())
        self._refresh_icon()

    # ── API ──────────────────────────────────────────────────────────────
    def setText(self, text: str) -> None:
        self._label.setText(text or "")

    def text(self) -> str:
        return self._label.text()

    def setIconName(self, name: str | None) -> None:
        self._icon_name = name
        self._refresh_icon()

    def setVariant(self, variant: str) -> None:
        if variant not in _VARIANTS or variant == self._variant:
            return
        self._variant = variant
        self.setStyleSheet(self._build_qss())
        self._refresh_icon()

    # ── internals ────────────────────────────────────────────────────────
    def _icon_color_token(self) -> str:
        return "accent" if self._variant == "accent" else "text_faint"

    def _refresh_icon(self) -> None:
        if not self._icon_name:
            self._glyph.setVisible(False)
            self._glyph.clear()
            return
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        size = max(9, round(self.fontMetrics().height() * 0.7))
        self._glyph.setPixmap(
            icons.make_pixmap(self._icon_name, self._icon_color_token(), size, dpr or 1.0)
        )
        self._glyph.setFixedSize(size, size)
        self._glyph.setVisible(True)

    def showEvent(self, event):  # noqa: N802
        self._refresh_icon()
        super().showEvent(event)

    def _build_qss(self) -> str:
        c, t, r = theme.Colors, theme.Typography, theme.Radii
        if self._variant == "accent":
            bg = c.accent_dim
            border = QColor(c.accent)
            border.setAlphaF(0.30)
            border_css = f"1px solid rgba({border.red()},{border.green()},{border.blue()},{border.alphaF():.2f})"
            fg = c.accent
        else:
            bg = "transparent"
            border_css = "0"
            fg = c.text_faint
        return f"""
        QFrame#SelectionChip {{
            background-color: {bg};
            border: {border_css};
            border-radius: {r.pill}px;
        }}
        QLabel#SelectionChipText {{
            color: {fg};
            font-family: {t.family_mono};
            font-size: 10px;
            font-weight: 600;
            background: transparent;
            border: 0;
        }}
        """


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QVBoxLayout

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    host = QWidget()
    host.setWindowTitle("SelectionChip — demo")
    host.resize(420, 240)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(24, 24, 24, 24)
    lay.setSpacing(12)

    lay.addWidget(QLabel("accent variant (plate-head):"))
    row1 = QHBoxLayout()
    row1.addWidget(SelectionChip("2 / 96", icon="check", variant="accent"))
    row1.addWidget(SelectionChip("12 / 96", icon="check", variant="accent"))
    row1.addWidget(SelectionChip("96 / 96", icon="check", variant="accent"))
    row1.addStretch(1)
    lay.addLayout(row1)

    lay.addWidget(QLabel("muted variant (rail-h6 count):"))
    row2 = QHBoxLayout()
    row2.addWidget(SelectionChip("3", variant="muted"))
    row2.addWidget(SelectionChip("12", variant="muted"))
    row2.addStretch(1)
    lay.addLayout(row2)

    lay.addStretch(1)
    host.show()
    _sys.exit(app.exec())
