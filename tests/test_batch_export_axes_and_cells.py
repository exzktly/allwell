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
from pathlib import Path
from types import SimpleNamespace

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


# ── the shared X row governs only the panels that share an x ────────────────

def test_the_shared_x_row_skips_a_panel_on_a_different_axis():
    """The line mode's CDF plots intensity where its siblings plot time.

    An X row filled in hours was clipping it to 0–48 intensity units — three
    correct panels and a fourth that looks empty, with nothing saying why.
    """
    spec = _spec(x_min=0.0, x_max=48.0,
                 panels=tuple((p.key, PanelAxis()) for p in _LINE_PANELS))
    out = spec.prefs_overlay({}, spec.drawn_keys(),
                             x_keys=("mean", "frac", "n"))
    buckets = out["axis_buckets"]
    for index in ("1", "2", "3"):
        assert buckets[index]["x_lim_min"] == 0.0
        assert buckets[index]["x_lim_max"] == 48.0
    assert "x_lim_min" not in buckets["4"], buckets["4"]
    assert "x_lim_max" not in buckets["4"]
    # …and the CDF still takes its own Y, so the exclusion is about x alone.
    y_spec = _spec(x_min=0.0,
                   panels=(("mean", PanelAxis()), ("frac", PanelAxis()),
                           ("n", PanelAxis()), ("cdf", PanelAxis(y_max=1.0))))
    y_out = y_spec.prefs_overlay({}, y_spec.drawn_keys(),
                                 x_keys=("mean", "frac", "n"))
    assert y_out["axis_buckets"]["4"]["y_lim_max"] == 1.0


def test_the_x_scale_is_not_pinned_on_a_panel_the_x_row_does_not_govern():
    fig = Figure()
    axes = [fig.add_subplot(4, 1, i) for i in (1, 2, 3, 4)]
    spec = _spec(x_log=True)
    pin_scales(list(zip(("mean", "frac", "n", "cdf"), axes)), spec,
               x_keys=("mean", "frac", "n"))
    assert all(ax._aw_force_xlog is True for ax in axes[:3])
    assert not hasattr(axes[3], "_aw_force_xlog")


def test_every_mode_says_which_panels_its_x_row_governs():
    """``None`` means all of them, which is right for a single-axes mode."""
    assert BatchExportPanel._X_PANELS == ("mean", "frac", "n")
    assert "cdf" not in (BatchExportPanel._X_PANELS or ())
    assert BarBatchExportPanel._X_PANELS == ("mean", "frac", "n")
    assert ScatterBatchExportPanel._X_PANELS is None
    assert ResponseBatchExportPanel._X_PANELS is None


# ── the rows behind a figure ────────────────────────────────────────────────

def _scope(wells, *, plot="line", val_col="gfp_mean_intensity"):
    from well_viewer.features.export.cell_scope_compute import CellScope

    return CellScope(plot=plot, val_col=val_col, ratios={}, threshold=0.0,
                     fluor_gates={}, wells=tuple(wells))


def _frame(n, well):
    import pandas as pd

    return pd.DataFrame({
        "gfp_mean_intensity": [10.0 * (i + 1) for i in range(n)],
        "well": [well] * n,
        "Included": [1] * n,
        "QC_Pass": [1] * n,
        "Crop_Pass": [1] * n,
        "Gate_Pass": [1] * n,
    })


def _cell_ctx(frames):
    """A ctx with just what ``write_cell_rows`` reads.

    Including ``runtime.cell_gating.worker``, which the shared gating-busy
    predicate consults: a stub without it would have the guard raise on every
    call, and a guard that raises is not a guard that refuses.
    """
    return SimpleNamespace(
        runtime=SimpleNamespace(cell_gating=SimpleNamespace(worker=None)),
        state=SimpleNamespace(
            dataset=SimpleNamespace(
                well_paths={w: f"/tmp/{w}" for w in frames},
                rows_version=1, data_dir="/tmp/ds", crop_region=None),
            selection=SimpleNamespace(selections=[]),
            cell_gating=SimpleNamespace(
                thresh_frac_on={}, thresh_count_on={}, scatter_gates=None,
                use_scatter_gates=False, gating_choices=None,
                thresh_choices=None, use_scatter_thresh=False),
        ),
    )


