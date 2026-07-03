"""Pure-math engine for the Spatial (per-cell physical-space) analysis feature.

Every numeric algorithm the Spatial tab needs lives here, deliberately Qt-free
and matplotlib-free so the pure-Python pytest suite exercises it with no GUI
(mirrors :mod:`well_viewer.domain.multivariate_compute`).

Scope
-----
Cells are 2-D points in pixel space (``shape_centroid_col``,
``shape_centroid_row`` — always emitted by the pipeline). Everything here
consumes an ``(N, 2)`` float array of centroids and returns plain numpy
arrays or the small dataclasses declared below. Colony detection is
parameter-driven (method + ``eps`` + ``min_samples``) so the same points can
be re-labelled cheaply on the Review side without touching the pipeline CSV.

Dependency policy
-----------------
numpy + scipy only. ``scipy.spatial`` (cKDTree, ConvexHull) and
``scipy.cluster.hierarchy`` are already pinned via multivariate/segmentation
transitively; no new third-party dep.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# Per-cell neighbour metrics
# ─────────────────────────────────────────────────────────────────────────────


def _as_points(points) -> np.ndarray:
    """Coerce *points* to an ``(N, 2)`` finite float array."""
    arr = np.asarray(points, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError("spatial_compute expects an (N, 2) array of centroids")
    return arr


def nearest_neighbour_distance(points: np.ndarray) -> np.ndarray:
    """Distance from every cell to its single nearest other cell.

    Returns an ``(N,)`` float array; ``NaN`` where a cell has no neighbour
    (N < 2). Uses ``scipy.spatial.cKDTree`` — O(N log N) for the whole array.
    """
    pts = _as_points(points)
    n = pts.shape[0]
    out = np.full(n, np.nan, dtype=float)
    if n < 2:
        return out
    from scipy.spatial import cKDTree
    tree = cKDTree(pts)
    # k=2 → returns [self (0.0), nearest other]. Slice column 1.
    dists, _ = tree.query(pts, k=2)
    out[:] = dists[:, 1]
    return out


def knn_mean_distance(points: np.ndarray, k: int = 5) -> np.ndarray:
    """Mean distance to the *k* nearest neighbours of each cell.

    Robust local-density surrogate (less noisy than NND). ``NaN`` when N ≤ k.
    """
    pts = _as_points(points)
    n = pts.shape[0]
    out = np.full(n, np.nan, dtype=float)
    if n <= k:
        return out
    from scipy.spatial import cKDTree
    tree = cKDTree(pts)
    dists, _ = tree.query(pts, k=k + 1)
    out[:] = dists[:, 1:].mean(axis=1)
    return out


def local_density(points: np.ndarray, k: int = 5) -> np.ndarray:
    """Local cell density estimate — ``k / (π · d_k²)`` — in cells per pixel².

    ``d_k`` is the distance to the k-th nearest neighbour. Matches the
    intuitive Ripley scaling and is robust to isolated outliers. ``NaN`` when
    N ≤ k.
    """
    pts = _as_points(points)
    n = pts.shape[0]
    out = np.full(n, np.nan, dtype=float)
    if n <= k:
        return out
    from scipy.spatial import cKDTree
    tree = cKDTree(pts)
    dists, _ = tree.query(pts, k=k + 1)
    dk = dists[:, k]
    with np.errstate(divide="ignore", invalid="ignore"):
        density = k / (np.pi * dk * dk)
    density[~np.isfinite(density)] = np.nan
    out[:] = density
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Ripley's L(r) − r
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RipleyResult:
    """Population summary of Ripley's L(r) − r."""

    r: np.ndarray                 # radii (pixels)
    L_minus_r: np.ndarray         # sample L(r) − r at each radius
    csr_lo: np.ndarray            # lower envelope (from N random draws)
    csr_hi: np.ndarray            # upper envelope
    n_points: int


