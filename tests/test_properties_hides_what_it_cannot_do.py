"""The Heat Map's Properties panel offers only what it can actually change.

The panel is built once per figure and showed nearly every section to every
plot. On the Heat Map most of those sections style artists the figure does not
contain, or write state its renderer never reads, so the control moved and the
grid did not — a Legend section over a figure with no legend, five Lines &
Markers rows over a figure with no lines, a Log scale toggle over axes that
carry row and column *positions*.

Two halves, and the second is the one that keeps the first honest:

* **The sections are gone.** Asserting only this passes against an
  implementation that empties the whole panel, so every section that *is* live
  on the Heat Map is named here too — the ones a user would lose if the gate
  went one facet too far.
* **The reasons stay true.** ``_STYLE_FACETS_A_FIGURE_LACKS`` claims each hidden
  facet does nothing, and the claim was established by measurement, not by
  reading: draw the heat map, change the pref, re-apply
  ``apply_export_style_prefs``, render to PNG, compare. Those measurements are
  re-run below, so a change that makes one of these prefs matter to the heat map
  fails here instead of leaving a control hidden that has quietly become useful.

The measuring half is also what found the bug in
``tests/test_grid_show_turns_the_grid_off.py``: comparing renders rather than
reading the call is what shows that **Show grid** off looked exactly like on.
"""

from __future__ import annotations

import hashlib
import io
import types

import pytest

from review.core.cell_gating_state import CellGatingState
from review.core.export_prefs_state import ExportPrefsState
from review.core.plot_state import PlotState
from review.core.plot_style import (
    DEFAULT_EXPORT_STYLE_PREFS,
    apply_export_style_prefs,
)
from review.features.heatmap import controller as HM
from review.features.heatmap.models import HeatmapLayout
from review.features.scatter.state import ScatterState

pytestmark = pytest.mark.qt

#: Sections that must not appear on the Heat Map, and the facet each is gated
#: on. Stated as a pair so a section renamed without its facet, or a facet
#: dropped without its section, is a failure rather than a silent pass.
_HIDDEN_SECTIONS = [
    ("Legend", "legend"),
    ("Lines & Markers", "marks"),
    ("Overlays", "plot_overlays"),
]

#: Sections that must survive. Without these the file cannot tell a correct gate
#: from one that hid the panel.
_KEPT_SECTIONS = [
    "Profile & Format",
    "Cell values",
    "Axes",
    "Statistics",
    "Grid",
    "Limits & Scale",
    "Time",
    "Layout",
]

_WELLS = {"A01": 5.0, "A02": 120.0, "B01": 300.0, "B02": 500.0}


# ── the built panel ─────────────────────────────────────────────────────────

def _stub_app():
    """The smallest app the sidebar builds against (as in the Colours tests)."""
    app = types.SimpleNamespace()
    app._app_state = types.SimpleNamespace(
        dataset=types.SimpleNamespace(data_dir=None),
        plot=PlotState(),
        selection=types.SimpleNamespace(selected_wells=["A01"]),
        scatter=ScatterState(),
        export_prefs=ExportPrefsState(),
        cell_gating=CellGatingState(),
    )
    app._app_state.cell_gating.scatter_gates = []
    from review.core.app_runtime import AppRuntime
    app._ctx = types.SimpleNamespace(
        app=app,
        state=app._app_state,
        runtime=AppRuntime(app),
        widgets=types.SimpleNamespace(
            get_or_none=lambda n: getattr(app, n, None),
            register=lambda n, w: (setattr(app, n, w), w)[1],
        ),
        notify=types.SimpleNamespace(set_status=lambda _m: None),
    )
    app._rep_sets_active = lambda: []
    app._parse_rc = lambda lbl: (lbl, 0, 0)
    app._well_display_label = lambda w: w
    app._replicate_display_label = lambda rs: getattr(rs, "name", "")
    app._use_fov_spread_active = lambda: False
    app._toggle_sem = lambda: None
    app._toggle_fov_replicates = lambda: None
    app._scatter_gate_draw_mode = False
    app._scatter_gate_selector = None
    app._statuses = []
    app._set_status = app._statuses.append
    return app


