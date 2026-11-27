"""The palette is the user's choice, made in the application and remembered (``ui/palette_setting.py``).

Precedence at start-up: ``--theme``, then ``ALLWELL_THEME``, then the stored choice, then the default. Each
case starts from a private settings file (the autouse ``ALLWELL_STATE_DIR``) and puts the baseline palette back.
``tests/test_the_stored_palette_is_the_one_the_window_starts_in.py`` drives the same function to a painted
window.
"""

from __future__ import annotations

import pytest

from ui import palette_setting, theme


def _forget() -> None:
    from core.app_settings import app_settings

    settings = app_settings()
    settings.remove(palette_setting.KEY)
    settings.sync()


@pytest.fixture(autouse=True)
def _baseline(monkeypatch):
    """No stored choice and no environment variable; the baseline palette put back afterwards."""
    # Set, then removed: monkeypatch records the original on the first change, so a variable the launcher
    # sets during a test is taken away again afterwards rather than leaking into the worker's next test.
    for var in ("ALLWELL_THEME", "ALLWELL_BUILD_THEME"):
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)
    _forget()
    yield
    _forget()
    theme.apply_theme("blue")


def test_nothing_stored_starts_in_the_default():
    assert palette_setting.stored_palette() is None
    assert palette_setting.apply_startup_palette() == theme.DEFAULT_THEME == "grey"


def test_a_stored_choice_is_the_palette_the_next_start_uses():
    palette_setting.store_palette("blue")
    assert palette_setting.stored_palette() == "blue"
    assert palette_setting.apply_startup_palette() == "blue"
    assert theme.Colors.canvas == theme._THEME_DEFAULTS["Colors"]["canvas"]


def test_a_builds_baked_palette_is_used_only_when_the_user_has_chosen_none(monkeypatch):
    monkeypatch.setenv("ALLWELL_BUILD_THEME", "blue")
    assert palette_setting.apply_startup_palette() == "blue"
    palette_setting.store_palette("grey")
    assert palette_setting.apply_startup_palette() == "grey"


def test_the_launcher_bakes_the_build_palette_below_the_users_choice(monkeypatch, tmp_path):
    import all_well_launcher

    (tmp_path / "allwell_theme.txt").write_text("blue\n", encoding="utf-8")
    monkeypatch.setattr(all_well_launcher, "_BUNDLE_DIR", tmp_path)
    all_well_launcher._apply_baked_theme()
    import os
    assert os.environ.get("ALLWELL_THEME") is None
    assert os.environ.get("ALLWELL_BUILD_THEME") == "blue"
    palette_setting.store_palette("grey")
    assert palette_setting.apply_startup_palette() == "grey"


def test_the_environment_variable_wins_over_the_stored_choice(monkeypatch):
    palette_setting.store_palette("blue")
    monkeypatch.setenv("ALLWELL_THEME", "grey")
    assert palette_setting.apply_startup_palette() == "grey"
    assert theme.Colors.canvas == theme.THEMES["grey"]["Colors"]["canvas"]


def test_the_command_line_wins_over_both(monkeypatch):
    palette_setting.store_palette("grey")
    monkeypatch.setenv("ALLWELL_THEME", "grey")
    assert palette_setting.apply_startup_palette("blue") == "blue"


def test_a_stored_name_that_is_no_palette_is_ignored():
    from core.app_settings import app_settings

    settings = app_settings()
    settings.setValue(palette_setting.KEY, "purple")
    settings.sync()
    assert palette_setting.stored_palette() is None
    assert palette_setting.apply_startup_palette() == theme.DEFAULT_THEME


def test_storing_a_name_that_is_no_palette_is_refused():
    with pytest.raises(ValueError, match="not a palette"):
        palette_setting.store_palette("purple")


def test_the_menu_offers_grey_first_marks_the_next_starts_palette_and_says_when_it_applies(qapp):
    from PySide6.QtWidgets import QMenu

    from widgets.menu_marks import is_current
    menu = QMenu()
    entries = palette_setting.add_palette_entries(menu)
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts == ["Palette: Grey", "Palette: Blue", "Applies the next time All-Well starts"]
    assert not any(a.isCheckable() for a in entries)          # commands, the current one marked
    assert [a.text() for a in entries if is_current(a)] == ["Palette: Grey"]
    assert not menu.actions()[-1].isEnabled()


def test_choosing_blue_in_the_menu_stores_blue_and_marks_it(qapp):
    from PySide6.QtWidgets import QMenu

    from widgets.menu_marks import is_current
    menu = QMenu()
    blue = next(a for a in palette_setting.add_palette_entries(menu) if a.text() == "Palette: Blue")
    blue.trigger()
    assert palette_setting.stored_palette() == "blue"
    other = QMenu()
    again = palette_setting.add_palette_entries(other)
    assert [a.text() for a in again if is_current(a)] == ["Palette: Blue"]


def test_the_rail_heads_menu_offers_the_palette_and_choosing_there_stores_it(qapp):
    """The control is reached where a person reaches it: the dataset button's menu, filled by the shell."""
    from types import SimpleNamespace

    from PySide6.QtWidgets import QMenu, QWidget

    from review.core.app_shell_view import _fill_dataset_menu
    from widgets.menu_marks import is_current

    host = QWidget()
    menu = QMenu(host)
    _fill_dataset_menu(SimpleNamespace(_browse=lambda: None), menu)
    next(a for a in menu.actions() if a.text() == "Palette: Blue").trigger()
    assert palette_setting.stored_palette() == "blue"
    # Opening the menu again rebuilds it, with the new choice marked and one entry per palette, not two.
    _fill_dataset_menu(SimpleNamespace(_browse=lambda: None), menu)
    entries = [a for a in menu.actions() if a.text().startswith("Palette: ")]
    assert [a.text() for a in entries] == ["Palette: Grey", "Palette: Blue"]
    assert [a.text() for a in entries if is_current(a)] == ["Palette: Blue"]
