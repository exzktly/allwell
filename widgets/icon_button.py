"""IconButton — a compact icon-only (or icon+text) QToolButton.

Renders a glyph from :mod:`widgets.icons` and recolors it per interaction
state: secondary text colour at rest, primary on hover, accent when checked
(state pixmaps baked into the ``QIcon``). Sizes derive from the font, and the
icon is rendered at the widget's device pixel ratio.

API additions over ``QToolButton``:
* ``IconButton(name, parent=None, *, size=None, tooltip="", checkable=False, text="")``
* ``setIconName(name)`` — swap the glyph.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QSize, Qt  # noqa: E402
from PySide6.QtWidgets import QToolButton  # noqa: E402

import theme  # noqa: E402
from widgets import icons  # noqa: E402


class IconButton(QToolButton):
    def __init__(self, name: str, parent=None, *, size: int | None = None,
                 tooltip: str = "", checkable: bool = False, text: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("IconButton")
        self._icon_name = name
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setCheckable(checkable)
        self.setAutoRaise(True)
        if tooltip:
            self.setToolTip(tooltip)
        if text:
            self.setText(text)
            self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        else:
            self.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._px = int(size) if size else max(14, round(self.fontMetrics().height() * 1.0))
        self._refresh_icon()
        self.setStyleSheet(self._build_qss())

    def setIconName(self, name: str) -> None:
        self._icon_name = name
        self._refresh_icon()

    def iconName(self) -> str:
        return self._icon_name

    def setKbdHint(self, text: str):
        """Wrap this button + a trailing KbdHint glyph into a QFrame.

        Returns the wrapper widget; drop it into a layout in place of the
        bare button. Equivalent to ``KbdHint.attach(self, text)`` — kept on
        IconButton for discoverability.
        """
        from widgets.kbd_hint import KbdHint as _KbdHint
        return _KbdHint.attach(self, text)

    def setIconPixels(self, px: int) -> None:
        self._px = int(px)
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        self.setIcon(icons.make_icon(self._icon_name, self._px, dpr=dpr or 1.0))
        self.setIconSize(QSize(self._px, self._px))

    # Re-render at the right DPR if the window moves to another screen.
    def showEvent(self, event):  # noqa: N802
        self._refresh_icon()
        super().showEvent(event)

    def _build_qss(self) -> str:
        c, t, r = theme.Colors, theme.Typography, theme.Radii
        pad = max(3, round(theme.Spacing.xs / 1))
        return f"""
        QToolButton#IconButton {{
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: {r.sm}px;
            color: {c.text_secondary};
            padding: {pad}px {pad + 2}px;
            font-size: {t.small_size}px;
            font-weight: {t.medium};
        }}
        QToolButton#IconButton:hover {{
            background-color: {c.hover};
            color: {c.text_primary};
        }}
        QToolButton#IconButton:pressed {{ background-color: {c.active}; }}
        QToolButton#IconButton:checked {{
            background-color: {c.accent_dim};
            border-color: {c.accent};
            color: {c.text_primary};
        }}
        QToolButton#IconButton:disabled {{ color: {c.text_faint}; }}
        """


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import (
        QApplication, QButtonGroup, QHBoxLayout, QLabel, QVBoxLayout,
        QWidget as _QW,
    )

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("IconButton — demo")
    pad = theme.Spacing.lg
    lay = QVBoxLayout(root)
    lay.setContentsMargins(pad, pad, pad, pad)
    lay.setSpacing(theme.Spacing.md)

    title = QLabel("IconButton")
    title.setObjectName("Title")
    lay.addWidget(title)

    lay.addWidget(QLabel("Plot toolbar group (pan/zoom are checkable + exclusive):"))
    bar = QHBoxLayout()
    bar.setSpacing(theme.Spacing.xs)
    home = IconButton("home", tooltip="Reset view")
    back = IconButton("arrow-left", tooltip="Back")
    fwd = IconButton("arrow-right", tooltip="Forward")
    pan = IconButton("move", tooltip="Pan", checkable=True)
    zoom = IconButton("zoom-in", tooltip="Zoom", checkable=True)
    save = IconButton("download", tooltip="Export")
    grp = QButtonGroup(root)
    grp.setExclusive(True)
    grp.addButton(pan)
    grp.addButton(zoom)
    for b in (home, back, fwd, pan, zoom, save):
        bar.addWidget(b)
    bar.addStretch(1)
    coords = QLabel("x = —   ·   y = —")
    coords.setObjectName("Mono")
    bar.addWidget(coords)
    lay.addLayout(bar)

    lay.addWidget(QLabel("With text:"))
    row2 = QHBoxLayout()
    row2.addWidget(IconButton("search", text="Search", tooltip="Search"))
    row2.addWidget(IconButton("plus", text="Add", tooltip="Add"))
    row2.addWidget(IconButton("sliders", text="Properties"))
    row2.addStretch(1)
    lay.addLayout(row2)
    lay.addStretch(1)

    root.resize(440, 240)
    root.show()
    _sys.exit(app.exec())
