"""Fixing a batch figure's axes, dropping its panels, and the rows behind it.

Two additions, and the risk in each is the same shape: a control that is
offered, moved, and then not honoured by the export it appears to configure.

**The axes.** Every batch figure passes through
``core.plot_style.apply_export_style_prefs`` on its way to disk, and that pass
re-reads the *application's* export preferences. A limit stamped on the axes at
render time is therefore one autoscale away from being replaced by the app's,
silently — so the limits ride in the preferences that pass is handed and the
scale is pinned with the marker it checks first. What must be true, and is
asserted below rather than assumed: the overlay never mutates the app's own
preferences dictionary, an unfilled field never overwrites an app-wide one, and
a panel the user unticked is genuinely absent from the figure rather than drawn
empty.

**The rows.** The per-cell CSV describes one export group, and the scope it is
built from reads the *live* selections. The bar mode never enters the group
scope at all, so a writer trusting an ambient one would give every group's file
the same wells under a different group's name — a plausible file of the wrong
cells, which is worse than no file.
"""
from __future__ import annotations

import copy
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib  # noqa: E402
matplotlib.use("Agg")
from matplotlib.figure import Figure  # noqa: E402

from well_viewer.features.batch_export._panels import (  # noqa: E402
    AxesSpec, Panel, PanelAxis, figure_height, panel_axes, pin_scales,
)
from well_viewer.features.batch_export.bar_panel import (  # noqa: E402
    BarBatchExportPanel,
)
from well_viewer.features.batch_export.base_panel import (  # noqa: E402
    BatchExportPanel, _parse_limit, _scale_choice, _summarise_notes,
)
from well_viewer.features.batch_export.response_panel import (  # noqa: E402
    ResponseBatchExportPanel,
)
from well_viewer.features.batch_export.scatter_panel import (  # noqa: E402
    ScatterBatchExportPanel,
)

_LINE_PANELS = (Panel("mean", "Value"), Panel("frac", "Fraction"),
                Panel("n", "N"), Panel("cdf", "CDF"))


def _spec(**kw):
    panels = kw.pop("panels", None)
    if panels is None:
        panels = tuple((p.key, PanelAxis()) for p in _LINE_PANELS)
    return AxesSpec(panels=panels, **kw)


# ── the panels a run keeps ──────────────────────────────────────────────────

def test_every_panel_reaches_the_figure_by_default():
    fig = Figure()
    laid = panel_axes(fig, _LINE_PANELS, _spec(), share_x=("mean", "frac", "n"))
    assert laid.drawn_keys == ("mean", "frac", "n", "cdf")
    assert len(fig.axes) == 4
    assert laid.scratch is None
    assert all(ax in fig.axes for ax in laid.axes.values())


def test_an_unticked_panel_is_absent_from_the_figure_not_drawn_empty():
    """Asserted on ``fig.axes``, not on the returned bundle.

    The bundle carries an entry for every declared panel either way — the
    shared renderer paints all four members of its ``LineAxes`` — so a test
    that only checked the bundle would pass against an implementation that
    drew the panel and left it blank, which is the wrong answer this is for.
    """
    panels = tuple((p.key, PanelAxis(included=(p.key != "frac")))
                   for p in _LINE_PANELS)
    fig = Figure()
    laid = panel_axes(fig, _LINE_PANELS, _spec(panels=panels),
                      share_x=("mean", "frac", "n"))
    assert laid.drawn_keys == ("mean", "n", "cdf")
    assert len(fig.axes) == 3
    # The excluded panel still has somewhere to be painted, and it is not on
    # the figure that gets exported.
    assert laid.axes["frac"] is not None
    assert laid.axes["frac"] not in fig.axes
    assert laid.scratch is not None
    for key in ("mean", "n", "cdf"):
        assert laid.axes[key] in fig.axes


