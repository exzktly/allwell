"""Registering the tiles of one (well, timepoint), and the pictures built from them.

Qt-free; numpy and scipy only. Units are micrometres unless a
name says ``_px``; image axes are x right, y down.

Every tile sees the same physical array, so a tile's position is known three
independent ways (tracker §3.2.2):

1. **recorded** by the stage (``tile_grid``): accurate to the stage's
   repeatability, and always available;
2. **lattice phase**: a tile's own lattice fit fixes where its sites sit
   *modulo one lattice vector*; the recorded position supplies the whole
   number of vectors, so this works whenever the recorded error is under half a
   pitch, and it uses every nanowell in the tile, not a strip of overlap;
3. **overlap correlation**: normalised correlation of the overlap strip between
   neighbours, using the cells and debris that break the lattice's periodicity.

**The periodicity trap.** An array is periodic, so correlating two tiles of it
has a peak at the true shift and at every lattice vector from it, and an
unconstrained search can lock onto the wrong one with high confidence. The
search here is confined to ``±pitch/2`` of the recorded offset by construction:
the search window is that wide, so the wrong peaks are not in it.

The pairwise measurements are combined in one weighted least-squares solve
(:func:`solve_placements`). Each tile reports which source placed it and its
residual; a tile with no usable measurement (a corner tile over a circular
array) keeps its recorded position and says so. It never fails the solve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

from pipeline.nanowell_lattice import LatticeFit

LATTICE = "lattice"
OVERLAP = "overlap"
RECORDED = "recorded"

#: The recorded error between two tiles, as a fraction of the pitch, at which
#: the whole-number-of-vectors choice stops being safe. The hard limit is
#: half a pitch; this is reported before it is reached.
NEAR_AMBIGUITY_FRACTION = 0.4
#: Two tiles' lattice fits whose pitch or basis differ by more than this
#: fraction of a pitch are not one lattice (molding distortion, a bad fit):
#: no phase constraint is made between them.
BASIS_MISMATCH_FRACTION = 0.05
#: Floor on one tile's lattice-phase uncertainty (um); stops one exact fit dominating.
_MIN_PHASE_SIGMA_UM = 0.05
#: Allowance, in quadrature, for what the statistical sigma cannot see: a lattice that is not
#: perfectly uniform across the array (molding, optics). It is a stated allowance, not a
#: measurement: 0.25 um is a choice, to be replaced by one fitted on real data (tracker T10).
_SYSTEMATIC_SIGMA_UM = 0.25
#: The prior on a tile's recorded position (um). It fixes the solve's overall
#: translation and anchors a tile that has no measurement; it is far weaker
#: than any measurement.
PRIOR_SIGMA_UM = 50.0
#: Smallest overlap, in px per side, that a correlation is trusted on.
_MIN_TEMPLATE_PX = 24
#: A correlation peak below this normalised value is not a match.
MIN_PEAK = 0.4
#: The best peak must beat the best peak elsewhere by this factor.
MIN_PEAK_RATIO = 1.05


# ── pairwise measurements ────────────────────────────────────────────────────
@dataclass(frozen=True)
class PairConstraint:
    """Tile *j*'s origin minus tile *i*'s, measured: ``offset_um`` +- ``sigma_um``."""
    i: str
    j: str
    offset_um: tuple[float, float]
    sigma_um: float
    source: str                     # LATTICE or OVERLAP
    #: Distance of the measured offset from the recorded one, um. For a lattice
    #: constraint this is the recorded error the whole-number choice absorbed.
    correction_um: float = 0.0
    warning: str = ""


@dataclass(frozen=True)
class TileObservation:
    """What registration knows about one tile before the solve."""
    tile_id: str
    recorded_left_top_um: tuple[float, float]
    extent_um: tuple[float, float]
    fit: LatticeFit | None = None      # fitted to this tile's sites, in tile-local um


def neighbour_pairs(tiles) -> list[tuple[str, str]]:
    """Pairs of tiles whose recorded rectangles overlap in both axes."""
    tiles = sorted(tiles, key=lambda t: t.tile_id)
    pairs = []
    for a in range(len(tiles)):
        for b in range(a + 1, len(tiles)):
            ta, tb = tiles[a], tiles[b]
            ox = min(ta.recorded_left_top_um[0] + ta.extent_um[0], tb.recorded_left_top_um[0] + tb.extent_um[0]) \
                - max(ta.recorded_left_top_um[0], tb.recorded_left_top_um[0])
            oy = min(ta.recorded_left_top_um[1] + ta.extent_um[1], tb.recorded_left_top_um[1] + tb.extent_um[1]) \
                - max(ta.recorded_left_top_um[1], tb.recorded_left_top_um[1])
            if ox > 0 and oy > 0:
                pairs.append((ta.tile_id, tb.tile_id))
    return pairs


def _nearest_lattice_vector(vector, basis):
    """The lattice vector nearest *vector* by Euclidean distance, and the runner-up's distance.

    Rounding in lattice coordinates picks the nearest corner of a
    parallelogram, which is not always the nearest lattice point; checking the
    neighbouring integer pairs fixes that.
    """
    inverse = np.linalg.inv(basis)
    base = np.round(inverse @ vector)
    candidates = []
    for dq in (-1, 0, 1):
        for dr in (-1, 0, 1):
            lattice = basis @ (base + (dq, dr))
            candidates.append((float(np.linalg.norm(vector - lattice)), lattice))
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1], candidates[0][0], candidates[1][0]


def lattice_phase_constraints(tiles) -> tuple[list[PairConstraint], list[str]]:
    """Pairwise offsets from the tiles' lattice fits, and why any pair was skipped.

    ``p_i + o_i`` is a site's position in the common frame, so for the same
    physical lattice ``o_j - o_i == p_i - p_j`` modulo a lattice vector (``p`` a
    site in tile-local coordinates, ``o`` the tile's origin). The recorded
    offset picks which lattice vector; the choice is reported as ambiguous when
    the recorded error it absorbs exceeds :data:`NEAR_AMBIGUITY_FRACTION` of a
    pitch.
    """
    by_id = {t.tile_id: t for t in tiles}
    constraints, skipped = [], []
    for i, j in neighbour_pairs(tiles):
        ti, tj = by_id[i], by_id[j]
        fi, fj = ti.fit, tj.fit
        if fi is None or fj is None or not fi.ok or not fj.ok:
            skipped.append(f"{i}-{j}: no lattice fit on "
                           f"{', '.join(n for n, f in ((i, fi), (j, fj)) if f is None or not f.ok)}")
            continue
        bi = np.column_stack((fi.a1, fi.a2))
        bj = np.column_stack((fj.a1, fj.a2))
        pitch = (fi.pitch + fj.pitch) / 2
        if np.abs(bi - bj).max() > BASIS_MISMATCH_FRACTION * pitch:
            skipped.append(f"{i}-{j}: the two fits are not one lattice "
                           f"(basis differs by {np.abs(bi - bj).max():.2f} um)")
            continue
        basis = (bi + bj) / 2
        recorded = np.subtract(tj.recorded_left_top_um, ti.recorded_left_top_um)
        phase = np.subtract(fi.phase_origin, fj.phase_origin)
        step, error, runner_up = _nearest_lattice_vector(phase - recorded, basis)
        offset = recorded + (phase - recorded) - step           # == phase - step
        sigma = math.sqrt(_SYSTEMATIC_SIGMA_UM ** 2 + sum(
            (max(f.rms_residual or 0.0, _MIN_PHASE_SIGMA_UM) ** 2) / max(sum(f.inlier_mask), 1)
            for f in (fi, fj)))
        warning = ""
        if error > NEAR_AMBIGUITY_FRACTION * pitch:
            warning = (f"{i}-{j}: the recorded positions are {error:.1f} um from the lattice phase, "
                       f"{error / pitch * 100:.0f} % of a pitch; near 50 % the whole-vector choice "
                       f"starts to be wrong")
        constraints.append(PairConstraint(i, j, (float(offset[0]), float(offset[1])), sigma, LATTICE,
                                          float(error), warning))
    return constraints, skipped


# ── overlap correlation ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class OverlapShift:
    ok: bool
    reason: str
    #: Tile j's origin in tile i's pixel coordinates, refined: expected + correction.
    offset_px: tuple[float, float] | None = None
    correction_px: tuple[float, float] | None = None
    peak: float | None = None            # normalised correlation at the best shift
    ratio: float | None = None           # best peak over the best peak elsewhere
    search_radius_px: int | None = None


def search_radius_px(pitch_um: float, pixel_size_um: float) -> int:
    """The widest overlap search that stays strictly inside ±pitch/2."""
    return max(int(math.ceil(pitch_um / 2 / pixel_size_um)) - 1, 0)


def _rect_sum(table, y0, y1, x0, x1):
    """Sums over ``[y0:y1, x0:x1]`` from a summed-area table padded with a zero row/column."""
    return table[np.ix_(y1, x1)] - table[np.ix_(y0, x1)] - table[np.ix_(y1, x0)] + table[np.ix_(y0, x0)]


def _padded_cumsum(a):
    out = np.zeros((a.shape[0] + 1, a.shape[1] + 1))
    out[1:, 1:] = a.cumsum(axis=0).cumsum(axis=1)
    return out


def _window_scores(f, g, ex: int, ey: int, radius: int):
    """Normalised cross-correlation of the overlap of *f* and *g* at every integer
    offset of *g*'s origin in ``[ex-radius, ex+radius] x [ey-radius, ey+radius]``.

    At each offset the score is the Pearson correlation of the pixels the two
    images share *at that offset*, so the overlap may shrink as the offset
    grows. A fixed template cut from inside the overlap cannot do this, and it
    matters: with the usual 10 % overlap the strip is narrower than one lattice
    pitch, and a template that must stay inside it at every shift of up to half
    a pitch would not fit at all. The sums over the shared pixels come from
    summed-area tables; only ``sum(f * g)`` needs an FFT. Offsets whose
    overlap is thinner than :data:`_MIN_TEMPLATE_PX` on either axis score NaN.
    """
    from scipy.signal import fftconvolve

    hf, wf = f.shape
    hg, wg = g.shape
    fx0, fx1 = max(0, ex - radius), min(wf, ex + radius + wg)
    fy0, fy1 = max(0, ey - radius), min(hf, ey + radius + hg)
    gx0, gx1 = max(0, -(ex + radius)), min(wg, wf - (ex - radius))
    gy0, gy1 = max(0, -(ey + radius)), min(hg, hf - (ey - radius))
    if fx1 <= fx0 or fy1 <= fy0 or gx1 <= gx0 or gy1 <= gy0:
        return None
    fc = f[fy0:fy1, fx0:fx1].astype(np.float64)
    gc = g[gy0:gy1, gx0:gx1].astype(np.float64)
    hfc, wfc = fc.shape
    hgc, wgc = gc.shape
    cross = fftconvolve(fc, gc[::-1, ::-1], mode="full")
    tx = np.arange(ex - radius, ex + radius + 1) + gx0 - fx0       # offsets in crop coordinates
    ty = np.arange(ey - radius, ey + radius + 1) + gy0 - fy0
    fxa, fxb = np.clip(tx, 0, wfc), np.clip(tx + wgc, 0, wfc)
    fya, fyb = np.clip(ty, 0, hfc), np.clip(ty + hgc, 0, hfc)
    gxa, gxb = fxa - tx, fxb - tx
    gya, gyb = fya - ty, fyb - ty
    width, height = fxb - fxa, fyb - fya
    n = np.outer(np.maximum(height, 0), np.maximum(width, 0)).astype(np.float64)
    ok = np.outer(height >= _MIN_TEMPLATE_PX, width >= _MIN_TEMPLATE_PX)
    sf = _rect_sum(_padded_cumsum(fc), fya, fyb, fxa, fxb)
    sf2 = _rect_sum(_padded_cumsum(fc * fc), fya, fyb, fxa, fxb)
    sg = _rect_sum(_padded_cumsum(gc), gya, gyb, gxa, gxb)
    sg2 = _rect_sum(_padded_cumsum(gc * gc), gya, gyb, gxa, gxb)
    # An offset with no overlap indexes past the correlation; it is masked below, so clip it.
    sfg = cross[np.ix_(np.clip(ty + hgc - 1, 0, cross.shape[0] - 1), np.clip(tx + wgc - 1, 0, cross.shape[1] - 1))]
    with np.errstate(divide="ignore", invalid="ignore"):
        numerator = sfg - sf * sg / n
        var_f = np.maximum(sf2 - sf ** 2 / n, 0)
        var_g = np.maximum(sg2 - sg ** 2 / n, 0)
        score = numerator / np.sqrt(var_f * var_g)
    # A flat region has variance that is only rounding noise, and dividing by it scores
    # noise. Variance must be a real fraction of the squared mean level to count.
    level = 1e-6 * (sf2 / np.maximum(n, 1) + sg2 / np.maximum(n, 1)) + 1e-12
    informative = (var_f / np.maximum(n, 1) > level) & (var_g / np.maximum(n, 1) > level)
    score[~ok | ~informative] = np.nan
    return score


def correlate_overlap(image_i, image_j, expected_offset_px, *, search_radius: int,
                      downsample: int = 1) -> OverlapShift:
    """Refine tile *j*'s offset in tile *i* by correlating their overlap, within ``±search_radius`` px.

    *expected_offset_px* ``(ex, ey)`` is where tile j's origin is expected to
    lie in tile i's pixels (the recorded offset over the pixel size). Every
    integer offset within the radius is scored (:func:`_window_scores`) and no
    offset beyond it is ever tried: that confinement is the answer to the
    periodicity trap (tracker §3.2.3), and passing a radius of half a pitch or
    more reopens it. Callers use :func:`search_radius_px`.

    *downsample* block-averages both tiles by an integer factor first: the
    overlap here is the independent check, the lattice phase gives the fine
    position, and a full-size 2048 px tile costs seconds per pair otherwise.
    Offsets are returned in the tiles' own pixels either way.
    """
    a = np.asarray(image_i, dtype=np.float32)
    b = np.asarray(image_j, dtype=np.float32)
    if a.ndim != 2 or b.ndim != 2:
        return OverlapShift(False, "Tiles must be 2-D images")
    if not (np.isfinite(a).all() and np.isfinite(b).all()):
        return OverlapShift(False, "A tile holds non-finite pixels (NaN or inf)",
                            search_radius_px=int(search_radius))
    ds = max(int(downsample), 1)
    if ds > 1:
        a = a[:a.shape[0] // ds * ds, :a.shape[1] // ds * ds]
        a = a.reshape(a.shape[0] // ds, ds, a.shape[1] // ds, ds).mean(axis=(1, 3))
        b = b[:b.shape[0] // ds * ds, :b.shape[1] // ds * ds]
        b = b.reshape(b.shape[0] // ds, ds, b.shape[1] // ds, ds).mean(axis=(1, 3))
    ex, ey = (int(round(v / ds)) for v in expected_offset_px)
    # Rounding the expected offset to the coarse grid moves the window's centre by up to ds/2
    # tile pixels, so the radius shrinks by that much to keep every offset tried inside
    # +-search_radius of the recorded one (the confinement is the point of the window).
    r = max(int(math.floor((search_radius - ds / 2) / ds)), 0) if ds > 1 else int(search_radius)
    score = _window_scores(a, b, ex, ey, r)
    if score is None or not np.isfinite(score).any():
        return OverlapShift(False, f"The overlap is too small to correlate within ±{search_radius} px",
                            search_radius_px=int(search_radius))
    filled = np.where(np.isfinite(score), score, -np.inf)
    py, px = np.unravel_index(int(np.argmax(filled)), score.shape)
    peak = float(score[py, px])
    masked = filled.copy()
    masked[max(py - 2, 0):py + 3, max(px - 2, 0):px + 3] = -np.inf
    second = float(masked.max())
    ratio = peak / second if second > 0 else math.inf

    def refine(minus, centre, plus):
        denom = minus - 2 * centre + plus
        return 0.0 if not math.isfinite(denom) or denom >= 0 else 0.5 * (minus - plus) / denom
    sub_x = refine(score[py, px - 1], peak, score[py, px + 1]) if 0 < px < score.shape[1] - 1 else 0.0
    sub_y = refine(score[py - 1, px], peak, score[py + 1, px]) if 0 < py < score.shape[0] - 1 else 0.0
    offset = (ex * ds + (px - r + sub_x) * ds, ey * ds + (py - r + sub_y) * ds)
    correction = (offset[0] - expected_offset_px[0], offset[1] - expected_offset_px[1])
    kept = int(search_radius)
    if peak < MIN_PEAK:
        return OverlapShift(False, f"The best match is weak (correlation {peak:.2f})", offset, correction,
                            peak, ratio, kept)
    if ratio < MIN_PEAK_RATIO:
        return OverlapShift(False, f"The best match is ambiguous (peak {peak:.2f}, runner-up "
                                   f"{second:.2f})", offset, correction, peak, ratio, kept)
    return OverlapShift(True, "", offset, correction, peak, ratio, kept)


def overlap_constraints(tiles, images: dict, pixel_size_um: float, pitch_um: float
                        , downsample: int = 1) -> tuple[list[PairConstraint], list[str]]:
    """Pairwise offsets from correlating each neighbouring pair's overlap."""
    by_id = {t.tile_id: t for t in tiles}
    radius = search_radius_px(pitch_um, pixel_size_um)
    constraints, skipped = [], []
    for i, j in neighbour_pairs(tiles):
        if i not in images or j not in images:
            continue
        recorded = np.subtract(by_id[j].recorded_left_top_um, by_id[i].recorded_left_top_um)
        result = correlate_overlap(images[i], images[j], recorded / pixel_size_um, search_radius=radius,
                                     downsample=downsample)
        if not result.ok:
            skipped.append(f"{i}-{j}: {result.reason}")
            continue
        offset = np.asarray(result.offset_px) * pixel_size_um
        sigma = pixel_size_um * 0.5 / max(result.peak, 0.1)
        constraints.append(PairConstraint(i, j, (float(offset[0]), float(offset[1])), sigma, OVERLAP,
                                          float(np.linalg.norm(offset - recorded))))
    return constraints, skipped


# ── the solve ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TileSolution:
    tile_id: str
    left_um: float
    top_um: float
    source: str                     # LATTICE, OVERLAP or RECORDED
    residual_um: float | None       # rms disagreement with this tile's own constraints
    #: How far the solved position is from the recorded one, measured after removing
    #: the group's common shift (:attr:`Registration.common_shift_um`). The solve's frame
    #: is pinned by the mean of the recorded positions, so one badly recorded tile would
    #: otherwise show as a correction on every other tile.
    correction_um: float
    n_constraints: int
    note: str = ""


@dataclass(frozen=True)
class Registration:
    tiles: tuple[TileSolution, ...]
    constraints: tuple[PairConstraint, ...] = ()
    skipped: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    #: The median (x, y) displacement of the tiles that were measured, removed before
    #: ``correction_um`` is reported. Positions themselves stay in the solve's frame.
    common_shift_um: tuple[float, float] = (0.0, 0.0)

    def positions(self) -> dict[str, tuple[float, float]]:
        return {t.tile_id: (t.left_um, t.top_um) for t in self.tiles}

    def by_id(self) -> dict[str, TileSolution]:
        return {t.tile_id: t for t in self.tiles}


def solve_placements(tiles, constraints, *, prior_sigma_um: float = PRIOR_SIGMA_UM,
                     skipped=()) -> Registration:
    """Weighted least squares over every pairwise constraint plus a weak recorded prior.

    Minimises ``sum w (o_j - o_i - t_ij)^2 + w0 sum (o_i - recorded_i)^2`` with
    ``w = 1 / sigma^2``. The two axes share one matrix. A tile that no
    constraint touches is pulled only by its prior and keeps its recorded
    position, and is reported as ``recorded``.
    """
    tiles = sorted(tiles, key=lambda t: t.tile_id)
    index = {t.tile_id: n for n, t in enumerate(tiles)}
    n = len(tiles)
    matrix = np.zeros((n, n))
    rhs = np.zeros((n, 2))
    w0 = 1.0 / prior_sigma_um ** 2
    for t in tiles:
        k = index[t.tile_id]
        matrix[k, k] += w0
        rhs[k] += w0 * np.asarray(t.recorded_left_top_um)
    for c in constraints:
        i, j, w = index[c.i], index[c.j], 1.0 / c.sigma_um ** 2
        matrix[i, i] += w
        matrix[j, j] += w
        matrix[i, j] -= w
        matrix[j, i] -= w
        rhs[j] += w * np.asarray(c.offset_um)
        rhs[i] -= w * np.asarray(c.offset_um)
    solved = np.linalg.solve(matrix, rhs)
    touched = {c.i for c in constraints} | {c.j for c in constraints}
    moved = np.array([solved[index[t.tile_id]] - np.asarray(t.recorded_left_top_um)
                      for t in tiles if t.tile_id in touched])
    common = np.median(moved, axis=0) if len(moved) else np.zeros(2)
    solutions = []
    for t in tiles:
        k = index[t.tile_id]
        mine = [c for c in constraints if t.tile_id in (c.i, c.j)]
        if mine:
            residuals = [np.linalg.norm(solved[index[c.j]] - solved[index[c.i]] - np.asarray(c.offset_um))
                         for c in mine]
            residual = float(np.sqrt(np.mean(np.square(residuals))))
            source = LATTICE if any(c.source == LATTICE for c in mine) else OVERLAP
            note = ""
        else:
            residual, source = None, RECORDED
            note = "no lattice fit and no usable overlap: kept at the recorded position"
        shift = common if t.tile_id in touched else np.zeros(2)
        correction = float(np.linalg.norm(solved[k] - np.asarray(t.recorded_left_top_um) - shift))
        solutions.append(TileSolution(t.tile_id, float(solved[k, 0]), float(solved[k, 1]), source,
                                      residual, correction, len(mine), note))
    warnings = tuple(c.warning for c in constraints if c.warning) + _disagreements(constraints)
    return Registration(tuple(solutions), tuple(constraints), tuple(skipped), warnings,
                        (float(common[0]), float(common[1])))


def _disagreements(constraints) -> tuple[str, ...]:
    """Warnings for a pair measured by both lattice phase and overlap that disagree.

    The lattice constraint is far tighter, so it wins the solve and the overlap
    measurement never moves the tile; a disagreement is therefore invisible in the
    residual of a tile with a single constraint. It means one of the two sources is
    wrong about this pair (a bad fit, a wrong whole-vector choice, or an overlap
    dominated by something that moved), which is worth saying.
    """
    by_pair: dict[tuple[str, str], dict[str, PairConstraint]] = {}
    for c in constraints:
        by_pair.setdefault((c.i, c.j), {})[c.source] = c
    out = []
    for (i, j), sources in sorted(by_pair.items()):
        if LATTICE in sources and OVERLAP in sources:
            lattice, overlap = sources[LATTICE], sources[OVERLAP]
            gap = float(np.hypot(lattice.offset_um[0] - overlap.offset_um[0],
                                 lattice.offset_um[1] - overlap.offset_um[1]))
            if gap > 3 * overlap.sigma_um:
                out.append(f"{i}-{j}: lattice phase and overlap correlation disagree by {gap:.1f} um "
                           f"(overlap uncertainty {overlap.sigma_um:.1f} um); the lattice value is used")
    return tuple(out)


def register_tiles(tiles, *, images: dict | None = None, pixel_size_um: float | None = None,
                   pitch_um: float | None = None) -> Registration:
    """All three sources, combined: lattice phase, overlap correlation, recorded.

    *images* (tile id to 2-D array) enables the overlap constraints and needs
    *pixel_size_um* and *pitch_um* for the search radius; without them only
    lattice phase and the recorded positions are used.
    """
    tiles = list(tiles)
    constraints, skipped = lattice_phase_constraints(tiles)
    if images is not None:
        if pixel_size_um is None or pitch_um is None:
            raise ValueError("Overlap correlation needs pixel_size_um and pitch_um")
        more, also_skipped = overlap_constraints(tiles, images, pixel_size_um, pitch_um)
        constraints += more
        skipped += also_skipped
    return solve_placements(tiles, constraints, skipped=skipped)


# ── the montage (for looking, not measuring) ─────────────────────────────────
@dataclass(frozen=True)
class Montage:
    image: np.ndarray                       # uint8, 0 where nothing covers
    covered: np.ndarray                     # bool, same shape
    stretch: tuple[float, float]            # the (low, high) that 0..255 spans
    um_per_px: float                        # one montage pixel, um
    downsample: int                         # integer factor from the tiles' own pixels
    origin_um: tuple[float, float]          # common-frame position of montage pixel (0, 0)


def _feather(h: int, w: int) -> np.ndarray:
    """Weight that rises linearly from each edge, so overlaps blend without a seam."""
    y = np.minimum(np.arange(h), np.arange(h)[::-1]) + 1.0
    x = np.minimum(np.arange(w), np.arange(w)[::-1]) + 1.0
    return np.minimum.outer(y, x).astype(np.float32)


def build_montage(images: dict, positions_um: dict, pixel_size_um: float, *,
                  max_side_px: int = 4096, percentiles=(1.0, 99.5)) -> Montage:
    """A downsampled, blended, 8-bit phase montage; the stretch it applied is returned.

    The long side is at most *max_side_px*: tiles are block-averaged by one
    integer factor and placed at the nearest montage pixel, so a tile may sit up
    to half a montage pixel from its registered position. Overlaps are
    weighted by distance from each tile's edge. Analysis never reads this image
    (it reads the full-resolution owner tile); it is what a person looks at.
    """
    if not images:
        raise ValueError("No tiles to assemble")
    names = sorted(images)
    shapes = {n: images[n].shape for n in names}
    left = min(positions_um[n][0] for n in names)
    top = min(positions_um[n][1] for n in names)
    right = max(positions_um[n][0] + shapes[n][1] * pixel_size_um for n in names)
    bottom = max(positions_um[n][1] + shapes[n][0] * pixel_size_um for n in names)
    long_px = max(right - left, bottom - top) / pixel_size_um
    factor = max(1, int(math.ceil(long_px / max_side_px)))
    scale = pixel_size_um * factor
    placed = {}
    for n in names:
        image = np.asarray(images[n], dtype=np.float64)
        h, w = (image.shape[0] // factor) * factor, (image.shape[1] // factor) * factor
        small = image[:h, :w].reshape(h // factor, factor, w // factor, factor).mean(axis=(1, 3))
        placed[n] = (small, int(round((positions_um[n][1] - top) / scale)),
                     int(round((positions_um[n][0] - left) / scale)))
    height = max(y0 + small.shape[0] for small, y0, _ in placed.values())
    width = max(x0 + small.shape[1] for small, _, x0 in placed.values())
    total = np.zeros((height, width), dtype=np.float64)
    weight = np.zeros((height, width), dtype=np.float64)
    for small, y0, x0 in placed.values():
        feather = _feather(*small.shape)
        total[y0:y0 + small.shape[0], x0:x0 + small.shape[1]] += small * feather
        weight[y0:y0 + small.shape[0], x0:x0 + small.shape[1]] += feather
    covered = weight > 0
    blended = np.zeros_like(total)
    blended[covered] = total[covered] / weight[covered]
    low, high = (float(v) for v in np.percentile(blended[covered], percentiles))
    if high <= low:
        high = low + 1.0
    scaled = np.clip((blended - low) / (high - low), 0, 1) * 255
    scaled[~covered] = 0
    return Montage(np.rint(scaled).astype(np.uint8), covered, (low, high), scale, factor, (left, top))


# ── fused-site crops ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FusedCrop:
    crop: np.ndarray                        # float32; NaN where no tile covers
    origin_um: tuple[float, float]          # common-frame position of the crop's pixel (0, 0) centre
    attributed_tile: str | None             # the tile holding most of the disk
    disk_fraction: dict = field(default_factory=dict)   # tile id -> share of the disk it covers
    uncovered_fraction: float = 0.0         # share of the disk no tile covers
    tiles_used: tuple[str, ...] = ()
    fused: bool = True                      # the row's flag: measured from a blend, not one tile


def fused_crop(site_xy_um, diameter_um: float, tiles: dict, pixel_size_um: float, *,
               margin_um: float = 0.0) -> FusedCrop:
    """One crop for a site no single tile holds, blended from every tile that covers it.

    *tiles* maps a tile id to ``(image, left_um, top_um)`` at its registered
    position. The crop is square, ``diameter + 2 * margin`` on a side, sampled
    bilinearly at the crop's pixel centres from each covering tile and weighted
    by distance from that tile's edge. The site's cells belong to the tile
    holding most of its disk (a tie goes to the lexically first id), so a cell is counted once.
    """
    from scipy import ndimage

    half = diameter_um / 2 + margin_um
    n = int(math.ceil(2 * half / pixel_size_um))
    cx, cy = float(site_xy_um[0]), float(site_xy_um[1])
    xs = cx - half + (np.arange(n) + 0.5) * pixel_size_um
    ys = cy - half + (np.arange(n) + 0.5) * pixel_size_um
    gx, gy = np.meshgrid(xs, ys)
    disk = np.hypot(gx - cx, gy - cy) <= diameter_um / 2
    total = np.zeros((n, n))
    weight = np.zeros((n, n))
    counts = {}
    covered_any = np.zeros((n, n), dtype=bool)
    for name in sorted(tiles):
        image, left, top = tiles[name]
        image = np.asarray(image, dtype=np.float64)
        u = (gx - left) / pixel_size_um - 0.5
        v = (gy - top) / pixel_size_um - 0.5
        inside = (u >= 0) & (u <= image.shape[1] - 1) & (v >= 0) & (v <= image.shape[0] - 1)
        if not inside.any():
            continue
        sample = ndimage.map_coordinates(image, [v, u], order=1, mode="nearest")
        ramp = np.minimum.reduce([u + 1, image.shape[1] - u, v + 1, image.shape[0] - v])
        w = np.where(inside, np.maximum(ramp, 1e-3), 0.0)
        total += sample * w
        weight += w
        covered_any |= inside
        counts[name] = int((inside & disk).sum())
    crop = np.full((n, n), np.nan, dtype=np.float32)
    ok = weight > 0
    crop[ok] = (total[ok] / weight[ok]).astype(np.float32)
    disk_total = int(disk.sum())
    fractions = {name: c / disk_total for name, c in counts.items() if c > 0}
    attributed = None
    if fractions:
        best = max(counts.values())
        attributed = min(name for name, c in counts.items() if c == best)
    uncovered = float((disk & ~covered_any).sum()) / disk_total
    return FusedCrop(crop, (float(xs[0]), float(ys[0])), attributed, fractions, uncovered,
                     tuple(name for name, c in counts.items() if c > 0))
