"""*Copy analysis from another dataset…* — the dialog behind the title bar's copy button.

Qt only. What can be copied and how well it fits is
:mod:`review.domain.recipe_compute`; reading and writing are
:mod:`review.persistence.recipe`. The button opens a menu
(:func:`copy_analysis_menu`) with three actions: copy from another dataset
folder, copy from a recipe file, and save this dataset's analysis as a recipe
file. Copying asks for the source, shows the fit of every scope before anything
is written, copies what the user leaves ticked, and reports what ran — the same
shape as the reset dialog beside it, and for the same reason: the checklist is
built from the roster, so the dialog cannot offer a scope the copy does not
implement. A recipe file is read into the same blocks a folder is, so the two
sources go through one preview and one copy.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QLabel, QMenu, QMessageBox,
    QVBoxLayout, QWidget,
)

from ui import theme
from review.domain.recipe_compute import RECIPE_FILE_SUFFIX, check_fit
from review.persistence.recipe import (
    apply_recipe, data_dir_of, read_dataset_blocks, read_open_dataset,
    read_recipe_file, write_recipe_file,
)

#: The file-dialog filter for recipe files.
RECIPE_FILTER = f"All-Well recipe (*{RECIPE_FILE_SUFFIX});;JSON (*.json)"

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


def copy_analysis_menu(ctx, parent: QWidget, *, reload_after=None) -> QMenu:
    """The copy button's menu. Shown with ``popup`` by the caller, never ``exec``."""
    menu = QMenu(parent)
    menu.setAttribute(Qt.WA_DeleteOnClose)
    menu.addAction("Copy analysis from another dataset…",
                   lambda: prompt_copy_analysis(ctx, reload_after=reload_after))
    menu.addAction("Copy analysis from a recipe file…",
                   lambda: prompt_copy_analysis(ctx, reload_after=reload_after,
                                                recipe_file=""))
    menu.addSeparator()
    menu.addAction("Save this dataset's analysis as a recipe file…",
                   lambda: prompt_export_recipe(ctx))
    return menu


def prompt_export_recipe(ctx, *, path=None) -> Path | None:
    """Write the open dataset's analysis to a recipe file. Returns where, or ``None``.

    *path* skips the save dialog (a test, or a caller that already knows).
    """
    data_dir = ctx.state.dataset.data_dir
    if not data_dir:
        QMessageBox.information(ctx.window, "No dataset",
                                "Open the dataset whose analysis to save first.")
        return None
    if ctx.persistence is not None:
        # The file must hold what the dataset holds now, not a save still pending.
        ctx.persistence.flush_all()
    source = read_open_dataset(ctx)
    if path is None:
        suggested = Path(data_dir) / f"{source.label}{RECIPE_FILE_SUFFIX}"
        path, _ = QFileDialog.getSaveFileName(
            ctx.window, "Save analysis as a recipe file", str(suggested), RECIPE_FILTER)
        if not path:
            return None
    try:
        written = write_recipe_file(source, path)
    except (OSError, ValueError) as exc:
        ctx.notify.set_status(f"Recipe file NOT saved: {exc}.")
        return None
    ctx.notify.set_status(f"Saved this dataset's analysis to {written.name}.")
    return written


def _pick_source(ctx, folder, recipe_file):
    """The source the user chose, as blocks, or ``None`` after saying why not."""
    if recipe_file is not None:
        if not recipe_file:
            recipe_file, _ = QFileDialog.getOpenFileName(
                ctx.window, "Copy analysis from which recipe file?", "", RECIPE_FILTER)
            if not recipe_file:
                return None
        try:
            return read_recipe_file(recipe_file)
        except ValueError as exc:
            QMessageBox.warning(ctx.window, "Not a recipe file",
                                f"{Path(recipe_file).name}: {exc}.")
            return None
    if folder is None:
        folder = QFileDialog.getExistingDirectory(
            ctx.window, "Copy analysis from which dataset?")
        if not folder:
            return None
    folder = Path(folder)
    if _same_dataset(folder, ctx.state.dataset.data_dir):
        QMessageBox.information(ctx.window, "Same dataset",
                                "That is the dataset already open.")
        return None
    return read_dataset_blocks(folder)


def prompt_copy_analysis(ctx, *, reload_after=None, folder=None,
                         recipe_file=None) -> list[str]:
    """Pick a source, preview the fit, copy what is ticked, reload.

    The source is a dataset folder, or a recipe file when *recipe_file* is given
    (``""`` asks for one). *folder* / a non-empty *recipe_file* skip the picker
    (a test, or a caller that already knows). Returns the scope keys copied.
    """
    data_dir = ctx.state.dataset.data_dir
    if not data_dir:
        QMessageBox.information(ctx.window, "No dataset",
                                "Open the dataset to copy analysis into first.")
        return []
    source = _pick_source(ctx, folder, recipe_file)
    if source is None:
        return []
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
