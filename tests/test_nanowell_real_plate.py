"""Section F of the nanowell tracker, on rendered tiles through ``locate_timepoint``'s real sequence.

The first real plate left most wells unaddressed and said nothing about the central square. These tests drive the
same chain the pipeline and Test detection run, with one input injected where a real plate differs from the
rendered one, and read what the run writes.
"""

from dataclasses import replace

import numpy as np
import pytest

from pipeline import nanowell_detect as nd
from pipeline import nanowell_run as nr
from tests._nanowell_run_fixture import PITCH, PX, build_well, record_for, threshold_segmenter
from tests._nanowell_synth import TilePlan, render_tiles

#: The first real plate's measured geometry (well D07): finer than the 100 um format's nominal 172 um pitch, and
#: wells a quarter wider than its 100 um, so the walls between them are 27 um where the format's are 72.
PLATE_PITCH, PLATE_DIAMETER = 152.6, 125.8


def _plate(*, timepoint="T0", blur=1.0, seed=1, rotation=7.0, shift=(0.0, 0.0), square=True):
    plan = TilePlan(2, 2, (700, 700), 0.1, PX)
    rendered = render_tiles(plan, pitch_um=PLATE_PITCH, diameter_um=PLATE_DIAMETER, radius_um=700.0,
                            rotation_deg=rotation, noise_sigma=0.05, blur_px=blur, seed=seed, array_shift_um=shift,
                            square_centre=square)
    return rendered, record_for(rendered, timepoint=timepoint)

SETTINGS = nr.NanowellSettings("100um")


def _centre_off_site(monkeypatch):
    """The array's centre half a pitch from every site, as a fitted rim the tiles cut places it (D07: 47 %)."""
    real = nd.detect_sites

    def shifted(*args, **kwargs):
        found = real(*args, **kwargs)
        if not found.ok or found.boundary_centre_um is None:
            return found
        a1 = np.asarray(found.fit.a1, dtype=float)
        return replace(found, boundary_centre_um=tuple(np.asarray(found.boundary_centre_um) + a1 / 2))
    monkeypatch.setattr(nd, "detect_sites", shifted)


def test_a_well_with_no_square_says_why_the_square_was_refused(monkeypatch):
    rendered, record = build_well(square_centre=False)
    _centre_off_site(monkeypatch)
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    assert located.status == nr.UNADDRESSED
    block = located.grid["fiducial"]
    assert block["best"] is not None and block["best"] < nd.MIN_SQUARENESS and block["scored"] >= 2
    sentence = (f"the central square was not found: the most square outline among the {block['scored']} sites "
                f"scored near the array centre reads {block['best']:.2f}, under the {nd.MIN_SQUARENESS:g} a square "
                "needs")
    assert block["reason"] == sentence
    assert located.reason.startswith("No accepted fiducial, array centre, or precise plate-rigid prediction; "
                                     + sentence + "; the array_centre lies ")
    assert located.grid["reason"] == located.reason and block["margin"] == nr.FIDUCIAL_MARGIN


def test_a_found_square_is_recorded_where_it_was_drawn():
    rendered, record = build_well()
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    assert located.status == nr.OK and located.origin_method == "fiducial"
    block = located.grid["fiducial"]
    assert block["reason"] == "" and block["best"] >= nd.MIN_SQUARENESS
    assert block["best"] - block["runner_up"] > nr.FIDUCIAL_MARGIN
    assert np.hypot(*(np.asarray(block["position_um"]) - rendered.array.origin)) < PITCH / 10


def test_the_block_is_written_to_the_grid_record_as_plain_numbers():
    import json

    rendered, record = build_well()
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    again = json.loads(json.dumps(located.grid["fiducial"]))
    assert again == located.grid["fiducial"]


# ── F2-F4: the plate's diameter is measured, never assumed ───────────────────
def test_the_plate_s_own_diameter_is_measured_and_every_site_is_measured_at_it():
    rendered, record = _plate()
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    assert located.status == nr.OK
    geometry = located.grid["geometry"]
    # Within 1 %: the montage's contrast blobs read these wells 9 % small, the format says 100.
    assert abs(located.diameter_um / PLATE_DIAMETER - 1) < 0.01
    assert geometry["diameter_used_um"] == located.diameter_um == geometry["diameter_check"]["measured_um"]
    assert geometry["diameter_check"]["agrees"] is False
    assert located.notes == [geometry["diameter_check"]["note"]]
    assert located.notes[0].startswith(f"The measured nanowell diameter ({located.diameter_um:.1f} um) disagrees with "
                                       "the 100 um the settings give")
    measured = nr.measure_timepoint(located, rendered.images, SETTINGS, 700.0, count_cells=False)
    # The real plate's run left 1,206 of 1,253 sites unmeasured: the template was sized from 100 um.
    assert not [n for n in measured.notes if "exceed the template" in n]
    fitted = [r["radius_um"] for r in measured.sites if r["geometry"] == nd.FITTED]
    assert len(fitted) > 50 and abs(2 * float(np.median(fitted)) / PLATE_DIAMETER - 1) < 0.01


