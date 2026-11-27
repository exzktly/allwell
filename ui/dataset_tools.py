"""Dataset tools: the commands about the open dataset, in one named place.

Five commands used to be five unlabelled icons in the title bar's right-hand
cluster — copy analysis, reveal the dataset's folder, storage, reset view,
reset analysis — beside six others, and in phone layout they could not be
reached at all (the icons were hidden as fat-finger targets). They are one list
now, *Dataset tools*, reached from where the dataset is named: at the desk the
last group of the dataset menu at the head of the rail, in phone layout a
full-screen list behind ``⋯`` › *Dataset tools*.

A :class:`Tool` is a label, a sentence, the callable, and whether it is
**destructive**. In the phone's list a destructive tool asks first, in an
alert in the middle of the screen, and does nothing until it is confirmed —
which is the condition the resets returned to the phone on.

Pane-free: the window builds the list and hands it over.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ui import theme

#: The caption the group carries in the dataset menu and the list's title.
TITLE = "Dataset tools"


@dataclass(frozen=True)
class Tool:
    label: str
    about: str
    run: Callable[[], None]
    destructive: bool = False


def confirmed(parent: QWidget | None, tool: Tool) -> bool:
    """Ask before a destructive tool runs. ``True`` only on an explicit yes."""
    answer = QMessageBox.question(
        parent, tool.label.rstrip("…"),
        f"{tool.about}\n\nContinue?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel)
    return answer == QMessageBox.StandardButton.Yes


class DatasetToolsList(QDialog):
    """The tools as a list of buttons, each with what it does under it.

    A window of its own, which phone layout shows full screen
    (``ui/phone_window.py``). Choosing a tool closes the list first and runs
    the tool afterwards, so the tool's own dialog is never a window opened from
    this one."""

    def __init__(self, parent: QWidget | None, tools: list[Tool]) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.setObjectName("DatasetToolsList")
        lay = QVBoxLayout(self)
        s = theme.Spacing
        lay.setContentsMargins(s.lg, s.lg, s.lg, s.lg)
        lay.setSpacing(s.sm)
        self.buttons: dict[str, QPushButton] = {}
        for tool in tools:
            button = QPushButton(tool.label, self)
            button.setProperty("variant", "destructive" if tool.destructive else "default")
            button.clicked.connect(lambda _=False, t=tool: self._choose(t))
            lay.addWidget(button)
            about = QLabel(tool.about, self)
            about.setObjectName("Muted")
            about.setWordWrap(True)
            lay.addWidget(about)
            self.buttons[tool.label] = button
        lay.addStretch(1)

    def _choose(self, tool: Tool) -> None:
        if tool.destructive and not confirmed(self, tool):
            return
        self.accept()
        QTimer.singleShot(0, tool.run)


def open_list(parent: QWidget | None, tools: list[Tool]) -> DatasetToolsList:
    """Show the list. Non-blocking: the caller's menu has already closed."""
    dialog = DatasetToolsList(parent, tools)
    dialog.setModal(True)
    dialog.show()
    return dialog
