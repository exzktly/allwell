"""A restarted batched run looks at the remote before it uploads or runs anything.

The report: restarting a stopped batched run sent the plate again — inputs already on the remote, and wells
whose finished output was still waiting there to be downloaded. A restart looked only at the local output folder;
and because every resume is incremental, its staged archives (and so its run id and run directory) differed from
the attempt it resumed, so that attempt's directory was never looked at.

These drive ``_BatchedRemoteProcess._run_batches`` — the app's own sequence: local filter, remote listing,
slicing, driver start, per-batch upload — against a local directory standing in for the remote host. The remote
shell commands (the listing, the moves and links) run for real under ``bash``; the upload stub behaves like
rsync's quick check, sending a file only when no copy of the same size and mtime is already in place.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import types
from pathlib import Path

import pytest

from services import run_journal
from services.remote_transport import _remote_join
from tests.test_batch_driver import _batched, _FakeProc

_REAL_POPEN = subprocess.Popen
_MTIME = 1_700_000_000
WELLS = ["A01", "A02", "A03", "A04", "A05", "A06"]


def _bash(argv_or_script, *, script: bool):
    cmd = ["bash", "-s"] if script else ["bash", "-c", _remote_join(argv_or_script)]
    child = _REAL_POPEN(cmd, stdin=subprocess.PIPE if script else None, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True)
    out, err = child.communicate(argv_or_script if script else None, timeout=20)
    return subprocess.CompletedProcess(cmd, child.returncode, out, err)


def _harness(tmp_path, monkeypatch, *, earlier=(), force=False, nanowell=False):
    plate = tmp_path / "plate"
    plate.mkdir(exist_ok=True)
    p = _batched(plate, WELLS, batch_size=2)
    for z in p._zipped.well_zips:
        os.utime(z, (_MTIME, _MTIME))
    remote_root = tmp_path / "remote"
    run_dir = remote_root / "runs" / "run-test"
    sent: list[str] = []

    def upload_subset(zips, dest, **kw):
        d = Path(dest)
        d.mkdir(parents=True, exist_ok=True)
        for z in zips:
            target, st = d / z.name, z.stat()
            if target.exists() and target.stat().st_size == st.st_size and int(target.stat().st_mtime) == int(st.st_mtime):
                continue
            shutil.copy2(z, target)
            sent.append(f"{d.name}/{z.name}")
        return (0, 0.0, 0)

    observed: list[str] = []
    queued: list = []
    # What the Fetcher was handed as finished on the remote by an earlier attempt (`remote_done_marker`): the
    # wells this run computes are announced to it too, by the batches' own `— done.` lines.
    p._fetcher.observe = lambda line: observed.append(line) if "by an earlier attempt" in line else None
    p._fetcher.after_queued = queued.append
    p._fetcher.run.nanowell = nanowell
    p._journal_keys = dict(argv_digest="d", source_key="s", host_alias="h")
    p._runner = types.SimpleNamespace(
        launcher=lambda: p._runner.host.launch_command,       # RemoteRunner.launcher(): the host's command, no node granted
        _cache=types.SimpleNamespace(_ssh_run=lambda argv, *, check, **kw: _bash(argv, script=False),
                                     _ssh_script=lambda script, *, check: _bash(script, script=True),
                                     upload_subset=upload_subset),
        _run=types.SimpleNamespace(remote_input_dir=str(run_dir / "in"), remote_output_dir=str(run_dir / "out"),
                                   remote_pidfile=str(run_dir / "run.pid")),
        host=types.SimpleNamespace(remote_staging_dir=str(remote_root), compute_launcher="srun --time=1:00:00", launch_command="srun --time=1:00:00",
                                   remote_scratch_dir="", ssh_argv=lambda **kw: ["ssh", "h"]),
        env_selector=("name", "allwell"),
        _build_remote_argv=lambda *a, **k: ["python", "pm.py"],
        _sweep_orphaned_allocation=lambda *a, **k: None,
        _claim_run_dirs=lambda *a, **k: "",
        close_remote_job=lambda **kw: None,
    )
    p._pipeline_argv = ["--force"] if force else []
    monkeypatch.setattr(run_journal, "stored_remote_addresses", lambda *a, **k: list(earlier))
    monkeypatch.setattr(run_journal, "recorded_remote_attempts", lambda *a, **k: list(earlier))
    return p, plate, run_dir, sent, observed, queued


def _run(p, monkeypatch):
    proc = _FakeProc()
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: proc)
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    for i in range(1, 5):
        proc.batch_finished(p, i)
    proc.finish()
    worker.join(timeout=30)
    assert not worker.is_alive()
    lines = []
    while not p._line_q.empty():
        item = p._line_q.get()
        if item:
            lines.append(item)
    return lines


def _finished_on(out_dir: Path, well: str) -> None:
    """What a well that covered its whole input leaves: its output and the pipeline's completion seal
    (``pipeline.run_report.seal_well_complete``)."""
    from pipeline.run_report import complete_seal_path

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{well}_out.zip").write_bytes(b"finished output")
    (out_dir / f"measurements_{well}.csv.gz").write_bytes(b"rows")
    complete_seal_path(out_dir, well).parent.mkdir(exist_ok=True)
    complete_seal_path(out_dir, well).write_text(f'{{"well": "{well}"}}')


def _uploaded_copy(plate: Path, dest: Path, well: str) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(plate / f"{well}.zip", dest / f"{well}.zip")


def test_a_restart_in_the_same_run_directory_sends_only_what_the_remote_lacks(tmp_path, monkeypatch):
    """A01 downloaded; A02 finished on the remote, not downloaded; A03/A04 swapped into ``in/`` mid-batch."""
    p, plate, run_dir, sent, observed, _ = _harness(tmp_path, monkeypatch)
    (p._local_output_dir / "measurements_A01.csv").write_text("well\nA01\n")
    _finished_on(run_dir / "out", "A02")
    for w in ("A03", "A04"):
        _uploaded_copy(plate, run_dir / "in", w)

    lines = _run(p, monkeypatch)

    assert p._returncode == 0, p._logged
    assert sent == ["b2/A05.zip", "b2/A06.zip"], (
        f"the restart re-sent what the remote already had: {sent}")
    assert any("Well A02 — done." in line for line in observed), "A02's finished output is never downloaded"
    assert any("Well A02 — done." in line for line in lines), "A02 is not counted on the plate"
    assert not (run_dir / "in").exists()


def test_a_resume_in_a_new_run_directory_takes_from_the_earlier_attempt(tmp_path, monkeypatch):
    """The resume is incremental, so its run directory is new; the earlier one is found through the journal."""
    old = tmp_path / "remote" / "runs" / "run-old"
    earlier = [{"remote_output_dir": str(old / "out"), "remote_input_dir": str(old / "in")}]
    p, plate, run_dir, sent, observed, _ = _harness(tmp_path, monkeypatch, earlier=earlier)
    _finished_on(old / "out", "A01")
    _finished_on(old / "out", "A02")
    _uploaded_copy(plate, old / "in", "A03")
    _uploaded_copy(plate, old / "b2", "A05")

    _run(p, monkeypatch)

    assert p._returncode == 0, p._logged
    # A03..A06 remain, in batches [A03, A04] and [A05, A06]; A03 and A05 are already on the remote.
    assert sent == ["b1/A04.zip", "b2/A06.zip"], sent
    assert sorted(f.name for f in (run_dir / "out").iterdir()) == [
        ".aw_complete", "A01_out.zip", "A02_out.zip", "measurements_A01.csv.gz", "measurements_A02.csv.gz"], (
        "the finished wells were not brought into this run's directory for the Fetcher")
    assert sorted(f.name for f in (run_dir / "out" / ".aw_complete").iterdir()) == ["A01.json", "A02.json"], (
        "the seals did not travel with the output, so a resume of this directory would run them again")
    assert {line.split()[1] for line in observed} == {"A01", "A02"}
    assert (old / "in" / "A03.zip").exists() and (old / "out" / "A01_out.zip").exists(), (
        "an earlier attempt's directory is linked from, never emptied")


def test_a_changed_archive_on_the_remote_is_sent_again(tmp_path, monkeypatch):
    """Reuse is decided by rsync's quick check, not by the name: a different file of the same name is replaced."""
    p, plate, run_dir, sent, _, _ = _harness(tmp_path, monkeypatch)
    (run_dir / "in").mkdir(parents=True)
    (run_dir / "in" / "A01.zip").write_bytes(b"not the plate's A01")

    _run(p, monkeypatch)

    assert "b1/A01.zip" in sent


