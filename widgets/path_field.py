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

from ui import theme
from widgets.icons import make_icon

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_FIELD_MIN_W = 180

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
        self.setMinimumWidth(_FIELD_MIN_W)

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
        self._browse_btn = QToolButton(row)
        self._browse_btn.setIcon(make_icon("folder-open"))
        self._browse_btn.setAutoRaise(True)
        self._browse_btn.setToolTip("Browse…")
        self._browse_btn.clicked.connect(self._browse)
        row_layout.addWidget(self._browse_btn)
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
        """Re-run the validator and show its message, if any. Also clears
        any :meth:`show_note` override — a real re-validation (a fresh path,
        or the host explicitly re-checking) always wins over a stale note."""
        self._set_hint_style("Danger")
        message = self._validity_message()
        self._hint.setText(message)
        self._hint.setVisible(bool(message))

    def show_note(self, text: str) -> None:
        """Show *text* as a neutral, non-error inline note in place of the
        validator's own hint, until the next :meth:`revalidate` call.

        For a destination whose validator would otherwise re-show the exact
        "already has content" wording a *pre-run* pick gets — except the
        content is now this run's own successful output, so the same red
        warning reads as "something went wrong" rather than "this destination
        is now spoken for; pick a new one to run again."
        """
        self._set_hint_style("Muted")
        self._hint.setText(text)
        self._hint.setVisible(bool(text))

    def show_warning(self, text: str) -> None:
        """Show *text* as a red inline hint, in place of the validator's own,
        until the next :meth:`revalidate` call.

        The counterpart to :meth:`show_note`, for a rule the validator cannot
        reach: a cross-field one. A validator sees a single path, so "this
        destination is also the source" can only be decided by the page — and
        without somewhere to say it, the page's Run button goes dark with no
        reason attached.
        """
        self._set_hint_style("Danger")
        self._hint.setText(text)
        self._hint.setVisible(bool(text))

    def _set_hint_style(self, object_name: str) -> None:
        if self._hint.objectName() == object_name:
            return
        self._hint.setObjectName(object_name)
        style = self._hint.style()
        style.unpolish(self._hint)
        style.polish(self._hint)

    def setLocked(self, locked: bool) -> None:
        """Disable only the Browse button, not the whole widget.

        ``setEnabled(False)`` used to be how every page locked a field for a
        run's duration — it dims the *entire* widget, including the path
        text, which is already read-only (``self._edit`` above) and has
        nothing interactive about it to disable in the first place. The
        result was the one piece of context a user watching a long run most
        wants to check — which folder is this actually running against —
        rendered at the lowest contrast on the page. Locking only the
        button that actually does something keeps the path fully legible.
        """
        self._browse_btn.setEnabled(not locked)

    def path(self) -> str:
        return self._path

    def message(self) -> str:
        return self._validity_message()

    def is_valid(self) -> bool:
        return bool(self._path) and self._validity_message() == ""
