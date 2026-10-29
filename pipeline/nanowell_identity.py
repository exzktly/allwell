"""Whether a nanowell's address names the same physical nanowell at every timepoint of its well.

Review joins a well's timepoints by address ``(q, r)``. Every check the run makes before writing those addresses —
the overlap of the detected address sets (``nanowell_frames.best_step``), the pitch, angle and roster consistency
(``nanowell_sites.check_consistency``, which folds angles modulo the lattice's symmetry) and the square's
orientation — is built from the same geometric evidence the addresses were made from, so they agree with one
another by construction. This module checks the result against evidence the addresses were not made from:

1. **The montages are registered on the central square.** The square is the array's one aperiodic landmark, so
   the reference timepoint's montage patch around it is correlated over the other timepoint's montage. A plain
   correlation would peak almost as high one lattice step away (a round nanowell shares the square's
   surroundings), so both are first replaced by their *lattice residual* (:func:`lattice_residual`): the image
   minus the mean of itself shifted by each nearest-neighbour lattice vector, which cancels everything periodic
   and leaves the square, the cells, debris and the rim. The rotation is the difference of the two timepoints'
   lattice angles folded to the smallest turn, the scale the ratio of their pitches.
2. **Every address is mapped through that registration and must land on itself**: within
   :data:`TOLERANCE_PITCHES` of the same address at the other timepoint, and nearer to it than to any other site.
   When it does not, the addresses the mapped sites do land on say what went wrong (:func:`diagnose`): one
   constant lattice step, a turn or reflection by one of the lattice's own symmetries, or failures confined to one
   tile.
3. **The tiles' layout is compared** (:func:`tile_layout_test`). A tile placed a lattice step off inside one
   timepoint's montage moves its sites' positions *and* their addresses together, so the address test cannot see
   it; but the stage visits the same fields at every timepoint, so each tile's registered position relative to the
   rest of the grid must not change between timepoints by anything near a pitch.

4. **The array's own outline is a second landmark** (``CENTRE_AGREEMENT_PITCHES``): the rim-fitted centre, mapped
   through the registration, must land near the other timepoint's, or a square-like decoy that moved could have
   been followed; a disagreement withholds the confirmation.

Each timepoint is compared with one **reference** — the earliest addressed timepoint whose square the run accepted
and carries ``(0, 0)``, unless the timepoints it contradicts agree among themselves and outnumber those it confirms
(:func:`check_well`) — so the verdicts compose.

**What it cannot see**: a *physical* half turn of the array. The hexagonal lattice and the square share the 180°
symmetry, so nothing in the images tells an array turned 180° from one that did not turn; a keyed plate cannot be
put back so, and only an array that turns inside its well could. The verdicts are :data:`CONFIRMED`, :data:`CONTRADICTED`, :data:`INCONCLUSIVE` (the evidence cannot
decide, with the reason), :data:`REFERENCE` and, for a timepoint never tested or a sidecar that no longer
describes the tables, :data:`NOT_CHECKED`. Only the first and the reference are good.

The check reads only what the run writes — the montage PNGs in ``<well>_out.zip``, ``nanowells/<well>_grid.json``
and ``nanowells/<well>.csv`` — so the run (``nanowell_task._finish_well``) and an audit of an existing dataset
(``python -m pipeline.nanowell_identity <output_dir>``) run the same code on the same inputs. It writes
``nanowells/<well>_identity.json`` with a digest of what it read; a sidecar whose digest no longer matches the
files is read as :data:`NOT_CHECKED` (:func:`read_sidecar`), never as its stale verdict.

:func:`stage_check` is the plate-level cross-check, sharing no evidence with the images: each well's address
origin in stage coordinates is fitted across the plate's wells by the plate's rigid motion, and a well off it by
more than the tolerance is flagged.

Qt-free; numpy, scipy and scikit-image.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger("allwell.nanowell")

NANOWELL_DIR = "nanowells"
SIDECAR_SUFFIX = "_identity.json"
#: The sidecar's format; :func:`read_sidecar` treats another as not checked.
IDENTITY_VERSION = 1

CONFIRMED = "confirmed"
CONTRADICTED = "contradicted"
INCONCLUSIVE = "inconclusive"
REFERENCE = "reference"
NOT_CHECKED = "not_checked"
#: The verdicts under which a timepoint's addresses may be joined with the reference's.
GOOD = (CONFIRMED, REFERENCE)
VERDICTS = (CONFIRMED, REFERENCE, INCONCLUSIVE, CONTRADICTED, NOT_CHECKED)

#: How far, in pitches, a mapped address may land from the same address at the other timepoint. A lattice step
#: error moves it a whole pitch; registration and site-fit noise on rendered plates are a few hundredths. Assumed,
#: not calibrated on a real plate (tracker Q1).
TOLERANCE_PITCHES = 0.25
#: How far, in pitches, the square is searched for around where it is expected in the other timepoint.
SEARCH_PITCHES = 4.0
#: Half the side of the patch correlated around the square, in pitches: the square and its first ring.
PATCH_PITCHES = 1.5
#: The correlation peak's least lead over the best correlation more than half a pitch from it. A synthetic choice
#: (tracker Q2): on rendered plates the square's lattice-residual peak leads by 0.4 and more.
MIN_MARGIN = 0.15
#: The least correlation at the peak for the square to count as found.
MIN_PEAK = 0.3
#: The fewest addresses two timepoints must share for the address test to decide anything.
MIN_COMPARED = 6
#: How far a correlation peak may sit from the square the run accepted at that timepoint, in pitches.
SQUARE_AGREEMENT_PITCHES = 0.25
#: How far, in pitches, the reference's rim-fitted array centre may land from the other timepoint's once mapped
#: through the registration. The square is the registration's only landmark; a square-like decoy the run accepted
#: at both timepoints that moved a site between them is followed by the correlation, and only the array's own
#: outline can say so. A fitted circle is loose by up to half a pitch on a real plate (ARCHITECTURE §6.2i), so
#: beyond this the timepoint is ``inconclusive``, not contradicted: a second landmark that cannot be trusted to a
#: pitch can withhold a confirmation but not accuse (tracker R7, Q4).
CENTRE_AGREEMENT_PITCHES = 0.75
#: Beyond this many pitches, two timepoints' tiles sit so differently against one another that they were not
#: acquired on one tile grid, and the layout test does not apply (the timepoint is then ``inconclusive``).
LAYOUT_REACH_PITCHES = 3.0
#: The fewest wells addressed at both timepoints for the stage cross-check to accuse one. With four, the rigid fit
#: absorbs a well a pitch off and spreads it over the rest (a 172 um error left residuals of 26 to 103 um on all
#: four), so a good well can read as the bad one.
STAGE_MIN_WELLS = 5
#: The side, in samples, the correlation surface is reduced to for the sidecar (the Identity view draws it).
SURFACE_SIDE = 48
#: How many failing addresses a sidecar lists per timepoint.
MAX_LISTED = 200

_MONTAGE_NAME = "{well}_{tp}_montage.png"


# ── the lattice ──────────────────────────────────────────────────────────────
def _screen_rotation(angle_deg: float) -> np.ndarray:
    """A counter-clockwise turn on screen (x right, y down) as a matrix acting on column vectors."""
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, s], [-s, c]])


def neighbour_vectors(pitch: float, angle_deg: float, kind: str) -> np.ndarray:
    """The nearest-neighbour lattice vectors (rows, x right and y down): six on a hexagonal lattice, four on a
    square one, the first at *angle_deg* counter-clockwise from east on screen (``LatticeFit.angle_deg``)."""
    step = 90.0 if kind == "square" else 60.0
    count = 4 if kind == "square" else 6
    out = []
    for k in range(count):
        a = math.radians(angle_deg + k * step)
        out.append((pitch * math.cos(a), -pitch * math.sin(a)))
    return np.asarray(out)


def fold_rotation(delta_deg: float, kind: str) -> float:
    """*delta_deg* folded into the lattice's half-period around zero: (-30, 30] hexagonal, (-45, 45] square."""
    period = 90.0 if kind == "square" else 60.0
    return float((delta_deg + period / 2) % period - period / 2) if math.isfinite(delta_deg) else 0.0


