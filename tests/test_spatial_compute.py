"""Pure-math unit tests for well_viewer.domain.spatial_compute.

Qt-free — no PySide6 import, no display. Runs entirely on synthetic point
patterns so failures point at the algorithm, not at the pipeline / GUI.
"""

from __future__ import annotations

import numpy as np
import pytest

from well_viewer.domain import spatial_compute as spc


# ── nearest-neighbour + k-NN + local density ─────────────────────────────────


def test_nnd_on_unit_square():
    """Four corners of a unit square → every NND = 1.0."""
    pts = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=float)
    nnd = spc.nearest_neighbour_distance(pts)
    assert np.allclose(nnd, 1.0)


def test_nnd_nan_when_only_one_point():
    nnd = spc.nearest_neighbour_distance(np.array([[0.0, 0.0]]))
    assert np.isnan(nnd[0])


def test_knn_mean_on_regular_grid():
    """3x3 grid: for the centre cell, 4 neighbours at 1 + 4 diagonals at √2,
    the k=4 mean distance = 1 (4 orthogonal neighbours)."""
    xs, ys = np.meshgrid(np.arange(3), np.arange(3))
    pts = np.column_stack([xs.ravel(), ys.ravel()]).astype(float)
    knn = spc.knn_mean_distance(pts, k=4)
    centre_idx = 4  # (1, 1) is the 5th point in row-major
    assert knn[centre_idx] == pytest.approx(1.0, abs=1e-9)


def test_local_density_scales_with_k():
    """Uniform points on [0,10]² → density estimate ~ N/area = 1.0 cells/px².
    """
    rng = np.random.default_rng(0)
    pts = rng.uniform(0, 10, size=(100, 2))
    density = spc.local_density(pts, k=5)
    finite = density[np.isfinite(density)]
    assert finite.size == 100
    # Point-density under uniformity should be close to 1 cell/pixel² — allow
    # ~5x tolerance for finite-N variance in the k-th NN.
    assert 0.2 < float(np.median(finite)) < 5.0


# ── Ripley's L ───────────────────────────────────────────────────────────────


def test_ripley_uniform_within_envelope():
    """A uniform pattern's L(r) − r should sit inside its own CSR envelope."""
    rng = np.random.default_rng(42)
    pts = rng.uniform(0, 100, size=(400, 2))
    result = spc.ripley_l(pts, n_r=25, n_sims=25, seed=1)
    inside = (result.L_minus_r >= result.csr_lo - 1e-6) & \
             (result.L_minus_r <= result.csr_hi + 1e-6)
    # Allow one or two radii to poke outside — this is a probabilistic check.
    assert int(inside.sum()) >= 23


def test_ripley_clustered_exceeds_envelope():
    """Ten tight blobs → L(r) − r sits ABOVE the CSR envelope at small r."""
    rng = np.random.default_rng(0)
    centres = rng.uniform(10, 90, size=(10, 2))
    pts_list = []
    for c in centres:
        pts_list.append(rng.normal(loc=c, scale=1.0, size=(30, 2)))
    pts = np.vstack(pts_list)
    result = spc.ripley_l(pts, n_r=25, n_sims=25, seed=2)
    # At small r a clustered pattern should exceed the envelope.
    small_r_idx = np.arange(3, 10)
    assert bool(np.any(result.L_minus_r[small_r_idx] > result.csr_hi[small_r_idx]))


# ── DBSCAN colony detection ──────────────────────────────────────────────────


def test_dbscan_finds_two_well_separated_blobs():
    blob_a = np.array([[0, 0], [0.5, 0], [0, 0.5], [0.5, 0.5]], dtype=float)
    blob_b = blob_a + np.array([50.0, 0.0])
    pts = np.vstack([blob_a, blob_b])
    colonies = spc.detect_colonies(pts, method=spc.METHOD_DBSCAN, eps=2.0,
                                    min_samples=2)
    assert colonies.n_colonies == 2
    # Blob A points share a label, blob B points share a different label.
    la = set(colonies.labels[:4].tolist())
    lb = set(colonies.labels[4:].tolist())
    assert len(la) == 1 and len(lb) == 1 and la != lb


def test_dbscan_eps_defaults_to_multiple_of_median_nnd():
    pts = np.array([[i, 0.0] for i in range(6)], dtype=float)  # NND = 1.0
    colonies = spc.detect_colonies(pts, eps_mult=2.0, min_samples=2)
    # eps = 2 × 1.0 = 2.0 → chain the whole line into one colony.
    assert colonies.n_colonies == 1
    assert colonies.eps == pytest.approx(2.0, abs=1e-9)


def test_single_linkage_cut():
    blob_a = np.array([[0, 0], [1, 0], [0, 1]], dtype=float)
    blob_b = blob_a + np.array([20.0, 0.0])
    pts = np.vstack([blob_a, blob_b])
    colonies = spc.detect_colonies(
        pts, method=spc.METHOD_SINGLE_LINKAGE, eps=2.0)
    assert colonies.n_colonies == 2


# ── colony stats + intra-colony geometry ─────────────────────────────────────


