"""What a second, line-by-line review of the per-sample-controls branch found.

None of these is a crash, which is why a fast lane of several thousand tests
stayed green over all of them. They are the other failure mode this branch keeps
running into: a panel, a status line or a CSV column stating something the
numbers beside it do not support — CLAUDE.md §7's rule that degraded output must
never be indistinguishable from good output, broken six different ways.

Two habits are deliberate here and worth keeping when this file grows:

* **assert at the boundary** (§7.4 #1). The axis-title tests read
  ``ax.get_title()`` after the real draw call rather than asking the suffix
  helper what it would have returned — a suffix helper that returns the right
  string while the renderer overwrites it is exactly what shipped;
* **compare the two paths against each other**, not each against a number the
  test computed. The tab and the batch export drew different values for two
  years because nothing ever ran both on one fixture and subtracted.
"""
from __future__ import annotations

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from PySide6.QtWidgets import QApplication

from well_viewer.domain import fold_change as _fc
from well_viewer.domain import fold_change_pairing as _pairing
from well_viewer.domain.batch_models import BarGroup, ReplicateSet
from well_viewer.domain.control_ref import KIND_LEGACY, ControlRef
from well_viewer.domain.fold_change_state import FoldChangeState
from well_viewer.features.barplot.collect import (
    collect_bar_items,
    collect_bar_items_for_group,
)

from tests.conftest import MockApp, make_agg

_SET = ControlRef.for_replicate_set
_WELL = ControlRef.for_well


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _two_set_app(**kw):
    """Two replicate sets of two wells each, with unequal per-well counts.

    Unequal on purpose: a set whose wells hold 4 and 6 events has a per-well
    mean of 5.0 and a cross-well spread that is not zero, so a test can tell
    the mean from the total and the spread from a fabricated zero.
    """
    app = MockApp(
        per_well_pts={
            "A01": [make_agg(0.0, 100.0, frac=0.20, n_above=4),
                    make_agg(6.0, 120.0, frac=0.25, n_above=5)],
            "A02": [make_agg(0.0, 110.0, frac=0.22, n_above=6),
                    make_agg(6.0, 130.0, frac=0.26, n_above=7)],
            "B01": [make_agg(0.0, 200.0, frac=0.40, n_above=8),
                    make_agg(6.0, 260.0, frac=0.52, n_above=11)],
            "B02": [make_agg(0.0, 210.0, frac=0.41, n_above=9),
                    make_agg(6.0, 270.0, frac=0.53, n_above=12)],
        },
        selections=[
            {"id": 1, "name": "CTRL", "wells": ["A01", "A02"], "source": "rep_set"},
            {"id": 2, "name": "DRUG", "wells": ["B01", "B02"], "source": "rep_set"},
        ],
    )
    for k, v in kw.items():
        setattr(app, k, v)
    return app


def _group(app):
    return BarGroup(
        name="G",
        members=[ReplicateSet(name="CTRL", wells=["A01", "A02"]),
                 ReplicateSet(name="DRUG", wells=["B01", "B02"])],
        solo_wells=[])


# ── the batch bar figure and its on-tab twin ─────────────────────────────────
#
# The batch collector took ``scale_bar_value``'s defaults for the operator and
# never composed the two axes at all, so a plate exported under **subtract**, or
# with vs-t0 on, carried numbers that did not match the tab it came from — on
# every bar, at every timepoint. Nothing compared the two paths, so nothing said
# so; the CSV beside the figure was normalized the other way.

@pytest.mark.parametrize("op_ctrl,op_t0,order,vs_t0", [
    ("ratio", "ratio", "ctrl_first", False),
    ("subtract", "ratio", "ctrl_first", False),
    ("ratio", "subtract", "ctrl_first", False),
    ("ratio", "ratio", "ctrl_first", True),
    ("ratio", "ratio", "t0_first", True),
    ("subtract", "subtract", "ctrl_first", True),
    ("subtract", "subtract", "t0_first", True),
])
def test_the_batch_bar_agrees_with_the_tab(op_ctrl, op_t0, order, vs_t0):
    app = _two_set_app(fc_op_ctrl=op_ctrl, fc_op_t0=op_t0, fc_order=order)
    ctx = app._ctx
    fc = FoldChangeState(vs_control_on=True, control=_SET("CTRL"), vs_t0_on=vs_t0)

    _ug, tab, _b = collect_bar_items(ctx, 6.0, fc_state=fc)
    batch = collect_bar_items_for_group(
        ctx, _group(app), [6.0], val_col=ctx.state.channel.active_val_col,
        threshold=1.0, use_sem=False, per_fov_spread=False, fc_state=fc)

    def panels(items):
        return {i.key: (round(i.mean, 9), round(i.frac, 9), round(i.n_above, 9))
                for i in items}

    assert panels(tab) == panels(batch)


