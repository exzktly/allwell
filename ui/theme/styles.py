"""Dark-palette colour tokens for the All-Well Qt app.

``get_color(name)`` is the live accessor (matplotlib plot colours etc.); the
same values are also exposed as module-level constants (``ACCENT``, ``PLOT_BG``,
``WELL_COLOR_1`` …) for import-time readers. The application chrome stylesheet
itself comes from the repo-root ``theme.qss()``; this module only supplies the
named colour values both sides reference.
"""

from __future__ import annotations

from typing import Dict


# ── Source of truth: the v2 dark palette ────────────────────────────────────
# H20 reconciliation: where this dict has a concept that also lives in
# ``theme.Colors`` (panel / accent / text-primary / borders / etc.) the value
# is sourced from there, so the two namespaces no longer hand out different
# hex codes for the same surface. Plot-specific tokens (PLOT_BG / PLOT_GRD /
# PLOT_SPN / PLOT_TXT) intentionally stay independent — those drive the
# Publication-mode plot canvas, which is white-on-dark on purpose.
from theme import Colors as _C

def _build_dark_theme() -> Dict[str, str]:
    # Rebuilt on demand (not captured once) so a theme switch — which mutates
    # ``theme.Colors`` in place — is reflected by ``get_color()``. Registered as
    # a ``theme`` hook via :func:`refresh_from_theme` below.
    return {
    # Surfaces — v2 design tokens
    "BG_APP": _C.surface,             # was "#0F172A"
    "BG_SIDE": _C.rail,               # was "#1E293B"
    "BG_PANEL": _C.panel,             # was "#111827"
    "BG_CELL": _C.panel_elevated,     # was "#334155"
    "BG_HOVER": _C.hover,             # was "#475569"
    "BORDER": _C.border,              # was "#64748B"
    # Tab chrome — kept independent (no v2 token covers tab states)
    "TAB_BG": "#1F2937", "TAB_BG_ACTIVE": "#374151",
    "TAB_FG": "#CBD5E1", "TAB_FG_ACTIVE": "#FFFFFF", "TAB_BORDER": "#475569",
    # Group banners — distinct semantic accents kept as-is
    "GRP_DATA": "#3B82F6", "GRP_CONFIG": "#84CC16",
    # Text hierarchy
    "TXT_PRI": _C.text_primary,       # was "#F8FAFC"
    "TXT_SEC": _C.text_secondary,     # was "#E2E8F0"
    "TXT_MUT": _C.text_muted,         # was "#94A3B8"
    # Interactive accent
    "ACCENT": _C.accent,              # was "#3B82F6"
    "ACCENT_DARK": _C.accent_pressed, # was "#2563EB"
    "WARN": _C.warn,                  # was "#F59E0B"
    # Publication-mode plot palette — independent of app chrome by design
    "PLOT_BG": "#FFFFFF", "PLOT_GRD": "#B8CAE3",
    "PLOT_SPN": "#7F9FC9", "PLOT_TXT": "#2E4768",
    # Contrast inks
    "CLR_WHITE": _C.ink_light, "CLR_OFF_WHITE": _C.accent_fg,
    # Status / dialog palette — independent (no v2 equivalents)
    "CLR_SUCCESS": "#059669", "CLR_SUCCESS_DARK": "#047857",
    "CLR_SUCCESS_BG_DARK": "#064E3B", "CLR_SUCCESS_TEXT_SOFT": "#6EE7B7",
    "CLR_DANGER": "#DC2626", "CLR_DANGER_DARK": "#B91C1C",
    "CLR_DANGER_BG": "#7F1D1D", "CLR_DANGER_HOVER": "#991B1B",
    "CLR_ERROR_BG_DARK": "#7F1D1D", "CLR_ERROR_TEXT_SOFT": "#FCA5A5",
    "CLR_WARN_DARK": "#D97706", "CLR_WARN_TEXT": "#92400E", "CLR_WARN_BG": "#78350F",
    "CLR_SLATE_BG": _C.surface, "CLR_SLATE_TEXT": "#FCD34D",
    "CLR_MUTED_DISABLED": "#A0AEC0", "CLR_MUTED_TEXT_SOFT": "#CBD5E1",
    "CLR_PLACEHOLDER": "#64748B", "CLR_ERR_BAR": "#4A5568",
    "CLR_AVAIL_WELL": "#1F2937", "CLR_AVAIL_HOVER": "#374151",
    "TOOLTIP_BG": _C.ink_light, "TOOLTIP_FG": "#1F2937",
    "button_bg": _C.panel_elevated, "button_text": _C.text_primary,
    "button_text_disabled": _C.text_muted, "BTN_TEXT_BLACK": _C.ink_dark,
    "INSET_SHADOW": _C.surface, "INSET_HIGHLIGHT": _C.border,
    }


_DARK_THEME: Dict[str, str] = _build_dark_theme()


# Well palette — derived from the canonical tuple in the repo-root ``theme``
# module so every consumer (matplotlib's ``axes.prop_cycle`` via
# ``widgets/plot_card.plot_palette``, the QSS template substitution below,
# and ``well_viewer.plate_layout.WELL_COLORS``) reads the same 96 entries
# in the same order. Adjusting the palette only requires editing the
# source tuple in ``theme.py``.
from theme import WELL_COLORS_TUPLE as _WELL_COLORS_TUPLE

_WELL_COLORS: Dict[str, str] = {
    f"WELL_COLOR_{_i}": _hex
    for _i, _hex in enumerate(_WELL_COLORS_TUPLE, start=1)
}




def get_theme_colors() -> Dict[str, str]:
    d = dict(_DARK_THEME)
    d.update(_WELL_COLORS)
    return d


def get_color(name: str) -> str:
    return get_theme_colors().get(name, "#000000")



# ── Back-compat module-level color constants (dark defaults) ─────────────────
# Plot/controller code reads these at import time; they are frozen to the
# dark palette. Code paths that need live values must call get_color().
for _name, _value in _DARK_THEME.items():
    globals()[_name] = _value
for _name, _value in _WELL_COLORS.items():
    globals()[_name] = _value


def refresh_from_theme(_name: "str | None" = None) -> None:
    """Re-derive the palette from the freshly-applied ``theme.Colors``.

    Registered as a ``theme.register_theme_hook`` so that after
    ``theme.apply_theme(...)`` runs the live ``get_color()`` accessor and the
    module-level colour constants track the active theme. Names pulled elsewhere
    via ``from ui.theme.styles import X`` are value copies frozen at their import
    time and are *not* updated — chrome that must follow a theme switch reads
    ``get_color()`` live.
    """
    _DARK_THEME.clear()
    _DARK_THEME.update(_build_dark_theme())
    for _n, _v in _DARK_THEME.items():
        globals()[_n] = _v


# Keep get_color()/constants in sync when the app applies a non-default theme.
import theme as _theme
_theme.register_theme_hook(refresh_from_theme)