def point_group(kind: str) -> list[tuple[str, np.ndarray]]:
    """The lattice's own symmetries as integer matrices on addresses, each with a name; the identity first.

    On the hexagonal lattice the pipeline's ``r`` axis is 60° counter-clockwise from ``q`` (``pipeline_info.json``
    conventions), so a 60° turn takes ``(q, r)`` to ``(-r, q + r)`` and the reflection in the ``q`` axis takes it
    to ``(q + r, -r)``; on the square lattice a 90° turn is ``(-r, q)`` and the reflection ``(q, -r)``. Which way a
    turn reads depends on the sign convention, so a turn is named by its size."""
    if kind == "square":
        turn, flip, n, step = np.array([[0, -1], [1, 0]]), np.array([[1, 0], [0, -1]]), 4, 90
    else:
        turn, flip, n, step = np.array([[0, -1], [1, 1]]), np.array([[1, 1], [0, -1]]), 6, 60
    out, power = [], np.eye(2, dtype=int)
    for k in range(n):
        size = min(k * step, 360 - k * step)
        out.append(("identity" if k == 0 else f"a turn by {size}°", power.copy()))
        out.append((f"a reflection (with a turn by {size}°)" if k else "a reflection", power @ flip))
        power = turn @ power
    return out


# ── what the run wrote ───────────────────────────────────────────────────────
@dataclass
class Timepoint:
    """One timepoint of a well, as the check reads it."""
    name: str
    status: str
    #: The montage (uint8 as the run wrote it), or a function of no arguments that reads it: :meth:`pixels` reads it
    #: once and :meth:`release` drops it, so a well's check holds two montages at a time, not every timepoint's
    #: (a 4096-pixel montage is 16 MB; fifteen as float32 were a gigabyte per worker, tracker R8).
    image: object = None
    um_per_px: float | None = None
    origin_um: tuple[float, float] | None = None  # common-frame position of the montage's pixel (0, 0) corner
    pitch: float | None = None
    angle_deg: float | None = None
    kind: str = "hex"
    mirrored: bool = False
    square_um: tuple[float, float] | None = None  # the square the run accepted, common frame
    square_reason: str = ""
    centre_um: tuple[float, float] | None = None  # the array's fitted centre, common frame
    sites: dict = field(default_factory=dict)     # (q, r) -> (x, y) common frame
    owners: dict = field(default_factory=dict)    # (q, r) -> owner tile
    tiles: dict = field(default_factory=dict)     # tile -> (left, top) common frame

    def pixels(self) -> np.ndarray:
        if callable(self.image):
            self.image = np.asarray(self.image())
        return self.image

    def release(self) -> None:
        """Drop a montage that can be read again; one that was handed in as an array is kept."""
        if isinstance(self.image, np.ndarray) and getattr(self, "_loader", None) is not None:
            self.image = self._loader

    @property
    def lattice_ok(self) -> bool:
        return (self.image is not None and self.um_per_px and self.origin_um is not None and self.pitch
                and self.angle_deg is not None and math.isfinite(self.pitch) and self.pitch > 0)


def square_accepted(block: dict) -> tuple[tuple[float, float] | None, str]:
    """``(position, why not)``: the square the run accepted at a timepoint (common frame), from its grid block.

    Accepted means the run's own rule (``nanowell_lattice.fiducial_accepted``): the best squareness leads the
    runner-up by the block's ``margin`` and is at least ``ratio`` times it. A timepoint anchored on the square
    (``origin_method == "fiducial"``) is accepted whatever the numbers say, since the run used it."""
    fid = block.get("fiducial") if isinstance(block, dict) else None
    if not isinstance(fid, dict) or not fid.get("position_um"):
        return None, (fid or {}).get("reason") or "the run found no central square"
    position = tuple(float(v) for v in fid["position_um"])
    if block.get("origin_method") == "fiducial":
        return position, ""
    try:
        best, runner, margin = float(fid["best"]), float(fid["runner_up"]), float(fid["margin"])
        ratio = float(fid.get("ratio") or 1.0)
    except (KeyError, TypeError, ValueError):
        return None, "the run's square search carries no scores"
    if best - runner > margin and best >= ratio * runner:
        return position, ""
    return None, (f"the run did not accept a central square (squareness {best:.2f} against a runner-up of "
                  f"{runner:.2f})")


def _address(row) -> tuple[int, int] | None:
    try:
        return int(float(row["nw_q"])), int(float(row["nw_r"]))
    except (KeyError, TypeError, ValueError):
        return None


def timepoints_of(grid: dict, site_rows, montages: dict) -> dict[str, Timepoint]:
    """``timepoint -> Timepoint`` from a well's grid record, its site rows and ``timepoint -> montage`` (uint8)."""
    out = {}
    for name, block in (grid.get("timepoints") or {}).items():
        if not isinstance(block, dict):
            continue
        square, why = square_accepted(block)
        boundary = block.get("array_boundary") if isinstance(block.get("array_boundary"), dict) else None
        image = montages.get(name)
        t = Timepoint(name, str(block.get("status")), image,
                      _float(block.get("montage_um_per_px")),
                      _pair(block.get("montage_origin_um")), _float(block.get("pitch_um")),
                      _float(block.get("angle_deg")), "square" if block.get("lattice_kind") == "square" else "hex",
                      bool(block.get("mirrored")), square, why,
                      _pair((boundary or {}).get("centre_um")))
        t._loader = image if callable(image) else None
        for tile in block.get("tiles") or ():
            try:
                t.tiles[str(tile["tile"])] = (float(tile["left_um"]), float(tile["top_um"]))
            except (KeyError, TypeError, ValueError):
                continue
        out[name] = t
    for row in site_rows:
        t = out.get(str(row.get("timepoint")))
        a = _address(row)
        if t is None or a is None:
            continue
        try:
            t.sites[a] = (float(row["well_x_um"]), float(row["well_y_um"]))
        except (KeyError, TypeError, ValueError):
            continue
        t.owners[a] = str(row.get("owner") or "")
    return out


