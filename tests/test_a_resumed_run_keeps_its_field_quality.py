"""A resumed run adds to a well's field-quality record; it does not replace it.

A suspended (or partly failed) run leaves a well measured on some of its fields,
and its ``field_quality`` record says so: ``measured`` 1 of ``measured_of`` 3,
the well listed in ``partial_wells``, and Review declines to score the plate.
Resume is an ``--incremental`` run, which re-measures only the fields that are
missing. Before this was fixed its record covered those alone, and it was
written in place of the earlier one:

* locally, ``write_pipeline_info`` rewrites the sidecar at the start of every
  run keeping only the user-side blocks, so the earlier block was gone before
  the run began, and the resumed well's record said 2 of 2;
* remotely, the host's output directory starts empty, and the fetch laid that
  host's record over the local one, well for well — the same 2 of 2.

Either way the well stopped being flagged partial while its block described two
of its three fields: a biased subset the scorer then scored as a whole plate.

The reviewer's scenario throughout: C01 measured on F001 of three fields; the
resumed run measures F002 and F003.
"""

from __future__ import annotations

import json
import logging
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest
from tifffile import imwrite

import process_microscopy as pm
from pipeline.field_quality import (
    FIELD_QUALITY_BLOCK, merge_fetched_field_quality, read_field_quality_block,
)

SCHEMA = ["experiment", "channel", "well", "fov", "timepoint"]
_REAL_GROUP = pm.process_image_group


def _frame(seed):
    r = np.random.default_rng(seed)
    return np.clip(r.normal(400, 30, (48, 48)), 0, 65535).astype(np.uint16)


@pytest.fixture
def stubbed_segmenter(monkeypatch):
    """The loader runs for real; StarDist, image writes and quantification do not."""
    from pipeline.field_quality import FOCUS_COLUMN

    labels = np.zeros((48, 48), np.int32)
    labels[5:15, 5:15] = 1
    monkeypatch.setattr(pm, "_segment_image_group",
                        lambda **k: (labels, None, None, None))
    monkeypatch.setattr(pm, "_write_processed_images", lambda **k: None)

    def _quantify(**k):
        # The CSV's (fov, timepoint) columns are what the incremental done-set
        # is read from, so the stub's rows must carry them as the real ones do.
        meta = pm.parse_filename(Path(k["nuclear_path"]), schema=SCHEMA, sep="_")
        return [{"well": meta["well"], "fov": meta["fov"],
                 "timepoint": meta["timepoint"], "nucleus_id": 1, "area": 100.0,
                 FOCUS_COLUMN: 0.3}]

    monkeypatch.setattr(pm, "_quantify_image_group", _quantify)


def _plate(tmp_path):
    plate = tmp_path / "plate"
    (plate / "in").mkdir(parents=True)
    with zipfile.ZipFile(plate / "in" / "C01.zip", "w") as zf:
        for fov in ("F001", "F002", "F003"):
            for i, channel in enumerate(("DAPI", "GFP")):
                tif = tmp_path / "frame.tif"
                imwrite(str(tif), _frame(seed=i))
                zf.write(tif, f"exp_{channel}_C01_{fov}_0h.tif")
    return plate


