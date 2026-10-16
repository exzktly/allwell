"""Two single-timepoint raw datasets, concatenated, then analysed: every nanowell joined across both (tracker P12).

The plate is imaged on day 0 with a 2 x 2 tile plan and again after being taken out and put back, moved,
with smaller, more heavily overlapped tiles. Concatenate relabels the second dataset's timepoint by the gap the user enters
and merges the acquisition records; Analyze then runs the merged input as one well with two timepoints.
"""

from __future__ import annotations

import csv
import json
import zipfile

import numpy as np
import pytest
from tifffile import imwrite

from datasets.concatenate import apply
from datasets.concatenate import concatenate_compute as cc
from tests._nanowell_run_fixture import build_well
from tests.test_nanowell_cli import WELL, _run_main
from util.acquisition_record import read_record, write_record_file


def _raw_dataset(root, rendered, record, token):
    (root / "in").mkdir(parents=True)
    tif = root.parent / f"{root.name}.tif"
    with zipfile.ZipFile(root / "in" / f"{WELL}.zip", "w") as zf:
        for fov, image in rendered.images.items():
            for channel, frame in (("PHASE", image), ("GFP", image * 0 + 10.0)):
                imwrite(str(tif), frame.astype("float32"))
                zf.write(tif, f"exp_{channel}_{WELL}_{fov}_{token}.tif")
    write_record_file(root / "in" / "acquisition.json", record)


@pytest.fixture(scope="module")
def merged(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("merge")
    first, record1 = build_well(rows=2, cols=2, tile=(700, 700), timepoint="0h", seed=21, radius_fraction=0.2)
    # The same physical array (radius 0.2 * 5719 um) under a different tile plan: smaller tiles, more overlap.
    second, record2 = build_well(rows=2, cols=2, tile=(640, 640), overlap=0.15, timepoint="0h", rotation=10.0,
                                 seed=22, array_shift_um=(90.0, -70.0), radius_fraction=0.2247)
    _raw_dataset(tmp / "d1", first, record1, "0h")
    _raw_dataset(tmp / "d2", second, record2, "0h")
    plan = cc.build_merge_plan(tmp / "d1", tmp / "d2", tmp / "dest", gap_hours=24.0)
    apply.execute_plan(plan)
    return tmp / "dest", first, second


def test_concatenate_relabels_the_second_timepoint_and_merges_the_records(merged):
    dest, _, _ = merged
    record = read_record(dest)
    assert set(record["timepoints"]) == {"0h", "24h"}
    assert record["timepoints"]["0h"]["tile_size_px"] == {"x": 700, "y": 700}         # each keeps its own plan
    assert record["timepoints"]["24h"]["tile_size_px"] == {"x": 640, "y": 640}
    assert {(t["timepoint"], t["field"]) for t in record["tiles"]} == {
        (tp, f) for tp in ("0h", "24h") for f in ("F0", "F1", "F2", "F3")}


def test_analyze_joins_every_nanowell_across_the_two_datasets_at_the_entered_time(merged, tmp_path, monkeypatch):
    dest, first, second = merged
    out = _run_main(monkeypatch, dest, "--nanowell_format", "100um", run_check=True)
    grid = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())
    assert [grid["timepoints"][t]["status"] for t in ("0h", "24h")] == ["ok", "ok"]
    rows = list(csv.DictReader((out / "nanowells" / f"{WELL}.csv").open()))
    by_tp = {tp: {(r["nw_q"], r["nw_r"]) for r in rows if r["timepoint"] == tp} for tp in ("0h", "24h")}
    truth = {(str(q), str(r)) for q, r in first.array.addresses}
    assert by_tp["0h"] == by_tp["24h"] and truth <= by_tp["0h"]                       # one roster, every drawn site
    # The second timepoint is placed by its own (different) tile plan, yet each address means the same nanowell:
    # the nanowell drawn at (1, 2) in both images is the one found at (1, 2) in both timepoints.
    for rendered, tp in ((first, "0h"), (second, "24h")):
        drawn = dict(zip(rendered.array.addresses, rendered.sites_um))
        row = next(r for r in rows if r["timepoint"] == tp and (r["nw_q"], r["nw_r"]) == ("1", "2"))
        assert np.hypot(float(row["well_x_um"]) - drawn[(1, 2)][0], float(row["well_y_um"]) - drawn[(1, 2)][1]) < 15
    info = json.loads((out / "pipeline_info.json").read_text())
    assert info["run_check"]["wells"][WELL]["health"] == "complete"
    assert info["acquisition"]["24h"]["tile_size_px"] == {"x": 640, "y": 640}


