"""The desktop app's one local folder, and the storage dialog that clears it.

Reported: the user was running out of disk space to app files that were never
cleaned up, spread over ``~/.cache/allwell``, the system temp folder and the
source checkout. The app's own files now live in one folder —
``%LOCALAPPDATA%\\allwell`` on Windows, ``~/.allwell`` on macOS and Linux
(``util.state_dir.gui_state_dir``) — and the title bar's storage button measures
and clears them (``core.local_storage``, ``ui.local_storage_view``).

What must never be cleared is pinned here as firmly as what must: a running
pool's spill, the live boot log, the analysis pipeline's scratch and its CZI
scan cache — the last two being the pipeline's, which this change was told
not to touch.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core import local_storage as ls
from util import state_dir


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A private home folder on a POSIX layout, so ``gui_state_dir`` is ``home/.allwell``."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(state_dir.STATE_DIR_ENV, raising=False)
    monkeypatch.setattr(state_dir, "is_windows", lambda: False)
    return home


def _write(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _age(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


# ── where the app's files go ────────────────────────────────────────────────

def test_on_windows_the_app_folder_is_localappdata(tmp_path, monkeypatch):
    local = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv(state_dir.STATE_DIR_ENV, raising=False)
    monkeypatch.setattr(state_dir, "is_windows", lambda: True)
    assert state_dir.gui_candidate_dirs()[0] == local / "allwell"
    assert state_dir.gui_state_dir() == local / "allwell"
    assert state_dir.gui_temp_dir() == local / "allwell" / "tmp"


def test_on_macos_and_linux_the_app_folder_is_dot_allwell(home, monkeypatch):
    # A LOCALAPPDATA left in the environment must not pull a Mac's files elsewhere.
    monkeypatch.setenv("LOCALAPPDATA", str(home / "nope"))
    assert state_dir.gui_state_dir() == home / ".allwell"
    assert state_dir.gui_temp_dir() == home / ".allwell" / "tmp"


def test_the_app_s_writers_all_use_the_app_folder(home, monkeypatch):
    """Boot log, bootstrap logs, font cache, a cell pool's spill and Batch
    Export's no-disk staging: every one the desktop app writes."""
    from startup import boot_diag, mpl_bootstrap
    from services import remote_bootstrap

    target = home / ".allwell"
    monkeypatch.setattr(boot_diag, "_LOG_PATH", None)
    assert boot_diag.log_path() == target / "allwell-boot.log"
    assert Path(mpl_bootstrap._cache_root()).parent == target / "mplcache"
    path, fh = remote_bootstrap._open_log_file()
    try:
        assert path.parent == target
    finally:
        fh.close()


def test_the_scan_cache_is_in_the_app_folder(home, monkeypatch):
    """The CZI scan cache sits in the app's folder like everything else, and
    its old copy in ``~/.cache/allwell`` is offered — unless
    ``ALLWELL_SCAN_CACHE`` still points runs at it."""
    from pipeline import scan_cache

    monkeypatch.delenv("ALLWELL_SCAN_CACHE", raising=False)
    assert scan_cache.cache_dir() == home / ".allwell" / "czi-scans"
    old = _write(home / ".cache" / "allwell" / "czi-scans" / "k" / "scan.json", 10)
    assert old.parent.parent in state_dir.legacy_gui_paths()
    monkeypatch.setenv("ALLWELL_SCAN_CACHE", str(old.parent.parent))
    assert old.parent.parent not in state_dir.legacy_gui_paths()


def _windows(tmp_path, monkeypatch) -> tuple[Path, Path]:
    """A Windows layout: ``LOCALAPPDATA`` and a home folder, no overrides."""
    local = tmp_path / "AppData" / "Local"
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv(state_dir.STATE_DIR_ENV, raising=False)
    monkeypatch.delenv(state_dir.SCRATCH_DIR_ENV, raising=False)
    monkeypatch.setattr(state_dir, "is_windows", lambda: True)
    # Keep the real temp folder and checkout out of what the dialog offers.
    monkeypatch.setattr(ls.tempfile, "tempdir", str(tmp_path / "system-temp"))
    (tmp_path / "system-temp").mkdir(exist_ok=True)
    monkeypatch.setattr(state_dir, "REPO_FONT_CACHE", tmp_path / "checkout" / ".mplcache")
    return local, home


