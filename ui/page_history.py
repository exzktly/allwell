"""The window's Back and Forward: where you were, and buttons to go there.

:class:`PageHistory` keeps a :class:`core.nav_history.NavHistory` of the
places the window reports — it asks ``location()`` after every move it is
told about (:meth:`PageHistory.record`) — and goes back or forward through
``go(place)``. It knows nothing of the panes: the window hands it the two
callables and connects the signals that mean "the user moved" (a mode switch,
Review's section, a section's page tab) to :meth:`record`, which is how this
module stays in ``ui/`` (§3, Rule 3).

Every button it makes (:meth:`make_button`) is enabled exactly when its
direction can go somewhere, and disabled — not hidden — otherwise, so the
pair keeps its place in the bar. Desk and phone layout each get their own pair
from the same history.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable

from PySide6.QtCore import QObject, Signal

from core.nav_history import NavHistory
from core.safe import swallowed

#: The shortcuts the window binds (``allwell._install_shortcuts``), named in
#: the buttons' tooltips.
BACK_SHORTCUT, FORWARD_SHORTCUT = "Ctrl+Left", "Ctrl+Right"


class PageHistory(QObject):
    #: What :meth:`can_back` / :meth:`can_forward` answer may have changed.
    changed = Signal()

    def __init__(self, parent: QObject | None, *, location: Callable[[], Hashable],
                 go: Callable[[Hashable], None]) -> None:
        super().__init__(parent)
        self._location = location
        self._go = go
        self.history = NavHistory()
        self.history.add_listener(self.changed.emit)

    def record(self, *_args) -> None:
        """The user may have moved: record where the window is now."""
        if self.history.is_replaying():
            return
        try:
            place = self._location()
        except RuntimeError:
            swallowed()  # read while a pane was being torn down
            return
        self.history.visit(place)

    def can_back(self) -> bool:
        return self.history.can_back()

    def can_forward(self) -> bool:
        return self.history.can_forward()

    def back(self) -> None:
        self._replay(self.history.back())

    def forward(self) -> None:
        self._replay(self.history.forward())

    def _replay(self, place) -> None:
        if place is None:
            return
        with self.history.replaying():
            self._go(place)

    def make_button(self, direction: str, parent=None):
        """An ``IconButton`` going *direction* (``"back"`` or ``"forward"``),
        enabled only while that direction has somewhere to go."""
        from widgets.icon_button import IconButton
        back = direction == "back"
        btn = IconButton("chevron-left" if back else "chevron-right", parent,
                         tooltip=(f"Back to the previous page ({BACK_SHORTCUT})" if back
                                  else f"Forward again ({FORWARD_SHORTCUT})"))
        # Keeps the object name "IconButton", which its own style is keyed on.
        btn.setProperty("historyDirection", direction)
        btn.clicked.connect(self.back if back else self.forward)
        can = self.can_back if back else self.can_forward

        def _sync() -> None:
            try:
                btn.setEnabled(can())
            except RuntimeError:
                swallowed()  # the button went with its window
        self.changed.connect(_sync)
        _sync()
        return btn
