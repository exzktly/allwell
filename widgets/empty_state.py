"""EmptyState — a centered icon + message placeholder for "nothing here yet".

Used where a figure / list / tab has no data: a large muted glyph above a
one-line tip (and an optional secondary line). Token-styled; the icon size
derives from the font (DPI-aware).

API
---
* ``EmptyState(text="", icon="image", parent=None, *, hint="")``
* ``setText(text)`` / ``setHint(text)`` / ``setIconName(name)``
* ``setAction(label, on_click)`` / ``clearAction()`` — an optional button under
  the hint, for a placeholder on a page where the way out is a single action.
  Absent unless asked for, so a site that only describes its emptiness looks
  exactly as it did. Note that the shared refresher
  (``well_viewer.core.ui_helpers._apply_reason``) rewrites only the text, hint
  and icon, so an action set once at build time survives every refresh.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

import theme
from widgets import icons


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
        # Only the fixed-size glyph is horizontally centered. The two word-wrap
        # labels take the FULL width (their text is centered internally): a
        # ``Qt.AlignHCenter`` here would size each label to its one-line sizeHint
        # width, defeating the layout's ``heightForWidth`` so a wrapped second
        # line has no height reserved and overlaps the first (the reported
        # collision on the Multivariate empty state).
        outer.addWidget(self._glyph, 0, Qt.AlignHCenter)
        outer.addSpacing(theme.Spacing.md)
        outer.addWidget(self._text, 0)
        outer.addSpacing(theme.Spacing.xs)
        outer.addWidget(self._hint, 0)
        # Horizontally centred like the glyph rather than stretched: a
        # full-width button on a page whose whole content is three centred
        # lines reads as a banner, not as the one thing to press.
        self._action = QPushButton("", self)
        self._action.setObjectName("EmptyStateAction")
        self._action.setVisible(False)
        self._action_slot = None
        outer.addSpacing(theme.Spacing.md)
        outer.addWidget(self._action, 0, Qt.AlignHCenter)
        outer.addStretch(1)
        # Belt-and-suspenders against the same 1-line-sizeHint quirk: reserve a
        # two-line minimum for each wrap label, sized to that label's OWN font
        # (the title renders at the larger h3 size, so the widget's default
        # font metrics would under-reserve it).
        from PySide6.QtGui import QFont, QFontMetrics
        _title_f = QFont(self.font())
        _title_f.setPixelSize(theme.Typography.h3_size)
        self._text.setMinimumHeight(QFontMetrics(_title_f).height() * 2)
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

    def setAction(self, label: str, on_click) -> None:
        """Show a button under the hint, replacing any previously set one.

        For a placeholder whose way out is one action the page can perform
        itself. Most sites have no such action — the hint names a control
        somewhere else — and those keep no button at all.
        """
        if self._action_slot is not None:
            self._action.clicked.disconnect(self._action_slot)
            self._action_slot = None
        self._action.setText(label or "")
        if callable(on_click):
            def _slot(_checked=False, _fn=on_click):
                _fn()
            self._action.clicked.connect(_slot)
            self._action_slot = _slot
        self._action.setVisible(bool(label))

    def clearAction(self) -> None:
        if self._action_slot is not None:
            self._action.clicked.disconnect(self._action_slot)
            self._action_slot = None
        self._action.setText("")
        self._action.setVisible(False)

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
        # ``text_muted`` rather than ``text_faint``: against the panel surface
        # the faint token measures about 2:1, which on a dark theme reads as an
        # absent glyph rather than a quiet one. This is the only picture on an
        # otherwise empty page.
        self._glyph.setPixmap(
            icons.make_pixmap(self._icon_name, "text_muted", px, dpr or 1.0)
        )

    def showEvent(self, event):
        self._refresh_glyph()
        super().showEvent(event)

    def _build_qss(self) -> str:
        # The hint carries the action — what to click to leave this state — so it
        # cannot be the least legible thing on the page. On ``text_muted`` it
        # measured about 3.2:1 against the panel surface, under the 4.5:1 that
        # 12px text needs. It moves to ``text_secondary``, which puts it on the
        # title's colour, so the hierarchy is carried by size and weight instead:
        # colour alone was doing a job the contrast budget could not afford.
        c, t = theme.Colors, theme.Typography
        return f"""
        #EmptyState {{ background: transparent; }}
        QLabel#EmptyStateText {{
            color: {c.text_secondary};
            font-size: {t.h3_size}px;
            font-weight: {t.semibold};
            background: transparent;
        }}
        QLabel#EmptyStateHint {{
            color: {c.text_secondary};
            font-size: {t.small_size}px;
            background: transparent;
        }}
        /* The optional action, when a site sets one. Same shape as the app's
           other secondary buttons — this is the way out of an empty page, not
           a call to action competing with the message above it. */
        QPushButton#EmptyStateAction {{
            background: {c.panel_elevated};
            border: 1px solid {c.border};
            border-radius: {theme.Radii.sm}px;
            color: {c.text_primary};
            padding: {theme.Spacing.xs}px {theme.Spacing.lg}px;
        }}
        QPushButton#EmptyStateAction:hover {{
            background: {c.hover};
            border-color: {c.border_strong};
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