def test_colony_stats_hull_area_and_size():
    pts = np.array([[0, 0], [4, 0], [4, 3], [0, 3], [2, 1.5]], dtype=float)
    labels = np.zeros(pts.shape[0], dtype=int)
    stats = spc.colony_stats(pts, labels)
    assert stats.ids.tolist() == [0]
    assert stats.size.tolist() == [5]
    # Rectangle 4×3 = 12; the interior point doesn't extend the hull.
    assert stats.hull_area[0] == pytest.approx(12.0, abs=1e-6)
    assert stats.packing_density[0] == pytest.approx(5.0 / 12.0, abs=1e-6)


def test_intra_colony_geometry_edge_vs_interior():
    # A ring of 8 corner-ish points plus one dead-centre point.
    ring = np.array([
        [0, 0], [1, 0], [2, 0],
        [2, 1],
        [2, 2], [1, 2], [0, 2],
        [0, 1],
    ], dtype=float)
    centre = np.array([[1.0, 1.0]])
    pts = np.vstack([ring, centre])
    labels = np.zeros(pts.shape[0], dtype=int)
    geom = spc.intra_colony_geometry(pts, labels, boundary_pct=25.0)
    # The centre point sits at d_edge = 1 (max); its is_boundary must be False.
    assert geom.is_boundary[-1] is np.False_ or bool(geom.is_boundary[-1]) is False
    # At least a couple of ring points must be flagged boundary.
    assert bool(geom.is_boundary[:8].any())
    # r/R for the centre is 0 (distance to centroid = 0 there).
    assert geom.r_over_R[-1] == pytest.approx(0.0, abs=1e-9)


# ── radial profile ───────────────────────────────────────────────────────────


def test_radial_profile_captures_gradient():
    """Metric = r/R linearly → profile.mean should be monotonic increasing."""
    r_over_R = np.linspace(0.0, 1.0, 500)
    metric = r_over_R.copy()
    profile = spc.radial_profile(r_over_R, metric, n_bins=10)
    diffs = np.diff(profile.mean)
    assert bool(np.all(diffs > 0))


def test_radial_profile_handles_empty_input():
    profile = spc.radial_profile(np.array([]), np.array([]), n_bins=5)
    assert profile.bin_centres.shape == (5,)
    assert bool(np.all(np.isnan(profile.mean)))


# ── variance decomposition (inter- vs intra-colony) ─────────────────────────


def test_variance_decomposition_identical_colonies_icc_zero():
    """Every colony has the same mean and same spread → ICC = 0."""
    metric = np.array([1.0, 2.0, 3.0,   # colony 0
                       1.0, 2.0, 3.0,   # colony 1
                       1.0, 2.0, 3.0])  # colony 2
    labels = np.array([0, 0, 0, 1, 1, 1, 2, 2, 2])
    d = spc.variance_decomposition(metric, labels)
    assert d.sigma2_between == pytest.approx(0.0, abs=1e-9)
    assert d.sigma2_within > 0
    assert d.icc == pytest.approx(0.0, abs=1e-9)
    assert d.sigma2_between + d.sigma2_within == pytest.approx(d.sigma2_total, abs=1e-9)


def test_variance_decomposition_pure_between_icc_one():
    """Each colony is a constant, but the colonies differ → ICC = 1."""
    metric = np.array([1.0, 1.0, 1.0,   # colony 0
                       5.0, 5.0, 5.0,   # colony 1
                       9.0, 9.0, 9.0])  # colony 2
    labels = np.array([0, 0, 0, 1, 1, 1, 2, 2, 2])
    d = spc.variance_decomposition(metric, labels)
    assert d.sigma2_within == pytest.approx(0.0, abs=1e-9)
    assert d.icc == pytest.approx(1.0, abs=1e-9)
    assert d.n_colonies == 3
    assert d.n_cells == 9


def test_variance_decomposition_drops_noise_and_nan():
    """Noise-labelled cells (-1) and non-finite metric values are excluded."""
    metric = np.array([1.0, 2.0, np.nan,  99.0,  10.0, 20.0])
    labels = np.array([0,   0,   0,        -1,   1,    1])
    d = spc.variance_decomposition(metric, labels)
    assert d.n_cells == 4          # excluded: the nan and the noise cell
    assert d.n_colonies == 2


def test_variance_decomposition_matches_hand_calc():
    metric = np.array([1.0, 3.0, 5.0, 7.0], dtype=float)
    labels = np.array([0, 0, 1, 1], dtype=int)
    # colony means: 2, 6; grand mean: 4; between SS = 2*(2-4)^2 + 2*(6-4)^2 = 16
    # within SS = (1-2)^2 + (3-2)^2 + (5-6)^2 + (7-6)^2 = 4
    d = spc.variance_decomposition(metric, labels)
    assert d.sigma2_between == pytest.approx(16.0 / 4.0)   # = 4
    assert d.sigma2_within == pytest.approx(4.0 / 4.0)     # = 1
    assert d.sigma2_total == pytest.approx(5.0)
    assert d.icc == pytest.approx(4.0 / 5.0)


def test_variance_decomposition_shape_mismatch_raises():
    with pytest.raises(ValueError):
        spc.variance_decomposition(np.array([1.0, 2.0]), np.array([0]))
