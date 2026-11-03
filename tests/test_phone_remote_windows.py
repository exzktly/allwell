"""The Remotes dialogs in phone layout — the "remote manager" of the report.

Reported from a phone: the remote manager, among other pop-out windows, "not sized for mobile … the buttons get
clipped, window is impossible to close". Measured on a 432 × 936 px phone before the fix: Remote Storage, a
1000 × 560 dialog whose rows of buttons and spin boxes need 893 px, was squeezed to the phone's width with
Delete…, Keep forever, Save and Close cut down to fragments; sideways, Manage Remote Servers needed 499 px of
height on a 432 px screen and was squeezed below it.

Built as the app builds them, shown on a ``PhoneShell``'s window with phone layout on, and found by the shell's
own sweep. The phone's screen and those helpers are ``tests/test_phone_windows.py``'s; Remote Storage lists a
local staging tree through ``tests/test_remote_storage_dialog.py``'s cache, so no ssh is spawned.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QWidget  # noqa: E402

from tests.test_phone_windows import (  # noqa: E402,F401
    SIDEWAYS, UPRIGHT, _on_screen, _open, _pump, _shell, phone, qapp,
)
from tests.test_remote_storage_dialog import _LocalCache, staged  # noqa: E402,F401


def _reachable(button, screen: QRect) -> bool:
    """Drawn whole, and either on the screen now or inside what scrolls, where a drag reaches it."""
    hint = button.minimumSizeHint()
    if button.width() < hint.width() or button.height() < hint.height():
        return False
    if _on_screen(button, screen):
        return True
    body = button.window().findChild(QWidget, "PhoneWindowBody")
    return body is not None and body.isAncestorOf(button) and body.rect().contains(
        QRect(button.mapTo(body, button.rect().topLeft()), button.size()))


def _buttons(window) -> list:
    return [b for b in window.findChildren(QAbstractButton) if b.isVisibleTo(window) and b.window() is window]


def test_remote_storage_on_a_phone_keeps_every_button_whole_and_a_close_on_screen(
        qapp, qtbot, phone, staged):  # noqa: F811
    from analyze import remote_storage_dialog as rsd
    host, _ = staged
    win, shell = _shell(qapp)
    dialog = rsd.RemoteStorageDialog(win, host, cache=_LocalCache(host))
    dialog.setModal(False)
    _open(qapp, shell, dialog)
    qtbot.waitUntil(lambda: dialog._tree.topLevelItemCount() == 2 and not dialog._busy, timeout=10_000)
    _pump(qapp)

    cut = [b.text() for b in _buttons(dialog) if not _reachable(b, UPRIGHT)]
    assert not cut, f"cut or off the screen: {cut}"
    assert UPRIGHT.contains(dialog.frameGeometry())
    assert any(_on_screen(b, UPRIGHT) for b in _buttons(dialog) if b.text() in ("Close", "Close ✕")), \
        "nothing that closes the dialog is on the screen"
    body = dialog.findChild(QWidget, "PhoneWindowBody")
    assert body.width() <= dialog.findChild(QWidget, "PhoneWindowScroll").viewport().width(), \
        "the content is still wider than the phone"


def test_manage_remotes_on_a_phone_held_sideways_scrolls_instead_of_squeezing(
        qapp, phone, monkeypatch):  # noqa: F811
    from analyze import remote_dialog as ard
    monkeypatch.setattr(ard, "load_remotes", lambda: {})
    phone(SIDEWAYS)
    win, shell = _shell(qapp)
    dialog = ard.ManageRemotesDialog(win)
    dialog.setModal(False)
    _open(qapp, shell, dialog)

    assert dialog.minimumSizeHint().height() <= SIDEWAYS.height(), "the content is squeezed below its minimum"
    cut = [b.text() for b in _buttons(dialog) if not _reachable(b, SIDEWAYS)]
    assert not cut, f"cut or off the screen: {cut}"
    assert dialog.frameGeometry() == SIDEWAYS
    assert any(_on_screen(b, SIDEWAYS) for b in _buttons(dialog) if b.text() in ("Close", "Close ✕"))