def test_scratch_sits_in_the_app_folder_on_macos_and_linux(home, monkeypatch):
    """Reported: the app's caches and the run scratch lived in different
    places depending on the platform. Scratch is now ``scratch/`` inside the
    app's folder everywhere — here ``~/.allwell/scratch``, as it always was —
    and the pipeline and the remote client resolve it identically."""
    from pipeline import io as pio
    from services.remote_scratch import _scratch_dir

    monkeypatch.delenv(state_dir.SCRATCH_DIR_ENV, raising=False)
    want = home / ".allwell" / "scratch"
    assert state_dir.gui_state_dir() / "scratch" == want
    assert pio.scratch_base_for(home / "out") == want
    assert _scratch_dir() == want
    assert state_dir.default_scratch_label() == "~/.allwell/scratch"
    assert state_dir.legacy_scratch_dir() is None


def test_scratch_sits_in_the_app_folder_on_windows(tmp_path, monkeypatch):
    """On Windows the app's folder is ``%LOCALAPPDATA%\\allwell``, and scratch
    used to be ``%USERPROFILE%\\.allwell\\scratch`` beside it in a different
    tree. Both now resolve to ``%LOCALAPPDATA%\\allwell\\scratch``."""
    from pipeline import io as pio
    from services.remote_scratch import _scratch_dir

    local, home = _windows(tmp_path, monkeypatch)
    want = local / "allwell" / "scratch"
    assert state_dir.gui_state_dir() / "scratch" == want
    assert pio.scratch_base_for(tmp_path / "out") == want
    assert _scratch_dir() == want
    assert state_dir.default_scratch_label() == str(want)
    assert not (home / ".allwell").exists(), "nothing is written to the old folder"


def test_scratch_follows_the_app_folder_but_not_into_the_temp_folder(tmp_path, monkeypatch):
    """When no app folder but the system temp one can be written, scratch gives
    up rather than following: each caller's own last resort applies (the output
    folder for the pipeline), not a tmpfs that may be memory."""
    from pipeline import io as pio

    monkeypatch.delenv(state_dir.SCRATCH_DIR_ENV, raising=False)
    temp_root = Path(state_dir.tempfile.gettempdir()) / state_dir.APP_DIRNAME
    monkeypatch.setattr(state_dir, "gui_candidate_dirs", lambda: [temp_root])
    assert state_dir.default_scratch_dir() is None
    assert pio.scratch_base_for(tmp_path / "out") == tmp_path / "out"


def test_on_windows_the_old_scratch_is_offered_as_legacy(tmp_path, monkeypatch):
    """An older version's scratch in ``~/.allwell/scratch`` is read by nothing
    once scratch moved, and can be gigabytes — so the legacy area offers it."""
    local, home = _windows(tmp_path, monkeypatch)
    old = _write(home / ".allwell" / "scratch" / "_tmp_images_x" / "a.tif", 4000)
    current = _write(local / "allwell" / "scratch" / "_tmp_images_y" / "b.tif", 3000)
    offered = {a.key: set(a.paths) for a in ls.cache_areas(None)}
    assert offered["legacy"] == {home / ".allwell" / "scratch"}
    assert all(current.parent.parent not in paths for paths in offered.values())
    ls.clear([a for a in ls.cache_areas(None) if a.key == "legacy"])
    assert not old.exists() and current.exists()