# ── the Events panel in the per-cell display modes ───────────────────────────
#
# ``draw_per_well_n_bars`` applies no fold-change scaling, on either surface
# that calls it. Both callers title all three panels before dispatching on
# display mode, so the Events panel carried "fold change vs CTRL" over its raw
# counts. ``draw_violin`` / ``draw_beeswarm`` already rescue the two panels they
# draw; nothing rescued this one.

def test_the_unscaled_events_panel_is_not_labelled_a_fold_change(qapp):
    from well_viewer.features.barplot.renderer import draw_per_well_n_bars
    app = _two_set_app()
    fig, ax = plt.subplots()
    try:
        ax.set_title("Events above threshold (N)  (fold change vs CTRL (ratio))")
        ax.set_ylabel("N(above)  (fold change vs CTRL (ratio))")
        draw_per_well_n_bars(app._ctx, ax, ["A01", "A02"], ["#f00", "#0f0"],
                             ["A01", "A02"], 6.0, 1.0)
        assert "fold change" not in ax.get_title()
        assert "fold change" not in ax.get_ylabel()
        assert "Events above threshold" in ax.get_title()
    finally:
        plt.close(fig)


# ── the Line tab's status line ───────────────────────────────────────────────
#
# A trace whose control was never assigned, or names a group that is not loaded,
# is blanked without any timepoint landing in the miss set. The status line read
# only that set, so two curves vanished under a legend that still named them and
# the status bar reported the ordinary well count.

def test_the_line_status_line_names_the_members_that_failed_to_pair():
    app = _two_set_app()
    app.selections.append(
        {"id": 3, "name": "OTHER", "wells": ["B01"], "source": "rep_set"})
    ctx = app._ctx
    ctx.state.plot.fc_vs_control_on = True
    ctx.state.plot.fc_control_mode = "per_member"
    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL"),
                                     "OTHER": _SET("DELETED")}
    from well_viewer.features.lineplot.controller import (
        collect_line_series, line_status_text,
    )
    series, _ = collect_line_series(ctx, threshold=1.0, use_sem=False)
    outcomes = {s.key: s.fc_outcome for s in series}
    assert outcomes["CTRL"] == _pairing.ControlOutcome.UNMAPPED
    assert outcomes["OTHER"] == _pairing.ControlOutcome.CONTROL_UNRESOLVED
    assert all(math.isnan(p.mean) for p in series[0].points), \
        "the members that failed to pair really are blank"

    # The value that reaches ``ctx.notify.set_status`` — asserting the sentence,
    # not the helper that could have produced it (§7.4 #1).
    rsets = ctx.app._rep_sets_active()
    text = line_status_text(ctx, series, set(), rsets, [])
    assert "1 with no control" in text
    assert "1 whose control is missing" in text
    assert "replicate set(s)" not in text, \
        "the ordinary well count, reported over a plot missing two curves"


def test_the_line_status_line_falls_back_to_the_well_count():
    """The clean case still reads as before — a shortfall sentence on every
    redraw would be its own kind of wrong."""
    from well_viewer.features.lineplot.controller import (
        collect_line_series, line_status_text,
    )
    app = _two_set_app()
    ctx = app._ctx
    series, _ = collect_line_series(ctx, threshold=1.0, use_sem=False)
    rsets = ctx.app._rep_sets_active()
    assert line_status_text(ctx, series, set(), rsets, []) == \
        "2 replicate set(s)  ·  4 well(s)"


# ── each panel drops on its own denominator, on the line path too ────────────
#
# ``normalize_member_pts`` blanked when the *mean* series was empty and passed
# an empty fraction series through as ``None``, which ``normalize_pts`` skips
# without complaint. The bar side grew ``panel_nan_flags`` for exactly this; the
# line side kept drawing the raw fraction under a fold-change title.

