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
    LocalInputMode,
    OutputFilter,
    RemoteRunner,
    ZippedInput,
    _REQUIRED_REMOTE_FILES,
    _compose_remote_cmd,
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
from services.remote_fetch import Fetcher
from tests._repo_scan import parse_source
from services.remote_fetch import FetchedOutputVerifier
from services import remote_fetch

# Every test here drives real `RemoteRunner` / `Fetcher` code against a host
# that does not exist. Answer the ssh and rsync primitives directly rather than
# spawning them to find that out (tests/conftest.py::offline_transport).
pytestmark = pytest.mark.usefixtures("offline_transport")


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
    the folders by picking ZIP mode.

    v5.2.24: new zips now persist in the input dir so the user can
    see them and so the next run picks them up as plain ZIP mode."""
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
    # v5.2.24: the log says where the new zips landed.
    assert "persisted to input dir" in blob

    # v5.2.24: ALL zips — existing AND newly-zipped — live in the
    # user's input dir, not in a throwaway tempdir.
    assert zipped.zip_dir == tmp_path
    for p in zipped.well_zips:
        assert p.parent == tmp_path, f"{p} should be in input dir"
    # Concrete file existence check, post-call.
    assert (tmp_path / "B03.zip").exists()
    assert (tmp_path / "B04.zip").exists()

    # New zips actually contain the source TIFs.
    b03 = tmp_path / "B03.zip"
    with zipfile.ZipFile(b03, "r") as zf:
        assert sorted(zf.namelist()) == ["B03_GFP_F001.tif", "B03_NIR_F001.tif"]

    # No tempdir was created → no cleanup callback needed.
    assert zipped.cleanup is None
    # Each source folder is removed the moment its own archive is published,
    # so the input dir never holds two full copies of a well at once. The
    # archive beside it is complete — `_zip_well_folder` publishes atomically.
    assert not (tmp_path / "B03").exists()
    assert not (tmp_path / "B04").exists()
    assert "folder removed" in blob


def test_stage_local_zips_mixed_mode_persisted_zips_survive_for_next_run(tmp_path):
    """v5.2.24: a second call to stage_local_zips after the first
    one wrote new zips must see plain ZIP mode (not MIXED) — the
    persistence is what makes the resume self-healing."""
    _make_well_zip(tmp_path, "A01.zip", ["NIR.tif"])
    (tmp_path / "B02").mkdir()
    (tmp_path / "B02" / "NIR.tif").write_bytes(b"\xff" * 32)

    # First call: writes B02.zip into tmp_path.
    first = stage_local_zips(
        tmp_path,
        filename_schema="well:channel",
        filename_sep="_",
    )
    assert first.mode is LocalInputMode.MIXED
    assert (tmp_path / "B02.zip").exists()

    # Second call: same input dir. The detector now sees zips for
    # both wells, so MIXED → ZIP. No re-zipping happens (the
    # existing zip wins per v5.2.21 same-well precedence).
    second = stage_local_zips(
        tmp_path,
        filename_schema="well:channel",
        filename_sep="_",
    )
    assert second.mode is LocalInputMode.ZIP
    assert second.cleanup is None
    assert set(p.name for p in second.well_zips) == {"A01.zip", "B02.zip"}


def test_stage_local_zips_mixed_mode_preserves_user_zips_on_error(tmp_path):
    """If _stage_mixed_mode raises mid-zip (e.g. PermissionError on
    a folder), the user's pre-existing zips MUST be untouched. Any
    successfully-written new zips persist (resume behavior). No
    leaked staging tempdir."""
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
                filename_schema="well:channel",
                filename_sep="_",
            )
    finally:
        rr._zip_well_folder = original

    # User's existing zip is byte-for-byte unchanged.
    assert (tmp_path / "A01.zip").read_bytes() == original_bytes
    # Folder is untouched.
    assert (tmp_path / "B02" / "NIR.tif").exists()
    # No partial zip survived (atomic rename inside _zip_well_folder).
    assert not (tmp_path / "B02.zip").exists()
    # No leaked staging dirs in /tmp.
    leaked = list(Path(tempfile.gettempdir()).glob("allwell-resume-*"))
    assert not leaked, f"staging dir leaked on error: {leaked}"


def test_stage_local_zips_mixed_mode_with_fluor_filter_still_copies_to_tempdir(tmp_path):
    """v5.2.18 invariant: with a channel filter, the filter must NOT
    mutate the user's input dir. New zips still land in input_dir
    (for the user to see), but the FILTERED versions go into a temp
    staging dir and the originals stay full-channel."""
    _make_well_zip(
        tmp_path, "A01.zip",
        ["A01_NIR.tif", "A01_GFP.tif", "A01_mCherry.tif"],
    )
    a01_before = (tmp_path / "A01.zip").read_bytes()
    (tmp_path / "B02").mkdir()
    (tmp_path / "B02" / "B02_NIR.tif").write_bytes(b"\xff" * 32)
    (tmp_path / "B02" / "B02_GFP.tif").write_bytes(b"\xff" * 32)
    (tmp_path / "B02" / "B02_mCherry.tif").write_bytes(b"\xff" * 32)

    zipped = stage_local_zips(
        tmp_path,
        filename_schema="well:channel",
        filename_sep="_",
        nuclear_token="NIR",
        fluor_tokens=["GFP"],  # drops mCherry
    )

    # The newly-zipped well lives in input_dir AND retains full
    # channel set (filter ran on a copy, not on this file).
    b02_in_input = tmp_path / "B02.zip"
    assert b02_in_input.exists()
    with zipfile.ZipFile(b02_in_input, "r") as zf:
        assert "B02_mCherry.tif" in zf.namelist()

    # User's existing A01.zip in input_dir is byte-for-byte unchanged.
    assert (tmp_path / "A01.zip").read_bytes() == a01_before

    # The returned well_zips point at the FILTERED copies in the
    # temp staging dir.
    assert zipped.zip_dir != tmp_path
    assert zipped.cleanup is not None
    for p in zipped.well_zips:
        assert p.parent == zipped.zip_dir
    # Filtered copies dropped mCherry.
    with zipfile.ZipFile(zipped.zip_dir / "B02.zip", "r") as zf:
        assert "B02_mCherry.tif" not in zf.namelist()

    zipped.cleanup()
    assert not zipped.zip_dir.exists()
    # Input dir survives cleanup.
    assert (tmp_path / "A01.zip").exists()
    assert (tmp_path / "B02.zip").exists()


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


def test_client_scratch_default_matches_the_pipelines(monkeypatch, tmp_path):
    """One scratch default across the app, not two.

    ``pipeline.io.scratch_base_for`` and ``remote_scratch._scratch_dir`` cannot
    share a constant (the client must not import the imaging stack), so they are
    pinned here instead. They used to disagree — the client fell back to the OS
    temp dir, which on macOS is an opaque ``/var/folders/…/T`` path and on Linux
    is often a tmpfs — and a remote run then staged gigabytes somewhere the user
    had never chosen and could not find.
    """
    from pipeline.io import DEFAULT_SCRATCH_SUBDIR
    from services.remote_runner import _scratch_dir

    monkeypatch.delenv("ALLWELL_SCRATCH_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert _scratch_dir() == tmp_path / DEFAULT_SCRATCH_SUBDIR


def test_client_scratch_honours_the_override(monkeypatch, tmp_path):
    from services.remote_runner import _scratch_dir

    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(tmp_path / "elsewhere"))
    assert _scratch_dir() == tmp_path / "elsewhere"


def test_a_filter_that_drops_nothing_stages_nothing(monkeypatch, tmp_path):
    """The whole-plate case: no local copy, upload straight from the input.

    The Analyze tab always asks for selective channels, so a run whose channel
    list happens to cover every image in the plate reaches the filtered path
    with a filter that drops nothing. That used to copy the entire plate into
    the OS temp dir — gigabytes onto the local scratch volume, read off a NAS,
    to produce archives byte-identical to the ones already on disk — before a
    single byte went to the remote.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(scratch))
    src = tmp_path / "in"
    src.mkdir()
    a01 = _make_well_zip(src, "A01.zip", ["Exp_NIR_A01.tif", "Exp_GFP_A01.tif"])
    b02 = _make_well_zip(src, "B02.zip", ["Exp_NIR_B02.tif", "Exp_GFP_B02.tif"])

    zipped = stage_local_zips(
        src,
        filename_schema="experiment:channel:well",
        filename_sep="_",
        nuclear_token="NIR",
        fluor_tokens=["GFP"],            # keeps every member of both archives
    )

    assert zipped.well_zips == (a01, b02)
    assert zipped.zip_dir == src
    assert zipped.cleanup is None
    assert list(scratch.glob("allwell-stage-*")) == []


def test_junk_members_never_force_a_rewrite(monkeypatch, tmp_path):
    """A `.DS_Store` is not a channel, so the channel filter has no say on it.

    Reported from a live run: 40 archives holding only the two requested
    channels still reported "rewriting 40 well archive(s)" and "keeps 276/279".
    The three extras per zip were a macOS `.DS_Store` and two directory entries.
    Letting the channel filter drop them made every archive look changed, which
    rebuilt the entire plate into scratch to remove a few hundred bytes a well.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(scratch))
    src = tmp_path / "in"
    src.mkdir()
    for well in ("C02", "C03"):
        z = src / f"{well}.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for ch in ("RFP", "GFP"):
                zf.writestr(f"Exp_{ch}_{well}_f0.tif", b"\0" * 128)
            zf.writestr(".DS_Store", b"junk")          # macOS
            zf.writestr(f"{well}/", b"")               # a directory entry
            zf.writestr("._Exp_RFP.tif", b"resource")  # AppleDouble sidecar

    zipped = stage_local_zips(
        src,
        filename_schema="experiment:channel:well:fov",
        filename_sep="_",
        nuclear_token="RFP",
        fluor_tokens=["GFP"],
    )

    assert sorted(p.name for p in zipped.well_zips) == ["C02.zip", "C03.zip"]
    assert all(p.parent == src for p in zipped.well_zips)
    assert zipped.cleanup is None
    assert list(scratch.glob("allwell-stage-*")) == []


def test_a_real_channel_drop_still_rewrites_past_the_junk(tmp_path, monkeypatch):
    """The junk must not mask a genuine drop, and is not carried into the copy."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(scratch))
    src = tmp_path / "in"
    src.mkdir()
    with zipfile.ZipFile(src / "C02.zip", "w") as zf:
        for ch in ("RFP", "GFP", "Cy5"):
            zf.writestr(f"Exp_{ch}_C02_f0.tif", b"\0" * 128)
        zf.writestr(".DS_Store", b"junk")

    zipped = stage_local_zips(
        src,
        filename_schema="experiment:channel:well:fov",
        filename_sep="_",
        nuclear_token="RFP",
        fluor_tokens=["GFP"],
    )

    staged = zipped.well_zips[0]
    assert staged.parent != src
    with zipfile.ZipFile(staged) as zf:
        assert sorted(zf.namelist()) == ["Exp_GFP_C02_f0.tif", "Exp_RFP_C02_f0.tif"]