def test_the_per_cell_file_names_the_wells_its_scope_asked_for(tmp_path,
                                                               monkeypatch):
    """The whole point of the file: it has to be about this group.

    Asserted on the rows and on the ``#`` block, not on the call happening —
    the wrong implementation that passes a "did it run" test is one that
    exports the plot on screen under this group's name.
    """
    import well_viewer.features.batch_export._cell_rows as cr

    frames = {"A01": _frame(3, "A01"), "A02": _frame(2, "A02")}
    monkeypatch.setattr(cr, "get_rows", lambda ctx, w: frames[w], raising=False)
    monkeypatch.setattr("well_viewer.data.data_loading.get_rows",
                        lambda ctx, w: frames[w])
    monkeypatch.setattr(cr, "_well_labels_map", lambda ctx: {})
    monkeypatch.setattr(cr, "_scope_block",
                        lambda ctx, scope, **kw: [
                            f"# wells ({len(kw['wells'])}): "
                            + ", ".join(w for _g, w in kw["wells"])])

    out = tmp_path / "batch_G_cells.csv"
    rows = cr.write_cell_rows(_cell_ctx(frames), _scope(
        [("G", "A01"), ("G", "A02")]), out)

    assert rows == 5, rows
    text = out.read_text()
    assert text.splitlines()[0] == "# wells (2): A01, A02"
    body = [ln for ln in text.splitlines() if not ln.startswith("#")]
    assert len(body) == 6, body            # header + five cells
    assert "well_label" in body[0]
    assert body[0].count(",") >= 5


def test_a_scope_naming_no_loaded_well_writes_nothing_and_says_so(tmp_path):
    from well_viewer.features.batch_export import _cell_rows as cr

    out = tmp_path / "batch_G_cells.csv"
    assert cr.write_cell_rows(_cell_ctx({}), _scope([("G", "Z99")]), out) == -1
    assert not out.exists(), "a file was written for a group with no wells"
    assert cr.write_cell_rows(_cell_ctx({}), None, out) == -1


def test_the_scope_overrides_carry_this_run_s_column_and_timepoints():
    """A batch run overrides the tab's column and ticked timepoints.

    Read unmodified, the file described the plot on screen — filed beside, and
    named for, a figure of something else. Asserted on the built ``CellScope``,
    because a kwargs dict that is assembled and never passed would satisfy a
    test of the dict alone.
    """
    panel = _ScopeStub(BatchExportPanel)
    assert panel._cell_scope_kwargs(val_col="mcherry_total_intensity",
                                    timepoints=[24.0, 48.0]) == {
        "val_col": "mcherry_total_intensity", "timepoints": (24.0, 48.0)}
    # Nothing overridden is an empty dict, so the tab's own answer stands.
    assert panel._cell_scope_kwargs(val_col=None, timepoints=None) == {}

    from well_viewer.features.barplot.cell_scope_compute import cell_scope

    scope = cell_scope(_scope_ctx(), val_col="mcherry_total_intensity",
                       timepoints=(24.0, 48.0))
    assert scope.val_col == "mcherry_total_intensity"
    assert scope.timepoints == (24.0, 48.0)


