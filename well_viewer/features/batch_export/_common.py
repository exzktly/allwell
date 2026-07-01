"""Shared imports + constants used by the batch-export panel modules."""

from __future__ import annotations

import logging

from ui.theme import PLOT_BG, PLOT_SPN, WARN, get_color
from well_viewer.domain.batch_models import BarGroup
from well_viewer.core.lazy import lazy as _lazy
# Lazy so batch export imports without barplot present (keeps it deletable).
_bar_render_items = _lazy("well_viewer.features.barplot.controller", "render_bar_items")
from well_viewer.data.data_loading import (
    _all_fluor_values,
    extract_well_token as _extract_well_token,
)
from well_viewer.domain.plate_layout import (
    PLATE_COLS as _PLATE_COLS,
    PLATE_ROWS as _PLATE_ROWS,
    WELL_COLORS,
)
from well_viewer.core.plot_style import apply_ax_style
from well_viewer.core.ui_helpers import (
    ask_name_dialog,
    btn_card,
    btn_danger,
    btn_primary,
    btn_secondary,
    clear_layout as _clear_layout_helper,
)
from well_viewer.domain.viewer_state import groups_with_loaded_wells as _groups_with_loaded_wells

_logger = logging.getLogger("well_viewer")


def unique_export_stem(stem: str, used: set) -> str:
    """Return *stem* made unique against *used*, recording it.

    Group names are sanitised to ``[A-Za-z0-9_-]`` for filenames, so two groups
    that differ only in punctuation (e.g. ``"Drug A/B"`` and ``"Drug A B"``)
    collapse to the same stem and the second export would silently overwrite the
    first. Appending ``_2``, ``_3``, … on collision keeps every group's output.
    """
    candidate = stem
    n = 2
    while candidate in used:
        candidate = f"{stem}_{n}"
        n += 1
    used.add(candidate)
    return candidate

_CLR_DANGER = "#d2453d"
_CLR_SUCCESS_DARK = "#2e7d32"
_CLR_PLACEHOLDER = "#9aa0a6"
_CLR_DISABLED_WELL = "#404040"
_CLR_ERR_BAR = "#333333"
_CLR_WHITE = "#ffffff"


__all__ = [
    "PLOT_BG", "PLOT_SPN", "WARN", "get_color",
    "BarGroup", "_bar_render_items",
    "_all_fluor_values", "_extract_well_token",
    "_PLATE_COLS", "_PLATE_ROWS", "WELL_COLORS",
    "apply_ax_style",
    "ask_name_dialog", "btn_card", "btn_danger", "btn_primary", "btn_secondary",
    "_clear_layout_helper",
    "_groups_with_loaded_wells", "_logger", "unique_export_stem",
    "_CLR_DANGER", "_CLR_SUCCESS_DARK", "_CLR_PLACEHOLDER",
    "_CLR_DISABLED_WELL", "_CLR_ERR_BAR", "_CLR_WHITE",
]
