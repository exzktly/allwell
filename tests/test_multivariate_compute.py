"""Tests for the Qt-free multivariate analysis engine.

These exercise ``review.domain.multivariate_compute`` with synthetic
fixtures (planted clusters, known correlations) so the algorithms are validated
without any Qt / matplotlib / dataset dependency — same contract as
``tests/test_stats_compute.py``.
"""

from __future__ import annotations

import numpy as np
import pytest

from review.domain import multivariate_compute as mvc


# ── standardize ──────────────────────────────────────────────────────────────

def test_standardize_zero_mean_unit_variance():
    rng = np.random.default_rng(0)
    X = rng.normal(loc=[10.0, -5.0], scale=[3.0, 0.1], size=(500, 2))
    Xs = mvc.standardize(X)
    assert np.allclose(Xs.mean(axis=0), 0.0, atol=1e-9)
    assert np.allclose(Xs.std(axis=0), 1.0, atol=1e-6)


def test_standardize_constant_column_is_centered_not_nan():
    X = np.column_stack([np.full(10, 7.0), np.arange(10.0)])
    Xs = mvc.standardize(X)
    assert np.all(np.isfinite(Xs))
    assert np.allclose(Xs[:, 0], 0.0)  # constant column collapses to zeros


def test_standardize_handles_residual_nan():
    X = np.array([[1.0, 2.0], [np.nan, 4.0], [3.0, 6.0]])
    Xs = mvc.standardize(X)
    assert np.all(np.isfinite(Xs))


# ── correlation ──────────────────────────────────────────────────────────────

def test_correlation_recovers_known_relationship():
    rng = np.random.default_rng(1)
    a = rng.normal(size=2000)
    b = 2.0 * a + rng.normal(scale=0.01, size=2000)  # near-perfect positive
    c = -a + rng.normal(scale=0.01, size=2000)       # near-perfect negative
    corr = mvc.correlation_matrix(np.column_stack([a, b, c]))
    assert corr.shape == (3, 3)
    assert corr[0, 1] > 0.99
    assert corr[0, 2] < -0.99


def test_correlation_requires_two_features():
    with pytest.raises(ValueError):
        mvc.correlation_matrix(np.arange(10.0).reshape(-1, 1))


# ── PCA loadings CSV companion ────────────────────────────────────────────────

def test_pca_loadings_csv_companion(tmp_path):
    """Export writes a feature×PC loadings table + explained-variance row next to
    the matrix, so PCA weightings are extractable for downstream analysis."""
    from types import SimpleNamespace
    import pandas as pd
    from review.features.multivariate.controller import _write_pca_loadings_csv

    rng = np.random.default_rng(7)
    X = rng.normal(size=(60, 3))
    data = {"X": X, "feature_labels": ["f1", "f2", "f3"]}
    matrix = tmp_path / "mat.csv"
    matrix.write_text("stub")

    out = _write_pca_loadings_csv(None, SimpleNamespace(standardize=True),
                                  data, str(matrix))
    assert out and out.endswith("mat_pca_loadings.csv")
    df = pd.read_csv(out, index_col=0)
    assert list(df.index)[:3] == ["f1", "f2", "f3"]
    assert "explained_variance_ratio" in df.index
    assert df.columns[0] == "PC1"
    # Each PC's loading vector is a unit-norm SVD right-singular direction.
    pc1 = df.loc[["f1", "f2", "f3"], "PC1"].to_numpy(dtype=float)
    assert np.isclose(np.linalg.norm(pc1), 1.0, atol=1e-6)


def test_cluster_feature_profiles_capture_cluster_identity():
    """Each cluster's z-scored feature means read as high/low vs the population —
    the interpretable 'what is this cluster' readout. Plant two blobs that differ
    on feature 0 and check the profile separates them on that feature."""
    rng = np.random.default_rng(11)
    low = rng.normal(loc=[-5, 0], scale=0.2, size=(80, 2))
    high = rng.normal(loc=[5, 0], scale=0.2, size=(80, 2))
    X = np.vstack([low, high])
    labels = np.array([0] * 80 + [1] * 80)
    ids, profile = mvc.cluster_feature_profiles(X, labels, n_clusters=2)
    assert ids == [0, 1]
    assert profile.shape == (2, 2)
    # Feature 0 separates the clusters (opposite signs); feature 1 is ~0 for both.
    assert profile[0, 0] < 0 < profile[1, 0]
    assert abs(profile[0, 1]) < 0.5 and abs(profile[1, 1]) < 0.5


