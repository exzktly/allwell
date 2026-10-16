"""The (well, timepoint) task body on rendered tiles, against the array that was drawn."""

from pathlib import Path

import numpy as np
import pytest

from pipeline import nanowell_measure as nm
from pipeline import nanowell_run as nr
from tests._nanowell_run_fixture import (
    build_well, paint_cell, record_for, threshold_segmenter,
)

SETTINGS = nr.NanowellSettings("100um")


@pytest.fixture(scope="module")
def well():
    rendered, record = build_well()
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    return rendered, record, located


def test_a_complete_grid_is_located_and_every_detected_site_gets_the_address_it_was_drawn_with(well):
    rendered, _, located = well
    assert located.status == nr.OK and located.origin_method == "fiducial"
    truth = {tuple(map(float, s)): a for s, a in zip(rendered.sites_um, rendered.array.addresses)}
    sites = np.array(list(truth))
    wrong = unaddressed = 0
    for point, entry in zip(located.detected_common, located.indexed):
        nearest = tuple(sites[np.argmin(np.linalg.norm(sites - point, axis=1))])
        if entry.q is None:
            unaddressed += 1
        elif (entry.q, entry.r) != truth[nearest]:
            wrong += 1
    assert wrong == 0 and unaddressed <= 0.02 * len(located.indexed)
    assert located.grid["status"] == nr.OK and len(located.grid["tiles"]) == 4


def test_a_missing_tile_is_an_incomplete_grid_and_nothing_is_processed():
    rendered, _ = build_well()
    record = record_for(rendered, drop=("F3",))
    images = {k: v for k, v in rendered.images.items() if k != "F3"}
    located = nr.locate_timepoint("T0", record, "A01", images, SETTINGS)
    assert located.status == nr.INCOMPLETE_GRID and "F3" not in located.positions or located.status == nr.BAD_GRID
    assert located.fit is None and located.indexed == ()


def test_a_tile_in_the_record_without_an_image_is_named():
    rendered, record = build_well()
    images = {k: v for k, v in rendered.images.items() if k != "F2"}
    located = nr.locate_timepoint("T0", record, "A01", images, SETTINGS)
    assert located.status == nr.NO_IMAGES and "F2" in located.reason


def test_a_timepoint_the_record_does_not_hold_has_a_status_not_an_exception(well):
    rendered, record, _ = well
    located = nr.locate_timepoint("T9", record, "A01", rendered.images, SETTINGS)
    assert located.status == nr.NO_RECORD


def test_the_wrong_plate_format_is_refused_by_name(well):
    rendered, record, _ = well
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, nr.NanowellSettings("50um"))
    assert located.status in (nr.FORMAT_MISMATCH, nr.NO_LATTICE) and located.indexed == ()


def test_every_roster_site_has_a_row_with_its_address_and_the_cell_painted_into_it_is_counted_once():
    rendered, record = build_well()
    truth = dict(zip(rendered.array.addresses, rendered.sites_um))
    target = (1, 2)
    paint_cell(rendered, truth[target], dx_um=3.0, dy_um=-2.0)
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    measured = nr.measure_timepoint(located, rendered.images, SETTINGS, located.boundary["radius_um"],
                                    segmenter=threshold_segmenter())
    rows = {(r["nw_q"], r["nw_r"]): r for r in measured.sites}
    assert set(truth) <= set(rows)                                       # no drawn site lacks a row
    assert rows[target]["n_cells"] == 1 and rows[target]["state"] == "measured"
    others = [r for a, r in rows.items() if a != target and r["state"] == "empty"]
    assert len(others) > 0.9 * len(truth) and all(r["n_cells"] == 0 for r in others)
    assert len(measured.cells["label"]) == 1
    assert (measured.cells["nw_ring"][0]) == nr.hex_ring(*target)
    assert np.hypot(measured.cells["well_x_um"][0] - truth[target][0], measured.cells["well_y_um"][0] - truth[target][1]) < 10
    assert measured.cells["fov"][0] in {"F0", "F1", "F2", "F3"}


