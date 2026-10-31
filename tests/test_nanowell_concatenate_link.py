"""Concatenate of two analysed nanowell datasets joins dataset 2's timepoints onto dataset 1's nanowells where the
two analyses agree, and sets the rest aside for an incremental Analyze (``datasets.concatenate.nanowell_link``).

Driven through ``build_merge_plan`` and ``execute_plan`` on the repo's realistic dataset (two fields, OS debris in
every archive), with nanowell tables whose two halves differ in every value a wrong join could
confuse: each dataset's site rows carry their own cell counts, its montages their own bytes, its array radius its
own number, so a table taken from the wrong side, or not renamed, reads differently from the right one.
"""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

import pytest

from datasets.concatenate import apply
from datasets.concatenate import concatenate_compute as cc
from datasets.core.dataset_paths import DatasetPaths
from datasets.core.nanowell_tables import refuse_unjoinable_nanowell_tables
from tests.fixtures.dataset_builder import build_dataset

SETTINGS = {"plate_format": "100um", "cell_diameter_um": 15.0}
#: A 37-site hexagonal array: the shape a circular well's roster has.
ROSTER = [(q, r) for q in range(-3, 4) for r in range(-3, 4) if max(abs(q), abs(r), abs(q + r)) <= 3]
#: The ring of sites a disk-rule well has past its array's rim (every one ``occluded`` at every timepoint).
DISK_RING = [(q, r) for q in range(-4, 5) for r in range(-4, 5) if max(abs(q), abs(r), abs(q + r)) == 4]


def _dataset(root: Path, *, wells=("A01",), timepoints=("0h", "24h"), cells=1, radius=1000.0, roster=ROSTER,
             step=None, flagged=(), disk_rule=False, fallback=(), ratios=None, settings=SETTINGS, tables=True):
    """An analysed nanowell dataset: every well's tables name every timepoint, ok, on *roster*, every site wholly
    imaged and detected. *cells* is each site's ``n_cells`` (one dataset's rows told from the other's).

    *step* maps a timepoint to the lattice step its origin sat off. Its rows are what the run writes for one: the
    same addresses as every other timepoint's (one row per roster site), with the array at ``roster - step`` in its
    frame, so no nanowell is detected on the crescent the step leaves — ``occluded``, none of the disk visible, as
    the rendered run writes it. *flagged* are the timepoints the dataset's own run flagged ``roster_mismatch``.
    *disk_rule* writes a well analysed before rosters were kept: no roster recorded, and rows for a ring of sites past
    the array's rim at every timepoint. *fallback* names the timepoints measured on the well's earliest template;
    *ratios* gives each timepoint's template floor ratio."""
    info_extra = {"nanowell": {"scheme_version": 1, "measure_version": 3, "settings": dict(settings)},
                  "acquisition": {tp: {"pixel_size_um": {"x": 4.3, "y": 4.3}} for tp in timepoints}}
    build_dataset(root, wells=list(wells), timepoints=list(timepoints), fovs=("F001", "F002"),
                  info_extra=info_extra, junk=True)
    for well in wells:
        with zipfile.ZipFile(root / "out" / f"{well}_out.zip", "a") as zf:
            for tp in timepoints:
                zf.writestr(f"{well}_{tp}_montage.png", f"{root.name} {tp}".encode())
    if not tables:
        return root
    folder = root / "out" / "nanowells"
    folder.mkdir()
    for well in wells:
        consistency = {tp: "roster_mismatch" if tp in flagged else "consistent" for tp in timepoints}
        blocks = {tp: {"status": "ok", "record_digest": f"{root.name}-{tp}", "consistency": consistency[tp],
                       "template": {"source": "earliest" if tp in fallback else "own",
                                    "floor_ratio": (ratios or {}).get(tp, 1.0)}} for tp in timepoints}
        array = {"radius_um": radius} if disk_rule else \
            {"radius_um": radius, "roster": [list(a) for a in roster], "roster_rule": "evidence"}
        (folder / f"{well}_grid.json").write_text(json.dumps({
            "scheme_version": 1, "measure_version": 3, "well": well, "array_radius": array, "timepoints": blocks}))
        lines, inside = [], set(roster)
        for tp in timepoints:
            dq, dr = (step or {}).get(tp, (0, 0))
            for q, r in [*roster, *(DISK_RING if disk_rule else ())]:
                there = (q + dq, r + dr) in inside
                lines.append(f"{tp},{q},{r},{there},1.0,{1.0 if there else 0.0},{'measured' if there else 'occluded'},"
                             f"{consistency[tp]},{cells if there else ''}\n")
        (folder / f"{well}.csv").write_text(
            "timepoint,nw_q,nw_r,detected,imaged_fraction,visible_fraction,state,consistency,n_cells\n"
            + "".join(lines))
        _template(folder / f"{well}_template.npz", timepoints[0])
    return root


