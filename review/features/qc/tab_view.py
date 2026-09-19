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
    QCheckBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from review.data.field_quality_cache import (
    REGISTER_PROBLEM, exclusion_can_apply, exclusion_effect_text,
    field_quality_status,
)
from review.data.focus_gate import (
    focus_effect_text, focus_gate_can_apply, focus_status, recorded_cut_note,
)
from .controller import get_qc_params, qc_excluded_count, set_qc_params
from .params import QCParams
from core.safe import swallowed
from ui import theme

# Pixel geometry for this module's controls (ARCHITECTURE §8: named once, never
# written inline at a call site).
_FQ_TOOLTIP = (
    "Drop every cell from a field this dataset's own run flagged on image "
    "quality — focus, saturation, contrast, or measurements that do not look "
    "like the rest of the well.\n"
    "Off by default: the flags are reported either way, and turning this on "
    "changes every number in the app.")

_FOCUS_TOOLTIP = (
    "Drop the individual cells whose own outline read as soft, rather than "
    "whole fields.\n"
    "This is what removes a focus gradient across one field or a coverslip "
    "tilt — a field whose overall focus is fine can still have a soft third, "
    "and no whole-field verdict can say so.\n"
    "The cut is absolute, not relative to the field: a score relative to its "
    "own frame cannot see a frame that is uniformly soft, because every cell "
    "in it deviates from its neighbours by nothing.\n"
    "Off by default — turning it on changes every number in the app.")

_GRID_INDENT = 18
_QUANTITY_LBL_W = 92
#: How wide the field-quality sentence is allowed to run.
#:
#: Unconstrained it fills the panel — about 1,200 px on a normal window —
#: while the controls above it stop at 612 (the debris grid's last field) and
#: 691 (the scatter hint). A note attached to the checkbox then read as a
#: page-wide alert, which ``QLabel#Notice``'s wash and left rule make
#: unmissable. Measured off the render rather than guessed at.
_FQ_TEXT_W = 700

#: Where a sentence that belongs to a checkbox starts.
#:
#: The panel already has this idiom and the first version of this row did not
#: use it: "No gates drawn yet — draw one on the Scatter Plot tab." sits at
#: its control's *label* column and is muted, while this sentence sat 26 px
#: further left, at the checkbox's *indicator* column, in full primary ink —
#: the brightest, left-most non-heading line in the block, directly above
#: "Then include cells by (choose one):". It read as that group's heading.
_FQ_TEXT_INDENT = 27