def test_a_cell_in_the_overlap_of_two_tiles_is_counted_by_one_row_only():
    rendered, record = build_well()
    # A site on the seam between F0 and F1 (x = the second tile's left edge + half the overlap).
    seam_x = rendered.true_left_top_um["F1"][0] + 0.5 * rendered.plan.overlap * rendered.plan.extent_um[0]
    sites = np.asarray(rendered.sites_um)
    pick = int(np.argmin(np.abs(sites[:, 0] - seam_x) + 0.2 * np.abs(sites[:, 1] - rendered.plan.extent_um[1] / 2)))
    paint_cell(rendered, sites[pick])
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    measured = nr.measure_timepoint(located, rendered.images, SETTINGS, located.boundary["radius_um"],
                                    segmenter=threshold_segmenter())
    total = sum(r["n_cells"] for r in measured.sites if r["n_cells"])
    assert total == 1 and len(measured.cells["label"]) == 1


def test_a_fused_site_has_an_undefined_count_not_zero():
    # 3 % overlap (90 um) is less than a nanowell (100 um) + margin: sites on the seam cannot be owned.
    rendered, record = build_well(overlap=0.03)
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    measured = nr.measure_timepoint(located, rendered.images, SETTINGS, located.boundary["radius_um"],
                                    segmenter=threshold_segmenter())
    fused = [r for r in measured.sites if r["fused"]]
    assert fused and all(r["n_cells"] is None and r["state"] == "measured" for r in fused)
    assert all(r["owner"] is None for r in fused)


def _loader(rendered):
    return lambda tp, tile: rendered.images.get(tile)


def test_a_well_runs_every_timepoint_against_one_shared_roster_and_joins_them_by_address():
    rendered, record = build_well()
    # The same plate imaged again after being taken out and put back: shifted and rotated a little.
    again, again_record = build_well(rotation=9.0, array_shift_um=(120.0, -80.0), seed=2, timepoint="T1")
    record["timepoints"].update(again_record["timepoints"])
    record["tiles"] += again_record["tiles"]
    images = {("T0", t): v for t, v in rendered.images.items()} | {("T1", t): v for t, v in again.images.items()}
    outcome = nr.run_well("A01", record, ["T0", "T1"], lambda tp, tile: images.get((tp, tile)), SETTINGS,
                          segmenter=threshold_segmenter())
    assert [outcome.timepoints[t]["status"] for t in ("T0", "T1")] == ["ok", "ok"]
    rows = {tp: {(r["nw_q"], r["nw_r"]) for r in outcome.sites if r["timepoint"] == tp} for tp in ("T0", "T1")}
    assert rows["T0"] == rows["T1"] and len(rows["T0"]) > 200            # one roster for the well
    truth = set(rendered.array.addresses)
    assert truth <= rows["T0"]
    assert outcome.radius["status"] == "shared" and outcome.radius["radius_um"] > 0
    assert outcome.timepoints["T1"]["consistency"] == "consistent"
    assert all(r["state"] in {"empty", "measured", "occluded", "clipped"} for r in outcome.sites)


def test_an_incomplete_timepoint_keeps_its_status_and_the_others_still_run():
    rendered, record = build_well()
    broken, broken_record = build_well(timepoint="T1")
    broken_record = record_for(broken, timepoint="T1", drop=("F3",))
    record["timepoints"].update(broken_record["timepoints"])
    record["tiles"] += broken_record["tiles"]
    images = {("T0", t): v for t, v in rendered.images.items()} | {("T1", t): v for t, v in broken.images.items()}
    outcome = nr.run_well("A01", record, ["T0", "T1"], lambda tp, tile: images.get((tp, tile)), SETTINGS,
                          segmenter=threshold_segmenter())
    assert outcome.timepoints["T0"]["status"] == "ok"
    assert outcome.timepoints["T1"]["status"] in (nr.INCOMPLETE_GRID, nr.BAD_GRID)
    assert {r["timepoint"] for r in outcome.sites} == {"T0"}


def test_an_unaddressed_timepoint_keeps_its_detected_sites_with_no_address():
    rendered, record = build_well()
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    located.status = nr.UNADDRESSED                                       # as if no origin evidence had held
    measured = nr.measure_timepoint(located, rendered.images, SETTINGS, located.boundary["radius_um"],
                                    segmenter=threshold_segmenter())
    assert measured.sites and all(r["nw_q"] is None and r["nw_r"] is None for r in measured.sites)
    assert any(r["n_cells"] == 0 for r in measured.sites)                   # still measured, just not joinable