def test_a_plate_that_matches_its_format_is_measured_too_and_says_nothing():
    rendered, record = build_well()
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    check = located.grid["geometry"]["diameter_check"]
    assert check["agrees"] is True and check["note"] == "" and located.notes == []
    # Measured, not the setting's 100 with a measurement beside it.
    assert located.diameter_um == check["measured_um"] != SETTINGS.diameter_um
    assert abs(located.diameter_um / 100.0 - 1) < 0.01


def test_a_diameter_that_cannot_be_measured_falls_back_to_the_format_and_says_so(monkeypatch):
    rendered, record = _plate()
    monkeypatch.setattr(nr, "MIN_DIAMETER_FITS", 10_000)
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    assert located.diameter_um == SETTINGS.diameter_um
    assert located.grid["geometry"]["diameter_check"]["measured_um"] is None
    assert any(n.startswith("The nanowell diameter could not be measured (") and
               n.endswith("10000 needed): the plate format's 100 um is used") for n in located.notes)


def test_one_diameter_serves_every_timepoint_of_a_well():
    first, record = _plate()
    # The same plate put back a little shifted and imaged softer: its own fit reads the wells a little smaller.
    second, second_record = _plate(timepoint="T1", blur=2.5, seed=2, rotation=8.0, shift=(60.0, -40.0))
    record["timepoints"].update(second_record["timepoints"])
    record["tiles"] += second_record["tiles"]
    images = {("T0", t): v for t, v in first.images.items()} | {("T1", t): v for t, v in second.images.items()}
    outcome = nr.run_well("A01", record, ["T0", "T1"], lambda tp, tile: images.get((tp, tile)), SETTINGS,
                          segmenter=threshold_segmenter())
    own = [outcome.timepoints[tp]["geometry"]["diameter_check"]["measured_um"] for tp in ("T0", "T1")]
    assert own[0] != own[1]
    used = {outcome.timepoints[tp]["geometry"]["diameter_used_um"] for tp in ("T0", "T1")}
    assert used == {float(np.median(own))} == {outcome.radius["diameter_used_um"]}
    assert not [n for tp in ("T0", "T1") for n in outcome.timepoints[tp].get("notes", []) if "exceed the template" in n]


# ── F5: the square on a plate with thin walls ────────────────────────────────
def test_the_square_anchors_a_plate_whose_walls_are_thin():
    rendered, record = _plate()
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    assert located.origin_method == "fiducial", located.grid["fiducial"]
    block = located.grid["fiducial"]
    assert np.hypot(*(np.asarray(block["position_um"]) - rendered.array.origin)) < PLATE_PITCH / 10


# ── F13: registration fits each tile at the plate's diameter ─────────────────
def test_tiles_are_registered_at_the_plate_s_diameter():
    """The expert review measured tiles 33 um off on this geometry when each tile's lattice was fitted with a 100 um
    template: the response is flat-topped on a 125.8 um well, and the lattice-phase steps between tiles (about eight
    pitches) multiply the error. The overlap correlation was right and was overruled."""
    plan = TilePlan(3, 3, (705, 520), 0.1, 2.0)              # 1410 x 1040 um tiles, the real plate's
    rendered = render_tiles(plan, pitch_um=PLATE_PITCH, diameter_um=PLATE_DIAMETER, radius_um=2000.0,
                            rotation_deg=-0.3, noise_sigma=0.05, blur_px=1.0, seed=1, square_centre=False)
    located = nr.locate_timepoint("T0", record_for(rendered), "A01", rendered.images, SETTINGS)
    errors = np.array([np.subtract(located.positions[t], rendered.true_left_top_um[t]) for t in located.positions])
    errors -= errors.mean(axis=0)                            # the common frame's own offset is free
    assert float(np.abs(errors).max()) < 4.0, errors.round(1)
    assert not [w for w in located.grid.get("registration_warnings", []) if "disagree" in w]
    assert located.grid["registration"]["key"] == nr.registration_key(
        record_for(rendered), "A01", "T0", rendered.images, SETTINGS, located.grid["pitch_seed_um"], "auto",
        located.diameter_um)


