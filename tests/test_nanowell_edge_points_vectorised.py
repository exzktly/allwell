"""``nanowell_detect._edge_points`` is vectorised over its rays without changing a bit.

The per-ray loop did two small-array means per ray, and ``refine_sites`` calls
it up to three times per site, so on a plate it was most of the detection time.
The vectorised version must give the same edges: each ray's plateau drop decides
``drop >= contrast``, so a last-bit change could flip a borderline ray. A plain
``mean(axis=1)`` of the boolean-selected columns does change the last bit (the
selection is Fortran-ordered, and an axis mean over that sums column by column),
which is why ``_row_means`` takes a C-contiguous copy first.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from pipeline import nanowell_detect as nd


@pytest.mark.parametrize("n", list(range(1, 201)))
def test_row_means_are_each_row_s_mean_bit_for_bit(n):
    rng = np.random.default_rng(n)
    rows = np.asfortranarray(rng.normal(0, 1, (6, n)) * 10.0 ** rng.uniform(-3, 6, (6, 1)))
    want = np.array([row.mean() for row in rows])
    assert np.array_equal(nd._row_means(rows), want)


def test_an_axis_mean_would_have_changed_the_last_bit():
    """The reason ``_row_means`` exists: the obvious vectorisation is not exact."""
    rng = np.random.default_rng(0)
    rows = np.asfortranarray(rng.normal(100, 30, (2000, 13)))   # as a boolean column selection returns
    want = np.array([row.mean() for row in rows])
    assert not np.array_equal(rows.mean(axis=1), want)
    assert np.array_equal(nd._row_means(rows), want)


def _old_edge_points(image, centre_px, radius_px, contrast):
    """The per-ray loop the vectorised version replaced, verbatim."""
    from scipy import ndimage

    angles = np.linspace(0, 2 * np.pi, nd._RAYS, endpoint=False)
    rhos = np.linspace(0.45 * radius_px, 1.7 * radius_px, 64)
    xs = centre_px[0] + np.outer(np.cos(angles), rhos)
    ys = centre_px[1] + np.outer(np.sin(angles), rhos)
    profile = ndimage.map_coordinates(image, [ys, xs], order=1, mode="nearest")
    step = rhos[1] - rhos[0]
    profile = ndimage.gaussian_filter1d(profile, 1.0 / step, axis=1)
    slope = np.gradient(profile, rhos, axis=1)
    peak = np.argmin(slope, axis=1)
    found = []
    inner_zone = rhos <= 0.7 * radius_px
    outer_zone = rhos >= 1.3 * radius_px
    for ray in range(nd._RAYS):
        k = int(peak[ray])
        if k < 2 or k > len(rhos) - 3:
            continue
        a, b, c = slope[ray, k - 1], slope[ray, k], slope[ray, k + 1]
        denom = a - 2 * b + c
        rho = rhos[k] + (0.5 * (a - c) / denom * step if denom > 0 else 0.0)
        drop = profile[ray, inner_zone].mean() - profile[ray, outer_zone].mean()
        if drop >= contrast:
            found.append((angles[ray], rho, drop / max(-slope[ray, k], 1e-9)))
    return found


def _same(x, y) -> bool:
    return x == y or (math.isnan(x) and math.isnan(y))


def test_the_edges_are_the_per_ray_loop_s_bit_for_bit():
    rng = np.random.default_rng(0)
    edges = 0
    yy, xx = np.mgrid[:120, :120]
    for trial in range(300):
        img = rng.normal(100, 20, (120, 120))
        cx, cy = rng.uniform(40, 80, 2)
        r = rng.uniform(8, 20)
        img += (np.hypot(xx - cx, yy - cy) < r) * rng.uniform(50, 400)
        if trial % 7 == 0:
            img[rng.random(img.shape) < 0.01] = np.nan if trial % 14 == 0 else 1e6
        centre = np.array([cx + rng.normal(0, 2), cy + rng.normal(0, 2)])
        radius, contrast = r * rng.uniform(0.8, 1.2), rng.uniform(0, 150)
        want = _old_edge_points(img, centre, radius, contrast)
        got = nd._edge_points(img, centre, radius, contrast)
        assert len(got) == len(want), trial
        for g, w in zip(got, want):
            assert all(_same(float(p), float(q)) for p, q in zip(g, w)), (trial, g, w)
        edges += len(want)
    assert edges > 5000, "too few edges found for this to measure anything"
