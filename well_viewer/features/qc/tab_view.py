"""QC debris-gate controls, embedded in the Sample Definitions → Cell Gating tab.

The Cell Gating tab is the QC editing home: the QC debris gate is a *fundamental*
inclusion layer that is always applied (``QC_Pass`` at the ``df_included_mask``
chokepoint), composing with the FluorGating thresholds and the scatter gates that
also live on this tab. Editing is live — each change recomputes the per-cell
``QC_Pass`` verdict, re-flags every plot/stat/the Review table, and refreshes the
Segmentation overlay (which still *renders* the verdict so excluded cells show
there).

The verdict is non-destructive and composes with manual curation (effective =
manual-keep AND passes-QC AND inside-gate). Flat-field correction is a run-time
step, not part of this gate.
"""

from __future__ import annotations


from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox, QLabel, QLineEdit, QWidget,
)

from .controller import get_qc_params, qc_excluded_count, set_qc_params
from .params import QCParams
from well_viewer.core.safe import swallowed
from widgets.flow_layout import FlowLayout, flow_group
import theme


def _num(text: str) -> float:
    try:
        return float(str(text).strip() or "0")
    except (TypeError, ValueError):
        return 0.0


def _fmt(v: float | None) -> str:
    return "" if v is None else f"{v:g}"


def build_qc_gate_controls(ctx, parent: QWidget) -> QWidget:
    """A compact QC debris-gate row for the Cell Gating tab.

    Returns the row widget (already laid out). Edits apply live to the whole
    dataset and refresh the Segmentation overlay so excluded cells show there
    immediately.

    A8 teardown: takes ``ctx`` — reads params off typed state, registers its
    widget handles through ``ctx.widgets`` and drives the QC controllers /
    Segmentation refresh through their ctx-native entry points.
    """
    row = QWidget(parent)
    hl = FlowLayout(row)
    hl.setContentsMargins(8, 2, 8, 2)

    params = get_qc_params(ctx)

    enable_cb = QCheckBox("QC debris gate", row)
    enable_cb.setChecked(bool(params.enabled))
    enable_cb.setToolTip(
        "Flag cells outside the size / solidity / circularity bounds as debris "
        "(non-destructive). Applies to the whole dataset; excluded cells show "
        "here and drop out of every plot/stat. Composes with manual deletes.")
    hl.addWidget(enable_cb)

    # Each label+edit is grouped into one FlowLayout item so a wrap at narrow
    # width never splits a field from its label (and no control clips off-edge).
    min_area_edit = QLineEdit(_fmt(params.min_area), row)
    hl.addWidget(flow_group(row, "min area", min_area_edit))
    max_area_edit = QLineEdit(_fmt(params.max_area), row)
    hl.addWidget(flow_group(row, "max area", max_area_edit))
    min_sol_edit = QLineEdit(_fmt(params.min_solidity), row)
    hl.addWidget(flow_group(row, "min solidity", min_sol_edit))
    max_region_edit = QLineEdit(_fmt(params.max_region_area), row)
    max_region_edit.setToolTip(
        "Reject cells sitting in an over-large bright region (px²) — catches "
        "debris StarDist shattered into many small cells that per-cell size "
        "can't, since each fragment is individually small.")
    hl.addWidget(flow_group(row, "max region", max_region_edit))
    min_circ_edit = QLineEdit(_fmt(params.min_circularity), row)
    _circ_tip = (
        "Circularity = 4π·area/perimeter² (1.0 = perfect circle). Min rejects "
        "jagged / non-round debris. Needs circularity in the data — collected "
        "automatically by the current pipeline; datasets processed before that "
        "must be reprocessed (or have shape perimeter present so it can be "
        "derived on load).")
    min_circ_edit.setToolTip(_circ_tip)
    hl.addWidget(flow_group(row, "min circ", min_circ_edit))
    max_circ_edit = QLineEdit(_fmt(params.max_circularity), row)
    max_circ_edit.setToolTip(_circ_tip)
    hl.addWidget(flow_group(row, "max circ", max_circ_edit))
    _edits = (min_area_edit, max_area_edit, min_sol_edit, max_region_edit,
              min_circ_edit, max_circ_edit)
    for w in _edits:
        w.setFixedWidth(theme.Metrics.num_field_w)
        w.setPlaceholderText("none")

    status = QLabel("", row)
    status.setObjectName("Muted")
    hl.addWidget(status)
    hl.addStretch(1)

    # stash refs so persistence / external recomputes can refresh the row.
    # ``ctx.widgets.register`` also stamps ``app._qc_gate_widgets`` so the legacy
    # ``qc_gate_refresh_widgets`` reader resolves it unchanged.
    ctx.widgets.register("_qc_gate_widgets", {
        "enable": enable_cb, "min_area": min_area_edit,
        "max_area": max_area_edit, "min_solidity": min_sol_edit,
        "max_region_area": max_region_edit,
        "min_circularity": min_circ_edit, "max_circularity": max_circ_edit,
        "status": status,
    })

    def _fields_params() -> QCParams:
        return QCParams(
            enabled=enable_cb.isChecked(),
            min_area=_num(min_area_edit.text()) or None,
            max_area=_num(max_area_edit.text()) or None,
            min_solidity=_num(min_sol_edit.text()) or None,
            max_region_area=_num(max_region_edit.text()) or None,
            min_circularity=_num(min_circ_edit.text()) or None,
            max_circularity=_num(max_circ_edit.text()) or None,
        ).with_updates()

    def _sync_enabled() -> None:
        on = enable_cb.isChecked()
        for w in _edits:
            w.setEnabled(on)

    def _update_status() -> None:
        if not ctx.state.dataset.cache:
            status.setText("")
            return
        n_excl, n_tot = qc_excluded_count(ctx)
        if n_tot:
            status.setText(f"flagged {n_excl:,} / {n_tot:,}")

    def _apply() -> None:
        _sync_enabled()
        set_qc_params(ctx, _fields_params())  # recompute + persist + redraw
        try:
            from well_viewer.features.image_browser.review_image.renderer import refresh_review_image_ctx
            refresh_review_image_ctx(ctx)  # reflect QC in the segmentation overlay
        except Exception:
            swallowed()  # was: pass (silent except)
        _update_status()

    # Apply live while typing (debounced) as well as on focus-out / Enter, so a
    # populated bound takes effect without the user having to tab out of the
    # field first. The debounce only re-fires after a short typing pause to
    # avoid recomputing the gate on every keystroke; the ``hasFocus`` guard
    # keeps the programmatic ``refresh_qc_gate_row`` setText calls from
    # triggering it (they run when the field isn't focused).
    _debounce = QTimer(row)
    _debounce.setSingleShot(True)
    _debounce.setInterval(350)
    _debounce.timeout.connect(_apply)

    enable_cb.toggled.connect(lambda _=None: _apply())
    for w in _edits:
        w.editingFinished.connect(_apply)
        w.textChanged.connect(
            lambda _t=None, _w=w: _debounce.start() if _w.hasFocus() else None)

    # Let a reset that clears the QC params re-fill this row without naming the
    # QC feature: the Gating reset scope fires whatever is stamped here and
    # no-ops when the row was never built (persistence/reset_scopes.py).
    ctx.runtime.qc.refresh_widgets_cb = lambda: qc_gate_refresh_widgets(ctx.window)

    _sync_enabled()
    _update_status()
    return row


