"""The per-sample control editor: what it lists, and what it lets you say.

The failure this window exists to prevent is a **table that describes a plot
which does not exist** — a row for a member the renderer does not normalize, or
a member normalized with no row. So the row builder is pure and its output is
asserted against ``collect_bar_items`` here, not eyeballed.

The window itself is a non-modal ``QDialog``. That is not a style preference:
the answer is judged against the plot behind it, so it has to stay open while
the user looks — and a modal dialog blocks forever under the offscreen QPA,
where pytest's signal timeout cannot interrupt Qt's C++ exec loop.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from well_viewer.core import fold_change_pairing_view as view
from well_viewer.domain.control_ref import ControlRef
from well_viewer.domain.fold_change_pairing import ControlOutcome
from well_viewer.features.barplot.collect import collect_bar_items

from tests.conftest import MockApp, make_agg


_SET = ControlRef.for_replicate_set
_SETS = [
    {"name": "CTRL", "wells": ["A01"], "hidden": False},
    {"name": "DRUG", "wells": ["B01"], "hidden": False},
    {"name": "CTRL2", "wells": ["C01"], "hidden": False},
    {"name": "DRUG2", "wells": ["D01"], "hidden": False},
]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


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
    return c


# ── the rows are the plot's members ──────────────────────────────────────────

def test_the_rows_are_the_bars_the_plot_draws(ctx):
    """The equality the editor lives or dies by."""
    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL")}
    _ug, items, _b = collect_bar_items(ctx, 0.0)
    assert [r["member"] for r in view.pairing_rows(ctx, "bar")] \
        == [i.key for i in items]


def test_the_rows_follow_the_key_space_when_pooling_is_off(ctx):
    """The case an editor built on the runtime's key list gets wrong: two rows
    while the renderer normalizes four members."""
    ctx.state.plot.bar_pool_replicates = False
    ctx.state.plot.fc_control_map = {}
    _ug, items, _b = collect_bar_items(ctx, 0.0)
    rows = view.pairing_rows(ctx, "bar")
    assert [r["member"] for r in rows] == [i.key for i in items]
    assert [r["member"] for r in rows] == ["A01", "B01", "C01", "D01"]


def test_a_row_reports_the_wells_actually_used(ctx):
    """The column that answers the question. The other three describe intent,
    and intent is what was already wrong."""
    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL")}
    row = next(r for r in view.pairing_rows(ctx, "bar") if r["member"] == "DRUG")
    assert row["resolves_to"] == ["A01"]


def test_a_row_reports_its_status_in_words(ctx):
    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL"),
                                     "CTRL": _SET("CTRL"),
                                     "CTRL2": _SET("DELETED")}
    by_member = {r["member"]: r for r in view.pairing_rows(ctx, "bar")}
    assert by_member["DRUG"]["status"] == "paired"
    assert by_member["CTRL"]["status"] == "own control"
    assert by_member["CTRL2"]["status"] == "control not found"
    assert by_member["DRUG2"]["status"] == "no control set"


def test_an_inherited_row_says_where_it_came_from(ctx):
    ctx.state.plot.bar_pool_replicates = False
    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL")}
    row = next(r for r in view.pairing_rows(ctx, "bar") if r["member"] == "B01")
    assert row["inherited_from"] == "DRUG"
    assert row["resolves_to"] == ["A01"]


def test_an_assignment_for_a_member_that_is_not_plotted_is_listed_not_dropped(ctx):
    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL"),
                                     "GONE": _SET("CTRL")}
    assert view.unused_rows(ctx, "bar") == [("GONE", _SET("CTRL"))]
    assert "GONE" not in [r["member"] for r in view.pairing_rows(ctx, "bar")]


def test_status_words_cover_every_outcome():
    """A roster with a hole shows the enum's own spelling to a user."""
    for outcome in ControlOutcome:
        assert view.status_word(outcome) != str(outcome), outcome


def test_auto_pair_addresses_each_member_by_its_own_kind(ctx):
    """A set name and a well token are different references."""
    refs = view.member_control_refs(ctx, "bar")
    assert refs["CTRL"] == _SET("CTRL")
    ctx.state.plot.bar_pool_replicates = False
    assert view.member_control_refs(ctx, "bar")["A01"] == ControlRef.for_well("A01")


# ── the window ───────────────────────────────────────────────────────────────

def test_the_window_is_not_modal(qapp, ctx):
    """A modal dialog blocks forever under the offscreen QPA, and the answer is
    judged against the plot behind it anyway."""
    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL")}
    win = view.build_pairing_window(ctx, "bar")
    assert win.isModal() is False
    win.deleteLater()


def test_the_table_has_a_row_per_member(qapp, ctx):
    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL")}
    from PySide6.QtWidgets import QTableWidget

    win = view.build_pairing_window(ctx, "bar")
    table = win.findChild(QTableWidget)
    assert table.rowCount() == 4
    assert table.item(0, view.COL_SAMPLE).text().endswith("CTRL")
    win.deleteLater()


