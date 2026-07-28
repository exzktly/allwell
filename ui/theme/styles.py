"""The last two legacy colour tokens without a `theme.Colors` home.

Every other named colour this module used to carry has migrated to
`theme.Colors` (the v2 design-token module) — either as a direct alias, a new
attribute, or by fixing a call site that hardcoded a stale pre-v2 hex value
directly. `get_color(name)` is the one live accessor still in use, for the two
`batch_export` progress-label colours (`CLR_DANGER`, `CLR_SUCCESS_DARK`) that
don't match `theme.Colors.danger`/`.success` closely enough to be a safe
direct swap. If a future change gives these an exact `theme.Colors` home too,
this module can be retired entirely.
"""

from __future__ import annotations

from theme import Colors as _C


def _build_dark_theme() -> dict[str, str]:
    # Rebuilt on demand (not captured once) so a theme switch — which mutates
    # ``theme.Colors`` in place — is reflected by ``get_color()``. Registered
    # as a ``theme`` hook via :func:`refresh_from_theme` below.
    return {
        "CLR_DANGER": _C.danger,
        "CLR_SUCCESS_DARK": "#047857",
    }


_DARK_THEME: dict[str, str] = _build_dark_theme()


def get_color(name: str) -> str:
    return _DARK_THEME.get(name, "#000000")


def refresh_from_theme(_name: str | None = None) -> None:
    """Re-derive the two tokens from the freshly-applied ``theme.Colors``.

    Registered as a ``theme.register_theme_hook`` so that after
    ``theme.apply_theme(...)`` runs, ``get_color("CLR_DANGER")`` tracks the
    active theme's ``danger`` token.
    """
    _DARK_THEME.clear()
    _DARK_THEME.update(_build_dark_theme())


# Keep get_color() in sync when the app applies a non-default theme.
import theme as _theme
_theme.register_theme_hook(refresh_from_theme)
