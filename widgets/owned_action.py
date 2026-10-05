"""Add an action to a menu or toolbar so that it is released with its owner.

``QMenu.addAction(text)`` — and the ``QToolBar`` / ``QWidget`` twins — creates
the ``QAction`` on the C++ side and hands back a wrapper for it. Connect a
Python callable to one of that wrapper's signals and PySide keeps the wrapper
for the rest of the process, after the menu and the action are both deleted.
Measured with PySide6 6.7.3, five menus of one action each, built and
destroyed: five wrappers left behind with ``triggered`` connected to a lambda,
none without the connection, and none when the same action is created in
Python with the menu as its parent. A context menu rebuilt on every
right-click, or the dataset menu rebuilt on every window, therefore grew the
process by one wrapper per action per use.

:func:`owned_action` is the second shape: the action is constructed here,
parented to *owner*, and added to it, so it goes when *owner* goes, whatever is
connected to it. ``tests/test_connected_actions_are_owned.py`` fails on a
connected action built the first way.

That only helps once the owner itself goes. A menu built on every opening and
parented to a long-lived widget stays alive, with its actions, until that widget
does; opening it through ``widgets.menu_exec.exec_transient_menu`` is what
deletes it after each use.

Qt only, no application knowledge.
"""

from __future__ import annotations

from PySide6.QtGui import QAction


def owned_action(owner, *args) -> QAction:
    """``owner.addAction(*args)``, with the action created on the Python side.

    *args* is what ``addAction`` takes for a plain action: ``(text,)`` or
    ``(icon, text)``. Returns the action, ready for its signals to be
    connected.
    """
    action = QAction(*args, owner)
    owner.addAction(action)
    return action
