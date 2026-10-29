"""Registering the tiles of one (well, timepoint), and the pictures built from them.

Qt-free; numpy and scipy only. Units are micrometres unless a
name says ``_px``; image axes are x right, y down.

Every tile sees the same physical array, so a tile's position is known three
independent ways (tracker §3.2.2):

1. **recorded** by the stage (``tile_grid``): accurate to the stage's
   repeatability, and always available;
2. **lattice phase**: a tile's own lattice fit fixes where its sites sit
   *modulo one lattice vector*. The whole number of vectors is chosen from the
   overlap's own content when that is decisive (:func:`choose_step`), else from
   the offset the well's other timepoints agree on when the caller passes one,
   else from the recorded position, which is right only while the recorded
   error is under half a pitch. It uses every nanowell in the tile, not a strip
   of overlap;
3. **overlap correlation**: normalised correlation of the overlap strip between
   neighbours, using the cells and debris that break the lattice's periodicity.

**The periodicity trap.** An array is periodic, so correlating two tiles of it
has a peak at the true shift and at every lattice vector from it, and an
unconstrained search can lock onto the wrong one with high confidence. The
search here is confined to ``±pitch/2`` of the recorded offset by construction:
the search window is that wide, so the wrong peaks are not in it.

**The whole-vector choice.** Lattice phase and the confined search both
settle a pair's offset only up to a whole lattice vector, which the recorded
position then picks; a stage error past half a pitch picks the wrong one with
full confidence, and every nanowell of that tile is then named after its
neighbour. :func:`choose_step` settles the choice from the overlap instead:
each tile is reduced to its aperiodic part (the image minus itself shifted by
each nearest-neighbour lattice vector, band-passed, as the identity check does
for the central square), and every candidate offset a whole vector apart is
scored by the correlation of the two tiles' shared pixels. Cells, debris and the
array's rim decide it; an overlap of bare lattice cannot, and says so.

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

#: Where a pair's whole-vector choice came from (:func:`choose_step`): the overlap's own content, the pairs around it
#: that content decided (a loop of tiles must close), the offset the well's other timepoints agree on, or the recorded
#: position.
STEP_CONTENT = "content"
STEP_CHAINED = "chained"
STEP_TIMEPOINTS = "timepoints"
STEP_STAGE = "stage"
#: Candidate offsets are tried this far from the recorded one, in pitches: a stage error of up to two pitches between
#: neighbouring tiles is corrected; one beyond it is not looked for.
STEP_REACH_PITCHES = 2.0
#: The aperiodic correlation the best candidate must reach, and its lead over the runner-up, for the overlap's content
#: to decide the step. On rendered pairs a true offset with a few cells or the array's rim in the overlap scores
#: 0.4-0.9 and leads by 0.3 or more; an overlap of bare lattice scores under 0.1 at every candidate (tracker U9).
STEP_MIN_PEAK = 0.25
STEP_MIN_MARGIN = 0.12
#: The lead must also be this many times the spread of the losing candidates' scores (1.4826 x their median absolute
#: deviation): those scores are what the region gives where the tiles do not match, so their spread is the noise a
#: lead has to clear. A fixed lead is not enough: a region of a few square pitches holds a few dozen independent
#: samples once band-passed, and on a rendered well wrong candidates scored up to 0.34 there (tracker U9a).
STEP_MARGIN_SPREADS = 3.0
#: Losing candidates needed to measure that spread; with fewer, content decides nothing.
_STEP_MIN_LOSERS = 4
#: A candidate is scored only on a shared region at least this large, in square pitches, and half a pitch wide on
#: each axis. Two diagonal neighbours share a corner of about three square pitches at a 10 % overlap; on rendered
#: plates a corner that small led with a wrong candidate (0.25 against the true one's 0.07) where a shared edge of
#: twenty or more square pitches never did (tracker U9).
STEP_MIN_AREA_PITCH2 = 6.0
#: A candidate sharing less than :data:`STEP_MIN_AREA_PITCH2` cannot win, but one sharing at least this many square
#: pitches, a quarter pitch wide, is still scored and competes: the winner must lead it too. Two diagonal neighbours at
#: a 10 % overlap share a corner of about three square pitches, while the wrong candidate a step closer shares about
#: eight; scored alone, that candidate won on a rendered well with the right one never in the contest, and the tile
#: went three quarters of a pitch astray in the solve (tracker U9a).
STEP_COMPETE_AREA_PITCH2 = 1.5
#: The aperiodic images are block-averaged until a pitch spans about this many pixels.
_STEP_PITCH_PX = 16
#: The band the aperiodic images are correlated in, Gaussian widths in pitches (as the identity check's).
_STEP_BAND_PITCHES = (0.04, 0.25)


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
    #: Where the whole-vector choice came from: STEP_CONTENT, STEP_TIMEPOINTS or STEP_STAGE ("" for a source that
    #: makes no such choice).
    step_from: str = ""
    #: Whole lattice vectors (um) between the offset used and the one the recorded position alone would have picked:
    #: (0, 0) unless content or the other timepoints moved the choice.
    step_moved_um: tuple[float, float] = (0.0, 0.0)


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


def lattice_phase_constraints(tiles, *, images: dict | None = None, pixel_size_um: float | None = None,
                              preferred: dict | None = None) -> tuple[list[PairConstraint], list[str]]:
    """Pairwise offsets from the tiles' lattice fits, and why any pair was skipped.

    ``p_i + o_i`` is a site's position in the common frame, so for the same
    physical lattice ``o_j - o_i == p_i - p_j`` modulo a lattice vector (``p`` a
    site in tile-local coordinates, ``o`` the tile's origin). Which lattice vector
    is settled by :func:`choose_step`: from the two tiles' overlap when *images*
    (tile id to 2-D array, with *pixel_size_um*) are given and their content is
    decisive, else from *preferred* (``(i, j)`` to the offset the well's other
    timepoints agree on), else from the recorded offset. A recorded choice is
    reported as ambiguous when the recorded error it absorbs exceeds
    :data:`NEAR_AMBIGUITY_FRACTION` of a pitch.
    """
    by_id = {t.tile_id: t for t in tiles}
    skipped, pairs = [], []
    reduced = _AperiodicTiles(images, pixel_size_um, tiles) if images is not None and pixel_size_um else None
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
        sigma = math.sqrt(_SYSTEMATIC_SIGMA_UM ** 2 + sum(
            (max(f.rms_residual or 0.0, _MIN_PHASE_SIGMA_UM) ** 2) / max(sum(f.inlier_mask), 1)
            for f in (fi, fj)))
        choice = choose_step(i, j, phase, basis, recorded, reduced=reduced,
                             preferred=None if preferred is None else preferred.get((i, j)))
        pairs.append(_Pair(i, j, phase, basis, recorded, pitch, sigma, choice))
    _settle_by_loops(tiles, pairs)
    if _open_loops(tiles, pairs) and any(p.choice.source in (STEP_CONTENT, STEP_CHAINED) for p in pairs):
        # Content's choices contradict one another: one of them is wrong and the loops cannot say which. The choices
        # made without content (the well's other timepoints, else the stage) are used instead when they close.
        plain = [_Pair(p.i, p.j, p.phase, p.basis, p.recorded, p.pitch, p.sigma,
                       choose_step(p.i, p.j, p.phase, p.basis, p.recorded,
                                   preferred=None if preferred is None else preferred.get((p.i, p.j))))
                 for p in pairs]
        if not _open_loops(tiles, plain):
            for old, new in zip(pairs, plain):
                if old.choice.source in (STEP_CONTENT, STEP_CHAINED):
                    new.choice = StepChoice(new.choice.offset_um, new.choice.source, old.choice.peak,
                                            old.choice.margin,
                                            f"{new.i}-{new.j}: the overlaps' content placed the tiles in loops that "
                                            f"do not close, so this pair's step is taken from the {new.choice.source} "
                                            f"instead")
            pairs = plain
    for pair, miss in _open_loops(tiles, pairs):
        if not pair.choice.warning:
            pair.choice = StepChoice(pair.choice.offset_um, pair.choice.source, pair.choice.peak, pair.choice.margin,
                                     f"{pair.i}-{pair.j}: the tiles' loop does not close: the solve places this pair "
                                     f"{miss:.0f} um from its own lattice offset, so one pair in the loop is a whole "
                                     f"step wrong")
    constraints = []
    for pair in pairs:
        step, error, _ = _nearest_lattice_vector(pair.phase - pair.recorded, pair.basis)
        offset = pair.choice.offset_um
        moved = offset - (pair.phase - step)              # whole vectors from the recorded position's own pick
        warning = pair.choice.warning
        if pair.choice.source == STEP_STAGE and error > NEAR_AMBIGUITY_FRACTION * pair.pitch and not warning:
            warning = (f"{pair.i}-{pair.j}: the recorded positions are {error:.1f} um from the lattice phase, "
                       f"{error / pair.pitch * 100:.0f} % of a pitch; near 50 % the whole-vector choice "
                       f"starts to be wrong")
        constraints.append(PairConstraint(pair.i, pair.j, (float(offset[0]), float(offset[1])), pair.sigma, LATTICE,
                                          float(np.linalg.norm(offset - pair.recorded)), warning, pair.choice.source,
                                          (float(moved[0]), float(moved[1]))))
    return constraints, skipped


@dataclass
class _Pair:
    """One neighbouring pair's lattice-phase measurement while its whole-vector choice is being settled."""
    i: str
    j: str
    phase: np.ndarray
    basis: np.ndarray
    recorded: np.ndarray
    pitch: float
    sigma: float
    choice: "StepChoice"


def _settle_by_loops(tiles, pairs: list[_Pair]) -> None:
    """Re-choose each pair the content could not decide from the pairs it did, where the two are joined by them.

    Tiles form loops (a 2 x 2 grid's four edges and two diagonals), and every loop must close: once content has fixed
    F0-F1 and F0-F2, the offset F1-F2 is fixed too, whatever the stage recorded. Solving the content-decided pairs
    alone gives each tile they reach a position, and a pair joining two such tiles takes the candidate nearest the
    offset between them. Without this a diagonal pair (too small an overlap to decide) keeps the stage's choice, and a
    stage error past half a pitch is then averaged into the solve against the pairs content put right.
    """
    decided = [p for p in pairs if p.choice.source == STEP_CONTENT]
    if not decided:
        return
    component = _components([(p.i, p.j) for p in decided])
    provisional = solve_placements(tiles, [PairConstraint(p.i, p.j, tuple(p.choice.offset_um), p.sigma, LATTICE)
                                           for p in decided]).positions()
    for pair in pairs:
        if pair.choice.source == STEP_CONTENT or component.get(pair.i) is None \
                or component.get(pair.i) != component.get(pair.j):
            continue
        implied = np.subtract(provisional[pair.j], provisional[pair.i])
        step = _nearest_lattice_vector(pair.phase - implied, pair.basis)[0]
        offset = pair.phase - step
        warning = pair.choice.warning
        if pair.choice.source == STEP_TIMEPOINTS and np.linalg.norm(offset - pair.choice.offset_um) > pair.pitch / 2:
            warning = (f"{pair.i}-{pair.j}: the pairs around it place this pair "
                       f"{np.linalg.norm(offset - pair.choice.offset_um):.0f} um from where the well's other "
                       f"timepoints agree it sits; the loop's offset is used")
        pair.choice = StepChoice(offset, STEP_CHAINED, pair.choice.peak, pair.choice.margin, warning)


def _components(edges) -> dict[str, int]:
    """Each node of *edges* to the number of its connected component."""
    parent: dict[str, str] = {}

    def root(n):
        while parent.setdefault(n, n) != n:
            n = parent[n]
        return n
    for a, b in edges:
        parent[root(a)] = root(b)
    names = sorted({root(n) for n in list(parent)})
    return {n: names.index(root(n)) for n in list(parent)}


def _open_loops(tiles, pairs: list[_Pair]) -> list[tuple[_Pair, float]]:
    """Each pair whose chosen offset the tiles' loops do not close around, with how far the joint solve misses it.

    After every choice the pairs are solved together; a pair the solve misses by a quarter pitch or more sits in a
    loop whose whole-vector choices contradict one another (one of them is a step wrong). Which pair of the loop is
    wrong is not decidable from the loop alone.
    """
    if len(pairs) < 3:
        return []
    solved = solve_placements(tiles, [PairConstraint(p.i, p.j, tuple(p.choice.offset_um), p.sigma, LATTICE)
                                      for p in pairs]).positions()
    out = []
    for pair in pairs:
        miss = float(np.linalg.norm(np.subtract(solved[pair.j], solved[pair.i]) - pair.choice.offset_um))
        if miss >= 0.25 * pair.pitch:
            out.append((pair, miss))
    return out


# ── the whole-vector choice ──────────────────────────────────────────────────
def _neighbour_vectors(basis) -> list[np.ndarray]:
    """The nearest-neighbour lattice vectors of *basis* (columns a1, a2), both signs: six on a hexagonal lattice,
    four on a square one."""
    a1, a2 = basis[:, 0], basis[:, 1]
    found = [a1, a2, a1 + a2, a1 - a2]
    shortest = min(float(np.linalg.norm(v)) for v in found)
    out = []
    for v in found:
        if float(np.linalg.norm(v)) <= shortest * 1.1:
            out += [v, -v]
    return out


def aperiodic_tile(image, basis_um, pixel_size_um: float) -> tuple[np.ndarray, float]:
    """One tile reduced to what the lattice does not repeat, block-averaged so a pitch spans about
    :data:`_STEP_PITCH_PX` pixels; returns the image and its pixel size (um).

    Each pixel minus the mean of itself shifted by every nearest-neighbour lattice vector *whose source lies inside
    the tile*: a periodic lattice cancels under any one such shift, so near an edge the shifts that leave the tile are
    dropped instead of continuing the edge, which would leave the lattice standing in exactly the strip that overlaps
    the next tile. Band-passed by a difference of Gaussians (:data:`_STEP_BAND_PITCHES`) with normalised convolution.
    """
    from scipy import ndimage

    basis = np.asarray(basis_um, dtype=float)
    pitch_um = min(float(np.linalg.norm(basis[:, 0])), float(np.linalg.norm(basis[:, 1])))
    ds = max(1, int(pitch_um / pixel_size_um / _STEP_PITCH_PX))
    a = np.asarray(image, dtype=np.float64)
    a = a[:a.shape[0] // ds * ds, :a.shape[1] // ds * ds]
    a = a.reshape(a.shape[0] // ds, ds, a.shape[1] // ds, ds).mean(axis=(1, 3))
    um = pixel_size_um * ds
    total = np.zeros_like(a)
    count = np.zeros_like(a)
    for vx, vy in _neighbour_vectors(basis):
        shifted = ndimage.shift(a, (-vy / um, -vx / um), order=1, mode="constant", cval=np.nan)
        valid = np.isfinite(shifted)
        total[valid] += shifted[valid]
        count += valid
    residual = np.where(count > 0, a - total / np.maximum(count, 1), np.nan)
    known = np.isfinite(residual)
    filled = np.where(known, residual, 0.0)
    pitch_px = pitch_um / um

    def blur(width):
        weight = ndimage.gaussian_filter(known.astype(np.float64), width)
        return np.where(weight > 0.2, ndimage.gaussian_filter(filled, width) / np.maximum(weight, 1e-9), np.nan)
    fine, coarse = (max(0.5, f * pitch_px) for f in _STEP_BAND_PITCHES)
    return np.where(known, blur(fine) - blur(coarse), np.nan), um


class _AperiodicTiles:
    """Each tile's :func:`aperiodic_tile`, made once and only when asked for."""

    def __init__(self, images: dict, pixel_size_um: float, tiles):
        self.images, self.pixel_size_um = images, float(pixel_size_um)
        self.fits = {t.tile_id: t.fit for t in tiles}
        self._made: dict = {}

    def get(self, tile_id: str, basis) -> tuple[np.ndarray, float] | None:
        if tile_id not in self._made:
            image = self.images.get(tile_id)
            self._made[tile_id] = None if image is None or np.ndim(image) != 2 else \
                aperiodic_tile(image, basis, self.pixel_size_um)
        return self._made[tile_id]


def _shared_rect(shape_a, shape_b, dx: int, dy: int, within=None) -> tuple[int, int, int, int]:
    """``(x0, x1, y0, y1)`` in *a* of the pixels *a* and *b* share with *b*'s origin at ``(dx, dy)`` in *a*, cut to
    *within* (another such rectangle) when given; empty ranges when they share nothing."""
    x0, x1 = max(0, dx), min(shape_a[1], dx + shape_b[1])
    y0, y1 = max(0, dy), min(shape_a[0], dy + shape_b[0])
    if within is not None:
        x0, x1, y0, y1 = max(x0, within[0]), min(x1, within[1]), max(y0, within[2]), min(y1, within[3])
    return x0, x1, y0, y1


def _shared_correlation(a, b, dx: int, dy: int, min_side: int, min_area: int, within=None) -> float:
    """Pearson correlation of *a* and *b* over the pixels they share with *b*'s origin at ``(dx, dy)`` in *a*, only
    those inside *within* (``(x0, x1, y0, y1)`` in *a*) when given; NaN when that rectangle is narrower than *min_side*
    on either axis, holds fewer than *min_area* pixels known in both, or is flat."""
    x0, x1, y0, y1 = _shared_rect(a.shape, b.shape, dx, dy, within)
    if x1 - x0 < min_side or y1 - y0 < min_side or (x1 - x0) * (y1 - y0) < min_area:
        return math.nan
    fa = a[y0:y1, x0:x1]
    fb = b[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
    both = np.isfinite(fa) & np.isfinite(fb)
    if both.sum() < min_area:
        return math.nan
    u, v = fa[both] - fa[both].mean(), fb[both] - fb[both].mean()
    denominator = math.sqrt(float((u * u).sum()) * float((v * v).sum()))
    return float((u * v).sum()) / denominator if denominator > 0 else math.nan


@dataclass(frozen=True)
class StepChoice:
    """How one pair's whole-vector choice was made (:func:`choose_step`)."""
    offset_um: np.ndarray
    source: str                       # STEP_CONTENT, STEP_TIMEPOINTS or STEP_STAGE
    peak: float | None = None         # the best candidate's aperiodic correlation
    margin: float | None = None       # its lead over the runner-up
    warning: str = ""


def candidate_scores(i: str, j: str, phase, basis, recorded,
                     reduced: _AperiodicTiles) -> list[tuple[float, np.ndarray, bool]]:
    """``(score, offset, may_win)`` for every offset ``phase - L`` (*L* a lattice vector) within
    :data:`STEP_REACH_PITCHES` of *recorded*, best first, scored by the aperiodic correlation of the two tiles' shared
    pixels; the best of the 3 x 3 pixels around each candidate is kept, so a candidate is not marked down for
    rounding.

    Every candidate is judged on the same pixels of tile *i*: those the recorded offset says the two tiles share. A
    candidate scored over its own shared region was judged on more of tile *i* the closer it put the tiles, and on a
    rendered well a wrong candidate a step closer matched a cell outside the true overlap with one a lattice vector
    away (0.36 against the true offset's 0.08). On one common region, the content that decides is the content the
    stage says both tiles saw. The cost: a stage error large enough to leave that region thin decides nothing, and the
    pair falls to the next source. A candidate whose part of the region is at least :data:`STEP_MIN_AREA_PITCH2`
    (half a pitch wide) may win; one with at least :data:`STEP_COMPETE_AREA_PITCH2` (a quarter pitch wide) only
    competes; thinner ones are left out."""
    pitch = min(float(np.linalg.norm(basis[:, 0])), float(np.linalg.norm(basis[:, 1])))
    ri, rj = reduced.get(i, basis), reduced.get(j, basis)
    if ri is None or rj is None:
        return []
    (a, um), (b, _) = ri, rj
    min_side = max(4, int(round(0.5 * pitch / um)))
    min_area = int(round(STEP_MIN_AREA_PITCH2 * (pitch / um) ** 2))
    compete_side = max(2, int(round(0.25 * pitch / um)))
    compete_area = int(round(STEP_COMPETE_AREA_PITCH2 * (pitch / um) ** 2))
    region = _shared_rect(a.shape, b.shape, *(int(round(v)) for v in np.asarray(recorded) / um))
    inverse = np.linalg.inv(basis)
    centre = np.round(inverse @ (phase - recorded))
    span = int(math.ceil(2 * STEP_REACH_PITCHES)) + 1       # lattice coordinates wide enough for any basis angle
    out = []
    for dq in range(-span, span + 1):
        for dr in range(-span, span + 1):
            offset = phase - basis @ (centre + (dq, dr))
            if np.linalg.norm(offset - recorded) > STEP_REACH_PITCHES * pitch:
                continue
            ex, ey = (int(round(v)) for v in offset / um)
            scores = [_shared_correlation(a, b, ex + sx, ey + sy, compete_side, compete_area, region)
                      for sx in (-1, 0, 1) for sy in (-1, 0, 1)]
            scores = [v for v in scores if math.isfinite(v)]
            if scores:
                x0, x1, y0, y1 = _shared_rect(a.shape, b.shape, ex, ey, region)
                may_win = min(x1 - x0, y1 - y0) >= min_side and (x1 - x0) * (y1 - y0) >= min_area
                out.append((max(scores), offset, may_win))
    return sorted(out, key=lambda c: -c[0])


def choose_step(i: str, j: str, phase, basis, recorded, *, reduced: _AperiodicTiles | None = None,
                preferred=None) -> StepChoice:
    """The offset ``phase - L`` for one pair, *L* the whole lattice vector chosen, and where the choice came from.

    In order: the overlap's content when it is decisive (the best candidate shares enough to win, scores at least
    :data:`STEP_MIN_PEAK` and leads every other candidate scored by :data:`STEP_MIN_MARGIN` and by
    :data:`STEP_MARGIN_SPREADS` times the spread of their scores); else the candidate nearest *preferred*, the offset the well's
    other timepoints agree on; else the candidate nearest the recorded offset. A content choice that disagrees with
    *preferred* is used and warned about: it was measured on this timepoint's own pixels.
    """
    phase, recorded = np.asarray(phase, dtype=float), np.asarray(recorded, dtype=float)
    pitch = min(float(np.linalg.norm(basis[:, 0])), float(np.linalg.norm(basis[:, 1])))
    peak = margin = None
    if reduced is not None:
        scored = candidate_scores(i, j, phase, basis, recorded, reduced)
        if len(scored) >= 2:
            peak, margin = scored[0][0], scored[0][0] - scored[1][0]
            losers = np.asarray([c[0] for c in scored[1:]])
            spread = 1.4826 * float(np.median(np.abs(losers - np.median(losers)))) if len(losers) >= _STEP_MIN_LOSERS \
                else math.inf
            if peak >= STEP_MIN_PEAK and margin >= max(STEP_MIN_MARGIN, STEP_MARGIN_SPREADS * spread) and scored[0][2]:
                offset = scored[0][1]
                warning = ""
                if preferred is not None and np.linalg.norm(offset - np.asarray(preferred)) > pitch / 2:
                    warning = (f"{i}-{j}: the overlap's content places this pair "
                               f"{np.linalg.norm(offset - np.asarray(preferred)):.0f} um from where the well's other "
                               f"timepoints agree it sits; the content's offset is used")
                return StepChoice(offset, STEP_CONTENT, peak, margin, warning)
    if preferred is not None:
        step = _nearest_lattice_vector(phase - np.asarray(preferred, dtype=float), basis)[0]
        return StepChoice(phase - step, STEP_TIMEPOINTS, peak, margin)
    step = _nearest_lattice_vector(phase - recorded, basis)[0]
    return StepChoice(phase - step, STEP_STAGE, peak, margin)


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


def _window_scores(f, g, ex: int, ey: int, radius: int) -> tuple[np.ndarray | None, bool]:
    """Normalised cross-correlation of the overlap of *f* and *g* at every integer
    offset of *g*'s origin in ``[ex-radius, ex+radius] x [ey-radius, ey+radius]``.

    At each offset the score is the Pearson correlation of the pixels the two
    images share *at that offset*, so the overlap may shrink as the offset
    grows. A fixed template cut from inside the overlap cannot do this, and it
    matters: with the usual 10 % overlap the strip is narrower than one lattice
    pitch, and a template that must stay inside it at every shift of up to half
    a pitch would not fit at all. The sums over the shared pixels come from
    summed-area tables; only ``sum(f * g)`` needs an FFT. Offsets whose
    overlap is thinner than :data:`_MIN_TEMPLATE_PX` on either axis, and
    offsets whose shared pixels are flat, score NaN. Also returns whether any
    offset had enough overlap, so a caller can tell *too small* from *flat*.
    """
    from scipy.signal import fftconvolve

    hf, wf = f.shape
    hg, wg = g.shape
    fx0, fx1 = max(0, ex - radius), min(wf, ex + radius + wg)
    fy0, fy1 = max(0, ey - radius), min(hf, ey + radius + hg)
    gx0, gx1 = max(0, -(ex + radius)), min(wg, wf - (ex - radius))
    gy0, gy1 = max(0, -(ey + radius)), min(hg, hf - (ey - radius))
    if fx1 <= fx0 or fy1 <= fy0 or gx1 <= gx0 or gy1 <= gy0:
        return None, False
    fc = f[fy0:fy1, fx0:fx1].astype(np.float64)
    gc = g[gy0:gy1, gx0:gx1].astype(np.float64)
    hfc, wfc = fc.shape
    hgc, wgc = gc.shape
    cross = fftconvolve(fc, gc[::-1, ::-1], mode="full")
    tx = np.arange(ex - radius, ex + radius + 1) + gx0 - fx0       # offsets in crop coordinates
    ty = np.arange(ey - radius, ey + radius + 1) + gy0 - fy0
    fxa, fxb = np.clip(tx, 0, wfc), np.clip(tx + wgc, 0, wfc)
    fya, fyb = np.clip(ty, 0, hfc), np.clip(ty + hgc, 0, hfc)
    # An offset whose overlap is empty clips to an empty range in f, and its range in g then falls outside g; it is
    # masked below, but it must index inside g's table.
    gxa, gxb = np.clip(fxa - tx, 0, wgc), np.clip(fxb - tx, 0, wgc)
    gya, gyb = np.clip(fya - ty, 0, hgc), np.clip(fyb - ty, 0, hgc)
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
    return score, bool(ok.any())


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
    score, geometry_ok = _window_scores(a, b, ex, ey, r)
    if score is None or not geometry_ok:
        return OverlapShift(False, f"The overlap is too small to correlate within ±{search_radius} px",
                            search_radius_px=int(search_radius))
    if not np.isfinite(score).any():
        return OverlapShift(False, "The overlap holds no structure to match (flat or constant)",
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
                        , downsample: int = 1, expected: dict | None = None) -> tuple[list[PairConstraint], list[str]]:
    """Pairwise offsets from correlating each neighbouring pair's overlap.

    The search is centred on the recorded offset, or on *expected* (``(i, j)`` to an offset in um) where given: the
    lattice-phase offset, whose whole-vector choice may have moved the pair a step from where the stage put it.
    """
    by_id = {t.tile_id: t for t in tiles}
    radius = search_radius_px(pitch_um, pixel_size_um)
    constraints, skipped = [], []
    for i, j in neighbour_pairs(tiles):
        if i not in images or j not in images:
            continue
        recorded = np.subtract(by_id[j].recorded_left_top_um, by_id[i].recorded_left_top_um)
        if expected is not None and (i, j) in expected:
            recorded = np.asarray(expected[(i, j)], dtype=float)
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
                   pitch_um: float | None = None, preferred: dict | None = None) -> Registration:
    """All three sources, combined: lattice phase, overlap correlation, recorded.

    *images* (tile id to 2-D array) enables the overlap constraints and the content-decided whole-vector choice
    (:func:`choose_step`), and needs *pixel_size_um* and *pitch_um* for the search radius; without them only
    lattice phase and the recorded positions are used. *preferred* maps a pair ``(i, j)`` to the offset the well's
    other timepoints agree it has, which settles the whole-vector choice where the content cannot.
    """
    tiles = list(tiles)
    if images is not None and (pixel_size_um is None or pitch_um is None):
        raise ValueError("Overlap correlation needs pixel_size_um and pitch_um")
    constraints, skipped = lattice_phase_constraints(tiles, images=images, pixel_size_um=pixel_size_um,
                                                     preferred=preferred)
    if images is not None:
        expected = {(c.i, c.j): c.offset_um for c in constraints}
        more, also_skipped = overlap_constraints(tiles, images, pixel_size_um, pitch_um, expected=expected)
        constraints += more
        skipped += also_skipped
    return solve_placements(tiles, constraints, skipped=skipped)


# ── one well's timepoints agreeing on its tile layout ───────────────────────
#: A pair this far from the offset the other timepoints agree on, in pitches, is a whole-vector choice gone wrong:
#: the stage repeats its tile program to a few micrometres, and a lattice step is a whole pitch.
LAYOUT_DISAGREES_PITCHES = 0.5


def pair_offsets(positions: dict, pairs) -> dict:
    """``(i, j)`` to tile *j*'s registered position minus tile *i*'s, um, for each pair both tiles of which are placed."""
    return {(i, j): np.subtract(positions[j], positions[i]) for i, j in pairs if i in positions and j in positions}


def agreed_offsets(layouts: dict, pitch_um: float) -> dict:
    """The offset of each pair the well's timepoints agree on: ``(i, j)`` to ``(x, y)`` um.

    *layouts* maps a timepoint to its :func:`pair_offsets`. The stage runs the same tile program at every timepoint,
    so a pair's offset repeats to the stage's repeatability; a timepoint whose whole-vector choice went wrong is a
    pitch from the rest. A pair is agreed when more than half of the timepoints placing it, and at least two, lie
    within :data:`LAYOUT_DISAGREES_PITCHES` of their median; the agreed offset is those timepoints' mean. Two
    timepoints a step apart are each half a pitch from their median, so neither is near it: two cannot say which of
    them is wrong, and it takes a third. A pair split evenly, or agreed on by a plurality short of a majority, is left
    out.
    """
    by_pair: dict = {}
    for offsets in layouts.values():
        for pair, offset in offsets.items():
            by_pair.setdefault(pair, []).append(np.asarray(offset, dtype=float))
    out = {}
    for pair, values in by_pair.items():
        values = np.asarray(values)
        middle = np.median(values, axis=0)
        near = values[np.linalg.norm(values - middle, axis=1) < LAYOUT_DISAGREES_PITCHES * pitch_um]
        if len(near) >= 2 and 2 * len(near) > len(values):
            mean = near.mean(axis=0)
            out[pair] = (float(mean[0]), float(mean[1]))
    return out


def disagreeing_pairs(offsets: dict, agreed: dict, pitch_um: float) -> list:
    """The pairs of one timepoint's :func:`pair_offsets` that sit :data:`LAYOUT_DISAGREES_PITCHES` or more from the
    offset the well's timepoints agree on, as ``(pair, distance um)``."""
    out = []
    for pair, offset in sorted(offsets.items()):
        if pair in agreed:
            distance = float(np.linalg.norm(np.asarray(offset) - np.asarray(agreed[pair])))
            if distance >= LAYOUT_DISAGREES_PITCHES * pitch_um:
                out.append((pair, distance))
    return out


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
