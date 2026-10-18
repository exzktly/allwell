"""A push button as wide as the widest label it may show.

A toggle whose label changes with its state ("FOV" / "FOV ✓") can sit inside a
popover whose size is fixed at its first show and does not grow when the text
does, so the ✓ clipped. The fix is a minimum width that reserves the widest
label — and that width is read off the button's font, so it has to be read
again whenever the font changes size: phone layout's larger text, or a widget
built while that text was on and then taken back to the desk, which kept the
phone's width (115 px against 64).

So the reservation is a property of the button rather than a number its caller
measured once: :meth:`ReservedWidthButton.reserve` states the labels, and the
button re-measures them after a UI scale (``_aw_rescale``, called by
``ui.phone_shell.rescale_widgets``). It measures off its *polished* font —
the stylesheet's — so a button measured in its constructor and one measured
after a rescale agree.
"""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton


class ReservedWidthButton(QPushButton):
    """A ``QPushButton`` whose minimum width fits the widest of its labels."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self._labels: tuple[str, ...] = ()
        self._slack = 0

    def reserve(self, labels, slack: int) -> None:
        """Keep room for the widest of *labels*, plus *slack* pixels for the
        stylesheet's padding and border."""
        self._labels = tuple(labels)
        self._slack = int(slack)
        self._apply_reserve()

    def _apply_reserve(self) -> None:
        if not self._labels:
            return
        self.ensurePolished()
        fm = self.fontMetrics()
        self.setMinimumWidth(max(fm.horizontalAdvance(t) for t in self._labels)
                             + self._slack)

    def _aw_rescale(self) -> None:
        """Re-measure the labels at the new type size."""
        self._apply_reserve()