# ── F14: the square is judged by how far it stands above the rest ───────────
def test_a_soft_square_under_the_old_floor_still_anchors_the_well(monkeypatch):
    """Defocus takes a rendered square to 0.37 against a runner-up of 0.06: it stands six times above every circle,
    and the old absolute floor of 0.4 refused it. The array centre is put half a pitch off, so only the square can
    anchor the well."""
    rendered, record = _plate(blur=4.0)
    _centre_off_site(monkeypatch)
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    block = located.grid["fiducial"]
    assert 0.25 < block["best"] < 0.4 and block["best"] >= 3 * block["runner_up"]
    assert located.status == nr.OK and located.origin_method == "fiducial"
    assert (block["margin"], block["ratio"]) == (nr.FIDUCIAL_MARGIN, nr.FIDUCIAL_RATIO)


# ── F16: site edges on thin walls with a phase halo ──────────────────────────
def _haloed_patch(*, halo=0.0, inner_dark=0.0, px=0.69, size_um=700.0):
    """A patch of the real plate's lattice at full resolution, with the rings phase contrast puts at a rim: a bright
    halo just outside it and a dark ring just inside (the expert review's model). Returns the image and the centres
    of the sites wholly inside it."""
    import math

    from scipy import ndimage

    n = int(size_um / px)
    centre = np.array([n * px / 2] * 2)
    basis = np.array([[PLATE_PITCH, PLATE_PITCH / 2], [0.0, -math.sqrt(3) * PLATE_PITCH / 2]])
    xs = (np.arange(n) + 0.5) * px
    gx, gy = np.meshgrid(xs, xs)
    pos = np.stack((gx, gy), -1) - centre
    base = np.round(pos @ np.linalg.inv(basis).T)
    nearest = np.full((n, n), np.inf)
    for dq in (-1, 0, 1):
        for dr in (-1, 0, 1):
            nearest = np.minimum(nearest, np.linalg.norm(pos - (base + (dq, dr)) @ basis.T, axis=-1))
    edge = nearest - PLATE_DIAMETER / 2                              # signed distance from the rim, um
    image = 0.2 + 0.8 / (1 + np.exp(np.clip(edge / 0.7, -50, 50)))
    image += halo * np.exp(-((edge - 3) / 2.5) ** 2) - inner_dark * np.exp(-((edge + 3) / 2.5) ** 2)
    image = ndimage.gaussian_filter(image, 0.7 / px) + np.random.default_rng(0).normal(0, 0.03, image.shape)
    sites = np.array([centre + basis @ (a, b) for a in range(-3, 4) for b in range(-3, 4)])
    return image, sites[(sites.min(axis=1) > 100) & (sites.max(axis=1) < n * px - 100)]


@pytest.mark.parametrize("halo, inner_dark, at_least", [(0.4, 0.0, 11), (0.4, 0.4, 9)])
def test_a_haloed_rim_on_thin_walls_is_fitted_at_the_measured_diameter(halo, inner_dark, at_least):
    """Seeded at the right diameter, the wall zone (1.3 to 1.7 radii) reached into the neighbour, whose rim is at 1.43
    radii, and a halo left no edge clearing the contrast: 0 of 11 sites fitted with a halo, 3 to 7 with a halo and a
    dark inner ring. Measurement seeds at the measured diameter since F2, so a haloed plate would have most sites
    predicted rather than fitted."""
    image, sites = _haloed_patch(halo=halo, inner_dark=inner_dark)
    assert len(sites) == 11
    fitted = [g.radius_um for g in nd.refine_sites(image, 0.69, sites, PLATE_DIAMETER, pitch_um=PLATE_PITCH)
              if g.geometry == nd.FITTED]
    assert len(fitted) >= at_least
    assert abs(2 * float(np.median(fitted)) - PLATE_DIAMETER) < 2.0      # the halo biases the edge by about 1 um


def test_measurement_fits_sites_with_the_plate_s_pitch(monkeypatch):
    """The cap needs the pitch: the call that fits every site at full resolution must pass the one located."""
    rendered, record = _plate()
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    seen = []
    real = nd.refine_sites

    def spy(*args, **kwargs):
        seen.append(kwargs.get("pitch_um"))
        return real(*args, **kwargs)
    monkeypatch.setattr(nd, "refine_sites", spy)
    nr.measure_timepoint(located, rendered.images, SETTINGS, 700.0, count_cells=False)
    assert seen and set(seen) == {located.pitch_um}


