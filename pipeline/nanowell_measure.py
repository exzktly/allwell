"""What is inside each nanowell: label-free first, fluorescence when present.

Qt-free; numpy, scipy and scikit-image only. Phase contrast is always there and
is the primary readout (tracker §3.6). Every nanowell in a well is the same molded
shape, so the phase halo of its rim and the illumination inside it are nearly
identical from site to site; dividing each site by the well's empty-nanowell
template leaves cells on a flat floor instead of beside a bright ring, which is
the main source of false cells along a rim.

The order of work:

1. :func:`build_template` takes sub-pixel-aligned crops of the visible sites and
   forms the template from the least-textured quarter (the empty ones), recording
   where it came from; with too few such sites it falls back to the well's earliest
   template and says so.
2. :func:`rim_suppress` divides every site's pixels by the template and sets
   everything outside the sites flat.
3. :func:`texture_threshold` and :func:`site_confluence` say what fraction of a
   site's interior holds cells, with no segmentation, from texture against the
   empty sites' own texture.
4. :func:`count_cells` keeps the cells a segmenter found whose centroid lies in an
   **owned** site's interior, so that a cell in the overlap of two tiles is counted
   once. The segmenter is passed in (production passes Cellpose's label-free path,
   :func:`cellpose_segmenter`), which keeps everything here testable without it.
5. :func:`area_estimate` stores a foreground-area count beside the segmented one and
   flags when they disagree: a clump segmented as one cell undercounts silently.
6. :func:`cell_table` is the per-cell record with the address and the well-frame
   position; :func:`site_fluorescence` the per-site intensity over the empty-site
   background; :func:`site_state` the per-site state of tracker §3.5, in which an
   empty site (a count of zero) and an occluded one (no count) cannot be confused.

Positions in micrometres are in the frame where pixel ``i`` covers
``[i, i + 1) * pixel_size`` (see ``nanowell_detect``).
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

#: Cells sit inside the rim: a site's interior for counting and confluence is this share of its radius.
INTERIOR_FRACTION = 0.9
#: The rim-suppressed tile is written over this multiple of the site radius (the rim itself is divided out).
SUPPRESSION_RADIUS = 1.05
#: The share of visible sites, least textured first, whose crops make the template.
TEMPLATE_QUARTER = 0.25
#: Fewer sites than this in that share and the earliest template is used instead.
MIN_TEMPLATE_SITES = 8
#: Texture is called cell when it exceeds the empty sites' by this many robust standard deviations.
TEXTURE_SIGMAS = 5.0
#: The two counts disagree when they differ by more than this share of the larger, and by at least one cell.
COUNT_DISAGREEMENT = 0.3

EMPTY = "empty"
MEASURED = "measured"
OCCLUDED = "occluded"
CLIPPED = "clipped"
SOURCE_OWN = "own"
SOURCE_EARLIEST = "earliest"
SOURCE_OWN_SPARSE = "own_sparse"


# ── C1: the empty-nanowell template ──────────────────────────────────────────
def site_crops(image, pixel_size_um: float, centres_um, half: int) -> np.ndarray:
    """Square crops of *image* centred on each site to a fraction of a pixel; NaN off the image.

    ``crops[k]`` is ``(2 * half + 1)`` pixels on a side with the site's centre at the middle
    pixel, sampled bilinearly, so crops of different sites line up whatever sub-pixel position
    each had. Shape ``(n, 2 * half + 1, 2 * half + 1)``.
    """
    from scipy import ndimage

    image = np.asarray(image, dtype=np.float64)
    centres = np.asarray(centres_um, dtype=float).reshape(-1, 2)
    offsets = np.arange(-half, half + 1, dtype=float)
    out = np.empty((len(centres), 2 * half + 1, 2 * half + 1))
    for k, (x, y) in enumerate(centres):
        cx, cy = x / pixel_size_um - 0.5, y / pixel_size_um - 0.5
        ys = np.broadcast_to((cy + offsets)[:, None], out.shape[1:])
        xs = np.broadcast_to((cx + offsets)[None, :], out.shape[1:])
        out[k] = ndimage.map_coordinates(image, [ys, xs], order=1, mode="constant", cval=np.nan)
    return out


def _disk(half: int, radius: float) -> np.ndarray:
    y, x = np.mgrid[-half:half + 1, -half:half + 1]
    return np.hypot(x, y) <= radius


@dataclass(frozen=True)
class Template:
    array: np.ndarray                   # (2h+1, 2h+1), the median empty crop, site-centred
    half: int
    source: str                         # SOURCE_OWN, SOURCE_EARLIEST or SOURCE_OWN_SPARSE
    n_sites: int                        # crops the median was taken over
    candidates: int                     # visible sites it was chosen from
    texture_cut: float | None           # the largest residual texture among the sites used


def _azimuthal_texture(crops, half: int, radius: float) -> np.ndarray:
    """Per crop, the RMS departure inside the disk from that crop's own radial mean.

    An empty nanowell is round, so it is its own radial average whatever fraction of the other
    sites hold cells; a cell off the centre is a departure from it. Needs no reference, which is
    why it can pick the empty sites when most of them are loaded.
    """
    y, x = np.mgrid[-half:half + 1, -half:half + 1]
    ring = np.rint(np.hypot(x, y)).astype(int)
    disk = np.hypot(x, y) <= radius
    residual = np.zeros(crops.shape[0])
    for k in range(int(ring[disk].max()) + 1):
        sel = disk & (ring == k)
        if not sel.any():
            continue
        values = crops[:, sel]
        residual += ((values - values.mean(axis=1, keepdims=True)) ** 2).sum(axis=1)
    return np.sqrt(residual / disk.sum())


def build_template(crops, *, interior_radius_px: float, quarter: float = TEMPLATE_QUARTER,
                   min_sites: int = MIN_TEMPLATE_SITES, earliest: Template | None = None) -> Template | None:
    """The empty-nanowell template: the median of the least-textured quarter of the visible crops.

    *crops* are from :func:`site_crops` for the sites that are visible and imaged (the caller
    decides which; a site with NaN anywhere in the disk is skipped here). Texture is the residual
    departure inside the disk from the crop's own radial mean (reference-free, so it picks the
    empty sites even when most are loaded), refined by a second pass against the template those
    sites make; a site holding cells stands out and is left out. With fewer than
    *min_sites* in the quarter (a confluent late timepoint, or the small format at high loading)
    *earliest* is returned as it stands, ``source="earliest"``, and nothing is measured from this
    timepoint's own crops; without one the quarter is used anyway and marked ``own_sparse``.
    ``None`` when no crop is usable at all. The template is in site-centred coordinates, so it stays
    valid after the plate is put back and the lattice has moved.
    """
    crops = np.asarray(crops, dtype=np.float64)
    if crops.ndim != 3 or len(crops) == 0:
        return earliest
    half = crops.shape[1] // 2
    disk = _disk(half, interior_radius_px)
    usable = ~np.isnan(crops[:, disk]).any(axis=1)
    crops = crops[usable]
    candidates = len(crops)
    if candidates == 0:
        return earliest
    texture = _azimuthal_texture(crops, half, interior_radius_px)
    take = max(int(math.floor(quarter * candidates)), 1)
    first = np.argsort(texture, kind="stable")[:take]
    # Second pass: against the template those sites make, which a loaded majority cannot move.
    reference = np.median(crops[first], axis=0)
    texture = np.sqrt(np.mean((crops[:, disk] - reference[disk]) ** 2, axis=1))
    take = max(int(math.floor(quarter * candidates)), 1)
    order = np.argsort(texture, kind="stable")[:take]
    if take < min_sites:
        if earliest is not None:
            return earliest
        source = SOURCE_OWN_SPARSE
    else:
        source = SOURCE_OWN
    return Template(np.median(crops[order], axis=0), half, source, take, candidates, float(texture[order].max()))


# ── C2: the rim-suppressed tile ──────────────────────────────────────────────
def _template_floor(template: Template) -> float:
    return 0.1 * float(np.median(template.array[_disk(template.half, template.half * 0.6)]))


def rim_suppress(image, pixel_size_um: float, template: Template, geometries,
                 *, radius: float = SUPPRESSION_RADIUS) -> np.ndarray:
    """The tile with every site divided by the template and everything else set flat (1.0).

    Inside each site's disk (out to *radius* times its fitted radius, which takes in the rim) a
    pixel is the image over the template at that pixel's offset from the site centre, sampled
    bilinearly. An empty site comes out at about 1 everywhere, rim included; a cell is a
    departure from 1. Pixels outside every site are exactly 1.0, so a segmenter sees cells on a
    flat floor.
    """
    from scipy import ndimage

    image = np.asarray(image, dtype=np.float64)
    h, w = image.shape
    out = np.ones((h, w))
    floor = _template_floor(template)
    for g in geometries:
        cx, cy = g.centre_um[0] / pixel_size_um - 0.5, g.centre_um[1] / pixel_size_um - 0.5
        reach = radius * g.radius_um / pixel_size_um
        x0, x1 = max(int(math.floor(cx - reach)), 0), min(int(math.ceil(cx + reach)) + 1, w)
        y0, y1 = max(int(math.floor(cy - reach)), 0), min(int(math.ceil(cy + reach)) + 1, h)
        if x1 <= x0 or y1 <= y0:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        inside = np.hypot(xx - cx, yy - cy) <= reach
        dx, dy = xx - cx + template.half, yy - cy + template.half
        valid = inside & (dx >= 0) & (dx <= 2 * template.half) & (dy >= 0) & (dy <= 2 * template.half)
        if not valid.any():
            continue
        values = ndimage.map_coordinates(template.array, [dy[valid], dx[valid]], order=1, mode="nearest")
        window = out[y0:y1, x0:x1]
        window[valid] = image[y0:y1, x0:x1][valid] / np.maximum(values, floor)
    return out


# ── C3: confluence without segmentation ──────────────────────────────────────
def _departure(rim_image, smoothing: float = 1.0) -> np.ndarray:
    from scipy import ndimage
    return np.abs(ndimage.gaussian_filter(np.asarray(rim_image, dtype=np.float64) - 1.0, smoothing))


def _interior_pixels(shape, pixel_size_um, g, fraction):
    h, w = shape
    cx, cy = g.centre_um[0] / pixel_size_um - 0.5, g.centre_um[1] / pixel_size_um - 0.5
    reach = fraction * g.radius_um / pixel_size_um
    x0, x1 = max(int(math.floor(cx - reach)), 0), min(int(math.ceil(cx + reach)) + 1, w)
    y0, y1 = max(int(math.floor(cy - reach)), 0), min(int(math.ceil(cy + reach)) + 1, h)
    if x1 <= x0 or y1 <= y0:
        return np.empty(0, dtype=int), np.empty(0, dtype=int)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    keep = np.hypot(xx - cx, yy - cy) <= reach
    return yy[keep], xx[keep]


def texture_threshold(rim_image, pixel_size_um: float, empty_geometries, *, sigmas: float = TEXTURE_SIGMAS,
                      fraction: float = INTERIOR_FRACTION) -> float | None:
    """How much departure from the template is texture an empty nanowell shows by itself.

    The departure (the smoothed absolute difference of the rim-suppressed image from 1) is
    collected over the interiors of the sites known to be empty; the threshold is its median plus
    *sigmas* robust standard deviations (1.4826 times the median absolute deviation). ``None``
    without any empty site: there is then nothing to call texture against.
    """
    departure = _departure(rim_image)
    values = []
    for g in empty_geometries:
        ys, xs = _interior_pixels(departure.shape, pixel_size_um, g, fraction)
        values.append(departure[ys, xs])
    if not values:
        return None
    pooled = np.concatenate(values)
    if not len(pooled):
        return None
    median = float(np.median(pooled))
    return median + sigmas * 1.4826 * float(np.median(np.abs(pooled - median)))


def site_confluence(rim_image, pixel_size_um: float, geometries, threshold: float | None, *,
                    fraction: float = INTERIOR_FRACTION) -> list[float | None]:
    """Per site, the share of its interior whose departure from the template exceeds *threshold*.

    ``None`` for a site with no interior pixel on the image, and for every site when there is no
    threshold. 0.0 is a measurement: an empty nanowell.
    """
    departure = _departure(rim_image)
    out = []
    for g in geometries:
        ys, xs = _interior_pixels(departure.shape, pixel_size_um, g, fraction)
        if threshold is None or not len(ys):
            out.append(None)
        else:
            out.append(float(np.mean(departure[ys, xs] > threshold)))
    return out


# ── C4 / C5: counting ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SiteCount:
    index: int
    n_cells: int
    labels: tuple[int, ...]             # the segmenter's labels assigned to this site


def cellpose_segmenter(diameter_px: float = 0.0, **options):
    """A segmenter for :func:`segment_tile` that calls Cellpose's label-free path.

    Imports ``pipeline.cellpose`` (and so the heavy stack) only when called. ``diameter_px`` is the
    cell size in pixels (0 lets Cellpose use its model's own scale).
    """
    def segment(image):
        from pipeline.cellpose import _segment_cellpose
        return _segment_cellpose(image, diameter=diameter_px, **options)
    return segment


def segment_tile(rim_image, segmenter) -> np.ndarray:
    """One segmenter call on the whole rim-suppressed tile, as the pipeline calls Cellpose today."""
    labels = np.asarray(segmenter(rim_image))
    if labels.shape != np.asarray(rim_image).shape:
        raise ValueError("The segmenter returned a label image of a different shape")
    return labels.astype(np.int64)


def _label_centroids(labels):
    from scipy import ndimage
    ids = np.unique(labels)
    ids = ids[ids > 0]
    if not len(ids):
        return ids, np.empty((0, 2))
    centres = np.array(ndimage.center_of_mass(np.ones_like(labels), labels, ids))        # (y, x)
    return ids, centres[:, ::-1]


def count_cells(labels, pixel_size_um: float, geometries, *, owned=None,
                fraction: float = INTERIOR_FRACTION) -> dict[int, SiteCount]:
    """Cells per site: those whose centroid lies in the interior of an owned site.

    *geometries* is the tile's list of sites (their ``index`` is the site's identity across
    tiles). *owned* is the set of site indices this tile owns (``ownership_report``): a site
    another tile owns is not counted here at all, so a cell in the overlap of two tiles is
    counted by exactly one of them. ``None`` counts every site given, which is right only for
    one tile. A site with no cell returns ``n_cells=0``. Cells straddling the rim, or lying
    between sites, belong to no site and are dropped.
    """
    ids, centroids = _label_centroids(np.asarray(labels))
    counted = {g.index: [] for g in geometries if owned is None or g.index in owned}
    sites = [g for g in geometries if g.index in counted]
    for label, (x, y) in zip(ids, centroids):
        for g in sites:
            cx, cy = g.centre_um[0] / pixel_size_um - 0.5, g.centre_um[1] / pixel_size_um - 0.5
            if math.hypot(x - cx, y - cy) <= fraction * g.radius_um / pixel_size_um:
                counted[g.index].append(int(label))
                break
    return {index: SiteCount(index, len(found), tuple(found)) for index, found in counted.items()}


@dataclass(frozen=True)
class AreaEstimate:
    index: int
    foreground_area_um2: float
    area_count: float | None            # foreground area over the median isolated cell; None if no isolated cell
    count_disagreement: bool | None     # None when the area estimate is undefined


def isolated_cell_area_um2(labels, pixel_size_um: float, geometries, counts: dict, *,
                           fraction: float = INTERIOR_FRACTION) -> float | None:
    """Median area of the cells that touch no other cell, over every counted site; ``None`` if none."""
    from scipy import ndimage

    labels = np.asarray(labels)
    touching = set()
    for label in np.unique(labels[labels > 0]):
        grown = ndimage.binary_dilation(labels == label, iterations=2)
        others = np.unique(labels[grown])
        if any(o not in (0, label) for o in others):
            touching.add(int(label))
    areas = [float(np.sum(labels == label)) * pixel_size_um ** 2
             for count in counts.values() for label in count.labels if label not in touching]
    return float(np.median(areas)) if areas else None


def area_estimate(labels, pixel_size_um: float, geometries, counts: dict, *,
                  fraction: float = INTERIOR_FRACTION, tolerance: float = COUNT_DISAGREEMENT,
                  isolated_area_um2: float | None = None) -> dict[int, AreaEstimate]:
    """The foreground-area count beside the segmented one, and whether they disagree.

    Each site's foreground area is the labelled area inside its interior; dividing by the median
    area of isolated cells gives a count that does not care how a clump was split. They disagree
    when they differ by at least one cell and by more than *tolerance* of the larger: a clump
    segmented as one cell undercounts silently otherwise. With no isolated cell anywhere the area
    count and the flag are ``None``, not zero.
    """
    labels = np.asarray(labels)
    median_area = isolated_area_um2 if isolated_area_um2 is not None else isolated_cell_area_um2(
        labels, pixel_size_um, geometries, counts, fraction=fraction)
    by_index = {g.index: g for g in geometries}
    out = {}
    for index, count in counts.items():
        ys, xs = _interior_pixels(labels.shape, pixel_size_um, by_index[index], fraction)
        area = float(np.sum(labels[ys, xs] > 0)) * pixel_size_um ** 2 if len(ys) else 0.0
        if median_area is None or median_area <= 0:
            out[index] = AreaEstimate(index, area, None, None)
            continue
        estimate = area / median_area
        gap = abs(count.n_cells - estimate)
        out[index] = AreaEstimate(index, area, estimate, bool(gap >= 1.0 and gap > tolerance * max(count.n_cells, estimate)))
    return out


# ── C6: per-cell records ─────────────────────────────────────────────────────
def cell_table(labels, pixel_size_um: float, geometries, counts: dict, addresses: dict, *,
               tile_origin_um=(0.0, 0.0), intensity: dict | None = None) -> dict[str, list]:
    """One row per counted cell: its site, address, well-frame position, area and mean intensities.

    ``addresses`` maps a site index to its ``(q, r)`` or ``None`` (an unaddressed timepoint keeps
    its rows, with no address, so they are not joinable over time). ``x_um``/``y_um`` are in the
    well's frame (the tile's origin plus the centroid). *intensity* maps a column name to an image
    the cells are measured on (the channels the pipeline quantifies today); its columns are
    ``mean_<name>``. Columns are lists, ready for a DataFrame.
    """
    from skimage.measure import regionprops

    labels = np.asarray(labels)
    site_of = {label: index for index, count in counts.items() for label in count.labels}
    columns = {"site_index": [], "address_q": [], "address_r": [], "x_um": [], "y_um": [], "area_um2": [],
               "label": []}
    for name in (intensity or {}):
        columns[f"mean_{name}"] = []
    reference = next(iter(intensity.values())) if intensity else None
    for region in regionprops(labels, intensity_image=reference):
        if region.label not in site_of:
            continue
        index = site_of[region.label]
        address = addresses.get(index)
        cy, cx = region.centroid
        columns["site_index"].append(index)
        columns["address_q"].append(None if address is None else int(address[0]))
        columns["address_r"].append(None if address is None else int(address[1]))
        columns["x_um"].append(float(tile_origin_um[0] + (cx + 0.5) * pixel_size_um))
        columns["y_um"].append(float(tile_origin_um[1] + (cy + 0.5) * pixel_size_um))
        columns["area_um2"].append(float(region.area) * pixel_size_um ** 2)
        columns["label"].append(int(region.label))
        for name, image in (intensity or {}).items():
            columns[f"mean_{name}"].append(float(np.asarray(image, dtype=np.float64)[labels == region.label].mean()))
    return columns


# ── C7: fluorescence ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SiteFluorescence:
    index: int
    integrated: float | None            # sum over the interior minus background x area
    mean_minus_background: float | None
    background: float | None            # the empty sites' median interior mean


def site_fluorescence(image, pixel_size_um: float, geometries, empty_indices, *,
                      fraction: float = INTERIOR_FRACTION) -> list[SiteFluorescence]:
    """Per-site integrated and mean intensity over the interior, minus the empty sites' background.

    Defined wherever the interior is on the image, with or without a segmentation. The
    background is the median interior mean of the sites in *empty_indices*. With no image (a
    timepoint without the channel) every number is ``None``, not zero; with no empty site the
    background and everything derived from it are ``None``.
    """
    if image is None:
        return [SiteFluorescence(g.index, None, None, None) for g in geometries]
    image = np.asarray(image, dtype=np.float64)
    empty = set(empty_indices)
    interior = {}
    for g in geometries:
        ys, xs = _interior_pixels(image.shape, pixel_size_um, g, fraction)
        interior[g.index] = image[ys, xs] if len(ys) else None
    backgrounds = [float(v.mean()) for i, v in interior.items() if i in empty and v is not None]
    background = float(np.median(backgrounds)) if backgrounds else None
    out = []
    for g in geometries:
        values = interior[g.index]
        if values is None or background is None:
            out.append(SiteFluorescence(g.index, None, None, background))
        else:
            out.append(SiteFluorescence(g.index, float(values.sum() - background * len(values)),
                                        float(values.mean() - background), background))
    return out


# ── C8: the state of a site ──────────────────────────────────────────────────
@dataclass(frozen=True)
class SiteState:
    state: str                          # EMPTY, MEASURED, OCCLUDED or CLIPPED
    n_cells: int | None                 # None (undefined) for OCCLUDED and CLIPPED, an integer otherwise
    confluence: float | None
    visible_fraction: float | None
    imaged_fraction: float | None
    partially_visible: bool | None      # measured, but not wholly visible and imaged; None when not measured
    fused: bool
    addressed: bool


def site_state(*, visible_fraction: float | None, imaged_fraction: float | None, n_cells: int | None,
               confluence: float | None, addressed: bool = True, fused: bool = False,
               occluded_below: float = 0.5, clipped_below: float = 0.5) -> SiteState:
    """The per-site state of tracker §3.5, and the contents metrics it may carry.

    ``clipped`` (imaged fraction below *clipped_below*, or off the image) and ``occluded``
    (visible fraction below *occluded_below*) carry **no** contents metrics: ``n_cells`` and
    ``confluence`` are ``None``, whatever was passed, because a count from a site that was not
    seen is not a count. A measured site with no cells is ``empty`` with ``n_cells == 0`` and
    ``confluence == 0.0``: a measurement. The two can therefore never be confused. A site seen only in
    part (above the thresholds, below 1) keeps its metrics with ``partially_visible`` set and
    both fractions on the row; ``fused`` and ``addressed`` are flags beside the state, not states:
    a fused site was measured from a blended crop, and an unaddressed timepoint's sites keep
    their metrics but cannot be joined over time.
    """
    if imaged_fraction is None or imaged_fraction < clipped_below:
        return SiteState(CLIPPED, None, None, visible_fraction, imaged_fraction, None, fused, addressed)
    if visible_fraction is None or visible_fraction < occluded_below:
        return SiteState(OCCLUDED, None, None, visible_fraction, imaged_fraction, None, fused, addressed)
    partial = visible_fraction < 1.0 or imaged_fraction < 1.0
    if n_cells is None:
        # Seen, but no count was made: still undefined, not zero.
        return SiteState(MEASURED, None, confluence, visible_fraction, imaged_fraction, partial, fused, addressed)
    if n_cells == 0:
        return SiteState(EMPTY, 0, 0.0 if confluence is None or confluence == 0 else confluence,
                         visible_fraction, imaged_fraction, partial, fused, addressed)
    return SiteState(MEASURED, int(n_cells), confluence, visible_fraction, imaged_fraction, partial, fused, addressed)
