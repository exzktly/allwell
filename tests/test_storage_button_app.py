"""Storage, reached through Dataset tools, on the real window.

Slow lane — this constructs the whole ``AllWellApp``. ``tests/test_local_storage.py``
holds what the dialog lists and clears; this file is the one that would notice
the dataset menu's *Storage…* not being wired to it.

Storage was a title-bar icon shown in every mode. It is one of the five
*Dataset tools* now, in the dataset menu at the head of Review's rail, so at
the desk it is reached from Review.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow


def _pump(qapp, n: int = 15) -> None:
    for _ in range(n):
        qapp.processEvents()


def test_storage_in_the_dataset_menu_opens_one_storage_dialog(qapp, qtbot):
    from allwell import AllWellApp
    from ui.local_storage_view import StorageDialog

    win = AllWellApp(None)
    qtbot.addWidget(win)
    win.show()
    _pump(qapp)
    assert not hasattr(win, "_storage_btn"), "the title bar still has a storage icon"

    menu = win._review._dataset_menu
    menu.aboutToShow.emit()                      # the menu is filled as it opens
    entries = {a.text(): a for a in menu.actions()}
    assert "Dataset tools" in entries and not entries["Dataset tools"].isEnabled()
    storage = entries["Storage…"]

    storage.trigger()
    _pump(qapp)
    dialog = win._storage_dialog
    assert isinstance(dialog, StorageDialog) and dialog.isVisible()
    qtbot.waitUntil(lambda: dialog._status.text() != "Measuring…", timeout=15_000)

    storage.trigger()     # a second press raises the open dialog, not a new one
    _pump(qapp)
    assert win._storage_dialog is dialog

    dialog.close()        # and a press after closing shows the same one again,
    _pump(qapp)           # surveyed afresh, rather than building another
    menu.aboutToShow.emit()
    next(a for a in menu.actions() if a.text() == "Storage…").trigger()
    _pump(qapp)
    assert win._storage_dialog is dialog and dialog.isVisible()
    assert len(win.findChildren(StorageDialog)) == 1
    qtbot.waitUntil(lambda: not dialog._working, timeout=15_000)
    dialog.close()
