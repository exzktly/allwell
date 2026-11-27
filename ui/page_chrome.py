"""A page's head and foot: its title with any quiet commands, and the one row of actions at its end.

The design language (``_Docs/Markdowns/DESIGN_LANGUAGE_TRACKER.md`` §2.7) gives a form page one skeleton: a page
title at the top left, cards, and one row of actions at the foot.

- :class:`PageHeader` — the page title, an optional detail line under it (a path, in the number role), and quiet
  commands at the right (Reload, Presets ▾, Sync from app).
- :class:`ActionRow` — the foot: the page's one primary action at the right, the buttons that sit beside it to
  its left, and at the left edge **why the primary action is disabled** while it is. A greyed Run with no reason
  beside it reads as broken; the reason (a line of help) says what to do instead, and is gone the moment the
  action is enabled — the row follows the button's own enabled state, so no caller has to remember to clear it.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ui import text_roles
from ui import theme


class PageHeader(QWidget):
    """The page title, an optional detail line, and quiet commands at the right."""

    def __init__(self, title: str, parent: QWidget | None = None, *, detail: str = "",
                 commands: tuple[QWidget, ...] = ()) -> None:
        super().__init__(parent)
        self.setObjectName("PageHeader")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.Spacing.sm)
        words = QVBoxLayout()
        words.setSpacing(theme.Spacing.xs // 2)
        self._title = text_roles.page_title(title, self)
        words.addWidget(self._title)
        self._detail = text_roles.number(detail, self)
        self._detail.setVisible(bool(detail))
        words.addWidget(self._detail)
        row.addLayout(words, 1)
        for command in commands:
            command.setParent(self)
            row.addWidget(command)

    def title(self) -> str:
        return self._title.text()

    def setTitle(self, text: str) -> None:  # noqa: N802 — Qt naming
        self._title.setText(text)

    def setDetail(self, text: str) -> None:  # noqa: N802 — Qt naming
        self._detail.setText(text)
        self._detail.setVisible(bool(text))


class _FollowEnabled(QObject):
    def __init__(self, row: "ActionRow") -> None:
        super().__init__(row)
        self._row = row

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 — Qt naming
        if ev.type() == QEvent.EnabledChange:
            self._row._sync()
        return False


class ActionRow(QWidget):
    """The foot of a form page: the reason the primary action is disabled, its neighbours, the primary action."""

    def __init__(self, primary: QPushButton, parent: QWidget | None = None, *,
                 others: tuple[QWidget, ...] = ()) -> None:
        super().__init__(parent)
        self.setObjectName("ActionRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.Spacing.sm)
        self._reason = text_roles.help_text("", self)
        row.addWidget(self._reason, 1)
        for other in others:
            other.setParent(self)
            row.addWidget(other)
        self._primary = primary
        primary.setParent(self)
        row.addWidget(primary)
        self._reason_text = ""
        primary.installEventFilter(_FollowEnabled(self))
        self._sync()

    def primary(self) -> QPushButton:
        return self._primary

    def setDisabledReason(self, text: str) -> None:  # noqa: N802 — Qt naming
        """What keeps the primary action disabled, in a sentence; shown only while it is."""
        self._reason_text = text
        self._sync()

    def reason(self) -> str:
        """The reason as shown now: empty while the primary action is enabled."""
        return self._reason.text() if self._reason.isVisibleTo(self) else ""

    def _sync(self) -> None:
        showing = bool(self._reason_text) and not self._primary.isEnabled()
        self._reason.setText(self._reason_text if showing else "")
        self._reason.setVisible(showing)
