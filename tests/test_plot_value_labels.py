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


@pytest.fixture(autouse=True)
def _close_figures():
    """These tests measure drawn figures, so they cannot close as they go.
    Closing after each keeps the file off the fast lane's figure-leak radar."""
    yield
    plt.close("all")

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


def test_a_whole_mean_and_a_whole_spread_both_read_as_counts():
    assert format_value_label(310.0, 22.0) == "310 ± 22"


@pytest.mark.parametrize("value,spread,expected", [
    # A two-well replicate set counting 27 and 33 events: mean exactly 30,
    # SD 4.2426. Formatting the spread with the mean's own "%d" *truncated* it
    # to 4 — a label understating the whisker drawn beneath it by 43 %.
    (30.0, 4.2426, "30 ± 4.24"),
    (12.0, 2.3094, "12 ± 2.31"),
    (10.0, 1.5, "10 ± 1.50"),
    (3.0, 1.6, "3 ± 1.60"),
])
def test_the_spread_is_formatted_from_its_own_magnitude(value, spread, expected):
    assert format_value_label(value, spread) == expected


def test_a_spread_the_mean_s_format_would_round_away_keeps_its_own():
    # With a format the user typed, both halves take it — except where that
    # rounds the spread to a measured zero, which it is not.
    label = format_value_label(1200.0, 0.4, fmt="%d")
    assert label != "1200 ± 0", "a small spread was printed as a measured zero"
    assert label == "1200 ± 0.400"


def test_the_zero_rescue_survives_a_format_carrying_units():
    # The rescue used to be guarded by ``float(tail)``, which raises on a
    # unit-bearing string — so it did nothing for exactly the formats most
    # likely to round a spread away.
    assert format_value_label(1200.0, 0.4, fmt="%.0f nM") == "1200 nM ± 0.400"


def test_a_users_format_is_honoured_on_both_halves():
    # An explicit format is a request for the pair to read alike, so unlike the
    # automatic case it governs the spread too.
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


#: Roughly the Bar tab's own figure. The default 6.4×4.8 leaves each of three
#: stacked panels about 68 px tall, where a label's 7-point offset plus its cap
#: height is a quarter of the panel — so a test that asked whether the text
#: landed inside would be measuring the fixture, not the renderer.
_BAR_FIGSIZE = (6.0, 9.0)


def _render_bars(items, value_labels, *, ctx=None, figsize=None):
    fig, (axm, axf, axn) = plt.subplots(3, 1, figsize=figsize or _BAR_FIGSIZE)
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


def test_the_value_label_does_not_land_on_the_n_a_mark():
    """Both are drawn at the bar top, at the same offset, alignment and point
    size — so at one shared offset they are pixel-identical and neither can be
    read. A single well with no replicate set has an undefined spread on every
    bar, which is the commonest selection there is, so this is the default
    case, not an edge one."""
    item = _item(spread=float("nan"), frac_spread=float("nan"),
                 n_above_spread=float("nan"))
    axes = _render_bars([item], (True, ""))
    fig = axes[0].figure
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    for name, ax in zip(("mean", "frac", "events"), axes):
        boxes = [t.get_window_extent(r) for t in ax.texts]
        assert len(boxes) == 2, f"{name}: expected an n/a mark and a value label"
        assert not boxes[0].overlaps(boxes[1]), (
            f"{name} panel: the n/a mark and the value label are drawn on top "
            f"of each other ({boxes[0]} vs {boxes[1]})")


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


def _negative_items():
    """Bars below the axis — what a subtractive fold change produces."""
    return [_item("A01", mean=-3.0, spread=0.5, frac=-0.4, frac_spread=0.05,
                  n_above=-2.0, n_above_spread=0.3),
            _item("A02", mean=-1.0, spread=0.2, frac=-0.2, frac_spread=0.02,
                  n_above=-1.0, n_above_spread=0.1)]


def test_headroom_is_never_a_crop_on_a_negative_panel():
    """``hi = max(tops) * headroom`` moves the ceiling *down* when every top is
    negative, so asking for more room gave less of it. The pad is added to the
    span instead."""
    off_m, _, _ = _render_bars(_negative_items(), (False, ""))
    on_m, _, _ = _render_bars(_negative_items(), (True, ""))
    assert on_m.get_ylim()[1] >= off_m.get_ylim()[1], (
        f"labels on cropped the panel: off={off_m.get_ylim()}, on={on_m.get_ylim()}")