def test_a_typoed_channel_still_fails_even_with_junk_present(tmp_path):
    """The zero-match guard reads the images, so junk cannot satisfy it."""
    src = tmp_path / "in"
    src.mkdir()
    with zipfile.ZipFile(src / "C02.zip", "w") as zf:
        zf.writestr("Exp_RFP_C02_f0.tif", b"\0" * 128)
        zf.writestr(".DS_Store", b"junk")

    with pytest.raises(RuntimeError, match="matched zero images"):
        stage_local_zips(
            src,
            filename_schema="experiment:channel:well:fov",
            filename_sep="_",
            nuclear_token="NIR",          # not in these filenames
            fluor_tokens=["GP"],          # typo
        )


def test_only_the_archives_that_change_are_staged(monkeypatch, tmp_path):
    """A mixed plate: the well that loses a channel is rewritten, the other
    is uploaded from the input untouched."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(scratch))
    src = tmp_path / "in"
    src.mkdir()
    intact = _make_well_zip(src, "A01.zip", ["Exp_NIR_A01.tif", "Exp_GFP_A01.tif"])
    _make_well_zip(
        src, "B02.zip",
        ["Exp_NIR_B02.tif", "Exp_GFP_B02.tif", "Exp_mCherry_B02.tif"],
    )

    zipped = stage_local_zips(
        src,
        filename_schema="experiment:channel:well",
        filename_sep="_",
        nuclear_token="NIR",
        fluor_tokens=["GFP"],            # only B02 loses anything
    )

    by_name = {p.name: p for p in zipped.well_zips}
    assert by_name["A01.zip"] == intact          # the source path itself
    assert by_name["B02.zip"].parent == zipped.zip_dir != src
    with zipfile.ZipFile(by_name["B02.zip"], "r") as zf:
        assert not any("mCherry" in m for m in zf.namelist())
    # The staging dir holds only the rewritten archive.
    assert sorted(p.name for p in zipped.zip_dir.iterdir()) == ["B02.zip"]
    assert zipped.cleanup is not None
    zipped.cleanup()
    # Cleaning up staging must not touch the input.
    assert intact.exists()


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


# ── v5.2.28: Stop button actually interrupts an in-flight rsync ───────────


def test_rsync_with_retry_cancels_when_stop_event_set_before_call():
    """If the stop event is already set when ``_rsync_with_retry`` is
    entered, it short-circuits without spawning rsync at all and
    returns rc=130 (SIGINT-style)."""
    import threading
    from services.remote_runner import _rsync_with_retry
    stop = threading.Event()
    stop.set()
    log_lines: list[str] = []
    result = _rsync_with_retry(
        ["/bin/sleep", "10"],  # would normally hang for 10 s
        log_fn=log_lines.append,
        stop_event=stop,
    )
    assert result.returncode == 130
    assert "cancelled by Stop request" in "".join(log_lines)


def test_rsync_with_retry_cancels_during_in_flight_rsync():
    """The actual bug the user reported: Stop fires WHILE a slow
    rsync subprocess is running. The runner must terminate that
    subprocess instead of waiting for it to finish on its own."""
    import threading
    import time
    from services.remote_runner import _rsync_with_retry

    stop = threading.Event()

    def fire_stop_soon():
        time.sleep(0.3)  # let the rsync get going first
        stop.set()

    threading.Thread(target=fire_stop_soon, daemon=True).start()
    t0 = time.monotonic()
    # Simulate a slow rsync by sleeping; the cancellable runner
    # should terminate it well before the 10s sleep completes.
    result = _rsync_with_retry(
        ["/bin/sleep", "10"],
        log_fn=None,
        stop_event=stop,
        timeout=15,
    )
    elapsed = time.monotonic() - t0

    assert result.returncode == 130
    # Must have terminated within a small window — well under the 10s sleep.
    assert elapsed < 3.0, f"Stop took {elapsed:.2f}s to interrupt the rsync"


def test_rsync_with_retry_no_stop_event_behaves_normally():
    """When stop_event is None (the bootstrap path), behavior is
    unchanged from pre-v5.2.28 — just runs the command and returns."""
    from services.remote_runner import _rsync_with_retry
    result = _rsync_with_retry(["/bin/true"], log_fn=None)
    assert result.returncode == 0


# ── v5.2.27: pre-flight cache-hit skips pipeline launch entirely ──────────


def _make_spawn_test_runner(
        host, tmp_path, monkeypatch, stop_fetchers, *, completed_remote_wells):
    """Plumb a RemoteRunner whose spawn() can run end-to-end without
    real ssh/rsync: mock _ssh_run/_ssh_script, ensure_uploaded, popen,
    and _list_remote_completed_wells return value."""
    runner = RemoteRunner(host)
    monkeypatch.setattr(
        DatasetCache, "_ssh_run",
        lambda self, argv, *, check: MagicMock(stdout="", returncode=0),
    )
    monkeypatch.setattr(
        DatasetCache, "_ssh_script",
        lambda self, script, *, check: MagicMock(stdout="", returncode=0),
    )
    # One key from both entry points. ``spawn`` derives the remote paths from
    # ``cache_key`` before the upload starts (so the allocation can be asked
    # for first) and checks that the upload landed under the same key — a fake
    # that answers differently describes a run whose bytes went somewhere the
    # pipeline would never look.
    _KEY = "cachekey" + "0" * 24
    monkeypatch.setattr(DatasetCache, "cache_key", lambda self, zipped: _KEY)
    monkeypatch.setattr(
        DatasetCache, "ensure_uploaded",
        lambda self, zipped, **kw: (_KEY, 0, 0.01),
    )
    monkeypatch.setattr(
        RemoteRunner, "sync_required_files",
        lambda self, **kw: None,
    )
    # Stub out the remote-completed-wells lister: the test parametrizes
    # how many wells "exist" on the remote.
    monkeypatch.setattr(
        FetchedOutputVerifier, "_list_remote_completed_wells",
        lambda self: set(completed_remote_wells),
    )
    # Capture the ssh argv we'd launch so the test can inspect it.
    captured = {}

    def fake_popen(argv, **kw):
        captured["argv"] = list(argv)
        m = MagicMock()
        m.stdout = MagicMock()
        m.stdout.__iter__ = lambda self: iter([])
        m.wait.return_value = 0
        m.returncode = 0
        m.pid = 1234
        return m

    monkeypatch.setattr("services.remote_runner.subprocess.Popen", fake_popen)
    stop_fetchers(runner)
    return runner, captured


def test_spawn_skips_pipeline_when_remote_has_all_wells(host, tmp_path, monkeypatch, stop_fetchers):
    """v5.2.27: previous run completed every well on the remote, then
    the local fetch was interrupted. Re-run must NOT re-launch the
    pipeline — pre-flight detects the cache hit and replaces the
    pipeline command with ``true`` so the downstream flow just
    refetches missing local files."""
    z1 = _make_well_zip(tmp_path, "A01.zip", ["x.tif"])
    z2 = _make_well_zip(tmp_path, "B02.zip", ["x.tif"])
    zipped = ZippedInput(
        zip_dir=tmp_path, mode=LocalInputMode.ZIP, well_zips=(z1, z2),
    )
    runner, captured = _make_spawn_test_runner(
        host, tmp_path, monkeypatch, stop_fetchers,
        completed_remote_wells={"A01", "B02"},
    )

    log_lines: list[str] = []
    runner.spawn(
        zipped,
        tmp_path / "out",
        pipeline_argv=["/usr/bin/python", "/local/process_microscopy.py",
                       "--input_dir", "/local/in", "--output_dir", "/local/out"],
        output_filter=OutputFilter.FULL,
        log_fn=log_lines.append,
    )
    blob = "".join(log_lines)
    # The cache-hit log must surface.
    assert "FULL CACHE HIT" in blob
    assert "2/2 well" in blob  # pre-flight count line
    # And the actual remote command should be ``true`` (skipping the
    # whole conda-run / process_microscopy launch).
    ssh_argv = captured["argv"]
    remote_cmd = ssh_argv[-1]  # last element is the remote command
    assert "exec true" in remote_cmd
    assert "process_microscopy" not in remote_cmd


def test_spawn_runs_pipeline_when_remote_is_partial(host, tmp_path, monkeypatch, stop_fetchers):
    """If only some wells are complete on the remote, the pipeline
    runs normally — its own per-well skip logic handles the rest."""
    z1 = _make_well_zip(tmp_path, "A01.zip", ["x.tif"])
    z2 = _make_well_zip(tmp_path, "B02.zip", ["x.tif"])
    z3 = _make_well_zip(tmp_path, "C03.zip", ["x.tif"])
    zipped = ZippedInput(
        zip_dir=tmp_path, mode=LocalInputMode.ZIP, well_zips=(z1, z2, z3),
    )
    runner, captured = _make_spawn_test_runner(
        host, tmp_path, monkeypatch, stop_fetchers,
        completed_remote_wells={"A01"},  # only 1 of 3 done
    )
    log_lines: list[str] = []
    runner.spawn(
        zipped,
        tmp_path / "out",
        pipeline_argv=["/usr/bin/python", "/local/process_microscopy.py",
                       "--input_dir", "/local/in", "--output_dir", "/local/out"],
        output_filter=OutputFilter.FULL,
        log_fn=log_lines.append,
    )
    blob = "".join(log_lines)
    assert "FULL CACHE HIT" not in blob
    assert "1/3 well" in blob  # pre-flight count surfaces the partial
    # And the actual remote command should still be the pipeline,
    # not the no-op ``true``.
    ssh_argv = captured["argv"]
    remote_cmd = ssh_argv[-1]
    assert "process_microscopy" in remote_cmd


def test_spawn_runs_pipeline_when_remote_is_empty(host, tmp_path, monkeypatch, stop_fetchers):
    """First-ever run on a fresh remote: pre-flight finds nothing,
    pipeline launches normally."""
    z1 = _make_well_zip(tmp_path, "A01.zip", ["x.tif"])
    zipped = ZippedInput(
        zip_dir=tmp_path, mode=LocalInputMode.ZIP, well_zips=(z1,),
    )
    runner, captured = _make_spawn_test_runner(
        host, tmp_path, monkeypatch, stop_fetchers,
        completed_remote_wells=set(),
    )
    log_lines: list[str] = []
    runner.spawn(
        zipped,
        tmp_path / "out",
        pipeline_argv=["/usr/bin/python", "/local/process_microscopy.py",
                       "--input_dir", "/local/in", "--output_dir", "/local/out"],
        output_filter=OutputFilter.FULL,
        log_fn=log_lines.append,
    )
    blob = "".join(log_lines)
    assert "FULL CACHE HIT" not in blob
    assert "0/1 well" in blob
    ssh_argv = captured["argv"]
    remote_cmd = ssh_argv[-1]
    assert "process_microscopy" in remote_cmd


# ── v5.2.25: deterministic run_id + remote-aware verify backstop ──────────


def test_compute_run_id_is_deterministic_for_same_inputs():
    """v5.2.25: same cache_key + same pipeline argv → same run_id.
    The whole point of the dedup is that re-running with the same
    config lands in the same remote output dir."""
    from services.remote_runner import _compute_run_id
    argv = [
        "/usr/bin/python", "process_microscopy.py",
        "--input_dir", "/in", "--output_dir", "/out",
        "--nuclear_token", "NIR", "--fluor_tokens", "GFP",
    ]
    a = _compute_run_id("abcdef0123456789", argv)
    b = _compute_run_id("abcdef0123456789", list(argv))
    assert a == b


def test_compute_run_id_changes_when_cache_key_changes():
    """Different input → different run_id."""
    from services.remote_runner import _compute_run_id
    argv = ["--nuclear_token", "NIR"]
    a = _compute_run_id("aaaaaaaaaaaaaaaa", argv)
    b = _compute_run_id("bbbbbbbbbbbbbbbb", argv)
    assert a != b


def test_compute_run_id_changes_when_pipeline_args_change():
    """Different segmentation config → different run_id so the
    output cache doesn't serve stale results."""
    from services.remote_runner import _compute_run_id
    a = _compute_run_id("abcd", ["--nuclear_token", "NIR"])
    b = _compute_run_id("abcd", ["--nuclear_token", "DAPI"])
    assert a != b


