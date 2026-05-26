"""Tests for services/remote_runner.py.

The unit tests here exercise the pure-logic surface (cache key
stability, argv rewriting, mode detection, output filter behavior, the
verify step). Anything that would touch real ssh/rsync is mocked.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.remote_config import RemoteHost
from services.remote_runner import (
    DatasetCache,
    Fetcher,
    LocalInputMode,
    OutputFilter,
    RemoteRunner,
    ZippedInput,
    _REQUIRED_REMOTE_FILES,
    _detect_local_input_mode,
    _filter_channels_in_place,
    _member_matches,
    _required_tokens,
    _rsync_ssh_transport,
    repo_root,
    stage_local_zips,
)


# ── helpers ─────────────────────────────────────────────────────────────────


@pytest.fixture
def host() -> RemoteHost:
    return RemoteHost(
        name="test",
        ssh_alias="test-host",
        remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging",
        remote_python="/opt/python",
        cpu_only=False,
        extra_args=(),
        cache_budget_gb=10,
        measured_bandwidth_mbps=100.0,
    )


def _make_well_zip(parent: Path, name: str, members: list[str]) -> Path:
    p = parent / name
    with zipfile.ZipFile(p, "w") as zf:
        for m in members:
            zf.writestr(m, b"\0" * 128)
    return p


# ── input-mode detection ────────────────────────────────────────────────────


def test_detect_zip_mode(tmp_path):
    _make_well_zip(tmp_path, "A01.zip", ["NIR_F001.tif", "GFP_F001.tif"])
    _make_well_zip(tmp_path, "B02.zip", ["NIR_F001.tif"])
    assert _detect_local_input_mode(tmp_path) is LocalInputMode.ZIP


def test_detect_folder_mode(tmp_path):
    (tmp_path / "A01").mkdir()
    (tmp_path / "B02").mkdir()
    (tmp_path / "A01" / "NIR_F001.tif").write_bytes(b"\0")
    assert _detect_local_input_mode(tmp_path) is LocalInputMode.FOLDER


def test_detect_flat_mode(tmp_path):
    (tmp_path / "Exp_NIR_A01_F001.tif").write_bytes(b"\0")
    (tmp_path / "Exp_GFP_A01_F001.tif").write_bytes(b"\0")
    assert _detect_local_input_mode(tmp_path) is LocalInputMode.FLAT


# ── stage_local_zips: zip-input fast path ───────────────────────────────────


def test_stage_local_zips_zip_input_passthrough(tmp_path):
    _make_well_zip(tmp_path, "A01.zip", ["NIR_F001.tif", "GFP_F001.tif"])
    _make_well_zip(tmp_path, "B02.zip", ["NIR_F001.tif"])
    zipped = stage_local_zips(
        tmp_path,
        filename_schema="experiment:channel:well:fov",
        filename_sep="_",
    )
    assert zipped.mode is LocalInputMode.ZIP
    assert zipped.zip_dir == tmp_path  # no copy
    assert len(zipped.well_zips) == 2
    assert zipped.cleanup is None  # no temp dir to clean up


# ── selective channel staging ───────────────────────────────────────────────


def test_required_tokens_combines_nuclear_and_fluor():
    keep = _required_tokens("NIR", ["GFP", "mCherry"])
    assert keep == {"NIR", "GFP", "mCherry"}


def test_required_tokens_skips_empty():
    keep = _required_tokens(None, ["GFP", "", "  "])
    assert keep == {"GFP"}


def test_member_matches_simple():
    assert _member_matches("Exp_NIR_A01_F001.tif", {"NIR"}) is True
    assert _member_matches("Exp_GFP_A01_F001.tif", {"NIR"}) is False
    # Path-prefixed names too.
    assert _member_matches("subfolder/Exp_NIR_A01.tif", {"NIR"}) is True


def test_filter_channels_drops_unused(tmp_path):
    z = _make_well_zip(
        tmp_path,
        "A01.zip",
        [
            "Exp_NIR_A01_F001.tif",
            "Exp_GFP_A01_F001.tif",
            "Exp_mCherry_A01_F001.tif",
            "Exp_DAPI_A01_F001.tif",
        ],
    )
    zipped = ZippedInput(zip_dir=tmp_path, mode=LocalInputMode.ZIP, well_zips=(z,))
    _filter_channels_in_place(
        tmp_path, (z,), LocalInputMode.ZIP,
        keep_tokens={"NIR", "GFP"},
        log_fn=None,
    )
    with zipfile.ZipFile(z, "r") as zf:
        remaining = zf.namelist()
    assert any("NIR" in m for m in remaining)
    assert any("GFP" in m for m in remaining)
    assert not any("mCherry" in m for m in remaining)
    assert not any("DAPI" in m for m in remaining)


def test_filter_channels_noop_when_all_kept(tmp_path):
    members = ["Exp_NIR_A01.tif", "Exp_GFP_A01.tif"]
    z = _make_well_zip(tmp_path, "A01.zip", members)
    size_before = z.stat().st_size
    _filter_channels_in_place(
        tmp_path, (z,), LocalInputMode.ZIP,
        keep_tokens={"NIR", "GFP"},
        log_fn=None,
    )
    # No rewrite needed.
    assert z.stat().st_size == size_before
    with zipfile.ZipFile(z, "r") as zf:
        assert sorted(zf.namelist()) == sorted(members)


# ── DatasetCache: cache-key stability ───────────────────────────────────────


def test_cache_key_stable_across_calls(tmp_path, host):
    z1 = _make_well_zip(tmp_path, "A01.zip", ["x.tif"])
    z2 = _make_well_zip(tmp_path, "B02.zip", ["x.tif"])
    zipped = ZippedInput(
        zip_dir=tmp_path, mode=LocalInputMode.ZIP, well_zips=(z1, z2)
    )
    cache = DatasetCache(host)
    k1 = cache.cache_key(zipped)
    k2 = cache.cache_key(zipped)
    assert k1 == k2
    assert len(k1) == 32  # 16-byte blake2b → 32 hex chars


def test_cache_key_changes_when_content_changes(tmp_path, host):
    z = _make_well_zip(tmp_path, "A01.zip", ["x.tif"])
    zipped = ZippedInput(
        zip_dir=tmp_path, mode=LocalInputMode.ZIP, well_zips=(z,)
    )
    cache = DatasetCache(host)
    k1 = cache.cache_key(zipped)

    # Re-write the file with different content.
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("x.tif", b"\xff" * 256)
    # Bump mtime explicitly to dodge filesystem stat caching on fast tests.
    new_mtime = z.stat().st_mtime + 5
    os.utime(z, (new_mtime, new_mtime))
    k2 = cache.cache_key(zipped)
    assert k1 != k2


def test_cache_key_changes_when_set_of_wells_changes(tmp_path, host):
    z1 = _make_well_zip(tmp_path, "A01.zip", ["x.tif"])
    zipped_one = ZippedInput(
        zip_dir=tmp_path, mode=LocalInputMode.ZIP, well_zips=(z1,)
    )
    cache = DatasetCache(host)
    k_one = cache.cache_key(zipped_one)

    z2 = _make_well_zip(tmp_path, "B02.zip", ["x.tif"])
    zipped_two = ZippedInput(
        zip_dir=tmp_path, mode=LocalInputMode.ZIP, well_zips=(z1, z2)
    )
    k_two = cache.cache_key(zipped_two)
    assert k_one != k_two


# ── RemoteRunner: argv rewriting ────────────────────────────────────────────


def test_remote_argv_swaps_input_output(host):
    runner = RemoteRunner(host)
    local = [
        "/usr/bin/python", "/local/process_microscopy.py",
        "--input_dir", "/local/data",
        "--output_dir", "/local/out",
        "--nuclear_token", "NIR",
        "--fluor_tokens", "GFP", "mCherry",
        "--filename_schema", "experiment:channel:well:fov",
        "--filename_sep", "_",
    ]
    remote = runner._build_remote_argv(  # noqa: SLF001
        local, "/remote/in", "/remote/out",
        OutputFilter.FULL, LocalInputMode.ZIP,
    )
    assert remote[0] == host.remote_python
    assert remote[1] == f"{host.remote_project_dir}/process_microscopy.py"
    i_in = remote.index("--input_dir")
    assert remote[i_in + 1] == "/remote/in"
    i_out = remote.index("--output_dir")
    assert remote[i_out + 1] == "/remote/out"
    assert "--no_save_tophat" not in remote


def test_remote_argv_csv_only_appends_no_save_tophat(host):
    runner = RemoteRunner(host)
    local = [
        "/usr/bin/python", "/local/pm.py",
        "--input_dir", "/x", "--output_dir", "/y",
    ]
    remote = runner._build_remote_argv(  # noqa: SLF001
        local, "/r/in", "/r/out", OutputFilter.CSV_ONLY, LocalInputMode.ZIP,
    )
    assert "--no_save_tophat" in remote


def test_remote_argv_respects_existing_no_save_tophat(host):
    runner = RemoteRunner(host)
    local = [
        "/usr/bin/python", "/local/pm.py",
        "--input_dir", "/x", "--output_dir", "/y",
        "--no_save_tophat",
    ]
    remote = runner._build_remote_argv(  # noqa: SLF001
        local, "/r/in", "/r/out", OutputFilter.CSV_ONLY, LocalInputMode.ZIP,
    )
    # Exactly one --no_save_tophat, not two.
    assert remote.count("--no_save_tophat") == 1


def test_remote_argv_appends_cpu_only_from_host(host):
    host_cpu = RemoteHost(**{**host.__dict__, "cpu_only": True})
    runner = RemoteRunner(host_cpu)
    local = [
        "/usr/bin/python", "/local/pm.py",
        "--input_dir", "/x", "--output_dir", "/y",
    ]
    remote = runner._build_remote_argv(  # noqa: SLF001
        local, "/r/in", "/r/out", OutputFilter.FULL, LocalInputMode.ZIP,
    )
    assert remote.count("--cpu_only") == 1


def test_remote_argv_appends_extra_args(host):
    host_extras = RemoteHost(**{**host.__dict__, "extra_args": ("--workers", "32")})
    runner = RemoteRunner(host_extras)
    local = [
        "/usr/bin/python", "/local/pm.py",
        "--input_dir", "/x", "--output_dir", "/y",
    ]
    remote = runner._build_remote_argv(  # noqa: SLF001
        local, "/r/in", "/r/out", OutputFilter.FULL, LocalInputMode.ZIP,
    )
    assert remote[-2:] == ["--workers", "32"]


# ── RemoteRunner.estimate ───────────────────────────────────────────────────


def test_estimate_remote_wins(host, tmp_path):
    # 50 MB / 100 Mbps → 4s transfer; assume 4× speedup.
    z = _make_well_zip(tmp_path, "A01.zip", ["x.tif"])
    z.write_bytes(b"\0" * 50 * 1024 * 1024)
    zipped = ZippedInput(zip_dir=tmp_path, mode=LocalInputMode.ZIP, well_zips=(z,))
    runner = RemoteRunner(host)
    est = runner.estimate(zipped, local_compute_s=600.0)
    assert est.will_win
    assert est.transfer_s > 0


def test_estimate_cache_hit_zeroes_transfer(host, tmp_path):
    z = _make_well_zip(tmp_path, "A01.zip", ["x.tif"])
    z.write_bytes(b"\0" * 1024)
    zipped = ZippedInput(zip_dir=tmp_path, mode=LocalInputMode.ZIP, well_zips=(z,))
    runner = RemoteRunner(host)
    est = runner.estimate(zipped, is_cache_hit=True)
    assert est.transfer_s == 0.0


def test_estimate_warns_when_local_would_win(host, tmp_path):
    # Tiny local compute + a lot of bytes → remote loses.
    big = _make_well_zip(tmp_path, "A01.zip", ["x.tif"])
    big.write_bytes(b"\0" * 500 * 1024 * 1024)  # 500 MB
    zipped = ZippedInput(zip_dir=tmp_path, mode=LocalInputMode.ZIP, well_zips=(big,))
    runner = RemoteRunner(host)
    est = runner.estimate(zipped, local_compute_s=10.0)
    assert not est.will_win


# ── Fetcher: stdout observation ─────────────────────────────────────────────


def test_fetcher_observes_well_done_marker(host, tmp_path):
    from services.remote_runner import _RemoteRun

    run = _RemoteRun(
        run_id="x",
        cache_key="k",
        remote_input_dir="/r/in",
        remote_output_dir="/r/out",
        remote_pidfile="/r/pid",
        local_output_dir=tmp_path,
        local_input_mode=LocalInputMode.ZIP,
    )
    # Don't actually start the worker thread — patch the queue put.
    with patch.object(Fetcher, "_loop", lambda self: None):
        fetcher = Fetcher(host, run, output_filter=OutputFilter.FULL)
        fetcher.observe("Zip mode: 12 well-zip files detected\n")
        fetcher.observe("A01 — done.\n")
        fetcher.observe("B02 — done.\n")
        fetcher.observe("noise line\n")
        fetcher.cancel()
    assert run.well_count_total == 12
    assert run.expected_wells == {"A01", "B02"}


def test_fetcher_ignores_unrelated_lines(host, tmp_path):
    from services.remote_runner import _RemoteRun

    run = _RemoteRun(
        run_id="x",
        cache_key="k",
        remote_input_dir="/r/in",
        remote_output_dir="/r/out",
        remote_pidfile="/r/pid",
        local_output_dir=tmp_path,
        local_input_mode=LocalInputMode.ZIP,
    )
    with patch.object(Fetcher, "_loop", lambda self: None):
        fetcher = Fetcher(host, run, output_filter=OutputFilter.FULL)
        fetcher.observe("processing A01...\n")
        fetcher.observe("\n")
        fetcher.observe("[zipper] copying foo\n")
        fetcher.cancel()
    assert run.expected_wells == set()


# ── Verify ──────────────────────────────────────────────────────────────────


def test_verify_passes_when_all_csvs_present(host, tmp_path):
    from services.remote_runner import _RemoteRun

    runner = RemoteRunner(host)
    run = _RemoteRun(
        run_id="x",
        cache_key="k",
        remote_input_dir="/r/in",
        remote_output_dir="/r/out",
        remote_pidfile="/r/pid",
        local_output_dir=tmp_path,
        local_input_mode=LocalInputMode.ZIP,
    )
    run.expected_wells = {"A01", "B02"}
    runner._run = run  # noqa: SLF001

    (tmp_path / "measurements_A01.csv").write_text("col1,col2\n1,2\n")
    (tmp_path / "measurements_B02.csv").write_text("col1,col2\n3,4\n")

    logs: list[str] = []
    missing = runner._verify_local_manifest(log=logs.append)  # noqa: SLF001
    assert missing == []
    assert any("all 2 wells" in m for m in logs)


def test_verify_reports_missing_csv(host, tmp_path):
    from services.remote_runner import _RemoteRun

    runner = RemoteRunner(host)
    run = _RemoteRun(
        run_id="x",
        cache_key="k",
        remote_input_dir="/r/in",
        remote_output_dir="/r/out",
        remote_pidfile="/r/pid",
        local_output_dir=tmp_path,
        local_input_mode=LocalInputMode.ZIP,
    )
    run.expected_wells = {"A01", "B02"}
    runner._run = run  # noqa: SLF001

    (tmp_path / "measurements_A01.csv").write_text("col1\n")
    # B02 missing on purpose.

    logs: list[str] = []
    missing = runner._verify_local_manifest(log=logs.append)  # noqa: SLF001
    assert missing
    assert any("B02" in m for m in missing)


def test_verify_reports_empty_csv(host, tmp_path):
    from services.remote_runner import _RemoteRun

    runner = RemoteRunner(host)
    run = _RemoteRun(
        run_id="x",
        cache_key="k",
        remote_input_dir="/r/in",
        remote_output_dir="/r/out",
        remote_pidfile="/r/pid",
        local_output_dir=tmp_path,
        local_input_mode=LocalInputMode.ZIP,
    )
    run.expected_wells = {"A01"}
    runner._run = run  # noqa: SLF001

    (tmp_path / "measurements_A01.csv").write_text("")  # empty
    logs: list[str] = []
    missing = runner._verify_local_manifest(log=logs.append)  # noqa: SLF001
    assert missing == ["empty CSV for A01"]


# ── sync_required_files ─────────────────────────────────────────────────────


def test_required_files_list_is_what_we_promised():
    """The contract from the design discussion: only these four files.

    v5.2.1 swapped ``_Docs/requirements.txt`` for
    ``_Docs/requirements-remote.txt`` so the remote install no longer
    drags in PySide6 / pyobjc / pyinstaller. If this assertion fails
    the contract drifted and both halves of the split need a look.
    """
    assert _REQUIRED_REMOTE_FILES == (
        "process_microscopy.py",
        "auto_threshold_core.py",
        "well_token.py",
        "_Docs/requirements-remote.txt",
    )


def test_remote_requirements_file_omits_gui_deps():
    """v5.2.1 contract: the headless requirements file must not list
    PySide6 / pyobjc / pyinstaller. If any of those leak back in we
    re-introduce the same wheel-availability failures on aarch64 / new
    Pythons that the v5.2.1 split was meant to fix."""
    from services.remote_runner import repo_root
    text = (repo_root() / "_Docs" / "requirements-remote.txt").read_text(
        encoding="utf-8",
    )
    # Strip comment lines so a doc-only mention doesn't break the check.
    body = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )
    assert "PySide6" not in body
    assert "pyobjc" not in body
    assert "pyinstaller" not in body
    # But it MUST contain the scientific stack the pipeline depends on.
    assert "tensorflow" in body.lower()
    assert "stardist" in body


def test_required_files_all_exist_in_repo():
    """If any of these files moves or is deleted, the runner breaks
    silently against a fresh remote. Catch that here, loudly."""
    root = repo_root()
    missing = [f for f in _REQUIRED_REMOTE_FILES if not (root / f).is_file()]
    assert not missing, f"Required pipeline files missing from repo: {missing}"


def test_sync_required_files_rsync_argv_shape(host, monkeypatch):
    """The rsync command must use -aR (relative paths) so subdirs are
    recreated under the destination, and must point at our four files."""
    runner = RemoteRunner(host)
    captured: dict = {}

    def fake_ssh_run(self, argv, *, check):
        captured.setdefault("ssh_calls", []).append(list(argv))
        return MagicMock(stdout="", returncode=0)

    def fake_subprocess_run(cmd, **kwargs):
        captured["rsync_cmd"] = list(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(DatasetCache, "_ssh_run", fake_ssh_run)
    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_subprocess_run)

    runner.sync_required_files()

    rsync = captured["rsync_cmd"]
    assert rsync[0] == "rsync"
    assert "-aR" in rsync
    # Last element is the destination — verify it targets remote_project_dir.
    assert rsync[-1] == f"{host.ssh_alias}:{host.remote_project_dir}/"
    # Every required file should appear (with a /./ pivot for --relative).
    for required in _REQUIRED_REMOTE_FILES:
        assert any(required in arg and "/./" in arg for arg in rsync), \
            f"missing rsync source for {required}"


def test_sync_required_files_mkdir_for_subdirs(host, monkeypatch):
    """Subdirs like _Docs/ must be pre-created on the remote — rsync
    with individual file sources doesn't create intermediate dirs."""
    runner = RemoteRunner(host)
    captured: list = []

    def fake_ssh_run(self, argv, *, check):
        captured.append(list(argv))
        return MagicMock(stdout="", returncode=0)

    def fake_subprocess_run(cmd, **kwargs):
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(DatasetCache, "_ssh_run", fake_ssh_run)
    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_subprocess_run)

    runner.sync_required_files()

    # First ssh call should be a `mkdir -p` covering the project dir
    # plus the _Docs subdir.
    assert captured, "expected at least one ssh mkdir call"
    mk = captured[0]
    assert mk[0] == "mkdir"
    assert "-p" in mk
    assert any(host.remote_project_dir in arg for arg in mk)
    assert any("_Docs" in arg for arg in mk)


