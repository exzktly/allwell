"""Shared UI controls for the Fold-change normalization feature.

Used by the Bar Plots and Line Graphs tabs. Two parallel dropdowns:

* Control — divides each bar / curve by the picked well or replicate set's
  mean at the matching timepoint. ``—`` disables this axis.
* Baseline — divides each bar / curve by its own value at the chosen
  reference timepoint. Pick any available timepoint, or "earliest" to
  mean "each member's first finite point" (the legacy ``vs t0``
  behaviour); ``—`` disables this axis.

Both dropdowns are independent and may be combined. They mirror the
shared ``ctx._fc_*`` state so the same selection applies to both tabs.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QLabel, QWidget,
)


_NONE_LABEL = "—"
# "earliest" means: pick each member's own earliest finite point — the
# original ``vs t0`` semantics. Distinct from naming an explicit tp so
# datasets with different first-tp values keep behaving consistently.
_EARLIEST_LABEL = "earliest (each member's first tp)"


# Re-export the scope registry + dirty-flag helpers from the pure-logic
# module. They live there so the unit tests can import them without
# pulling in PySide6; callers below use the same names as if they were
# defined here.
from well_viewer.domain.fold_change_scopes import (
    flush_dirty_scopes,  # noqa: F401 - re-exported for the tab-change handler
    get_scope as _get_scope,
    registered_scopes,
)
from well_viewer.core.safe import swallowed
import theme

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_OP_CTRL_COMBO_MIN_W = 110
_OP_T0_COMBO_MIN_W = 110
_ORDER_COMBO_MIN_W = 130

# Disambiguation suffix appended to a well-token combo entry when there is
# a replicate set with the same name. Without this, picking the entry
# would silently resolve to the replicate set (see resolve_control_wells)
# leaving the user no way to address the bare well. The suffix is stripped
# before the label is stored / dispatched, so existing saved selections
# without collisions are unaffected.
_WELL_DISAMBIG_SUFFIX = " (well)"


def _all_member_entries(ctx) -> list[tuple[str, object]]:
    """``(display text, ControlRef)`` for everything the Control combo offers.

    Replicate-set names first, then the plotted wells, then every remaining
    dataset well — so a well used purely as a reference (a background well
    subtracted from the samples, say) can be chosen even when it is not part
    of the current plot selection.

    Each entry carries its own :class:`~well_viewer.domain.control_ref.ControlRef`,
    which is what makes the choice unambiguous. The ``" (well)"`` suffix on a
    well whose token equals a replicate-set name is now **display only**:
    nothing parses it back, so a set that happens to be *named* ``A01 (well)``
    can no longer be mistaken for the well ``A01``.

    A8 teardown: reads typed ``ctx.state.selection`` / ``ctx.state.dataset``
    so it takes ``ctx`` and names no god-object.
    """
    from well_viewer.domain.control_ref import ControlRef

    entries: list[tuple[str, object]] = []
    repset_names: set[str] = set()
    # Replicate-set names and well tokens are tracked apart. Sharing one "seen"
    # set makes a well whose token equals a replicate-set name look like a
    # duplicate of it, so the well is dropped from the list entirely and the
    # suffixed entry below is never reached — which is the case the suffix
    # exists for.
    seen_wells: set[str] = set()
    for s in (ctx.state.selection.selections or []):
        if s.get("hidden"):
            continue
        name = s.get("name") or ""
        if not name or name in repset_names:
            continue
        wells = s.get("wells") or []
        if any(w in (ctx.state.dataset.well_paths or {}) for w in wells):
            entries.append((name, ControlRef.for_replicate_set(name)))
            repset_names.add(name)
    well_paths = ctx.state.dataset.well_paths or {}
    for w in sorted(ctx.state.selection.selected_wells or [], key=lambda x: x):
        if w in well_paths and w not in seen_wells:
            ref = ControlRef.for_well(w)
            entries.append((ref.combo_text(disambiguate=w in repset_names), ref))
            seen_wells.add(w)
    # Expose every remaining dataset well too, so a well used purely as a
    # reference — e.g. a negative-control or background well subtracted from the
    # samples via "vs Control → Subtract" at each matching timepoint — can be
    # chosen as the Control even when it isn't part of the current plot
    # selection. Plotted members stay first; the rest follow in plate order.
    for w in sorted(well_paths.keys(), key=lambda x: x):
        if w not in seen_wells:
            ref = ControlRef.for_well(w)
            entries.append((ref.combo_text(disambiguate=w in repset_names), ref))
            seen_wells.add(w)
    return entries


def _all_member_labels(ctx) -> list[str]:
    """Just the display texts of :func:`_all_member_entries`.

    Kept for the Cell Gating optimizer, which builds its own combo and hands
    the text straight to the resolver — a legacy reference, resolved by the
    old precedence.
    """
    return [text for text, _ref in _all_member_entries(ctx)]


def _repopulate_control_combo(ctx, combo: QComboBox) -> None:
    """Refresh the Control combo's item list from current selections.

    Skips the clear-and-repopulate if (a) the combo's popup is currently
    visible (don't yank the dropdown out from under the user) or (b) the
    candidate list is identical to what the combo already shows. The
    selected index is still re-synced from ctx state.
    """
    entries = _all_member_entries(ctx)
    saved = ctx.state.plot.fc_control

    candidate_items: list[tuple[str, object]] = [(_NONE_LABEL, None), *entries]
    # A stored control that matches nothing on offer is **not** dropped back to
    # "—". It stays selected, under a label saying so. Silently resetting the
    # combo left the panel claiming no control while the plot went on dividing
    # by one — the control panel misreporting live state, which is worse than
    # an entry the user has to deal with. This happens whenever the thing the
    # control named goes away: a replicate set deleted, a well not in this
    # dataset.
    if saved is not None and not any(ref == saved for _t, ref in candidate_items):
        candidate_items.append(
            (f"{saved.display_name} — not in current selections", saved))

    current_items = [combo.itemText(i) for i in range(combo.count())]
    popup_visible = False
    try:
        view = combo.view()
        popup_visible = bool(view is not None and view.isVisible())
    except Exception:
        swallowed()  # was: pass (silent except)

    blocked = combo.blockSignals(True)
    try:
        if current_items != [t for t, _r in candidate_items] and not popup_visible:
            combo.clear()
            for text, ref in candidate_items:
                combo.addItem(text, ref)
        # Matched on the reference, never on the text: the text carries a
        # display-only suffix that changes as collisions come and go.
        idx = 0
        for i in range(combo.count()):
            if combo.itemData(i) == saved:
                idx = i
                break
        combo.setCurrentIndex(idx)
    finally:
        combo.blockSignals(blocked)


_OP_LABEL_RATIO = "Ratio (÷)"
_OP_LABEL_SUBTRACT = "Subtract (−)"

_ORDER_LABEL_CTRL_FIRST = "Control first"
_ORDER_LABEL_T0_FIRST = "Baseline first"


def _repopulate_op_combo(ctx, combo: QComboBox, *, attr: str) -> None:
    """Populate an Operator combo with the two FC operators and set the
    current selection from ``PlotState.<attr>``. ``attr`` is one of
    ``"fc_op_ctrl"`` / ``"fc_op_t0"`` — each axis has its own combo."""
    chosen = str(getattr(ctx.state.plot, attr, "ratio") or "ratio")
    blocked = combo.blockSignals(True)
    try:
        combo.clear()
        combo.addItem(_OP_LABEL_RATIO)
        combo.addItem(_OP_LABEL_SUBTRACT)
        combo.setCurrentIndex(1 if chosen == "subtract" else 0)
    finally:
        combo.blockSignals(blocked)


def _repopulate_order_combo(ctx, combo: QComboBox) -> None:
    """Populate the Order combo with the two FC application orders and
    set the current selection from ``PlotState.fc_order``."""
    chosen = str(getattr(ctx.state.plot, "fc_order", "ctrl_first") or "ctrl_first")
    blocked = combo.blockSignals(True)
    try:
        combo.clear()
        combo.addItem(_ORDER_LABEL_CTRL_FIRST)
        combo.addItem(_ORDER_LABEL_T0_FIRST)
        combo.setCurrentIndex(1 if chosen == "t0_first" else 0)
    finally:
        combo.blockSignals(blocked)


def _available_timepoint_strs(ctx) -> list[str]:
    """Return the dataset's timepoints formatted as the bar tp-strings
    (``f"{t:.4g}"``), so the baseline picker matches the rest of the UI."""
    try:
        tps = sorted(ctx.state.dataset.all_timepoints_cache or [])
    except Exception:
        return []
    return [f"{float(t):.4g}" for t in tps]


def _repopulate_baseline_combo(ctx, combo: QComboBox) -> None:
    """Refresh the Baseline combo with every available timepoint.

    Items: ``—`` (off), ``earliest`` (each member's first finite point —
    the original ``vs t0`` behaviour), then one entry per dataset
    timepoint formatted the same way the Bar Plots tp-multi-select uses
    (``f"{t:.4g}"``). Selection is driven from ``PlotState.fc_vs_t0_on``
    and ``PlotState.fc_baseline_tp``: empty + on → ``earliest``; a
    matching tp string → that timepoint; off → ``—``.
    """
    on = bool(ctx.state.plot.fc_vs_t0_on)
    chosen_tp = str(getattr(ctx.state.plot, "fc_baseline_tp", "") or "").strip()
    tp_strs = _available_timepoint_strs(ctx)

    blocked = combo.blockSignals(True)
    try:
        combo.clear()
        combo.addItem(_NONE_LABEL)
        combo.addItem(_EARLIEST_LABEL)
        for tp in tp_strs:
            combo.addItem(tp)
        if not on:
            combo.setCurrentIndex(0)
        elif chosen_tp and chosen_tp in tp_strs:
            combo.setCurrentText(chosen_tp)
        else:
            combo.setCurrentIndex(1)  # earliest
    finally:
        combo.blockSignals(blocked)


class _ControlCombo(QComboBox):
    """QComboBox that refreshes its item list from current selections on popup."""

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._app = ctx

    def showPopup(self) -> None:  # type: ignore[override]
        _repopulate_control_combo(self._app, self)
        super().showPopup()


class _BaselineCombo(QComboBox):
    """QComboBox that re-lists the dataset's timepoints on every popup.

    Without this, the Baseline combo is only populated at build time and on
    fold-change state mutations. If it was built before the dataset's timepoint
    cache was filled (the common case on first load), it would keep showing only
    ``—`` / ``earliest`` and none of the real timepoints. Refreshing on popup —
    like ``_ControlCombo`` — guarantees the live timepoint list is shown.
    """

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._app = ctx

    def showPopup(self) -> None:  # type: ignore[override]
        _repopulate_baseline_combo(self._app, self)
        super().showPopup()


def _sync_widgets_to_state(ctx, *, skip_scope: str = "") -> None:
    """Mirror ``ctx._fc_*`` state into every installed widget set.

    Called after a state mutation so the other tab's widgets reflect the
    new state — the per-tab combos are independent QWidget instances, so
    without this sync the inactive tab's UI would go stale. Iterates the
    scope registry, so adding a third tab only requires registering it.
    Op combos use the standard naming convention ``_fc_op_ctrl_combo_<scope>``
    / ``_fc_op_t0_combo_<scope>``.
    """
    for scope in registered_scopes():
        if scope.name == skip_scope:
            continue
        reg = ctx.widgets
        ctrl = reg.get_or_none(scope.ctrl_combo_attr)
        base = reg.get_or_none(scope.baseline_combo_attr)
        if ctrl is not None:
            _repopulate_control_combo(ctx, ctrl)
        if base is not None:
            _repopulate_baseline_combo(ctx, base)
        op_ctrl_combo = reg.get_or_none(f"_fc_op_ctrl_combo_{scope.name}")
        if op_ctrl_combo is not None:
            _repopulate_op_combo(ctx, op_ctrl_combo, attr="fc_op_ctrl")
        op_t0_combo = reg.get_or_none(f"_fc_op_t0_combo_{scope.name}")
        if op_t0_combo is not None:
            _repopulate_op_combo(ctx, op_t0_combo, attr="fc_op_t0")
        order_combo = reg.get_or_none(f"_fc_order_combo_{scope.name}")
        if order_combo is not None:
            _repopulate_order_combo(ctx, order_combo)


_BAR_LIMIT_KEYS = ("x_lim_min", "x_lim_max", "y_lim_min", "y_lim_max")


def _reset_bar_ylim_edits(ctx) -> None:
    """Drop the bar plot's user-entered axis limits so the autofit reclaims the
    panels after a fold-change change.

    A fold-change mutation moves the bar values onto a different scale (raw
    100-1500 vs ratio 0-10 vs subtract -100..300), so previously pinned limits no
    longer make sense. The limits live in the per-figure export-axis store
    (``ctx.state.export_prefs.axis_prefs['bar']``) — both the flat keys and the
    per-panel ``axis_buckets``; log scale / font sizes / axis target are kept.
    Idempotent and a no-op when nothing is stored yet."""
    export_prefs = getattr(getattr(ctx, "state", None), "export_prefs", None)
    axis_prefs = getattr(export_prefs, "axis_prefs", None)
    if not isinstance(axis_prefs, dict):
        return
    bar = axis_prefs.get("bar")
    if not isinstance(bar, dict):
        return
    for _k in _BAR_LIMIT_KEYS:
        bar.pop(_k, None)
    buckets = bar.get("axis_buckets")
    if isinstance(buckets, dict):
        for _b in buckets.values():
            if isinstance(_b, dict):
                for _k in _BAR_LIMIT_KEYS:
                    _b.pop(_k, None)


def set_fold_change_state(
    ctx, *,
    vs_control_on: bool | None = None,
    control=None,
    clear_control: bool = False,
    vs_t0_on: bool | None = None,
    baseline_tp: str | None = None,
    op: str | None = None,
    op_ctrl: str | None = None,
    op_t0: str | None = None,
    order: str | None = None,
    initiating_scope: str = "",
) -> None:
    """Single entry point for fold-change state mutations.

    Mirrors the shape of ``runtime_app._set_active_channel`` — every
    combo handler funnels through here so state mutation, widget sync,
    and redraw fire in a fixed order regardless of which tab initiated
    the change. ``None`` arguments leave the corresponding field
    unchanged. ``initiating_scope`` lets the caller skip syncing back
    into the widget that just wrote the value (it already holds it,
    and re-applying could yank an open popup).
    """
    # The control label is stored exactly as the combo offered it, suffix and
    # all. Stripping the " (well)" suffix here — which this did — defeated the
    # only thing the suffix is for: ``resolve_control_wells`` tries the
    # suffixed spelling against the well tokens *before* it tries a replicate
    # set name, and that ordering is what lets a user address the well `A01`
    # when a replicate set is also called `A01`. With the suffix stripped that
    # branch is unreachable, so picking the well entry silently normalised
    # against the replicate set instead.
    #
    # Nothing downstream needs the bare form: ``resolve_control_wells``
    # understands both spellings, and ``_repopulate_control_combo`` already
    # matches the saved value against the suffixed candidate first. Documents
    # written before this keep their bare labels and keep resolving the way
    # they always did — the ambiguity they were saved with cannot be recovered.
    ctx.state.plot.set_fold_change(
        vs_control_on=vs_control_on,
        control=control,
        clear_control=clear_control,
        vs_t0_on=vs_t0_on,
        baseline_tp=baseline_tp,
        op=op,
        op_ctrl=op_ctrl,
        op_t0=op_t0,
        order=order,
    )

    # Any FC mutation invalidates the user's previous y-axis range —
    # under different FC settings the bar values live on a different
    # scale (raw 100-1500 vs ratio 0-10 vs subtract -100 to 300). Clear
    # the stored bar axis limits so the renderer's autofit takes over.
    _reset_bar_ylim_edits(ctx)

    _sync_widgets_to_state(ctx, skip_scope=initiating_scope)

    # Announce on the bus. The RedrawCoordinator subscriber coalesces the
    # redraw — visible scope now, others marked dirty for redraw on tab switch
    # (the tab-change handler calls ``flush_dirty_scopes``). The bus emit *is*
    # the redraw trigger; see redraw_coordinator.py / fold_change_scopes.py.
    ctx.bus.plot_options_changed.emit(ctx.state.plot)


def install_fold_change_controls(ctx, parent: QWidget, layout, *, scope: str) -> None:
    """Insert the Fold-change Control + Baseline dropdowns into *layout*.

    ``scope`` is "bar" or "line" — used to namespace the per-tab widget
    references stashed on ``ctx``. The dropdowns write through to the
    shared ``ctx._fc_*`` state so both tabs stay in lockstep.

    A8: takes ``ctx``; the combos stash per-tab handles on the ctx and write the
    shared fold-change state, so it unwraps the handle once at this boundary.
    """
    title = QLabel("Fold change:", parent)
    f = title.font(); f.setBold(True); title.setFont(f)
    layout.addWidget(title)

    layout.addWidget(QLabel("Control", parent))
    ctrl_combo = _ControlCombo(ctx, parent)
    ctrl_combo.setMinimumWidth(theme.Metrics.combo_min_w)
    ctrl_combo.setToolTip(
        "Divide each bar / curve by the picked well or replicate set's "
        "mean at the same timepoint. — disables this axis."
    )
    _repopulate_control_combo(ctx, ctrl_combo)
    layout.addWidget(ctrl_combo)

    # Per-axis operator selector for the control axis.
    op_ctrl_combo = QComboBox(parent)
    op_ctrl_combo.setMinimumWidth(_OP_CTRL_COMBO_MIN_W)
    op_ctrl_combo.setToolTip(
        "How the control denominator is applied. 'Ratio' divides each "
        "value by the control; 'Subtract' subtracts it. Independent of "
        "the baseline operator — you can subtract the control and divide "
        "by t0 (or vice-versa)."
    )
    _repopulate_op_combo(ctx, op_ctrl_combo, attr="fc_op_ctrl")
    layout.addWidget(op_ctrl_combo)

    layout.addWidget(QLabel("Baseline", parent))
    baseline_combo = _BaselineCombo(ctx, parent)
    baseline_combo.setMinimumWidth(theme.Metrics.combo_min_w)
    baseline_combo.setToolTip(
        "Normalise each bar / curve against its own value at the "
        "reference timepoint. — disables this axis."
    )
    _repopulate_baseline_combo(ctx, baseline_combo)
    layout.addWidget(baseline_combo)

    # Per-axis operator selector for the baseline-tp axis.
    op_t0_combo = QComboBox(parent)
    op_t0_combo.setMinimumWidth(_OP_T0_COMBO_MIN_W)
    op_t0_combo.setToolTip(
        "How the baseline-tp denominator is applied. 'Ratio' divides "
        "each value by its own baseline; 'Subtract' subtracts it. "
        "Independent of the control operator."
    )
    _repopulate_op_combo(ctx, op_t0_combo, attr="fc_op_t0")
    layout.addWidget(op_t0_combo)

    # Order selector — which axis applies first. Matters only with mixed
    # ops (same-op cases commute mathematically).
    layout.addWidget(QLabel("Order", parent))
    order_combo = QComboBox(parent)
    order_combo.setMinimumWidth(_ORDER_COMBO_MIN_W)
    order_combo.setToolTip(
        "Which fold-change axis applies first. ‘Control first’ "
        "(ΔΔCt-style): control is applied, then baseline normalises the "
        "result against its own post-control baseline. ‘Baseline first’: "
        "baseline is applied, then control is divided/subtracted against "
        "its own post-baseline control. Commutes when both axes use the "
        "same operator; matters with mixed ops."
    )
    _repopulate_order_combo(ctx, order_combo)
    layout.addWidget(order_combo)

    def _on_ctrl_changed(_idx: int) -> None:
        # The reference, not the text. The text is chrome and may carry a
        # display-only suffix; parsing it back is what made a replicate set
        # named "A01 (well)" unselectable.
        ref = ctrl_combo.currentData()
        if ref is None:
            set_fold_change_state(
                ctx, vs_control_on=False, clear_control=True,
                initiating_scope=scope,
            )
        else:
            set_fold_change_state(
                ctx, vs_control_on=True, control=ref,
                initiating_scope=scope,
            )

    def _on_baseline_changed(_idx: int) -> None:
        text = baseline_combo.currentText()
        if text == _NONE_LABEL or not text:
            set_fold_change_state(
                ctx, vs_t0_on=False, baseline_tp="",
                initiating_scope=scope,
            )
        elif text == _EARLIEST_LABEL:
            set_fold_change_state(
                ctx, vs_t0_on=True, baseline_tp="",
                initiating_scope=scope,
            )
        else:
            # An explicit timepoint string from ``_available_timepoint_strs``.
            set_fold_change_state(
                ctx, vs_t0_on=True, baseline_tp=text,
                initiating_scope=scope,
            )

    def _on_op_ctrl_changed(_idx: int) -> None:
        text = op_ctrl_combo.currentText()
        op_v = "subtract" if text == _OP_LABEL_SUBTRACT else "ratio"
        set_fold_change_state(ctx, op_ctrl=op_v, initiating_scope=scope)

    def _on_op_t0_changed(_idx: int) -> None:
        text = op_t0_combo.currentText()
        op_v = "subtract" if text == _OP_LABEL_SUBTRACT else "ratio"
        set_fold_change_state(ctx, op_t0=op_v, initiating_scope=scope)

    def _on_order_changed(_idx: int) -> None:
        text = order_combo.currentText()
        order_v = "t0_first" if text == _ORDER_LABEL_T0_FIRST else "ctrl_first"
        set_fold_change_state(ctx, order=order_v, initiating_scope=scope)

    ctrl_combo.currentIndexChanged.connect(_on_ctrl_changed)
    baseline_combo.currentIndexChanged.connect(_on_baseline_changed)
    op_ctrl_combo.currentIndexChanged.connect(_on_op_ctrl_changed)
    op_t0_combo.currentIndexChanged.connect(_on_op_t0_changed)
    order_combo.currentIndexChanged.connect(_on_order_changed)

    # Stash references using the scope descriptor's attribute names so
    # the cross-tab sync helper picks them up via the registry. Falls
    # back to the standard naming pattern when the scope isn't
    # registered (e.g. a test stub).
    descriptor = _get_scope(scope)
    ctrl_attr = (descriptor.ctrl_combo_attr if descriptor
                 else f"_fc_ctrl_combo_{scope}")
    baseline_attr = (descriptor.baseline_combo_attr if descriptor
                     else f"_fc_baseline_combo_{scope}")
    # These combo handles live in the widget registry (``ctx`` is a frozen
    # dataclass — it can't hold per-scope widget attrs), keyed by the same
    # names the cross-tab sync helper looks them up under. register() also
    # stamps ``app.<name>`` so any legacy attribute reader still resolves.
    ctx.widgets.register(ctrl_attr, ctrl_combo)
    ctx.widgets.register(baseline_attr, baseline_combo)
    # Per-axis op combos stash under the standard naming convention the
    # cross-tab sync helper looks up.
    ctx.widgets.register(f"_fc_op_ctrl_combo_{scope}", op_ctrl_combo)
    ctx.widgets.register(f"_fc_op_t0_combo_{scope}", op_t0_combo)
    ctx.widgets.register(f"_fc_order_combo_{scope}", order_combo)
