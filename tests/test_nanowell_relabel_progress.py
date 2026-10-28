"""Validate's nanowell relabel reports its progress, and rewrites a cell table without holding it whole.

Reported: the repair showed "Applying repairs…" and then nothing, for a plate whose cell tables run to millions of
rows. A log that stays silent for minutes cannot be told from a hung one.
"""
from __future__ import annotations

import csv
import gzip

from datasets.validate import nanowell_relabel as rl


def _cells(path, rows):
    opener = gzip.open(path, "wt", newline="") if path.suffix == ".gz" else path.open("w", newline="")
    with opener as fh:
        writer = csv.writer(fh)
        writer.writerow(["well", "fov", "timepoint", "nucleus_id", "nw_q", "nw_r", "nw_ring", "nw_label"])
        writer.writerows(rows)


def _read(path):
    opener = gzip.open(path, "rt", newline="") if path.suffix == ".gz" else path.open(newline="")
    with opener as fh:
        return list(csv.DictReader(fh))


def test_a_cell_table_is_relabelled_row_by_row_and_says_how_far_it_has_got(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "PROGRESS_EVERY_ROWS", 2)
    path = tmp_path / "m_A01.csv.gz"
    _cells(path, [("A01", "F1", "T0", 1, 0, 0, 0, "0_0"), ("A01", "F1", "T2", 2, 0, 0, 0, "0_0"),
                  ("A01", "F1", "T2", 3, 2, 0, 2, "2_0"), ("A01", "F1", "T2", 4, "", "", "", ""),
                  ("A01", "F2", "T2", 5, -1, 1, 1, "-1_1")])
    said = []
    unplaced, n = rl._relabel_cells_file(path, {"T2": (1, 0)}, {(0, 0), (1, 0), (0, 1), (-1, 1), (1, -1)},
                                         {"T2": "hex"}, said.append)
    rows = _read(path)
    assert n == 5 and unplaced == {"T2": 1}
    assert [(r["timepoint"], r["nw_q"], r["nw_r"], r["nw_label"]) for r in rows] == [
        ("T0", "0", "0", "0_0"),                     # not a shifted timepoint: untouched
        ("T2", "1", "0", "1_0"),                     # renamed by the step
        ("T2", "", "", ""),                          # (3, 0) is not in the roster: the cell keeps no address
        ("T2", "", "", ""),                          # had no address: untouched
        ("T2", "0", "1", "0_1")]
    assert said == ["    m_A01.csv.gz: 2 cells so far…", "    m_A01.csv.gz: 4 cells so far…"]
    assert not list(tmp_path.glob("*.part"))


def test_planning_says_what_it_found_for_each_well(tmp_path):
    import json

    folder = tmp_path / "nanowells"
    folder.mkdir()
    for well in ("A01", "A02"):
        (folder / f"{well}_grid.json").write_text(json.dumps({"array_radius": {"radius_um": None}, "timepoints": {}}))
        (folder / f"{well}.csv").write_text("timepoint,nw_q,nw_r\n")
    said = []
    assert rl.plan_dataset(tmp_path, said.append) == {}
    assert said == ["  A01 (1/2): timepoints share one frame", "  A02 (2/2): timepoints share one frame"]