def test_sync_required_files_raises_when_local_file_missing(host, monkeypatch, tmp_path):
    """If the user's local working copy is missing required files, fail
    early with a clear error instead of trying to rsync a nonexistent
    source (which rsync handles, but the resulting remote would silently
    keep stale code)."""
    monkeypatch.setattr("services.remote_runner.repo_root", lambda: tmp_path)
    runner = RemoteRunner(host)
    with pytest.raises(FileNotFoundError, match="missing locally"):
        runner.sync_required_files()


def test_sync_required_files_surfaces_rsync_failure(host, monkeypatch):
    """A non-transient rsync failure (e.g. exit 23 — permission/path
    error) must surface as a RuntimeError after the retry helper
    fast-fails out, not retry forever."""
    runner = RemoteRunner(host)

    def fake_ssh_run(self, argv, *, check):
        return MagicMock(stdout="", returncode=0)

    def fail_rsync(cmd, **kwargs):
        import subprocess as sp
        return sp.CompletedProcess(
            cmd, returncode=23, stdout="", stderr="ssh: connection refused",
        )

    monkeypatch.setattr(DatasetCache, "_ssh_run", fake_ssh_run)
    monkeypatch.setattr("services.remote_runner.subprocess.run", fail_rsync)
    monkeypatch.setattr("services.remote_runner.time.sleep", lambda _s: None)

    with pytest.raises(RuntimeError, match="connection refused"):
        runner.sync_required_files()


