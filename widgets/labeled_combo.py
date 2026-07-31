"""LabeledComboBox — a QComboBox that paints its own scope label as a fixed
prefix inside the closed box ("Channel: RFP") instead of sitting beside a
separate ``QLabel``.

Only the *painted* text changes. ``currentText()``, ``currentData()``,
``currentIndex()`` and every Qt signal still report the plain item text —
this overrides ``paintEvent`` to prefix the label when drawing the closed
box, nothing about the combo's value. Existing code that reads the combo
(``.currentText()``, controller wiring, tests) is unaffected; the popup list
still shows the plain item text too, since only the closed-box paint changes.

Use in place of a bare ``QComboBox`` wherever a control band would otherwise
pair a ``QLabel`` with the combo via ``ControlBand.add_scope(label, combo)``
— pass ``label=""`` to ``add_scope`` instead, since the combo now describes
itself.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox, QStylePainter


class LabeledComboBox(QComboBox):
    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self._label = label

    def label(self) -> str:
        return self._label

    def setLabel(self, label: str) -> None:
        self._label = label
        self.updateGeometry()
        self.update()

    def _prefix_width(self) -> int:
        if not self._label:
            return 0
        return self.fontMetrics().horizontalAdvance(f"{self._label}: ")

    def sizeHint(self):
        size = super().sizeHint()
        size.setWidth(size.width() + self._prefix_width())
        return size

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        size.setWidth(size.width() + self._prefix_width())
        return size

    def paintEvent(self, event) -> None:
        painter = QStylePainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        if self._label and opt.currentText:
            opt.currentText = f"{self._label}: {opt.currentText}"
        painter.drawComplexControl(QStyle.CC_ComboBox, opt)
        painter.drawControl(QStyle.CE_ComboBoxLabel, opt)
