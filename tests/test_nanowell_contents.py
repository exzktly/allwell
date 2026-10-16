"""Section C: what is inside each nanowell (tracker §3.5, §3.6).

The scene is a small painted lattice (own renderer, 1 um per pixel) so every number a test
asserts is known from construction: which sites hold how many cells, where.
"""

import numpy as np
import pytest

from pipeline import nanowell_measure as nm
from pipeline.nanowell_detect import FITTED, SiteGeometry

PX = 1.0
RADIUS = 12.0
PITCH = 40.0
FLOOR, RIM, BACKGROUND = 0.15, 0.9, 0.35
CELL = 0.55                              # a cell's brightness over the floor


def _centres(n=4, margin=30.0):
    return [(margin + PITCH * c, margin + PITCH * r) for r in range(n) for c in range(n)]


def _geometries(centres):
    return [SiteGeometry(i, (x, y), RADIUS, FITTED, 0.2, 64) for i, (x, y) in enumerate(centres)]


def _paint(centres, cells=None, *, size=190, seed=0, noise=0.004, shading=0.0):
    """Nanowell lattice image; *cells* maps site index -> list of (dx, dy, radius) offsets in px."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    img = np.full((size, size), BACKGROUND)
    for x, y in centres:
        r = np.hypot(xx + 0.5 - x, yy + 0.5 - y)
        img[r <= RADIUS] = FLOOR
        img[(r <= RADIUS) & (r > RADIUS - 2.0)] = RIM
    for index, blobs in (cells or {}).items():
        x, y = centres[index]
        for dx, dy, rad in blobs:
            img[np.hypot(xx + 0.5 - x - dx, yy + 0.5 - y - dy) <= rad] += CELL
    from scipy import ndimage

    img = ndimage.gaussian_filter(img, 1.2)                  # optics: no real rim is a one-pixel step
    img += shading * (xx / size)
    return img + rng.normal(0, noise, img.shape)


def _threshold_segmenter(level=2.5):
    """A stand-in for Cellpose: connected components of the rim-suppressed image above *level*."""
    from scipy import ndimage

    def segment(rim_image):
        labels, _ = ndimage.label(np.asarray(rim_image) > level)
        return labels
    return segment


CENTRES = _centres()
GEOMS = _geometries(CENTRES)
LOADED = {i: [(-3, 0, 3), (3, 2, 3)] for i in range(16) if i % 2 == 0}        # 8 loaded of 16
SINGLE = {1: [(0, 0, 3)], 5: [(2, 2, 3)], 6: [(-4, 2, 3), (4, -3, 3)], 9: [(0, 4, 3)]}


def _crops(image, half=16):
    return nm.site_crops(image, PX, CENTRES, half)


# ── C1 ──────────────────────────────────────────────────────────────────────
def test_template_ignores_loaded_sites_that_a_plain_median_would_absorb():
    # 12 of 16 sites are so full (5 cells of radius 5) that over half of each pixel's sites hold a
    # cell: the plain median is then cell-coloured; the least-textured quarter is the 4 empty sites.
    rng = np.random.default_rng(3)
    cells = {i: [(rng.uniform(-6, 6), rng.uniform(-6, 6), 5) for _ in range(5)] for i in range(12)}
    crops = _crops(_paint(CENTRES, cells))
    template = nm.build_template(crops, interior_radius_px=RADIUS, min_sites=2)
    plain = np.median(crops, axis=0)
    inside = nm._disk(template.half, 0.9 * RADIUS)
    assert template.source == nm.SOURCE_OWN and template.n_sites == 4 and template.candidates == 16
    assert np.abs(template.array[inside] - FLOOR).mean() < 0.15
    assert np.abs(plain[inside] - FLOOR).mean() > 0.3


def test_template_is_not_fooled_by_a_cell_at_the_same_place_in_every_loaded_site():
    # Loaded sites all hold one cell dead centre: a majority-median reference would call THEM the template.
    cells = {i: [(2, 2, 4)] for i in range(12)}
    template = nm.build_template(_crops(_paint(CENTRES, cells)), interior_radius_px=RADIUS, min_sites=2)
    assert template.array[template.half + 2, template.half + 2] == pytest.approx(FLOOR, abs=0.03)


def test_a_centred_round_cell_is_not_taken_for_an_empty_site():
    # Radially symmetric, so the first (azimuthal) ranking cannot see it and puts it first; the second
    # pass, against the template the others make, drops it for the next empty site.
    rng = np.random.default_rng(1)
    y, x = np.mgrid[-16:17, -16:17]
    base = np.where(np.hypot(x, y) <= 12, FLOOR, BACKGROUND)
    bump = 0.4 * (np.rint(np.hypot(x, y)) <= 4)
    crops = [base + rng.normal(0, 0.002, base.shape) for _ in range(5)]
    crops.insert(0, base + bump + rng.normal(0, 0.0003, base.shape))                          # index 0
    crops += [base + 0.4 * (np.hypot(x - 7, y + 5) <= 4) + rng.normal(0, 0.002, base.shape) for _ in range(10)]
    template = nm.build_template(np.array(crops), interior_radius_px=RADIUS, min_sites=2)
    assert 0 not in template.used and set(template.used) <= set(range(1, 6)) and len(template.used) == 4


def test_template_falls_back_to_the_earliest_and_says_so():
    crops = _crops(_paint(CENTRES))
    earliest = nm.build_template(crops, interior_radius_px=RADIUS, min_sites=2)
    sparse = nm.build_template(crops[:6], interior_radius_px=RADIUS, earliest=earliest)      # 1 site in a quarter
    # The earliest's pixels, saying so, and with none of its crop indices: they index another timepoint's crops.
    assert sparse.source == nm.SOURCE_EARLIEST and sparse.used == ()
    assert np.array_equal(sparse.array, earliest.array) and earliest.source == nm.SOURCE_OWN
    own = nm.build_template(crops[:6], interior_radius_px=RADIUS)
    assert own.source == nm.SOURCE_OWN_SPARSE and own.n_sites == 1
    assert nm.build_template(np.empty((0, 33, 33)), interior_radius_px=RADIUS) is None


def test_template_skips_sites_cut_by_the_image_edge():
    crops = _crops(_paint(CENTRES, size=190), half=16)
    crops[0, 16, 16] = np.nan                      # the site's own centre pixel
    template = nm.build_template(crops, interior_radius_px=RADIUS, min_sites=2)
    assert template.candidates == 15 and not np.isnan(template.array).any()


def test_crops_line_up_whatever_the_subpixel_position():
    shifted = [(x + 0.4, y - 0.3) for x, y in CENTRES]
    crops = nm.site_crops(_paint(shifted, noise=0.0), PX, shifted, 16)
    assert np.nanmax(np.abs(crops - crops[0])) < 0.1       # one blur-free profile per site, ring included


# ── C2 ──────────────────────────────────────────────────────────────────────
def test_rim_suppression_flattens_empty_sites_and_leaves_cells_standing():
    image = _paint(CENTRES, SINGLE)
    template = nm.build_template(_crops(image), interior_radius_px=RADIUS, min_sites=2)
    rim = nm.rim_suppress(image, PX, template, GEOMS)
    empty = [g for g in GEOMS if g.index not in SINGLE][:6]
    for g in empty:
        ys, xs = nm._interior_pixels(rim.shape, PX, g, 1.0)
        assert np.abs(rim[ys, xs] - 1.0).max() < 0.2            # the rim is divided out too
    x, y = CENTRES[1]
    assert rim[int(y), int(x)] > 1.5                            # a cell: 0.7 over a 0.15 floor
    assert rim[0, 0] == 1.0 and rim[-1, -1] == 1.0              # outside every site is flat


def test_the_raw_rim_would_be_read_as_cells_without_suppression():
    image = _paint(CENTRES)
    template = nm.build_template(_crops(image), interior_radius_px=RADIUS, min_sites=2)
    rim = nm.rim_suppress(image, PX, template, GEOMS)
    labels = _threshold_segmenter(1.5)(rim)
    assert labels.max() == 0
    assert (_threshold_segmenter(0.6)(image).max()) > 0         # the same level on the raw image finds the rings


# ── C3 ──────────────────────────────────────────────────────────────────────
def test_confluence_tracks_the_painted_coverage():
    big = {2: [(0, 0, 4)], 3: [(0, 0, 6)], 7: [(0, 0, 8)]}
    image = _paint(CENTRES, big)
    template = nm.build_template(_crops(image), interior_radius_px=RADIUS, min_sites=2)
    rim = nm.rim_suppress(image, PX, template, GEOMS)
    empties = [g for g in GEOMS if g.index not in big]
    cut = nm.texture_threshold(rim, PX, empties)
    conf = nm.site_confluence(rim, PX, GEOMS, cut)
    interior = np.pi * (0.9 * RADIUS) ** 2
    for index, rad in ((2, 4), (3, 6), (7, 8)):
        # "Departs from an empty nanowell" includes the optical halo, so it reads high: bounded below by the
        # painted coverage and above by the coverage of a disk 3 px (about 2.5 sigma of the blur) larger.
        assert np.pi * rad ** 2 / interior <= conf[index] <= np.pi * (rad + 3) ** 2 / interior
    assert conf[2] < conf[3] < conf[7]
    assert all(conf[i] == 0.0 for i in range(16) if i not in big)       # 0.0 is a measurement


def test_confluence_is_undefined_without_empties_or_without_interior():
    rim = np.ones((190, 190))
    assert nm.texture_threshold(rim, PX, []) is None
    assert nm.site_confluence(rim, PX, GEOMS[:2], None) == [None, None]
    off = [SiteGeometry(0, (900.0, 900.0), RADIUS, FITTED)]
    assert nm.site_confluence(rim, PX, off, 0.1) == [None]


# ── C4 / C5 ─────────────────────────────────────────────────────────────────
def _count(image, owned=None):
    template = nm.build_template(_crops(image), interior_radius_px=RADIUS, min_sites=2)
    rim = nm.rim_suppress(image, PX, template, GEOMS)
    labels = nm.segment_tile(rim, _threshold_segmenter())
    return labels, nm.count_cells(labels, PX, GEOMS, owned=owned)


def test_counts_are_exact_for_separated_cells_and_zero_is_reported_as_zero():
    labels, counts = _count(_paint(CENTRES, SINGLE))
    assert {i: c.n_cells for i, c in counts.items()} == {i: len(SINGLE.get(i, ())) for i in range(16)}


def test_segmenter_shape_is_checked():
    with pytest.raises(ValueError):
        nm.segment_tile(np.ones((5, 5)), lambda image: np.zeros((4, 4)))


def test_a_touching_clump_undercounts_and_the_area_estimate_flags_it():
    # Site 6: nine cells fused into one blob; every other loaded site holds isolated cells.
    clump = [(dx, dy, 3) for dx in (-4, 0, 4) for dy in (-4, 0, 4)]
    cells = {1: [(0, 0, 3)], 2: [(0, 0, 3)], 5: [(0, 0, 3)], 6: clump}
    labels, counts = _count(_paint(CENTRES, cells))
    assert counts[6].n_cells == 1                            # the threshold segmenter merges them
    est = nm.area_estimate(labels, PX, GEOMS, counts)
    assert est[6].area_count > 2.0 and est[6].count_disagreement is True
    assert all(est[i].count_disagreement is False for i in (1, 2, 5))
    assert est[0].count_disagreement is False                # 0 cells, 0 area: they agree


def test_a_sub_cell_area_gap_is_not_a_disagreement():
    # One cell counted, half a cell of area: a 50 % gap, but less than one cell, so no flag.
    labels = np.zeros((190, 190), dtype=int)
    x, y = CENTRES[0]
    labels[int(y) - 3:int(y) + 4, int(x) - 3:int(x) + 4] = 1                  # 49 px
    counts = nm.count_cells(labels, PX, GEOMS)
    est = nm.area_estimate(labels, PX, GEOMS, counts, isolated_area_um2=98.0)
    assert est[0].area_count == pytest.approx(0.5) and est[0].count_disagreement is False


def test_area_estimate_is_undefined_not_zero_without_an_isolated_cell():
    labels, counts = _count(_paint(CENTRES))
    est = nm.area_estimate(labels, PX, GEOMS, counts)
    assert all(e.area_count is None and e.count_disagreement is None for e in est.values())


# ── C9 ──────────────────────────────────────────────────────────────────────
def test_a_cell_in_an_overlap_is_counted_by_the_owning_tile_only():
    # Two tiles see the same site (index 5 in both); only tile A owns it.
    image = _paint(CENTRES, {5: [(0, 0, 3)]})
    labels_a, a = _count(image, owned={5})
    labels_b, b = _count(image, owned=set(range(16)) - {5})
    assert 5 in a and 5 not in b
    assert a[5].n_cells + sum(c.n_cells for i, c in b.items() if i == 5) == 1
    # Without the ownership filter both tiles count it: the double count the filter exists to prevent.
    unfiltered_a = nm.count_cells(labels_a, PX, GEOMS)
    unfiltered_b = nm.count_cells(labels_b, PX, GEOMS)
    assert unfiltered_a[5].n_cells + unfiltered_b[5].n_cells == 2


def test_cells_on_the_rim_or_between_sites_belong_to_no_site():
    labels = np.zeros((190, 190), dtype=int)
    x, y = CENTRES[0]
    labels[int(y), int(x + RADIUS - 0.5)] = 1                # on the rim (beyond 0.9 R)
    labels[int(y + PITCH / 2), int(x + PITCH / 2)] = 2       # between sites
    assert sum(c.n_cells for c in nm.count_cells(labels, PX, GEOMS).values()) == 0


# ── C6 ──────────────────────────────────────────────────────────────────────
def test_cell_table_carries_address_and_well_frame_position():
    image = _paint(CENTRES, {5: [(2, 2, 3)]})
    labels, counts = _count(image)
    addresses = {i: (i % 4, i // 4) for i in range(16)}
    table = nm.cell_table(labels, PX, GEOMS, counts, addresses, tile_origin_um=(1000.0, 2000.0),
                          intensity={"gfp": image})
    assert table["site_index"] == [5]
    assert (table["address_q"], table["address_r"]) == ([1], [1])
    assert table["x_um"][0] == pytest.approx(1000 + CENTRES[5][0] + 2, abs=0.6)
    assert table["y_um"][0] == pytest.approx(2000 + CENTRES[5][1] + 2, abs=0.6)
    assert table["area_um2"][0] == pytest.approx(np.pi * 9, abs=6)
    assert table["mean_gfp"][0] > FLOOR + 0.3
    unaddressed = nm.cell_table(labels, PX, GEOMS, counts, {})
    assert unaddressed["address_q"] == [None] and unaddressed["site_index"] == [5]


def test_every_cell_s_mean_is_its_own_pixels_on_every_channel():
    """Roll-up review X22 measured each mean through a full-tile mask per cell per channel; the means are now one
    labelled pass per channel. Each cell has its own value on each channel, a label outside any site comes first
    (so a mean read from the wrong position is wrong), and a NaN pixel makes that cell's mean NaN, as before."""
    labels = np.zeros((190, 190), dtype=np.int32)
    labels[5:8, 5:8] = 1                                    # between sites: not a counted cell
    for label, site in ((2, 0), (3, 5), (4, 10)):
        x, y = CENTRES[site]
        labels[int(y) - 2:int(y) + 2, int(x) - 2:int(x) + 2] = label
    rng = np.random.default_rng(3)
    gfp, rfp = rng.uniform(0, 100, labels.shape), rng.uniform(0, 100, labels.shape)
    x, y = CENTRES[10]
    rfp[int(y), int(x)] = np.nan
    counts = nm.count_cells(labels, PX, GEOMS)
    table = nm.cell_table(labels, PX, GEOMS, counts, {}, intensity={"gfp": gfp, "rfp": rfp})
    assert table["label"] == [2, 3, 4]
    np.testing.assert_allclose(table["mean_gfp"], [gfp[labels == k].mean() for k in (2, 3, 4)], rtol=1e-12)
    np.testing.assert_allclose(table["mean_rfp"][:2], [rfp[labels == k].mean() for k in (2, 3)], rtol=1e-12)
    assert np.isnan(table["mean_rfp"][2])