def test_sync_required_files_uses_translated_paths_on_windows(host, monkeypatch):
    """On Windows, the rsync sources must be in /c/... Cygwin form so
    Git for Windows' rsync accepts them. The /./ pivot for relative
    paths must come AFTER the translation."""
    runner = RemoteRunner(host)
    captured: dict = {}

    def fake_ssh_run(self, argv, *, check):
        return MagicMock(stdout="", returncode=0)

    def fake_subprocess_run(cmd, **kwargs):
        captured["rsync_cmd"] = list(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    # Pretend we're on Windows and Git Bash is installed.
    monkeypatch.setattr("services.remote_config._is_windows", lambda: True)
    monkeypatch.setattr("services.remote_config.Path.is_file", lambda self: True)
    # Pretend the repo root is on the C: drive.
    monkeypatch.setattr(
        "services.remote_runner.repo_root",
        lambda: Path("C:\\Users\\me\\allwell"),
    )
    monkeypatch.setattr(DatasetCache, "_ssh_run", fake_ssh_run)
    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_subprocess_run)

    runner.sync_required_files()

    rsync = captured["rsync_cmd"]
    assert rsync[0].endswith("rsync.exe"), "should use Git Bash rsync on Windows"
    # Every required-file source must reference /c/Users/me/allwell/./<file>
    for required in _REQUIRED_REMOTE_FILES:
        assert any(
            f"/c/Users/me/allwell/./{required}" == arg for arg in rsync
        ), f"sources should be cygwin-form; missing /c/Users/me/allwell/./{required} in {rsync}"


