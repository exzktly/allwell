"""A FOV-group is done when it was processed to the end — whether or not it held cells.

The full-app review (pull request #1343), row C6. Two defects with one cause:
nothing recorded which groups *finished*.

* A field that legitimately holds no cells writes no CSV row, and coverage was
  read back from the CSV alone. A three-field well with one empty field
  recorded 2 of 3 groups, was reported INCOMPLETE, was never sealed, and was
  run again whole by every plain re-run — for ever, since the field stays
  empty.
* The incremental done-set covered for that by also counting the output
  archive's image members. But a group's images are written *before* it is
  quantified, so a group whose quantification raised left members and no rows,
  and every later ``--incremental`` skipped it: 0 rows added once the cause
  was cured.

``pipeline/done_groups.py`` records, per well, the groups that returned from
the per-group pipeline and which of them held no cells. These tests drive the
real ``process_well_zip_task`` — extraction, correction, image writes,
quantification, the CSV, the archive, the records and the seal — with only the
segmenter stubbed, over an archive carrying what a real one carries
(``.DS_Store``, an AppleDouble twin under ``__MACOSX/``, a directory entry).
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import numpy as np
import pytest
import tifffile

import process_microscopy as pm
from pipeline import run_report
from pipeline.completeness import coverage_for_well, well_record
from pipeline.done_groups import (
    DONE_DIRNAME, done_record_path, merge_fetched_done_record, parse_done_record, read_done_record,
)
from pipeline.incremental_keys import incremental_done_fov_keys, read_done_fov_keys
from pipeline.well_skip import finished_output

SCHEMA = ["experiment", "channel", "well", "fov", "timepoint"]
KEY = "settings-one"
FIELDS = ("f1", "f2", "f3")
#: The field with no cells. Its nuclear image is the bright one, which is how the stub segmenter knows it: the
#: segmenter is handed pixels, not a path.
EMPTY = "f3"
ALL = {(f, "t0") for f in FIELDS}


def _plate(tmp_path) -> tuple[Path, Path]:
    """``in/A01.zip`` with three fields of two channels, and the debris a real archive carries."""
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    rng = np.random.default_rng(0)
    with zipfile.ZipFile(inp / "A01.zip", "w") as zf:
        zf.writestr(zipfile.ZipInfo("thumbs/"), b"")
        zf.writestr(".DS_Store", b"\x00\x01junk")
        for fov in FIELDS:
            for channel in ("DAPI", "GFP"):
                level = 1000 if (fov == EMPTY and channel == "DAPI") else 100
                tif = tmp_path / "frame.tif"
                tifffile.imwrite(tif, (level + rng.integers(0, 5, (96, 96))).astype(np.uint16))
                zf.write(tif, f"exp_{channel}_A01_{fov}_t0.tif")
                zf.writestr(f"__MACOSX/._exp_{channel}_A01_{fov}_t0.tif", b"appledouble")
    return inp, out


class _Segmenter:
    """Two cells in every field but the empty one; counts how many fields it was asked to segment."""

    def __init__(self):
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        labels = np.zeros((96, 96), np.int32)
        if float(np.median(kwargs["nir_corr"])) < 500:
            labels[10:30, 10:30] = 1
            labels[50:70, 50:70] = 2
        return labels, None, None, None, None


@pytest.fixture
def segmenter(monkeypatch):
    stub = _Segmenter()
    monkeypatch.setattr(pm, "_segment_image_group", stub)
    return stub


def _run(inp, out, *, incremental=False, settings_key=KEY, prior=None):
    kw = dict(nuclear_token="DAPI", fluor_tokens=["GFP"], tophat_nir=False, tophat_radius_nir=50,
              tophat_fluor=[False], tophat_radius_fluor=[50], schema=SCHEMA, sep="_",
              segmentation_method="classical_threshold", background_method="none", flatfield_mode="none")
    return pm.process_well_zip_task("A01", inp / "A01.zip", out, kw, "DAPI", ["GFP"], "results", SCHEMA, "_",
                                    incremental, prior, settings_key)


def _record(out) -> dict:
    return run_report.recorded_well_report(out, "A01")


def _sealed(out) -> bool:
    return run_report.complete_seal_path(out, "A01").exists()


def _csv_keys(out) -> set:
    return read_done_fov_keys(out / "results_A01.csv")


def _fail_quantification_of(monkeypatch, fov: str) -> None:
    """Make *fov*'s quantification raise, after its images have been written (the order the real group runs in)."""
    real = pm._quantify_image_group

    def quantify(**kwargs):
        if f"_{fov}_" in Path(kwargs["nuclear_path"]).name:
            raise RuntimeError(f"quantification of {fov} failed")
        return real(**kwargs)

    monkeypatch.setattr(pm, "_quantify_image_group", quantify)


