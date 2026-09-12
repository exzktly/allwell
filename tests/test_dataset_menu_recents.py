"""The rail head's dataset menu, and the recent datasets under it.

The rail head is the control a user reaches for to change plate. Its menu held
one item — ``Open`` — so the way back to the plate opened ten minutes ago was
to remember where it lived and find it in a folder picker, while Home had known
the answer the whole time.

Everything here presses what the menu actually offers rather than asserting the
menu was built: an action that carries the right label and is connected to
nothing looks identical from the outside, and this repository has shipped that
twice. One test goes through ``_dataset_button`` — the constructor the shell
calls — because a helper that fills a menu correctly is worth nothing if the
button never asks it to.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from well_viewer.core.app_shell_view import (  # noqa: E402
    RECENTS_IN_MENU, _dataset_button, _fill_dataset_menu,
)
from well_viewer.core.app_state import AppState  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _Settings:
    """``QSettings``' two methods over a dict, so no test writes to the machine."""

    def __init__(self):
        self._d = {}

    def value(self, key, default=None):
        return self._d.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's spelling
        self._d[key] = value


def _win(monkeypatch, paths, *, data_dir=None, opened=None):
    """A stand-in for the main window, with a patched settings backend.

    Patched at ``core.app_settings.app_settings`` — where the code
    reads it — rather than by hanging a method on the fake window. A fake seam
    the application never uses is how this list once passed a whole suite while
    reading and writing nothing.
    """
    from core import app_settings as app_settings_module
    from well_viewer.core.recent_datasets import record_open

    settings = _Settings()
    for i, path in enumerate(paths):
        record_open(settings, str(path), Path(path).name, float(i))
    monkeypatch.setattr(app_settings_module, "app_settings", lambda: settings)

    state = AppState()
    state.dataset.data_dir = data_dir
    browsed = []
    win = SimpleNamespace(
        _app_state=state,
        _browse=lambda: browsed.append(True),
        _browsed=browsed,
    )
    # Handed back on the window so a test that reads the history afterwards has
    # the stub in hand rather than importing ``app_settings`` by name, which
    # works only if the import happens after the patch above.
    win._settings = settings
    win._ctx = SimpleNamespace(
        window=SimpleNamespace(
            _load_path=(opened.append if opened is not None else (lambda _p: None))),
        state=state,
    )
    return win


def _labels(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def test_the_menu_lists_the_datasets_opened_recently(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMenu

    a, b = tmp_path / "plate_a", tmp_path / "plate_b"
    a.mkdir()
    b.mkdir()
    menu = QMenu()
    _fill_dataset_menu(_win(monkeypatch, [a, b]), menu)

    assert _labels(menu) == ["Open…", "Recent", "plate_b", "plate_a"], (
        "the menu does not offer the history, newest first, under a heading")


def test_choosing_a_recent_entry_opens_that_dataset(qapp, tmp_path, monkeypatch):
    """Asserted at the boundary: the path that reached ``_load_path``.

    Not that an action exists carrying the folder's name — an action with the
    right text and no connection is exactly what this is for.
    """
    from PySide6.QtWidgets import QMenu

    a, b = tmp_path / "plate_a", tmp_path / "plate_b"
    a.mkdir()
    b.mkdir()
    opened = []
    menu = QMenu()
    _fill_dataset_menu(_win(monkeypatch, [a, b], opened=opened), menu)

    action = next(x for x in menu.actions() if x.text() == "plate_a")
    action.trigger()
    # ``open_recent`` defers by 50 ms, the same deferral the folder picker uses.
    QApplication.processEvents()
    from PySide6.QtTest import QTest
    QTest.qWait(150)
    assert opened == [a]


def test_open_still_reaches_the_folder_picker(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMenu

    a = tmp_path / "plate_a"
    a.mkdir()
    win = _win(monkeypatch, [a])
    menu = QMenu()
    _fill_dataset_menu(win, menu)

    next(x for x in menu.actions() if x.text() == "Open…").trigger()
    assert win._browsed == [True], (
        "adding the history cost the menu the one item it started with")


def test_a_folder_that_is_gone_is_offered_and_refuses(qapp, tmp_path, monkeypatch):
    """Same rule as Home's card, because it is the same function: shown,
    disabled, with the reason on the tooltip. Hiding it would tell the user
    their history was lost when a drive is merely unplugged."""
    from PySide6.QtWidgets import QMenu

    here = tmp_path / "mounted"
    here.mkdir()
    opened = []
    menu = QMenu()
    _fill_dataset_menu(_win(monkeypatch, [here, tmp_path / "unmounted"],
                            opened=opened), menu)

    gone = next(x for x in menu.actions() if x.text() == "unmounted")
    assert gone.isEnabled() is False
    assert "not reachable" in gone.toolTip()
    gone.trigger()
    from PySide6.QtTest import QTest
    QTest.qWait(120)
    assert opened == []


def test_the_open_dataset_is_not_offered_back_to_itself(qapp, tmp_path,
                                                        monkeypatch):
    """The button already names it, and its menu is how you leave it.

    The in/out layout is where a string comparison misses: the list stores the
    folder Review was pointed at, and ``data_dir`` is the ``out`` inside it.
    """
    from PySide6.QtWidgets import QMenu

    root = tmp_path / "plate_a"
    (root / "out").mkdir(parents=True)
    other = tmp_path / "plate_b"
    other.mkdir()
    menu = QMenu()
    _fill_dataset_menu(_win(monkeypatch, [root, other], data_dir=root / "out"),
                       menu)

    assert _labels(menu) == ["Open…", "Recent", "plate_b"]


def test_the_menu_is_capped(qapp, tmp_path, monkeypatch):
    """A ten-item list under a folder button is a file manager with a caret."""
    from PySide6.QtWidgets import QMenu

    paths = []
    for i in range(RECENTS_IN_MENU + 3):
        p = tmp_path / f"plate_{i}"
        p.mkdir()
        paths.append(p)
    menu = QMenu()
    _fill_dataset_menu(_win(monkeypatch, paths), menu)

    # Two chrome rows: Open… and the Recent heading.
    assert len(_labels(menu)) == RECENTS_IN_MENU + 2


def test_a_history_of_nothing_leaves_the_menu_as_it_was(qapp, monkeypatch):
    """No empty heading over no rows — a section that says only its own name."""
    from PySide6.QtWidgets import QMenu

    menu = QMenu()
    _fill_dataset_menu(_win(monkeypatch, []), menu)

    assert _labels(menu) == ["Open…"]


def test_the_button_fills_its_own_menu_when_it_is_about_to_be_shown(
        qapp, tmp_path, monkeypatch):
    """Through ``_dataset_button``, the constructor the shell calls.

    Two things at once, and the second is why this goes through the real
    builder: that the menu is populated at all, and that it is populated
    **again** on every show. The history changes while the session runs — every
    load adds to it, Home's × takes rows off it — so a menu filled once at
    window construction shows the list as it stood at launch for the rest of
    the session, and nothing about that looks broken.
    """
    a = tmp_path / "plate_a"
    a.mkdir()
    win = _win(monkeypatch, [a])
    parent = QWidget()
    button = _dataset_button(win, parent)

    assert "plate_a" in _labels(button.menu())

    b = tmp_path / "plate_b"
    b.mkdir()
    from well_viewer.core.recent_datasets import record_open
    record_open(win._settings, str(b), "plate_b", 99.0)

    button.menu().aboutToShow.emit()
    assert _labels(button.menu())[:3] == ["Open…", "Recent", "plate_b"], (
        "the menu still shows the history as it stood when the window was "
        "built")