# ── verify_and_cleanup decision matrix ──────────────────────────────────────


@pytest.fixture
def runner_with_run(host, tmp_path):
    from services.remote_runner import _RemoteRun

    runner = RemoteRunner(host)
    run = _RemoteRun(
        run_id="run123",
        cache_key="deadbeefcafe",
        remote_input_dir="/r/in",
        remote_output_dir="/r/out",
        remote_pidfile="/r/pid",
        local_output_dir=tmp_path,
        local_input_mode=LocalInputMode.ZIP,
    )
    run.expected_wells = {"A01"}
    runner._run = run  # noqa: SLF001
    (tmp_path / "x_A01.csv").write_text("ok\n")
    return runner


def test_verify_and_cleanup_run_mode_removes_run_dir_only(runner_with_run, monkeypatch):
    runner = runner_with_run
    calls: list[list[str]] = []

    def fake_ssh_run(self, argv, *, check):
        calls.append(list(argv))
        return MagicMock(stdout="", returncode=0)

    monkeypatch.setattr(DatasetCache, "_ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        RemoteRunner, "_rsync_optional",
        lambda self, src, dest, log_fn: None,
    )
    ok = runner.verify_and_cleanup(ok=True, cleanup_mode="run", log_fn=lambda _m: None)
    assert ok
    # Exactly one rm -rf, targeting the run dir.
    rm_calls = [c for c in calls if c and c[0] == "rm"]
    assert len(rm_calls) == 1
    assert "runs/run123" in rm_calls[0][-1]


