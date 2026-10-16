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
from tests._nanowell_run_fixture import build_well, record_for, threshold_segmenter
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


@pytest.mark.parametrize("noise, radius, seed, rotation", [(0.05, 700.0, 0, -12.0), (0.15, 700.0, 0, -12.0),
                                                           (0.15, 500.0, 1, 8.0)])
def test_a_small_array_on_a_wide_noisy_floor_holds_only_its_own_wells(noise, radius, seed, rotation):
    """Most of the image is flat floor, whose camera noise splits into speckle; none of it may become a well. The
    third case is one where reading the typical well from every blob of the second split, speckle included, found
    no well at all (review X35-R2)."""
    rendered, image = _image(radius_um=radius, rotation_deg=rotation, noise_sigma=noise, cells=40 if seed == 0
                             else 30, debris=40 if seed == 0 else 30, seed=seed, square_centre=seed == 0)
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
    off = ni.compare_given(172.0, 200.0, ni.PITCH_AGREEMENT, "pitch", "addresses use the measured pitch")
    assert off["agrees"] is False and "172.0" in off["note"] and "200" in off["note"]
    same = ni.compare_given(172.4, 172.0, ni.PITCH_AGREEMENT, "pitch", "addresses use the measured pitch")
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


# ── review X35-R1: one frame for every timepoint ─────────────────────────────
_SQUARE = dict(lattice="square", array_shape="square", radius_fraction=0.22, square_centre=False)


def _timepoints(hidden=(), at=("T1", "T2"), names=("T0", "T1", "T2")):
    """The same square array at each of *names*, put back turned a little each time; *hidden* drawn dark at *at*."""
    record, sources = None, {}
    for k, tp in enumerate(names):
        rendered, rec = build_well(rotation=-15.0 + 2 * k, timepoint=tp, seed=1 + k,
                                   dark_sites=hidden if tp in at else (), **_SQUARE)
        if record is None:
            record = rec
        else:
            record["timepoints"].update(rec["timepoints"])
            record["tiles"] += rec["tiles"]
        sources[tp] = rendered
    return record, sources


def _offsets(outcome, tp, rendered):
    """``{address - drawn address: count}`` over *tp*'s rows; ``None`` counts the rows with no address."""
    truth = dict(zip(rendered.array.addresses, rendered.sites_um))
    keys, points = list(truth), np.asarray(list(truth.values()))
    out = {}
    for row in (r for r in outcome.sites if r["timepoint"] == tp):
        k = int(np.argmin(np.hypot(points[:, 0] - row["well_x_um"], points[:, 1] - row["well_y_um"])))
        offset = None if row["nw_q"] is None else (row["nw_q"] - keys[k][0], row["nw_r"] - keys[k][1])
        out[offset] = out.get(offset, 0) + 1
    return out


def _corner(rendered):
    """The 21 sites of a 15 x 15 square array beyond ``q + r = 8``: hiding them moves the array's middle site."""
    return [a for a in rendered.array.addresses if a[0] + a[1] >= 9]


def test_a_corner_hidden_at_most_timepoints_keeps_every_address_and_is_written_occluded_where_hidden():
    """Hidden at two timepoints of three, the corner moves those timepoints' own middle site by a step: each is
    moved back onto the timepoint that saw the whole array, so every address is the drawn one at every
    timepoint, and the corner — in no majority — is still in the roster and written as occluded where hidden."""
    clean, _ = build_well(rotation=-15.0, timepoint="T0", seed=1, **_SQUARE)
    corner = _corner(clean)
    record, sources = _timepoints(corner)
    outcome = nr.run_well("A01", record, list(sources), lambda tp, tile: sources[tp].images.get(tile), INFER,
                          segmenter=threshold_segmenter())
    for tp, rendered in sources.items():
        assert outcome.timepoints[tp]["status"] == nr.OK
        assert _offsets(outcome, tp, rendered) == {(0, 0): len(rendered.array.addresses)}, tp
    for tp in ("T1", "T2"):
        block = outcome.timepoints[tp]
        assert block.get("origin_shift") == [-1, -1] and block.get("origin_registered_to") == "T0"
        assert block["consistency"] == "roster_mismatch"           # its own array is not T0's: said, not hidden
        rows = {(r["nw_q"], r["nw_r"]): r for r in outcome.sites if r["timepoint"] == tp}
        assert all(rows[a]["state"] == "occluded" and rows[a]["detected"] is False for a in corner)
    assert outcome.timepoints["T0"]["consistency"] == "consistent" and "origin_shift" not in outcome.timepoints["T0"]
    assert len(outcome.radius["roster"]) == len(sources["T0"].array.addresses)


