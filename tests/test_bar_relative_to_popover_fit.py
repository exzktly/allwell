"""The Fold change page gives the *Then relative to* row the height it needs.

Reported, while the panel was a popover: after choosing a variable the
dropdowns' text was cut off vertically. The row adds its "within each" line
once a variable is chosen, the menu kept the height it opened at, and the
layout squeezed both combos from 23 px to 12 px to fit. The panel is a page of
the inspector now, in a scrolling slot that lays its content out at its own
height; the test holds the reported symptom — a combo shorter than its size
hint — against the real panel in a real inspector.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from review.core.inspector_panels import make_popover_button
from widgets.inspector import Inspector
from review.features.barplot import ratio_baseline_view as view
from tests.test_bar_relative_to import _app
from tests.test_bar_relative_to_control import _Registry


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_choosing_a_variable_grows_the_page_instead_of_squeezing_it(qapp, monkeypatch):
    ctx, _ids = _app()
    ctx.widgets = _Registry()
    monkeypatch.setattr(view, "redraw_bars", lambda _c: None)
    host = QWidget()
    button, panel, layout = make_popover_button("Fold change ▾", host, tab="data")
    row = view.build_relative_to_row(ctx, panel, layout)
    insp = Inspector()
    insp.setTabs([("data", "Data")])
    insp.resize(360, 240)                 # shorter than the page will become
    insp.show()
    insp.pushPage("Fold change", "Fold change", panel)
    qapp.processEvents()
    opened = panel.height()

    combo = row.findChild(QComboBox, "barRelVariable")
    combo.setCurrentIndex(combo.findText("Treatment"))
    qapp.processEvents()

    for cb in row.findChildren(QComboBox):
        assert cb.height() >= cb.sizeHint().height(), (cb.objectName(), cb.height())
    assert panel.height() >= panel.sizeHint().height()
    assert panel.height() > opened, "the page kept the height it opened at"
    insp.giveBackAll()
    insp.deleteLater()
    host.deleteLater()
