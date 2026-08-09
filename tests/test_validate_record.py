"""A finished Validate scan writes the dataset's completeness record.

The gap these cover: Review's Home page reports a ``run_check`` record rather
than scanning, and a dataset produced before the end-of-run check existed has
none. Home's only offered action for such a dataset is to open the Datasets
Validate tool — which measures exactly the same thing and, until this existed,
threw the answer away. Every test here builds a real dataset on disk and runs
the real scan, so what is asserted is what a user's folder would receive.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from datasets.validate import validate_compute as vc
from datasets.validate.record import block_from_report, write_record
from pipeline.completeness import HEALTH_STATES
from tests.fixtures.dataset_builder import build_dataset


def _info(root):
    return json.loads((root / "out" / "pipeline_info.json").read_text())


def _truncate(root, well: str, keep_tp: str = "0h") -> None:
    """Leave *well* covering only one timepoint, in both its archive and CSV."""
    import csv
    import zipfile

    out_zip = root / "out" / f"{well}_out.zip"
    with zipfile.ZipFile(out_zip) as zf:
        members = zf.namelist()
    kept = [m for m in members if m.split("_")[-1].split(".")[0] == keep_tp]
    with zipfile.ZipFile(out_zip, "w") as zf:
        for member in kept:
            zf.writestr(member, b"x")
    csv_path = root / "out" / f"measurements_{well}.csv"
    with csv_path.open() as handle:
        rows = list(csv.DictReader(handle))
    fieldnames = list(rows[0].keys())
    rows = [r for r in rows if r["timepoint"] == keep_tp]
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ── the record a scan produces ──────────────────────────────────────────────

def test_a_scan_of_a_healthy_plate_records_every_well_complete(tmp_path):
    build_dataset(tmp_path, wells=["A01", "B02"], timepoints=["0h", "24h"])
    block = block_from_report(vc.build_report(tmp_path))

    assert block["totals"] == {
        "wells": 2, "complete": 2, "partial": 0, "missing_csv": 0, "no_output": 0,
        "intent": block["totals"]["intent"],
        "covered_csv": block["totals"]["covered_csv"],
        "covered_any": block["totals"]["covered_any"],
    }
    assert block["totals"]["intent"] == block["totals"]["covered_any"]
    assert set(block["wells"]) == {"A01", "B02"}
    assert all(w["health"] in HEALTH_STATES for w in block["wells"].values())


def test_the_record_carries_the_coverage_sizes_not_only_what_is_missing(tmp_path):
    """``missing`` alone says how much is absent, never how much was asked for.
    Home's plate draws a fraction, so the record has to carry both."""
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h", "24h", "48h"])
    _truncate(tmp_path, "A01")

    well = block_from_report(vc.build_report(tmp_path))["wells"]["A01"]
    assert well["intent"] == 3
    assert well["covered_any"] == 1
    assert well["covered_csv"] == 1
    assert well["missing_count"] == 2
    assert well["health"] == "partial"


def test_a_full_archive_with_an_empty_csv_separates_the_two_counts(tmp_path):
    """The state Home reports as "images, no measurements": nothing is missing,
    because the archive covers everything, and yet the CSV the plots read is
    empty. One number cannot say that; two can."""
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h", "24h"])
    csv_path = tmp_path / "out" / "measurements_A01.csv"
    header = csv_path.read_text().splitlines()[0]
    csv_path.write_text(header + "\n")

    well = block_from_report(vc.build_report(tmp_path))["wells"]["A01"]
    assert well["covered_any"] == 2
    assert well["covered_csv"] == 0
    assert well["missing_count"] == 0
    assert well["csv_state"] == "empty"


def test_the_record_says_a_scan_produced_it(tmp_path):
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    block = block_from_report(vc.build_report(tmp_path))
    assert block["source"] == "validate"
    assert block["finished_at"], "a record with no timestamp cannot be read as a snapshot"


def test_findings_carry_across_with_the_codes_the_page_already_renders(tmp_path):
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"], csv_prefix="measurements")
    (tmp_path / "out" / "run2_A01.csv").write_text(
        "well,fov,timepoint,timepoint_hours\nA01,F001,0h,0\n")

    block = block_from_report(vc.build_report(tmp_path))
    codes = {f["code"] for f in block["findings"]}
    assert "mixed_csv_prefix" in codes
    assert all({"severity", "code", "message", "well"} <= set(f) for f in block["findings"])


# ── writing it back ─────────────────────────────────────────────────────────

def test_writing_the_record_leaves_every_other_sidecar_key_alone(tmp_path):
    """The sidecar carries the user's own work — notes, sample definitions,
    gating. A from-scratch rebuild would have to name each one to keep it."""
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    info_path = tmp_path / "out" / "pipeline_info.json"
    before = json.loads(info_path.read_text())
    before["notes"] = "the incubator alarmed overnight"
    before["sample_definitions"] = {"groups": [{"name": "ctrl", "wells": ["A01"]}]}
    info_path.write_text(json.dumps(before))

    write_record(vc.build_report(tmp_path))

    after = _info(tmp_path)
    assert after["notes"] == "the incubator alarmed overnight"
    assert after["sample_definitions"] == before["sample_definitions"]
    for key in before:
        assert key in after, f"writing the record dropped {key!r}"
    assert after["run_check"]["source"] == "validate"


def test_writing_twice_replaces_rather_than_accumulates(tmp_path):
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h", "24h"])
    write_record(vc.build_report(tmp_path))
    _truncate(tmp_path, "A01")
    write_record(vc.build_report(tmp_path))

    record = _info(tmp_path)["run_check"]
    assert record["totals"]["complete"] == 0
    assert record["totals"]["partial"] == 1
    assert record["wells"]["A01"]["covered_any"] == 1


