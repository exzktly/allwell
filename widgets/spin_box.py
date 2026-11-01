"""Spin boxes that a layout cannot squeeze below the width their own number needs.

Reported: the nanowell focus threshold on the Nanowells page and on the QC panel clipped its value after two or
three digits. The cause is in how a spin box's minimum width is set. The stylesheet puts a ``min-width`` floor under
a spin box's text area (``ui.theme.Metrics.spin_min_w``), and Qt's stylesheet engine turns that into an *explicit*
minimum width on the widget — 60 px at the UI size — which overrides the minimum a layout would otherwise ask the
widget for. 60 px leaves a 14 px text area once the padding and the up/down buttons are taken out. So whenever a row
of controls is given less than it asks for and its labels cannot give way, the layout shrinks every spin box in it
to that 60 px and draws the number in 14 px: "75" needs 15, "100" needs 22.

:class:`SpinBox` and :class:`DoubleSpinBox` raise that explicit minimum to their own size hint — the width their
widest value needs — after every polish and whenever their range or decimals change. (Never a prefix or suffix: the
app puts a unit in the label beside the box, ``tests/test_entry_fields_hold_only_the_value.py``.) A row short of room then wraps, scrolls or squeezes something else, never the number. They are the only
spin boxes the application constructs (``tests/test_spin_boxes_keep_their_number.py`` holds every site to that).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox

#: The events after which the stylesheet may have set the explicit minimum width again, or the widest value's width
#: may have changed with the font.
_REHOLD = (QEvent.Polish, QEvent.StyleChange, QEvent.FontChange, QEvent.Show)


class _HoldsItsNumber:
    """Mixed into a Qt spin box: keep its explicit minimum width at its size hint."""

    def _hold(self) -> None:
        width = self.sizeHint().width()
        if self.minimumWidth() != width:
            self.setMinimumWidth(width)

    def event(self, event) -> bool:  # Qt naming
        handled = super().event(event)
        if event.type() in _REHOLD:
            self._hold()
        return handled

    def setRange(self, low, high) -> None:  # Qt naming
        super().setRange(low, high)
        self._hold()

    def setMinimum(self, value) -> None:  # Qt naming
        super().setMinimum(value)
        self._hold()

    def setMaximum(self, value) -> None:  # Qt naming
        super().setMaximum(value)
        self._hold()


class SpinBox(_HoldsItsNumber, QSpinBox):
    """A ``QSpinBox`` no layout can squeeze below the width its widest value needs (module docstring)."""


class DoubleSpinBox(_HoldsItsNumber, QDoubleSpinBox):
    """A ``QDoubleSpinBox`` no layout can squeeze below the width its widest value needs (module docstring)."""

    def setDecimals(self, places: int) -> None:  # Qt naming
        super().setDecimals(places)
        self._hold()