def test_the_scatter_modes_build_two_different_scopes():
    """The aggregate page is a different plot — different markers, a different
    gate layer — and reading the per-cell scope for it wrote a file whose own
    block said it was about the other page."""
    cells = ScatterBatchExportPanel.__new__(ScatterBatchExportPanel)
    cells._scatter_mode = "cells"
    agg = ScatterBatchExportPanel.__new__(ScatterBatchExportPanel)
    agg._scatter_mode = "aggregate"
    assert cells._CELL_SCOPE_FN == "cell_scope"
    assert agg._CELL_SCOPE_FN == "agg_cell_scope"

    from well_viewer.features.scatter import cell_scope_compute as m

    assert callable(m.cell_scope) and callable(m.agg_cell_scope)


class _ScopeStub:
    """The scope-kwargs seam off the real class, with no widgets."""

    def __init__(self, cls):
        self._cell_scope_kwargs = cls._cell_scope_kwargs.__get__(self, cls)


def _scope_ctx():
    """Enough ctx for ``scope_from_ctx`` and the bar tab's own reads."""
    return SimpleNamespace(
        state=SimpleNamespace(
            channel=SimpleNamespace(active_val_col="gfp_mean_intensity",
                                    ratio_index={}, fluor_channels=[],
                                    ratio_metrics=[], label_to_channel_key={},
                                    active_channel="gfp",
                                    active_metric="mean_intensity"),
            bar=SimpleNamespace(violin=False, swarm=False, tp_selections={}),
            cell_gating=SimpleNamespace(thresh_frac_on={}, thresh_count_on={},
                                        use_scatter_thresh=False,
                                        scatter_gates=None),
            selection=SimpleNamespace(selections=[], selected_wells=set(),
                                      active_solo_wells=set()),
            dataset=SimpleNamespace(well_paths={}),
        ),
    )


# ── the wiring, not the helpers ─────────────────────────────────────────────
#
# The two tests below drive the panel's own methods. Everything above tests a
# piece; a piece that is correct and never called is the defect §7.7 is about,
# and an adversarial review proved it here: replacing ``_axes_pairs`` with
# ``return []`` (no scale ever pinned, on any mode) and making
# ``_write_cell_rows_for`` return immediately (the file never written, on any
# mode) both left the whole lane green.


class _SaveStub:
    """``_save_figure`` off the real class, with no widgets and no spool."""

    _save_figure = BatchExportPanel._save_figure
    _axes_pairs = BatchExportPanel._axes_pairs
    _axes_spec = BatchExportPanel._axes_spec
    _capture_group_params = BatchExportPanel._capture_group_params
    _PANELS = (Panel("plot", "Plot"),)
    _X_PANELS = None

    def __init__(self, spec):
        self._spool = None
        self._run_axes = spec
        self._ctx = SimpleNamespace(
            state=SimpleNamespace(
                export_prefs=SimpleNamespace(style_prefs={}),
                channel=SimpleNamespace(active_val_col="gfp_mean_intensity"),
            ),
        )


def test_saving_a_figure_applies_this_run_s_scale_and_limits(tmp_path):
    """Through ``_save_figure``, which is where the two mechanisms are joined.

    The wrong implementation that passes a test of ``pin_scales`` alone is one
    whose ``_save_figure`` never calls it — which is what the review's mutation
    built, and what the lane did not notice.
    """
    spec = AxesSpec(x_log=True,
                    panels=(("plot", PanelAxis(y_min=5.0, y_max=50.0)),))
    panel = _SaveStub(spec)
    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    ax.plot([1, 2, 3], [10, 20, 30])
    out = tmp_path / "batch_G.png"

    panel._save_figure(fig, out, "png", group=None, title="G")

    assert out.exists() and out.stat().st_size > 0
    assert ax.get_xscale() == "log"
    assert ax.get_ylim() == pytest.approx((5.0, 50.0))


def test_saving_a_figure_with_no_run_axes_leaves_the_figure_alone():
    """So the assertions above are measuring the spec rather than a default."""
    panel = _SaveStub(None)
    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    ax.plot([1, 2, 3], [10, 20, 30])
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        panel._save_figure(fig, Path(tmp) / "x.png", "png",
                           group=None, title="G")
    assert ax.get_xscale() == "linear"


