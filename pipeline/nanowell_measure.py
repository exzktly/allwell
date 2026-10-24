"""What is inside each nanowell: label-free first, fluorescence when present.

Qt-free; numpy, scipy and scikit-image only. Phase contrast is always there and
is the primary readout (tracker §3.6). Every nanowell in a well is the same molded
shape, so the phase halo of its rim and the illumination inside it are nearly
identical from site to site; dividing each site by the well's template of an
empty nanowell leaves cells on a flat floor instead of beside a bright ring, which
is the main source of false cells along a rim.

**Nothing here needs an empty nanowell to exist** (tracker L1). A plate seeded at
twenty cells per nanowell has none, and every quantity that was once read from
"the empty ones" is read instead from the level the pixels *crowd around*
(:func:`modal_level`): cells sit in different places in different nanowells and
scatter on both sides of the floor's brightness, while the bare floor is one
tight level, so the densest window of values is the floor's for as long as bare
floor is the largest single population — it need not be the majority.

The order of work:

1. :func:`build_template` takes sub-pixel-aligned crops of the visible sites, each
   levelled by its own floor and surround (:func:`level_crops`), and forms the
   template pixel by pixel from the level the crops crowd around
   (:func:`modal_stack`); with too few crops it falls back to the well's earliest
   template and says so.
2. :func:`site_level` brings each site to the template's levels: a gain from its
   floor-to-surround contrast (its floor is its modal level), the median over its
   neighbours on the same tile (:func:`neighbourhood_median`), and an offset from
   its own surround; :func:`rim_suppress` divides every site's pixels by the
   template and sets everything outside the sites flat.
3. :func:`threshold_from_departures` and :func:`site_confluence` say what fraction
   of a site's interior holds cells, with no segmentation, from texture against the
   bare floor's own texture, measured over every site with the cells clipped away.
4. :func:`count_cells` keeps the cells a segmenter found whose centroid lies in an
   **owned** site's interior, so that a cell in the overlap of two tiles is counted
   once. The segmenter is passed in (production passes Cellpose's label-free path,
   :func:`cellpose_segmenter`), which keeps everything here testable without it.
5. :func:`area_estimate` stores a foreground-area count beside the segmented one and
   flags when they disagree: a clump segmented as one cell undercounts silently.
6. :func:`cell_table` is the per-cell record with the address and the well-frame
   position; :func:`site_fluorescence` the per-site intensity over the floor's
   background (:func:`site_floor_levels`); :func:`site_state` the per-site state of tracker §3.5, in which an
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
#: The share of a set of values the modal level is read over (:func:`modal_level`): the narrowest window holding
#: this share of them. Small enough that bare floor need only be the largest population, not the majority.
MODAL_SHARE = 0.25
#: Fewer usable crops than this and the earliest template is used instead.
MIN_TEMPLATE_SITES = 8
#: At most this many crops make a template, taken evenly through the well (memory: a crop of a 125 um nanowell at
#: 0.69 um per pixel is 247 px square, and every crop is held, sorted, at once).
TEMPLATE_MAX_CROPS = 400
#: Texture is called cell when it exceeds the bare floor's by this many robust standard deviations.
TEXTURE_SIGMAS = 5.0
#: The two counts disagree when they differ by more than this share of the larger, and by at least one cell.
COUNT_DISAGREEMENT = 0.3

#: A template is built to cover a site this much larger than nominal: the site fit accepts a radius up to 30 % off.
TEMPLATE_HEADROOM = 1.3
#: Where a site's two reference levels are read, as multiples of its radius: the floor inside this (cells aside, the
#: nanowell's bottom) and the surround beyond the other (the lattice between nanowells, which never holds cells).
FLOOR_RADIUS = 0.6
SURROUND_RADIUS = 1.2
#: The template's surround must differ from its floor by this share of the surround for an offset to be measurable
#: from a site's floor and surround: two equal levels fix a scale but say nothing about an offset.
MIN_LEVEL_CONTRAST = 0.05
#: The version of how a timepoint is measured: rim suppression, the template, the texture threshold, confluence and
#: counts. It is recorded on every grid record and in ``pipeline_info.json`` (``nanowell_task``), and reuse — an
#: incremental run's kept timepoints, a resume checkpoint, Merge Plates — refuses to mix two. Raise it whenever a change
#: here moves a measured number. 2: each site is brought to the template's levels before it is divided by it, and one
#: texture threshold is taken per timepoint. 3: every site is fitted, owned and measured at the plate's own nanowell
#: diameter, measured on the image (``nanowell_run._tile_diameter``; nanowell tracker F2-F4). 4: nothing is read from
#: empty nanowells (tracker L1): the template is each pixel's modal level over every crop, a site's floor is its modal
#: level, its gain is its floor-to-surround contrast over its same-tile neighbours and its offset comes from its own
#: surround (one offset per tile could not follow light added unevenly across a well), the texture threshold is the
#: floor's texture over every site with the cells clipped away, and the fluorescence background is each tile's
#: floor level. 5: a site's offset is the median of its same-tile neighbours' offsets, read from nanowells only, as
#: its gain already was (``nanowell_run._smoothed_offsets``): one misread surround moved one nanowell's floor alone.
MEASURE_VERSION = 5
#: Below this a departure from the template is division noise, not texture (a flat noise-free tile has a spread of 0).
MIN_TEXTURE_THRESHOLD = 0.02
#: A timepoint's template whose floor-to-surround ratio (:func:`floor_ratio`) has moved by more than a factor of two
#: from the earliest timepoint's is flagged (tracker C12): the template has taken on the cells' colour, which happens
#: once cells, not bare floor, are what most crops show at most pixels (a confluent timepoint). Measured with the
#: least-textured-quarter template this replaced: a 1.6x exposure change 0.002; noise up to 0.15 at most 0.009; a
#: Gaussian defocus of 2.5 px (a fifth of the radius) 0.17 and of 4 px 0.41.
TEMPLATE_DRIFT_LIMIT = math.log(2.0)

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


def template_half(site_radius_px: float) -> int:
    """The crop half-width to build a template with: it must reach every site :func:`rim_suppress` will see.

    The reach is ``SUPPRESSION_RADIUS`` times the radius, and fitted radii run up to ``TEMPLATE_HEADROOM``
    times nominal, so a template built at exactly the nominal reach rejects the first slightly large site.
    """
    return int(math.ceil(SUPPRESSION_RADIUS * TEMPLATE_HEADROOM * site_radius_px)) + 1


def owned_indices(owners, tile: str) -> set[int]:
    """The site indices *tile* owns, from ``OwnershipReport.owners`` (one tile name, or None for fused/unimaged, per site).

    Sites with owner None are in nobody's set: nothing counts them here (fused sites are segmented on
    a blended crop, tracker C14), so their count is absent, which :func:`site_state` reads as undefined.
    The indices are positions in the list the report was made from, so the geometries passed to
    :func:`count_cells` must carry the same indices (all sites of the well, not a per-tile renumbering).
    """
    return {i for i, owner in enumerate(owners) if owner == tile}


def fits_template(template: Template, geometries, *, radius: float = SUPPRESSION_RADIUS, pixel_size_um: float):
    """Split *geometries* into (those the template covers, those it does not): within its crop, and finite out to
    the site's reach.

    :func:`rim_suppress` refuses a site larger than the template, because leaving it flat would read
    as empty; a caller with a mixed list uses this to suppress what fits and to report the rest as
    unmeasured.
    """
    # Covered means every template pixel the site reaches is a number, not only that the crop is big enough: a NaN
    # the median kept beyond the interior made rim_suppress refuse, and so fail, the whole well (roll-up review X24).
    y, x = np.mgrid[-template.half:template.half + 1, -template.half:template.half + 1]
    holes = np.hypot(x, y)[~np.isfinite(template.array)]
    finite_within = float(holes.min()) if holes.size else math.inf
    fit, too_large = [], []
    for g in geometries:
        reach = radius * g.radius_um / pixel_size_um
        (fit if reach <= template.half and reach < finite_within else too_large).append(g)
    return fit, too_large


def _disk(half: int, radius: float) -> np.ndarray:
    y, x = np.mgrid[-half:half + 1, -half:half + 1]
    return np.hypot(x, y) <= radius


@dataclass(frozen=True)
class Template:
    array: np.ndarray                   # (2h+1, 2h+1), an empty nanowell, site-centred
    half: int
    source: str                         # SOURCE_OWN, SOURCE_EARLIEST or SOURCE_OWN_SPARSE
    n_sites: int                        # crops the template was taken over
    candidates: int                     # usable crops it was chosen from
    texture_cut: float | None           # kept for templates saved before tracker L1; None for one built since
    used: tuple[int, ...] = ()          # which of the crops passed in it was taken over


def modal_level(values, share: float = MODAL_SHARE) -> float:
    """The level *values* crowd around: the median of the narrowest window holding *share* of them (a "shorth").

    A nanowell's bare floor is one tight level; cells add values on both sides of it, at different pixels in
    different nanowells. So the densest window sits on the floor for as long as bare floor is the largest single
    population — a median needs it to be the majority, which a nanowell holding twenty cells is not. Non-finite
    values are ignored; NaN when none is left. Ties (quantised camera counts) take the lowest window.
    """
    v = np.asarray(values, dtype=np.float64).ravel()
    v = np.sort(v[np.isfinite(v)])
    n = len(v)
    if n == 0:
        return math.nan
    m = max(int(math.ceil(share * n)), 1)
    if m >= n:
        return float(np.median(v))
    start = int(np.argmin(v[m - 1:] - v[:n - m + 1]))
    return float(np.median(v[start:start + m]))


def modal_stack(stack, share: float = MODAL_SHARE, *, min_values: int = 1) -> np.ndarray:
    """:func:`modal_level` at every pixel of a ``(n, h, w)`` stack, over the finite values there; NaN at a pixel with
    fewer than *min_values* of them."""
    stack = np.asarray(stack, dtype=np.float64)
    ordered = np.sort(stack, axis=0)                     # NaN sorts last
    valid = np.isfinite(stack).sum(axis=0)
    out = np.full(stack.shape[1:], np.nan)
    for n in np.unique(valid):
        n = int(n)
        if n < max(min_values, 1):
            continue
        where = valid == n
        values = ordered[:n, where]                      # (n, k)
        m = max(int(math.ceil(share * n)), 1)
        if m >= n:
            out[where] = np.median(values, axis=0)
            continue
        start = np.argmin(values[m - 1:] - values[:n - m + 1], axis=0)
        window = np.take_along_axis(values, start[None, :] + np.arange(m)[:, None], axis=0)
        out[where] = np.median(window, axis=0)
    return out


def as_fallback(earliest: Template | None) -> Template | None:
    """*earliest* as this timepoint's template: the same pixels, ``source="earliest"`` and no ``used`` crops.

    Handing back the earliest template object unchanged left it saying ``own`` with the earliest timepoint's crop
    indices, so the caller read them as this timepoint's — an ``IndexError`` when there were fewer crops here, and
    the wrong nanowells silently otherwise (found with tracker C16).
    """
    from dataclasses import replace

    return None if earliest is None else replace(earliest, source=SOURCE_EARLIEST, used=())


def build_template(crops, *, interior_radius_px: float, min_sites: int = MIN_TEMPLATE_SITES,
                   earliest: Template | None = None, max_crops: int = TEMPLATE_MAX_CROPS) -> Template | None:
    """The empty-nanowell template: each pixel's modal level (:func:`modal_stack`) over every usable crop.

    *crops* are levelled crops (:func:`level_crops`) of the sites that are visible and imaged (the caller decides
    which); a crop with NaN anywhere in the disk of *interior_radius_px* is skipped here, so pass
    ``SUPPRESSION_RADIUS`` times the site radius: the template must cover what :func:`rim_suppress` divides. No crop
    has to be empty: at each pixel the crops showing bare floor agree and the ones showing a cell scatter, so the
    densest window of values is the floor's as long as cells do not sit at the same place in most nanowells. Up to
    *max_crops* crops are used, taken evenly through the list. With fewer than *min_sites* usable crops (most of the
    well clipped or occluded) *earliest* is returned as it stands, ``source="earliest"``; without one the crops are
    used anyway and marked ``own_sparse``. ``None`` when no crop is usable at all. The template is in site-centred
    coordinates, so it stays valid after the plate is put back and the lattice has moved.
    """
    crops = np.asarray(crops, dtype=np.float64)
    if crops.ndim != 3 or len(crops) == 0:
        return as_fallback(earliest)
    half = crops.shape[1] // 2
    disk = _disk(half, interior_radius_px)
    positions = np.flatnonzero(~np.isnan(crops[:, disk]).any(axis=1))
    candidates = len(positions)
    if candidates == 0:
        return as_fallback(earliest)
    if candidates < min_sites:
        if earliest is not None:
            return as_fallback(earliest)
        source = SOURCE_OWN_SPARSE
    else:
        source = SOURCE_OWN
    if candidates > max_crops:
        positions = positions[np.unique(np.linspace(0, candidates - 1, max_crops).round().astype(int))]
    array = modal_stack(crops[positions], min_values=min(min_sites, len(positions)))
    return Template(array, half, source, len(positions), candidates, None, tuple(int(k) for k in positions))


def save_template(path, template: Template, timepoint: str) -> None:
    """Write *template* (and the timepoint it was built at) to *path*, an ``.npz``, atomically (tracker C16).

    A template object does not survive a run: a checkpoint keeps its summary, and so does a kept timepoint's grid
    block. Kept beside them, a resumed or incremental run can still fall back to the earliest template when a
    later timepoint has too few empty nanowells to build its own.
    """
    import os
    from pathlib import Path

    path = Path(path)
    tmp = path.with_name(path.name + ".tmp.npz")
    np.savez(tmp, array=np.asarray(template.array, dtype=np.float64), half=template.half, source=template.source,
             n_sites=template.n_sites, candidates=template.candidates,
             texture_cut=np.nan if template.texture_cut is None else template.texture_cut,
             used=np.asarray(template.used, dtype=np.int64), timepoint=timepoint)
    os.replace(tmp, path)


def load_template(path) -> tuple[Template, str] | None:
    """``(template, timepoint)`` from :func:`save_template`, or ``None`` when absent or unreadable."""
    from pathlib import Path

    path = Path(path)
    if not path.is_file():
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            cut = float(data["texture_cut"])
            template = Template(np.array(data["array"]), int(data["half"]), str(data["source"]), int(data["n_sites"]),
                                int(data["candidates"]), None if math.isnan(cut) else cut,
                                tuple(int(v) for v in data["used"]))
            return template, str(data["timepoint"])
    except (OSError, ValueError, KeyError):
        return None


def floor_ratio(template: Template) -> float | None:
    """The template's floor over its surround: the median inside 0.6 of the site radius over the median beyond
    1.2 site radii (the lattice floor between nanowells, at the crop's corners). ``None`` when unusable.

    It is what changes when a template is built from loaded nanowells — cells on the floor move the floor's level
    and not the surround's — and what does not change with exposure (a ratio) or focus (both regions are flat,
    away from the rim). The crop's half-width carries 30 % headroom over 1.05 site radii, so the site radius is
    the half-width over 1.365. A number on the timepoint's ``template`` block, so a later run can compare with
    a timepoint an earlier run kept, which a template object cannot be (tracker C12).
    """
    floors, surrounds = crop_levels(np.asarray(template.array, dtype=np.float64)[None], template.half)
    top, bottom = float(floors[0]), float(surrounds[0])
    if not (math.isfinite(top) and math.isfinite(bottom)) or top <= 0 or bottom <= 0:
        return None
    return round(top / bottom, 6)


def crop_levels(crops, half: int) -> tuple[np.ndarray, np.ndarray]:
    """Per crop, ``(floor, surround)``: the modal level (:func:`modal_level`) inside ``FLOOR_RADIUS`` of the site
    radius and the median beyond ``SURROUND_RADIUS`` (the crop's corners), NaN where a region has no number.

    The floor is the modal level, not the median, because a loaded nanowell's floor is mostly cells' pixels at
    twenty cells to a nanowell, and its median is then the cells' level; the surround is the lattice between
    nanowells, which holds no cells, so its median is its level. The crop's half-width carries 30 % headroom over
    1.05 site radii, so the site radius is the half-width over 1.365."""
    crops = np.asarray(crops, dtype=np.float64).reshape(-1, 2 * half + 1, 2 * half + 1)
    yy, xx = np.mgrid[-half:half + 1, -half:half + 1]
    r = np.hypot(xx, yy)
    site = half / (TEMPLATE_HEADROOM * SUPPRESSION_RADIUS)
    floor, surround = crops[:, r <= FLOOR_RADIUS * site], crops[:, r >= SURROUND_RADIUS * site]
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)                # an all-NaN region is a NaN level, by design
        return np.array([modal_level(f) for f in floor]), np.nanmedian(surround, axis=1)


def level_crops(crops, half: int) -> np.ndarray:
    """*crops* mapped onto one shared floor and surround: each crop's own floor to the crops' median floor-to-surround
    ratio and its surround to 1, so neither a tile's gain nor its offset changes how a crop compares with another.

    The template is then taken over crops that agree whatever tile they came from (:func:`build_template`); a
    crop's floor is its modal level, the bare floor's even when cells cover much of it. Where the floor and surround
    are too alike to fix both
    (``MIN_LEVEL_CONTRAST``), and for a crop whose levels are not numbers, each crop is scaled by its surround
    only. A crop with no surround on the image is NaN, and :func:`build_template` skips it.
    """
    crops = np.asarray(crops, dtype=np.float64)
    floors, surrounds = crop_levels(crops, half)
    good = np.isfinite(floors) & np.isfinite(surrounds) & (surrounds > 0)
    out = np.full(crops.shape, np.nan)
    if not good.any():
        return out
    ratio = float(np.median(floors[good] / surrounds[good]))
    contrast = surrounds - floors
    two_point = good & (np.abs(contrast) >= MIN_LEVEL_CONTRAST * surrounds) & (abs(1 - ratio) >= MIN_LEVEL_CONTRAST)
    scale_only = good & ~two_point
    out[two_point] = ratio + (1 - ratio) * (crops[two_point] - floors[two_point, None, None]) \
        / contrast[two_point, None, None]
    out[scale_only] = crops[scale_only] / surrounds[scale_only, None, None]
    return out


def template_levels(template: Template) -> tuple[float, float]:
    """The template's own ``(floor, surround)``: what every site is brought to."""
    floors, surrounds = crop_levels(np.asarray(template.array, dtype=np.float64)[None], template.half)
    return float(floors[0]), float(surrounds[0])


def offset_identifiable(template: Template) -> bool:
    """Whether a site's floor and surround can tell an offset from a gain: the template's two levels must differ by
    ``MIN_LEVEL_CONTRAST`` of its surround. Two equal levels fix a scale and say nothing about an offset."""
    f_t, s_t = template_levels(template)
    return math.isfinite(f_t) and math.isfinite(s_t) and s_t > 0 and abs(s_t - f_t) >= MIN_LEVEL_CONTRAST * s_t


def site_contrast(floor: float, surround: float) -> float | None:
    """A site's surround minus its floor: the template's contrast through the site's gain alone, since light added
    to both cancels. The floor is the site's modal level (:func:`crop_levels`), the bare floor's whether or not the
    nanowell holds cells; ``None`` when either level is not a number."""
    if not (math.isfinite(floor) and math.isfinite(surround)):
        return None
    return float(surround - floor)


def neighbourhood_median(centres_um, values, radius_um: float, groups=None, *, min_neighbours: int = 3) -> np.ndarray:
    """Each site's value as the median of the finite values within *radius_um* of it (its own included), counting
    only sites of its own group (its tile: one exposure, one image) when *groups* is given.

    A site with fewer than *min_neighbours* such values takes the median of its group's; NaN where its group has
    none. One nanowell can mislead — one whose floor is almost all cells has a modal level that is the cells' — but
    the light a tile was taken with changes over many nanowells, so the median over the neighbourhood follows the
    light and outvotes the odd one."""
    from scipy.spatial import cKDTree

    centres = np.asarray(centres_um, dtype=np.float64).reshape(-1, 2)
    values = np.asarray(values, dtype=np.float64)
    groups = np.zeros(len(values), dtype=int) if groups is None else np.asarray(list(groups), dtype=object)
    out = np.full(len(values), np.nan)
    for group in dict.fromkeys(groups.tolist()):
        members = np.flatnonzero(groups == group)
        known = members[np.isfinite(values[members])]
        if not len(known):
            continue
        overall = float(np.median(values[known]))
        tree = cKDTree(centres[known])
        for k, near in zip(members, tree.query_ball_point(centres[members], radius_um)):
            out[k] = float(np.median(values[known[near]])) if len(near) >= min_neighbours else overall
    return out


def site_level(surround: float, contrast: float, template: Template) -> tuple[float, float] | None:
    """``(offset, gain)`` bringing a site to the template's levels, from its own *surround* and a floor-to-surround
    *contrast* (its own, or its neighbourhood's: :func:`neighbourhood_median`); ``None`` when they give no positive
    gain or the template cannot tell an offset from a gain (:func:`offset_identifiable`).

    A site shows the template's floor ``F`` and surround ``S`` as ``g * F + o`` and ``g * S + o``. The contrast
    ``g * (S - F)`` gives the gain whatever light was added, and the surround then gives the offset. The gain is
    not taken from the surround after subtracting an offset: the lattice is dark, so an offset a few hundredths
    wrong made the gain tens of per cent wrong and flattened every cell under added light (tracker L1, measured on
    a rendered well with glare toward its centre)."""
    f_t, s_t = template_levels(template)
    if not all(math.isfinite(v) for v in (surround, contrast, f_t, s_t)) or abs(s_t - f_t) < MIN_LEVEL_CONTRAST * s_t:
        return None
    gain = contrast / (s_t - f_t)
    if not (math.isfinite(gain) and gain > 0):
        return None
    return float(surround - gain * s_t), float(gain)


def site_gain(surround: float, offset: float, template: Template) -> float | None:
    """The gain that brings a site whose surround reads *surround*, at *offset*, to the template's surround;
    ``None`` when it is not a positive number (no surround on the image, or an offset at or above it)."""
    s_t = template_levels(template)[1]
    if not (math.isfinite(surround) and math.isfinite(s_t)) or s_t <= 0:
        return None
    gain = (surround - offset) / s_t
    return gain if math.isfinite(gain) and gain > 0 else None


def ratio_drift(ratio: float | None, earliest: float | None) -> float | None:
    """``|ln(ratio / earliest)|``: how many e-folds the floor-to-surround ratio moved, either way (cells may be
    brighter or darker than the floor). ``None`` when either is missing."""
    if not ratio or not earliest or ratio <= 0 or earliest <= 0:
        return None
    return abs(math.log(ratio / earliest))


def template_drift(template: Template, earliest: Template) -> float | None:
    """:func:`ratio_drift` of two template objects."""
    return ratio_drift(floor_ratio(template), floor_ratio(earliest))


# ── C2: the rim-suppressed tile ──────────────────────────────────────────────
def _template_floor(template: Template) -> float:
    level = float(np.median(template.array[_disk(template.half, template.half * 0.6)]))
    if not math.isfinite(level) or level <= 0:
        raise ValueError(f"The template's central level is {level}; dividing by it is meaningless "
                         "(a background-subtracted image needs its offset restored first)")
    return 0.1 * level


def rim_suppress(image, pixel_size_um: float, template: Template, geometries,
                 *, radius: float = SUPPRESSION_RADIUS, levels: dict | None = None) -> np.ndarray:
    """The tile with every site divided by the template and everything else set flat (1.0).

    *levels* maps a site's index to ``(offset, gain)``: its pixels are taken as ``(value - offset) / gain`` before
    the division, which brings a site on a brighter, dimmer or offset tile to the template's levels (its empty floor
    and its surround then read as the template's, so as 1). A site absent from it is divided as it stands.

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
        reach = radius * g.radius_um / pixel_size_um
        if reach > template.half:
            raise ValueError(f"Site {g.index} reaches {reach:.1f} px but the template covers only {template.half}; "
                             "leaving the rest flat would read as an empty site")
    if not np.isfinite(template.array[_disk(template.half, max(
            (radius * g.radius_um / pixel_size_um for g in geometries), default=0.0))]).all():
        raise ValueError("The template has non-finite values inside the suppression reach; build it with "
                         "interior_radius_px = SUPPRESSION_RADIUS x the site radius")
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
        pixels = image[y0:y1, x0:x1][valid]
        if levels is not None and g.index in levels:
            offset, gain = levels[g.index]
            pixels = (pixels - offset) / gain
        window[valid] = pixels / np.maximum(values, floor)
    return out


# ── C3: confluence without segmentation ──────────────────────────────────────
def _departure(rim_image, smoothing: float = 1.0) -> np.ndarray:
    """Smoothed |rim - 1|; non-finite input pixels are flat (1.0) here so they cannot spread. Callers check
    :func:`_finite_interior` separately, because a flat stand-in must never read as a measurement."""
    from scipy import ndimage
    rim = np.asarray(rim_image, dtype=np.float64)
    rim = np.where(np.isfinite(rim), rim, 1.0)
    return np.abs(ndimage.gaussian_filter(rim - 1.0, smoothing))


def _all_finite(rim_image, pixel_size_um, g, fraction) -> bool:
    ys, xs = _interior_pixels(np.shape(rim_image), pixel_size_um, g, fraction)
    return bool(len(ys)) and bool(np.isfinite(np.asarray(rim_image)[ys, xs]).all())


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


#: A site whose interior is less than this share on the image is not measured (a quadrant is not a site).
MIN_INTERIOR_COVERED = 0.5


def _covered(n_pixels: int, pixel_size_um: float, g, fraction: float) -> bool:
    nominal = math.pi * (fraction * g.radius_um / pixel_size_um) ** 2
    return n_pixels >= MIN_INTERIOR_COVERED * nominal


def interior_imaged(shape, pixel_size_um: float, g, fraction: float = INTERIOR_FRACTION) -> float:
    """The share of a site's interior that lies on an image of *shape* — the pixels the count and the confluence
    read, which is what :func:`site_state` must judge the site by (tracker C15), rather than the disk's share of
    the tiles' rectangles."""
    nominal = math.pi * (fraction * g.radius_um / pixel_size_um) ** 2
    return min(len(_interior_pixels(shape, pixel_size_um, g, fraction)[0]) / nominal, 1.0) if nominal > 0 else 0.0


def texture_threshold(rim_image, pixel_size_um: float, geometries, *, sigmas: float = TEXTURE_SIGMAS,
                      fraction: float = INTERIOR_FRACTION) -> float | None:
    """How much departure from the template is texture the bare floor shows by itself, over *geometries*' interiors
    on one rim-suppressed tile (:func:`site_departures`, :func:`threshold_from_departures`). ``None`` without any
    usable site: there is then nothing to call texture against."""
    return threshold_from_departures(site_departures(rim_image, pixel_size_um, geometries, fraction=fraction),
                                     sigmas=sigmas)[0]


def site_departures(rim_image, pixel_size_um: float, geometries, *, fraction: float = INTERIOR_FRACTION,
                    stride: int = 1) -> list[np.ndarray]:
    """The departure values over the interiors of *geometries* on one rim-suppressed tile, to be pooled across the
    tiles of a timepoint (:func:`threshold_from_departures`); every *stride*-th pixel of each, to bound the memory a
    well of a thousand nanowells takes. A site with a non-finite pixel in its interior gives nothing: a site with a
    hole is not a calibration."""
    departure = _departure(rim_image)
    values = []
    for g in geometries:
        if not _all_finite(rim_image, pixel_size_um, g, fraction):
            continue
        ys, xs = _interior_pixels(departure.shape, pixel_size_um, g, fraction)
        values.append(departure[ys[::stride], xs[::stride]])
    return values


#: While the bare floor's texture is being separated from the cells', values beyond this many robust standard deviations
#: are set aside; the threshold itself is then ``TEXTURE_SIGMAS`` over what is kept. Clipping at five let cells that
#: covered 40 % of the floor pull the threshold to 1.7x the floor's own; three, from the densest values, to 1.16x.
CLIP_SIGMAS = 3.0
#: The most times the kept values are re-chosen (they settle in a handful).
THRESHOLD_ITERATIONS = 50


def threshold_from_departures(values, *, sigmas: float = TEXTURE_SIGMAS) -> tuple[float | None, float | None]:
    """``(threshold, cell share)`` over pooled interior departures of every site: the bare floor's median plus
    *sigmas* robust standard deviations, never under ``MIN_TEXTURE_THRESHOLD``; ``(None, None)`` with no value.

    The values are every site's, cells included, and no site is assumed empty (tracker L1). The floor's departures
    are the densest values near zero, so the search starts from the narrowest window holding a quarter of them
    (:func:`modal_level`'s window, which begins at the floor) and grows: the values within ``CLIP_SIGMAS`` robust
    standard deviations of what is kept are kept, until that stops changing. Cells lie further out and are never
    taken in, so what is kept is the floor's own spread. Measured on a half-normal floor with cells uniform over
    0.05-0.3 (floor sigma 0.03): 4 % above the floor-only threshold at a cell share of 20 %, 15 % at 40 %, 33 % at
    50 %, 67 % at 55 % (where starting from every value instead of the densest gives 5.1x); at 60 % the cells' values
    reach down into the floor's and the threshold is theirs. The *cell share* (the
    fraction of values above the threshold) is returned for the caller to record; one above a half is noted.
    """
    values = [v for v in values if len(v)]
    if not values:
        return None, None
    pooled = np.concatenate(values)
    ordered = np.sort(pooled)
    n = len(ordered)
    m = max(int(math.ceil(MODAL_SHARE * n)), 1)
    start = int(np.argmin(ordered[m - 1:] - ordered[:n - m + 1])) if m < n else 0
    kept = ordered[:start + m]

    def spread_above(sample, k):
        median = float(np.median(sample))
        return median + k * 1.4826 * float(np.median(np.abs(sample - median)))

    for _ in range(THRESHOLD_ITERATIONS):
        grown = ordered[:int(np.searchsorted(ordered, spread_above(kept, CLIP_SIGMAS), side="right"))]
        if len(grown) == len(kept) or not len(grown):
            break
        kept = grown
    threshold = max(spread_above(kept, sigmas), MIN_TEXTURE_THRESHOLD)
    return threshold, float(np.mean(pooled > threshold))


def site_confluence(rim_image, pixel_size_um: float, geometries, threshold: float | None, *,
                    fraction: float = INTERIOR_FRACTION) -> list[float | None]:
    """Per site, the share of its interior whose departure from the template exceeds *threshold*.

    ``None`` for a site with under half its interior on the image or any non-finite pixel in it, and
    for every site when there is no threshold. 0.0 is a measurement: an empty nanowell.
    """
    departure = _departure(rim_image)
    out = []
    for g in geometries:
        ys, xs = _interior_pixels(departure.shape, pixel_size_um, g, fraction)
        if (threshold is None or not _covered(len(ys), pixel_size_um, g, fraction)
                or not _all_finite(rim_image, pixel_size_um, g, fraction)):
            out.append(None)
        else:
            out.append(float(np.mean(departure[ys, xs] > threshold)))
    return out


# ── what the segmenter is shown ──────────────────────────────────────────────
#: The percentiles Cellpose scales an image by before segmenting it: its default normalisation maps the whole
#: image's 1st percentile to 0 and its 99th to 1, so every site on a tile shares one scale.
SEGMENTER_PERCENTILES = (1.0, 99.0)


@dataclass(frozen=True)
class SegmenterView:
    """One site as the segmenter receives it, on the 0-1 scale it normalises the whole tile to.

    ``floor`` is the median of the site's interior there (the bare floor of a correctly levelled site sits where
    the flat 1.0 between sites does); ``contrast`` is the spread between the interior's 1st and 99th percentiles,
    the share of the segmenter's range the site's cells occupy. Both are ``None`` when they cannot be read: no
    interior on the image, a non-finite pixel in it, or a tile with no spread at all."""
    index: int
    floor: float | None
    contrast: float | None


def segmenter_view(rim_image, pixel_size_um: float, geometries, *,
                   fraction: float = INTERIOR_FRACTION) -> dict[int, SegmenterView]:
    """Each site's interior on the segmenter's own scale (:class:`SegmenterView`), read from *rim_image* exactly as
    :func:`segment_tile` passes it.

    The point is to make a site the segmenter could not see visible in the output: two neighbouring nanowells
    whose cells look alike in the raw image can reach the segmenter at very different contrast once each has been
    levelled and divided by the template, and a site whose cells occupy a few per cent of the tile's range is one
    whose zero count says nothing about its cells. The tile's percentiles are taken over the whole image, flat
    1.0 between sites included, because that is what the segmenter scales."""
    rim = np.asarray(rim_image, dtype=np.float64)
    finite = rim[np.isfinite(rim)]
    low, high = (np.percentile(finite, SEGMENTER_PERCENTILES) if finite.size else (math.nan, math.nan))
    span = float(high - low)
    out = {}
    for g in geometries:
        ys, xs = _interior_pixels(rim.shape, pixel_size_um, g, fraction)
        values = rim[ys, xs]
        if not len(values) or not np.isfinite(values).all() or not (math.isfinite(span) and span > 0):
            out[g.index] = SegmenterView(g.index, None, None)
            continue
        p_low, p_high = np.percentile(values, SEGMENTER_PERCENTILES)
        out[g.index] = SegmenterView(g.index, float((np.median(values) - low) / span),
                                     float((p_high - p_low) / span))
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
    cell size in pixels (0 lets Cellpose use its model's own scale); *options* go to
    ``pipeline.cellpose._segment_cellpose`` (``model_type``: ``nanowell_run.CELLPOSE_MODELS``).
    """
    def segment(image, lowhigh=None):
        from pipeline.cellpose import _segment_cellpose
        return _segment_cellpose(image, diameter=diameter_px, lowhigh=lowhigh, **options)

    def segment_many(images, lowhigh):
        from pipeline.cellpose import _segment_cellpose_many
        return _segment_cellpose_many(images, diameter=diameter_px, lowhigh=lowhigh, **options)
    segment.accepts_range = True              # segment_sites hands it the tile's scale for each crop
    segment.segment_many = segment_many       # and several crops at once (CROP_BATCH)
    return segment


#: What the segmenter is given (``NanowellSettings.segment_on``). ``raw``: the tile as it was imaged, which is what
#: Cellpose's models — Cellpose-SAM's among them — were trained on; a cell on a rim is still not counted, because
#: :func:`count_cells` keeps only a centroid inside a nanowell's interior. ``rim``: the rim-suppressed tile
#: (:func:`rim_suppress`), every nanowell divided by the empty-nanowell template and everything between them flat,
#: which takes the rim out of the segmenter's way but is a ratio image no model was trained on, scaled by its own
#: 1st-99th percentiles so a near-flat empty nanowell's noise is stretched across the segmenter's range.
SEGMENT_ON = ("raw", "rim")


def segmenter_input(image, rim_image, segment_on: str) -> np.ndarray:
    """The image the segmenter is given for one tile: *image* as imaged (``raw``) or *rim_image* (``rim``)."""
    if segment_on == "raw":
        return np.asarray(image, dtype=np.float64)
    if segment_on == "rim":
        return np.asarray(rim_image)
    raise ValueError(f"Unknown segmenter input {segment_on!r}; one of {SEGMENT_ON}")


#: How far a nanowell's crop reaches from its centre, in its radii (:func:`segment_sites`): its rim and a little of
#: the lattice beyond, so a cell against the rim is whole in the crop; the count only keeps a cell whose centre lies
#: inside the interior (``INTERIOR_FRACTION``).
CROP_REACH = 1.2
#: How many nanowell crops :func:`segment_sites` gives a segmenter that takes several at once (``segment_many``).
#: Cellpose runs 8 images' network tiles in each pass; 32 keeps a progress tick every few seconds on a slow GPU.
CROP_BATCH = 32


def segment_sites(image, pixel_size_um: float, geometries, segmenter, *, reach: float = CROP_REACH,
                  on_site=None) -> np.ndarray:
    """One segmenter call per nanowell on a crop around it, pasted into a label image of the whole tile.

    The segmenter's work is the pixels it is given, so on a tile where the lattice between nanowells is most of the
    area, segmenting only the nanowells is that much less work. Each crop is scaled as the whole tile would be: a
    segmenter that takes a range (``accepts_range``, Cellpose's) is given the tile's own 1st-99th percentiles
    (``SEGMENTER_PERCENTILES``), because a crop scaled to its own range would stretch an empty nanowell's noise across
    the segmenter's range. A segmenter that also takes several crops at once (``segment_many``, Cellpose's) is given
    them ``CROP_BATCH`` at a time, so they share its network passes; each crop's labels are still its own. Labels are
    renumbered so no two crops share one; where two crops overlap, the first label to a pixel keeps it. *on_site*,
    when given, is called with no argument after each nanowell is segmented, so a run can say how far through a
    tile's nanowells it is."""
    image = np.asarray(image)
    h, w = image.shape
    finite = image[np.isfinite(image)]
    lowhigh = (tuple(float(v) for v in np.percentile(finite, SEGMENTER_PERCENTILES))
               if finite.size and getattr(segmenter, "accepts_range", False) else None)
    windows = [win for g in geometries if (win := crop_window(g, pixel_size_um, (h, w), reach)) is not None]
    many = getattr(segmenter, "segment_many", None) if lowhigh is not None else None
    batch = CROP_BATCH if many is not None else 1
    out = np.zeros((h, w), dtype=np.int64)
    next_id = 0
    for start in range(0, len(windows), batch):
        chunk = windows[start:start + batch]
        crops = [image[win] for win in chunk]
        if many is not None:
            found = many(crops, lowhigh)
        else:
            found = [segmenter(c) if lowhigh is None else segmenter(c, lowhigh=lowhigh) for c in crops]
        for win, crop, labels in zip(chunk, crops, found):
            labels = np.asarray(labels)
            if labels.shape != crop.shape:
                raise ValueError("The segmenter returned a label image of a different shape")
            labels = labels.astype(np.int64)
            window = out[win]
            free = (labels > 0) & (window == 0)
            window[free] = labels[free] + next_id
            next_id += int(labels.max()) if labels.size else 0
            if on_site is not None:
                on_site()
    return out


def crop_window(g, pixel_size_um: float, shape, reach: float = CROP_REACH) -> tuple[slice, slice] | None:
    """The ``(rows, cols)`` of a tile of *shape* that :func:`segment_sites` crops around site *g*, clipped to the
    tile; ``None`` when nothing of it is inside."""
    h, w = shape
    cx, cy = g.centre_um[0] / pixel_size_um - 0.5, g.centre_um[1] / pixel_size_um - 0.5
    half = reach * g.radius_um / pixel_size_um
    x0, x1 = max(int(math.floor(cx - half)), 0), min(int(math.ceil(cx + half)) + 1, w)
    y0, y1 = max(int(math.floor(cy - half)), 0), min(int(math.ceil(cy + half)) + 1, h)
    return (slice(y0, y1), slice(x0, x1)) if x1 > x0 and y1 > y0 else None


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
    tiles). *owned* is the set of site indices this tile owns (:func:`owned_indices` of ``ownership_report``): a site
    another tile owns is not counted here at all, so a cell in the overlap of two tiles is
    counted by exactly one of them. ``None`` counts every site given, which is right only for
    one tile. A site with no cell returns ``n_cells=0``. Cells straddling the rim, or lying
    between sites, belong to no site and are dropped.
    """
    known = {g.index for g in geometries}
    missing = sorted(set(owned or ()) - known)
    if missing:
        raise ValueError(f"Owned sites {missing} have no geometry in this tile; "
                         "they cannot be counted, and omitting them would read as undefined")
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


def isolated_cell_area_um2(labels, pixel_size_um: float, counts: dict) -> float | None:
    """Median area of the counted cells that touch no other cell and no image edge; ``None`` if none.

    A cell cut by the tile edge is too small to stand for a cell, and one touching another may be
    half a clump. A clump that touches nothing still passes as one cell: contact cannot tell it
    from a large cell, so where clumps predominate the area count inherits their undercount.
    """
    from scipy import ndimage

    labels = np.asarray(labels)
    h, w = labels.shape
    boxes = ndimage.find_objects(labels)
    areas = []
    for count in counts.values():
        for label in count.labels:
            box = boxes[label - 1] if 0 < label <= len(boxes) else None
            if box is None:
                continue
            y0, y1 = max(box[0].start - 3, 0), min(box[0].stop + 3, h)
            x0, x1 = max(box[1].start - 3, 0), min(box[1].stop + 3, w)
            if box[0].start == 0 or box[1].start == 0 or box[0].stop == h or box[1].stop == w:
                continue
            window = labels[y0:y1, x0:x1]
            grown = ndimage.binary_dilation(window == label, iterations=2)
            if any(o not in (0, label) for o in np.unique(window[grown])):
                continue
            areas.append(float(np.sum(window == label)) * pixel_size_um ** 2)
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
        labels, pixel_size_um, counts)
    by_index = {g.index: g for g in geometries}
    out = {}
    unknown = sorted(set(counts) - set(by_index))
    if unknown:
        raise ValueError(f"Counted sites {unknown} have no geometry")
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
    from scipy import ndimage
    from skimage.measure import regionprops

    labels = np.asarray(labels)
    site_of = {label: index for index, count in counts.items() for label in count.labels}
    columns = {"site_index": [], "address_q": [], "address_r": [], "x_um": [], "y_um": [], "area_um2": [],
               "label": []}
    for name in (intensity or {}):
        columns[f"mean_{name}"] = []
    regions = [region for region in regionprops(labels) if region.label in site_of]
    # Every cell's mean on every channel in one labelled pass per channel, rather than a full-tile mask per cell
    # per channel — that was ~18 ms a cell, a quarter of an hour a timepoint on a dense well (roll-up review X22).
    kept = [region.label for region in regions]
    means = {name: ndimage.mean(np.asarray(image, dtype=np.float64), labels=labels, index=kept) if kept else []
             for name, image in (intensity or {}).items()}
    for position, region in enumerate(regions):
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
        for name in (intensity or {}):
            columns[f"mean_{name}"].append(float(means[name][position]))
    return columns


# ── C7: fluorescence ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SiteFluorescence:
    index: int
    integrated: float | None            # sum over the interior minus background x area
    mean_minus_background: float | None
    background: float | None            # the floor's level the numbers are taken over


def site_floor_levels(image, pixel_size_um: float, geometries, *, fraction: float = INTERIOR_FRACTION) -> dict:
    """Per site index, the modal level (:func:`modal_level`) of its interior on *image*: the floor's, with or without
    cells on it. A site under half on the image, or with a non-finite pixel in its interior, is left out."""
    image = np.asarray(image, dtype=np.float64)
    out = {}
    for g in geometries:
        ys, xs = _interior_pixels(image.shape, pixel_size_um, g, fraction)
        values = image[ys, xs]
        if _covered(len(ys), pixel_size_um, g, fraction) and np.isfinite(values).all():
            out[g.index] = modal_level(values)
    return out


def site_fluorescence(image, pixel_size_um: float, geometries, *, background: float | None,
                      fraction: float = INTERIOR_FRACTION) -> list[SiteFluorescence]:
    """Per-site integrated and mean intensity over the interior, minus *background*.

    *background* is the floor's level on this tile: the median of its sites' :func:`site_floor_levels`, from the
    caller, which needs no empty nanowell (tracker L1). Defined wherever at least half the interior is on the image
    and every pixel of it is a number (``None`` otherwise), with or without a segmentation. With no image (a
    timepoint without the channel) every number is ``None``, not zero; with no *background* it and everything
    derived from it are ``None``.
    """
    if image is None:
        return [SiteFluorescence(g.index, None, None, None) for g in geometries]
    image = np.asarray(image, dtype=np.float64)
    out = []
    for g in geometries:
        ys, xs = _interior_pixels(image.shape, pixel_size_um, g, fraction)
        values = image[ys, xs]
        usable = _covered(len(ys), pixel_size_um, g, fraction) and np.isfinite(values).all()
        if not usable or background is None:
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


def _finite_or_none(value):
    return None if value is None or not math.isfinite(value) else float(value)


def site_state(*, visible_fraction: float | None, imaged_fraction: float | None, n_cells: int | None,
               confluence: float | None, addressed: bool = True, fused: bool = False,
               occluded_below: float = 0.5, clipped_below: float = 0.5,
               interior_imaged: float | None = None) -> SiteState:
    """The per-site state of tracker §3.5, and the contents metrics it may carry.

    ``clipped`` (imaged fraction below *clipped_below*, or off the image) and ``occluded``
    (visible fraction below *occluded_below*) carry **no** contents metrics: ``n_cells`` and
    ``confluence`` are ``None``, whatever was passed, because a count from a site that was not
    seen is not a count. A measured site with no cells is ``empty`` with ``n_cells == 0`` and the confluence it was
    given (0.0 when measured, ``None`` when no confluence was: it is never filled in). A
    non-finite fraction counts as unknown, and an unknown imaged fraction is ``clipped``, an
    unknown visible one ``occluded``. The two can therefore never be confused. A site seen only in
    part (above the thresholds, below 1) keeps its metrics with ``partially_visible`` set and
    both fractions on the row; ``fused`` and ``addressed`` are flags beside the state, not states:
    a fused site was measured from a blended crop, and an unaddressed timepoint's sites keep
    their metrics but cannot be joined over time.

    *interior_imaged* is the share of the interior on the image the contents were read from
    (:func:`interior_imaged`). ``imaged_fraction`` is measured on the tiles' rectangles in um and the
    contents on image pixels, so near an image edge the two can disagree; when the contents could not
    see ``MIN_INTERIOR_COVERED`` of the interior the site is ``clipped`` whatever the rectangles say —
    otherwise a count of zero from a half-seen interior would read as an empty nanowell (tracker C15).
    """
    visible_fraction = _finite_or_none(visible_fraction)
    imaged_fraction = _finite_or_none(imaged_fraction)
    if (imaged_fraction is None or imaged_fraction < clipped_below
            or (interior_imaged is not None and interior_imaged < MIN_INTERIOR_COVERED)):
        return SiteState(CLIPPED, None, None, visible_fraction, imaged_fraction, None, fused, addressed)
    if visible_fraction is None or visible_fraction < occluded_below:
        return SiteState(OCCLUDED, None, None, visible_fraction, imaged_fraction, None, fused, addressed)
    partial = visible_fraction < 1.0 or imaged_fraction < 1.0
    if n_cells is None:
        # Seen, but no count was made: still undefined, not zero.
        return SiteState(MEASURED, None, confluence, visible_fraction, imaged_fraction, partial, fused, addressed)
    if n_cells == 0:
        return SiteState(EMPTY, 0, confluence, visible_fraction, imaged_fraction, partial, fused, addressed)
    return SiteState(MEASURED, int(n_cells), confluence, visible_fraction, imaged_fraction, partial, fused, addressed)
