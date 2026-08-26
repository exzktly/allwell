"""A remote run's ``pipeline_info.json`` arrives while the run is still going.

Review reads that sidecar for the filename schema, the channel tokens and
``available_timepoints`` / ``available_fovs``. The Fetcher has always brought
each well's CSV down as that well finished, but the sidecar came only from
``RemoteRunner.verify_and_cleanup`` at the very end — so a plate opened mid-run
had per-well results and nothing to interpret them with, and fell back to
guessing channels from CSV column names.

``Fetcher._sidecar_loop`` closes that. The tests here pin the three properties
that make it safe to run beside everything else writing to that path:

* it installs the remote's sidecar when there is none locally,
* it **never** overwrites one that is already there — a previous run's sidecar
  carries cell-gating thresholds, sample definitions, ratios, notes and the
  run_report, none of which the remote's copy has,
* what it installs is **stamped provisional**, because a sidecar written by a
  job still running describes only that job's input — one batch's split, on a
  batched run — and a stopped run leaves this file behind as the dataset's
  only description of itself.

Plus the two lifecycle facts: the poll ends with the run, and both production
call sites turn it on.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from services.remote_config import RemoteHost
from services.remote_runner import Fetcher, LocalInputMode, OutputFilter
from tests._repo_scan import parse_source


@pytest.fixture
def host():
    return RemoteHost(
        name="test", ssh_alias="cluster",
        remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging",
        remote_python="/opt/python",
    )


def _make_run(tmp_path: Path):
    from services.remote_runner import _RemoteRun
    return _RemoteRun(
        run_id="run-abc", cache_key="k", remote_input_dir="/r/in",
        remote_output_dir="/r/out", remote_pidfile="/r/pid",
        local_output_dir=tmp_path, local_input_mode=LocalInputMode.ZIP,
    )


REMOTE_SIDECAR = {
    "filename_schema": "experiment_channel_well_fov_timepoint",
    "separator": "_",
    "fluor_tokens": ["dapi", "gfp"],
    "available_timepoints": ["0h", "24h"],
    "available_fovs": ["1", "2"],
}


def _fetcher(host, tmp_path, logs=None):
    """A Fetcher with neither of its threads running — the tests drive the
    sidecar methods directly, so no timer and no queue worker."""
    from unittest.mock import patch
    with patch.object(Fetcher, "_loop", lambda self: None):
        return Fetcher(host, _make_run(tmp_path),
                       output_filter=OutputFilter.FULL,
                       log_fn=(logs.append if logs is not None else None))


def _fake_rsync(monkeypatch, payload: dict | None):
    """Stand in for the rsync that pulls the sidecar.

    *payload* is what the remote has: a dict writes that JSON to the temp
    destination and succeeds, ``None`` means the remote has not written the
    file yet and rsync fails the way it does for a missing source.
    """
    import services.remote_runner as RR
    calls: list[str] = []

    def _run(cmd, **kwargs):
        import subprocess
        calls.append(cmd[-2])           # the remote source
        if payload is None:
            return subprocess.CompletedProcess(cmd, returncode=23)
        Path(cmd[-1]).write_text(json.dumps(payload))
        return subprocess.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr(RR, "_rsync_with_retry", _run)
    return calls


def test_the_sidecar_lands_locally_while_the_run_is_still_going(
        host, tmp_path, monkeypatch):
    logs: list[str] = []
    f = _fetcher(host, tmp_path, logs)
    _fake_rsync(monkeypatch, REMOTE_SIDECAR)

    assert f._pull_sidecar_once("cluster:/r/out/pipeline_info.json",
                                tmp_path / "pipeline_info.json") is True

    info = json.loads((tmp_path / "pipeline_info.json").read_text())
    assert info["fluor_tokens"] == ["dapi", "gfp"]
    assert info["available_timepoints"] == ["0h", "24h"]
    assert any("pipeline_info.json" in line for line in logs), (
        "the moment the plate becomes openable mid-run is worth one log line"
    )


def test_what_it_installs_says_it_is_provisional(host, tmp_path, monkeypatch):
    """A batched run's sidecar describes one batch's split, and has no
    run_report at all. Degraded output must not look like finished output."""
    f = _fetcher(host, tmp_path)
    _fake_rsync(monkeypatch, REMOTE_SIDECAR)

    f._pull_sidecar_once("cluster:/r/out/pipeline_info.json",
                         tmp_path / "pipeline_info.json")

    info = json.loads((tmp_path / "pipeline_info.json").read_text())
    provisional = info.get("provisional")
    assert provisional, (
        "the mid-run sidecar is indistinguishable from a finished one — the "
        "shortfall has to be in the artifact, not only in the run log"
    )
    assert provisional["source"] == "mid_run_fetch"
    assert provisional["run_id"] == "run-abc"


def test_an_existing_local_sidecar_is_never_overwritten(
        host, tmp_path, monkeypatch):
    """A re-run's local sidecar is authoritative: it carries the user's
    cell-gating thresholds, sample definitions, ratios, notes and the
    run_report, and the remote's copy carries none of them."""
    dest = tmp_path / "pipeline_info.json"
    dest.write_text(json.dumps({
        "fluor_tokens": ["dapi", "gfp", "rfp"],
        "cell_gating": {"thresh_frac_on": {"gfp": 0.42}},
        "run_report": {"wells": {"A01": "complete"}},
    }))
    f = _fetcher(host, tmp_path)
    calls = _fake_rsync(monkeypatch, REMOTE_SIDECAR)

    # The loop stands down before it ever reaches the network.
    f._sidecar_loop()
    assert calls == [], "polled a remote sidecar with a local one already here"

    # And the single-attempt path refuses too, in case it is reached directly.
    assert f._pull_sidecar_once("cluster:/r/out/pipeline_info.json", dest) is True

    info = json.loads(dest.read_text())
    assert info["cell_gating"]["thresh_frac_on"]["gfp"] == 0.42
    assert info["run_report"]["wells"] == {"A01": "complete"}
    assert "provisional" not in info