def _float(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _pair(value) -> tuple[float, float] | None:
    try:
        x, y = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    return (x, y) if math.isfinite(x) and math.isfinite(y) else None


def square_names_the_centre(t: Timepoint) -> bool:
    """Whether the site nearest the square the run accepted at *t* is address ``(0, 0)``, within the tolerance.

    The square is the central nanowell by convention (``pipeline_info.json``'s conventions, the rings Review
    counts), so a timepoint whose own square does not carry ``(0, 0)`` is addressed off its own landmark."""
    if t.square_um is None or (0, 0) not in t.sites or not t.pitch:
        return False
    names = list(t.sites)
    d = np.hypot(*(np.array([t.sites[a] for a in names]) - np.asarray(t.square_um)).T)
    k = int(np.argmin(d))
    return names[k] == (0, 0) and d[k] <= TOLERANCE_PITCHES * t.pitch


def reference_candidates(timepoints: dict[str, Timepoint], order) -> list[str]:
    """The timepoints that can be a reference, in the order they are preferred: addressed (status ``ok``), with a
    square the run accepted, a readable montage and lattice and sites — those whose square carries ``(0, 0)``
    first, each group in *order*."""
    usable = [n for n in order if (t := timepoints.get(n)) is not None and t.status == "ok"
              and t.square_um is not None and t.lattice_ok and t.sites] if True else []
    return sorted(usable, key=lambda n: not square_names_the_centre(timepoints[n]))


def choose_reference(timepoints: dict[str, Timepoint], order) -> tuple[str | None, str]:
    """``(reference, why none)``: the first of :func:`reference_candidates`."""
    candidates = reference_candidates(timepoints, order)
    if candidates:
        return candidates[0], ""
    return None, ("no addressed timepoint of this well has a central square the run accepted, so there is no "
                  "landmark to register the timepoints on")


# ── registering on the square ────────────────────────────────────────────────
def lattice_residual(image: np.ndarray, pitch_px: float, angle_deg: float, kind: str) -> np.ndarray:
    """*image* minus the mean of itself shifted by each nearest-neighbour lattice vector (pixels).

    A periodic lattice is the same after any of those shifts, so it cancels; the square, which differs from every
    one of its neighbours, does not, nor do cells, debris or the array's rim. Shifts are bilinear and the image's
    edge is continued, which leaves a border of about one pitch that the caller keeps clear of."""
    from scipy import ndimage

    image = np.asarray(image, dtype=np.float32)
    total = np.zeros_like(image)
    vectors = neighbour_vectors(pitch_px, angle_deg, kind)
    for vx, vy in vectors:
        total += ndimage.shift(image, (float(vy), float(vx)), order=1, mode="nearest")
    return image - total / len(vectors)


@dataclass
class SquareMatch:
    ok: bool
    reason: str = ""
    found_um: tuple[float, float] | None = None   # where the reference's square lands in the other's common frame
    peak: float | None = None
    runner_up: float | None = None
    margin: float | None = None
    rotation_deg: float = 0.0
    scale: float = 1.0
    surface: list | None = None                   # the correlation surface, reduced to SURFACE_SIDE samples a side
    surface_box_um: list | None = None            # [left, top, right, bottom] of the surface, common frame

    @property
    def as_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "found_um": _round(self.found_um), "peak": _r3(self.peak),
                "runner_up": _r3(self.runner_up), "margin": _r3(self.margin), "rotation_deg": round(self.rotation_deg, 4),
                "scale": round(self.scale, 6), "surface": self.surface, "surface_box_um": _round(self.surface_box_um)}


def _r3(value):
    return None if value is None else round(float(value), 4)


def _round(values):
    return None if values is None else [round(float(v), 3) for v in values]


def _crop(image: np.ndarray, centre_px, half: int) -> tuple[np.ndarray, tuple[int, int]]:
    """A square crop of *image* around *centre_px* (x, y) of half-side *half*, clipped to the image, and the
    crop's top-left pixel (x, y)."""
    h, w = image.shape
    x0, y0 = max(0, int(round(centre_px[0])) - half), max(0, int(round(centre_px[1])) - half)
    x1, y1 = min(w, int(round(centre_px[0])) + half + 1), min(h, int(round(centre_px[1])) + half + 1)
    return image[y0:y1, x0:x1], (x0, y0)


def _to_px(t: Timepoint, xy) -> np.ndarray:
    """Common-frame micrometres to the montage's continuous pixel index (x, y), pixel centres at integers."""
    return (np.asarray(xy, dtype=float) - np.asarray(t.origin_um)) / t.um_per_px - 0.5


def _to_um(t: Timepoint, px) -> np.ndarray:
    return (np.asarray(px, dtype=float) + 0.5) * t.um_per_px + np.asarray(t.origin_um)


def search_centres(ref: Timepoint, other: Timepoint) -> list[tuple[float, float]]:
    """Where the reference's square may be in *other*: the same common-frame point (both frames are the
    acquisition's tile grid, so a plate that did not move puts it there), the square the run accepted at *other*,
    and *other*'s fitted array centre."""
    centres = [ref.square_um]
    for c in (other.square_um, other.centre_um):
        if c is not None:
            centres.append(c)
    return centres


