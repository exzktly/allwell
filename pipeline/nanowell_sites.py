"""What state each nanowell site is in: occluded, clipped, in focus, consistent.

Qt-free; numpy and scipy only. These functions describe sites that
``nanowell_detect`` found and fitted; none of them drops a site. A number that
cannot be measured is ``None`` (undefined), never zero: a site under a bubble has
no focus, not a focus of nothing, and a site outside the tiles has no occlusion
state at all.

Units and frames are those of ``nanowell_detect``: micrometres in the frame where
pixel ``i`` covers ``[i, i + 1) * pixel_size``, image y down.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from pipeline.field_quality import edge_focus
from pipeline.nanowell_detect import FITTED, SiteGeometry
from pipeline.nanowell_lattice import ArrayBoundary, LatticeFit

#: A site whose visible share falls below this is called occluded. A caller that wants another
#: cut reads ``visible_fraction`` itself; this is only the default word for it.
OCCLUDED_BELOW = 0.5
#: Pitch and angle may differ between timepoints of one well by this much (tracker §3.4.4).
PITCH_TOLERANCE = 0.005
ANGLE_TOLERANCE_DEG = 0.5
#: A nanowell diameter further than this from the setting's is a format mismatch.
FORMAT_RADIUS_TOLERANCE = 0.25
#: Nanowells wider than this share of the pitch would touch their neighbours.
_MAX_DIAMETER_TO_PITCH = 0.95


# ── N5: occlusion ────────────────────────────────────────────────────────────
def bubble_mask(image, pixel_size_um: float, fit: LatticeFit, array_centre_um, array_radius_um: float, *,
                reference_points_um=None, threshold: float = 0.3) -> np.ndarray:
    """True where the lattice's own signal is missing inside the array: a bubble.

    The image is demodulated at the lattice's first-order frequencies and smoothed over about
    one pitch. Where nanowells repeat, the amplitude is that of the lattice; under a bubble the
    texture is gone and the amplitude falls to nothing. Pixels below *threshold* times the
    typical lattice amplitude are flagged. The typical amplitude is read at *reference_points_um*
    (the detected sites, which are by construction where the lattice is) when they are given,
    and otherwise is the array's own 80th percentile, which fails when most of the array is
    hidden: with 84 % of it under a bubble the percentile is itself bubble. Outside the array the
    floor is flat too, and is never flagged: the mask says "a bubble is here", not "no nanowell
    is here". The demodulation runs on an image block-averaged to about 8 px per pitch, because
    the amplitude is smooth over a pitch, and the mask is returned at full size.
    """
    from scipy import ndimage

    if not fit.ok:
        raise ValueError(f"Cannot find a bubble mask without a lattice: {fit.reason}")
    full = np.asarray(image, dtype=np.float64)
    h, w = full.shape
    pitch_px = fit.pitch / pixel_size_um
    f = max(1, int(pitch_px // 8))
    hh, ww = (h // f) * f, (w // f) * f
    image = full[:hh, :ww].reshape(hh // f, f, ww // f, f).mean(axis=(1, 3)) if f > 1 else full
    ch, cw = image.shape
    scale = pixel_size_um * f
    basis = np.column_stack((fit.a1, fit.a2))                    # um
    dual = np.linalg.inv(basis).T                                # columns: g1, g2 in cycles/um
    frequencies = [dual[:, 0], dual[:, 1]]
    if fit.kind == "hex":
        frequencies.append(dual[:, 0] + dual[:, 1])
    ys, xs = np.mgrid[0:ch, 0:cw]
    x_um, y_um = (xs + 0.5) * scale, (ys + 0.5) * scale
    sigma = 0.6 * fit.pitch / scale
    centred = image - ndimage.gaussian_filter(image, 2.0 * fit.pitch / scale)
    amplitude = np.zeros((ch, cw))
    for g in frequencies:
        carrier = np.exp(-2j * np.pi * (g[0] * x_um + g[1] * y_um))
        product = centred * carrier
        amplitude += np.hypot(ndimage.gaussian_filter(product.real, sigma), ndimage.gaussian_filter(product.imag, sigma))
    amplitude /= len(frequencies)
    inside = np.hypot(x_um - array_centre_um[0], y_um - array_centre_um[1]) <= array_radius_um - fit.pitch / 2
    if not inside.any():
        return np.zeros((h, w), dtype=bool)
    if reference_points_um is not None and len(reference_points_um):
        pts = np.asarray(reference_points_um, dtype=float)
        px_idx = np.clip(np.rint(pts[:, 0] / scale - 0.5).astype(int), 0, cw - 1)
        py_idx = np.clip(np.rint(pts[:, 1] / scale - 0.5).astype(int), 0, ch - 1)
        typical = float(np.median(amplitude[py_idx, px_idx]))
    else:
        typical = float(np.percentile(amplitude[inside], 80))
    coarse = inside & (amplitude < threshold * typical)
    mask = np.zeros((h, w), dtype=bool)
    mask[:hh, :ww] = np.kron(coarse, np.ones((f, f), dtype=bool)) if f > 1 else coarse
    return mask


@dataclass(frozen=True)
class SiteVisibility:
    index: int
    #: Share of the site's disk that is visible: bright like a nanowell and outside any bubble.
    #: ``None`` when no pixel of the disk is inside the image.
    visible_fraction: float | None
    #: The disk's brightness over the lattice around it, relative to the well's typical site
    #: (1 is typical, 0 is a black disk on black). ``None`` when it cannot be measured.
    contrast: float | None
    occluded: bool | None               # visible_fraction below OCCLUDED_BELOW; None when undefined


def _disk_pixels(image_shape, centre_px, inner: float, outer: float):
    """Pixel indices (y, x) with ``inner <= distance <= outer`` from *centre_px*, clipped to the image."""
    h, w = image_shape
    x0, x1 = max(int(math.floor(centre_px[0] - outer)), 0), min(int(math.ceil(centre_px[0] + outer)) + 1, w)
    y0, y1 = max(int(math.floor(centre_px[1] - outer)), 0), min(int(math.ceil(centre_px[1] + outer)) + 1, h)
    if x1 <= x0 or y1 <= y0:
        return np.empty(0, dtype=int), np.empty(0, dtype=int)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    distance = np.hypot(xx - centre_px[0], yy - centre_px[1])
    keep = (distance >= inner) & (distance <= outer)
    return yy[keep], xx[keep]


def site_visibility(image, pixel_size_um: float, geometries, *, mask=None, neighbours: int = 12
                    ) -> list[SiteVisibility]:
    """Each site's visible fraction and contrast, from the image at its fitted geometry.

    A nanowell is a light disk on a dark lattice, so its disk is bright; a site under a bubble
    is a black disk on the black lattice, with no brightness over its surroundings. A disk pixel
    counts as visible when it lies above the midpoint between a typical site's brightness and a
    typical lattice level, and **typical means local**: the median over the *neighbours* nearest
    fitted sites, so illumination that falls toward the array's rim (to 36 % in one test) moves
    the midpoint with it instead of reading as a bubble. With fewer than three fitted sites the
    percentiles of the whole image stand in. With a bubble *mask* a pixel under it is not visible
    whatever its brightness. A site off the image has ``None`` for both numbers, not zero.

    ``occluded`` is simply a visible fraction under :data:`OCCLUDED_BELOW`. **A dark cell that
    covers most of a nanowell therefore reads as occluded**, exactly as a bubble does: telling
    the two apart (a cell has internal structure, a bubble is flat) is for the contents section,
    and a cell on a third of the disk, as in the tests, is not flagged.
    """
    from scipy import spatial

    image = np.asarray(image, dtype=np.float64)
    h, w = image.shape
    pack = []
    for g in geometries:
        centre = (g.centre_um[0] / pixel_size_um - 0.5, g.centre_um[1] / pixel_size_um - 0.5)
        radius = g.radius_um / pixel_size_um
        disk = _disk_pixels((h, w), centre, 0.0, 0.85 * radius)
        ring = _disk_pixels((h, w), centre, 1.35 * radius, 1.8 * radius)
        pack.append((g, disk, ring))
    fitted = [(g.centre_um, float(image[d].mean()), float(image[r].mean()))
              for g, d, r in pack if g.geometry == FITTED and len(d[0]) and len(r[0])]
    tree = bright_at = dark_at = None
    if len(fitted) >= 3:
        tree = spatial.cKDTree(np.array([f[0] for f in fitted]))
        bright_at = np.array([f[1] for f in fitted])
        dark_at = np.array([f[2] for f in fitted])
        k = min(neighbours, len(fitted))
    fallback = (float(np.percentile(image, 90)), float(np.percentile(image, 10)))
    out = []
    for g, disk, ring in pack:
        if not len(disk[0]):
            out.append(SiteVisibility(g.index, None, None, None))
            continue
        if tree is not None:
            _, near = tree.query(g.centre_um, k=k)
            near = np.atleast_1d(near)
            bright, dark = float(np.median(bright_at[near])), float(np.median(dark_at[near]))
        else:
            bright, dark = fallback
        midpoint, span = (bright + dark) / 2, bright - dark
        values = image[disk]
        seen = values >= midpoint
        if mask is not None:
            seen &= ~np.asarray(mask, dtype=bool)[disk]
        fraction = float(seen.mean())
        contrast = None
        if len(ring[0]) and span > 0:
            contrast = float((values.mean() - image[ring].mean()) / span)
        out.append(SiteVisibility(g.index, fraction, contrast, bool(fraction < OCCLUDED_BELOW)))
    return out


# ── N6: how much of a site was imaged, and how much of it is array and well ──
@dataclass(frozen=True)
class SiteCoverage:
    index: int
    imaged_fraction: float              # share of the disk inside the union of the tiles
    inside_array_fraction: float | None     # share inside the fitted array boundary; None without one
    inside_well_fraction: float | None      # share inside the well wall; None without one


def _disk_samples(count: int = 127) -> np.ndarray:
    """Deterministic sample points spread evenly over the area of a unit disk (a Vogel spiral).

    Every point stands for the same area, so the share of them inside a boundary is the share of
    the disk's area. (Concentric rings of 6, 12, 18 ... points weighted each ring equally per
    point, which gave the outer ring 39 % of the points for 23 % of the area and overstated a
    chord's outside share by up to 2.5x.)
    """
    k = np.arange(count)
    radius = np.sqrt((k + 0.5) / count)
    angle = k * math.pi * (3 - math.sqrt(5))
    return np.column_stack((radius * np.cos(angle), radius * np.sin(angle)))


def site_coverage(geometries, tile_rects, *, array_centre_um=None, array_radius_um=None,
                  well_centre_um=None, well_radius_um=None) -> list[SiteCoverage]:
    """Imaged, in-array and in-well shares of every site's disk. Never raises on a clipped edge.

    *tile_rects* are ``(left, top, right, bottom)`` in um (the registered tiles). A site wholly
    outside the tiles has ``imaged_fraction`` 0.0, which is a measurement here: it was not
    imaged. The array and well shares are ``None`` when no boundary is given, because
    "unknown" is not "all of it".
    """
    rects = np.asarray(list(tile_rects), dtype=float).reshape(-1, 4)
    samples = _disk_samples()
    out = []
    for g in geometries:
        pts = np.asarray(g.centre_um) + g.radius_um * samples
        if len(rects):
            inside_tiles = np.zeros(len(pts), dtype=bool)
            for left, top, right, bottom in rects:
                inside_tiles |= (pts[:, 0] >= left) & (pts[:, 0] <= right) & (pts[:, 1] >= top) & (pts[:, 1] <= bottom)
            imaged = float(inside_tiles.mean())
        else:
            imaged = 0.0
        in_array = in_well = None
        if array_centre_um is not None and array_radius_um is not None:
            in_array = float((np.hypot(pts[:, 0] - array_centre_um[0], pts[:, 1] - array_centre_um[1])
                              <= array_radius_um).mean())
        if well_centre_um is not None and well_radius_um is not None:
            in_well = float((np.hypot(pts[:, 0] - well_centre_um[0], pts[:, 1] - well_centre_um[1])
                             <= well_radius_um).mean())
        out.append(SiteCoverage(g.index, imaged, in_array, in_well))
    return out


# ── N7: focus from the rim ───────────────────────────────────────────────────
def rim_focus(image, pixel_size_um: float, geometry: SiteGeometry) -> float | None:
    """Focus of one site's rim by ``pipeline.field_quality.edge_focus``; ``None`` when undefined.

    Every nanowell rim is the same strong edge, so a site's focus is measured whether or not it
    holds cells. A ``predicted`` site has no measured rim, so its focus is undefined, and so is
    one whose disk runs off the image.
    """
    if geometry.geometry != FITTED:
        return None
    image = np.asarray(image, dtype=np.float64)
    h, w = image.shape
    cx, cy = geometry.centre_um[0] / pixel_size_um - 0.5, geometry.centre_um[1] / pixel_size_um - 0.5
    r = geometry.radius_um / pixel_size_um
    half = int(math.ceil(1.6 * r)) + 2
    x0, y0 = int(round(cx)) - half, int(round(cy)) - half
    if x0 < 0 or y0 < 0 or x0 + 2 * half + 1 > w or y0 + 2 * half + 1 > h:
        return None
    patch = image[y0:y0 + 2 * half + 1, x0:x0 + 2 * half + 1]
    yy, xx = np.mgrid[0:2 * half + 1, 0:2 * half + 1]
    mask = np.hypot(xx + x0 - cx, yy + y0 - cy) <= r
    value = edge_focus(patch, mask)
    return None if value is None else float(value)


# ── N8 / N8a: across timepoints ──────────────────────────────────────────────
@dataclass(frozen=True)
class TimepointLattice:
    """What one timepoint of a well measured about its lattice."""
    timepoint: str
    pitch_um: float
    angle_deg: float
    #: The plate's rotation since the reference timepoint, from the plate-rigid fit, in the same
    #: convention as ``angle_deg``. Zero for the reference itself.
    plate_rotation_deg: float = 0.0
    roster: frozenset | None = None     # addresses in the roster built from the shared radius


@dataclass(frozen=True)
class Consistency:
    timepoint: str
    status: str                         # "consistent" or a named violation
    detail: str = ""


def _fold(angle: float, period: float) -> float:
    return (angle + period / 2) % period - period / 2


def check_consistency(timepoints, *, reference: str | None = None) -> list[Consistency]:
    """Pitch, angle (after the plate's rotation) and roster against a reference timepoint.

    The reference is *reference* or the first. A violation is a named status on the timepoint,
    never an exception: ``pitch_mismatch`` beyond 0.5 %, ``angle_mismatch`` beyond 0.5 degrees
    once the plate's measured rotation is removed (folded into the lattice's own 60-degree
    period), ``roster_mismatch`` when the address sets differ. The first violated check names
    the status; every violated check is in the detail.
    """
    timepoints = list(timepoints)
    if not timepoints:
        return []
    ref = next((t for t in timepoints if t.timepoint == reference), timepoints[0])
    out = []
    for t in timepoints:
        problems = []
        if abs(t.pitch_um / ref.pitch_um - 1) > PITCH_TOLERANCE:
            problems.append(("pitch_mismatch", f"pitch {t.pitch_um:.2f} um against {ref.pitch_um:.2f} "
                                               f"({(t.pitch_um / ref.pitch_um - 1) * 100:+.2f} %)"))
        angle = _fold((t.angle_deg - t.plate_rotation_deg) - ref.angle_deg, 60.0)
        if abs(angle) > ANGLE_TOLERANCE_DEG:
            problems.append(("angle_mismatch", f"angle differs by {angle:+.2f} degrees after removing the "
                                               f"plate's rotation of {t.plate_rotation_deg:.2f}"))
        if t.roster is not None and ref.roster is not None and t.roster != ref.roster:
            problems.append(("roster_mismatch", f"{len(t.roster ^ ref.roster)} addresses differ from the "
                                                f"reference roster"))
        out.append(Consistency(t.timepoint, problems[0][0] if problems else "consistent",
                               "; ".join(text for _, text in problems)))
    return out


@dataclass(frozen=True)
class SharedRadius:
    radius_um: float | None             # one array-model radius for the well; None when none fitted
    status: str                         # "shared", or "unresolved" when no timepoint fitted a boundary
    deviations_um: dict                 # timepoint -> its own radius minus the shared one (None: no boundary)
    flags: dict                         # timepoint -> "radius_disagrees" | "no_boundary"


def shared_array_radius(boundaries: dict, pitch_um: float) -> SharedRadius:
    """One array-model radius per well: the median of the timepoints whose boundary fitted.

    Independently noisy radius estimates select different edge sites, so every timepoint's
    roster is built from this one value (tracker N8a). Each timepoint's own deviation from it
    is recorded; a timepoint disagreeing by more than half a pitch is flagged
    ``radius_disagrees``, one with no fitted boundary ``no_boundary``, and neither is dropped.
    """
    fitted = {k: b.radius for k, b in boundaries.items() if isinstance(b, ArrayBoundary) and b.status == "fitted"}
    if not fitted:
        return SharedRadius(None, "unresolved", {k: None for k in boundaries},
                            {k: "no_boundary" for k in boundaries})
    shared = float(np.median(list(fitted.values())))
    deviations = {k: (fitted[k] - shared if k in fitted else None) for k in boundaries}
    flags = {}
    for k in boundaries:
        if k not in fitted:
            flags[k] = "no_boundary"
        elif abs(deviations[k]) > pitch_um / 2:
            flags[k] = "radius_disagrees"
    return SharedRadius(shared, "shared", deviations, flags)


# ── N9: the plate format ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class FormatCheck:
    #: True when the format setting is consistent with *both* the pitch and the fitted nanowells;
    #: False for a mismatch; ``None`` (undefined) when there is nothing to check it against.
    ok: bool | None
    status: str                         # "ok", "format_mismatch" or "unverified"
    reason: str = ""


def check_format(pitch_um: float | None, nominal_diameter_um: float, fitted_radii_um=None) -> FormatCheck:
    """Whether the plate format setting can be right for what was measured.

    Two independent tests. The lattice's pitch is read from the image before any matched filter
    is chosen, and a nanowell nearly as wide as the pitch would touch its neighbours, so a
    setting that wide against a finer lattice is wrong whatever else happens. And once sites
    are fitted, their median diameter must be within 25 % of the setting's. Either failing is
    reported as a format mismatch instead of letting a template of the wrong size detect the
    wrong thing.

    **Passing the pitch test alone proves little**: a setting too small for the array passes it.
    With no fitted radii the answer is therefore ``unverified`` (``ok=None``), not ``ok``; a
    caller that has no fitted sites at all (detection failed) must not read the setting as
    confirmed. An unreadable pitch is likewise ``unverified``.
    """
    if pitch_um is None or not math.isfinite(pitch_um) or pitch_um <= 0:
        return FormatCheck(None, "unverified", "The lattice pitch could not be measured, so the plate format "
                                               "setting cannot be checked")
    if nominal_diameter_um >= _MAX_DIAMETER_TO_PITCH * pitch_um:
        return FormatCheck(False, "format_mismatch",
                           f"The plate format sets a {nominal_diameter_um:g} um nanowell but the lattice pitch "
                           f"measures {pitch_um:.1f} um, so neighbouring nanowells would touch: the setting is "
                           f"probably for a different plate format")
    radii = [r for r in (fitted_radii_um or []) if r is not None and math.isfinite(r)]
    if not radii:
        return FormatCheck(None, "unverified", "No nanowell was fitted, so only the pitch could be checked "
                                               "against the setting (which a setting that is too small passes)")
    measured = 2 * float(np.median(radii))
    if abs(measured / nominal_diameter_um - 1) > FORMAT_RADIUS_TOLERANCE:
        return FormatCheck(False, "format_mismatch",
                           f"The fitted nanowells measure {measured:.1f} um across but the plate format "
                           f"sets {nominal_diameter_um:g} um")
    return FormatCheck(True, "ok")