# ── C7 ──────────────────────────────────────────────────────────────────────
def test_fluorescence_is_over_the_empty_background_and_none_is_not_zero():
    image = np.full((190, 190), 10.0)
    x, y = CENTRES[3]
    yy, xx = np.mgrid[0:190, 0:190]
    image[np.hypot(xx + 0.5 - x, yy + 0.5 - y) <= 6] += 50.0
    result = nm.site_fluorescence(image, PX, GEOMS, empty_indices=[0, 1, 2])
    by = {r.index: r for r in result}
    assert by[0].background == 10.0 and by[0].mean_minus_background == pytest.approx(0.0)
    assert by[3].integrated == pytest.approx(50.0 * np.pi * 36, rel=0.05)
    assert all(r.integrated is None and r.background is None for r in nm.site_fluorescence(None, PX, GEOMS, [0]))
    no_empty = nm.site_fluorescence(image, PX, GEOMS, [])
    assert all(r.background is None and r.integrated is None for r in no_empty)


# ── C8 ──────────────────────────────────────────────────────────────────────
def test_an_empty_site_and_an_occluded_site_can_never_be_confused():
    empty = nm.site_state(visible_fraction=1.0, imaged_fraction=1.0, n_cells=0, confluence=0.0)
    occluded = nm.site_state(visible_fraction=0.2, imaged_fraction=1.0, n_cells=0, confluence=0.0)
    clipped = nm.site_state(visible_fraction=1.0, imaged_fraction=0.3, n_cells=0, confluence=0.0)
    off = nm.site_state(visible_fraction=1.0, imaged_fraction=None, n_cells=3, confluence=0.4)
    assert (empty.state, empty.n_cells, empty.confluence) == (nm.EMPTY, 0, 0.0)
    assert occluded.state == nm.OCCLUDED and occluded.n_cells is None and occluded.confluence is None
    assert clipped.state == nm.CLIPPED and clipped.n_cells is None and clipped.confluence is None
    assert off.state == nm.CLIPPED and off.n_cells is None


