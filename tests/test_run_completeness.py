"""A run that loses FOV-groups must not look like a run that didn't.

The failure this closes: a well whose FOV-groups partly failed still writes its
per-well CSV and ``<well>_out.zip`` from the groups that survived. On disk that
output is shaped exactly like a clean well's, and nothing recorded how many
FOV-groups the well's *input* held — so no consumer, least of all the viewer,
could tell a six-field well from the same well with three fields lost to write
errors. Every per-field statistic then quietly described whatever fields made it
through, and the first visible symptom was an error bar that wasn't there.

Three defences, one per layer:

* the scratch volume is checked *before* the run, because it is usually not the
  volume holding the dataset — a dataset disk with terabytes free says nothing
  about the disk the run actually stages its working set on;
* a well stops attempting FOV-groups once the storage has failed repeatedly,
  instead of discovering it once per group for thousands of groups;
* every well records how much of its input its output covers, the run folds that
  into ``pipeline_info.json``, and the viewer says so at load.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.io import estimate_group_scratch_bytes, preflight_scratch_space
from pipeline.run_report import (
    clear_run_report,
    collect_run_report,
    summarize,
    write_well_report,
)


# ── The completeness record ─────────────────────────────────────────────────


def test_a_partial_well_is_recorded_as_partial(tmp_path):
    write_well_report(tmp_path, "C02", groups_total=138, groups_covered=46,
                      failed=["a.tif", "b.tif"])
    write_well_report(tmp_path, "E06", groups_total=138, groups_covered=138)

    report = collect_run_report(tmp_path)
    assert report["incomplete_wells"] == ["C02"]
    assert report["groups_total"] == 276
    assert report["groups_covered"] == 184
    assert report["wells"]["C02"]["failed"] == ["a.tif", "b.tif"]


def test_a_complete_run_says_nothing(tmp_path):
    write_well_report(tmp_path, "E06", groups_total=6, groups_covered=6)
    report = collect_run_report(tmp_path)
    assert report["incomplete_wells"] == []
    assert summarize(report) == ""


def test_the_summary_names_the_wells_and_counts_the_gap(tmp_path):
    write_well_report(tmp_path, "C02", groups_total=138, groups_covered=46)
    write_well_report(tmp_path, "D08", groups_total=138, groups_covered=92)
    msg = summarize(collect_run_report(tmp_path))
    assert "2 well(s)" in msg
    assert "138" not in msg or "138 group(s) missing" in msg
    assert "C02" in msg and "D08" in msg
    assert "--incremental" in msg, "the recovery route belongs in the message"


def test_reports_survive_being_written_by_separate_processes(tmp_path):
    """One file per well, never appended to — so a worker pool cannot interleave
    two wells' records into a corrupt file."""
    for i in range(12):
        write_well_report(tmp_path, f"A{i:02d}", groups_total=6,
                          groups_covered=6 if i % 2 else 3)
    report = collect_run_report(tmp_path)
    assert len(report["wells"]) == 12
    assert len(report["incomplete_wells"]) == 6


def test_collect_is_empty_before_any_run_and_clear_removes_it(tmp_path):
    assert collect_run_report(tmp_path)["wells"] == {}
    write_well_report(tmp_path, "C02", groups_total=6, groups_covered=6)
    clear_run_report(tmp_path)
    assert collect_run_report(tmp_path)["wells"] == {}


def test_recording_never_fails_the_run(tmp_path):
    """Bookkeeping that cannot be written must not cost the data it describes."""
    blocked = tmp_path / "nope"
    blocked.write_text("i am a file, not a directory")
    assert write_well_report(blocked, "C02", groups_total=6, groups_covered=1) is None


# ── The scratch preflight ───────────────────────────────────────────────────


def test_group_estimate_counts_inputs_and_intermediates():
    # 5 MB per image, 2 channels: both inputs staged, plus the float32
    # intermediates that are the bulk of it.
    est = estimate_group_scratch_bytes(5_000_000, 2)
    assert est > 5_000_000 * 2, "intermediates dominate and must be counted"


def test_preflight_refuses_a_volume_too_small_for_one_well(tmp_path, monkeypatch):
    import pipeline.io as io

    monkeypatch.setattr(io, "_free_bytes", lambda p: 100 * (1 << 20))   # 100 MiB
    ok, msg = io.preflight_scratch_space(
        tmp_path, sample_image_bytes=5_000_000, n_channels=2,
        groups_per_well=138, concurrent_wells=4,
    )
    assert ok is False
    assert "one well" in msg
    assert "ALLWELL_SCRATCH_DIR" in msg, "the message must say how to move scratch"