def test_a_negative_bar_s_label_is_inside_the_panel():
    """It is drawn *below* the bar, so the room it needs is at the floor — the
    top padding that serves every positive bar does nothing for it."""
    axes = _render_bars(_negative_items(), (True, ""))
    fig = axes[0].figure
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    for name, ax in zip(("mean", "frac", "events"), axes):
        box = ax.get_window_extent(r)
        for t in ax.texts:
            tb = t.get_window_extent(r)
            assert tb.y0 >= box.y0, (
                f"{name} panel: {t.get_text()!r} is drawn below the axes")


def test_a_label_turns_inward_under_a_pinned_y_limit(monkeypatch):
    """``apply_export_style_to_current`` re-applies the user's own y-limit
    after the draw, and these labels carry ``annotation_clip=False`` — so an
    outside label ends up over the panel title rather than clipped. The same
    ceiling the significance brackets already stack under."""
    import well_viewer.core.plot_style as ps
    from well_viewer.core.plot_style import DEFAULT_EXPORT_STYLE_PREFS

    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["axis_buckets"] = {"1": {"y_lim_max": "1250"}}
    monkeypatch.setattr(ps, "figure_prefs", lambda _c, _f: prefs)

    axm, _, _ = _render_bars([_item()], None, ctx=_StubCtx())
    ps.apply_export_style_prefs(axm.figure, prefs)
    axm.figure.canvas.draw()
    r = axm.figure.canvas.get_renderer()
    (label,) = axm.texts
    assert label.get_window_extent(r).y1 <= axm.get_window_extent(r).y1, (
        "the label was drawn above a panel the user had pinned")


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


def _render_line(value_labels, fmt="", *, fc_active=False, series=None):
    from well_viewer.features.lineplot.controller import LineAxes, render_line_series

    fig, (axm, axf, axn, axc) = plt.subplots(4, 1, figsize=(6.0, 12.0))
    render_line_series(
        LineAxes(axm, axf, axn, axc), series or _line_series(),
        fov_active=False, fc_active=fc_active, threshold=1.0,
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


def test_the_line_fraction_panel_gets_room_under_fold_change():
    """With fold change off the panel's room comes from its own clamp. With it
    on, the clamp does not run — and nothing else was widening this panel, so
    the topmost label was drawn over the title while the Bar tab's Fraction
    panel, drawing the same field, had room."""
    import numpy as np
    from well_viewer.features.lineplot.controller import LinePoint, LineSeries

    pts = [LinePoint(t=0.0, mean=1.0, spread=0.1, frac=1.2, n_above=10.0,
                     n_total=100, frac_spread=0.1, n_above_spread=1.0),
           LinePoint(t=4.0, mean=2.5, spread=0.2, frac=2.2, n_above=30.0,
                     n_total=100, frac_spread=0.2, n_above_spread=2.0)]
    series = [LineSeries(key="A01", display="A01", color="#4488cc", kind="well",
                         wells=["A01"], points=pts, cdf_vals=np.array([1.0, 2.0]))]

    _, off_f, _, _ = _render_line(False, fc_active=True, series=series)
    _, on_f, _, _ = _render_line(True, fc_active=True, series=series)
    assert on_f.get_ylim()[1] > off_f.get_ylim()[1], (
        f"no room reserved: off={off_f.get_ylim()}, on={on_f.get_ylim()}")
    fig = on_f.figure
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    top = on_f.get_window_extent(r).y1
    for t in on_f.texts:
        assert t.get_window_extent(r).y1 <= top, (
            f"{t.get_text()!r} is drawn above the Fraction panel")


def test_the_fraction_panel_is_not_capped_where_a_label_would_be_clipped():
    """A bar sitting at a fraction of 1.0 must have room above the 1.05 clamp
    for its own label.

    Measured on the drawn figure, not asserted about the constant: reading
    ``LABEL_FRAC_CAP > 1.05`` passes just as happily against a renderer that
    never applies it, which is what the first version of this test did.
    """
    tall = _item(frac=1.0, frac_spread=0.0)
    _, off_f, _ = _render_bars([tall], (False, ""))
    _, on_f, _ = _render_bars([tall], (True, ""))
    assert on_f.get_ylim()[1] > off_f.get_ylim()[1], (
        f"the Fraction panel reserved no room for its label "
        f"(off={off_f.get_ylim()}, on={on_f.get_ylim()})")
    (label,) = on_f.texts
    fig = on_f.figure
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    assert label.get_window_extent(r).y1 <= on_f.get_window_extent(r).y1, (
        "the label on a bar at fraction 1.0 is drawn above the panel")


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