def test_a_partly_seen_site_keeps_its_metrics_and_says_so():
    part = nm.site_state(visible_fraction=0.7, imaged_fraction=1.0, n_cells=2, confluence=0.1, fused=True)
    assert (part.state, part.n_cells, part.partially_visible, part.fused) == (nm.MEASURED, 2, True, True)
    whole = nm.site_state(visible_fraction=1.0, imaged_fraction=1.0, n_cells=2, confluence=0.1)
    assert whole.partially_visible is False


def test_a_seen_site_without_a_count_is_undefined_not_empty():
    s = nm.site_state(visible_fraction=1.0, imaged_fraction=1.0, n_cells=None, confluence=0.2)
    assert s.state == nm.MEASURED and s.n_cells is None


def test_unaddressed_sites_keep_their_metrics():
    s = nm.site_state(visible_fraction=1.0, imaged_fraction=1.0, n_cells=4, confluence=0.3, addressed=False)
    assert s.addressed is False and s.n_cells == 4


# ── review findings ─────────────────────────────────────────────────────────
def _template_of(array, half):
    return nm.Template(np.asarray(array, dtype=float), half, nm.SOURCE_OWN, 4, 16, 0.0)


@pytest.mark.parametrize("level", [0.0, -1.0, float("nan")])
def test_a_template_that_cannot_be_divided_by_is_refused(level):
    one = [SiteGeometry(0, (30.0, 30.0), 8.0, FITTED)]
    with pytest.raises(ValueError, match="central level"):
        nm.rim_suppress(np.ones((60, 60)), PX, _template_of(np.full((25, 25), level), 12), one)


