"""End-to-end test for Validate & Repair: real dataset on disk, real planner,
real repair, then reopened through the same readers Review uses.

Qt-free and dependency-free (nothing here segments anything), so — unlike
``tests/test_segmentation_e2e.py`` — this needs no ``importorskip`` and runs
in the fast lane (``tests/conftest.py`` only auto-marks ``slow`` when
``viewer_app`` is in a test's fixture names, which this file never uses).
"""

from __future__ import annotations

import csv
import json
import zipfile

from datasets.validate.apply import apply_repairs
from datasets.validate.validate_compute import build_report
from tests.fixtures.dataset_builder import build_dataset
from well_viewer.data.data_loading import load_well_csv
from well_viewer.domain.viewer_state import read_pipeline_info


def test_validate_repair_end_to_end_then_reopen_in_review(tmp_path):
    build_dataset(tmp_path, wells=["A01", "B02"], timepoints=["0h", "24h", "48h"])

    # Crash-truncate A01 to its first timepoint only.
    out_zip = tmp_path / "out" / "A01_out.zip"
    with zipfile.ZipFile(out_zip) as zf:
        members = zf.namelist()
    keep = [m for m in members if m.endswith("_0h.tif") or "_0h_" in m]
    with zipfile.ZipFile(out_zip, "w") as zf:
        for m in keep:
            zf.writestr(m, b"x")
    csv_path = tmp_path / "out" / "measurements_A01.csv"
    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
    fieldnames = list(rows[0].keys())
    rows = [r for r in rows if r["timepoint"] == "0h"]
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    # Inflate the sidecar's availability the way a crash leaves it (written
    # before any image work, so it still advertises the full plate).
    info_path = tmp_path / "out" / "pipeline_info.json"
    info = json.loads(info_path.read_text())
    assert info["available_timepoints"] == ["0h", "24h", "48h"]

    report = build_report(tmp_path)
    assert not report.is_clean
    a01 = next(w for w in report.wells if w.well == "A01")
    assert set(a01.missing) == {("F001", "24h"), ("F001", "48h")}
    # The sidecar's own availability list is still accurate here (nothing
    # deleted the timepoints' raw *input* images) — the missing-FOV finding,
    # not a sidecar-drift finding, is what carries this scenario. Repair is a
    # no-op metadata action; completing the run (Analyze, incremental) is the
    # actual fix, which this feature hands off to rather than performing.
    result = apply_repairs(tmp_path, report)
    assert result.summary == "nothing to repair"

    # Whatever Validate reports, the dataset must still be a dataset Review
    # can open — the assertion that would have caught a rewrite desyncing the
    # four coupled timepoint values (ARCHITECTURE §6b).
    extractor, fluor_tokens, smfish_tokens, pinfo = read_pipeline_info(tmp_path / "out")
    assert extractor is not None
    assert "gfp" in fluor_tokens
    assert pinfo["available_timepoints"] == ["0h", "24h", "48h"]

    df = load_well_csv(tmp_path / "out" / "measurements_A01.csv")
    assert len(df) == 1
    assert set(df["timepoint"]) == {"0h"}

    df_b02 = load_well_csv(tmp_path / "out" / "measurements_B02.csv")
    assert len(df_b02) == 3
    assert set(df_b02["timepoint"]) == {"0h", "24h", "48h"}