# ── F19: the square's edge angle is recorded, refused or not ─────────────────
def test_the_square_s_edge_angle_is_recorded_whether_the_timepoint_is_kept_or_refused():
    """The orientation check stays a refusal (it catches a lattice fit folded onto the wrong row, and the user
    confirmed the square's edges run along the plate's: Q11). What a real plate needs to confirm that is the angle
    the square was measured at against the row the fit chose, on the grid record either way."""
    rendered, record = build_well(rows=1, cols=1, tile=(900, 900))
    kept = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    block = kept.grid["fiducial"]
    assert kept.status == nr.OK and abs(block["edge_discrepancy_deg"]) < 3.0
    assert block["edge_deg"] == pytest.approx(block["edge_expected_deg"] + block["edge_discrepancy_deg"], abs=1e-6)
    refused = nr.locate_timepoint("T0", record, "A01", rendered.images,
                                  nr.NanowellSettings("100um", square_edge_deg=30.0))
    assert refused.status == nr.ORIENTATION_REFUSED
    again = refused.grid["fiducial"]
    assert again["edge_deg"] == pytest.approx(block["edge_deg"])
    assert abs(abs(again["edge_discrepancy_deg"]) - 30.0) < 3.0


# ── F8: an unaddressed timepoint still has a row for every nanowell ──────────
def test_an_unaddressed_timepoint_measures_every_nanowell_of_its_array_without_addresses(monkeypatch):
    """Without an origin the rows were the sites detection found, so a nanowell dark under a bubble had no row at all,
    against the rule that no nanowell's position is ever skipped. The lattice and the array's outline place every
    one; only the address needs the origin."""
    from pipeline import nanowell_lattice as lattice

    hidden = [(1, 0), (0, 1), (-1, 1)]
    rendered, record = build_well(dark_sites=hidden)
    real_resolve = lattice.resolve_origin
    monkeypatch.setattr(lattice, "resolve_origin", lambda fit, *, fiducial=None, array_centre=None, plate_rigid=None,
                        **rest: real_resolve(fit, plate_rigid=plate_rigid, **rest))
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    assert located.status == nr.UNADDRESSED
    radius = located.boundary["radius_um"]
    measured = nr.measure_timepoint(located, rendered.images, SETTINGS, radius, count_cells=False)
    assert all(r["nw_q"] is None and r["nw_r"] is None for r in measured.sites)
    rows = np.array([(r["well_x_um"], r["well_y_um"]) for r in measured.sites])
    truth = dict(zip(rendered.array.addresses, rendered.sites_um))

    def row_at(xy):
        d = np.hypot(*(rows - xy).T)
        return int(np.argmin(d)) if d.min() < PITCH / 4 else None
    assert all(row_at(xy) is not None for xy in truth.values())          # every drawn nanowell has a row
    assert len(measured.sites) == len(truth)                             # and nothing beyond the array does
    for address in hidden:
        row = measured.sites[row_at(truth[address])]
        assert row["detected"] is False and row["state"] == "occluded"


# ── F15: a square of another size than the nanowells ────────────────────────
def _square_of_side(rendered, side_um, rotation_deg=7.0):
    """Redraw the central site of *rendered* (``build_well``'s plate) as a bright square *side_um* across, edges along
    the lattice's rows: the renderer draws it exactly one nanowell across, and a real mold need not."""
    from scipy import ndimage

    from tests._nanowell_synth import _BACKGROUND, _FLOOR, _NANOWELL, _screen_rotation

    del _BACKGROUND
    pose = _screen_rotation(rotation_deg)
    origin = np.asarray(rendered.array.origin, dtype=float)
    px = rendered.plan.pixel_size_um
    for name, image in rendered.images.items():
        left, top = rendered.true_left_top_um[name]
        h, w = image.shape
        gx, gy = np.meshgrid(left + (np.arange(w) + 0.5) * px, top + (np.arange(h) + 0.5) * px)
        local = np.stack((gx, gy), axis=-1) - origin
        cell = np.hypot(local[..., 0], local[..., 1]) < 0.5 * PITCH
        if not cell.any():
            continue
        square = 1 / (1 + np.exp(np.clip(-(side_um / 2 - np.abs(local @ pose).max(axis=-1)) / max(px, 1.0), -50, 50)))
        drawn = ndimage.gaussian_filter(_FLOOR + (_NANOWELL - _FLOOR) * square, 1.0)
        image[cell] = drawn[cell].astype(image.dtype)