def test_a_forced_run_downloads_nothing_it_did_not_compute(tmp_path, monkeypatch):
    p, plate, run_dir, sent, observed, _ = _harness(tmp_path, monkeypatch, force=True)
    _finished_on(run_dir / "out", "A02")
    _uploaded_copy(plate, run_dir / "in", "A01")

    _run(p, monkeypatch)

    assert observed == [], "Force asked for every well to be computed again"
    assert "b1/A02.zip" in sent and "b1/A01.zip" not in sent, sent


@pytest.mark.parametrize("nanowell", [True, False])
def test_a_nanowell_run_frees_the_wells_it_adopted(tmp_path, monkeypatch, nanowell):
    p, plate, run_dir, sent, observed, queued = _harness(tmp_path, monkeypatch, nanowell=nanowell)
    _finished_on(run_dir / "out", "A02")
    _run(p, monkeypatch)
    adopted_release = [t for t in queued if t.keywords.get("wells") == ["A02"]]
    assert bool(adopted_release) is nanowell


def test_an_adoption_across_builds_is_named(tmp_path, monkeypatch):
    """Tracker F7: a well another build finished is still adopted — the reuse keys deliberately span builds — but the
    log says so, from the build its seal records."""
    from pipeline.run_report import complete_seal_path

    p, plate, run_dir, sent, observed, _ = _harness(tmp_path, monkeypatch)
    _finished_on(run_dir / "out", "A02")
    complete_seal_path(run_dir / "out", "A02").write_text('{"well": "A02", "build": "0.0.1"}')
    _run(p, monkeypatch)
    assert [line.split()[1] for line in observed] == ["A02"]
    assert any("finished by another build" in m and "0.0.1" in m and "A02" in m for m in p._logged), p._logged


