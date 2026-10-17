"""The startup/crash log and the remote-bootstrap logs land in one folder.

Reported: "only bootstrap-XXX.log files get written there" — the user was sent to
``~/.cache/allwell`` for the crash log and found only bootstrap logs. The two
writers chose their folder by different rules (the boot log preferred
``%LOCALAPPDATA%\\allwell`` on Windows; the bootstrap hardcoded
``~/.cache/allwell``). Both now ask ``util.state_dir.user_state_dir``, and so
does the CZI scan cache, so they cannot drift apart again.

The bootstrap log also records where the launching app's boot log is and
whether it exists (``ALLWELL_BOOT_LOG``), so a missing crash log can be traced
from a file the user can already find.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import scan_cache
from services import remote_bootstrap
from startup import boot_diag
from util import state_dir


@pytest.fixture
def shared(tmp_path, monkeypatch):
    """Point the one shared folder function somewhere private."""
    target = tmp_path / "state"
    target.mkdir()
    monkeypatch.setattr(state_dir, "user_state_dir", lambda: target)
    monkeypatch.setattr(state_dir, "candidate_dirs", lambda: [target])
    monkeypatch.setattr(boot_diag, "_LOG_PATH", None)
    monkeypatch.delenv("ALLWELL_SCAN_CACHE", raising=False)
    return target


def test_every_local_log_and_cache_uses_the_shared_folder(shared, monkeypatch):
    assert boot_diag.log_path() == shared / "allwell-boot.log"

    path, fh = remote_bootstrap._open_log_file()
    try:
        assert path is not None and path.parent == shared
        assert path.name.startswith("bootstrap-")
    finally:
        if fh is not None:
            fh.close()

    assert scan_cache.cache_dir() == shared / "czi-scans"


def test_the_home_cache_folder_comes_first_even_when_localappdata_is_set(tmp_path, monkeypatch):
    home, local = tmp_path / "home", tmp_path / "local"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    assert state_dir.candidate_dirs()[0] == home / ".cache" / "allwell"
    assert state_dir.candidate_dirs()[1] == local / "allwell"
    assert state_dir.user_state_dir() == home / ".cache" / "allwell"


def test_the_bootstrap_log_says_where_the_app_s_boot_log_is(tmp_path, monkeypatch):
    log = tmp_path / "allwell-boot.log"
    monkeypatch.setenv("ALLWELL_BOOT_LOG", str(log))
    assert remote_bootstrap._boot_log_line() == f"app boot log: {log} (MISSING)"
    log.write_text("x")
    assert remote_bootstrap._boot_log_line() == f"app boot log: {log} (exists)"
    monkeypatch.delenv("ALLWELL_BOOT_LOG")
    assert "not reported" in remote_bootstrap._boot_log_line()


def test_the_app_exports_its_boot_log_path_to_child_processes():
    """``allwell.py`` sets ``ALLWELL_BOOT_LOG`` at import, before any child can
    be started. Checked on the source: importing ``allwell`` here would open the
    real boot log."""
    src = (Path(__file__).resolve().parent.parent / "allwell.py").read_text()
    head = src[: src.index("from PySide6")]
    assert 'environ["ALLWELL_BOOT_LOG"] = str(_boot_diag.log_path())' in head