@pytest.mark.parametrize("side_um, read_at", [(60.0, (0.45, 0.6)), (150.0, (1.0, 1.2))])
def test_a_square_of_another_size_than_the_nanowells_still_anchors_the_well(monkeypatch, side_um, read_at):
    """The square was only ever scored where the round-nanowell filter had found a site, and only read at the
    nanowell's size: a 60 or 150 um square among 100 um wells was not a site at all, and the well fell to its array
    centre (here put half a pitch off, so that it cannot)."""
    rendered, record = build_well()
    _square_of_side(rendered, side_um)
    _centre_off_site(monkeypatch)
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    assert located.status == nr.OK and located.origin_method == "fiducial", located.reason
    block = located.grid["fiducial"]
    assert np.hypot(*(np.asarray(block["position_um"]) - rendered.array.origin)) < PITCH / 10
    # Read at its own size: a small square by a small ring; a large one as large as its neighbours leave room for.
    assert block["size_factor"] in read_at


def test_no_square_drawn_is_no_square_found_at_any_size():
    rendered, record = build_well(square_centre=False)
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    assert located.origin_method == "array_centre" and located.grid["fiducial"]["best"] < nd.MIN_SQUARENESS


# ── F20: straight-edged debris inside ordinary nanowells is not the square ──
def _rectangles(rendered, pieces, rotation_deg=7.0):
    """Draw hard-edged rectangles of debris: each ``(address, width_um, height_um, turn_deg, value)`` centred on that
    site, turned from the lattice's rows by *turn_deg*, *value* bright or dark."""
    from tests._nanowell_synth import _screen_rotation

    truth = dict(zip(rendered.array.addresses, rendered.sites_um))
    px = rendered.plan.pixel_size_um
    for name, image in rendered.images.items():
        left, top = rendered.true_left_top_um[name]
        h, w = image.shape
        gx, gy = np.meshgrid(left + (np.arange(w) + 0.5) * px, top + (np.arange(h) + 0.5) * px)
        for address, width, height, turn, value in pieces:
            local = (np.stack((gx, gy), axis=-1) - truth[address]) @ _screen_rotation(rotation_deg + turn)
            inside = (np.abs(local[..., 0]) < width / 2) & (np.abs(local[..., 1]) < height / 2)
            image[inside] = value


def test_straight_edged_debris_in_ordinary_nanowells_is_never_taken_for_the_square(monkeypatch):
    """Reading every site at sizes smaller than a nanowell made straight-edged debris inside an ordinary one read as a
    small square: with five pieces and no square drawn, 4 of 10 rendered plates were anchored on debris. A smaller
    square must now be light against its surround, as a nanowell is. The real plate's geometry, no square, the array
    centre put off so a false square is the only anchor; without the guard the first layout anchors on the dark
    rectangle at 0.79 against 0.10. (A layout of four mixed pieces was dropped: it anchored nothing with or without
    the guard, so it measured nothing.)"""
    rendered, record = _plate(square=False)
    _rectangles(rendered, [((0, 0), 70.0, 70.0, 2.0, 0.05)])         # dark, squarely aligned, in the centre well
    _centre_off_site(monkeypatch)
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    assert located.origin_method != "fiducial", located.grid["fiducial"]


def test_a_ring_read_larger_than_a_nanowell_stops_at_the_neighbours_on_a_square_lattice():
    """Read at 1.2 diameters, the ring's floor of 1.2 radii reached 1.44 nanowell radii, past the neighbours' rims at
    1.43 on the real plate's walls; on a hexagonal lattice six arcs cancel in the four-fold sum, on a square one four
    add up, and every site picked up squareness from its neighbours (0.11 against 0.04 measured in review)."""
    from scipy import ndimage

    px, n = 2.0, 600
    xs = (np.arange(n) + 0.5) * px
    gx, gy = np.meshgrid(xs, xs)
    nearest_x = np.round(gx / PLATE_PITCH) * PLATE_PITCH
    nearest_y = np.round(gy / PLATE_PITCH) * PLATE_PITCH
    rim = np.hypot(gx - nearest_x, gy - nearest_y) - PLATE_DIAMETER / 2
    image = ndimage.gaussian_filter(0.15 + 0.7 / (1 + np.exp(np.clip(rim / px, -50, 50))), 1.5)
    site = (4 * PLATE_PITCH, 4 * PLATE_PITCH)
    _, scores = nd.score_fiducial(image, px, PLATE_DIAMETER, [site], pitch_um=PLATE_PITCH, size_factors=(1.2,))
    assert all(s.coherence < 0.06 for s in scores)              # 0.116 with the ring reaching the neighbours
