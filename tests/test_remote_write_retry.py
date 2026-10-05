"""The remote client's own writes survive a transient storage fault.

Moving the pipeline's scratch off the output volume fixed the local run, and
retry-shielding the per-well CSV and ``<well>_out.zip`` fixed the pipeline's two
remaining writes on it. Neither touches a *remote* run, where the pipeline
executes on the cluster and the client is the one doing I/O on the user's
volumes: it reads the whole plate off the input volume to stage the upload, and
writes every fetched well back onto the output volume. Both of those were
unguarded, so a single dropped SMB session — ``OSError: [Errno 9] Bad file
descriptor`` — failed a well, or the whole staging step, exactly as the local
run used to.

These tests pin the shield on the client side, and the diagnostics that tell a
local write fault apart from a remote one.
"""

from __future__ import annotations

import errno
import json
import types
import zipfile
from pathlib import Path

import pytest

import util.retry_io as rio
from services.remote_runner import OutputFilter
from services.remote_transport import _RSYNC_RETRY_CODES
from services.remote_staging import (
    _materialize_staging,
    _StagePlan,
)
from services.remote_fetch import (
    Fetcher,
    refresh_pipeline_info_available_from_outputs,
)
from services import remote_fetch
from services import remote_staging


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    monkeypatch.setattr(rio, "DEFAULT_BASE_DELAY_S", 0)


def _ebadf() -> OSError:
    return OSError(errno.EBADF, "Bad file descriptor")


def _flaky(fail_times: int, real):
    """Wrap *real*, raising EBADF for its first *fail_times* calls."""
    state = {"n": 0}

    def _wrapper(*a, **k):
        state["n"] += 1
        if state["n"] <= fail_times:
            raise _ebadf()
        return real(*a, **k)

    _wrapper.calls = state
    return _wrapper


# ── the shared primitive ────────────────────────────────────────────────────

def test_retry_io_retries_then_succeeds():
    calls = []

    def _fn():
        calls.append(1)
        if len(calls) < 3:
            raise _ebadf()
        return "ok"

    assert rio.retry_io(_fn, what="A01.zip") == "ok"
    assert len(calls) == 3


def test_retry_io_names_the_protocol_behind_the_errno():
    def _fn():
        raise _ebadf()

    with pytest.raises(OSError) as ei:
        rio.retry_io(_fn, what="A01_out.zip", advice="Do the thing.")
    msg = str(ei.value)
    assert "A01_out.zip" in msg
    assert "SMB" in msg              # the errno hint
    assert "Do the thing." in msg    # the caller's advice
    assert ei.value.__cause__.errno == errno.EBADF


def test_retry_io_cleanup_runs_after_every_failed_attempt():
    cleaned = []

    with pytest.raises(OSError):
        rio.retry_io(
            lambda: (_ for _ in ()).throw(_ebadf()),
            what="x", cleanup=lambda: cleaned.append(1), attempts=3,
        )
    assert len(cleaned) == 3


def test_retry_io_does_not_retry_a_non_oserror():
    calls = []

    def _fn():
        calls.append(1)
        raise ValueError("nope")

    with pytest.raises(ValueError):
        rio.retry_io(_fn, what="x")
    assert len(calls) == 1


def test_volume_hint_only_speaks_when_it_recognises_the_errno():
    assert "SMB" in rio.volume_hint(_ebadf())
    assert rio.volume_hint(OSError(errno.ENOENT, "No such file")) == ""
    assert rio.volume_hint(ValueError("not an OSError")) == ""


# ── upload staging: reading the plate off the user's input volume ───────────
#
# Only an archive the run has to REWRITE is read here — one whose channel filter
# or incremental done-set drops some of its members. An archive that survives
# intact is never copied at all; its source path goes straight to rsync, so
# there is no client-side write to shield.


def _rewrite_plan(tmp_path) -> tuple[Path, _StagePlan]:
    """A source archive of two members, planned down to one."""
    src = tmp_path / "A01.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("A01_GFP.tif", b"gfp-bytes")
        zf.writestr("A01_mCherry.tif", b"mch-bytes")
    return src, _StagePlan(src=src, keep=("A01_GFP.tif",), n_members=2)


