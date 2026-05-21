"""Shared UI controls for the Fold-change normalization feature.

Used by the Bar Plots and Line Graphs tabs. Two parallel dropdowns:

* Control — divides each bar / curve by the picked well or replicate set's
  mean at the matching timepoint. ``—`` disables this axis.
* Baseline — divides each bar / curve by its own value at the chosen
  reference timepoint. Currently only ``t0`` (each member's first
  available timepoint) is offered; ``—`` disables this axis.

Both dropdowns are independent and may be combined. They mirror the
shared ``app._fc_*`` state so the same selection applies to both tabs.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QWidget,
)


_NONE_LABEL = "—"
_T0_LABEL = "t0 (first timepoint)"


# Re-export the scope registry + dirty-flag helpers from the pure-logic
# module. They live there so the unit tests can import them without
# pulling in PySide6; callers below use the same names as if they were
# defined here.
from well_viewer.fold_change_scopes import (  # noqa: E402
    FoldChangeScope,
    current_scope_name,
    flush_dirty_scopes,
    get_scope as _get_scope,
    redraw_scopes_or_defer,
    register_fold_change_scope,
    registered_scopes,
)

# Disambiguation suffix appended to a well-token combo entry when there is
# a replicate set with the same name. Without this, picking the entry
# would silently resolve to the replicate set (see resolve_control_wells)
# leaving the user no way to address the bare well. The suffix is stripped
# before the label is stored / dispatched, so existing saved selections
# without collisions are unaffected.
_WELL_DISAMBIG_SUFFIX = " (well)"


def _strip_well_suffix(label: str) -> str:
    """Remove the well-disambiguation suffix if present."""
    if label.endswith(_WELL_DISAMBIG_SUFFIX):
        return label[: -len(_WELL_DISAMBIG_SUFFIX)]
    return label


def _all_member_labels(app) -> list[str]:
    """Replicate-set names + selected well tokens currently plotted.

    When a well token collides with a rep-set name (e.g. someone named a
    rep-set "A01"), the well entry is suffixed with " (well)" so the
    user can pick either unambiguously.
    """
    names: list[str] = []
    repset_names: set[str] = set()
    seen: set[str] = set()
    for s in (app._app_state.selection.selections or []):
        if s.get("hidden"):
            continue
        name = s.get("name") or ""
        if not name or name in seen:
            continue
        wells = s.get("wells") or []
        if any(w in (app._app_state.dataset.well_paths or {}) for w in wells):
            names.append(name)
            seen.add(name)
            repset_names.add(name)
    well_paths = app._app_state.dataset.well_paths or {}
    for w in sorted(app._app_state.selection.selected_wells or [], key=lambda x: x):
        if w in well_paths and w not in seen:
            entry = w + _WELL_DISAMBIG_SUFFIX if w in repset_names else w
            names.append(entry)
            seen.add(w)
    return names


def _repopulate_control_combo(app, combo: QComboBox) -> None:
    """Refresh the Control combo's item list from current selections.

    Skips the clear-and-repopulate if (a) the combo's popup is currently
    visible (don't yank the dropdown out from under the user) or (b) the
    candidate list is identical to what the combo already shows. The
    selected index is still re-synced from app state.
    """
    members = _all_member_labels(app)
    saved = app._app_state.plot.fc_control_label or ""
    # If the saved label matches a bare well token AND there's a
    # collision-suffixed entry in the candidate list, prefer the suffixed
    # form so the user-visible state is unambiguous.
    if saved and saved in members:
        saved_display = saved
    elif saved + _WELL_DISAMBIG_SUFFIX in members:
        saved_display = saved + _WELL_DISAMBIG_SUFFIX
    else:
        saved_display = saved

    candidate_items = [_NONE_LABEL, *members]
    current_items = [combo.itemText(i) for i in range(combo.count())]
    popup_visible = False
    try:
        view = combo.view()
        popup_visible = bool(view is not None and view.isVisible())
    except Exception:
        pass

    blocked = combo.blockSignals(True)
    try:
        if current_items != candidate_items and not popup_visible:
            combo.clear()
            for m in candidate_items:
                combo.addItem(m)
        idx = combo.findText(saved_display) if saved_display else 0
        combo.setCurrentIndex(idx if idx >= 0 else 0)
    finally:
        combo.blockSignals(blocked)


def _repopulate_baseline_combo(app, combo: QComboBox) -> None:
    """Refresh the Baseline combo. Only ``t0`` is supported today."""
    on = bool(app._app_state.plot.fc_vs_t0_on)
    blocked = combo.blockSignals(True)
    try:
        combo.clear()
        combo.addItem(_NONE_LABEL)
        combo.addItem(_T0_LABEL)
        combo.setCurrentIndex(1 if on else 0)
    finally:
        combo.blockSignals(blocked)


class _ControlCombo(QComboBox):
    """QComboBox that refreshes its item list from current selections on popup."""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self._app = app

    def showPopup(self) -> None:  # type: ignore[override]
        _repopulate_control_combo(self._app, self)
        super().showPopup()


def _sync_widgets_to_state(app, *, skip_scope: str = "") -> None:
    """Mirror ``app._fc_*`` state into every installed widget set.

    Called after a state mutation so the other tab's widgets reflect the
    new state — the per-tab combos are independent QWidget instances, so
    without this sync the inactive tab's UI would go stale. Iterates the
    scope registry, so adding a third tab only requires registering it.
    """
    for scope in registered_scopes():
        if scope.name == skip_scope:
            continue
        ctrl = getattr(app, scope.ctrl_combo_attr, None)
        base = getattr(app, scope.baseline_combo_attr, None)
        if ctrl is not None:
            _repopulate_control_combo(app, ctrl)
        if base is not None:
            _repopulate_baseline_combo(app, base)


def set_fold_change_state(
    app, *,
    vs_control_on: "bool | None" = None,
    control_label: "str | None" = None,
    vs_t0_on: "bool | None" = None,
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
    # Normalize the control label here (UI layer): drop the disambiguation
    # suffix so the stored state stays a bare well token / rep-set name —
    # PlotState.set_fold_change keeps the UI artefact out of the state object.
    if control_label is not None and control_label.endswith(_WELL_DISAMBIG_SUFFIX):
        control_label = control_label[: -len(_WELL_DISAMBIG_SUFFIX)]
    app._app_state.plot.set_fold_change(
        vs_control_on=vs_control_on,
        control_label=control_label,
        vs_t0_on=vs_t0_on,
    )

    _sync_widgets_to_state(app, skip_scope=initiating_scope)

    # Redraw only the visible scope; mark others dirty for redraw on
    # tab switch. The runtime_app's tab-change handler calls
    # ``flush_dirty_scopes`` so the deferred work fires the moment the
    # user looks at the other tab. Closes the deferred 'redraw only
    # visible tab' item from the refactor plan.
    redraw_scopes_or_defer(app)
    # Announce on the bus. Additive — the explicit redraw above still runs.
    app._event_bus.plot_options_changed.emit(app._app_state.plot)


def install_fold_change_controls(app, parent: QWidget, layout, *, scope: str) -> None:
    """Insert the Fold-change Control + Baseline dropdowns into *layout*.

    ``scope`` is "bar" or "line" — used to namespace the per-tab widget
    references stashed on ``app``. The dropdowns write through to the
    shared ``app._fc_*`` state so both tabs stay in lockstep.
    """
    title = QLabel("Fold change:", parent)
    f = title.font(); f.setBold(True); title.setFont(f)
    layout.addWidget(title)

    layout.addWidget(QLabel("Control", parent))
    ctrl_combo = _ControlCombo(app, parent)
    ctrl_combo.setMinimumWidth(140)
    ctrl_combo.setToolTip(
        "Divide each bar / curve by the picked well or replicate set's "
        "mean at the same timepoint. — disables this axis."
    )
    _repopulate_control_combo(app, ctrl_combo)
    layout.addWidget(ctrl_combo)

    layout.addWidget(QLabel("Baseline", parent))
    baseline_combo = QComboBox(parent)
    baseline_combo.setMinimumWidth(140)
    baseline_combo.setToolTip(
        "Divide each bar / curve by its own value at the reference "
        "timepoint. — disables this axis."
    )
    _repopulate_baseline_combo(app, baseline_combo)
    layout.addWidget(baseline_combo)

    def _on_ctrl_changed(_idx: int) -> None:
        text = ctrl_combo.currentText()
        if text == _NONE_LABEL or not text:
            set_fold_change_state(
                app, vs_control_on=False, control_label="",
                initiating_scope=scope,
            )
        else:
            set_fold_change_state(
                app, vs_control_on=True, control_label=text,
                initiating_scope=scope,
            )

    def _on_baseline_changed(_idx: int) -> None:
        text = baseline_combo.currentText()
        set_fold_change_state(
            app, vs_t0_on=(text == _T0_LABEL),
            initiating_scope=scope,
        )

    ctrl_combo.currentIndexChanged.connect(_on_ctrl_changed)
    baseline_combo.currentIndexChanged.connect(_on_baseline_changed)

    # Stash references using the scope descriptor's attribute names so
    # the cross-tab sync helper picks them up via the registry. Falls
    # back to the standard naming pattern when the scope isn't
    # registered (e.g. a test stub).
    descriptor = _get_scope(scope)
    ctrl_attr = (descriptor.ctrl_combo_attr if descriptor
                 else f"_fc_ctrl_combo_{scope}")
    baseline_attr = (descriptor.baseline_combo_attr if descriptor
                     else f"_fc_baseline_combo_{scope}")
    setattr(app, ctrl_attr, ctrl_combo)
    setattr(app, baseline_attr, baseline_combo)
