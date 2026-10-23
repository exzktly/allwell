"""Bringing a nanowell to the template's levels before dividing by it (``nanowell_measure`` MEASURE_VERSION 2).

Reported: a well of 1,472 nanowells had a confluence value for 17. Confluence needs a texture threshold, and each tile
took its own from the empty nanowells it owned; the empty nanowells were the least-textured quarter on raw intensity,
so they gathered on the dimmest tiles and every other tile had no threshold and no confluence. These are the pieces
that replace that, tested without rendering a plate: the per-crop levels, the crops levelled for choosing empty
nanowells, each tile's offset from its empty nanowells, each site's gain from its own surround, the division that
applies them, the one threshold pooled across tiles, and the version that keeps old and new measurements apart.
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
    """The same textured nanowell seen through two gains and two offsets levels to the same crop, so the least-textured
    quarter is chosen by the nanowell and not by the tile it is on."""
    base = _crop(0.6, 1.0, texture=0.05, seed=1)
    seen = np.stack([base, 1.4 * base, 0.7 * base + 0.3, 1.2 * base - 0.1])
    levelled = nm.level_crops(seen, HALF)
    for crop in levelled[1:]:
        np.testing.assert_allclose(crop, levelled[0], atol=1e-9)


def test_each_tile_s_offset_is_recovered_from_its_empty_nanowells():
    template = _template()
    def seen(gain, offset):
        return (gain * 0.6 + offset, gain * 1.0 + offset)               # (floor, surround) of an empty nanowell
    levels = {"F0": [seen(1.0, 0.0), seen(1.3, 0.0)], "F1": [seen(0.8, 0.25), seen(1.1, 0.25)]}
    found = nm.tile_offsets(levels, template, ["F0", "F1", "F2"])
    assert found.identifiable
    assert found.offsets["F0"] == pytest.approx(0.0) and found.offsets["F1"] == pytest.approx(0.25)
    assert found.fallback == ("F2",) and found.offsets["F2"] == pytest.approx(0.125)   # the well's median


def test_no_offset_is_claimed_when_the_floor_and_surround_cannot_tell_it_from_a_gain():
    found = nm.tile_offsets({"F0": [(0.99, 1.0)]}, _template(floor=0.99, surround=1.0), ["F0"])
    assert not found.identifiable and found.offsets == {"F0": 0.0}


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


def test_one_threshold_is_pooled_from_every_tile_s_empty_nanowells():
    a, b = np.full(50, 0.01), np.full(50, 0.03)
    assert nm.threshold_from_departures([a, b]) == nm.threshold_from_departures([np.concatenate([a, b])])
    assert nm.threshold_from_departures([]) is None
    assert nm.threshold_from_departures([np.array([])]) is None


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