def _run_main(monkeypatch, plate, *, incremental, failing=()):
    """``main`` for real, with the well pool run serially in this process.

    The pool pickles its task into worker processes, which would not carry
    this file's stubs; everything on either side of it is the real code.
    """
    def _group(nuclear_path, fluor_paths, **kwargs):
        if any(f"_{fov}_" in Path(nuclear_path).name for fov in failing):
            raise ValueError("the run was suspended before this field")
        return _REAL_GROUP(nuclear_path, fluor_paths, **kwargs)

    def _serial(*, well_zips, output_dir, shared_kwargs_template, nuclear_token,
                fluor_tokens, csv_prefix, schema, sep, incremental,
                incremental_prior_dir, settings_key, force, **_ignored):
        for label, zip_path in well_zips:
            pm.process_well_zip_task(
                label, zip_path, output_dir, shared_kwargs_template,
                nuclear_token, fluor_tokens, csv_prefix, schema, sep,
                incremental, incremental_prior_dir, settings_key, force)
        return []

    monkeypatch.setattr(pm, "process_image_group", _group)
    monkeypatch.setattr(pm, "process_well_zips", _serial)
    monkeypatch.setattr(pm._suspend, "install_handlers", lambda **k: None)
    monkeypatch.setattr(pm._suspend, "watch_parent", lambda *a, **k: None)
    argv = ["process_microscopy.py", "--input_dir", str(plate / "in"),
            "--output_dir", str(plate / "out"), "--nuclear_token", "DAPI",
            "--fluor_tokens", "GFP", "--filename_schema", ":".join(SCHEMA),
            "--workers", "1", "--background", "none",
            "--flatfield", "none", "--no_auto_threshold", "--no_run_check"]
    if incremental:
        argv.append("--incremental")
    monkeypatch.setattr(sys, "argv", argv)
    try:
        pm.main()
    except SystemExit as exc:
        assert not exc.code, f"the pipeline exited {exc.code}"
    return read_field_quality_block(plate / "out" / pm.PIPELINE_INFO_FILENAME)


def _fovs(record):
    return sorted(f["fov"] for f in record["fields"])


def test_a_resumed_local_well_is_measured_across_both_runs(
        tmp_path, monkeypatch, stubbed_segmenter):
    plate = _plate(tmp_path)
    first = _run_main(monkeypatch, plate, incremental=False,
                      failing=("F002", "F003"))
    assert first["partial_wells"] == ["C01"], "the fixture's first run was not partial"

    block = _run_main(monkeypatch, plate, incremental=True)
    rec = block["wells"]["C01"]
    assert _fovs(rec) == ["F001", "F002", "F003"], (
        "the resumed run's record replaced the fields measured before it")
    assert (rec["measured"], rec["measured_of"]) == (3, 3)
    assert block["partial_wells"] == []


def test_a_resumed_local_well_still_short_stays_partial(
        tmp_path, monkeypatch, stubbed_segmenter):
    """The wrong implementation this excludes is the one that clears the flag
    because the resumed run measured everything *it* was given."""
    plate = _plate(tmp_path)
    _run_main(monkeypatch, plate, incremental=False, failing=("F002", "F003"))

    block = _run_main(monkeypatch, plate, incremental=True, failing=("F003",))
    rec = block["wells"]["C01"]
    assert _fovs(rec) == ["F001", "F002"]
    assert (rec["measured"], rec["measured_of"]) == (2, 3)
    assert block["partial_wells"] == ["C01"], (
        "a well measured on two of its three fields was not flagged partial")


def test_a_resumed_well_with_no_earlier_record_counts_the_whole_well(
        tmp_path, monkeypatch, stubbed_segmenter):
    """No earlier block to add to: a plate analysed before field quality was
    recorded, or whose sidecar was rebuilt. The resumed run's record must still
    say the well has three fields, not the two it was given — otherwise two of
    three read as a whole well."""
    plate = _plate(tmp_path)
    _run_main(monkeypatch, plate, incremental=False, failing=("F002", "F003"))
    info = plate / "out" / pm.PIPELINE_INFO_FILENAME
    sidecar = json.loads(info.read_text())
    del sidecar[FIELD_QUALITY_BLOCK]
    info.write_text(json.dumps(sidecar))

    block = _run_main(monkeypatch, plate, incremental=True)
    rec = block["wells"]["C01"]
    assert (rec["measured"], rec["measured_of"]) == (2, 3)
    assert block["partial_wells"] == ["C01"]


# ── remote: the fetch lays the host's sidecar over the local one ────────────

def _field(fov):
    return {"fov": fov, "timepoint": "0h", "focus": 0.3}