def ripley_l(
    points: np.ndarray,
    *,
    r_max: float | None = None,
    n_r: int = 40,
    n_sims: int = 20,
    envelope: tuple[float, float] = (0.025, 0.975),
    seed: int = 0,
) -> RipleyResult:
    """Ripley's L(r) − r with a CSR envelope of *n_sims* random point patterns.

    Uses the axis-aligned bounding box of *points* as the study window (the
    ordinary edge correction is skipped — for a rough clustered-vs-dispersed
    read the envelope absorbs the bias). ``L(r) - r > 0`` reads as
    *clustered*, ``< 0`` as *dispersed*; the envelope shows the range
    consistent with complete spatial randomness at the same N.
    """
    pts = _as_points(points)
    n = pts.shape[0]
    if n < 2:
        r = np.linspace(0.0, 1.0 if r_max is None else float(r_max), max(2, n_r))
        zeros = np.zeros_like(r)
        return RipleyResult(r=r, L_minus_r=zeros, csr_lo=zeros, csr_hi=zeros,
                            n_points=n)

    xmin, ymin = pts.min(axis=0)
    xmax, ymax = pts.max(axis=0)
    width = max(xmax - xmin, 1e-9)
    height = max(ymax - ymin, 1e-9)
    area = width * height
    if r_max is None:
        r_max = 0.25 * min(width, height)
    r_max = max(float(r_max), 1e-9)
    r = np.linspace(r_max / n_r, r_max, n_r)

    def _l_minus_r(pattern: np.ndarray) -> np.ndarray:
        from scipy.spatial import cKDTree
        tree = cKDTree(pattern)
        counts = np.zeros_like(r, dtype=float)
        # ``count_neighbors`` returns pairs within r (self-pairs included);
        # subtract N per radius to remove them.
        pairs = tree.count_neighbors(tree, r=r)
        counts[:] = pairs.astype(float) - pattern.shape[0]
        with np.errstate(divide="ignore", invalid="ignore"):
            k_r = area * counts / (pattern.shape[0] * (pattern.shape[0] - 1))
            L_r = np.sqrt(k_r / np.pi)
        return L_r - r

    sample = _l_minus_r(pts)

    rng = np.random.default_rng(seed)
    sim_L = np.empty((n_sims, n_r), dtype=float)
    for i in range(n_sims):
        sim = rng.uniform(
            low=(xmin, ymin), high=(xmax, ymax), size=(n, 2))
        sim_L[i] = _l_minus_r(sim)
    lo_q, hi_q = envelope
    csr_lo = np.quantile(sim_L, lo_q, axis=0)
    csr_hi = np.quantile(sim_L, hi_q, axis=0)

    return RipleyResult(r=r, L_minus_r=sample, csr_lo=csr_lo, csr_hi=csr_hi,
                        n_points=n)


# ─────────────────────────────────────────────────────────────────────────────
# Colony detection
# ─────────────────────────────────────────────────────────────────────────────

METHOD_DBSCAN = "dbscan"
METHOD_SINGLE_LINKAGE = "single_linkage"


def _dbscan(points: np.ndarray, eps: float, min_samples: int) -> np.ndarray:
    """Minimal DBSCAN — labels in [0, n_clusters); -1 for noise. cKDTree-backed."""
    from scipy.spatial import cKDTree
    pts = _as_points(points)
    n = pts.shape[0]
    labels = np.full(n, -1, dtype=int)
    if n == 0:
        return labels
    tree = cKDTree(pts)
    neighbours = tree.query_ball_point(pts, r=eps)
    visited = np.zeros(n, dtype=bool)
    cluster_id = 0
    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True
        nbrs = list(neighbours[i])
        if len(nbrs) < min_samples:
            continue
        labels[i] = cluster_id
        j = 0
        while j < len(nbrs):
            q = nbrs[j]
            if not visited[q]:
                visited[q] = True
                q_nbrs = neighbours[q]
                if len(q_nbrs) >= min_samples:
                    seen = set(nbrs)
                    for x in q_nbrs:
                        if x not in seen:
                            nbrs.append(x)
                            seen.add(x)
            if labels[q] == -1:
                labels[q] = cluster_id
            j += 1
        cluster_id += 1
    return labels


def _single_linkage_cut(points: np.ndarray, eps: float) -> np.ndarray:
    """Single-linkage hierarchical clustering, cut at height ``eps``.

    Every point ends up in a cluster (no noise concept — a singleton is its
    own cluster).
    """
    pts = _as_points(points)
    n = pts.shape[0]
    if n == 0:
        return np.array([], dtype=int)
    if n == 1:
        return np.zeros(1, dtype=int)
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import pdist
    Z = linkage(pdist(pts), method="single")
    labels = fcluster(Z, t=max(float(eps), 1e-9), criterion="distance")
    return (labels.astype(int) - 1)  # 0-indexed


@dataclass
class ColonyLabels:
    """Result of a colony-detection pass."""

    labels: np.ndarray                     # (N,) int; -1 = noise (DBSCAN only)
    method: str
    eps: float
    min_samples: int
    n_colonies: int


