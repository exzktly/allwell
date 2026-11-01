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

They also commit a typed value only when it is finished rather than on every keystroke
(``setKeyboardTracking(False)``). Qt's default emits ``valueChanged`` for each digit, so typing "100" into a box wired
to a recompute ran it for 1, then 10, then 100: a two-bin histogram drawn on the way to two hundred, a DBSCAN pass per
digit. "Finished" is Enter, the box losing focus, or a pause of :data:`TYPING_PAUSE_MS` in typing. The pause is not
optional: a click on a plot canvas, a tool button or a tab bar ends no edit, and a window that is not the active one
has no focus to lose, so without it a number typed and then left was silently dropped — reported on the Nanowells
page's Rim focus box, whose first change (made with Enter) applied and whose later ones (ended by a click) did not.
The arrows, the wheel and ``setValue`` still emit at once; only typing waits.
``tests/test_spin_boxes_commit_typed_values_once.py`` holds it.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox

#: How long a pause in typing commits the number typed (module docstring): long enough that typing "60" at an
#: ordinary pace commits 60 and not 6 first — the Cell Gating panel's text bounds use the same second
#: (``qc/tab_view._TYPING_PAUSE_MS``).
TYPING_PAUSE_MS = 1000

#: The events after which the stylesheet may have set the explicit minimum width again, or the widest value's width
#: may have changed with the font.
_REHOLD = (QEvent.Polish, QEvent.StyleChange, QEvent.FontChange, QEvent.Show)


class _HoldsItsNumber:
    """Mixed into a Qt spin box: keep its explicit minimum width at its size hint, and commit typing once."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setKeyboardTracking(False)          # one valueChanged per typed value, not per keystroke (docstring)
        # ...committed on Enter, on focus-out, or after a pause in typing. ``textEdited`` is the user's typing only, so
        # a refresh from state (``setValue``, :meth:`show_value`) never starts the pause.
        self._commit_pause = QTimer(self)
        self._commit_pause.setSingleShot(True)
        self._commit_pause.setInterval(TYPING_PAUSE_MS)
        self._commit_pause.timeout.connect(self.interpretText)
        self.lineEdit().textEdited.connect(lambda _text: self._commit_pause.start())

    def _hold(self) -> None:
        width = self.sizeHint().width()
        if self.minimumWidth() != width:
            self.setMinimumWidth(width)

    def event(self, event) -> bool:  # Qt naming
        handled = super().event(event)
        if event.type() in _REHOLD:
            self._hold()
        return handled

    def show_value(self, value) -> None:
        """Show *value* as a refresh from state would: set it only when the box does not already hold it.

        ``setValue`` rewrites the text field even when the value is unchanged, so a refresh that ran while the user
        was typing — a redraw, a tile arriving — threw the typed number away before Enter could commit it. With
        keyboard tracking off the box's value stays the committed one until Enter or focus-out, so an equal value
        here means the state has not moved and the field is left as the user has it."""
        if self.value() != value:
            self.setValue(value)

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