def test_a_control_with_no_fraction_blanks_only_the_fraction():
    app = MockApp(
        per_well_pts={
            "A01": [make_agg(0.0, 100.0, frac=float("nan"), n_above=4)],
            "B01": [make_agg(0.0, 200.0, frac=0.50, n_above=8)],
        },
        selections=[],
    )
    ctx = app._ctx
    cache = _fc_cache_for(ctx)
    fc = FoldChangeState(vs_control_on=True, control=_WELL("A01"))
    denoms = _pairing.control_denominators(ctx, cache, fc, ["B01"])
    out = _pairing.normalize_member_pts(
        list(app.per_well_pts["B01"]), denoms, "B01",
        fc=fc, n_per_fov=False, baseline_tp=None,
        op_ctrl="ratio", op_t0="ratio")

    assert out[0][1] == pytest.approx(2.0)      # mean normalized
    assert math.isnan(out[0][3])                # fraction blanked, not raw
    assert out[0][4] == pytest.approx(2.0)      # events normalized


def _fc_cache_for(ctx):
    from well_viewer.domain.fold_change_series_cache import ControlSeriesCache
    return ControlSeriesCache(
        ctx, threshold=1.0, val_col=ctx.state.channel.active_val_col,
        use_sem=False, per_fov_spread=False, fluor_gates=None)


# ── blanking the Events panel in *both* its representations ─────────────────

def test_blank_point_blanks_the_per_fov_events_value_too():
    """Index 7 is what the Events panel plots with Aggregate-FOVs on."""
    pt = (0.0, 100.0, 3.0, 0.5, 5, 10, 0.1, 6.5, 0.9, 1.2, 11)
    out = _fc.blank_point(pt)
    assert math.isnan(out[4]), "the count"
    assert math.isnan(out[7]), "the per-FOV mean — the one that survived"
    assert out[8] == 0.0
    assert out[10] == 11, "the CSV's cross-well total is a measurement, not a ratio"


def test_blank_point_can_blank_one_panel():
    pt = (0.0, 100.0, 3.0, 0.5, 5, 10, 0.1, 6.5, 0.9, 1.2, 11)
    out = _fc.blank_point(pt, mean=False, n=False)
    assert out[1] == 100.0
    assert math.isnan(out[3])
    assert out[4] == 5
    assert out[7] == 6.5


# ── the CSV's replicate columns on a row that has no replicates ─────────────
#
# ``n_above_total`` means "the cross-well total behind a replicate
# aggregation". A per-well bar set it to its own count, so ``_replicate_unit_for``
# — which keys off ``is None`` — reported a unit of ``well`` beside a spread of
# 0.000000, the "the replicates agreed" reading the column documentation says is
# impossible for one well. The same non-None value also beat the per-FOV branch
# in ``n_above_threshold``.

def _solo_app():
    return MockApp(
        per_well_pts={"A01": [make_agg(0.0, 100.0, frac=0.2, n_above=4,
                                       n_above_pf_mean=4.5,
                                       n_above_pf_spread=0.7)]},
        selections=[],
    )


def test_a_single_well_bar_reports_no_replicate_total():
    ctx = _solo_app()._ctx
    _ug, items, _b = collect_bar_items(ctx, 0.0)
    assert [i.key for i in items] == ["A01"]
    assert items[0].n_above_total is None


def test_a_single_well_row_leaves_the_replicate_columns_empty():
    """Through the collector, not by hand-picking ``bar_metric_row``'s args."""
    from well_viewer.features.export.service import (
        _replicate_unit_for, bar_metric_row,
    )
    ctx = _solo_app()._ctx
    _ug, items, _b = collect_bar_items(ctx, 0.0)
    item = items[0]
    row = bar_metric_row(
        mean=item.mean, spread=item.spread, frac=item.frac,
        frac_spread=item.frac_spread, has=item.has_mean,
        n_above=item.n_above, n_above_spread=item.n_above_spread,
        ch="gfp", metric="mean_intensity", tp_str="0", threshold=1.0,
        band_lbl="SD", n_above_total=item.n_above_total,
        replicate_unit=_replicate_unit_for(item, per_fov=False),
    )
    assert row["n_above_replicate_unit"] == ""
    assert row["err_n_above_replicate_SD"] == ""
    assert row["n_above_threshold"] == "4"


