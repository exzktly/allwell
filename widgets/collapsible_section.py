"""CollapsibleSection — a titled panel whose body expands/collapses smoothly.

Layout: a header row (chevron + title + optional "value preview" slot) above a
content area. Clicking the header toggles the body with an animation on the
body's ``maximumHeight``.

API
---
* ``addWidget(w)`` / ``addLayout(l)`` — append to the content area.
* ``setExpanded(bool)`` / ``isExpanded()`` — programmatic toggle (animated).
* ``title`` — Qt property (also ``setTitle`` / ``titleText``).
* ``setValueWidget(w)`` — put a small preview widget (swatch, label, ...) on the
  right of the header so a collapsed section still shows its state.
* ``toggled(bool)`` — emitted on expand/collapse.

Styled from ``theme`` tokens; chevron and paddings are font/spacing relative.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import (  # noqa: E402
    Property, QEasingCurve, QPropertyAnimation, Qt, Signal,
)
from PySide6.QtWidgets import (  # noqa: E402
    QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

import theme  # noqa: E402

_QWIDGETSIZE_MAX = 16_777_215  # Qt's QWIDGETSIZE_MAX


class _SectionHeader(QWidget):
    clicked = Signal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CollapsibleSectionHeader")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)

        m = theme.Spacing.sm
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.Spacing.md, m, theme.Spacing.md, m)
        lay.setSpacing(theme.Spacing.sm)

        self.chevron = QLabel(self)
        self.chevron.setObjectName("CollapsibleSectionChevron")
        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("CollapsibleSectionTitle")
        self.value_host = QWidget(self)
        self.value_host.setObjectName("CollapsibleSectionValue")
        self._value_layout = QHBoxLayout(self.value_host)
        self._value_layout.setContentsMargins(0, 0, 0, 0)
        self._value_layout.setSpacing(theme.Spacing.xs)

        lay.addWidget(self.chevron)
        lay.addWidget(self.title_label)
        lay.addStretch(1)
        lay.addWidget(self.value_host)

    def set_value_widget(self, w: QWidget | None) -> None:
        while self._value_layout.count():
            item = self._value_layout.takeAt(0)
            old = item.widget()
            if old is not None:
                old.setParent(None)
                old.deleteLater()
        if w is not None:
            self._value_layout.addWidget(w)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class CollapsibleSection(QWidget):
    """A titled, animated, collapsible container."""

    toggled = Signal(bool)

    # Glyphs scale with the header font, so no hardcoded pixel art.
    _CHEVRON_OPEN = "▾"   # ▾
    _CHEVRON_SHUT = "▸"   # ▸

    def __init__(self, title: str = "", parent: QWidget | None = None,
                 *, expanded: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("CollapsibleSection")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        self._expanded = bool(expanded)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = _SectionHeader(title, self)
        self._header.clicked.connect(self._on_header_clicked)

        self._body = QWidget(self)
        self._body.setObjectName("CollapsibleSectionBody")
        self._body.setAttribute(Qt.WA_StyledBackground, True)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(
            theme.Spacing.md, theme.Spacing.sm, theme.Spacing.md, theme.Spacing.md,
        )
        self._body_layout.setSpacing(theme.Spacing.sm)

        root.addWidget(self._header)
        root.addWidget(self._body)

        self._anim = QPropertyAnimation(self._body, b"maximumHeight", self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._anim.finished.connect(self._on_anim_finished)

        self.setStyleSheet(self._build_qss())
        self._apply_state(animate=False)

    # ── content API ──────────────────────────────────────────────────────
    def addWidget(self, widget: QWidget) -> None:
        self._body_layout.addWidget(widget)
        if self._expanded:
            self._body.setMaximumHeight(_QWIDGETSIZE_MAX)

    def addLayout(self, layout) -> None:
        self._body_layout.addLayout(layout)
        if self._expanded:
            self._body.setMaximumHeight(_QWIDGETSIZE_MAX)

    def contentLayout(self) -> QVBoxLayout:
        return self._body_layout

    def setValueWidget(self, widget: QWidget | None) -> None:
        self._header.set_value_widget(widget)

    # ── expand/collapse API ──────────────────────────────────────────────
    def isExpanded(self) -> bool:
        return self._expanded

    def setExpanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._apply_state(animate=True)
        self.toggled.emit(expanded)

    def toggle(self) -> None:
        self.setExpanded(not self._expanded)

    # ── title property ───────────────────────────────────────────────────
    def titleText(self) -> str:
        return self._header.title_label.text()

    def setTitle(self, text: str) -> None:
        self._header.title_label.setText(text)

    title = Property(str, titleText, setTitle)

    # ── internals ────────────────────────────────────────────────────────
    def _on_header_clicked(self) -> None:
        self.toggle()

    def _content_height(self) -> int:
        return max(0, self._body_layout.sizeHint().height())

    def _apply_state(self, *, animate: bool) -> None:
        self._header.chevron.setText(
            self._CHEVRON_OPEN if self._expanded else self._CHEVRON_SHUT
        )
        target = self._content_height() if self._expanded else 0
        if not animate:
            self._body.setVisible(self._expanded)
            self._body.setMaximumHeight(
                _QWIDGETSIZE_MAX if self._expanded else 0
            )
            return

        self._body.setVisible(True)
        start = self._body.maximumHeight()
        if start >= _QWIDGETSIZE_MAX:
            start = self._content_height()
        self._anim.stop()
        self._anim.setStartValue(int(start))
        self._anim.setEndValue(int(target))
        self._anim.start()

    def _on_anim_finished(self) -> None:
        if self._expanded:
            self._body.setMaximumHeight(_QWIDGETSIZE_MAX)
        else:
            self._body.setVisible(False)

    def _build_qss(self) -> str:
        c, t, r = theme.Colors, theme.Typography, theme.Radii
        return f"""
        #CollapsibleSection {{
            background-color: {c.panel};
            border: 1px solid {c.border_subtle};
            border-radius: {r.md}px;
        }}
        #CollapsibleSectionHeader {{
            background-color: transparent;
            border: none;
            border-top-left-radius: {r.md}px;
            border-top-right-radius: {r.md}px;
        }}
        #CollapsibleSectionHeader:hover {{
            background-color: {c.hover};
        }}
        #CollapsibleSectionTitle {{
            color: {c.text_primary};
            font-size: {t.h3_size}px;
            font-weight: {t.semibold};
            background: transparent;
        }}
        #CollapsibleSectionChevron {{
            color: {c.text_secondary};
            background: transparent;
        }}
        #CollapsibleSectionValue, #CollapsibleSectionBody {{
            background: transparent;
        }}
        """


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QLabel, QLineEdit, QVBoxLayout,
        QWidget as _QW,
    )

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("CollapsibleSection — demo")
    outer = QVBoxLayout(root)
    pad = theme.Spacing.lg
    outer.setContentsMargins(pad, pad, pad, pad)
    outer.setSpacing(theme.Spacing.md)

    s1 = CollapsibleSection("Appearance", expanded=True)
    s1.addWidget(QLabel("Trace width"))
    s1.addWidget(QComboBox())
    chk = QCheckBox("Show grid")
    chk.setChecked(True)
    s1.addWidget(chk)
    swatch = QLabel("  ")
    swatch.setFixedSize(28, 14)
    swatch.setStyleSheet(f"background-color: {theme.Colors.trace[0]}; "
                         f"border-radius: {theme.Radii.xs}px;")
    s1.setValueWidget(swatch)

    s2 = CollapsibleSection("Threshold", expanded=False)
    s2.addWidget(QLabel("Cutoff fraction"))
    s2.addWidget(QLineEdit("0.50"))
    vlabel = QLabel("0.50")
    vlabel.setObjectName("Mono")
    s2.setValueWidget(vlabel)

    s3 = CollapsibleSection("Annotations", expanded=False)
    s3.addWidget(QLabel("(empty)"))

    for s in (s1, s2, s3):
        outer.addWidget(s)
    outer.addStretch(1)

    root.resize(340, 420)
    root.show()
    _sys.exit(app.exec())