def test_verify_and_cleanup_all_mode_removes_both(runner_with_run, monkeypatch):
    runner = runner_with_run
    calls: list[list[str]] = []

    def fake_ssh_run(self, argv, *, check):
        calls.append(list(argv))
        return MagicMock(stdout="", returncode=0)

    monkeypatch.setattr(DatasetCache, "_ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        RemoteRunner, "_rsync_optional",
        lambda self, src, dest, log_fn: None,
    )
    runner.verify_and_cleanup(ok=True, cleanup_mode="all", log_fn=lambda _m: None)
    rm_calls = [c for c in calls if c and c[0] == "rm"]
    assert len(rm_calls) == 2
    assert any("runs/run123" in c[-1] for c in rm_calls)
    assert any("datasets/" in c[-1] for c in rm_calls)


def test_verify_and_cleanup_none_mode_removes_nothing(runner_with_run, monkeypatch):
    runner = runner_with_run
    calls: list[list[str]] = []

    def fake_ssh_run(self, argv, *, check):
        calls.append(list(argv))
        return MagicMock(stdout="", returncode=0)

    monkeypatch.setattr(DatasetCache, "_ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        RemoteRunner, "_rsync_optional",
        lambda self, src, dest, log_fn: None,
    )
    runner.verify_and_cleanup(ok=True, cleanup_mode="none", log_fn=lambda _m: None)
    rm_calls = [c for c in calls if c and c[0] == "rm"]
    assert rm_calls == []


