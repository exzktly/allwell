"""PySide6 can route a non-``QEvent`` (a ``QStandardItem`` from a ``QCompleter``
popup) through an ``eventFilter``. Filters that then touch ``event.type()`` or
forward to ``super().eventFilter(obj, event)`` crash the whole window with a
``TypeError`` (``QObject.eventFilter`` rejects the non-``QObject`` event).

This reproduces the reported crash — a ``QStandardItem`` reaching the help
drawer's filter, which watches the main window — and pins the guard: the filter
must bail (return ``False``) before any ``event.type()`` / ``super()`` access.
"""
from __future__ import annotations

from PySide6.QtGui import QStandardItem
from PySide6.QtWidgets import QWidget


def test_drawer_eventfilter_survives_qstandarditem(qtbot):
    from widgets.drawer import Drawer

    host = QWidget()
    qtbot.addWidget(host)
    host.resize(800, 600)
    drawer = Drawer(host)

    # Before the guard this raised:
    #   TypeError: QObject.eventFilter called with wrong argument types:
    #   eventFilter(<host>, QStandardItem)
    assert drawer.eventFilter(host, QStandardItem()) is False
