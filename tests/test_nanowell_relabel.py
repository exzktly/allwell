"""Datasets → Validate repairs a nanowell dataset whose timepoints were addressed a lattice step apart.

A dataset analysed before the run settled its timepoints' frames (``pipeline.nanowell_run._settle_steps``) holds,
for a timepoint whose origin landed a site off, rows under its neighbours' addresses. The fixture here is made by
the real run with that settling switched off, which is exactly the earlier behaviour, on a well whose cells are
painted into the same physical nanowells at every timepoint. Validate must find the well, and Apply repairs must
relabel it so that each address holds the same physical nanowell at every timepoint — measured against a run of the
fixed pipeline on the same images.
"""
from __future__ import annotations

import csv
import json

import pytest

from pipeline import nanowell_run as nr
from tests.test_nanowell_timepoint_frames import PAINT, _cells_at, _off_by, _painted_well

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")

TOKENS = {"T0": "0h", "T1": "24h", "T2": "48h"}


def _as_hours(record):
    for block in record["tiles"]:
        block["timepoint"] = TOKENS[block["timepoint"]]
    record["timepoints"] = {TOKENS[k]: v for k, v in record["timepoints"].items()}
    return record


class _Rendered:                           # tests.test_nanowell_run._plate_of reads only .images
    def __init__(self, images):
        self.images = images


def _dataset(tmp_path, monkeypatch, name, squares, seeds, off, *, settle=True):
    """A whole analysed dataset (``plate/in``, ``plate/out``) of one painted well, through ``process_microscopy``."""
    from tests.test_nanowell_cli import _run_main
    from tests.test_nanowell_run import _plate_of

    record, images = _painted_well(squares, seeds)
    record = _as_hours(record)
    plate = _plate_of(tmp_path / name, [(TOKENS[tp], _Rendered(images[tp])) for tp in squares], record)
    with monkeypatch.context() as patch:   # undone after this run alone, never the test's own fixtures
        _off_by(patch, {TOKENS[tp]: k for tp, k in off.items()})
        if not settle:                     # the run as it was before it settled timepoint frames
            patch.setattr(nr, "_settle_steps", lambda outcome, anchored, kept=None: (list(anchored), [], {}))
        _run_main(patch, plate, "--nanowell_format", "100um")
    return plate


def _sites(plate) -> list[dict]:
    return list(csv.DictReader((plate / "out" / "nanowells" / "A01.csv").open()))


def _cells(plate) -> list[dict]:
    import gzip

    [path] = [p for p in (plate / "out").glob("*_A01.csv*")]
    opener = gzip.open(path, "rt") if path.suffix == ".gz" else path.open()
    with opener as fh:
        return list(csv.DictReader(fh))


def _follows(rows, timepoints) -> bool:
    return all([_cells_at(rows, tp).get(a) for a in PAINT] == [1] * len(PAINT) for tp in timepoints)