def test_an_adopted_well_held_for_the_re_anchoring_is_not_freed_before_the_pass(tmp_path, monkeypatch):
    """Tracker F4: the adopted wells' release ran at batch 1's hand-off and deleted the archive and outputs the
    final pass re-runs a held well from. A well this run took over a hold for is kept until that pass."""
    from services.remote_batch_resume import download_adopted

    queued: list = []
    run = types.SimpleNamespace(nanowell=True, nanowell_adopted_holds={"A02"})
    proc = types.SimpleNamespace(_adopted_pending=["A02", "A03"], _log=lambda m: None,
                                 _fetcher=types.SimpleNamespace(run=run, observe=lambda line: None,
                                                                after_queued=queued.append))
    download_adopted(proc)
    assert [t.keywords.get("held") for t in queued] == [{"A02"}], "a held well's files were released before its pass"


def test_the_earlier_attempt_survives_the_app_s_real_call_order(tmp_path, monkeypatch):
    """The batched run records its own address in ``__init__``, before ``_run_batches`` asks for earlier ones.

    The order the app runs: ``PipelineRunner`` opens the journal, ``_BatchedRemoteProcess.__init__`` records this
    attempt's address (``_record_remote_in_journal``), and only then does the batch loop read the journal for the
    attempt it resumes. Recording must keep the earlier address, or the lookup this whole path depends on finds
    only the run that is asking.
    """
    from services import pipeline_runner as PR
    from services import run_journal as J
    from services.remote_process import _record_remote_in_journal
    from services.remote_scratch import source_fingerprint
    from tests.test_run_journal_reattach import _plate

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    out = tmp_path / "out"
    out.mkdir()
    in_dir = _plate(tmp_path)
    keys = dict(argv_digest="d1", source_key=source_fingerprint(in_dir), host_alias="hpc")

    def attempt(run_id: str) -> None:
        runner = PR.PipelineRunner()
        runner._journal_spec = {"opts": {}}
        runner._open_journal(in_dir, out, ["--x"], {})
        run = types.SimpleNamespace(run_id=run_id, cache_key=f"key-{run_id}",
                                    remote_input_dir=f"/s/runs/{run_id}/in", remote_output_dir=f"/s/runs/{run_id}/out")
        _record_remote_in_journal(out, run=run, **keys)

    attempt("run-first")
    attempt("run-resume")
    found = J.stored_remote_addresses(out, argv_digests=[keys["argv_digest"]], source_key=keys["source_key"],
                                      host_alias=keys["host_alias"])
    assert "/s/runs/run-first/out" in [a["remote_output_dir"] for a in found], (
        "recording the resumed attempt's address lost the earlier attempt's, so its leftovers are never found")


def test_a_plate_with_nothing_left_to_run_still_downloads_what_finished_on_the_remote(tmp_path, monkeypatch):
    p, plate, run_dir, sent, observed, _ = _harness(tmp_path, monkeypatch)
    for w in WELLS[1:]:
        (p._local_output_dir / f"measurements_{w}.csv").write_text(f"well\n{w}\n")
    _finished_on(run_dir / "out", "A01")

    _run(p, monkeypatch)

    assert p._returncode == 0 and sent == []
    assert [line.split()[1] for line in observed] == ["A01"]
    assert any("downloading the 1 finished there" in m for m in p._logged)


def test_the_previous_attempt_s_well_states_survive_the_next_run_opening(tmp_path, monkeypatch):
    """``open_run`` clears the per-well records, which describe the current run; the restart needs the last
    attempt's, so they are carried. Driven in the app's order: run one records, run two opens, run two reads."""
    from services import run_journal as J

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    out = tmp_path / "out"
    out.mkdir()
    J.open_run(out, spec={}, mode="remote-batched", input_dir=str(tmp_path / "in"), argv=["--x"])
    J.record_well(out, "A01", "fetched")
    J.record_well(out, "A02", "analysed")
    J.record_well(out, "A03", "uploaded")
    J.open_run(out, spec={}, mode="remote-batched", input_dir=str(tmp_path / "in"), argv=["--x"])   # the restart
    J.record_well(out, "A04", "uploaded")
    J.open_run(out, spec={}, mode="remote-batched", input_dir=str(tmp_path / "in"), argv=["--x"])   # a second restart

    assert J.read_journal(out).wells == {}, "the current run's own record must start empty"
    assert J.previous_well_states(out) == {"A01": "fetched", "A02": "analysed", "A03": "uploaded",
                                           "A04": "uploaded"}


