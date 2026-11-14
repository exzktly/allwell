"""The phone shell's walks of every widget skip what is not a live ``QWidget``.

On the CI runner ``QApplication.allWidgets()`` handed the phone restyle a ``QStandardItem`` — a stale wrapper whose
address a new widget had reused — and the switch raised ``AttributeError: 'QStandardItem' object has no attribute
'parentWidget'`` (and, in other runs, crashed). It is the allocator's choice whether an address is reused, so the
case is built here by handing the walk such objects directly.
"""

from __future__ import annotations

import ast

from PySide6.QtGui import QStandardItem
from PySide6.QtWidgets import QApplication, QLabel

from tests._repo_scan import parse_source
from ui import phone_shell


def test_a_stale_wrapper_or_a_deleted_widget_is_not_walked(qapp, monkeypatch):
    live = QLabel("live")
    gone = QLabel("gone")
    import shiboken6
    shiboken6.delete(gone)
    stale = QStandardItem("stale")
    monkeypatch.setattr(QApplication, "allWidgets", staticmethod(lambda: [stale, gone, live]))
    assert phone_shell.all_live_widgets() == [live]


def test_every_walk_in_the_phone_shell_goes_through_the_live_filter():
    path = phone_shell.__file__
    tree = parse_source(path)
    raw = [node.lineno for node in ast.walk(tree)
           if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
           and node.func.attr == "allWidgets"]
    helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "all_live_widgets")
    assert raw == [c.lineno for c in ast.walk(helper) if isinstance(c, ast.Call)
                   and isinstance(c.func, ast.Attribute) and c.func.attr == "allWidgets"], raw
