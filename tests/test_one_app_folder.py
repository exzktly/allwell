"""Everything All-Well keeps on a machine is in one folder, on every platform.

Reported: the app's caches, the run scratch, the remotes / job queue / run
index, the CZI scan cache and the Qt settings were spread over four or five
places, and which places depended on the platform. They are now all in
``util.state_dir.gui_state_dir`` — ``%LOCALAPPDATA%\\allwell`` on Windows,
``~/.allwell`` on macOS and Linux.

The user's own records must survive that: each is moved over from where an
earlier version kept it the first time it is asked for
(``util.state_dir.adopt_legacy_file``), and the Qt settings are copied out of
the native store once (``core.app_settings``). Those moves are what this file
pins — including that they never happen while ``ALLWELL_STATE_DIR`` is set,
which is the variable that keeps a test run out of the real folders.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services import run_journal as _run_journal
from util import state_dir

#: Taken at import, before ``conftest`` redirects ``run_journal.index_path``
#: to a per-test folder for every test: this file measures the real one.
_REAL_INDEX_PATH = _run_journal.index_path


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def posix(tmp_path, monkeypatch):
    """A macOS/Linux layout with a private home folder and no overrides."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(state_dir.STATE_DIR_ENV, raising=False)
    monkeypatch.delenv("ALLWELL_REMOTES", raising=False)
    monkeypatch.setattr(state_dir, "is_windows", lambda: False)
    return home


@pytest.fixture
def windows(tmp_path, monkeypatch):
    """A Windows layout: ``%LOCALAPPDATA%``, ``%APPDATA%`` and a home folder."""
    home = tmp_path / "home"
    home.mkdir()
    local = tmp_path / "AppData" / "Local"
    roaming = tmp_path / "AppData" / "Roaming"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.delenv(state_dir.STATE_DIR_ENV, raising=False)
    monkeypatch.delenv("ALLWELL_REMOTES", raising=False)
    monkeypatch.setattr(state_dir, "is_windows", lambda: True)
    return home, local / "allwell", roaming / "allwell"


# ── where each record lives ─────────────────────────────────────────────────

def _paths():
    from services import job_queue, suspend_record
    from services.remote_config import default_config_path
    return {
        "remotes": default_config_path(),
        "queue": job_queue.queue_path(),
        "index": _REAL_INDEX_PATH(),
        "suspended": suspend_record.record_path(),
    }


def test_on_macos_and_linux_every_record_is_in_dot_allwell(posix):
    app = posix / ".allwell"
    assert _paths() == {
        "remotes": app / "remotes.toml",
        "queue": app / "job_queue.json",
        "index": app / "run_index.json",
        "suspended": app / "suspended_runs.json",
    }


def test_on_windows_every_record_is_in_localappdata(windows):
    _, app, _ = windows
    assert _paths() == {
        "remotes": app / "remotes.toml",
        "queue": app / "job_queue.json",
        "index": app / "run_index.json",
        "suspended": app / "suspended_runs.json",
    }


# ── the user's records follow the app ───────────────────────────────────────

def test_on_macos_and_linux_the_config_folder_s_records_move_over(posix):
    old = posix / ".config" / "allwell"
    remotes = _write(old / "remotes.toml", '[remote.x]\nssh_alias = "x"\n')
    _write(old / "job_queue.json", '{"jobs": []}')
    _write(old / "run_index.json", "[]")
    paths = _paths()
    app = posix / ".allwell"
    assert (app / "remotes.toml").read_text() == '[remote.x]\nssh_alias = "x"\n'
    assert (app / "job_queue.json").read_text() == '{"jobs": []}'
    assert (app / "run_index.json").read_text() == "[]"
    assert paths["remotes"] == app / "remotes.toml"
    assert not remotes.exists() and not old.exists(), "the emptied old folder goes too"


def test_on_windows_the_records_move_out_of_appdata_and_the_home_folder(windows):
    home, app, roaming = windows
    _write(roaming / "remotes.toml", "appdata")
    _write(home / ".config" / "allwell" / "remotes.toml", "dot-config")
    _write(roaming / "job_queue.json", "queue")
    _write(home / ".allwell" / "suspended_runs.json", "[]")
    _paths()
    # %APPDATA% first: it is the copy earlier versions read when both existed.
    assert (app / "remotes.toml").read_text() == "appdata"
    assert (app / "job_queue.json").read_text() == "queue"
    assert (app / "suspended_runs.json").read_text() == "[]"
    assert not (roaming / "remotes.toml").exists()
    assert not (home / ".allwell" / "suspended_runs.json").exists()


