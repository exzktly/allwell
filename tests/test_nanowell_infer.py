"""Finding a nanowell array by its contrast and inferring its geometry (tracker X35), on rendered images.

Every fixture is built so that the assumption this replaces gives a different answer: a square lattice (a
hexagonal-only fit has no lattice to give), a lattice turned away from the image axes (an axis-aligned reading
gets every address wrong), an array whose outline is not a circle (a circle's roster holds the caps between it and
its circumcircle), wells hidden in the middle of the array (an extent read from the wells found alone would lose
them), and illumination that falls across the image (one global threshold reads a different well size at each end).
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline import nanowell_infer as ni
from pipeline import nanowell_run as nr
from tests._nanowell_run_fixture import build_well, threshold_segmenter
from tests._nanowell_synth import TilePlan, render_tiles

PX, PITCH, DIAMETER = 4.3, 172.0, 100.0
INFER = nr.NanowellSettings("100um", geometry=nr.GEOMETRY_INFER)


def _image(**render):
    """One 900 px tile holding a whole array (or as much as *array_shift_um* leaves on it): ``(rendered, image)``."""
    render.setdefault("radius_um", 1300.0)
    render.setdefault("noise_sigma", 0.05)
    plan = TilePlan(1, 1, (900, 900), 0.0, PX)
    rendered = render_tiles(plan, pitch_um=PITCH, diameter_um=DIAMETER, blur_px=1.0, **render)
    return rendered, rendered.images["F0"]


def _infer(image):
    wells = ni.find_wells_by_contrast(image, PX)
    assert wells.ok, wells.reason
    return wells, ni.infer_array(wells.points_um, pixel_size_um=PX, covered=np.ones(image.shape, bool),
                                 edge_points_um=wells.edge_points_um, diameter_um=wells.diameter_um)


def _drawn_address(rendered, point):
    sites = np.asarray(rendered.sites_um)
    k = int(np.argmin(np.linalg.norm(sites - point, axis=1)))
    assert np.linalg.norm(sites[k] - point) < PITCH / 4, "a position no drawn nanowell is near"
    return rendered.array.addresses[k]


def _lattice_address(geometry, point):
    basis = np.column_stack((geometry.fit.a1, geometry.fit.a2))
    return tuple(np.rint(np.linalg.solve(basis, np.asarray(point) - geometry.fit.phase_origin)).astype(int))


def _roster_matches_the_drawing(rendered, geometry):
    """Every roster site is a drawn nanowell at the same lattice offset, and every drawn one is in the roster."""
    offsets = {tuple(np.subtract(_lattice_address(geometry, p), _drawn_address(rendered, p))) for p in geometry.roster_um}
    assert len(offsets) == 1, f"the addresses are not one lattice translation of the drawn ones: {sorted(offsets)[:5]}"
    assert len(geometry.roster_um) == len(rendered.array.roster)


@pytest.mark.parametrize("rotation", [10.0, -20.0])
def test_a_square_lattice_is_found_square_at_its_own_pitch_and_angle(rotation):
    rendered, image = _image(lattice="square", rotation_deg=rotation, square_centre=False)
    _, geometry = _infer(image)
    assert geometry.ok, geometry.reason
    assert geometry.fit.kind == "square"
    assert geometry.fit.pitch == pytest.approx(PITCH, rel=0.005)
    assert geometry.fit.angle_deg == pytest.approx(rotation, abs=0.3)
    _roster_matches_the_drawing(rendered, geometry)


def test_a_hexagonal_lattice_under_falling_light_with_cells_and_debris_is_found_hexagonal():
    rendered, image = _image(rotation_deg=-12.0, vignette=0.4, illumination=0.6, cells=60, debris=60)
    wells, geometry = _infer(image)
    assert geometry.ok and geometry.fit.kind == "hex"
    assert geometry.fit.pitch == pytest.approx(PITCH, rel=0.005)
    assert geometry.fit.angle_deg == pytest.approx(-12.0, abs=0.3)
    assert wells.diameter_um == pytest.approx(DIAMETER, rel=0.05)
    assert sum(geometry.found) == len(rendered.array.roster)          # every well found, at both ends of the ramp
    _roster_matches_the_drawing(rendered, geometry)


def test_half_the_image_a_third_as_bright_still_has_its_wells_found():
    """Two tiles of one montage at different gains (nothing matches them photometrically): one split for the whole
    image puts the dim half's wells below it, so each pixel is split against its own neighbourhood instead."""
    rendered, image = _image(radius_um=1700.0, rotation_deg=-12.0, noise_sigma=0.05)
    image = image.copy()
    image[:, 450:] *= 0.35
    _, geometry = _infer(image)
    assert geometry.ok and geometry.shape == ni.ARRAY_CIRCLE
    # Within the neighbourhood's reach of the step (2.5 spacings) the bright side sets the split and dim wells are
    # lost; they stay in the roster as not found. Beyond it the dim half is read like the bright one.
    dim = [f for p, f in zip(geometry.roster_um, geometry.found) if p[0] > 450 * PX + 2.5 * PITCH]
    assert sum(dim) == len(dim) > 100