def test_the_kept_panels_are_laid_out_as_their_own_rows():
    """Three kept panels make a 3-row figure, not a 4-row one with a hole."""
    panels = tuple((p.key, PanelAxis(included=(p.key != "cdf")))
                   for p in _LINE_PANELS)
    fig = Figure()
    panel_axes(fig, _LINE_PANELS, _spec(panels=panels))
    rows = {ax.get_subplotspec().get_geometry()[0] for ax in fig.axes}
    assert rows == {3}, rows
    positions = sorted(ax.get_subplotspec().get_geometry()[2] for ax in fig.axes)
    assert positions == [0, 1, 2]


def test_x_sharing_falls_back_to_the_first_panel_that_survived():
    """Unticking the top panel must not leave the rest sharing an absent axis."""
    panels = tuple((p.key, PanelAxis(included=(p.key != "mean")))
                   for p in _LINE_PANELS)
    fig = Figure()
    laid = panel_axes(fig, _LINE_PANELS, _spec(panels=panels),
                      share_x=("mean", "frac", "n"))
    assert laid.axes["mean"] not in fig.axes
    # ``frac`` is now the base and ``n`` shares with it.
    assert laid.axes["n"].get_shared_x_axes().joined(
        laid.axes["n"], laid.axes["frac"])
    assert not laid.axes["n"].get_shared_x_axes().joined(
        laid.axes["n"], laid.axes["mean"])


def test_a_run_with_every_panel_unticked_is_refused_not_drawn_blank():
    spec = _spec(panels=tuple((p.key, PanelAxis(included=False))
                              for p in _LINE_PANELS))
    assert spec.draws_nothing() is True
    assert spec.drawn_keys() == ()
    # And a spec that keeps one is not refused, so the predicate is measuring
    # the selection rather than answering True for everything.
    kept = _spec(panels=tuple((p.key, PanelAxis(included=(p.key == "n")))
                              for p in _LINE_PANELS))
    assert kept.draws_nothing() is False
    # A mode that declares no panels at all is not "drawing nothing" either.
    assert AxesSpec().draws_nothing() is False


def test_the_figure_shrinks_with_the_panels_it_kept():
    assert figure_height(13.0, 4, 4) == pytest.approx(13.0)
    assert figure_height(13.0, 4, 2) == pytest.approx(6.5)
    assert figure_height(13.0, 4, 1) == pytest.approx(3.25)
    # Never below one panel's share, whatever it is handed.
    assert figure_height(13.0, 4, 0) == pytest.approx(3.25)


# ── the limits reach the style pass, and nothing else ───────────────────────

def test_the_overlay_never_mutates_the_app_s_own_preferences():
    """``_ensure_export_style_prefs`` hands back the live dictionary.

    Merged into in place, a batch run's numbers would be sitting in the Export
    Style sidebar afterwards, applied to every plot the user drew next.
    """
    base = {"x_lim_min": 1.0, "axis_buckets": {"1": {"y_lim_max": 9.0}}}
    before = copy.deepcopy(base)
    spec = _spec(x_min=0.0, x_max=5.0,
                 panels=(("mean", PanelAxis(y_min=2.0)),
                         ("frac", PanelAxis(y_max=1.0))))
    out = spec.prefs_overlay(base, ("mean", "frac"))
    assert base == before, "the app's preferences were modified"
    assert out is not base
    assert out["axis_buckets"] is not base["axis_buckets"]
    assert out["axis_buckets"]["1"] is not base["axis_buckets"]["1"]


