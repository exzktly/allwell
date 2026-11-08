"""The editor's "Pair by variable" row, driven through its own widgets.

CLAUDE.md §7.6: a control that exists is not a control that works. These tests
build the real per-sample editor window, choose the rule in its combos, press
its **Pair** button and assert what reached ``fc_control_map`` and the table —
not what the pure function would return if it were called.
"""
from __future__ import annotations

import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLabel, QPushButton, QTableWidget

from review.core import fold_change_pairing_view as view
from review.domain import sample_variables as sv
from review.domain.control_ref import ControlRef

from tests.conftest import MockApp, make_agg

_SET = ControlRef.for_replicate_set

#: Interleaved, so pairing by position and pairing by annotation disagree.
_SETS = [
    {"name": "HeLa-drug", "wells": ["A01"], "hidden": False},
    {"name": "U2OS-veh", "wells": ["B01"], "hidden": False},
    {"name": "HeLa-veh", "wells": ["C01"], "hidden": False},
    {"name": "U2OS-drug", "wells": ["D01"], "hidden": False},
]


class _StubBus:
    class _Sig:
        @staticmethod
        def emit(*_a):
            return None

    plot_options_changed = _Sig()


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _value(vset, vid, label):
    return next(v.id for v in vset.by_id(vid).values if v.label == label)


@pytest.fixture
def ctx():
    app = MockApp(
        per_well_pts={w: [make_agg(0.0, v)] for w, v in
                      (("A01", 100.0), ("B01", 200.0),
                       ("C01", 50.0), ("D01", 150.0))},
        selections=_SETS,
    )
    c = app._ctx
    c.state.plot.fc_vs_control_on = True
    c.state.plot.fc_control_mode = "per_member"
    c.state.plot.fc_control_map = {}
    vset, line = sv.new_variable(sv.VariableSet(), "Cell line")
    vset, compound = sv.new_variable(vset, "Compound")
    for vid, labels in ((line, ("HeLa", "U2OS")), (compound, ("DMSO", "DrugA"))):
        for label in labels:
            vset = sv.add_value(vset, vid, label)
    for well, cl, cp in (("A01", "HeLa", "DrugA"), ("B01", "U2OS", "DMSO"),
                         ("C01", "HeLa", "DMSO"), ("D01", "U2OS", "DrugA")):
        vset = sv.assign(vset, line, [well], _value(vset, line, cl))
        vset = sv.assign(vset, compound, [well], _value(vset, compound, cp))
    c.state.selection.variables = vset
    c.bus = _StubBus()
    c.widgets = types.SimpleNamespace(get_or_none=lambda _n: None,
                                      register=lambda *_a: None)
    return c


def _choose(win, object_name, text):
    combo = win.findChild(QComboBox, object_name)
    index = combo.findText(text)
    assert index >= 0, [combo.itemText(i) for i in range(combo.count())]
    combo.setCurrentIndex(index)


def test_pressing_pair_writes_each_cell_line_s_own_vehicle(qapp, ctx):
    win = view.build_pairing_window(ctx, "bar")
    _choose(win, "fcPairVariable", "Compound")
    _choose(win, "fcPairValue", "DMSO")
    checks = {c.text(): c for c in win.findChildren(QCheckBox)}
    # The default the row ticks: the vehicles differ on Cell line.
    assert {t: c.isChecked() for t, c in checks.items()} == {"Cell line": True}
    win.findChild(QPushButton, "fcPairByVariable").click()

    assert ctx.state.plot.fc_control_map == {
        "HeLa-drug": _SET("HeLa-veh"), "U2OS-veh": _SET("U2OS-veh"),
        "HeLa-veh": _SET("HeLa-veh"), "U2OS-drug": _SET("U2OS-veh"),
    }
    # The table the user reads agrees with the mapping. The stub registry does
    # not hold the window, so the state sync cannot reach it; rebuild directly.
    win._aw_populate()
    table = win.findChild(QTableWidget)
    resolves = {table.item(r, view.COL_SAMPLE).text().split()[-1]:
                table.item(r, view.COL_RESOLVES).text()
                for r in range(table.rowCount())}
    assert resolves == {"HeLa-drug": "C01", "U2OS-veh": "B01",
                        "HeLa-veh": "C01", "U2OS-drug": "B01"}
    result = [lbl.text() for lbl in win.findChildren(QLabel)
              if lbl.text().startswith("Paired")]
    assert result == ["Paired 4 of 4."]
    win.deleteLater()


def test_unticking_the_condition_names_the_ambiguity_and_clears_old_pairs(qapp, ctx):
    ctx.state.plot.fc_control_map = {"HeLa-drug": _SET("U2OS-veh")}
    win = view.build_pairing_window(ctx, "bar")
    _choose(win, "fcPairVariable", "Compound")
    _choose(win, "fcPairValue", "DMSO")
    for check in win.findChildren(QCheckBox):
        check.setChecked(False)
    win.findChild(QPushButton, "fcPairByVariable").click()

    assert ctx.state.plot.fc_control_map == {}
    result = [lbl.text() for lbl in win.findChildren(QLabel)
              if lbl.text().startswith("Paired")]
    assert len(result) == 1
    assert result[0].startswith(
        "Paired 0 of 4. Not paired: HeLa-drug — U2OS-veh, HeLa-veh all have "
        "Compound = DMSO")
    win.deleteLater()


def test_with_no_variables_the_row_says_where_to_define_one(qapp, ctx):
    ctx.state.selection.variables = sv.VariableSet()
    win = view.build_pairing_window(ctx, "bar")
    hints = [lbl for lbl in win.findChildren(QLabel)
             if "Home → Variables" in lbl.text()]
    assert len(hints) == 1 and not hints[0].isHidden()
    assert win.findChild(QPushButton, "fcPairByVariable").isVisibleTo(win) is False
    win.deleteLater()