@pytest.mark.parametrize("noise", [0.05, 0.15])
def test_a_small_array_on_a_wide_noisy_floor_holds_only_its_own_wells(noise):
    """Most of the image is flat floor, whose camera noise splits into speckle; none of it may become a well."""
    rendered, image = _image(radius_um=700.0, rotation_deg=-12.0, noise_sigma=noise, cells=40, debris=40)
    _, geometry = _infer(image)
    assert geometry.ok and geometry.shape == ni.ARRAY_CIRCLE and geometry.outliers == 0
    _roster_matches_the_drawing(rendered, geometry)
    assert all(geometry.found)


def test_an_occluded_well_keeps_its_place_in_the_array_and_is_reported_not_found():
    hidden = [(1, 0), (-2, 3)]
    rendered, image = _image(rotation_deg=5.0, dark_sites=hidden)
    _, geometry = _infer(image)
    assert geometry.ok
    _roster_matches_the_drawing(rendered, geometry)                   # the hidden two are still in the roster
    not_found = {_drawn_address(rendered, p) for p, f in zip(geometry.roster_um, geometry.found) if not f}
    assert not_found == set(hidden)
    assert geometry.missing == 2 and all(geometry.imaged)


@pytest.mark.parametrize("lattice, shape, expected", [
    ("hex", "circle", ni.ARRAY_CIRCLE), ("hex", "square", ni.ARRAY_POLYGON), ("square", "square", ni.ARRAY_POLYGON),
    ("square", "circle", ni.ARRAY_CIRCLE)])
def test_the_array_s_outline_is_read_from_the_wells_and_holds_exactly_the_drawn_sites(lattice, shape, expected):
    rendered, image = _image(lattice=lattice, array_shape=shape, rotation_deg=8.0, radius_um=1100.0,
                             square_centre=False)
    _, geometry = _infer(image)
    assert geometry.ok and geometry.shape == expected and not geometry.clipped
    _roster_matches_the_drawing(rendered, geometry)
    middle = _drawn_address(rendered, geometry.centre_um)
    assert middle == (0, 0)                                           # the array's centre is its centre site


def test_an_array_the_image_cuts_is_clipped_and_a_polygon_has_no_centre_to_anchor_on():
    rendered, image = _image(lattice="square", array_shape="square", rotation_deg=6.0, radius_um=1100.0,
                             array_shift_um=(900.0, 0.0), square_centre=False)
    wells, geometry = _infer(image)
    assert geometry.ok and geometry.clipped and geometry.shape == ni.ARRAY_POLYGON
    assert geometry.centre_um is None                                 # the seen half's middle is not the array's
    assert len(wells.edge_points_um) > 0
    seen = {_drawn_address(rendered, p) for p, f in zip(geometry.roster_um, geometry.found) if f}
    assert len(seen) == int(sum(geometry.found)) > 50


def test_a_given_length_that_disagrees_is_reported_with_both_numbers():
    off = ni.compare_given(172.0, 200.0, ni.PITCH_AGREEMENT, "pitch")
    assert off["agrees"] is False and "172.0" in off["note"] and "200" in off["note"]
    same = ni.compare_given(172.4, 172.0, ni.PITCH_AGREEMENT, "pitch")
    assert same["agrees"] is True and same["note"] == ""


# ── through the run ──────────────────────────────────────────────────────────
def _run(settings, **render):
    rendered, record = build_well(**render)
    outcome = nr.run_well("A01", record, ["T0"], lambda tp, tile: rendered.images.get(tile), settings,
                          segmenter=threshold_segmenter())
    return rendered, outcome


@pytest.fixture(scope="module")
def square_well():
    """A square lattice in a square array, turned 15 degrees, one well under a bubble, no central square."""
    return _run(INFER, lattice="square", array_shape="square", rotation=-15.0, radius_fraction=0.22,
                square_centre=False, dark_sites=[(2, 1)])


def test_an_inferred_square_array_gets_every_drawn_address_and_no_other(square_well):
    rendered, outcome = square_well
    block = outcome.timepoints["T0"]
    assert block["status"] == nr.OK and block["origin_method"] == "array_centre"
    assert block["lattice_kind"] == "square" and block["geometry"]["shape"] == ni.ARRAY_POLYGON
    rows = {(r["nw_q"], r["nw_r"]): r for r in outcome.sites}
    truth = dict(zip(rendered.array.addresses, rendered.sites_um))
    assert set(rows) == set(truth)
    off = [a for a, r in rows.items() if np.hypot(r["well_x_um"] - truth[a][0], r["well_y_um"] - truth[a][1]) > 10]
    assert off == []
    assert rows[(2, 1)]["nw_ring"] == 2 and rows[(3, -1)]["nw_ring"] == 3          # square rings, not hex distance
    assert outcome.radius["roster"] and outcome.radius["radius_um"] is None


