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


def sync_preview_well_for_image_tabs(app) -> None:
    """Keep current preview well unless the active selection supplies one."""
    cur_sel = app._sel_by_id(app._app_state.selection.current_selection_id)
    sel_wells = (cur_sel.get("wells") or []) if cur_sel else []

    # Preserve explicit user choice when still valid.
    current = app._app_state.preview.selected_well
    if current in app._app_state.dataset.well_paths:
        # If the active selection has wells, prefer its first loaded well.
        for tok in sel_wells:
            if tok in app._app_state.dataset.well_paths:
                app._app_state.preview.selected_well = tok
                return
        return

    # If no valid current well, use the active selection's first loaded well.
    for tok in sel_wells:
        if tok in app._app_state.dataset.well_paths:
            app._app_state.preview.selected_well = tok
            return

    # Final fallback: keep previous behavior of choosing first available well.
    if app._app_state.dataset.well_paths:
        app._app_state.preview.selected_well = sorted(app._app_state.dataset.well_paths.keys(), key=app._parse_rc)[0]


def refresh_preview_picker_shim(app) -> None:
    from well_viewer.features.preview.preview_view import refresh_preview_picker as _refresh_preview_picker_view
    from well_viewer.data.data_loading import extract_well_token as _extract_well_token
    from ui.theme import get_color
    _refresh_preview_picker_view(
        app,
        button_bg=get_color("button_bg"),
        button_text=get_color("button_text"),
        button_text_disabled=get_color("button_text_disabled"),
        accent=get_color("ACCENT"),
        clr_white=get_color("CLR_WHITE"),
        clr_accent_dark=get_color("ACCENT_DARK"),
        extract_well_token_fn=_extract_well_token,
    )