def test_a_template_smaller_than_the_site_is_refused_not_left_flat():
    one = [SiteGeometry(0, (30.0, 30.0), 8.0, FITTED)]
    with pytest.raises(ValueError, match="covers only 4"):
        nm.rim_suppress(np.ones((60, 60)), PX, _template_of(np.full((9, 9), 0.5), 4), one)


def test_a_template_with_nan_inside_the_reach_is_refused():
    one = [SiteGeometry(0, (30.0, 30.0), 8.0, FITTED)]
    array = np.full((25, 25), 0.5)
    array[12, 20] = np.nan                                     # 8 px from the centre: inside the 8.4 reach, outside the level disk
    with pytest.raises(ValueError, match="non-finite"):
        nm.rim_suppress(np.ones((60, 60)), PX, _template_of(array, 12), one)


def test_geometry_order_is_not_site_identity_and_an_owned_site_without_geometry_is_refused():
    labels = np.zeros((190, 190), dtype=int)
    x, y = CENTRES[7]
    labels[int(y) - 1:int(y) + 2, int(x) - 1:int(x) + 2] = 1
    shuffled = [GEOMS[9], GEOMS[7], GEOMS[2]]                   # index != position
    counts = nm.count_cells(labels, PX, shuffled, owned={7, 2})
    assert {i: c.n_cells for i, c in counts.items()} == {7: 1, 2: 0}      # a dropped cell would read 0 at 7
    with pytest.raises(ValueError, match=r"\[5\]"):
        nm.count_cells(labels, PX, shuffled, owned={7, 5})
    with pytest.raises(ValueError, match=r"\[3\]"):
        nm.area_estimate(labels, PX, shuffled, {3: nm.SiteCount(3, 0, ())})


