"""A nanowell keeps its address across timepoints even when one timepoint's origin lands a site off.

Every timepoint of a well is anchored on its own evidence — the central square when it can be read, otherwise the
fitted array centre — and a nanowell's address ``(q, r)`` is counted from that origin. Review joins a well's
timepoints by that address, so a timepoint anchored one site off names every nanowell after its neighbour, and fold
change and trajectories then silently pair each nanowell with its neighbour from that timepoint on. The run used to
detect the step and only write ``roster_mismatch`` beside rows that kept the wrong addresses; it compared each
timepoint with the union of the others, so the one square-anchored timepoint could be the one blamed; it searched
only one step; and an incremental run kept such rows for good.

Each test paints cells into the same physical nanowells at every timepoint and asserts that the address holding
the cell is the same at every timepoint — the property the join depends on. Comparing address *sets* cannot see
this: every timepoint is measured on one shared roster, so the sets agree by construction.
"""
from __future__ import annotations

import json
import threading
from dataclasses import replace

import numpy as np
import pytest

from pipeline import nanowell_detect as nd
from pipeline import nanowell_run as nr
from tests._nanowell_run_fixture import build_well, paint_cell, threshold_segmenter

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")

SETTINGS = nr.NanowellSettings("100um")
#: Nanowells (in the rendered array's own addresses, which a square-anchored timepoint reproduces) given a cell.
PAINT = [(0, 2), (2, -1), (-3, 1), (1, 3), (-2, -2)]


def _off_by(monkeypatch, steps: dict) -> None:
    """Move the fitted array centre of each timepoint in *steps* by that many ``a1`` steps, as a fitted circle left
    loose by a hexagonal rim would: the timepoint is then anchored that many sites off when it has no square."""
    real_detect, real_locate = nd.detect_sites, nr.locate_timepoint
    current = threading.local()           # per thread: run_well locates timepoints on several at once

    def detect(*args, **kwargs):
        fit = real_detect(*args, **kwargs)
        k = steps.get(getattr(current, "tp", None), 0)
        if k and fit.ok and fit.boundary_centre_um is not None:
            fit = replace(fit, boundary_centre_um=tuple(np.asarray(fit.boundary_centre_um) + k * np.asarray(fit.fit.a1)))
        return fit

    def locate(tp, *args, **kwargs):
        current.tp = tp
        return real_locate(tp, *args, **kwargs)

    monkeypatch.setattr(nd, "detect_sites", detect)
    monkeypatch.setattr(nr, "locate_timepoint", locate)


def _painted_well(squares: dict, seeds: dict):
    """``(record, images)`` of one well over the timepoints of *squares* (timepoint -> whether its central square is
    drawn), a cell painted into every nanowell of :data:`PAINT` at every timepoint."""
    record, images = None, {}
    for tp, square in squares.items():
        rendered, rec = build_well(timepoint=tp, seed=seeds[tp], rotation=7.0, square_centre=square)
        sites = dict(zip(rendered.array.addresses, rendered.sites_um))
        for address in PAINT:
            paint_cell(rendered, sites[address], amount=0.6, radius_px=4.0)
        images[tp] = rendered.images
        if record is None:
            record = rec
        else:
            record["timepoints"].update(rec["timepoints"])
            record["tiles"] += rec["tiles"]
    return record, images


def _run(record, images, timepoints):
    return nr.run_well("A01", record, timepoints, lambda tp, tile: images[tp].get(tile), SETTINGS,
                       segmenter=threshold_segmenter())


def _cells_at(rows, timepoint) -> dict:
    """``address -> n_cells`` of one timepoint's rows (the pipeline's ``nanowells/<well>.csv`` rows)."""
    return {(int(r["nw_q"]), int(r["nw_r"])): int(r["n_cells"]) for r in rows
            if r["timepoint"] == timepoint and r.get("nw_q") not in (None, "") and r.get("n_cells") not in (None, "")}


def _assert_cells_follow_their_nanowell(rows, timepoints):
    for tp in timepoints:
        cells = _cells_at(rows, tp)
        assert [cells.get(a) for a in PAINT] == [1] * len(PAINT), f"{tp}: the painted nanowells' addresses"
        assert [cells.get((q - 1, r)) for q, r in PAINT] == [0] * len(PAINT), f"{tp}: their (-1, 0) neighbours"


