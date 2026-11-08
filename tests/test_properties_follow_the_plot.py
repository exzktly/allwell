"""The Properties panel offers each plot the controls that plot can honour, follows a multi-view figure's view, and
keeps — never writes — a setting whose control is hidden.

The declarations are held to the code by ``test_every_properties_figure_declares_its_facets``; this file holds the
panel to the declarations, through the real ``ExportStyleSidebar`` built against the real feature registry. The
expected section lists are written out per figure rather than derived from the declaration, so a panel that ignored
the declaration (offering everything, as it did before) fails here even though the declaration itself is right.
"""

from __future__ import annotations

import types

import pytest

from review.core.cell_gating_state import CellGatingState
from review.core.export_prefs_state import ExportPrefsState
from review.core.plot_state import PlotState
from review.features.scatter.state import ScatterState


def _stub_app():
    from review.core.app_runtime import AppRuntime
    from review.features.registry import build_feature_registry

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
    app._ctx = types.SimpleNamespace(
        app=app,
        state=app._app_state,
        runtime=AppRuntime(app),
        features=build_feature_registry(),
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
    app._set_status = lambda _m: None
    return app


def _sidebar(qtbot, monkeypatch, attr: str):
    from matplotlib.figure import Figure

    from review.features.export.properties_view import ExportStyleSidebar

    fig = Figure(figsize=(4, 3))
    fig.add_subplot(1, 1, 1)
    app = _stub_app()
    setattr(app, attr, fig)
    monkeypatch.setattr(ExportStyleSidebar, "_redraw_bound_figure", lambda self: True, raising=False)
    monkeypatch.setattr(ExportStyleSidebar, "_redraw_active_plot", lambda self: None, raising=False)
    sb = ExportStyleSidebar(app, None, fig, None, default_name=attr)
    qtbot.addWidget(sb)
    app._export_style_sidebars = {id(fig): sb}
    return app, fig, sb


def _shown_sections(sb) -> list[str]:
    from widgets.collapsible_section import CollapsibleSection

    return [s.titleText() for s in sb.findChildren(CollapsibleSection) if s.isVisibleTo(sb)]


def _shown_labels(sb, title: str) -> list[str]:
    """The row labels of one section, as built and not hidden — read from the panel's own row records, because a
    collapsed section hides its body and visibility would then say nothing about the rows."""
    from PySide6.QtWidgets import QLabel

    out = []
    for sec, _facet, widgets in sb._kit.rows:
        if sec.titleText() != title or any(w.isHidden() for w in widgets):
            continue
        if isinstance(widgets[0], QLabel) and len(widgets) > 1:
            out.append(widgets[0].text())
    return out


_ALWAYS = ["Profile & Format", "Axes", "Grid", "Layout"]

#: What each figure shows, in panel order — written out, not derived, so the panel is measured against a claim.
_EXPECTED_SECTIONS = {
    "_line_fig": ["Profile & Format", "Colours", "Axes", "Statistics", "Legend", "Overlays", "Time",
                  "Lines & Markers", "Grid", "Limits & Scale", "Layout"],
    "_bar_fig": ["Profile & Format", "Colours", "Bars", "Beeswarm", "Axes", "Statistics", "Legend", "Overlays",
                 "Time", "Lines & Markers", "Grid", "Limits & Scale", "Layout"],
    "_scatter_fig": ["Profile & Format", "Density", "Gates", "Axes", "Legend", "Overlays", "Lines & Markers",
                     "Grid", "Limits & Scale", "Layout"],
    "_scatter_agg_fig": ["Profile & Format", "Axes", "Statistics", "Legend", "Overlays", "Lines & Markers",
                         "Grid", "Limits & Scale", "Layout"],
    "_heatmap_fig": ["Profile & Format", "Cell values", "Axes", "Statistics", "Time", "Grid", "Layout"],
    "_distribution_fig": ["Profile & Format", "Axes", "Legend", "Overlays", "Time", "Lines & Markers", "Grid",
                          "Limits & Scale", "Layout"],
    "_response_fig": ["Profile & Format", "Colours", "Axes", "Statistics", "Legend", "Time", "Lines & Markers",
                      "Grid", "Limits & Scale", "Layout"],
    "_sp_fig": ["Profile & Format", "Axes", "Legend", "Time", "Lines & Markers", "Grid", "Limits & Scale",
                "Layout"],
    "_nw_fig": ["Profile & Format", "Beeswarm", "Axes", "Legend", "Lines & Markers", "Grid", "Limits & Scale",
                "Layout"],
    "_mv_fig": ["Profile & Format", "Axes", "Legend", "Grid", "Limits & Scale", "Layout"],
}


@pytest.mark.parametrize("attr", sorted(_EXPECTED_SECTIONS))
def test_each_plot_is_shown_the_sections_it_can_use(qtbot, monkeypatch, attr):
    _app, _fig, sb = _sidebar(qtbot, monkeypatch, attr)
    assert _shown_sections(sb) == _EXPECTED_SECTIONS[attr]


@pytest.mark.parametrize("attr", sorted(_EXPECTED_SECTIONS))
def test_a_section_the_plot_cannot_use_is_not_even_built(qtbot, monkeypatch, attr):
    """Not built, rather than built and hidden: a built control holds a getter, and ``_persist`` writes every getter
    it holds, so a hidden-but-built control would write its seeded value onto a figure that never shows it."""
    from widgets.collapsible_section import CollapsibleSection

    _app, _fig, sb = _sidebar(qtbot, monkeypatch, attr)
    assert [s.titleText() for s in sb.findChildren(CollapsibleSection)] == _EXPECTED_SECTIONS[attr]


def test_the_expected_lists_cover_every_panel_figure():
    from review.features.registry import build_feature_registry

    assert set(_EXPECTED_SECTIONS) == set(build_feature_registry().style_facets())
    assert all(set(_ALWAYS) <= set(v) for v in _EXPECTED_SECTIONS.values())


def test_show_mode_is_offered_on_the_bar_plot_alone(qtbot, monkeypatch):
    """``bar_show_mode`` is read by the bar renderer only: set from the Line panel it changed the Bar tab instead."""
    _a, _f, line = _sidebar(qtbot, monkeypatch, "_line_fig")
    _a, _f, bar = _sidebar(qtbot, monkeypatch, "_bar_fig")
    assert _shown_labels(line, "Statistics") == ["Error bars", "Across"]
    assert _shown_labels(bar, "Statistics")[-3:] == ["Error bars", "Across", "Show"]


def test_group_label_size_is_offered_on_the_bar_plot_alone(qtbot, monkeypatch):
    _a, _f, line = _sidebar(qtbot, monkeypatch, "_line_fig")
    _a, _f, bar = _sidebar(qtbot, monkeypatch, "_bar_fig")
    assert "Group label size" not in _shown_labels(line, "Axes")
    assert "Group label size" in _shown_labels(bar, "Axes")


def test_the_scatters_keep_their_marker_rows_and_lose_the_line_rows(qtbot, monkeypatch):
    _a, _f, sb = _sidebar(qtbot, monkeypatch, "_scatter_agg_fig")
    assert _shown_labels(sb, "Lines & Markers") == ["Marker style", "Marker size", "Marker edge width"]


def test_the_beeswarm_follows_the_nanowells_view(qtbot, monkeypatch):
    """Built once, shown on the Distribution view, hidden on the rest, and shown again on the way back."""
    from review.core.plot_style import mark_style_view

    app, fig, sb = _sidebar(qtbot, monkeypatch, "_nw_fig")
    assert "Beeswarm" in _shown_sections(sb)
    mark_style_view(app._ctx, fig, "array")
    assert "Beeswarm" not in _shown_sections(sb)
    mark_style_view(app._ctx, fig, "distribution")
    assert "Beeswarm" in _shown_sections(sb)


def test_the_nanowells_redraw_stamps_its_view(monkeypatch):
    """The stamp is made by the page's own redraw, on every path — not only by a test calling it.

    ``redraw`` is driven with a page whose card is hidden, which is the earliest return after the stamp, so the
    stamp is shown to sit before every path that leaves the function."""
    from review.features.nanowell import controller as NC

    fig = types.SimpleNamespace()
    card = types.SimpleNamespace(isHidden=lambda: True)
    widgets = {"_nw_fig": fig, "_nw_canvas": object(), "_nw_card": card}
    ctx = types.SimpleNamespace(widgets=types.SimpleNamespace(get_or_none=widgets.get))
    st = types.SimpleNamespace(view="trajectories", loaded_for=None, computing=False)
    monkeypatch.setattr(NC, "_state", lambda _c: st)
    for name in ("_page_deleted", "unread_wells", "load_queued"):
        monkeypatch.setattr(NC, name, lambda *_a: False)
    for name in ("sync_focus_rule", "_call", "_refresh_empty", "redraw_detail"):
        monkeypatch.setattr(NC, name, lambda *_a, **_k: None)
    NC.redraw(ctx)
    assert fig._aw_style_view == "trajectories"


def test_a_hidden_control_never_writes_its_setting(qtbot, monkeypatch):
    """While a view withholds a facet, an edit anywhere in the panel leaves that facet's stored value alone.

    No figure today withholds a facet whose controls carry a pref key (the Nanowells beeswarm writes PlotState from
    its own signal), so the declaration here is synthetic: Legend withheld in a view named ``"quiet"``. The values
    are chosen so the right and the wrong answer differ — the stored font size is 9, the hidden widget is moved to
    17 — and the same edit made while the row is shown is checked to write, so a ``_persist`` that wrote nothing at
    all could not pass."""
    from review.core.plot_style import mark_style_view
    from review.domain.style_facets import FigureFacets

    app, fig, sb = _sidebar(qtbot, monkeypatch, "_line_fig")
    real = sb._facets
    sb._facets = FigureFacets(has=real.has, lacks=real.lacks,
                              views={"quiet": {"legend": "a view that draws no legend"}})
    sb._prefs["legend_font_size"] = 9
    mark_style_view(app._ctx, fig, "quiet")
    assert "Legend" not in _shown_sections(sb)
    sb._setters["legend_font_size"](17)
    sb._persist()
    assert sb._prefs["legend_font_size"] == 9
    mark_style_view(app._ctx, fig, "")
    assert "Legend" in _shown_sections(sb)
    sb._persist()
    assert sb._prefs["legend_font_size"] == 17


def test_the_footer_names_what_is_not_offered_and_why(qtbot, monkeypatch):
    _a, _f, sb = _sidebar(qtbot, monkeypatch, "_line_fig")
    note = sb._not_offered_label
    assert note.isVisibleTo(sb)
    assert note.text() == "8 settings not offered for this plot"
    assert "• Statistics → Show: ``bar_show_mode`` is read by the bar renderer alone" in note.toolTip()


def test_the_footer_count_follows_the_view(qtbot, monkeypatch):
    from review.core.plot_style import mark_style_view

    app, fig, sb = _sidebar(qtbot, monkeypatch, "_nw_fig")
    before = sb._not_offered_label.text()
    mark_style_view(app._ctx, fig, "array")
    assert before == "12 settings not offered for this plot"
    assert sb._not_offered_label.text() == "13 settings not offered for this plot"
    assert "Beeswarm: Only the Distribution view draws a beeswarm" in sb._not_offered_label.toolTip()


# ── what the panel reads off the figure as drawn ───────────────────────────


def _segment_texts(sb, title: str, row: str) -> list[str]:
    from widgets.segmented_control import SegmentedControl

    for sec, _facet, widgets in sb._kit.rows:
        if sec.titleText() == title and widgets[0].text() == row:
            seg = widgets[-1]
            assert isinstance(seg, SegmentedControl)
            return [seg.segmentText(i) for i in range(seg.count())]
    raise AssertionError(f"no {title} → {row} row")


def test_error_bars_offer_none_and_ci_only_where_they_are_drawn(qtbot, monkeypatch):
    """Line reads ``use_sem`` alone: offered None or 95% CI it drew SEM under the other label."""
    _a, _f, line = _sidebar(qtbot, monkeypatch, "_line_fig")
    _a, _f, bar = _sidebar(qtbot, monkeypatch, "_bar_fig")
    assert _segment_texts(line, "Statistics", "Error bars") == ["SEM", "SD"]
    assert _segment_texts(bar, "Statistics", "Error bars") == ["None", "SEM", "SD", "95% CI"]


def _axis_row_shown(sb) -> bool:
    return not any(w.isHidden() for w in sb._axis_row)


def test_the_axis_target_follows_the_panels_the_figure_has(qtbot, monkeypatch):
    """One panel: no choice, no row. A second panel drawn later is offered on the next re-gate, and an image
    panel, which the style pass skips, never is."""
    from review.core.plot_style import regate_properties

    app, fig, sb = _sidebar(qtbot, monkeypatch, "_line_fig")
    assert not _axis_row_shown(sb)
    fig.add_subplot(2, 1, 2)
    crop = fig.add_subplot(2, 2, 1)
    crop._aw_image_panel = True
    regate_properties(app._ctx, fig)
    assert _axis_row_shown(sb)
    assert [sb._axis_cb.itemText(i) for i in range(sb._axis_cb.count())] == ["All", "1", "2"]


def test_the_legend_section_follows_whether_the_plot_draws_a_legend(qtbot, monkeypatch):
    """Read off the figure: a plot drawn with no legend hides the section; a legend switched off is still a
    legend, so the section stays to switch it back on."""
    from review.core.plot_style import regate_properties

    app, fig, sb = _sidebar(qtbot, monkeypatch, "_mv_fig")
    ax = fig.axes[0]
    ax.imshow([[0.0, 1.0], [1.0, 0.0]])
    regate_properties(app._ctx, fig)
    assert "Legend" not in _shown_sections(sb)
    ax.plot([0, 1], [0, 1], label="cluster 1")
    ax.legend().set_visible(False)
    regate_properties(app._ctx, fig)
    assert "Legend" in _shown_sections(sb)


def test_the_style_pass_reaches_a_legend_drawn_on_the_figure():
    """The Nanowells page keys its Distribution, QC and composite views with ``fig.legend``."""
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D

    from review.core.plot_style import apply_export_style_prefs, figure_draws_a_legend

    fig = Figure()
    fig.add_subplot(1, 1, 1).plot([0, 1], [0, 1])
    key = fig.legend(handles=[Line2D([], [], label="responding")], fontsize=7)
    assert figure_draws_a_legend(fig)
    apply_export_style_prefs(fig, {"legend_show": True, "legend_font_size": 13})
    assert key.get_texts()[0].get_fontsize() == 13
    apply_export_style_prefs(fig, {"legend_show": False, "legend_font_size": 13})
    assert not key.get_visible()


def test_x_log_is_not_offered_on_an_axis_of_positions(qtbot, monkeypatch):
    from review.core.plot_style import mark_position_axes, regate_properties

    app, fig, sb = _sidebar(qtbot, monkeypatch, "_bar_fig")
    ax = fig.axes[0]
    ax.bar([0, 1, 2], [3, 4, 5])
    mark_position_axes(ax, y=False)
    regate_properties(app._ctx, fig)
    xlog, _xl, _xb = sb._log_axis_widgets["x"]
    ylog, _yl, _yb = sb._log_axis_widgets["y"]
    assert xlog.isHidden() and not ylog.isHidden()


def test_one_layout_engine_at_a_time(qtbot, monkeypatch):
    _a, _f, sb = _sidebar(qtbot, monkeypatch, "_line_fig")
    tight, constrained = sb._layout_engine_toggles
    constrained.setChecked(True)
    tight.setChecked(True)
    assert tight.isChecked() and not constrained.isChecked()
    assert sb._prefs["layout_constrained"] is False


def test_phone_layout_says_the_layout_switches_do_nothing_there(qtbot, monkeypatch):
    from core import phone_layout
    from review.core.plot_style import regate_properties

    app, fig, sb = _sidebar(qtbot, monkeypatch, "_line_fig")
    assert sb._layout_phone_note.isHidden() and all(t.isEnabled() for t in sb._layout_engine_toggles)
    monkeypatch.setattr(phone_layout, "is_on", lambda: True)
    regate_properties(app._ctx, fig)
    assert not sb._layout_phone_note.isHidden()
    assert not any(t.isEnabled() for t in sb._layout_engine_toggles)


def test_a_shared_section_says_it_is_shared_and_a_per_plot_one_does_not(qtbot, monkeypatch):
    from PySide6.QtWidgets import QLabel
    from widgets.collapsible_section import CollapsibleSection

    _a, _f, sb = _sidebar(qtbot, monkeypatch, "_bar_fig")
    notes = {sec.titleText(): any(lbl.text().startswith("Shared —") for lbl in sec.findChildren(QLabel))
             for sec in sb.findChildren(CollapsibleSection)}
    assert {t for t, shared in notes.items() if shared} == {
        "Colours", "Bars", "Beeswarm", "Statistics", "Overlays", "Time"}
