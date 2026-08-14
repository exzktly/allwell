"""What an adversarial review found in this branch, pinned so it stays found.

Every one of these shipped through a green fast lane. Three reasons, and each
test below is written against the reason as much as the defect:

* **the fast lane could not see the code.** The batch-export render paths are
  `slow`-marked, so a ``NameError`` on every batch line export was invisible.
  These exercise them without building the app;
* **ruff does not flag a name that is loaded and never bound** (CLAUDE.md
  §7.5 #11), and a flow-insensitive AST sweep does not flag one bound on only
  one branch;
* **a test that asserts a helper cannot see what the widget shows** (§7.4 #1),
  which is how a legacy control reference resolved perfectly while the combo
  beside it reported "not in current selections".
"""
from __future__ import annotations

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QComboBox

from well_viewer.domain.batch_models import BarGroup
from well_viewer.domain.control_ref import KIND_LEGACY, ControlRef
from well_viewer.domain.fold_change import FC_STATE_OFF
from well_viewer.domain.fold_change_state import FoldChangeState
from well_viewer.features.barplot.collect import collect_bar_items_for_group

from tests.conftest import MockApp, make_agg

_SET = ControlRef.for_replicate_set
_WELL = ControlRef.for_well


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ── a member with no point at the drawn timepoint ────────────────────────────
#
# ``n_above_total`` was bound only inside the branch that found a point, so a
# member without one either inherited the previous member's count into its own
# CSV cell — a fabricated integer on a row whose mean is NaN — or, when it came
# first, raised ``UnboundLocalError`` and killed the whole export group.

@pytest.fixture
def sparse_group_ctx():
    app = MockApp(
        per_well_pts={"A01": [make_agg(0.0, 100.0)],
                      "A02": [make_agg(5.0, 200.0)]},
        selections=[],
    )
    return app._ctx


@pytest.mark.parametrize("order", [["A01", "A02"], ["A02", "A01"]])
def test_a_member_with_no_point_reports_no_count(sparse_group_ctx, order):
    grp = BarGroup(name="G", members=[], solo_wells=list(order))
    items = collect_bar_items_for_group(
        sparse_group_ctx, grp, target_t=0.0, threshold=0.0, use_sem=True,
        val_col="gfp_mean_intensity", fluor_gates=None, per_fov_spread=False,
        fc_state=FC_STATE_OFF)
    by_key = {i.key: i for i in items}
    assert by_key["A02"].has_mean is False
    assert math.isnan(by_key["A02"].n_above), (
        "a count inherited from the member before it, on a row with no data"
    )
    # Solo wells are not replicate aggregations, so neither reports a
    # cross-well total. What separates them is the drawn value: the member with
    # a point has a finite one, the member without it does not.
    assert by_key["A01"].n_above_total is None
    assert by_key["A02"].n_above_total is None
    assert by_key["A01"].n_above == 5.0, "the member that has data"


def test_the_order_of_the_members_does_not_change_the_answer(sparse_group_ctx):
    """It did: one order fabricated a count, the other raised."""
    def counts(order):
        grp = BarGroup(name="G", members=[], solo_wells=list(order))
        items = collect_bar_items_for_group(
            sparse_group_ctx, grp, target_t=0.0, threshold=0.0, use_sem=True,
            val_col="gfp_mean_intensity", fluor_gates=None,
            per_fov_spread=False, fc_state=FC_STATE_OFF)
        return {i.key: i.n_above_total for i in items}

    assert counts(["A01", "A02"]) == counts(["A02", "A01"])


# ── a legacy control reference ───────────────────────────────────────────────
#
# Every document written before the control's kind was recorded reopened with
# its control shown as "not in current selections" — the control panel
# misreporting live state, which is exactly what that fallback entry exists to
# prevent — while the plot went on normalizing correctly. The resolver was
# right the whole time; only the widget was wrong.

@pytest.fixture
def legacy_ctx():
    app = MockApp(
        per_well_pts={"A01": [make_agg(0.0, 100.0)],
                      "B01": [make_agg(0.0, 200.0)]},
        selections=[{"name": "CTRL", "wells": ["A01"], "hidden": False}],
    )
    return app._ctx