def test_the_restart_says_why_a_well_the_journal_expected_is_redone(tmp_path, monkeypatch):
    p, plate, run_dir, sent, observed, _ = _harness(tmp_path, monkeypatch)
    _finished_on(run_dir / "out", "A02")
    monkeypatch.setattr(run_journal, "previous_well_states",
                        lambda _d: {"A02": "analysed", "A03": "analysed", "A04": "uploaded", "A05": "fetched"})

    _run(p, monkeypatch)

    log = "".join(p._logged)
    assert "1 well(s) the journal recorded as finished on the remote, but their output is no longer there" in log
    assert ": A03" in log
    assert "recorded as uploaded, but their archives are no longer on the remote — sending them again: A04" in log
    assert "recorded as downloaded, but they have no complete CSV here — processing them again: A05" in log
    assert "A02" not in log.split("finished on the remote, but")[1].split("\n")[0], "A02's output IS there"


def test_an_attempt_recorded_by_an_earlier_build_is_found(tmp_path, monkeypatch):
    """9.12.5 recorded its address under the older argv digest, which also hashed execution knobs such as
    ``--workers``. A restart on the current build must still find that attempt's uploads — through the real
    journal, not a stubbed lookup."""
    from services import run_journal as J
    from services.remote_run_id import legacy_argv_digests, pipeline_argv_digest

    argv = ["python", "process_microscopy.py", "--input_dir", "/in", "--output_dir", "/out", "--workers", "6",
            "--nuclear_token", "DAPI"]
    new, legacy = pipeline_argv_digest(argv), legacy_argv_digests(argv)
    assert new not in legacy, "the fixture must reproduce the digest change, or it tests nothing"

    old = tmp_path / "remote" / "runs" / "run-old"
    p, plate, run_dir, sent, _, _ = _harness(tmp_path, monkeypatch)
    monkeypatch.undo()                                  # the real stored_remote_addresses, not the stub
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    out = p._local_output_dir
    J.open_run(out, spec={}, mode="remote-batched", input_dir=str(plate), argv=argv)
    J.record_remote(out, run_id="run-old", cache_key="k-old", host_alias="h",
                    remote_input_dir=str(old / "in"), remote_output_dir=str(old / "out"),
                    argv_digest=legacy[0], source_key="s")
    J.open_run(out, spec={}, mode="remote-batched", input_dir=str(plate), argv=argv)   # the restart
    p._journal_keys = dict(argv_digest=new, source_key="s", host_alias="h")
    p._legacy_digests = legacy
    _uploaded_copy(plate, old / "b1", "A01")
    _uploaded_copy(plate, old / "in", "A03")

    _run(p, monkeypatch)

    assert p._returncode == 0, p._logged
    assert "b1/A01.zip" not in sent and "b2/A03.zip" not in sent, (
        f"an upload recorded by the earlier build was sent again: {sent}")


# ── re-attaching the earlier attempt's output directory, where its half-finished wells' checkpoints are keyed ──

def _journal_with(tmp_path, monkeypatch, *addresses):
    from services import run_journal as J

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    out = tmp_path / "local-out"
    out.mkdir(exist_ok=True)
    J.open_run(out, spec={}, mode="remote-batched", input_dir="/in", argv=["--x"])
    for run_id, digest in addresses:
        J.record_remote(out, run_id=run_id, cache_key="k", host_alias="h", remote_input_dir=f"/r/runs/{run_id}/in",
                        remote_output_dir=f"/r/runs/{run_id}/out", argv_digest=digest, source_key="s")
    return out


def _probe_runner(existing: set[str], calls: list):
    def ssh_run(argv, *, check, **kw):
        calls.append(argv)
        return types.SimpleNamespace(returncode=0 if argv[-1] in existing else 1, stdout="")
    return types.SimpleNamespace(_cache=types.SimpleNamespace(_ssh_run=ssh_run))


KEYS = dict(argv_digest="d-new", source_key="s", host_alias="h")


def test_a_resume_writes_into_the_earlier_attempt_s_output_directory(tmp_path, monkeypatch):
    from services.remote_batch_resume import reattach_output_dir

    out = _journal_with(tmp_path, monkeypatch, ("run-first", "d-old"))
    calls: list = []
    chosen = reattach_output_dir(_probe_runner({"/r/runs/run-first/out"}, calls), out, KEYS, ("d-old",),
                                 fresh="/r/runs/run-now/out", forced=False, log=lambda _m: None)
    assert chosen == "/r/runs/run-first/out"


def test_the_fresh_directory_is_kept_when_resuming_is_not_safe(tmp_path, monkeypatch):
    from services.remote_batch_resume import reattach_output_dir

    out = _journal_with(tmp_path, monkeypatch, ("run-first", "d-old"))
    gone = reattach_output_dir(_probe_runner(set(), []), out, KEYS, ("d-old",), fresh="/f", forced=False,
                               log=lambda _m: None)
    forced_calls: list = []
    forced = reattach_output_dir(_probe_runner({"/r/runs/run-first/out"}, forced_calls), out, KEYS, ("d-old",),
                                 fresh="/f", forced=True, log=lambda _m: None)
    other = reattach_output_dir(_probe_runner({"/r/runs/run-first/out"}, []), out,
                                dict(KEYS, source_key="other-images"), ("d-old",), fresh="/f", forced=False,
                                log=lambda _m: None)
    assert gone == "/f", "a directory the remote no longer has is not written into"
    assert forced == "/f" and forced_calls == [], "a forced run computes everything again"
    assert other == "/f", "changed images are not this plate's run"


