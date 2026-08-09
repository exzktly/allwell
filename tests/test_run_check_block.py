"""The ``run_check`` block a finished run writes into ``pipeline_info.json``.

Pure logic — a synthetic dataset on disk, no Qt, no app, no imaging stack. The
block is what the Review Home page reports, so what these tests pin is not the
scan's cleverness but its honesty: a well the scan could not read must not look
like a well it read and found complete, and a number it could not compute must
not arrive as a zero.
"""

from __future__ import annotations

import time
import zipfile
from pathlib import Path

import pytest

from pipeline.completeness import (
    csv_keys_and_state,
    errors_since,
    scan_run,
    wells_with_output,
)

SCHEMA = ["exp", "channel", "well", "fov", "timepoint"]
SEP = "_"
PREFIX = "measurements"


def _name(well: str, fov: str, tp: str, *, channel: str = "DAPI") -> str:
    return f"exp1_{channel}_{well}_{fov}_{tp}.tif"


def _zip(path: Path, names) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, b"x")
    return path


def _csv(path: Path, keys) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"{fov},{tp},1" for fov, tp in keys)
    path.write_text("fov,timepoint,nucleus_id\n" + rows + ("\n" if rows else ""))
    return path


def _plate(root: Path, wells, *, fovs=("F001", "F002"), tps=("0", "24h")):
    """A complete little dataset: input archive, output archive and CSV per well."""
    in_dir, out_dir = root / "in", root / "out"
    containers = {}
    for well in wells:
        names = [_name(well, f, t) for f in fovs for t in tps]
        containers[well] = _zip(in_dir / f"{well}.zip", names)
        _zip(out_dir / f"{well}_out.zip", names)
        _csv(out_dir / f"{PREFIX}_{well}.csv", [(f, t) for f in fovs for t in tps])
    return containers, out_dir


def _scan(containers, out_dir, **kw):
    kw.setdefault("schema", SCHEMA)
    kw.setdefault("sep", SEP)
    kw.setdefault("csv_prefix", PREFIX)
    kw.setdefault("fov_field_present", True)
    return scan_run(in_containers=containers, output_dir=out_dir, **kw)


# ── the clean plate ─────────────────────────────────────────────────────────