def test_an_empty_site_with_no_confluence_keeps_it_undefined():
    s = nm.site_state(visible_fraction=1.0, imaged_fraction=1.0, n_cells=0, confluence=None)
    assert s.state == nm.EMPTY and s.n_cells == 0 and s.confluence is None


def test_nan_fractions_are_unknown_not_fully_seen():
    nan = float("nan")
    assert nm.site_state(visible_fraction=nan, imaged_fraction=1.0, n_cells=3, confluence=0.1).state == nm.OCCLUDED
    assert nm.site_state(visible_fraction=1.0, imaged_fraction=nan, n_cells=3, confluence=0.1).state == nm.CLIPPED


def test_a_nan_pixel_in_the_interior_leaves_fluorescence_undefined_for_that_site_only():
    image = np.full((190, 190), 10.0)
    x, y = CENTRES[3]
    image[int(y), int(x)] = np.nan
    by = {r.index: r for r in nm.site_fluorescence(image, PX, GEOMS, [0, 1, 2])}
    assert by[3].integrated is None and by[3].mean_minus_background is None
    assert by[4].integrated == pytest.approx(0.0) and by[4].background == 10.0


def test_a_site_mostly_off_the_image_is_not_measured():
    edge = [SiteGeometry(0, (0.5, 0.5), 10.0, FITTED), SiteGeometry(1, (95.0, 95.0), 10.0, FITTED)]
    rim = np.ones((190, 190))
    assert nm.site_confluence(rim, PX, edge, 0.1) == [None, 0.0]
    fl = nm.site_fluorescence(np.full((190, 190), 5.0), PX, edge, [1])
    assert fl[0].integrated is None and fl[1].integrated == pytest.approx(0.0)


