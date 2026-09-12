"""Preview-related helpers.

The zip-member discovery + image-decode helpers are pure data/IO and live in
:mod:`review.data.zip_members` so the data layer can use them without
importing up into this feature. Only ``read_member_bytes`` is still re-exported
here, for the one call site that reaches it this way (the smFISH worker); the
four names that were re-exported beside it — ``_NullContext``,
``classify_member``, ``scan_zip_members`` and ``open_imgref_as_array`` — had no
caller left, and a re-export nobody imports is dead wiring that reads as live.
"""

from __future__ import annotations

# Re-exported for the smFISH worker — the implementation is in the data layer.
from review.data.zip_members import read_member_bytes  # noqa: F401
# Acyclic: preview_view imports review_image.renderer (not preview.controller),
# so this can sit at module scope rather than being deferred inside the shim.
from review.features.preview.preview_view import (
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
        from review.domain.viewer_state import parse_rc
        preview.selected_well = sorted(well_paths.keys(), key=parse_rc)[0]


def refresh_preview_picker_shim(ctx) -> None:
    # The preview picker refresh reads its state/widgets off ctx; the former
    # styling kwargs are unused by the view, so this is now a thin ctx delegate.
    _refresh_preview_picker_view(ctx)
