"""Frozen-bundle root resolution for the build-profile marker.

A PyInstaller build bakes ``active_profile.txt`` and ships ``profiles/`` at the
bundle root (``sys._MEIPASS``). ``profile._repo_root()`` must resolve there when
frozen — deriving it from ``__file__``'s ``parents[2]`` misses the marker on
layouts that split code from data (a macOS ``.app``), silently falling back to
the full app over a thin (stripped) bundle and crashing at startup. This guards
the fix that makes ``_repo_root()`` prefer ``sys._MEIPASS`` (like
``core/app_fonts.py`` / ``all_well_launcher.py``).
"""
from __future__ import annotations

from pathlib import Path

import well_viewer.core.profile as prof


def test_repo_root_prefers_meipass_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(prof.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert prof._repo_root() == tmp_path
    assert prof.profiles_dir() == tmp_path / "profiles"


def test_repo_root_falls_back_to_source_tree_when_not_frozen(monkeypatch):
    monkeypatch.delattr(prof.sys, "_MEIPASS", raising=False)
    # In a dev checkout the root is two parents up from the package module.
    assert prof._repo_root() == Path(prof.__file__).resolve().parents[2]


def test_active_profile_reads_baked_marker_via_meipass(monkeypatch, tmp_path):
    """With no env var and a frozen ``_MEIPASS`` root, the baked marker +
    ``profiles/`` there select the profile — the exact frozen-thin path."""
    import shutil

    monkeypatch.delenv(prof._ENV_VAR, raising=False)
    shutil.copytree(prof.profiles_dir(), tmp_path / "profiles")
    monkeypatch.setattr(prof.sys, "_MEIPASS", str(tmp_path), raising=False)
    (tmp_path / prof._BAKED_MARKER).write_text("thin", encoding="utf-8")

    assert prof.active_profile().name == "thin"
    # Remove the marker → a genuinely-full frozen build resolves to the full app.
    (tmp_path / prof._BAKED_MARKER).unlink()
    assert prof.active_profile() is prof.FULL