class _RowsStub:
    """``_write_cell_rows_for`` off the real class."""

    _write_cell_rows_for = BatchExportPanel._write_cell_rows_for
    _cell_scope_kwargs = BatchExportPanel._cell_scope_kwargs
    _note_shortfall = BatchExportPanel._note_shortfall
    _wants_cell_rows = BatchExportPanel._wants_cell_rows
    _wants_image_files = BatchExportPanel._wants_image_files
    _out_path = BatchExportPanel._out_path
    _app_group_scope = BatchExportPanel._app_group_scope
    _app_val_col_scope = BatchExportPanel._app_val_col_scope
    _control_selections = BatchExportPanel._control_selections
    _fold_change_control_refs = BatchExportPanel._fold_change_control_refs
    _CELL_SCOPE = "well_viewer.features.lineplot.cell_scope_compute"
    _CELL_SCOPE_FN = "cell_scope"

    def __init__(self, wanted=True):
        self._wanted = wanted
        self._fc_control = None
        self._fc_control_map = {}
        self._run_notes = []
        self._ctx = SimpleNamespace(
            state=SimpleNamespace(
                channel=SimpleNamespace(active_val_col="gfp_mean_intensity"),
                selection=SimpleNamespace(selections=[], selected_wells=set(),
                                          current_selection_id=None),
            ),
        )

    def _wants_cell_rows(self):  # noqa: F811 — the stub's own switch
        return self._wanted


def _group(name="Drug A"):
    from well_viewer.domain.batch_models import BarGroup, ReplicateSet

    return BarGroup(name, [ReplicateSet("rep 1", ["B02", "B03"])])


def test_a_run_that_asked_for_rows_writes_them_under_the_group_s_stem(
        tmp_path, monkeypatch):
    """Drives ``_write_cell_rows_for``, and asserts the file and the scope.

    A test of ``write_cell_rows`` alone passes against a panel that never calls
    it — the review's second mutation — so this one goes through the method the
    job actually invokes and checks what reached the writer.
    """
    import well_viewer.features.batch_export.base_panel as bp

    seen = {}

    def _fake_scope(ctx, **kwargs):
        seen["kwargs"] = kwargs
        seen["members"] = [s.get("name") for s in ctx.state.selection.selections]
        seen["column"] = ctx.state.channel.active_val_col
        return "SCOPE"

    monkeypatch.setattr(
        "well_viewer.features.lineplot.cell_scope_compute.cell_scope",
        _fake_scope)

    def _fake_write(ctx, scope, path):
        seen["scope"] = scope
        Path(path).write_text("rows")
        return 7

    monkeypatch.setattr(bp, "write_cell_rows", _fake_write)

    panel = _RowsStub()
    panel._write_cell_rows_for(_group(), tmp_path, "batch_Drug_A",
                               val_col="mcherry_total_intensity",
                               timepoints=[24.0])

    assert (tmp_path / "batch_Drug_A_cells.csv").read_text() == "rows"
    assert seen["scope"] == "SCOPE"
    # Built inside the group scope, so it is about this group's members.
    assert seen["members"] == ["rep 1"]
    assert seen["column"] == "mcherry_total_intensity"
    assert seen["kwargs"] == {"val_col": "mcherry_total_intensity",
                              "timepoints": (24.0,)}
    assert panel._run_notes == []


def test_a_run_that_did_not_ask_for_rows_writes_none(tmp_path, monkeypatch):
    import well_viewer.features.batch_export.base_panel as bp

    monkeypatch.setattr(bp, "write_cell_rows",
                        lambda *a, **k: pytest.fail("should not be called"))
    panel = _RowsStub(wanted=False)
    panel._write_cell_rows_for(_group(), tmp_path, "batch_Drug_A")
    assert list(tmp_path.iterdir()) == []


