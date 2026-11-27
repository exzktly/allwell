"""A status chip: a small neutral pill that names a state, with a dot of the state's colour.

The design language's chips report a state in a word and a dot — *Idle*, *Running*, *Done*, *2 alerts* — on a
neutral pill. Colour follows meaning (§2.2 rule 3) and is never the only signal, which is why the word is always
there:

- ``neutral`` — the secondary ink: idle, not checked, a plain count.
- ``running`` — the accent: in progress. Never amber; amber means something needs attention.
- ``done`` — green.
- ``attention`` — amber.
- ``failure`` — red.

A chip that only reports is not pressable; the colours are written by :func:`qss`, which the application
stylesheet includes (``ui.theme.qss``), keyed on the ``tone`` property, so nothing here caches a token.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ui import theme

TONES = ("neutral", "running", "done", "attention", "failure")
#: The dot's diameter.
DOT = 8


class StatusChip(QWidget):
    """A pill: an optional dot in the tone's colour, and the state in words."""

    def __init__(self, text: str = "", parent: QWidget | None = None, *, tone: str = "neutral",
                 dot: bool = True) -> None:
        super().__init__(parent)
        self.setProperty("statusChip", True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        half = theme.Spacing.xs // 2
        lay.setContentsMargins(theme.Spacing.sm, half, theme.Spacing.sm, half)
        lay.setSpacing(theme.Spacing.xs)
        self._dot = QLabel(self)
        self._dot.setProperty("statusDot", True)
        self._dot.setFixedSize(DOT, DOT)
        self._dot.setVisible(dot)
        self._label = QLabel(text, self)
        lay.addWidget(self._dot, 0, Qt.AlignVCenter)
        lay.addWidget(self._label, 0, Qt.AlignVCenter)
        self._tone = ""
        self.setTone(tone)

    def setText(self, text: str) -> None:  # noqa: N802 — Qt naming
        self._label.setText(text)

    def text(self) -> str:
        return self._label.text()

    def tone(self) -> str:
        return self._tone

    def setTone(self, tone: str) -> None:  # noqa: N802 — Qt naming
        if tone not in TONES:
            raise ValueError(f"{tone!r} is not a tone; the tones are {TONES}")
        self._tone = tone
        for w in (self, self._dot):
            w.setProperty("tone", tone)
            w.style().unpolish(w)
            w.style().polish(w)
        self.update()


def qss(c, t, r) -> str:
    """The chip's rules, from the token classes *c* (``Colors``), *t* (``Typography``) and *r* (``Radii``).
    Included by ``ui.theme.qss``."""
    ink = {"neutral": c.text_secondary, "running": c.accent, "done": c.success, "attention": c.warn,
           "failure": c.danger}
    rules = [f"""
QWidget[statusChip="true"] {{
    background-color: {c.control}; border-radius: {r.pill}px;
}}
QWidget[statusChip="true"] QLabel {{ background: transparent; color: {c.text_primary}; font-size: {t.small_size}px; }}
QLabel[statusDot="true"] {{ border-radius: {DOT // 2}px; }}
"""]
    for tone, colour in ink.items():
        rules.append(f'QLabel[statusDot="true"][tone="{tone}"] {{ background-color: {colour}; }}\n')
    return "".join(rules)
