"""A batched run that dies before its node boots does not re-send its uploads.

Batched remote mode launches the driver (`srun …`) first and uploads batch 1
into ``<run_dir>/b1`` while the scheduler is still finding a node. When the node
then fails to boot — ``srun: error: Nodes … are still not ready`` / ``Something
is wrong with the boot of the nodes`` — nothing has touched those wells. The
retry used to begin with ``rm -rf <run_dir>/b1 … b<N> <run_dir>/bg`` and upload
the whole of batch 1, and the empty-well reference, all over again.

The run directory is keyed on the name, size and mtime of every staged archive,
so whatever an earlier attempt left in it under a well's name is that well's
archive. These tests drive the app's own call sequence — attempt, dead driver,
attempt — against a local directory standing in for the remote, with an upload
stub that behaves like rsync's quick check (skip a file whose size and mtime
already match). The remote shell commands themselves run for real, under
``bash``, against that directory.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import types
from pathlib import Path

from services.remote_batch_staging import prune_staged
from services.remote_transport import _remote_join
from tests.test_batch_driver import _batched, _FakeProc

_REAL_POPEN = subprocess.Popen


def _remote(p, proc, remote_root: Path, sent: list[str]):
    """Give *p* a runner whose 'remote' is the local directory *remote_root*."""

    def ssh_run(argv, *, check, **kw):
        # `subprocess.Popen` is patched to hand back the fake driver, so the
        # real one is captured at import and used directly here.
        child = _REAL_POPEN(["bash", "-c", _remote_join(argv)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
        out, err = child.communicate(timeout=20)
        return subprocess.CompletedProcess(argv, child.returncode, out, err)

    def upload_subset(zips, dest, **kw):
        # rsync's quick check: a destination file whose size and mtime match
        # the source is not transferred. `-a` preserves the mtime, as copy2 does.
        d = Path(dest)
        d.mkdir(parents=True, exist_ok=True)
        for z in zips:
            target = d / z.name
            st = z.stat()
            if (target.exists() and target.stat().st_size == st.st_size
                    and int(target.stat().st_mtime) == int(st.st_mtime)):
                continue
            shutil.copy2(z, target)
            sent.append(f"{d.name}/{z.name}")
        return (0, 0.0, 0)

    run_dir = remote_root / "runs" / "run-test"
    p._runner = types.SimpleNamespace(
        _cache=types.SimpleNamespace(_ssh_run=ssh_run,
                                     upload_subset=upload_subset),
        _run=types.SimpleNamespace(
            remote_input_dir=str(run_dir / "in"),
            remote_output_dir=str(run_dir / "out"),
            remote_pidfile=str(run_dir / "run.pid")),
        host=types.SimpleNamespace(
            remote_staging_dir=str(remote_root),
            compute_launcher="srun --time=1:00:00",
            remote_scratch_dir="", ssh_argv=lambda **kw: ["ssh", "h"]),
        env_selector=("name", "allwell"),
        _build_remote_argv=lambda *a, **k: ["python", "pm.py"],
        _sweep_orphaned_allocation=lambda *a, **k: None,
        close_remote_job=lambda **kw: None,
    )
    return run_dir


def _attempt(tmp_path, monkeypatch, remote_root, *, node_boots: bool):
    """One run of a three-well plate in batches of two."""
    proc = _FakeProc()
    sent: list[str] = []
    p = _batched(tmp_path, ["A01", "A02", "A03"], batch_size=2)
    # `_batched` writes the archives afresh on every call, which would give the
    # second attempt new mtimes. A changed archive is a different plate — the
    # run directory is keyed on the mtimes — so the test pins them: a retry of
    # the same plate offers the same files, as it does in the app.
    for z in p._zipped.well_zips:
        os.utime(z, (1_700_000_000, 1_700_000_000))
    run_dir = _remote(p, proc, remote_root, sent)
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: proc)

    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    if node_boots:
        proc.say("[remote] batch-driver: batch 1 finished rc=0\n")
        proc.say("[remote] batch-driver: batch 2 finished rc=0\n")
    else:
        # The upload of batch 1 completes while srun waits on the node; then
        # the boot fails and the driver's stream ends with nothing reported.
        for _ in range(200):
            if (run_dir / "b1" / "A02.zip").exists():
                break
            threading.Event().wait(0.05)
    proc.finish()
    worker.join(timeout=30)
    assert not worker.is_alive()
    return p, sent, run_dir


def test_a_retry_after_a_failed_boot_does_not_resend_batch_one(
        tmp_path, monkeypatch):
    remote_root = tmp_path / "remote"
    inputs = tmp_path / "plate"
    inputs.mkdir()

    p1, sent1, run_dir = _attempt(inputs, monkeypatch, remote_root,
                                  node_boots=False)
    assert sent1 == ["b1/A01.zip", "b1/A02.zip"], sent1
    assert p1._returncode not in (0, None), "the dead node must not read as success"

    p2, sent2, _ = _attempt(inputs, monkeypatch, remote_root, node_boots=True)
    assert p2._returncode == 0
    assert sent2 == ["b2/A03.zip"], (
        f"the retry re-sent wells the failed attempt had already uploaded: "
        f"{sent2}")
    assert any("2 of 2 well-zip(s) are already on the remote" in m
               for m in p2._logged), "the reuse must be visible in the log"


def test_a_kept_batch_directory_is_emptied_of_other_batches_wells(
        tmp_path, monkeypatch):
    """The driver's ``mv b<i> in`` hands the pipeline the whole directory, so a
    well left there by an attempt that sliced the plate differently would be
    processed in the wrong batch, and then again in its own."""
    remote_root = tmp_path / "remote"
    inputs = tmp_path / "plate"
    inputs.mkdir()
    stale = remote_root / "runs" / "run-test" / "b1"
    stale.mkdir(parents=True)
    (stale / "A03.zip").write_bytes(b"from another slicing")
    (stale / "leftover-dir").mkdir()
    (stale / "leftover-dir" / "x").write_bytes(b"x")

    p, sent, run_dir = _attempt(inputs, monkeypatch, remote_root,
                                node_boots=True)

    assert p._returncode == 0
    assert sorted(f.name for f in (run_dir / "b1").iterdir()) == [
        "A01.zip", "A02.zip"]
    assert sent == ["b1/A01.zip", "b1/A02.zip", "b2/A03.zip"]


def test_an_interrupted_batch_already_swapped_in_is_still_cleared(
        tmp_path, monkeypatch):
    """``in/`` is a batch the driver had started on; nothing records which of
    its wells finished, so it is the one directory a retry does not keep."""
    remote_root = tmp_path / "remote"
    inputs = tmp_path / "plate"
    inputs.mkdir()
    half_done = remote_root / "runs" / "run-test" / "in"
    half_done.mkdir(parents=True)
    (half_done / "A01.zip").write_bytes(b"half processed")

    _attempt(inputs, monkeypatch, remote_root, node_boots=False)

    assert not half_done.exists()


def test_a_prune_that_fails_falls_back_to_clearing_the_directory(tmp_path):
    """Any answer but a clean listing ends in ``rm -rf``: a re-send costs time,
    a stray well in a batch costs a duplicate result."""
    calls: list[list[str]] = []

    def ssh_run(argv, *, check, **kw):
        calls.append(list(argv))
        return types.SimpleNamespace(stdout="", returncode=124)

    logged: list[str] = []
    prune_staged(types.SimpleNamespace(_ssh_run=ssh_run), "/r/runs/run-test/b1",
                 [tmp_path / "A01.zip"], "batch 1", logged.append)

    assert calls[-1] == ["rm", "-rf", "/r/runs/run-test/b1"]
    assert not logged, "nothing was found, so nothing may be reported as reused"