def test_staging_rewrite_survives_a_transient_fault(monkeypatch, tmp_path):

    src, plan = _rewrite_plan(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()

    flaky = _flaky(2, remote_staging.atomic_zip)
    monkeypatch.setattr(remote_staging, "atomic_zip", flaky)

    staged = _materialize_staging([plan], staging)

    assert staged == (staging / "A01.zip",)
    with zipfile.ZipFile(staged[0], "r") as zf:
        assert zf.namelist() == ["A01_GFP.tif"]
    assert flaky.calls["n"] == 3
    # The user's input is untouched by any of it.
    with zipfile.ZipFile(src, "r") as zf:
        assert len(zf.namelist()) == 2


def test_staging_rewrite_reports_the_volume_when_the_budget_runs_out(
    monkeypatch, tmp_path,
):

    _src, plan = _rewrite_plan(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(
        remote_staging, "atomic_zip", lambda *a, **k: (_ for _ in ()).throw(_ebadf()))

    with pytest.raises(OSError) as ei:
        _materialize_staging([plan], staging)
    assert "A01.zip" in str(ei.value)
    assert "network mount" in str(ei.value)
    # The partial must not be left for the uploader to ship.
    assert not (staging / "A01.zip").exists()


def test_an_intact_archive_is_never_copied(tmp_path):
    """The pass-through case: no write, and the upload list names the source."""
    src, _plan = _rewrite_plan(tmp_path)
    plan = _StagePlan(src=src, keep=None, n_members=2)
    staging = tmp_path / "staging"
    staging.mkdir()

    staged = _materialize_staging([plan], staging)

    assert staged == (src,)
    assert list(staging.iterdir()) == []


# ── the fetch side: writing results onto the user's output volume ───────────

def _fetcher_stub(out_dir: Path, *, incremental: bool = False, log=None):
    """A Fetcher shaped just enough for the unbound methods under test.

    ``Fetcher.__init__`` starts a worker thread and wants a live host/run, so
    the methods are called unbound against this stub instead.
    """
    return types.SimpleNamespace(
        output_filter=OutputFilter.FULL,
        run=types.SimpleNamespace(
            local_output_dir=out_dir,
            incremental=incremental,
            remote_output_dir="/remote/out",
        ),
        _log=(log if log is not None else (lambda _m: None)),
    )


def test_materialize_survives_a_transient_fault(monkeypatch, tmp_path):
    import services.remote_runner as rr

    out_dir = tmp_path / "results"
    out_dir.mkdir()
    src_zip = tmp_path / "transit.zip"
    with zipfile.ZipFile(src_zip, "w") as zf:
        zf.writestr("a_labels.tif", b"x")

    flaky = _flaky(1, rr.os.replace)
    monkeypatch.setattr(rr.os, "replace", flaky)

    assert Fetcher._materialize(_fetcher_stub(out_dir), "A01", src_zip) is True
    assert (out_dir / "A01_out.zip").is_file()
    assert flaky.calls["n"] == 2
    # No staging debris beside the published archive.
    assert not list(out_dir.glob(".*tmp"))


def test_materialize_failure_names_the_output_volume(monkeypatch, tmp_path):
    import services.remote_runner as rr

    out_dir = tmp_path / "results"
    out_dir.mkdir()
    src_zip = tmp_path / "transit.zip"
    src_zip.write_bytes(b"x")
    monkeypatch.setattr(
        rr.os, "replace", lambda *a, **k: (_ for _ in ()).throw(_ebadf()))

    lines: list[str] = []
    stub = _fetcher_stub(out_dir, log=lines.append)
    assert Fetcher._materialize(stub, "A01", src_zip) is False

    warn = "".join(lines)
    assert "A01_out.zip" in warn
    assert "network mount" in warn
    assert not (out_dir / "A01_out.zip").exists()
    assert not list(out_dir.glob(".*tmp"))


# ── telling a local write fault apart from an empty remote ──────────────────

def _rsync_glob_stub(monkeypatch, *, listed: list[str], pull_ok: bool):
    import services.remote_runner as rr

    monkeypatch.setattr(remote_fetch, "resolve_rsync_binary", lambda: "rsync")
    monkeypatch.setattr(remote_fetch, "_rsync_ssh_transport", lambda _host: [])
    monkeypatch.setattr(
        rr.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout="\n".join(listed).encode(), stderr=b"",
        ),
    )
    stub = types.SimpleNamespace(
        host=types.SimpleNamespace(ssh_argv=lambda **k: ["ssh", "h"]),
        _rsync=lambda _argv: pull_ok,
    )
    # ``_rsync_glob`` is now a two-line composition of the listing and the pull,
    # split so the CSV half of a well's fetch can learn its byte total before it
    # starts. Bind the real halves onto the stub so this still exercises them.
    from services.remote_fetch import Fetcher
    stub._list_remote_glob = lambda src: Fetcher._list_remote_glob(stub, src)
    stub._pull_listed = lambda h, f, d: Fetcher._pull_listed(stub, h, f, d)
    return stub


def test_rsync_glob_reports_listed_and_pulled_separately(monkeypatch, tmp_path):
    stub = _rsync_glob_stub(
        monkeypatch, listed=["/remote/out/m_A01.csv"], pull_ok=False)
    listed, pulled = Fetcher._rsync_glob(stub, "h:/remote/out/*_A01.csv", tmp_path)
    assert (listed, pulled) == (1, 0)


def test_rsync_glob_reports_an_empty_remote_as_nothing_listed(
    monkeypatch, tmp_path,
):
    stub = _rsync_glob_stub(monkeypatch, listed=[], pull_ok=True)
    assert Fetcher._rsync_glob(stub, "h:/remote/out/*_A01.csv", tmp_path) == (0, 0)


def test_rsync_retries_a_local_file_io_error():
    """rsync exit 11 is what a destination-side EBADF looks like — a transient
    on the *local* end of the transfer, so it must be retried like the network
    codes. Permission-class failures (23) still surface immediately."""
    assert 11 in _RSYNC_RETRY_CODES
    assert 23 not in _RSYNC_RETRY_CODES


# ── the sidecar rewrite ─────────────────────────────────────────────────────

def test_pipeline_info_refresh_survives_a_transient_fault(monkeypatch, tmp_path):
    import services.remote_runner as rr

    out = tmp_path / "results"
    out.mkdir()
    (out / "pipeline_info.json").write_text(json.dumps({
        "schema": "well:fov:timepoint:channel", "separator": "_",
        "available_timepoints": [], "available_fovs": [],
    }))
    with zipfile.ZipFile(out / "A01_out.zip", "w") as zf:
        zf.writestr("A01_1_0_GFP_tophat.tif", b"x")

    flaky = _flaky(1, rr.os.replace)
    monkeypatch.setattr(rr.os, "replace", flaky)

    assert refresh_pipeline_info_available_from_outputs(out) is True
    assert flaky.calls["n"] == 2
    assert json.loads((out / "pipeline_info.json").read_text())["available_fovs"]