def qc_gate_refresh_widgets(win) -> None:
    """Re-fill the embedded gate row from the current params (after a load /
    external change). No-op when the Cell Gating tab hasn't been built.

    Signals are blocked throughout: these are programmatic updates that *mirror*
    already-applied params, so they must not fire the gate's change handlers.
    In particular ``setChecked`` fires the enable toggle → ``_apply``, which
    would read the not-yet-populated min/max fields and clobber the freshly
    loaded bounds back to ``None`` — the bug where a reopened set's QC filter
    showed its values but didn't apply until a field was re-entered.
    """
    w = getattr(win, "_qc_gate_widgets", None)
    if not w:
        return
    p = get_qc_params(win._ctx)
    edit_keys = ("min_area", "max_area", "min_solidity", "max_region_area",
                 "min_circularity", "max_circularity")
    fields = [w["enable"]] + [w[k] for k in edit_keys]
    blocked = [f.blockSignals(True) for f in fields]
    try:
        w["enable"].setChecked(bool(p.enabled))
        w["min_area"].setText(_fmt(p.min_area))
        w["max_area"].setText(_fmt(p.max_area))
        w["min_solidity"].setText(_fmt(p.min_solidity))
        w["max_region_area"].setText(_fmt(p.max_region_area))
        w["min_circularity"].setText(_fmt(p.min_circularity))
        w["max_circularity"].setText(_fmt(p.max_circularity))
    finally:
        for f, b in zip(fields, blocked):
            f.blockSignals(b)
    # Re-sync the edit fields' enabled state by hand (the toggle handler that
    # normally does this was just blocked).
    on = bool(p.enabled)
    for k in edit_keys:
        w[k].setEnabled(on)