def test_the_old_scratch_is_kept_when_runs_are_pointed_at_it(tmp_path, monkeypatch):
    """A user who set the Scratch disk field to that same folder is still using it."""
    _, home = _windows(tmp_path, monkeypatch)
    _write(home / ".allwell" / "scratch" / "_tmp_images_x" / "a.tif", 4000)
    monkeypatch.setenv(state_dir.SCRATCH_DIR_ENV, str(home / ".allwell" / "scratch"))
    assert state_dir.legacy_scratch_dir() is None
    monkeypatch.setenv(state_dir.SCRATCH_DIR_ENV, str(home / ".allwell" / "scratch" / "sub"))
    assert state_dir.legacy_scratch_dir() is None


# ── what the dialog offers ──────────────────────────────────────────────────

@pytest.fixture
def populated(home, monkeypatch):
    """A home folder holding every kind of file, removable and not."""
    app = home / ".allwell"
    monkeypatch.setattr(ls.tempfile, "tempdir", str(home / "system-temp"))
    (home / "system-temp").mkdir()
    monkeypatch.setattr(state_dir, "REPO_FONT_CACHE", home / "checkout" / ".mplcache")
    files = {
        # the app's folder
        "fonts": _write(app / "mplcache" / "fontlist.json", 1000),
        "boot": _write(app / "allwell-boot.log", 7),          # this launch's
        "boot1": _write(app / "allwell-boot.log.1", 300),
        "bootstrap": _write(app / "bootstrap-20260101T000000.log", 200),
        # a remote setup written to within the grace period, and the newest one:
        # either may still be running, so neither is offered
        "bootstrap_recent": _write(app / "bootstrap-20260102T000000.log", 20),
        "bootstrap_new": _write(app / "bootstrap-20260103T000000.log", 30),
        "dead_spill": _write(app / "tmp" / "allwell-cellpool-a" / "cells.duckdb", 5000),
        "live_spill": _write(app / "tmp" / "allwell-cellpool-b" / "cells.duckdb", 4000),
        "young_unowned": _write(app / "tmp" / "allwell_batch_nodisk_c" / "f.png", 3000),
        "old_unowned": _write(app / "tmp" / "allwell_batch_nodisk_d" / "f.png", 2000),
        # runs' files and the user's records, in the same folder: never offered
        "scratch": _write(app / "scratch" / "_tmp_images_x" / ".fov_1.done", 9000),
        "suspended": _write(app / "suspended_runs.json", 50),
        "scan": _write(app / "czi-scans" / "k" / "scan.json", 8000),
        "remotes": _write(app / "remotes.toml", 30),
        "queue": _write(app / "job_queue.json", 30),
        "settings": _write(app / "settings.ini", 30),
        # earlier versions' leftovers
        "legacy_boot": _write(home / ".cache" / "allwell" / "allwell-boot.log", 40),
        "legacy_fonts": _write(home / ".cache" / "allwell" / "mplcache" / "f.json", 600),
        "legacy_scan": _write(home / ".cache" / "allwell" / "czi-scans" / "k" / "scan.json", 260),
        "legacy_spill": _write(home / "system-temp" / "allwell-cellpool-z" / "c.duckdb", 700),
        "repo_fonts": _write(home / "checkout" / ".mplcache" / "fontlist.json", 400),
        # the open dataset
        "rows": _write(home / "plate" / ".aw_parquet" / "A01.parquet", 6000),
        "csv": _write(home / "plate" / "A01.csv", 100),
    }
    # Zero-padded so every owner file is OWNER_BYTES long, whatever the PID.
    for key, pid in (("dead_spill", _dead_pid()), ("live_spill", os.getpid()),
                     ("legacy_spill", _dead_pid())):
        (files[key].parent / "owner.pid").write_text(f"{pid:010d}\n")
    _age(files["old_unowned"].parent, 2 * ls.UNOWNED_GRACE_S)
    _age(files["bootstrap"], 2 * ls.LIVE_LOG_GRACE_S)
    _age(files["bootstrap_recent"], ls.LIVE_LOG_GRACE_S / 3)
    return files


#: Size of one zero-padded ``owner.pid``.
OWNER_BYTES = 11


def _offered(areas) -> dict[str, set[Path]]:
    return {a.key: set(a.paths) for a in areas}