def test_a_single_well_row_keeps_the_per_fov_float_form():
    from well_viewer.features.export.service import (
        _replicate_unit_for, bar_metric_row,
    )
    app = _solo_app()
    ctx = app._ctx
    ctx.state.plot.use_fov_replicates = True
    _ug, items, _b = collect_bar_items(ctx, 0.0)
    item = items[0]
    row = bar_metric_row(
        mean=item.mean, spread=item.spread, frac=item.frac,
        frac_spread=item.frac_spread, has=item.has_mean,
        n_above=item.n_above, n_above_spread=item.n_above_spread,
        ch="gfp", metric="mean_intensity", tp_str="0", threshold=1.0,
        band_lbl="SD", n_above_total=item.n_above_total,
        replicate_unit=_replicate_unit_for(item, per_fov=True),
    )
    assert row["n_above_threshold"] == "4.500000"


# ── the line CSV's Events columns ────────────────────────────────────────────

def test_the_line_csv_carries_all_three_events_columns():
    from well_viewer.features.export.service import (
        line_metric_fieldnames, line_metric_row,
    )
    cols = line_metric_fieldnames("gfp", "mean_intensity", "SD")
    assert "n_above_replicate_mean" in cols
    assert "err_n_above_replicate_SD" in cols
    assert "n_above_replicate_unit" in cols

    rset_pt = (0.0, 100.0, 3.0, 0.5, 5.0, 10, 0.1, 0.0, 0.0, 1.4, 10)
    row = line_metric_row(rset_pt, ch="gfp", metric="mean_intensity",
                          threshold=1.0, band_lbl="SD")
    assert row["n_above_threshold"] == 10
    assert row["n_above_replicate_mean"] == "5.000000"
    assert row["err_n_above_replicate_SD"] == "1.400000"
    assert row["n_above_replicate_unit"] == "well"

    solo_pt = (0.0, 100.0, 3.0, 0.5, 4, 10, 0.1, 0.0, 0.0)
    solo = line_metric_row(solo_pt, ch="gfp", metric="mean_intensity",
                           threshold=1.0, band_lbl="SD")
    assert solo["err_n_above_replicate_SD"] == ""
    assert solo["n_above_replicate_unit"] == ""


# ── the axis phrase for "on, and nothing resolved" ───────────────────────────

def test_a_single_control_that_named_nothing_says_so():
    assert _fc.control_axis_phrase("") == "vs control (none resolved)"
    assert _fc.control_axis_phrase("DMSO") == "vs DMSO"


# ── the fold-change state survives a rename ──────────────────────────────────
#
# ``concrete_control`` returns None for a legacy reference that resolves to
# nothing — a control group not currently loaded, which is the state the
# retain-don't-drop contract exists for. Assigning that result cleared the
# user's control while ``fc_vs_control_on`` stayed True: every bar NaN under a
# title reading "vs control", and ``null`` written to disk on the next save.

def test_renaming_a_group_keeps_an_unresolvable_control():
    from well_viewer.core.selection_controller import _upgrade_fc_controls
    app = _two_set_app()
    ctx = app._ctx
    ctx.state.plot.fc_vs_control_on = True
    ctx.state.plot.fc_control = ControlRef(KIND_LEGACY, "NOT_LOADED")
    ctx.state.plot.fc_control_map = {"DRUG": ControlRef(KIND_LEGACY, "ALSO_GONE")}

    _upgrade_fc_controls(ctx)

    assert ctx.state.plot.fc_control == ControlRef(KIND_LEGACY, "NOT_LOADED")
    assert ctx.state.plot.fc_control_map["DRUG"] == ControlRef(KIND_LEGACY, "ALSO_GONE")


def test_renaming_onto_an_existing_key_keeps_the_renamed_entry():
    """The live pairing wins the collision, not the dormant one it lands on."""
    from well_viewer.core.plot_state import PlotState
    plot = PlotState()
    plot.fc_control_map = {"A": _SET("CTRL_A"), "B": _SET("CTRL_B")}
    assert plot.rename_member("A", "B") is True
    assert plot.fc_control_map == {"B": _SET("CTRL_A")}


# ── the pairing window agrees with itself ────────────────────────────────────

