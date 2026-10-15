"""``--only_wells``: a run over part of a plate leaves the rest of it alone.

A subset run writes into a directory that already holds the rest of the plate,
and several of the records a run leaves behind are rebuilt at its end from "the
wells this run processed". Written naively, a subset run would replace the
whole plate's ``run_report`` and ``field_quality`` blocks with the subset's, and
every other well would silently lose the record that says whether its output is
whole — degraded output indistinguishable from good (CLAUDE.md §7).

The scenario throughout: B02 is analysed whole; C01 comes out partial (F002 and
F003 lost); a second run names only C01.

``main`` runs for real, with the well pool run serially in this process and the
segmenter stubbed — the harness ``test_a_resumed_run_keeps_its_field_quality``
uses, for the same reason.
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest
from tifffile import imwrite

import process_microscopy as pm
from pipeline.field_quality import FIELD_QUALITY_BLOCK
from pipeline.field_quality_record import finalize_field_quality
from pipeline.run_report import finalize_run_report, write_well_report
from pipeline.well_skip import scope_to_only_wells, select_only_wells
from pipeline import well_skip
import pipeline.suspend as _suspend

SCHEMA = ["experiment", "channel", "well", "fov", "timepoint"]
_REAL_GROUP = pm.process_image_group


def _frame(seed):
    r = np.random.default_rng(seed)
    return np.clip(r.normal(400, 30, (48, 48)), 0, 65535).astype(np.uint16)


@pytest.fixture
def stubbed_segmenter(monkeypatch):
    from pipeline.field_quality import FOCUS_COLUMN

    labels = np.zeros((48, 48), np.int32)
    labels[5:15, 5:15] = 1
    monkeypatch.setattr(pm, "_segment_image_group",
                        lambda **k: (labels, None, None, None))
    monkeypatch.setattr(pm, "_write_processed_images", lambda **k: None)

    def _quantify(**k):
        meta = pm.parse_filename(Path(k["nuclear_path"]), schema=SCHEMA, sep="_")
        return [{"well": meta["well"], "fov": meta["fov"],
                 "timepoint": meta["timepoint"], "nucleus_id": 1, "area": 100.0,
                 FOCUS_COLUMN: 0.3}]

    monkeypatch.setattr(pm, "_quantify_image_group", _quantify)


def _plate(tmp_path, wells=("B02", "C01")):
    plate = tmp_path / "plate"
    (plate / "in").mkdir(parents=True)
    tif = tmp_path / "frame.tif"
    for well in wells:
        with zipfile.ZipFile(plate / "in" / f"{well}.zip", "w") as zf:
            for fov in ("F001", "F002", "F003"):
                for i, channel in enumerate(("DAPI", "GFP")):
                    imwrite(str(tif), _frame(seed=i))
                    zf.write(tif, f"exp_{channel}_{well}_{fov}_0h.tif")
    return plate


def _run_main(monkeypatch, plate, *extra, failing=(), ran=None, check=True):
    """``main`` for real; returns the sidecar. *ran* collects the wells the
    pool was handed, so a test can say which wells the run processed."""
    def _group(nuclear_path, fluor_paths, **kwargs):
        name = Path(nuclear_path).name
        if any(tag in name for tag in failing):
            raise ValueError("lost to a write error")
        return _REAL_GROUP(nuclear_path, fluor_paths, **kwargs)

    def _serial(*, well_zips, output_dir, shared_kwargs_template, nuclear_token,
                fluor_tokens, csv_prefix, schema, sep, incremental,
                incremental_prior_dir, settings_key, force, force_wells=None,
                **_ignored):
        forced = well_skip._canonical_force_wells(force_wells)
        for label, zip_path in well_zips:
            if ran is not None:
                ran.append(label)
            pm.process_well_zip_task(
                label, zip_path, output_dir, shared_kwargs_template,
                nuclear_token, fluor_tokens, csv_prefix, schema, sep,
                well_skip.incremental_for_well(incremental, label, forced),
                incremental_prior_dir, settings_key, force or label in forced)
        return []

    monkeypatch.setattr(pm, "process_image_group", _group)
    monkeypatch.setattr(pm, "process_well_zips", _serial)
    monkeypatch.setattr(_suspend, "install_handlers", lambda **k: None)
    monkeypatch.setattr(_suspend, "watch_parent", lambda *a, **k: None)
    argv = ["process_microscopy.py", "--input_dir", str(plate / "in"),
            "--output_dir", str(plate / "out"), "--nuclear_token", "DAPI",
            "--fluor_tokens", "GFP", "--filename_schema", ":".join(SCHEMA),
            "--workers", "1", "--background", "none",
            "--flatfield", "none", "--no_auto_threshold", *extra]
    monkeypatch.setattr(sys, "argv", argv)
    try:
        pm.main()
    except SystemExit as exc:
        if not check:
            raise
        assert not exc.code, f"the pipeline exited {exc.code}"
    return json.loads((plate / "out" / pm.PIPELINE_INFO_FILENAME).read_text())


def _first_run(monkeypatch, plate):
    """B02 whole, C01 missing F002 and F003."""
    info = _run_main(monkeypatch, plate,
                     failing=("_C01_F002_", "_C01_F003_"))
    report = info["run_report"]["wells"]
    assert (report["C01"]["groups_covered"], report["C01"]["groups_total"]) == (1, 3)
    assert (report["B02"]["groups_covered"], report["B02"]["groups_total"]) == (3, 3)
    return info


# ── the run itself ───────────────────────────────────────────────────────────

def test_only_the_named_well_is_processed_and_the_rest_is_left_alone(
        tmp_path, monkeypatch, stubbed_segmenter):
    plate = _plate(tmp_path)
    _first_run(monkeypatch, plate)
    # A finished well's CSV is stored gzipped; "left alone" is asserted on the
    # file that is actually there, and on it being the gzipped one.
    b02_csv = plate / "out" / "measurements_B02.csv.gz"
    assert b02_csv.is_file()
    before = (b02_csv.read_bytes(), b02_csv.stat().st_mtime_ns)

    ran: list[str] = []
    info = _run_main(monkeypatch, plate, "--only_wells", "C1", "--incremental",
                     ran=ran)

    assert ran == ["C01"], f"the run processed {ran}, not only C01"
    assert (b02_csv.read_bytes(), b02_csv.stat().st_mtime_ns) == before, (
        "a well outside --only_wells had its output rewritten")
    report = info["run_report"]
    assert set(report["wells"]) == {"B02", "C01"}, (
        "the subset run's run_report replaced the plate's with its own wells: "
        f"{sorted(report['wells'])}")
    assert (report["wells"]["C01"]["groups_covered"],
            report["wells"]["C01"]["groups_total"]) == (3, 3)
    assert report["incomplete_wells"] == []
    assert (report["groups_covered"], report["groups_total"]) == (6, 6)

    quality = info[FIELD_QUALITY_BLOCK]["wells"]
    assert set(quality) == {"B02", "C01"}, (
        "the subset run's field_quality block dropped the wells it did not run")
    assert sorted(f["fov"] for f in quality["C01"]["fields"]) == ["F001", "F002", "F003"]


def test_the_run_check_still_describes_every_well_in_the_input(
        tmp_path, monkeypatch, stubbed_segmenter):
    """The end-of-run completeness check covers the directory, not the run: a
    subset run leaves a plate behind, and the check is how a reader learns what
    that plate holds. The wrong implementation is one that hands the check the
    narrowed well list."""
    plate = _plate(tmp_path)
    before = _first_run(monkeypatch, plate)["run_check"]["wells"]["B02"]
    info = _run_main(monkeypatch, plate, "--only_wells", "C01", "--incremental")
    # B02 is listed either way — the check unions the input with whatever the
    # output holds — so its presence proves nothing. Its *record* does: handed
    # the narrowed list, the check sees B02's output with no input behind it.
    assert info["run_check"]["wells"]["B02"] == before, (
        "the subset run's completeness check described B02 differently from "
        f"the whole-plate run: {info['run_check']['wells']['B02']} != {before}")


def test_a_named_well_missing_from_the_input_refuses_the_run(
        tmp_path, monkeypatch, stubbed_segmenter):
    plate = _plate(tmp_path)
    ran: list[str] = []
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, plate, "--only_wells", "C01", "H12", ran=ran,
                  check=False)
    assert exc.value.code == 2
    assert ran == [], "a refused run processed wells before refusing"


def test_without_the_flag_every_well_runs(tmp_path, monkeypatch, stubbed_segmenter):
    plate = _plate(tmp_path)
    ran: list[str] = []
    _run_main(monkeypatch, plate, ran=ran)
    assert ran == ["B02", "C01"]


# ── the selection ────────────────────────────────────────────────────────────

def test_selection_matches_well_labels_canonically():
    items = [("A01", Path("A01.zip")), ("C08", Path("C08.zip")), ("H12", Path("H12.zip"))]
    kept, missing = select_only_wells(items, ["c8", "A1", "B05"])
    assert [w for w, _p in kept] == ["A01", "C08"]
    assert missing == ["B05"]
    assert select_only_wells(items, None) == (items, [])


def test_folder_mode_is_narrowed_and_refused_the_same_way():
    folders = [("A01", Path("A01")), ("B02", Path("B02"))]
    refusals: list[str] = []

    class _Log:
        def info(self, *a):
            pass

    zips, kept = scope_to_only_wells([], folders, ["B2"], refuse=refusals.append,
                                     log=_Log())
    assert (zips, kept, refusals) == ([], [("B02", Path("B02"))], [])
    scope_to_only_wells([], folders, ["B2", "D04"], refuse=refusals.append, log=_Log())
    assert refusals and "D04" in refusals[0]


# ── the records carried across the sidecar rewrite ───────────────────────────

class _Log:
    def __init__(self):
        self.lines = []

    def info(self, *a):
        self.lines.append(a)

    warning = info


def _atomic(path):
    import contextlib

    @contextlib.contextmanager
    def _w():
        with open(path, "w") as fh:
            yield fh
    return _w()


def test_a_well_the_subset_ran_does_not_inherit_its_previous_record(tmp_path):
    """C01 ran and died before writing a record. It must end with no record,
    not with the previous run's — a stale "complete" is worse than a gap."""
    info = tmp_path / "pipeline_info.json"
    info.write_text("{}")
    prior = {"wells": {
        "B02": {"well": "B02", "groups_total": 3, "groups_covered": 3},
        "C01": {"well": "C01", "groups_total": 3, "groups_covered": 3},
    }}
    finalize_run_report(tmp_path, info, atomic_writer=_atomic, log=_Log(),
                        prior_block=prior, ran_wells=["C01"])
    wells = json.loads(info.read_text())["run_report"]["wells"]
    assert set(wells) == {"B02"}