@pytest.mark.parametrize("name,kind", [("CTRL", "set"), ("B01", "well")])
def test_a_legacy_control_selects_its_own_entry(qapp, legacy_ctx, name, kind):
    from well_viewer.core.fold_change_controls_view import _repopulate_control_combo

    legacy_ctx.state.plot.fc_control = ControlRef.legacy(name)
    combo = QComboBox()
    _repopulate_control_combo(legacy_ctx, combo)
    assert combo.currentText() == name, (
        "the combo reported no control while the plot normalized against one"
    )
    assert combo.currentData() == ControlRef(kind, name)
    combo.deleteLater()


def test_a_legacy_control_is_written_concrete(qapp, legacy_ctx):
    """The claim the docs already made: the ambiguity retires once per
    document. It did not — ``concrete_control`` had one caller, in rename."""
    from well_viewer.core.fold_change_controls_view import _repopulate_control_combo

    legacy_ctx.state.plot.fc_control = ControlRef.legacy("CTRL")
    combo = QComboBox()
    _repopulate_control_combo(legacy_ctx, combo)
    assert legacy_ctx.state.plot.fc_control == _SET("CTRL")
    combo.deleteLater()


def test_a_legacy_control_that_resolves_to_nothing_stays_legacy(qapp, legacy_ctx):
    """A dataset that has not finished loading must not have its control
    guessed at — that is what the kind exists to stop."""
    from well_viewer.core.fold_change_controls_view import _repopulate_control_combo

    legacy_ctx.state.plot.fc_control = ControlRef.legacy("GONE")
    combo = QComboBox()
    _repopulate_control_combo(legacy_ctx, combo)
    assert legacy_ctx.state.plot.fc_control.kind == KIND_LEGACY
    assert "not in current selections" in combo.currentText()
    combo.deleteLater()


# ── the per-sample count is per tab ──────────────────────────────────────────

def test_the_summary_counts_the_tab_it_is_shown_on(qapp):
    """Bar and Line draw different member lists once pooling is off, so a count
    taken against one and shown on the other reports a plot that is not on
    screen."""
    from well_viewer.core.fold_change_controls_view import per_sample_combo_text

    app = MockApp(
        per_well_pts={w: [make_agg(0.0, 1.0)] for w in
                      ("A01", "A02", "B01", "B02")},
        selections=[{"name": "S0", "wells": ["A01", "A02"], "hidden": False},
                    {"name": "S1", "wells": ["B01", "B02"], "hidden": False}],
    )
    ctx = app._ctx
    ctx.state.plot.fc_vs_control_on = True
    ctx.state.plot.fc_control_mode = "per_member"
    ctx.state.plot.bar_pool_replicates = False
    ctx.state.plot.fc_control_map = {"S0": _SET("S0"), "S1": _SET("S0")}

    assert per_sample_combo_text(ctx, "line") == "Per-sample control (2/2)"
    # Bar keys on four well tokens here; both inherit their set's control.
    assert per_sample_combo_text(ctx, "bar") == "Per-sample control (4/4)"


# ── the exported figure's title ──────────────────────────────────────────────

def test_a_control_reference_never_reaches_a_figure_title():
    """Passing the reference itself printed its dataclass repr into the title
    of every batch bar figure."""
    from well_viewer.domain.fold_change import fold_change_suffix

    got = fold_change_suffix(True, False, _WELL("A01").display_name)
    assert got == "  (fold change vs A01 (ratio))"
    assert "ControlRef" not in got


# ── the batch bar path drops each panel on its own denominator ───────────────

def test_a_batch_bar_member_whose_control_is_gone_is_blank(qapp):
    app = MockApp(
        per_well_pts={"A01": [make_agg(0.0, 100.0)],
                      "B01": [make_agg(0.0, 200.0)]},
        selections=[],
    )
    grp = BarGroup(name="G", members=[], solo_wells=["A01", "B01"])
    fc = FoldChangeState(vs_control_on=True, control_mode="per_member",
                         control_map=(("B01", _WELL("A01")),))
    items = collect_bar_items_for_group(
        app._ctx, grp, target_t=0.0, threshold=0.0, use_sem=True,
        val_col="gfp_mean_intensity", fluor_gates=None, per_fov_spread=False,
        fc_state=fc)
    by_key = {i.key: i for i in items}
    assert by_key["B01"].mean == pytest.approx(2.0)
    assert math.isnan(by_key["A01"].mean), "unmapped, so a placeholder"
    assert by_key["A01"].fc_outcome == "unmapped"


