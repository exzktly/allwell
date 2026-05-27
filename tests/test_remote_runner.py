"""Tests for services/remote_runner.py.

The unit tests here exercise the pure-logic surface (cache key
stability, argv rewriting, mode detection, output filter behavior, the
verify step). Anything that would touch real ssh/rsync is mocked.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
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
    _CONDA_ACTIVATE_SNIPPET,
    _conda_run_prefix,
    _filter_channels_in_staging,
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


def test_detect_mixed_mode_zips_plus_folders_for_other_wells(tmp_path):
    """v5.2.21: input dir has zips for some wells AND folders for
    DIFFERENT wells — the signature of a previous run that died
    partway through compress_input_well_folders. Must be recognised
    as MIXED so the staging path can resume."""
    _make_well_zip(tmp_path, "A01.zip", ["NIR.tif"])
    _make_well_zip(tmp_path, "A02.zip", ["NIR.tif"])
    (tmp_path / "B03").mkdir()
    (tmp_path / "B03" / "NIR.tif").write_bytes(b"\0")
    (tmp_path / "B04").mkdir()
    (tmp_path / "B04" / "NIR.tif").write_bytes(b"\0")
    assert _detect_local_input_mode(tmp_path) is LocalInputMode.MIXED


def test_detect_mode_empty_leftover_folders_dont_trigger_mixed(tmp_path):
    """Compress-and-delete sometimes leaves empty folder husks (the
    rename completed, the rmtree raced with another process). An
    empty folder should NOT trigger MIXED detection — it's just
    cleanup debris, not real loose content."""
    _make_well_zip(tmp_path, "A01.zip", ["NIR.tif"])
    (tmp_path / "A01").mkdir()  # empty husk left behind
    (tmp_path / "B02").mkdir()  # another empty husk
    assert _detect_local_input_mode(tmp_path) is LocalInputMode.ZIP


def test_detect_mode_folder_with_existing_zip_for_same_well_is_zip(tmp_path):
    """If a well has BOTH a zip AND a folder of TIFs (the truly
    half-finished case where the zip was written but the folder
    rmtree never ran), the zip wins — same well, no extra work to do
    on the leftover folder. MIXED should be reserved for wells with
    NO existing zip."""
    _make_well_zip(tmp_path, "A01.zip", ["NIR.tif"])
    (tmp_path / "A01").mkdir()
    (tmp_path / "A01" / "NIR.tif").write_bytes(b"\0")
    # No OTHER wells with folders only — so this is ZIP mode.
    assert _detect_local_input_mode(tmp_path) is LocalInputMode.ZIP


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


def test_stage_local_zips_mixed_mode_resumes_from_leftover_folders(tmp_path):
    """v5.2.21: a dropped-drive interrupted a prior local
    compress_input_well_folders run, leaving the input dir with some
    completed zips and some unzipped folders. The staging step must
    pick this up as a resume — zip the leftover folders and combine
    with the existing zips for the union of wells, not silently drop
    the folders by picking ZIP mode."""
    _make_well_zip(tmp_path, "A01.zip", ["A01_NIR_F001.tif"])
    _make_well_zip(tmp_path, "A02.zip", ["A02_NIR_F001.tif"])
    (tmp_path / "B03").mkdir()
    (tmp_path / "B03" / "B03_NIR_F001.tif").write_bytes(b"\xff" * 64)
    (tmp_path / "B03" / "B03_GFP_F001.tif").write_bytes(b"\xff" * 64)
    (tmp_path / "B04").mkdir()
    (tmp_path / "B04" / "B04_NIR_F001.tif").write_bytes(b"\xff" * 64)

    logs: list[str] = []
    zipped = stage_local_zips(
        tmp_path,
        filename_schema="well:channel:fov",
        filename_sep="_",
        log_fn=logs.append,
    )

    assert zipped.mode is LocalInputMode.MIXED
    # Four wells total: 2 existing zips + 2 newly-zipped from folders.
    assert len(zipped.well_zips) == 4
    names = sorted(p.name for p in zipped.well_zips)
    assert names == ["A01.zip", "A02.zip", "B03.zip", "B04.zip"]
    # The resume log line must surface so the user knows what's
    # happening (and which wells are being newly zipped).
    blob = "".join(logs)
    assert "resume detected" in blob
    assert "B03" in blob
    assert "B04" in blob

    # User's existing zips are untouched (path still points at input dir).
    a01 = next(p for p in zipped.well_zips if p.name == "A01.zip")
    assert a01.parent == tmp_path
    # Newly-zipped wells live in the staging dir.
    b03 = next(p for p in zipped.well_zips if p.name == "B03.zip")
    assert b03.parent == zipped.zip_dir
    assert zipped.zip_dir != tmp_path

    # New zips actually contain the source TIFs.
    with zipfile.ZipFile(b03, "r") as zf:
        assert sorted(zf.namelist()) == ["B03_GFP_F001.tif", "B03_NIR_F001.tif"]

    # Cleanup tears down the staging dir.
    assert zipped.cleanup is not None
    zipped.cleanup()
    assert not zipped.zip_dir.exists()
    # The user's zips and folders remain in the input dir post-cleanup.
    assert (tmp_path / "A01.zip").exists()
    assert (tmp_path / "B03" / "B03_NIR_F001.tif").exists()


def test_stage_local_zips_mixed_mode_preserves_user_zips_on_error(tmp_path):
    """If _stage_mixed_mode raises mid-zip (e.g. PermissionError on a
    folder), the user's pre-existing zips MUST be untouched and the
    staging dir MUST be cleaned up — no leaked temp dir, no mutation
    of the input dir."""
    _make_well_zip(tmp_path, "A01.zip", ["NIR.tif"])
    original_bytes = (tmp_path / "A01.zip").read_bytes()
    (tmp_path / "B02").mkdir()
    (tmp_path / "B02" / "NIR.tif").write_bytes(b"\xff" * 32)

    import services.remote_runner as rr

    def fake_zip_well_folder(folder: Path, out_zip: Path) -> int:
        raise PermissionError("drive yanked mid-zip")

    original = rr._zip_well_folder
    rr._zip_well_folder = fake_zip_well_folder
    try:
        with pytest.raises(PermissionError, match="drive yanked"):
            stage_local_zips(
                tmp_path,
                filename_schema="well:channel:fov",
                filename_sep="_",
            )
    finally:
        rr._zip_well_folder = original

    # User's zip is byte-for-byte unchanged.
    assert (tmp_path / "A01.zip").read_bytes() == original_bytes
    # Folder is untouched.
    assert (tmp_path / "B02" / "NIR.tif").exists()
    # No leaked staging dirs.
    leaked = list(Path(tempfile.gettempdir()).glob("allwell-resume-*"))
    assert not leaked, f"staging dir leaked on error: {leaked}"


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
    _filter_channels_in_staging(
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
    _filter_channels_in_staging(
        tmp_path, (z,), LocalInputMode.ZIP,
        keep_tokens={"NIR", "GFP"},
        log_fn=None,
    )
    # No rewrite needed.
    assert z.stat().st_size == size_before
    with zipfile.ZipFile(z, "r") as zf:
        assert sorted(zf.namelist()) == sorted(members)


def test_filter_channels_raises_when_typo_drops_everything(tmp_path):
    """v5.2.18: if a typo'd channel name matches zero members in a
    well, refuse to ship an empty zip. The pipeline downstream would
    crash with a confusing 'no images found' message far from the
    real cause."""
    z = _make_well_zip(
        tmp_path,
        "A01.zip",
        ["Exp_NIR_A01_F001.tif", "Exp_GFP_A01_F001.tif"],
    )
    # User typoed "GP" instead of "GFP" — neither token matches.
    with pytest.raises(RuntimeError, match=r"matched zero images.*A01.*typo"):
        _filter_channels_in_staging(
            tmp_path, (z,), LocalInputMode.ZIP,
            keep_tokens={"NUR", "GP"},  # both typoed
            log_fn=None,
        )


def test_stage_local_zips_with_fluor_filter_preserves_user_input(tmp_path):
    """v5.2.18: stage_local_zips MUST NOT modify the user's original
    input zips. Pre-v5.2.18 it rewrote them in place — a typo'd
    channel name silently deleted frames from the user's dataset.
    Verify the user's zip is byte-for-byte unchanged after staging
    a fluor-filter request."""
    members = [
        "Exp_NIR_A01_F001.tif",
        "Exp_GFP_A01_F001.tif",
        "Exp_mCherry_A01_F001.tif",
    ]
    z = _make_well_zip(tmp_path, "A01.zip", members)
    original_bytes = z.read_bytes()
    original_size = z.stat().st_size
    original_mtime = z.stat().st_mtime

    zipped = stage_local_zips(
        tmp_path,
        filename_schema="experiment:channel:well:fov",
        filename_sep="_",
        nuclear_token="NIR",
        fluor_tokens=["GFP"],  # drops mCherry
    )

    # The user's original file is untouched.
    assert z.read_bytes() == original_bytes
    assert z.stat().st_size == original_size
    assert z.stat().st_mtime == original_mtime
    with zipfile.ZipFile(z, "r") as zf:
        assert sorted(zf.namelist()) == sorted(members)

    # The staged copy lives in a separate temp dir and HAS been filtered.
    assert zipped.zip_dir != tmp_path
    assert len(zipped.well_zips) == 1
    staged = zipped.well_zips[0]
    assert staged.parent == zipped.zip_dir
    with zipfile.ZipFile(staged, "r") as zf:
        staged_members = zf.namelist()
    assert any("NIR" in m for m in staged_members)
    assert any("GFP" in m for m in staged_members)
    assert not any("mCherry" in m for m in staged_members)

    # A cleanup hook is provided so the temp dir gets removed.
    assert zipped.cleanup is not None
    zipped.cleanup()
    assert not zipped.zip_dir.exists()


def test_stage_local_zips_typoed_channel_does_not_corrupt_user_input(tmp_path):
    """v5.2.18 worst-case scenario: user types nonsense channel
    names. The staging must (a) NOT modify the user's input, AND
    (b) raise a helpful error before anything reaches the remote."""
    members = ["Exp_NIR_A01_F001.tif", "Exp_GFP_A01_F001.tif"]
    z = _make_well_zip(tmp_path, "A01.zip", members)
    original_bytes = z.read_bytes()

    with pytest.raises(RuntimeError, match=r"matched zero images"):
        stage_local_zips(
            tmp_path,
            filename_schema="experiment:channel:well:fov",
            filename_sep="_",
            nuclear_token="WRONG",
            fluor_tokens=["ALSO_WRONG"],
        )

    # User's zip is still pristine after the error.
    assert z.read_bytes() == original_bytes
    with zipfile.ZipFile(z, "r") as zf:
        assert sorted(zf.namelist()) == sorted(members)


def test_filter_channels_logs_per_well_progress(tmp_path):
    """v5.2.20: pre-v5.2.20 the channel filter only emitted ONE
    summary line at the end of the whole plate. For a 96-well filter
    that meant total silence for tens of seconds. Now each well
    contributes a progress line so the user can see the filter
    chewing through the plate."""
    # A01 — all kept (no-op). B02 — partial (rewrite). C03 — all kept.
    a01 = _make_well_zip(tmp_path, "A01.zip", ["NIR_F001.tif", "GFP_F001.tif"])
    b02 = _make_well_zip(
        tmp_path, "B02.zip",
        ["NIR_F001.tif", "GFP_F001.tif", "DAPI_F001.tif"],
    )
    c03 = _make_well_zip(tmp_path, "C03.zip", ["NIR_F001.tif", "GFP_F001.tif"])

    lines: list[str] = []
    _filter_channels_in_staging(
        tmp_path, (a01, b02, c03), LocalInputMode.ZIP,
        keep_tokens={"NIR", "GFP"},
        log_fn=lambda s: lines.append(s),
    )
    blob = "".join(lines)
    # Per-well status with position-of-total prefix.
    assert "(1/3): A01" in blob
    assert "(2/3): B02" in blob
    assert "(3/3): C03" in blob
    # No-op wells say "all N kept".
    assert "A01" in blob and "no-op" in blob
    # The rewrite well reports its before→after size.
    assert "B02" in blob and "kept 2/3" in blob
    # End-of-plate summary still fires.
    assert "summary" in blob and "rewrote 1" in blob


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
    # v5.2.13: argv now starts with ``conda run --no-capture-output -n
    # <env> python <script>`` instead of the hard-coded host.remote_python.
    assert remote[:6] == ["conda", "run", "--no-capture-output", "-n", "allwell", "python"]
    assert remote[6] == f"{host.remote_project_dir}/process_microscopy.py"
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


# ── DatasetCache.ensure_uploaded: error path ────────────────────────────────


def _make_zipped(tmp_path, names):
    """Helper: build a ZippedInput with N tiny well-zips."""
    zips = []
    for n in names:
        p = _make_well_zip(tmp_path, n, ["x.tif"])
        p.write_bytes(b"\0" * 1024)
        zips.append(p)
    return ZippedInput(zip_dir=tmp_path, mode=LocalInputMode.ZIP, well_zips=tuple(zips))


def test_ensure_uploaded_raises_on_nonzero_rsync_rc(host, tmp_path, monkeypatch):
    """v5.2.14: pre-v5.2.14 ``ensure_uploaded`` returned even when rsync
    exited non-zero (e.g. rc=23 'partial transfer due to error'), so
    ``spawn()`` happily started the pipeline against an incomplete
    remote input dir. The user saw 'pipeline failed' rather than the
    real upload-side error. Must now raise."""
    zipped = _make_zipped(tmp_path, ["A01.zip", "B02.zip"])
    cache = DatasetCache(host)

    monkeypatch.setattr(DatasetCache, "_ssh_run", lambda self, argv, *, check: MagicMock(stdout="", returncode=0))
    # rsync reports rc=23 ("partial transfer due to error") — non-retry.
    monkeypatch.setattr(
        "services.remote_runner._run_rsync_streaming",
        lambda *a, **kw: (0, 23),
    )

    with pytest.raises(RuntimeError, match="exit code 23"):
        cache.ensure_uploaded(zipped)


def test_ensure_uploaded_raises_when_remote_file_count_mismatches(host, tmp_path, monkeypatch):
    """v5.2.14: belt-and-braces. rsync can exit 0 yet have skipped
    some files (source-side read errors, etc.). Verify count after
    upload and raise on mismatch — the pipeline must not start against
    a half-empty remote dataset dir."""
    zipped = _make_zipped(tmp_path, ["A01.zip", "B02.zip", "C03.zip"])  # 3 wells
    cache = DatasetCache(host)

    # v5.2.16: verification now goes through _ssh_script (preserves
    # ~/ expansion). _ssh_run is still used for mkdir.
    monkeypatch.setattr(
        DatasetCache, "_ssh_run",
        lambda self, argv, *, check: MagicMock(stdout="", returncode=0),
    )
    monkeypatch.setattr(
        DatasetCache, "_ssh_script",
        lambda self, script, *, check: MagicMock(stdout="2\n", returncode=0),
    )
    # rsync says success but lied.
    monkeypatch.setattr(
        "services.remote_runner._run_rsync_streaming",
        lambda *a, **kw: (1024, 0),
    )

    with pytest.raises(RuntimeError, match=r"expected 3 well zip\(s\).*found 2"):
        cache.ensure_uploaded(zipped)


def test_ensure_uploaded_succeeds_when_count_matches(host, tmp_path, monkeypatch):
    """Happy path: rsync rc=0 AND remote count matches local → return
    the cache key tuple, no raise."""
    zipped = _make_zipped(tmp_path, ["A01.zip", "B02.zip"])
    cache = DatasetCache(host)

    monkeypatch.setattr(
        DatasetCache, "_ssh_run",
        lambda self, argv, *, check: MagicMock(stdout="", returncode=0),
    )
    monkeypatch.setattr(
        DatasetCache, "_ssh_script",
        lambda self, script, *, check: MagicMock(stdout="2\n", returncode=0),
    )
    monkeypatch.setattr(
        "services.remote_runner._run_rsync_streaming",
        lambda *a, **kw: (2048, 0),
    )

    key, sent, elapsed = cache.ensure_uploaded(zipped)
    assert key  # non-empty
    assert sent == 2048
    assert elapsed > 0


def test_ensure_uploaded_verification_preserves_tilde_in_script(host, tmp_path, monkeypatch):
    """v5.2.16 regression: the verification ssh call must NOT
    shlex-quote the remote_path, because remote_staging_dir is
    ``~/.allwell-staging`` and single quotes around ``~/...`` kill
    tilde expansion on the remote — the count comes back 0 and the
    user sees 'expected 2 found 0' even though rsync transferred fine.

    Pipe a script via _ssh_script so the remote bash parses ``~/``
    normally."""
    zipped = _make_zipped(tmp_path, ["A01.zip"])
    cache = DatasetCache(host)

    captured = {}

    def fake_ssh_script(self, script, *, check):
        captured["script"] = script
        return MagicMock(stdout="1\n", returncode=0)

    monkeypatch.setattr(
        DatasetCache, "_ssh_run",
        lambda self, argv, *, check: MagicMock(stdout="", returncode=0),
    )
    monkeypatch.setattr(DatasetCache, "_ssh_script", fake_ssh_script)
    monkeypatch.setattr(
        "services.remote_runner._run_rsync_streaming",
        lambda *a, **kw: (1024, 0),
    )

    cache.ensure_uploaded(zipped)
    # The remote staging dir starts with ``~/`` (the default). Bash
    # only expands the tilde when it's unquoted, so the script must
    # contain bare ``~/`` — NOT ``'~/...`` (single-quoted) or
    # ``"~/..."`` (double-quoted).
    assert "~/.allwell-staging" in captured["script"]
    assert "'~/" not in captured["script"]
    assert '"~/' not in captured["script"]
    # And it must actually do the count.
    assert "ls -1" in captured["script"]
    assert "wc -l" in captured["script"]


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


def test_fetcher_observes_well_done_with_logging_prefix(host, tmp_path):
    """v5.2.17 regression: process_microscopy.py emits
    ``log.info("Well %s — done.", well_label)`` with a basicConfig
    formatter that prepends ``YYYY-MM-DD HH:MM:SS  INFO     ``. The
    pre-v5.2.17 regex anchored on ``^`` and used .match(), so the
    timestamp-prefixed line never matched and the Fetcher silently
    pulled zero wells. The user saw 'rsync never completes' after a
    successful remote run because no per-well rsync ever fired.

    The fix uses .search() against an ``(?:Well\\s+)?`` substring
    pattern so the actual production log lines parse correctly."""
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
        # Exact format the basicConfig formatter produces in
        # process_microscopy.py: "%(asctime)s  %(levelname)-8s  %(message)s"
        fetcher.observe("2026-05-27 10:30:45  INFO      Well A01 — done.\n")
        fetcher.observe("2026-05-27 10:31:02  INFO      Well B02 — done.\n")
        # Banner with a timestamp prefix must also still match.
        fetcher.observe("2026-05-27 10:29:00  INFO      Zip mode: 24 well-zip files detected\n")
        fetcher.cancel()
    assert run.expected_wells == {"A01", "B02"}
    assert run.well_count_total == 24


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
    recreated under the destination, and must point at our four files
    as bare relative paths (v5.2.12: no /./ pivot)."""
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
    # Every required file should appear as a bare relative path.
    for required in _REQUIRED_REMOTE_FILES:
        assert required in rsync, \
            f"missing bare-relative rsync source for {required}"


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


