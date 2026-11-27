"""The palette a user chose is the one the window is painted in at the next start.

Through the start-up path ``allwell.main`` takes (``ui.palette_setting.apply_startup_palette``, which the
render harness's ``start_window`` also calls) to a built window, read from a grab of what it paints — not from
the tokens. Full-app test (one window per case), so ``slow``.
"""

from __future__ import annotations

import pytest

from tests.test_the_render_harness_shows_the_shipped_theme import _dominant_colour

pytestmark = pytest.mark.slow


@pytest.fixture
def started(qapp, monkeypatch):
    from tests._page_render_case import start_window
    from tests._screens import pump
    from ui import theme

    built = []

    def start(stored: str | None, env: str | None):
        from ui import palette_setting

        if stored:
            palette_setting.store_palette(stored)
        if env:
            monkeypatch.setenv("ALLWELL_THEME", env)
        else:
            monkeypatch.delenv("ALLWELL_THEME", raising=False)
        win = start_window(qapp)
        win.resize(1280, 800)
        win.show()
        pump(qapp, 20, 200)
        built.append(win)
        return win

    try:
        yield start
    finally:
        for win in built:
            win.close()
        theme.apply_theme("blue")
        qapp.setStyleSheet(theme.qss())


def test_a_stored_blue_starts_the_window_in_blue(started):
    from ui import theme

    blue = theme._THEME_DEFAULTS["Colors"]["canvas"].upper()
    grey = theme.THEMES["grey"]["Colors"]["canvas"].upper()
    painted = _dominant_colour(started("blue", None))
    assert painted == blue, f"stored blue, started painted {painted} (blue {blue}, grey {grey})"


def test_allwell_theme_wins_over_the_stored_choice(started):
    from ui import theme

    grey = theme.THEMES["grey"]["Colors"]["canvas"].upper()
    painted = _dominant_colour(started("blue", "grey"))
    assert painted == grey, f"ALLWELL_THEME=grey over a stored blue painted {painted}, not grey {grey}"
