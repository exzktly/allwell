"""The Fold change popover grows with the *Then relative to* row.

Reported: after choosing a variable the dropdowns' text was cut off vertically.
The row adds its "within each" line once a variable is chosen, the menu kept
the height it opened at, and the layout squeezed both combos from 23 px to
12 px to fit. Measured against the real ``make_popover_button`` menu.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from review.core.ui_helpers import make_popover_button
from review.features.barplot import ratio_baseline_view as view
from tests.test_bar_relative_to import _app
from tests.test_bar_relative_to_control import _Registry


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_choosing_a_variable_grows_the_popover_instead_of_squeezing_it(qapp, monkeypatch):
    ctx, _ids = _app()
    ctx.widgets = _Registry()
    monkeypatch.setattr(view, "redraw_bars", lambda _c: None)
    host = QWidget()
    button, panel, layout = make_popover_button("Fold change ▾", host)
    row = view.build_relative_to_row(ctx, panel, layout)
    menu = button.menu()
    menu.popup(host.mapToGlobal(host.rect().bottomLeft()))
    qapp.processEvents()
    opened = menu.height()

    combo = row.findChild(QComboBox, "barRelVariable")
    combo.setCurrentIndex(combo.findText("Treatment"))
    qapp.processEvents()

    for cb in row.findChildren(QComboBox):
        assert cb.height() >= cb.sizeHint().height(), (cb.objectName(), cb.height())
    assert panel.height() >= panel.sizeHint().height()
    assert menu.height() > opened, "the menu kept the height it opened at"
    menu.close()
    host.deleteLater()