# ── the tick labels ──────────────────────────────────────────────────────────
#
# With three or more controls the axis title collapses to "vs own control", so
# without a per-bar line the figure cannot be read without its CSV — the
# shortfall the feature exists to prevent, applied to the feature itself.

def _bar(display, outcome, control=None):
    from well_viewer.features.barplot.collect import BarItem
    return BarItem(key=display, display=display, color="#000000",
                   mean=1.0, spread=0.0, has_mean=True,
                   frac=0.0, frac_spread=0.0, has_frac=True,
                   n_above=0.0, n_above_spread=0.0,
                   fc_control=control, fc_outcome=outcome)


def test_one_control_leaves_the_ticks_alone():
    """The axis title already names it; repeating it under every bar is noise."""
    from well_viewer.domain.fold_change_pairing import tick_labels

    assert tick_labels([_bar("A", "ok", _SET("D")),
                        _bar("B", "ok", _SET("D"))]) == ["A", "B"]


def test_differing_controls_are_named_under_each_bar():
    from well_viewer.domain.fold_change_pairing import tick_labels

    assert tick_labels([_bar("A", "self", _SET("A")),
                        _bar("B", "ok", _SET("A")),
                        _bar("C", "ok", _SET("X"))]) \
        == ["A\n(ctrl)", "B\n÷ A", "C\n÷ X"]


def test_a_failed_pairing_always_says_so():
    """Even under one control: an empty slot beside bars that drew is exactly
    the ambiguity the outcome vocabulary exists for."""
    from well_viewer.domain.fold_change_pairing import tick_labels

    assert tick_labels([_bar("A", "ok", _SET("D")),
                        _bar("B", "unmapped"),
                        _bar("C", "control_unresolved", _SET("G"))]) \
        == ["A", "B\nno control", "C\ncontrol missing"]


def test_the_control_axis_being_off_leaves_the_ticks_alone():
    from well_viewer.domain.fold_change_pairing import tick_labels

    assert tick_labels([_bar("A", ""), _bar("B", "")]) == ["A", "B"]


# ── the Events replicate spread ──────────────────────────────────────────────

def test_a_replicate_spread_of_zero_is_written_as_zero():
    """A two-well set holding 5 and 5 has a spread of exactly 0.0, which is a
    measurement. Blanking it conflated "the replicates agreed" with "there was
    one replicate and nothing could be computed"."""
    from well_viewer.features.export.service import bar_metric_row

    row = bar_metric_row(
        mean=1.0, spread=0.0, frac=0.5, frac_spread=0.0, has=True,
        n_above=5.0, n_above_spread=0.0, n_above_total=10,
        replicate_unit="well", ch="GFP", metric="mean_intensity",
        tp_str="0", threshold=0.0, band_lbl="SD")
    assert row["err_n_above_replicate_SD"] == "0.000000"


def test_a_single_well_row_leaves_the_replicate_columns_empty():
    """Nothing to average over, so nothing computed — and the unit column is
    what says which of the two an empty spread means."""
    from well_viewer.features.export.service import bar_metric_row

    row = bar_metric_row(
        mean=1.0, spread=0.0, frac=0.5, frac_spread=0.0, has=True,
        n_above=5.0, n_above_spread=0.0, n_above_total=None,
        replicate_unit="", ch="GFP", metric="mean_intensity",
        tp_str="0", threshold=0.0, band_lbl="SD")
    assert row["err_n_above_replicate_SD"] == ""
    assert row["n_above_replicate_unit"] == ""


# ── the editor's colour chip ─────────────────────────────────────────────────

def test_each_row_takes_the_colour_of_its_own_bar(qapp):
    """``color_for_well`` hands an unparseable token the same fallback for
    every group, so every chip came out identical — and identical to no bar on
    the plate. The row is meant to be findable in the figure."""
    from well_viewer.core.fold_change_pairing_view import member_color

    app = MockApp(
        per_well_pts={"A01": [make_agg(0.0, 1.0)], "B01": [make_agg(0.0, 2.0)]},
        selections=[{"name": "CTRL", "wells": ["A01"], "hidden": False},
                    {"name": "DRUG", "wells": ["B01"], "hidden": False}],
    )
    ctx = app._ctx
    assert member_color(ctx, "bar", "CTRL") != member_color(ctx, "bar", "DRUG")