def test_sync_required_files_uses_bare_relative_sources_with_cwd(host, monkeypatch):
    """v5.2.12: rsync sources are bare relative paths
    (``process_microscopy.py``, ``_Docs/requirements-remote.txt``)
    and the subprocess.run cwd is the repo root. Pre-v5.2.12 we used
    absolute paths with the ``/./`` pivot, which older rsync versions
    (macOS bundled 2.6.9) mis-handled — they preserved the FULL local
    path on the remote, creating directories like
    ``~/allwell/Users/me/allwell/_Docs/requirements-remote.txt`` instead
    of ``~/allwell/_Docs/requirements-remote.txt``.

    Bare-relative + cwd works on every rsync version we've seen.
    """
    runner = RemoteRunner(host)
    captured: dict = {}

    def fake_ssh_run(self, argv, *, check):
        return MagicMock(stdout="", returncode=0)

    def fake_subprocess_run(cmd, **kwargs):
        captured["rsync_cmd"] = list(cmd)
        captured["cwd"] = kwargs.get("cwd")
        return MagicMock(returncode=0, stdout="", stderr="")

    # Pin repo_root so the assertion below knows the expected cwd.
    fake_root = Path("/Users/me/allwell")
    monkeypatch.setattr(
        "services.remote_runner.repo_root",
        lambda: fake_root,
    )
    # The pre-flight check inside sync_required_files calls
    # ``(root / f).is_file()`` for each required file. ``fake_root``
    # doesn't actually exist on disk, so we patch Path.is_file to
    # return True for any path under it.
    real_is_file = Path.is_file

    def fake_is_file(self):
        try:
            self.relative_to(fake_root)
            return True
        except ValueError:
            return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", fake_is_file)
    monkeypatch.setattr(DatasetCache, "_ssh_run", fake_ssh_run)
    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_subprocess_run)

    runner.sync_required_files()

    rsync = captured["rsync_cmd"]
    # cwd must be the repo root so rsync resolves bare relative paths.
    assert captured["cwd"] == "/Users/me/allwell", \
        f"subprocess cwd should be the repo root; got: {captured['cwd']}"
    # Each required file should appear as a BARE RELATIVE path (no
    # leading /, no /./ pivot). That's the v5.2.12 fix.
    for required in _REQUIRED_REMOTE_FILES:
        assert required in rsync, \
            f"missing bare-relative source {required!r}; got: {rsync}"
        # And critically: no absolute version of the path is in the
        # argv (would mean we regressed back to the broken pre-v5.2.12
        # construction).
        for arg in rsync:
            assert not arg.endswith(f"/./{required}"), \
                f"v5.2.12 regression: /./ pivot found in {arg!r}"


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


