"""Card — the design language's raised surface: the card fill, a hairline edge, the medium radius, and padding.

Design language §2.1 rule 5: three surfaces — canvas, card, control — and "a card has a hairline; nothing else
draws a box". A page groups what belongs together into cards (Analyze's run foot and Run card, a Datasets tool's
*Folders* and *What to copy*, Batch Export's *Groups* and *Output*), each optionally headed by a section heading.

API
---
* ``Card(title="", parent=None, *, commands=())`` — *title* is a section heading at the top left; *commands* are
  widgets set at the right of that heading row (a status chip, a quiet "Show log").
* ``body()`` — the card's content layout (a ``QVBoxLayout`` on its own body widget), for the caller to fill.
* ``heading()`` — the heading row's ``QHBoxLayout``, or ``None`` for a card with no heading.

Its plain containers are transparent, so a row built from a bare ``QWidget`` does not paint the canvas across
the card (the rule is in :func:`qss`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui import theme


class Card(QFrame):
    """A titled (or untitled) raised panel."""

    def __init__(self, title: str = "", parent: QWidget | None = None, *,
                 commands: tuple[QWidget, ...] = ()) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        pad = theme.Spacing.lg
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(pad, theme.Spacing.md, pad, theme.Spacing.md)
        self._layout.setSpacing(theme.Spacing.sm)
        self._heading: QHBoxLayout | None = None
        self._title: QLabel | None = None
        if title or commands:
            self._heading = QHBoxLayout()
            self._heading.setSpacing(theme.Spacing.sm)
            if title:
                self._title = QLabel(title, self)
                self._title.setProperty("textRole", "sectionHeading")
                self._heading.addWidget(self._title)
            self._heading.addStretch(1)
            for command in commands:
                command.setParent(self)
                self._heading.addWidget(command)
            self._layout.addLayout(self._heading)
        # A widget of its own, so a child's parentWidget() is the body and its layout() the one it sits in.
        self._body_widget = QWidget(self)
        self._body_widget.setObjectName("CardBody")
        self._body = QVBoxLayout(self._body_widget)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(theme.Spacing.sm)
        self._layout.addWidget(self._body_widget)

    def body(self) -> QVBoxLayout:
        return self._body

    def heading(self) -> QHBoxLayout | None:
        return self._heading

    def title(self) -> str:
        return self._title.text() if self._title is not None else ""


def qss() -> str:
    c, r = theme.Colors, theme.Radii
    return f"""
    QFrame#Card {{
        background-color: {c.card};
        border: 1px solid {c.hairline};
        border-radius: {r.lg}px;
    }}
    QFrame#Card .QWidget {{ background: transparent; }}
    """