def test_verify_and_cleanup_skips_when_failure(runner_with_run, monkeypatch):
    runner = runner_with_run
    calls: list[list[str]] = []

    def fake_ssh_run(self, argv, *, check):
        calls.append(list(argv))
        return MagicMock(stdout="", returncode=0)

    monkeypatch.setattr(DatasetCache, "_ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        RemoteRunner, "_rsync_optional",
        lambda self, src, dest, log_fn: None,
    )
    ok = runner.verify_and_cleanup(ok=False, cleanup_mode="all", log_fn=lambda _m: None)
    assert not ok
    rm_calls = [c for c in calls if c and c[0] == "rm"]
    assert rm_calls == []


def test_verify_and_cleanup_skips_when_local_missing(runner_with_run, monkeypatch):
    runner = runner_with_run
    # Delete the CSV the fixture created — verification must fail and
    # cleanup must NOT fire.
    (runner_with_run._run.local_output_dir / "x_A01.csv").unlink()  # noqa: SLF001

    calls: list[list[str]] = []

    def fake_ssh_run(self, argv, *, check):
        calls.append(list(argv))
        return MagicMock(stdout="", returncode=0)

    monkeypatch.setattr(DatasetCache, "_ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        RemoteRunner, "_rsync_optional",
        lambda self, src, dest, log_fn: None,
    )
    logs: list[str] = []
    ok = runner.verify_and_cleanup(ok=True, cleanup_mode="all", log_fn=logs.append)
    assert not ok
    rm_calls = [c for c in calls if c and c[0] == "rm"]
    assert rm_calls == []
    assert any("keeping remote files" in m for m in logs)