# ── (a) one empty field: whole, sealed, skipped ─────────────────────────────

def test_a_well_with_one_empty_field_is_whole_and_sealed(tmp_path, segmenter):
    inp, out = _plate(tmp_path)
    records, failed = _run(inp, out)

    assert (len(records), failed) == (4, []), "the fixture: two cells in each of two fields, none in the third"
    assert _csv_keys(out) == ALL - {(EMPTY, "t0")}
    rec = _record(out)
    assert (rec["groups_total"], rec["groups_covered"]) == (3, 3)
    assert run_report.well_is_whole(rec) is True
    assert _sealed(out) is True
    assert run_report.summarize(run_report.block_of({"A01": rec})) == ""


def test_a_plain_re_run_skips_the_well(tmp_path, segmenter):
    """``finished_output`` is the predicate the well pool skips on (``process_well_zips``'s ``output_exists``)."""
    inp, out = _plate(tmp_path)
    _run(inp, out)
    assert finished_output(out, "A01", "results", KEY) is True


def test_an_incremental_re_run_does_nothing_and_leaves_the_well_sealed(tmp_path, segmenter):
    inp, out = _plate(tmp_path)
    _run(inp, out)
    before = (segmenter.calls, _record(out), done_record_path(out, "A01").read_text())

    assert _run(inp, out, incremental=True) == ([], [])

    assert (segmenter.calls, _record(out), done_record_path(out, "A01").read_text()) == before
    assert _sealed(out) is True


# ── (b) the empty group is counted and named ────────────────────────────────

def test_the_empty_group_is_counted_and_named_in_every_record(tmp_path, segmenter):
    inp, out = _plate(tmp_path)
    _run(inp, out)

    rec = _record(out)
    assert rec["groups_no_cells"] == 1
    assert rec["no_cells"] == [f"{EMPTY}/t0"]
    assert rec["failed"] == []

    done = json.loads(done_record_path(out, "A01").read_text())
    assert done == {"well": "A01", "format": 1, "digest": KEY,
                    "groups": [["f1", "t0"], ["f2", "t0"], ["f3", "t0"]],
                    "no_cells": [["f3", "t0"]]}

    block = run_report.block_of({"A01": rec})
    assert (block["groups_total"], block["groups_covered"], block["groups_no_cells"]) == (3, 3, 1)
    assert block["incomplete_wells"] == []
    assert run_report.no_cells_note(block) == (
        "NO CELLS: 1 FOV-group(s) in 1 well(s) were processed to the end and held no cells (A01 (1)). "
        "They are counted as covered; pipeline_info.json names them per well.")

    with zipfile.ZipFile(inp / "A01.zip") as zf:
        members = [Path(n).name for n in zf.namelist() if not n.endswith("/")]
    cov = coverage_for_well(
        "A01", in_members=members, csv_keys=_csv_keys(out), csv_state="ok", out_zip=out / "A01_out.zip",
        out_folder=None, schema=SCHEMA, sep="_", fov_field_present=True, done_record=read_done_record(out, "A01"))
    assert (cov.intent, cov.covered_csv, cov.covered_any, cov.no_cells, cov.missing) == (3, 2, 3, 1, ())
    assert well_record(cov)["no_cells"] == 1


def test_a_well_of_only_empty_fields_is_covered_and_says_so(tmp_path, monkeypatch, segmenter):
    """Every field empty: no CSV at all, three groups covered, all three named as holding no cells."""
    inp, out = _plate(tmp_path)
    monkeypatch.setattr(pm, "_segment_image_group", lambda **k: (np.zeros((96, 96), np.int32), None, None, None, None))
    records, failed = _run(inp, out)

    assert (records, failed) == ([], [])
    rec = _record(out)
    assert (rec["groups_total"], rec["groups_covered"], rec["groups_no_cells"]) == (3, 3, 3)
    assert rec["no_cells"] == ["f1/t0", "f2/t0", "f3/t0"]


# ── (c) a group whose quantification failed is not done ─────────────────────

def test_a_group_that_failed_after_its_images_were_written_is_not_done(tmp_path, monkeypatch, segmenter):
    inp, out = _plate(tmp_path)
    _fail_quantification_of(monkeypatch, "f2")
    records, failed = _run(inp, out)

    assert (len(records), failed) == (2, ["exp_DAPI_A01_f2_t0.tif"])
    with zipfile.ZipFile(out / "A01_out.zip") as zf:
        assert any("_f2_" in n for n in zf.namelist()), "the fixture: the failed group's images are in the archive"
    rec = _record(out)
    assert (rec["groups_total"], rec["groups_covered"], rec["groups_no_cells"]) == (3, 2, 1)
    assert rec["failed"] == ["exp_DAPI_A01_f2_t0.tif"]
    assert run_report.well_is_whole(rec) is False
    assert _sealed(out) is False
    assert read_done_record(out, "A01", KEY).groups == {("f1", "t0"), ("f3", "t0")}
    keys, _from_csv = incremental_done_fov_keys(out, "A01", csv_prefix="results", safe_well="A01",
                                                schema=SCHEMA, sep="_", settings_key=KEY)
    assert keys == {("f1", "t0"), ("f3", "t0")}