def test_an_inherited_assignment_is_not_reported_unused():
    app = _two_set_app()
    ctx = app._ctx
    controls = _pairing.build_per_member_controls(
        ctx, ["B01", "B02"], {"DRUG": _SET("CTRL")},
        member_wells={"B01": ["B01"], "B02": ["B02"]},
        member_rsets={"B01": "DRUG", "B02": "DRUG"},
    )
    assert controls.get("B01").inherited_from == "DRUG"
    assert controls.unused == (), "consumed through inheritance, not unused"


def test_the_table_and_the_footer_share_one_resolved_mapping():
    from well_viewer.core import fold_change_pairing_view as view
    app = _two_set_app()
    ctx = app._ctx
    ctx.state.plot.bar_pool_replicates = False
    ctx.state.plot.fc_vs_control_on = True
    ctx.state.plot.fc_control_mode = "per_member"
    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL")}
    rows = view.pairing_rows(ctx, "bar")
    assert any(r["inherited_from"] == "DRUG" for r in rows)
    assert view.unused_rows(ctx, "bar") == []


# ── the summary count describes what is on screen ────────────────────────────

def test_the_summary_count_excludes_a_hidden_pure_control():
    app = _two_set_app()
    ctx = app._ctx
    controls = _pairing.build_per_member_controls(
        ctx, ["CTRL", "DRUG"], {"CTRL": _SET("CTRL"), "DRUG": _SET("CTRL")},
        member_wells={"CTRL": ["A01", "A02"], "DRUG": ["B01", "B02"]},
        render="hidden",
    )
    assert controls.hidden_members() == ("CTRL",)
    assert _pairing.summary_text(controls) == "Per-sample control (1/1)"


# ── two controls that share a token are two controls ─────────────────────────

def test_tick_labels_do_not_collapse_a_well_and_a_set_of_one_name():
    class _Item:
        def __init__(self, display, control):
            self.display = display
            self.fc_control = control
            self.fc_outcome = _pairing.ControlOutcome.OK

    items = [_Item("X", _WELL("A01")), _Item("Y", _SET("A01"))]
    labels = _pairing.tick_labels(items)
    assert all("÷" in lbl for lbl in labels), \
        "two distinct controls, so each bar has to say which one"


# ── the saved-plot summary counts controls, not entries ──────────────────────

def test_the_saved_plot_summary_counts_distinct_controls():
    from well_viewer.features.saved_plots.collect import _fc_control_phrase
    fc = {
        "control_mode": "per_member",
        "control_map": {
            "A": {"kind": "set", "name": "C1"},
            "B": {"kind": "set", "name": "C2"},
            "C": {"kind": "set", "name": "C3"},
            "D": {"kind": "set", "name": "C1"},
            "E": {"kind": "set", "name": "C2"},
        },
    }
    assert _fc_control_phrase(fc) == "vs own control (×3)"


# ── the two shortfall wordings cannot drift ──────────────────────────────────

def test_both_shortfall_surfaces_word_it_the_same_way():
    class _Item:
        def __init__(self, outcome):
            self.display = "X"
            self.fc_control = None
            self.fc_outcome = outcome

    items = [_Item(_pairing.ControlOutcome.UNMAPPED),
             _Item(_pairing.ControlOutcome.CONTROL_UNRESOLVED)]
    from_items = _pairing.shortfall_from_items(items)
    phrase = _pairing._shortfall_phrase(
        {_pairing.ControlOutcome.UNMAPPED: 1,
         _pairing.ControlOutcome.CONTROL_UNRESOLVED: 1})
    assert phrase in from_items


# ── the modal guard sees a context menu ──────────────────────────────────────
#
# ``QMenu.exec`` blocks in Qt's C++ event loop exactly as a dialog does, and
# offscreen it never gets the click that would close it — pytest's signal-based
# timeout cannot interrupt it. It was outside ``_MODAL_CALLS`` while nine call
# sites reached for it, one of them in a module whose docstring cites that guard
# as the reason it is a non-modal window. Adding ``QMenu`` to that list does not
# work (see ``widgets/menu_exec.py``); the seam is what the guard patches.

def test_the_menu_seam_is_a_modal_call(qapp):
    from PySide6.QtWidgets import QMenu

    from tests.conftest import ModalDialogInTest
    from widgets import menu_exec

    menu = QMenu()
    with pytest.raises(ModalDialogInTest):
        menu_exec.exec_menu(menu, None)