def test_a_fixed_radius_decides_the_roster_not_the_timepoints_own_boundary():
    rendered, record = build_well(rows=1, cols=1, tile=(900, 900))
    load = lambda tp, tile: rendered.images.get(tile)           # noqa: E731
    own = nr.run_well("A01", record, ["T0"], load, SETTINGS, segmenter=threshold_segmenter())
    radius = own.radius["radius_um"]
    fixed = nr.run_well("A01", record, ["T0"], load, SETTINGS, segmenter=threshold_segmenter(),
                        fixed_radius_um=0.6 * radius)
    assert fixed.radius["radius_um"] == pytest.approx(0.6 * radius) and fixed.radius["status"] == "shared"
    assert len(fixed.sites) < 0.6 * len(own.sites)                  # ~0.36 of the area
    assert fixed.timepoints["T0"]["radius_deviation_um"] == pytest.approx(radius - 0.6 * radius, rel=0.05)


def _block(ratio, **template):
    return {"status": "ok", "notes": ["kept note"],
            "template": {"source": "own", "n_sites": 10, "candidates": 40, "floor_ratio": ratio, **template}}


def test_drift_is_measured_against_the_earliest_timepoint_in_time_not_the_first_one_measured():
    """The blocks arrive kept-first or measured-first; the earliest is the first in time with a ratio of its own
    (24h fell back to an earlier template and has none). 48h's floor moved threefold: suspect, and noted once
    however often the pass runs."""
    blocks = {"48h": _block(3.0), "72h": _block(1.2), "24h": {"status": "ok", "template": {"source": "earliest"}},
              "0h": _block(1.0)}
    order = ["0h", "24h", "48h", "72h"]
    nr.flag_template_drift(blocks, order)
    nr.flag_template_drift(blocks, order)
    late, fine = blocks["48h"]["template"], blocks["72h"]["template"]
    assert (late["earliest_timepoint"], late["suspect"]) == ("0h", True)
    assert late["drift_vs_earliest"] == pytest.approx(np.log(3.0))
    assert [n for n in blocks["48h"]["notes"] if n.startswith("The empty-nanowell template departs")] == [
        "The empty-nanowell template departs from the earliest timepoint's (0h): its floor against the surround "
        "is 3.00x the earliest's (beyond a factor of two). Most nanowells here may hold cells, so the template may "
        "be cell-coloured and this timepoint's counts and confluence biased"]
    assert blocks["48h"]["notes"][0] == "kept note"
    assert fine["drift_vs_earliest"] == pytest.approx(np.log(1.2)) and "suspect" not in fine
    assert "drift_vs_earliest" not in blocks["24h"]["template"] and "earliest_timepoint" not in blocks["0h"]["template"]


def test_a_floor_that_darkens_is_flagged_as_well_as_one_that_brightens():
    blocks = {"0h": _block(1.0), "24h": _block(0.4)}
    nr.flag_template_drift(blocks, ["0h", "24h"])
    assert blocks["24h"]["template"]["suspect"] is True


def test_a_template_measured_now_carries_its_floor_ratio_for_later_runs():
    rendered, record = build_well()
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    measured = nr.measure_timepoint(located, rendered.images, SETTINGS, located.boundary["radius_um"],
                                    segmenter=threshold_segmenter())
    assert measured.template["floor_ratio"] == pytest.approx(
        nm.floor_ratio(measured.template["_object"])) and measured.template["floor_ratio"] > 0


def test_a_site_whose_interior_is_off_its_owner_tile_s_image_is_clipped_not_empty():
    """Ownership is decided on the tiles' rectangles in um; the contents are read from image pixels. When the
    image holds less than its rectangle (here F0 cut short by 50 px, 215 um, within the 301 um overlap), the
    sites F0 owns along that edge have under half their interior on the image: a count from what is left is not a count (tracker C15)."""
    rendered, record = build_well()
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    images = dict(rendered.images)
    images["F0"] = images["F0"][:, :-50]
    measured = nr.measure_timepoint(located, images, SETTINGS, located.boundary["radius_um"],
                                    segmenter=threshold_segmenter())
    width_um = images["F0"].shape[1] * located.pixel_size_um
    left = located.positions["F0"][0]
    whole = nr.measure_timepoint(located, rendered.images, SETTINGS, located.boundary["radius_um"],
                                 segmenter=threshold_segmenter())
    before = {(r["nw_q"], r["nw_r"]): r["state"] for r in whole.sites}
    # Sites F0 owns whose centre is now off its image, and which were seen whole before the cut.
    cut = [r for r in measured.sites if r["owner"] == "F0" and r["well_x_um"] - left > width_um
           and before[(r["nw_q"], r["nw_r"])] in ("empty", "measured")]
    assert cut, "no seen F0 site has its centre off the shortened image"
    assert all(r["state"] == "clipped" and r["n_cells"] is None and r["confluence"] is None for r in cut)
    kept = {(r["nw_q"], r["nw_r"]): r["state"] for r in measured.sites
            if r["owner"] == "F0" and r["well_x_um"] - left < width_um - 60}
    assert kept and all(state == before[a] for a, state in kept.items())                # nothing else moved


