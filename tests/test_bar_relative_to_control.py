"""The *Then relative to* row, driven through its own combos and checkboxes.

CLAUDE.md §7.6: a control that exists is not a control that works. These pick
the variable and value in the real combos and assert what reached
``BarPlotState`` and that the bars were redrawn — not what the row offers.
"""
from __future__ import annotations

import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QVBoxLayout, QWidget

from review.features.barplot import ratio_baseline_view as view
from tests.test_bar_relative_to import _app


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _Registry:
    def __init__(self):
        self.by_name = {}

    def register(self, name, widget):
        self.by_name[name] = widget
        return widget

    def get_or_none(self, name):
        return self.by_name.get(name)


@pytest.fixture
def built(qapp, monkeypatch):
    ctx, ids = _app()
    ctx.widgets = _Registry()
    redraws = []
    monkeypatch.setattr(view, "redraw_bars", lambda c: redraws.append(c))
    host = QWidget()
    lay = QVBoxLayout(host)
    row = view.build_relative_to_row(ctx, host, lay)
    yield types.SimpleNamespace(ctx=ctx, ids=ids, row=row, redraws=redraws, host=host)
    host.deleteLater()


def _choose(row, name, text):
    combo = row.findChild(QComboBox, name)
    index = combo.findText(text)
    assert index >= 0, [combo.itemText(i) for i in range(combo.count())]
    combo.setCurrentIndex(index)


def test_choosing_treatment_vehicle_writes_the_rule_with_rna_ticked(built):
    _choose(built.row, "barRelVariable", "Treatment")
    _choose(built.row, "barRelValue", "Vehicle")
    bar = built.ctx.state.feature_slice("bar")
    assert (bar.rel_variable_id, bar.rel_value_id) == (built.ids["treat"], built.ids["veh"])
    assert bar.rel_within == [built.ids["rna"]]
    checks = {c.text(): c.isChecked() for c in built.row.findChildren(QCheckBox)}
    assert checks == {"RNA": True}
    assert built.redraws, "choosing the rule did not redraw the bars"


def test_unticking_a_variable_writes_it_and_redraws(built):
    _choose(built.row, "barRelVariable", "Treatment")
    _choose(built.row, "barRelValue", "Vehicle")
    before = len(built.redraws)
    built.row.findChild(QCheckBox).setChecked(False)
    assert built.ctx.state.feature_slice("bar").rel_within == []
    assert len(built.redraws) == before + 1


def test_choosing_the_dash_turns_it_off(built):
    _choose(built.row, "barRelVariable", "Treatment")
    _choose(built.row, "barRelVariable", view.OFF_LABEL)
    bar = built.ctx.state.feature_slice("bar")
    assert not bar.relative_to_on() and bar.rel_within == []


def test_a_restored_rule_is_shown_by_the_row(built):
    bar = built.ctx.state.feature_slice("bar")
    bar.rel_variable_id, bar.rel_value_id = built.ids["treat"], built.ids["veh"]
    bar.rel_within = [built.ids["rna"]]
    built.row._aw_refresh()
    assert built.row.findChild(QComboBox, "barRelVariable").currentText() == "Treatment"
    assert built.row.findChild(QComboBox, "barRelValue").currentText() == "Vehicle"


def test_changing_the_value_writes_the_new_value(built):
    """Vehicle is the first value, so choosing it fires nothing; Drug does."""
    from tests.test_bar_relative_to import _value

    _choose(built.row, "barRelVariable", "Treatment")
    _choose(built.row, "barRelValue", "Drug")
    vset = built.ctx.state.selection.variables
    assert built.ctx.state.feature_slice("bar").rel_value_id == _value(
        vset, built.ids["treat"], "Drug")
