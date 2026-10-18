"""The title bar's storage button, through the real window.

Slow lane — this constructs the whole ``AllWellApp``. ``tests/test_local_storage.py``
holds what the dialog lists and clears; this file is the one that would notice
the button not being wired to it, or being gated to Review like the dataset
actions beside it when a full disk is not a Review-only problem.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow


def _pump(qapp, n: int = 15) -> None:
    for _ in range(n):
        qapp.processEvents()


def test_the_button_opens_one_storage_dialog_in_every_mode(qapp, qtbot):
    from allwell import AllWellApp
    from core.local_storage_view import StorageDialog

    win = AllWellApp(None)
    qtbot.addWidget(win)
    win.show()
    _pump(qapp)
    btn = win._storage_btn

    from core.panes import PANE_MODES

    for idx, mode in reversed(list(enumerate(PANE_MODES))):   # end on Review
        win._on_titlebar_mode_seg(idx)
        _pump(qapp)
        assert win._review.currentMode() == mode
        assert btn.isVisible(), f"the storage button is hidden in {mode}"

    btn.click()
    _pump(qapp)
    dialog = win._storage_dialog
    assert isinstance(dialog, StorageDialog) and dialog.isVisible()
    qtbot.waitUntil(lambda: dialog._status.text() != "Measuring…", timeout=15_000)

    btn.click()           # a second click raises the open dialog, not a new one
    _pump(qapp)
    assert win._storage_dialog is dialog
    dialog.close()