def test_a_remote_that_has_not_written_it_yet_is_not_an_error(
        host, tmp_path, monkeypatch):
    """The overwhelmingly common poll result. It must be quiet, leave no
    temp file behind, and report "keep trying"."""
    logs: list[str] = []
    f = _fetcher(host, tmp_path, logs)
    _fake_rsync(monkeypatch, None)

    assert f._pull_sidecar_once("cluster:/r/out/pipeline_info.json",
                                tmp_path / "pipeline_info.json") is False

    assert not (tmp_path / "pipeline_info.json").exists()
    assert list(tmp_path.iterdir()) == [], "a failed pull left a temp file"
    assert logs == [], "a not-yet-written sidecar is not news, every 15 seconds"


def test_the_first_attempt_waits_rather_than_firing_at_launch(
        host, tmp_path, monkeypatch):
    """The poller starts in the same breath as the ssh that launches the
    pipeline, so at t=0 the remote cannot have written the file — an immediate
    pull is a round trip guaranteed to miss. It is also what made three
    spawn-path tests fail: a background rsync landing in the middle of them."""
    import threading
    import services.remote_runner as RR

    f = _fetcher(host, tmp_path)
    calls = _fake_rsync(monkeypatch, REMOTE_SIDECAR)
    monkeypatch.setattr(RR, "SIDECAR_POLL_S", 30.0)

    t = threading.Thread(target=f._sidecar_loop, daemon=True)
    t.start()
    try:
        assert not f._sidecar_done.wait(0.5)   # still waiting out the interval
        assert calls == [], "the poller reached the network at t=0"
    finally:
        f._sidecar_done.set()
        t.join(timeout=5)
    assert not t.is_alive(), "ending the run did not end the poll"


def test_the_poll_ends_when_the_run_does(host, tmp_path, monkeypatch):
    """`flush_and_join` and `cancel` both end it. Past that point
    `verify_and_cleanup` owns this path and a poller would race it."""
    f = _fetcher(host, tmp_path)
    assert not f._sidecar_done.is_set()
    f.cancel()
    assert f._sidecar_done.is_set()

    g = _fetcher(host, tmp_path)
    monkeypatch.setattr(g._worker, "is_alive", lambda: False)
    g.flush_and_join(timeout=0.01)
    assert g._sidecar_done.is_set()


def test_the_poller_is_off_unless_asked(host, tmp_path):
    """It reaches the network on a timer. A Fetcher built to exercise one
    method — which is what every other Fetcher test does — must not."""
    assert _fetcher(host, tmp_path)._sidecar is None


def test_every_production_fetcher_polls_for_the_sidecar():
    """The flip side of the default being off: the two call sites that launch
    a real run must both turn it on, and so must any third one."""
    src = Path(__file__).resolve().parent.parent / "services" / "remote_runner.py"
    tree = parse_source(src)
    missing: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "Fetcher":
            continue
        enabled = any(
            kw.arg == "poll_sidecar"
            and isinstance(kw.value, ast.Constant) and kw.value.value is True
            for kw in node.keywords
        )
        if not enabled:
            missing.append(node.lineno)

    assert not missing, (
        "Fetcher(...) built without poll_sidecar=True at "
        f"{', '.join(f'services/remote_runner.py:{n}' for n in missing)} — that "
        "run's pipeline_info.json will not reach the user until the run ends, "
        "so the plate cannot be opened in Review while it is going."
    )
