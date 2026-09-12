"""Frozen-bundle root resolution for the build-profile marker.

A PyInstaller build bakes ``active_profile.txt`` and ships ``profiles/`` at the
bundle root (``sys._MEIPASS``). ``profile._repo_root()`` must resolve there when
frozen — deriving it from ``__file__``'s ``parents[2]`` misses the marker on
layouts that split code from data (a macOS ``.app``), silently falling back to
the full app over a thin (stripped) bundle and crashing at startup. This guards
the fix that makes ``_repo_root()`` prefer ``sys._MEIPASS`` (like
``startup/app_fonts.py`` / ``all_well_launcher.py``).
"""
from __future__ import annotations

import core.profile as prof
from tests._repo_scan import REPO_ROOT


def test_repo_root_prefers_meipass_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(prof.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert prof._repo_root() == tmp_path
    assert prof.profiles_dir() == tmp_path / "profiles"


def test_repo_root_falls_back_to_source_tree_when_not_frozen(monkeypatch):
    """Unfrozen, the fallback is the repository root — checked against the root itself.

    This assertion used to read
    ``prof._repo_root() == Path(prof.__file__).resolve().parents[2]``, which
    computed the expected value exactly the way the function did. It therefore
    accepted whatever the function answered: any parent count at all passed, so
    long as the module and the test had chosen the same one. The only change it
    could ever detect was one made to one of them and not the other — which is
    how it eventually failed, when lifting this module from
    ``well_viewer/core/`` to ``core/`` made ``parents[2]`` name the directory
    *above* the checkout. It caught the symptom by accident and had never been
    able to catch the defect.

    `tests/test_profile_repo_root.py` holds the same rule from the other side
    and goes on to drive `active_profile()`, which is what the application
    actually calls.
    """
    monkeypatch.delattr(prof.sys, "_MEIPASS", raising=False)
    assert prof._repo_root() == REPO_ROOT, (
        f"unfrozen, _repo_root() answered {prof._repo_root()} rather than the "
        f"repository root {REPO_ROOT}. Nothing raises on that: profiles_dir() "
        "names a directory that is not there and every build falls back "
        "silently to the full application.")


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
