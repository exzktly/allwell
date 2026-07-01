"""Cell-gating threshold persistence (within ``pipeline_info.json``)."""

from __future__ import annotations

import logging

_logger = logging.getLogger("well_viewer")


def save_to_pipeline_info(app) -> None:
    """Persist non-default cell gating params to pipeline_info.json.

    Called from the Cell Gating tab whenever the user edits a value. Silently
    no-ops when no data directory is loaded or the sidecar is missing.
    """
    if not app._app_state.dataset.data_dir:
        return
    if not hasattr(app, "_cell_gating_thresh_frac_edits"):
        return
    from well_viewer.domain.gating_state import (
        build_gating_block,
        save_gating_to_pipeline_info,
    )
    from well_viewer.features.grouping.cell_gating_tab_view import cell_gating_get_thresh_frac_on
    fluor_gates = app._get_all_fluor_gates()
    # Persist every key that has a Cell Gating row, including ratio entries
    # (keyed by ``ratio:<name>``). Iterating only ``app._app_state.channel.fluor_channels``
    # silently dropped ratio thresholds and forced the default on next load.
    thresh_frac_on = {
        key: cell_gating_get_thresh_frac_on(app, key)
        for key in (app._cell_gating_thresh_frac_edits or {}).keys()
    }
    def _sorted_or_none(s):
        return None if s is None else sorted(s)
    block = build_gating_block(
        fluor_gates, thresh_frac_on,
        scatter_gates=getattr(app, "_scatter_gates", None),
        use_scatter_gates=getattr(app, "_cell_gating_use_scatter_gates", False),
        scatter_gate_choices_gating=_sorted_or_none(
            getattr(app, "_cell_gating_gating_choices", None)),
        scatter_gate_choices_thresh=_sorted_or_none(
            getattr(app, "_cell_gating_thresh_choices", None)),
        use_scatter_thresh=getattr(app, "_cell_gating_use_scatter_thresh", False),
    )
    save_gating_to_pipeline_info(app._app_state.dataset.data_dir, block)


def load_from_pipeline_info(app) -> bool:
    """Apply any saved cell_gating block in pipeline_info.json.

    Returns True when a block was found and applied. The Cell Gating tab is
    built lazily, so when the sidecar has persisted thresholds we force-build
    the tab here so its QLineEdits exist before we try to set them.
    """
    if not app._app_state.dataset.data_dir:
        return False
    from well_viewer.domain.gating_state import read_gating_params
    block = read_gating_params(app._app_state.dataset.data_dir)
    if not block:
        return False

    # One deferred feature import for the whole function (the tab is built lazily
    # so persistence can't import it at module scope without a cycle).
    from well_viewer.features.grouping.cell_gating_tab_view import (
        _cell_gating_apply_scatter_gate_mode,
        _cell_gating_refresh_gate_filter,
        _cell_gating_refresh_gate_summary,
        build_cell_gating_subtab,
    )

    if not hasattr(app, "_cell_gating_fluor_gate_edits"):
        build = getattr(app, "_centre_build_pending", None)
        if callable(build):
            build("Sample Definitions")
        build_cell_gating_subtab(app)
    if not hasattr(app, "_cell_gating_fluor_gate_edits"):
        return False

    applied = False
    # ``cell_area_threshold`` is retired from this block — the QC layer now owns
    # cell-area gating and migrates any legacy value (see QCParams.from_pipeline_info).

    fluor_gates = block.get("fluor_gates") or {}
    if isinstance(fluor_gates, dict):
        for ch, val in fluor_gates.items():
            edit = app._cell_gating_fluor_gate_edits.get(str(ch))
            if edit is None:
                continue
            try:
                edit.setText(str(float(val)))
                applied = True
            except (ValueError, TypeError):
                pass

    thresh_frac_on = block.get("thresh_frac_on") or {}
    if isinstance(thresh_frac_on, dict):
        if not hasattr(app, "_thresh_frac_on_saved"):
            app._thresh_frac_on_saved = {}
        for ch, val in thresh_frac_on.items():
            try:
                fval = float(val)
            except (ValueError, TypeError):
                continue
            app._thresh_frac_on_saved[str(ch)] = fval
            edit = app._cell_gating_thresh_frac_edits.get(str(ch))
            if edit is not None:
                edit.setText(str(fval))
                applied = True

    # Restore the scatter-gate inclusion mode. The gates already carry their
    # axis columns, so they are usable straight from the sidecar. Sync the
    # checkbox + FluorGating-edit enablement + summary label to match.
    scatter_gates = block.get("scatter_gates")
    if isinstance(scatter_gates, list) and scatter_gates:
        app._scatter_gates = [dict(g) for g in scatter_gates if isinstance(g, dict)]
        applied = True
    app._cell_gating_use_scatter_gates = bool(block.get("use_scatter_gates", False))
    app._cell_gating_use_scatter_thresh = bool(block.get("use_scatter_thresh", False))
    # Per-layer gate selections: a list of names, or absent ⇒ None (all).
    # Migration of older single-selection schemas: ``scatter_gate_choices`` (one
    # combined list) and ``scatter_gate_choice`` (one name) both seed BOTH layers.
    def _legacy_selection():
        if "scatter_gate_choices" in block and isinstance(block["scatter_gate_choices"], list):
            return set(str(n) for n in block["scatter_gate_choices"])
        legacy = block.get("scatter_gate_choice")
        return {str(legacy)} if legacy else None
    for key, attr in (
        ("scatter_gate_choices_gating", "_cell_gating_gating_choices"),
        ("scatter_gate_choices_thresh", "_cell_gating_thresh_choices"),
    ):
        if key in block:
            sel = block.get(key)
            setattr(app, attr, set(str(n) for n in sel) if isinstance(sel, list) else None)
        else:
            setattr(app, attr, _legacy_selection())
    for attr, flag in (
        ("_cell_gating_use_scatter_gates_cb", app._cell_gating_use_scatter_gates),
        ("_cell_gating_use_scatter_thresh_cb", app._cell_gating_use_scatter_thresh),
    ):
        cb = getattr(app, attr, None)
        if cb is not None:
            blocked = cb.blockSignals(True)
            try:
                cb.setChecked(flag)
            finally:
                cb.blockSignals(blocked)
    _cell_gating_refresh_gate_filter(app)
    _cell_gating_apply_scatter_gate_mode(app)
    _cell_gating_refresh_gate_summary(app)

    # Mirror the just-loaded value into the plotting ctxbar quick-edit so it
    # shows the persisted threshold on load (guarded; no-op before it's built).
    refresh = getattr(app, "_refresh_thresh_frac_quick_edit", None)
    if callable(refresh):
        refresh()

    return applied
