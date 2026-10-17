"""A batched nanowell run frees each batch's wells on the remote once the batch has ended.

The re-anchoring at the end of each pipeline invocation re-runs only that invocation's wells, and reads nothing
of the other wells but their grid records under ``nanowells/``. So once a batch's pipeline has exited, its wells'
input archives, output archives and cell CSVs are never read again on the remote. Holding them until the run
ended — what a nanowell run did — made the batch size bound nothing: a 60-well plate of 8 GB inputs needed the
whole plate's input and output on the remote at once.

Three layers, each driven the way the app drives it:

* :func:`release_batch` — what is pulled again, what is deleted, what is kept;
* ``Fetcher.after_queued`` — the release runs on the Fetcher's worker *behind* every well already queued, which is
  what guarantees each well's first download was attempted before its remote copy goes;
* ``_BatchedRemoteProcess._run_batches`` — the loop hands each batch's wells to the release after the batch ends.
"""

from __future__ import annotations

import json
import subprocess
import threading
import unittest.mock as mock
from pathlib import Path
from types import SimpleNamespace

import services.remote_transport as transport
from services.remote_fetch_nanowells import pull_nanowell_results, release_batch


def _grid(folder: Path, well: str, methods) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{well}_grid.json").write_text(json.dumps(
        {"well": well, "timepoints": {f"{i}h": {"status": "ok", "origin_method": m} for i, m in enumerate(methods)}}))


class _Fetcher:
    """The surface ``release_batch`` uses: the run record, the host, listing and pulling."""

    def __init__(self, local: Path, *, fetched, remote_files, short_pull=()):
        self.run = SimpleNamespace(
            nanowell=True, local_output_dir=local, remote_input_dir="/r/in", remote_output_dir="/r/out",
            fetched_wells=set(fetched), nanowell_released=set())
        self.host = SimpleNamespace(ssh_alias="gpu")
        self.remote_files = set(remote_files)      # names under /r/out (and nanowells/<name>)
        self.short_pull = set(short_pull)          # globs whose pull lands nothing
        self.pulled: list[str] = []

    def _list_remote_glob(self, src):
        name = src.rsplit("/", 1)[1]
        if "*" in name:
            suffix = name.split("*", 1)[1]
            hits = [f"/r/out/{n}" for n in sorted(self.remote_files) if n.endswith(suffix)]
        else:
            hits = [f"/r/out/{name}"] if name in self.remote_files or "nanowells" in src else []
        return "gpu", hits, None

    def _pull_listed(self, host, files, dest):
        self.pulled += files
        return len(files)

    def _rsync_glob(self, src_glob, dest_dir):
        host, files, _ = self._list_remote_glob(src_glob)
        if src_glob.split(":", 1)[1] in self.short_pull:
            return len(files), 0
        return len(files), self._pull_listed(host, files, dest_dir)


def _capture_deletes(monkeypatch) -> list[list[str]]:
    deleted: list[list[str]] = []
    monkeypatch.setattr(transport, "delete_remote_paths",
                        lambda host, paths, **k: deleted.append(list(paths)))
    return deleted


def test_a_finished_batch_s_wells_are_deleted_and_the_re_anchored_one_pulled_again(tmp_path, monkeypatch):
    nanowells = tmp_path / "nanowells"
    _grid(nanowells, "A01", ["fiducial", "fiducial"])
    _grid(nanowells, "B02", ["fiducial", "plate_rigid"])          # this batch's re-anchoring re-ran it
    _grid(nanowells, "Z99", ["plate_rigid"])                       # an earlier batch's well: not this batch's
    deleted = _capture_deletes(monkeypatch)
    fetcher = _Fetcher(tmp_path, fetched={"A01", "B02"},
                       remote_files={"B02_out.zip", "measurements_B02.csv.gz"})
    lines: list[str] = []

    released = release_batch(fetcher, ["A01", "B02", "C03"], log=lines.append)

    assert released == ["A01", "B02"]
    assert deleted == [
        ["/r/in/A01.zip", "/r/out/A01_out.zip", "/r/out/*_A01.csv", "/r/out/*_A01.csv.gz"],
        ["/r/in/B02.zip", "/r/out/B02_out.zip", "/r/out/*_B02.csv", "/r/out/*_B02.csv.gz"],
    ], "C03 never landed locally, so its remote copy is the only one and must stay"
    assert not any("nanowells" in p for paths in deleted for p in paths), (
        "every later batch's plate fit reads the grid records in nanowells/")
    assert "/r/out/B02_out.zip" in fetcher.pulled and "/r/out/measurements_B02.csv.gz" in fetcher.pulled
    assert "/r/out/Z99_out.zip" not in fetcher.pulled, "an earlier batch's well is not this batch's to pull"
    assert "/r/out/A01_out.zip" not in fetcher.pulled, "a well the re-anchoring did not re-run is not pulled again"
    assert fetcher.run.nanowell_released == {"A01", "B02"}
    assert "freed the remote copies of 2 nanowell well(s), 1 pulled again" in "".join(lines)