def test_an_incremental_run_after_the_cause_is_cured_processes_the_failed_group(tmp_path, monkeypatch, segmenter):
    inp, out = _plate(tmp_path)
    with monkeypatch.context() as broken:
        _fail_quantification_of(broken, "f2")
        _run(inp, out)
    calls = segmenter.calls

    records, failed = _run(inp, out, incremental=True)

    assert (len(records), failed) == (2, [])
    assert {(r["fov"], r["timepoint"]) for r in records} == {("f2", "t0")}
    assert segmenter.calls == calls + 1, "only the failed group is processed again"
    assert _csv_keys(out) == {("f1", "t0"), ("f2", "t0")}
    rec = _record(out)
    assert (rec["groups_total"], rec["groups_covered"], rec["groups_no_cells"], rec["failed"]) == (3, 3, 1, [])
    assert _sealed(out) is True
    assert read_done_record(out, "A01", KEY).groups == ALL


def test_a_whole_well_re_run_starts_the_record_again(tmp_path, monkeypatch, segmenter):
    """A group done in an earlier run and failed in this whole-well one must not stay done on the old record."""
    inp, out = _plate(tmp_path)
    _run(inp, out)
    assert read_done_record(out, "A01", KEY).groups == ALL

    _fail_quantification_of(monkeypatch, "f1")
    _run(inp, out)

    assert read_done_record(out, "A01", KEY).groups == {("f2", "t0"), ("f3", "t0")}
    assert _record(out)["groups_covered"] == 2
    assert _sealed(out) is False


def test_a_whole_well_rewrite_that_dies_leaves_no_record_behind(tmp_path, segmenter):
    """The record is withdrawn before the rewrite, as the seal is: one left behind by a run that died part way
    would go on vouching for the earlier output's groups. The rewrite dies for real here — the well's CSV cannot
    be written, because a folder is in its place."""
    inp, out = _plate(tmp_path)
    _run(inp, out)
    assert read_done_record(out, "A01", KEY).groups == ALL and _sealed(out)
    (out / "results_A01.csv").mkdir()
    (out / "results_A01.csv" / "in-the-way").write_text("x")

    with pytest.raises(OSError):
        _run(inp, out)

    assert done_record_path(out, "A01").exists() is False
    assert _sealed(out) is False


# ── (d) an output with no record behaves as before ──────────────────────────

def _as_written_before_the_record(out) -> None:
    """Make *out* what an earlier build left for this well: no record of finished groups, coverage from the CSV
    alone (2 of 3), and so no seal."""
    shutil.rmtree(out / DONE_DIRNAME)
    run_report.write_well_report(out, "A01", groups_total=3, groups_covered=2)
    assert not _sealed(out)


def test_an_output_with_no_record_is_read_as_before(tmp_path, segmenter):
    inp, out = _plate(tmp_path)
    _run(inp, out)
    _as_written_before_the_record(out)
    calls = segmenter.calls

    assert read_done_record(out, "A01", KEY) is None
    # The earlier rule: the CSV's two groups, and the third from the archive's image members.
    keys, from_csv = incremental_done_fov_keys(out, "A01", csv_prefix="results", safe_well="A01",
                                               schema=SCHEMA, sep="_", settings_key=KEY)
    assert (keys, from_csv) == (ALL, True)

    assert _run(inp, out, incremental=True) == ([], [])
    assert segmenter.calls == calls, "a well with no record was treated as having unfinished groups"
    rec = _record(out)
    assert (rec["groups_total"], rec["groups_covered"]) == (3, 2), "an up-to-date well's record is left as it was"
    assert "groups_no_cells" not in rec
    assert not (out / DONE_DIRNAME).exists(), "an incremental run that did nothing wrote a record"

    with zipfile.ZipFile(inp / "A01.zip") as zf:
        members = [Path(n).name for n in zf.namelist() if not n.endswith("/")]
    cov = coverage_for_well(
        "A01", in_members=members, csv_keys=_csv_keys(out), csv_state="ok", out_zip=out / "A01_out.zip",
        out_folder=None, schema=SCHEMA, sep="_", fov_field_present=True, done_record=read_done_record(out, "A01"))
    assert (cov.covered_csv, cov.covered_any, cov.no_cells, cov.missing) == (2, 3, 0, ())