def test_a_timepoint_whose_lost_edge_cannot_be_told_is_not_addressed_rather_than_addressed_by_a_guess():
    """A whole edge row hidden: the 14-row array fits inside the 15-row one from either edge, and nothing in the
    sites says which edge was lost. The timepoint is unaddressed with that reason; it is never shifted silently."""
    clean, _ = build_well(rotation=-15.0, timepoint="T0", seed=1, **_SQUARE)
    row = [a for a in clean.array.addresses if a[0] == max(q for q, _ in clean.array.addresses)]
    record, sources = _timepoints(row)
    outcome = nr.run_well("A01", record, list(sources), lambda tp, tile: sources[tp].images.get(tile), INFER,
                          segmenter=threshold_segmenter())
    assert _offsets(outcome, "T0", sources["T0"]) == {(0, 0): len(clean.array.addresses)}
    for tp in ("T1", "T2"):
        block = outcome.timepoints[tp]
        assert block["status"] == nr.UNADDRESSED and "cannot be told" in block["reason"]
        assert set(_offsets(outcome, tp, sources[tp])) == {None}  # its wells measured, none given an address


def test_a_reference_that_lost_sites_of_its_own_does_not_decide_the_step_by_overlap():
    """Review X35-R11: T0 lost its q = +7 row, T1 (the reference) one corner site. By overlap the wrong step won by
    one site (210 against 209) and every T0 address named its neighbour. The rows each array spans decide instead,
    and T0's spans differ along q, so which edge it lost cannot be told: it is unaddressed, never shifted."""
    clean, _ = build_well(rotation=-15.0, timepoint="TX", seed=1, **_SQUARE)
    addresses = clean.array.addresses
    row = [a for a in addresses if a[0] == max(q for q, _ in addresses)]
    for corner in ([(-7, -7)], [(-7, -7), (-7, -6), (-6, -7)]):
        a, record = build_well(rotation=-15.0, timepoint="T0", seed=2, dark_sites=row, **_SQUARE)
        b, later = build_well(rotation=-14.0, timepoint="T1", seed=3, dark_sites=corner, **_SQUARE)
        record["timepoints"].update(later["timepoints"])
        record["tiles"] += later["tiles"]
        images = {"T0": a.images, "T1": b.images}
        outcome = nr.run_well("A01", record, ["T0", "T1"], lambda tp, tile: images[tp].get(tile), INFER,
                              segmenter=threshold_segmenter())
        assert outcome.timepoints["T0"]["status"] == nr.UNADDRESSED and "edge row" in outcome.timepoints["T0"]["reason"]
        assert set(_offsets(outcome, "T0", a)) == {None}
        assert set(_offsets(outcome, "T1", b)) == {(0, 0)}


