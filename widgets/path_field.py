"""A labelled path picker: caption + a compact read-only box (basename, full
path on hover) + an icon-only Browse button, with an optional inline validity
hint.

Unifies two near-duplicate private widgets that grew independently —
``datasets/concatenate/view.py::_FolderField`` (folder-only, with a validator
and an inline ``Danger`` hint so three of these can sit on one equation row
and each still say which field is wrong) and
``datasets/ome_import/view.py::_PathField`` (an injected ``pick`` callable so
the same shape serves a file dialog too, no hint). No All-Well knowledge —
only ``theme`` and ``widgets.icons`` — so it lives under ``widgets/`` rather
than a Datasets-local module (CLAUDE.md §3).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import theme
from widgets.icons import make_icon

# The picker callable's shape: given the field's current path and the widget
# to parent a dialog to, return the newly chosen path ("" for "cancelled").
Picker = Callable[[str, "QWidget | None"], str]


def folder_picker(prompt: str) -> Picker:
    """Build a folder-picker ``Picker`` for :class:`PathField`.

    The dialog **must** be parented — a bare unparented ``QFileDialog`` on
    macOS (the ship target) opens as a detached window instead of an app
    sheet — so the returned closure takes and forwards *parent* rather than
    hardcoding ``None``.
    """
    def _pick(current: str, parent: QWidget | None = None) -> str:
        return QFileDialog.getExistingDirectory(
            parent, prompt, current or "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )

    return _pick


class PathField(QWidget):
    """Caption + path box + Browse button, with an optional inline hint."""

    changed = Signal()

    def __init__(
        self,
        title: str,
        *,
        pick: Picker,
        validator: Callable[[Path], str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._pick = pick
        self._validator = validator
        self._path = ""
        self.setMinimumWidth(180)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(theme.Spacing.xs)

        label = QLabel(title, self)
        label.setObjectName("Muted")
        label.setWordWrap(True)
        col.addWidget(label)

        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(theme.Spacing.xs)
        self._edit = QLineEdit(row)
        self._edit.setReadOnly(True)
        self._edit.setPlaceholderText("(not set)")
        self._edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row_layout.addWidget(self._edit, 1)
        browse = QToolButton(row)
        browse.setIcon(make_icon("folder-open"))
        browse.setAutoRaise(True)
        browse.setToolTip("Browse…")
        browse.clicked.connect(self._browse)
        row_layout.addWidget(browse)
        col.addWidget(row)

        self._hint = QLabel("", self)
        self._hint.setObjectName("Danger")
        self._hint.setWordWrap(True)
        self._hint.setVisible(False)
        col.addWidget(self._hint)

    def _browse(self) -> None:
        chosen = self._pick(self._path, self)
        if chosen:
            self.set_path(chosen)
            self.changed.emit()

    def set_path(self, path: str) -> None:
        self._path = path
        self._edit.setText(Path(path).name or path)
        self._edit.setToolTip(path)
        self.revalidate()

    def _validity_message(self) -> str:
        if not self._path or self._validator is None:
            return ""
        return self._validator(Path(self._path))

    def revalidate(self) -> None:
        message = self._validity_message()
        self._hint.setText(message)
        self._hint.setVisible(bool(message))

    def path(self) -> str:
        return self._path

    def message(self) -> str:
        return self._validity_message()

    def is_valid(self) -> bool:
        return bool(self._path) and self._validity_message() == ""
