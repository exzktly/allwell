"""Bringing a nanowell to the template's levels before dividing by it (``nanowell_measure`` MEASURE_VERSION 2).

Reported: a well of 1,472 nanowells had a confluence value for 17. Confluence needs a texture threshold, and each tile
took its own from the empty nanowells it owned; the empty nanowells were the least-textured quarter on raw intensity,
so they gathered on the dimmest tiles and every other tile had no threshold and no confluence. These are the pieces
that replace that, tested without rendering a plate: the per-crop levels, the levelled crops the template is taken
over, each site's offset from its own floor and surround smoothed over its neighbours (tracker L1, which removed every
reliance on empty nanowells), each site's gain from its own surround, the division that applies them, the one
threshold pooled across every site, and the version that keeps old and new measurements apart.
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline import nanowell_measure as nm

HALF = 20
SITE = HALF / (nm.TEMPLATE_HEADROOM * nm.SUPPRESSION_RADIUS)


def _crop(floor: float, surround: float, *, texture: float = 0.0, seed: int = 0) -> np.ndarray:
    """A site-centred crop: *floor* inside the nanowell, *surround* beyond it, with optional texture on the floor."""
    yy, xx = np.mgrid[-HALF:HALF + 1, -HALF:HALF + 1]
    r = np.hypot(xx, yy)
    crop = np.where(r <= SITE, floor, surround).astype(np.float64)
    if texture:
        rng = np.random.default_rng(seed)
        crop[r <= 0.8 * SITE] += texture * rng.standard_normal(int((r <= 0.8 * SITE).sum()))
    return crop


def _template(floor=0.6, surround=1.0) -> nm.Template:
    return nm.Template(_crop(floor, surround), HALF, nm.SOURCE_OWN, 8, 32, 0.0, ())


def test_crop_levels_read_the_floor_and_the_surround():
    floors, surrounds = nm.crop_levels(np.stack([_crop(30.0, 50.0), _crop(3.0, 5.0)]), HALF)
    assert floors.tolist() == [30.0, 3.0] and surrounds.tolist() == [50.0, 5.0]


def test_levelled_crops_do_not_depend_on_a_tile_s_gain_or_offset():
    """The same textured nanowell seen through two gains and two offsets levels to the same crop, so the template is
    taken over crops that agree whatever tile they came from."""
    base = _crop(0.6, 1.0, texture=0.05, seed=1)
    seen = np.stack([base, 1.4 * base, 0.7 * base + 0.3, 1.2 * base - 0.1])
    levelled = nm.level_crops(seen, HALF)
    for crop in levelled[1:]:
        np.testing.assert_allclose(crop, levelled[0], atol=1e-9)


def _loaded(floor: float, surround: float, *, seed: int) -> np.ndarray:
    """:func:`_crop` with cells over about 40 % of the floor, each of its own brightness: no bare-floor median."""
    crop = _crop(floor, surround)
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[-HALF:HALF + 1, -HALF:HALF + 1]
    for _ in range(6):
        cx, cy = rng.uniform(-0.5, 0.5, 2) * SITE
        crop[np.hypot(xx - cx, yy - cy) <= 0.22 * SITE] += rng.uniform(0.1, 0.5) * (surround - floor)
    return crop


def test_each_site_s_level_comes_from_its_own_floor_and_surround_under_its_cells():
    """Tracker L1: a level no longer needs a tile's empty nanowells, of which a well seeded at twenty cells to a
    nanowell has none, and it is no longer one offset per tile. A loaded nanowell's floor is its modal level, so its
    own two levels give its gain and its offset."""
    template = _template()
    disk = np.hypot(*np.mgrid[-HALF:HALF + 1, -HALF:HALF + 1]) <= 0.6 * SITE
    for gain, offset, seed in ((1.0, 0.0, 1), (1.3, 0.25, 2), (0.8, -0.1, 3)):
        crop = gain * _loaded(0.6, 1.0, seed=seed) + offset
        (floor,), (surround,) = nm.crop_levels(crop[None], HALF)
        assert np.median(crop[disk]) > gain * 0.6 + offset + 0.02          # a median would read the cells
        assert floor == pytest.approx(gain * 0.6 + offset, abs=1e-6)
        found = nm.site_level(surround, nm.site_contrast(floor, surround), template)
        assert found == pytest.approx((offset, gain), abs=1e-6)


def test_no_level_is_claimed_when_the_floor_and_surround_cannot_tell_an_offset_from_a_gain():
    template = _template(floor=0.99, surround=1.0)
    assert not nm.offset_identifiable(template)
    assert nm.site_level(1.0, 0.01, template) is None
    assert nm.offset_identifiable(_template())
    assert nm.site_level(0.6, nm.site_contrast(0.7, 0.6), _template()) is None    # floor above surround: no gain
    assert nm.site_contrast(float("nan"), 1.0) is None


def test_light_added_over_a_dark_lattice_does_not_flatten_the_cells():
    """The case that set the design (tracker L1, measured on a rendered well with glare toward its centre). The
    lattice is dark (0.15) and the floor light (0.8), as on the plates; glare adds 0.5 to both. The gain comes from
    the contrast, which the glare does not touch, so a cell divides out to the same height with and without it.
    Taken from the surround after an offset 0.065 short — what one smoothed reading of a peak gave — it would have
    been (0.15 + 0.065) / 0.15 = 1.43 times too large."""
    from pipeline.nanowell_detect import SiteGeometry

    template = _template(floor=0.8, surround=0.15)
    yy, xx = np.mgrid[0:60, 0:60]
    r = np.hypot(xx - 29.5, yy - 29.5)
    site = SiteGeometry(1, (30.0, 30.0), SITE, "fitted")
    heights = []
    for glare in (0.0, 0.5):
        image = np.where(r <= SITE, 0.8, 0.15) + glare
        image[np.hypot(xx - 33.5, yy - 29.5) <= 2] += 0.2                   # a cell, 0.2 over the floor
        crop = nm.site_crops(image, 1.0, [(30.0, 30.0)], HALF)
        (floor,), (surround,) = nm.crop_levels(crop, HALF)
        level = nm.site_level(surround, nm.site_contrast(floor, surround), template)
        heights.append(nm.rim_suppress(image, 1.0, template, [site], levels={1: level})[29, 33])
    assert heights[0] == pytest.approx(1.25, abs=1e-6) and heights[1] == pytest.approx(heights[0], abs=1e-6)


def test_a_contrast_follows_its_own_tile_and_outvotes_a_nanowell_lost_under_cells():
    """Two tiles overlap with different gains (contrast 0.4 and 0.6): each site takes the median of its own tile's
    neighbours only. One nanowell whose floor read as cells (contrast 0.05) is outvoted; one that gave none takes
    its neighbours'; a tile with none at all stays NaN."""
    xs, ys = np.meshgrid(np.arange(0, 1500, 150.0), np.arange(0, 1500, 150.0))
    centres = np.column_stack([xs.ravel(), ys.ravel()])
    # In the overlap (x 450 to 1050) the two tiles own alternate sites, so every neighbourhood there mixes them.
    groups = ["F0" if x < 450 or (x < 1050 and (x + y) % 300 == 0) else "F1" for x, y in centres]
    contrast = np.where(np.array(groups) == "F0", 0.4, 0.6)
    read = contrast.copy()
    read[22], read[23] = 0.05, np.nan                                      # both F0, outside the overlap
    found = nm.neighbourhood_median(centres, read, 225.0, groups)
    np.testing.assert_allclose(found, contrast)
    assert np.isnan(nm.neighbourhood_median(centres, np.full(len(centres), np.nan), 225.0, groups)).all()