@pytest.mark.parametrize("true_pitch, px", [(143.0, 3.0), (86.0, 2.15)])
def test_a_50um_array_registers_on_its_lattice_whichever_pitch_it_really_has(true_pitch, px, monkeypatch):
    """The 50 µm format's nominal pitch (143 µm, tracker N25) is a guess at the plate. Seeding the per-tile fits
    with it directly made every tile's fit fail on an 86 µm array (and the old 86 µm seed did the same on a 143 µm
    one), leaving registration with no lattice constraint and saying nothing. The pitch is measured on the tiles
    first, so every tile fits and is placed on the lattice either way."""
    from tests import _nanowell_run_fixture as fx
    from tests._nanowell_synth import TilePlan, render_tiles

    monkeypatch.setattr(fx, "PX", px)
    plan = TilePlan(2, 2, (700, 700), 0.15, px)
    extent = (plan.step_um[0] + plan.extent_um[0], plan.step_um[1] + plan.extent_um[1])
    rendered = render_tiles(plan, pitch_um=true_pitch, diameter_um=50.0, radius_um=0.3 * min(extent),
                            rotation_deg=7.0, noise_sigma=0.05, blur_px=1.0, seed=1)
    located = nr.locate_timepoint("T0", record_for(rendered), "A01", rendered.images, nr.NanowellSettings("50um"))
    grid = located.grid
    assert located.status == nr.OK
    assert (grid["pitch_seed_source"], grid["tile_lattice_fits"]) == ("tiles", [4, 4])
    assert grid["pitch_seed_um"] == pytest.approx(true_pitch, rel=0.03)    # a seed: the fits keep ±20 % of it
    assert located.pitch_um == pytest.approx(true_pitch, rel=0.005)        # the fit's own pitch
    assert [t["source"] for t in grid["tiles"]] == ["lattice"] * 4 and grid["registration_warnings"] == []


def test_a_timepoint_where_no_tile_s_lattice_fits_says_so(monkeypatch):
    rendered, record = build_well()
    monkeypatch.setattr(nr, "working_pitch", lambda *a, **k: (300.0, "nominal"))      # far from the array's 172
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    assert located.grid["tile_lattice_fits"] == [0, 4]
    assert located.grid["registration_warnings"][-1].startswith(
        "No tile's lattice could be fitted (working pitch 300.0 um, nominal): every tile was placed")


def test_a_checkpoint_gives_back_the_timepoint_s_own_template(tmp_path):
    """A resumed run reads its earliest timepoint back from the checkpoint; with the template object kept beside
    it, that timepoint can still be the fallback for a later, crowded one (tracker C16)."""
    from pipeline.nanowell_task import TimepointCheckpoints

    rendered, record = build_well()
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    measured = nr.measure_timepoint(located, rendered.images, SETTINGS, located.boundary["radius_um"],
                                    segmenter=threshold_segmenter())
    checkpoints = TimepointCheckpoints(tmp_path / "staging", "key")
    checkpoints.save("T0", {"k": 1}, measured)
    back = checkpoints.load("T0", {"k": 1}).template["_object"]
    original = measured.template["_object"]
    assert np.array_equal(back.array, original.array)
    assert (back.half, back.source, back.n_sites, back.used) == (original.half, original.source, original.n_sites,
                                                                 original.used)