def test_preflight_warns_but_proceeds_when_merely_tight(tmp_path, monkeypatch):
    """The estimate is rough, so a tight volume warns — refusing to start on a
    heuristic would be worse than the risk it guards against."""
    import pipeline.io as io

    per_group = estimate_group_scratch_bytes(1_000_000, 2)
    monkeypatch.setattr(io, "_free_bytes", lambda p: per_group * 12)
    ok, msg = io.preflight_scratch_space(
        tmp_path, sample_image_bytes=1_000_000, n_channels=2,
        groups_per_well=10, concurrent_wells=4,
    )
    assert ok is True and msg, "tight means warn, not stop"


def test_preflight_is_silent_when_there_is_room(tmp_path, monkeypatch):
    import pipeline.io as io

    monkeypatch.setattr(io, "_free_bytes", lambda p: 500 * (1 << 30))
    ok, msg = io.preflight_scratch_space(
        tmp_path, sample_image_bytes=5_000_000, n_channels=2,
        groups_per_well=138, concurrent_wells=4,
    )
    assert ok is True and msg == ""


def test_preflight_says_nothing_when_free_space_is_unknowable(tmp_path, monkeypatch):
    import pipeline.io as io

    monkeypatch.setattr(io, "_free_bytes", lambda p: None)
    assert preflight_scratch_space(
        tmp_path, sample_image_bytes=5_000_000, n_channels=2,
        groups_per_well=138, concurrent_wells=4,
    ) == (True, "")


# ── Stopping a well once the storage is gone ────────────────────────────────


def test_a_well_stops_attempting_groups_after_repeated_io_failures(monkeypatch):
    """Without this a full volume is rediscovered once per FOV-group: every
    remaining group fails identically, each writing a traceback, while the run
    spends hours producing complete-looking output for a gutted well."""
    import process_microscopy as pm

    attempted: list[str] = []

    def _boom(nuclear_path, fluor_paths, **kwargs):
        attempted.append(Path(nuclear_path).name)
        raise OSError("No space left on device")

    monkeypatch.setattr(pm, "process_image_group", _boom)
    groups = [(Path(f"exp_RFP_C02_f{i}_t0.tif"), []) for i in range(20)]
    records, failed = pm._process_groups_in_worker(
        groups, {"output_dir": Path("/nonexistent")}, "C02", errors_dir=None,
    )
    assert records == []
    assert len(failed) == 20, "every group is reported missing, attempted or not"
    assert len(attempted) == pm._MAX_CONSECUTIVE_IO_FAILURES, (
        f"stopped after {len(attempted)} attempts; expected to give up at "
        f"{pm._MAX_CONSECUTIVE_IO_FAILURES}"
    )


def test_a_lone_failure_does_not_stop_the_well(monkeypatch):
    """One bad frame is not a failing disk — the rest of the well must run."""
    import process_microscopy as pm

    seen: list[str] = []

    def _one_bad(nuclear_path, fluor_paths, **kwargs):
        seen.append(Path(nuclear_path).name)
        if "f3" in Path(nuclear_path).name:
            raise OSError("transient")
        return [{"nucleus_id": 1}]

    monkeypatch.setattr(pm, "process_image_group", _one_bad)
    groups = [(Path(f"exp_RFP_C02_f{i}_t0.tif"), []) for i in range(8)]
    records, failed = pm._process_groups_in_worker(
        groups, {"output_dir": Path("/nonexistent")}, "C02", errors_dir=None,
    )
    assert len(seen) == 8, "every group is still attempted"
    assert len(failed) == 1 and len(records) == 7


def test_non_io_failures_do_not_trip_the_storage_abort(monkeypatch):
    """A segmentation bug that throws on every frame is not a storage problem;
    aborting the well would hide how widespread it is."""
    import process_microscopy as pm

    attempted: list[str] = []

    def _value_error(nuclear_path, fluor_paths, **kwargs):
        attempted.append(Path(nuclear_path).name)
        raise ValueError("bad model output")

    monkeypatch.setattr(pm, "process_image_group", _value_error)
    groups = [(Path(f"exp_RFP_C02_f{i}_t0.tif"), []) for i in range(6)]
    _records, failed = pm._process_groups_in_worker(
        groups, {"output_dir": Path("/nonexistent")}, "C02", errors_dir=None,
    )
    assert len(attempted) == 6 and len(failed) == 6


# ── A remote run says what it is doing while it stages ──────────────────────


