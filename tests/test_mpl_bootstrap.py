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
import os

import pytest

from tests._repo_scan import REPO_ROOT

#: Where the bundled fonts actually live, relative to the repository root.
_FONTS_DIR = os.path.abspath(os.path.join(str(REPO_ROOT), "assets", "fonts"))


@pytest.fixture(autouse=True)
def _restore_font_manager():
    """configure() rebuilds matplotlib's global font manager; snapshot and
    restore it so this module can't pollute other tests' font lookups."""
    fm = pytest.importorskip("matplotlib.font_manager")
    saved = (fm.findSystemFonts, fm.fontManager)
    try:
        yield
    finally:
        fm.findSystemFonts, fm.fontManager = saved


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