def test_staging_from_a_re_attached_directory_moves_rather_than_links(tmp_path, monkeypatch):
    """Re-attached, the earlier run directory is this run's: its archives are moved, so nothing is left behind."""
    old = tmp_path / "remote" / "runs" / "run-old"
    p, plate, run_dir, sent, observed, _ = _harness(tmp_path, monkeypatch)
    p._runner._run.remote_output_dir = str(old / "out")
    _finished_on(old / "out", "A02")
    _uploaded_copy(plate, old / "in", "A03")

    _run(p, monkeypatch)

    assert p._returncode == 0, p._logged
    assert "b2/A03.zip" not in sent and "b1/A03.zip" not in sent, sent
    assert not (old / "in" / "A03.zip").exists(), "a re-attached directory's archive was copied, not moved"
    assert [line.split()[1] for line in observed] == ["A02"]
    assert not (run_dir / "out").exists() or not any((run_dir / "out").iterdir()), (
        "finished output in the re-attached directory was linked somewhere else")


# ── the remote scratch: recorded in the journal, and read back to say which wells will resume ──

def test_the_remote_scratch_line_is_recorded_in_the_journal(tmp_path, monkeypatch):
    from services import run_journal as J
    from services.remote_batch_resume import note_remote_scratch

    out = _journal_with(tmp_path, monkeypatch, ("run-first", "d-old"))
    proc = types.SimpleNamespace(_local_output_dir=out)
    note_remote_scratch(proc, "2026-10-17 01:00:00  INFO     Scratch  : /home/u/.allwell/scratch\n")
    note_remote_scratch(proc, "2026-10-17 02:00:00  INFO     Scratch  : /somewhere/else\n")   # once per run
    assert J.read_journal(out).remote["remote_scratch_dir"] == "/home/u/.allwell/scratch"


def test_a_restart_says_which_part_way_wells_will_resume(tmp_path, monkeypatch):
    """Against a stand-in host: the earlier attempt's output directory, re-attached, and its scratch holding A03's
    checkpoint under this run's settings — and an unrelated directory of another configuration, not counted."""
    import hashlib

    from pipeline.fov_checkpoint import _output_stamp
    from services.remote_batch_resume import _settings_stamps

    old = tmp_path / "remote" / "runs" / "run-old"
    scratch = tmp_path / "remote-scratch"
    p, plate, run_dir, sent, _, _ = _harness(tmp_path, monkeypatch)
    p._pipeline_argv = ["python", "pm.py", "--input_dir", "/in", "--output_dir", "/out", "--nuclear_token", "DAPI"]
    p._runner._run.remote_output_dir = str(old / "out")
    p._runner._run.reattached = True
    p._runner.host.remote_scratch_dir = str(scratch)
    (old / "out").mkdir(parents=True)
    stamp = _output_stamp(old / "out")
    assert stamp == hashlib.blake2b(str((old / "out").resolve()).encode(), digest_size=6).hexdigest()
    (settings,) = _settings_stamps(p._pipeline_argv)[:1]
    ours = scratch / f"_tmp_images_{stamp}_{settings}_A03"
    ours.mkdir(parents=True)
    (ours / ".fov_exp_A03_F001.done").touch()
    (ours / ".fov_exp_A03_F002.done").touch()
    theirs = scratch / f"_tmp_images_{stamp}_deadbeef_A04"
    theirs.mkdir()
    (theirs / ".fov_exp_A04_F001.done").touch()

    _run(p, monkeypatch)

    log = "".join(p._logged)
    assert "1 well(s) left part-way will continue from their checkpoints" in log, log
    assert "A03 (2 field group(s) done)" in log
    assert "A04" not in log.split("will continue from their checkpoints")[1].split("\n")[0]


def _restart_with_no_scratch_folder_set(tmp_path, monkeypatch, *, checkpoint: bool) -> tuple[str, Path]:
    """A re-attached restart on a host with no scratch folder set; with *checkpoint*, A03's two finished fields are
    in the earlier attempt's output folder, where that attempt's pipeline kept them (``--scratch_fallback_dir``).
    The run's log and that folder, resolved."""
    from pipeline.fov_checkpoint import _output_stamp
    from services.remote_batch_resume import _settings_stamps

    old = tmp_path / "remote" / "runs" / "run-old"
    p, _plate, _run_dir, _sent, _, _ = _harness(tmp_path, monkeypatch)
    p._pipeline_argv = ["python", "pm.py", "--input_dir", "/in", "--output_dir", "/out", "--nuclear_token", "DAPI"]
    p._runner._run.remote_output_dir = str(old / "out")
    p._runner._run.reattached = True
    assert p._runner.host.remote_scratch_dir == ""
    (old / "out").mkdir(parents=True)
    if checkpoint:
        (settings,) = _settings_stamps(p._pipeline_argv)[:1]
        ours = old / "out" / f"_tmp_images_{_output_stamp(old / 'out')}_{settings}_A03"
        ours.mkdir()
        (ours / ".fov_exp_A03_F001.done").touch()
        (ours / ".fov_exp_A03_F002.done").touch()
    _run(p, monkeypatch)
    return "".join(p._logged), (old / "out").resolve()


