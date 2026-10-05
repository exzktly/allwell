"""The matplotlib font bootstrap restricts discovery to the bundled fonts.

matplotlib otherwise enumerates every system font on first cache build (minutes
on a Windows 365 Cloud PC). ``startup.mpl_bootstrap.configure()`` must limit
``findSystemFonts`` to the app's bundled fonts and pin a cache dir.

The expected font directory is derived from ``tests._repo_scan.REPO_ROOT``, not
from ``mpl_bootstrap.__file__``: ``assets/fonts`` is repository-level while the
module lives in ``startup/``, and a test that re-derives the path the same way
the module does is satisfied by any answer the module gives — including the
wrong one. Anchoring it independently is what caught the module still resolving
the directory beside its own source after it moved into the package.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

import pytest

from tests._repo_scan import REPO_ROOT

#: Where the bundled fonts actually live, relative to the repository root.
_FONTS_DIR = os.path.abspath(os.path.join(str(REPO_ROOT), "assets", "fonts"))


@pytest.fixture(autouse=True)
def _restore_font_manager():
    """configure() rebuilds matplotlib's global font manager; snapshot and
    restore it so this module can't pollute other tests' font lookups."""
    fm = pytest.importorskip("matplotlib.font_manager")
    saved = (fm.findSystemFonts, fm.fontManager, fm.findfont, fm.get_font_names)
    try:
        yield
    finally:
        fm.findSystemFonts, fm.fontManager, fm.findfont, fm.get_font_names = saved


def test_configure_restricts_font_discovery_to_bundled():
    pytest.importorskip("matplotlib")
    from startup import mpl_bootstrap

    mpl_bootstrap.configure()

    fonts_dir = _FONTS_DIR
    bundled = {os.path.abspath(f) for f in glob.glob(os.path.join(fonts_dir, "*.ttf"))}
    assert bundled, f"expected bundled fonts to exist under {fonts_dir}"

    # The module has to be looking at that same directory. Asserted separately
    # from the discovery check below so a module resolving `assets/fonts` beside
    # its own source fails here, naming the path it chose, rather than failing
    # further down as "no fonts discovered".
    assert mpl_bootstrap._FONTS_DIR == fonts_dir, (
        f"mpl_bootstrap resolves its font directory to "
        f"{mpl_bootstrap._FONTS_DIR!r}, not {fonts_dir!r} — `assets/fonts` is "
        f"repository-level and the module is one level down in `startup/`, so "
        f"the path must be derived from the repository root")

    import matplotlib.font_manager as fm
    discovered = {os.path.abspath(p) for p in fm.findSystemFonts()}
    # Only bundled fonts are discoverable — no system-wide enumeration.
    assert discovered
    assert all(p.startswith(fonts_dir) for p in discovered)
    assert bundled <= discovered


def test_configure_sets_a_cache_dir():
    pytest.importorskip("matplotlib")
    from startup import mpl_bootstrap

    mpl_bootstrap.configure()
    assert os.environ.get("MPLCONFIGDIR")


def test_configure_is_defensive_without_bundled_fonts(monkeypatch):
    """No bundled fonts → leave matplotlib untouched rather than break."""
    from startup import mpl_bootstrap
    monkeypatch.setattr(mpl_bootstrap, "_bundled_fonts", lambda: [])
    mpl_bootstrap.configure()  # must not raise


# ── a cold cache never scans the system's fonts ─────────────────────────────
#
# ``matplotlib.font_manager`` builds its global manager as the last statement of
# its own import, so a ``findSystemFonts`` patched after the import is too late
# whenever there is no cached font list: the first launch, a fresh per-user
# state directory, every matplotlib upgrade. That was the launch-time stall.
# Only a process that has not imported the module yet can show it, so this runs
# one, with an empty cache directory, and records every call matplotlib's own
# ``findSystemFonts`` receives — ``None`` for ``fontpaths`` is the system scan.

_COLD_LAUNCH = r"""
import json, os, sys
calls = []
def _profile(frame, event, arg):
    if (event == "call" and frame.f_code.co_name == "findSystemFonts"
            and frame.f_code.co_filename.endswith("font_manager.py")):
        calls.append(frame.f_locals.get("fontpaths") is None)
sys.setprofile(_profile)
from startup import mpl_bootstrap
mpl_bootstrap.configure()
sys.setprofile(None)
import matplotlib, matplotlib.font_manager as fm
print(json.dumps({
    "system_scans": sum(calls),
    "fonts": [e.fname for e in fm.fontManager.ttflist],
    "findfont_is_live": fm.findfont.__self__ is fm.fontManager,
    "names_is_live": fm.get_font_names.__self__ is fm.fontManager,
    "data_fonts": os.path.join(matplotlib.get_data_path(), "fonts"),
    "cache": os.path.join(matplotlib.get_cachedir(),
                          f"fontlist-v{fm.FontManager.__version__}.json"),
}))
"""


def _launch_cold(cache_dir) -> dict:
    env = dict(os.environ, MPLCONFIGDIR=str(cache_dir))
    out = subprocess.run([sys.executable, "-c", _COLD_LAUNCH], cwd=str(REPO_ROOT),
                         env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_cold_cache_launch_never_scans_the_system_fonts(tmp_path):
    pytest.importorskip("matplotlib")
    first = _launch_cold(tmp_path)
    assert first["system_scans"] == 0, (
        f"configure() let matplotlib enumerate the system's fonts "
        f"{first['system_scans']} time(s) on a cold cache — the scan ran inside "
        f"`import matplotlib.font_manager`, before discovery was restricted")

    # What it built instead is the restricted list, and it is the one on disk:
    # the placeholder that stood in for the import must not survive it.
    allowed = (_FONTS_DIR, first["data_fonts"])
    assert first["fonts"]
    assert all(os.path.abspath(f).startswith(allowed) for f in first["fonts"])
    with open(first["cache"], encoding="utf-8") as fh:
        assert len(json.load(fh)["ttflist"]) == len(first["fonts"])

    # And the next launch reads that list rather than building another.
    second = _launch_cold(tmp_path)
    assert second["system_scans"] == 0
    assert sorted(second["fonts"]) == sorted(first["fonts"])


def test_font_lookups_answer_from_the_restricted_manager(tmp_path):
    """``findfont`` was bound by the import to the manager it loaded; replacing
    ``fontManager`` without rebinding it left lookups on the placeholder."""
    pytest.importorskip("matplotlib")
    launched = _launch_cold(tmp_path)
    assert launched["findfont_is_live"]
    assert launched["names_is_live"]


def test_the_font_list_version_is_read_without_importing_it():
    fm = pytest.importorskip("matplotlib.font_manager")
    from startup import mpl_bootstrap
    assert mpl_bootstrap._font_list_version() == fm.FontManager.__version__
