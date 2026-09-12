"""Black→color LUTs: the single-hue LUT names ramp black→color, not white→color."""
from __future__ import annotations

import pytest

from widgets.lut_cmaps import lut_cmap


def _rgb(cm, x):
    r, g, b, _a = cm(x)
    return (r, g, b)


def test_mono_lut_ramps_black_to_color():
    cm = lut_cmap("Reds")
    lo = _rgb(cm, 0.0)
    hi = _rgb(cm, 1.0)
    # Low end is black (all channels ~0), not white.
    assert max(lo) < 0.02
    # High end is saturated red.
    assert hi[0] > 0.98 and hi[1] < 0.02 and hi[2] < 0.02


def test_each_mono_lut_high_end_matches_its_color():
    for name, (r, g, b) in (("Greens", (0, 1, 0)), ("Blues", (0, 0, 1))):
        cm = lut_cmap(name)
        assert max(_rgb(cm, 0.0)) < 0.02          # black low end
        hi = _rgb(cm, 1.0)
        assert abs(hi[0] - r) < 0.02 and abs(hi[1] - g) < 0.02 and abs(hi[2] - b) < 0.02


def test_reverse_suffix_flips_the_ramp():
    cm = lut_cmap("Reds_r")
    assert _rgb(cm, 0.0)[0] > 0.98   # starts red
    assert max(_rgb(cm, 1.0)) < 0.02  # ends black


def test_non_mono_names_fall_back_to_builtin():
    from matplotlib import colormaps
    # Non-mono names return the built-in colormap (matplotlib hands back a fresh
    # object each access, so compare by name, not identity).
    assert lut_cmap("viridis").name == colormaps["viridis"].name == "viridis"
    # Built-in reverse variant is handled by matplotlib itself.
    assert lut_cmap("viridis_r").name == "viridis_r"


@pytest.mark.qt
def test_lut_colormap_to_rgb_reds_is_black_to_red():
    import numpy as np
    from review.core.ui_helpers import lut_colormap_to_rgb

    ramp = np.tile(np.linspace(0.0, 1.0, 16, dtype=np.float32)[:, None], (1, 4))
    rgb = lut_colormap_to_rgb(ramp, 0.0, 1.0, cmap_name="Reds")
    # Top row (low intensity) is black; bottom row (high) is red.
    assert rgb[0].max() < 6
    assert rgb[-1, :, 0].min() > 240 and rgb[-1, :, 1].max() < 6 and rgb[-1, :, 2].max() < 6


def test_resolve_channel_tint_override_and_default():
    from widgets.lut_cmaps import CHANNEL_TINTS as _CHANNEL_TINTS
    from widgets.lut_cmaps import resolve_channel_tint

    # Explicit per-channel override (0-255) wins and is normalized to 0..1,
    # case-insensitively keyed.
    assert resolve_channel_tint("GFP", 0, {"gfp": (255, 0, 0)}) == (1.0, 0.0, 0.0)

    # No override → fixed palette by channel index.
    assert resolve_channel_tint("gfp", 0, {}) == _CHANNEL_TINTS[0]
    assert resolve_channel_tint("rfp", 1, {}) == _CHANNEL_TINTS[1]
    assert resolve_channel_tint("anything", 0, None) == _CHANNEL_TINTS[0]  # None map

    # Index wraps around the palette.
    n = len(_CHANNEL_TINTS)
    assert resolve_channel_tint("c", n, {}) == _CHANNEL_TINTS[0]
