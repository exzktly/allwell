"""The palette this user chose, remembered between sessions, and the one the application starts in.

Two palettes are offered, grey and blue (``ui/palettes.py``); grey is the default. The choice is the user's,
made in the application (the rail head's menu, :func:`add_palette_entries`), and stored in the application's settings
file (``core.app_settings``), reached through that module's ``app_settings`` at each call — the one door
onto the store, which the test suite redirects per test.

**Precedence at start-up**, in :func:`apply_startup_palette`: ``--theme`` on the command line, then the
``ALLWELL_THEME`` environment variable, then the stored choice, then the palette a build baked in
(``ALLWELL_BUILD_THEME``, set by ``all_well_launcher._apply_baked_theme``), then ``ui.theme.DEFAULT_THEME``.
The command line and the environment variable are explicit requests for one launch (a developer, a test) and
win over a remembered preference; a build's default does not, or the user's choice would never hold in that
build. A stored or baked name that is no longer a palette is ignored.

**The choice applies at the next start.** Many widgets build their style sheets from the colour tokens when
they are constructed and keep them, so recolouring a running window would leave those widgets in the old
palette. The menu says so beside the choice.
"""

from __future__ import annotations

import os

from core import app_settings as _settings_store
from ui import theme

#: The environment variable a build's baked default palette arrives in.
BUILD_ENV = "ALLWELL_BUILD_THEME"

#: The settings key the choice is stored under.
KEY = "appearance/palette"

#: Shown in the menu, under the choice, because nothing visible happens when it is made.
APPLIES_NEXT_START = "Applies the next time All-Well starts"

#: The name each palette is offered under, in the order offered: the default first.
LABELS = {"grey": "Grey", "blue": "Blue"}


def stored_palette() -> str | None:
    """The palette this user chose, or ``None`` when none is stored or the stored name is no palette."""
    value = _settings_store.app_settings().value(KEY)
    name = str(value) if value is not None else ""
    return name if name in theme.available_themes() else None


def store_palette(name: str) -> None:
    """Remember *name* as this user's palette."""
    if name not in theme.available_themes():
        raise ValueError(f"{name!r} is not a palette; the palettes are {theme.available_themes()}")
    settings = _settings_store.app_settings()
    settings.setValue(KEY, name)
    settings.sync()


def apply_startup_palette(command_line: str | None = None) -> str:
    """Apply the palette to start in, before any widget is built, and return its name: ``--theme`` (passed as
    *command_line*), else ``ALLWELL_THEME``, else the stored choice, else the build's, else ``DEFAULT_THEME``."""
    if command_line:
        return theme.apply_theme(command_line)
    return theme.select_theme_from_env(default=stored_palette() or build_palette() or theme.DEFAULT_THEME)


def build_palette() -> str | None:
    """The palette this build baked in as its default (``ALLWELL_BUILD_THEME``), or ``None``."""
    name = os.environ.get(BUILD_ENV, "").strip()
    return name if name in theme.available_themes() else None


def add_palette_entries(menu) -> list:
    """The palettes as plain commands in *menu* — *Palette: Grey*, *Palette: Blue* — the one the next start will use
    marked (``widgets.menu_marks``), then a line saying when a change takes effect. Plain commands, not a submenu of
    checkable entries: a menu holds commands, and a setting's current value is marked rather than ticked (the
    application's menu conventions). Returns the palette entries.

    The marked entry is the palette the next start will use when nothing overrides it — the stored choice, or the
    default — which is what choosing an entry changes."""
    from widgets.menu_marks import mark_current
    from widgets.owned_action import owned_action

    current = stored_palette() or theme.DEFAULT_THEME
    names = [n for n in LABELS if n in theme.available_themes()]
    names += [n for n in theme.available_themes() if n not in names]
    entries = []
    for name in names:
        action = mark_current(owned_action(menu, ""), f"Palette: {LABELS.get(name, name.capitalize())}",
                              name == current)
        action.setData(name)
        action.triggered.connect(lambda _checked=False, _name=name: store_palette(_name))
        entries.append(action)
    note = owned_action(menu, APPLIES_NEXT_START)
    note.setEnabled(False)
    return entries