@pytest.mark.parametrize("speck", [(9, 0), (8, 0)])
def test_a_bright_speck_beside_the_array_at_one_timepoint_adds_no_nanowell_to_the_well(speck):
    """Review X35-R12: with a roster made of every timepoint's sites, one on-lattice speck two steps out (or one)
    added 10 (or 3) phantom nanowells to every timepoint, for good, and the speckled timepoint became the reference.
    The roster is the sites the timepoints support; the speck is named on its own timepoint."""
    record, sources = _timepoints()
    _paint_well(sources["T1"], speck)
    outcome = nr.run_well("A01", record, list(sources), lambda tp, tile: sources[tp].images.get(tile), INFER,
                          segmenter=threshold_segmenter())
    drawn = set(sources["T0"].array.addresses)
    assert {tuple(a) for a in outcome.radius["roster"]} == drawn
    for tp, rendered in sources.items():
        assert outcome.timepoints[tp]["status"] == nr.OK
        assert _offsets(outcome, tp, rendered) == {(0, 0): len(drawn)}, tp
    assert any("no other timepoint supports" in n for n in outcome.timepoints["T1"]["notes"])
    assert [outcome.timepoints[tp]["consistency"] for tp in sources] == ["consistent", "roster_mismatch", "consistent"]


def test_an_edge_row_seen_at_one_timepoint_only_stays_in_the_roster():
    """The other half of X35-R12: a whole row a bubble hid at two timepoints of three (anchored here by the central
    square, so every frame is sure) was lit at the third, so it is the array's, and is written occluded where hid."""
    common = dict(_SQUARE, square_centre=True)
    clean, _ = build_well(rotation=-15.0, timepoint="TX", **common)
    row = [a for a in clean.array.addresses if a[0] == max(q for q, _ in clean.array.addresses)]
    record, images, sources = None, {}, {}
    for k, tp in enumerate(("T0", "T1", "T2")):
        rendered, rec = build_well(rotation=-15.0 + k, timepoint=tp, seed=2 + k, dark_sites=row if k else (), **common)
        if record is None:
            record = rec
        else:
            record["timepoints"].update(rec["timepoints"])
            record["tiles"] += rec["tiles"]
        sources[tp] = rendered
    outcome = nr.run_well("A01", record, list(sources), lambda tp, tile: sources[tp].images.get(tile), INFER,
                          segmenter=threshold_segmenter())
    assert {tuple(a) for a in outcome.radius["roster"]} == set(clean.array.addresses)
    for tp in ("T1", "T2"):
        rows = {(r["nw_q"], r["nw_r"]): r for r in outcome.sites if r["timepoint"] == tp}
        assert all(rows[a]["state"] == "occluded" for a in row), tp


def test_the_well_is_measured_at_one_diameter_at_every_timepoint():
    """Review X35-R14: 130 um nanowells under the 100 um setting read about 125 um, at the 25 % line, so the
    diameter chosen per timepoint flipped between 125 and 100 within one well."""
    plan = TilePlan(2, 2, (700, 700), 0.1, 2.0)
    extent = plan.step_um[0] + plan.extent_um[0]
    record, images = None, {}
    for k, tp in enumerate(("T0", "T1", "T2")):
        rendered = render_tiles(plan, pitch_um=172.0, diameter_um=130.1, radius_um=0.30 * extent,
                                rotation_deg=7.0 + 0.5 * k, noise_sigma=0.05 + 0.02 * k, blur_px=1.0, seed=k + 1)
        rec = record_for(rendered, timepoint=tp)
        if record is None:
            record = rec
        else:
            record["timepoints"].update(rec["timepoints"])
            record["tiles"] += rec["tiles"]
        images[tp] = rendered.images
    outcome = nr.run_well("A01", record, list(images), lambda tp, tile: images[tp].get(tile), INFER,
                          segmenter=threshold_segmenter())
    used = {outcome.timepoints[tp]["geometry"]["diameter_used_um"] for tp in images}
    assert len(used) == 1 and used == {outcome.radius["diameter_used_um"]}
    assert {outcome.located[tp].diameter_um for tp in images} == used        # what the sites were measured at


def _paint_well(rendered, address):
    """A light nanowell-sized disk drawn at *address* (in every tile that sees it): a speck on the lattice."""
    basis = np.column_stack((rendered.array.a1, rendered.array.a2))
    x, y = basis @ np.asarray(address, dtype=float) + np.asarray(rendered.array.origin)
    px = rendered.plan.pixel_size_um
    for name, image in rendered.images.items():
        left, top = rendered.true_left_top_um[name]
        yy, xx = np.mgrid[0:image.shape[0], 0:image.shape[1]]
        image[np.hypot((xx + 0.5) * px + left - x, (yy + 0.5) * px + top - y) <= DIAMETER / 2] = 0.85