def test_staging_reports_progress_per_archive(tmp_path, monkeypatch):
    """The longest silent stretch of a remote run: the whole plate is copied
    into scratch before a byte is uploaded, and the copy helper logs only on
    RETRY — so a healthy run printed nothing between "[remote] target:" and the
    staged-count line, which reads as a hang on a slow input volume."""
    import services.remote_runner as rr

    src = tmp_path / "in"
    src.mkdir()
    for well in ("C02", "C03", "D04"):
        (src / f"{well}.zip").write_bytes(b"x" * 1024)

    lines: list[str] = []
    monkeypatch.setattr(rr, "_new_scratch_dir",
                        lambda prefix: tmp_path / "stage")
    (tmp_path / "stage").mkdir()
    monkeypatch.setattr(rr, "sweep_stale_scratch", lambda **kw: 0)
    monkeypatch.setattr(rr, "_filter_zip_channels",
                        lambda *a, **k: None, raising=False)

    out = rr.stage_local_zips(
        src, filename_schema="experiment:channel:well:fov:timepoint",
        filename_sep="_", fluor_tokens=None, nuclear_token=None,
        log_fn=lines.append, incremental=True, prior_output_dir=tmp_path / "out",
    )
    assert out is not None
    text = "".join(lines)
    assert "scanning input" in text, "the very first slow step must announce itself"
    assert "staging 3 well archive(s)" in text
    for well in ("C02", "C03", "D04"):
        assert f"{well} —" in text, f"no per-archive tick for {well}: {text}"


# ── The viewer says so at load ──────────────────────────────────────────────


def test_the_viewer_reports_incomplete_wells(monkeypatch):
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from well_viewer.core import load_controller as lc

    said: list[str] = []
    ctx = SimpleNamespace(
        state=SimpleNamespace(dataset=SimpleNamespace(
            well_paths={"C02": Path("c02.csv"), "E06": Path("e06.csv")},
            pipeline_info={"run_report": {
                "incomplete_wells": ["C02"],
                "wells": {
                    "C02": {"groups_total": 138, "groups_covered": 46},
                    "E06": {"groups_total": 138, "groups_covered": 138},
                },
            }},
        )),
        notify=SimpleNamespace(set_status=lambda m: said.append(m),
                               toast=lambda m, **k: said.append(m)),
    )
    lc._report_incomplete_wells(ctx)
    assert said and "C02" in said[0]
    assert "92" in said[0], "the number of missing groups belongs in the message"


def test_the_viewer_is_silent_for_a_complete_dataset():
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from well_viewer.core import load_controller as lc

    said: list[str] = []
    ctx = SimpleNamespace(
        state=SimpleNamespace(dataset=SimpleNamespace(
            well_paths={"E06": Path("e06.csv")},
            pipeline_info={"run_report": {"incomplete_wells": [], "wells": {}}},
        )),
        notify=SimpleNamespace(set_status=lambda m: said.append(m),
                               toast=lambda m, **k: said.append(m)),
    )
    lc._report_incomplete_wells(ctx)
    assert said == []


def test_a_dataset_from_before_the_record_existed_is_silent():
    """No ``run_report`` block at all — an older run — must not warn."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from well_viewer.core import load_controller as lc

    said: list[str] = []
    ctx = SimpleNamespace(
        state=SimpleNamespace(dataset=SimpleNamespace(
            well_paths={"E06": Path("e06.csv")}, pipeline_info={},
        )),
        notify=SimpleNamespace(set_status=lambda m: said.append(m),
                               toast=lambda m, **k: said.append(m)),
    )
    lc._report_incomplete_wells(ctx)
    assert said == []


def test_the_record_merges_into_pipeline_info_without_losing_it(tmp_path):
    """The sidecar is written before any image work so metadata survives a
    crash; folding the completeness record in must not rewrite it from scratch."""
    import process_microscopy as pm
    from pipeline.io import _safe_atomic_text_write
    from pipeline.run_report import merge_into_pipeline_info

    info = tmp_path / pm.PIPELINE_INFO_FILENAME
    info.write_text(json.dumps({"schema": "a:b:c", "fluor_tokens": ["GFP"]}))
    merge_into_pipeline_info(
        info, {"run_report": {"incomplete_wells": ["C02"]}},
        atomic_writer=_safe_atomic_text_write)
    out = json.loads(info.read_text())
    assert out["schema"] == "a:b:c" and out["fluor_tokens"] == ["GFP"]
    assert out["run_report"]["incomplete_wells"] == ["C02"]
