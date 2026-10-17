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
    p._fetcher.observe = observed.append
    p._fetcher.after_queued = queued.append
    p._fetcher.run.nanowell = nanowell
    p._journal_keys = dict(argv_digest="d", source_key="s", host_alias="h")
    p._runner = types.SimpleNamespace(
        _cache=types.SimpleNamespace(_ssh_run=lambda argv, *, check, **kw: _bash(argv, script=False),
                                     _ssh_script=lambda script, *, check: _bash(script, script=True),
                                     upload_subset=upload_subset),
        _run=types.SimpleNamespace(remote_input_dir=str(run_dir / "in"), remote_output_dir=str(run_dir / "out"),
                                   remote_pidfile=str(run_dir / "run.pid")),
        host=types.SimpleNamespace(remote_staging_dir=str(remote_root), compute_launcher="srun --time=1:00:00",
                                   remote_scratch_dir="", ssh_argv=lambda **kw: ["ssh", "h"]),
        env_selector=("name", "allwell"),
        _build_remote_argv=lambda *a, **k: ["python", "pm.py"],
        _sweep_orphaned_allocation=lambda *a, **k: None,
        close_remote_job=lambda **kw: None,
    )
    p._pipeline_argv = ["--force"] if force else []
    monkeypatch.setattr(run_journal, "stored_remote_addresses", lambda *a, **k: list(earlier))
    return p, plate, run_dir, sent, observed, queued


def _run(p, monkeypatch):
    proc = _FakeProc()
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: proc)
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    for i in range(1, 5):
        proc.say(f"[remote] batch-driver: batch {i} finished rc=0\n")
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
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{well}_out.zip").write_bytes(b"finished output")
    (out_dir / f"measurements_{well}.csv.gz").write_bytes(b"rows")


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
        "A01_out.zip", "A02_out.zip", "measurements_A01.csv.gz", "measurements_A02.csv.gz"], (
        "the finished wells were not brought into this run's directory for the Fetcher")
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
        proc.say(f"[remote] batch-driver: batch {i} finished rc=0\n")
    proc.finish()
    worker.join(timeout=30)

    assert p._returncode == 0, p._logged
    assert order[0] == "node requested" and "upload bg" in order, order
    assert order.index("node requested") < order.index("upload bg") < order.index("upload b1"), order