def test_the_earliest_template_is_the_earliest_timepoint_s_even_when_it_was_not_anchored(monkeypatch):
    """T0's square was not found (unaddressed), T1's was. The run measured anchored timepoints first, so T1's
    template became "the earliest" and T0 was compared with it — the flag on the wrong timepoint. Measured in
    time, the earliest is T0's, and T1 is the one compared."""
    rendered, record = build_well()
    again, again_record = build_well(rotation=9.0, array_shift_um=(120.0, -80.0), seed=2, timepoint="T1")
    record["timepoints"].update(again_record["timepoints"])
    record["tiles"] += again_record["tiles"]
    images = {("T0", t): v for t, v in rendered.images.items()} | {("T1", t): v for t, v in again.images.items()}
    real = nr.locate_timepoint

    def t0_unaddressed(tp, *args, **kwargs):
        located = real(tp, *args, **kwargs)
        if tp == "T0":
            located.status, located.reason, located.indexed = nr.UNADDRESSED, "no square found", ()
            located.grid = dict(located.grid, status=nr.UNADDRESSED)
        return located

    monkeypatch.setattr(nr, "locate_timepoint", t0_unaddressed)
    outcome = nr.run_well("A01", record, ["T0", "T1"], lambda tp, tile: images.get((tp, tile)), SETTINGS,
                          segmenter=threshold_segmenter())
    assert outcome.earliest_template[0] == "T0"
    assert outcome.timepoints["T1"]["template"]["earliest_timepoint"] == "T0"
    assert "earliest_timepoint" not in outcome.timepoints["T0"]["template"]


@pytest.mark.parametrize("rotation_deg, seed", [(7.0, 1), (-11.0, 3), (15.0, 4)])
def test_a_bubble_over_the_central_square_does_not_refuse_the_timepoint_on_orientation(rotation_deg, seed):
    """With the square hidden, the best-scoring site is an ordinary circle whose orientation is noise. The check
    must not run against it: the timepoint is anchored from the array centre instead (roll-up review X21)."""
    from tests import _nanowell_run_fixture as F
    from tests._nanowell_synth import TilePlan, render_tiles

    plan = TilePlan(2, 2, (700, 700), 0.1, F.PX)
    uw, uh = plan.step_um[0] + plan.extent_um[0], plan.step_um[1] + plan.extent_um[1]
    rendered = render_tiles(plan, pitch_um=F.PITCH, diameter_um=F.DIAMETER, radius_um=0.3 * min(uw, uh),
                            rotation_deg=rotation_deg, noise_sigma=0.05, blur_px=1.0, seed=seed,
                            bubble=(uw / 2, uh / 2, 0.6 * F.PITCH))
    located = nr.locate_timepoint("T0", record_for(rendered), "A01", rendered.images, SETTINGS)
    assert (located.status, located.reason if located.status != nr.OK else "") == (nr.OK, "")
    assert located.origin_method != "fiducial"


def test_a_spilled_well_holds_its_montages_on_disk_and_measures_the_same(tmp_path):
    """Roll-up review X23: every timepoint is located before any is measured, so held in memory a time-course held
    every montage at once. With *spill_dir* each located montage is a memory map of a file there, and the
    numbers do not change."""
    rendered, record = build_well()
    again, again_record = build_well(rotation=9.0, array_shift_um=(120.0, -80.0), seed=2, timepoint="T1")
    record["timepoints"].update(again_record["timepoints"])
    record["tiles"] += again_record["tiles"]
    images = {("T0", t): v for t, v in rendered.images.items()} | {("T1", t): v for t, v in again.images.items()}
    run = lambda **kw: nr.run_well("A01", record, ["T0", "T1"], lambda tp, tile: images.get((tp, tile)),  # noqa: E731
                                   SETTINGS, segmenter=threshold_segmenter(), **kw)
    held, spilled = run(), run(spill_dir=tmp_path / "spill")
    for tp in ("T0", "T1"):
        montage = spilled.located[tp].montage
        assert isinstance(montage.image, np.memmap) and isinstance(montage.covered, np.memmap)
        assert Path(montage.image.filename).parent == tmp_path / "spill"
        np.testing.assert_array_equal(montage.image, held.located[tp].montage.image)
    assert spilled.sites == held.sites and spilled.cells == held.cells


@pytest.mark.parametrize("diameter_um, finding", [
    (100.0, None),                                                     # the plate's own: nothing to say
    (140.0, "The fitted nanowells measure 99.7 um across but the plate format sets 140 um"),
    (150.0, "No nanowell was fitted, so only the pitch could be checked"),   # past the fit's 30 %: none fitted
])
def test_the_format_s_diameter_is_checked_against_the_nanowells_fitted_at_full_resolution(diameter_um, finding):
    """Roll-up review X25: the format check's diameter half never ran, because locating has no fitted nanowells.
    It runs on the sites measuring fits at full resolution, and a finding is written on the timepoint."""
    from tests._nanowell_run_fixture import build_well

    rendered, record = build_well()
    settings = nr.NanowellSettings("custom", custom=(172.0, diameter_um))
    outcome = nr.run_well("A01", record, ["T0"], lambda tp, tile: rendered.images.get(tile), settings,
                          segmenter=threshold_segmenter())
    notes = outcome.timepoints["T0"]["notes"]
    format_notes = [n for n in notes if "plate format" in n or "No nanowell was fitted" in n]
    assert [n[:len(finding)] for n in format_notes] == ([] if finding is None else [finding])