def test_cluster_feature_profiles_empty_cluster_is_nan():
    X = np.random.default_rng(1).normal(size=(20, 3))
    labels = np.zeros(20, dtype=int)  # cluster 1 requested but never used
    _ids, profile = mvc.cluster_feature_profiles(X, labels, n_clusters=2)
    assert profile.shape == (2, 3)
    assert np.all(np.isnan(profile[1]))  # empty cluster row is NaN, not a crash


def test_subsample_for_projection_caps_slow_methods_only():
    """t-SNE / UMAP get capped (so the main-thread projection can't hang on a big
    selection) with the parallel per-cell arrays sliced to match; PCA and small
    selections pass through untouched."""
    from review.features.multivariate.controller import (
        _subsample_for_projection, _MAX_PROJECTION_CELLS,
    )
    n = _MAX_PROJECTION_CELLS + 1000
    X = np.random.default_rng(2).normal(size=(n, 4))
    data = {"X": X, "group": np.array(["g"] * n, dtype=object),
            "well": np.array(["A01"] * n, dtype=object),
            "timepoint": np.arange(n, dtype=float), "n_cells": n}

    Xp, dp, total = _subsample_for_projection(X, data, "tsne")
    assert Xp.shape[0] == _MAX_PROJECTION_CELLS == dp["group"].shape[0] \
        == dp["timepoint"].shape[0] == dp["n_cells"]
    assert total == n  # original count reported for the footnote

    # PCA is cheap → never capped; a small t-SNE selection also passes through.
    assert _subsample_for_projection(X, data, "pca")[2] == 0
    small = X[:50]
    assert _subsample_for_projection(small, {**data, "X": small,
                                             "group": data["group"][:50],
                                             "well": data["well"][:50],
                                             "timepoint": data["timepoint"][:50]},
                                     "tsne")[2] == 0


def test_pca_loadings_csv_skipped_when_matrix_too_small(tmp_path):
    from types import SimpleNamespace
    from review.features.multivariate.controller import _write_pca_loadings_csv

    data = {"X": np.zeros((5, 1)), "feature_labels": ["only"]}  # < 2 features
    out = _write_pca_loadings_csv(None, SimpleNamespace(standardize=True),
                                  data, str(tmp_path / "m.csv"))
    assert out is None


# ── PCA ──────────────────────────────────────────────────────────────────────

def test_pca_explained_variance_ratio_sums_to_one_when_full_rank():
    rng = np.random.default_rng(2)
    X = rng.normal(size=(300, 2))
    res = mvc.pca_svd(X, n_components=2)
    assert res.scores.shape == (300, 2)
    assert 0.99 <= float(res.explained_variance_ratio.sum()) <= 1.0 + 1e-9


def test_pca_separates_two_gaussian_blobs_on_pc1():
    rng = np.random.default_rng(3)
    blob_a = rng.normal(loc=[-8, 0, 0, 0], scale=0.3, size=(200, 4))
    blob_b = rng.normal(loc=[8, 0, 0, 0], scale=0.3, size=(200, 4))
    X = np.vstack([blob_a, blob_b])
    # Raw SVD (no standardization) — PC1 should capture the dominant-variance
    # signal axis and cleanly (linearly) separate the two blobs.
    res = mvc.pca_svd(X, n_components=2, standardize_data=False)
    pc1 = res.scores[:, 0]
    lo, hi = sorted([pc1[:200], pc1[200:]], key=np.mean)
    assert lo.max() < hi.min()             # whole of one blob one side of the other
    assert hi.min() - lo.max() > 2.0       # with a real gap
    assert res.explained_variance_ratio[0] > 0.9  # signal axis dominates variance


def test_project_2d_pca_labels_report_variance():
    rng = np.random.default_rng(4)
    coords, labels = mvc.project_2d(rng.normal(size=(100, 3)), method="pca")
    assert coords.shape == (100, 2)
    assert labels[0].startswith("PC1") and "%" in labels[0]


def test_project_2d_rejects_unknown_method():
    with pytest.raises(ValueError):
        mvc.project_2d(np.zeros((5, 3)), method="magic")


# ── clustering ───────────────────────────────────────────────────────────────

