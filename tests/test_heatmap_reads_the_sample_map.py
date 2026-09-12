"""The Heat Map's row and column ticks say what the sample map says.

Every other plotting surface in Review already names a well by what the user
typed on Home → Sample Map — ``selections_model.figure_names_map`` for the
``well_name`` column in every exported CSV, ``well_display_label_and_source``
for a tick on Bar or Line. The Heat Map was the holdout: it drew ``A``–``H``
and ``1``–``12`` from the plate default, or a bare ``1``, ``2``, ``3`` on a
layout built by *Auto-populate from selection*, over a plate whose doses and
cell lines were fully annotated. The annotation was read for pooling, for
colour and for grouping, and never for the one thing on this tab the user
could see.

The two halves of the rule are tested separately because they fail
differently:

* :func:`sample_variables.labels_for_axis_lines` decides *what* a line is
  called, and the trap in it is a variable that is the same everywhere —
  a plate that is all ``HeLa`` must not print ``HeLa`` on all eight rows and
  all twelve columns, which is strictly less informative than the ``A``–``H``
  it displaced. So every fixture here carries a constant variable beside the
  varying one, and an implementation that simply joined every variable that
  rolled up would print it.
* ``redraw_heatmap`` decides *whether* that reaches matplotlib, and the
  assertions for it are on ``ax.get_xticklabels()`` — the outbound call, not
  the computed list (CLAUDE.md §7.4 #1). Asserting the helper's return value
  alone passes just as happily against a renderer that never calls it, which
  is the state this whole file is about.
"""

from __future__ import annotations

import types

import pytest

from review.domain.sample_variables import (
    Variable,
    VariableSet,
    VariableValue,
    labels_for_axis_lines,
)
from review.features.heatmap import controller as HM
from review.features.heatmap.models import HeatmapLayout

_TP = 0.0
_VAL = "gfp_mean_intensity"


def _dose_and_line_plate() -> VariableSet:
    """A 2×2 block of wells: dose varies by column, cell line is constant.

    ``Cell line`` is the same on every well on purpose. It rolls up cleanly on
    every row and every column, so a rule that kept every variable that
    resolves would put ``HeLa`` on all four ticks.
    """
    dose = Variable(
        id="v_dose", name="Dose", kind="number", unit="µM",
        values=(VariableValue(id="d1", label="1", number=1.0),
                VariableValue(id="d10", label="10", number=10.0)),
    )
    line = Variable(
        id="v_line", name="Cell line",
        values=(VariableValue(id="hela", label="HeLa"),),
    )
    return VariableSet(
        variables=(dose, line),
        assignments={
            "v_dose": {"A01": "d1", "B01": "d1", "A02": "d10", "B02": "d10"},
            "v_line": {w: "hela" for w in ("A01", "A02", "B01", "B02")},
        },
    )


# ── what a line is called ────────────────────────────────────────────────────

def test_a_variable_that_varies_along_the_axis_names_its_ticks():
    vset = _dose_and_line_plate()
    # The two columns of the block.
    assert labels_for_axis_lines(vset, [["A01", "B01"], ["A02", "B02"]]) == [
        "1 µM", "10 µM",
    ]


def test_a_variable_that_is_the_same_everywhere_names_nothing():
    """``Cell line`` is HeLa on every well, so it distinguishes no row."""
    vset = _dose_and_line_plate()
    # The two rows of the block: each holds both doses, so ``Dose`` is MIXED
    # and drops out, and ``Cell line`` resolves but does not vary.
    assert labels_for_axis_lines(vset, [["A01", "A02"], ["B01", "B02"]]) == ["", ""]


def test_a_line_whose_wells_disagree_is_not_described_by_either_value():
    vset = _dose_and_line_plate()
    labels = labels_for_axis_lines(vset, [["A01", "A02"], ["B01"]])
    # The first line spans both doses; the second is one well at 1 µM. The
    # second still resolves, which is what makes the per-tick fallback in the
    # renderer possible.
    assert labels == ["", "1 µM"]