def test_the_subset_s_own_record_wins_over_the_prior_one(tmp_path):
    info = tmp_path / "pipeline_info.json"
    info.write_text("{}")
    write_well_report(tmp_path, "C01", groups_total=3, groups_covered=2)
    prior = {"wells": {
        "B02": {"well": "B02", "groups_total": 3, "groups_covered": 3},
        "C01": {"well": "C01", "groups_total": 3, "groups_covered": 3},
    }}
    finalize_run_report(tmp_path, info, atomic_writer=_atomic, log=_Log(),
                        prior_block=prior, ran_wells=["C01"])
    block = json.loads(info.read_text())["run_report"]
    assert block["wells"]["C01"]["groups_covered"] == 2
    assert block["incomplete_wells"] == ["C01"]
    assert block["wells"]["B02"]["groups_covered"] == 3


def test_a_non_incremental_subset_replaces_its_wells_quality_records(tmp_path):
    """Re-measured whole, so C01's earlier fields must not be added to the new
    ones — and with no new record at all, C01 has none."""
    info = tmp_path / "pipeline_info.json"
    info.write_text("{}")
    prior = {"wells": {
        "B02": {"well": "B02", "fields": [{"fov": "F001"}], "measured": 1, "measured_of": 1},
        "C01": {"well": "C01", "fields": [{"fov": "F009"}], "measured": 1, "measured_of": 1},
    }}
    finalize_field_quality(tmp_path, info, atomic_writer=_atomic, log=_Log(),
                           prior_block=prior, incremental=False,
                           replaced_wells=["C01"])
    wells = json.loads(info.read_text())[FIELD_QUALITY_BLOCK]["wells"]
    assert set(wells) == {"B02"}


