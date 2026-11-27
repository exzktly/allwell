"""The header's remote-node indicator, through the real window.

Slow lane — this constructs the whole ``AllWellApp``. ``tests/test_remote_node_indicator.py`` holds what the dot
shows; this file is the one that would notice the indicator missing from the header, gated to one mode like the
Review cluster beside it, or wired to nothing.

Manage Remotes is modal, so its ``exec`` is replaced by one that records the dialog and returns; everything up to it
— the header's handler, Analyze's opener, the dialog's construction — is the real code.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow


def _pump(qapp, n: int = 15) -> None:
    for _ in range(n):
        qapp.processEvents()


def test_the_indicator_is_in_the_header_in_every_mode_and_opens_manage_remotes(qapp, qtbot, monkeypatch):
    from allwell import AllWellApp
    from analyze.remote_dialog import ManageRemotesDialog
    from core.panes import PANE_MODES
    from ui.remote_node_button import RemoteNodeButton

    opened: list = []
    monkeypatch.setattr(ManageRemotesDialog, "exec", lambda self: opened.append(self) or 0)

    win = AllWellApp(None)
    qtbot.addWidget(win)
    win.show()
    _pump(qapp)
    btn = win._remote_node_btn
    assert isinstance(btn, RemoteNodeButton)
    assert btn.parentWidget() is win._header
    layout = win._header.layout()
    assert layout.indexOf(btn) < layout.indexOf(win._help_btn)

    for idx, mode in reversed(list(enumerate(PANE_MODES))):   # end on Review
        win._on_titlebar_mode_seg(idx)
        _pump(qapp)
        assert win._review.currentMode() == mode
        assert btn.isVisible(), f"the remote-node indicator is hidden in {mode}"
        btn.click()
        _pump(qapp)
        assert len(opened) == 1, f"clicking the indicator in {mode} did not open Manage Remotes"
        opened.clear()