def test_a_line_holding_an_unannotated_well_is_not_described():
    """``roll_up``'s strict half: one unassigned well voids the whole line.

    ``H12`` is on the plate and in no variable's assignment map, so the first
    line resolves nothing at all — not the dose, and not the cell line either.
    That leaves ``Cell line`` resolved on exactly one of the two lines, which
    is the case the "varies along the axis" rule deliberately does not drop:
    one tick carrying ``HeLa`` where its neighbour carries nothing is told
    apart by it, so the surviving tick says everything it resolved.
    """
    vset = _dose_and_line_plate()
    assert labels_for_axis_lines(vset, [["A01", "H12"], ["A02", "B02"]]) == [
        "", "10 µM · HeLa",
    ]


def test_an_empty_line_and_an_empty_axis_are_both_answered():
    """An empty line resolves nothing, for the same reason and to the same end."""
    vset = _dose_and_line_plate()
    assert labels_for_axis_lines(vset, [[], ["A02", "B02"]]) == [
        "", "10 µM · HeLa",
    ]
    assert labels_for_axis_lines(vset, []) == []


def test_two_varying_variables_compose_one_label():
    """Both axes' worth of annotation on one axis, joined the way a group name is."""
    dose = Variable(
        id="v_dose", name="Dose", kind="number", unit="µM",
        values=(VariableValue(id="d1", label="1", number=1.0),
                VariableValue(id="d10", label="10", number=10.0)),
    )
    line = Variable(
        id="v_line", name="Cell line",
        values=(VariableValue(id="hela", label="HeLa"),
                VariableValue(id="u2os", label="U2OS")),
    )
    vset = VariableSet(
        variables=(dose, line),
        assignments={
            "v_dose": {"A01": "d1", "A02": "d10"},
            "v_line": {"A01": "hela", "A02": "u2os"},
        },
    )
    assert labels_for_axis_lines(vset, [["A01"], ["A02"]]) == [
        "1 µM · HeLa", "10 µM · U2OS",
    ]


# ── whether it reaches the axes ──────────────────────────────────────────────

@pytest.fixture
def drawn(monkeypatch):
    """Draw the heat map over the 2×2 block and hand back its axes.

    Returns a callable so each test can vary the layout, the variable set and
    the toggle without rebuilding the stubs. Everything the renderer reaches
    for outside its own module is stubbed to a constant; the value of a cell
    is fixed at 1.0, because this file is about the labels around the grid and
    not the numbers in it.
    """
    from matplotlib.figure import Figure

    def _draw(*, layout, vset, from_sample_map=True):
        fig = Figure()
        ax = fig.add_subplot(1, 1, 1)
        canvas = types.SimpleNamespace(draw_idle=lambda: None)
        widgets = {
            "_heatmap_fig": fig, "_heatmap_ax": ax, "_heatmap_canvas": canvas,
            "_heatmap_cax": None,
            "_heatmap_metric_cb": types.SimpleNamespace(
                currentText=lambda: HM.METRIC_MEAN),
            "_heatmap_tp_cb": types.SimpleNamespace(currentIndex=lambda: 0),
        }
        ctx = types.SimpleNamespace(
            window=None,
            state=types.SimpleNamespace(
                dataset=types.SimpleNamespace(
                    well_paths=dict.fromkeys(["A01", "A02", "B01", "B02"], "p")),
                channel=types.SimpleNamespace(
                    active_val_col=_VAL, active_channel="GFP",
                    active_metric="mean_intensity", ratio_index={}),
                plot=types.SimpleNamespace(use_fov_replicates=False, use_sem=True),
                selection=types.SimpleNamespace(selected_wells=set(), variables=vset),
                # Read by ``stats_compute._gate_cache_sig``, which keys the
                # colour-scale cache — reached even with the cell value stubbed.
                cell_gating=types.SimpleNamespace(
                    use_scatter_thresh=False, use_scatter_gates=False,
                    scatter_gates=[], thresh_choices=None, selected_gate=None),
                heatmap=types.SimpleNamespace(
                    tp_values=[_TP], scale_mode="Auto", vmin=None, vmax=None,
                    repset_avg=False, log_scale=False, show_error=False,
                    label_from_sample_map=from_sample_map),
            ),
            runtime=types.SimpleNamespace(heatmap=types.SimpleNamespace(
                layouts=[layout], global_range_cache=None, label_drag=None,
                colorbar=None, global_vmin=None, global_vmax=None)),
            widgets=types.SimpleNamespace(get_or_none=widgets.get),
            notify=types.SimpleNamespace(set_status=lambda _m: None),
        )
        ctx.state.heatmap.active_layout_name = layout.name
        monkeypatch.setattr(HM, "current_focus_tp", lambda _c: None)
        monkeypatch.setattr(HM, "on_cutoff_for_column", lambda *_a, **_k: 0.0)
        monkeypatch.setattr(HM, "get_all_fluor_gates", lambda _c: {})
        monkeypatch.setattr(HM, "active_channel_label", lambda _c: "GFP")
        monkeypatch.setattr(HM, "apply_export_style_to_current",
                            lambda *_a, **_k: None)
        monkeypatch.setattr(HM, "_cell_stat",
                            lambda *_a, **_k: (1.0, float("nan")))
        monkeypatch.setattr(HM, "_cell_value", lambda *_a, **_k: 1.0)
        HM.redraw_heatmap(ctx)
        return ax

    return _draw


