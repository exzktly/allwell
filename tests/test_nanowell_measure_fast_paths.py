"""The nanowell measuring's fast paths compute exactly what the code they replaced computed, to the last bit.

Measuring a nanowell (``pipeline/nanowell_measure.py``, ``nanowell_sites.rim_focus``) was about 0.12 s of CPU a
nanowell, more than half of each nanowell's time on a GPU node. Each hot spot was rewritten to do less work for the
same numbers: centroids and areas from one ``bincount`` pass instead of ``np.unique`` and a ``regionprops`` object
per cell, the cells that touch another found from the tile's outlines instead of a dilation per cell, the focus
profile from the pixels it reads, the template's crops levelled only where the template uses them, a site's
interior mask kept between the ten calls that ask for it. Every one of them is a claim that nothing changed, and
each claim is held here against the old code, kept verbatim as the reference, on inputs built so that the obvious
wrong rewrite gives a different answer: objects exactly two steps apart (the dilation's reach) and three, counting
circles that overlap (so "the first site" and "a site" differ), signed zeros, infinities, NaN crops, more crops than
the template takes.

The whole well, end to end, is held by the A/B harness the change was made with (byte-identical site rows, cell
rows and grid records on a rendered four-timepoint well); these are the durable part.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import ndimage

from pipeline import nanowell_measure as nm
from pipeline import nanowell_sites as ns
from pipeline.field_quality import edge_focus
from pipeline.nanowell_detect import FITTED, SiteGeometry

PX = 0.65


# ── the code these replaced, verbatim (the reference) ────────────────────────
def _old_label_centroids(labels):
    ids = np.unique(labels)
    ids = ids[ids > 0]
    if not len(ids):
        return ids, np.empty((0, 2))
    centres = np.array(ndimage.center_of_mass(np.ones_like(labels), labels, ids))        # (y, x)
    return ids, centres[:, ::-1]


def _old_count_cells(labels, pixel_size_um, geometries, *, owned=None, fraction=nm.INTERIOR_FRACTION):
    ids, centroids = _old_label_centroids(np.asarray(labels))
    counted = {g.index: [] for g in geometries if owned is None or g.index in owned}
    sites = [g for g in geometries if g.index in counted]
    for label, (x, y) in zip(ids, centroids):
        for g in sites:
            cx, cy = g.centre_um[0] / pixel_size_um - 0.5, g.centre_um[1] / pixel_size_um - 0.5
            if math.hypot(x - cx, y - cy) <= fraction * g.radius_um / pixel_size_um:
                counted[g.index].append(int(label))
                break
    return {index: nm.SiteCount(index, len(found), tuple(found)) for index, found in counted.items()}


def _old_own_labels(labels, win, own, geometries, pixel_size_um):
    others = [g for g in geometries if g.index != own.index]
    if not others or not labels.any():
        return labels
    ids, centroids = _old_label_centroids(labels)
    y0, x0 = win[0].start, win[1].start
    drop = []
    for label, (x, y) in zip(ids, centroids):
        for g in others:
            cx, cy = g.centre_um[0] / pixel_size_um - 0.5, g.centre_um[1] / pixel_size_um - 0.5
            if math.hypot(x + x0 - cx, y + y0 - cy) <= nm.INTERIOR_FRACTION * g.radius_um / pixel_size_um:
                drop.append(label)
                break
    if drop:
        labels = np.where(np.isin(labels, drop), 0, labels)
    return labels


def _old_isolated_cell_area_um2(labels, pixel_size_um, counts):
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


def _old_cell_table(labels, pixel_size_um, geometries, counts, addresses, *, tile_origin_um=(0.0, 0.0),
                    intensity=None):
    from skimage.measure import regionprops

    labels = np.asarray(labels)
    site_of = {label: index for index, count in counts.items() for label in count.labels}
    columns = {"site_index": [], "address_q": [], "address_r": [], "x_um": [], "y_um": [], "area_um2": [],
               "label": []}
    for name in (intensity or {}):
        columns[f"mean_{name}"] = []
    regions = [region for region in regionprops(labels) if region.label in site_of]
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


def _old_rim_focus(image, pixel_size_um, geometry):
    if geometry.geometry != FITTED:
        return None
    h, w = np.shape(image)
    cx, cy = geometry.centre_um[0] / pixel_size_um - 0.5, geometry.centre_um[1] / pixel_size_um - 0.5
    r = geometry.radius_um / pixel_size_um
    half = int(math.ceil(1.6 * r)) + 2
    x0, y0 = int(round(cx)) - half, int(round(cy)) - half
    if x0 < 0 or y0 < 0 or x0 + 2 * half + 1 > w or y0 + 2 * half + 1 > h:
        return None
    patch = np.asarray(np.asarray(image)[y0:y0 + 2 * half + 1, x0:x0 + 2 * half + 1], dtype=np.float64)
    yy, xx = np.mgrid[0:2 * half + 1, 0:2 * half + 1]
    mask = np.hypot(xx + x0 - cx, yy + y0 - cy) <= r
    value = edge_focus(patch, mask)
    return None if value is None else float(value)


def _old_modal_stack(stack, share=nm.MODAL_SHARE, *, min_values=1):
    stack = np.asarray(stack, dtype=np.float64)
    ordered = np.sort(stack, axis=0)
    valid = np.isfinite(stack).sum(axis=0)
    out = np.full(stack.shape[1:], np.nan)
    for n in np.unique(valid):
        n = int(n)
        if n < max(min_values, 1):
            continue
        where = valid == n
        values = ordered[:n, where]
        m = max(int(math.ceil(share * n)), 1)
        if m >= n:
            out[where] = np.median(values, axis=0)
            continue
        start = np.argmin(values[m - 1:] - values[:n - m + 1], axis=0)
        window = np.take_along_axis(values, start[None, :] + np.arange(m)[:, None], axis=0)
        out[where] = np.median(window, axis=0)
    return out


def _old_crop_levels(crops, half):
    import warnings

    crops = np.asarray(crops, dtype=np.float64).reshape(-1, 2 * half + 1, 2 * half + 1)
    yy, xx = np.mgrid[-half:half + 1, -half:half + 1]
    r = np.hypot(xx, yy)
    site = half / (nm.TEMPLATE_HEADROOM * nm.SUPPRESSION_RADIUS)
    floor, surround = crops[:, r <= nm.FLOOR_RADIUS * site], crops[:, r >= nm.SURROUND_RADIUS * site]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.array([nm.modal_level(f) for f in floor]), np.nanmedian(surround, axis=1)


def _old_level_crops(crops, half):
    crops = np.asarray(crops, dtype=np.float64)
    floors, surrounds = _old_crop_levels(crops, half)
    good = np.isfinite(floors) & np.isfinite(surrounds) & (surrounds > 0)
    out = np.full(crops.shape, np.nan)
    if not good.any():
        return out
    ratio = float(np.median(floors[good] / surrounds[good]))
    contrast = surrounds - floors
    two_point = good & (np.abs(contrast) >= nm.MIN_LEVEL_CONTRAST * surrounds) \
        & (abs(1 - ratio) >= nm.MIN_LEVEL_CONTRAST)
    scale_only = good & ~two_point
    out[two_point] = ratio + (1 - ratio) * (crops[two_point] - floors[two_point, None, None]) \
        / contrast[two_point, None, None]
    out[scale_only] = crops[scale_only] / surrounds[scale_only, None, None]
    return out


def _old_build_template(crops, *, interior_radius_px, min_sites=nm.MIN_TEMPLATE_SITES, earliest=None,
                        max_crops=nm.TEMPLATE_MAX_CROPS):
    crops = np.asarray(crops, dtype=np.float64)
    if crops.ndim != 3 or len(crops) == 0:
        return nm.as_fallback(earliest)
    half = crops.shape[1] // 2
    disk = nm._disk(half, interior_radius_px)
    positions = np.flatnonzero(~np.isnan(crops[:, disk]).any(axis=1))
    candidates = len(positions)
    if candidates == 0:
        return nm.as_fallback(earliest)
    if candidates < min_sites:
        if earliest is not None:
            return nm.as_fallback(earliest)
        source = nm.SOURCE_OWN_SPARSE
    else:
        source = nm.SOURCE_OWN
    if candidates > max_crops:
        positions = positions[np.unique(np.linspace(0, candidates - 1, max_crops).round().astype(int))]
    array = _old_modal_stack(crops[positions], min_values=min(min_sites, len(positions)))
    return nm.Template(array, half, source, len(positions), candidates, None, tuple(int(k) for k in positions))


def _old_interior_pixels(shape, pixel_size_um, g, fraction):
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


def _old_rim_suppress(image, pixel_size_um, template, geometries, *, radius=nm.SUPPRESSION_RADIUS, levels=None):
    image = np.asarray(image, dtype=np.float64)
    h, w = image.shape
    out = np.ones((h, w))
    floor = nm._template_floor(template)
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


def _old_disk_pixels(image_shape, centre_px, inner, outer):
    h, w = image_shape
    x0, x1 = max(int(math.floor(centre_px[0] - outer)), 0), min(int(math.ceil(centre_px[0] + outer)) + 1, w)
    y0, y1 = max(int(math.floor(centre_px[1] - outer)), 0), min(int(math.ceil(centre_px[1] + outer)) + 1, h)
    if x1 <= x0 or y1 <= y0:
        return np.empty(0, dtype=int), np.empty(0, dtype=int)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    distance = np.hypot(xx - centre_px[0], yy - centre_px[1])
    keep = (distance >= inner) & (distance <= outer)
    return yy[keep], xx[keep]


# ── fixtures where a wrong rewrite answers differently ───────────────────────
def _labels(rng, shape, dtype=np.int64) -> np.ndarray:
    """Rectangles with gaps in their numbering, some on the tile's edge, and pairs of objects exactly one, two and
    three steps apart, straight and diagonal: two steps is as far as "touching" reaches."""
    h, w = shape
    lab = np.zeros(shape, dtype=np.int64)
    k = 0
    for _ in range(int(rng.integers(20, 60))):
        k += int(rng.integers(1, 4))
        y, x = int(rng.integers(0, h)), int(rng.integers(0, w))
        ry, rx = int(rng.integers(1, 6)), int(rng.integers(1, 6))
        lab[max(y - ry, 0):y + ry, max(x - rx, 0):x + rx] = k
    for dy, dx in ((1, 0), (1, 1), (2, 0), (0, 2), (2, 1), (3, 0), (0, 3)):
        y, x = int(rng.integers(8, h - 16)), int(rng.integers(8, w - 16))
        lab[y:y + 3, x:x + 3] = k + 1
        lab[y + 2 + dy:y + 5 + dy, x + 2 + dx:x + 5 + dx] = k + 2
        k += 2
    return lab.astype(dtype)


def _sites(rng, shape, n) -> list[SiteGeometry]:
    """Sites large enough on a small tile that their counting circles overlap: the first site that holds a cell is
    then not the only one."""
    h, w = shape
    return [SiteGeometry(i, (float(rng.uniform(-10, w * PX + 10)), float(rng.uniform(-10, h * PX + 10))),
                         float(rng.uniform(4, 30)), FITTED) for i in range(n)]


def _overlapping(sites) -> bool:
    for a in sites:
        for b in sites:
            if a.index < b.index and math.dist(a.centre_um, b.centre_um) < (a.radius_um + b.radius_um) * 0.9:
                return True
    return False


@pytest.mark.parametrize("dtype", [np.int64, np.int32, np.uint16])
def test_centroids_from_one_pass_are_center_of_mass_s(dtype):
    rng = np.random.default_rng(1)
    for _ in range(40):
        lab = _labels(rng, (int(rng.integers(40, 140)), int(rng.integers(40, 140))), dtype)
        ids, centres = nm._label_centroids(lab)
        want_ids, want = _old_label_centroids(lab)
        assert np.array_equal(ids, want_ids) and ids.dtype == want_ids.dtype
        assert np.array_equal(centres, want)
    lab = _labels(rng, (60, 60))
    lab[:3, :3] = -4                                 # a negative label is no object, as np.unique's filter had it
    assert np.array_equal(nm._label_centroids(lab)[1], _old_label_centroids(lab)[1])
    empty = np.zeros((5, 5), dtype=np.int64)
    assert len(nm._label_centroids(empty)[0]) == 0 and nm._label_centroids(empty)[1].shape == (0, 2)


def test_counting_and_a_crop_s_own_cells_pick_the_site_the_loop_picked():
    rng = np.random.default_rng(2)
    overlapping = 0
    for trial in range(120):
        shape = (int(rng.integers(40, 140)), int(rng.integers(40, 140)))
        lab = _labels(rng, shape)
        sites = _sites(rng, shape, int(rng.integers(1, 10)))
        overlapping += _overlapping(sites)
        owned = {g.index for g in sites if rng.random() < 0.8}
        assert repr(nm.count_cells(lab, PX, sites, owned=owned)) == repr(_old_count_cells(lab, PX, sites,
                                                                                          owned=owned))
        win = (slice(int(rng.integers(0, 40)), None), slice(int(rng.integers(0, 40)), None))
        assert np.array_equal(nm._own_labels(lab, win, sites[0], sites, PX),
                              _old_own_labels(lab, win, sites[0], sites, PX))
    assert overlapping > 30                          # the fixture did test "first", not merely "any"


def test_isolated_area_is_what_a_dilation_per_cell_found():
    rng = np.random.default_rng(3)
    found = 0
    for trial in range(150):
        shape = (int(rng.integers(40, 140)), int(rng.integers(40, 140)))
        lab = _labels(rng, shape, [np.int64, np.int32, np.uint16][trial % 3])
        sites = _sites(rng, shape, int(rng.integers(1, 10)))
        counts = _old_count_cells(lab, PX, sites)
        want = _old_isolated_cell_area_um2(lab, PX, counts)
        assert nm.isolated_cell_area_um2(lab, PX, counts) == want
        found += want is not None
    assert found > 50


def test_two_steps_touch_and_three_do_not():
    """The pair the dilation's reach decides, alone: what the mutation of ``_WITHIN_TWO_STEPS`` must break."""
    for (dy, dx), touching in (((2, 0), True), ((1, 1), True), ((0, 2), True), ((3, 0), False), ((2, 1), False)):
        lab = np.zeros((30, 30), dtype=np.int64)
        lab[10:13, 10:13] = 1
        lab[12 + dy:15 + dy, 12 + dx:15 + dx] = 2
        assert (1 in nm._touching_labels(lab)) is touching, (dy, dx)
        counts = {0: nm.SiteCount(0, 1, (1,))}
        assert nm.isolated_cell_area_um2(lab, PX, counts) == _old_isolated_cell_area_um2(lab, PX, counts)