def test_validate_finds_and_relabels_a_timepoint_whose_array_centre_landed_a_site_off(tmp_path, monkeypatch):
    """0h and 24h read the square; 48h cannot, and its array centre lands a site off. The caller's real sequence:
    the run writes the dataset, Validate scans it, Apply repairs relabels it, Validate scans it again."""
    from datasets.validate.apply import apply_repairs
    from datasets.validate.nanowell_relabel import BACKUP_DIRNAME, UNMEASURED_STATE
    from datasets.validate.validate_compute import build_report

    squares, seeds, off = {"T0": True, "T1": True, "T2": False}, {"T0": 1, "T1": 2, "T2": 3}, {"T2": 1}
    plate = _dataset(tmp_path, monkeypatch, "old", squares, seeds, off, settle=False)
    hours = list(TOKENS.values())
    assert not _follows(_sites(plate), hours), "the fixture must hold the defect it repairs"

    report = build_report(plate)
    found = [f for f in report.findings if f.code == "nanowell_misaddressed"]
    assert len(found) == 1, [f.code for f in report.findings]
    finding = found[0]
    assert finding.well == "A01" and "48h by (1, 0)" in finding.message and "0h's" in finding.message
    assert report.misaddressed_nanowell_wells == ("A01",) and report.repairable

    result = apply_repairs(plate, report)
    assert result.relabelled_nanowell_wells == ("A01",) and "relabelled 1 misaddressed nanowell well" in result.summary
    sites = _sites(plate)
    assert _follows(sites, hours)

    # Against the fixed pipeline on the same images: the cell table's addresses are the same cells' addresses, and
    # every nanowell the repaired timepoint measured holds the count the fixed run gives it.
    fixed = _dataset(tmp_path, monkeypatch, "fixed", squares, seeds, off)
    key = lambda rows: sorted((r["timepoint"], r["nw_q"], r["nw_r"], r["nucleus_id"]) for r in rows)  # noqa: E731
    assert key(_cells(plate)) == key(_cells(fixed))
    placeholders = [r for r in sites if r["state"] == UNMEASURED_STATE and not r["n_cells"]]
    measured = {(r["timepoint"], r["nw_q"], r["nw_r"]): r["n_cells"] for r in sites if r not in placeholders}
    reference = {(r["timepoint"], r["nw_q"], r["nw_r"]): r["n_cells"] for r in _sites(fixed)}
    assert {k: v for k, v in measured.items() if reference.get(k) != v} == {}
    assert {r["timepoint"] for r in placeholders} == {"48h"} and len(placeholders) > 0
    assert len(sites) == len(_sites(fixed))                        # one row per roster site and timepoint

    grid = json.loads((plate / "out" / "nanowells" / "A01_grid.json").read_text())
    block = grid["timepoints"]["48h"]
    assert (block["origin_shift"], block["origin_registered_to"], block["consistency"]) == ([1, 0], "0h", "consistent")
    assert block["relabelled"]["unmeasured_sites"] == len(placeholders)
    [backup] = list((plate / "out" / "nanowells" / BACKUP_DIRNAME).iterdir())
    assert sorted(p.name for p in backup.iterdir()) == sorted(["A01.csv", "A01_grid.json",
                                                               _cells_path_name(plate)])

    again = build_report(plate)
    assert not [f for f in again.findings if f.code.startswith("nanowell_misaddressed")]
    assert not again.misaddressed_nanowell_wells

    # Review reads the repaired dataset and joins each painted nanowell across all three timepoints.
    from review.features.nanowell import series_compute as sc
    from review.features.nanowell.collect import load_nanowell_data

    grid_frame = sc._addressed_grid(load_nanowell_data(plate / "out"))
    joined = {(tp, int(q), int(r)): n for tp, q, r, n in
              zip(grid_frame.timepoint, grid_frame.nw_q, grid_frame.nw_r, grid_frame.n_cells)}
    assert [[joined[(tp, *a)] for tp in hours] for a in PAINT] == [[1, 1, 1]] * len(PAINT)


def _cells_path_name(plate) -> str:
    [path] = [p for p in (plate / "out").glob("*_A01.csv*")]
    return path.name