def _block_layout() -> HeatmapLayout:
    """A 2×2 grid: column 0 is the 1 µM pair, column 1 the 10 µM pair."""
    layout = HeatmapLayout(name="Block", rows=2, cols=2)
    layout.assign(0, 0, ["A01"])
    layout.assign(1, 0, ["B01"])
    layout.assign(0, 1, ["A02"])
    layout.assign(1, 1, ["B02"])
    return layout


def test_the_column_ticks_carry_the_doses_the_columns_hold(drawn):
    ax = drawn(layout=_block_layout(), vset=_dose_and_line_plate())
    assert [t.get_text() for t in ax.get_xticklabels()] == ["1 µM", "10 µM"]


def test_the_row_ticks_fall_back_where_the_sample_map_says_nothing(drawn):
    """Both rows span both doses, so the rows keep the grid's own numbering."""
    ax = drawn(layout=_block_layout(), vset=_dose_and_line_plate())
    assert [t.get_text() for t in ax.get_yticklabels()] == ["1", "2"]


def test_the_fallback_is_per_tick_not_per_axis(drawn):
    """One undescribable column does not cost the other column its label."""
    layout = HeatmapLayout(name="Mixed", rows=2, cols=2)
    layout.assign(0, 0, ["A01"])
    layout.assign(1, 0, ["A02"])   # column 0 spans both doses
    layout.assign(0, 1, ["B02"])
    layout.assign(1, 1, ["B02"])   # column 1 is 10 µM throughout
    layout.col_labels = ["left", "right"]
    ax = drawn(layout=layout, vset=_dose_and_line_plate())
    assert [t.get_text() for t in ax.get_xticklabels()] == ["left", "10 µM"]


def test_a_plate_with_no_variables_draws_the_grid_labels_it_always_drew(drawn):
    layout = _block_layout()
    layout.col_labels = ["1", "2"]
    layout.row_labels = ["A", "B"]
    ax = drawn(layout=layout, vset=VariableSet())
    assert [t.get_text() for t in ax.get_xticklabels()] == ["1", "2"]
    assert [t.get_text() for t in ax.get_yticklabels()] == ["A", "B"]


def test_the_toggle_off_restores_the_grid_labels(drawn):
    layout = _block_layout()
    layout.col_labels = ["1", "2"]
    ax = drawn(layout=layout, vset=_dose_and_line_plate(), from_sample_map=False)
    assert [t.get_text() for t in ax.get_xticklabels()] == ["1", "2"]


# ── a name the user typed is not overwritten ─────────────────────────────────

def test_a_typed_tick_name_outranks_the_sample_map(drawn):
    """A rename that redraws as something else is a control that does not work.

    The sample-map rung ships **on**, so nobody opted into having their labels
    replaced; and ``_on_label_double_click`` writes into the same
    ``col_labels`` slot the plate default lives in, which is why the layout
    records *which* ticks the user named rather than inferring it from the
    text (a user may perfectly well type ``A``).
    """
    layout = _block_layout()
    layout.col_labels = ["Vehicle", "Treated"]
    layout.mark_label_typed("col", 0)
    ax = drawn(layout=layout, vset=_dose_and_line_plate())

    # Column 0 was named by the user; column 1 was not, so the dose speaks.
    assert [t.get_text() for t in ax.get_xticklabels()] == ["Vehicle", "10 µM"]