def _template(path: Path, timepoint: str) -> None:
    import numpy as np

    from pipeline.nanowell_measure import Template, save_template

    save_template(path, Template(np.ones((5, 5)), 2, "own", 3, 9, 0.1, (0, 1, 2)), timepoint)


def _tables(root: Path, well="A01"):
    folder = root / "out" / "nanowells"
    grid = json.loads((folder / f"{well}_grid.json").read_text())
    with (folder / f"{well}.csv").open(newline="") as fh:
        return grid, list(csv.DictReader(fh))


def _cell_timepoints(root: Path, well="A01") -> set[str]:
    from util.csv_gz import existing_csv, open_csv_text

    with open_csv_text(existing_csv(root / "out" / f"measurements_{well}.csv")) as fh:
        return {row["timepoint"] for row in csv.DictReader(fh)}


def _montages(root: Path, well="A01") -> dict[str, bytes]:
    with zipfile.ZipFile(root / "out" / f"{well}_out.zip") as zf:
        return {n: zf.read(n) for n in zf.namelist() if n.endswith("_montage.png")}


def _concatenate(tmp_path, first, second):
    plan = cc.build_merge_plan(first, second, tmp_path / "dest", gap_hours=24.0)
    lines = []
    summary = apply.execute_plan(plan, log=lines.append)
    return plan, summary, lines, tmp_path / "dest"


def test_agreeing_analyses_join_every_dataset_2_timepoint_onto_dataset_1s_nanowells(tmp_path):
    first = _dataset(tmp_path / "d1", cells=1, radius=1000.0, ratios={"0h": 1.0, "24h": 1.0})
    second = _dataset(tmp_path / "d2", cells=7, radius=1010.0, ratios={"0h": 3.0, "24h": 3.0})
    plan, summary, lines, dest = _concatenate(tmp_path, first, second)
    assert plan.nanowell_link.joined == ("A01",) and not plan.nanowell_link.set_aside
    grid, rows = _tables(dest)
    assert list(grid["timepoints"]) == ["0h", "24h", "48h", "72h"]
    assert grid["timepoints"]["48h"]["record_digest"] == "d2-0h"             # dataset 2's 0h, renamed
    assert grid["array_radius"]["radius_um"] == 1000.0                     # one roster: dataset 1's
    cells = {(r["timepoint"], r["n_cells"]) for r in rows}
    assert cells == {("0h", "1"), ("24h", "1"), ("48h", "7"), ("72h", "7")}
    by_tp = {tp: {(r["nw_q"], r["nw_r"]) for r in rows if r["timepoint"] == tp} for tp in ("0h", "48h")}
    assert by_tp["0h"] == by_tp["48h"] and len(by_tp["0h"]) == len(ROSTER)
    # Every timepoint compared with the well's earliest template, which is now dataset 1's 0h: dataset 2's ratio of
    # 3 is beyond the factor of two from it, where against its own 0h it was no drift at all.
    assert grid["timepoints"]["72h"]["template"]["earliest_timepoint"] == "0h"
    assert grid["timepoints"]["72h"]["template"]["suspect"] is True
    from pipeline.nanowell_measure import load_template

    assert load_template(dest / "out" / "nanowells" / "A01_template.npz")[1] == "0h"
    assert _cell_timepoints(dest) == {"0h", "24h", "48h", "72h"}
    assert _montages(dest) == {"A01_0h_montage.png": b"d1 0h", "A01_24h_montage.png": b"d1 24h",
                               "A01_48h_montage.png": b"d2 0h", "A01_72h_montage.png": b"d2 24h"}
    info = json.loads((dest / "out" / "pipeline_info.json").read_text())
    assert set(info["acquisition"]) == {"0h", "24h", "48h", "72h"}
    assert any("Joined dataset 2's nanowell results" in line for line in lines)
    assert "wait for Analyze" not in summary


