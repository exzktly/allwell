"""Shared imports + constants used by the batch-export panel modules."""

from __future__ import annotations

import logging

from ui.theme import get_color
from review.domain.batch_models import BarGroup
from review.core.lazy import lazy as _lazy
# Lazy so batch export imports without barplot present (keeps it deletable).
_bar_render_items = _lazy("review.features.barplot.controller", "render_bar_items")
from review.data.data_loading import (
    _all_fluor_values,
    extract_well_token as _extract_well_token,
)
from review.domain.plate_layout import (
    PLATE_COLS as _PLATE_COLS,
    PLATE_ROWS as _PLATE_ROWS,
    WELL_COLORS,
)
from review.core.plot_style import apply_ax_style
from review.core.ui_helpers import (
    ask_name_dialog,
    btn_card,
    btn_danger,
    btn_primary,
    btn_secondary,
    clear_layout as _clear_layout_helper,
)
from review.domain.viewer_state import groups_with_loaded_wells as _groups_with_loaded_wells
from ui import theme

_logger = logging.getLogger("review")


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

_CLR_PLACEHOLDER = theme.CPub.placeholder
_CLR_DISABLED_WELL = theme.CPub.well_disabled
_CLR_ERR_BAR = theme.CPub.err_bar
_CLR_WHITE = theme.Colors.ink_light


__all__ = [
    "get_color",
    "BarGroup", "_bar_render_items",
    "_all_fluor_values", "_extract_well_token",
    "_PLATE_COLS", "_PLATE_ROWS", "WELL_COLORS",
    "apply_ax_style",
    "ask_name_dialog", "btn_card", "btn_danger", "btn_primary", "btn_secondary",
    "_clear_layout_helper",
    "_groups_with_loaded_wells", "_logger", "unique_export_stem",
    "_CLR_PLACEHOLDER",
    "_CLR_DISABLED_WELL", "_CLR_ERR_BAR", "_CLR_WHITE",
]
