"""The two reset commands and the scope-picker dialog behind one of them.

Qt builders only: what each scope actually clears lives in
``well_viewer/persistence/reset_scopes.py``, which is where a new scope should
be added. This module turns the scope table into a checklist and reports what
ran, so the dialog can never list a scope the reset does not implement.

* :func:`prompt_reset_view` — the title bar's circular arrow.
* :func:`prompt_reset_analysis` — the title bar's trash can.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QMessageBox, QVBoxLayout,
    QWidget,
)

from well_viewer.persistence.reset_scopes import ANALYSIS_SCOPES, clear_analysis, reset_view

_logger = logging.getLogger("well_viewer")


def prompt_reset_view(ctx) -> bool:
    """Confirm and reset the whole view tier. Returns ``True`` when it ran.

    One confirmation and no scope picker: nothing in this tier is a scientific
    decision, so there is no sub-selection worth offering.
    """
    resp = QMessageBox.question(
        ctx.window,
        "Reset view?",
        "This returns the view to how a freshly-opened dataset looks:\n\n"
        "  • selected wells and group visibility\n"
        "  • fold-change scopes\n"
        "  • per-tab controls, plot options, and draw order\n"
        "  • figure style and axis preferences\n"
        "  • heatmap arrangement, Multivariate configuration, Image Table grid\n\n"
        "Your sample definitions, gating, curation, and data files are left "
        "alone.\n\nContinue?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if resp != QMessageBox.Yes:
        return False
    reset_view(ctx)
    ctx.notify.set_status("View reset.")
    return True


class ResetAnalysisDialog(QDialog):
    """Checklist of analysis scopes, all ticked by default.

    Built from ``ANALYSIS_SCOPES`` rather than a hand-written list so a scope
    added to the table appears here automatically — the drift this whole
    change exists to remove starts with a dialog that lists one thing and a
    command that clears another.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Reset analysis")
        self.setModal(True)
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Choose what to discard. Each item is cleared everywhere it is "
            "saved, and the dataset is reloaded afterwards. Your image and CSV "
            "files are never touched.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._boxes: dict[str, QCheckBox] = {}
        for scope in ANALYSIS_SCOPES:
            row = QWidget(self)
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 6, 0, 0)
            row_layout.setSpacing(0)
            box = QCheckBox(scope.title, row)
            box.setChecked(True)
            row_layout.addWidget(box)
            detail = QLabel(scope.detail, row)
            detail.setObjectName("Muted")
            detail.setWordWrap(True)
            detail.setContentsMargins(20, 0, 0, 0)
            row_layout.addWidget(detail)
            layout.addWidget(row)
            self._boxes[scope.key] = box

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self)
        buttons.button(QDialogButtonBox.Ok).setText("Reset")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for box in self._boxes.values():
            box.toggled.connect(lambda _on: self._sync_ok(buttons))
        self._sync_ok(buttons)

    def _sync_ok(self, buttons: QDialogButtonBox) -> None:
        """Disable Reset when nothing is ticked — an empty reset that reports
        success is indistinguishable from one that silently failed."""
        buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self.selected_keys()))

    def selected_keys(self) -> list[str]:
        """Keys of the ticked scopes, in table order."""
        return [key for key, box in self._boxes.items() if box.isChecked()]


def prompt_reset_analysis(ctx, *, reload_after=None) -> list[str]:
    """Ask which analysis scopes to clear, clear them, and reload.

    *reload_after* is called with no arguments once the clears have run; the
    shell passes its dataset-reload callable. Returns the scope keys that
    actually completed — which is not necessarily what was ticked, since a scope
    that raises is logged and skipped so the others still run.
    """
    if not ctx.state.dataset.data_dir:
        QMessageBox.information(
            ctx.window, "No dataset", "Load a dataset before resetting.")
        return []

    dialog = ResetAnalysisDialog(ctx.window)
    if dialog.exec() != QDialog.Accepted:
        return []
    keys = dialog.selected_keys()
    if not keys:
        return []

    done = clear_analysis(ctx, keys)
    if reload_after is not None:
        try:
            reload_after()
        except Exception:
            _logger.exception("reset: reloading the dataset failed")

    titles = [s.title for s in ANALYSIS_SCOPES if s.key in done]
    if not titles:
        ctx.notify.set_status("Reset failed — see the log for details.")
    elif len(titles) == len(keys):
        ctx.notify.set_status(f"Reset: {', '.join(titles).lower()}.")
    else:
        # Never report a partial reset as a whole one: the user has to know
        # which of the things they ticked are still on disk.
        missed = [s.title for s in ANALYSIS_SCOPES
                  if s.key in keys and s.key not in done]
        ctx.notify.set_status(
            f"Reset: {', '.join(titles).lower()}. FAILED: "
            f"{', '.join(missed).lower()} — see the log.")
    return done