def test_a_scan_overwrites_a_records_the_run_wrote_and_says_so(tmp_path):
    """A dataset whose run recorded it complete, then lost a well to a deleted
    archive. The newer record is the scan's, and it must be labelled as one."""
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    info_path = tmp_path / "out" / "pipeline_info.json"
    info = json.loads(info_path.read_text())
    info["run_check"] = {"version": 1, "source": "run", "wells": {},
                         "totals": {"wells": 1, "complete": 1}}
    info_path.write_text(json.dumps(info))

    write_record(vc.build_report(tmp_path))
    assert _info(tmp_path)["run_check"]["source"] == "validate"


def test_a_sidecar_that_cannot_be_parsed_raises_rather_than_writing_nothing(tmp_path):
    """The caller decides what a failure to record means. Swallowing it here is
    how a scan reports success having written nothing."""
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    report = vc.build_report(tmp_path)
    (tmp_path / "out" / "pipeline_info.json").write_text("{ not json")
    with pytest.raises(ValueError):
        write_record(report)


# ── what Home then reads ────────────────────────────────────────────────────

def test_home_reads_the_scans_record_the_same_way_it_reads_a_runs(tmp_path):
    from well_viewer.features.home import collect as hc

    build_dataset(tmp_path, wells=["A01", "B02"], timepoints=["0h", "24h"])
    _truncate(tmp_path, "B02")
    block = write_record(vc.build_report(tmp_path))

    ctx = SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(
            data_dir=tmp_path / "out", pipeline_info={"run_check": block},
            well_paths={"A01": None, "B02": None}),
        selection=SimpleNamespace(selected_wells=set(), notes_text=""),
    ))
    cells = hc.plate_cells(ctx)
    assert cells["A01"] == hc.CELL_COMPLETE
    assert cells["B02"] == hc.CELL_PARTIAL
    assert hc.plate_fractions(ctx)["B02"] == pytest.approx(0.5)
    assert hc.identity(ctx)["source"] == "validate"
    assert all(t.available for t in hc.stat_tiles(ctx) if t.label == "Coverage")


def test_home_picks_up_a_record_written_while_the_dataset_was_open(tmp_path):
    """The loop Home's own next step sets up: it says the dataset has never
    been checked, sends the user to Validate, and has to show the answer when
    they come back. Review holds the sidecar in memory, so without a re-read
    the page that prompted the scan still reports no record."""
    from well_viewer.features.home.controller import resync_record

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    info = {"schema": "well_fov_timepoint", "notes": "typed but not saved"}
    ctx = SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(data_dir=tmp_path / "out", pipeline_info=info)))

    resync_record(ctx)
    assert "run_check" not in info, "premise: nothing has recorded this dataset yet"

    write_record(vc.build_report(tmp_path))
    resync_record(ctx)

    assert info["run_check"]["source"] == "validate"
    assert info["notes"] == "typed but not saved", (
        "re-reading the whole sidecar would discard edits not yet written")


def test_a_record_deleted_on_disk_stops_being_reported(tmp_path):
    from well_viewer.features.home.controller import resync_record

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    block = write_record(vc.build_report(tmp_path))
    info = {"run_check": block}
    ctx = SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(data_dir=tmp_path / "out", pipeline_info=info)))

    on_disk_path = tmp_path / "out" / "pipeline_info.json"
    on_disk = json.loads(on_disk_path.read_text())
    on_disk.pop("run_check")
    on_disk_path.write_text(json.dumps(on_disk))

    resync_record(ctx)
    assert "run_check" not in info


def test_an_unreadable_sidecar_keeps_the_record_already_loaded(tmp_path):
    """A transient read failure must not cost a record the page already has."""
    from well_viewer.features.home.controller import resync_record

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    block = write_record(vc.build_report(tmp_path))
    info = {"run_check": block}
    ctx = SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(data_dir=tmp_path / "out", pipeline_info=info)))

    (tmp_path / "out" / "pipeline_info.json").write_text("{ not json")
    resync_record(ctx)
    assert info["run_check"] == block


# ── the worker's own seam ───────────────────────────────────────────────────

def test_the_worker_records_the_scan_it_just_finished(tmp_path, qapp):
    """``ValidateWorker.run`` is what the Datasets pane actually calls. Run it
    on this thread — the record write sits between the scan and ``succeeded``,
    and a test of ``write_record`` alone would not notice it being unwired."""
    from datasets.validate.worker import ValidateWorker

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    worker = ValidateWorker(str(tmp_path))
    lines: list[str] = []
    worker.logLine.connect(lines.append)
    worker.run()

    assert _info(tmp_path)["run_check"]["source"] == "validate"
    assert any("Recorded into pipeline_info.json" in line for line in lines)


def test_a_record_that_cannot_be_written_is_said_out_loud(tmp_path, qapp, monkeypatch):
    """Silence here would leave the user re-running a scan that appears to do
    nothing, because Home would go on reporting the dataset as unchecked."""
    from datasets.validate import worker as worker_mod

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    monkeypatch.setattr(worker_mod, "write_record", lambda *a, **k: (_ for _ in ()).throw(
        OSError("read-only filesystem")))

    worker = worker_mod.ValidateWorker(str(tmp_path))
    lines: list[str] = []
    succeeded: list = []
    worker.logLine.connect(lines.append)
    worker.succeeded.connect(lambda *a: succeeded.append(a))
    worker.run()

    assert any("Could not record this scan" in line for line in lines)
    assert succeeded, "a failure to record must not fail the scan"
