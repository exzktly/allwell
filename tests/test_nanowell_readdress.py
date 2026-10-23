"""An anchor set after a run is applied from the run's saved measurements, with no image read (tracker H1–H7).

The plate is ``test_nanowell_reanchor``'s four wells imaged twice, with cells drawn in B02, whose central square and
outline give nothing at either timepoint (``test_nanowell_manual_anchor_run._run``'s blind well). Two runs of the
real pipeline are the module's fixture: the plate run blind, and the plate run with the anchor already set. The
re-address applied to a copy of the blind run's output must then write what the anchored run wrote.
"""

from __future__ import annotations

import csv
import gzip
import json
import shutil

import numpy as np
import pytest

import pipeline.nanowell_readdress as rd
from pipeline import nanowell_run as nr
from tests._nanowell_run_fixture import build_well
from tests.test_nanowell_manual_anchor_run import CLICK_ERROR_UM, _run
from tests.test_nanowell_reanchor import TILE, _write_plate
from util.nanowell_anchors import anchors_path, set_anchor

#: Cells in B02 at both timepoints: one in each of four nanowells, two in a fifth.
CELLS = (((1, 2), 3.0), ((1, 3), 3.0), ((-2, 4), 3.0), ((0, 1), 3.0), ((2, -1), -20.0), ((2, -1), 20.0))


@pytest.fixture(scope="module")
def square():
    rendered, _ = build_well(rows=1, cols=1, tile=TILE, well="B02", timepoint="0h", seed=11, radius_fraction=0.26)
    return np.asarray(rendered.sites_um[rendered.array.addresses.index((0, 0))]), rendered


@pytest.fixture(scope="module")
def runs(tmp_path_factory, square):
    """``(blind plate, anchored run's out/)``: the plate run without the anchor, and a copy run with it."""
    base = tmp_path_factory.mktemp("readdress")
    with pytest.MonkeyPatch.context() as mp:
        blind, _ = _write_plate(base / "blind", b02_cells=CELLS)
        _run(mp, blind)
        anchored, _ = _write_plate(base / "anchored", b02_cells=CELLS)
        set_anchor(anchors_path(anchored / "in"), "B02", "0h", square[0] + CLICK_ERROR_UM)
        _run(mp, anchored)
    return blind, anchored / "out"


@pytest.fixture
def plate(runs, tmp_path):
    """A fresh copy of the blind run's dataset, to anchor and address."""
    copy = tmp_path / "plate"
    shutil.copytree(runs[0], copy)
    return copy


def _sites(out, well="B02"):
    return (out / "nanowells" / f"{well}.csv").read_text()


def _cells(out, well="B02"):
    with gzip.open(out / f"measurements_{well}.csv.gz", "rt", newline="") as fh:
        return list(csv.DictReader(fh))


def _grid(out, well="B02"):
    return json.loads((out / "nanowells" / f"{well}_grid.json").read_text())


def _anchor(plate, square, offset=(0.0, 0.0)):
    set_anchor(anchors_path(plate / "in"), "B02", "0h", square[0] + CLICK_ERROR_UM + np.asarray(offset))


# ── the same answer as a run ─────────────────────────────────────────────────
def test_the_blind_well_really_is_unaddressed_and_holds_its_cells(runs):
    out = runs[0] / "out"
    grid = _grid(out)["timepoints"]
    assert [grid[tp]["status"] for tp in ("0h", "24h")] == [nr.UNADDRESSED, nr.UNADDRESSED]
    cells = _cells(out)
    assert len(cells) >= 2 * len(CELLS) - 2 and all(not c["nw_q"] for c in cells)


def test_addressing_from_the_saved_rows_writes_what_a_run_with_the_anchor_writes(runs, plate, square):
    _anchor(plate, square)
    summary = rd.readdress_dataset(plate / "out", plate / "in")
    assert summary.addressed == {"B02/0h": rd.MANUAL, "B02/24h": rd.PLATE_RIGID} and summary.refused == {}
    out, reference = plate / "out", runs[1]
    assert _sites(out) == _sites(reference)                       # every site row, every column, byte for byte
    mine, theirs = _cells(out), _cells(reference)
    assert mine == theirs and len(mine) >= 10
    assert {(c["timepoint"], c["nw_label"]) for c in mine} >= {("0h", "1_2"), ("0h", "2_-1"), ("24h", "-2_4")}
    grid, ref = _grid(out), _grid(reference)
    assert grid["array_radius"] == ref["array_radius"]
    for tp in ("0h", "24h"):
        a, b = grid["timepoints"][tp], ref["timepoints"][tp]
        for key in ("status", "reason", "origin_method", "origin_agreement", "consistency", "consistency_detail",
                    "manual_anchor_um", "anchors_digest", "roster_size"):
            assert a.get(key) == b.get(key), (tp, key)
        # The origin is placed on a lattice fitted to the rows, not to the montage: the same site, within a micrometre.
        np.testing.assert_allclose(a["origin_stage_um"], b["origin_stage_um"], atol=1.0)
        assert a["readdressed"]["origin_method"] == a["origin_method"] and "readdressed" not in b
    info = json.loads((out / "pipeline_info.json").read_text())["nanowell"]
    assert info["reanchor"]["anchored"] == ["B02/24h"] and info["reanchor"]["remaining"] == {}
    assert info["readdress"]["addressed"] == {"B02/0h": "manual", "B02/24h": "plate_rigid"}