def test_cells_cut_by_the_tile_edge_do_not_set_the_isolated_cell_size():
    labels = np.zeros((60, 60), dtype=int)
    labels[0:4, 20:30] = 1                                       # cut by the top edge: 40 px
    labels[30:40, 30:40] = 2                                     # whole: 100 px
    counts = {0: nm.SiteCount(0, 2, (1, 2))}
    assert nm.isolated_cell_area_um2(labels, PX, counts) == 100.0


def test_touching_cells_do_not_set_the_isolated_cell_size():
    labels = np.zeros((60, 60), dtype=int)
    labels[10:20, 10:20] = 1
    labels[10:20, 20:30] = 2                                     # touches 1: half a clump, 100 px each
    labels[40:46, 40:46] = 3                                     # alone: 36 px
    counts = {0: nm.SiteCount(0, 3, (1, 2, 3))}
    assert nm.isolated_cell_area_um2(labels, PX, counts) == 36.0


# ── second review ───────────────────────────────────────────────────────────
def test_a_nan_hole_in_the_rim_image_is_undefined_not_empty():
    rim = np.ones((190, 190))
    x, y = CENTRES[5]
    rim[int(y) - 3:int(y) + 3, int(x) - 3:int(x) + 3] = np.nan               # a stitching gap in site 5
    cut = nm.texture_threshold(rim, PX, GEOMS)
    assert cut is not None and np.isfinite(cut)                              # the other 15 sites still calibrate
    conf = nm.site_confluence(rim, PX, GEOMS, cut)
    assert conf[5] is None and conf[4] == 0.0 and conf[6] == 0.0             # the hole does not spread to neighbours
    assert nm.texture_threshold(rim, PX, [GEOMS[5]]) is None                 # a site with a hole is no calibration
    # A hole just outside site 4's interior: smoothing would carry it in if it were not made flat first.
    x4, y4 = CENTRES[4]
    near = np.ones((190, 190))
    near[int(y4) - 1:int(y4) + 1, int(x4) + 12:int(x4) + 14] = np.nan
    assert np.isfinite(nm._departure(near)).all()
    assert np.isfinite(nm.texture_threshold(near, PX, [GEOMS[4]]))


