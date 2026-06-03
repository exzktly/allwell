"""QC sidebar view — edit the debris-gating thresholds *after* a run.

Mirrors the Segmentation tab's role: a per-dataset curation surface. The debris
gate (min/max area, min solidity) is a pure threshold on the persisted shape
metrics, so editing it here recomputes the per-cell ``QC_Pass`` verdict live and
re-renders every plot/stat — no re-segmentation, no re-run. Excluded cells are
*flagged*, never deleted, and the verdict composes with manual curation
(effective = manual-keep AND passes-QC).

Flat-field correction is shown read-only: it rescales pixel intensities at
quantification time and can only be changed by re-running the pipeline.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from .controller import get_qc_params, qc_excluded_count, seed_qc_from_pipeline_info, set_qc_params
from .params import QCParams


def _num(text: str) -> float:
    try:
        return float(str(text).strip() or "0")
    except (TypeError, ValueError):
        return 0.0


def build_qc_tab(app, parent: QWidget) -> None:
    """Populate the Review *QC* tab with the debris-gating editor."""
    layout = QVBoxLayout(parent)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)

    hint = QLabel(
        "Quality control flags cells without deleting them. Adjust the debris "
        "gate below and the change applies live to every plot, statistic and "
        "the Review table — excluded cells stay in the data with their flag "
        "cleared, and your manual include/exclude edits are preserved "
        "(a cell counts only when manually kept AND it passes QC).", parent)
    hint.setObjectName("Muted")
    hint.setWordWrap(True)
    layout.addWidget(hint)

    # ── Flat-field (read-only) ────────────────────────────────────────────
    info = getattr(app._app_state.dataset, "pipeline_info", None) or {}
    ff_mode = str((info.get("qc_options", {}) or {}).get("flatfield", "none"))
    ff = QLabel(f"Flat-field correction (this run): <b>{ff_mode}</b> — "
                "rescales pixel intensities, so changing it requires re-running "
                "the pipeline.", parent)
    ff.setObjectName("Muted")
    ff.setWordWrap(True)
    layout.addWidget(ff)

    rule = QFrame(parent)
    rule.setFrameShape(QFrame.HLine)
    rule.setObjectName("HRule")
    layout.addWidget(rule)

    # ── Debris gate ───────────────────────────────────────────────────────
    params = get_qc_params(app)

    enable_cb = QCheckBox("Remove debris by size / shape", parent)
    enable_cb.setChecked(bool(params.enabled))
    layout.addWidget(enable_cb)

    grid_host = QWidget(parent)
    grid = QGridLayout(grid_host)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(4)

    def _fmt(v: Optional[float]) -> str:
        return "" if v is None else (f"{v:g}")

    min_area_edit = QLineEdit(_fmt(params.min_area), grid_host)
    max_area_edit = QLineEdit(_fmt(params.max_area), grid_host)
    min_sol_edit = QLineEdit(_fmt(params.min_solidity), grid_host)
    for w in (min_area_edit, max_area_edit, min_sol_edit):
        w.setFixedWidth(90)
        w.setPlaceholderText("none")
    grid.addWidget(QLabel("Min area (px²)", grid_host), 0, 0)
    grid.addWidget(min_area_edit, 0, 1)
    grid.addWidget(QLabel("Max area (px²)", grid_host), 1, 0)
    grid.addWidget(max_area_edit, 1, 1)
    grid.addWidget(QLabel("Min solidity (0–1)", grid_host), 2, 0)
    grid.addWidget(min_sol_edit, 2, 1)
    layout.addWidget(grid_host)

    readout = QLabel("", parent)
    readout.setObjectName("Muted")
    readout.setWordWrap(True)
    layout.addWidget(readout)

    btn_row = QWidget(parent)
    bl = QHBoxLayout(btn_row)
    bl.setContentsMargins(0, 0, 0, 0)
    apply_btn = QPushButton("Apply", btn_row)
    reset_btn = QPushButton("Reset to run settings", btn_row)
    bl.addWidget(apply_btn)
    bl.addWidget(reset_btn)
    bl.addStretch(1)
    layout.addWidget(btn_row)
    layout.addStretch(1)

    # ── behaviour ─────────────────────────────────────────────────────────
    def _fields_params() -> QCParams:
        return QCParams(
            enabled=enable_cb.isChecked(),
            min_area=_num(min_area_edit.text()) or None,
            max_area=_num(max_area_edit.text()) or None,
            min_solidity=_num(min_sol_edit.text()) or None,
        ).with_updates()  # normalize 0 → None via _pos

    def _sync_enabled() -> None:
        on = enable_cb.isChecked()
        for w in (min_area_edit, max_area_edit, min_sol_edit):
            w.setEnabled(on)

    def _update_readout(preview: bool = True) -> None:
        cache = app._app_state.dataset.cache
        if not cache:
            readout.setText("Load a dataset to preview QC exclusions.")
            return
        p = _fields_params() if preview else get_qc_params(app)
        n_excl, n_tot = qc_excluded_count(app, params=p)
        if n_tot == 0:
            readout.setText("No cells loaded yet.")
            return
        pct = (100.0 * n_excl / n_tot) if n_tot else 0.0
        verb = "would exclude" if preview else "excludes"
        readout.setText(
            f"QC {verb} <b>{n_excl:,}</b> of {n_tot:,} loaded cells "
            f"({pct:.1f}%). Press Apply to commit.")

    def _on_apply() -> None:
        set_qc_params(app, _fields_params())
        _update_readout(preview=False)

    def _on_reset() -> None:
        seed_qc_from_pipeline_info(app)
        p = get_qc_params(app)
        enable_cb.setChecked(bool(p.enabled))
        min_area_edit.setText(_fmt(p.min_area))
        max_area_edit.setText(_fmt(p.max_area))
        min_sol_edit.setText(_fmt(p.min_solidity))
        _sync_enabled()
        set_qc_params(app, p)
        _update_readout(preview=False)

    enable_cb.toggled.connect(lambda _=None: (_sync_enabled(), _update_readout()))
    for w in (min_area_edit, max_area_edit, min_sol_edit):
        w.textChanged.connect(lambda _=None: _update_readout())
    apply_btn.clicked.connect(_on_apply)
    reset_btn.clicked.connect(_on_reset)

    _sync_enabled()
    _update_readout()