def test_without_a_shared_radius_an_unaddressed_timepoint_is_a_failed_one_too(monkeypatch):
    """Roll-up review X27: with no roster radius to share, the addressed timepoints were marked ``no_lattice`` and the
    unaddressed ones kept their status — with no rows, outside what the caller counts as failed."""
    from types import SimpleNamespace

    from pipeline import nanowell_lattice as lattice
    from tests._nanowell_run_fixture import build_well

    rendered, record = build_well()
    real_resolve = lattice.resolve_origin

    def no_origin(fit, *, fiducial=None, array_centre=None, plate_rigid=None, fiducial_margin):
        return real_resolve(fit, fiducial=None, array_centre=None, plate_rigid=plate_rigid,
                            fiducial_margin=fiducial_margin)

    monkeypatch.setattr(lattice, "resolve_origin", no_origin)
    monkeypatch.setattr(nr, "_shared_radius", lambda *a, **k: SimpleNamespace(
        radius_um=None, status="none", deviations_um={}, flags={}))
    outcome = nr.run_well("A01", record, ["T0"], lambda tp, tile: rendered.images.get(tile), SETTINGS,
                          segmenter=threshold_segmenter())
    assert outcome.located["T0"].status == nr.UNADDRESSED
    assert outcome.timepoints["T0"]["status"] == nr.NO_LATTICE and outcome.sites == []
    assert outcome.timepoints["T0"]["status"] in nr.TERMINAL_WITHOUT_ROWS


@pytest.mark.parametrize("pitch_um, diameter_um, px, fmt", [
    (172.0, 100.0, 1.3, "100um"),       # the 100 um plate at a finer pixel size than the other tests use
    (143.0, 50.0, 1.3, "50um"),
    (120.0, 70.0, 1.3, "custom"),       # a plate no preset describes
    (300.0, 200.0, 2.0, "custom"),
])
def test_a_plate_is_located_at_its_own_pitch_and_pixel_size(pitch_um, diameter_um, px, fmt):
    """Tracker X34: the fixture's record wrote 4.3 um/px whatever the tiles' pixel size, so no test ever placed
    tiles at another one — and a probe that looked like the pipeline failing at 2 um/px was the fixture. A plate of
    any hex geometry, given as the custom format, is located and its addresses are the ones drawn."""
    from tests._nanowell_run_fixture import record_for
    from tests._nanowell_synth import TilePlan, render_tiles

    extent_um = 3000.0
    tile = int(round(extent_um / 1.9 / px))
    plan = TilePlan(2, 2, (tile, tile), 0.1, px)
    full = (plan.step_um[0] + plan.extent_um[0], plan.step_um[1] + plan.extent_um[1])
    rendered = render_tiles(plan, pitch_um=pitch_um, diameter_um=diameter_um, radius_um=0.3 * min(full),
                            rotation_deg=5.0, noise_sigma=0.05, blur_px=1.0, seed=3)
    settings = (nr.NanowellSettings("custom", custom=(pitch_um, diameter_um)) if fmt == "custom"
                else nr.NanowellSettings(fmt))
    located = nr.locate_timepoint("T0", record_for(rendered), "A01", rendered.images, settings)
    assert (located.status, located.reason) == (nr.OK, "")
    assert located.fit.pitch == pytest.approx(pitch_um, rel=0.01)
    truth = {tuple(map(float, s)): a for s, a in zip(rendered.sites_um, rendered.array.addresses)}
    sites = np.array(list(truth))
    addressed = wrong = 0
    for point, entry in zip(located.detected_common, located.indexed):
        if entry.q is None:
            continue
        addressed += 1
        wrong += (entry.q, entry.r) != truth[tuple(sites[np.argmin(np.linalg.norm(sites - point, axis=1))])]
    assert wrong == 0 and addressed >= 0.9 * len(located.indexed)
