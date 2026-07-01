"""LutSelector — pick a colour-map (LUT) for review-image display.

A trigger button showing the current LUT's gradient + name, with a reverse-LUT
toggle and a reset button next to it; clicking the trigger opens a `Popover`
with a searchable, categorised list (Perceptual / Diverging / Categorical /
Cyclic), each row a `GradientStrip` + monospace name, and a ``n / m`` match-count
in the search header.

LUTs come from matplotlib's colormap registry when available (sampled to colour
stops for the previews); if matplotlib isn't importable a small built-in set is
used so the widget still works.

API
---
* ``LutSelector(parent=None, *, lut="viridis", reversed=False)``
* ``setLut(name, reversed=False)`` / ``lut() -> str`` / ``isReversed() -> bool``
* ``lutChanged(name: str, reversed: bool)`` — emitted when the user picks a LUT,
  toggles reverse, or resets.
* ``availableLuts() -> dict[str, list[str]]`` — the category → [names] registry.

Token-styled; sizes are font-relative.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, Signal  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

import theme  # noqa: E402
from widgets.gradient_strip import GradientStrip  # noqa: E402
from widgets.popover import Popover  # noqa: E402
from widgets.search_input import SearchInput  # noqa: E402

_CATEGORIES: dict[str, list[str]] = {
    "Perceptual": ["viridis", "plasma", "inferno", "magma", "cividis",
                   "mako", "rocket", "turbo"],
    "Monochrome": ["Greys", "Greens", "Reds", "Blues", "Purples", "Oranges"],
    "Diverging":  ["coolwarm", "RdBu", "PiYG", "PRGn", "BrBG", "Spectral",
                   "RdYlBu"],
    "Categorical": ["tab10", "tab20", "Set1", "Set2", "Set3", "Paired",
                    "Dark2", "Accent"],
    "Cyclic":     ["twilight", "twilight_shifted", "hsv"],
}

# A tiny built-in fallback (used only when matplotlib isn't available, or for a
# name matplotlib doesn't have) — a 3-stop approximation per name.
_FALLBACK_STOPS: dict[str, list[str]] = {
    "viridis":  ["#440154", "#21918c", "#fde725"],
    "plasma":   ["#0d0887", "#cc4778", "#f0f921"],
    "inferno":  ["#000004", "#bc3754", "#fcffa4"],
    "magma":    ["#000004", "#b73779", "#fcfdbf"],
    "cividis":  ["#00204d", "#7c7b78", "#ffea46"],
    "coolwarm": ["#3b4cc0", "#dddddd", "#b40426"],
    "RdBu":     ["#b2182b", "#f7f7f7", "#2166ac"],
    "twilight": ["#e2d9e2", "#3f3a76", "#e2d9e2"],
    "hsv":      ["#ff0000", "#00ff00", "#0000ff"],
    "tab10":    ["#1f77b4", "#ff7f0e", "#2ca02c"],
}
_GENERIC_FALLBACK = ["#0b0f17", "#6b8afd", "#f0f4ff"]


def _cmap_stops(name: str, n: int = 24) -> list[QColor]:
    """Colour stops for *name*, sampled from matplotlib if available."""
    try:
        import matplotlib  # noqa: F401
        from matplotlib import colormaps as _mpl_cmaps
        cmap = _mpl_cmaps[name]
        out = []
        for i in range(n):
            r, g, b, _a = cmap(i / (n - 1))
            out.append(QColor(round(r * 255), round(g * 255), round(b * 255)))
        return out
    except Exception:
        fb = _FALLBACK_STOPS.get(name, _GENERIC_FALLBACK)
        return [QColor(c) for c in fb]


def _all_lut_names() -> list[str]:
    seen, out = set(), []
    for names in _CATEGORIES.values():
        for n in names:
            if n not in seen:
                seen.add(n)
                out.append(n)
    return out


class _LutRow(QWidget):
    """One row in the picker: a gradient strip + a monospace name; click → pick."""

    clicked = Signal(str)

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self.setObjectName("LutRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.Spacing.sm, theme.Spacing.xs,
                               theme.Spacing.sm, theme.Spacing.xs)
        lay.setSpacing(theme.Spacing.sm)
        strip = GradientStrip(_cmap_stops(name))
        strip.setFixedHeight(max(12, round(self.fontMetrics().height() * 0.85)))
        strip.setMinimumWidth(60)
        strip.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lbl = QLabel(name, self)
        lbl.setObjectName("Mono")
        lbl.setMinimumWidth(max(80, self.fontMetrics().horizontalAdvance("twilight_shifted")))
        lay.addWidget(strip, 1)
        lay.addWidget(lbl, 0)

    def name(self) -> str:
        return self._name

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._name)
        super().mousePressEvent(event)


class LutSelector(QWidget):
    lutChanged = Signal(str, bool)   # (name, reversed)

    def __init__(self, parent: QWidget | None = None, *,
                 lut: str = "viridis", reversed: bool = False,
                 default: str = "viridis") -> None:
        super().__init__(parent)
        self.setObjectName("LutSelector")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._default = default
        self._lut = lut if lut in _all_lut_names() else "viridis"
        self._reversed = bool(reversed)
        self._rows: list[_LutRow] = []
        self._cat_headers: list[QLabel] = []

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.Spacing.xs)

        # trigger: gradient + name (a flat button), then reverse + reset
        self._trigger = QPushButton(self)
        self._trigger.setObjectName("LutTrigger")
        self._trigger.setCursor(Qt.PointingHandCursor)
        tl = QHBoxLayout(self._trigger)
        tl.setContentsMargins(theme.Spacing.xs, 2, theme.Spacing.xs, 2)
        tl.setSpacing(theme.Spacing.sm)
        self._trigger_strip = GradientStrip(_cmap_stops(self._lut), reversed=self._reversed)
        self._trigger_strip.setFixedHeight(max(12, round(self.fontMetrics().height() * 0.85)))
        self._trigger_strip.setMinimumWidth(48)
        self._trigger_strip.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._trigger_name = QLabel(self._lut, self._trigger)
        self._trigger_name.setObjectName("Mono")
        tl.addWidget(self._trigger_strip, 1)
        tl.addWidget(self._trigger_name, 0)
        self._trigger.clicked.connect(self._open_popover)
        lay.addWidget(self._trigger, 1)

        self._reverse_btn = QToolButton(self)
        self._reverse_btn.setObjectName("LutReverse")
        self._reverse_btn.setText("⇄")
        self._reverse_btn.setCheckable(True)
        self._reverse_btn.setChecked(self._reversed)
        self._reverse_btn.setCursor(Qt.PointingHandCursor)
        self._reverse_btn.setToolTip("Reverse LUT")
        self._reverse_btn.toggled.connect(self._on_reverse_toggled)
        lay.addWidget(self._reverse_btn)

        self._reset_btn = QToolButton(self)
        self._reset_btn.setObjectName("LutReset")
        self._reset_btn.setText("Reset")
        self._reset_btn.setCursor(Qt.PointingHandCursor)
        self._reset_btn.setToolTip(f"Reset to {self._default}")
        self._reset_btn.clicked.connect(lambda: self.setLut(self._default, False))
        lay.addWidget(self._reset_btn)

        self._popover: Popover | None = None
        self.setStyleSheet(self._build_qss())

    # ── API ──────────────────────────────────────────────────────────────
    def setLut(self, name: str, reversed: bool = False) -> None:  # noqa: A002
        if name not in _all_lut_names():
            name = self._lut
        changed = (name != self._lut or bool(reversed) != self._reversed)
        self._lut = name
        self._reversed = bool(reversed)
        self._trigger_strip.setStops(_cmap_stops(self._lut))
        self._trigger_strip.setReversed(self._reversed)
        self._trigger_name.setText(self._lut)
        self._reverse_btn.blockSignals(True)
        self._reverse_btn.setChecked(self._reversed)
        self._reverse_btn.blockSignals(False)
        if changed:
            self.lutChanged.emit(self._lut, self._reversed)

    def lut(self) -> str:
        return self._lut

    def isReversed(self) -> bool:
        return self._reversed

    @staticmethod
    def availableLuts() -> dict[str, list[str]]:
        return {k: list(v) for k, v in _CATEGORIES.items()}

    # ── internals ────────────────────────────────────────────────────────
    def _on_reverse_toggled(self, on: bool) -> None:
        if on != self._reversed:
            self.setLut(self._lut, on)

    def _open_popover(self) -> None:
        if self._popover is None:
            self._popover = self._build_popover()
        # ensure the search box is cleared each open
        self._search.setText("")
        self._apply_filter("")
        self._popover.popup(self._trigger, side="bottom", align="start")

    def _build_popover(self) -> Popover:
        pop = Popover(self.window() if self.window() is not self else None)
        content = QWidget()
        v = QVBoxLayout(content)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(theme.Spacing.sm)
        self._search = SearchInput(placeholder="Filter LUTs…", hint="")
        self._search.textChanged.connect(self._apply_filter)
        v.addWidget(self._search)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMinimumHeight(max(180, round(self.fontMetrics().height() * 14)))
        scroll.setMinimumWidth(max(260, round(self.fontMetrics().horizontalAdvance("0") * 24)))
        inner = QWidget()
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(theme.Spacing.xs)
        for cat, names in _CATEGORIES.items():
            hdr = QLabel(cat.upper())
            hdr.setObjectName("Caption")
            hdr.setProperty("lutCategory", True)
            iv.addWidget(hdr)
            self._cat_headers.append(hdr)
            for nm in names:
                row = _LutRow(nm)
                row.clicked.connect(self._pick)
                iv.addWidget(row)
                self._rows.append(row)
        iv.addStretch(1)
        scroll.setWidget(inner)
        v.addWidget(scroll, 1)
        pop.setContentWidget(content)
        return pop

    def _apply_filter(self, text: str) -> None:
        q = (text or "").strip().lower()
        total = len(self._rows)
        shown = 0
        # row visibility
        for row in self._rows:
            vis = (q in row.name().lower()) if q else True
            row.setVisible(vis)
            if vis:
                shown += 1
        # category headers: hide a header if none of its rows are visible
        cat_names = list(_CATEGORIES.items())
        row_idx = 0
        for (cat, names), hdr in zip(cat_names, self._cat_headers):
            any_vis = False
            for _nm in names:
                if self._rows[row_idx].isVisible():
                    any_vis = True
                row_idx += 1
            hdr.setVisible(any_vis)
        self._search.setHintText(f"{shown} / {total}")

    def _pick(self, name: str) -> None:
        self.setLut(name, self._reversed)
        if self._popover is not None:
            self._popover.close()

    # ── style ────────────────────────────────────────────────────────────
    def _build_qss(self) -> str:
        c, r, t = theme.Colors, theme.Radii, theme.Typography
        return f"""
        #LutSelector {{ background: transparent; }}
        QPushButton#LutTrigger {{
            background-color: {c.panel_elevated};
            border: 1px solid {c.border};
            border-radius: {r.sm}px;
            padding: 2px 4px;
            text-align: left;
        }}
        QPushButton#LutTrigger:hover {{ border-color: {c.border_strong}; }}
        QPushButton#LutTrigger QLabel#Mono {{
            color: {c.text_secondary};
            font-family: {t.family_mono};
            font-size: {t.small_size}px;
        }}
        QToolButton#LutReverse, QToolButton#LutReset {{
            background-color: {c.panel_elevated};
            border: 1px solid {c.border};
            border-radius: {r.sm}px;
            color: {c.text_secondary};
            padding: 3px 8px;
            font-size: {t.small_size}px;
        }}
        QToolButton#LutReverse:hover, QToolButton#LutReset:hover {{
            background-color: {c.hover}; color: {c.text_primary};
        }}
        QToolButton#LutReverse:checked {{
            background-color: {c.accent_dim}; border-color: {c.accent}; color: {c.text_primary};
        }}
        #LutRow {{ background: transparent; border-radius: {r.xs}px; }}
        #LutRow:hover {{ background-color: {c.hover}; }}
        #LutRow QLabel#Mono {{ color: {c.text_secondary}; font-family: {t.family_mono}; font-size: {t.small_size}px; }}
        QLabel#Caption[lutCategory="true"] {{ color: {c.text_muted}; padding-top: {theme.Spacing.xs}px; }}
        """


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QFormLayout, QVBoxLayout, QWidget as _QW

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("LutSelector — demo")
    pad = theme.Spacing.lg
    outer = QVBoxLayout(root)
    outer.setContentsMargins(pad, pad, pad, pad)
    outer.setSpacing(theme.Spacing.md)
    title = QLabel("LutSelector")
    title.setObjectName("Title")
    outer.addWidget(title)

    form = QFormLayout()
    form.setSpacing(theme.Spacing.md)
    lut = LutSelector(lut="viridis")
    lut2 = LutSelector(lut="coolwarm", reversed=True, default="coolwarm")
    form.addRow("Channel GFP LUT:", lut)
    form.addRow("Channel RFP LUT:", lut2)
    outer.addLayout(form)

    echo = QLabel("(pick a LUT / toggle reverse)")
    echo.setObjectName("Secondary")
    outer.addWidget(echo)
    lut.lutChanged.connect(lambda n, rv: echo.setText(f"GFP LUT → {n}{' (reversed)' if rv else ''}"))
    lut2.lutChanged.connect(lambda n, rv: echo.setText(f"RFP LUT → {n}{' (reversed)' if rv else ''}"))
    outer.addStretch(1)

    root.resize(460, 220)
    root.show()
    _sys.exit(app.exec())