def test_a_timepoint_whose_array_centre_lands_a_site_off_is_moved_onto_the_square_anchored_ones(tmp_path,
                                                                                                  monkeypatch):
    """T0 and T1 read the square; T2 cannot and its array centre lands one site off. Its rows were written under
    the neighbours' addresses (the painted nanowell (0, 2) read empty at T2 and its neighbour held the cell)."""
    _off_by(monkeypatch, {"T2": 1})
    record, images = _painted_well({"T0": True, "T1": True, "T2": False}, {"T0": 1, "T1": 2, "T2": 3})
    outcome = _run(record, images, ["T0", "T1", "T2"])
    assert [outcome.timepoints[tp]["origin_method"] for tp in ("T0", "T1", "T2")] == [
        "fiducial", "fiducial", "array_centre"]
    _assert_cells_follow_their_nanowell(outcome.sites, ["T0", "T1", "T2"])
    t2 = outcome.timepoints["T2"]
    assert (t2["status"], t2["consistency"], t2["origin_shift"], t2["origin_registered_to"]) == (
        nr.OK, "consistent", [1, 0], "T0")
    assert "origin_shift" not in outcome.timepoints["T0"] and "origin_shift" not in outcome.timepoints["T1"]

    # And through Review's join, which is what pairs a nanowell's timepoints.
    from review.features.nanowell import series_compute as sc
    from review.features.nanowell.collect import load_nanowell_data
    from pipeline import nanowell_task as nt

    out = tmp_path
    nt.write_sites_csv(out / "nanowells" / "A01.csv", outcome.sites, [])
    nt.write_grid_json(out / "nanowells" / "A01_grid.json", "A01", outcome, SETTINGS)
    grid = sc._addressed_grid(load_nanowell_data(out))
    joined = {(tp, int(q), int(r)): n for tp, q, r, n in zip(grid.timepoint, grid.nw_q, grid.nw_r, grid.n_cells)}
    assert [[joined[(tp, *a)] for tp in ("T0", "T1", "T2")] for a in PAINT] == [[1, 1, 1]] * len(PAINT)


def test_the_square_anchored_baseline_is_never_the_timepoint_moved_when_later_ones_agree_on_a_wrong_centre(
        monkeypatch):
    """T0 reads the square; T1 and T2 cannot and both array centres land the same site off. Compared with the union
    of the others, T0 was the timepoint flagged, and T1 and T2 kept the wrong addresses as ``consistent``."""
    _off_by(monkeypatch, {"T1": 1, "T2": 1})
    record, images = _painted_well({"T0": True, "T1": False, "T2": False}, {"T0": 1, "T1": 4, "T2": 3})
    outcome = _run(record, images, ["T0", "T1", "T2"])
    _assert_cells_follow_their_nanowell(outcome.sites, ["T0", "T1", "T2"])
    assert "origin_shift" not in outcome.timepoints["T0"]
    assert [outcome.timepoints[tp].get("origin_shift") for tp in ("T1", "T2")] == [[1, 0], [1, 0]]
    assert [outcome.timepoints[tp]["consistency"] for tp in ("T0", "T1", "T2")] == ["consistent"] * 3


def test_a_timepoint_two_sites_off_is_moved_by_two(monkeypatch):
    """Only one step was searched, so a centre two sites off was reported as one step and never put right."""
    _off_by(monkeypatch, {"T1": 2})
    record, images = _painted_well({"T0": True, "T1": False}, {"T0": 1, "T1": 3})
    outcome = _run(record, images, ["T0", "T1"])
    _assert_cells_follow_their_nanowell(outcome.sites, ["T0", "T1"])
    assert outcome.timepoints["T1"]["origin_shift"] == [2, 0]


def test_two_equally_anchored_timepoints_a_site_apart_are_left_unaddressed_not_guessed(monkeypatch):
    """Neither reads the square and their array centres land a site apart: nothing tells which is right. Both were
    written ``ok`` under addresses a site apart; now neither is addressed, and the reason says how to settle it."""
    _off_by(monkeypatch, {"T1": 1})
    record, images = _painted_well({"T0": False, "T1": False}, {"T0": 1, "T1": 3})
    outcome = _run(record, images, ["T0", "T1"])
    for tp in ("T0", "T1"):
        block = outcome.timepoints[tp]
        assert block["status"] == nr.UNADDRESSED and "cannot be told" in block["reason"], tp
        assert "anchor set by hand" in block["reason"]
    assert not any(r.get("nw_q") not in (None, "") for r in outcome.sites)
    assert outcome.sites, "measured on the nanowells it saw, with no address"


