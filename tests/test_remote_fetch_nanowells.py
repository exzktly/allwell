"""A nanowell well's tables come back with its other output (tracker P9)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from services.remote_fetch_nanowells import fetch_nanowell_tables, is_nanowell_run


class _Fetcher:
    """Records what it was asked to list and pull; the remote holds only the names in ``remote``."""

    def __init__(self, local, remote):
        self.run = SimpleNamespace(local_output_dir=local, remote_output_dir="/scratch/out")
        self.host = SimpleNamespace(ssh_alias="gpu")
        self.remote, self.listed, self.pulled = set(remote), [], []

    def _list_remote_glob(self, src):
        self.listed.append(src)
        name = src.rsplit("/", 1)[1]
        return "gpu", ([f"/scratch/out/nanowells/{name}"] if name in self.remote else []), None

    def _pull_listed(self, host, files, dest):
        self.pulled += [(f, str(dest)) for f in files]
        return len(files)


def _sidecar(path, **blocks):
    (path / "pipeline_info.json").write_text(json.dumps(blocks))


def test_both_tables_are_pulled_by_exact_name_into_the_nanowells_directory(tmp_path):
    _sidecar(tmp_path, nanowell={"scheme_version": 1})
    fetcher = _Fetcher(tmp_path, {"A01.csv", "A01_grid.json", "A010.csv"})
    assert fetch_nanowell_tables(fetcher, "A01") == 2
    assert fetcher.listed == ["gpu:/scratch/out/nanowells/A01.csv", "gpu:/scratch/out/nanowells/A01_grid.json"]
    assert [f for f, _ in fetcher.pulled] == ["/scratch/out/nanowells/A01.csv", "/scratch/out/nanowells/A01_grid.json"]
    assert {d for _, d in fetcher.pulled} == {str(tmp_path / "nanowells")} and (tmp_path / "nanowells").is_dir()


def test_a_run_that_is_not_in_nanowell_mode_costs_no_remote_call(tmp_path):
    _sidecar(tmp_path, execution_options={})
    fetcher = _Fetcher(tmp_path, {"A01.csv"})
    assert fetch_nanowell_tables(fetcher, "A01") == 0 and fetcher.listed == [] and not (tmp_path / "nanowells").exists()
    assert not is_nanowell_run(tmp_path / "missing")                      # no sidecar yet: not known to be nanowell


def test_a_table_the_remote_lacks_is_not_invented(tmp_path):
    _sidecar(tmp_path, nanowell={})
    fetcher = _Fetcher(tmp_path, {"A01_grid.json"})
    assert fetch_nanowell_tables(fetcher, "A01") == 1


def test_fetching_a_well_through_the_fetcher_brings_its_tables(tmp_path, monkeypatch):
    """Driven through ``Fetcher._fetch_one`` (the hook is a line in it), with the transport stubbed."""
    import zipfile
    from unittest.mock import patch

    from services.remote_config import RemoteHost
    from services.remote_fetch import Fetcher
    from services.remote_run_state import _RemoteRun
    from services.remote_runner import LocalInputMode, OutputFilter

    host = RemoteHost(name="t", ssh_alias="test-host", remote_project_dir="~/allwell",
                      remote_staging_dir="~/.s", remote_python="/opt/python", cpu_only=False, extra_args=(),
                      cache_budget_gb=10, measured_bandwidth_mbps=100.0)
    out = tmp_path / "out"
    out.mkdir()
    _sidecar(out, nanowell={"scheme_version": 1})
    run = _RemoteRun(run_id="r", cache_key="k", remote_input_dir="/r/in", remote_output_dir="/r/out",
                     remote_pidfile="/r/pid", local_output_dir=out, local_input_mode=LocalInputMode.ZIP)
    with patch.object(Fetcher, "_loop", lambda self: None):
        fetcher = Fetcher(host, run, output_filter=OutputFilter.FULL, log_fn=lambda _m: None)

    def fake_rsync(self, argv):
        with zipfile.ZipFile(argv[-1], "w") as zf:
            zf.writestr("x_labels.tif", b"x")
        return True

    def fake_list(self, src):
        name = src.rsplit("/", 1)[1]
        return "test-host", ([f"/r/out/nanowells/{name}"] if "nanowells" in src else [f"/r/out/{name}"]), 10

    def fake_pull(self, host_, files, dest_dir):
        for f in files:
            (dest_dir / f.rsplit("/", 1)[1]).write_text("remote")
        return len(files)

    monkeypatch.setattr(Fetcher, "_rsync", fake_rsync)
    monkeypatch.setattr(Fetcher, "_remote_file_size", lambda self, p: None)
    monkeypatch.setattr(Fetcher, "_already_local", lambda self, w: None)
    monkeypatch.setattr(Fetcher, "_list_remote_glob", fake_list)
    monkeypatch.setattr(Fetcher, "_pull_listed", fake_pull)
    monkeypatch.setattr(Fetcher, "_delete_well_files_on_remote", lambda self, w: None)
    monkeypatch.setattr(Fetcher, "_pull_settings_marker", lambda self, w: None)
    try:
        fetcher._fetch_one("A01")
    finally:
        fetcher.cancel()
    assert sorted(p.name for p in (out / "nanowells").iterdir()) == ["A01.csv", "A01_grid.json"]
