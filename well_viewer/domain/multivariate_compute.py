"""Pure-math engine for the Multivariate (higher-dimensional analysis) feature.

Every numeric algorithm the Multivariate tab needs lives here, deliberately
**Qt-free and matplotlib-free** so the pure-Python pytest suite exercises it with
no GUI (mirrors :mod:`well_viewer.domain.stats_compute`). The feature's
``collect.py`` assembles the per-cell feature matrix (walking wells + gating);
this module turns that matrix into correlations, PCA scores, cluster labels,
cluster fractions, and faceted pivots. The feature's ``renderer.py`` draws the
results.

Dependency policy: PCA (numpy SVD), correlation, and k-means / hierarchical
clustering (``scipy.cluster``) require **no** dependency beyond the numpy+scipy
already pinned by the segmentation stack. UMAP and t-SNE need ``umap-learn`` /
``scikit-learn`` — those are imported *lazily* inside :func:`umap_project` /
:func:`tsne_project` and raise :class:`MethodUnavailable` when absent, so this
module imports cleanly even in an environment without them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Sequence

import numpy as np


class MethodUnavailable(RuntimeError):
    """Raised when an optional projection method's library is not installed.

    ``package`` is the pip name to install (``"scikit-learn"`` / ``"umap-learn"``)
    so the controller can surface a precise "install X to enable" message.
    """

    def __init__(self, package: str, method: str) -> None:
        self.package = package
        self.method = method
        super().__init__(
            f"{method} requires the '{package}' package, which is not installed."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Preprocessing
# ─────────────────────────────────────────────────────────────────────────────

def standardize(X: np.ndarray) -> np.ndarray:
    """Return *X* z-scored per column (zero mean, unit variance).

    Multivariate features span wildly different units — raw intensities in the
    thousands next to solidity in ``[0, 1]`` — so PCA and distance-based
    clustering are meaningless without standardizing first. Zero-variance columns
    (every cell identical) would divide by zero; those are centered only (their
    standardized values collapse to 0), never NaN. Input is expected finite
    (``collect`` drops non-finite rows); any residual NaN is treated as the
    column mean via :func:`numpy.nanmean` / :func:`numpy.nanstd`.
    """
    X = np.asarray(X, dtype=float)
    if X.ndim != 2:
        raise ValueError("standardize expects a 2-D (n_cells x n_features) array")
    mean = np.nanmean(X, axis=0)
    std = np.nanstd(X, axis=0)
    std = np.where(np.isfinite(std) & (std > 0), std, 1.0)
    out = (X - mean) / std
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Correlation
# ─────────────────────────────────────────────────────────────────────────────

def correlation_matrix(X: np.ndarray) -> np.ndarray:
    """Pearson correlation matrix across feature columns (``n_feat x n_feat``).

    Scale-invariant, so it does not need :func:`standardize`. Constant columns
    yield ``NaN`` correlations (undefined) — the renderer masks those cells.
    """
    X = np.asarray(X, dtype=float)
    if X.ndim != 2 or X.shape[1] < 2:
        raise ValueError("correlation_matrix needs at least two feature columns")
    if X.shape[0] < 2:
        raise ValueError("correlation_matrix needs at least two cells")
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.corrcoef(X, rowvar=False)
    return np.atleast_2d(corr)


# ─────────────────────────────────────────────────────────────────────────────
# Dimensionality reduction
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PCAResult:
    """PCA projection payload consumed by the renderer."""

    scores: np.ndarray                 # n_cells x n_components
    components: np.ndarray             # n_components x n_features (loadings)
    explained_variance_ratio: np.ndarray  # length n_components, in [0, 1]


def pca_svd(X: np.ndarray, n_components: int = 2,
            standardize_data: bool = True) -> PCAResult:
    """Principal-component projection of *X* via numpy SVD (no scikit-learn).

    Centering (and optional standardizing) then a thin SVD; scores are the
    projection onto the top ``n_components`` singular directions. Using SVD
    directly keeps us within the ``numpy<1.25`` pin the TF 2.13 stack imposes.
    """
    X = np.asarray(X, dtype=float)
    if X.ndim != 2:
        raise ValueError("pca_svd expects a 2-D array")
    n_samples, n_features = X.shape
    k = int(max(1, min(n_components, n_features, n_samples)))
    Xc = standardize(X) if standardize_data else (X - np.nanmean(X, axis=0))
    Xc = np.nan_to_num(Xc, nan=0.0, posinf=0.0, neginf=0.0)
    # full_matrices=False → economy SVD; U*S are the principal-component scores.
    U, S, Vt = np.linalg.svd(Xc, full_matrices=False)
    scores = U[:, :k] * S[:k]
    total = float(np.sum(S ** 2))
    if total > 0:
        evr = (S[:k] ** 2) / total
    else:
        evr = np.zeros(k, dtype=float)
    return PCAResult(scores=scores, components=Vt[:k], explained_variance_ratio=evr)


def umap_project(X: np.ndarray, n_components: int = 2,
                 standardize_data: bool = True, seed: int = 0) -> np.ndarray:
    """UMAP 2-D embedding. Raises :class:`MethodUnavailable` if umap-learn absent."""
    try:
        import umap  # availability probe + import
    except Exception as exc:  # ImportError, or a broken numba/llvm install
        raise MethodUnavailable("umap-learn", "UMAP") from exc
    X = np.asarray(X, dtype=float)
    data = standardize(X) if standardize_data else np.nan_to_num(X)
    n_neighbors = int(min(15, max(2, data.shape[0] - 1)))
    reducer = umap.UMAP(
        n_components=int(n_components), n_neighbors=n_neighbors, random_state=seed,
    )
    return np.asarray(reducer.fit_transform(data), dtype=float)


def tsne_project(X: np.ndarray, n_components: int = 2,
                 standardize_data: bool = True, seed: int = 0) -> np.ndarray:
    """t-SNE 2-D embedding. Raises :class:`MethodUnavailable` if scikit-learn absent."""
    try:
        from sklearn.manifold import TSNE
    except Exception as exc:
        raise MethodUnavailable("scikit-learn", "t-SNE") from exc
    X = np.asarray(X, dtype=float)
    data = standardize(X) if standardize_data else np.nan_to_num(X)
    # Perplexity must be < n_samples; clamp for small selections.
    perplexity = float(min(30.0, max(2.0, (data.shape[0] - 1) / 3.0)))
    tsne = TSNE(
        n_components=int(n_components), perplexity=perplexity,
        init="pca", random_state=seed,
    )
    return np.asarray(tsne.fit_transform(data), dtype=float)


def project_2d(X: np.ndarray, method: str = "pca",
               standardize_data: bool = True, seed: int = 0):
    """Dispatch a 2-D projection by name → ``(coords, axis_labels)``.

    ``coords`` is ``n_cells x 2``; ``axis_labels`` are the two axis captions
    (PCA reports explained-variance %). Unknown methods and missing optional
    libraries propagate as ``ValueError`` / :class:`MethodUnavailable`.
    """
    method = (method or "pca").lower()
    if method == "pca":
        res = pca_svd(X, n_components=2, standardize_data=standardize_data)
        evr = res.explained_variance_ratio
        labels = [
            f"PC{i + 1} ({(evr[i] * 100 if i < len(evr) else 0):.1f}%)"
            for i in range(2)
        ]
        return res.scores[:, :2], labels
    if method == "umap":
        coords = umap_project(X, standardize_data=standardize_data, seed=seed)
        return coords[:, :2], ["UMAP-1", "UMAP-2"]
    if method == "tsne":
        coords = tsne_project(X, standardize_data=standardize_data, seed=seed)
        return coords[:, :2], ["t-SNE-1", "t-SNE-2"]
    raise ValueError(f"unknown projection method: {method!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Clustering
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ClusterResult:
    """Cluster assignment payload."""

    labels: np.ndarray        # length n_cells, integer cluster id in [0, k)
    n_clusters: int
    n_sampled: int = 0        # cells used to fit (== n_cells unless subsampled)

    @property
    def subsampled(self) -> bool:
        return 0 < self.n_sampled < len(self.labels)


def kmeans_cluster(X: np.ndarray, k: int, standardize_data: bool = True,
                   seed: int = 0) -> ClusterResult:
    """k-means over the feature matrix via ``scipy.cluster.vq.kmeans2``.

    Deterministic (fixed ``seed``, ``minit="++"``). Standardizes first so no
    single high-magnitude feature dominates the Euclidean distance.
    """
    from scipy.cluster.vq import kmeans2

    X = np.asarray(X, dtype=float)
    k = int(k)
    if k < 1:
        raise ValueError("k must be >= 1")
    n = X.shape[0]
    if n < k:
        raise ValueError(f"need at least k={k} cells, got {n}")
    data = standardize(X) if standardize_data else np.nan_to_num(X)
    _centroids, labels = kmeans2(data, k, minit="++", seed=seed, missing="warn")
    return ClusterResult(labels=np.asarray(labels, dtype=int), n_clusters=k, n_sampled=n)


def hierarchical_cluster(X: np.ndarray, k: int, method: str = "ward",
                         standardize_data: bool = True, max_samples: int = 2000,
                         seed: int = 0) -> ClusterResult:
    """Agglomerative clustering via ``scipy.cluster.hierarchy``.

    ``linkage`` is O(n^2) memory, so for large cell counts we fit on a seeded
    random subsample of ``max_samples`` cells and assign the rest to the nearest
    subsample-cluster centroid. ``ClusterResult.subsampled`` flags that path so
    the renderer can annotate "clustered on N sampled cells".
    """
    from scipy.cluster.hierarchy import fcluster, linkage

    X = np.asarray(X, dtype=float)
    k = int(k)
    if k < 1:
        raise ValueError("k must be >= 1")
    n = X.shape[0]
    if n < k:
        raise ValueError(f"need at least k={k} cells, got {n}")
    data = standardize(X) if standardize_data else np.nan_to_num(X)

    if n <= max_samples:
        Z = linkage(data, method=method)
        labels = fcluster(Z, t=k, criterion="maxclust") - 1  # 1-based → 0-based
        return ClusterResult(labels=np.asarray(labels, dtype=int),
                             n_clusters=k, n_sampled=n)

    rng = np.random.default_rng(seed)
    sample_idx = rng.choice(n, size=max_samples, replace=False)
    sample = data[sample_idx]
    Z = linkage(sample, method=method)
    sample_labels = fcluster(Z, t=k, criterion="maxclust") - 1
    # Centroid per sampled cluster, then nearest-centroid assign for every cell.
    uniq = np.unique(sample_labels)
    centroids = np.vstack([sample[sample_labels == c].mean(axis=0) for c in uniq])
    # Euclidean distance to each centroid; argmin picks the cluster.
    dists = np.linalg.norm(data[:, None, :] - centroids[None, :, :], axis=2)
    labels = uniq[np.argmin(dists, axis=1)]
    return ClusterResult(labels=np.asarray(labels, dtype=int),
                         n_clusters=int(len(uniq)), n_sampled=int(max_samples))


def cluster_feature_profiles(X: np.ndarray, labels: np.ndarray,
                             n_clusters: int | None = None):
    """Mean of each feature within each cluster, on z-scored features.

    Because t-SNE / UMAP axes aren't interpretable (and even k-means clusters need
    characterising), the actionable readout is *what each cluster looks like*: the
    per-feature mean over its cells. Features are :func:`standardize`-d first so the
    profile reads as "high / low vs the population" on a common scale, independent
    of raw units. Returns ``(cluster_ids, profile)`` where ``profile`` is
    ``n_clusters x n_features`` (a row is ``NaN`` for an empty cluster).
    """
    X = standardize(np.asarray(X, dtype=float))
    labels = np.asarray(labels, dtype=int)
    if X.ndim != 2 or labels.shape[0] != X.shape[0]:
        raise ValueError("X and labels must share the number of cells")
    cluster_ids = (list(range(int(n_clusters))) if n_clusters is not None
                   else sorted(set(int(v) for v in labels)))
    rows = []
    for cid in cluster_ids:
        mask = labels == cid
        rows.append(X[mask].mean(axis=0) if mask.any()
                    else np.full(X.shape[1], np.nan))
    return cluster_ids, (np.vstack(rows) if rows else np.empty((0, X.shape[1])))


def cluster_fractions(labels: np.ndarray, groups: Sequence,
                      n_clusters: int | None = None):
    """Per-group fraction of cells in each cluster.

    Returns ``(group_names, cluster_ids, fractions)`` where ``fractions`` is a
    ``n_groups x n_clusters`` array whose rows sum to 1 (empty groups sum to 0).
    Drives the stacked-bar "phenotype composition per well/group" panel.
    """
    labels = np.asarray(labels, dtype=int)
    groups = np.asarray(list(groups), dtype=object)
    if labels.shape[0] != groups.shape[0]:
        raise ValueError("labels and groups must have the same length")
    cluster_ids = (list(range(int(n_clusters))) if n_clusters is not None
                   else sorted(set(int(v) for v in labels)))
    # Preserve first-appearance order of groups for stable stacked-bar columns.
    group_names: list = []
    seen = set()
    for g in groups:
        if g not in seen:
            seen.add(g)
            group_names.append(g)
    fractions = np.zeros((len(group_names), len(cluster_ids)), dtype=float)
    for gi, g in enumerate(group_names):
        gmask = groups == g
        total = int(np.count_nonzero(gmask))
        if total == 0:
            continue
        glabels = labels[gmask]
        for ci, cid in enumerate(cluster_ids):
            fractions[gi, ci] = np.count_nonzero(glabels == cid) / total
    return group_names, cluster_ids, fractions


# ─────────────────────────────────────────────────────────────────────────────
# Multi-factor faceting
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PivotResult:
    """A 2-D factor pivot for the faceting mode."""

    values: np.ndarray                 # n_rows x n_cols aggregate grid (NaN = empty)
    row_labels: list = field(default_factory=list)
    col_labels: list = field(default_factory=list)


def pivot_facets(value: np.ndarray, row_factor: Sequence, col_factor: Sequence,
                 agg: str = "mean") -> PivotResult:
    """Aggregate *value* into a row-factor x col-factor grid (pivot-table).

    Uses pandas ``pivot_table`` with ``aggfunc=agg`` (mean / median / count /
    std). Empty cells are ``NaN``. Row/column labels come out sorted for a
    deterministic grid.
    """
    import pandas as pd

    value = np.asarray(value, dtype=float)
    row_factor = list(row_factor)
    col_factor = list(col_factor)
    if not (len(value) == len(row_factor) == len(col_factor)):
        raise ValueError("value, row_factor and col_factor must be equal length")
    frame = pd.DataFrame({"row": row_factor, "col": col_factor, "val": value})
    table = frame.pivot_table(index="row", columns="col", values="val",
                              aggfunc=agg, dropna=False)
    table = table.sort_index(axis=0).sort_index(axis=1)
    return PivotResult(
        values=table.to_numpy(dtype=float),
        row_labels=[str(r) for r in table.index.tolist()],
        col_labels=[str(c) for c in table.columns.tolist()],
    )
