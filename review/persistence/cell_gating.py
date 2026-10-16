"""Cell-gating threshold persistence (within ``pipeline_info.json``)."""

from __future__ import annotations

import logging

_logger = logging.getLogger("review")


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
    from review.core.channel_state_controller import (
        get_all_fluor_gates,
        seg_channel_token_of,
    )
    from review.domain.gating_state import (
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
        # The nuclear channel's ThreshFracOn default differs from the readout
        # default, so the block builder needs to know which channel it is to
        # diff each value against the right one.
        seg_channel_token=seg_channel_token_of(ctx),
        thresh_count_on=dict(cg.thresh_count_on),
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
    from review.domain.gating_state import read_gating_params
    block = read_gating_params(state.dataset.data_dir)
    if not block:
        return False
    return apply_gating_block(ctx, block)


def apply_gating_block(ctx, block) -> bool:
    """Apply a ``cell_gating`` *block* (as produced by ``build_gating_block``)
    onto the live ``ctx.state.cell_gating`` slice + Cell Gating tab widgets.

    Extracted from :func:`load_from_pipeline_info` so both the sidecar load path
    AND Saved-Plots restore can re-apply the same gating state (gate values +
    scatter-gate config) from an in-memory block, not just the on-disk sidecar.
    Returns True when the block was applied (the tab materialised + widgets
    mirrored); False for an empty block or when the tab could not be built.

    Note: this seeds the typed slice + widgets but does NOT reset pre-existing
    slice values and does NOT run the gating worker — the sidecar-load path
    applies it onto a freshly-cleared slice and recomputes the Included
    population separately (``_kick_off_gating_after_load``). Saved-Plots restore
    wants a reset + a worker kick, so it uses :func:`restore_gating_block`.
    """
    if not isinstance(block, dict) or not block:
        return False
    applied = _write_gating_block_to_slice(ctx.state.cell_gating, block)
    if not _mirror_gating_to_widgets(ctx):
        return False
    return applied


def clear_from_pipeline_info(ctx) -> None:
    """Reset the live gating slice and drop the sidecar's ``cell_gating`` block.

    The title bar's "Reset all saved state" action deletes ``persistence.json``,
    but the fluorescence gates, the ThreshFracOn / ThreshCountOn cutoffs, and
    the drawn scatter gates are not stored there — they live in ``pipeline_info.json`` so they
    travel with the dataset. Deleting the one file therefore left every gate in
    place, and the reload restored them from the sidecar as if nothing had
    happened. This clears both halves: the slice first (so nothing in the reload
    can write the old values back), then the sidecar block. Pipeline-owned keys
    in the sidecar are untouched.
    """
    from review.domain.gating_state import save_gating_to_pipeline_info

    restore_gating_block(ctx, {})
    data_dir = ctx.state.dataset.data_dir
    if data_dir:
        # An empty block removes the key outright — see save_gating_to_pipeline_info.
        save_gating_to_pipeline_info(data_dir, {})


def restore_gating_block(ctx, block) -> None:
    """Reset the live gating slice to defaults, apply *block*, and recompute the
    Included population (kicks the gating worker).

    Saved-Plots restore uses this so a restored plot recreates the EXACT gated
    population behind the saved figure: the reset clears any gate the user added
    after saving (``build_gating_block`` / the block only carry non-default
    values), and the worker kick refreshes the Included flags every plot reads.
    Best-effort — a missing / empty / all-default block still resets + refreshes.
    """
    cg = ctx.state.cell_gating
    cg.fluor_gates.clear()
    cg.thresh_frac_on.clear()
    cg.thresh_count_on.clear()
    cg.scatter_gates = []
    cg.use_scatter_gates = False
    cg.use_scatter_thresh = False
    cg.gating_choices = None
    cg.thresh_choices = None
    # The pass this kicks records its own configuration when it starts; until
    # then a well read from disk must not be re-gated with the one just cleared.
    cg.applied_gating = None
    if isinstance(block, dict) and block:
        _write_gating_block_to_slice(cg, block)
    _mirror_gating_to_widgets(ctx, run_worker=True)


def _write_gating_block_to_slice(cg, block: dict) -> bool:
    """Write a gating *block*'s gate values + scatter-gate config onto the typed
    ``CellGatingState`` slice *cg*. Pure state mutation — no widgets, no worker.

    R6 + Epic-cellgating: the typed slice is the source of truth. Additive for
    ``fluor_gates`` / ``thresh_frac_on`` / ``thresh_count_on`` (only keys the
    block carries are written); callers wanting a clean reset clear the dicts
    first.
    """
    # ``cell_area_threshold`` is retired from this block — the QC layer now owns
    # cell-area gating and migrates any legacy value (see QCParams.from_pipeline_info).
    applied = False
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

    # Absent on every sidecar written before count metrics carried their own
    # cutoff. Nothing to migrate: the accessor's default then applies, which is
    # the value that makes those plots draw at all.
    thresh_count_on = block.get("thresh_count_on") or {}
    if isinstance(thresh_count_on, dict):
        for ch, val in thresh_count_on.items():
            try:
                fval = float(val)
            except (ValueError, TypeError):
                continue
            cg.thresh_count_on[str(ch)] = fval
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
    return applied


def _mirror_gating_to_widgets(ctx, *, run_worker: bool = False) -> bool:
    """Materialize the lazily-built Cell Gating tab and mirror the typed slice
    onto its widgets. When *run_worker*, also kick the gating worker so the
    Included population is recomputed. Returns False when the tab can't be built.
    """
    # Materialize the lazily-built tab (core seam; the one app-bound step) so its
    # QLineEdits + checkboxes exist, then mirror the typed slice onto them.
    from review.core.channel_state_controller import (
        ensure_cell_gating_tab_built,
        refresh_thresh_frac_quick_edit,
    )
    if not ensure_cell_gating_tab_built(ctx):
        return False
    from review.features.home.cell_gating.controller import (
        cell_gating_apply_persisted_state,
        cell_gating_start_gating_worker,
    )
    cell_gating_apply_persisted_state(ctx)

    # Mirror the just-loaded value into Plotting's band quick-edit so it
    # shows the persisted threshold on load (guarded; no-op before it's built).
    refresh_thresh_frac_quick_edit(ctx)

    if run_worker:
        cell_gating_start_gating_worker(ctx)
    return True