def _info_with(path, wells):
    path.write_text(json.dumps({"schema": ":".join(SCHEMA), FIELD_QUALITY_BLOCK: {
        "wells": wells, "partial_wells": [], "measured": 0, "measured_of": None}}))


def test_a_resumed_remote_well_is_added_to_its_earlier_record(tmp_path):
    prior = {"wells": {
        "C01": {"well": "C01", "fields": [_field("F001")], "measured": 1, "measured_of": 3},
        "B02": {"well": "B02", "fields": [_field("F001")], "measured": 1, "measured_of": 1},
    }}
    info = tmp_path / "pipeline_info.json"
    # The host was sent only F002 and F003, so its well has two fields of two.
    _info_with(info, {"C01": {"well": "C01", "fields": [_field("F002"), _field("F003")],
                              "measured": 2, "measured_of": 2}})

    assert merge_fetched_field_quality(info, prior, incremental=True)
    block = read_field_quality_block(info)
    rec = block["wells"]["C01"]
    assert _fovs(rec) == ["F001", "F002", "F003"]
    assert (rec["measured"], rec["measured_of"]) == (3, 3)
    assert set(block["wells"]) == {"C01", "B02"}
    assert block["partial_wells"] == []


def test_a_resumed_remote_well_still_short_stays_partial(tmp_path):
    prior = {"wells": {"C01": {"well": "C01", "fields": [_field("F001")],
                               "measured": 1, "measured_of": 3}}}
    info = tmp_path / "pipeline_info.json"
    _info_with(info, {"C01": {"well": "C01", "fields": [_field("F002")],
                              "measured": 1, "measured_of": 2}})

    merge_fetched_field_quality(info, prior, incremental=True)
    block = read_field_quality_block(info)
    assert (block["wells"]["C01"]["measured"], block["wells"]["C01"]["measured_of"]) == (2, 3)
    assert block["partial_wells"] == ["C01"]


def test_a_full_remote_run_still_replaces_the_record(tmp_path):
    """Not incremental: the host measured the whole well again, so its record
    is the well's — merging it with the old one would double-count nothing but
    would keep fields from a run whose settings may differ."""
    prior = {"wells": {"C01": {"well": "C01", "fields": [_field("F009")],
                               "measured": 1, "measured_of": 3}}}
    info = tmp_path / "pipeline_info.json"
    _info_with(info, {"C01": {"well": "C01", "fields": [_field("F001")],
                              "measured": 1, "measured_of": 1}})
    merge_fetched_field_quality(info, prior)
    assert _fovs(read_field_quality_block(info)["wells"]["C01"]) == ["F001"]


def test_the_remote_fetch_tells_the_merge_the_run_was_incremental():
    """The flag is the whole fix on the remote side, so the call must pass it."""
    import inspect

    from services import remote_runner

    src = inspect.getsource(remote_runner)
    assert "merge_fetched_field_quality(info_path, prior_field_quality, " \
           "incremental=run.incremental)" in src


def test_logging_names_the_merged_plate(tmp_path, caplog):
    """The finalize line summarises the merged block, not the resumed handful:
    a resumed well that is now whole must not be announced as partial."""
    from pipeline.field_quality import finalize_field_quality, write_well_field_quality
    from pipeline.io import _safe_atomic_text_write

    out = tmp_path / "out"
    out.mkdir()
    write_well_field_quality(out, "C01", fields=[_field("F002"), _field("F003")],
                             measured_of=3)
    info = out / "pipeline_info.json"
    info.write_text("{}")
    prior = {"wells": {"C01": {"well": "C01", "fields": [_field("F001")],
                               "measured": 1, "measured_of": 3}}}
    with caplog.at_level(logging.INFO):
        finalize_field_quality(out, info, atomic_writer=_safe_atomic_text_write,
                               log=logging.getLogger("t"), prior_block=prior)
    assert "PARTIAL" not in caplog.text
    assert "3 field(s) measured across 1 well(s)" in caplog.text
