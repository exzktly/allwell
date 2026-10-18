"""The title bar's storage dialog: what the app keeps on disk, and clearing it.

Qt only — which areas exist, how big they are and how each is cleared live in
:mod:`core.local_storage`, so the dialog can never list an area the clear does
not implement. Measuring and clearing both run off the GUI thread
(``core.worker_bridge.run_off_thread``): a few gigabytes of spilled cells or a
plate's row cache is a walk of thousands of files.

* :class:`StorageDialog` — the dialog. Opened non-modally by the title bar.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from core import local_storage as ls
from core.worker_bridge import run_off_thread
from util.state_dir import gui_state_dir

#: Indent of an area's description under its checkbox, matching the reset
#: dialog's (``review/core/reset_scopes_view.py``).
_DETAIL_INDENT = 20
_ROW_TOP = 6


class StorageDialog(QDialog):
    """One checkbox per :class:`core.local_storage.CacheArea`, with its size.

    *dataset_dir* is the open dataset's folder or ``None``; it adds the row
    cache area. Nothing in use is offered for deletion — that rule is
    ``core.local_storage``'s, applied when the areas are listed.
    """

    def __init__(self, parent=None, *, dataset_dir: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Storage")
        self._dataset_dir = dataset_dir
        #: The last clear's outcome, for the status line and for tests.
        self.last_result: ls.ClearResult | None = None
        #: The sentence describing that outcome. Kept, because a clear is
        #: followed by a re-measure, and the line must still say what the clear
        #: did once the new sizes are in.
        self._note = ""
        self._bridge = None
        self._sizes: dict[str, int] = {}
        self._areas: list[ls.CacheArea] = []
        self._boxes: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)
        where = QLabel(
            f"The app keeps its logs, caches and temporary files in "
            f"{gui_state_dir()}. Your images, CSVs and analysis are never "
            f"touched here, and neither are an Analyze run's scratch files.",
            self)
        where.setWordWrap(True)
        layout.addWidget(where)

        self._rows = QVBoxLayout()
        layout.addLayout(self._rows)

        self._status = QLabel("", self)
        self._status.setObjectName("Muted")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        buttons = QWidget(self)
        bl = QHBoxLayout(buttons)
        bl.setContentsMargins(0, 0, 0, 0)
        self._open_btn = QPushButton("Open folder", buttons)
        self._open_btn.setProperty("variant", "secondary")
        self._open_btn.clicked.connect(self._open_folder)
        bl.addWidget(self._open_btn)
        bl.addStretch(1)
        self._clear_btn = QPushButton("Clear selected", buttons)
        self._clear_btn.setProperty("variant", "danger")
        self._clear_btn.clicked.connect(self.clear_selected)
        bl.addWidget(self._clear_btn)
        close = QPushButton("Close", buttons)
        close.clicked.connect(self.close)
        bl.addWidget(close)
        layout.addWidget(buttons)

        self.refresh()

    # ── building and measuring ─────────────────────────────────────────

    def refresh(self) -> None:
        """Re-list the areas and measure them in the background."""
        self._areas = ls.cache_areas(self._dataset_dir)
        self._build_rows()
        self._set_working(True, "Measuring…")
        areas = list(self._areas)
        self._bridge = run_off_thread(lambda: ls.measure(areas),
                                      on_done=self._on_measured,
                                      on_failed=self._on_failed)

    def _build_rows(self) -> None:
        previous = {k: b.isChecked() for k, b in self._boxes.items()}
        while self._rows.count():
            item = self._rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._boxes = {}
        for area in self._areas:
            row = QWidget(self)
            rl = QVBoxLayout(row)
            rl.setContentsMargins(0, _ROW_TOP, 0, 0)
            rl.setSpacing(0)
            box = QCheckBox(area.label, row)
            box.setChecked(previous.get(area.key, area.default_on))
            box.toggled.connect(lambda _on: self._sync_total())
            rl.addWidget(box)
            detail = QLabel(area.detail, row)
            detail.setObjectName("Muted")
            detail.setWordWrap(True)
            detail.setContentsMargins(_DETAIL_INDENT, 0, 0, 0)
            rl.addWidget(detail)
            self._rows.addWidget(row)
            self._boxes[area.key] = box

    def _on_measured(self, sizes: dict) -> None:
        self._sizes = dict(sizes)
        for area in self._areas:
            box = self._boxes.get(area.key)
            if box is not None:
                box.setText(f"{area.label} — {ls.format_bytes(self._sizes.get(area.key, 0))}")
        self._set_working(False, self._note)
        self._sync_total()

    def _on_failed(self, message: str) -> None:
        self._set_working(False, f"Could not measure or clear: {message}")

    def _set_working(self, working: bool, text: str) -> None:
        self._clear_btn.setEnabled(not working)
        for box in self._boxes.values():
            box.setEnabled(not working)
        self._status.setText(text)

    def selected_keys(self) -> list[str]:
        """Keys of the ticked areas, in the order listed."""
        return [k for k, b in self._boxes.items() if b.isChecked()]

    def _sync_total(self) -> None:
        keys = self.selected_keys()
        total = sum(self._sizes.get(k, 0) for k in keys)
        self._clear_btn.setEnabled(bool(keys) and total > 0)
        self._clear_btn.setText(
            f"Clear {ls.format_bytes(total)}" if total else "Clear selected")

    # ── clearing ───────────────────────────────────────────────────────

    def clear_selected(self) -> None:
        """Clear the ticked areas in the background, then re-measure."""
        keys = set(self.selected_keys())
        chosen = [a for a in self._areas if a.key in keys]
        if not chosen:
            return
        self._set_working(True, "Clearing…")
        self._bridge = run_off_thread(lambda: ls.clear(chosen),
                                      on_done=self._on_cleared,
                                      on_failed=self._on_failed)

    def _on_cleared(self, result: ls.ClearResult) -> None:
        text = f"Freed {ls.format_bytes(result.freed)}."
        if result.failed:
            shown = ", ".join(Path(p).name for p in result.failed[:3])
            more = f" and {len(result.failed) - 3} more" if len(result.failed) > 3 else ""
            text += (f" {len(result.failed)} file(s) could not be removed because "
                     f"something is using them ({shown}{more}); close what holds "
                     f"them, or restart the app, and clear again.")
        self.last_result = result
        self._note = text
        self.refresh()

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(gui_state_dir())))
