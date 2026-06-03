"""QC debris-gate controls, embedded in the Segmentation tab.

The Segmentation panel is the QC editing home: it already renders a reference
image with a per-cell include/exclude overlay, so tuning the debris gate here
shows live which cells it removes (they render excluded, same as a manual
delete) and applies to the whole dataset at once. Editing is live — each change
recomputes the per-cell ``QC_Pass`` verdict, re-flags every plot/stat/the Review
table, and redraws the image.

The verdict is non-destructive and composes with manual curation (effective =
manual-keep AND passes-QC). Flat-field correction is a run-time step, not part of
this gate.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QWidget,
)

from .controller import get_qc_params, qc_excluded_count, set_qc_params
from .params import QCParams


def _num(text: str) -> float:
    try:
        return float(str(text).strip() or "0")
    except (TypeError, ValueError):
        return 0.0


def _fmt(v: Optional[float]) -> str:
    return "" if v is None else f"{v:g}"


def build_qc_gate_controls(app, parent: QWidget) -> QWidget:
    """A compact QC debris-gate row for the Segmentation panel sidebar.

    Returns the row widget (already laid out). Edits apply live to the whole
    dataset and refresh the review image so excluded cells show immediately.
    """
    row = QWidget(parent)
    hl = QHBoxLayout(row)
    hl.setContentsMargins(8, 2, 8, 2)
    hl.setSpacing(6)

    params = get_qc_params(app)

    enable_cb = QCheckBox("QC debris gate", row)
    enable_cb.setChecked(bool(params.enabled))
    enable_cb.setToolTip(
        "Flag cells outside the size / solidity bounds as debris (non-"
        "destructive). Applies to the whole dataset; excluded cells show here "
        "and drop out of every plot/stat. Composes with manual deletes.")
    hl.addWidget(enable_cb)

    hl.addWidget(QLabel("min area", row))
    min_area_edit = QLineEdit(_fmt(params.min_area), row)
    hl.addWidget(min_area_edit)
    hl.addWidget(QLabel("max area", row))
    max_area_edit = QLineEdit(_fmt(params.max_area), row)
    hl.addWidget(max_area_edit)
    hl.addWidget(QLabel("min solidity", row))
    min_sol_edit = QLineEdit(_fmt(params.min_solidity), row)
    hl.addWidget(min_sol_edit)
    for w in (min_area_edit, max_area_edit, min_sol_edit):
        w.setFixedWidth(70)
        w.setPlaceholderText("none")

    status = QLabel("", row)
    status.setObjectName("Muted")
    hl.addWidget(status)
    hl.addStretch(1)

    # stash refs so persistence / external recomputes can refresh the row
    app._qc_gate_widgets = {
        "enable": enable_cb, "min_area": min_area_edit,
        "max_area": max_area_edit, "min_solidity": min_sol_edit, "status": status,
    }

    def _fields_params() -> QCParams:
        return QCParams(
            enabled=enable_cb.isChecked(),
            min_area=_num(min_area_edit.text()) or None,
            max_area=_num(max_area_edit.text()) or None,
            min_solidity=_num(min_sol_edit.text()) or None,
        ).with_updates()

    def _sync_enabled() -> None:
        on = enable_cb.isChecked()
        for w in (min_area_edit, max_area_edit, min_sol_edit):
            w.setEnabled(on)

    def _update_status() -> None:
        if not app._app_state.dataset.cache:
            status.setText("")
            return
        n_excl, n_tot = qc_excluded_count(app)
        if n_tot:
            status.setText(f"flagged {n_excl:,} / {n_tot:,}")

    def _apply() -> None:
        _sync_enabled()
        set_qc_params(app, _fields_params())  # recompute + persist + redraw
        try:
            from well_viewer.features.review_image.renderer import refresh_review_image
            refresh_review_image(app)  # reflect QC in the segmentation overlay
        except Exception:
            pass
        _update_status()

    enable_cb.toggled.connect(lambda _=None: _apply())
    for w in (min_area_edit, max_area_edit, min_sol_edit):
        w.editingFinished.connect(_apply)

    _sync_enabled()
    _update_status()
    return row


def qc_gate_refresh_widgets(app) -> None:
    """Re-fill the embedded gate row from the current params (after a load /
    external change). No-op when the Segmentation panel hasn't been built."""
    w = getattr(app, "_qc_gate_widgets", None)
    if not w:
        return
    p = get_qc_params(app)
    w["enable"].setChecked(bool(p.enabled))
    w["min_area"].setText(_fmt(p.min_area))
    w["max_area"].setText(_fmt(p.max_area))
    w["min_solidity"].setText(_fmt(p.min_solidity))