def test_a_noise_free_flat_tile_does_not_call_a_whisper_a_cell():
    rim = np.ones((190, 190))
    rim[int(CENTRES[2][1]), int(CENTRES[2][0])] += 1e-9
    cut = nm.texture_threshold(rim, PX, GEOMS)
    assert cut == nm.MIN_TEXTURE_THRESHOLD
    assert nm.site_confluence(rim, PX, GEOMS, cut)[2] == 0.0


@pytest.mark.parametrize("fitted_radius", [10.0, 10.5, 12.5, 13.0])
def test_a_template_built_with_headroom_covers_every_radius_the_fit_accepts(fitted_radius):
    # Nominal 10 px; the site fit accepts up to +30 %. Crops are cut the documented way.
    centres = _centres()
    nominal = 10.0
    image = _paint(centres)
    crops = nm.site_crops(image, PX, centres, nm.template_half(nominal))
    template = nm.build_template(crops, interior_radius_px=nm.SUPPRESSION_RADIUS * nominal, min_sites=2)
    geoms = [SiteGeometry(i, c, fitted_radius if i == 0 else nominal, FITTED) for i, c in enumerate(centres)]
    rim = nm.rim_suppress(image, PX, template, geoms)
    assert rim.shape == image.shape


def test_a_site_the_template_cannot_cover_is_split_off_not_left_to_fail_the_tile():
    template = _template_of(np.full((25, 25), 0.5), 12)
    geoms = [SiteGeometry(0, (30.0, 30.0), 8.0, FITTED), SiteGeometry(1, (90.0, 30.0), 13.0, FITTED)]
    fit, big = nm.fits_template(template, geoms, pixel_size_um=PX)
    assert [g.index for g in fit] == [0] and [g.index for g in big] == [1]
    nm.rim_suppress(np.ones((120, 60)), PX, template, fit)
    with pytest.raises(ValueError):
        nm.rim_suppress(np.ones((120, 60)), PX, template, geoms)


