"""Section F of the nanowell tracker, on rendered tiles through ``locate_timepoint``'s real sequence.

The first real plate left most wells unaddressed and said nothing about the central square. These tests drive the
same chain the pipeline and Test detection run, with one input injected where a real plate differs from the
rendered one, and read what the run writes.
"""

from dataclasses import replace

import numpy as np

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
