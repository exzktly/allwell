"""A remote nanowell run reuses the tile registration this computer already holds for its wells.

Reported: re-running a well on a remote host registered every tile again although the well had been registered
before. The host's output folder is a fresh one and the Reuse registration folder is on this computer, so the host
had nothing to reuse. ``services.remote_registration`` now uploads each sent well's grid record and the host's run is
given ``--nanowell_registration_from`` naming it.

Wrong implementations these reject: sending nothing (the reported behaviour); sending a record for a well the run
does not send, or the output folder's record ahead of the Reuse registration folder's (the pipeline reads them in
the other order); passing the local folder through to the host; naming a host folder when the upload failed; a
standard (non-nanowell) run sending anything; and a constructor that never wires it.
"""

from __future__ import annotations

import types
from pathlib import Path

from services.remote_registration import local_grids, send_registration


def _grid(folder: Path, well: str, text: str = "{}") -> Path:
    path = folder / "nanowells" / f"{well}_grid.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _argv(out: Path, reg: Path | None = None, nanowell: bool = True) -> list[str]:
    argv = ["/py", "/pm.py", "--input_dir", "/in", "--output_dir", str(out)]
    if nanowell:
        argv += ["--nanowell_format", "100um"]
    if reg is not None:
        argv += ["--nanowell_registration_from", str(reg)]
    return argv


def test_each_sent_well_takes_the_first_local_folder_that_has_its_record(tmp_path):
    out, reg = tmp_path / "out", tmp_path / "earlier"
    a01_reg = _grid(reg, "A01")
    _grid(out, "A01")                                   # read after the Reuse registration folder, as the pipeline does
    a02_out = _grid(out, "A02")
    _grid(out, "A03")                                   # not sent by this run
    a01_masks = out / "nanowells" / "A01_masks.npz"     # chosen on its own: the earlier folder has none
    a01_masks.write_bytes(b"npz")
    assert local_grids(_argv(out, reg), ["A02", "A01", "B01"]) == [a01_reg, a01_masks, a02_out]
    assert local_grids(_argv(out), ["A01"]) == [out / "nanowells" / "A01_grid.json", a01_masks]
    assert local_grids(_argv(out, nanowell=False), ["A01"]) == []


class _Cache:
    def __init__(self, rc=0):
        self.rc, self.ssh, self.sent = rc, [], []

    def _ssh_run(self, argv, *, check, **kw):
        self.ssh.append(list(argv))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def upload_subset(self, files, dest, **kw):
        self.sent.append((list(files), dest, kw))
        return (100, 0.0, self.rc)


def test_the_records_go_to_the_run_s_own_folder_and_a_failed_upload_names_none(tmp_path):
    out = tmp_path / "out"
    grid = _grid(out, "A01")
    cache, logged = _Cache(), []
    assert send_registration(cache, "~/.st/runs/run-x", _argv(out), ["A01"], logged.append) == "~/.st/runs/run-x/reg"
    assert cache.ssh == [["mkdir", "-p", "~/.st/runs/run-x/reg/nanowells"]]
    assert cache.sent[0][:2] == ([grid], "~/.st/runs/run-x/reg/nanowells")
    assert "log_fn" not in cache.sent[0][2], "a grid record logged as an upload reads as a well to the progress view"
    assert "stored registration sent for 1 well(s), cell masks for 0" in "".join(logged)
    failed = _Cache(rc=23)
    assert send_registration(failed, "/r", _argv(out), ["A01"], logged.append) == ""
    assert "could not be sent (rsync rc=23)" in logged[-1]
    nothing = _Cache()
    assert send_registration(nothing, "/r", _argv(out), ["B01"], logged.append) == ""
    assert nothing.ssh == [] and nothing.sent == []


