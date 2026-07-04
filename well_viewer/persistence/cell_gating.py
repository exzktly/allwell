"""Cell-gating threshold persistence (within ``pipeline_info.json``)."""

from __future__ import annotations

import logging

_logger = logging.getLogger("well_viewer")


def save_to_pipeline_info(ctx) -> None:
    """Persist non-default cell gating params to pipeline_info.json.

    Called from the Cell Gating tab whenever the user edits a value. Silently
    no-ops when no data directory is loaded or the sidecar is missing.

    Epic-cellgating: fully ``ctx``-native. Both the gate *values* (R6:
    ``fluor_gates`` / ``thresh_frac_on``) and the scatter-gate *config*
    (``scatter_gates`` + use-flags + per-layer gate selections) live on the
    typed ``ctx.state.cell_gating`` slice, so nothing is read off the god-object.
    ``fluor_gates`` is snapshotted via the core accessor (which iterates the
    fluor channels and applies the 0.0 default), matching the historical block —
    ratio entries stay out of ``fluor_gates`` exactly as before.
    """
    state = ctx.state
    if not state.dataset.data_dir:
        return
    from well_viewer.core.channel_state_controller import get_all_fluor_gates
    from well_viewer.domain.gating_state import (
        build_gating_block,
        save_gating_to_pipeline_info,
    )
    cg = state.cell_gating

    def _sorted_or_none(s):
        return None if s is None else sorted(s)

    block = build_gating_block(
        get_all_fluor_gates(ctx),
        # ``thresh_frac_on`` carries every non-default Cell Gating row (fluor
        # channels + ``ratio:<name>`` keys); ``build_gating_block`` drops any at
        # the default, so passing the typed slice reproduces the historical block.
        dict(cg.thresh_frac_on),
        scatter_gates=cg.scatter_gates or None,
        use_scatter_gates=cg.use_scatter_gates,
        scatter_gate_choices_gating=_sorted_or_none(cg.gating_choices),
        scatter_gate_choices_thresh=_sorted_or_none(cg.thresh_choices),
        use_scatter_thresh=cg.use_scatter_thresh,
    )
    save_gating_to_pipeline_info(state.dataset.data_dir, block)


def load_from_pipeline_info(ctx) -> bool:
    """Apply any saved cell_gating block in pipeline_info.json.

    Returns True when a block was found and applied. The gate values + scatter
    config are written to the typed ``ctx.state.cell_gating`` slice (the source
    of truth); the (lazily-built) Cell Gating tab is then materialized and its
    widgets are seeded from the slice.

    Epic-cellgating: fully ``ctx``-native. The scatter-gate config joins the
    R6 gate values on ``ctx.state.cell_gating``, so persistence writes typed
    state directly. The lazy-tab force-build is delegated to the core seam
    ``ensure_cell_gating_tab_built`` (persistence never names the god-object nor
    imports ``AppRuntime``); the widget mirror is a ``ctx``-native feature call.
    """
    state = ctx.state
    if not state.dataset.data_dir:
        return False
    from well_viewer.domain.gating_state import read_gating_params
    block = read_gating_params(state.dataset.data_dir)
    if not block:
        return False

    applied = False
    # ``cell_area_threshold`` is retired from this block — the QC layer now owns
    # cell-area gating and migrates any legacy value (see QCParams.from_pipeline_info).

    # R6 + Epic-cellgating: the typed CellGatingState slice is the source of
    # truth — write the gate values and the scatter-gate config onto it directly.
    cg = state.cell_gating
    fluor_gates = block.get("fluor_gates") or {}
    if isinstance(fluor_gates, dict):
        for ch, val in fluor_gates.items():
            try:
                fval = float(val)
            except (ValueError, TypeError):
                continue
            cg.fluor_gates[str(ch)] = fval
            applied = True

    thresh_frac_on = block.get("thresh_frac_on") or {}
    if isinstance(thresh_frac_on, dict):
        for ch, val in thresh_frac_on.items():
            try:
                fval = float(val)
            except (ValueError, TypeError):
                continue
            cg.thresh_frac_on[str(ch)] = fval
            applied = True

    # Restore the scatter-gate inclusion / thresholding config. The gates already
    # carry their axis columns, so they are usable straight from the sidecar.
    scatter_gates = block.get("scatter_gates")
    if isinstance(scatter_gates, list) and scatter_gates:
        cg.scatter_gates = [dict(g) for g in scatter_gates if isinstance(g, dict)]
        applied = True
    cg.use_scatter_gates = bool(block.get("use_scatter_gates", False))
    cg.use_scatter_thresh = bool(block.get("use_scatter_thresh", False))
    # Per-layer gate selections: a list of names, or absent ⇒ None (all).
    # Migration of older single-selection schemas: ``scatter_gate_choices`` (one
    # combined list) and ``scatter_gate_choice`` (one name) both seed BOTH layers.
    def _legacy_selection():
        if "scatter_gate_choices" in block and isinstance(block["scatter_gate_choices"], list):
            return set(str(n) for n in block["scatter_gate_choices"])
        legacy = block.get("scatter_gate_choice")
        return {str(legacy)} if legacy else None
    for key, attr in (
        ("scatter_gate_choices_gating", "gating_choices"),
        ("scatter_gate_choices_thresh", "thresh_choices"),
    ):
        if key in block:
            sel = block.get(key)
            setattr(cg, attr, set(str(n) for n in sel) if isinstance(sel, list) else None)
        else:
            setattr(cg, attr, _legacy_selection())

    # Materialize the lazily-built tab (core seam; the one app-bound step) so its
    # QLineEdits + checkboxes exist, then mirror the typed slice onto them.
    from well_viewer.core.channel_state_controller import (
        ensure_cell_gating_tab_built,
        refresh_thresh_frac_quick_edit,
    )
    if not ensure_cell_gating_tab_built(ctx):
        return False
    from well_viewer.features.grouping.cell_gating_tab_view import (
        cell_gating_apply_persisted_state,
    )
    cell_gating_apply_persisted_state(ctx)

    # Mirror the just-loaded value into the plotting ctxbar quick-edit so it
    # shows the persisted threshold on load (guarded; no-op before it's built).
    refresh_thresh_frac_quick_edit(ctx)

    return applied