def test_a_record_already_in_the_app_folder_is_never_overwritten(posix):
    _write(posix / ".allwell" / "remotes.toml", "new")
    _write(posix / ".config" / "allwell" / "remotes.toml", "old")
    assert _paths()["remotes"].read_text() == "new"
    assert (posix / ".config" / "allwell" / "remotes.toml").read_text() == "old"


def test_nothing_moves_while_the_state_dir_is_overridden(posix, tmp_path, monkeypatch):
    """``ALLWELL_STATE_DIR`` means "not the real folders": a move then would
    take the user's real records out of their real home (the test harness sets
    it for every test)."""
    old = _write(posix / ".config" / "allwell" / "remotes.toml", "real")
    monkeypatch.setenv(state_dir.STATE_DIR_ENV, str(tmp_path / "elsewhere"))
    assert _paths()["remotes"] == tmp_path / "elsewhere" / "remotes.toml"
    assert old.read_text() == "real"
    assert not (tmp_path / "elsewhere" / "remotes.toml").exists()


def test_a_move_that_cannot_copy_keeps_reading_the_original(posix, monkeypatch):
    old = _write(posix / ".config" / "allwell" / "remotes.toml", "real")
    import shutil

    def refuse(*_a, **_k):
        raise PermissionError("denied")

    monkeypatch.setattr(shutil, "copy2", refuse)
    assert _paths()["remotes"] == old
    assert old.read_text() == "real"
    assert not (posix / ".allwell" / "remotes.toml").exists()


def test_the_bootstrap_writes_the_remotes_file_the_app_reads(windows):
    """``remote_bootstrap`` resolved its own path, by a rule that could pick a
    different file from the app's on Windows."""
    from services import remote_bootstrap
    from services.remote_config import default_config_path

    _, app, _ = windows
    assert remote_bootstrap.default_config_path() == default_config_path() == app / "remotes.toml"


# ── the Qt settings ─────────────────────────────────────────────────────────

@pytest.fixture
def real_app_settings(monkeypatch):
    """``core.app_settings.app_settings`` as shipped, not the harness's stub."""
    pytest.importorskip("PySide6")
    import importlib

    import core.app_settings as mod
    fresh = importlib.reload(mod)
    yield fresh
    importlib.reload(mod)


def _native(tmp_path, monkeypatch, mod, values: dict):
    """An INI file standing in for the registry or a plist."""
    from PySide6.QtCore import QSettings

    path = tmp_path / "native.ini"
    store = QSettings(str(path), QSettings.IniFormat)
    for k, v in values.items():
        store.setValue(k, v)
    store.sync()
    monkeypatch.setattr(mod, "_native_settings",
                        lambda: QSettings(str(path), QSettings.IniFormat))
    return path


def test_settings_are_an_ini_file_in_the_app_folder(posix, real_app_settings, tmp_path, monkeypatch):
    mod = real_app_settings
    _native(tmp_path, monkeypatch, mod, {})
    s = mod.app_settings()
    assert Path(s.fileName()) == posix / ".allwell" / "settings.ini"


def test_settings_from_the_native_store_are_carried_over_once(posix, real_app_settings,
                                                              tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    mod = real_app_settings
    native = _native(tmp_path, monkeypatch, mod,
                     {"recent/datasets": ["/a", "/b"], "phone_text_size": "L"})
    s = mod.app_settings()
    assert s.value("recent/datasets") == ["/a", "/b"]
    assert s.value("phone_text_size") == "L"
    assert QSettings(str(native), QSettings.IniFormat).allKeys() == [], \
        "the native store is cleared once the copy is safely written"


def test_existing_settings_are_not_replaced_by_the_native_store(posix, real_app_settings,
                                                                tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    mod = real_app_settings
    ini = posix / ".allwell" / "settings.ini"
    ini.parent.mkdir(parents=True)
    current = QSettings(str(ini), QSettings.IniFormat)
    current.setValue("phone_text_size", "S")
    current.sync()
    native = _native(tmp_path, monkeypatch, mod, {"phone_text_size": "L"})
    assert mod.app_settings().value("phone_text_size") == "S"
    assert QSettings(str(native), QSettings.IniFormat).value("phone_text_size") == "L"


def test_settings_are_not_carried_over_while_the_state_dir_is_overridden(
        posix, real_app_settings, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    mod = real_app_settings
    monkeypatch.setenv(state_dir.STATE_DIR_ENV, str(tmp_path / "elsewhere"))
    native = _native(tmp_path, monkeypatch, mod, {"phone_text_size": "L"})
    assert mod.app_settings().value("phone_text_size") is None
    assert QSettings(str(native), QSettings.IniFormat).value("phone_text_size") == "L"