def test_the_cell_table_is_regionprops_s():
    rng = np.random.default_rng(4)
    for trial in range(60):
        shape = (int(rng.integers(40, 140)), int(rng.integers(40, 140)))
        lab = _labels(rng, shape)
        sites = _sites(rng, shape, int(rng.integers(1, 10)))
        counts = _old_count_cells(lab, PX, sites)
        addresses = {g.index: None if g.index % 3 == 0 else (g.index, -g.index) for g in sites}
        intensity = {"GFP": rng.random(shape).astype(np.float32) * 100}
        got = nm.cell_table(lab, PX, sites, counts, addresses, tile_origin_um=(3.0, 7.5), intensity=intensity)
        want = _old_cell_table(lab, PX, sites, counts, addresses, tile_origin_um=(3.0, 7.5), intensity=intensity)
        assert repr(got) == repr(want)


def test_area_estimate_is_unchanged():
    rng = np.random.default_rng(5)
    for _ in range(40):
        shape = (int(rng.integers(40, 140)), int(rng.integers(40, 140)))
        lab = _labels(rng, shape)
        sites = _sites(rng, shape, int(rng.integers(1, 10)))
        counts = _old_count_cells(lab, PX, sites)
        want = _old_isolated_cell_area_um2(lab, PX, counts)
        assert repr(nm.area_estimate(lab, PX, sites, counts)) == repr(
            nm.area_estimate(lab, PX, sites, counts, isolated_area_um2=want))


