"""RailNav — vertical accent-bar navigation for the left rail.

Mockup target: the eight section items (Plotting / smFISH / Statistics /
Image Table / Segmentation / Review CSV / Sample Definitions / Batch Export)
between the rail's mode-seg and the plate. Each row carries a leading
Lucide icon + label + optional trailing count chip. The active row has a
``--accent-dim`` background **and** a 2-px left accent bar drawn via
``::before`` in the mockup CSS — here we paint that as a ``QLabel`` strip
inside the row widget so QSS handles it without a custom ``paintEvent``.

One-of-N selection model. Emits ``currentChanged(key)`` when the active row
changes (either by user click or by ``setCurrentKey``). Items carry an
opaque ``key`` (string by default — caller's choice) for routing to the
corresponding ``QStackedWidget`` page.

API
---
* ``RailNav(parent=None)``
* ``addItem(label, *, icon=None, key=None, count=None) -> row``
  (returns the row widget for further tweaks; ``key`` defaults to the label.)
* ``setCurrentKey(key)`` / ``currentKey() -> str | None``
* ``setCount(key, n | None)`` — show or hide the trailing count chip.
* ``setItemEnabled(key, bool)`` — disable a row (greyed, click-inert).
* ``items() -> list[str]`` — the keys in order.
* signal ``currentChanged(key)``
* ``bindingAdapter()`` — returns ``(getter, setter, signal)``.

Note: the Q8 locked decision drops count asides for the v1 nav rows, but
the API stays in place for future use.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

import theme  # noqa: E402
from widgets import icons  # noqa: E402
from widgets.selection_chip import SelectionChip  # noqa: E402


class _RailNavRow(QFrame):
    """One item in the RailNav.

    Carries the accent-bar strip on the left + icon + label + optional
    trailing count chip. Click handling is done at the row level so the
    hit target is the whole strip, not just the label.
    """

    clicked = Signal(str)

    def __init__(self, key: str, label: str, *, icon: str | None,
                 parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("RailNavRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty("active", False)
        self.setCursor(Qt.PointingHandCursor)
        self._key = key
        self._icon_name = icon

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 2-px accent strip on the left edge — hidden by default; shown via
        # property selector ``[active="true"]``.
        self._strip = QFrame(self)
        self._strip.setObjectName("RailNavRowStrip")
        self._strip.setAttribute(Qt.WA_StyledBackground, True)
        self._strip.setProperty("active", False)
        # ~13% of body height (2px at 1×, scales on hi-dpi); architecture §7
        # mandates fontMetrics-derived sizes.
        self._strip.setFixedWidth(max(2, round(self.fontMetrics().height() * 0.13)))
        outer.addWidget(self._strip, 0)

        body = QFrame(self)
        body.setObjectName("RailNavRowBody")
        body.setAttribute(Qt.WA_StyledBackground, True)
        body.setAttribute(Qt.WA_Hover, True)
        body.setProperty("active", False)
        self._body = body
        bl = QHBoxLayout(body)
        _pad_x, _pad_y = theme.Spacing.md, theme.Spacing.sm
        bl.setContentsMargins(_pad_x, _pad_y, _pad_x, _pad_y)
        bl.setSpacing(theme.Spacing.sm)

        self._glyph = QLabel(body)
        self._glyph.setObjectName("RailNavRowGlyph")
        # ~14× viewer text height — was 15px hardcoded; scales with font.
        _gs = max(12, round(self.fontMetrics().height() * 1.0))
        self._glyph.setFixedSize(_gs, _gs)
        self._glyph.setVisible(bool(icon))

        self._label = QLabel(label, body)
        self._label.setObjectName("RailNavRowLabel")
        self._label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._chip = SelectionChip("", variant="muted", parent=body)
        self._chip.setVisible(False)

        bl.addWidget(self._glyph, 0, Qt.AlignVCenter)
        bl.addWidget(self._label, 1, Qt.AlignVCenter)
        bl.addWidget(self._chip, 0, Qt.AlignVCenter)

        outer.addWidget(body, 1)

    def key(self) -> str:
        return self._key

    def setActive(self, on: bool) -> None:
        self.setProperty("active", bool(on))
        # Mirror the property onto the body widget so QSS rules targeting
        # ``QFrame#RailNavRowBody[active="true"]`` resolve. Qt QSS's
        # descendant + property combinator is unreliable across platforms;
        # carrying the property on the body itself avoids that.
        self._body.setProperty("active", bool(on))
        # Carry the property on the strip too — the descendant +
        # property combinator (``QFrame#RailNavRow[active="true"]
        # QFrame#RailNavRowStrip``) doesn't re-evaluate reliably when
        # the parent's property flips, leaving the previous accent bar
        # painted on the first-clicked row.
        self._strip.setProperty("active", bool(on))
        for w in (self, self._body, self._strip):
            w.style().unpolish(w)
            w.style().polish(w)
        self._refresh_icon()

    def setCount(self, n: int | None) -> None:
        if n is None:
            self._chip.setVisible(False)
            return
        self._chip.setText(str(int(n)))
        self._chip.setVisible(True)

    def setIconName(self, name: str | None) -> None:
        self._icon_name = name
        self._glyph.setVisible(bool(name))
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        if not self._icon_name:
            return
        active = bool(self.property("active"))
        token = "accent" if active else "text_muted"
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        self._glyph.setPixmap(
            icons.make_pixmap(self._icon_name, token, 15, dpr or 1.0)
        )

    def mousePressEvent(self, ev):  # noqa: N802
        if ev.button() == Qt.LeftButton and self.isEnabled():
            self.clicked.emit(self._key)
        super().mousePressEvent(ev)

    def showEvent(self, ev):  # noqa: N802
        self._refresh_icon()
        super().showEvent(ev)


class RailNav(QFrame):
    currentChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RailNav")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(1)

        self._rows: dict[str, _RailNavRow] = {}
        self._order: list[str] = []
        self._current: str | None = None

        self.setStyleSheet(self._build_qss())

    # ── API ──────────────────────────────────────────────────────────────
    def addItem(self, label: str, *, icon: str | None = None,
                key: str | None = None, count: int | None = None) -> _RailNavRow:
        k = key if key is not None else label
        if k in self._rows:
            raise ValueError(f"RailNav already has key {k!r}")
        row = _RailNavRow(k, label, icon=icon, parent=self)
        row.clicked.connect(self._on_row_clicked)
        if count is not None:
            row.setCount(count)
        self._rows[k] = row
        self._order.append(k)
        self._lay.addWidget(row)
        if self._current is None:
            self._set_current_internal(k, emit=False)
        return row

    def setCurrentKey(self, key: str | None) -> None:
        if key is None or key == self._current:
            return
        if key not in self._rows:
            return
        self._set_current_internal(key, emit=True)

    def currentKey(self) -> str | None:
        return self._current

    def setCount(self, key: str, n: int | None) -> None:
        row = self._rows.get(key)
        if row is not None:
            row.setCount(n)

    def setItemEnabled(self, key: str, enabled: bool) -> None:
        row = self._rows.get(key)
        if row is not None:
            row.setEnabled(bool(enabled))

    def items(self) -> list[str]:
        return list(self._order)

    # ── binding adapter ──────────────────────────────────────────────────
    def bindingAdapter(self):
        return (self.currentKey, self.setCurrentKey, self.currentChanged)

    # ── internals ────────────────────────────────────────────────────────
    def _on_row_clicked(self, key: str) -> None:
        if key == self._current:
            return
        self._set_current_internal(key, emit=True)

    def _set_current_internal(self, key: str, *, emit: bool) -> None:
        prev = self._current
        if prev is not None:
            prev_row = self._rows.get(prev)
            if prev_row is not None:
                prev_row.setActive(False)
        self._current = key
        self._rows[key].setActive(True)
        if emit:
            self.currentChanged.emit(key)

    def _build_qss(self) -> str:
        c, t, r = theme.Colors, theme.Typography, theme.Radii
        return f"""
        QFrame#RailNav {{
            background: transparent;
            border: 0;
        }}
        QFrame#RailNavRow {{
            background: transparent;
        }}
        QFrame#RailNavRow:disabled {{ color: {c.text_faint}; }}
        QFrame#RailNavRowStrip {{
            background: transparent;
            border: 0;
        }}
        QFrame#RailNavRowStrip[active="true"] {{
            background-color: {c.accent};
            border-top-right-radius: 2px;
            border-bottom-right-radius: 2px;
        }}
        QFrame#RailNavRowBody {{
            background-color: transparent;
            border-radius: {r.sm}px;
            margin: 0 4px 0 6px;
        }}
        QFrame#RailNavRowBody:hover {{
            background-color: {c.panel};
        }}
        QFrame#RailNavRowBody[active="true"] {{
            background-color: {c.accent_dim};
        }}
        QFrame#RailNavRowBody[active="true"]:hover {{
            background-color: {c.accent_dim};
        }}
        QLabel#RailNavRowLabel {{
            color: {c.text_secondary};
            font-family: {t.family};
            font-size: {t.emph_size}px;
            font-weight: 500;
            background: transparent;
        }}
        QFrame#RailNavRow:hover QLabel#RailNavRowLabel,
        QFrame#RailNavRow[active="true"] QLabel#RailNavRowLabel {{
            color: {c.text_primary};
        }}
        """


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    host = QWidget()
    host.setObjectName("Rail")
    host.setStyleSheet(f"#Rail {{ background-color: {theme.Colors.rail}; }}")
    host.setWindowTitle("RailNav — demo")
    host.resize(280, 480)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(8, 12, 8, 12)
    lay.setSpacing(8)

    head = QLabel("SECTION", host)
    head.setStyleSheet(
        f"color: {theme.Colors.text_muted}; font-size: 11px; "
        f"letter-spacing: 0.08em; font-weight: 600;"
    )
    head.setContentsMargins(9, 0, 0, 0)
    lay.addWidget(head)

    nav = RailNav(host)
    nav.addItem("Plotting",          icon="line-chart")
    nav.addItem("smFISH",            icon="dna")
    nav.addItem("Statistics",        icon="sigma")
    nav.addItem("Image Table",       icon="layout-grid")
    nav.addItem("Segmentation",      icon="scan-line")
    nav.addItem("Review CSV",        icon="file-spreadsheet")
    nav.addItem("Sample Definitions",icon="tag")
    nav.addItem("Batch Export",      icon="boxes")
    lay.addWidget(nav)
    lay.addStretch(1)

    out = QLabel("Active: Plotting", host)
    out.setStyleSheet(f"color: {theme.Colors.text_muted}; padding: 0 9px;")
    nav.currentChanged.connect(lambda k: out.setText(f"Active: {k}"))
    lay.addWidget(out)

    host.show()
    _sys.exit(app.exec())
