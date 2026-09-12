"""A Heat Map cell reports its spread across replicates, like every other tab.

The Heat Map drew one number per cell and nothing about how well that number
was determined — the tab's own builder said so in a comment ("Heatmap never
draws error bands or per-FOV spread"), and the card's shared error-band
controls were hidden on that basis. A cell pooling four FOVs of one well and a
cell holding a single field looked identical.

The spread comes from the same place the bar's whisker does: the replicate
list ``group_replicate_stats`` produces — per well, or per FOV when the
app-wide per-FOV toggle is on — reduced by the same ``_reduce_reps``. So the
``±`` under a cell and the whisker on the bar for the same set cannot measure
different things, and both follow the app's SD/SEM choice.

Two facts this file exists to pin, because both are ways for a cell to lie
quietly:

* **``Cell count`` never prints a ``±``.** It is a pooled total, not a mean of
  replicates, so it has no spread. A number there would be the silent
  substitution CLAUDE.md §7 is about, in the one metric whose value is not an
  average.
* **A single replicate prints nothing either.** An undefined spread is not the
  same fact as replicates that agreed exactly, and ``0.00`` is how the two
  became indistinguishable in the CSVs before ``_reduce_reps`` was fixed.
"""

from __future__ import annotations

import math
import types

import pytest

from review.domain.stats_compute import replicate_mean_and_spread
from review.features.heatmap import controller as HM
from review.features.heatmap.models import HeatmapLayout

_VAL = "gfp_mean_intensity"
_TP = 0.0

# (timepoint, mean, median, frac, n_above, n_total, …) — the AggPoint layout.
# The two wells differ in mean so the set has a real spread to measure; a pair
# that agreed would draw ``± 0`` under both the fixed and the broken code.
_POINTS = {
    "A01": [(_TP, 10.0, 0.0, 0.5, 5.0, 10, 0.0, 0.0, 0.0, 0.0)],
    "A02": [(_TP, 2.0, 0.0, 0.1, 9.0, 90, 0.0, 0.0, 0.0, 0.0)],
}


def _stats_ctx(*, use_sem: bool = True, err_kind: str = "sem"):
    """A ctx whose aggregator is the map above and whose wells are both loaded."""
    def _well(label, **_kw):
        return _POINTS[label]

    def _group(wells, **_kw):
        n_above = sum(_POINTS[w][0][4] for w in wells)
        den = sum(_POINTS[w][0][5] for w in wells)
        num = sum(_POINTS[w][0][1] * _POINTS[w][0][5] for w in wells)
        return [(_TP, num / den, 0.0, n_above / den, n_above, den,
                 0.0, 0.0, 0.0, 0.0)]

    return types.SimpleNamespace(
        state=types.SimpleNamespace(
            dataset=types.SimpleNamespace(well_paths={"A01": "a", "A02": "b"}),
            channel=types.SimpleNamespace(ratio_index={}, active_val_col=_VAL),
            plot=types.SimpleNamespace(
                use_fov_replicates=False, use_sem=use_sem,
                bar_err_kind=err_kind, bar_show_mode="mean_spread"),
            heatmap=types.SimpleNamespace(repset_avg=False),
            cell_gating=types.SimpleNamespace(
                use_scatter_thresh=False, use_scatter_gates=False,
                scatter_gates=[], thresh_choices=None, selected_gate=None),
            repo=None,
        ),
        runtime=types.SimpleNamespace(
            aggregate=types.SimpleNamespace(well=_well, group=_group),
        ),
    )


def _stat(ctx, wells, metric):
    return HM._cell_stat(ctx, wells, _TP, metric, _VAL, 0.0, {})


# ── what the number is ───────────────────────────────────────────────────────

def test_the_spread_is_the_sem_of_the_wells_the_cell_holds():
    """Two per-well means, 10 and 2: SEM is 5.657/√2 = 4.0 exactly."""
    value, spread = _stat(_stats_ctx(use_sem=True), ["A01", "A02"], HM.METRIC_MEAN)
    assert value == pytest.approx(6.0)
    assert spread == pytest.approx(4.0)


def test_the_sd_sem_choice_is_the_app_wide_one():
    """The same cell, with Properties → Statistics on SD: 5.657, not 4.0."""
    _value, spread = _stat(_stats_ctx(use_sem=False), ["A01", "A02"], HM.METRIC_MEAN)
    assert spread == pytest.approx(8.0 / math.sqrt(2.0))


def test_the_cell_agrees_with_the_reducer_the_bar_uses():
    """Not a reimplementation of the arithmetic — the same call, same answer."""
    from review.domain.stats_compute import group_replicate_stats

    ctx = _stats_ctx()
    reps = group_replicate_stats(ctx, ["A01", "A02"], _TP, use_fov=False,
                                 val_col=_VAL, threshold=0.0, fluor_gates={})
    expected = replicate_mean_and_spread(reps, "mean", use_sem=True)
    assert _stat(ctx, ["A01", "A02"], HM.METRIC_MEAN) == pytest.approx(expected)