def test_successive_imaging_analysed_then_raw_then_incremental_adds_the_new_timepoint_to_the_same_roster(
        tmp_path, monkeypatch):
    """The user's own cadence (tracker P10d): the plate is imaged and analysed at 0h; it is imaged again, the raw
    images concatenated onto the analysed dataset; an incremental Analyze then adds 24h. Dataset 1's nanowell
    tables come through Concatenate as they were, 0h is not measured again, and 24h joins the same nanowells."""
    first, record1 = build_well(rows=2, cols=2, tile=(700, 700), timepoint="0h", seed=21, radius_fraction=0.2)
    second, record2 = build_well(rows=2, cols=2, tile=(640, 640), overlap=0.15, timepoint="0h", rotation=10.0,
                                 seed=22, array_shift_um=(90.0, -70.0), radius_fraction=0.2247)
    from tests._nanowell_run_fixture import paint_cell

    for site in list(first.sites_um)[:5]:                    # seeded: a few cells, so there are rows to keep
        paint_cell(first, site, dx_um=3.0)
    _raw_dataset(tmp_path / "d1", first, record1, "0h")
    _run_main(monkeypatch, tmp_path / "d1", "--nanowell_format", "100um")
    first_rows = list(csv.DictReader((tmp_path / "d1" / "out" / "nanowells" / f"{WELL}.csv").open()))
    _raw_dataset(tmp_path / "d2", second, record2, "0h")

    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", gap_hours=24.0)
    assert plan.ds2_raw
    apply.execute_plan(plan)
    dest = tmp_path / "dest"
    assert list(csv.DictReader((dest / "out" / "nanowells" / f"{WELL}.csv").open())) == first_rows
    assert (dest / "out" / "nanowells" / f"{WELL}_template.npz").is_file()

    from pipeline import nanowell_run as nr

    measured = []
    real = nr.measure_timepoint
    monkeypatch.setattr(nr, "measure_timepoint",
                        lambda located, *a, **k: (measured.append(located.timepoint), real(located, *a, **k))[1])
    _run_main(monkeypatch, dest, "--nanowell_format", "100um", "--incremental")
    assert measured == ["24h"]                                                  # 0h kept, not measured again
    rows = list(csv.DictReader((dest / "out" / "nanowells" / f"{WELL}.csv").open()))
    assert [r for r in rows if r["timepoint"] == "0h"] == first_rows
    by_tp = {tp: {(r["nw_q"], r["nw_r"]) for r in rows if r["timepoint"] == tp} for tp in ("0h", "24h")}
    assert by_tp["0h"] == by_tp["24h"] and len(by_tp["24h"]) > 100              # one roster
    grid = json.loads((dest / "out" / "nanowells" / f"{WELL}_grid.json").read_text())
    assert grid["timepoints"]["24h"]["status"] == "ok"
    assert grid["timepoints"]["24h"]["template"]["earliest_timepoint"] == "0h"


def test_two_analysed_nanowell_datasets_are_refused_with_the_route_that_works(tmp_path, monkeypatch):
    for name, token in (("d1", "0h"), ("d2", "0h")):
        rendered, record = build_well(rows=1, cols=1, tile=(900, 900), timepoint=token, radius_fraction=0.26)
        _raw_dataset(tmp_path / name, rendered, record, token)
        _run_main(monkeypatch, tmp_path / name, "--nanowell_format", "100um")
    with pytest.raises(cc.ConcatError, match="cannot carry them when both datasets are analysed") as raised:
        cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", gap_hours=24.0)
    assert "Concatenate the analysed dataset with dataset 2's raw images instead" in str(raised.value)


def test_raw_images_without_a_record_are_refused_onto_an_analysed_nanowell_dataset(tmp_path, monkeypatch):
    """Roll-up review X13: the merge writes a record only when both datasets have one, so dataset 1's nanowell
    tables would arrive in a destination with no record to extend them from. Refused at plan time, before
    anything is written, naming the remedy."""
    first, record1 = build_well(rows=1, cols=1, tile=(900, 900), timepoint="0h", radius_fraction=0.26)
    second, record2 = build_well(rows=1, cols=1, tile=(900, 900), timepoint="0h", radius_fraction=0.26, seed=5)
    _raw_dataset(tmp_path / "d1", first, record1, "0h")
    _run_main(monkeypatch, tmp_path / "d1", "--nanowell_format", "100um")
    _raw_dataset(tmp_path / "d2", second, record2, "0h")
    (tmp_path / "d2" / "in" / "acquisition.json").unlink()
    with pytest.raises(cc.ConcatError, match="dataset 2 has no acquisition record") as raised:
        cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", gap_hours=24.0)
    assert "Convert dataset 2 with the CZI, ND2 or OME import" in str(raised.value)
    assert not (tmp_path / "dest").exists()
