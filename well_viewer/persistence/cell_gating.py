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
    block = build_gating_block(
        fluor_gates, thresh_frac_on,
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

    if not hasattr(app, "_cell_gating_fluor_gate_edits"):
        build = getattr(app, "_centre_build_pending", None)
        if callable(build):
            build("Sample Definitions")
        from well_viewer.features.grouping.cell_gating_tab_view import build_cell_gating_subtab
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

    # Mirror the just-loaded value into the plotting ctxbar quick-edit so it
    # shows the persisted threshold on load (guarded; no-op before it's built).
    refresh = getattr(app, "_refresh_thresh_frac_quick_edit", None)
    if callable(refresh):
        refresh()

    return applied