def test_each_area_offers_exactly_what_may_go(populated, home):
    f = populated
    app = home / ".allwell"
    offered = _offered(ls.cache_areas(home / "plate"))
    assert offered == {
        "fonts": {app / "mplcache"},
        "temp": {f["dead_spill"].parent, f["old_unowned"].parent},
        "logs": {f["boot1"], f["bootstrap"]},
        "legacy": {f["legacy_boot"], f["legacy_fonts"].parent, f["legacy_scan"].parent.parent,
                   f["legacy_spill"].parent, f["repo_fonts"].parent},
        "rows": {f["rows"].parent},
    }
    # The row cache is offered unticked: clearing it slows the next open.
    assert [a.key for a in ls.cache_areas(home / "plate") if not a.default_on] == ["rows"]
    # No dataset open, no row cache area.
    assert "rows" not in _offered(ls.cache_areas(None))


def test_clearing_everything_frees_exactly_that_and_spares_the_rest(populated, home):
    f = populated
    areas = ls.cache_areas(home / "plate")
    sizes = ls.measure(areas)
    assert sizes == {"fonts": 1000, "temp": 7000 + OWNER_BYTES, "logs": 500,
                     "legacy": 2000 + OWNER_BYTES, "rows": 6000}
    result = ls.clear(areas)
    assert result.freed == sum(sizes.values()) and result.failed == []
    gone = ("fonts", "boot1", "bootstrap", "dead_spill", "old_unowned",
            "legacy_boot", "legacy_fonts", "legacy_scan", "legacy_spill", "repo_fonts", "rows")
    kept = ("boot", "bootstrap_recent", "bootstrap_new", "live_spill", "young_unowned",
            "scratch", "suspended", "scan", "remotes", "queue", "settings", "csv")
    assert [k for k in gone if f[k].exists()] == []
    assert [k for k in kept if not f[k].exists()] == []
    assert not f["rows"].parent.exists() and not f["dead_spill"].parent.exists()


def test_a_file_that_will_not_go_is_reported_and_not_counted(populated, home, monkeypatch):
    """On Windows a file something holds open cannot be deleted. The rest of
    its folder still goes, the freed total leaves it out, and it is named."""
    held = populated["rows"]
    _write(held.parent / "B02.parquet", 500)
    real_unlink = os.unlink

    def unlink(path, *a, **k):
        # ``rmtree`` on POSIX unlinks by bare name relative to a directory fd.
        if Path(path).name == held.name:
            raise PermissionError("in use")
        return real_unlink(path, *a, **k)

    monkeypatch.setattr(os, "unlink", unlink)
    rows = [a for a in ls.cache_areas(home / "plate") if a.key == "rows"]
    result = ls.clear(rows)
    assert result.freed == 500
    assert result.failed == [str(held)]
    assert held.exists() and not (held.parent / "B02.parquet").exists()
    # The folder left behind must still be enterable. The read-only retry once
    # set its mode to read+write outright, dropping the execute bit: nothing in
    # it could be reached, and as a non-root user (CI) the leftover measured as
    # empty, so the freed total counted the held file too.
    import stat
    assert os.stat(held.parent).st_mode & stat.S_IXUSR, "the leftover folder was made untraversable"


def test_the_row_cache_folder_name_is_the_parquet_cache_s():
    from review.data import parquet_cache

    assert ls.ROW_CACHE_DIRNAME == parquet_cache.CACHE_DIRNAME


def test_sizes_read_as_a_file_manager_shows_them():
    assert [ls.format_bytes(n) for n in (0, 999, 1000, 812_000, 1_400_000_000)] == [
        "0 B", "999 B", "1.0 KB", "812 KB", "1.4 GB"]


# ── the dialog, driven through its own button ───────────────────────────────