#: The focus cut's box. Narrower than the debris bounds' 80 px because it holds
#: a value under 2 with two or three decimals -- "0.083" -- where an area bound
#: holds five digits. Sized to its content rather than to its neighbours, since
#: it sits on a checkbox's row and a box wider than its text reads as an empty
#: field somebody forgot.
_FOCUS_EDIT_W = 64


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
    fq_cb.setToolTip(_FQ_TOOLTIP)
    fq_head_layout.addWidget(fq_cb)
    fq_head_layout.addStretch(1)
    fq_layout.addWidget(fq_head)
    fq_status = QLabel("", fq)
    fq_status.setWordWrap(True)
    fq_status.setMaximumWidth(_FQ_TEXT_W)
    fq_layout.addWidget(fq_status)
    # The one line that depends on the checkbox. The finding above it stays
    # the same whether or not the box is ticked — the flags are reported
    # either way — which left the panel saying nothing at all about what
    # ticking it did.
    fq_effect = QLabel("", fq)
    fq_effect.setObjectName("Muted")
    fq_effect.setWordWrap(True)
    fq_effect.setMaximumWidth(_FQ_TEXT_W)
    fq_layout.addWidget(fq_effect)
    # Indented on the labels rather than on the container: the checkbox must
    # stay in the debris gate's own column, so it reads as a sibling gate
    # rather than as a fifth debris row, and only the sentences move. A
    # negative margin on the head does not do it — Qt clamps those to zero,
    # and the render showed the whole row shifted right.
    for _lbl in (fq_status, fq_effect):
        _lbl.setIndent(_FQ_TEXT_INDENT)
    outer.addWidget(fq)

    # ── the same question one pooling down: which CELLS are soft ────────────
    #
    # A third gate, and the reason it is not folded into the one above is that
    # they answer different questions and neither subsumes the other. That one
    # is the run's verdict about a whole FIELD — it removes all of a field or
    # none of it, and it is the only thing that can see a field that is
    # clipped, low-contrast, or unlike the rest of the plate. This one is an
    # absolute cut on each cell's own boundary sharpness, so it removes the
    # soft PART of a field: a focus gradient, a coverslip tilt. Neither is a
    # whole-field fact and neither is a rectangle, which is why this is a
    # column rather than a crop.
    #
    # Off by default for the same reason as its neighbour, and with its cut in
    # a field rather than a default, because no threshold can be guessed --
    # the Playground is where a user finds theirs.
    foc = QWidget(row)
    foc_layout = QVBoxLayout(foc)
    foc_layout.setContentsMargins(0, 6, 0, 0)
    foc_layout.setSpacing(2)
    foc_head = QWidget(foc)
    foc_head_layout = QHBoxLayout(foc_head)
    foc_head_layout.setContentsMargins(0, 0, 0, 0)
    foc_cb = QCheckBox("Exclude out-of-focus cells", foc_head)
    foc_cb.setChecked(bool(params.exclude_soft_cells))
    foc_cb.setToolTip(_FOCUS_TOOLTIP)
    foc_head_layout.addWidget(foc_cb)
    foc_head_layout.addSpacing(8)
    foc_below = QLabel("below", foc_head)
    foc_below.setObjectName("Muted")
    foc_head_layout.addWidget(foc_below)
    # The cut sits ON the checkbox's own row rather than under it: it is the
    # checkbox's operand, and every other number on this panel that qualifies
    # a rule sits beside the rule. A row of its own read as a fifth debris
    # bound, which is a rule about cells rather than about the imaging.
    foc_edit = QLineEdit(_fmt(params.focus_min), foc_head)
    foc_edit.setFixedWidth(_FOCUS_EDIT_W)
    foc_edit.setPlaceholderText("none")
    foc_edit.setToolTip(
        "The per-cell focus value below which a cell counts as out of focus. "
        "Seeded from this dataset's own run when it recorded one.\n"
        "Blank means no cut, and nothing here can supply a default: the "
        "number depends mostly on the microscope — the point-spread function, "
        "the pixel size, the nuclear contrast — rather than on the plate. It "
        "is a transition width in pixels, so brightness, cell size and "
        "crowding each move it under 5 %; clipping is the exception, and a "
        "saturated plate reads sharper than it is.\n"
        "Distribution… beside this box draws the selected well's own curve "
        "with a live count of what a cut would keep and drop.")
    foc_head_layout.addWidget(foc_edit)
    foc_head_layout.addSpacing(8)
    # The evidence for the number, beside the box it goes in. The Playground
    # draws one FIELD of one channel; the cut applies to the whole dataset, so
    # a per-WELL curve is the one that answers "what will this do to my data".
    # Pooling every field and timepoint the well holds, from the column the
    # run already wrote -- no image decode.
    foc_curve_btn = QPushButton("Distribution…", foc_head)
    foc_curve_btn.setToolTip(
        "Plot the selected well's per-cell focus as a cumulative curve, with "
        "your cut marked and a live count of what it keeps and drops.\n"
        "\n"
        "Pools every field and timepoint in the well, which is the scope the "
        "cut actually applies at — the Image Playground's version measures a "
        "single field of whichever channel is on screen there.")
    foc_curve_btn.clicked.connect(
        lambda _=False: _show_well_focus_curve(ctx))
    foc_head_layout.addWidget(foc_curve_btn)
    foc_head_layout.addStretch(1)
    foc_layout.addWidget(foc_head)
    foc_status = QLabel("", foc)
    foc_status.setWordWrap(True)
    foc_status.setMaximumWidth(_FQ_TEXT_W)
    foc_layout.addWidget(foc_status)
    foc_effect = QLabel("", foc)
    foc_effect.setObjectName("Muted")
    foc_effect.setWordWrap(True)
    foc_effect.setMaximumWidth(_FQ_TEXT_W)
    foc_layout.addWidget(foc_effect)
    for _lbl in (foc_status, foc_effect):
        _lbl.setIndent(_FQ_TEXT_INDENT)
    outer.addWidget(foc)

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
        "field_quality_effect": fq_effect,
        "exclude_soft_cells": foc_cb, "focus_min": foc_edit,
        "focus_status": foc_status, "focus_effect": foc_effect,
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
            exclude_soft_cells=foc_cb.isChecked(),
        ).with_updates(
            focus_min=foc_edit.text(),
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
                  "field_quality_effect": fq_effect,
                  "exclude_flagged_fields": fq_cb})
        _refresh_focus_status(
            ctx, {"focus_status": foc_status, "focus_effect": foc_effect,
                  "exclude_soft_cells": foc_cb})

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
    foc_cb.toggled.connect(lambda _=None: _apply())
    # The cut gets the same live-while-typing treatment as the debris bounds,
    # including the ``hasFocus`` guard that keeps the programmatic refill in
    # ``qc_gate_refresh_widgets`` from firing it.
    foc_edit.editingFinished.connect(_apply)
    foc_edit.textChanged.connect(
        lambda _t=None: _debounce.start() if foc_edit.hasFocus() else None)
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
    text, register = field_quality_status(ctx)
    label.setText(text)
    # Amber for a refusal, the panel's sub-status ink for everything else.
    # A finding and a dataset with nothing to say are two different sentences
    # and they share one style **today**, deliberately: the sibling idiom on
    # this panel ("No gates drawn yet — draw one on the Scatter Plot tab.")
    # is muted, and a finding rendered at full primary ink was measured to be
    # the brightest, left-most non-heading line in the block, reading as the
    # heading of the radio group beneath it. The registers stay three because
    # they are three statements; the styling is a separate decision and this
    # is the one the render supports.
    label.setObjectName("Notice" if register == REGISTER_PROBLEM else "Muted")
    label.style().unpolish(label)
    label.style().polish(label)
    effect = w.get("field_quality_effect")
    if effect is not None:
        effect.setText(exclusion_effect_text(ctx))
    # Enabled only when ticking it would actually exclude something — see
    # ``exclusion_can_apply``. Gating on "is there a block" left the box live
    # on a plate the scorer refuses, where a tick stays ticked and drops
    # nothing.
    box.setEnabled(exclusion_can_apply(ctx))
    box.setToolTip(_FQ_TOOLTIP if box.isEnabled() else
                   "Nothing to exclude on this dataset — the line below says "
                   "why.")