def _old_tables(tmp_path, monkeypatch, squares, seeds, off):
    """``out`` of one painted well written by the run without frame settling, from ``run_well`` directly."""
    from pipeline import nanowell_task as nt
    from tests._nanowell_run_fixture import threshold_segmenter

    record, images = _painted_well(squares, seeds)
    with monkeypatch.context() as patch:
        _off_by(patch, off)
        patch.setattr(nr, "_settle_steps", lambda outcome, anchored, kept=None: (list(anchored), [], {}))
        outcome = nr.run_well("A01", record, list(squares), lambda tp, tile: images[tp].get(tile),
                              nr.NanowellSettings("100um"), segmenter=threshold_segmenter())
    out = tmp_path / "out"
    nt.write_sites_csv(out / "nanowells" / "A01.csv", outcome.sites, [])
    nt.write_grid_json(out / "nanowells" / "A01_grid.json", "A01", outcome, nr.NanowellSettings("100um"))
    px = {tp: float(record["timepoints"][tp]["pixel_size_um"]["x"]) for tp in squares}
    with (out / "m_A01.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=nt.cell_columns([]))
        writer.writeheader()
        writer.writerows(nt.cell_records(outcome, "A01", [], px))
    return out


def test_the_square_anchored_baseline_is_kept_when_the_later_timepoints_share_a_wrong_centre(tmp_path, monkeypatch):
    """T0 reads the square; T1 and T2 share an array centre a site off. The earlier run decided the roster in their
    frame and flagged T0. The repair keeps T0's addresses, moves T1 and T2 onto them, and moves the roster too."""
    from datasets.validate import nanowell_relabel as rl

    out = _old_tables(tmp_path, monkeypatch, {"T0": True, "T1": False, "T2": False}, {"T0": 1, "T1": 4, "T2": 3},
                      {"T1": 1, "T2": 1})
    rows = list(csv.DictReader((out / "nanowells" / "A01.csv").open()))
    assert not _follows(rows, ["T0", "T1", "T2"])
    plan = rl.plan_dataset(out)["A01"]
    assert (plan.reference, plan.shifts, plan.roster_offset) == ("T0", {"T1": (1, 0), "T2": (1, 0)}, (-1, 0))
    rl.relabel_dataset(out, {"A01": plan}, ["m"])
    rows = list(csv.DictReader((out / "nanowells" / "A01.csv").open()))
    assert _follows(rows, ["T0", "T1", "T2"])
    cells = list(csv.DictReader((out / "m_A01.csv").open()))
    assert {(r["nw_q"], r["nw_r"]) for r in cells if r["timepoint"] == "T2"} <= {(str(q), str(r)) for q, r in PAINT}
    grid = json.loads((out / "nanowells" / "A01_grid.json").read_text())
    assert "origin_shift" not in grid["timepoints"]["T0"] and "relabelled" in grid["timepoints"]["T0"]
    assert [grid["timepoints"][tp]["consistency"] for tp in ("T0", "T1", "T2")] == ["consistent"] * 3
    assert rl.plan_dataset(out) == {}


def test_two_equally_anchored_timepoints_a_site_apart_are_reported_and_left_untouched(tmp_path, monkeypatch):
    """Neither reads the square and their centres land a site apart: the tables cannot say which is right."""
    from datasets.validate import nanowell_relabel as rl

    out = _old_tables(tmp_path, monkeypatch, {"T0": False, "T1": False}, {"T0": 1, "T1": 3}, {"T1": 1})
    before = {p.name: p.read_bytes() for p in (out / "nanowells").iterdir()}
    plan = rl.plan_dataset(out)["A01"]
    assert not plan.repairable and plan.verdict == "tie"
    severity, code, message = rl.finding_of(plan)
    assert code == "nanowell_frames_ambiguous" and "Set an anchor by hand" in message
    assert rl.relabel_dataset(out, {"A01": plan}, ["m"]) == []
    assert {p.name: p.read_bytes() for p in (out / "nanowells").iterdir()} == before


def test_a_well_whose_timepoints_agree_is_not_listed(tmp_path, monkeypatch):
    """The control: every origin right, nothing found."""
    from datasets.validate import nanowell_relabel as rl

    out = _old_tables(tmp_path, monkeypatch, {"T0": True, "T1": False, "T2": True}, {"T0": 1, "T1": 3, "T2": 2}, {})
    assert rl.plan_dataset(out) == {}


def test_a_relabelled_origin_moves_by_the_measured_offset_between_the_old_and_new_origin_nanowells():
    from datasets.validate.nanowell_relabel import _moved_origin

    rows = [{"timepoint": "T2", "nw_q": "0", "nw_r": "0", "well_x_um": "10.0", "well_y_um": "10.0"},
            {"timepoint": "T2", "nw_q": "-1", "nw_r": "0", "well_x_um": "-162.0", "well_y_um": "12.0"},
            {"timepoint": "T0", "nw_q": "-1", "nw_r": "0", "well_x_um": "999.0", "well_y_um": "999.0"}]
    assert _moved_origin({"origin_stage_um": [100.0, 200.0]}, rows, "T2", (1, 0)) == [-72.0, 202.0]
    assert _moved_origin({"origin_stage_um": None}, rows, "T2", (1, 0)) is None


def test_two_hidden_rim_nanowells_are_not_read_as_a_step():
    """A bubble over two rim nanowells, where the image covers only those two of the rim wholly: they are imaged and
    empty, which is also what a step (1, 0) predicts there. Two sites are noise, not a crescent."""
    from datasets.validate.nanowell_relabel import _step_against_roster

    roster = {(q, r) for q in range(-3, 4) for r in range(-3, 4) if max(abs(q), abs(r), abs(q + r)) <= 3}
    interior = {a for a in roster if max(abs(a[0]), abs(a[1]), abs(a[0] + a[1])) <= 2}
    hidden = {(3, 0), (2, 1)}
    rows = [{"timepoint": "T1", "nw_q": str(q), "nw_r": str(r), "imaged_fraction": "1.0",
             "detected": str((q, r) not in hidden)} for q, r in sorted(interior | hidden)]
    assert _step_against_roster(rows, roster) == (0, 0)
    # The same rim with a whole crescent empty is a step.
    crescent = {a for a in roster if (a[0] + 1, a[1]) not in roster}
    rows = [{"timepoint": "T1", "nw_q": str(q), "nw_r": str(r), "imaged_fraction": "1.0",
             "detected": str((q, r) not in crescent)} for q, r in sorted(roster)]
    assert _step_against_roster(rows, roster) == (1, 0)
