"""Heat Map cell-text contrast: readable ink regardless of the active colormap.

Regression coverage for ``_contrast_ink`` (heatmap/controller.py), which
replaced a hardcoded ``color="black"`` on both per-cell text calls — a real
legibility bug against colormaps (viridis, inferno, ...) whose low end is
near-black. Uses ``im.to_rgba(v)`` (the ``AxesImage``'s own method) rather
than reconstructing ``cmap(norm(v))`` by hand, so the answer always matches
what's actually drawn — including a colormap's ``set_bad`` masking for
non-finite / non-positive-under-``LogNorm`` cells.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

pytest.importorskip("matplotlib")

import theme
from well_viewer.features.heatmap.controller import _contrast_ink


def _viridis_image(masked=None):
    import matplotlib
    from matplotlib.figure import Figure

    fig = Figure()
    ax = fig.add_subplot()
    cmap = matplotlib.colormaps["viridis"].copy()
    cmap.set_bad(color="#123456")
    data = masked if masked is not None else np.array([[1.0, 100.0]])
    return ax.imshow(data, cmap=cmap, vmin=1.0, vmax=100.0)


def test_low_end_of_viridis_gets_light_ink():
    im = _viridis_image()
    ink = _contrast_ink(im.to_rgba(1.0))
    assert ink == theme.Colors.ink_light


def test_high_end_of_viridis_gets_dark_ink():
    im = _viridis_image()
    ink = _contrast_ink(im.to_rgba(100.0))
    assert ink == theme.Colors.ink_dark


def test_log_masked_negative_value_uses_background_not_a_crash():
    from matplotlib.colors import LogNorm
    import matplotlib
    from matplotlib.figure import Figure

    fig = Figure()
    ax = fig.add_subplot()
    cmap = matplotlib.colormaps["viridis"].copy()
    bg = "#123456"
    cmap.set_bad(color=bg)
    arr = np.array([[1.0, 100.0], [-5.0, 50.0]])
    masked = np.ma.masked_where(arr <= 0, arr)
    im = ax.imshow(masked, cmap=cmap, norm=LogNorm(vmin=1.0, vmax=100.0))

    rgba = im.to_rgba(-5.0)
    from matplotlib.colors import to_rgba
    assert rgba == to_rgba(bg)
    # Must resolve to a real ink token, not raise.
    assert _contrast_ink(rgba) in (theme.Colors.ink_dark, theme.Colors.ink_light)