def register_on_square(ref: Timepoint, other: Timepoint) -> SquareMatch:
    """Correlate the reference's lattice-residual patch around its square over *other*'s lattice residual.

    The patch is turned by the folded difference of the two lattice angles and scaled by the ratio of pitches
    before it is correlated, so the result is a similarity transform taking the reference's square to
    ``found_um``. The search covers :data:`SEARCH_PITCHES` around every point :func:`search_centres` names."""
    from scipy import ndimage
    from skimage.feature import match_template

    if not (ref.lattice_ok and other.lattice_ok):
        return SquareMatch(False, "a montage or its lattice is missing")
    rotation = fold_rotation(other.angle_deg - ref.angle_deg, ref.kind)
    scale = other.pitch / ref.pitch
    pitch_o = other.pitch / other.um_per_px
    pitch_r = ref.pitch / ref.um_per_px
    half_o = max(4, int(round(PATCH_PITCHES * pitch_o)))
    # The reference's residual around its square, sampled on the other's pixel grid through the inverse transform.
    centre_r = _to_px(ref, ref.square_um)
    reach_r = int(math.ceil((PATCH_PITCHES * math.sqrt(2) + 1.5) * pitch_r)) + 2
    crop_r, (x0r, y0r) = _crop(ref.pixels(), centre_r, reach_r)
    if min(crop_r.shape) < 2 * reach_r - 2:
        return SquareMatch(False, "the reference's square is too near the edge of its montage to be read",
                           rotation_deg=rotation, scale=scale)
    resid_r = lattice_residual(crop_r, pitch_r, ref.angle_deg, ref.kind)
    grid = np.arange(-half_o, half_o + 1, dtype=float) * other.um_per_px          # other-frame offsets, um
    gx, gy = np.meshgrid(grid, grid)
    inverse = np.linalg.inv(scale * _screen_rotation(rotation))
    offsets = np.stack((gx.ravel(), gy.ravel()))
    back = inverse @ offsets / ref.um_per_px                                        # reference pixels
    coords = np.stack((back[1] + centre_r[1] - y0r, back[0] + centre_r[0] - x0r))
    template = ndimage.map_coordinates(resid_r, coords, order=1, mode="nearest").reshape(gx.shape)
    if float(template.std()) <= 1e-6:
        return SquareMatch(False, "the reference's square shows nothing aperiodic to correlate",
                           rotation_deg=rotation, scale=scale)
    # The other's residual over the search box.
    centres = np.array([_to_px(other, c) for c in search_centres(ref, other)])
    span = SEARCH_PITCHES * pitch_o + half_o + 1.5 * pitch_o + 2
    h, w = other.pixels().shape
    bx0, by0 = int(max(0, math.floor(centres[:, 0].min() - span))), int(max(0, math.floor(centres[:, 1].min() - span)))
    bx1 = int(min(w, math.ceil(centres[:, 0].max() + span) + 1))
    by1 = int(min(h, math.ceil(centres[:, 1].max() + span) + 1))
    box = other.pixels()[by0:by1, bx0:bx1]
    if min(box.shape) <= 2 * half_o + 1:
        return SquareMatch(False, "the other timepoint's montage does not reach where the square should be",
                           rotation_deg=rotation, scale=scale)
    resid_o = lattice_residual(box, pitch_o, other.angle_deg, other.kind)
    surface = match_template(resid_o, template.astype(np.float32))
    # Only positions whose template lies clear of the residual's continued border, and within the search radius.
    yy, xx = np.mgrid[0:surface.shape[0], 0:surface.shape[1]]
    cx, cy = xx + half_o + bx0, yy + half_o + by0
    inside = np.zeros(surface.shape, dtype=bool)
    for c in centres:
        inside |= np.hypot(cx - c[0], cy - c[1]) <= SEARCH_PITCHES * pitch_o
    # The residual continues the box's edge, so a template that reaches within a pitch of it reads artefact.
    border = int(math.ceil(pitch_o)) + 1
    bh, bw = box.shape
    inside &= ((xx >= border) & (yy >= border) & (xx + 2 * half_o < bw - border) & (yy + 2 * half_o < bh - border))
    valid = np.where(inside, surface, -np.inf)
    if not np.isfinite(valid).any():
        return SquareMatch(False, "no place in the search window can be correlated", rotation_deg=rotation,
                           scale=scale)
    iy, ix = np.unravel_index(int(np.argmax(valid)), valid.shape)
    peak = float(valid[iy, ix])
    dy, dx = _subpixel(valid, iy, ix)
    found = _to_um(other, (ix + dx + half_o + bx0, iy + dy + half_o + by0))
    away = np.hypot(xx - ix, yy - iy) > 0.5 * pitch_o
    rest = valid[away & np.isfinite(valid)]
    runner = float(rest.max()) if rest.size else float("nan")
    margin = peak - runner if math.isfinite(runner) else peak
    reduced, box_um = _reduce_surface(np.where(inside, surface, np.nan), other, bx0 + half_o, by0 + half_o)
    match = SquareMatch(True, "", (float(found[0]), float(found[1])), peak, runner if math.isfinite(runner) else None,
                        margin, rotation, scale, reduced, box_um)
    if peak < MIN_PEAK:
        match.ok, match.reason = False, (f"the reference's square correlates at most {peak:.2f} anywhere in the search "
                                         f"window, under the {MIN_PEAK:g} a match needs")
    elif margin < MIN_MARGIN:
        match.ok, match.reason = False, (f"the square's best match ({peak:.2f}) leads the next place more than half a "
                                         f"pitch away by only {margin:.2f}, under {MIN_MARGIN:g}: something else in the "
                                         "window looks as much like it")
    return match


def _subpixel(surface: np.ndarray, iy: int, ix: int) -> tuple[float, float]:
    """A parabola's vertex through the peak and its neighbours on each axis; 0 at an edge or a flat top."""
    def vertex(a, b, c):
        denom = a - 2 * b + c
        return 0.0 if not all(map(math.isfinite, (a, b, c))) or denom >= 0 else float(np.clip(0.5 * (a - c) / denom,
                                                                                              -0.5, 0.5))
    h, w = surface.shape
    dy = vertex(surface[iy - 1, ix], surface[iy, ix], surface[iy + 1, ix]) if 0 < iy < h - 1 else 0.0
    dx = vertex(surface[iy, ix - 1], surface[iy, ix], surface[iy, ix + 1]) if 0 < ix < w - 1 else 0.0
    return dy, dx


