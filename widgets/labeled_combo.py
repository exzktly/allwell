"""LabeledComboBox — a QComboBox that paints its own scope label as a fixed
prefix inside the closed box ("Channel: RFP") instead of sitting beside a
separate ``QLabel``.

Only the *painted* text changes. ``currentText()``, ``currentData()``,
``currentIndex()`` and every Qt signal still report the plain item text —
this overrides ``paintEvent`` to prefix the label when drawing the closed
box, nothing about the combo's value. Existing code that reads the combo
(``.currentText()``, controller wiring, tests) is unaffected; the popup list
still shows the plain item text too, since only the closed-box paint changes.

Sizes to exactly what's painted — the current item's text plus the label
prefix — not to the widest item in the list, which is what plain QComboBox's
own ``sizeHint()`` does (``AdjustToContentsOnFirstShow``, its default policy,
still measures every item up front). A combo whose longest item is
"Fraction Threshold On" would otherwise sit that wide even while showing
"Property: N", wasting the row's space on every shorter selection. Call
sites should not add their own ``setFixedWidth`` / ``setMinimumContentsLength``
/ QSS ``min-width`` on top of this — those all fight the same goal by
flooring the box wider than its current content needs.

Use in place of a bare ``QComboBox`` wherever a control band would otherwise
pair a ``QLabel`` with the combo via ``ControlBand.add_scope(label, combo)``
— pass ``label=""`` to ``add_scope`` instead, since the combo now describes
itself.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox, QStylePainter


class LabeledComboBox(QComboBox):
    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self._label = label
        self._last_hint = QSize()

    def label(self) -> str:
        return self._label

    def setLabel(self, label: str) -> None:
        self._label = label
        self.updateGeometry()
        self.update()

    def _painted_text(self) -> str:
        # Before a caller populates the combo (or an empty dataset leaves it
        # with no items), currentText() is "" — paint the bare label rather
        # than nothing, so the box reads "Timepoint" instead of collapsing to
        # an unlabelled sliver now that its width tracks painted content.
        text = self.currentText()
        if not self._label:
            return text
        return f"{self._label}: {text}" if text else self._label

    def sizeHint(self) -> QSize:
        # Ask the current style for the full control size (frame, padding,
        # drop-down arrow) around exactly the painted text's content size —
        # correct under any QSS this widget carries, and it's what makes this
        # shrink-to-fit instead of the default's shrink-to-widest-item.
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        fm = self.fontMetrics()
        text = self._painted_text()
        content = QSize(fm.horizontalAdvance(text), fm.height())
        return self.style().sizeFromContents(QStyle.CT_ComboBox, opt, content, self)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event) -> None:
        # The painted text (and so the ideal width) can change from a route
        # that has this widget's signals blocked (population under
        # blockSignals(True), a common "seed without re-triggering the
        # handler" pattern) — currentIndexChanged won't fire then, but Qt
        # still schedules a repaint on any state change regardless of
        # blockSignals, which only suppresses signal emission. Checking here
        # catches every path uniformly instead of hooking each signal that
        # might change the text.
        hint = self.sizeHint()
        if hint != self._last_hint:
            self._last_hint = hint
            self.updateGeometry()

        painter = QStylePainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        # Same text _painted_text() sized the box for — painting anything
        # else here would size and draw out of step.
        opt.currentText = self._painted_text()
        painter.drawComplexControl(QStyle.CC_ComboBox, opt)
        painter.drawControl(QStyle.CE_ComboBoxLabel, opt)
