"""The one seam for opening a context menu and waiting on the answer.

``QMenu.exec`` blocks inside Qt's C++ event loop exactly as a modal dialog
does, and under the offscreen QPA nothing ever delivers the click that would
close it: the test hangs, and pytest's signal-based ``--timeout`` cannot
interrupt a C++ loop. ``tests/conftest.py::_no_modal_dialogs`` exists to turn
that hang into a named failure, and for dialogs it does — but ``QMenu.exec``
cannot be monkeypatched. Shiboken exposes it as a static/instance overload, so
assigning to ``QMenu.exec`` writes a class attribute that instance lookup then
ignores:

    >>> QMenu.exec = raises                # accepted
    >>> QMenu().exec                       # <built-in method exec ...>

A guard that quietly does nothing is worse than no guard, because it is
reported as coverage. So every call site goes through this function instead,
which is ordinary Python and therefore patchable, and the autouse fixture
patches *it*.

Call it through the module, never by importing the name::

    from widgets import menu_exec
    chosen = menu_exec.exec_menu(menu, btn.mapToGlobal(btn.rect().bottomLeft()))

``from widgets.menu_exec import exec_menu`` binds the function into the
importing module's globals, where a patch applied here can no longer reach it.
``tests/test_menu_exec_seam.py`` fails on both spellings of the raw call.
"""

from __future__ import annotations


def exec_menu(menu, global_pos):
    """Open *menu* at *global_pos* and return the chosen ``QAction``, or None."""
    return menu.exec(global_pos)


def exec_transient_menu(menu, global_pos):
    """Open a menu built for this one opening, then delete it.

    A menu built on every click and parented to a long-lived widget is never
    freed by Qt: it stays a child of that widget, with its actions, until the
    widget goes. Measured on a job-queue row, five openings left ten menus
    alive (each with its chain submenu). The menu is deleted with
    ``deleteLater`` so the chosen action is still valid for the caller; it is
    skipped when choosing an action already deleted the menu's parent.
    """
    try:
        return exec_menu(menu, global_pos)
    finally:
        import shiboken6
        if shiboken6.isValid(menu):
            menu.deleteLater()
