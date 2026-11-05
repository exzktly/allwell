"""A word-wrapped label with a maximum width that reports the height it will really have.

A ``QLabel`` with ``setWordWrap(True)`` and ``setMaximumWidth(cap)`` is drawn at
no more than *cap* wide, but a layout asks a widget for its height at the width
it is *offered*, not the width it will be given: ``QLabel.heightForWidth`` is
answered for the whole offered width and ignores the cap. On a wide page the
label is offered more than *cap*, answers "one line", gets drawn two lines
tall, and the second line is cut off — or, inside a container, the whole
container is squeezed by the missing height and its other rows overlap.

:class:`CappedLabel` clamps the offered width to its own maximum before
answering, so the height it reserves is the height it needs. Use it wherever a
wrapped note is deliberately kept to a readable measure.

No All-Well coupling — a plain reusable widget (belongs in ``widgets/``).
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel


class CappedLabel(QLabel):
    """``QLabel`` whose :meth:`heightForWidth` honours :meth:`maximumWidth`."""

    def heightForWidth(self, width: int) -> int:  # Qt naming
        return super().heightForWidth(min(width, self.maximumWidth()))