def test_a_re_anchored_well_whose_second_pull_fails_is_kept(tmp_path, monkeypatch):
    _grid(tmp_path / "nanowells", "B02", ["plate_rigid"])
    deleted = _capture_deletes(monkeypatch)
    fetcher = _Fetcher(tmp_path, fetched={"B02"}, remote_files={"B02_out.zip"}, short_pull={"/r/out/B02_out.zip"})
    lines: list[str] = []

    assert release_batch(fetcher, ["B02"], log=lines.append) == []
    assert deleted == [], "the remote copy is the only current one once the local pull failed"
    assert fetcher.run.nanowell_released == set()
    assert "keeping it on the remote" in "".join(lines)


def test_a_well_is_released_once(tmp_path, monkeypatch):
    deleted = _capture_deletes(monkeypatch)
    fetcher = _Fetcher(tmp_path, fetched={"A01"}, remote_files=set())
    release_batch(fetcher, ["A01"], log=lambda _m: None)
    release_batch(fetcher, ["A01"], log=lambda _m: None)
    assert len(deleted) == 1


def test_a_run_that_is_not_nanowell_is_left_to_the_per_well_delete(tmp_path, monkeypatch):
    deleted = _capture_deletes(monkeypatch)
    fetcher = _Fetcher(tmp_path, fetched={"A01"}, remote_files=set())
    fetcher.run.nanowell = False
    assert release_batch(fetcher, ["A01"], log=lambda _m: None) == []
    assert deleted == [] and fetcher.pulled == []


def test_verification_does_not_pull_a_released_well_again(tmp_path):
    """Its files are gone from the remote; asking for them only prints a 'not pulled' note per file."""
    folder = tmp_path / "nanowells"
    _grid(folder, "A01", ["plate_rigid"])
    _grid(folder, "B02", ["plate_rigid"])
    pulled: list[str] = []
    verifier = SimpleNamespace(
        _run=SimpleNamespace(local_output_dir=tmp_path, remote_output_dir="/r/out", nanowell=True,
                             nanowell_released={"A01"}),
        host=SimpleNamespace(ssh_alias="gpu"),
        _rsync_optional=lambda src, dest, *, log_fn, absent_note="": pulled.append(src))
    pull_nanowell_results(verifier, log=lambda _m: None)
    assert pulled == ["gpu:/r/out/nanowells/", "gpu:/r/out/B02_out.zip", "gpu:/r/out/*_B02.csv.gz"]


# ── the Fetcher runs the release behind the wells already queued ─────────────

def test_after_queued_runs_behind_every_well_already_queued():
    from services.remote_fetch import Fetcher

    order: list[str] = []
    gate = threading.Event()
    with mock.patch.object(Fetcher, "_fetch_one", lambda self, well: (gate.wait(5), order.append(well))):
        fetcher = Fetcher(SimpleNamespace(ssh_alias="h"), SimpleNamespace(expected_wells=set(),
                          well_count_total=0, fetched_wells=set()),
                          output_filter=None, log_fn=lambda _m: None)
        try:
            fetcher.observe("2026-01-01 00:00:00  INFO  Well A01 — done.\n")
            fetcher.observe("2026-01-01 00:00:00  INFO  Well B02 — done.\n")
            fetcher.after_queued(lambda f: order.append(f"release-by-{type(f).__name__}"))
            gate.set()
            fetcher.flush_and_join(timeout=5)
        finally:
            fetcher.cancel()
    assert order == ["A01", "B02", "release-by-Fetcher"]


# ── the batch loop hands each batch's wells to the release ───────────────────

def _drive_batches(tmp_path, wells, *, nanowell, batch_size):
    from services.remote_commands import unpack_login_shell_command
    from tests.test_batched_empty_well_correction import _batched, _FakeProc, _stub_runner

    queued: list = []
    proc = _FakeProc()
    p = _batched(tmp_path, wells, batch_size=batch_size)
    p._fetcher.run.nanowell = nanowell
    p._fetcher.after_queued = queued.append
    _stub_runner(p, proc, lambda zips, dest, **kw: (0, 0.0, 0))
    p._pipeline_argv = []
    (tmp_path / "out").mkdir(exist_ok=True)
    p._local_output_dir = tmp_path / "out"
    with mock.patch.object(subprocess, "Popen", lambda a, **k: (unpack_login_shell_command(a[-1]), proc)[1]):
        worker = threading.Thread(target=p._run_batches, daemon=True)
        worker.start()
        for i in range(1, 7):
            proc.say(f"[remote] batch-driver: batch {i} finished rc=0\n")
        proc.finish()
        worker.join(timeout=20)
    return p, queued


def test_each_finished_batch_releases_exactly_its_own_wells(tmp_path):
    p, queued = _drive_batches(tmp_path, ["A01", "A02", "A03"], nanowell=True, batch_size=2)
    assert p._returncode == 0, p._logged
    assert [task.keywords["wells"] for task in queued] == [["A01", "A02"], ["A03"]]
    assert all(task.func is release_batch for task in queued)


def test_a_run_that_is_not_nanowell_queues_no_release(tmp_path):
    p, queued = _drive_batches(tmp_path, ["A01", "A02"], nanowell=False, batch_size=1)
    assert p._returncode == 0, p._logged
    assert queued == []