def test_with_no_scratch_folder_set_the_resume_looks_where_the_pipeline_kept_its_checkpoints(tmp_path, monkeypatch):
    """With no scratch folder set, a remote run's scratch is its own output folder (or a Slurm node's own disk) —
    never ``~/.allwell/scratch``, which is where the probe looked, so a restart said every part-way well would start
    again while the checkpoints sat in the folder it had just re-attached."""
    log, out = _restart_with_no_scratch_folder_set(tmp_path, monkeypatch, checkpoint=True)
    assert (f"[remote] 1 well(s) left part-way will continue from their checkpoints in {out}: "
            "A03 (2 field group(s) done)\n") in log, log


def test_with_none_found_the_line_says_a_node_s_own_disk_cannot_be_seen(tmp_path, monkeypatch):
    log, out = _restart_with_no_scratch_folder_set(tmp_path, monkeypatch, checkpoint=False)
    assert (f"[remote] no part-way checkpoints for this plate in {out} (a Slurm job that used its node's own disk "
            "kept its checkpoints there, which cannot be seen from here) — wells left part-way start again.\n") in log, log


def test_the_job_gets_its_first_batch_before_the_earlier_attempt_s_wells_download(tmp_path, monkeypatch):
    """The user's order: paint every well's state, start the job, then drain what is waiting to download — so the
    transfer fills the queue wait and the compute instead of delaying the job."""
    p, plate, run_dir, sent, _, _ = _harness(tmp_path, monkeypatch)
    _finished_on(run_dir / "out", "A02")
    _uploaded_copy(plate, run_dir / "in", "A03")
    order: list[str] = []
    real_ssh = p._runner._cache._ssh_run

    def ssh_run(argv, *, check, **kw):
        if argv and argv[0] == "touch" and "ready" in argv[-1]:
            order.append(f"batch handed: {Path(argv[-1]).name}")
        return real_ssh(argv, check=check, **kw)

    p._runner._cache._ssh_run = ssh_run
    p._fetcher.observe = lambda line: order.append(f"download {line.split()[1]}")
    real_put = p._line_q.put
    p._line_q.put = lambda item: (order.append(f"painted {item.split()[1]}")
                                  if item and "finished on the remote by an earlier attempt" in item else None,
                                  real_put(item))[1]

    _run(p, monkeypatch)

    first_batch = next(i for i, e in enumerate(order) if e.startswith("batch handed"))
    assert order.index("painted A02") < first_batch, order
    assert order.index("download A02") > first_batch, f"the download did not wait for the job: {order}"
    assert any("↑ A03.zip already on the remote" in m for m in p._logged), "a reused input is not painted uploaded"


def test_a_resume_does_not_send_the_empty_well_blank_again(tmp_path, monkeypatch):
    """The blank lives in the run directory's bg/; a resume in a new run directory found its bg/ empty and sent
    the blank again — for a background well, a whole well's upload. The earlier attempt's copy is linked over."""
    import tests.test_a_batched_restart_reuses_the_remote as here

    monkeypatch.setattr(here, "WELLS", ["A01", "A02", "H12"])
    old = tmp_path / "remote" / "runs" / "run-old"
    earlier = [{"remote_output_dir": str(old / "out"), "remote_input_dir": str(old / "in")}]
    p, plate, run_dir, sent, _, _ = _harness(tmp_path, monkeypatch, earlier=earlier)
    p._pipeline_argv = ["--background", "empty_well", "--background_well", "H12"]
    _uploaded_copy(plate, old / "bg", "H12")
    (old / "out").mkdir(parents=True)

    _run(p, monkeypatch)

    assert p._returncode == 0, p._logged
    assert not any(s.endswith("H12.zip") for s in sent), f"the blank was uploaded again: {sent}"
    assert (run_dir / "bg" / "H12.zip").exists()
    assert (old / "bg" / "H12.zip").exists(), "an earlier attempt's directory is linked from, never emptied"


def test_the_node_is_requested_before_the_empty_well_blank_uploads(tmp_path, monkeypatch):
    """The blank is a whole well. Uploaded before the driver was launched, it held the node request behind its
    transfer, so a slow link left the run with no job asked for at all; the driver reads it only when batch 1 is
    handed over, which is after it lands."""
    import tests.test_a_batched_restart_reuses_the_remote as here

    monkeypatch.setattr(here, "WELLS", ["A01", "A02", "H12"])
    p, plate, run_dir, sent, _, _ = _harness(tmp_path, monkeypatch)
    p._pipeline_argv = ["--background", "empty_well", "--background_well", "H12"]
    order: list[str] = []
    upload = p._runner._cache.upload_subset

    def recording_upload(zips, dest, **kw):
        order.append(f"upload {Path(dest).name}")
        return upload(zips, dest, **kw)

    p._runner._cache.upload_subset = recording_upload
    proc = __import__("tests.test_batch_driver", fromlist=["_FakeProc"])._FakeProc()

    def launch(argv, **kw):
        order.append("node requested")
        return proc

    monkeypatch.setattr("subprocess.Popen", launch)
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    for i in range(1, 4):
        proc.batch_finished(p, i)
    proc.finish()
    worker.join(timeout=30)

    assert p._returncode == 0, p._logged
    assert order[0] == "node requested" and "upload bg" in order, order
    assert order.index("node requested") < order.index("upload bg") < order.index("upload b1"), order