def detect_colonies(
    points: np.ndarray,
    *,
    method: str = METHOD_DBSCAN,
    eps: float | None = None,
    eps_mult: float = 3.0,
    min_samples: int = 4,
) -> ColonyLabels:
    """Cluster centroids in physical space into colonies.

    ``eps`` defaults to ``eps_mult · median(NND)`` — the self-scaling ε that
    adapts to whatever density the dataset happens to have. ``min_samples``
    is ignored by ``METHOD_SINGLE_LINKAGE``.
    """
    pts = _as_points(points)
    n = pts.shape[0]
    if eps is None or eps <= 0:
        nnd = nearest_neighbour_distance(pts)
        finite = nnd[np.isfinite(nnd)]
        median = float(np.median(finite)) if finite.size else 1.0
        eps = float(eps_mult) * max(median, 1e-9)
    if n == 0:
        return ColonyLabels(labels=np.array([], dtype=int), method=method,
                            eps=float(eps), min_samples=int(min_samples),
                            n_colonies=0)
    if method == METHOD_SINGLE_LINKAGE:
        labels = _single_linkage_cut(pts, eps)
    else:
        labels = _dbscan(pts, eps=float(eps), min_samples=int(min_samples))
    unique = {int(v) for v in labels if int(v) >= 0}
    return ColonyLabels(labels=labels, method=method, eps=float(eps),
                        min_samples=int(min_samples), n_colonies=len(unique))


# ─────────────────────────────────────────────────────────────────────────────
# Colony aggregates
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ColonyStats:
    """Per-colony aggregates.

    Every array is parallel: element ``i`` describes colony ``ids[i]``.
    """

    ids: np.ndarray                # (K,) int
    size: np.ndarray               # (K,) int — cell count
    hull_area: np.ndarray          # (K,) float — pixels²; 0 when |colony| < 3
    packing_density: np.ndarray    # (K,) float — cells / hull_area (NaN for tiny)
    eccentricity: np.ndarray       # (K,) float — √(1 - λ_min/λ_max) of covariance
    orientation: np.ndarray        # (K,) float — angle of dominant eigenvector (rad)
    centroid: np.ndarray           # (K, 2) float — mean of member points


def _hull_area(points: np.ndarray) -> float:
    if points.shape[0] < 3:
        return 0.0
    try:
        from scipy.spatial import ConvexHull
    except ImportError:  # pragma: no cover
        return 0.0
    try:
        return float(ConvexHull(points).volume)  # ``volume`` = 2-D area
    except Exception:  # pragma: no cover — degenerate (collinear)
        return 0.0


def _cov_shape(points: np.ndarray) -> tuple[float, float]:
    """Return ``(eccentricity, orientation_rad)`` of the point covariance ellipse."""
    if points.shape[0] < 2:
        return 0.0, 0.0
    c = points - points.mean(axis=0)
    cov = np.cov(c, rowvar=False)
    if not np.all(np.isfinite(cov)):
        return 0.0, 0.0
    vals, vecs = np.linalg.eigh(cov)
    lam_min = float(vals[0])
    lam_max = float(vals[1])
    if lam_max <= 0:
        return 0.0, 0.0
    ecc = float(np.sqrt(max(0.0, 1.0 - lam_min / lam_max)))
    v = vecs[:, 1]
    return ecc, float(np.arctan2(v[1], v[0]))


def colony_stats(points: np.ndarray, labels: np.ndarray) -> ColonyStats:
    """Aggregate per-colony statistics from centroids + colony assignment."""
    pts = _as_points(points)
    labels = np.asarray(labels, dtype=int)
    ids = np.array(sorted({int(v) for v in labels if int(v) >= 0}), dtype=int)
    if ids.size == 0:
        return ColonyStats(
            ids=ids,
            size=np.array([], dtype=int),
            hull_area=np.array([], dtype=float),
            packing_density=np.array([], dtype=float),
            eccentricity=np.array([], dtype=float),
            orientation=np.array([], dtype=float),
            centroid=np.empty((0, 2), dtype=float),
        )
    size = np.zeros(ids.size, dtype=int)
    hull = np.zeros(ids.size, dtype=float)
    density = np.full(ids.size, np.nan, dtype=float)
    ecc = np.zeros(ids.size, dtype=float)
    orient = np.zeros(ids.size, dtype=float)
    centroid = np.zeros((ids.size, 2), dtype=float)
    for i, cid in enumerate(ids):
        mask = labels == int(cid)
        block = pts[mask]
        size[i] = int(mask.sum())
        centroid[i] = block.mean(axis=0)
        area = _hull_area(block)
        hull[i] = area
        if area > 0:
            density[i] = size[i] / area
        ecc[i], orient[i] = _cov_shape(block)
    return ColonyStats(ids=ids, size=size, hull_area=hull,
                       packing_density=density, eccentricity=ecc,
                       orientation=orient, centroid=centroid)


