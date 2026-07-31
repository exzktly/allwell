"""Bare-`QApplication` widget tests for `widgets._support.set_variant`."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from widgets._support import set_variant  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_set_variant_updates_the_dynamic_property(qapp):
    btn = QPushButton("Go")
    btn.setProperty("variant", "secondary")
    set_variant(btn, "primary")
    assert btn.property("variant") == "primary"