def test_the_occluded_well_has_its_row_and_says_it_was_not_seen(square_well):
    _, outcome = square_well
    row = next(r for r in outcome.sites if (r["nw_q"], r["nw_r"]) == (2, 1))
    assert row["detected"] is False and row["state"] == "occluded" and row["n_cells"] is None
    assert outcome.timepoints["T0"]["geometry"]["n_missing"] == 1
    assert any("showed no light nanowell" in n for n in outcome.timepoints["T0"]["notes"])


def test_the_same_square_array_is_not_located_on_the_format_s_hexagonal_geometry():
    """The fixture discriminates: the default geometry has no lattice to give this plate."""
    rendered, record = build_well(lattice="square", array_shape="square", rotation=-15.0, radius_fraction=0.22,
                                  square_centre=False)
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, nr.NanowellSettings("100um"))
    assert located.status != nr.OK


def test_a_circular_array_s_inferred_roster_holds_no_site_beyond_its_rim():
    rendered, outcome = _run(INFER)
    assert outcome.timepoints["T0"]["geometry"]["shape"] == ni.ARRAY_CIRCLE
    assert {(r["nw_q"], r["nw_r"]) for r in outcome.sites} == set(rendered.array.addresses)


@pytest.mark.parametrize("given, agrees", [(200.0, False), (172.0, True)])
def test_a_custom_pitch_is_compared_with_the_measured_one_and_a_disagreement_is_reported(given, agrees):
    settings = nr.NanowellSettings("custom", custom=(given, DIAMETER), geometry=nr.GEOMETRY_INFER)
    _, outcome = _run(settings)
    block = outcome.timepoints["T0"]
    assert block["status"] == nr.OK
    check = block["geometry"]["pitch_check"]
    assert check["given_um"] == given and check["measured_um"] == pytest.approx(PITCH, rel=0.005)
    assert check["agrees"] is agrees
    assert any("disagrees with the 200 um" in n for n in block["notes"]) is (not agrees)


def test_the_geometry_joins_the_reuse_key_only_when_it_is_inferred():
    assert "geometry" not in nr.NanowellSettings("100um").key()
    assert INFER.key()["geometry"] == "infer"
    with pytest.raises(ValueError, match="geometry"):
        nr.NanowellSettings("100um", geometry="round")


def test_a_corner_hidden_at_one_timepoint_stays_in_the_well_s_roster_and_is_seen_as_occluded_there():
    """The shared roster is what at least half the timepoints' own arrays hold: one timepoint whose corner wells
    are all hidden (its own hull is cut short there) keeps them, as not seen, rather than dropping them."""
    corner = [(7, 7), (7, 6), (6, 7)]
    common = dict(lattice="square", array_shape="square", radius_fraction=0.22, square_centre=False)
    clean, record = build_well(rotation=-15.0, timepoint="T0", **common)
    hidden, later = build_well(rotation=-13.0, timepoint="T1", seed=2, dark_sites=corner, **common)
    record["timepoints"].update(later["timepoints"])
    record["tiles"] += later["tiles"]
    images = {"T0": clean.images, "T1": hidden.images}
    outcome = nr.run_well("A01", record, ["T0", "T1"], lambda tp, tile: images[tp].get(tile), INFER,
                          segmenter=threshold_segmenter())
    assert all(outcome.timepoints[tp]["status"] == nr.OK for tp in ("T0", "T1"))
    by_tp = {tp: {(r["nw_q"], r["nw_r"]): r for r in outcome.sites if r["timepoint"] == tp} for tp in ("T0", "T1")}
    assert set(by_tp["T0"]) == set(by_tp["T1"]) == set(clean.array.addresses)
    assert all(by_tp["T1"][a]["detected"] is False and by_tp["T1"][a]["state"] == "occluded" for a in corner)
    assert outcome.radius["roster_differences"] == {"T0": 0, "T1": 3}


def test_an_earlier_run_that_anchored_nothing_leaves_no_roster_to_keep():
    """A well whose earlier run anchored no timepoint recorded an empty roster; re-anchored from the rest of the
    plate, its timepoints must get the roster they now support, not the empty one."""
    rendered, record = build_well(lattice="square", array_shape="square", rotation=-15.0, radius_fraction=0.22,
                                  square_centre=False)
    outcome = nr.run_well("A01", record, ["T0"], lambda tp, tile: rendered.images.get(tile), INFER,
                          segmenter=threshold_segmenter(), fixed_roster=[])
    assert {(r["nw_q"], r["nw_r"]) for r in outcome.sites} == set(rendered.array.addresses)