def _selected_well(ctx) -> str:
    """The well this panel's curve is about.

    The plate's current selection when there is exactly one, otherwise the
    last one clicked, otherwise the first the dataset holds — in that order,
    because a user who has selected one well means that one, and a user who
    has selected six is not asking a per-well question.
    """
    sel = getattr(ctx.state, "selection", None)
    wells = sorted(getattr(sel, "selected_wells", None) or ())
    if len(wells) == 1:
        return wells[0]
    last = getattr(sel, "last_sel", "") or ""
    if last:
        return str(last)
    if wells:
        return wells[0]
    cache = getattr(ctx.state.dataset, "cache", None) or {}
    return next(iter(sorted(cache)), "")


def _show_well_focus_curve(ctx) -> None:
    """Open the selected well's focus distribution.

    Reuses the Playground's window and renderer rather than drawing a second
    one: the two views answer the same question at different scopes, and a
    user comparing them must not have to decide whether two differently-drawn
    curves mean the same thing.
    """
    from review.core.focus_curve_view import show_focus_distribution
    from review.data.focus_gate import well_focus_distribution

    well = _selected_well(ctx)
    if not well:
        return
    show_focus_distribution(ctx, well_focus_distribution(ctx, well))


def _refresh_focus_status(ctx, w) -> None:
    """Re-read the dataset's focus column into the panel's third row.

    Shared by the builder's own handler and by
    :func:`qc_gate_refresh_widgets`, for the same reason its field-quality
    sibling is: the panel must not say one thing when it is built and another
    when it is refilled.
    """
    label = w.get("focus_status")
    box = w.get("exclude_soft_cells")
    if label is None or box is None:
        return
    text, register = focus_status(ctx)
    label.setText(text)
    label.setObjectName("Notice" if register == REGISTER_PROBLEM else "Muted")
    label.style().unpolish(label)
    label.style().polish(label)
    effect = w.get("focus_effect")
    if effect is not None:
        # The provenance rides on the effect line rather than taking a third
        # row of its own: it is empty in the ordinary case (the cut in force
        # IS the run's), and when it is not, a user comparing this panel with
        # Analyze's form needs both numbers side by side rather than a warning
        # that they disagree.
        effect.setText(" ".join(
            part for part in (focus_effect_text(ctx), recorded_cut_note(ctx))
            if part))
    # Live only when ticking it would exclude something. "Is there a column"
    # is the weaker predicate that leaves the box live on a dataset with no
    # threshold, where the tick stays ticked and drops nothing -- and every
    # number is then read by someone who believes a focus filter is on.
    box.setEnabled(focus_gate_can_apply(ctx))
    box.setToolTip(_FOCUS_TOOLTIP if box.isEnabled() else
                   "Nothing to exclude at this threshold — the line below "
                   "says why.")


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
    fields = [w["enable"], w["exclude_flagged_fields"],
              w["exclude_soft_cells"], w["focus_min"]] + [
        w[k] for k in edit_keys]
    blocked = [f.blockSignals(True) for f in fields]
    try:
        w["enable"].setChecked(bool(p.enabled))
        w["exclude_flagged_fields"].setChecked(bool(p.exclude_flagged_fields))
        w["exclude_soft_cells"].setChecked(bool(p.exclude_soft_cells))
        w["focus_min"].setText(_fmt(p.focus_min))
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
    # And the focus row, which is a fact about the dataset in the same way.
    _refresh_focus_status(win._ctx, w)
