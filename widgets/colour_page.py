"""The one way to pick a colour: swatches, then a custom colour, on one page.

Picking a colour was a chain of pop-ups — a panel, a popover of swatches over
it, and a colour-picker popover over that for a custom colour.
:class:`ColourPage` is one page holding both halves: the palette's swatches at
the top, and under them the custom picker (a saturation/value square, a hue
strip and a hex field) that the swatch row's *Custom* tile used to open in a
pop-up of its own. Nothing opens on top of it.

:attr:`colorPicked` carries the colour on every change — a swatch pressed, the
square or the strip dragged, a hex value entered. A caller that only wants the
finished choice reads :meth:`color` when the page is left.

Built from the same parts as ``widgets/color_picker_popover.py`` (``SvSquare``,
``HueStrip``) and ``widgets/color_swatch_row.py``. Qt-only, no All-Well
knowledge.
"""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression, Signal
from PySide6.QtGui import QColor, QRegularExpressionValidator
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from ui import theme
from widgets.color_picker_popover import HueStrip, SvSquare
from widgets.color_swatch_row import ColorSwatchRow


#: The custom picker's smallest size: the square about 22 characters by 10
#: lines of the page's font and never under 120 × 110 px, the hue strip two
#: characters wide and never under 12 px — what ``color_picker_popover`` uses.
_SV_MIN_W, _SV_CHARS, _SV_MIN_H, _SV_LINES = 120, 22, 110, 10
_HUE_MIN_W, _HUE_CHARS = 12, 2


def hex_of(color: QColor) -> str:
    return f"#{color.red():02X}{color.green():02X}{color.blue():02X}"


class ColourPage(QWidget):
    """Swatches over a custom picker; :attr:`colorPicked` on every change."""

    colorPicked = Signal(QColor)

    def __init__(self, parent: QWidget | None = None, *, colors=None, recents=None) -> None:
        super().__init__(parent)
        self.setObjectName("ColourPage")
        self._color = QColor(theme.Colors.accent)
        self._updating = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.Spacing.sm)
        self.swatches = ColorSwatchRow(colors, self, allow_custom=False, recents=recents)
        self.swatches.colorPicked.connect(self._on_swatch)
        outer.addWidget(self.swatches)
        caption = QLabel("Custom", self)
        caption.setProperty("textRole", "label")
        outer.addWidget(caption)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.Spacing.sm)
        fm = self.fontMetrics()
        self.sv_square = SvSquare(self)
        self.sv_square.setMinimumSize(max(_SV_MIN_W, fm.averageCharWidth() * _SV_CHARS),
                                      max(_SV_MIN_H, fm.height() * _SV_LINES))
        self.hue_strip = HueStrip(self)
        self.hue_strip.setMinimumWidth(max(_HUE_MIN_W, fm.averageCharWidth() * _HUE_CHARS))
        row.addWidget(self.sv_square, 1)
        row.addWidget(self.hue_strip, 0)
        outer.addLayout(row)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.hex_edit = QLineEdit(self)
        self.hex_edit.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"#?([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})"), self.hex_edit))
        self.hex_edit.setPlaceholderText("#RRGGBB")
        form.addRow("Hex", self.hex_edit)
        outer.addLayout(form)
        self.sv_square.svChanged.connect(lambda _s, _v: self._on_square())
        self.hue_strip.hueChanged.connect(self._on_hue)
        self.hex_edit.editingFinished.connect(self._on_hex)
        self._show(self._color)

    # ── API ──────────────────────────────────────────────────────────────
    def setColor(self, color) -> None:
        """Show *color* as the current one. Emits nothing: filling a page is
        not the user choosing."""
        color = QColor(color)
        if color.isValid():
            self._color = color
            self._show(color)

    def color(self) -> QColor:
        return QColor(self._color)

    # ── internals ────────────────────────────────────────────────────────
    def _show(self, color: QColor) -> None:
        self._updating = True
        try:
            hue, sat, val, _a = color.getHsvF()
            if hue < 0:
                hue = self.hue_strip.hue()      # achromatic: keep the strip where it was
            self.hue_strip.setHue(hue)
            self.sv_square.setHue(hue)
            self.sv_square.setSv(sat, val)
            self.hex_edit.setText(hex_of(color))
            self.swatches.setCurrentColor(color)
        finally:
            self._updating = False

    def _pick(self, color: QColor) -> None:
        self._color = QColor(color)
        self._show(self._color)
        self.colorPicked.emit(QColor(self._color))

    def _on_swatch(self, color: QColor) -> None:
        if not self._updating and QColor(color).isValid():
            self._pick(QColor(color))

    def _from_square(self) -> QColor:
        sat, val = self.sv_square.sv()
        return QColor.fromHsvF(min(0.999, max(0.0, self.hue_strip.hue())), sat, val)

    def _on_square(self) -> None:
        if not self._updating:
            self._color = self._from_square()
            self._updating = True
            try:
                self.hex_edit.setText(hex_of(self._color))
            finally:
                self._updating = False
            self.colorPicked.emit(QColor(self._color))

    def _on_hue(self, hue: float) -> None:
        if not self._updating:
            self.sv_square.setHue(hue)
            self._on_square()

    def _on_hex(self) -> None:
        if self._updating:
            return
        text = self.hex_edit.text().strip()
        color = QColor(text if text.startswith("#") else f"#{text}")
        if color.isValid() and color.rgb() != self._color.rgb():
            self._pick(color)