def test_the_host_s_argv_names_the_sent_folder_and_never_the_local_one():
    from services.remote_config import RemoteHost
    from services.remote_runner import LocalInputMode, OutputFilter, RemoteRunner

    local = _argv(Path("/local/out"), Path("/local/earlier"))
    runner = RemoteRunner(RemoteHost(name="t", ssh_alias="t"))
    runner._run = types.SimpleNamespace(registration_dir="~/.st/runs/run-x/reg")
    remote = runner._build_remote_argv(local, "/r/in", "/r/out", OutputFilter.FULL, LocalInputMode.ZIP)
    assert "/local/earlier" not in remote
    assert remote[remote.index("--nanowell_registration_from") + 1] == "~/.st/runs/run-x/reg"
    runner._run = types.SimpleNamespace(registration_dir="")
    remote = runner._build_remote_argv(local, "/r/in", "/r/out", OutputFilter.FULL, LocalInputMode.ZIP)
    assert "--nanowell_registration_from" not in remote


def test_the_batched_constructor_sends_the_records_and_records_the_folder(tmp_path, monkeypatch):
    """The real ``_BatchedRemoteProcess.__init__``: the run's own wells' records go up, and the folder is what the
    host's argv will name."""
    import services.remote_process as rp

    out = tmp_path / "out"
    grid = _grid(out, "A01")
    zips = [tmp_path / "A01.zip"]
    monkeypatch.setattr(rp, "reattach_output_dir", lambda *a, fresh, **k: fresh)
    monkeypatch.setattr(rp, "_run_prepared", lambda *a, **k: None)
    monkeypatch.setattr(rp, "Fetcher", lambda *a, **k: types.SimpleNamespace(on_well_removed=None))
    monkeypatch.setattr(rp._BatchedRemoteProcess, "_run_batches", lambda self: None)
    cache = _Cache()
    cache.cache_key = lambda zipped: "k" * 32
    runner = types.SimpleNamespace(
        _cache=cache, _host_argv_extras=lambda _f: [],
        host=types.SimpleNamespace(remote_staging_dir="~/.st", ssh_alias="h"))
    rp._BatchedRemoteProcess(runner, types.SimpleNamespace(well_zips=zips, mode=None, incremental=False),
                             out, _argv(out), 2, output_filter=None)
    assert runner._run.registration_dir.endswith("/reg") and runner._run.registration_dir.startswith("~/.st/runs/")
    assert cache.sent[0][0] == [grid]


def test_both_remote_run_paths_send_the_records():
    """The single-shot path prepares its run in ``RemoteRunner._prepare_run`` and the batched one in
    ``_BatchedRemoteProcess.__init__``; a path that forgot the call would leave its re-runs registering every tile
    again, as reported (the batched constructor is also driven above)."""
    import ast

    from tests._repo_scan import parse_source

    def callers(module, name):
        tree = parse_source(Path(module))
        return {f.name for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
                and any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == name for n in ast.walk(f))}

    assert "_prepare_run" in callers("services/remote_runner.py", "send_registration")
    assert "__init__" in callers("services/remote_process.py", "send_registration")


def test_the_restated_names_match_the_pipeline_s():
    from pipeline.nanowell_masks import MASKS_SUFFIX
    from pipeline.nanowell_task import NANOWELL_DIR
    from services import remote_fetch_nanowells as rfn

    assert (rfn.MASKS_SUFFIX, rfn.NANOWELL_DIR) == (MASKS_SUFFIX, NANOWELL_DIR)


def test_a_fetched_well_brings_its_masks_back_without_counting_them_against_its_tables(tmp_path):
    """The masks a host made come back for the next run; a host without them (an older run) loses nothing."""
    from services.remote_fetch_nanowells import fetch_nanowell_tables

    listed: list[str] = []

    def list_glob(pattern):
        listed.append(pattern.rsplit("/", 1)[-1])
        name = pattern.rsplit("/", 1)[-1]
        return ("h", [] if name.endswith("_masks.npz") else [name], 1)

    fetcher = types.SimpleNamespace(
        run=types.SimpleNamespace(nanowell=True, local_output_dir=tmp_path, remote_output_dir="/r/out"),
        host=types.SimpleNamespace(ssh_alias="h"), _list_remote_glob=list_glob,
        _pull_listed=lambda host, files, dest: len(files))
    assert fetch_nanowell_tables(fetcher, "A01") == 5
    assert "A01_masks.npz" in listed
