"""What ``RemoteRunner.spawn`` leaves behind for the rest of the run.

Row R4 of ``_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` splits
``spawn`` into phases. Characterised by mutation first, against every test that
drives the remote client, and two of spawn's own outputs had no test at all —
each of these survived with the whole set green:

* the suspend request was armed on a full cache hit, where no pipeline is
  launched to read it, so Suspend was offered for a run that could only ignore
  it;
* the runner stopped keeping the ``Fetcher`` it handed to the process, so Stop
  tore down the ssh and left the per-well download running on its own thread.

Both are asserted on what a caller can see: ``can_request_suspend``, and the
``Fetcher`` the returned process reads from being the one ``cancel`` stops.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from services.remote_config import RemoteHost
from services.remote_fetch import Fetcher, FetchedOutputVerifier
from services.remote_runner import LocalInputMode, RemoteRunner, ZippedInput
from services.remote_upload import DatasetCache


def _host() -> RemoteHost:
    return RemoteHost(
        name="test", ssh_alias="test-host", remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10,
        measured_bandwidth_mbps=100.0, compute_launcher="")


def _spawn(monkeypatch, tmp_path, stop_fetchers, *, already_done: set[str]):
    key = "cachekey" + "0" * 24
    monkeypatch.setattr(DatasetCache, "_ssh_run",
                        lambda self, argv, *, check, **kw:
                        MagicMock(stdout="", returncode=0))
    monkeypatch.setattr(DatasetCache, "cache_key", lambda self, zipped: key)
    monkeypatch.setattr(DatasetCache, "ensure_uploaded",
                        lambda self, zipped, **kw: (key, 1234, 0.5))
    monkeypatch.setattr(DatasetCache, "gc", lambda self, budget, **kw: None)
    monkeypatch.setattr(RemoteRunner, "sync_required_files",
                        lambda self, **kw: None)
    monkeypatch.setattr(FetchedOutputVerifier, "_list_remote_completed_wells",
                        lambda self: set(already_done))

    def fake_popen(argv, **kw):
        m = MagicMock()
        m.stdout = iter(())
        m.wait.return_value = 0
        m.returncode = 0
        return m

    monkeypatch.setattr("services.remote_runner.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "services.remote_runner.subprocess.run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", ""))

    zip_path = tmp_path / "A01.zip"
    zip_path.write_bytes(b"PK\x05\x06" + b"\0" * 18)
    zipped = ZippedInput(zip_dir=tmp_path, mode=LocalInputMode.ZIP,
                         well_zips=(zip_path,))
    runner = RemoteRunner(_host())
    stop_fetchers(runner)
    proc = runner.spawn(zipped, tmp_path / "out",
                        ["python", "process_microscopy.py"],
                        output_filter=None, log_fn=lambda _m: None)
    return runner, proc


@pytest.mark.parametrize(("already_done", "suspendable"), [
    (set(), True),
    ({"A01"}, False),
])
def test_suspend_is_offered_only_when_a_pipeline_was_launched(
        monkeypatch, tmp_path, stop_fetchers, already_done, suspendable):
    """A full cache hit launches ``true`` instead of the pipeline: there is
    nothing on the host that would ever read a suspend request."""
    runner, _ = _spawn(monkeypatch, tmp_path, stop_fetchers,
                       already_done=already_done)
    assert runner.can_request_suspend is suspendable


def test_stop_cancels_the_downloads_the_process_is_feeding(
        monkeypatch, tmp_path, stop_fetchers):
    cancelled: list[Fetcher] = []
    real_cancel = Fetcher.cancel

    def spy(self):
        cancelled.append(self)
        real_cancel(self)

    monkeypatch.setattr(Fetcher, "cancel", spy)
    runner, proc = _spawn(monkeypatch, tmp_path, stop_fetchers,
                          already_done=set())
    runner.cancel(proc)
    assert cancelled, "Stop never reached the per-well downloads"
    assert cancelled[0] is proc._fetcher
