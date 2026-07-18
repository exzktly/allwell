"""Guard: every icon URL baked into ``theme.qss()`` must exist on disk.

The PyInstaller spec (``_Docs/_Installation/all_well.spec``) bundles
``assets/icons/`` whole, and ``theme._icon_path`` resolves each glyph under
``sys._MEIPASS`` in the frozen app. A ``url(...)`` in the stylesheet that points
at a missing SVG renders a **blank** control (combo chevron, spin arrows,
checkbox tick) with no error — invisible until someone eyeballs the built app.

This test fails fast at CI time instead: it parses the generated stylesheet,
pulls every ``url(...)`` that ends in ``.svg``, and asserts the file is really
there. It also confirms the icons live under ``assets/icons/`` (the directory
the spec bundles), so a new glyph dropped somewhere the build doesn't ship is
caught too. ``theme`` is Qt-free at import, so no ``QApplication`` is needed.
"""

from __future__ import annotations

import re
from pathlib import Path

import theme

# ``image: url(/abs/path/to/glyph.svg);`` — capture the path inside url(...),
# tolerating optional surrounding quotes.
_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")

_ICONS_DIR = (Path(theme.__file__).resolve().parent / "assets" / "icons")


def _svg_urls() -> list[str]:
    return [u for u in _URL_RE.findall(theme.qss()) if u.endswith(".svg")]


def test_theme_qss_icon_urls_exist() -> None:
    urls = _svg_urls()
    # Sanity floor: the combo/menu chevron is always referenced, so an empty
    # match set means the regex (or qss()) broke, not that there's nothing to
    # check.
    assert urls, "theme.qss() emitted no .svg url() references — regex/qss drift?"
    missing = sorted(u for u in urls if not Path(u).is_file())
    assert not missing, (
        "theme.qss() references icon file(s) that don't exist on disk — a "
        "frozen build would render blank controls:\n  " + "\n  ".join(missing)
    )


def test_theme_icons_live_under_bundled_dir() -> None:
    """Every referenced icon sits in ``assets/icons/`` — the tree the spec ships.

    A glyph resolved from anywhere else would be missing from the bundle even
    though the file exists in a dev checkout.
    """
    stray = sorted(
        u for u in _svg_urls()
        if Path(u).resolve().parent != _ICONS_DIR
    )
    assert not stray, (
        "theme.qss() references icon(s) outside assets/icons/ (not bundled by "
        "all_well.spec):\n  " + "\n  ".join(stray)
    )
