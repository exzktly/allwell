"""The page-render harness paints the window in the theme a user starts in.

``ui/theme.py``'s token classes hold the *blue* baseline until a theme is applied, and only
``allwell.main`` applies one. ``tests/_page_render_case.py`` builds the window without ``main``, so for as
long as it did not apply the theme itself, ``tests/test_every_page_renders.py`` and every screenshot it saved
showed a palette no user sees — and the first design audit was drawn from those screenshots and had to be
redone (``_Docs/Markdowns/DESIGN_LANGUAGE_TRACKER.md``, row D01).

This builds the window through the harness's own entry point (``start_window``) and reads the colour the
window's background is actually painted, from a grab — not ``Colors.surface``, which is the token the bug
left untouched, and not the stylesheet text. Full-app test (one window per case), so ``slow``.
"""

from __future__ import annotations

from collections import Counter

import pytest

pytestmark = pytest.mark.slow


def _dominant_colour(win) -> str:
    """The colour covering most of the window's grab: the window background the sidebar and pages sit on."""
    img = win.grab().toImage()
    counts: Counter[str] = Counter()
    step = 7
    for y in range(0, img.height(), step):
        for x in range(0, img.width(), step):
            counts[img.pixelColor(x, y).name().upper()] += 1
    return counts.most_common(1)[0][0]


@pytest.fixture
def harness_window(qapp, monkeypatch):
    from tests._page_render_case import start_window
    from tests._screens import pump
    from ui import theme

    built = []

    def build(env_theme: str | None):
        if env_theme is None:
            monkeypatch.delenv("ALLWELL_THEME", raising=False)
        else:
            monkeypatch.setenv("ALLWELL_THEME", env_theme)
        win = start_window(qapp)
        win.resize(1280, 800)
        win.show()
        pump(qapp, 20, 200)
        built.append(win)
        return win

    try:
        yield build
    finally:
        for win in built:
            win.close()
        theme.apply_theme("blue")
        qapp.setStyleSheet(theme.qss())


def test_the_harness_window_is_painted_in_the_default_theme(harness_window):
    from ui import theme

    grey_surface = theme.THEMES[theme.DEFAULT_THEME]["Colors"]["surface"].upper()
    blue_surface = theme._THEME_DEFAULTS["Colors"]["surface"].upper()
    assert grey_surface != blue_surface, "the fixture cannot tell the two palettes apart"
    win = harness_window(None)
    painted = _dominant_colour(win)
    assert painted == grey_surface, (
        f"the harness window is painted {painted}; the default theme ({theme.DEFAULT_THEME}) paints "
        f"{grey_surface} and the unthemed blue baseline {blue_surface}")


def test_the_harness_honours_allwell_theme_so_both_palettes_can_be_rendered(harness_window):
    from ui import theme

    blue_surface = theme._THEME_DEFAULTS["Colors"]["surface"].upper()
    win = harness_window("blue")
    assert _dominant_colour(win) == blue_surface