def test_a_well_whose_two_rosters_differ_leaves_all_of_dataset_2_for_analyze(tmp_path):
    first = _dataset(tmp_path / "d1", cells=1)
    second = _dataset(tmp_path / "d2", cells=7, roster=ROSTER + [(4, 0)])
    plan, summary, lines, dest = _concatenate(tmp_path, first, second)
    assert plan.nanowell_link.joined == ()
    assert set(plan.nanowell_link.set_aside["A01"]) == {"48h", "72h"}
    assert "1 only in dataset 2's" in plan.nanowell_link.set_aside["A01"]["48h"]
    grid, rows = _tables(dest)
    assert list(grid["timepoints"]) == ["0h", "24h"] and {r["timepoint"] for r in rows} == {"0h", "24h"}
    assert _cell_timepoints(dest) == {"0h", "24h"}                      # no cells without a grid block behind them
    assert set(_montages(dest)) == {"A01_0h_montage.png", "A01_24h_montage.png"}
    with zipfile.ZipFile(dest / "in" / "A01.zip") as zf:                # Analyze still has dataset 2's images
        assert "exp_GFP_A01_F001_72h.tif" in zf.namelist()
    assert "2 nanowell timepoint(s) of dataset 2 wait for Analyze" in summary
    assert any(line.startswith("A01: dataset 2's 48h, 72h left for an incremental Analyze") for line in lines)


def test_a_dataset_2_timepoint_anchored_a_step_off_is_set_aside_alone(tmp_path):
    """Tracker M14. Rows exist only at the roster's addresses, at every timepoint, so the step-off timepoint's
    detected addresses overlap dataset 1's best where they are: the check that compared addresses could not fire on
    the tables a run writes (its old fixture moved the rows off the roster). Its empty crescent is what shows."""
    first = _dataset(tmp_path / "d1")
    second = _dataset(tmp_path / "d2", cells=7, step={"24h": (1, 0)})
    plan, _summary, _lines, dest = _concatenate(tmp_path, first, second)
    assert plan.nanowell_link.set_aside == {"A01": {"72h": (
        "one side of its rim is empty, as a lattice step of (1, 0) off dataset 1's nanowells leaves it (or a bubble "
        "or glare over that side), so its addresses may name neighbouring nanowells")}}
    grid, rows = _tables(dest)
    assert list(grid["timepoints"]) == ["0h", "24h", "48h"]
    assert {r["timepoint"] for r in rows} == {"0h", "24h", "48h"}      # no site rows without a grid block
    assert _cell_timepoints(dest) == {"0h", "24h", "48h"}


def test_a_dataset_2_timepoint_its_own_run_flagged_is_set_aside_and_never_joined(tmp_path):
    """Dataset 2's run found 24h's nanowells a step from its 0h's and flagged it. The link skipped a flagged
    timepoint's rows altogether, so it was never judged, and was joined as it was."""
    first = _dataset(tmp_path / "d1")
    second = _dataset(tmp_path / "d2", cells=7, step={"24h": (1, 0)}, flagged=("24h",))
    plan, _summary, _lines, dest = _concatenate(tmp_path, first, second)
    assert plan.nanowell_link.set_aside == {"A01": {"72h": (
        "dataset 2's own analysis flagged it roster_mismatch (its nanowells sat a lattice step from its other "
        "timepoints'), so its addresses may name neighbouring nanowells")}}
    assert list(_tables(dest)[0]["timepoints"]) == ["0h", "24h", "48h"]


def test_a_disk_rule_timepoint_a_step_off_is_found_by_its_detected_addresses(tmp_path):
    """A well analysed before rosters were kept has rows for a ring of sites past its rim, so a step-off
    timepoint's nanowells are detected at addresses dataset 1's are not: the addresses show the step."""
    first = _dataset(tmp_path / "d1", disk_rule=True)
    second = _dataset(tmp_path / "d2", cells=7, step={"24h": (1, 0)}, disk_rule=True)
    plan, _summary, _lines, _dest = _concatenate(tmp_path, first, second)
    assert plan.nanowell_link.set_aside == {"A01": {"72h": (
        "its nanowells sit (1, 0) lattice steps from dataset 1's, so its addresses may name neighbouring nanowells")}}
    agreeing = _dataset(tmp_path / "d3", cells=7, disk_rule=True)
    assert cc.build_merge_plan(first, agreeing, tmp_path / "dest3", gap_hours=24.0).nanowell_link.set_aside == {}


