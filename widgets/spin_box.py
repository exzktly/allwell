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
The arrows, the wheel and ``setValue`` still emit at once; only typing waits. While typed text is not yet the value in
force it is shown in italics (:meth:`_HoldsItsNumber.pending`), so a number waiting for its pause is never mistaken
for one already applied — a pause in the middle of a number commits what is there, and the change of style says so.
``tests/test_spin_boxes_commit_typed_values_once.py`` holds it.

**A number the box will not take is refused out loud.** Typed text outside the range (20 in a box that takes 30 to
95, or 96 in one that takes up to 95) cannot be committed, and Qt's own answer on Enter or on leaving the box is to put the
last value back without a word — so a user who typed 20 and clicked away saw the box snap back to 75 and could only
conclude their edits were being thrown away. That was reported twice on the rim focus boxes. Qt calls :meth:`fixup`
exactly then, so the box emits ``refused(text)`` there — and a number over the maximum is kept as typed
(:meth:`validate`) rather than losing the keystroke that took it over, which turned 96 into 9 and committed that — and the screen that owns it says what range it takes
(:meth:`range_text`). After a pause the box commits a number it takes, refuses one no further digit could
rescue, and leaves a partial one (the 4 on the way to 45) as typed rather than refusing it under the user's fingers.
``tests/test_spin_boxes_say_what_they_refuse.py`` holds it.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QTimer, Signal
from PySide6.QtGui import QValidator
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
        self._commit_pause.timeout.connect(self._commit_if_acceptable)
        self.lineEdit().textEdited.connect(lambda _text: self._commit_pause.start())
        # Typed but not yet in force: italics until it commits or is put back.
        self.lineEdit().textChanged.connect(lambda _text: self._show_pending())
        self.valueChanged.connect(lambda _value: self._show_pending())

    def pending(self) -> bool:
        """Whether the text in the field is not the value in force: typed and not yet committed."""
        text = self.lineEdit().text().strip()
        if text == self.textFromValue(self.value()).strip():
            return False
        if self.validate(text, 0)[0] != QValidator.Acceptable:
            return True
        return self.valueFromText(text) != self.value()

    def _commit_if_acceptable(self) -> None:
        """The pause's commit. A number the box takes is committed; one that more typing could still make a number it
        takes (the 4 on the way to 45) is left as typed; one that no further digit can rescue (20 in a box that takes
        30 to 95, or 96 in a box that takes up to 95) is refused now — put back, and said — rather than left in
        italics for ever."""
        text = self.lineEdit().text()
        if self.validate(text, 0)[0] == QValidator.Acceptable:
            self.interpretText()
        elif not any(self.validate(text + digit, 0)[0] == QValidator.Acceptable for digit in "0123456789"):
            self.interpretText()

    def validate(self, text, pos):  # Qt naming
        """Qt's answer, except that a number outside the range is *Intermediate*, never *Invalid*.

        Qt refuses the keystroke that takes a number over the maximum, so typing 96 into a box that takes up to 95
        left "9" — and committed 9, a number the user never typed, without a word. Kept as typed instead, the
        number is refused out loud on Enter, focus-out or the pause (:meth:`fixup`, module docstring)."""
        result = super().validate(text, pos)
        if result[0] == QValidator.Invalid:
            try:
                number = float(str(text).strip())
            except ValueError:
                return result
            if number > self.maximum() or number < self.minimum():
                return (QValidator.Intermediate, text, pos)
        return result

    def fixup(self, text):  # Qt naming
        """Qt calls this on Enter or focus-out when the text is not a value the box takes, just before it puts the
        last value back: say so (module docstring)."""
        typed = str(text).strip()
        if typed and typed != self.textFromValue(self.value()).strip():
            self.refused.emit(typed)
        return super().fixup(text)

    def range_text(self) -> str:
        """The range the box takes, as a person would write it: ``"30–95"``."""
        return f"{self.textFromValue(self.minimum())}–{self.textFromValue(self.maximum())}"

    def _show_pending(self) -> None:
        font = self.lineEdit().font()
        pending = self.pending()
        if font.italic() != pending:
            font.setItalic(pending)
            self.lineEdit().setFont(font)

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

    #: Typed text the box refused and replaced with its last value (module docstring).
    refused = Signal(str)


class DoubleSpinBox(_HoldsItsNumber, QDoubleSpinBox):
    """A ``QDoubleSpinBox`` no layout can squeeze below the width its widest value needs (module docstring)."""

    #: Typed text the box refused and replaced with its last value (module docstring).
    refused = Signal(str)

    def setDecimals(self, places: int) -> None:  # Qt naming
        super().setDecimals(places)
        self._hold()
