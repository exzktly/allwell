"""The matplotlib font bootstrap restricts discovery to the bundled fonts.

matplotlib otherwise enumerates every system font on first cache build (minutes
on a Windows 365 Cloud PC). ``mpl_bootstrap.configure()`` must limit
``findSystemFonts`` to the app's bundled fonts and pin a cache dir.
"""

from __future__ import annotations

import glob
import os

import pytest


def test_configure_restricts_font_discovery_to_bundled():
    pytest.importorskip("matplotlib")
    import mpl_bootstrap

    mpl_bootstrap.configure()

    fonts_dir = os.path.abspath(os.path.join(
        os.path.dirname(mpl_bootstrap.__file__), "assets", "fonts"))
    bundled = {os.path.abspath(f) for f in glob.glob(os.path.join(fonts_dir, "*.ttf"))}
    assert bundled, "expected bundled fonts to exist"

    import matplotlib.font_manager as fm
    discovered = {os.path.abspath(p) for p in fm.findSystemFonts()}
    # Only bundled fonts are discoverable — no system-wide enumeration.
    assert discovered
    assert all(p.startswith(fonts_dir) for p in discovered)
    assert bundled <= discovered


def test_configure_sets_a_cache_dir():
    pytest.importorskip("matplotlib")
    import mpl_bootstrap

    mpl_bootstrap.configure()
    assert os.environ.get("MPLCONFIGDIR")


def test_configure_is_defensive_without_bundled_fonts(monkeypatch):
    """No bundled fonts → leave matplotlib untouched rather than break."""
    import mpl_bootstrap
    monkeypatch.setattr(mpl_bootstrap, "_bundled_fonts", lambda: [])
    mpl_bootstrap.configure()  # must not raise