def test_a_site_s_gain_brings_its_surround_to_the_template_s():
    template = _template()
    assert nm.site_gain(1.55, 0.25, template) == pytest.approx(1.3)
    assert nm.site_gain(0.2, 0.25, template) is None                      # an offset above the surround
    assert nm.site_gain(float("nan"), 0.0, template) is None              # no surround on the image


def test_the_division_applies_a_site_s_level_and_leaves_an_empty_one_at_one():
    from pipeline.nanowell_detect import SiteGeometry

    template = _template()
    image = np.full((60, 60), 1.0 * 1.3 + 0.25)                             # surround through gain 1.3, offset 0.25
    yy, xx = np.mgrid[0:60, 0:60]
    image[np.hypot(xx - 29.5, yy - 29.5) <= SITE] = 0.6 * 1.3 + 0.25        # an empty floor, the same way
    site = SiteGeometry(7, (30.0, 30.0), SITE, "fitted")
    rim = nm.rim_suppress(image, 1.0, template, [site], levels={7: (0.25, 1.3)})
    interior = np.hypot(xx - 29.5, yy - 29.5) <= 0.8 * SITE
    np.testing.assert_allclose(rim[interior], 1.0, atol=1e-6)
    raw = nm.rim_suppress(image, 1.0, template, [site])                     # without its level: not 1
    assert abs(float(np.median(raw[interior])) - 1.0) > 0.5