def test_a_group_with_no_loaded_wells_is_reported_not_silently_skipped(
        tmp_path, monkeypatch):
    import well_viewer.features.batch_export.base_panel as bp

    monkeypatch.setattr(
        "well_viewer.features.lineplot.cell_scope_compute.cell_scope",
        lambda ctx, **k: "SCOPE")
    monkeypatch.setattr(bp, "write_cell_rows", lambda ctx, scope, path: -1)

    panel = _RowsStub()
    panel._write_cell_rows_for(_group(), tmp_path, "batch_Drug_A")
    assert len(panel._run_notes) == 1
    assert "Drug A" in panel._run_notes[0]


def test_a_scope_the_tab_declines_to_build_is_reported(tmp_path, monkeypatch):
    """Some tabs return ``None`` before a first draw. Said, not written blank."""
    monkeypatch.setattr(
        "well_viewer.features.lineplot.cell_scope_compute.cell_scope",
        lambda ctx, **k: None)
    panel = _RowsStub()
    panel._write_cell_rows_for(_group(), tmp_path, "batch_Drug_A")
    assert list(tmp_path.iterdir()) == []
    assert len(panel._run_notes) == 1
    assert "no cell scope" in panel._run_notes[0]


def test_the_three_refusals_are_told_apart(tmp_path, monkeypatch):
    """Three problems, three fixes, three sentences.

    "No loaded wells" printed over a corrupt cache sends the user to fix a
    selection that is already correct — and printed over a gating recompute it
    hides the one case where waiting is the whole answer.
    """
    import well_viewer.features.batch_export.base_panel as bp

    monkeypatch.setattr(
        "well_viewer.features.lineplot.cell_scope_compute.cell_scope",
        lambda ctx, **k: "SCOPE")
    said = {}
    for code, expect in ((-1, "no loaded wells"),
                         (-2, "would read"),
                         (-3, "gating is still recomputing")):
        monkeypatch.setattr(bp, "write_cell_rows",
                            lambda ctx, scope, path, _c=code: _c)
        panel = _RowsStub()
        panel._write_cell_rows_for(_group(), tmp_path, "batch_Drug_A")
        assert len(panel._run_notes) == 1
        said[code] = panel._run_notes[0]
        assert expect in said[code], (code, said[code])
    # And they are genuinely three different sentences, not one with a number.
    assert len(set(said.values())) == 3


def test_the_writer_refuses_while_cell_gating_is_recomputing(monkeypatch,
                                                             tmp_path):
    """Asserted through ``write_cell_rows`` itself, not only its caller.

    The runner pumps the event loop between jobs, which is where the gating
    worker's callbacks land — so a run started mid-recompute would give its
    early groups one gating state and its later ones another.
    """
    import well_viewer.features.batch_export._cell_rows as cr

    monkeypatch.setattr(cr, "_gating_busy", lambda ctx: True)
    out = tmp_path / "x.csv"
    assert cr.write_cell_rows(_cell_ctx({}), _scope([("G", "A01")]), out) == -3
    assert not out.exists()
    # And it does not refuse when the worker is idle, so the guard is measuring
    # the flag rather than declining everything.
    monkeypatch.setattr(cr, "_gating_busy", lambda ctx: False)
    assert cr.write_cell_rows(_cell_ctx({}), _scope([("G", "A01")]), out) == -1


def test_unreadable_rows_are_not_reported_as_a_missing_selection(monkeypatch,
                                                                 tmp_path):
    import well_viewer.features.batch_export._cell_rows as cr

    monkeypatch.setattr(cr, "_gating_busy", lambda ctx: False)

    def _boom(ctx, well):
        raise OSError("cache is bad")

    monkeypatch.setattr("well_viewer.data.data_loading.get_rows", _boom)
    ctx = _cell_ctx({"A01": None})
    assert cr.write_cell_rows(ctx, _scope([("G", "A01")]), tmp_path / "x.csv") == -2