def test_applying_the_anchors_again_changes_nothing(plate, square):
    _anchor(plate, square)
    rd.readdress_dataset(plate / "out", plate / "in")
    before = (_sites(plate / "out"), _cells(plate / "out"), _grid(plate / "out"))
    again = rd.readdress_dataset(plate / "out", plate / "in")
    assert again.addressed == {} and again.refused == {}
    assert (_sites(plate / "out"), _cells(plate / "out"), _grid(plate / "out")) == before


def test_a_later_incremental_run_keeps_the_addressed_timepoints(plate, square, monkeypatch):
    """The grid block carries the anchor and the plate's anchors digest a run stamps, so ``_prior_state`` keeps both
    timepoints and nothing of B02 is measured again (tracker H7)."""
    _anchor(plate, square)
    rd.readdress_dataset(plate / "out", plate / "in")
    measured = []
    real = nr.measure_timepoint
    monkeypatch.setattr(nr, "measure_timepoint", lambda located, *a, **k: measured.append(located.timepoint)
                        or real(located, *a, **k))
    _run(monkeypatch, plate, extra=("--incremental",))
    grid = _grid(plate / "out")["timepoints"]
    assert [grid[tp]["status"] for tp in ("0h", "24h")] == [nr.OK, nr.OK]
    assert measured == []


# ── what it refuses ──────────────────────────────────────────────────────────
def _unchanged(plate, before):
    return (_sites(plate / "out"), _grid(plate / "out")) == before


def test_an_anchor_on_a_neighbour_is_refused_and_writes_nothing(plate, square):
    step = np.asarray(square[1].array.a1)
    before = (_sites(plate / "out"), _grid(plate / "out"))
    _anchor(plate, square, offset=step)
    summary = rd.readdress_dataset(plate / "out", plate / "in")
    assert "B02/0h" in summary.refused and "laid out from the site nearest" in summary.refused["B02/0h"]
    assert summary.addressed == {} and _unchanged(plate, before)


def test_cells_that_do_not_match_the_counts_are_refused(plate, square):
    path = plate / "out" / "nanowells" / "B02.csv"
    rows = list(csv.DictReader(path.open(newline="")))
    target = next(r for r in rows if r["timepoint"] == "0h" and r["n_cells"] not in ("", "0"))
    target["n_cells"] = str(int(float(target["n_cells"])) + 1)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    _anchor(plate, square)
    summary = rd.readdress_dataset(plate / "out", plate / "in")
    assert "do not match the nanowells' counts at 1 nanowell" in summary.refused["B02/0h"]


def test_rows_that_are_not_the_rim_the_rule_decides_are_refused(plate, square):
    """An interior row removed: the rule keeps every site a quarter pitch inside the circle, so the rows are not what
    a run would write. (A missing row in the rim band is read as a site seen whole and empty, as the layout rule
    left it out: that one cannot be told from the rows.)"""
    path = plate / "out" / "nanowells" / "B02.csv"
    rows = list(csv.DictReader(path.open(newline="")))
    zero = [r for r in rows if r["timepoint"] == "0h"]
    centre = np.median([[float(r["well_x_um"]), float(r["well_y_um"])] for r in zero], axis=0)
    inner = sorted(zero, key=lambda r: np.hypot(float(r["well_x_um"]) - centre[0], float(r["well_y_um"]) - centre[1]))
    removed = next(r for r in inner[1:] if r["n_cells"] in ("", "0"))
    rows.remove(removed)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    _anchor(plate, square)
    summary = rd.readdress_dataset(plate / "out", plate / "in")
    assert "lack 1 it includes" in summary.refused["B02/0h"]


def test_an_inferred_geometry_is_refused(plate, square):
    path = plate / "out" / "nanowells" / "B02_grid.json"
    grid = json.loads(path.read_text())
    grid["settings"]["geometry"] = nr.GEOMETRY_INFER
    path.write_text(json.dumps(grid))
    _anchor(plate, square)
    assert "inferred" in rd.readdress_dataset(plate / "out", plate / "in").refused["B02/0h"]


def test_an_anchor_on_a_well_the_run_addressed_is_recorded_when_it_names_the_same_square(runs, tmp_path, square):
    """A01 was addressed from its square: an anchor on that square is recorded as a run records it, and one on a
    neighbour is refused."""
    plate = tmp_path / "plate"
    shutil.copytree(runs[0], plate)
    grid = _grid(plate / "out", "A01")["timepoints"]["0h"]
    assert grid["origin_method"] == "fiducial"
    square_a01 = np.subtract(grid["origin_stage_um"], grid["stage_offset_um"])
    set_anchor(anchors_path(plate / "in"), "A01", "0h", square_a01 + CLICK_ERROR_UM)
    sites = _sites(plate / "out", "A01")
    summary = rd.readdress_dataset(plate / "out", plate / "in")
    assert summary.addressed.get("A01/0h") == rd.MANUAL and "A01/0h" not in summary.refused
    block = _grid(plate / "out", "A01")["timepoints"]["0h"]
    assert (block["origin_method"], block["origin_agreement"]) == ("manual", "agree")
    assert _sites(plate / "out", "A01") == sites                   # nothing about its rows changes
    step = 172.0 * np.array([np.cos(np.radians(7.0)), -np.sin(np.radians(7.0))])
    set_anchor(anchors_path(plate / "in"), "A01", "0h", square_a01 + 2 * step)
    refused = rd.readdress_dataset(plate / "out", plate / "in").refused
    assert "at another nanowell than the anchor's" in refused["A01/0h"]
