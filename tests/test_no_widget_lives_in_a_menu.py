"""No widget lives in a menu.

A menu is for commands: click and it closes. A ``QWidgetAction`` puts a live
widget in one — a tick-list, a slider, a panel — which is a popover by another
name, opened from wherever the menu happens to hang, and one surface deeper than
whatever it was opened from. Anything that changes what a page shows is a page
of the inspector (``widgets/inspector.py``).

Every construction of a ``QWidgetAction`` is on the roster below, keyed by file
and function, with the row of the surfaces plan that removes it.
"""

from __future__ import annotations

from tests import _surface_guards as scan

#: ``(file, function)`` -> why the site is still there. An entry starting
#: ``pending Snn`` names the row of the surfaces plan that removes it; one
#: starting ``kept`` is permanent and says why.
WIDGET_ACTIONS: dict[tuple[str, str], str] = {
    ('review/features/nanowell/popout_view.py', '_check_entry'):
        'pending S21: the crops pop-out takes the multi-select component',
    ('ui/phone_shell.py', 'PhoneShell._menu_caption'):
        "kept: a caption label over a group of the phone menu's entries — text, not a control, and QMenu has no caption of its own that the phone's touch styling can size",
    ('ui/phone_shell.py', 'PhoneShell._text_size_row'):
        "pending S23: the phone's text-size choice becomes three plain commands",
    ('widgets/checkable_dropdown.py', 'CheckableDropdown._open_menu'):
        'pending S12: one of the multi-select builds',
}


def unrostered_widget_actions(trees=None, roster=None) -> list[str]:
    trees = scan.app_trees() if trees is None else trees
    return scan.unrostered(scan.widget_actions(trees), WIDGET_ACTIONS if roster is None else roster)


def test_no_new_widget_actions():
    found = unrostered_widget_actions()
    assert not found, (
        'a QWidgetAction puts a live widget in a menu: make it a page of the inspector (review.core.inspector_service.push), or roster it with a reason'
        ":\n  " + "\n  ".join(found))


def test_widget_actions_describes_only_real_sites():
    gone = scan.stale(scan.widget_actions(scan.app_trees()), WIDGET_ACTIONS)
    assert not gone, (
        "these WIDGET_ACTIONS entries no longer match a site — delete them, so the "
        "roster only shrinks:\n  " + "\n  ".join(gone))