def test_a_complete_plate_reports_every_well_complete(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01", "A02", "B01"])
    block = _scan(containers, out_dir)

    assert block["totals"]["wells"] == 3
    assert block["totals"]["complete"] == 3
    assert block["totals"]["partial"] == 0
    assert set(block["wells"]) == {"A01", "A02", "B01"}
    assert all(rec["health"] == "complete" for rec in block["wells"].values())
    assert block["partial_scan"] is False
    assert block["skipped"] is False


def test_totals_are_the_sum_of_the_per_well_numbers(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01", "A02"])
    block = _scan(containers, out_dir)
    wells = block["wells"].values()
    assert block["totals"]["intent"] == sum(w["intent"] for w in wells)
    assert block["totals"]["covered_csv"] == sum(w["covered_csv"] for w in wells)
    assert block["totals"]["covered_any"] == sum(w["covered_any"] for w in wells)
    # Four (fov, timepoint) groups per well, two wells.
    assert block["totals"]["intent"] == 8


# ── the states the page paints ──────────────────────────────────────────────

def test_a_well_missing_groups_reads_partial_and_names_them(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01"])
    # Rewrite A01's output to cover half its input.
    _zip(out_dir / "A01_out.zip", [_name("A01", "F001", "0")])
    _csv(out_dir / f"{PREFIX}_A01.csv", [("F001", "0")])

    rec = _scan(containers, out_dir)["wells"]["A01"]
    assert rec["health"] == "partial"
    assert rec["intent"] == 4
    assert rec["covered_csv"] == 1
    assert rec["missing_count"] == 3
    assert ["F002", "24h"] in rec["missing"]


def test_a_whole_archive_behind_an_empty_csv_is_not_complete(tmp_path):
    """The state the two covered numbers exist to tell apart.

    Every plot reads the CSV, so a well whose CSV holds nothing has nothing to
    show — but its archive covers the input, so nothing is *missing* and a
    coverage figure alone would call it finished.
    """
    containers, out_dir = _plate(tmp_path, ["A01"])
    _csv(out_dir / f"{PREFIX}_A01.csv", [])

    rec = _scan(containers, out_dir)["wells"]["A01"]
    assert rec["missing_count"] == 0
    assert rec["covered_any"] == 4
    assert rec["covered_csv"] == 0
    assert rec["health"] == "missing", "images with no measurements is not complete"


def test_a_staged_but_never_processed_well_is_distinguishable(tmp_path):
    """Staged and never run is *not* a fifth health state.

    Every one of its groups is missing, so the shared classifier calls it
    partial — the same answer Validate gives, which is the point of sharing it.
    What separates it from a half-finished well is that it covers nothing at
    all, and the record carries the numbers to say so without anyone
    re-deriving completeness: a reader wanting to paint "staged, never
    processed" asks for ``covered_any == 0``, not for a new classification.
    """
    containers, out_dir = _plate(tmp_path, ["A01"])
    (out_dir / "A01_out.zip").unlink()
    (out_dir / f"{PREFIX}_A01.csv").unlink()

    rec = _scan(containers, out_dir)["wells"]["A01"]
    assert rec["health"] == "partial"
    assert rec["covered_any"] == 0
    assert rec["covered_csv"] == 0
    assert rec["missing_count"] == rec["intent"] == 4
    assert rec["has_in"] is True
    assert rec["has_out_zip"] is False
    assert rec["csv_state"] == "missing"


def test_a_half_finished_well_is_not_confused_with_an_unstarted_one(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01", "A02"])
    # A01 never ran; A02 got half way.
    (out_dir / "A01_out.zip").unlink()
    (out_dir / f"{PREFIX}_A01.csv").unlink()
    _zip(out_dir / "A02_out.zip", [_name("A02", "F001", "0")])
    _csv(out_dir / f"{PREFIX}_A02.csv", [("F001", "0")])

    wells = _scan(containers, out_dir)["wells"]
    assert wells["A01"]["health"] == wells["A02"]["health"] == "partial"
    assert wells["A01"]["covered_any"] == 0
    assert wells["A02"]["covered_any"] == 1


def test_folder_mode_output_counts_as_image_evidence(tmp_path):
    """A run told not to compress writes ``<out>/<well>/`` instead of an
    archive. A reader that only knew about archives would call the well empty."""
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    names = [_name("A01", "F001", "0"), _name("A01", "F002", "0")]
    containers = {"A01": _zip(in_dir / "A01.zip", names)}
    folder = out_dir / "A01"
    folder.mkdir(parents=True)
    for name in names:
        (folder / name).touch()
    _csv(out_dir / f"{PREFIX}_A01.csv", [("F001", "0"), ("F002", "0")])

    rec = _scan(containers, out_dir)["wells"]["A01"]
    assert rec["has_out_folder"] is True
    assert rec["has_out_zip"] is False
    assert rec["health"] == "complete"


def test_a_single_fov_dataset_is_measured_per_timepoint(tmp_path):
    """No ``fov`` field in the schema: the diff runs on timepoint alone rather
    than reporting every well as missing everything."""
    schema = ["exp", "channel", "well", "timepoint"]
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    names = [f"exp1_DAPI_A01_{tp}.tif" for tp in ("0", "24h", "48h")]
    containers = {"A01": _zip(in_dir / "A01.zip", names)}
    _zip(out_dir / "A01_out.zip", names[:2])
    # The CSV's own fov column is normalised to "1" by the pipeline.
    _csv(out_dir / f"{PREFIX}_A01.csv", [("1", "0"), ("1", "24h")])

    rec = _scan(containers, out_dir, schema=schema,
                fov_field_present=False)["wells"]["A01"]
    assert rec["missing_count"] == 1
    assert rec["missing"] == [["", "48h"]]


def test_input_and_output_need_not_be_siblings(tmp_path):
    """``main()`` holds two unrelated directories, not a dataset root."""
    in_dir = tmp_path / "somewhere" / "raw"
    out_dir = tmp_path / "elsewhere" / "results"
    names = [_name("A01", "F001", "0")]
    containers = {"A01": _zip(in_dir / "A01.zip", names)}
    _zip(out_dir / "A01_out.zip", names)
    _csv(out_dir / f"{PREFIX}_A01.csv", [("F001", "0")])

    assert _scan(containers, out_dir)["wells"]["A01"]["health"] == "complete"


# ── the record is an inventory, not a log of this invocation ────────────────

def test_wells_this_run_skipped_are_still_recorded(tmp_path):
    """The hazard that rules out deriving this from ``run_report``.

    An incremental run records completeness only for wells it actually
    processed — a well that was already up to date returns before the record is
    written, and the block is replaced wholesale each run. A record built that
    way would report a 96-well plate as three wells. This one reads the disk, so
    a run that touched one well still describes all three.
    """
    containers, out_dir = _plate(tmp_path, ["A01", "A02", "A03"])
    # However few wells the invocation touched, the caller passes the full map
    # it resolved from the input directory.
    block = _scan(containers, out_dir)
    assert set(block["wells"]) == {"A01", "A02", "A03"}
    assert block["totals"]["complete"] == 3


def test_a_dataset_whose_inputs_were_cleaned_away_still_reports_its_plate(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01", "A02"])
    for path in containers.values():
        path.unlink()

    block = _scan({}, out_dir)
    assert set(block["wells"]) == {"A01", "A02"}
    assert all(rec["has_in"] is False for rec in block["wells"].values())
    # No input to compare against means nothing can be called missing.
    assert all(rec["missing_count"] == 0 for rec in block["wells"].values())


def test_wells_with_output_finds_every_kind_of_evidence(tmp_path):
    out_dir = tmp_path / "out"
    _zip(out_dir / "A01_out.zip", ["x.tif"])
    _csv(out_dir / f"{PREFIX}_B02.csv", [("F001", "0")])
    (out_dir / "C03").mkdir(parents=True)
    (out_dir / "errors").mkdir(parents=True)
    assert wells_with_output(out_dir, PREFIX) == {"A01", "B02", "C03"}


def test_the_errors_directory_is_not_mistaken_for_a_well(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01"])
    (out_dir / "errors").mkdir(parents=True, exist_ok=True)
    assert set(_scan(containers, out_dir)["wells"]) == {"A01"}


# ── truncation, so a broken plate cannot bloat the sidecar ──────────────────

def test_the_missing_list_is_capped_but_the_count_is_not(tmp_path):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    names = [_name("A01", f"F{i:03d}", "0") for i in range(50)]
    containers = {"A01": _zip(in_dir / "A01.zip", names)}
    _zip(out_dir / "A01_out.zip", [])
    _csv(out_dir / f"{PREFIX}_A01.csv", [])

    rec = _scan(containers, out_dir, max_missing_per_well=20)["wells"]["A01"]
    assert rec["missing_count"] == 50
    assert len(rec["missing"]) == 20


# ── errors ──────────────────────────────────────────────────────────────────

def _traceback(out_dir: Path, name: str, exc_type: str, *, mtime: float | None = None):
    err_dir = out_dir / "errors"
    err_dir.mkdir(parents=True, exist_ok=True)
    path = err_dir / name
    path.write_text(
        f"Well: A01\nPID:  123\nException type: {exc_type}\n\nTraceback…\n")
    if mtime is not None:
        import os
        os.utime(path, (mtime, mtime))
    return path


def test_errors_are_counted_and_named_by_type(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01"])
    _traceback(out_dir, "A01_F001_pid1.txt", "MemoryError")
    _traceback(out_dir, "A01_F002_pid1.txt", "MemoryError")
    _traceback(out_dir, "A01_F003_pid1.txt", "OSError")

    errors = _scan(containers, out_dir)["errors"]
    assert errors["files"] == 3
    assert errors["by_type"] == {"MemoryError": 2, "OSError": 1}


def test_a_previous_runs_tracebacks_are_not_counted_as_this_runs(tmp_path):
    """``errors/`` is never cleared and its filenames carry no run id, so a
    clean re-run into the same folder would otherwise inherit the last run's
    failures and report a healthy plate as broken."""
    containers, out_dir = _plate(tmp_path, ["A01"])
    _traceback(out_dir, "A01_F001_pid1.txt", "MemoryError", mtime=time.time() - 3600)
    started = time.time() - 60
    _traceback(out_dir, "A01_F002_pid9.txt", "OSError")

    errors = _scan(containers, out_dir, started_at=started)["errors"]
    assert errors["files"] == 1
    assert errors["by_type"] == {"OSError": 1}


def test_no_start_time_counts_everything_rather_than_nothing(tmp_path):
    out_dir = tmp_path / "out"
    _traceback(out_dir, "A01_pid1.txt", "ValueError", mtime=time.time() - 9999)
    count, by_type = errors_since(out_dir, None)
    assert count == 1 and by_type == {"ValueError": 1}


def test_an_absent_errors_directory_is_zero_not_a_crash(tmp_path):
    assert errors_since(tmp_path, None) == (0, {})


# ── findings ────────────────────────────────────────────────────────────────

def test_two_csv_prefixes_in_one_folder_are_reported(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01"])
    _csv(out_dir / "other_A01.csv", [("F001", "0")])

    codes = [f["code"] for f in _scan(containers, out_dir)["findings"]]
    assert "mixed_csv_prefix" in codes


def test_a_csv_with_no_well_behind_it_is_reported(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01"])
    _csv(out_dir / f"{PREFIX}_H12.csv", [("F001", "0")])

    findings = {f["code"]: f for f in _scan(containers, out_dir)["findings"]}
    assert "phantom_csv" in findings
    assert "H12" in findings["phantom_csv"]["message"]


def test_a_clean_plate_reports_no_findings(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01", "A02"])
    assert _scan(containers, out_dir)["findings"] == []


# ── degraded scans say so ───────────────────────────────────────────────────

def test_an_unreadable_input_archive_does_not_take_down_the_scan(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01", "A02"])
    containers["A01"].write_bytes(b"this is not a zip")

    block = _scan(containers, out_dir)
    assert set(block["wells"]) == {"A01", "A02"}
    # An unlistable input yields no intent, so nothing is claimed missing —
    # but the well is still recorded, and its output evidence still counts.
    assert block["wells"]["A01"]["has_in"] is False
    assert block["wells"]["A02"]["health"] == "complete"


def test_an_unreadable_csv_is_recorded_as_unreadable(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01"])
    (out_dir / f"{PREFIX}_A01.csv").write_bytes(b"\xff\xfe\x00garbage")

    rec = _scan(containers, out_dir)["wells"]["A01"]
    assert rec["csv_state"] in ("unreadable", "empty")
    assert rec["health"] != "complete"


def test_a_well_that_blows_up_mid_scan_marks_the_record_partial(tmp_path, monkeypatch):
    """A truncated record must never be mistaken for a clean plate."""
    import pipeline.completeness as pc

    containers, out_dir = _plate(tmp_path, ["A01", "A02"])
    real = pc.coverage_for_well

    def _boom(well, **kw):
        if well == "A01":
            raise RuntimeError("disk went away")
        return real(well, **kw)

    monkeypatch.setattr(pc, "coverage_for_well", _boom)
    block = _scan(containers, out_dir)

    assert block["partial_scan"] is True
    assert "A01" not in block["wells"], "a well that could not be read is not recorded"
    assert block["totals"]["wells"] == 1, "totals describe what was actually scanned"


# ── the run's own context ───────────────────────────────────────────────────

def test_the_block_carries_the_runs_identity(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01"])
    block = _scan(containers, out_dir, finished_at="2026-08-09T12:00:00Z",
                  started_at_iso="2026-08-09T10:00:00Z", duration_s=7200.0,
                  host="hpc-login-3", mode="zip", suspended=True)
    assert block["version"] == 1
    assert block["finished_at"] == "2026-08-09T12:00:00Z"
    assert block["duration_s"] == 7200.0
    assert block["host"] == "hpc-login-3"
    assert block["mode"] == "zip"
    assert block["suspended"] is True
    assert block["schema"] == "exp:channel:well:fov:timepoint"
    assert block["separator"] == SEP
    assert block["csv_prefix"] == PREFIX


def test_failed_group_labels_reach_the_well_they_belong_to(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01"])
    block = _scan(containers, out_dir, failed_by_well={"A01": ["F003_24h"]})
    assert block["wells"]["A01"]["failed"] == ["F003_24h"]


def test_the_block_is_json_serialisable(tmp_path):
    """It is written into pipeline_info.json, so a tuple key or a Path in here
    would fail at the very end of a run that had already done all its work."""
    import json

    containers, out_dir = _plate(tmp_path, ["A01"])
    block = _scan(containers, out_dir, failed_by_well={"A01": ["F003_24h"]})
    assert json.loads(json.dumps(block)) == block


# ── the CSV reader ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(("body", "expected_state"), [
    ("fov,timepoint\nF001,0\n", "ok"),
    ("fov,timepoint\n", "empty"),
    ("nucleus_id,area\n1,50\n", "empty"),
    ("", "unreadable"),
])
def test_csv_state_distinguishes_absent_empty_and_unparseable(tmp_path, body,
                                                              expected_state):
    path = tmp_path / "m_A01.csv"
    path.write_text(body)
    assert csv_keys_and_state(path)[1] == expected_state


def test_a_missing_csv_is_missing_not_unreadable(tmp_path):
    assert csv_keys_and_state(tmp_path / "nope.csv") == (set(), "missing")
