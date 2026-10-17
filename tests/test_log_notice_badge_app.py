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


def test_a_logged_warning_raises_the_log_buttons_badge_until_opened(qapp):
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
    finally:
        win.close()
