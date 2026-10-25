"""A remote re-run skips the wells finished here under the same settings, as a local run does, unless forced.

Reported: a remote nanowell run started again straight after the previous one finished re-uploaded and recomputed
the whole plate. Measured on the code: a local re-run skips a finished well (``process_microscopy``'s pool), but a
remote one sent every well — only an incremental run left finished wells out, a remote nanowell run cannot be
incremental, and *Clean up remote* had emptied the host's output directory, so the host recomputed everything.

These drive the real ``PipelineRunner._run_pipeline_remote`` with the real staging over a plate whose wells are
finished locally; only the host is a stub that records what it was handed (the harness of
``test_force_reprocesses_a_remote_run``).
"""

from __future__ import annotations

import csv
import json
import types
import zipfile
from pathlib import Path

import pytest


pytest.importorskip("PySide6")

SCHEMA = "experiment:channel:well:fov:timepoint"
NANOWELL = {"nanowell_format": "100um", "phase_token": "DAPI", "segmentation_method": "cellpose_labelfree"}


def _plate(tmp_path: Path) -> tuple[Path, Path]:
    """Two wells, A01 and B02, each measured in the output folder."""
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    for well in ("A01", "B02"):
        with zipfile.ZipFile(inp / f"{well}.zip", "w") as zf:
            for fov in (1, 2):
                for ch in ("DAPI", "GFP"):
                    zf.writestr(f"e_{ch}_{well}_{fov}_T01.tif", b"x")
        with open(out / f"measurements_{well}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["well", "fov", "timepoint"])
            w.writeheader()
            for fov in (1, 2):
                w.writerow({"well": well, "fov": str(fov), "timepoint": "T01"})
    return inp, out


def _opts(**extra) -> dict:
    return {"filename_schema": SCHEMA, "filename_sep": "_", "fluor_tokens": ["GFP"], "nuclear_token": "DAPI",
            "csv_prefix": "measurements", **extra}


def _record(out: Path, well: str, opts: dict, host_extras=()) -> None:
    """What the host's pipeline leaves beside a well (and the Fetcher brings back): the settings it ran under."""
    from pipeline.cli_args import settings_key_from_argv
    from pipeline.settings_record import record_well_settings
    from services.pipeline_service import build_pipeline_args

    argv = build_pipeline_args(Path("process_microscopy.py"), out.parent / "in", out, opts)
    record_well_settings(out, well, settings_key_from_argv([*argv[2:], *host_extras]))


def _run(inp: Path, out: Path, opts: dict, *, host_extras=(), argv_sent: list | None = None):
    """``(wells sent, log lines)`` for one remote run of *inp* into *out*; the argv the host was handed goes into
    *argv_sent* when given."""
    import services.pipeline_runner as pr
    from services.pipeline_service import build_pipeline_args

    spawned: list = []

    class _Proc:
        pid = 1
        returncode = 0
        stdout = iter(())
        _ssh = None

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def spawn(zipped, out_dir, argv, **k):
        spawned.append(zipped)
        if argv_sent is not None:
            argv_sent.append(list(argv))
        return _Proc()

    r = pr.PipelineRunner()
    r._remote = types.SimpleNamespace(name="cluster")
    r._remote_runner = types.SimpleNamespace(
        estimate=lambda z: types.SimpleNamespace(format=lambda: "est"),
        spawn=spawn, verify_and_cleanup=lambda ok, **k: True,
        close_remote_job=lambda **k: None, cancel=lambda proc: None,
        can_request_suspend=False, _host_argv_extras=lambda _filter: list(host_extras))
    args = build_pipeline_args(Path("process_microscopy.py"), inp, out, opts)
    r._run_pipeline_remote(inp, out, args, opts)
    lines = []
    while not r.log_q.empty():
        kind, payload = r.log_q.get_nowait()
        if kind in ("line", "done"):
            lines.append(str(payload))
    sent = sorted(p.stem for z in spawned for p in z.well_zips)
    return (sent if spawned else None), lines


def test_a_plain_re_run_of_a_finished_plate_sends_nothing_and_says_so(tmp_path):
    inp, out = _plate(tmp_path)
    for well in ("A01", "B02"):
        _record(out, well, _opts())
    sent, lines = _run(inp, out, _opts())
    assert sent is None                                                   # the host was never started
    assert any("2 well(s) already finished here" in ln and "A01, B02" in ln for ln in lines)
    assert any("Nothing to upload" in ln for ln in lines)
    assert any("✓ A01: up to date (local)" in ln for ln in lines)


def test_a_well_in_the_force_list_is_sent_and_the_rest_are_not(tmp_path):
    inp, out = _plate(tmp_path)
    for well in ("A01", "B02"):
        _record(out, well, _opts())
    sent, _lines = _run(inp, out, _opts(force_wells=["A01"]))
    assert sent == ["A01"]


def test_force_sends_every_well(tmp_path):
    inp, out = _plate(tmp_path)
    for well in ("A01", "B02"):
        _record(out, well, _opts())
    sent, _lines = _run(inp, out, _opts(force=True))
    assert sent == ["A01", "B02"]


def test_a_well_made_under_other_settings_is_sent_again(tmp_path):
    """The reuse is keyed on the settings: B02 was measured with another minimum nucleus area."""
    inp, out = _plate(tmp_path)
    _record(out, "A01", _opts())
    _record(out, "B02", _opts(min_nucleus_area_px=999))
    sent, _lines = _run(inp, out, _opts())
    assert sent == ["B02"]


def test_the_host_s_own_flags_count_among_the_settings(tmp_path):
    """A host whose extra arguments change what is computed: a well recorded without them is not finished for it."""
    inp, out = _plate(tmp_path)
    _record(out, "A01", _opts(), host_extras=["--min_nucleus_area_px", "77"])
    _record(out, "B02", _opts())
    sent, _lines = _run(inp, out, _opts(), host_extras=["--min_nucleus_area_px", "77"])
    assert sent == ["B02"]


def test_a_well_without_its_csv_is_sent(tmp_path):
    inp, out = _plate(tmp_path)
    for well in ("A01", "B02"):
        _record(out, well, _opts())
    (out / "measurements_B02.csv").write_text("")                         # empty: not a measurement
    sent, _lines = _run(inp, out, _opts())
    assert sent == ["B02"]


def _grid(out: Path, well: str, *timepoints: str) -> None:
    """A nanowell well's grid record naming *timepoints* as measured."""
    (out / "nanowells").mkdir(exist_ok=True)
    (out / "nanowells" / f"{well}_grid.json").write_text(
        json.dumps({"timepoints": {tp: {"status": "ok"} for tp in timepoints}}))


def _forced(argv: list[str]) -> list[str]:
    if "--force_wells" not in argv:
        return []
    rest = argv[argv.index("--force_wells") + 1:]
    return [a for a in rest[:next((i for i, a in enumerate(rest) if a.startswith("--")), len(rest))]]


def test_a_nanowell_re_run_skips_its_finished_wells_too(tmp_path):
    """The reported case: a remote nanowell run, which cannot be incremental, run again on a finished plate."""
    inp, out = _plate(tmp_path)
    opts = _opts(**NANOWELL)
    _record(out, "A01", opts)
    _grid(out, "A01", "T01")
    (out / "measurements_B02.csv").unlink()                               # B02 never finished
    sent, lines = _run(inp, out, opts)
    assert sent == ["B02"]
    assert any("1 well(s) already finished here" in ln and "A01" in ln for ln in lines)
    # and a forced nanowell well is redone
    sent, _lines = _run(inp, out, _opts(**NANOWELL, force_wells=["A01"]))
    assert sent == ["A01", "B02"]


def test_an_unparsable_run_finishes_nothing(tmp_path):
    """No key means "cannot tell", never "matches": settings_match would accept an empty key as matching anything."""
    from services.remote_finished_wells import finished_here

    _inp, out = _plate(tmp_path)
    # Parses, but the pipeline would refuse it (a nanowell run with no phase channel), so it has no key.
    argv = ["python", "pm.py", "--input_dir", "in", "--output_dir", str(out), "--nanowell_format", "100um"]
    assert finished_here(out, argv, ["A01", "B02"]) == []



def test_the_batched_path_asks_the_same_question(tmp_path):
    """A batched run skipped any well with a local CSV: neither the force list nor the settings counted, so a well
    named in Force wells, or measured under other settings, was skipped as done. It now asks
    ``services.remote_finished_wells`` too. (What follows the skip needs a host, so only the skip is read.)"""
    from pipeline.settings_record import record_well_settings
    from tests.test_batched_run_progress import _bare_batched

    proc, out = _bare_batched(tmp_path, ["A01", "A02", "A03"])
    for well in ("A01", "A02", "A03"):
        (out / f"measurements_{well}.csv").write_text("cell_id\n1\n")
    record_well_settings(out, "A03", "0123456789abcdef")                  # made under other settings
    proc._pipeline_argv = ["--csv_prefix", "measurements", "--force_wells", "A01"]
    logged: list[str] = []
    proc._log = logged.append
    proc._run_batches()
    skip_lines = [ln for ln in logged if "already complete locally" in ln]
    assert len(skip_lines) == 1 and ": A02." in skip_lines[0], skip_lines


def test_the_batched_path_never_skips_an_incremental_run_s_wells(tmp_path):
    """An incremental run's staged archives hold only the fields still missing, and every one of its wells has a
    local CSV: skipping them as finished would drop exactly the work the run is for. Its argv need not carry
    ``--incremental`` (the runner makes a resume incremental), so the staged flag decides."""
    from tests.test_batched_run_progress import _bare_batched

    proc, out = _bare_batched(tmp_path, ["A01"])
    (out / "measurements_A01.csv").write_text("cell_id\n1\n")
    proc._zipped.incremental = True
    logged: list[str] = []
    proc._log = logged.append
    proc._run_batches()
    assert not any("already complete locally" in ln for ln in logged)


def test_a_nanowell_well_short_of_a_timepoint_is_sent_whole_and_forced(tmp_path):
    """Left part way (its grid names fewer timepoints than its archive holds) or given a new timepoint since: either
    way its tables do not cover its input, so it is not finished. It is sent whole — the host analyses it in full —
    and forced, so an earlier attempt's output on the host cannot be kept in its place."""
    inp, out = _plate(tmp_path)
    opts = _opts(**NANOWELL)
    for well in ("A01", "B02"):
        _record(out, well, opts)
        _grid(out, well, "T01")
    with zipfile.ZipFile(inp / "B02.zip", "a") as zf:                      # a timepoint added since
        for ch in ("DAPI", "GFP"):
            zf.writestr(f"e_{ch}_B02_1_T02.tif", b"x")
    argv: list = []
    sent, _lines = _run(inp, out, opts, argv_sent=argv)
    assert sent == ["B02"]
    assert _forced(argv[-1]) == ["B02"]


def test_a_nanowell_well_its_record_says_failed_is_sent_again(tmp_path):
    inp, out = _plate(tmp_path)
    opts = _opts(**NANOWELL)
    for well in ("A01", "B02"):
        _record(out, well, opts)
        _grid(out, well, "T01")
    (out / "pipeline_info.json").write_text(json.dumps({"run_report": {"wells": {
        "A01": {"groups_total": 1, "groups_covered": 1},
        "B02": {"groups_total": 1, "groups_covered": 1, "failed": ["T01"]}}}}))
    argv: list = []
    sent, _lines = _run(inp, out, opts, argv_sent=argv)
    assert sent == ["B02"] and _forced(argv[-1]) == ["B02"]


def test_a_well_without_output_is_sent_but_not_forced(tmp_path):
    """Forcing is for a well with partial output here; one with none has nothing an earlier attempt could stand in
    for, and the force list the user typed is kept as it was."""
    inp, out = _plate(tmp_path)
    opts = _opts(**NANOWELL)
    _record(out, "A01", opts)
    _grid(out, "A01", "T01")
    (out / "measurements_B02.csv").unlink()
    argv: list = []
    sent, _lines = _run(inp, out, opts, argv_sent=argv)
    assert sent == ["B02"] and _forced(argv[-1]) == []


def test_forcing_merges_with_the_wells_the_user_named():
    from services.remote_finished_wells import argv_forcing

    argv = ["--input_dir", "in", "--force_wells", "C03", "D04", "--output_dir", "out"]
    assert argv_forcing(argv, ["D04", "E05"]) == [
        "--input_dir", "in", "--output_dir", "out", "--force_wells", "C03", "D04", "E05"]
    assert argv_forcing(argv, []) == argv


# ── what the adversarial review found ────────────────────────────────────────

def test_no_auto_threshold_changes_no_well_s_own_output():
    """``finished_here`` treats a well recorded with and without ``--no_auto_threshold`` as made under the same
    settings, because every batched host is given it (``batch_pipeline_argv``) and so is a single-shot run that sends
    part of a plate. That is sound only while the flag gates nothing but the plate-level cell-gating pass: the one
    pipeline module that reads it must stay ``pipeline/auto_threshold_pass.py``."""
    import ast

    from tests._repo_scan import parsed_sources, rel

    readers = set()
    for path, tree in parsed_sources(roots=("pipeline", "util")):
        for node in ast.walk(tree):
            attr = isinstance(node, ast.Attribute) and node.attr == "no_auto_threshold"
            text = isinstance(node, ast.Constant) and node.value == "no_auto_threshold"
            if attr or text:
                readers.add(rel(path))
    readers.discard("util/settings_digest.py")                             # names it as keyed, reads nothing
    assert readers == {"pipeline/auto_threshold_pass.py"}, readers
    import process_microscopy

    assert "no_auto_threshold" not in Path(process_microscopy.__file__).read_text().replace(
        "run_auto_threshold_pass", "")


def test_a_well_its_host_computed_with_no_auto_threshold_is_finished(tmp_path):
    """Reported by review: a batched host always gets ``--no_auto_threshold``, which is keyed, so no well a batched
    run computed ever matched the client's key, and every batched re-run sent the whole plate again."""
    from services.remote_commands import batch_pipeline_argv

    inp, out = _plate(tmp_path)
    _record(out, "A01", _opts(), host_extras=batch_pipeline_argv([])[-1:])  # as a batch's host recorded it
    _record(out, "B02", _opts())
    sent, _lines = _run(inp, out, _opts())
    assert sent is None                                                   # both finished: nothing sent


def test_a_partial_single_shot_upload_leaves_the_plate_s_gating_to_this_computer(tmp_path, monkeypatch):
    """Reported by review: the host's auto-threshold pass reads only the wells it was sent and its fetched sidecar
    replaced the plate's thresholds with a subset's. Sending part of a plate now tells the host not to, and the pass
    runs here over every well after the fetch — unless the user turned it off."""
    import services.pipeline_runner as pr

    regated: list = []
    monkeypatch.setattr(pr, "run_local_auto_threshold", lambda out, argv, log: regated.append(list(argv)))
    inp, out = _plate(tmp_path)
    _record(out, "A01", _opts())
    (out / "measurements_B02.csv").unlink()
    argv: list = []
    sent, _lines = _run(inp, out, _opts(), argv_sent=argv)
    assert sent == ["B02"] and "--no_auto_threshold" in argv[-1]
    assert len(regated) == 1 and "--no_auto_threshold" not in regated[0]

    regated.clear()
    argv.clear()
    sent, _lines = _run(inp, out, _opts(auto_threshold=False), argv_sent=argv)
    assert sent == ["B02"] and regated == []                              # the user's choice stands


def test_a_whole_plate_upload_keeps_the_host_s_own_gating(tmp_path, monkeypatch):
    import services.pipeline_runner as pr

    regated: list = []
    monkeypatch.setattr(pr, "run_local_auto_threshold", lambda out, argv, log: regated.append(argv))
    inp, out = _plate(tmp_path)
    for well in ("A01", "B02"):
        (out / f"measurements_{well}.csv").unlink()
    argv: list = []
    sent, _lines = _run(inp, out, _opts(), argv_sent=argv)
    assert sent == ["A01", "B02"] and "--no_auto_threshold" not in argv[-1] and regated == []


def test_a_well_its_record_says_was_left_part_way_is_sent_again(tmp_path):
    """Reported by review: a well wound down part way still writes a CSV and a settings record, and only a nanowell
    well was checked for wholeness. Every well with a completeness record is now."""
    inp, out = _plate(tmp_path)
    for well in ("A01", "B02"):
        _record(out, well, _opts())
    (out / "pipeline_info.json").write_text(json.dumps({"run_report": {"wells": {
        "A01": {"groups_total": 2, "groups_covered": 2},
        "B02": {"groups_total": 2, "groups_covered": 1, "aborted_reason": "suspended"}}}}))
    argv: list = []
    sent, _lines = _run(inp, out, _opts(), argv_sent=argv)
    # Sent, not forced: only a nanowell run forces (`nanowell_forced`), since a forced well moves the run's address.
    assert sent == ["B02"] and _forced(argv[-1]) == []


def test_a_nanowell_timepoint_that_did_not_finish_ok_is_not_covered(tmp_path):
    """Reported by review: every timepoint the grid record named counted, whatever its status; the rule
    (``completeness.nanowell_coverage``) counts only those that finished ``ok``."""
    inp, out = _plate(tmp_path)
    opts = _opts(**NANOWELL)
    for well in ("A01", "B02"):
        _record(out, well, opts)
        _grid(out, well, "T01")
    (out / "nanowells" / "B02_grid.json").write_text(json.dumps({"timepoints": {"T01": {"status": "unaddressed"}}}))
    sent, _lines = _run(inp, out, opts)
    assert sent == ["B02"]


def test_a_finished_blank_alone_ends_the_run_without_its_out_zip_and_a_forced_one_is_sent(tmp_path):
    """Reported by review: the measured-blank check asked for ``<blank>_out.zip``, which a folder-layout or CSV-only
    fetch never keeps, so a finished plate uploaded its blank alone and recomputed it; and it ignored Force wells."""
    inp, out = _plate(tmp_path)
    opts = _opts(background="empty_well", background_well="B02")
    for well in ("A01", "B02"):
        _record(out, well, opts)
    sent, lines = _run(inp, out, opts)
    assert sent is None and any("Nothing to upload" in ln for ln in lines)
    sent, _lines = _run(inp, out, _opts(background="empty_well", background_well="B02", force_wells=["B02"]))
    assert sent == ["B02"]


def test_a_settings_marker_that_does_not_arrive_is_never_taken_as_a_match(tmp_path):
    """Reported by review: a failed pull left the previous computation's marker beside the new output, which a
    later run under those settings then skipped as current. A failed pull now leaves a marker nothing matches."""
    from pipeline.settings_record import record_well_settings, well_settings_allow_reuse
    from services.remote_finished_wells import UNKNOWN_SETTINGS, pull_settings_marker

    out = tmp_path / "out"
    record_well_settings(out, "A01", "0123456789abcdef")                  # the earlier computation's
    dest = out / ".aw_settings" / "A01.json"
    assert pull_settings_marker(lambda *a, **k: False, None, "remote", dest) is False
    assert json.loads(dest.read_text())["digest"] == UNKNOWN_SETTINGS
    assert not well_settings_allow_reuse(out, "A01", "0123456789abcdef")


def test_a_batched_run_s_blank_measured_against_its_run_directory_copy_is_finished(tmp_path):
    """Reported by review: a batched host is given the blank as ``--background_dir=<run dir>/bg``, which is keyed,
    so the blank's record never matched the client's key. The batched filter now keys with the blank's argv as the
    host gets it (``remote_batch_resume._blank_argv``) and the batch flag either way."""
    import types

    from pipeline.cli_args import settings_key_from_argv
    from pipeline.settings_record import record_well_settings
    from services.remote_commands import batch_pipeline_argv
    from tests.test_batched_run_progress import _bare_batched

    proc, out = _bare_batched(tmp_path, ["A01", "H12"])
    for well in ("A01", "H12"):
        (out / f"measurements_{well}.csv").write_text("cell_id\n1\n")
    argv = ["--csv_prefix", "measurements", "--background", "empty_well", "--background_well", "H12"]
    proc._pipeline_argv = argv
    proc._runner = types.SimpleNamespace(host=types.SimpleNamespace(remote_staging_dir="/stage"),
                                         _host_argv_extras=lambda _f: [], _run=None)
    proc._run_id = "run1"
    host_argv = ["--input_dir", "in", "--output_dir", "out", *batch_pipeline_argv(argv),
                 "--background_dir=/stage/runs/run1/bg"]
    record_well_settings(out, "H12", settings_key_from_argv(host_argv))
    logged: list[str] = []
    proc._log = logged.append
    proc._run_batches()
    skip_lines = [ln for ln in logged if "skipping upload + compute" in ln]
    assert len(skip_lines) == 1 and "A01, H12" in skip_lines[0], logged[:6]
