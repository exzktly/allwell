"""The startup/crash log and the remote-bootstrap logs land in one folder.

Reported: "only bootstrap-XXX.log files get written there" — the user was sent to
``~/.cache/allwell`` for the crash log and found only bootstrap logs. The two
writers chose their folder by different rules (the boot log preferred
``%LOCALAPPDATA%\\allwell`` on Windows; the bootstrap hardcoded
``~/.cache/allwell``). Both now ask ``util.state_dir.gui_state_dir`` — the
desktop app's one folder — so they cannot drift apart again. The CZI scan cache
lives there too, as ``czi-scans/``.

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
    """Point the app's folder somewhere private."""
    target = tmp_path / "state"
    target.mkdir()
    monkeypatch.setenv(state_dir.STATE_DIR_ENV, str(target))
    monkeypatch.setattr(boot_diag, "_LOG_PATH", None)
    monkeypatch.delenv("ALLWELL_SCAN_CACHE", raising=False)
    return target


def test_every_app_log_and_the_scan_cache_use_the_app_folder(shared):
    target = shared
    assert boot_diag.log_path() == target / "allwell-boot.log"

    path, fh = remote_bootstrap._open_log_file()
    try:
        assert path is not None and path.parent == target
        assert path.name.startswith("bootstrap-")
    finally:
        if fh is not None:
            fh.close()

    assert scan_cache.cache_dir() == target / "czi-scans"


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