def _sidebar(qtbot, monkeypatch, *, kind: str):
    """The real Properties panel, built for the named figure.

    *kind* names the ``app`` attribute the figure is bound to, which is exactly
    how the panel decides what it is looking at. A colour-bar axes is added for
    the heat map because the real figure has one and the Axis # picker's
    behaviour is about it.
    """
    from matplotlib.figure import Figure

    from review.features.export.sidebar_view import ExportStyleSidebar

    fig = Figure(figsize=(4, 3))
    fig.add_subplot(1, 2, 1)
    # A second axes either way, so the two Axis #-picker tests differ only in
    # whether it is a colour bar — which is the thing being measured.
    second = fig.add_subplot(1, 2, 2)
    if kind == "_heatmap_fig":
        setattr(second, "_aw_colorbar", True)
    app = _stub_app()
    setattr(app, kind, fig)
    monkeypatch.setattr(ExportStyleSidebar, "_redraw_bound_figure",
                        lambda self: True, raising=False)
    sb = ExportStyleSidebar(app, None, fig, None, default_name=kind)
    qtbot.addWidget(sb)
    return app, sb


def _section_titles(sidebar) -> list[str]:
    from widgets.collapsible_section import CollapsibleSection

    return [s.titleText() for s in sidebar.findChildren(CollapsibleSection)]


def _row_labels(sidebar, section_title: str) -> list[str]:
    """Every label drawn inside one section, as the user reads them."""
    from PySide6.QtWidgets import QLabel
    from widgets.collapsible_section import CollapsibleSection

    for sec in sidebar.findChildren(CollapsibleSection):
        if sec.titleText() == section_title:
            return [w.text() for w in sec.findChildren(QLabel)]
    return []


@pytest.mark.parametrize("title,_facet", _HIDDEN_SECTIONS)
def test_a_section_the_heat_map_cannot_use_is_not_built(
        qtbot, monkeypatch, title, _facet):
    _app, sb = _sidebar(qtbot, monkeypatch, kind="_heatmap_fig")
    assert title not in _section_titles(sb), _section_titles(sb)


@pytest.mark.parametrize("title", _KEPT_SECTIONS)
def test_the_sections_the_heat_map_does_use_are_still_built(
        qtbot, monkeypatch, title):
    _app, sb = _sidebar(qtbot, monkeypatch, kind="_heatmap_fig")
    assert title in _section_titles(sb), _section_titles(sb)


@pytest.mark.parametrize("title,_facet", _HIDDEN_SECTIONS)
def test_the_same_sections_are_still_built_for_a_line_plot(
        qtbot, monkeypatch, title, _facet):
    """The gate is one figure's, not a deletion.

    Every hidden section is live on a Line plot, so a fix that removed the
    sections outright — rather than gating them — fails here.
    """
    _app, sb = _sidebar(qtbot, monkeypatch, kind="_line_fig")
    assert title in _section_titles(sb), _section_titles(sb)


def test_the_group_label_size_row_is_not_offered(qtbot, monkeypatch):
    _app, sb = _sidebar(qtbot, monkeypatch, kind="_heatmap_fig")
    labels = _row_labels(sb, "Axes")
    assert "Group label size" not in labels, labels
    # The rows beside it are live and must stay.
    for kept in ("Tick label size", "Title size"):
        assert kept in labels, labels


def test_the_group_label_size_row_is_still_offered_on_a_bar_plot(
        qtbot, monkeypatch):
    _app, sb = _sidebar(qtbot, monkeypatch, kind="_bar_fig")
    assert "Group label size" in _row_labels(sb, "Axes")


def test_the_log_scale_rows_are_not_offered(qtbot, monkeypatch):
    _app, sb = _sidebar(qtbot, monkeypatch, kind="_heatmap_fig")
    labels = _row_labels(sb, "Limits & Scale")
    assert "Log scale" not in labels, labels
    assert "Log base" not in labels, labels
    # The typed limits and the button that clears them do work here.
    assert "X limits" in labels, labels


def test_the_log_scale_rows_are_still_offered_on_a_line_plot(
        qtbot, monkeypatch):
    _app, sb = _sidebar(qtbot, monkeypatch, kind="_line_fig")
    labels = _row_labels(sb, "Limits & Scale")
    assert "Log scale" in labels and "Log base" in labels, labels