def test_a_timepoint_that_agrees_is_left_where_it_anchored(monkeypatch):
    """The control: three timepoints, every origin right, none moved."""
    _off_by(monkeypatch, {})
    record, images = _painted_well({"T0": True, "T1": False, "T2": True}, {"T0": 1, "T1": 3, "T2": 2})
    outcome = _run(record, images, ["T0", "T1", "T2"])
    assert [outcome.timepoints[tp]["origin_method"] for tp in ("T0", "T1", "T2")] == [
        "fiducial", "array_centre", "fiducial"]
    assert not any("origin_shift" in outcome.timepoints[tp] for tp in ("T0", "T1", "T2"))
    _assert_cells_follow_their_nanowell(outcome.sites, ["T0", "T1", "T2"])


def test_an_incremental_run_whose_kept_timepoint_was_a_site_off_analyses_the_well_again(tmp_path, monkeypatch):
    """The caller's real sequence. 0h is analysed alone with no square and its array centre a site off — alone,
    nothing shows it. 24h, which reads the square, is added incrementally: its nanowells sit a site from the kept
    ones', so instead of being moved onto 0h's wrong frame (or 0h's rows kept beside it), the well is analysed
    again over both timepoints and the frame is chosen with both in view — 24h's square."""
    from tests.test_nanowell_cli import _rows, _run_main
    from tests.test_nanowell_run import _plate_of

    _off_by(monkeypatch, {"0h": 1})
    record, images = _painted_well({"T0": False, "T1": True}, {"T0": 1, "T1": 3})
    tokens = {"T0": "0h", "T1": "24h"}
    for block in record["tiles"]:
        block["timepoint"] = tokens[block["timepoint"]]
    record["timepoints"] = {tokens[k]: v for k, v in record["timepoints"].items()}
    first_only = {**{k: v for k, v in record.items() if k not in ("timepoints", "tiles")},
                  "timepoints": {"0h": record["timepoints"]["0h"]},
                  "tiles": [t for t in record["tiles"] if t["timepoint"] == "0h"]}

    class _Rendered:                       # _plate_of reads only .images
        def __init__(self, images):
            self.images = images

    plate = _plate_of(tmp_path, [("0h", _Rendered(images["T0"]))], first_only)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um")
    assert _cells_at(_rows(out, "A01.csv"), "0h").get((0, 2)) == 0, "alone, 0h is anchored a site off"

    _plate_of(tmp_path, [("0h", _Rendered(images["T0"])), ("24h", _Rendered(images["T1"]))], record)
    _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--incremental")
    _assert_cells_follow_their_nanowell(_rows(out, "A01.csv"), ["0h", "24h"])
    grid = json.loads((out / "nanowells" / "A01_grid.json").read_text())
    assert "lattice steps from the earlier run's" in grid["array_radius"].get("reanalysed_because", "")
    assert grid["timepoints"]["0h"].get("origin_shift") == [1, 0]
    assert grid["timepoints"]["0h"].get("origin_registered_to") == "24h"


def test_an_earlier_record_with_a_step_off_timepoint_is_not_reused():
    """A record written before timepoints were settled can hold rows under neighbouring addresses, flagged
    ``roster_mismatch`` — on the wrong timepoint or the right one outvoted. Neither may be carried forward."""
    from pipeline.nanowell_task import _has_a_step_off_timepoint

    circle = {"array_radius": {"radius_um": 900.0, "roster_rule": "evidence"},
              "timepoints": {"0h": {"status": "ok", "consistency": "roster_mismatch"},
                             "24h": {"status": "ok", "consistency": "consistent"}}}
    assert _has_a_step_off_timepoint(circle)
    assert not _has_a_step_off_timepoint({**circle, "timepoints": {"0h": {"consistency": "consistent"}}})
    # An inferred array's timepoints each see their own array, so a mismatch there is an observation, not a step.
    assert not _has_a_step_off_timepoint({**circle, "array_radius": {"radius_um": None, "roster": [[0, 0]]}})