def test_a_forced_subset_without_incremental_keeps_the_other_wells_records(
        tmp_path, monkeypatch, stubbed_segmenter):
    """Not incremental, so nothing but ``--only_wells`` is asking for the prior
    blocks to be carried: the case where forgetting to read them would pass
    every incremental test above."""
    plate = _plate(tmp_path)
    _first_run(monkeypatch, plate)
    ran: list[str] = []
    info = _run_main(monkeypatch, plate, "--only_wells", "C01",
                     "--force_wells", "C01", ran=ran)
    assert ran == ["C01"]
    assert set(info["run_report"]["wells"]) == {"B02", "C01"}
    assert info["run_report"]["wells"]["C01"]["groups_covered"] == 3
    quality = info[FIELD_QUALITY_BLOCK]["wells"]
    assert set(quality) == {"B02", "C01"}
    assert sorted(f["fov"] for f in quality["C01"]["fields"]) == ["F001", "F002", "F003"]


def test_a_blank_outside_the_subset_still_corrects_it(
        tmp_path, monkeypatch, stubbed_segmenter):
    """The empty-well blank is a reference the correction is built from, not a
    well whose output is wanted: naming only C01 must not stop B02 correcting
    it, nor start processing B02 as a well."""
    plate = _plate(tmp_path)
    built: list[object] = []
    from pipeline.empty_well import prepare_models as real

    def _spy(shared_kwargs, **kw):
        built.append(kw.get("groups"))
        return real(shared_kwargs, **kw)

    monkeypatch.setattr(pm, "prepare_models", _spy)
    ran: list[str] = []
    argv = ["--only_wells", "C01", "--background", "empty_well",
            "--background_well", "B02"]
    info = _run_main(monkeypatch, plate, *argv, ran=ran)
    assert ran == ["C01"]
    assert built, "no background model was built for the subset"
    assert info["qc_options"]["background"] == "empty_well"