def test_the_bar_only_show_segment_is_not_built(qtbot, monkeypatch):
    """Not merely absent from the layout — never constructed.

    ``_resync_plot_state_segments`` re-seeds ``_show_seg`` on every dataset
    load. A widget built and then left out of the grid would still be re-seeded
    and would still write ``bar_show_mode`` when something moved it, which is a
    control that does nothing wearing a second disguise.
    """
    _app, sb = _sidebar(qtbot, monkeypatch, kind="_heatmap_fig")
    assert getattr(sb, "_show_seg", None) is None
    assert "Show" not in _row_labels(sb, "Statistics")
    # Error bars and Across *are* read by the heat map — they pick the interval
    # the per-cell ± prints — so they stay.
    assert "Error bars" in _row_labels(sb, "Statistics")
    assert "Across" in _row_labels(sb, "Statistics")


def test_the_show_segment_is_still_built_for_a_bar_plot(qtbot, monkeypatch):
    _app, sb = _sidebar(qtbot, monkeypatch, kind="_bar_fig")
    assert getattr(sb, "_show_seg", None) is not None
    assert "Show" in _row_labels(sb, "Statistics")


def test_the_axis_picker_leaves_out_the_colour_bar(qtbot, monkeypatch):
    """The colour bar is an axes of the figure and not a panel to target.

    Every per-axis pass in ``apply_export_style_prefs`` skips a
    ``_aw_colorbar`` axes, so offering it was a menu entry that moved nothing.
    """
    _app, sb = _sidebar(qtbot, monkeypatch, kind="_heatmap_fig")
    cb = sb._axis_cb
    assert [cb.itemText(i) for i in range(cb.count())] == ["All", "1"]


def test_the_axis_picker_still_lists_every_plot_panel(qtbot, monkeypatch):
    """A two-panel figure with no colour bar keeps both of its numbers."""
    _app, sb = _sidebar(qtbot, monkeypatch, kind="_line_fig")
    cb = sb._axis_cb
    assert [cb.itemText(i) for i in range(cb.count())] == ["All", "1", "2"]


def test_the_response_figure_predicate_is_defined_once(qtbot, monkeypatch):
    """``_is_response_fig`` had two identical definitions, the second shadowing
    the first. Harmless while they agreed, and a silent divergence the moment
    one was edited."""
    import ast
    import inspect

    from review.features.export import sidebar_view

    tree = ast.parse(inspect.getsource(sidebar_view))
    names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"defined more than once in sidebar_view: {sorted(dupes)}"


# ── the measurements the facet table's reasons rest on ──────────────────────

