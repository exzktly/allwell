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

A host that wants the box to keep one width whatever is selected — a pair of
axis pickers whose row should not jump as the choice changes — calls
:meth:`LabeledComboBox.setFitsWidest`: the box then sizes to its widest entry
(label prefix included), and changes width only when its entries change.

Either way the **popup** is wide enough for its longest entry. A Qt popup is
only as wide as the closed box, so a box sized to a short current entry would
otherwise open a list whose longer entries are cut off and cannot be read.

Use in place of a bare ``QComboBox`` wherever a control band would otherwise
pair a ``QLabel`` with the combo via ``ControlBand.add_scope(label, combo)``
— pass ``label=""`` to ``add_scope`` instead, since the combo now describes
itself.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox, QStyleOptionViewItem, QStylePainter


class LabeledComboBox(QComboBox):
    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self._label = label
        self._label_shown = True
        self._fits_widest = False
        self._last_hint = QSize()

    def label(self) -> str:
        return self._label

    def setLabel(self, label: str) -> None:
        self._label = label
        self.updateGeometry()
        self.update()

    def setLabelShown(self, shown: bool) -> None:
        """Paint the label prefix (the default) or only the value — for a host
        that shows the label beside the box itself (a form's label column).
        Without its label the box also gives up its fit-to-content minimum, so
        a long value elides in a narrow row rather than widening it."""
        self._label_shown = bool(shown)
        self.updateGeometry()
        self.update()

    def labelShown(self) -> bool:
        return self._label_shown

    def setFitsWidest(self, fits: bool) -> None:
        """Size the box to its widest entry (``True``) rather than to the one
        selected (the default), so its width does not change with the choice."""
        self._fits_widest = bool(fits)
        self.updateGeometry()
        self.update()

    def fitsWidest(self) -> bool:
        return self._fits_widest

    def _painted_text(self, text: str | None = None) -> str:
        # Before a caller populates the combo (or an empty dataset leaves it
        # with no items), currentText() is "" — paint the bare label rather
        # than nothing, so the box reads "Timepoint" instead of collapsing to
        # an unlabelled sliver now that its width tracks painted content.
        # *text* is an entry other than the current one, as the box would
        # paint it were it selected (what setFitsWidest measures).
        text = self.currentText() if text is None else text
        if not self._label or not self._label_shown:
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
        if self._fits_widest:
            width = max([fm.horizontalAdvance(self._painted_text(self.itemText(i))) for i in range(self.count())]
                        or [fm.horizontalAdvance(self._painted_text())])
        else:
            width = fm.horizontalAdvance(self._painted_text())
        content = QSize(width, fm.height())
        return self.style().sizeFromContents(QStyle.CT_ComboBox, opt, content, self)

    def minimumSizeHint(self) -> QSize:
        if not self._label_shown:
            return QComboBox.minimumSizeHint(self)
        return self.sizeHint()

    def showPopup(self) -> None:
        # The list opens at least as wide as its longest entry. Qt sizes the
        # popup to the closed box, which here may be fitted to a short entry.
        # Qt widens a popup only to the widest entry's *text* measured in the
        # box's font, which leaves out what each row adds around it (the
        # menu-style row's check column, the sheet's item padding), so a long
        # entry is still cut off. Every row is asked for its own width instead:
        # ``sizeHintForColumn`` would read only the rows already on screen.
        self.view().setMinimumWidth(self.popup_width())
        super().showPopup()

    def popup_width(self) -> int:
        """The width the popup's list needs to show every entry whole."""
        view = self.view()
        model, column = self.model(), self.modelColumn()
        option = QStyleOptionViewItem()
        view.initViewItemOption(option)
        delegate = view.itemDelegate()
        rows = [delegate.sizeHint(option, model.index(i, column)).width() for i in range(self.count())]
        need = max(rows or [0]) + 2 * view.frameWidth()
        if self.count() > self.maxVisibleItems():
            need += self.style().pixelMetric(QStyle.PM_ScrollBarExtent, None, view)
        return need

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
