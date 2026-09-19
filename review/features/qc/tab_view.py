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

from review.data.field_quality_cache import (
    field_quality_block, field_quality_status_text,
)
from .controller import get_qc_params, qc_excluded_count, set_qc_params
from .params import QCParams
from core.safe import swallowed
from ui import theme

# Pixel geometry for this module's controls (ARCHITECTURE §8: named once, never
# written inline at a call site).
_GRID_INDENT = 18
_QUANTITY_LBL_W = 92
#: How wide the field-quality sentence is allowed to run.
#:
#: Unconstrained it fills the panel — about 1,200 px on a normal window —
#: while every control above it stops around 700, so a note attached to the
#: checkbox above read as a page-wide alert. ``QLabel#Notice`` paints a wash
#: and a left rule, which makes the difference obvious; the render is where it
#: showed. Sized to the controls it sits under rather than to the panel.
_FQ_TEXT_W = 700


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

    # ── image quality, as reported by the run that produced this dataset ─────
    #
    # A second, independent gate on the same panel, and deliberately worded so
    # it cannot be mistaken for the first. The debris gate is a rule the user
    # writes about cells; this is a verdict the *pipeline* already reached
    # about whole fields — out of focus, clipped, or nothing like the rest of
    # the plate. It is reported whether or not it is applied, because a plate
    # with six defocused fields is worth knowing about even from a user who
    # would rather keep them.
    #
    # Off by default. Turning it on drops every row from a flagged field, which
    # moves every number in the app, so it is a thing chosen after reading what
    # was flagged rather than discovered afterwards.
    fq = QWidget(row)
    fq_layout = QVBoxLayout(fq)
    fq_layout.setContentsMargins(0, 6, 0, 0)
    fq_layout.setSpacing(2)
    fq_head = QWidget(fq)
    fq_head_layout = QHBoxLayout(fq_head)
    fq_head_layout.setContentsMargins(0, 0, 0, 0)
    fq_cb = QCheckBox("Exclude fields the run flagged", fq_head)
    fq_cb.setChecked(bool(params.exclude_flagged_fields))
    fq_cb.setToolTip(
        "Drop every cell from a field this dataset's own run flagged on image "
        "quality — focus, saturation, dynamic range, or a count that does not "
        "look like the rest of its well.\n"
        "Off by default: the flags are reported either way, and turning this "
        "on changes every number in the app.")
    fq_head_layout.addWidget(fq_cb)
    fq_head_layout.addStretch(1)
    fq_layout.addWidget(fq_head)
    fq_status = QLabel("", fq)
    fq_status.setWordWrap(True)
    fq_status.setMaximumWidth(_FQ_TEXT_W)
    fq_layout.addWidget(fq_status)
    outer.addWidget(fq)

    # stash refs so persistence / external recomputes can refresh the row.
    # ``ctx.widgets.register`` also stamps ``app._qc_gate_widgets`` so the legacy
    # ``qc_gate_refresh_widgets`` reader resolves it unchanged.
    ctx.widgets.register("_qc_gate_widgets", {
        "enable": enable_cb, "min_area": min_area_edit,
        "max_area": max_area_edit, "min_solidity": min_sol_edit,
        "max_region_area": max_region_edit,
        "min_circularity": min_circ_edit, "max_circularity": max_circ_edit,
        "status": status,
        "exclude_flagged_fields": fq_cb, "field_quality_status": fq_status,
    })

    def _fields_params() -> QCParams:
        """The six boxes as a normalised :class:`QCParams`.

        The bounds go through ``with_updates`` **as keyword arguments**, which
        is the only way its ``_pos`` normalisation runs — it filters on
        ``k in changes``, so a bare ``.with_updates()`` after the constructor
        normalises nothing. ``_pos`` is where the rule lives: a blank,
        unparseable, zero or negative box means *no bound*, which for a
        **maximum** is the whole point (``qc_params._pos``'s own docstring calls
        ``max_area == 0`` a foot-gun — it would exclude every cell), and for a
        minimum is the same population a bound of zero would keep anyway. This
        function briefly coerced the text itself and passed the result to the
        constructor, which left a typed ``0`` in ``max_area`` intact and blanked
        every plot.
        """
        return QCParams(
            enabled=enable_cb.isChecked(),
            exclude_flagged_fields=fq_cb.isChecked(),
        ).with_updates(
            min_area=min_area_edit.text(),
            max_area=max_area_edit.text(),
            min_solidity=min_sol_edit.text(),
            max_region_area=max_region_edit.text(),
            min_circularity=min_circ_edit.text(),
            max_circularity=max_circ_edit.text(),
        )

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

    def _update_field_quality_status() -> None:
        _refresh_field_quality_status(
            ctx, {"field_quality_status": fq_status,
                  "exclude_flagged_fields": fq_cb})

    def _apply() -> None:
        _sync_enabled()
        set_qc_params(ctx, _fields_params())  # recompute + persist + redraw
        try:
            from review.features.image_browser.review_image.renderer import refresh_review_image_ctx
            refresh_review_image_ctx(ctx)  # reflect QC in the segmentation overlay
        except Exception:
            swallowed()  # was: pass (silent except)
        _update_status()
        _update_field_quality_status()

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
    fq_cb.toggled.connect(lambda _=None: _apply())
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
    _update_field_quality_status()
    return row


def _refresh_field_quality_status(ctx, w) -> None:
    """Re-read the dataset's field-quality block into the panel's sentence.

    Shared by the builder's own handler and by
    :func:`qc_gate_refresh_widgets`, so the panel cannot say one thing when it
    is built and another when it is refilled.

    Three registers, because the three states are genuinely different and a
    reader who cannot tell them apart is the failure this subsystem exists to
    prevent: a refusal (amber, it says what it could not do), a finding (the
    app's normal text), and nothing to say (muted). The checkbox is disabled
    where there is nothing to apply, so a control that would do nothing does
    not look live.
    """
    label = w.get("field_quality_status")
    box = w.get("exclude_flagged_fields")
    if label is None or box is None:
        return
    text, problem = field_quality_status_text(ctx)
    label.setText(text)
    label.setObjectName("Notice" if problem else ("" if text else "Muted"))
    label.style().unpolish(label)
    label.style().polish(label)
    box.setEnabled(bool(field_quality_block(ctx)))


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
    fields = [w["enable"], w["exclude_flagged_fields"]] + [
        w[k] for k in edit_keys]
    blocked = [f.blockSignals(True) for f in fields]
    try:
        w["enable"].setChecked(bool(p.enabled))
        w["exclude_flagged_fields"].setChecked(bool(p.exclude_flagged_fields))
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
    # And the field-quality sentence, which is a fact about the **dataset**
    # rather than about the params — so it is the one thing on this panel that
    # a load changes without any control moving. Refilling only the widget
    # *values* left it reading "analysed before field quality was recorded"
    # over a plate whose block was right there, which the render caught.
    _refresh_field_quality_status(win._ctx, w)
