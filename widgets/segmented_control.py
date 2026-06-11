"""SegmentedControl — a pill-style, mutually-exclusive button group.

A flat "track" containing 2+ checkable segments where exactly one is active.
Used for scope pickers (All / Plot 1 / Plot 2), per-plot view switchers
(Line / Bar / Scatter / …), error-band modes, etc.

API
---
* ``addSegment(text, icon=None, data=None) -> int`` — append a segment, returns
  its index.
* ``setCurrentIndex(i)`` / ``currentIndex()`` — programmatic selection.
* ``currentData()`` / ``segmentText(i)`` — convenience accessors.
* ``count()`` — number of segments.
* ``currentChanged(int)`` — emitted when the active segment changes.

Token-styled; segment height/padding derive from the font, so it scales with
DPI / font scaling.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QSize, Qt, Signal  # noqa: E402
from PySide6.QtGui import QIcon, QPainter  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QButtonGroup, QHBoxLayout, QSizePolicy, QStyle, QStyleOptionToolButton,
    QToolButton, QWidget,
)

import theme  # noqa: E402


class _Segment(QToolButton):
    def __init__(self, text: str, icon: QIcon | None, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setObjectName("Segment")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if text:
            self.setText(text)
        if icon is not None and not icon.isNull():
            self.setIcon(icon)
            self.setToolButtonStyle(
                Qt.ToolButtonTextBesideIcon if text else Qt.ToolButtonIconOnly
            )
            ih = max(12, round(self.fontMetrics().height() * 0.95))
            self.setIconSize(QSize(ih, ih))

    def minimumSizeHint(self):  # noqa: N802
        # Force the parent layout to reserve room for the full text + icon
        # so segments never truncate. fm.horizontalAdvance includes kerning;
        # add the icon + spacing + padding the paintEvent uses.
        fm = self.fontMetrics()
        tw = fm.horizontalAdvance(self.text()) if self.text() else 0
        iw = self.iconSize().width() if self.icon() is not None and not self.icon().isNull() else 0
        spacing = 6 if (iw and tw) else 0
        pad_h = 22   # 10 left + 10 right + 2 border slack (matches QSS padding 5x10).
        pad_v = 14
        return QSize(tw + iw + spacing + pad_h,
                     fm.height() + pad_v)

    def paintEvent(self, _ev):  # noqa: N802
        """Paint the panel via the platform style, then draw the icon + text
        group centred horizontally in the button's content rect.

        QToolButton with ``ToolButtonTextBesideIcon`` left-anchors its content
        across styles; ``text-align: center`` in QSS only affects QPushButton.
        Drawing the content manually is the most reliable cross-platform way
        to centre Review / Analyze labels in their Expanding segments.
        """
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QPalette

        p = QPainter(self)
        try:
            opt = QStyleOptionToolButton()
            self.initStyleOption(opt)
            # Suppress the style's own icon + text rendering so we can place
            # them centred ourselves.
            opt.text = ""
            opt.icon = QIcon()
            self.style().drawComplexControl(QStyle.CC_ToolButton, opt, p, self)

            cr = self.rect()
            spacing = 6
            text = self.text()
            icon = self.icon()
            isize = self.iconSize()
            has_icon = icon is not None and not icon.isNull() and \
                self.toolButtonStyle() != Qt.ToolButtonTextOnly
            has_text = bool(text) and self.toolButtonStyle() != Qt.ToolButtonIconOnly

            fm = self.fontMetrics()
            tw = fm.horizontalAdvance(text) if has_text else 0
            iw = isize.width() if has_icon else 0
            total_w = iw + (spacing if has_icon and has_text else 0) + tw
            x = cr.left() + max(0, (cr.width() - total_w) // 2)
            y_centre = cr.center().y()

            if has_icon:
                ix = x
                iy = y_centre - isize.height() // 2
                mode = QIcon.Mode.Normal if self.isEnabled() else QIcon.Mode.Disabled
                if self.isChecked():
                    mode = QIcon.Mode.Selected
                icon.paint(p, QRect(ix, iy, isize.width(), isize.height()),
                           Qt.AlignCenter, mode)
                x += iw + spacing

            if has_text:
                col = self.palette().color(
                    QPalette.ButtonText if self.isEnabled() else QPalette.Disabled
                )
                # Honour QSS-resolved text colour by querying the palette via
                # the current style option (the platform style filled it in).
                if opt.palette is not None:
                    col = opt.palette.color(QPalette.ButtonText)
                p.setPen(col)
                p.drawText(QRect(x, cr.top(), tw, cr.height()),
                           Qt.AlignVCenter | Qt.AlignLeft, text)
        finally:
            p.end()


class SegmentedControl(QWidget):
    """Pill-style exclusive group of segments."""

    currentChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SegmentedControl")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        pad = max(2, round(theme.Spacing.xs / 2))
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(pad, pad, pad, pad)
        self._layout.setSpacing(pad)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[_Segment] = []
        self._data: list[object] = []
        self._current = -1

        self._group.idClicked.connect(self._on_clicked)
        self.setStyleSheet(self._build_qss())

    # ── API ──────────────────────────────────────────────────────────────
    def addSegment(self, text: str = "", icon: QIcon | None = None,
                   data: object | None = None) -> int:
        idx = len(self._buttons)
        btn = _Segment(text, icon, self)
        self._layout.addWidget(btn, 1)
        self._group.addButton(btn, idx)
        self._buttons.append(btn)
        self._data.append(data)
        if self._current < 0:
            self.setCurrentIndex(0)
        return idx

    def count(self) -> int:
        return len(self._buttons)

    def currentIndex(self) -> int:
        return self._current

    def setCurrentIndex(self, index: int) -> None:
        if index < 0 or index >= len(self._buttons):
            return
        if index == self._current:
            # Make sure the visual state matches even on a no-op set.
            self._buttons[index].setChecked(True)
            return
        self._current = index
        self._buttons[index].setChecked(True)
        self.currentChanged.emit(index)

    def currentData(self) -> object | None:
        if 0 <= self._current < len(self._data):
            return self._data[self._current]
        return None

    def setCurrentByData(self, value) -> None:
        """Select the segment whose ``data`` equals *value* (no-op if none)."""
        for i, d in enumerate(self._data):
            if d == value:
                self.setCurrentIndex(i)
                return

    def bindingAdapter(self):
        """``(getter, setter, change_signal)`` for binding-driven panels — the
        bound value is the segment ``data`` (pass ``data=`` to ``addSegment``)."""
        return (self.currentData, self.setCurrentByData, self.currentChanged)

    def segmentText(self, index: int) -> str:
        if 0 <= index < len(self._buttons):
            return self._buttons[index].text()
        return ""

    def setSegmentEnabled(self, index: int, enabled: bool) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].setEnabled(enabled)

    # ── internals ────────────────────────────────────────────────────────
    def _on_clicked(self, idx: int) -> None:
        if idx != self._current:
            self._current = idx
            self.currentChanged.emit(idx)

    def _build_qss(self) -> str:
        c, t, r = theme.Colors, theme.Typography, theme.Radii
        return f"""
        #SegmentedControl {{
            background-color: {c.panel};
            border: 1px solid {c.border_subtle};
            border-radius: {r.sm}px;
        }}
        #SegmentedControl QToolButton#Segment {{
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: {r.xs}px;
            color: {c.text_secondary};
            padding: 5px 10px;
            font-size: {t.small_size}px;
            font-weight: {t.medium};
            text-align: center;
            qproperty-toolButtonStyle: ToolButtonTextBesideIcon;
        }}
        #SegmentedControl QToolButton#Segment:hover {{
            color: {c.text_primary};
        }}
        #SegmentedControl QToolButton#Segment:checked {{
            background-color: {c.panel_elevated};
            border-color: {c.border_subtle};
            color: {c.text_primary};
        }}
        #SegmentedControl QToolButton#Segment:disabled {{
            color: {c.text_faint};
        }}
        """


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget as _QW

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("SegmentedControl — demo")
    pad = theme.Spacing.lg
    lay = QVBoxLayout(root)
    lay.setContentsMargins(pad, pad, pad, pad)
    lay.setSpacing(theme.Spacing.md)

    title = QLabel("SegmentedControl")
    title.setObjectName("Title")
    lay.addWidget(title)

    scope = SegmentedControl()
    for name in ("All", "Plot 1", "Plot 2"):
        scope.addSegment(name)
    lay.addWidget(scope)

    view = SegmentedControl()
    for name in ("Line", "Bar", "Scatter", "Dist", "Heat"):
        view.addSegment(name, data=name.lower())
    view.setCurrentIndex(2)
    lay.addWidget(view)

    err = SegmentedControl()
    for name in ("SD", "SEM"):
        err.addSegment(name)
    err.setCurrentIndex(1)
    err.setSegmentEnabled(0, True)
    lay.addWidget(err)

    echo = QLabel("currentChanged → (interact above)")
    echo.setObjectName("Secondary")
    lay.addWidget(echo)
    scope.currentChanged.connect(lambda i: echo.setText(f"scope → {i} ({scope.segmentText(i)})"))
    view.currentChanged.connect(lambda i: echo.setText(f"view → {i} (data={view.currentData()})"))
    err.currentChanged.connect(lambda i: echo.setText(f"err → {i} ({err.segmentText(i)})"))
    lay.addStretch(1)

    root.resize(420, 280)
    root.show()
    _sys.exit(app.exec())