def test_the_resolves_column_shows_the_wells(qapp, ctx):
    from PySide6.QtWidgets import QTableWidget

    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL")}
    win = view.build_pairing_window(ctx, "bar")
    table = win.findChild(QTableWidget)
    row = next(r for r in range(table.rowCount())
               if table.item(r, view.COL_SAMPLE).text().endswith("DRUG")
               and not table.item(r, view.COL_SAMPLE).text().endswith("DRUG2"))
    assert table.item(row, view.COL_RESOLVES).text() == "A01"
    win.deleteLater()


def test_an_unresolvable_control_keeps_its_slot_labelled(qapp, ctx):
    """Dropping it back to "—" would make the table agree with itself and
    disagree with what the user set."""
    from PySide6.QtWidgets import QComboBox, QTableWidget

    ctx.state.plot.fc_control_map = {"DRUG": _SET("DELETED")}
    win = view.build_pairing_window(ctx, "bar")
    table = win.findChild(QTableWidget)
    row = next(r for r in range(table.rowCount())
               if table.item(r, view.COL_SAMPLE).text().endswith(" DRUG"))
    combo: QComboBox = table.cellWidget(row, view.COL_CONTROL)
    assert combo.currentData() == _SET("DELETED")
    assert combo.currentText().endswith(view.MISSING_SUFFIX)
    win.deleteLater()


def test_changing_a_row_writes_through_to_the_plot(qapp, ctx):
    from PySide6.QtWidgets import QComboBox, QTableWidget

    ctx.bus = _StubBus()
    ctx.state.plot.fc_control_map = {}
    win = view.build_pairing_window(ctx, "bar")
    table = win.findChild(QTableWidget)
    row = next(r for r in range(table.rowCount())
               if table.item(r, view.COL_SAMPLE).text().endswith(" DRUG"))
    combo: QComboBox = table.cellWidget(row, view.COL_CONTROL)
    idx = next(i for i in range(combo.count())
               if combo.itemData(i) == _SET("CTRL"))
    combo.setCurrentIndex(idx)

    assert ctx.state.plot.fc_control_map == {"DRUG": _SET("CTRL")}
    _ug, items, _b = collect_bar_items(ctx, 0.0)
    assert {i.key: i.mean for i in items}["DRUG"] == pytest.approx(2.0)
    win.deleteLater()


def test_clearing_a_row_removes_the_entry_rather_than_storing_an_empty_one(qapp, ctx):
    from PySide6.QtWidgets import QComboBox, QTableWidget

    ctx.bus = _StubBus()
    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL")}
    win = view.build_pairing_window(ctx, "bar")
    table = win.findChild(QTableWidget)
    row = next(r for r in range(table.rowCount())
               if table.item(r, view.COL_SAMPLE).text().endswith(" DRUG"))
    combo: QComboBox = table.cellWidget(row, view.COL_CONTROL)
    combo.setCurrentIndex(0)   # the "—" entry
    assert "DRUG" not in ctx.state.plot.fc_control_map
    win.deleteLater()


class _StubBus:
    """``set_fold_change_state`` announces on the bus; MockApp has none."""

    class _Sig:
        @staticmethod
        def emit(*_a):
            return None

    plot_options_changed = _Sig()


# ── the popover's entry point ────────────────────────────────────────────────

def test_the_control_combo_offers_the_per_sample_entry(qapp, ctx):
    from PySide6.QtWidgets import QComboBox

    from well_viewer.core.fold_change_controls_view import (
        PER_SAMPLE,
        _repopulate_control_combo,
    )

    ctx.state.plot.fc_control_mode = "single"
    combo = QComboBox()
    _repopulate_control_combo(ctx, combo)
    assert combo.itemData(0) is None, "— stays first"
    assert combo.itemData(1) is PER_SAMPLE
    # The member list below the separator is byte-identical to what it was:
    # every replicate set, then every loaded well.
    texts = [combo.itemText(i) for i in range(3, combo.count())]
    assert texts == ["CTRL", "DRUG", "CTRL2", "DRUG2",
                     "A01", "B01", "C01", "D01"]
    combo.deleteLater()


def test_the_combo_reports_the_resolved_count_while_per_sample_is_on(qapp, ctx):
    from PySide6.QtWidgets import QComboBox

    from well_viewer.core.fold_change_controls_view import (
        PER_SAMPLE,
        _repopulate_control_combo,
    )

    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL"),
                                     "DRUG2": _SET("DELETED")}
    combo = QComboBox()
    _repopulate_control_combo(ctx, combo)
    assert combo.currentData() is PER_SAMPLE
    assert combo.currentText() == "Per-sample control (1/4)"
    combo.deleteLater()


def test_the_combo_selects_the_single_control_when_that_is_the_mode(qapp, ctx):
    from PySide6.QtWidgets import QComboBox

    from well_viewer.core.fold_change_controls_view import _repopulate_control_combo

    ctx.state.plot.fc_control_mode = "single"
    ctx.state.plot.fc_control = _SET("CTRL2")
    combo = QComboBox()
    _repopulate_control_combo(ctx, combo)
    assert combo.currentData() == _SET("CTRL2")
    combo.deleteLater()
