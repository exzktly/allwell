"""How a menu marks the entry that is the current one.

A menu of commands holds commands. A *checkable* action in one is a tick box
in a menu — a widget the theme draws differently on every platform, and one
that toggles off when the current entry is pressed again. The page you are on,
or the job a job's input comes from, is a fact about the list, not a setting:
its entry is an ordinary command, drawn in bold, and pressing it does what
pressing any other entry does. Its text is not changed, so it reads — and is
found — the same whether it is current or not.

:func:`mark_current` sets that and records it as the ``current`` Qt property,
which is what tests read.
"""

from __future__ import annotations

from PySide6.QtGui import QAction

#: The Qt property an action carries: whether it is the current entry.
CURRENT_PROPERTY = "current"


def mark_current(action: QAction, text: str, current: bool) -> QAction:
    """Set *action*'s text to *text*, drawn as the current entry or not."""
    action.setText(text)
    font = action.font()
    font.setBold(bool(current))
    action.setFont(font)
    action.setProperty(CURRENT_PROPERTY, bool(current))
    return action


def is_current(action: QAction) -> bool:
    return bool(action.property(CURRENT_PROPERTY))