def test_an_incremental_timepoint_is_moved_onto_the_kept_roster():
    clean, _ = build_well(rotation=-15.0, timepoint="T0", seed=1, **_SQUARE)
    record, sources = _timepoints(_corner(clean), at=("T1",), names=("T1",))
    kept = [list(a) for a in clean.array.addresses]
    outcome = nr.run_well("A01", record, ["T1"], lambda tp, tile: sources[tp].images.get(tile), INFER,
                          segmenter=threshold_segmenter(), fixed_roster=kept)
    assert outcome.timepoints["T1"].get("origin_shift") == [-1, -1]
    assert _offsets(outcome, "T1", sources["T1"]) == {(0, 0): len(kept)}


def test_a_timepoint_of_another_lattice_kind_is_refused_by_name_not_joined():
    """Review X35-R4, R15: a hexagonal and a square address cannot name the same nanowell. Three timepoints with the
    earliest the odd one out, so "the earliest decides" and "the majority decides" give different answers."""
    common = dict(array_shape="circle", square_centre=False, radius_fraction=0.22, rotation=-15.0)
    record, images = None, {}
    for k, (tp, lattice) in enumerate((("T0", "square"), ("T1", "hex"), ("T2", "hex"))):
        rendered, rec = build_well(timepoint=tp, seed=1 + k, lattice=lattice, **common)
        if record is None:
            record = rec
        else:
            record["timepoints"].update(rec["timepoints"])
            record["tiles"] += rec["tiles"]
        images[tp] = rendered.images
    outcome = nr.run_well("A01", record, list(images), lambda tp, tile: images[tp].get(tile), INFER,
                          segmenter=threshold_segmenter())
    assert [outcome.timepoints[tp]["status"] for tp in images] == [nr.LATTICE_KIND_MISMATCH, nr.OK, nr.OK]
    assert "square lattice" in outcome.timepoints["T0"]["reason"]
    assert nr.LATTICE_KIND_MISMATCH in nr.TERMINAL_WITHOUT_ROWS
    assert not [r for r in outcome.sites if r["timepoint"] == "T0"]


def test_an_incremental_timepoint_of_another_kind_than_the_kept_run_is_refused():
    """Review X35-R13: the vote once counted only this run's timepoints, so a square timepoint added to a hexagonal
    well was addressed on the hexagonal roster and joined to it by address."""
    common = dict(array_shape="circle", square_centre=False, radius_fraction=0.22, rotation=-15.0)
    hexagonal, record = build_well(timepoint="T0", lattice="hex", **common)
    first = nr.run_well("A01", record, ["T0"], lambda tp, tile: hexagonal.images.get(tile), INFER,
                        segmenter=threshold_segmenter())
    square, later = build_well(timepoint="T1", seed=2, lattice="square", **common)
    kept = first.timepoints["T0"]
    reference = {"timepoint": "T0", "pitch_um": kept["pitch_um"], "angle_deg": kept["angle_deg"],
                 "roster_size": kept["roster_size"], "lattice_kind": kept["lattice_kind"]}
    second = nr.run_well("A01", later, ["T1"], lambda tp, tile: square.images.get(tile), INFER,
                         segmenter=threshold_segmenter(), fixed_roster=first.radius["roster"], reference=reference)
    block = second.timepoints["T1"]
    assert block["status"] == nr.LATTICE_KIND_MISMATCH and "the earlier run kept a hex one" in block["reason"]
    assert not second.sites


