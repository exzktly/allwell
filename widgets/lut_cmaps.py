"""Black→color LUT colormaps for single-hue fluorescence LUTs.

The single-colour LUT names (Reds/Greens/Blues/Purples/Oranges) must ramp
**black→colour** (black at low intensity, saturated colour at high) — the standard
fluorescence look on a black background. matplotlib's built-in single-hue maps ramp
*white→colour* instead, so we build a black→colour ``LinearSegmentedColormap`` for
those names and fall back to the built-in colormap for everything else (viridis,
magma, Greys, …).

Qt-free and matplotlib-only, and it lives in ``widgets/`` (not ``review/``) so
both the standalone ``widgets/lut_selector.py`` strip and ``review.core``
renderers can import it without inverting the package layering.
"""

from __future__ import annotations


# Saturated single colours for the monochrome LUT names → black→colour ramp.
# ``Greys`` is intentionally absent: it stays a plain greyscale (black→white) ramp,
# handled by the callers' non-colormap fast path / the built-in ``Greys_r``.
# Mirrors image_table/image_compose.py::LUT_COLORS (kept in sync by hand).
LUT_MONO_COLORS: dict[str, tuple[float, float, float]] = {
    "Reds":    (1.0, 0.0, 0.0),
    "Greens":  (0.0, 1.0, 0.0),
    "Blues":   (0.0, 0.0, 1.0),
    "Purples": (0.56, 0.0, 1.0),
    "Oranges": (1.0, 0.55, 0.0),
    "Cyan":    (0.0, 1.0, 1.0),
    "Magenta": (1.0, 0.0, 1.0),
}


def lut_cmap(name: str):
    """Return the matplotlib ``Colormap`` for LUT *name*.

    Black→colour ``LinearSegmentedColormap`` for the monochrome LUT names in
    :data:`LUT_MONO_COLORS`; otherwise the built-in colormap of that name. A
    trailing ``"_r"`` reverses the result (for the built-ins matplotlib already
    registers its own reversed variant, so we pass the full name straight through).
    """
    from matplotlib import colormaps as _cmaps
    from matplotlib.colors import LinearSegmentedColormap

    reverse = name.endswith("_r")
    base = name[:-2] if reverse else name
    mono = LUT_MONO_COLORS.get(base)
    if mono is None:
        # Built-in colormap (viridis, magma, Greys, Greys_r, …) — matplotlib
        # handles the "_r" reverse suffix itself, so use the original name.
        return _cmaps[name]
    cm = LinearSegmentedColormap.from_list(base, [(0.0, 0.0, 0.0), mono])
    return cm.reversed() if reverse else cm


# Additive tints for a multi-channel composite (black background → hue), in
# channel order; a channel past the end of the list falls back to the start.
# Distinct from ``LUT_MONO_COLORS`` above, which names a *LUT* the user picked:
# these are the automatic per-channel hues an overlay assigns when nobody has.
CHANNEL_TINTS: tuple[tuple[float, float, float], ...] = (
    (0.0, 1.0, 0.0),  # green
    (1.0, 0.0, 0.0),  # red / magenta-ish
    (0.0, 0.6, 1.0),  # blue
    (1.0, 0.0, 1.0),  # magenta
    (0.0, 1.0, 1.0),  # cyan
    (1.0, 1.0, 0.0),  # yellow
)


def resolve_channel_tint(channel: str, index: int,
                         tint_map: dict[str, tuple] | None = None) -> tuple:
    """Additive tint for a channel as an ``(r, g, b)`` 0..1 float tuple.

    An explicit per-channel override (the shared ``tint_by_channel``, 0–255)
    wins; otherwise fall back to the fixed palette by channel index. Pure (no
    Qt / ctx), so it's unit-testable on its own.

    It lives here rather than with either caller because both the Cell Browser's
    composite and the Image Table's builder shelf ask it, and they are separately
    removable features — the Image Table used to import it out of the Cell
    Browser, which made deleting one break the other.
    """
    override = (tint_map or {}).get(str(channel).strip().lower())
    if override is not None:
        return tuple(max(0.0, min(1.0, float(c) / 255.0)) for c in override)
    return CHANNEL_TINTS[index % len(CHANNEL_TINTS)]