# ── v5.2.10: rsync --info gating for legacy rsync ──────────────────────────


def test_rsync_supports_info_flag_recognises_modern_rsync(monkeypatch):
    """rsync 3.2.7 (current default on most Linux + recent Git Bash)
    must be detected as supporting --info=...."""
    from services.remote_runner import _rsync_supports_info_flag, _rsync_version
    _rsync_version.cache_clear()
    monkeypatch.setattr(
        "services.remote_runner.subprocess.run",
        MagicMock(return_value=subprocess.CompletedProcess(
            [], 0,
            stdout="rsync  version 3.2.7  protocol version 31\n",
            stderr="",
        )),
    )
    assert _rsync_supports_info_flag() is True


def test_rsync_supports_info_flag_rejects_legacy_rsync(monkeypatch):
    """macOS bundles rsync 2.6.9. Detect it and skip --info=...
    rather than blowing up bootstrap with 'unknown option'."""
    from services.remote_runner import _rsync_supports_info_flag, _rsync_version
    _rsync_version.cache_clear()
    monkeypatch.setattr(
        "services.remote_runner.subprocess.run",
        MagicMock(return_value=subprocess.CompletedProcess(
            [], 0,
            stdout="rsync  version 2.6.9  protocol version 29\n",
            stderr="",
        )),
    )
    assert _rsync_supports_info_flag() is False