def test_a_single_replicate_has_an_undefined_spread_not_a_zero_one():
    value, spread = _stat(_stats_ctx(), ["A01"], HM.METRIC_MEAN)
    assert value == pytest.approx(10.0)
    assert math.isnan(spread)


def test_the_cell_count_total_has_no_spread_at_all():
    """It is a sum over the set, not a mean of replicates — so there is none."""
    value, spread = _stat(_stats_ctx(), ["A01", "A02"], HM.METRIC_COUNT)
    assert value == pytest.approx(100.0)
    assert math.isnan(spread)


def test_a_cell_holding_no_loaded_well_reports_neither():
    value, spread = _stat(_stats_ctx(), ["H12"], HM.METRIC_MEAN)
    assert math.isnan(value) and math.isnan(spread)


# ── whether it reaches the figure ────────────────────────────────────────────

@pytest.fixture
def drawn(monkeypatch):
    """Draw a 1×2 grid whose two cells differ, and hand back the axes.

    Cell (0, 0) has a spread and cell (0, 1) has none. A renderer that printed
    a ``±`` on every cell and one that printed it on none both fail against
    this pair; a fixture where both cells looked alike could not tell either
    from the fixed code (CLAUDE.md §7.8 rule 2).
    """
    from matplotlib.figure import Figure

    _CELL = {(0, 0): (6.0, 4.0), (0, 1): (2.0, float("nan"))}

    def _draw(*, show_error, metric=HM.METRIC_MEAN, err_kind="sem"):
        fig = Figure()
        ax = fig.add_subplot(1, 1, 1)
        layout = HeatmapLayout(name="Pair", rows=1, cols=2)
        layout.assign(0, 0, ["A01", "A02"])
        layout.assign(0, 1, ["B01"])
        widgets = {
            "_heatmap_fig": fig, "_heatmap_ax": ax,
            "_heatmap_canvas": types.SimpleNamespace(draw_idle=lambda: None),
            "_heatmap_cax": None,
            "_heatmap_metric_cb": types.SimpleNamespace(currentText=lambda: metric),
            "_heatmap_tp_cb": types.SimpleNamespace(currentIndex=lambda: 0),
        }
        ctx = types.SimpleNamespace(
            window=None,
            state=types.SimpleNamespace(
                dataset=types.SimpleNamespace(
                    well_paths=dict.fromkeys(["A01", "A02", "B01"], "p")),
                channel=types.SimpleNamespace(
                    active_val_col=_VAL, active_channel="GFP",
                    active_metric="mean_intensity", ratio_index={}),
                plot=types.SimpleNamespace(
                    use_fov_replicates=False, use_sem=(err_kind != "sd"),
                    bar_err_kind=err_kind, bar_show_mode="mean_spread"),
                selection=types.SimpleNamespace(selected_wells=set(), variables=None),
                cell_gating=types.SimpleNamespace(
                    use_scatter_thresh=False, use_scatter_gates=False,
                    scatter_gates=[], thresh_choices=None, selected_gate=None),
                heatmap=types.SimpleNamespace(
                    tp_values=[_TP], scale_mode="Auto", vmin=None, vmax=None,
                    repset_avg=False, log_scale=False, show_error=show_error,
                    label_from_sample_map=False, active_layout_name="Pair"),
            ),
            runtime=types.SimpleNamespace(heatmap=types.SimpleNamespace(
                layouts=[layout], global_range_cache=None, label_drag=None,
                colorbar=None, global_vmin=None, global_vmax=None)),
            widgets=types.SimpleNamespace(get_or_none=widgets.get),
            notify=types.SimpleNamespace(set_status=lambda _m: None),
        )
        monkeypatch.setattr(HM, "current_focus_tp", lambda _c: None)
        monkeypatch.setattr(HM, "on_cutoff_for_column", lambda *_a, **_k: 0.0)
        monkeypatch.setattr(HM, "get_all_fluor_gates", lambda _c: {})
        monkeypatch.setattr(HM, "active_channel_label", lambda _c: "GFP")
        monkeypatch.setattr(HM, "apply_export_style_to_current", lambda *_a, **_k: None)
        monkeypatch.setattr(HM, "_cell_value", lambda _c, w, *_a, **_k: _CELL[
            (0, 0) if "A01" in list(w) else (0, 1)][0])

        def _stub_stat(_c, wells, *_a, **_k):
            key = (0, 0) if "A01" in list(wells) else (0, 1)
            v, e = _CELL[key]
            return v, (e if metric != HM.METRIC_COUNT else float("nan"))

        monkeypatch.setattr(HM, "_cell_stat", _stub_stat)
        HM.redraw_heatmap(ctx)
        return ax

    return _draw


def _texts(ax):
    return [t.get_text() for t in ax.texts]


def test_the_cell_with_a_spread_prints_it_and_the_cell_without_does_not(drawn):
    ax = drawn(show_error=True)
    assert [t for t in _texts(ax) if t.startswith("±")] == ["± 4.00"]


