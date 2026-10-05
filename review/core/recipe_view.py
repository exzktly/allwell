"""*Copy analysis from another dataset…* — the dialog behind the title bar's copy button.

Qt only. What can be copied and how well it fits is
:mod:`review.domain.recipe_compute`; reading and writing are
:mod:`review.persistence.recipe`. This module asks for a source folder, shows
the fit of every scope before anything is written, copies what the user leaves
ticked, and reports what ran — the same shape as the reset dialog beside it,
and for the same reason: the checklist is built from the roster, so the dialog
cannot offer a scope the copy does not implement.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QLabel, QMessageBox,
    QVBoxLayout, QWidget,
)

from ui import theme
from review.domain.recipe_compute import check_fit
from review.persistence.recipe import (
    apply_recipe, data_dir_of, read_dataset_blocks, read_open_dataset,
)

_logger = logging.getLogger("review")


class CopyAnalysisDialog(QDialog):
    """One row per recipe scope: a checkbox and the sentence saying how it fits.

    A scope the source has nothing for, or that cannot be read, is shown
    unticked and disabled with its reason, rather than left out: a dialog that
    silently omitted the gating row would read as "this source has no gating"
    when the truth might be "its sidecar is unreadable".
    """

    def __init__(self, fits, source_label: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Copy analysis")
        self.setModal(True)
        layout = QVBoxLayout(self)
        intro = QLabel(
            f"Copy from {source_label} into the dataset open now. Each ticked "
            f"item replaces this dataset's own; saved plots are added beside "
            f"its own. The dataset is reloaded afterwards. Image and CSV files "
            f"are never touched.", self)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self._boxes: dict[str, QCheckBox] = {}
        for fit in fits:
            row = QWidget(self)
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, theme.Spacing.xs, 0, 0)
            row_layout.setSpacing(0)
            box = QCheckBox(fit.title, row)
            usable = fit.status in ("clean", "warning")
            box.setChecked(usable)
            box.setEnabled(usable)
            row_layout.addWidget(box)
            detail = QLabel(fit.describe(), row)
            detail.setObjectName("Muted")
            detail.setWordWrap(True)
            detail.setContentsMargins(theme.Spacing.lg, 0, 0, 0)
            row_layout.addWidget(detail)
            layout.addWidget(row)
            self._boxes[fit.key] = box
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self)
        buttons.button(QDialogButtonBox.Ok).setText("Copy")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for box in self._boxes.values():
            box.toggled.connect(lambda _on: self._sync_ok(buttons))
        self._sync_ok(buttons)

    def _sync_ok(self, buttons: QDialogButtonBox) -> None:
        buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self.selected_keys()))

    def selected_keys(self) -> list[str]:
        return [k for k, box in self._boxes.items()
                if box.isEnabled() and box.isChecked()]


def _same_dataset(folder: Path, data_dir: Path) -> bool:
    try:
        return data_dir_of(folder).resolve() == Path(data_dir).resolve()
    except OSError:
        return False


def prompt_copy_analysis(ctx, *, reload_after=None, folder=None) -> list[str]:
    """Pick a source, preview the fit, copy what is ticked, reload.

    *folder* skips the folder picker (a test, or a caller that already knows).
    Returns the scope keys that were copied.
    """
    data_dir = ctx.state.dataset.data_dir
    if not data_dir:
        QMessageBox.information(ctx.window, "No dataset",
                                "Open the dataset to copy analysis into first.")
        return []
    if folder is None:
        folder = QFileDialog.getExistingDirectory(
            ctx.window, "Copy analysis from which dataset?")
        if not folder:
            return []
    folder = Path(folder)
    if _same_dataset(folder, data_dir):
        QMessageBox.information(ctx.window, "Same dataset",
                                "That is the dataset already open.")
        return []
    source = read_dataset_blocks(folder)
    if ctx.persistence is not None:
        # So the preview's "replaces what this dataset has now" is measured
        # against what is really on disk, not against a save still pending.
        ctx.persistence.flush_all()
    fits = check_fit(source, read_open_dataset(ctx))
    dialog = CopyAnalysisDialog(fits, source.label, ctx.window)
    if dialog.exec() != QDialog.Accepted:
        return []
    keys = dialog.selected_keys()
    if not keys:
        return []
    result = apply_recipe(ctx, source, keys)
    if reload_after is not None:
        try:
            reload_after()
        except Exception:
            _logger.exception("copy analysis: reloading the dataset failed")
    titles = {f.key: f.title for f in fits}
    done = ", ".join(titles[k].lower() for k in result.applied)
    msg = f"Copied from {source.label}: {done or 'nothing'}."
    if result.plots_added:
        msg += f" {result.plots_added} saved plot(s) added, not yet drawn here."
    if result.failed:
        # Never report a partial copy as a whole one.
        msg += " FAILED: " + "; ".join(f"{titles.get(k, k).lower()} ({why})"
                                       for k, why in result.failed.items())
    ctx.notify.set_status(msg)
    return list(result.applied)