def test_a_record_of_another_format_is_no_record(tmp_path, segmenter):
    inp, out = _plate(tmp_path)
    _run(inp, out)
    path = done_record_path(out, "A01")
    payload = json.loads(path.read_text())
    assert parse_done_record(json.dumps(payload), KEY).groups == ALL
    assert parse_done_record(json.dumps({**payload, "format": 2}), KEY) is None
    assert parse_done_record(json.dumps({**payload, "groups": "f1"}), KEY) is None
    assert parse_done_record("{", KEY) is None


# ── (e) a record made under other settings is not reused ────────────────────

def _prior_with_csv_and_record_only(tmp_path, out) -> Path:
    """A prior directory holding the well's CSV and its record and nothing else: no archive whose image members
    the earlier rule could fall back on, and no settings marker, so the well itself is taken as it is."""
    prior = tmp_path / "prior"
    (prior / DONE_DIRNAME).mkdir(parents=True)
    for path in out.glob("results_A01.csv*"):
        shutil.copy(path, prior / path.name)
    shutil.copy(done_record_path(out, "A01"), done_record_path(prior, "A01"))
    return prior


@pytest.mark.parametrize("key, expected", [
    (KEY, ALL),
    ("settings-two", ALL - {(EMPTY, "t0")}),
    ("", ALL - {(EMPTY, "t0")}),
])
def test_a_record_is_taken_only_under_the_settings_it_was_made_with(tmp_path, segmenter, key, expected):
    inp, out = _plate(tmp_path)
    _run(inp, out)
    prior = _prior_with_csv_and_record_only(tmp_path, out)

    keys, _from_csv = incremental_done_fov_keys(prior, "A01", csv_prefix="results", safe_well="A01",
                                                schema=SCHEMA, sep="_", settings_key=key)
    assert keys == expected


def test_a_run_under_other_settings_processes_the_empty_field_again(tmp_path, segmenter):
    inp, out = _plate(tmp_path)
    _run(inp, out)
    prior = _prior_with_csv_and_record_only(tmp_path, out)
    calls = segmenter.calls

    assert _run(inp, out, incremental=True, prior=prior) == ([], [])
    assert segmenter.calls == calls, "under its own settings the record makes the empty field done"

    _run(inp, out, incremental=True, prior=prior, settings_key="settings-two")
    assert segmenter.calls == calls + 1, "a record made under other settings made the empty field look done"
    assert json.loads(done_record_path(out, "A01").read_text())["digest"] == "settings-two"
    assert read_done_record(out, "A01", KEY) is None


def test_a_record_made_with_no_settings_key_answers_only_a_reader_with_none(tmp_path, segmenter):
    inp, out = _plate(tmp_path)
    _run(inp, out, settings_key="")
    assert read_done_record(out, "A01", "").groups == ALL
    assert read_done_record(out, "A01", KEY) is None
    assert read_done_record(out, "A01", None).groups == ALL, "a reader that reuses nothing takes any record"


# ── a remote incremental fetch merges the record it brings back ─────────────

def _write(path: Path, groups, no_cells, digest=KEY) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"well": "A01", "format": 1, "digest": digest,
                                "groups": [list(k) for k in groups], "no_cells": [list(k) for k in no_cells]}))


def test_an_incremental_fetch_adds_to_the_local_record(tmp_path):
    dest, fetched = tmp_path / "out" / DONE_DIRNAME / "A01.json", tmp_path / "fetched.json"
    _write(dest, [("f1", "t0"), ("f3", "t0")], [("f3", "t0")])
    _write(fetched, [("f4", "t0"), ("f5", "t0")], [("f5", "t0")])

    assert merge_fetched_done_record(fetched, dest, incremental=True) is True
    merged = parse_done_record(dest.read_text(), KEY)
    assert merged.groups == {("f1", "t0"), ("f3", "t0"), ("f4", "t0"), ("f5", "t0")}
    assert merged.no_cells == {("f3", "t0"), ("f5", "t0")}


def test_a_whole_fetch_and_one_under_other_settings_replace_the_local_record(tmp_path):
    dest, fetched = tmp_path / "out" / DONE_DIRNAME / "A01.json", tmp_path / "fetched.json"
    _write(dest, [("f1", "t0"), ("f3", "t0")], [("f3", "t0")])
    _write(fetched, [("f4", "t0")], [])
    merge_fetched_done_record(fetched, dest, incremental=False)
    assert parse_done_record(dest.read_text(), KEY).groups == {("f4", "t0")}

    _write(dest, [("f1", "t0"), ("f3", "t0")], [("f3", "t0")])
    _write(fetched, [("f4", "t0")], [], digest="settings-two")
    merge_fetched_done_record(fetched, dest, incremental=True)
    assert parse_done_record(dest.read_text(), "settings-two").groups == {("f4", "t0")}
    assert parse_done_record(dest.read_text(), KEY) is None
