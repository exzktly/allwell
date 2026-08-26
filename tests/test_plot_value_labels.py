"""The ``mean ± spread`` value-label overlay.

The overlay is off until the Properties sidebar's Overlays section turns it on,
and what it prints has to say exactly as much as was measured — no more. These
tests pin both halves: the formatting rules in
``core/plot_annotations.py``, and the fact that the labels actually reach the
axes the Bar and Line renderers draw.

The assertions are on ``ax.texts`` rather than on the formatter's return value.
That is the point: the formatter being right is not evidence that a label was
drawn, and every previous version of this mistake in this codebase (a colour
computed correctly and never pushed to the plate; a control offered in a combo
and never selected) passed a test of the value while the screen stayed wrong.
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("matplotlib")

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from well_viewer.core.plot_annotations import (  # noqa: E402
    LABEL_HEADROOM,
    format_value_label,
    value_label_settings,
)
from well_viewer.core.plot_state import PlotState  # noqa: E402
from well_viewer.features.barplot.controller import BarItem, render_bar_items  # noqa: E402


# ── defaults ─────────────────────────────────────────────────────────────────

def test_the_overlay_is_off_by_default():
    plot = PlotState()
    assert plot.show_value_labels is False
    assert plot.value_label_format == ""


def test_a_renderer_with_no_ctx_reads_the_overlay_as_off():
    # The batch-export bar path renders with ctx=None on purpose. A surface
    # that has not been taught about the overlay must not draw one by accident.
    assert value_label_settings(None) == (False, "")


# ── formatting ───────────────────────────────────────────────────────────────

def test_an_unprintable_value_yields_no_label():
    assert format_value_label(float("nan")) == ""
    assert format_value_label(float("inf")) == ""
    assert format_value_label(None) == ""


@pytest.mark.parametrize("spread", [None, float("nan"), 0.0, -1.0])
def test_a_spread_that_was_not_measured_is_not_printed_as_zero(spread):
    # "± 0" over a bar whose spread was never computed says the replicates
    # agreed. That is the confusion the whole n/a mark exists to prevent.
    label = format_value_label(12.5, spread)
    assert "±" not in label, f"spread={spread!r} printed a ± in {label!r}"
    assert label == "12.50"


def test_a_measured_spread_is_printed_with_the_mean():
    assert format_value_label(12.5, 1.25) == "12.50 ± 1.25"


def test_both_halves_share_the_mean_s_format():
    # 310 is whole, so the count reads as a count — and so does its spread.
    assert format_value_label(310.0, 22.0) == "310 ± 22"


def test_a_spread_the_mean_s_format_would_round_away_keeps_its_own():
    # 1200 is whole, so the mean's format is "%d" and the spread would print as
    # "0" — a measured zero, which it is not.
    label = format_value_label(1200.0, 0.4)
    assert label != "1200 ± 0", "a small spread was printed as a measured zero"
    assert label == "1200 ± 0.400"


def test_a_users_format_is_honoured():
    assert format_value_label(12.5, 1.25, fmt="%.1f") == "12.5 ± 1.2"


def test_a_nonsense_format_still_prints_the_number():
    # The format comes straight from a text field, so it can be anything. It
    # must not take the plot down, and it must not silently swallow the value.
    label = format_value_label(12.5, fmt="%q")
    assert label == "12.50"


# ── the bar panels ───────────────────────────────────────────────────────────

def _item(key="A01", *, mean=1200.0, spread=85.0, has_mean=True,
          frac=0.42, frac_spread=0.05, has_frac=True,
          n_above=310.0, n_above_spread=22.0):
    return BarItem(
        key=key, display=key, color="#888888",
        mean=mean, spread=spread, has_mean=has_mean,
        frac=frac, frac_spread=frac_spread, has_frac=has_frac,
        n_above=n_above, n_above_spread=n_above_spread,
    )


def _render_bars(items, value_labels, *, ctx=None):
    fig, (axm, axf, axn) = plt.subplots(3, 1)
    render_bar_items(
        ax_mean=axm, ax_frac=axf, ax_n=axn, use_groups=False, items=items,
        threshold=1.0, warn_color="#ff0000", border_color="#333333",
        placeholder_color="#888888", disabled_well_color="#555555",
        err_bar_color="#000000", value_labels=value_labels, ctx=ctx,
    )
    return axm, axf, axn


def _texts(ax):
    return [t.get_text() for t in ax.texts]


def test_the_bars_carry_no_labels_when_the_overlay_is_off():
    axm, axf, axn = _render_bars([_item()], (False, ""))
    assert _texts(axm) == [] and _texts(axf) == [] and _texts(axn) == []


def test_every_bar_panel_is_labelled_when_the_overlay_is_on():
    axm, axf, axn = _render_bars([_item()], (True, ""))
    assert _texts(axm) == ["1200 ± 85"]
    assert _texts(axf) == ["0.420 ± 0.050"]
    assert _texts(axn) == ["310 ± 22"]


def test_a_bar_with_no_computable_spread_is_labelled_with_its_mean_alone():
    axm, _, _ = _render_bars([_item(spread=float("nan"))], (True, ""))
    # The n/a mark is drawn by the same pass; the value label is the other one.
    labels = [t for t in _texts(axm) if t != "n/a"]
    assert labels == ["1200"], f"got {_texts(axm)!r}"


def test_a_placeholder_bar_gets_no_label():
    # Nothing was computable, so the column is the dashed placeholder. A number
    # over it would be the one bar whose text and shape disagree.
    axm, _, _ = _render_bars(
        [_item(mean=float("nan"), spread=0.0, has_mean=False)], (True, ""))
    assert [t for t in _texts(axm) if t != "n/a"] == []


def test_a_users_format_reaches_the_bars():
    axm, _, _ = _render_bars([_item()], (True, "%.1e"))
    assert _texts(axm) == ["1.2e+03 ± 8.5e+01"]


class _StubPlot:
    show_value_labels = True
    value_label_format = ""
    bar_err_kind = "sem"
    bar_show_mode = "mean"          # "Mean" — bars only, no error bars
    use_fov_replicates = False
    bar_group_mode = "well"
    bar_color_mode = "plate"
    show_threshold_line = False
    fold_change_active = False


class _StubState:
    plot = _StubPlot()


class _StubBar:
    xs: list = []
    xs_keys: list = []


class _StubRuntime:
    barplot = _StubBar()


class _StubCtx:
    state = _StubState()
    runtime = _StubRuntime()


def test_a_label_states_no_spread_when_the_panel_draws_no_error_bar():
    # Properties → Statistics → Show: "Mean". The whisker is gone, so a label
    # claiming a ± would describe a plot other than the one on screen.
    axm, axf, axn = _render_bars([_item()], None, ctx=_StubCtx())
    assert _texts(axm) == ["1200"]
    assert _texts(axf) == ["0.420"]
    assert _texts(axn) == ["310"]


def test_the_panel_makes_room_for_the_labels():
    off_m, _, _ = _render_bars([_item()], (False, ""))
    on_m, _, _ = _render_bars([_item()], (True, ""))
    off_hi = off_m.get_ylim()[1]
    on_hi = on_m.get_ylim()[1]
    assert on_hi > off_hi, (
        "the labels are drawn above the error cap; without extra headroom the "
        f"tallest bar's label lands on the title (off={off_hi}, on={on_hi})"
    )
    # A widening, never a narrowing — the data must still fit.
    assert on_hi >= (1200.0 + 85.0) * LABEL_HEADROOM * 0.99


# ── the line panels ──────────────────────────────────────────────────────────

def _line_series():
    import numpy as np
    from well_viewer.features.lineplot.controller import LinePoint, LineSeries

    pts = [
        LinePoint(t=0.0, mean=100.0, spread=8.0, frac=0.2, n_above=30.0,
                  n_total=100, frac_spread=0.03, n_above_spread=4.0),
        LinePoint(t=4.0, mean=250.0, spread=0.0, frac=0.6, n_above=90.0,
                  n_total=150, frac_spread=0.0, n_above_spread=0.0),
    ]
    return [LineSeries(key="A01", display="A01", color="#4488cc", kind="well",
                       wells=["A01"], points=pts,
                       cdf_vals=np.array([1.0, 2.0, 3.0]))]


def _render_line(value_labels, fmt=""):
    from well_viewer.features.lineplot.controller import LineAxes, render_line_series

    fig, (axm, axf, axn, axc) = plt.subplots(4, 1)
    render_line_series(
        LineAxes(axm, axf, axn, axc), _line_series(),
        fov_active=False, fc_active=False, threshold=1.0,
        legend_kw={"fontsize": 8}, warn_color="#ff0000", muted_fg="#333333",
        value_labels=value_labels, value_label_format=fmt,
    )
    return axm, axf, axn, axc


def test_the_traces_carry_no_labels_when_the_overlay_is_off():
    axm, axf, axn, axc = _render_line(False)
    assert _texts(axm) == [] and _texts(axf) == [] and _texts(axn) == []


def test_every_time_panel_is_labelled_when_the_overlay_is_on():
    axm, axf, axn, axc = _render_line(True)
    assert _texts(axm) == ["100 ± 8", "250"]
    assert _texts(axf) == ["0.200 ± 0.030", "0.600"]
    assert _texts(axn) == ["30 ± 4", "90"]


def test_the_cdf_panel_takes_no_value_labels():
    # Its points are one per cell, not a mean of anything — there is no spread
    # to state and one label per cell would bury the curve.
    _, _, _, axc = _render_line(True)
    assert _texts(axc) == []


def test_the_line_panels_make_room_for_the_labels():
    off_m, _, _, _ = _render_line(False)
    on_m, _, _, _ = _render_line(True)
    assert on_m.get_ylim()[1] > off_m.get_ylim()[1]


def test_the_fraction_panel_is_not_capped_where_a_label_would_be_clipped():
    from well_viewer.core.plot_annotations import LABEL_FRAC_CAP

    assert LABEL_FRAC_CAP > 1.05, (
        "a trace at a fraction of 1.0 needs room above the 1.05 clamp for its "
        "own label, or the text lands on the panel title"
    )


# ── persistence ──────────────────────────────────────────────────────────────

@pytest.mark.qt
def test_the_overlay_round_trips_through_view_state(loaded_app):
    from well_viewer.persistence import view_state as vs

    app = loaded_app
    app._app_state.plot.show_value_labels = True
    app._app_state.plot.value_label_format = "%.1f"
    snap = vs.snapshot(app._ctx)
    assert snap.get("show_value_labels") is True
    assert snap.get("value_label_format") == "%.1f"

    app._app_state.plot.show_value_labels = False
    app._app_state.plot.value_label_format = ""
    vs.restore(app._ctx, snap)
    assert app._app_state.plot.show_value_labels is True
    assert app._app_state.plot.value_label_format == "%.1f"


def test_a_new_dataset_keeps_the_overlay():
    # Presentation preferences survive a plate change; only settings that name
    # *this* plate's wells or timepoints are reset.
    plot = PlotState()
    plot.show_value_labels = True
    plot.value_label_format = "%.2f"
    plot.reset_for_new_dataset()
    assert plot.show_value_labels is True
    assert plot.value_label_format == "%.2f"


def test_the_anchor_clears_a_negative_bar_downwards():
    # A subtractive fold change puts bars below the axis. The label must go
    # below them, not through them.
    from well_viewer.core.plot_annotations import annotate_value

    fig, ax = plt.subplots()
    annotate_value(ax, 0, -5.0, 1.0, anchor=-6.0)
    (ann,) = ax.texts
    assert ann.get_va() == "top"
    assert ann.xyann[1] < 0
    assert math.isclose(ann.xy[1], -6.0)