def test_rim_focus_on_the_narrow_patch_is_the_whole_patch_s():
    rng = np.random.default_rng(6)
    measured = 0
    for trial in range(200):
        r_px = float(rng.uniform(4, 60))
        h, w = int(rng.integers(int(3.6 * r_px) + 8, int(3.6 * r_px) + 80)), int(rng.integers(int(3.6 * r_px) + 8,
                                                                                         int(3.6 * r_px) + 80))
        edge = 0.0 if trial % 10 == 0 else 1.7 * r_px + 3               # a tenth may run off the image
        g = SiteGeometry(0, (float(rng.uniform(edge, w - edge)) * PX, float(rng.uniform(edge, h - edge)) * PX),
                         r_px * PX, FITTED)
        cx, cy = g.centre_um[0] / PX - 0.5, g.centre_um[1] / PX - 0.5
        yy, xx = np.mgrid[0:h, 0:w]
        disk = (np.hypot(xx - cx, yy - cy) <= r_px).astype(float)
        image = (rng.normal(100, 5, (h, w)) + 60 * ndimage.gaussian_filter(disk, rng.uniform(0.5, 4))
                 ).astype(np.float32)
        if trial % 5 == 0:
            image[rng.random(image.shape) < 0.01] = np.nan
        want = _old_rim_focus(image, PX, g)
        assert ns.rim_focus(image, PX, g) == want
        measured += want is not None
    assert measured > 120


