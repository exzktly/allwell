"""A remote run's batch size comes back when its pipeline_info.json is opened.

``pipeline_info.json``'s ``execution_options`` is the pipeline's own
``vars(args)``, and the batch size is not a pipeline argument: it is the
client's choice of how ``RemoteRunner`` slices the plate into launches. So the
sidecar never recorded it, and **Open pipeline_info.json** restored every
setting of a batched run except the batch size — the field kept whatever it
held, and reproducing the run from its own sidecar uploaded the whole plate at
once onto a remote sized for a few wells.

The client now stamps it into the local sidecar after the end-of-run fetch
(``services.pipeline_runner.record_remote_batch_size``), and the loader reads
it back. Three things are pinned here:

* the writer, including what it must refuse to do;
* the runner's real order — the fetch inside ``verify_and_cleanup`` replaces
  the local sidecar with the remote one, so a stamp written before it would be
  destroyed on arrival. The stub fetch below does exactly that overwrite;
* the loader, through the real ``AnalyzeTab``.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

from services.pipeline_runner import record_remote_batch_size


def _write_sidecar(out: Path, exec_opts: dict | None = None, **extra) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    p = out / "pipeline_info.json"
    info = {"schema": "experiment:channel:well", **extra}
    if exec_opts is not None:
        info["execution_options"] = exec_opts
    p.write_text(json.dumps(info))
    return p


def _read(p: Path) -> dict:
    return json.loads(p.read_text())


# ── the writer ───────────────────────────────────────────────────────────────

def test_the_batch_size_is_added_beside_the_pipeline_s_own_options(tmp_path):
    p = _write_sidecar(tmp_path, {"nuclear_token": "DAPI", "workers": 3},
                       notes={"text": "keep me"})
    assert record_remote_batch_size(tmp_path, {"remote_batch_size": "4"}) is True
    info = _read(p)
    assert info["execution_options"] == {
        "nuclear_token": "DAPI", "workers": 3, "remote_batch_size": 4}
    assert info["notes"] == {"text": "keep me"}
    assert info["schema"] == "experiment:channel:well"


def test_an_unbatched_remote_run_records_zero_rather_than_nothing(tmp_path):
    """So loading it resets the field instead of inheriting the last value."""
    p = _write_sidecar(tmp_path, {"workers": 1})
    for blank in ("", "0", None, 0):
        assert record_remote_batch_size(tmp_path, {"remote_batch_size": blank}) is True
        assert _read(p)["execution_options"]["remote_batch_size"] == 0
    assert record_remote_batch_size(tmp_path, {}) is True
    assert _read(p)["execution_options"]["remote_batch_size"] == 0


def test_an_unreadable_value_records_nothing(tmp_path):
    """Unknown is absent, and absent leaves the form alone — never a made-up 0."""
    p = _write_sidecar(tmp_path, {"workers": 1})
    assert record_remote_batch_size(tmp_path, {"remote_batch_size": "four"}) is False
    assert "remote_batch_size" not in _read(p)["execution_options"]


def test_a_missing_sidecar_is_not_created(tmp_path):
    assert record_remote_batch_size(tmp_path, {"remote_batch_size": "4"}) is False
    assert not (tmp_path / "pipeline_info.json").exists()


def test_a_sidecar_with_no_execution_block_gets_one(tmp_path):
    p = _write_sidecar(tmp_path)
    assert record_remote_batch_size(tmp_path, {"remote_batch_size": "2"}) is True
    assert _read(p)["execution_options"] == {"remote_batch_size": 2}


# ── the runner's real order ──────────────────────────────────────────────────

def _run_remote(monkeypatch, tmp_path, batch_size: str) -> Path:
    """Drive the real ``_run_pipeline_remote`` with a stub remote.

    The stub's ``verify_and_cleanup`` stands in for the end-of-run fetch: it
    overwrites the local sidecar wholesale with one that has no batch size,
    which is what rsyncing the remote's sidecar home does.
    """
    import services.pipeline_runner as pr
    from services.pipeline_service import build_pipeline_args
    from services.remote_runner import LocalInputMode, ZippedInput

    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir()
    zipped = ZippedInput(zip_dir=in_dir, mode=LocalInputMode.ZIP,
                         well_zips=(in_dir / "A01.zip",))
    monkeypatch.setattr(pr, "stage_local_zips", lambda *a, **k: zipped)
    # A previous run's stamp, which the fetch must replace and the stamp restore.
    _write_sidecar(out_dir, {"remote_batch_size": 99})

    class _Proc:
        pid = 1
        returncode = 0
        stdout = iter(())
        _ssh = None

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    spawned: list[dict] = []

    def spawn(z, out, argv, **k):
        spawned.append(k)
        return _Proc()

    def fetch(ok, **k):
        _write_sidecar(out_dir, {"nuclear_token": "DAPI"})
        return True

    r = pr.PipelineRunner()
    r._remote = types.SimpleNamespace(name="h")
    r._remote_runner = types.SimpleNamespace(
        claim_granted_node=lambda **k: None,  # no granted node on this host
        estimate=lambda z: types.SimpleNamespace(format=lambda: "est"),
        spawn=spawn, verify_and_cleanup=fetch,
        close_remote_job=lambda **k: None, cancel=lambda proc: None,
        can_request_suspend=False)
    opts = {"filename_schema": "experiment:channel:well:fov:timepoint",
            "filename_sep": "_", "fluor_tokens": ["GFP"], "nuclear_token": "DAPI",
            "csv_prefix": "measurements", "remote_batch_size": batch_size}
    args = build_pipeline_args(Path("process_microscopy.py"), in_dir, out_dir, opts)
    r._run_pipeline_remote(in_dir, out_dir, args, opts)
    assert spawned, "the stub remote was never launched, so nothing was measured"
    return out_dir / "pipeline_info.json"


def test_the_stamp_survives_the_fetch_that_replaces_the_sidecar(monkeypatch, tmp_path):
    info = _read(_run_remote(monkeypatch, tmp_path, "3"))
    assert info["execution_options"] == {"nuclear_token": "DAPI", "remote_batch_size": 3}


# ── the loader ───────────────────────────────────────────────────────────────

def test_open_pipeline_info_restores_the_batch_size(qtbot):
    from analyze.tab import AnalyzeTab

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    assert tab._remote_batch_size_edit.text() == "0"

    tab._apply_pipeline_info({"schema_version": 2, "execution_options": {
        "remote_batch_size": 6, "cpu_only": True}})
    assert tab._remote_batch_size_edit.text() == "6"

    # A local run's sidecar has no such key: the field is left as it stands.
    tab._apply_pipeline_info({"schema_version": 2, "execution_options": {
        "cpu_only": False}})
    assert tab._cpu_only_cb.isChecked() is False, "the second block was not read"
    assert tab._remote_batch_size_edit.text() == "6"