def test_compute_run_id_ignores_input_output_dirs_and_force():
    """Paths and ``--force`` shouldn't churn the hash: paths are
    already captured by cache_key, and ``--force`` is a re-run
    instruction (not a config change) — it should reuse the same
    cache slot and overwrite the previous outputs in place."""
    from services.remote_runner import _compute_run_id
    base = ["--nuclear_token", "NIR"]
    with_paths = base + ["--input_dir", "/some/path", "--output_dir", "/other"]
    with_other_paths = base + ["--input_dir", "/different", "--output_dir", "/x"]
    with_force = base + ["--force"]
    assert _compute_run_id("k", base) == _compute_run_id("k", with_paths)
    assert _compute_run_id("k", base) == _compute_run_id("k", with_other_paths)
    assert _compute_run_id("k", base) == _compute_run_id("k", with_force)


def test_list_remote_completed_wells_parses_out_zip_listing(runner_with_run, monkeypatch):
    """v5.2.25 backstop: ssh-listing of ``*_out.zip`` files in the
    remote output dir yields the set of completed wells. Used by
    _verify_local_manifest to discover wells the pipeline skipped
    (no ``Well X — done.`` marker fires for them)."""
    monkeypatch.setattr(
        DatasetCache, "_ssh_script",
        lambda self, script, *, check: MagicMock(
            stdout=(
                "/r/out/A01_out.zip\n"
                "/r/out/B02_out.zip\n"
                "/r/out/H12_out.zip\n"
                # Junk that shouldn't parse as a well — make sure
                # the parser stays strict.
                "/r/out/random_other.zip\n"
            ),
            returncode=0,
        ),
    )
    wells = runner_with_run._output_verifier()._list_remote_completed_wells()
    assert wells == {"A01", "B02", "H12"}


def test_list_remote_completed_wells_handles_empty_dir(runner_with_run, monkeypatch):
    """Empty listing or no ``*_out.zip`` files → empty set, never raises."""
    monkeypatch.setattr(
        DatasetCache, "_ssh_script",
        lambda self, script, *, check: MagicMock(stdout="", returncode=0),
    )
    assert runner_with_run._output_verifier()._list_remote_completed_wells() == set()


def test_list_remote_completed_wells_degrades_on_ssh_error(runner_with_run, monkeypatch):
    """Network blip / missing ssh binary → return empty set, NOT
    raise. The verify pass falls back to ``expected_wells``-only
    (same behavior as pre-v5.2.25)."""
    def fail(self, script, *, check):
        raise OSError("ssh: no such file")
    monkeypatch.setattr(DatasetCache, "_ssh_script", fail)
    assert runner_with_run._output_verifier()._list_remote_completed_wells() == set()


