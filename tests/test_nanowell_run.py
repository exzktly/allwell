"""The (well, timepoint) task body on rendered tiles, against the array that was drawn."""

import numpy as np
import pytest

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
    xs = [x for x, a in zip(measured.cells["x_um"], measured.cells["site_index"])]
    assert len(measured.cells["label"]) == 1
    assert (measured.cells["nw_ring"][0]) == nr.hex_ring(*target)
    assert np.hypot(measured.cells["x_um"][0] - truth[target][0], measured.cells["y_um"][0] - truth[target][1]) < 10
    assert xs and measured.cells["fov"][0] in {"F0", "F1", "F2", "F3"}


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
    assert rows["T0"] == rows["T1"] and len(rows["T0"]) > 600            # one roster for the well
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