# ── _rsync_ssh_transport (v5.2.5) ───────────────────────────────────────────


def test_rsync_ssh_transport_includes_standard_options(host):
    """The -e ssh string every rsync passes must carry the same policy
    options the runner's direct ssh calls do: accept-new for first
    contact, ConnectTimeout to fail fast, BatchMode=yes to refuse
    password prompts."""
    argv = _rsync_ssh_transport(host)
    assert argv[0] == "-e", "first element must be the rsync -e flag"
    ssh_cmd = argv[1]
    assert "StrictHostKeyChecking=accept-new" in ssh_cmd
    assert "ConnectTimeout=15" in ssh_cmd
    assert "BatchMode=yes" in ssh_cmd


def test_rsync_ssh_transport_inserts_i_when_key_set(host):
    """v5.2.5 fix: ssh_key_path on the host must thread into rsync's
    underlying ssh transport via -i <path>. Without this, the rsync
    calls bare-ssh and fail "Permission denied" on a keys-only host
    even though direct ssh calls (Test SSH button) succeed."""
    keyed = RemoteHost(**{**host.__dict__, "ssh_key_path": "/tmp/my_key"})
    argv = _rsync_ssh_transport(keyed)
    ssh_cmd = argv[1]
    assert "-i" in ssh_cmd
    # Path follows the -i flag.
    parts = ssh_cmd.split()
    i_idx = parts.index("-i")
    assert parts[i_idx + 1] == "/tmp/my_key"