def test_verify_manifest_uses_remote_listing_as_backstop(runner_with_run, monkeypatch, tmp_path):
    """End-to-end: pipeline skipped a well silently (no ``done.``
    marker, so expected_wells doesn't include it). The remote
    output dir HAS its ``B02_out.zip`` though. Verify must spot
    that ``B02``'s CSV is missing locally and surface it for the
    retry pass — that's how a 'deleted one CSV, re-run' user
    flow gets the CSV back without re-analyzing.

    runner_with_run sets expected_wells={'A01'} and pre-creates
    *_A01.csv locally. We add B02 to the remote listing and check
    that B02 ends up flagged as missing."""
    monkeypatch.setattr(
        DatasetCache, "_ssh_script",
        lambda self, script, *, check: MagicMock(
            stdout="/r/out/A01_out.zip\n/r/out/B02_out.zip\n",
            returncode=0,
        ),
    )
    log_lines: list[str] = []
    missing = runner_with_run._output_verifier()._verify_local_manifest(
        log=log_lines.append,
    )
    # A01 has its CSV (pre-created in the fixture). B02 doesn't —
    # the backstop discovered the well, the local check noticed
    # it's missing.
    assert any("B02" in m for m in missing)
    assert not any(m.endswith("A01") for m in missing)


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
    remote = runner._build_remote_argv(
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


def test_remote_argv_pins_scratch_to_the_remote_output_dir(host):
    """The pipeline default moved to ``~/.allwell/scratch`` to keep a local
    run's bulk I/O off a network-mounted output volume. On a cluster that
    default is wrong — home is typically a small-quota NFS share — so the
    remote argv pins scratch to the remote output dir, which is where it lived
    before the default changed."""
    runner = RemoteRunner(host)
    local = [
        "/usr/bin/python", "/local/pm.py",
        "--input_dir", "/x", "--output_dir", "/y",
    ]
    remote = runner._build_remote_argv(
        local, "/r/in", "/r/out", OutputFilter.FULL, LocalInputMode.ZIP,
    )
    assert "--scratch_dir=/r/out" in remote


def test_remote_argv_drops_a_local_scratch_dir(host):
    """A local scratch path is meaningless on the remote host and must never be
    forwarded — in either the ``--scratch_dir X`` or ``--scratch_dir=X`` form."""
    runner = RemoteRunner(host)
    for local in (
        ["/usr/bin/python", "/local/pm.py", "--input_dir", "/x",
         "--scratch_dir", "/Volumes/LocalSSD/scratch", "--output_dir", "/y"],
        ["/usr/bin/python", "/local/pm.py", "--input_dir", "/x",
         "--scratch_dir=/Volumes/LocalSSD/scratch", "--output_dir", "/y"],
    ):
        remote = runner._build_remote_argv(
            local, "/r/in", "/r/out", OutputFilter.FULL, LocalInputMode.ZIP,
        )
        assert not any("LocalSSD" in a for a in remote)
        assert remote.count("--scratch_dir=/r/out") == 1


def test_remote_argv_csv_only_appends_no_save_tophat(host):
    runner = RemoteRunner(host)
    local = [
        "/usr/bin/python", "/local/pm.py",
        "--input_dir", "/x", "--output_dir", "/y",
    ]
    remote = runner._build_remote_argv(
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
    remote = runner._build_remote_argv(
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
    remote = runner._build_remote_argv(
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
    remote = runner._build_remote_argv(
        local, "/r/in", "/r/out", OutputFilter.FULL, LocalInputMode.ZIP,
    )
    assert remote[-2:] == ["--workers", "32"]


# ── RemoteRunner.estimate ───────────────────────────────────────────────────


def test_estimate_reports_transfer_and_nothing_it_cannot_measure(host, tmp_path):
    """The estimate used to end in "remote wins" / "LOCAL would be faster",
    derived from a hardcoded max(60s, 5s-per-well) local-compute figure and a
    flat assumed 4x remote speedup. On a real plate that guessed 60s against a
    run that took eight hours — a thousand-fold error — and the verdict told
    the user to run locally, printed beside a genuinely measured transfer size,
    which is what made a constant look like a measurement. It could not have
    been right in principle either: it assumes local compute pays no I/O, and a
    NAS-resident dataset pays network reads for every image either way. Bytes
    and transfer time are measurable; which side is faster is not, so it is no
    longer claimed."""
    z = _make_well_zip(tmp_path, "A01.zip", ["x.tif"])
    z.write_bytes(b"\0" * 50 * 1024 * 1024)
    zipped = ZippedInput(zip_dir=tmp_path, mode=LocalInputMode.ZIP, well_zips=(z,))
    runner = RemoteRunner(host)
    est = runner.estimate(zipped)

    assert est.transfer_s > 0
    assert est.bytes_to_transfer == 50 * 1024 * 1024
    text = est.format()
    assert "50.00 GB" not in text and "0.05 GB" in text
    for claim in ("wins", "faster", "vs local", "compute"):
        assert claim not in text, f"estimate still asserts {claim!r}: {text}"
    assert not hasattr(est, "will_win")
    assert not hasattr(est, "local_compute_s")


def test_estimate_cache_hit_zeroes_transfer(host, tmp_path):
    z = _make_well_zip(tmp_path, "A01.zip", ["x.tif"])
    z.write_bytes(b"\0" * 1024)
    zipped = ZippedInput(zip_dir=tmp_path, mode=LocalInputMode.ZIP, well_zips=(z,))
    runner = RemoteRunner(host)
    est = runner.estimate(zipped, is_cache_hit=True)
    assert est.transfer_s == 0.0


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


# ── Per-well upload progress ────────────────────────────────────────────────


_FAKE_RSYNC = r"""#!/bin/sh
printf 'sending incremental file list\n'
printf 'A01.zip\n'
printf '      1,048,576  10%%   10.00MB/s    0:00:09\r'
printf '      5,242,880  50%%   10.00MB/s    0:00:05\r'
printf '     10,485,760 100%%   10.00MB/s    0:00:00 (xfr#1, to-chk=1/2)\r\n'
printf 'B02.zip\n'
printf '     15,728,640  60%%   10.00MB/s    0:00:02\r'
printf '     20,971,520 100%%   10.00MB/s    0:00:00 (xfr#2, to-chk=0/2)\r\n'
printf 'sent 20,971,520 bytes  received 35 bytes  1,000,000.00 bytes/sec\n'
"""


def _run_fake_rsync(tmp_path, sizes):
    """Drive the real streaming reader with a script that prints the same
    ``--info=name1,stats2,progress2`` stream rsync does. Returns every line
    the reader handed to ``log_fn``."""
    import stat as _stat

    from services.remote_runner import _run_rsync_streaming

    fake = tmp_path / "fakersync.sh"
    fake.write_text(_FAKE_RSYNC)
    fake.chmod(fake.stat().st_mode | _stat.S_IEXEC)
    lines: list[str] = []
    _run_rsync_streaming([str(fake)], log_fn=lines.append, progress_fn=None,
                         stop_event=None, well_sizes=sizes)
    return lines


def test_upload_progress_is_attributed_to_the_well_in_flight(tmp_path):
    """``progress2`` counts bytes for the whole rsync, not per file, so a
    percentage alone says nothing about any one well. ``name1`` marks where
    each well's share of that running total begins, and the local zip's own
    size is the denominator."""
    from services.pipeline_runner import parse_well_transfer

    sizes = {"A01.zip": 10_485_760, "B02.zip": 10_485_760}
    samples = [parse_well_transfer(m) for m in _run_fake_rsync(tmp_path, sizes)]
    samples = [s for s in samples if s is not None]
    assert ("A01", "up", 0.0) in samples
    assert ("A01", "up", 0.5) in samples
    # B02's window rebases on the cumulative total at the moment it started —
    # otherwise its first sample would read as already finished.
    assert ("B02", "up", 0.0) in samples
    assert ("B02", "up", 0.5) in samples
    assert samples[-1] == ("B02", "up", 1.0)


def test_upload_progress_never_reaches_the_log(tmp_path):
    """A sample a second per well is the same flood the transient marker
    exists to keep out of the log."""
    from services.pipeline_runner import is_transient_progress

    lines = _run_fake_rsync(tmp_path, {"A01.zip": 10_485_760,
                                       "B02.zip": 10_485_760})
    logged = [m for m in lines if not is_transient_progress(m)]
    # What survives is the events: the per-file names and rsync's own summary.
    assert any("↑ (1) A01.zip" in m for m in logged)
    assert any("↑ (2) B02.zip" in m for m in logged)
    assert not any("xfer" in m for m in logged)


def test_a_plate_wide_rsync_without_sizes_emits_no_well_progress(tmp_path):
    """The code sync uploads source files, not well archives — nothing there
    is a well, so nothing may claim to be one."""
    lines = _run_fake_rsync(tmp_path, None)
    assert not any("xfer" in m for m in lines)


def test_well_zip_sizes_skips_an_archive_that_vanished(tmp_path):
    """A missing archive gets no entry rather than a guessed size: the
    transfer will fail on its own and say so, and a dial on a well that is
    not moving is worse than no dial."""
    from services.remote_runner import _well_zip_sizes

    real = tmp_path / "A01.zip"
    real.write_bytes(b"\0" * 4096)
    sizes = _well_zip_sizes([real, tmp_path / "B02.zip"])
    assert sizes == {"A01.zip": 4096}


# ── Fetcher: stdout observation ─────────────────────────────────────────────


def test_fetcher_observes_well_done_marker(host, tmp_path):
    from services.remote_run_state import _RemoteRun

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


def _make_run(tmp_path):
    from services.remote_run_state import _RemoteRun
    return _RemoteRun(
        run_id="x", cache_key="k", remote_input_dir="/r/in",
        remote_output_dir="/r/out", remote_pidfile="/r/pid",
        local_output_dir=tmp_path, local_input_mode=LocalInputMode.ZIP,
    )


def test_transfer_heartbeat_reports_progress(host, tmp_path):
    """A silent per-well fetch must not look hung: the heartbeat emits periodic
    'still transferring… (Ns, M MB so far)' lines with the growing byte count."""
    import time as _time
    run = _make_run(tmp_path)
    logs: list[str] = []
    with patch.object(Fetcher, "_loop", lambda self: None):
        fetcher = Fetcher(host, run, output_filter=OutputFilter.FULL,
                          log_fn=logs.append)
    dest = tmp_path / "allwell-x-A01_out.zip"
    # simulate rsync streaming into a hidden temp file in the dest dir
    (tmp_path / f".{dest.name}.abc123").write_bytes(b"\0" * (3 * 1024 * 1024))
    with fetcher._transfer_heartbeat("A01", dest, interval=0.05,
                                     breadcrumb_interval=0.1):
        _time.sleep(0.16)                       # allow a couple of beats
    joined = "".join(logs)
    # No remote size was supplied, so no fraction may be claimed — bytes-so-far
    # is all this run knows, and an unknown denominator must not be invented.
    assert "xfer" not in joined
    # Two cadences: a live sample every beat, marked transient so it reaches
    # the status line and never the log, plus a rarer durable breadcrumb.
    from services.remote_runner import TRANSIENT_PREFIX
    assert TRANSIENT_PREFIX in joined
    assert joined.count(TRANSIENT_PREFIX) > joined.count("still transferring")
    assert "still transferring" in joined
    assert "A01" in joined and "MB so far" in joined
    # no heartbeat lines leak after the context exits
    n = joined.count("still transferring")
    _time.sleep(0.12)
    assert "".join(logs).count("still transferring") == n


def test_transfer_heartbeat_fills_the_well_dial_when_the_size_is_known(host, tmp_path):
    """Per-well download progress: bytes landed over the remote file's size.

    The numerator was already here — the heartbeat sums the growing rsync
    partial in the destination directory. The denominator is what turns it
    into a proportion the plate can draw.
    """
    import time as _time

    from services.pipeline_runner import parse_well_transfer

    run = _make_run(tmp_path)
    logs: list[str] = []
    with patch.object(Fetcher, "_loop", lambda self: None):
        fetcher = Fetcher(host, run, output_filter=OutputFilter.FULL,
                          log_fn=logs.append)
    dest = tmp_path / "allwell-x-A01_out.zip"
    (tmp_path / f".{dest.name}.abc123").write_bytes(b"\0" * 2_000_000)
    with fetcher._transfer_heartbeat("A01", dest, interval=0.05,
                                     breadcrumb_interval=10.0,
                                     total_bytes=4_000_000):
        _time.sleep(0.16)
    samples = [parse_well_transfer(m) for m in logs]
    samples = [s for s in samples if s is not None]
    assert ("A01", "down", 0.0) in samples, "a dial opens at zero"
    assert ("A01", "down", 0.5) in samples, "2 MB of 4 MB is half"
    # The window always closes, so a failed fetch cannot leave a part-filled
    # download on the plate for the rest of the run.
    assert samples[-1] == ("A01", "down", 1.0)


def test_a_missing_remote_file_yields_no_size(host, tmp_path):
    """The honest failure: no denominator, so no fraction — never a guess."""
    run = _make_run(tmp_path)
    with patch.object(Fetcher, "_loop", lambda self: None):
        fetcher = Fetcher(host, run, output_filter=OutputFilter.FULL)
    real = tmp_path / "thing.bin"
    real.write_bytes(b"\0" * 12345)
    # ssh_argv -> [] runs the size script through a local bash, which is
    # exactly the shell the remote would run it in.
    with patch.object(type(host), "ssh_argv", lambda self, allocate_tty=False: []):
        assert fetcher._remote_file_size(str(real)) == 12345
        assert fetcher._remote_file_size(str(tmp_path / "nope.bin")) is None


def test_zip_is_complete(tmp_path):
    import zipfile
    from services.remote_scratch import _zip_is_complete
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("a.tif", b"data")
    assert _zip_is_complete(good)                       # valid, fully written
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"PK\x03\x04 truncated, no central directory")
    assert not _zip_is_complete(bad)                    # partial download
    assert not _zip_is_complete(tmp_path / "missing.zip")


def test_fetch_one_keeps_complete_zip_despite_rsync_error(host, tmp_path):
    """The stuck-final-fetch bug: rsync can exit non-zero (timeout / reset on
    close) *after* the zip is fully across. The well must still materialise —
    not get discarded and re-fetched forever — so the run can finish."""
    import zipfile
    run = _make_run(tmp_path)
    logs: list[str] = []
    with patch.object(Fetcher, "_loop", lambda self: None):
        f = Fetcher(host, run, output_filter=OutputFilter.FULL, log_fn=logs.append)
    well = "A01"
    # Where the Fetcher looks for the transit zip: the transit directory beside
    # the run's own output, not the home scratch volume. A remote run has no
    # reason to touch home, and writing a whole well's output there and then
    # copying it across is what used to fill it.
    from services.remote_scratch import _transit_dir
    tmp_zip = (_transit_dir(run.local_output_dir)
               / f"allwell-{run.run_id}-{well}_out.zip")
    with zipfile.ZipFile(tmp_zip, "w") as z:            # a COMPLETE zip on disk
        z.writestr("A01_overlay.tif", b"data")
    try:
        with patch.object(Fetcher, "_rsync", return_value=False), \
             patch.object(Fetcher, "_rsync_glob", return_value=(1, 1)), \
             patch.object(Fetcher, "_remote_file_size", lambda self, p: None), \
             patch.object(Fetcher, "_materialize", lambda self, w, z: True), \
             patch.object(Fetcher, "_delete_well_files_on_remote", lambda self, w: None):
            f._fetch_one(well)
        assert well in run.fetched_wells                # accepted, not discarded
        assert any("complete on disk" in m for m in logs)
    finally:
        try:
            tmp_zip.unlink()
        except OSError:
            pass


def test_flush_and_join_does_not_drain_when_cancelled(host, tmp_path):
    """Stop pressed → cancel() → flush_and_join must NOT drain the queue: no
    'draining fetcher' wait, no download summary — just reap the worker and
    report the cancellation, so Stop is immediate."""
    run = _make_run(tmp_path)
    logs: list[str] = []
    with patch.object(Fetcher, "_loop", lambda self: None):
        fetcher = Fetcher(host, run, output_filter=OutputFilter.FULL,
                          log_fn=logs.append)
        # two wells queued as if mid-run, then the user hits Stop
        fetcher.observe("Zip mode: 2 well-zip files detected\n")
        fetcher.observe("A01 — done.\n")
        fetcher.observe("B02 — done.\n")
        fetcher.cancel()
        fetcher.flush_and_join(timeout=5)
    joined = "".join(logs)
    assert "cancelled" in joined
    assert "draining fetcher" not in joined
    assert "wells downloaded" not in joined
    assert not fetcher._worker.is_alive()


def test_flush_and_join_drains_when_not_cancelled(host, tmp_path):
    """Normal completion still drains and emits the download summary."""
    run = _make_run(tmp_path)
    logs: list[str] = []
    with patch.object(Fetcher, "_loop", lambda self: None):
        fetcher = Fetcher(host, run, output_filter=OutputFilter.FULL,
                          log_fn=logs.append)
        fetcher.flush_and_join(timeout=5)
    joined = "".join(logs)
    assert "cancelled" not in joined
    assert "fetcher:" in joined          # end-of-stream summary emitted


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
    from services.remote_run_state import _RemoteRun

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


def test_fetcher_triggers_on_mangled_em_dash(host, tmp_path):
    """A UTF-8 remote stream decoded with a cp1252 Windows locale arrives with
    the em-dash mojibake'd ("Well A01 â€" done."). The Fetcher must still fire
    per-well — otherwise no download progress shows and the out dir only fills
    from the end-of-run backstop."""
    from services.remote_run_state import _RemoteRun

    run = _RemoteRun(
        run_id="x", cache_key="k", remote_input_dir="/r/in",
        remote_output_dir="/r/out", remote_pidfile="/r/pid",
        local_output_dir=tmp_path, local_input_mode=LocalInputMode.ZIP,
    )
    with patch.object(Fetcher, "_loop", lambda self: None):
        fetcher = Fetcher(host, run, output_filter=OutputFilter.FULL)
        fetcher.observe('2026-05-27 10:30:45  INFO      Well A01 â€" done.\n')
        fetcher.observe("2026-05-27 10:31:02  INFO      Well B02 � done.\n")
        fetcher.cancel()
    assert run.expected_wells == {"A01", "B02"}


def test_fetcher_ignores_unrelated_lines(host, tmp_path):
    from services.remote_run_state import _RemoteRun

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


def test_fetcher_fetch_one_logs_size_and_timing(host, tmp_path, monkeypatch):
    """v5.2.18: per-well fetch must emit visible progress lines —
    start-of-download, size+throughput summary, CSV count, and final
    materialised confirmation. Pre-v5.2.18 the Fetcher was completely
    silent during downloads; the user couldn't tell whether the GUI
    had hung or was making progress."""
    from services.remote_run_state import _RemoteRun

    run = _RemoteRun(
        run_id="r",
        cache_key="k",
        remote_input_dir="/r/in",
        remote_output_dir="/r/out",
        remote_pidfile="/r/pid",
        local_output_dir=tmp_path,
        local_input_mode=LocalInputMode.ZIP,
    )
    run.well_count_total = 2

    log_lines: list[str] = []

    def fake_rsync(self, argv):
        # Simulate a successful download: write a 1MB temp zip if the
        # call is the per-well zip pull.
        for a in argv:
            if a.startswith("/") and a.endswith("_out.zip"):
                Path(a).write_bytes(b"\0" * 1024 * 1024)
        return True

    monkeypatch.setattr(Fetcher, "_rsync", fake_rsync)
    # The CSV half is listed before the zip is pulled — the dial needs its byte
    # total up front to span both transfers on one gauge — so the seam here is
    # the listing plus the pull, not the combined ``_rsync_glob``.
    monkeypatch.setattr(Fetcher, "_list_remote_glob",
                        lambda self, src: ("h", ["/r/out/m_A01.csv"], 512))
    monkeypatch.setattr(Fetcher, "_pull_listed",
                        lambda self, host, files, dest: len(files))
    monkeypatch.setattr(Fetcher, "_materialize", lambda self, w, z: True)
    # The size query is a real ssh; there is no remote here.
    monkeypatch.setattr(Fetcher, "_remote_file_size", lambda self, p: None)

    with patch.object(Fetcher, "_loop", lambda self: None):
        fetcher = Fetcher(
            host, run, output_filter=OutputFilter.FULL,
            log_fn=lambda m: log_lines.append(m),
        )
        fetcher._fetch_one("A01")
        fetcher.cancel()

    blob = "".join(log_lines)
    # Start line includes a position-of-total prefix.
    assert "(1/2) A01: starting download" in blob
    # Zip size and rate.
    assert "A01: 1.0 MB zip" in blob
    assert "Mbps" in blob
    # CSV count.
    assert "A01: 1 CSV file(s)" in blob
    # Final materialised marker with running tally.
    assert "✓ A01: materialised to" in blob
    assert "1/2 wells fetched" in blob


def test_fetcher_fetch_one_warns_when_zip_missing(host, tmp_path, monkeypatch):
    """If the remote well_out.zip can't be pulled, the warn line must
    include the remote output dir so the user can investigate."""
    from services.remote_run_state import _RemoteRun

    run = _RemoteRun(
        run_id="r",
        cache_key="k",
        remote_input_dir="/r/in",
        remote_output_dir="/r/out",
        remote_pidfile="/r/pid",
        local_output_dir=tmp_path,
        local_input_mode=LocalInputMode.ZIP,
    )

    log_lines: list[str] = []
    monkeypatch.setattr(Fetcher, "_rsync", lambda self, argv: False)
    monkeypatch.setattr(Fetcher, "_remote_file_size", lambda self, p: None)

    with patch.object(Fetcher, "_loop", lambda self: None):
        fetcher = Fetcher(
            host, run, output_filter=OutputFilter.FULL,
            log_fn=lambda m: log_lines.append(m),
        )
        fetcher._fetch_one("B02")
        fetcher.cancel()

    blob = "".join(log_lines)
    assert "B02: WARN could not pull B02_out.zip" in blob
    assert "/r/out" in blob  # remote dir surfaced for debugging


# ── Verify ──────────────────────────────────────────────────────────────────


def test_verify_passes_when_all_csvs_and_zips_present(host, tmp_path):
    """v5.2.29: verify now demands both the CSV AND the per-well
    out_zip when output_filter=FULL (the default). Pre-create both
    to confirm the happy path stays green."""
    from services.remote_run_state import _RemoteRun

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
    runner._run = run

    (tmp_path / "measurements_A01.csv").write_text("col1,col2\n1,2\n")
    (tmp_path / "measurements_B02.csv").write_text("col1,col2\n3,4\n")
    (tmp_path / "A01_out.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    (tmp_path / "B02_out.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)

    logs: list[str] = []
    missing = runner._output_verifier()._verify_local_manifest(log=logs.append)
    assert missing == []
    assert any("all 2 wells" in m for m in logs)


def test_verify_flags_missing_out_zip_even_when_csv_is_present(host, tmp_path):
    """v5.2.29: the user reported that on resume, a well with the
    CSV present locally would NOT trigger a re-pull of the missing
    out_zip — verify treated the CSV alone as 'complete'. Now the
    out_zip must also be present (under FULL filter + ZIP mode) for
    a well to count as complete, so the existing retry path re-pulls
    the missing zip."""
    from services.remote_run_state import _RemoteRun

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
    runner._run = run

    # Both CSVs present.
    (tmp_path / "measurements_A01.csv").write_text("ok\n")
    (tmp_path / "measurements_B02.csv").write_text("ok\n")
    # Only A01 has its zip; B02's never made it back.
    (tmp_path / "A01_out.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)

    logs: list[str] = []
    missing = runner._output_verifier()._verify_local_manifest(log=logs.append)
    assert any("B02_out.zip" in m for m in missing)
    assert not any("A01" in m for m in missing)


def test_verify_skips_zip_check_when_csv_only_filter(host, tmp_path):
    """In CSV_ONLY mode the user has explicitly opted out of the
    per-well zip — verify must NOT flag the absent zip as missing."""
    from services.remote_run_state import _RemoteRun

    runner = RemoteRunner(host)
    run = _RemoteRun(
        run_id="x",
        cache_key="k",
        remote_input_dir="/r/in",
        remote_output_dir="/r/out",
        remote_pidfile="/r/pid",
        local_output_dir=tmp_path,
        local_input_mode=LocalInputMode.ZIP,
        output_filter=OutputFilter.CSV_ONLY,
    )
    run.expected_wells = {"A01"}
    runner._run = run

    (tmp_path / "measurements_A01.csv").write_text("ok\n")
    # No A01_out.zip — but CSV_ONLY user doesn't want it.

    logs: list[str] = []
    missing = runner._output_verifier()._verify_local_manifest(log=logs.append)
    assert missing == []


def test_verify_expects_out_zip_even_in_folder_mode(host, tmp_path):
    """Output is always materialised as <well>_out.zip now (compression is
    mandatory and the Fetcher keeps the zip for every input mode), so verify
    must expect the zip even when the local input was per-well folders. (The
    old behaviour expected an extracted <well>/ dir, which no longer exists —
    that mismatch failed verify and skipped the post-run local-folder cleanup.)"""
    from services.remote_run_state import _RemoteRun

    runner = RemoteRunner(host)
    run = _RemoteRun(
        run_id="x",
        cache_key="k",
        remote_input_dir="/r/in",
        remote_output_dir="/r/out",
        remote_pidfile="/r/pid",
        local_output_dir=tmp_path,
        local_input_mode=LocalInputMode.FOLDER,
    )
    run.expected_wells = {"A01", "B02"}
    runner._run = run

    (tmp_path / "measurements_A01.csv").write_text("ok\n")
    (tmp_path / "measurements_B02.csv").write_text("ok\n")
    # A01 has its out-zip, B02 doesn't.
    (tmp_path / "A01_out.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)

    logs: list[str] = []
    missing = runner._output_verifier()._verify_local_manifest(log=logs.append)
    assert any("B02" in m and "_out.zip" in m for m in missing)
    assert not any("A01" in m for m in missing)


def test_verify_reports_missing_csv(host, tmp_path):
    from services.remote_run_state import _RemoteRun

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
    runner._run = run

    (tmp_path / "measurements_A01.csv").write_text("col1\n")
    # B02 missing on purpose.

    logs: list[str] = []
    missing = runner._output_verifier()._verify_local_manifest(log=logs.append)
    assert missing
    assert any("B02" in m for m in missing)


def test_verify_reports_empty_csv(host, tmp_path):
    from services.remote_run_state import _RemoteRun

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
    runner._run = run

    (tmp_path / "measurements_A01.csv").write_text("")  # empty
    logs: list[str] = []
    missing = runner._output_verifier()._verify_local_manifest(log=logs.append)
    assert missing == ["empty CSV for A01"]


# ── sync_required_files ─────────────────────────────────────────────────────


def test_required_files_list_is_what_we_promised():
    """The contract from the design discussion: only these files.

    v5.2.1 swapped ``requirements.txt`` for
    ``requirements-remote.txt`` so the remote install no longer
    drags in PySide6 / pyobjc / pyinstaller. If this assertion fails
    the contract drifted and both halves of the split need a look.
    """
    assert _REQUIRED_REMOTE_FILES == (
        "process_microscopy.py",
        # General Qt-free utilities (well-token parsing, filename-schema
        # parsing, timepoint parsing, logged-swallow).
        "util/__init__.py",
        "util/aw_safe.py",
        # One os.scandir in place of a stat per directory entry; client-side
        # only, listed because the manifest mirrors every util/ file.
        "util/dir_scan.py",
        # Rebinds sys.stdout / sys.stderr when a frozen windowed build nulls them;
        # imported by process_microscopy at module load (and by well_plate_zipper).
        # Reduces a metadata label to a filename token; both packed-file
        # converters name their images through it.
        "util/filename_token.py",
        "util/frozen_io.py",
        # The retry-with-backoff shield every pipeline write on the output
        # volume goes through (util.retry_io), imported by pipeline.io.
        "util/retry_io.py",
        # The one atomic JSON write; pipeline.run_report and
        # process_microscopy both replace pipeline_info.json through it.
        "util/json_write.py",
        # Serpentine plate-position labelling for packed OME-TIFF input. The
        # de-interleave runs client-side (before remote sync), so a remote run
        # never imports this — but the manifest mirrors every ``util/`` file.
        "util/plate_grid.py",
        "util/schema.py",
        # FOV-token numeric ordering, imported by process_microscopy
        # (``_collect_available_fovs``).
        "util/fov.py",
        # Channel-name → role heuristic (converter + Analyze).
        "util/channel_roles.py",
        # Filename-schema inference for the Analyze input scan.
        "util/schema_infer.py",
        "util/timepoint.py",
        # The one string naming the configuration a well's output was
        # produced under, so a re-run with a different segmenter is not
        # answered "already done" (util.settings_digest). Read on both
        # sides, so the digest has to be computed identically on the worker
        # host and locally.
        "util/settings_digest.py",
        "util/well_token.py",
        # Free-space primitives + the ENOSPC wording the retry shield rewrites
        # through (util.disk_space).
        "util/disk_space.py",
        # Atomic + verified per-well archive publication (util.zip_write),
        # imported by process_microscopy and pipeline.io.
        # "Would the pipeline group this file as input?" — one set, read by
        # process_microscopy's scanners and by the upload planner, which
        # decides from the same question which members survive a rewrite.
        "util/image_exts.py",
        "util/zip_write.py",
        # The Qt-free ``pipeline`` package (analysis-domain modules +
        # segmenters/writers), re-imported at module top level.
        "pipeline/__init__.py",
        "pipeline/auto_threshold.py",
        # The post-run pass over that sampler — which estimator this
        # run's arguments call for, and the merge into
        # pipeline_info.json. Called at the end of every run.
        "pipeline/auto_threshold_pass.py",
        "pipeline/background.py",
        "pipeline/capacity.py",
    # The pipeline's argparse declaration, imported by process_microscopy at
    # module scope.
    "pipeline/cli_args.py",
        "pipeline/cellpose.py",
        "pipeline/completeness.py",
        # The blank-well sampler + model builders behind empty_well correction.
        "pipeline/empty_well.py",
        # Per-field image-quality metrics (focus, saturation, dynamic range,
        # background level), computed inside the quantification pass itself —
        # so unlike the client-side entries below, the remote worker really
        # does import this one.
        "pipeline/field_quality.py",
        # Its stdlib-only half — the per-well record, the collected block and
        # the merge the fetch performs. Imported by the remote CLIENT, which
        # may not reach numpy or scipy.
        "pipeline/field_quality_record.py",
        # The pretrained-weights cache directory and the cross-process
        # download lock that stops N workers fetching the same model at once.
        # ``process_microscopy`` re-exports these, and the remote worker loads
        # its model through them on every run.
        "pipeline/stardist_cache.py",
        "pipeline/flatfield.py",
        # Per-FOV resume checkpoints, keyed on the output directory and the
        # settings so one plate cannot resume another's intermediates.
        "pipeline/fov_checkpoint.py",
        # Which of the three input layouts a plate is in; process_microscopy
    # imports it at module scope.
    "pipeline/input_layout.py",
    "pipeline/mixed_input.py",
    "pipeline/incremental_keys.py",
        "pipeline/well_skip.py",
        "pipeline/io.py",
        # The Zeiss CZI reader and its per-well explode; client-side for the
        # same reason, listed for the same manifest completeness.
        "pipeline/czi_read.py",
        "pipeline/czi_set.py",
        "pipeline/czi_explode.py",
        "pipeline/scan_pool.py",
        # The Nikon ND2 adapter and its per-well explode; client-side for the
        # same reason, listed for the same manifest completeness.
        "pipeline/nd2_read.py",
        "pipeline/nd2_explode.py",
        # What the exploders share: the member-name rule, the resume check,
        # the worker split, the timepoint plan and the in-memory TIFF encode.
        "pipeline/explode_common.py",
        # The elapsed-hour naming, split out of ``explode_common`` so that a
        # header scan need not import numpy — see
        # ``tests/test_a_scan_worker_imports_no_pixels.py``.
        "pipeline/timepoints.py",
        # Where a plate's parsed headers are remembered between sessions, so a
        # folder reopened costs no parsing. Client-side like the converters it
        # serves, and listed because the manifest mirrors every ``pipeline/`` file
        # rather than carrying exceptions nobody can see.
        "pipeline/scan_cache.py",
        # Packed OME-TIFF de-interleave; client-side, listed for manifest
        # completeness (mirrors every ``pipeline/`` file).
        "pipeline/ome_explode.py",
        # The loose-TIF grouping pre-pass, spawned as a subprocess; client-side
        # for the same reason (the remote is sent archives already grouped),
        # listed for the same manifest completeness.
        "pipeline/well_plate_zipper.py",
        "pipeline/run_report.py",
        # Where the settings digest is recorded beside each well's output,
        # and the reuse decision that reads it back.
        "pipeline/settings_record.py",
        "pipeline/sidecar.py",
        "pipeline/shape_features.py",
        "pipeline/stardist.py",
        "pipeline/suspend.py",
        "pipeline/threshold_optimize.py",
        "requirements-remote.txt",
    )


def test_required_remote_files_cover_pipeline_root_module_imports():
    """Every repo-root single-file module ``process_microscopy`` imports (at any
    level) must be uploaded, or a remote run aborts with ModuleNotFoundError —
    the ``flatfield_core`` regression. Guards against future drift when a new
    shared repo-root module is added to the pipeline.
    """
    import ast
    from services.remote_runner import repo_root
    root = repo_root()
    tree = parse_source((root / "process_microscopy.py"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module.split(".")[0]]
        elif isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        for nm in names:
            if (root / f"{nm}.py").is_file():   # a repo-root single-file module
                imported_roots.add(f"{nm}.py")
    missing = imported_roots - set(_REQUIRED_REMOTE_FILES)
    assert not missing, (
        "repo-root modules imported by process_microscopy but NOT in "
        f"_REQUIRED_REMOTE_FILES (remote run would ModuleNotFoundError): {sorted(missing)}"
    )


def test_remote_requirements_file_omits_gui_deps():
    """v5.2.1 contract: the headless requirements file must not list
    PySide6 / pyobjc / pyinstaller. If any of those leak back in we
    re-introduce the same wheel-availability failures on aarch64 / new
    Pythons that the v5.2.1 split was meant to fix."""
    from services.remote_runner import repo_root
    text = (repo_root() / "requirements-remote.txt").read_text(
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

    # First ssh call should be a `mkdir -p` covering the project dir plus
    # every subdirectory the manifest names. The subdirectories are read out
    # of the manifest rather than written here: this test used to name
    # `_Docs` — the one manifest entry that has since moved to the repository
    # root — so it asserted against a path nobody syncs any more, and it would
    # have gone on passing right up until that entry was the only one left.
    from services.remote_runner import _REQUIRED_REMOTE_FILES

    subdirs = {f.split("/")[0] for f in _REQUIRED_REMOTE_FILES if "/" in f}
    assert subdirs, "the manifest has no subdirectory entries left to check"
    assert captured, "expected at least one ssh mkdir call"
    mk = captured[0]
    assert mk[0] == "mkdir"
    assert "-p" in mk
    assert any(host.remote_project_dir in arg for arg in mk)
    for sub in sorted(subdirs):
        assert any(sub in arg for arg in mk), (
            f"manifest subdirectory {sub!r} is not pre-created; rsync with "
            f"individual file sources will not make it. mkdir argv: {mk}")


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
    (``process_microscopy.py``, ``requirements-remote.txt``)
    and the subprocess.run cwd is the repo root. Pre-v5.2.12 we used
    absolute paths with the ``/./`` pivot, which older rsync versions
    (macOS bundled 2.6.9) mis-handled — they preserved the FULL local
    path on the remote, creating directories like
    ``~/allwell/Users/me/allwell/requirements-remote.txt`` instead
    of ``~/allwell/requirements-remote.txt``.

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
    from services.remote_run_state import _RemoteRun

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
    runner._run = run
    (tmp_path / "x_A01.csv").write_text("ok\n")
    # v5.2.29: the verify step now also requires the per-well
    # out_zip / extracted folder when output_filter=FULL (the
    # default). Pre-create the zip so existing happy-path tests
    # stay green.
    (tmp_path / "A01_out.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    return runner


def test_verify_and_cleanup_run_mode_removes_run_dir_only(runner_with_run, monkeypatch):
    runner = runner_with_run
    calls: list[list[str]] = []

    def fake_ssh_run(self, argv, *, check):
        calls.append(list(argv))
        return MagicMock(stdout="", returncode=0)

    monkeypatch.setattr(DatasetCache, "_ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        FetchedOutputVerifier, "_rsync_optional",
        lambda self, src, dest, log_fn, **kw: None,
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
        FetchedOutputVerifier, "_rsync_optional",
        lambda self, src, dest, log_fn, **kw: None,
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
        FetchedOutputVerifier, "_rsync_optional",
        lambda self, src, dest, log_fn, **kw: None,
    )
    runner.verify_and_cleanup(ok=True, cleanup_mode="none", log_fn=lambda _m: None)
    rm_calls = [c for c in calls if c and c[0] == "rm"]
    assert rm_calls == []


def test_verify_and_cleanup_dry_run_does_not_rm(runner_with_run, monkeypatch):
    """v5.2.23: with dry_run=True, the ``rm -rf`` ssh calls must NOT
    fire. Instead, a probe via _ssh_script reports what WOULD be
    deleted (path, file count, total bytes)."""
    runner = runner_with_run
    rm_calls: list[list[str]] = []
    script_calls: list[str] = []

    def fake_ssh_run(self, argv, *, check):
        rm_calls.append(list(argv))
        return MagicMock(stdout="", returncode=0)

    def fake_ssh_script(self, script, *, check):
        script_calls.append(script)
        # Probe returns realistic size + count.
        return MagicMock(stdout="BYTES 12345678\nFILES 42\n", returncode=0)

    monkeypatch.setattr(DatasetCache, "_ssh_run", fake_ssh_run)
    monkeypatch.setattr(DatasetCache, "_ssh_script", fake_ssh_script)
    monkeypatch.setattr(
        FetchedOutputVerifier, "_rsync_optional",
        lambda self, src, dest, log_fn, **kw: None,
    )
    log_lines: list[str] = []
    ok = runner.verify_and_cleanup(
        ok=True, cleanup_mode="all", dry_run=True,
        log_fn=log_lines.append,
    )
    assert ok
    # No actual rm -rf was fired.
    assert not any(c and c[0] == "rm" for c in rm_calls), (
        f"dry-run must not delete; got rm calls: {rm_calls}"
    )
    # Four probes: the verify backstop and the end-of-run reconciliation each
    # list *_out.zip on the remote output dir, plus one dry-run probe per
    # cleanup target (run dir + dataset cache).
    assert len(script_calls) == 4
    dry_run_probes = [s for s in script_calls if "du -sb" in s]
    assert len(dry_run_probes) == 2
    blob = "".join(log_lines)
    # Both targets appear with [DRY-RUN] prefix, with byte/file counts.
    assert "[DRY-RUN]" in blob
    assert "would delete run dir run123" in blob
    assert "would delete dataset cache deadbeef" in blob
    # Counts surfaced.
    assert "42 file(s)" in blob
    assert "11.8 MB" in blob  # 12345678 bytes → 11.8 MB
    # Footer reminds the user it was a dry run.
    assert "no files were actually deleted" in blob


def test_verify_and_cleanup_dry_run_handles_missing_remote_path(runner_with_run, monkeypatch):
    """If the remote path doesn't exist (already cleaned up by a
    previous run), the dry-run probe must say so explicitly — not
    print a 0-byte/0-file delete line that would be misleading."""
    runner = runner_with_run

    monkeypatch.setattr(
        DatasetCache, "_ssh_run",
        lambda self, argv, *, check: MagicMock(stdout="", returncode=0),
    )
    monkeypatch.setattr(
        DatasetCache, "_ssh_script",
        lambda self, script, *, check: MagicMock(stdout="MISSING\n", returncode=0),
    )
    monkeypatch.setattr(
        FetchedOutputVerifier, "_rsync_optional",
        lambda self, src, dest, log_fn, **kw: None,
    )
    log_lines: list[str] = []
    runner.verify_and_cleanup(
        ok=True, cleanup_mode="run", dry_run=True,
        log_fn=log_lines.append,
    )
    blob = "".join(log_lines)
    assert "doesn't exist on remote" in blob
    assert "already cleaned up" in blob


def test_verify_and_cleanup_dry_run_with_none_mode_is_a_noop(runner_with_run, monkeypatch):
    """dry_run + cleanup_mode='none' = nothing to delete and no
    ``[DRY-RUN]`` chatter in the log. The v5.2.25 verify backstop
    may run an ``ls`` probe to look for already-completed wells —
    that's a different concern and stays."""
    runner = runner_with_run
    script_calls: list[str] = []

    monkeypatch.setattr(
        DatasetCache, "_ssh_run",
        lambda self, argv, *, check: MagicMock(stdout="", returncode=0),
    )
    monkeypatch.setattr(
        DatasetCache, "_ssh_script",
        lambda self, script, *, check: (
            script_calls.append(script) or MagicMock(stdout="", returncode=0)
        ),
    )
    monkeypatch.setattr(
        FetchedOutputVerifier, "_rsync_optional",
        lambda self, src, dest, log_fn, **kw: None,
    )
    log_lines: list[str] = []
    runner.verify_and_cleanup(
        ok=True, cleanup_mode="none", dry_run=True,
        log_fn=log_lines.append,
    )
    # No du-sb dry-run probe should fire — cleanup_mode=none
    # short-circuits before any cleanup probing.
    dry_run_probes = [s for s in script_calls if "du -sb" in s]
    assert dry_run_probes == []
    blob = "".join(log_lines)
    assert "[DRY-RUN]" not in blob


def test_verify_and_cleanup_skips_when_failure(runner_with_run, monkeypatch):
    runner = runner_with_run
    calls: list[list[str]] = []

    def fake_ssh_run(self, argv, *, check):
        calls.append(list(argv))
        return MagicMock(stdout="", returncode=0)

    monkeypatch.setattr(DatasetCache, "_ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        FetchedOutputVerifier, "_rsync_optional",
        lambda self, src, dest, log_fn, **kw: None,
    )
    ok = runner.verify_and_cleanup(ok=False, cleanup_mode="all", log_fn=lambda _m: None)
    assert not ok
    rm_calls = [c for c in calls if c and c[0] == "rm"]
    assert rm_calls == []


def test_verify_and_cleanup_skips_when_local_missing(runner_with_run, monkeypatch):
    runner = runner_with_run
    # Delete the CSV the fixture created — verification must fail and
    # cleanup must NOT fire.
    (runner_with_run._run.local_output_dir / "x_A01.csv").unlink()

    calls: list[list[str]] = []

    def fake_ssh_run(self, argv, *, check):
        calls.append(list(argv))
        return MagicMock(stdout="", returncode=0)

    monkeypatch.setattr(DatasetCache, "_ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        FetchedOutputVerifier, "_rsync_optional",
        lambda self, src, dest, log_fn, **kw: None,
    )
    # `_retry_missing_wells` runs against a host that does not exist: it lists
    # the well's CSVs over ssh and re-pulls what verify found missing, which is
    # three rsync attempts and 14 s of backoff to establish what these two
    # lines say outright (tests/conftest.py::_no_real_network_tools).
    monkeypatch.setattr(
        "services.remote_runner.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=255, stdout=b"", stderr=b""),
    )
    monkeypatch.setattr(
        "services.remote_fetch._rsync_with_retry",
        lambda cmd, **kw: MagicMock(returncode=255, stdout="", stderr=""),
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
    from services.remote_runner import _rsync_version
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
    from services.remote_runner import _rsync_version
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
    assert _looks_like_rsync_filename("requirements-remote.txt") is True


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
    from services.remote_run_state import _RemoteRun
    runner = RemoteRunner(host)
    run = _RemoteRun(
        run_id="x", cache_key="k",
        remote_input_dir="/r/in", remote_output_dir="/r/out",
        remote_pidfile="/r/pid", local_output_dir=tmp_path,
        local_input_mode=LocalInputMode.ZIP,
    )
    run.expected_wells = {"A01"}
    runner._run = run
    (tmp_path / "x_A01.csv").write_text("ok\n")

    rm_calls = []
    monkeypatch.setattr(
        DatasetCache, "_ssh_run",
        lambda self, argv, *, check: (
            rm_calls.append(list(argv)) if argv and argv[0] == "rm" else None
        ) or MagicMock(stdout="", returncode=0),
    )
    monkeypatch.setattr(
        FetchedOutputVerifier, "_rsync_optional",
        lambda self, src, dest, log_fn, **kw: None,
    )
    # `_retry_missing_wells` runs against a host that does not exist: it lists
    # the well's CSVs over ssh and re-pulls what verify found missing, which is
    # three rsync attempts and 14 s of backoff to establish what these two
    # lines say outright (tests/conftest.py::_no_real_network_tools).
    monkeypatch.setattr(
        "services.remote_runner.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=255, stdout=b"", stderr=b""),
    )
    monkeypatch.setattr(
        "services.remote_fetch._rsync_with_retry",
        lambda cmd, **kw: MagicMock(returncode=255, stdout="", stderr=""),
    )

    # Default call (no cleanup_mode arg)
    runner.verify_and_cleanup(ok=True, log_fn=lambda _m: None)
    # No rm calls — the default doesn't delete anything.
    assert rm_calls == [], \
        f"v5.2.11 default must not delete; observed rm calls: {rm_calls}"


def test_cancel_stops_in_flight_fetcher(host):
    """Stop during the final per-well rsync must interrupt the download.

    RemoteRunner.cancel now sets the cancel flag AND cancels the Fetcher —
    whose in-flight rsync (and bounded-retry loop) polls that stop event —
    instead of only ssh-killing the remote. Without this, clicking Stop while
    the final fetch was blocking / retry-looping did nothing.
    """
    runner = RemoteRunner(host)
    cancelled = {"n": 0}

    class _FakeFetcher:
        def cancel(self):
            cancelled["n"] += 1

    runner._fetcher = _FakeFetcher()
    # _run is None → cancel() returns before any ssh call, after the fetcher hook.
    runner.cancel(None)
    assert runner._cancelled.is_set()
    assert cancelled["n"] == 1


# ── end-of-run retry backstop: must not wall-clock-hang or over-pull ──────────

def _retry_runner(host, tmp_path, monkeypatch):
    """A RemoteRunner with a _run and the rsync helpers stubbed; returns
    (runner, pulled) where pulled records each re-pull's argv + kwargs."""
    import services.remote_runner as rr
    run = _make_run(tmp_path)
    run.expected_wells = {"A01"}
    runner = rr.RemoteRunner(host)
    runner._run = run
    pulled: list[dict] = []

    def _fake_rsync(argv, *, log_fn=None, timeout=300, stop_event=None, **kw):
        pulled.append({"argv": " ".join(map(str, argv)),
                       "timeout": timeout, "stop_event": stop_event})
        return MagicMock(returncode=0)

    monkeypatch.setattr(remote_fetch, "_rsync_with_retry", _fake_rsync)
    monkeypatch.setattr(remote_fetch, "resolve_rsync_binary", lambda: "rsync")
    monkeypatch.setattr(remote_fetch, "_rsync_ssh_transport", lambda h: [])
    monkeypatch.setattr(remote_fetch, "to_rsync_path", lambda p: str(p))
    return runner, pulled, rr


def test_retry_skips_artifacts_already_present(host, tmp_path, monkeypatch):
    """If the CSV and the out-zip are already local, the backstop re-pulls
    nothing — no needless transfer (and no chance to hang)."""
    runner, pulled, _ = _retry_runner(host, tmp_path, monkeypatch)
    (tmp_path / "data_A01.csv").write_text("x")
    (tmp_path / "A01_out.zip").write_text("zip")
    runner._output_verifier()._retry_missing_wells(["missing CSV for A01"], log=lambda _m: None)
    assert pulled == []


def test_retry_csv_miss_does_not_drag_the_zip(host, tmp_path, monkeypatch):
    """A missing CSV must NOT trigger re-pull of the (present) multi-hundred-MB
    out-zip — the old unconditional zip pull is what fed the wall-clock loop."""
    runner, pulled, rr = _retry_runner(host, tmp_path, monkeypatch)
    (tmp_path / "A01_out.zip").write_text("zip")  # zip present; CSV absent
    monkeypatch.setattr(
        rr.subprocess, "run",
        lambda *a, **k: MagicMock(stdout=b"/r/out/data_A01.csv\n", returncode=0))
    runner._output_verifier()._retry_missing_wells(["missing CSV for A01"], log=lambda _m: None)
    assert any("data_A01.csv" in p["argv"] for p in pulled)
    assert not any("A01_out.zip" in p["argv"] for p in pulled)


def test_retry_zip_repull_has_no_walltimeout_and_is_stop_aware(host, tmp_path, monkeypatch):
    """The genuinely-missing out-zip re-pull runs with timeout=None (no
    wall-clock kill of a progressing transfer) and the cancel event wired in
    (so Stop interrupts it) — the fix for the '7/7 fetched, then rsync retry'
    hang."""
    runner, pulled, _ = _retry_runner(host, tmp_path, monkeypatch)
    (tmp_path / "data_A01.csv").write_text("x")  # CSV present; zip absent
    runner._output_verifier()._retry_missing_wells(["missing A01_out.zip locally"], log=lambda _m: None)
    assert len(pulled) == 1
    assert "A01_out.zip" in pulled[0]["argv"]
    assert pulled[0]["timeout"] is None
    assert pulled[0]["stop_event"] is runner._cancelled


def test_flush_and_join_waits_for_worker_not_a_fixed_cap(host, tmp_path):
    """flush_and_join must block until the download worker actually finishes —
    NOT return after a fixed interval while it's still in flight. Returning
    early let verify_and_cleanup delete the remote mid-fetch ("completed run →
    deleted cache → then finished fetcher")."""
    import time
    import threading as _t
    run = _make_run(tmp_path)
    run.well_count_total = 1
    started = _t.Event()

    def _slow_loop(self):
        started.set()
        time.sleep(0.5)                      # outlives the tiny join interval
        self.run.fetched_wells.add("A01")

    with patch.object(Fetcher, "_loop", _slow_loop):
        fetcher = Fetcher(host, run, output_filter=OutputFilter.FULL,
                          log_fn=lambda _m: None)
    assert started.wait(2.0)
    t0 = time.monotonic()
    fetcher.flush_and_join(timeout=0.1)       # tiny cap — must still wait it out
    elapsed = time.monotonic() - t0
    assert not fetcher._worker.is_alive()     # did not return while worker alive
    assert "A01" in run.fetched_wells         # worker's work completed first
    assert elapsed >= 0.4                     # blocked past the 0.1s interval


def test_scratch_dir_honors_env_override(tmp_path, monkeypatch):
    """Local scratch (input staging, transit zips) must be redirectable off the
    OS temp drive — Windows %TEMP% on C: filled up when staging a NAS dataset
    for a remote run."""
    from services import remote_runner as rr
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(tmp_path / "scratch"))
    base = rr._scratch_dir()
    assert base == tmp_path / "scratch"
    assert base.is_dir()
    d = rr._new_scratch_dir("allwell-stage-")
    assert d.parent == base and d.name.startswith("allwell-stage-")


def test_scratch_dir_falls_back_when_override_unusable(tmp_path, monkeypatch):
    from services import remote_runner as rr
    # A path that can't be created (a file in the way) → fall back to the
    # default, which is the same ~/.allwell/scratch the pipeline uses.
    bad = tmp_path / "afile"
    bad.write_text("x")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(bad / "nope"))
    assert rr._scratch_dir() == home / ".allwell" / "scratch"


def test_scratch_dir_falls_back_to_os_temp_when_home_is_unwritable(
    tmp_path, monkeypatch,
):
    """Last resort. An unwritable home must not fail the run outright."""
    import tempfile as _t
    from services import remote_runner as rr
    blocked = tmp_path / "afile"
    blocked.write_text("x")
    monkeypatch.delenv("ALLWELL_SCRATCH_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: blocked))
    assert rr._scratch_dir() == Path(_t.gettempdir())


def test_sweep_stale_scratch_removes_old_only(tmp_path, monkeypatch):
    import time as _time
    from services import remote_runner as rr
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(tmp_path))
    old = tmp_path / "allwell-stage-OLD"; old.mkdir()
    os.utime(old, (_time.time() - 7 * 3600, _time.time() - 7 * 3600))
    fresh = tmp_path / "allwell-stage-NEW"; fresh.mkdir()
    other = tmp_path / "not-ours"; other.mkdir()
    n = rr.sweep_stale_scratch(max_age_h=6.0)
    assert n == 1
    assert not old.exists()      # stale leftover removed
    assert fresh.exists()        # a concurrent run's live staging is kept
    assert other.exists()        # unrelated dirs untouched


# ── _compose_remote_cmd: direct vs scheduler-launched compute ────────────────


_PIDFILE = "~/.allwell-staging/run-abc/pid"
_ARGV = ["conda", "run", "--no-capture-output", "-n", "allwell", "python",
         "~/allwell/process_microscopy.py", "--input_dir", "~/.allwell-staging/in",
         "--output_dir", "~/.allwell-staging/out"]


def test_compose_remote_cmd_direct_is_legacy_shape():
    """No launcher → byte-identical to the pre-compute_launcher command:
    record PID on the login shell, activate conda there, exec the pipeline.
    This is the backward-compat guard — every existing host hits this path."""
    cmd = _compose_remote_cmd("", _PIDFILE, _ARGV, cache_hit=False)
    assert cmd.startswith('echo $$ > "$HOME"')          # tilde-aware pidfile
    assert _CONDA_ACTIVATE_SNIPPET in cmd
    assert "srun" not in cmd
    assert "bash -lc" not in cmd
    # conda activation precedes the exec of the pipeline.
    assert cmd.index(_CONDA_ACTIVATE_SNIPPET) < cmd.index("process_microscopy.py")


def test_compose_remote_cmd_scheduled_wraps_in_srun():
    """A launcher → the login shell records its PID then execs srun, and the
    conda activation + pipeline run INSIDE the srun step (bash -lc) so they
    land on the scheduled compute node, not the login node."""
    launcher = "srun --partition=gpu --gres=gpu:1 --time=4:00:00"
    cmd = _compose_remote_cmd(launcher, _PIDFILE, _ARGV, cache_hit=False)
    # PID still recorded on the login side, before srun.
    assert cmd.startswith('echo $$ > "$HOME"')
    assert cmd.index("echo $$") < cmd.index("srun")
    # The launcher tokens are present and followed by bash -lc.
    assert "srun --partition=gpu --gres=gpu:1 --time=4:00:00 bash -lc" in cmd
    # conda + the pipeline are inside the wrapped (quoted) inner script.
    assert _CONDA_ACTIVATE_SNIPPET in cmd
    assert "process_microscopy.py" in cmd
    # The job id is captured for the scancel cancel-channel.
    assert "SLURM_JOB_ID" in cmd
    assert ".slurm" in cmd
    # A status echo runs inside the allocation (after a node is granted) so
    # the user sees the job id + node + partition once the queue wait clears.
    assert "[remote] scheduler: allocation granted" in cmd
    assert "SLURMD_NODENAME" in cmd
    assert "SLURM_JOB_PARTITION" in cmd
    # ...and it runs before the pipeline exec.
    assert cmd.index("allocation granted") < cmd.index("process_microscopy.py")


def test_compose_remote_cmd_cache_hit_never_allocates_a_node():
    """A full cache hit replaces the pipeline with the ``true`` no-op; even
    with a launcher configured we must NOT consume a compute allocation just
    to run ``true``, so the command stays the direct (unwrapped) shape."""
    cmd = _compose_remote_cmd(
        "srun --partition=gpu", _PIDFILE, ["true"], cache_hit=True,
    )
    assert "srun" not in cmd
    assert "bash -lc" not in cmd
    assert cmd.endswith("exec true")


def test_compose_remote_cmd_whitespace_only_launcher_is_direct():
    """A launcher that is only whitespace is treated as 'no launcher' so a
    stray space in remotes.toml can't silently force scheduler mode."""
    cmd = _compose_remote_cmd("   ", _PIDFILE, _ARGV, cache_hit=False)
    assert "srun" not in cmd
    assert "bash -lc" not in cmd


# ── close_remote_job: guaranteed scheduler-job teardown ──────────────────────


def _runner_with_run(compute_launcher: str):
    """A RemoteRunner with a minimal _run set, no real ssh."""
    h = RemoteHost(name="c", ssh_alias="lobby", compute_launcher=compute_launcher)
    r = RemoteRunner(h)
    from services.remote_run_state import _RemoteRun
    r._run = _RemoteRun(
        run_id="run-x",
        cache_key="k",
        remote_input_dir="~/.allwell-staging/in",
        remote_output_dir="~/.allwell-staging/out",
        remote_pidfile="~/.allwell-staging/runs/run-x/run.pid",
        local_output_dir=Path("/tmp/out"),
        local_input_mode=LocalInputMode.ZIP,
        output_filter=OutputFilter.FULL,
        incremental=False,
    )
    return r


def test_close_remote_job_noop_on_direct_host():
    """Direct-execution host (no launcher) → never shells out to scancel."""
    r = _runner_with_run("")
    with patch("services.remote_runner.subprocess.run") as run:
        r.close_remote_job()
    run.assert_not_called()


def test_close_remote_job_scancels_scheduled_host():
    """Scheduler-backed host → ssh's a scancel of the recorded job id and
    logs that a lingering job was cancelled."""
    r = _runner_with_run("srun --partition=gpu")
    logs: list[str] = []
    with patch("services.remote_runner.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="AW_SCANCELLED 12345\n", stderr="",
        )
        r.close_remote_job(log_fn=logs.append)
    run.assert_called_once()
    sent = " ".join(run.call_args.args[0])
    assert "scancel" in sent
    assert ".slurm" in sent      # reads the recorded job-id sidecar
    assert any("cancelled lingering job 12345" in m for m in logs)


def test_close_remote_job_logs_clean_finish_when_nothing_to_cancel():
    """The common case: srun already exited, so scancel finds nothing — we
    still emit a definite end-of-job marker instead of silence."""
    r = _runner_with_run("srun --partition=gpu")
    logs: list[str] = []
    with patch("services.remote_runner.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        r.close_remote_job(log_fn=logs.append)
    assert any("already finished" in m for m in logs)


def test_close_remote_job_noop_before_run_starts():
    h = RemoteHost(name="c", ssh_alias="lobby", compute_launcher="srun -p gpu")
    r = RemoteRunner(h)  # no _run yet
    with patch("services.remote_runner.subprocess.run") as run:
        r.close_remote_job()
    run.assert_not_called()


def test_close_remote_job_swallows_ssh_errors():
    """Teardown is best-effort: an ssh timeout/OSError must not raise."""
    r = _runner_with_run("srun -p gpu")
    with patch(
        "services.remote_runner.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=10),
    ):
        r.close_remote_job()  # must not raise


def test_retry_repulls_out_zip_for_folder_mode(host, tmp_path, monkeypatch):
    """Audit G2 — a FOLDER + FULL run whose out-zip failed to land must still
    be re-pulled. ``_verify_local_manifest`` requires ``{well}_out.zip`` for
    every FULL run (FOLDER included), so if ``_retry_missing_wells`` skips the
    zip re-pull for FOLDER, verify→retry→verify never converges and remote
    cleanup is skipped forever."""
    import services.remote_runner as rr
    run = _make_run(tmp_path)
    run.local_input_mode = rr.LocalInputMode.FOLDER
    run.output_filter = rr.OutputFilter.FULL
    run.expected_wells = {"A01"}
    runner = rr.RemoteRunner(host)
    runner._run = run

    pulled: list[str] = []

    def _fake_rsync(argv, *, log_fn=None, timeout=300, stop_event=None, **kw):
        pulled.append(" ".join(map(str, argv)))
        return MagicMock(returncode=0)

    monkeypatch.setattr(remote_fetch, "_rsync_with_retry", _fake_rsync)
    monkeypatch.setattr(remote_fetch, "resolve_rsync_binary", lambda: "rsync")
    monkeypatch.setattr(remote_fetch, "_rsync_ssh_transport", lambda h: [])
    monkeypatch.setattr(remote_fetch, "to_rsync_path", lambda p: str(p))

    (tmp_path / "data_A01.csv").write_text("x")   # CSV present; out-zip absent
    runner._output_verifier()._retry_missing_wells(["missing A01_out.zip locally"], log=lambda _m: None)

    assert any("A01_out.zip" in p for p in pulled), \
        "FOLDER-mode out-zip must be re-pulled (was silently skipped)"


def test_close_remote_job_warns_on_ssh_failure():
    """Audit E13 — a failed scancel must be surfaced (an orphaned SLURM
    allocation could keep running unnoticed), not swallowed silently."""
    r = _runner_with_run("srun -p gpu")
    logs: list[str] = []
    with patch(
        "services.remote_runner.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=10),
    ):
        r.close_remote_job(log_fn=logs.append)   # must not raise
    assert any("WARN" in m and "scancel" in m for m in logs), \
        f"expected a scancel-failure warning; got {logs}"


def test_old_rsync_still_gets_progress_flags():
    """macOS ships rsync 2.6.9, which has no ``--info=`` at all — and that is
    the machine most runs are launched from. The gate around ``--info`` fell
    through to NOTHING, so rsync ran silently for the whole transfer and every
    consumer downstream went dark with it: no per-file lines means no
    ``[remote] ↑ <well>.zip``, so the Upload rail sat at 0/N and no well took
    the "uploaded" colour; no progress stream means no transfer bar and no
    per-well pie slices. A multi-hour upload was indistinguishable from a hang.
    ``-v`` and ``--progress`` both predate 3.0 and produce shapes the existing
    parsers already accept."""
    from unittest import mock

    # Patched on `remote_transport`, which is where both functions live now.
    # `remote_runner` re-exports them, but a re-export is a *binding*: rebinding
    # the name there leaves `_rsync_progress_flags` still reading the original
    # out of its own module globals, so the patch would silently not apply.
    from services import remote_transport as rt

    with mock.patch.object(rt, "_rsync_supports_info_flag", lambda: True):
        assert rt._rsync_progress_flags() == ["--info=name1,stats2,progress2"]
    with mock.patch.object(rt, "_rsync_supports_info_flag", lambda: False):
        assert rt._rsync_progress_flags() == ["-v", "--progress"]
    # Never empty: an rsync that narrates nothing is the bug.
    for supported in (True, False):
        with mock.patch.object(rt, "_rsync_supports_info_flag", lambda: supported):
            assert rt._rsync_progress_flags()


def test_legacy_rsync_output_shapes_still_parse():
    """What rsync 2.6.9 actually prints under ``-v --progress``: a bare
    filename per transferred file, and a per-file percentage line."""
    import re

    from services.remote_runner import _looks_like_rsync_filename

    assert _looks_like_rsync_filename("C08.zip")
    assert not _looks_like_rsync_filename("sending incremental file list")

    # The same regex _stream_rsync_with_retry uses for the percentage stream.
    progress_re = re.compile(r"(\d[\d,]*)\s+(\d+)%\s+(\S+)\s+\S+(?:\s+\(.*\))?")
    m = progress_re.search(
        "     32,768  100%   31.25MB/s    0:00:00 (xfer#1, to-check=5/6)")
    assert m is not None
    assert m.group(2) == "100"
    assert int(m.group(1).replace(",", "")) == 32768