def test_the_driver_is_told_where_earlier_attempts_kept_the_blank(tmp_path, monkeypatch):
    """The blank's path is part of the settings digest, and it moves with the run directory. Unless the pipeline
    is told the earlier attempts' paths it reads their part-way checkpoints as another configuration's and prunes
    them — the reported restart that began a 25%-done well again."""
    import tests.test_a_batched_restart_reuses_the_remote as here
    from services.remote_commands import unpack_login_shell_command

    monkeypatch.setattr(here, "WELLS", ["A01", "A02", "H12"])
    p, plate, run_dir, sent, _, _ = _harness(tmp_path, monkeypatch)
    p._pipeline_argv = ["--background", "empty_well", "--background_well", "H12"]
    old = tmp_path / "remote" / "runs" / "run-old"
    monkeypatch.setattr(run_journal, "recorded_remote_attempts", lambda *a, **k: [
        {"remote_input_dir": str(old / "in"), "remote_output_dir": str(run_dir / "out")}])
    proc = _FakeProc()
    launched: list = []
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: launched.append(argv) or proc)
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    for i in range(1, 4):
        proc.batch_finished(p, i)
    proc.finish()
    worker.join(timeout=30)

    assert p._returncode == 0, p._logged
    script = unpack_login_shell_command(launched[0][-1])
    assert f"--background_dir_alias={old}/bg" in script, "the earlier attempt's blank path was not passed"
    assert f"--background_dir_alias={run_dir}/bg" not in script, "this run's own path is not an alias"


# ── a well the remote left part way is not a finished well ──────────────────────────────────────────────────────