@pytest.mark.qt
def test_the_dialog_measures_and_its_button_clears(populated, home, qtbot):
    from ui.local_storage_view import StorageDialog

    dialog = StorageDialog(None, dataset_dir=home / "plate")
    qtbot.addWidget(dialog)
    clear_btn = dialog._clear_btn
    qtbot.waitUntil(lambda: clear_btn.isEnabled(), timeout=10_000)
    assert dialog._status.text() == "", "the status line still says it is measuring"
    # Ticked by default: everything but the row cache.
    assert dialog.selected_keys() == ["fonts", "temp", "logs", "legacy"]
    assert clear_btn.text() == "Clear 10.5 KB"

    clear_btn.click()
    qtbot.waitUntil(lambda: dialog.last_result is not None, timeout=10_000)
    # The clear is followed by a re-measure; the sentence must survive it.
    qtbot.waitUntil(lambda: clear_btn.text() == "Clear selected", timeout=10_000)
    assert dialog.last_result.freed == 1000 + 7000 + 500 + 2000 + 2 * OWNER_BYTES
    assert "Freed 10.5 KB." in dialog._status.text()
    assert populated["rows"].exists(), "the unticked row cache was cleared"
    assert populated["scratch"].exists() and populated["boot"].exists()


# ── findings of the review ──────────────────────────────────────────────────

def test_a_link_inside_a_cleared_folder_is_removed_not_followed(populated, home):
    """A link in a dead spill, and a row cache that is itself a link, must cost
    nothing outside the folders cleared — not even on the way to measuring."""
    precious = _write(home / "precious" / "data.csv", 123)
    (populated["dead_spill"].parent / "to-precious").symlink_to(precious.parent)
    (populated["dead_spill"].parent / "a-file-link").symlink_to(precious)
    other = home / "other-plate"
    _write(other / ".aw_parquet" / "Z.parquet", 50)
    (home / "linked-plate").mkdir()
    (home / "linked-plate" / ".aw_parquet").symlink_to(other / ".aw_parquet")

    areas = ls.cache_areas(home / "linked-plate")
    assert _offered(areas)["rows"] == set(), "a linked row cache was offered"
    sizes = ls.measure(areas)
    assert sizes["temp"] == 7000 + OWNER_BYTES, "a link's target was measured"
    ls.clear(areas)
    assert precious.read_bytes() == b"\0" * 123
    assert (other / ".aw_parquet" / "Z.parquet").exists()
    assert not populated["dead_spill"].parent.exists()


def test_a_windows_junction_counts_as_a_link(monkeypatch, tmp_path):
    """``os.path.islink`` is False for a directory junction, and the Python the
    app ships with walks into one. The reparse-point attribute is what says so."""
    real = os.lstat

    class _Junction:
        st_mode = 0o040755                 # a directory, not a symlink
        st_file_attributes = 0x400 | 0x10  # REPARSE_POINT | DIRECTORY

    junction = tmp_path / "junction"
    junction.mkdir()
    monkeypatch.setattr(ls.os, "lstat",
                        lambda p, *a, **k: _Junction() if Path(p) == junction else real(p, *a, **k))
    assert ls.is_link(junction)
    assert not ls.is_link(tmp_path)


def test_an_owner_on_another_host_is_never_judged_from_here(home, tmp_path):
    """A Linux home folder shared over NFS: process 1 is absent on this machine
    but says nothing about the machine that wrote it."""
    from core import process

    spill = home / ".allwell" / "tmp" / "allwell-cellpool-remote"
    spill.mkdir(parents=True)
    (spill / "owner.pid").write_text(f"{_dead_pid()}@another-host.example\n")
    _age(spill, 10 * ls.UNOWNED_GRACE_S)
    assert process.owner_alive(spill) is True
    assert spill not in _offered(ls.cache_areas(None))["temp"]
    (spill / "owner.pid").write_text(process.owner_text(_dead_pid()))
    assert process.owner_alive(spill) is False