def _crops(rng, n, half):
    crops = rng.normal(100, 10, (n, 2 * half + 1, 2 * half + 1))
    crops[:, half - 4:half + 3, half - 6:half + 1] += 40                   # a cell in each
    crops = np.round(crops)                                                  # camera counts: ties in the shorth
    crops[1, :, :half // 2] = np.nan                                         # off the image
    crops[2] = np.nan                                                        # no level at all
    crops[3, half + 2:, :] = np.nan
    crops[4, half:half + 2, half:half + 2] = -0.0
    yy, xx = np.mgrid[-half:half + 1, -half:half + 1]
    crops[5][np.hypot(xx, yy) >= 0.8 * half] = np.nan                       # no surround: no level, pixels and all
    return crops.astype(np.float32)


def test_crop_levels_are_each_crop_s_modal_level_and_surround_median():
    rng = np.random.default_rng(7)
    half = 20
    crops = _crops(rng, 30, half)
    for case in (crops, crops.astype(np.float64)):
        got, want = nm.crop_levels(case, half), _old_crop_levels(case, half)
        assert all(np.array_equal(a, b, equal_nan=True) for a, b in zip(got, want))
    infinite = crops.astype(np.float64)
    infinite[6, half, half] = -np.inf                # modal_stack keeps -inf among the values; modal_level drops it
    infinite[7, half, half] = np.inf
    got, want = nm.crop_levels(infinite, half), _old_crop_levels(infinite, half)
    assert all(np.array_equal(a, b, equal_nan=True) for a, b in zip(got, want))


def test_a_crop_with_minus_infinity_takes_modal_level_s_floor():
    """``modal_stack`` sorts -inf in among the values it keeps and so drops the largest finite one; ``modal_level``
    drops the -inf. They differ when the densest window holds the largest value: a tight cluster at the top."""
    half = 20
    yy, xx = np.mgrid[-half:half + 1, -half:half + 1]
    floor = np.hypot(xx, yy) <= nm.FLOOR_RADIUS * half / (nm.TEMPLATE_HEADROOM * nm.SUPPRESSION_RADIUS)
    rng = np.random.default_rng(13)
    crop = np.full((2 * half + 1,) * 2, 100.0)
    k = int(floor.sum())
    # Denser toward the top (a square root's spacing shrinks), so the densest window ends at the largest value.
    values = np.concatenate((rng.uniform(0, 1000, k - k // 3), 2000 + 10 * np.sqrt(np.linspace(0, 1, k // 3))))
    values[0] = -np.inf
    crop[floor] = values
    got, want = nm.crop_levels(crop[None], half), _old_crop_levels(crop[None], half)
    assert np.array_equal(got[0], want[0]) and np.array_equal(got[1], want[1])
    assert nm.modal_stack(crop[floor][:, None]) != want[0]           # the case the guard exists for


def test_modal_stack_takes_the_median_of_the_window_s_middle():
    rng = np.random.default_rng(8)
    for n in (1, 2, 3, 4, 7, 8, 9, 40, 41):
        stack = rng.integers(0, 5, (n, 12, 12)).astype(float)
        stack[stack == 0] = -0.0
        stack[rng.random(stack.shape) < 0.2] = np.nan
        for min_values in (1, 2):
            got, want = nm.modal_stack(stack, min_values=min_values), _old_modal_stack(stack, min_values=min_values)
            assert np.array_equal(got, want, equal_nan=True) and np.array_equal(np.signbit(got), np.signbit(want))


@pytest.mark.parametrize("max_crops", [8, 400])
def test_a_template_from_raw_crops_is_the_template_from_levelled_ones(max_crops):
    """More crops than the template takes (8), and fewer (400): levelling only those it takes changes nothing."""
    rng = np.random.default_rng(9)
    half = 20
    crops = _crops(rng, 40, half)
    radius = nm.SUPPRESSION_RADIUS * half / (nm.TEMPLATE_HEADROOM * nm.SUPPRESSION_RADIUS)
    want = _old_build_template(_old_level_crops(crops, half), interior_radius_px=radius, max_crops=max_crops)
    got = nm.build_template(crops, raw_half=half, interior_radius_px=radius, max_crops=max_crops)
    assert (got.source, got.n_sites, got.candidates, got.used) == (want.source, want.n_sites, want.candidates,
                                                                     want.used)
    assert np.array_equal(got.array, want.array, equal_nan=True)
    assert np.array_equal(np.signbit(got.array), np.signbit(want.array))
    assert np.array_equal(nm.level_crops(crops, half), _old_level_crops(crops, half), equal_nan=True)
    # No crop with a level: every crop NaN, so the fallback, either way.
    earliest = nm.Template(np.ones((2 * half + 1,) * 2), half, nm.SOURCE_OWN, 9, 9, None, ())
    none = np.full_like(crops, np.nan)
    assert repr(nm.build_template(none, raw_half=half, interior_radius_px=radius, earliest=earliest)) == repr(
        _old_build_template(_old_level_crops(none, half), interior_radius_px=radius, earliest=earliest))


def test_an_interior_kept_between_calls_is_the_one_computed_afresh():
    rng = np.random.default_rng(10)
    nm._INTERIORS.clear()
    for _ in range(300):
        shape = (int(rng.integers(30, 200)), int(rng.integers(30, 200)))
        g = SiteGeometry(0, (float(rng.uniform(-5, shape[1] * PX + 5)), float(rng.uniform(-5, shape[0] * PX + 5))),
                         float(rng.uniform(1, 40)), FITTED)
        for _again in range(2):                      # the second call is served from the kept mask
            ys, xs = nm._interior_pixels(shape, PX, g, nm.INTERIOR_FRACTION)
            want = _old_interior_pixels(shape, PX, g, nm.INTERIOR_FRACTION)
            assert np.array_equal(ys, want[0]) and np.array_equal(xs, want[1])
            ys[:] = -1                               # a caller writing into what it was given spoils nothing
    # Two sites with the same window and reach but centres half a pixel apart are two masks, not one: a key of the
    # window alone would hand the second the first's.
    reach = 10.0
    radius_um = reach * PX / nm.INTERIOR_FRACTION
    a = SiteGeometry(0, ((20.1 + 0.5) * PX, (40.3 + 0.5) * PX), radius_um, FITTED)
    b = SiteGeometry(1, ((20.6 + 0.5) * PX, (40.3 + 0.5) * PX), radius_um, FITTED)
    wants = [_old_interior_pixels((200, 200), PX, g, nm.INTERIOR_FRACTION) for g in (a, b)]
    assert not np.array_equal(wants[0][1], wants[1][1])          # the fixture tells the two apart
    for g, want in ((a, wants[0]), (b, wants[1]), (a, wants[0])):
        got = nm._interior_pixels((200, 200), PX, g, nm.INTERIOR_FRACTION)
        assert all(np.array_equal(p, q) for p, q in zip(got, want))
    assert len(nm._INTERIORS) <= nm._INTERIORS_KEPT


def test_rim_suppression_is_the_grid_s():
    rng = np.random.default_rng(11)
    half = 30
    yy, xx = np.mgrid[-half:half + 1, -half:half + 1]
    template = nm.Template(1.0 + 0.3 * (np.hypot(xx, yy) > 18) + 0.01 * rng.random((2 * half + 1,) * 2), half,
                           nm.SOURCE_OWN, 20, 20, None, ())
    for dtype in (np.float32, np.float64, np.uint16):
        image = (rng.random((150, 170)) * 1000).astype(dtype)
        sites = [SiteGeometry(i, (float(rng.uniform(-5, 170 * PX + 5)), float(rng.uniform(-5, 150 * PX + 5))),
                              float(rng.uniform(4, 15)), FITTED) for i in range(12)]
        levels = {g.index: (float(rng.uniform(-5, 5)), float(rng.uniform(0.5, 2))) for g in sites[::2]}
        got = nm.rim_suppress(image, PX, template, sites, levels=levels)
        want = _old_rim_suppress(image, PX, template, sites, levels=levels)
        assert np.array_equal(got, want)


def test_a_disk_s_pixels_are_the_grid_s():
    rng = np.random.default_rng(12)
    for _ in range(200):
        shape = (int(rng.integers(20, 150)), int(rng.integers(20, 150)))
        centre = (float(rng.uniform(-10, shape[1] + 10)), float(rng.uniform(-10, shape[0] + 10)))
        inner = float(rng.uniform(0, 10))
        outer = inner + float(rng.uniform(0, 30))
        got, want = ns._disk_pixels(shape, centre, inner, outer), _old_disk_pixels(shape, centre, inner, outer)
        assert all(np.array_equal(p, q) for p, q in zip(got, want))