def test_a_stacked_mode_writes_one_bucket_per_kept_panel_in_figure_order():
    """Keyed by the axes' position on the figure, which is what the style pass
    reads — so unticking a panel must renumber the ones after it."""
    spec = _spec(x_min=0.0,
                 panels=(("mean", PanelAxis(y_min=1.0, y_max=2.0)),
                         ("frac", PanelAxis(included=False, y_min=99.0)),
                         ("n", PanelAxis(y_min=3.0, y_max=4.0)),
                         ("cdf", PanelAxis())))
    out = spec.prefs_overlay({}, spec.drawn_keys())
    buckets = out["axis_buckets"]
    assert buckets["1"]["y_lim_min"] == 1.0 and buckets["1"]["y_lim_max"] == 2.0
    # ``n`` is the second row now, not the third.
    assert buckets["2"]["y_lim_min"] == 3.0 and buckets["2"]["y_lim_max"] == 4.0
    # The unticked panel's own number reaches nothing.
    assert not any(b.get("y_lim_min") == 99.0 for b in buckets.values())
    # The shared x goes on every kept panel.
    assert all(buckets[k]["x_lim_min"] == 0.0 for k in ("1", "2", "3"))


def test_a_single_panel_mode_writes_flat_preferences_so_every_facet_gets_them():
    """A faceted scatter draws one axes per group and they are one plot.

    A bucket on the first would leave the rest autoscaled while the user had
    asked for one range — so the single-panel form is flat, and scoped to All.
    """
    spec = AxesSpec(x_min=1.0, x_max=2.0,
                    panels=(("plot", PanelAxis(y_min=3.0, y_max=4.0)),))
    out = spec.prefs_overlay({"axis_target": "2"}, ("plot",))
    assert out["axis_target"] == "All"
    assert (out["x_lim_min"], out["x_lim_max"]) == (1.0, 2.0)
    assert (out["y_lim_min"], out["y_lim_max"]) == (3.0, 4.0)
    assert out["axis_buckets"] == {}


def test_an_unfilled_field_leaves_the_app_wide_preference_alone():
    """Blank is a question the user did not answer, not an answer of "none"."""
    base = {"y_lim_min": 7.0, "y_lim_max": 8.0, "x_lim_min": 6.0}
    out = AxesSpec(panels=(("plot", PanelAxis()),)).prefs_overlay(base, ("plot",))
    assert out["y_lim_min"] == 7.0 and out["y_lim_max"] == 8.0
    assert out["x_lim_min"] == 6.0
    # And a filled one does replace it, so the test above is measuring the
    # blank rather than an overlay that writes nothing at all.
    filled = AxesSpec(panels=(("plot", PanelAxis(y_min=1.0)),))
    assert filled.prefs_overlay(base, ("plot",))["y_lim_min"] == 1.0


# ── the scale is pinned where the style pass looks ──────────────────────────

def test_a_pinned_scale_is_stamped_where_the_style_pass_reads_it():
    """``_aw_force_*log`` is what ``apply_export_style_prefs`` checks before it
    consults the shared ``x_log`` — so a value set anywhere else is one restyle
    away from being replaced by the app's."""
    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    spec = AxesSpec(x_log=True, panels=(("plot", PanelAxis(y_log=False)),))
    pin_scales([("plot", ax)], spec)
    assert ax._aw_force_xlog is True
    assert ax._aw_force_ylog is False


def test_an_auto_scale_pins_nothing_so_a_derived_axis_keeps_its_own():
    """The Response tab's dose axis derives log from the doses.

    Pinning ``False`` for a user who never touched the control would take it
    linear — which is why Auto is ``None`` and not ``False``.
    """
    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    pin_scales([("plot", ax)], AxesSpec(panels=(("plot", PanelAxis()),)))
    assert not hasattr(ax, "_aw_force_xlog")
    assert not hasattr(ax, "_aw_force_ylog")


def test_one_panel_key_pins_every_axes_it_names():
    """A faceted scatter repeats its key across its facets.

    Passed as a mapping they would collapse to one entry and the rest would go
    unpinned while the user had asked for one scale — hence pairs.
    """
    fig = Figure()
    axes = [fig.add_subplot(3, 1, i) for i in (1, 2, 3)]
    spec = AxesSpec(x_log=True, panels=(("plot", PanelAxis(y_log=True)),))
    pin_scales([("plot", ax) for ax in axes], spec)
    assert all(ax._aw_force_xlog is True for ax in axes)
    assert all(ax._aw_force_ylog is True for ax in axes)