def test_clear_lists_again_and_spares_what_came_into_use(populated, home):
    """The dialog stands open; a pool opens meanwhile. Clear must not act on the
    list it showed."""
    from core import process

    listed = ls.cache_areas(home / "plate")
    assert populated["dead_spill"].parent in _offered(listed)["temp"]
    (populated["dead_spill"].parent / "owner.pid").write_text(process.owner_text())
    ls.clear_now(["temp"], home / "plate")
    assert populated["dead_spill"].exists(), "a folder that came into use was cleared"
    assert not populated["old_unowned"].exists()


def test_the_in_out_layout_s_row_cache_is_offered(home):
    rows = _write(home / "plate" / "out" / ".aw_parquet" / "A01.parquet", 10)
    assert _offered(ls.cache_areas(home / "plate"))["rows"] == {rows.parent}


def test_sizes_never_read_1000_of_a_unit():
    assert [ls.format_bytes(n) for n in (99_950, 999_950, 999_999, 999_999_999)] == [
        "100 KB", "1.0 MB", "1.0 MB", "1.0 GB"]


@pytest.mark.qt
def test_an_empty_leftover_can_still_be_cleared(home, qtbot):
    """Zero bytes is still something to clear: an empty folder a crash left."""
    from ui.local_storage_view import StorageDialog

    leftover = home / ".allwell" / "tmp" / "allwell_batch_nodisk_empty"
    leftover.mkdir(parents=True)
    _age(leftover, 2 * ls.UNOWNED_GRACE_S)
    dialog = StorageDialog(None, dataset_dir=None)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: bool(dialog._boxes) and not dialog._working, timeout=10_000)
    for key, box in dialog._boxes.items():
        box.setChecked(key == "temp")
    assert dialog._clear_btn.isEnabled() and dialog._clear_btn.text() == "Clear selected"
    dialog._clear_btn.click()
    qtbot.waitUntil(lambda: dialog.last_result is not None, timeout=10_000)
    assert not leftover.exists()


@pytest.mark.qt
def test_a_dataset_opened_meanwhile_is_not_cleared_under_the_old_name(home, qtbot):
    from ui.local_storage_view import StorageDialog

    first = _write(home / "first" / ".aw_parquet" / "A.parquet", 10)
    second = _write(home / "second" / ".aw_parquet" / "B.parquet", 10)
    open_now = [home / "first"]
    dialog = StorageDialog(None, dataset_dir=lambda: open_now[0])
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: bool(dialog._boxes) and not dialog._working, timeout=10_000)
    for key, box in dialog._boxes.items():
        box.setChecked(key == "rows")
    open_now[0] = home / "second"
    dialog._clear_btn.click()
    qtbot.waitUntil(lambda: not dialog._working and "Another dataset" in dialog._status.text(),
                    timeout=10_000)
    assert first.exists() and second.exists(), "a clear ran against a list for another dataset"
    assert "(second)" in dialog._boxes["rows"].text()


def test_measuring_never_enters_a_junction(populated, monkeypatch):
    """The walk prunes reparse points itself: ``os.walk`` on the Python the app
    ships with descends into a Windows junction, so a junction to a large folder
    would have been counted — and, before ``rmtree``, cleared."""
    inside = populated["dead_spill"].parent / "junction"
    _write(inside / "huge.bin", 50_000)
    real = os.lstat

    class _Junction:
        st_mode = 0o040755
        st_file_attributes = 0x400 | 0x10

    monkeypatch.setattr(ls.os, "lstat",
                        lambda p, *a, **k: _Junction() if Path(p) == inside else real(p, *a, **k))
    assert ls.size_of(populated["dead_spill"].parent) == 5000 + OWNER_BYTES


def test_the_newest_bootstrap_log_is_kept_however_old(home):
    """A remote setup can outlast any grace period; its log is the newest."""
    app = home / ".allwell"
    older = _write(app / "bootstrap-20250101T000000.log", 10)
    newest = _write(app / "bootstrap-20250102T000000.log", 10)
    _age(older, 5 * ls.LIVE_LOG_GRACE_S)
    _age(newest, 4 * ls.LIVE_LOG_GRACE_S)
    assert _offered(ls.cache_areas(None))["logs"] == {older}