def test_a_dataset_2_timepoint_measured_on_its_own_fallback_template_is_set_aside(tmp_path):
    first = _dataset(tmp_path / "d1")
    second = _dataset(tmp_path / "d2", cells=7, fallback=("24h",))
    plan, _summary, _lines, dest = _concatenate(tmp_path, first, second)
    assert list(plan.nanowell_link.set_aside.get("A01", {})) == ["72h"]
    assert "dataset 2's earliest empty-nanowell template" in plan.nanowell_link.set_aside["A01"]["72h"]
    assert list(_tables(dest)[0]["timepoints"]) == ["0h", "24h", "48h"]


def test_a_well_only_dataset_2_analysed_comes_whole_under_its_new_timepoints(tmp_path):
    first = _dataset(tmp_path / "d1", wells=("A01",))
    second = _dataset(tmp_path / "d2", wells=("A01", "B02"), cells=7)
    plan, _summary, _lines, dest = _concatenate(tmp_path, first, second)
    assert plan.nanowell_link.ds2_only == ("B02",)
    grid, rows = _tables(dest, "B02")
    assert grid["well"] == "B02" and list(grid["timepoints"]) == ["48h", "72h"]
    assert {r["timepoint"] for r in rows} == {"48h", "72h"}
    from pipeline.nanowell_measure import load_template

    assert load_template(dest / "out" / "nanowells" / "B02_template.npz")[1] == "48h"


def test_two_analyses_under_different_nanowell_settings_are_refused_before_writing(tmp_path):
    first = _dataset(tmp_path / "d1")
    second = _dataset(tmp_path / "d2", settings=dict(SETTINGS, cell_diameter_um=20.0))
    with pytest.raises(cc.ConcatError, match="different nanowell settings"):
        cc.build_merge_plan(first, second, tmp_path / "dest", gap_hours=24.0)
    assert not (tmp_path / "dest").exists()


def test_only_one_dataset_holding_nanowell_results_is_refused_naming_the_other(tmp_path):
    first = _dataset(tmp_path / "d1")
    second = _dataset(tmp_path / "d2", tables=False)

    def paths(root):
        return DatasetPaths(root=root, in_dir=root / "in", out_dir=root / "out")

    with pytest.raises(ValueError, match="Dataset 1 holds nanowell results .* and dataset 2 holds none"):
        refuse_unjoinable_nanowell_tables(paths(first), paths(second), {}, {}, ValueError)
    with pytest.raises(cc.ConcatError, match="Dataset 2 holds nanowell results"):
        cc.build_merge_plan(second, first, tmp_path / "dest", gap_hours=24.0)


def test_a_record_only_dataset_2_has_is_refused_when_both_are_analysed(tmp_path, monkeypatch):
    """Dataset 2's tables were addressed from its record; a merge without dataset 1's writes none, and the
    timepoints set aside for Analyze could not be placed. Refused at plan time, before anything is written."""
    first = _dataset(tmp_path / "d1")
    second = _dataset(tmp_path / "d2")
    monkeypatch.setattr(cc, "read_record", lambda root: {"timepoints": {}} if Path(root) == second else None)
    with pytest.raises(cc.ConcatError, match="Dataset 2 has nanowell tables addressed from its acquisition record"):
        cc.build_merge_plan(first, second, tmp_path / "dest", gap_hours=24.0)
    assert not (tmp_path / "dest").exists()


def test_a_dataset_1_well_whose_record_names_no_timepoint_still_takes_dataset_2s(tmp_path):
    """Dataset 1's tables of the well carry nothing (``carry_nanowell_tables`` writes no table for a record with no
    timepoint), so dataset 2's are written on their own rather than joined onto a table that does not exist."""
    first = _dataset(tmp_path / "d1")
    grid_path = first / "out" / "nanowells" / "A01_grid.json"
    grid = json.loads(grid_path.read_text())
    grid["timepoints"] = {}
    grid_path.write_text(json.dumps(grid))
    (first / "out" / "nanowells" / "A01.csv").write_text("timepoint,nw_q,nw_r,detected,consistency,n_cells\n")
    second = _dataset(tmp_path / "d2", cells=7)
    _plan, _summary, _lines, dest = _concatenate(tmp_path, first, second)
    grid, rows = _tables(dest)
    assert list(grid["timepoints"]) == ["48h", "72h"] and {r["n_cells"] for r in rows} == {"7"}