def test_rsync_supports_info_flag_fails_safe_when_rsync_missing(monkeypatch):
    """If rsync isn't on PATH the version probe raises; fail-safe to
    False so the caller omits --info= and doesn't crash."""
    from services.remote_runner import _rsync_supports_info_flag, _rsync_version
    _rsync_version.cache_clear()
    monkeypatch.setattr(
        "services.remote_runner.subprocess.run",
        MagicMock(side_effect=FileNotFoundError("rsync")),
    )
    assert _rsync_supports_info_flag() is False


def test_sync_required_files_omits_info_flag_on_legacy_rsync(host, monkeypatch):
    """Integration: a host with legacy rsync gets the bare argv
    (no --info=stats0), avoiding the 'unknown option' exit 1 that
    blocked the user's bootstrap."""
    from services.remote_runner import _rsync_supports_info_flag, _rsync_version
    _rsync_version.cache_clear()
    captured = {}

    runner = RemoteRunner(host)
    monkeypatch.setattr(
        DatasetCache, "_ssh_run",
        lambda self, argv, *, check: MagicMock(stdout="", returncode=0),
    )

    def fake_run(cmd, **kwargs):
        # First call: rsync --version → return legacy version.
        # Second call: the actual rsync — capture and succeed.
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout="rsync  version 2.6.9  protocol version 29\n",
                stderr="",
            )
        captured["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_run)
    runner.sync_required_files()
    assert "--info=stats0" not in captured["cmd"], \
        f"--info=stats0 should be omitted on legacy rsync; argv: {captured['cmd']}"


def test_sync_required_files_includes_info_flag_on_modern_rsync(host, monkeypatch):
    """Inverse of the above — modern rsync still gets the --info
    flag so the GUI's terser log output is preserved."""
    from services.remote_runner import _rsync_supports_info_flag, _rsync_version
    _rsync_version.cache_clear()
    captured = {}
    runner = RemoteRunner(host)
    monkeypatch.setattr(
        DatasetCache, "_ssh_run",
        lambda self, argv, *, check: MagicMock(stdout="", returncode=0),
    )

    def fake_run(cmd, **kwargs):
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout="rsync  version 3.2.7  protocol version 31\n",
                stderr="",
            )
        captured["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_run)
    runner.sync_required_files()
    assert "--info=stats0" in captured["cmd"]


# ── v5.2.15: --append-verify gating for legacy rsync ───────────────────────


def test_rsync_supports_append_verify_recognises_modern_rsync(monkeypatch):
    """--append-verify lands in rsync 3.0.0 (2008). Anything newer
    must be detected as supporting it."""
    from services.remote_runner import _rsync_supports_append_verify, _rsync_version
    _rsync_version.cache_clear()
    monkeypatch.setattr(
        "services.remote_runner.subprocess.run",
        MagicMock(return_value=subprocess.CompletedProcess(
            [], 0,
            stdout="rsync  version 3.2.7  protocol version 31\n",
            stderr="",
        )),
    )
    assert _rsync_supports_append_verify() is True


def test_rsync_supports_append_verify_rejects_legacy_rsync(monkeypatch):
    """macOS's bundled rsync 2.6.9 rejects --append-verify with
    'Unrecognized option --append-verify' → exit 1. This was the
    failure that motivated v5.2.15 (user's well-zip upload died
    with exit 1 right after the v5.2.13 conda-run fix landed)."""
    from services.remote_runner import _rsync_supports_append_verify, _rsync_version
    _rsync_version.cache_clear()
    monkeypatch.setattr(
        "services.remote_runner.subprocess.run",
        MagicMock(return_value=subprocess.CompletedProcess(
            [], 0,
            stdout="rsync  version 2.6.9  protocol version 29\n",
            stderr="",
        )),
    )
    assert _rsync_supports_append_verify() is False


def test_ensure_uploaded_omits_append_verify_on_legacy_rsync(host, tmp_path, monkeypatch):
    """Integration: rsync 2.6.9 must get a bare command WITHOUT
    --append-verify so it doesn't blow up with 'Unrecognized option'."""
    from services.remote_runner import _rsync_version
    _rsync_version.cache_clear()

    zipped = _make_zipped(tmp_path, ["A01.zip"])
    cache = DatasetCache(host)
    captured = {}

    monkeypatch.setattr(
        DatasetCache, "_ssh_run",
        lambda self, argv, *, check: MagicMock(stdout="", returncode=0),
    )
    monkeypatch.setattr(
        DatasetCache, "_ssh_script",
        lambda self, script, *, check: MagicMock(stdout="1\n", returncode=0),
    )
    monkeypatch.setattr(
        "services.remote_runner.subprocess.run",
        MagicMock(return_value=subprocess.CompletedProcess(
            [], 0,
            stdout="rsync  version 2.6.9  protocol version 29\n",
            stderr="",
        )),
    )

    def capture_streaming(cmd, **kw):
        captured["cmd"] = list(cmd)
        return (1024, 0)

    monkeypatch.setattr("services.remote_runner._run_rsync_streaming", capture_streaming)

    cache.ensure_uploaded(zipped)
    assert "--append-verify" not in captured["cmd"], (
        f"--append-verify should be omitted on legacy rsync; argv: {captured['cmd']}"
    )


def test_ensure_uploaded_includes_append_verify_on_modern_rsync(host, tmp_path, monkeypatch):
    """Inverse of the above — modern rsync keeps --append-verify so
    resumed transfers re-verify the existing tail by checksum."""
    from services.remote_runner import _rsync_version
    _rsync_version.cache_clear()

    zipped = _make_zipped(tmp_path, ["A01.zip"])
    cache = DatasetCache(host)
    captured = {}

    monkeypatch.setattr(
        DatasetCache, "_ssh_run",
        lambda self, argv, *, check: MagicMock(stdout="", returncode=0),
    )
    monkeypatch.setattr(
        DatasetCache, "_ssh_script",
        lambda self, script, *, check: MagicMock(stdout="1\n", returncode=0),
    )
    monkeypatch.setattr(
        "services.remote_runner.subprocess.run",
        MagicMock(return_value=subprocess.CompletedProcess(
            [], 0,
            stdout="rsync  version 3.2.7  protocol version 31\n",
            stderr="",
        )),
    )

    def capture_streaming(cmd, **kw):
        captured["cmd"] = list(cmd)
        return (1024, 0)

    monkeypatch.setattr("services.remote_runner._run_rsync_streaming", capture_streaming)

    cache.ensure_uploaded(zipped)
    assert "--append-verify" in captured["cmd"]


def test_ensure_uploaded_error_message_uses_actual_attempt_count(host, tmp_path, monkeypatch):
    """v5.2.15: pre-v5.2.15 the error always said 'after 4 attempts'
    even when rc=1 (non-retry code) broke after a single try, which
    misled debugging. Verify the message now reflects the real count
    AND points to the rsync: log line as the diagnostic."""
    from services.remote_runner import _rsync_version
    _rsync_version.cache_clear()

    zipped = _make_zipped(tmp_path, ["A01.zip"])
    cache = DatasetCache(host)

    monkeypatch.setattr(
        DatasetCache, "_ssh_run",
        lambda self, argv, *, check: MagicMock(stdout="0\n", returncode=0),
    )
    monkeypatch.setattr(
        "services.remote_runner.subprocess.run",
        MagicMock(return_value=subprocess.CompletedProcess(
            [], 0,
            stdout="rsync  version 3.2.7  protocol version 31\n",
            stderr="",
        )),
    )
    # rc=1 is non-retry — must break on the first attempt.
    monkeypatch.setattr(
        "services.remote_runner._run_rsync_streaming",
        lambda *a, **kw: (0, 1),
    )

    with pytest.raises(RuntimeError) as exc:
        cache.ensure_uploaded(zipped)
    msg = str(exc.value)
    assert "1 attempt" in msg and "4 attempts" not in msg
    # Diagnostic breadcrumb so the user knows where to look.
    assert "rsync:" in msg


# ── v5.2.11: ~ quoting + auto_gc gating + cleanup default ───────────────────


def test_remote_quote_preserves_tilde_expansion():
    """The bug v5.2.11 fixes: stock shlex.quote("~/foo") → "'~/foo'"
    which the remote bash treats as literal, creating a directory
    called ~. _remote_quote routes ~/foo through "$HOME"/foo so the
    remote shell expands $HOME properly. The inner shlex.quote of the
    remainder may or may not single-quote depending on shell-special
    chars — what matters is that the LEADING ~ is replaced with the
    expanding form."""
    from services.remote_runner import _remote_quote
    # Must start with "$HOME" so the remote shell expands it.
    assert _remote_quote("~/foo").startswith('"$HOME"')
    assert "foo" in _remote_quote("~/foo")
    assert _remote_quote("~") == '"$HOME"'
    # Crucially: no version of the output contains '~' (the bug).
    assert "~" not in _remote_quote("~/foo")
    # Non-tilde paths fall back to plain shlex.quote semantics.
    assert _remote_quote("simple") == "simple"
    # Paths with spaces still get single-quoted; we don't break that case.
    assert "'" in _remote_quote("/path with spaces")


def test_remote_join_renders_full_command():
    """The full join: mkdir -p ~/.allwell-staging produces a command
    containing "$HOME" rather than a quoted literal ~ — the remote
    shell will then expand $HOME instead of creating a ~ directory."""
    from services.remote_runner import _remote_join
    cmd = _remote_join(["mkdir", "-p", "~/.allwell-staging"])
    assert cmd.startswith("mkdir -p ")
    assert '"$HOME"' in cmd
    assert ".allwell-staging" in cmd
    # No single-quoted ~ anywhere (the bug).
    assert "'~/" not in cmd
    assert "'~'" not in cmd


def test_looks_like_rsync_filename_accepts_zip_paths():
    """v5.2.20: rsync --info=name1 emits bare filenames on their own
    line. The heuristic must accept paths a user would actually see
    (well zips, required-files like process_microscopy.py and
    subfolder/file.txt)."""
    from services.remote_runner import _looks_like_rsync_filename
    assert _looks_like_rsync_filename("A01.zip") is True
    assert _looks_like_rsync_filename("process_microscopy.py") is True
    assert _looks_like_rsync_filename("_Docs/requirements-remote.txt") is True


def test_looks_like_rsync_filename_rejects_progress_and_status_lines():
    """The heuristic must reject the noise rsync emits alongside
    filename lines — progress percentages, byte stats, error
    markers, the file-list announcement, blanks, and the wider
    stats2 / status surface that pre-cleanup leaked through."""
    from services.remote_runner import _looks_like_rsync_filename
    # Empties.
    assert _looks_like_rsync_filename("") is False
    assert _looks_like_rsync_filename(" ") is False
    # Progress line — starts with whitespace + numbers + percent.
    assert _looks_like_rsync_filename("    12,345  50%  1.20MB/s    0:00:01") is False
    # The 'sent X / Total ...' summary (capital and lowercase variants).
    assert _looks_like_rsync_filename("sent 1,234,567 bytes  received 567 bytes  ...") is False
    assert _looks_like_rsync_filename("Total bytes sent: 1,234,567") is False
    assert _looks_like_rsync_filename("total size is 1,234  speedup is 1.00") is False
    # Errors and announcements.
    assert _looks_like_rsync_filename("rsync: connection unexpectedly closed") is False
    assert _looks_like_rsync_filename("rsync error: error in socket IO (code 10)") is False
    assert _looks_like_rsync_filename("building file list ... done") is False
    assert _looks_like_rsync_filename("sending incremental file list") is False
    # --info=stats2 multi-line footer — these all start with capitals
    # and avoid the legacy prefix list. Caught now because they
    # contain whitespace.
    assert _looks_like_rsync_filename("Number of files: 96") is False
    assert _looks_like_rsync_filename("Number of regular files transferred: 12") is False
    assert _looks_like_rsync_filename("Literal data: 1,234,567 bytes") is False
    assert _looks_like_rsync_filename("Matched data: 0 bytes") is False
    assert _looks_like_rsync_filename("File list size: 123") is False
    assert _looks_like_rsync_filename("File list generation time: 0.001 seconds") is False
    # Non-fatal warnings.
    assert _looks_like_rsync_filename("file has vanished: bar.tmp") is False
    assert _looks_like_rsync_filename("created directory /remote/datasets/abc") is False
    assert _looks_like_rsync_filename("deleting some_obsolete.zip") is False
    assert _looks_like_rsync_filename("*deleting subdir/old.tif") is False
    assert _looks_like_rsync_filename("WARNING: foo failed verification") is False
    assert _looks_like_rsync_filename("IO error encountered -- skipping file deletion") is False
    assert _looks_like_rsync_filename("skipping non-regular file \"foo\"") is False


def test_ensure_uploaded_upload_cmd_includes_name1_on_modern_rsync(host, tmp_path, monkeypatch):
    """v5.2.20: per-file logging requires ``--info=name1`` so rsync
    emits filenames; assert the upload command actually includes
    that flag on rsync 3.1+."""
    from services.remote_runner import _rsync_version
    _rsync_version.cache_clear()

    z = _make_well_zip(tmp_path, "A01.zip", ["x.tif"])
    z.write_bytes(b"\0" * 1024)
    zipped = ZippedInput(zip_dir=tmp_path, mode=LocalInputMode.ZIP, well_zips=(z,))
    cache = DatasetCache(host)
    captured = {}

    monkeypatch.setattr(
        DatasetCache, "_ssh_run",
        lambda self, argv, *, check: MagicMock(stdout="", returncode=0),
    )
    monkeypatch.setattr(
        DatasetCache, "_ssh_script",
        lambda self, script, *, check: MagicMock(stdout="1\n", returncode=0),
    )
    monkeypatch.setattr(
        "services.remote_runner.subprocess.run",
        MagicMock(return_value=subprocess.CompletedProcess(
            [], 0,
            stdout="rsync  version 3.2.7  protocol version 31\n",
            stderr="",
        )),
    )

    def capture_streaming(cmd, **kw):
        captured["cmd"] = list(cmd)
        return (1024, 0)

    monkeypatch.setattr("services.remote_runner._run_rsync_streaming", capture_streaming)

    cache.ensure_uploaded(zipped)
    info_flag = next((a for a in captured["cmd"] if a.startswith("--info=")), "")
    assert "name1" in info_flag, (
        f"--info= must include 'name1' so rsync emits filename lines for per-file logging; "
        f"got {info_flag!r}"
    )


def test_conda_run_prefix_default_env():
    """v5.2.13: the conda-run prefix uses ``--no-capture-output`` (so
    progress streams through) and names the ``allwell`` env."""
    prefix = _conda_run_prefix()
    assert prefix == ["conda", "run", "--no-capture-output", "-n", "allwell"]


def test_conda_run_prefix_custom_env():
    """Future-proofing: the helper accepts a custom env name."""
    prefix = _conda_run_prefix("foo")
    assert prefix[-1] == "foo"


def test_conda_activate_snippet_discovers_conda_sh():
    """The activate snippet must check the bootstrap-managed location
    first (``$HOME/miniforge3/etc/profile.d/conda.sh``) AND fall back to
    ``conda info --base`` for hosts with a pre-existing system conda
    (HPC modules, ``/opt/conda``, etc.). This is exactly the discovery
    path that failed pre-v5.2.13 when the user's HPC had system conda."""
    snippet = _CONDA_ACTIVATE_SNIPPET
    assert "$HOME/miniforge3/etc/profile.d/conda.sh" in snippet
    assert "conda info --base" in snippet
    # Sources the script so ``conda`` becomes available as a shell
    # function in the current process.
    assert '. "$CONDA_SH"' in snippet
    # Hard-fails with a clear log line on hosts where neither path
    # turns up conda.sh, rather than producing an obscure
    # ``conda: command not found`` later.
    assert "cannot find conda.sh" in snippet
    assert "exit 127" in snippet


def test_gc_is_disabled_when_auto_gc_false(host, monkeypatch):
    """v5.2.11: shared-machine safety. gc() must do NOTHING when
    auto_gc=False (the default)."""
    no_gc_host = RemoteHost(**{**host.__dict__, "auto_gc": False})
    cache = DatasetCache(no_gc_host)
    ssh_calls = []
    monkeypatch.setattr(
        DatasetCache, "_ssh_run",
        lambda self, argv, *, check: ssh_calls.append(argv) or MagicMock(stdout="", returncode=0),
    )
    monkeypatch.setattr(
        DatasetCache, "_ssh_script",
        lambda self, script, *, check: ssh_calls.append(script) or MagicMock(stdout="", returncode=0),
    )

    log_lines: list[str] = []
    cache.gc(1024 * 1024 * 1024, log_fn=log_lines.append)

    # No ssh calls made: no listing, no eviction.
    assert ssh_calls == []
    # User informed that gc was skipped + how to enable.
    assert any("auto_gc disabled" in line for line in log_lines)


def test_gc_runs_when_auto_gc_true(host, monkeypatch):
    """Inverse of the above — power user explicitly enables auto_gc,
    so the listing query DOES run."""
    gc_host = RemoteHost(**{**host.__dict__, "auto_gc": True})
    cache = DatasetCache(gc_host)
    ssh_scripts: list[str] = []

    def fake_script(self, script, *, check):
        ssh_scripts.append(script)
        # Return empty listing → no eviction needed.
        return MagicMock(stdout="", returncode=0)

    monkeypatch.setattr(DatasetCache, "_ssh_script", fake_script)
    cache.gc(1024 * 1024 * 1024)
    # gc tried to list datasets.
    assert ssh_scripts, "gc should have queried the remote when auto_gc=True"


def test_verify_and_cleanup_defaults_to_none(host, tmp_path, monkeypatch):
    """v5.2.11: default cleanup_mode is 'none' (no auto-delete).
    User opts in explicitly via the GUI checkbox or
    --cleanup-remote=run / =all on the CLI."""
    from services.remote_runner import _RemoteRun
    runner = RemoteRunner(host)
    run = _RemoteRun(
        run_id="x", cache_key="k",
        remote_input_dir="/r/in", remote_output_dir="/r/out",
        remote_pidfile="/r/pid", local_output_dir=tmp_path,
        local_input_mode=LocalInputMode.ZIP,
    )
    run.expected_wells = {"A01"}
    runner._run = run  # noqa: SLF001
    (tmp_path / "x_A01.csv").write_text("ok\n")

    rm_calls = []
    monkeypatch.setattr(
        DatasetCache, "_ssh_run",
        lambda self, argv, *, check: (
            rm_calls.append(list(argv)) if argv and argv[0] == "rm" else None
        ) or MagicMock(stdout="", returncode=0),
    )
    monkeypatch.setattr(
        RemoteRunner, "_rsync_optional",
        lambda self, src, dest, log_fn: None,
    )

    # Default call (no cleanup_mode arg)
    runner.verify_and_cleanup(ok=True, log_fn=lambda _m: None)
    # No rm calls — the default doesn't delete anything.
    assert rm_calls == [], \
        f"v5.2.11 default must not delete; observed rm calls: {rm_calls}"