def test_rsync_ssh_transport_omits_i_when_no_key(host):
    argv = _rsync_ssh_transport(host)
    ssh_cmd = argv[1]
    assert "-i" not in ssh_cmd.split()


def test_rsync_ssh_transport_expands_tilde(host):
    """Wizard keys live at ~/.ssh/allwell_<alias> — must expand
    before reaching rsync's ssh."""
    keyed = RemoteHost(**{
        **host.__dict__, "ssh_key_path": "~/.ssh/allwell_bigbox",
    })
    argv = _rsync_ssh_transport(keyed)
    ssh_cmd = argv[1]
    assert "~" not in ssh_cmd, f"tilde should be expanded; got: {ssh_cmd}"
    assert "allwell_bigbox" in ssh_cmd


def test_sync_required_files_passes_e_flag_to_rsync(host, monkeypatch):
    """Integration: the actual rsync invocation for sync_required_files
    includes the -e flag with the key. This is the call site whose
    absence of -e was the root cause of the v5.2.4 user-report bug."""
    keyed = RemoteHost(**{**host.__dict__, "ssh_key_path": "/tmp/k"})
    runner = RemoteRunner(keyed)
    captured = {}

    def fake_ssh_run(self, argv, *, check):
        return MagicMock(stdout="", returncode=0)

    def fake_subprocess_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(DatasetCache, "_ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        "services.remote_runner.subprocess.run", fake_subprocess_run,
    )

    runner.sync_required_files()
    rsync = captured["cmd"]
    assert "-e" in rsync, f"rsync must pass -e ssh ...; got: {rsync}"
    e_idx = rsync.index("-e")
    inner = rsync[e_idx + 1]
    assert "-i" in inner
    assert "/tmp/k" in inner
    assert "StrictHostKeyChecking=accept-new" in inner
