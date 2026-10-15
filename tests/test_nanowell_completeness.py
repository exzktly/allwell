"""Completeness of a nanowell well is counted in timepoints, the unit of work there (tracker P8)."""

from __future__ import annotations

import json

from pipeline import completeness as C

SCHEMA = ["experiment", "channel", "well", "fov", "timepoint"]
MEMBERS = [f"exp_PHASE_A01_F{f}_{tp}.tif" for f in range(4) for tp in ("0h", "24h", "48h")]


def _well(tmp_path, statuses, *, rows=3, out_zip=True):
    grid = {"scheme_version": 1, "well": "A01", "timepoints": {tp: {"status": s} for tp, s in statuses.items()}}
    (tmp_path / "nanowells").mkdir()
    (tmp_path / "nanowells" / "A01_grid.json").write_text(json.dumps(grid))
    (tmp_path / "nanowells" / "A01.csv").write_text("timepoint\n" + "0h\n" * rows)
    if out_zip:
        (tmp_path / "A01_out.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    return tmp_path


def _coverage(tmp_path):
    return C.nanowell_coverage("A01", in_members=MEMBERS, grid_path=tmp_path / "nanowells" / "A01_grid.json",
                               sites_path=tmp_path / "nanowells" / "A01.csv", has_out_zip=(tmp_path / "A01_out.zip").exists(),
                               schema=SCHEMA, sep="_")


def test_a_timepoint_that_did_not_finish_is_missing_not_hidden_by_the_other_tiles(tmp_path):
    cov = _coverage(_well(tmp_path, {"0h": "ok", "24h": "ok", "48h": "incomplete_grid"}))
    assert (cov.intent, cov.covered_csv, cov.missing) == (3, 2, (("", "48h"),))
    assert C.health(missing=cov.missing, csv_state=cov.csv_state, has_in_container=True, has_out_zip=True,
                    has_out_folder=False) == "partial"


def test_a_well_of_empty_nanowells_with_no_cell_rows_is_complete(tmp_path):
    cov = _coverage(_well(tmp_path, {"0h": "ok", "24h": "ok", "48h": "ok"}))
    assert cov.missing == () and cov.intent == 3          # no per-cell CSV is needed to be whole
    assert C.health(missing=cov.missing, csv_state=cov.csv_state, has_in_container=True, has_out_zip=True,
                    has_out_folder=False) == "complete"


def test_a_timepoint_the_run_never_recorded_is_missing(tmp_path):
    cov = _coverage(_well(tmp_path, {"0h": "ok"}))
    assert cov.missing == (("", "24h"), ("", "48h"))


def test_an_empty_or_absent_nanowell_table_is_not_a_readable_csv(tmp_path):
    assert _coverage(_well(tmp_path, {"0h": "ok", "24h": "ok", "48h": "ok"}, rows=0)).csv_state == "empty"
    (tmp_path / "nanowells" / "A01.csv").unlink()
    assert _coverage(tmp_path).csv_state == "missing"


def test_the_end_of_run_scan_uses_it_for_a_nanowell_well_and_the_old_rule_otherwise(tmp_path):
    _well(tmp_path, {"0h": "ok", "24h": "ok", "48h": "ok"})
    cov = C._scan_well_coverage("A01", None, tmp_path, "measurements", SCHEMA, "_", True, ())
    assert cov.intent == 0 and cov.missing == ()                  # no readable input: nothing asked for
    other = C._scan_well_coverage("B02", None, tmp_path, "measurements", SCHEMA, "_", True, ())
    assert other.csv_state == "missing" and other.has_out_zip is False
