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
                threshold: float = 0.3) -> np.ndarray:
    """True where the lattice's own signal is missing inside the array: a bubble.

    The image is demodulated at the lattice's first-order frequencies and smoothed over about
    one pitch. Where nanowells repeat, the amplitude is that of the lattice; under a bubble the
    texture is gone and the amplitude falls to nothing. Pixels below *threshold* times the
    array's typical amplitude (its 80th percentile) are flagged. Outside the array the floor is
    flat too, and is never flagged: the mask says "a bubble is here", not "no nanowell is here".
    """
    from scipy import ndimage

    if not fit.ok:
        raise ValueError(f"Cannot find a bubble mask without a lattice: {fit.reason}")
    image = np.asarray(image, dtype=np.float64)
    h, w = image.shape
    basis = np.column_stack((fit.a1, fit.a2))                    # um
    dual = np.linalg.inv(basis).T                                # columns: g1, g2 in cycles/um
    frequencies = [dual[:, 0], dual[:, 1]]
    if fit.kind == "hex":
        frequencies.append(dual[:, 0] + dual[:, 1])
    ys, xs = np.mgrid[0:h, 0:w]
    x_um, y_um = (xs + 0.5) * pixel_size_um, (ys + 0.5) * pixel_size_um
    sigma = 0.6 * fit.pitch / pixel_size_um
    centred = image - ndimage.gaussian_filter(image, 2.0 * fit.pitch / pixel_size_um)
    amplitude = np.zeros((h, w))
    for g in frequencies:
        carrier = np.exp(-2j * np.pi * (g[0] * x_um + g[1] * y_um))
        product = centred * carrier
        amplitude += np.hypot(ndimage.gaussian_filter(product.real, sigma), ndimage.gaussian_filter(product.imag, sigma))
    amplitude /= len(frequencies)
    inside = np.hypot(x_um - array_centre_um[0], y_um - array_centre_um[1]) <= array_radius_um - fit.pitch / 2
    if not inside.any():
        return np.zeros((h, w), dtype=bool)
    typical = float(np.percentile(amplitude[inside], 80))
    return inside & (amplitude < threshold * typical)


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


def site_visibility(image, pixel_size_um: float, geometries, *, mask=None) -> list[SiteVisibility]:
    """Each site's visible fraction and contrast, from the image at its fitted geometry.

    A nanowell is a light disk on a dark lattice, so its disk is bright; a site under a bubble
    is a black disk on the black lattice, with no brightness over its surroundings. The well's
    typical site and typical lattice level come from the *fitted* sites (the percentiles of the
    whole image when fewer than three fitted), and a disk pixel counts as visible when it lies
    above their midpoint. With a bubble *mask* a pixel under it is not visible whatever its
    brightness. A site off the image has ``None`` for both numbers, not zero.
    """
    image = np.asarray(image, dtype=np.float64)
    h, w = image.shape
    pack = []
    for g in geometries:
        centre = (g.centre_um[0] / pixel_size_um - 0.5, g.centre_um[1] / pixel_size_um - 0.5)
        radius = g.radius_um / pixel_size_um
        disk = _disk_pixels((h, w), centre, 0.0, 0.85 * radius)
        ring = _disk_pixels((h, w), centre, 1.35 * radius, 1.8 * radius)
        pack.append((g, disk, ring))
    inside = [float(image[d].mean()) for g, d, r in pack if g.geometry == FITTED and len(d[0])]
    around = [float(image[r].mean()) for g, d, r in pack if g.geometry == FITTED and len(r[0])]
    if len(inside) >= 3:
        bright, dark = float(np.median(inside)), float(np.median(around))
    else:
        bright, dark = float(np.percentile(image, 90)), float(np.percentile(image, 10))
    midpoint = (bright + dark) / 2
    span = bright - dark
    out = []
    for g, disk, ring in pack:
        if not len(disk[0]):
            out.append(SiteVisibility(g.index, None, None, None))
            continue
        values = image[disk]
        seen = values >= midpoint
        if mask is not None:
            seen &= ~np.asarray(mask, dtype=bool)[disk]
        fraction = float(seen.mean())
        contrast = None
        if len(ring[0]) and span > 0:
            contrast = float((values.mean() - image[ring].mean()) / span)
        out.append(SiteVisibility(g.index, fraction, contrast, fraction < OCCLUDED_BELOW))
    return out


# ── N6: how much of a site was imaged, and how much of it is array and well ──
@dataclass(frozen=True)
class SiteCoverage:
    index: int
    imaged_fraction: float              # share of the disk inside the union of the tiles
    inside_array_fraction: float | None     # share inside the fitted array boundary; None without one
    inside_well_fraction: float | None      # share inside the well wall; None without one


def _disk_samples(count_rings: int = 4) -> np.ndarray:
    """Deterministic sample points over a unit disk, each ring weighted by its area."""
    points = [(0.0, 0.0)]
    for ring in range(1, count_rings + 1):
        radius = ring / count_rings
        n = 6 * ring
        angles = 2 * math.pi * (np.arange(n) + 0.5 * (ring % 2)) / n
        points.extend(zip(radius * np.cos(angles), radius * np.sin(angles)))
    return np.asarray(points)


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
    ok: bool
    status: str                         # "ok" or "format_mismatch"
    reason: str = ""


def check_format(pitch_um: float, nominal_diameter_um: float, fitted_radii_um=None) -> FormatCheck:
    """Whether the plate format setting can be right for what was measured.

    Two independent tests. The lattice's pitch is read from the image before any matched filter
    is chosen, and a nanowell nearly as wide as the pitch would touch its neighbours, so a
    setting that wide against a finer lattice is wrong whatever else happens. And once sites
    are fitted, their median diameter must be within 25 % of the setting's. Either failing is
    reported as a format mismatch instead of letting a template of the wrong size detect the
    wrong thing.
    """
    if nominal_diameter_um >= _MAX_DIAMETER_TO_PITCH * pitch_um:
        return FormatCheck(False, "format_mismatch",
                           f"The plate format sets a {nominal_diameter_um:g} um nanowell but the lattice pitch "
                           f"measures {pitch_um:.1f} um, so neighbouring nanowells would touch: the setting is "
                           f"probably for a different plate format")
    radii = [r for r in (fitted_radii_um or []) if r is not None and math.isfinite(r)]
    if radii:
        measured = 2 * float(np.median(radii))
        if abs(measured / nominal_diameter_um - 1) > FORMAT_RADIUS_TOLERANCE:
            return FormatCheck(False, "format_mismatch",
                               f"The fitted nanowells measure {measured:.1f} um across but the plate format "
                               f"sets {nominal_diameter_um:g} um")
    return FormatCheck(True, "ok")