# ─────────────────────────────────────────────────────────────────────────────
# Intra-colony geometry
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class IntraColonyGeometry:
    """Per-cell intra-colony geometry.

    Every array is length N (parallel with the input *points*). Cells whose
    label is ``-1`` (noise) get ``NaN`` everywhere.
    """

    d_centroid: np.ndarray         # (N,) distance from cell to its colony centroid
    d_edge: np.ndarray             # (N,) distance to hull edge (positive = interior)
    r_over_R: np.ndarray           # (N,) normalized radial position [0, 1]
    is_boundary: np.ndarray        # (N,) bool — d_edge below boundary_pct percentile


def _point_to_segment_distance(px, py, ax, ay, bx, by) -> np.ndarray:
    """Distance from a batch of points to the segment (A, B). Vectorised."""
    apx, apy = px - ax, py - ay
    abx, aby = bx - ax, by - ay
    ab2 = abx * abx + aby * aby
    if ab2 <= 0:
        return np.hypot(apx, apy)
    t = np.clip((apx * abx + apy * aby) / ab2, 0.0, 1.0)
    dx = apx - t * abx
    dy = apy - t * aby
    return np.hypot(dx, dy)


def _distance_to_hull_edge(points: np.ndarray) -> np.ndarray:
    """Minimum distance from each point to the convex-hull boundary.

    Positive for interior points; 0 on the boundary. Points outside the hull
    would give a negative value in a signed formulation, but since the hull
    is built from the same points there are no external points here.
    """
    if points.shape[0] < 3:
        return np.zeros(points.shape[0], dtype=float)
    try:
        from scipy.spatial import ConvexHull
    except ImportError:  # pragma: no cover
        return np.zeros(points.shape[0], dtype=float)
    try:
        hull = ConvexHull(points)
    except Exception:  # pragma: no cover
        return np.zeros(points.shape[0], dtype=float)
    verts = points[hull.vertices]
    n_pts = points.shape[0]
    out = np.full(n_pts, np.inf, dtype=float)
    for a, b in zip(verts, np.roll(verts, -1, axis=0)):
        d = _point_to_segment_distance(
            points[:, 0], points[:, 1], a[0], a[1], b[0], b[1])
        out = np.minimum(out, d)
    return out


def intra_colony_geometry(
    points: np.ndarray,
    labels: np.ndarray,
    *,
    boundary_pct: float = 20.0,
) -> IntraColonyGeometry:
    """Per-cell distance-to-centroid / distance-to-edge / normalized radius.

    Boundary cells are the bottom ``boundary_pct``% of ``d_edge`` inside each
    colony (per-colony percentile, so an edge is edge regardless of colony
    size).
    """
    pts = _as_points(points)
    labels = np.asarray(labels, dtype=int)
    n = pts.shape[0]
    d_cent = np.full(n, np.nan, dtype=float)
    d_edge = np.full(n, np.nan, dtype=float)
    r_norm = np.full(n, np.nan, dtype=float)
    is_bnd = np.zeros(n, dtype=bool)
    if n == 0:
        return IntraColonyGeometry(d_centroid=d_cent, d_edge=d_edge,
                                    r_over_R=r_norm, is_boundary=is_bnd)
    for cid in {int(v) for v in labels if int(v) >= 0}:
        idx = np.flatnonzero(labels == cid)
        block = pts[idx]
        centre = block.mean(axis=0)
        dc = np.linalg.norm(block - centre, axis=1)
        d_cent[idx] = dc
        R = float(dc.max()) if dc.size else 0.0
        r_norm[idx] = (dc / R) if R > 0 else 0.0
        d_edge[idx] = _distance_to_hull_edge(block)
        thresh = float(np.percentile(d_edge[idx], boundary_pct)) \
            if idx.size else 0.0
        is_bnd[idx] = d_edge[idx] <= thresh
    return IntraColonyGeometry(d_centroid=d_cent, d_edge=d_edge,
                                r_over_R=r_norm, is_boundary=is_bnd)