def _drawn_heatmap(monkeypatch):
    """A real Heat Map figure, drawn the way the tab draws one."""
    from matplotlib.figure import Figure

    # Matplotlib's default canvas, not a small one: at 4x3 the colour bar's
    # label falls off the right edge at every font size, so "Axis label size
    # changes the figure" measures as false for a reason that is about the
    # fixture rather than about the panel.
    fig = Figure()
    ax = fig.add_subplot(1, 2, 1)
    cax = fig.add_subplot(1, 2, 2)
    layout = HeatmapLayout(name="Block", rows=2, cols=2)
    layout.assign(0, 0, ["A01"])
    layout.assign(0, 1, ["A02"])
    layout.assign(1, 0, ["B01"])
    layout.assign(1, 1, ["B02"])
    widgets = {
        "_heatmap_fig": fig, "_heatmap_ax": ax,
        "_heatmap_canvas": types.SimpleNamespace(draw_idle=lambda: None),
        "_heatmap_cax": cax,
        "_heatmap_metric_cb": types.SimpleNamespace(
            currentText=lambda: HM.METRIC_MEAN),
        "_heatmap_tp_cb": types.SimpleNamespace(currentIndex=lambda: 0),
    }
    ctx = types.SimpleNamespace(
        window=None,
        state=types.SimpleNamespace(
            dataset=types.SimpleNamespace(well_paths=dict.fromkeys(_WELLS, "p")),
            channel=types.SimpleNamespace(
                active_val_col="v", active_channel="GFP",
                active_metric="mean_intensity", ratio_index={}),
            plot=types.SimpleNamespace(use_fov_replicates=False, use_sem=True,
                                       time_scale=1.0, time_offset=0.0),
            selection=types.SimpleNamespace(selected_wells=set(), variables=None),
            cell_gating=types.SimpleNamespace(
                use_scatter_thresh=False, use_scatter_gates=False,
                scatter_gates=[], thresh_choices=None, selected_gate=None),
            heatmap=types.SimpleNamespace(
                tp_values=[0.0], scale_mode="Auto", vmin=None, vmax=None,
                repset_avg=False, log_scale=False, show_error=False,
                label_from_sample_map=False, active_layout_name="Block"),
        ),
        runtime=types.SimpleNamespace(heatmap=types.SimpleNamespace(
            layouts=[layout], global_range_cache=None, label_drag=None,
            colorbar=None, global_vmin=None, global_vmax=None)),
        widgets=types.SimpleNamespace(get_or_none=widgets.get),
        notify=types.SimpleNamespace(set_status=lambda _m: None),
    )

    def _value(_ctx, wells, *_a, **_k):
        vals = [_WELLS[w] for w in (wells or []) if w in _WELLS]
        return sum(vals) / len(vals) if vals else float("nan")

    monkeypatch.setattr(HM, "current_focus_tp", lambda _c: None)
    monkeypatch.setattr(HM, "on_cutoff_for_column", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(HM, "get_all_fluor_gates", lambda _c: {})
    monkeypatch.setattr(HM, "active_channel_label", lambda _c: "GFP")
    monkeypatch.setattr(HM, "apply_export_style_to_current", lambda *_a, **_k: None)
    monkeypatch.setattr(HM, "_cell_value", _value)
    monkeypatch.setattr(
        HM, "_cell_stat", lambda c, w, *_a, **_k: (_value(c, w), float("nan")))
    HM.redraw_heatmap(ctx)
    return fig


def _rendered_with(monkeypatch, **overrides) -> str:
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    fig = _drawn_heatmap(monkeypatch)
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs.update(overrides)
    apply_export_style_prefs(fig, prefs)
    FigureCanvasAgg(fig)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=72)
    return hashlib.sha256(buf.getvalue()).hexdigest()


#: One pref per hidden row, moved far enough that any effect would be visible.
_PREFS_THAT_SHOULD_DO_NOTHING = [
    ("legend", "legend_show", False),
    ("legend", "legend_font_size", 24),
    ("legend", "legend_loc", "upper left"),
    ("marks", "line_style", ":"),
    ("marks", "line_width", 7.0),
    ("marks", "marker_style", "s"),
    ("marks", "marker_size", 18.0),
    ("marks", "marker_edge_width", 4.0),
    ("group_labels", "group_label_size", 30),
    ("log_axes", "x_log", True),
    ("log_axes", "y_log", True),
    ("log_axes", "x_log_base", "2"),
    ("log_axes", "y_log_base", "2"),
]


@pytest.mark.parametrize("facet,key,value", _PREFS_THAT_SHOULD_DO_NOTHING)
def test_a_hidden_pref_really_changes_nothing_on_the_heat_map(
        monkeypatch, facet, key, value):
    """The facet table says these do nothing. Re-measure it rather than trust it.

    A pref that starts mattering to the heat map fails here, which is the point:
    the row would then be hiding a control that had become useful, and the
    reason written beside it in ``_STYLE_FACETS_A_FIGURE_LACKS`` would be false.
    """
    assert _rendered_with(monkeypatch) == _rendered_with(monkeypatch, **{key: value}), (
        f"{key} changes the Heat Map, so the {facet!r} facet is wrongly listed"
    )


@pytest.mark.parametrize("key,value", [
    ("title_size", 30),
    ("tick_label_size", 30),
    ("axis_label_size", 30),
    ("tick_length", 15.0),
    ("grid_alpha", 0.9),
    ("layout_tight", True),
])
def test_a_kept_pref_really_does_change_the_heat_map(monkeypatch, key, value):
    """The measuring harness has to be able to see a difference at all.

    Without this the parametrised test above would pass just as happily against
    a renderer that drew nothing, or a comparison that hashed the same figure
    twice (CLAUDE.md §7.3 #1).
    """
    assert _rendered_with(monkeypatch) != _rendered_with(monkeypatch, **{key: value})