def test_a_format_too_wide_for_the_measured_pitch_is_a_note_on_an_inferred_geometry_not_a_refusal():
    """Review X35-R3: 80 um nanowells on a 105 um pitch, run as the 100 um format. The diameter is within the 25 %
    the threshold is trusted to, so the setting's 100 um is kept — and it nearly fills the pitch, which the format
    check refuses. An inferred geometry measured the plate itself: it addresses it and says what was wrong."""
    plan = TilePlan(2, 2, (700, 700), 0.1, 2.0)
    extent = plan.step_um[0] + plan.extent_um[0]
    rendered = render_tiles(plan, pitch_um=105.0, diameter_um=80.0, radius_um=0.26 * extent, rotation_deg=7.0,
                            noise_sigma=0.05, blur_px=1.0, seed=1, square_centre=False)
    record = record_for(rendered)
    run = lambda settings: nr.run_well("A01", record, ["T0"], lambda tp, tile: rendered.images.get(tile),  # noqa: E731
                                       settings, segmenter=threshold_segmenter())
    assert run(nr.NanowellSettings("100um")).timepoints["T0"]["status"] == nr.FORMAT_MISMATCH
    outcome = run(INFER)
    block = outcome.timepoints["T0"]
    assert block["status"] == nr.OK and block["geometry"]["diameter_check"]["agrees"] is True
    assert {(r["nw_q"], r["nw_r"]) for r in outcome.sites} == set(rendered.array.addresses)
    assert any("neighbouring nanowells would touch" in n and "addresses the plate as measured" in n
               for n in block["notes"])


def test_a_format_whose_nanowells_are_the_wrong_size_is_measured_at_the_measured_size_and_says_so():
    """Review X35-R3: a plate of 50 um nanowells on an 86 um pitch run as the 100 um format. The format's own
    geometry refuses it; an inferred one addresses it, fits the sites at the diameter it measured, and says so."""
    plan = TilePlan(2, 2, (700, 700), 0.1, 2.0)
    extent = plan.step_um[0] + plan.extent_um[0]
    rendered = render_tiles(plan, pitch_um=86.0, diameter_um=50.0, radius_um=0.26 * extent, rotation_deg=7.0,
                            noise_sigma=0.05, blur_px=1.0, seed=1, square_centre=False)
    record = record_for(rendered)
    run = lambda settings: nr.run_well("A01", record, ["T0"], lambda tp, tile: rendered.images.get(tile),  # noqa: E731
                                       settings, segmenter=threshold_segmenter())
    assert run(nr.NanowellSettings("100um")).timepoints["T0"]["status"] == nr.FORMAT_MISMATCH
    outcome = run(INFER)
    block = outcome.timepoints["T0"]
    assert block["status"] == nr.OK
    assert {(r["nw_q"], r["nw_r"]) for r in outcome.sites} == set(rendered.array.addresses)
    assert np.median([r["radius_um"] for r in outcome.sites]) == pytest.approx(25.0, rel=0.1)
    assert sum(r["geometry"] == "fitted" for r in outcome.sites) > 0.9 * len(outcome.sites)
    assert any("measured at the measured diameter" in n for n in block["notes"])
    assert not any("addresses use the measured nanowell diameter" in n for n in block["notes"])


# ── review X35-R2: the montage path, beyond one 900 px tile ──────────────────
def _hex_2px():
    plan = TilePlan(2, 2, (700, 700), 0.1, 2.0)
    rendered = render_tiles(plan, pitch_um=PITCH, diameter_um=DIAMETER, radius_um=0.26 * (plan.step_um[0]
                            + plan.extent_um[0]), rotation_deg=7.0, noise_sigma=0.05, blur_px=1.0, seed=1,
                            square_centre=False)
    return rendered, record_for(rendered)


@pytest.mark.parametrize("make", [
    lambda: build_well(rotation=-15.0, seed=1, lattice="square", array_shape="circle", square_centre=False,
                       radius_fraction=0.22),
    lambda: build_well(rotation=-15.0, seed=2, lattice="square", array_shape="circle", square_centre=False,
                       radius_fraction=0.22),
    _hex_2px,
], ids=["square-circle-seed1", "square-circle-seed2", "hex-2x2-at-2um"])
def test_a_montage_s_wells_are_all_found_whatever_its_floor_does_under_the_local_split(make):
    """The local split turns the flat floor round the array into large merged blobs, and a ring of them round the
    array once filled the whole array in as one hole: each of these located nothing (``no_lattice``)."""
    rendered, record = make()
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, INFER)
    assert located.status == nr.OK, located.reason
    geometry = located.grid["geometry"]
    assert geometry["n_found"] == geometry["n_sites"] == len(rendered.array.roster)


