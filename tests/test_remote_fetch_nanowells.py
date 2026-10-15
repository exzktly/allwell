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