def test_the_style_pass_really_honours_what_is_pinned_and_handed():
    """End to end through the real ``apply_export_style_prefs``.

    The two mechanisms are only worth using if that function reads them, and
    reading its source is not the same as running it.
    """
    from well_viewer.core.plot_style import apply_export_style_prefs

    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    ax.plot([1, 2, 3], [10, 20, 30])
    spec = AxesSpec(x_log=True,
                    panels=(("plot", PanelAxis(y_min=5.0, y_max=50.0)),))
    pin_scales([("plot", ax)], spec)
    # The app's own preferences say linear and a different range; the panel's
    # must win, because it is the panel the user pressed Run on.
    app_prefs = {"x_log": False, "y_lim_min": 999.0, "y_lim_max": 1000.0}
    apply_export_style_prefs(fig, spec.prefs_overlay(app_prefs, ("plot",)))
    assert ax.get_xscale() == "log"
    assert ax.get_ylim() == pytest.approx((5.0, 50.0))


# ── the fields ──────────────────────────────────────────────────────────────

def test_a_blank_or_unreadable_bound_autoscales_rather_than_inventing_one():
    assert _parse_limit("12.5") == 12.5
    assert _parse_limit("  ") is None
    assert _parse_limit("") is None
    assert _parse_limit("1OO") is None
    assert _parse_limit("-3") == -3.0


def test_the_scale_combo_reads_auto_as_unset(qapp):
    from PySide6.QtWidgets import QComboBox

    combo = QComboBox()
    for label in ("Auto", "Linear", "Log"):
        combo.addItem(label)
    combo.setCurrentIndex(0)
    assert _scale_choice(combo) is None
    combo.setCurrentIndex(1)
    assert _scale_choice(combo) is False
    combo.setCurrentIndex(2)
    assert _scale_choice(combo) is True
    assert _scale_choice(None) is None


def test_a_run_s_shortfalls_are_counted_rather_than_all_printed():
    assert _summarise_notes([]) == ""
    assert _summarise_notes(["A failed"]) == "A failed"
    out = _summarise_notes(["A failed", "B failed", "C failed"])
    assert out.startswith("A failed")
    assert "2 more" in out


# ── every mode declares panels and a cell scope ─────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_every_mode_declares_the_panels_its_figure_actually_draws():
    """Counted against the figure builder's own subplot count.

    Inherited silently, a mode would offer the line mode's four rows over a
    three-panel figure and its third bucket would land on an axes that is not
    there.
    """
    assert [p.key for p in BatchExportPanel._PANELS] == [
        "mean", "frac", "n", "cdf"]
    assert [p.key for p in BarBatchExportPanel._PANELS] == ["mean", "frac", "n"]
    assert [p.key for p in ScatterBatchExportPanel._PANELS] == ["plot"]
    assert [p.key for p in ResponseBatchExportPanel._PANELS] == ["plot"]


def test_every_mode_names_the_cell_scope_of_its_own_source_tab():
    """The per-cell file must describe the population the mode's tab draws.

    A mode reading another tab's scope would produce a file that is internally
    consistent and about a different plot.
    """
    from importlib import import_module

    expected = {
        BatchExportPanel: ("Line Graphs", "lineplot"),
        BarBatchExportPanel: ("Bar Plots", "barplot"),
        ScatterBatchExportPanel: ("Scatter Plot", "scatter"),
        ResponseBatchExportPanel: ("Response Curves", "response"),
    }
    for cls, (tab, feature) in expected.items():
        assert cls._SOURCE_TAB == tab
        assert cls._CELL_SCOPE == (
            f"well_viewer.features.{feature}.cell_scope_compute")
        # And the module really exposes the callable, so a typo in the path is
        # a failure here rather than at the end of somebody's export.
        assert callable(import_module(cls._CELL_SCOPE).cell_scope)
