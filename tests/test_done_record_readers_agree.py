"""Every reader of "which FOV-groups are done" takes the well's record of finished groups the same way.

The full-app review (pull request #1343), row C6. ``pipeline/done_groups.py``
records which groups a well task processed to the end. The well task's own
readers are driven in ``test_a_field_with_no_cells_is_done.py``; these are the
others, each of which used to spell *done* as "in the CSV, or has images in
the output archive":

* the remote upload's incremental split (``services.remote_staging``),
* the remote re-run's "is this well finished here" (``services.remote_finished_wells``),
* the Fetcher bringing the record back (``pull_done_record``),
* Validate (``datasets.validate.validate_compute``).

One fixture serves all four, and it is built so the two rules disagree in
both directions: field ``F002`` has images in the archive and no rows (its
quantification failed), and field ``F003`` has neither rows nor images but is
in the record as measured with no cells. By the earlier rule F002 is done and
F003 is not; by the record it is the other way round.
"""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from datasets.validate import validate_compute as vc
from pipeline.done_groups import DONE_DIRNAME, done_record_path, parse_done_record
from services.remote_finished_wells import _field_coverage, pull_done_record
from services.remote_staging import stage_local_zips
from tests.fixtures.dataset_builder import build_dataset

SCHEMA = "experiment:channel:well:fov:timepoint"
KEY = "settings-one"
F1, F2, F3 = ("F001", "0h"), ("F002", "0h"), ("F003", "0h")


def _dataset(tmp_path, *, record: bool = True, digest: str = KEY) -> Path:
    """A01 with three fields in its input (and the debris a real archive carries); see the module docstring."""
    root = build_dataset(tmp_path, wells=["A01"], timepoints=["0h"], fovs=("F001", "F002", "F003"), junk=True)
    out = root / "out"
    rows = list(csv.DictReader((out / "measurements_A01.csv").open()))
    with (out / "measurements_A01.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(r for r in rows if r["fov"] == "F001")
    with zipfile.ZipFile(out / "A01_out.zip") as zf:
        kept = {n: zf.read(n) for n in zf.namelist() if "_F003_" not in n and not n.endswith("/")}
    with zipfile.ZipFile(out / "A01_out.zip", "w") as zf:
        for name, data in kept.items():
            zf.writestr(name, data)
    if record:
        _write_record(done_record_path(out, "A01"), [F1, F3], [F3], digest)
    return root


def _write_record(path: Path, groups, no_cells, digest: str = KEY) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"well": "A01", "format": 1, "digest": digest,
                                "groups": [list(k) for k in groups], "no_cells": [list(k) for k in no_cells]}))


# ── the remote upload's incremental split ───────────────────────────────────

def _staged_fields(root: Path) -> set[str]:
    zipped = stage_local_zips(root / "in", filename_schema=SCHEMA, filename_sep="_", incremental=True,
                              prior_output_dir=root / "out")
    fields = set()
    for archive in zipped.well_zips:
        with zipfile.ZipFile(archive) as zf:
            fields |= {Path(n).stem.split("_")[3] for n in zf.namelist()}
    return fields


def test_the_upload_split_sends_the_failed_field_and_not_the_empty_one(tmp_path):
    assert _staged_fields(_dataset(tmp_path)) == {"F002"}


def test_the_upload_split_of_a_well_with_no_record_is_the_earlier_one(tmp_path):
    """No record: images count as done, so F002 is not sent and F003 — no rows, no images — is."""
    assert _staged_fields(_dataset(tmp_path, record=False)) == {"F003"}


# ── "is this well finished here" ────────────────────────────────────────────

def _covered(root: Path, key: str = KEY) -> bool:
    args = SimpleNamespace(input_dir=str(root / "in"), csv_prefix="measurements", filename_schema=SCHEMA,
                           filename_sep="_")
    return _field_coverage(root / "out", args, key)("A01")


def test_a_well_with_a_failed_field_is_not_finished_here_though_its_images_came_back(tmp_path):
    """With images for every field in the archive — the empty one's too, as a real run leaves — the earlier rule
    calls this well covered. The record does not: F002 never finished."""
    root = _dataset(tmp_path)
    with zipfile.ZipFile(root / "out" / "A01_out.zip", "a") as zf:
        zf.writestr("exp_GFP_A01_F003_0h_labels.tif", b"mask")
    assert _covered(root) is False


