"""Black→color LUT colormaps for single-hue fluorescence LUTs.

The single-colour LUT names (Reds/Greens/Blues/Purples/Oranges) must ramp
**black→colour** (black at low intensity, saturated colour at high) — the standard
fluorescence look on a black background. matplotlib's built-in single-hue maps ramp
*white→colour* instead, so we build a black→colour ``LinearSegmentedColormap`` for
those names and fall back to the built-in colormap for everything else (viridis,
magma, Greys, …).

Qt-free and matplotlib-only, and it lives in ``widgets/`` (not ``well_viewer/``) so
both the standalone ``widgets/lut_selector.py`` strip and ``well_viewer.core``
renderers can import it without inverting the package layering.
"""

from __future__ import annotations

from typing import Dict, Tuple

# Saturated single colours for the monochrome LUT names → black→colour ramp.
# ``Greys`` is intentionally absent: it stays a plain greyscale (black→white) ramp,
# handled by the callers' non-colormap fast path / the built-in ``Greys_r``.
# Mirrors image_table/image_compose.py::LUT_COLORS (kept in sync by hand).
LUT_MONO_COLORS: Dict[str, Tuple[float, float, float]] = {
    "Reds":    (1.0, 0.0, 0.0),
    "Greens":  (0.0, 1.0, 0.0),
    "Blues":   (0.0, 0.0, 1.0),
    "Purples": (0.56, 0.0, 1.0),
    "Oranges": (1.0, 0.55, 0.0),
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
