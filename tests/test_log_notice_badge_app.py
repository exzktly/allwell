"""The log badge through the real window: built on the real log button.

Slow lane — this constructs the whole ``AllWellApp`` once. The fast tests in
``test_log_notice_badge.py`` drive ``_append_log_line`` on a stub; this one
proves the constructor actually attaches the badge to the status bar's log
button and that a record logged through the real ``review`` logger reaches it.
"""

from __future__ import annotations

import logging

import pytest

pytestmark = pytest.mark.slow


def _pump(qapp, n: int = 15) -> None:
    for _ in range(n):
        qapp.processEvents()


def test_a_logged_warning_raises_the_log_buttons_badge_until_opened(qapp, monkeypatch):
    from allwell import AllWellApp
    from widgets.notice_badge import NoticeBadge

    win = AllWellApp(None)
    try:
        win.show()
        _pump(qapp)
        badge = win._log_badge
        assert isinstance(badge, NoticeBadge)
        assert badge.parentWidget() is win._log_btn

        badge.clear()
        logging.getLogger("review").warning("badge probe")
        _pump(qapp)
        assert badge.tone() == "warn"
        assert badge.isVisible()

        win._toggle_log_drawer()
        _pump(qapp)
        assert badge.tone() is None
        assert not badge.isVisible()

        # The drawer names the boot log, which lives in a hidden folder and is
        # needed only after the app has died — when nobody remembers the path.
        from PySide6.QtWidgets import QLabel

        from startup import boot_diag
        texts = [lbl.text() for lbl in win._log_drawer.findChildren(QLabel)]
        assert any(str(boot_diag.log_path()) in t for t in texts), texts

        # A previous session that was killed is announced at the next start
        # through the same drawer: the sentence, with the kept file's path,
        # reaches the history the drawer shows.
        monkeypatch.setattr(boot_diag, "_PREVIOUS_ENDING",
                            (boot_diag.log_path().with_name("allwell-boot.log.1"), "killed"))
        boot_diag.announce_previous_ending()
        _pump(qapp)
        assert any("WARNING  allwell.boot" in line and "allwell-boot.log.1" in line
                   for line in win._log_ring), win._log_ring[-3:]
    finally:
        win.close()
