"""Preview-related helpers extracted from well_viewer3 for incremental migration.

The zip-member discovery + image-decode helpers (``classify_member``,
``read_member_bytes``, ``scan_zip_members``, ``open_imgref_as_array``) are pure
data/IO and now live in :mod:`well_viewer.data.zip_members` so the data layer can
use them without importing up into this feature. They are re-exported here for
backward compatibility with existing call sites.
"""

from __future__ import annotations

# Re-exported for backward compatibility — implementations live in the data layer.
from well_viewer.data.zip_members import (  # noqa: F401
    _NullContext,
    classify_member,
    open_imgref_as_array,
    read_member_bytes,
    scan_zip_members,
)
# Acyclic: preview_view imports review_image.renderer (not preview.controller),
# so this can sit at module scope rather than being deferred inside the shim.
from well_viewer.features.preview.preview_view import (
    refresh_preview_picker as _refresh_preview_picker_view,
)


def sync_preview_well_for_image_tabs(ctx) -> None:
    """Keep current preview well unless the active selection supplies one."""
    sel_state = ctx.state.selection
    preview = ctx.state.feature_slice("preview")
    if preview is None:  # Image Browser disabled — no preview state
        return
    cur_id = sel_state.current_selection_id
    cur_sel = next((s for s in sel_state.selections if s.get("id") == cur_id), None)
    sel_wells = (cur_sel.get("wells") or []) if cur_sel else []
    well_paths = ctx.state.dataset.well_paths

    # Preserve explicit user choice when still valid.
    current = preview.selected_well
    if current in well_paths:
        # If the active selection has wells, prefer its first loaded well.
        for tok in sel_wells:
            if tok in well_paths:
                preview.selected_well = tok
                return
        return

    # If no valid current well, use the active selection's first loaded well.
    for tok in sel_wells:
        if tok in well_paths:
            preview.selected_well = tok
            return

    # Final fallback: keep previous behavior of choosing first available well.
    if well_paths:
        from well_viewer.domain.viewer_state import parse_rc
        preview.selected_well = sorted(well_paths.keys(), key=parse_rc)[0]


def refresh_preview_picker_shim(ctx) -> None:
    # The preview picker refresh reads its state/widgets off ctx; the former
    # styling kwargs are unused by the view, so this is now a thin ctx delegate.
    _refresh_preview_picker_view(ctx)