def test_ownership_report_feeds_count_cells_through_owned_indices():
    from pipeline.tile_grid import RecordedTile, ownership_report, place_from_record

    width = 190.0
    plan = place_from_record([RecordedTile("L", (0.0, 0.0)), RecordedTile("R", (175.0, 0.0))],
                             tile_size_px={"x": width, "y": width}, pixel_size_um={"x": 1.0, "y": 1.0})
    sites = np.array(list(CENTRES) + [(182.0, 70.0)])           # the last one straddles the seam
    report = ownership_report(plan, sites, 24.0, 2.0)
    left = nm.owned_indices(report.owners, "L")
    right = nm.owned_indices(report.owners, "R")
    assert left and not (left & right)                                       # nobody is owned twice
    fused = {i for i, o in enumerate(report.owners) if o is None}
    assert 16 in fused and not (left | right) & fused                     # a fused site is in nobody's set
    labels = np.zeros((190, 190), dtype=int)
    i = min(left)
    x, y = CENTRES[i]
    labels[int(y) - 1:int(y) + 2, int(x) - 1:int(x) + 2] = 1
    counts = nm.count_cells(labels, PX, GEOMS, owned=left)
    assert counts[i].n_cells == 1 and set(counts) == left
    assert nm.count_cells(labels, PX, GEOMS, owned=right).keys() == right    # the other tile does not count it


# ── C15: the state judges the interior the contents read ─────────────────────
def test_interior_imaged_is_the_share_of_the_interior_on_the_image():
    whole = nm.interior_imaged((190, 190), PX, GEOMS[5])
    assert whole == pytest.approx(1.0, abs=0.03)
    half_off = SiteGeometry(0, (190.0, 100.0), RADIUS, FITTED, 0.2, 64)        # centre on the right edge
    assert nm.interior_imaged((190, 190), PX, half_off) == pytest.approx(0.5, abs=0.06)
    off = SiteGeometry(0, (194.0, 100.0), RADIUS, FITTED, 0.2, 64)
    assert nm.interior_imaged((190, 190), PX, off) < nm.MIN_INTERIOR_COVERED


def test_a_site_whose_interior_was_not_seen_is_clipped_whatever_the_rectangles_say():
    state = nm.site_state(visible_fraction=1.0, imaged_fraction=0.9, n_cells=0, confluence=None,
                          interior_imaged=0.4)
    assert (state.state, state.n_cells, state.confluence) == (nm.CLIPPED, None, None)
    seen = nm.site_state(visible_fraction=1.0, imaged_fraction=0.9, n_cells=0, confluence=0.0, interior_imaged=0.8)
    assert (seen.state, seen.n_cells) == (nm.EMPTY, 0)


# ── C12: a timepoint's template against the earliest ─────────────────────────
def _template_for(loaded, *, seed, gain=1.0, blur=0.0, noise=0.004):
    from scipy import ndimage

    centres = _centres(n=7)[:40]
    rng = np.random.default_rng(seed)
    cells = {i: [(rng.uniform(-6, 6), rng.uniform(-6, 6), 5) for _ in range(5)] for i in range(loaded)}
    image = _paint(centres, cells, size=300, seed=seed, noise=noise) * gain
    if blur:
        image = ndimage.gaussian_filter(image, blur)
    return nm.build_template(nm.site_crops(image, PX, centres, 16), interior_radius_px=1.05 * RADIUS)


def test_the_floor_ratio_flags_a_mostly_loaded_well_and_not_exposure_noise_or_focus():
    """The reviewer's case: 36 of 40 sites loaded leaves no empty quarter, and the template is cell-coloured
    while still reported ``own``. Its floor-to-surround ratio moves far past a factor of two; 30 loaded, 1.6x
    the exposure, three times the noise and a 2.5 px defocus do not."""
    earliest = _template_for(4, seed=3)
    loaded = _template_for(36, seed=43)
    assert loaded.source == nm.SOURCE_OWN                         # nothing in the template itself says so
    assert nm.template_drift(loaded, earliest) > 1.0
    for other in (_template_for(30, seed=37), _template_for(30, seed=37, gain=1.6),
                  _template_for(10, seed=11, noise=0.15), _template_for(10, seed=11, blur=2.5)):
        assert nm.template_drift(other, earliest) < 0.5 * nm.TEMPLATE_DRIFT_LIMIT