def test_a_well_whose_record_covers_every_field_is_finished_here(tmp_path):
    root = _dataset(tmp_path)
    _write_record(done_record_path(root / "out", "A01"), [F1, F2, F3], [F2, F3])
    assert _covered(root) is True
    assert _covered(root, "settings-two") is False, (
        "a record made under other settings was taken: by the earlier rule F003, with no images, is missing")


def test_a_well_with_no_record_is_finished_here_by_the_earlier_rule(tmp_path):
    root = _dataset(tmp_path, record=False)
    assert _covered(root) is False                      # F003 has neither rows nor images
    with zipfile.ZipFile(root / "out" / "A01_out.zip", "a") as zf:
        zf.writestr("exp_GFP_A01_F003_0h_labels.tif", b"mask")
    assert _covered(root) is True                       # images are done, as they were


# ── the Fetcher brings the record back ──────────────────────────────────────

def _pull_of(payload):
    """A stand-in for ``remote_transport.pull_small_file``: writes *payload* to the destination, or fails."""
    asked = []

    def pull(host, remote_path, dest, **kwargs):
        asked.append(remote_path)
        if payload is None:
            return False
        _write_record(dest, *payload)
        return True

    pull.asked = asked
    return pull


def _local(tmp_path) -> Path:
    out = tmp_path / "out"
    _write_record(done_record_path(out, "A01"), [F1, F3], [F3])
    return out


def _groups(out: Path):
    record = parse_done_record(done_record_path(out, "A01").read_text(), KEY)
    return record.groups, record.no_cells


def test_a_whole_fetched_well_takes_the_host_s_record(tmp_path):
    out = _local(tmp_path)
    pull = _pull_of(([F1, F2], []))
    assert pull_done_record(pull, None, "/remote/out", out, "A01", incremental=False) is True
    assert pull.asked == [f"/remote/out/{DONE_DIRNAME}/A01.json"]
    assert _groups(out) == ({F1, F2}, set())
    assert sorted(p.name for p in (out / DONE_DIRNAME).iterdir()) == ["A01.json"], "the staged copy was left behind"


def test_an_incremental_fetch_adds_the_host_s_fields_to_the_local_record(tmp_path):
    out = _local(tmp_path)
    assert pull_done_record(_pull_of(([F2], [])), None, "/remote/out", out, "A01", incremental=True) is True
    assert _groups(out) == ({F1, F2, F3}, {F3})


def test_a_whole_fetch_with_no_record_to_bring_removes_the_local_one(tmp_path):
    """It described the output the fetch just replaced; the well is then read by the earlier rule."""
    out = _local(tmp_path)
    assert pull_done_record(_pull_of(None), None, "/remote/out", out, "A01", incremental=False) is False
    assert not done_record_path(out, "A01").exists()


def test_an_incremental_fetch_with_no_record_to_bring_keeps_the_local_one(tmp_path):
    out = _local(tmp_path)
    assert pull_done_record(_pull_of(None), None, "/remote/out", out, "A01", incremental=True) is True
    assert _groups(out) == ({F1, F3}, {F3})


# ── Validate ────────────────────────────────────────────────────────────────

def _well(root: Path):
    (report,) = vc.build_report(root).wells
    return report


def test_validate_reports_the_failed_field_missing_and_counts_the_empty_one(tmp_path):
    report = _well(_dataset(tmp_path))
    assert report.missing == (F2,)
    assert (report.intent, report.covered_csv, report.covered_any, report.no_cells) == (3, 1, 2, 1)


def test_validate_reads_a_well_with_no_record_as_before(tmp_path):
    report = _well(_dataset(tmp_path, record=False))
    assert report.missing == (F3,)
    assert (report.intent, report.covered_csv, report.covered_any, report.no_cells) == (3, 1, 2, 0)


def test_validate_takes_a_record_made_under_any_settings(tmp_path):
    """It describes the folder; it reuses nothing, so the settings the record was made under do not bar it."""
    assert _well(_dataset(tmp_path, digest="settings-two")).missing == (F2,)


@pytest.mark.parametrize("total, covered, no_cells, expected", [
    (3, 3, 3, True),       # every group measured, none with cells: a run that counts its empty fields
    (3, 0, 0, True),       # the same well as an earlier run recorded it: coverage from the CSV alone
    (3, 3, 1, False),      # one empty field in a well that has cells
    (3, 2, 0, False),
])
def test_a_well_that_segmented_no_cells_anywhere_is_recognised_in_both_records(total, covered, no_cells, expected):
    evidence = vc.WellEvidence(groups_total=total, groups_covered=covered, groups_no_cells=no_cells)
    assert evidence.segmented_zero_cells is expected