def test_a_small_square_array_on_one_tile_is_found_whole():
    rendered, image = _image(lattice="square", array_shape="circle", rotation_deg=8.0, radius_um=700.0,
                             square_centre=False, seed=0)
    _, geometry = _infer(image)
    assert geometry.shape == ni.ARRAY_CIRCLE and sum(geometry.found) == len(geometry.found) == 49
    _roster_matches_the_drawing(rendered, geometry)


def test_a_square_lattice_turned_past_its_unambiguous_band_is_refused_with_the_limit_named():
    """Review X35-R6: a square lattice repeats every 90 degrees, so near 45 its axis cannot be chosen the same way
    at every timepoint. Turned 44 degrees it is refused, naming the limit, rather than addressed on either axis."""
    rendered, record = build_well(rows=1, cols=1, tile=(900, 900), rotation=44.0, lattice="square",
                                  array_shape="square", radius_fraction=0.3, square_centre=False)
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, INFER)
    assert located.status == nr.NO_LATTICE and "rotated" in located.reason and "40" in located.reason


def test_every_tile_is_fitted_as_the_centre_tiles_lattice_kind(monkeypatch):
    """Review X35-R7: a sparse edge tile left to choose its kind can choose the other one."""
    from pipeline import nanowell_infer

    kinds = []
    real = nanowell_infer.contrast_fit
    monkeypatch.setattr(nanowell_infer, "contrast_fit",
                        lambda image, px, **k: (kinds.append(k.get("kind", "auto")), real(image, px, **k))[1])
    rendered, record = build_well(rotation=-15.0, seed=1, **_SQUARE)
    nr.locate_timepoint("T0", record, "A01", rendered.images, INFER)
    assert kinds[-4:] == ["square"] * 4                         # the four tiles' registration fits


def test_a_well_whose_timepoints_disagree_on_the_outline_records_mixed():
    """Review X35-R9: the outline is one defined value, ``mixed`` when the timepoints read different ones."""
    from types import SimpleNamespace

    from pipeline.nanowell_lattice import fit_lattice

    points = np.array([(x * 10.0, y * 10.0) for x in range(6) for y in range(6)])
    fit = fit_lattice(points, kind="square")
    located = {tp: SimpleNamespace(array_sites=points, array_found=np.ones(len(points), bool), fit=fit,
                                   origin_montage=(0.0, 0.0), array_shape=shape, notes=[])
               for tp, shape in (("T0", ni.ARRAY_CIRCLE), ("T1", ni.ARRAY_POLYGON))}
    _, block, _ = nr._shared_roster(located, ["T0", "T1"], None)
    assert block["shape"] == "mixed"
    _, block, _ = nr._shared_roster({"T0": located["T0"]}, ["T0"], None)
    assert block["shape"] == ni.ARRAY_CIRCLE


def test_an_earlier_run_that_anchored_nothing_leaves_no_roster_to_keep():
    """A well whose earlier run anchored no timepoint recorded an empty roster; re-anchored from the rest of the
    plate, its timepoints must get the roster they now support, not the empty one."""
    rendered, record = build_well(lattice="square", array_shape="square", rotation=-15.0, radius_fraction=0.22,
                                  square_centre=False)
    outcome = nr.run_well("A01", record, ["T0"], lambda tp, tile: rendered.images.get(tile), INFER,
                          segmenter=threshold_segmenter(), fixed_roster=[])
    assert {(r["nw_q"], r["nw_r"]) for r in outcome.sites} == set(rendered.array.addresses)
