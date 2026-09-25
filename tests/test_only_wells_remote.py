"""``--only_wells`` on a remote run: only the subset is sent, and the host is
told only about what it was sent.

``stage_local_zips`` returns from several branches (zip, flat, folder, mixed,
incremental), so the subset is applied once to whatever it returns
(``services.pipeline_runner.scope_staged_to_only_wells``) rather than in some of
them. Two things follow that are easy to get wrong, and each has a test here:

* an incremental staging keeps a well back when its local output is already
  whole, so a named well may not be uploaded at all — and the host's pipeline
  refuses a ``--only_wells`` naming a well its input lacks. The host's argv
  names only what was sent;
* the empty-well blank is uploaded whatever the subset says, because the host
  builds its correction from it.

The runner is the real ``PipelineRunner`` driving ``_run_pipeline_remote`` on
this thread; the remote is a stub whose ``spawn`` records what it was handed,
in the shape ``tests/test_stop_never_waits_on_a_remote_upload.py`` uses.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from services.pipeline_runner import argv_with_only_wells, scope_staged_to_only_wells
from services.remote_runner import LocalInputMode, ZippedInput


def _zipped(tmp_path, wells, up_to_date=()):
    return ZippedInput(zip_dir=tmp_path, mode=LocalInputMode.ZIP,
                       well_zips=tuple(tmp_path / f"{w}.zip" for w in wells),
                       up_to_date_wells=tuple(up_to_date))


# ── the filter ───────────────────────────────────────────────────────────────

def test_the_upload_is_narrowed_to_the_subset(tmp_path):
    z = _zipped(tmp_path, ["A01", "B02", "C03"], up_to_date=["D04", "E05"])
    scoped_z, scoped = scope_staged_to_only_wells(z, ["b2", "D04"])
    assert [p.stem for p in scoped_z.well_zips] == ["B02"]
    assert scoped_z.up_to_date_wells == ("D04",)
    assert scoped == ["B02", "D04"]


def test_the_blank_is_uploaded_though_the_subset_does_not_name_it(tmp_path):
    z = _zipped(tmp_path, ["A01", "B02", "H12"])
    scoped_z, scoped = scope_staged_to_only_wells(z, ["B02"], keep=["H12"])
    assert [p.stem for p in scoped_z.well_zips] == ["B02", "H12"]
    assert scoped == ["B02"], "the blank is sent, not run"


def test_a_named_well_the_input_lacks_refuses_before_upload(tmp_path):
    with pytest.raises(ValueError, match="H12"):
        scope_staged_to_only_wells(_zipped(tmp_path, ["A01"]), ["A01", "H12"])


def test_no_subset_changes_nothing(tmp_path):
    z = _zipped(tmp_path, ["A01", "B02"])
    assert scope_staged_to_only_wells(z, None) == (z, None)
    assert scope_staged_to_only_wells(z, []) == (z, None)


def test_the_argv_list_is_rewritten_in_place_of_the_old_one():
    argv = ["pm.py", "--input_dir", "/in", "--only_wells", "A01", "B02", "--workers", "2"]
    assert argv_with_only_wells(argv, ["B02"]) == [
        "pm.py", "--input_dir", "/in", "--only_wells", "B02", "--workers", "2"]
    tail = ["pm.py", "--only_wells", "A01"]
    assert argv_with_only_wells(tail, ["C03", "A01"]) == ["pm.py", "--only_wells", "A01", "C03"]


# ── the runner ───────────────────────────────────────────────────────────────

def _run(monkeypatch, tmp_path, zipped, *, only_wells, opts_extra=None):
    import services.pipeline_runner as pr
    from services.pipeline_service import build_pipeline_args

    monkeypatch.setattr(pr, "stage_local_zips", lambda *a, **k: zipped)
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

    def spawn(z, out, argv, **k):
        spawned.append((z, list(argv)))
        return _Proc()

    remote = types.SimpleNamespace(
        estimate=lambda z: types.SimpleNamespace(format=lambda: "est"),
        spawn=spawn, verify_and_cleanup=lambda ok, **k: True,
        close_remote_job=lambda **k: None, cancel=lambda proc: None,
        can_request_suspend=False)
    r = pr.PipelineRunner()
    r._remote = types.SimpleNamespace(name="h")
    r._remote_runner = remote
    opts = {"filename_schema": "experiment:channel:well:fov:timepoint",
            "filename_sep": "_", "fluor_tokens": ["GFP"], "nuclear_token": "DAPI",
            "csv_prefix": "measurements", "only_wells": only_wells,
            **(opts_extra or {})}
    args = build_pipeline_args(Path("process_microscopy.py"), tmp_path / "in",
                               tmp_path / "out", opts)
    r._run_pipeline_remote(tmp_path / "in", tmp_path / "out", args, opts)
    events = []
    while not r.log_q.empty():
        events.append(r.log_q.get_nowait())
    return spawned, events


def _totals(events):
    return {p[0]: p[1] for k, p in events if k == "stage_total"}


def test_the_host_is_sent_and_told_only_the_subset(monkeypatch, tmp_path):
    z = _zipped(tmp_path, ["A01", "B02", "C03"], up_to_date=["D04"])
    spawned, events = _run(monkeypatch, tmp_path, z, only_wells=["B02", "D04"])
    (sent, argv), = spawned
    assert [p.stem for p in sent.well_zips] == ["B02"]
    # D04 was named but kept back as already whole: the host must not be told
    # to run a well its input does not hold, or it refuses the whole run.
    only = argv[argv.index("--only_wells") + 1:]
    only = only[:next((i for i, a in enumerate(only) if a.startswith("--")), len(only))]
    assert only == ["B02"]
    totals = _totals(events)
    assert totals["group"] == 4, "Group counts the plate that was grouped"
    assert totals["upload"] == 1
    assert totals["download"] == 2, "Download counts the run's wells: B02 and D04"
    marked = [p for k, p in events if k == "line" and "up to date (local)" in p]
    assert marked == ["[remote] ✓ D04: up to date (local)\n"]


def test_a_subset_that_is_already_whole_runs_nothing(monkeypatch, tmp_path):
    z = _zipped(tmp_path, ["A01"], up_to_date=["D04"])
    spawned, events = _run(monkeypatch, tmp_path, z, only_wells=["D04"])
    assert spawned == [], "the host was launched with nothing to run"
    done = [p for k, p in events if k == "done"]
    assert done and "every named well is already up to date" in done[0]


def test_a_named_well_missing_locally_fails_before_anything_is_sent(
        monkeypatch, tmp_path):
    spawned, events = _run(monkeypatch, tmp_path, _zipped(tmp_path, ["A01"]),
                           only_wells=["A01", "H12"])
    assert spawned == []
    errors = [p for k, p in events if k == "error"]
    assert errors and "H12" in errors[0]


def test_the_blank_rides_along_in_empty_well_mode(monkeypatch, tmp_path):
    z = _zipped(tmp_path, ["A01", "B02", "H12"])
    spawned, _ = _run(monkeypatch, tmp_path, z, only_wells=["B02"],
                      opts_extra={"background": "empty_well", "background_well": "H12"})
    (sent, argv), = spawned
    assert [p.stem for p in sent.well_zips] == ["B02", "H12"]
    only = argv[argv.index("--only_wells") + 1:]
    assert only[0] == "B02" and (len(only) == 1 or only[1].startswith("--"))


# ── the run address ──────────────────────────────────────────────────────────

def test_a_different_subset_is_a_different_run_address():
    """The host's run directory is addressed by a digest of the argv, and a run
    reattaches to its own earlier directory by it. A subset missing from that
    digest would let a run over B02 reattach to a run over A01."""
    from services.remote_run_id import pipeline_argv_digest

    base = ["process_microscopy.py", "--input_dir", "/in", "--output_dir", "/out",
            "--nuclear_token", "DAPI"]
    a = pipeline_argv_digest(base + ["--only_wells", "A01"])
    b = pipeline_argv_digest(base + ["--only_wells", "B02"])
    whole = pipeline_argv_digest(base)
    assert len({a, b, whole}) == 3
