"""QC debris-gate controls, embedded in the Home → Cell Gating tab.

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
    QCheckBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget,
)

from .controller import get_qc_params, qc_excluded_count, set_qc_params
from .params import QCParams
from well_viewer.core.safe import swallowed
import theme

# Pixel geometry for this module's controls (ARCHITECTURE §8: named once, never
# written inline at a call site).
_GRID_INDENT = 18
_QUANTITY_LBL_W = 92


def _bound(text: str) -> float | None:
    """A QC bound typed into a box: the number, or ``None`` for an empty box.

    ``float(text or "0") or None`` was the spelling, and it throws away a typed
    zero along with an empty box because ``0.0`` is falsy — so "minimum
    solidity 0" silently became "no minimum solidity", with nothing on screen
    saying the value had been discarded. Only an unparseable or blank box means
    "no bound"; every number the user typed, zero included, is one.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _fmt(v: float | None) -> str:
    return "" if v is None else f"{v:g}"


def build_qc_gate_controls(ctx, parent: QWidget) -> QWidget:
    """The QC debris-gate controls for the Cell Gating tab.

    Returns the widget (already laid out): an enable checkbox with the flagged
    count beside it, over a grid with one row per measured quantity and its
    min / max bounds in aligned columns. Edits apply live to the whole
    dataset and refresh the Segmentation overlay so excluded cells show there
    immediately.

    A8 teardown: takes ``ctx`` — reads params off typed state, registers its
    widget handles through ``ctx.widgets`` and drives the QC controllers /
    Segmentation refresh through their ctx-native entry points.
    """
    row = QWidget(parent)
    outer = QVBoxLayout(row)
    outer.setContentsMargins(8, 2, 8, 2)
    outer.setSpacing(4)

    params = get_qc_params(ctx)

    head = QWidget(row)
    head_layout = QHBoxLayout(head)
    head_layout.setContentsMargins(0, 0, 0, 0)
    enable_cb = QCheckBox("QC debris gate", head)
    enable_cb.setChecked(bool(params.enabled))
    enable_cb.setToolTip(
        "Flag cells outside the size / solidity / circularity bounds as debris "
        "(non-destructive). Applies to the whole dataset; excluded cells show "
        "here and drop out of every plot/stat. Composes with manual deletes.")
    head_layout.addWidget(enable_cb)
    head_layout.addSpacing(12)
    status = QLabel("", head)
    status.setObjectName("Muted")
    head_layout.addWidget(status)
    head_layout.addStretch(1)
    outer.addWidget(head)

    # One row per measured quantity, min and max in aligned columns. Laid out as
    # a grid rather than a single wrapping row of "min area / max area / min
    # solidity / …" fields: those are three different quantities with six labels
    # that read as one undifferentiated strip, and a bound is only meaningful
    # beside the thing it bounds. Quantities that admit only one bound show an
    # em-dash in the other column, so "no such bound" is visibly different from
    # "a bound I have not filled in".
    _circ_tip = (
        "Circularity = 4π·area/perimeter² (1.0 = perfect circle). Min rejects "
        "jagged / non-round debris. Needs circularity in the data — collected "
        "automatically by the current pipeline; datasets processed before that "
        "must be reprocessed (or have shape perimeter present so it can be "
        "derived on load).")
    _area_tip = "Cell area in pixels; the primary size gate."
    _sol_tip = (
        "Solidity = area / convex-hull area. Low values are ragged or "
        "fragmented objects. There is no upper bound: a perfectly convex cell "
        "is not a defect.")
    _region_tip = (
        "Reject cells sitting in an over-large bright region (px²) — catches "
        "debris StarDist shattered into many small cells that per-cell size "
        "can't, since each fragment is individually small. There is no lower "
        "bound: a small bright region is just a cell.")

    grid = QWidget(row)
    gl = QGridLayout(grid)
    gl.setContentsMargins(_GRID_INDENT, 0, 0, 0)
    gl.setHorizontalSpacing(10)
    gl.setVerticalSpacing(3)
    gl.setColumnMinimumWidth(0, _QUANTITY_LBL_W)
    gl.setColumnStretch(3, 1)
    for col, cap in ((1, "min"), (2, "max")):
        head_lbl = QLabel(cap, grid)
        head_lbl.setObjectName("Muted")
        gl.addWidget(head_lbl, 0, col)

    def _field(value, tip: str) -> QLineEdit:
        edit = QLineEdit(_fmt(value), grid)
        edit.setFixedWidth(theme.Metrics.num_field_w)
        edit.setPlaceholderText("none")
        edit.setToolTip(tip)
        return edit

    def _absent() -> QLabel:
        """The column a quantity has no bound for. An em-dash, not a gap: a
        bound that does not exist should not look like one left blank."""
        lbl = QLabel("—", grid)
        lbl.setObjectName("Muted")
        return lbl

    min_area_edit = _field(params.min_area, _area_tip)
    max_area_edit = _field(params.max_area, _area_tip)
    min_sol_edit = _field(params.min_solidity, _sol_tip)
    max_region_edit = _field(params.max_region_area, _region_tip)
    min_circ_edit = _field(params.min_circularity, _circ_tip)
    max_circ_edit = _field(params.max_circularity, _circ_tip)

    rows = (
        ("Area", _area_tip, min_area_edit, max_area_edit),
        ("Solidity", _sol_tip, min_sol_edit, None),
        ("Region area", _region_tip, None, max_region_edit),
        ("Circularity", _circ_tip, min_circ_edit, max_circ_edit),
    )
    for r, (name, tip, lo, hi) in enumerate(rows, start=1):
        name_lbl = QLabel(name, grid)
        name_lbl.setToolTip(tip)
        gl.addWidget(name_lbl, r, 0)
        gl.addWidget(lo if lo is not None else _absent(), r, 1)
        gl.addWidget(hi if hi is not None else _absent(), r, 2)
    outer.addWidget(grid)

    _edits = (min_area_edit, max_area_edit, min_sol_edit, max_region_edit,
              min_circ_edit, max_circ_edit)

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
            # ``_bound(...)`` rather than ``float(...) or None``: the falsy-zero
            # bug ``util.timepoint`` carries a paragraph about. A deliberately
            # typed ``0`` is a real bound — "solidity at least 0", "area at
            # least 0" — and reads as an empty box, so a user who typed it got
            # no bound at all and no sign that the value had been discarded.
            min_area=_bound(min_area_edit.text()),
            max_area=_bound(max_area_edit.text()),
            min_solidity=_bound(min_sol_edit.text()),
            max_region_area=_bound(max_region_edit.text()),
            min_circularity=_bound(min_circ_edit.text()),
            max_circularity=_bound(max_circ_edit.text()),
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