def test_clearing_a_typed_name_hands_the_tick_back_to_the_sample_map(drawn):
    layout = _block_layout()
    layout.col_labels = ["Vehicle", "Treated"]
    layout.mark_label_typed("col", 0)
    layout.mark_label_typed("col", 0, False)
    ax = drawn(layout=layout, vset=_dose_and_line_plate())

    assert [t.get_text() for t in ax.get_xticklabels()] == ["1 µM", "10 µM"]


def test_a_typed_name_is_kept_on_a_plate_with_no_variables(drawn):
    """The marker changes precedence; it never invents a label of its own."""
    layout = _block_layout()
    layout.col_labels = ["Vehicle", "Treated"]
    layout.mark_label_typed("col", 0)
    ax = drawn(layout=layout, vset=VariableSet())

    assert [t.get_text() for t in ax.get_xticklabels()] == ["Vehicle", "Treated"]


# ── two ticks that compose one name are still told apart ─────────────────────

def _run_plate() -> VariableSet:
    """Rows A–D HeLa, rows E–H U2OS — a variable constant across a *run*."""
    line = Variable(
        id="v_line", name="Cell line",
        values=(VariableValue(id="hela", label="HeLa"),
                VariableValue(id="u2os", label="U2OS")),
    )
    return VariableSet(
        variables=(line,),
        assignments={"v_line": {
            f"{r}{c:02d}": ("hela" if r in "ABCD" else "u2os")
            for r in "ABCDEFGH" for c in range(1, 13)
        }},
    )


def test_a_run_of_one_value_takes_the_grid_label_as_its_suffix():
    """Four rows reading ``HeLa`` cannot be told apart, which is the whole job.

    The drop rule catches a variable constant across the *whole* axis. A run of
    it resolves two distinct values, so the variable is kept and the axis went
    ``HeLa ×4, U2OS ×4`` with ``A``–``H`` gone — row 0 indistinguishable from
    row 1. The suffix is the same answer ``names_for_well_sets`` gives to two
    groups that compose one name.
    """
    rows = list("ABCDEFGH")
    lines = [[f"{r}{c:02d}" for c in range(1, 13)] for r in rows]

    assert labels_for_axis_lines(_run_plate(), lines, fallbacks=rows) == [
        "HeLa (A)", "HeLa (B)", "HeLa (C)", "HeLa (D)",
        "U2OS (E)", "U2OS (F)", "U2OS (G)", "U2OS (H)",
    ]


def test_a_name_only_one_tick_carries_takes_no_suffix():
    """The suffix marks a collision; it is not decoration on every tick."""
    vset = _dose_and_line_plate()

    assert labels_for_axis_lines(
        vset, [["A01", "B01"], ["A02", "B02"]], fallbacks=["1", "2"]) == [
        "1 µM", "10 µM"]


def test_replicate_columns_of_one_dose_stay_distinguishable(drawn):
    """The per-well layout *Auto-populate* builds with Rep-set avg off.

    Two columns holding different replicates of one dose composed the same
    string and lost their numbers, so the axis said which dose each column was
    and no longer said which column.
    """
    layout = HeatmapLayout(name="Reps", rows=1, cols=4)
    for c, well in enumerate(["A01", "B01", "A02", "B02"]):
        layout.assign(0, c, [well])
    ax = drawn(layout=layout, vset=_dose_and_line_plate())

    # Two doses over four replicate columns: the variable varies along the
    # axis, so it is kept — and each pair then needs its column number back.
    assert [t.get_text() for t in ax.get_xticklabels()] == [
        "1 µM (1)", "1 µM (2)", "10 µM (3)", "10 µM (4)"]


def test_a_variable_with_one_value_across_the_axis_still_names_nothing(drawn):
    """The drop rule, not the suffix, answers the fully-constant case.

    Both columns here are 1 µM, so the dose distinguishes neither and the grid
    numbers stay — rather than the axis reading ``1 µM (1)``, ``1 µM (2)``,
    which is the column number wearing a word that adds nothing.
    """
    layout = HeatmapLayout(name="Same", rows=1, cols=2)
    layout.assign(0, 0, ["A01"])
    layout.assign(0, 1, ["B01"])   # both 1 µM
    ax = drawn(layout=layout, vset=_dose_and_line_plate())

    assert [t.get_text() for t in ax.get_xticklabels()] == ["1", "2"]