def test_one_threshold_is_the_floor_s_whatever_share_of_the_pooled_values_are_cells():
    """Tracker L1: the threshold is pooled over every site, cells included, and the cells are clipped away. Against
    the threshold the floor alone gives, it holds within 5 % with a fifth of the values cells (measured 4.4 %) and
    within 17 % with two fifths (15.5 %), within 35 % with half (33 %) and within 80 % at 55 % (67 %; starting from
    every value rather than the densest it is 5.1x there); the share above the threshold is returned beside it."""
    rng = np.random.default_rng(0)
    floor = np.abs(rng.normal(0, 0.03, 100_000))
    alone, share = nm.threshold_from_departures([floor])
    assert share == pytest.approx(0.0, abs=0.002)
    for fraction, tolerance in ((0.2, 0.05), (0.4, 0.17), (0.5, 0.35), (0.55, 0.8)):
        cells = rng.uniform(0.05, 0.3, int(len(floor) * fraction / (1 - fraction)))
        found, share = nm.threshold_from_departures([floor[:50_000], cells, floor[50_000:]])
        assert found == pytest.approx(alone, rel=tolerance)
        assert share == pytest.approx(fraction * np.mean(cells > found), abs=0.02)
    a, b = np.full(50, 0.01), np.full(50, 0.03)
    assert nm.threshold_from_departures([a, b]) == nm.threshold_from_departures([np.concatenate([a, b])])
    assert nm.threshold_from_departures([]) == (None, None)
    assert nm.threshold_from_departures([np.array([])]) == (None, None)


def test_measurements_of_another_version_are_not_mixed():
    from datasets.core.nanowell_tables import nanowell_block_conflict
    from pipeline import nanowell_run as nr

    old = {"nanowell": {"scheme_version": 1, "settings": {"plate_format": "100um"}}}
    new = {"nanowell": {"scheme_version": 1, "measure_version": nm.MEASURE_VERSION,
                        "settings": {"plate_format": "100um"}}}
    assert nanowell_block_conflict(old, new) == (
        "the two datasets' nanowell results were computed under different measurement version")
    assert nanowell_block_conflict(new, new) is None
    from types import SimpleNamespace

    outcome = SimpleNamespace(timepoints={"0h": {"record_digest": "d"}}, radius={})
    located = SimpleNamespace(status="ok", origin_method="fiducial", manual_anchor_um=None)
    key = nr._checkpoint_key(outcome, "0h", located, 100.0, None, nr.NanowellSettings("100um"), {})
    assert key["measure_version"] == nm.MEASURE_VERSION              # a checkpoint of another version is not taken back


def test_a_site_that_is_not_a_nanowell_has_no_say_in_its_neighbours_gain():
    """Found by the end-to-end lane (tracker L1): bare floor outside the array has a contrast near zero, and where such
    sites were most of a neighbourhood its median gave a gain of 0.003, which turned noise into a cell. A contrast
    beyond ``PLAUSIBLE_CONTRAST_FACTOR`` of its tile's median is set aside, each tile by its own median."""
    from pipeline import nanowell_run as nr

    contrasts = [0.40, 0.42, 0.001, 0.38, 1.5, float("nan"), 0.60, 0.62, 0.01]
    owners = ["F0"] * 6 + ["F1"] * 3
    kept = nr._plausible(contrasts, owners)
    assert [c == c for c in kept] == [True, True, False, True, False, False, True, True, False]
    assert kept[:2] == [0.40, 0.42] and kept[6:8] == [0.60, 0.62]