# ─────────────────────────────────────────────────────────────────────────────
# Radial metric profile
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class VarianceDecomposition:
    """One-way ANOVA decomposition of a per-cell metric by colony label.

    ``sigma2_between`` is the variance among per-colony means (weighted by
    colony size), ``sigma2_within`` is the pooled within-colony variance,
    and ``sigma2_total`` = between + within by construction (population
    variance decomposition — no Bessel correction on the totals). ``icc`` is
    the intraclass correlation coefficient ``between / total`` — 1.0 means
    all variation is inter-colony (every colony perfectly homogeneous),
    0.0 means all variation is intra-colony (colony identity carries no
    information about the signal). Noise-labelled cells (``-1``) are dropped.
    """

    sigma2_between: float
    sigma2_within: float
    sigma2_total: float
    icc: float
    n_colonies: int
    n_cells: int


def variance_decomposition(
    metric: np.ndarray,
    labels: np.ndarray,
) -> VarianceDecomposition:
    """Split ``var(metric)`` into inter-colony and intra-colony components.

    ``labels`` is the per-cell colony assignment (``-1`` = noise). Cells with
    non-finite metric or noise labels are excluded. Colonies with a single
    cell contribute to the between-mean but not to the within-variance.
    """
    m = np.asarray(metric, dtype=float)
    l = np.asarray(labels, dtype=int)
    if m.shape != l.shape:
        raise ValueError("metric and labels must have the same shape")
    keep = np.isfinite(m) & (l >= 0)
    m = m[keep]
    l = l[keep]
    if m.size == 0:
        return VarianceDecomposition(0.0, 0.0, 0.0, 0.0, 0, 0)
    grand_mean = float(m.mean())
    ids = np.unique(l)
    ss_between = 0.0
    ss_within = 0.0
    for cid in ids:
        block = m[l == int(cid)]
        n = block.size
        colony_mean = float(block.mean())
        ss_between += n * (colony_mean - grand_mean) ** 2
        ss_within += float(((block - colony_mean) ** 2).sum())
    n_total = int(m.size)
    sigma2_between = ss_between / n_total
    sigma2_within = ss_within / n_total
    sigma2_total = float(m.var())  # population variance = between + within
    icc = 0.0 if sigma2_total <= 0 else (sigma2_between / sigma2_total)
    return VarianceDecomposition(
        sigma2_between=sigma2_between,
        sigma2_within=sigma2_within,
        sigma2_total=sigma2_total,
        icc=float(icc),
        n_colonies=int(ids.size),
        n_cells=n_total,
    )


@dataclass
class RadialProfile:
    """Metric aggregated by normalized radial position (r/R)."""

    bin_centres: np.ndarray        # (B,) mid-point of each r/R bin
    mean: np.ndarray               # (B,) metric mean per bin
    sem: np.ndarray                # (B,) standard error per bin
    n_per_bin: np.ndarray          # (B,) cell count per bin


def radial_profile(
    r_over_R: np.ndarray,
    metric: np.ndarray,
    *,
    n_bins: int = 10,
) -> RadialProfile:
    """Bin *metric* by *r_over_R* and return per-bin mean ± SEM.

    Bins span ``[0, 1]`` inclusive; NaN cells are dropped.
    """
    r = np.asarray(r_over_R, dtype=float)
    m = np.asarray(metric, dtype=float)
    if r.shape != m.shape:
        raise ValueError("r_over_R and metric must have the same shape")
    mask = np.isfinite(r) & np.isfinite(m)
    r = r[mask]
    m = m[mask]
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    centres = 0.5 * (edges[:-1] + edges[1:])
    means = np.full(n_bins, np.nan, dtype=float)
    sems = np.full(n_bins, np.nan, dtype=float)
    counts = np.zeros(n_bins, dtype=int)
    if r.size == 0:
        return RadialProfile(bin_centres=centres, mean=means, sem=sems,
                             n_per_bin=counts)
    which = np.clip(np.digitize(r, edges) - 1, 0, n_bins - 1)
    for b in range(n_bins):
        block = m[which == b]
        counts[b] = block.size
        if block.size:
            means[b] = float(np.mean(block))
            if block.size > 1:
                sems[b] = float(np.std(block, ddof=1) / np.sqrt(block.size))
            else:
                sems[b] = 0.0
    return RadialProfile(bin_centres=centres, mean=means, sem=sems,
                          n_per_bin=counts)


__all__ = [
    "METHOD_DBSCAN",
    "METHOD_SINGLE_LINKAGE",
    "RipleyResult",
    "ColonyLabels",
    "ColonyStats",
    "IntraColonyGeometry",
    "RadialProfile",
    "VarianceDecomposition",
    "nearest_neighbour_distance",
    "knn_mean_distance",
    "local_density",
    "ripley_l",
    "detect_colonies",
    "colony_stats",
    "intra_colony_geometry",
    "radial_profile",
    "variance_decomposition",
]