def _planted_blobs(seed=5):
    rng = np.random.default_rng(seed)
    centers = np.array([[0, 0], [10, 10], [0, 10]], dtype=float)
    parts = [rng.normal(loc=c, scale=0.4, size=(150, 2)) for c in centers]
    X = np.vstack(parts)
    truth = np.repeat([0, 1, 2], 150)
    return X, truth


def _agreement(labels, truth):
    """Best-match fraction of points whose predicted cluster maps to truth."""
    from itertools import permutations
    best = 0.0
    uniq = sorted(set(int(v) for v in labels))
    for perm in permutations(range(3)):
        mapping = {u: perm[i] for i, u in enumerate(uniq[:3])}
        mapped = np.array([mapping.get(int(v), -1) for v in labels])
        best = max(best, float(np.mean(mapped == truth)))
    return best


def test_kmeans_recovers_planted_clusters_and_is_deterministic():
    X, truth = _planted_blobs()
    r1 = mvc.kmeans_cluster(X, k=3)
    r2 = mvc.kmeans_cluster(X, k=3)
    assert np.array_equal(r1.labels, r2.labels)  # fixed seed → deterministic
    assert _agreement(r1.labels, truth) > 0.95


def test_hierarchical_recovers_planted_clusters():
    X, truth = _planted_blobs()
    res = mvc.hierarchical_cluster(X, k=3, method="ward")
    assert res.n_clusters == 3
    assert not res.subsampled
    assert _agreement(res.labels, truth) > 0.95


def test_hierarchical_subsamples_above_threshold():
    X, _ = _planted_blobs()  # 450 rows
    res = mvc.hierarchical_cluster(X, k=3, method="ward", max_samples=100)
    assert res.subsampled
    assert res.n_sampled == 100
    assert res.labels.shape[0] == X.shape[0]  # every cell still labelled


def test_kmeans_rejects_too_few_cells():
    with pytest.raises(ValueError):
        mvc.kmeans_cluster(np.zeros((2, 3)), k=5)


# ── cluster fractions ────────────────────────────────────────────────────────

def test_cluster_fractions_rows_sum_to_one():
    labels = np.array([0, 0, 1, 1, 1, 2])
    groups = np.array(["A", "A", "A", "B", "B", "B"])
    names, cids, frac = mvc.cluster_fractions(labels, groups, n_clusters=3)
    assert names == ["A", "B"]
    assert cids == [0, 1, 2]
    assert np.allclose(frac.sum(axis=1), 1.0)
    assert np.isclose(frac[0, 0], 2 / 3)   # group A: 2/3 in cluster 0
    assert np.isclose(frac[1, 2], 1 / 3)   # group B: 1/3 in cluster 2


def test_cluster_fractions_length_mismatch_raises():
    with pytest.raises(ValueError):
        mvc.cluster_fractions(np.array([0, 1]), ["A"])


# ── faceting ─────────────────────────────────────────────────────────────────

def test_pivot_facets_shape_and_values():
    value = np.array([1.0, 3.0, 5.0, 7.0])
    rows = ["drug", "drug", "ctrl", "ctrl"]
    cols = ["t0", "t1", "t0", "t1"]
    res = mvc.pivot_facets(value, rows, cols, agg="mean")
    assert res.row_labels == ["ctrl", "drug"]
    assert res.col_labels == ["t0", "t1"]
    # ctrl/t0 = 5, drug/t1 = 3
    assert np.isclose(res.values[0, 0], 5.0)
    assert np.isclose(res.values[1, 1], 3.0)


def test_pivot_facets_length_mismatch_raises():
    with pytest.raises(ValueError):
        mvc.pivot_facets(np.array([1.0, 2.0]), ["a"], ["x", "y"])


# ── optional-method gating ───────────────────────────────────────────────────

def test_umap_and_tsne_raise_method_unavailable_when_absent():
    import importlib.util
    if importlib.util.find_spec("umap") is None:
        with pytest.raises(mvc.MethodUnavailable) as ei:
            mvc.umap_project(np.zeros((10, 3)))
        assert ei.value.package == "umap-learn"
    if importlib.util.find_spec("sklearn") is None:
        with pytest.raises(mvc.MethodUnavailable) as ei:
            mvc.tsne_project(np.zeros((10, 3)))
        assert ei.value.package == "scikit-learn"