def _reduce_surface(surface: np.ndarray, t: Timepoint, x0: int, y0: int) -> tuple[list, list]:
    """The surface block-maximised to at most :data:`SURFACE_SIDE` a side, as rounded lists (NaN as ``None``), and
    the common-frame box ``[left, top, right, bottom]`` it covers."""
    h, w = surface.shape
    f = max(1, int(math.ceil(max(h, w) / SURFACE_SIDE)))
    hh, ww = int(math.ceil(h / f)) * f, int(math.ceil(w / f)) * f
    padded = np.full((hh, ww), np.nan)
    padded[:h, :w] = surface
    with np.errstate(all="ignore"):
        blocks = padded.reshape(hh // f, f, ww // f, f)
        finite = np.isfinite(blocks).any(axis=(1, 3))
        reduced = np.where(finite, np.nanmax(np.where(np.isfinite(blocks), blocks, -np.inf), axis=(1, 3)), np.nan)
    rows = [[None if not math.isfinite(v) else round(float(v), 3) for v in row] for row in reduced]
    left, top = _to_um(t, (x0 - 0.5, y0 - 0.5))
    right, bottom = _to_um(t, (x0 + ww - 0.5, y0 + hh - 0.5))
    return rows, [float(left), float(top), float(right), float(bottom)]


def mapper(ref: Timepoint, match: SquareMatch):
    """The similarity transform the match implies, as a function of common-frame points (rows) at the reference."""
    a = match.scale * _screen_rotation(match.rotation_deg)
    src, dst = np.asarray(ref.square_um, dtype=float), np.asarray(match.found_um, dtype=float)

    def apply(points):
        points = np.asarray(points, dtype=float).reshape(-1, 2)
        return (points - src) @ a.T + dst
    return apply


# ── the address test ─────────────────────────────────────────────────────────
@dataclass
class AddressTest:
    compared: int = 0
    failing: list = field(default_factory=list)   # [q, r, distance in pitches or None, landed q, landed r, tile]
    median_pitches: float | None = None
    max_pitches: float | None = None
    pairs: list = field(default_factory=list)     # (reference address, landed address) for every mapped site
    distances: dict = field(default_factory=dict)  # (q, r) -> distance in pitches, every compared address


def address_test(ref: Timepoint, other: Timepoint, transform) -> AddressTest:
    """Map every reference address and compare it with *other*'s sites.

    An address both timepoints hold fails when its mapped point lands more than :data:`TOLERANCE_PITCHES` from the
    same address at *other*, or nearer another address. An address only the reference holds fails when its mapped
    point lands on one of *other*'s sites (within the tolerance) carrying another address: that place has a name at
    *other*, and it is not this one."""
    from scipy.spatial import cKDTree

    out = AddressTest()
    if not ref.sites or not other.sites:
        return out
    tol = TOLERANCE_PITCHES * other.pitch
    names = list(ref.sites)
    mapped = transform(np.array([ref.sites[a] for a in names]))
    others = list(other.sites)
    tree = cKDTree(np.array([other.sites[b] for b in others]))
    near_d, near_i = tree.query(mapped)
    distances = []
    for a, point, nd, ni in zip(names, mapped, near_d, near_i):
        landed = others[int(ni)]
        if nd <= tol:
            out.pairs.append((a, landed))
        if a in other.sites:
            d = float(np.hypot(*(point - np.asarray(other.sites[a])))) / other.pitch
            distances.append(d)
            out.distances[a] = d
            out.compared += 1
            if d > TOLERANCE_PITCHES or landed != a:
                out.failing.append([a[0], a[1], round(d, 3), landed[0], landed[1], other.owners.get(a, "")])
        elif nd <= tol and landed != a:
            out.compared += 1
            out.failing.append([a[0], a[1], None, landed[0], landed[1], other.owners.get(landed, "")])
    if distances:
        out.median_pitches = round(float(np.median(distances)), 4)
        out.max_pitches = round(float(np.max(distances)), 4)
    return out


def diagnose(test: AddressTest, kind: str) -> dict:
    """What the addresses the mapped sites landed on say: ``{"kind": ..., "detail": ...}``.

    ``none`` when nothing failed; ``step`` when one constant lattice step explains most of them; ``symmetry`` when
    one of the lattice's own turns or reflections does (:func:`point_group`); ``tile`` when the failures lie in one
    tile; ``scattered`` otherwise, with the count."""
    if not test.failing:
        return {"kind": "none", "detail": ""}
    pairs = test.pairs
    best = None
    for name, m in point_group(kind):
        if not pairs:
            break
        shifts = Counter(tuple(int(v) for v in (np.asarray(b) - m @ np.asarray(a))) for a, b in pairs)
        c, n = shifts.most_common(1)[0]
        support = n / len(pairs)
        if best is None or support > best[0] + 1e-9:
            best = (support, name, c)
    if best is not None and best[0] >= 0.8:
        support, name, c = best
        if name == "identity" and c != (0, 0):
            return {"kind": "step", "detail": f"every address is one lattice step ({c[0]:+d}, {c[1]:+d}) off "
                                              f"({support:.0%} of the sites)", "step": list(c)}
        if name != "identity":
            return {"kind": "symmetry", "detail": f"the addresses are {name} of the reference's"
                                                  + (f" and a step ({c[0]:+d}, {c[1]:+d})" if c != (0, 0) else "")
                                                  + f" ({support:.0%} of the sites)", "symmetry": name,
                    "step": list(c)}
    tiles = Counter(f[5] for f in test.failing if f[5])
    if tiles:
        tile, n = tiles.most_common(1)[0]
        if n >= 0.8 * len(test.failing) and len(tiles) <= 2:
            return {"kind": "tile", "detail": f"{n} of the {len(test.failing)} failing addresses lie in tile {tile}",
                    "tile": tile}
    return {"kind": "scattered", "detail": f"{len(test.failing)} of {test.compared} addresses fail, in no one pattern"}


def tile_layout_test(ref: Timepoint, other: Timepoint) -> dict:
    """Whether each tile sits where it did relative to the rest of the grid: ``{"ok", "applies", "moved": [[tile,
    um]], "reason"}``.

    The stage visits the same fields at every timepoint, so the registered tile positions of two timepoints differ
    by one translation (the plate moves under the stage; the stage grid does not turn). The tiles are grouped by
    that difference: the largest group whose differences agree within :data:`TOLERANCE_PITCHES` is the grid's
    motion, and a tile departing from it by more was placed differently inside one of the two montages — a tile a
    lattice step off, whose sites' positions and addresses are wrong together, which the address test cannot see.
    A median would not do: with half the grid a step off (a row or a column of a 2 x 2) it falls between the two
    halves, and every tile departs by half a pitch (tracker R1). When the groups are as large as each other, which
    half is right cannot be told, and the layout still disagrees. A tile further than
    :data:`LAYOUT_REACH_PITCHES` from the grid's motion means the timepoints were not acquired on one tile grid:
    the test does not apply, and the caller cannot confirm."""
    common = sorted(set(ref.tiles) & set(other.tiles))
    if len(common) < 2:
        return {"ok": True, "applies": False, "moved": [], "reason": "fewer than two tiles in common"}
    deltas = np.array([np.subtract(other.tiles[t], ref.tiles[t]) for t in common])
    tol = TOLERANCE_PITCHES * other.pitch
    apart = np.hypot(*(deltas[:, None, :] - deltas[None, :, :]).transpose(2, 0, 1))
    support = (apart <= tol).sum(axis=1)
    best = int(np.argmax(support))
    centre = np.median(deltas[apart[best] <= tol], axis=0)
    off = np.hypot(*(deltas - centre).T)
    moved = [[t, round(float(d), 2)] for t, d in zip(common, off) if d > tol]
    if not moved:
        return {"ok": True, "applies": True, "moved": [], "reason": ""}
    if float(off.max()) > LAYOUT_REACH_PITCHES * other.pitch:
        return {"ok": True, "applies": False, "moved": moved,
                "reason": f"tiles sit up to {float(off.max()):.0f} um differently relative to one another, more than "
                          f"{LAYOUT_REACH_PITCHES:g} pitches, so the two timepoints were not acquired on one tile grid "
                          "and their layouts cannot be compared"}
    split = int(support[best]) * 2 <= len(common)
    return {"ok": False, "applies": True, "moved": moved, "reason": (
        "tile(s) " + ", ".join(f"{t} ({d:.0f} um, {d / other.pitch:.2f} pitch)" for t, d in moved)
        + " sit differently against the rest of the grid than at the reference: placed differently in one of the "
          "two montages" + ("; the tiles split into groups as large as each other, so which group is right cannot "
                            "be told from the layout" if split else ""))}


# ── one timepoint against the reference ──────────────────────────────────────
def compare(ref: Timepoint, other: Timepoint) -> dict:
    """The verdict on *other* against *ref*, with everything that decided it."""
    result = {"verdict": INCONCLUSIVE, "reason": "", "reference": ref.name}
    if other.status != "ok":
        result["reason"] = f"the timepoint is {other.status}: its addresses were not assigned by the run"
        return result
    if not other.lattice_ok:
        result["reason"] = "the timepoint has no montage or lattice to register"
        return result
    if other.kind != ref.kind:
        result.update(verdict=CONTRADICTED, reason=f"the timepoint's lattice is {other.kind}, the reference's "
                                                   f"{ref.kind}")
        return result
    match = register_on_square(ref, other)
    result["square"] = match.as_dict
    layout = tile_layout_test(ref, other)
    result["tiles"] = layout
    if other.mirrored != ref.mirrored:
        result.update(verdict=CONTRADICTED, reason="the timepoint's lattice is mirrored against the reference's")
        return result
    if not match.ok:
        result["reason"] = match.reason
        if not layout["ok"]:
            result.update(verdict=CONTRADICTED, reason=layout["reason"])
        return result
    transform = mapper(ref, match)
    test = address_test(ref, other, transform)
    diagnosis = diagnose(test, ref.kind)
    agreement = None
    if other.square_um is not None:
        agreement = float(np.hypot(*np.subtract(match.found_um, other.square_um))) / other.pitch
    result.update(compared=test.compared, median_pitches=test.median_pitches, max_pitches=test.max_pitches,
                  failing=test.failing[:MAX_LISTED], failing_count=len(test.failing), diagnosis=diagnosis,
                  square_agreement_pitches=None if agreement is None else round(agreement, 4),
                  distances=[[a[0], a[1], round(d, 4)] for a, d in sorted(test.distances.items())])
    centre = None
    if ref.centre_um is not None and other.centre_um is not None:
        centre = float(np.hypot(*(transform(ref.centre_um)[0] - np.asarray(other.centre_um)))) / other.pitch
        result["centre_agreement_pitches"] = round(centre, 4)
    problems = []
    if test.failing:
        problems.append(f"{len(test.failing)} of {test.compared} addresses do not land on themselves: "
                        f"{diagnosis['detail']}")
    if agreement is not None and agreement > SQUARE_AGREEMENT_PITCHES:
        problems.append(f"the square the run accepted at this timepoint is {agreement:.2f} pitch from where the "
                        "reference's square correlates: the run took something else for the square")
    if not layout["ok"]:
        problems.append(layout["reason"])
    if problems:
        result.update(verdict=CONTRADICTED, reason="; ".join(problems))
    elif test.compared < MIN_COMPARED:
        result["reason"] = (f"only {test.compared} address(es) in common with the reference, {MIN_COMPARED} needed")
    elif not layout["applies"] and len(set(ref.tiles) & set(other.tiles)) >= 2:
        result["reason"] = f"every address lands on itself, but {layout['reason']}, so a tile placed a step off " \
                           "could not be ruled out"
    elif centre is not None and centre > CENTRE_AGREEMENT_PITCHES:
        result["reason"] = (f"every address lands on itself, but the array's fitted centre lands {centre:.2f} pitch "
                            "from this timepoint's once registered on the square: the square may have moved against "
                            "the array (a decoy), or a fitted rim is loose")
    else:
        result.update(verdict=CONFIRMED, reason=f"{test.compared} addresses land on themselves, at most "
                                                f"{test.max_pitches:.2f} pitch off")
    return result


def _against(timepoints: dict, reference: str, order) -> dict:
    """Every timepoint of *order* compared with *reference*; each other montage is dropped once compared."""
    out = {}
    for name in order:
        t = timepoints.get(name)
        if t is None:
            continue
        if name == reference:
            out[name] = {"verdict": REFERENCE, "reason": "the reference timepoint", "square_um": _round(t.square_um)}
            continue
        try:
            out[name] = compare(timepoints[reference], t)
        except (ValueError, np.linalg.LinAlgError, MemoryError) as exc:
            log.warning("Identity check of timepoint %s failed: %s", name, exc, exc_info=True)
            out[name] = {"verdict": INCONCLUSIVE, "reason": f"the check failed: {exc}"}
        t.release()
    return out


def _signature(entry: dict) -> tuple | None:
    """What a contradicted timepoint's addresses are against the reference (a step, a symmetry), or ``None``."""
    diagnosis = entry.get("diagnosis") or {}
    if entry.get("verdict") != CONTRADICTED or diagnosis.get("kind") not in ("step", "symmetry"):
        return None
    return diagnosis["kind"], tuple(diagnosis.get("step") or ()), diagnosis.get("symmetry")


def _good(results: dict) -> int:
    return sum(v.get("verdict") in GOOD for v in results.values())


def check_well(grid: dict, site_rows, montages: dict, order=None) -> dict:
    """Every timepoint of one well against its reference: the sidecar's ``timepoints`` and ``reference``.

    The reference is the first of :func:`reference_candidates`. A reference addressed off itself would make every
    correct timepoint ``contradicted`` and keep the wrong one (tracker R4), so when the contradicted timepoints
    agree on what they are against it (one step, one symmetry) and are at least as many as those it confirms, the
    first of them that can be a reference is tried as well, and the reference that more timepoints agree with is
    kept. Alike, the one whose square carries ``(0, 0)`` is kept when only one does; otherwise which is right cannot
    be told, and every timepoint of both is ``contradicted`` with that reason: the two are known to disagree, so
    neither may be joined to the other, as the run leaves both frames of a tie unaddressed rather than guess
    (``nanowell_run._settle_steps``)."""
    timepoints = timepoints_of(grid, site_rows, montages)
    order = list(order) if order is not None else list(timepoints)
    candidates = reference_candidates(timepoints, order)
    if not candidates:
        _, why = choose_reference(timepoints, order)
        return {"reference": None, "reference_reason": why,
                "timepoints": {n: {"verdict": INCONCLUSIVE, "reason": why} for n in order if n in timepoints}}
    reference = candidates[0]
    results = _against(timepoints, reference, order)
    timepoints[reference].release()
    out = {"reference": reference, "reference_reason": "", "timepoints": results}
    signatures = Counter(sig for v in results.values() if (sig := _signature(v)) is not None)
    if not signatures:
        return out
    signature, count = signatures.most_common(1)[0]
    group = [n for n in candidates if n != reference and _signature(results.get(n, {})) == signature]
    if not group or count < _good(results) - 1:
        return out
    other = group[0]
    alternative = _against(timepoints, other, order)
    timepoints[other].release()
    mine, theirs = _good(results), _good(alternative)
    centred = {n: square_names_the_centre(timepoints[n]) for n in (reference, other)}
    if theirs > mine or (theirs == mine and centred[other] and not centred[reference]):
        alternative_reason = (f"{reference}, the first candidate, is contradicted by {count} timepoint(s) that agree "
                              f"with one another; {other} is confirmed by more of the well's timepoints")
        return {"reference": other, "reference_reason": alternative_reason, "timepoints": alternative}
    if theirs == mine and centred[other] == centred[reference]:
        why = (f"{reference} and {other} each make the other's addresses wrong ({signature[0]}), and as many "
               "timepoints agree with each: which is right cannot be told. An anchor set by hand on the central "
               "square settles it")
        disputed = {n for n, v in {**results, **alternative}.items() if v.get("verdict") in GOOD + (CONTRADICTED,)}
        for n in disputed:
            results[n] = {"verdict": CONTRADICTED, "reason": why, "diagnosis": {"kind": "tie", "detail": why}}
        out["reference"], out["reference_reason"] = None, why
    return out


# ── the files ────────────────────────────────────────────────────────────────
def sidecar_path(out_dir, well: str) -> Path:
    return Path(out_dir) / NANOWELL_DIR / f"{well}{SIDECAR_SUFFIX}"


def inputs_digest(out_dir, well: str) -> str | None:
    """A digest of the files the check reads for *well*: the sites table, the grid record and the montages'
    CRCs; ``None`` when the tables are missing."""
    folder = Path(out_dir) / NANOWELL_DIR
    sites, grid = folder / f"{well}.csv", folder / f"{well}_grid.json"
    if not sites.is_file() or not grid.is_file():
        return None
    h = hashlib.sha256()
    for path in (sites, grid):
        h.update(path.read_bytes())
    archive = Path(out_dir) / f"{well}_out.zip"
    if archive.is_file():
        try:
            with zipfile.ZipFile(archive) as zf:
                for info in sorted(zf.infolist(), key=lambda i: i.filename):
                    if info.filename.endswith("_montage.png"):
                        h.update(f"{info.filename}:{info.CRC}:{info.file_size}".encode())
        except (OSError, zipfile.BadZipFile):
            h.update(b"unreadable-archive")
    return h.hexdigest()[:20]


def montage_loaders(out_dir, well: str, timepoints) -> dict:
    """``timepoint -> function reading its uint8 montage`` from ``<well>_out.zip``, for the timepoints the archive
    holds: :func:`check_well` reads each when it compares it and drops it after, rather than holding them all."""
    from skimage.io import imread

    archive = Path(out_dir) / f"{well}_out.zip"
    if not archive.is_file():
        return {}
    try:
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        log.warning("Well %s — the montage archive cannot be read for the identity check: %s", well, exc)
        return {}

    def loader(name):
        def load():
            with zipfile.ZipFile(archive) as zf:
                image = imread(io.BytesIO(zf.read(name)))
            return image if image.ndim == 2 else image[..., 0]
        return load

    return {tp: loader(_MONTAGE_NAME.format(well=well, tp=tp)) for tp in timepoints
            if _MONTAGE_NAME.format(well=well, tp=tp) in names}


def read_montages(out_dir, well: str, timepoints) -> dict:
    """``timepoint -> uint8 montage`` from ``<well>_out.zip``; a timepoint without one is absent."""
    from skimage.io import imread

    archive = Path(out_dir) / f"{well}_out.zip"
    out = {}
    if not archive.is_file():
        return out
    try:
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
            for tp in timepoints:
                name = _MONTAGE_NAME.format(well=well, tp=tp)
                if name in names:
                    image = imread(io.BytesIO(zf.read(name)))
                    out[tp] = image if image.ndim == 2 else image[..., 0]
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        log.warning("Well %s — the montage archive cannot be read for the identity check: %s", well, exc)
    return out


def _read_sites(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def check_well_dir(out_dir, well: str, order=None) -> dict | None:
    """Check *well* of the output folder *out_dir* from its files and write its sidecar; returns the sidecar, or
    ``None`` when the well has no nanowell tables."""
    folder = Path(out_dir) / NANOWELL_DIR
    grid_path, sites_path = folder / f"{well}_grid.json", folder / f"{well}.csv"
    if not grid_path.is_file() or not sites_path.is_file():
        return None
    digest = inputs_digest(out_dir, well)
    grid = json.loads(grid_path.read_text(encoding="utf-8"))
    names = list(order) if order is not None else list(grid.get("timepoints") or {})
    payload = {"version": IDENTITY_VERSION, "well": well, "inputs": digest,
               "tolerance_pitches": TOLERANCE_PITCHES, "min_margin": MIN_MARGIN,
               **check_well(grid, _read_sites(sites_path), montage_loaders(out_dir, well, names), names)}
    previous = read_sidecar(out_dir, well, verify=False)
    if previous and isinstance(previous.get("stage"), dict) and previous.get("inputs") == digest:
        payload["stage"] = previous["stage"]
    write_sidecar(out_dir, well, payload)
    return payload


def check_after_run(out_dir, well: str, order=None) -> dict | None:
    """:func:`check_well_dir` as the run calls it once a well's tables and montages are written: every timepoint
    that is not good is logged as a warning, and a check that cannot run is logged and leaves no sidecar (so the
    well reads :data:`NOT_CHECKED`), never failing the well."""
    from util.aw_safe import swallow

    payload = None
    with swallow(f"the identity check of well {well}", level=logging.WARNING, logger=log):
        payload = check_well_dir(out_dir, well, order)
    if payload is None:
        return None
    for tp, entry in payload["timepoints"].items():
        if entry.get("verdict") in (CONTRADICTED, INCONCLUSIVE):
            log.warning("Well %s timepoint %s: nanowell identity %s against %s (%s)", well, tp, entry["verdict"],
                        payload.get("reference") or "no reference", entry.get("reason", ""))
    return payload


def write_sidecar(out_dir, well: str, payload: dict) -> None:
    path = sidecar_path(out_dir, well)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    tmp.replace(path)


def read_sidecar(out_dir, well: str, *, verify: bool = True) -> dict | None:
    """The well's sidecar, or ``None`` when there is none, it cannot be read, it is of another version, or (with
    *verify*) it no longer describes the files it was computed from."""
    path = sidecar_path(out_dir, well)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != IDENTITY_VERSION:
        return None
    if verify and payload.get("inputs") != inputs_digest(out_dir, well):
        return None
    return payload


def verdicts(out_dir, well: str, timepoints) -> dict[str, tuple[str, str]]:
    """``timepoint -> (verdict, reason)`` for *well*, :data:`NOT_CHECKED` for every timepoint when the sidecar is
    missing or stale, and for a timepoint it does not name. A stage-check failure overrides a good verdict."""
    payload = read_sidecar(out_dir, well)
    if payload is None:
        why = ("the identity check has not been run on these tables" if not sidecar_path(out_dir, well).is_file()
               else "the identity check was run on other tables than these (they changed since)")
        return {tp: (NOT_CHECKED, why) for tp in timepoints}
    return {tp: verdict_in(payload, tp) for tp in timepoints}


def verdict_in(payload: dict, tp: str) -> tuple[str, str]:
    """``(verdict, reason)`` for *tp* in a sidecar *payload* already read: :data:`NOT_CHECKED` for a timepoint it does
    not name, and a stage-check failure overriding a good or inconclusive verdict."""
    entry = (payload.get("timepoints") or {}).get(tp)
    if not isinstance(entry, dict):
        return NOT_CHECKED, "the identity check did not reach this timepoint"
    verdict, reason = str(entry.get("verdict") or NOT_CHECKED), str(entry.get("reason") or "")
    flagged = ((payload.get("stage") or {}).get("timepoints") or {}).get(tp)
    if isinstance(flagged, dict) and flagged.get("ok") is False and verdict in GOOD + (INCONCLUSIVE,):
        verdict, reason = CONTRADICTED, flagged.get("reason") or "the stage cross-check failed"
    return (verdict if verdict in VERDICTS else NOT_CHECKED), reason


# ── the plate-level stage cross-check ────────────────────────────────────────
def stage_check(out_dir, wells=None) -> dict:
    """Fit each timepoint's well origins in stage coordinates to the plate's earliest by its rigid motion and flag
    the wells off it; the result is written into each checked well's sidecar under ``stage`` and returned as
    ``well -> {timepoint -> {"ok", "residual_um", "reason"}}``.

    Each physical plate is fitted on its own (``nanowell_task.plates_of``): a Merge Plates dataset holds two plates
    that moved differently, and one fit over both flagged every well (tracker R3). Only origins anchored on the
    well's own evidence take part (``nanowell_task._INDEPENDENT``): a plate-rigid origin was predicted from this very
    fit and would agree with it by construction. It needs a stage position for every tile (``origin_stage_um``)
    and :data:`STAGE_MIN_WELLS` such wells at both timepoints; otherwise nothing is flagged and the reason is
    recorded. The tolerance is the image check's quarter pitch, not calibrated on a real plate's stage (tracker
    Q5)."""
    from pipeline.nanowell_task import plates_of

    folder = Path(out_dir) / NANOWELL_DIR
    grids = {}
    for path in sorted(folder.glob("*_grid.json")):
        well = path.name[: -len("_grid.json")]
        if wells is not None and well not in wells:
            continue
        try:
            grids[well] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    results: dict[str, dict] = {w: {} for w in grids}
    references, reasons = {}, {}
    for plate, members in plates_of(grids).items():
        reference, reason = _stage_check_plate(members, results)
        references[plate], reasons[plate] = reference, reason
    for well, entries in results.items():
        # Read without verifying the digest: in a batched remote run the final pass runs after a released well's
        # montage archive was deleted on the remote, which would fail the digest there and drop this block without
        # a word (tracker R2). The block lands in the sidecar the well's own check wrote; the digest is verified
        # where the sidecar is read.
        payload = read_sidecar(out_dir, well, verify=False)
        if payload is None:
            if entries:
                log.warning("Well %s — no identity sidecar to record the stage cross-check in", well)
            continue
        plate = grids[well].get("plate") or ""
        payload["stage"] = {"reference": references.get(plate), "reason": reasons.get(plate, ""),
                            "timepoints": entries}
        write_sidecar(out_dir, well, payload)
    single = len(references) == 1
    return {"reference": next(iter(references.values())) if single else references,
            "reason": next(iter(reasons.values())) if single else reasons, "wells": results}


def _stage_check_plate(grids: dict, results: dict) -> tuple[str | None, str]:
    """:func:`stage_check` over one plate's wells (*grids*), filling *results*; ``(reference, why none)``."""
    from pipeline.nanowell_lattice import fit_plate_rigid
    from pipeline.nanowell_task import _INDEPENDENT

    origins: dict[str, dict] = {}
    pitches: dict[str, float] = {}
    order: list[str] = []
    for well, grid in grids.items():
        for tp, block in (grid.get("timepoints") or {}).items():
            if tp not in order:
                order.append(tp)
            if not isinstance(block, dict) or block.get("status") != "ok":
                continue
            point = _pair(block.get("origin_stage_um"))
            if point is not None and block.get("origin_method") in _INDEPENDENT:
                origins.setdefault(tp, {})[well] = point
                if _float(block.get("pitch_um")):
                    pitches[well] = float(block["pitch_um"])
    reference = next((tp for tp in order if len(origins.get(tp, {})) >= STAGE_MIN_WELLS), None)
    if reference is None:
        return None, (f"fewer than {STAGE_MIN_WELLS} wells anchored on their own evidence have stage positions at any "
                      "one timepoint")
    for tp in order:
        if tp == reference:
            continue
        common = set(origins.get(reference, {})) & set(origins.get(tp, {}))
        fit = (fit_plate_rigid(origins[reference], origins[tp]) if len(common) >= STAGE_MIN_WELLS else None)
        if fit is None or not fit.ok:
            why = (fit.reason if fit is not None else
                   f"{len(common)} well(s) anchored on their own evidence have stage positions at both {reference} "
                   f"and {tp}; the plate's motion needs {STAGE_MIN_WELLS} before it can single one out")
            for well in grids:
                results[well][tp] = {"ok": None, "residual_um": None, "reason": why}
            continue
        for well, residual in fit.residuals:
            tol = TOLERANCE_PITCHES * pitches.get(well, float("nan"))
            ok = bool(residual <= tol) if math.isfinite(tol) else None
            results[well][tp] = {"ok": ok, "residual_um": round(float(residual), 3), "reason": "" if ok is not False
                                 else (f"the well's address origin sits {residual:.0f} um off the plate's rigid "
                                       f"motion since {reference} (stage positions), more than {tol:.0f} um")}
    return reference, ""


# ── the audit ────────────────────────────────────────────────────────────────
def wells_in(out_dir) -> list[str]:
    folder = Path(out_dir) / NANOWELL_DIR
    return sorted(p.name[: -len("_grid.json")] for p in folder.glob("*_grid.json")) if folder.is_dir() else []


def summary_lines(results: dict) -> list[str]:
    """One line per well: its counts of each verdict, and each failing timepoint's reason."""
    lines = []
    for well, payload in sorted(results.items()):
        if payload is None:
            lines.append(f"{well}: no nanowell tables")
            continue
        counts = Counter(v.get("verdict") for v in payload["timepoints"].values())
        lines.append(f"{well}: " + ", ".join(f"{counts[v]} {v}" for v in VERDICTS if counts[v])
                     + (f" (reference {payload['reference']})" if payload.get("reference") else
                        f" ({payload.get('reference_reason')})"))
        for tp, v in payload["timepoints"].items():
            if v.get("verdict") in (CONTRADICTED, INCONCLUSIVE):
                lines.append(f"  {tp}: {v['verdict']} — {v.get('reason', '')}")
    return lines


def stage_check_after_run(out_dir) -> None:
    """:func:`stage_check` as the run calls it at the end of a plate: each flagged well logged, a failure logged."""
    from util.aw_safe import swallow

    with swallow("the nanowell identity stage cross-check", level=logging.WARNING, logger=log):
        result = stage_check(out_dir)
        for well, entries in sorted(result["wells"].items()):
            for tp, entry in entries.items():
                if entry.get("ok") is False:
                    log.warning("Well %s timepoint %s: nanowell identity contradicted by the stage positions (%s)",
                                well, tp, entry["reason"])


def audit(out_dir, wells=None) -> dict:
    """Check every (or each named) well of an analysed dataset, write the sidecars, run the stage cross-check."""
    results = {well: check_well_dir(out_dir, well) for well in (wells or wells_in(out_dir))}
    stage_check(out_dir, wells)
    return {well: read_sidecar(out_dir, well) or results[well] for well in results}


def main(argv=None) -> int:
    """``python -m pipeline.nanowell_identity <output_dir> [--well A01 ...]``: audit a dataset; exit 1 when any
    timepoint is contradicted, 2 when none is but any is inconclusive, 0 when every timepoint is good."""
    import argparse

    parser = argparse.ArgumentParser(description="Check that each nanowell address names the same nanowell at "
                                                 "every timepoint, and write nanowells/<well>_identity.json.")
    parser.add_argument("output_dir")
    parser.add_argument("--well", action="append", dest="wells", help="check only this well (repeatable)")
    args = parser.parse_args(argv)
    if not (Path(args.output_dir) / NANOWELL_DIR).is_dir():
        print(f"{args.output_dir} holds no {NANOWELL_DIR}/ folder: it is not an analysed nanowell dataset")
        return 2
    results = audit(args.output_dir, args.wells)
    for line in summary_lines(results):
        print(line)
    verdict_set = {v.get("verdict") for p in results.values() if p for v in p["timepoints"].values()}
    return 1 if CONTRADICTED in verdict_set else 2 if INCONCLUSIVE in verdict_set else 0


if __name__ == "__main__":                                        # pragma: no cover - the command line
    raise SystemExit(main())
