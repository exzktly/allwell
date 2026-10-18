"""The title bar's storage dialog: what the app keeps on disk, and clearing it.

Qt only — which areas exist, how big they are and how each is cleared live in
:mod:`core.local_storage`, so the dialog can never list an area the clear does
not implement. Listing, measuring and clearing all run off the GUI thread
(``core.worker_bridge.run_off_thread``): a few gigabytes of spilled cells, a
plate's row cache or a crowded system temp folder is a walk of thousands of
files.

* :class:`StorageDialog` — the dialog. Opened non-modally by the title bar,
  which keeps one and re-surveys it on each opening rather than building
  another.
"""

from __future__ import annotations

from collections.abc import Callable
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

    *dataset_dir* says which dataset is open: a callable returning its folder
    (or ``None``), asked afresh at every survey and at every Clear — the dialog
    is non-modal, and the user can open another dataset while it stands open.
    A plain path is accepted too. Nothing in use is offered for deletion; that
    rule is ``core.local_storage``'s, and it is applied again when Clear is
    pressed (``clear_now``).
    """

    def __init__(self, parent=None, *,
                 dataset_dir: Callable[[], Path | None] | Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Storage")
        self._dataset_fn = dataset_dir if callable(dataset_dir) else (lambda: dataset_dir)
        #: The dataset folder the rows on screen describe.
        self._listed_dataset: Path | None = None
        #: The last clear's outcome, for the status line and for tests.
        self.last_result: ls.ClearResult | None = None
        #: The sentence describing that outcome. Kept, because a clear is
        #: followed by a re-survey, and the line must still say what the clear
        #: did once the new sizes are in.
        self._note = ""
        self._working = False
        self._bridge = None
        self._sizes: dict[str, int] = {}
        self._areas: list[ls.CacheArea] = []
        self._boxes: dict[str, QCheckBox] = {}
        self._ticked: dict[str, bool] = {}

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

    # ── surveying ──────────────────────────────────────────────────────

    def refresh(self) -> None:
        """List and measure the areas in the background, then show them.

        Does nothing while a survey or a clear is already running: the one in
        flight will show the current state when it lands.
        """
        if self._working:
            return
        dataset = self._dataset_fn()
        self._set_working(True, "Measuring…")
        self._bridge = run_off_thread(
            lambda: (dataset, *ls.survey(dataset)),
            on_done=self._on_surveyed, on_failed=self._on_failed)

    def _on_surveyed(self, outcome) -> None:
        dataset, areas, sizes = outcome
        self._listed_dataset = dataset
        self._areas = list(areas)
        self._sizes = dict(sizes)
        self._build_rows()
        self._set_working(False, self._note)

    def _build_rows(self) -> None:
        self._ticked.update({k: b.isChecked() for k, b in self._boxes.items()})
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
            box = QCheckBox(
                f"{area.label} — {ls.format_bytes(self._sizes.get(area.key, 0))}", row)
            box.setChecked(self._ticked.get(area.key, area.default_on))
            box.toggled.connect(lambda _on: self._sync_clear())
            rl.addWidget(box)
            detail = QLabel(area.detail, row)
            detail.setObjectName("Muted")
            detail.setWordWrap(True)
            detail.setContentsMargins(_DETAIL_INDENT, 0, 0, 0)
            rl.addWidget(detail)
            self._rows.addWidget(row)
            self._boxes[area.key] = box

    def _on_failed(self, message: str) -> None:
        self._note = ""
        self._set_working(False, f"Could not measure or clear: {message}")

    def _set_working(self, working: bool, text: str) -> None:
        self._working = working
        for box in self._boxes.values():
            box.setEnabled(not working)
        self._status.setText(text)
        self._sync_clear()

    def selected_keys(self) -> list[str]:
        """Keys of the ticked areas, in the order listed."""
        return [k for k, b in self._boxes.items() if b.isChecked()]

    def _sync_clear(self) -> None:
        """Enable Clear when a ticked area holds anything — a file of zero bytes
        or an empty leftover folder is still something to clear."""
        keys = set(self.selected_keys())
        has_any = any(a.paths for a in self._areas if a.key in keys)
        total = sum(self._sizes.get(k, 0) for k in keys)
        self._clear_btn.setEnabled(not self._working and has_any)
        self._clear_btn.setText(
            f"Clear {ls.format_bytes(total)}" if total else "Clear selected")

    # ── clearing ───────────────────────────────────────────────────────

    def clear_selected(self) -> None:
        """Clear the ticked areas in the background, then re-survey.

        The areas are listed again when the job runs (``clear_now``), so what is
        removed is what may go *now*. If another dataset has been opened since
        the rows were shown, nothing is cleared: the rows are refreshed for the
        dataset now open, and the user presses Clear again.
        """
        keys = self.selected_keys()
        if not keys or self._working:
            return
        dataset = self._dataset_fn()
        if dataset != self._listed_dataset:
            self._note = ("Another dataset was opened; the list now shows it. "
                          "Check what is ticked and clear again.")
            self.refresh()
            return
        self._note = ""
        self._set_working(True, "Clearing…")
        self._bridge = run_off_thread(lambda: ls.clear_now(keys, dataset),
                                      on_done=self._on_cleared,
                                      on_failed=self._on_failed)

    def _on_cleared(self, result: ls.ClearResult) -> None:
        text = f"Freed {ls.format_bytes(result.freed)}."
        if result.failed:
            shown = ", ".join(Path(p).name for p in result.failed[:3])
            more = f" and {len(result.failed) - 3} more" if len(result.failed) > 3 else ""
            text += (f" {len(result.failed)} item(s) could not be removed — in use "
                     f"or protected ({shown}{more}). Close what holds them, or "
                     f"restart the app, and clear again.")
        self.last_result = result
        self._note = text
        self._working = False
        self.refresh()

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(gui_state_dir())))