def test_the_toggle_off_prints_no_error_at_all(drawn):
    ax = drawn(show_error=False)
    assert [t for t in _texts(ax) if t.startswith("±")] == []


def test_the_values_and_well_names_survive_the_extra_line(drawn):
    """The ``±`` is added to the cell, it does not displace what was there."""
    texts = _texts(drawn(show_error=True))
    assert "6.00" in texts and "2.00" in texts
    assert "A01+1" in texts and "B01" in texts


def test_the_title_names_which_spread_the_marks_are(drawn):
    """SD and SEM differ by √n and look identical on the cell."""
    ax = drawn(show_error=True)
    assert ax.get_title().endswith("(± SEM)")


def test_a_grid_with_no_measurable_spread_does_not_claim_one_in_its_title(drawn):
    ax = drawn(show_error=True, metric=HM.METRIC_COUNT)
    assert "±" not in ax.get_title()
    assert [t for t in _texts(ax) if t.startswith("±")] == []


def test_the_three_lines_of_a_cell_do_not_land_on_each_other(drawn):
    """The ``±`` is given room; it does not print on top of the value.

    Asserting the strings are present passes just as well against a layout
    that stacks all three at the cell's centre — which is what a mutation
    setting both offsets to ``float(r)`` proves, and why this measures the
    positions instead (CLAUDE.md §7.8 rule 1).
    """
    ax = drawn(show_error=True)
    ys = {round(t.get_position()[1], 3) for t in ax.texts
          if t.get_position()[0] == 0}

    assert len(ys) == 3


def test_a_cell_without_an_error_keeps_the_two_line_layout_it_always_had(drawn):
    """Turning the toggle on must not shift the cells that print no error."""
    with_err = drawn(show_error=True)
    without = drawn(show_error=False)

    def _ys(ax, col):
        return sorted(round(t.get_position()[1], 3) for t in ax.texts
                      if t.get_position()[0] == col)

    # Column 1 has no measurable spread under either setting.
    assert _ys(with_err, 1) == _ys(without, 1)


# ── the interval is the one the bar chart draws ──────────────────────────────

def test_the_95_percent_ci_option_scales_the_cell_the_way_it_scales_the_bar(drawn):
    """``Properties → Statistics`` offers four kinds, and ``use_sem`` holds two.

    Reading the boolean alone drew a plain SEM under a panel set to 95% CI and
    titled it ``SEM`` — the same replicates, two intervals differing by 1.96,
    with neither figure saying so. ``core.plot_state.error_bar_settings`` is
    the one reading of that panel, and the bar's ``ci_scale`` and this come
    from it.
    """
    value, spread = _stat(_stats_ctx(err_kind="ci95"), ["A01", "A02"],
                          HM.METRIC_MEAN)

    assert value == pytest.approx(6.0)
    assert spread == pytest.approx(4.0 * 1.96)
    # And the figure says which interval it is, rather than calling it SEM.
    assert drawn(show_error=True, err_kind="ci95").get_title().endswith(
        "(± 95% CI)")


def test_asking_for_no_error_bars_silences_the_cell_annotation_too(drawn):
    """*Error bars: None* is a request, not a bar-tab-only preference."""
    ax = drawn(show_error=True, err_kind="none")

    assert [t for t in _texts(ax) if t.startswith("±")] == []
    assert "±" not in ax.get_title()


def test_the_sd_option_is_neither_scaled_nor_mislabelled(drawn):
    _value, spread = _stat(_stats_ctx(use_sem=False, err_kind="sd"),
                           ["A01", "A02"], HM.METRIC_MEAN)

    assert spread == pytest.approx(8.0 / math.sqrt(2.0))
    assert drawn(show_error=True, err_kind="sd").get_title().endswith("(± SD)")


# ── a spread never prints as a zero it is not ────────────────────────────────

@pytest.mark.parametrize("value,metric,fmt,expected", [
    # The automatic fraction format is two decimals, which flattens this.
    (0.004, HM.METRIC_FRACTION, "", "0.004"),
    # A user format can be coarser still.
    (0.004, HM.METRIC_MEAN, "%d", "0.004"),
    # A spread the chosen format renders honestly is left alone.
    (0.25, HM.METRIC_FRACTION, "", "0.25"),
    (12.5, HM.METRIC_MEAN, "", "12.50"),
    # A measured zero really is zero, and says so.
    (0.0, HM.METRIC_FRACTION, "", "0.00"),
])
def test_a_small_spread_is_never_rendered_as_zero(value, metric, fmt, expected):
    """``± 0.00`` for a real 0.004 is the conflation this whole feature removes.

    A cell with no measurable spread already prints nothing at all, so a ``±``
    reading zero on the grid is a claim that the replicates agreed exactly.
    """
    assert HM._format_cell_error(value, metric, fmt) == expected


def test_an_undefined_spread_still_prints_nothing():
    assert HM._format_cell_error(float("nan"), HM.METRIC_MEAN, "") == ""