def _left_part_way_on(out_dir: Path, well: str) -> None:
    """What a well suspended part way leaves: the CSV and ``<well>_out.zip`` of the groups it finished, and no
    completion seal (``tests/test_a_part_way_well_is_not_sealed.py`` pins that the pipeline leaves exactly this)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{well}_out.zip").write_bytes(b"the groups it reached")
    (out_dir / f"measurements_{well}.csv.gz").write_bytes(b"some rows")


@pytest.mark.parametrize("where", ["re-attached", "earlier attempt"])
def test_a_well_the_remote_left_part_way_is_run_again_not_adopted(tmp_path, monkeypatch, where):
    """The reviewed case: the app dies, the mid-batch watchdog suspends the pipeline, and the wells in flight write
    their CSV and out.zip from the groups they finished. A Resume must not take that output as the well's whole —
    it must send the well and run it, so its missing groups are computed."""
    old = tmp_path / "remote" / "runs" / "run-old"
    earlier = [{"remote_output_dir": str(old / "out"), "remote_input_dir": str(old / "in")}]
    p, plate, run_dir, sent, observed, _ = _harness(
        tmp_path, monkeypatch, earlier=earlier if where == "earlier attempt" else ())
    if where == "re-attached":
        p._runner._run.remote_output_dir = str(old / "out")
    _finished_on(old / "out", "A01")
    _left_part_way_on(old / "out", "A02")

    _run(p, monkeypatch)

    assert p._returncode == 0, p._logged
    assert [line.split()[1] for line in observed] == ["A01"], (
        f"a well left part way on the remote was adopted as finished: {observed}")
    assert "b1/A02.zip" in sent, f"the part-way well was not sent to be run again: {sent}"
    assert any("A02" in m and "part way" in m for m in p._logged), "nothing says why A02 is run again"
    # Tracker B1: sending it is half; the host writing into the re-attached directory must not then skip it as
    # complete. The app leaves the withdrawal note the host's skip reads (`run_report.finished_on_this_host`).
    from pipeline.run_report import finished_on_this_host, withdrawn_note_path

    note = withdrawn_note_path(old / "out", "A02")
    assert note.exists() is (where == "re-attached"), (
        f"{where}: the note is for the directory the host writes into, and only that one")
    if where == "re-attached":
        assert not finished_on_this_host(old / "out", "A02"), "the host would still skip the part-way well"


# ── the incident: a Resume that forces a well must still find the attempt it resumes (tracker F2) ──────────────

@pytest.mark.parametrize("recorded_by", ["this build", "the build before"])
def test_a_resume_that_forces_a_well_takes_over_the_attempt_it_resumes(tmp_path, monkeypatch, recorded_by):
    """Incident 1's shape. The first attempt finished batches 1 and 2 on the remote (A01–A04, sealed, not yet
    downloaded when the run stopped), uploaded batch 3 (A05, A06) to its ``b3`` and stopped. The Resume names A04
    in ``--force_wells`` — the user's Force wells, or the runner's own ``nanowell_forced`` for a well with partial
    tables here. ``--force_wells`` was part of the run's digest, so the Resume could not find the attempt it
    resumed: every finished well was recomputed and both uploaded archives sent again. Now the force list is left
    out of the digest, the attempt is found, A01–A03 are taken from it, A04 alone is run again (not adopted), and
    nothing already on the remote is uploaded."""
    from services import run_journal as J
    from services.remote_finished_wells import argv_forcing
    from services.remote_run_id import legacy_argv_digests, pipeline_argv_digest

    old = tmp_path / "remote" / "runs" / "run-old"
    p, plate, run_dir, sent, observed, _ = _harness(tmp_path, monkeypatch)
    monkeypatch.undo()                                  # the real stored_remote_addresses, not the stub
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    out = p._local_output_dir
    first = ["python", "process_microscopy.py", "--input_dir", str(plate), "--output_dir", str(out),
             "--csv_prefix", "measurements", "--nuclear_token", "DAPI"]
    resume = argv_forcing(first, ["A04"])
    recorded = (pipeline_argv_digest(first) if recorded_by == "this build"
                else __import__("services.remote_run_id", fromlist=["x"]).earlier_argv_digest(first))
    J.open_run(out, spec={}, mode="remote-batched", input_dir=str(plate), argv=first)
    J.record_remote(out, run_id="run-old", cache_key="k-old", host_alias="h", remote_input_dir=str(old / "in"),
                    remote_output_dir=str(old / "out"), argv_digest=recorded, source_key="s")
    J.open_run(out, spec={}, mode="remote-batched", input_dir=str(plate), argv=resume)   # the Resume
    p._pipeline_argv = resume
    p._journal_keys = dict(argv_digest=pipeline_argv_digest(resume), source_key="s", host_alias="h")
    p._legacy_digests = legacy_argv_digests(resume)
    for well in ("A01", "A02", "A03", "A04"):
        _finished_on(old / "out", well)
    _uploaded_copy(plate, old / "in", "A04")            # batch 2's input, swapped in and never cleared
    for well in ("A05", "A06"):
        _uploaded_copy(plate, old / "b3", well)

    _run(p, monkeypatch)

    assert p._returncode == 0, p._logged
    assert sorted(line.split()[1] for line in observed) == ["A01", "A02", "A03"], (
        f"the Resume did not take the earlier attempt's finished wells, or took the forced one: {observed}")
    assert sent == [], f"archives the earlier attempt had already uploaded were sent again: {sent}"


# ── every attempt is kept, though several wrote into one output directory (tracker F5) ─────────────────────────

def _three_attempts(tmp_path, monkeypatch):
    """Attempt 1 in ``run-1``; attempts 2 and 3 each in a run directory of their own, re-attached to ``run-1/out``
    (``reattach_output_dir``): the history a twice-resumed batched run leaves, recorded through the real journal."""
    from services import run_journal as J

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    out = tmp_path / "local-out"
    out.mkdir(exist_ok=True)
    root = tmp_path / "remote" / "runs"
    for n in (1, 2, 3):
        J.open_run(out, spec={}, mode="remote-batched", input_dir="/in", argv=["--x"])
        J.record_remote(out, run_id=f"run-{n}", cache_key=f"k{n}", host_alias="h",
                        remote_input_dir=str(root / f"run-{n}" / "in"), remote_output_dir=str(root / "run-1" / "out"),
                        argv_digest="d", source_key="s")
    return out, root


def test_the_journal_keeps_every_attempt_that_shared_an_output_directory(tmp_path, monkeypatch):
    from services import run_journal as J

    out, root = _three_attempts(tmp_path, monkeypatch)
    attempts = J.recorded_remote_attempts(out, argv_digests=["d"], source_key="s", host_alias="h")
    assert [Path(a["remote_input_dir"]).parent.name for a in attempts] == ["run-3", "run-2", "run-1"], (
        "an attempt that re-attached to the same output directory as a later one was dropped from the journal, so "
        f"its staged archives, its held wells and its blank's path are lost: {attempts}")


def test_a_resume_takes_the_staged_batches_of_an_intermediate_attempt(tmp_path, monkeypatch):
    """Attempt 2 staged batch 2 in its own ``b2`` and stopped; attempt 3 is this run. Its archives are on the remote,
    and the journal is the only record of where."""
    out, root = _three_attempts(tmp_path, monkeypatch)
    p, plate, run_dir, sent, observed, _ = _harness(tmp_path, monkeypatch)
    monkeypatch.undo()
    from services import run_journal as J

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    p._local_output_dir = out
    p._journal_keys = dict(argv_digest="d", source_key="s", host_alias="h")
    p._legacy_digests = ()
    p._runner._run.remote_output_dir = str(root / "run-1" / "out")          # re-attached, as attempts 2 and 3 were
    for well in ("A03", "A04"):
        _uploaded_copy(plate, root / "run-2" / "b2", well)

    _run(p, monkeypatch)

    assert p._returncode == 0, p._logged
    assert not {"b2/A03.zip", "b2/A04.zip"} & set(sent), (
        f"the intermediate attempt's uploaded batch was sent again: {sent}")
