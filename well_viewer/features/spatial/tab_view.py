"""Spatial-analysis tab builder.

Assembles the tab's controls (level segmented control, viz combo, metric
picker, timepoint filter, export/copy/save) and the matplotlib ``PlotCard``,
then wires every control to controller functions. Widget + refresh-closure
handles go through ``ctx.widgets`` (which stamps ``app._sp_*`` so legacy
readers resolve). Controller and renderer signatures take the ``ViewerContext``
directly — no unwrap and no lambda reach-throughs.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QPushButton, QSpinBox, QWidget,
)

from widgets.control_band import ControlBand
from widgets.icons import make_icon
from widgets.labeled_combo import LabeledComboBox

from well_viewer.core.plot_card_actions import (
    copy_active_card_as_svg, save_active_card_figure, save_card_to_gallery,
)
from well_viewer.core.safe import swallowed
from well_viewer.features.spatial import state as sp_state
from well_viewer.features.spatial import controller as sp_ctrl
from well_viewer.features.spatial.collect import available_metric_columns
from well_viewer.domain.tp_match import tp_close

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_METRIC_CB_MIN_W = 160
_SUMMARY_METRIC_CB_MIN_W = 180
_VIZ_CB_MIN_W = 180

_FIG_W, _FIG_H = 7.6, 6.6

_LEVEL_ORDER = (
    (sp_state.LEVEL_CELL, "Cell"),
    (sp_state.LEVEL_COLONY, "Colony"),
    (sp_state.LEVEL_INTRA, "Intra-colony"),
    (sp_state.LEVEL_SUMMARY, "Summary"),
)

_LEVEL_TOOLTIP = (
    "Choose the scale of analysis.\n"
    "• Cell — per-cell metrics in the well plane "
    "(nearest-neighbour distance, k-NN density, Ripley clustering).\n"
    "• Colony — one aggregate per auto-detected colony "
    "(size, hull area, packing density, per-colony metric spread).\n"
    "• Intra-colony — each cell's position relative to its colony "
    "(distance to centroid, distance to hull edge, boundary/interior flag).\n"
    "• Summary — one scalar per (well, timepoint) frame across the "
    "current well selection. Gross cross-condition comparison, not a "
    "single-frame view. Export CSV writes one row per (well, timepoint)."
)

_VIZ_LABELS = {
    sp_state.VIZ_SPATIAL_MAP: "Spatial map",
    sp_state.VIZ_NND_HISTOGRAM: "NND histogram",
    sp_state.VIZ_RIPLEY: "Ripley L(r) − r",
    sp_state.VIZ_COLONY_SIZE: "Colony size histogram",
    sp_state.VIZ_COLONY_METRIC_DIST: "Per-colony distribution",
    sp_state.VIZ_RADIAL_PROFILE: "Radial profile",
    sp_state.VIZ_BOUNDARY_INTERIOR: "Boundary vs interior",
    sp_state.VIZ_SUMMARY_PANEL: "Summary panel",
}

_VIZ_TOOLTIPS = {
    sp_state.VIZ_SPATIAL_MAP:
        "Scatter of every cell centroid in the well plane, coloured by the "
        "selected metric / colony id / normalised radial position (r/R). "
        "Convex-hull overlays show colony footprints when detected.",
    sp_state.VIZ_NND_HISTOGRAM:
        "Distribution of nearest-neighbour distance (distance to each cell's "
        "single closest neighbour). The dashed vertical shows the median NND — "
        "the reference scale for the colony-detection ε multiplier.",
    sp_state.VIZ_RIPLEY:
        "Ripley's L(r) − r vs the CSR envelope (grey band = 2.5–97.5 % from "
        "Monte-Carlo random point patterns). Curve above the band means "
        "clustered at that radius; below means dispersed; inside means "
        "consistent with complete spatial randomness.",
    sp_state.VIZ_COLONY_SIZE:
        "Histogram of cells-per-colony for every colony DBSCAN / single-linkage "
        "found in the current well and timepoint. The vertical line marks the "
        "median colony size.",
    sp_state.VIZ_COLONY_METRIC_DIST:
        "One violin per colony (sorted by median), showing the within-colony "
        "spread of the selected metric. The top-left callout gives the "
        "variance decomposition: ICC = σ²_between / σ²_total. ICC near 1 "
        "means variability is almost entirely between colonies (colony "
        "identity carries the signal); ICC near 0 means variability lives "
        "inside colonies (colony identity is uninformative).",
    sp_state.VIZ_RADIAL_PROFILE:
        "The selected metric binned by normalised radial position (r/R = 0 at "
        "the colony centroid, 1 at the outermost cell). Points ± SEM answer "
        "'do edge cells behave differently from centre cells?'.",
    sp_state.VIZ_BOUNDARY_INTERIOR:
        "Split violin of the selected metric partitioned by distance to the "
        "colony hull edge — the bottom-N % of d_edge are 'boundary' cells, "
        "the rest are 'interior' cells. N is set by the Boundary % spinbox.",
    sp_state.VIZ_SUMMARY_PANEL:
        "Per-(well, timepoint) matrix of one summary scalar (median NND, "
        "colony count, mean colony size, packing density, ICC, …) walked "
        "across the current well selection and every loaded timepoint. Pick "
        "Heatmap for a wells × timepoints matrix or Line plot for one series "
        "per well. Export CSV writes one long-format row per (well, timepoint) "
        "with every summary column at once.",
}

_METRIC_PICKER_TOOLTIP = (
    "Per-cell CSV column used as the signal in colour maps, radial "
    "profiles, per-colony violins, and the boundary/interior split.\n"
    "Any <channel>_<property> intensity / count column, any shape_* "
    "morphology column, and any user-defined ratio channel is eligible."
)

_TP_PICKER_TOOLTIP = (
    "Spatial analysis is per frame — pick which timepoint to analyse. "
    "'First available' uses each well's smallest finite timepoint."
)

#: Why the picker is greyed at Summary level. The Metric picker states its own
#: reason when disabled and this one did not, so a control that had just been
#: given a visibly disabled appearance still could not say what would re-enable
#: it.
_TP_PICKER_SUMMARY_TOOLTIP = (
    _TP_PICKER_TOOLTIP
    + "\n\n(Disabled: Summary level walks every timepoint — they are the "
    "columns of the well × timepoint matrix it draws, not a choice. Pick "
    "another Level to analyse one frame.)"
)

_METHOD_ORDER = (
    (sp_state.METHOD_DBSCAN, "DBSCAN"),
    (sp_state.METHOD_SINGLE_LINKAGE, "Single-linkage"),
)

_METHOD_TOOLTIPS = {
    sp_state.METHOD_DBSCAN:
        "DBSCAN — density-based. Groups cells whose ε-neighbourhood contains "
        "≥ min-samples other cells; the rest are noise (grey in the map). "
        "Handles varied colony densities and rejects outliers.",
    sp_state.METHOD_SINGLE_LINKAGE:
        "Single-linkage hierarchical — chains any two cells closer than ε "
        "into the same colony. Simpler mental model (ε is a hard cut-off "
        "distance) but doesn't separate noise from real clusters.",
}


def build_spatial_tab(ctx, parent: QWidget) -> None:
    from widgets.plot_card import PlotCard
    from widgets.segmented_control import SegmentedControl
    from well_viewer.core.ui_helpers import (
        make_plot_with_right_dock, make_properties_button,
    )

    st = ctx.state.feature_slice("spatial")
    reg = ctx.widgets

    # The tab body builds inside the plot area (left of the Properties dock).
    # There is one control row: the shared band. This tab used to spend two —
    # level/viz/metric/timepoint on the first, colony-detection and summary
    # parameters on the second — but the parameter groups are shown only when
    # the current (level, viz) actually consumes them, and the band's flow skips
    # a hidden group, so at any one setting the row carries a handful of live
    # controls and wraps only when the pane is genuinely too narrow.
    plot_area, layout, export_dock = make_plot_with_right_dock(parent)
    reg.register("_spatial_export_dock", export_dock)
    parent = plot_area
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    band = ControlBand(parent)

    level_seg = SegmentedControl()
    for key, label in _LEVEL_ORDER:
        level_seg.addSegment(text=label, data=key)
    level_seg.setCurrentByData(st.level)
    level_seg.setToolTip(_LEVEL_TOOLTIP)
    level_seg.currentChanged.connect(
        lambda idx: sp_ctrl.sp_set_level(ctx, _LEVEL_ORDER[idx][0]))
    reg.register("_sp_level_seg", level_seg)
    band.add_scope("Level", level_seg)

    viz_cb = QComboBox()
    viz_cb.setMinimumWidth(_VIZ_CB_MIN_W)
    viz_cb.setToolTip(
        "Visualisation of the current level. Hover any option in the "
        "dropdown for the specific interpretation."
    )
    viz_cb.currentIndexChanged.connect(
        lambda _i: sp_ctrl.sp_set_viz(ctx, viz_cb.currentData() or ""))
    reg.register("_sp_viz_cb", viz_cb)
    band.add_scope("Viz", viz_cb)

    metric_cb = QComboBox()
    metric_cb.setMinimumWidth(_METRIC_CB_MIN_W)
    metric_cb.setToolTip(_METRIC_PICKER_TOOLTIP)
    metric_cb.currentIndexChanged.connect(
        lambda _i: sp_ctrl.sp_set_metric(ctx, metric_cb.currentData() or ""))
    reg.register("_sp_metric_cb", metric_cb)
    band.add_scope("Metric", metric_cb)

    tp_cb = LabeledComboBox("Timepoint")
    tp_cb.addItem("First available", None)
    tp_cb.setToolTip(_TP_PICKER_TOOLTIP)
    tp_cb.currentIndexChanged.connect(
        lambda _i: sp_ctrl.sp_set_timepoint(ctx, tp_cb.currentData()))
    reg.register("_sp_tp_cb", tp_cb)
    band.add_scope("", tp_cb)

    # Labelled, not a bare tick. A "✓" button says nothing about what it does;
    # this one forces a fresh compute, and the glyph now says so too.
    recompute_btn = QPushButton("Recompute")
    recompute_btn.setProperty("variant", "secondary")
    recompute_btn.setIcon(make_icon("refresh-cw", 14))
    recompute_btn.setToolTip(
            "Force a fresh compute.\n"
            "Per-frame levels (Cell / Colony / Intra-colony) auto-recompute "
            "on every control change, so the button is only strictly needed "
            "at Summary level — where colony parameter / metric / well-"
            "selection changes are queued until you click it (one DBSCAN "
            "per (well × timepoint) frame is expensive to run on every "
            "keystroke).")
    recompute_btn.clicked.connect(lambda _=False: sp_ctrl.sp_recompute(ctx))
    reg.register("_sp_recompute_btn", recompute_btn)
    band.add_action(recompute_btn)

    from well_viewer.core.ui_helpers import add_menu_action, make_action_menu_button
    export_btn, export_menu = make_action_menu_button("Export ▾", band,
                                                      tooltip="Export this plot")
    export_csv_act = add_menu_action(
        export_menu, "Export Summary CSV…",
        lambda: sp_ctrl.sp_export_summary_csv(ctx),
        tooltip="Summary level only")
    add_menu_action(export_menu, "Copy SVG", lambda: copy_active_card_as_svg(ctx))
    add_menu_action(export_menu, "Save Figure…", lambda: save_active_card_figure(ctx))
    export_menu.addSeparator()
    add_menu_action(export_menu, "Save to Saved Plots",
                    lambda: save_card_to_gallery(ctx, "_sp_card"))
    reg.register("_sp_export_btn", export_btn)
    reg.register("_sp_export_csv_act", export_csv_act)
    band.add_action(export_btn)
    band.add_action(make_properties_button(ctx, band, "spatial"))

    # ── parameters: colony detection, boundary, summary display ─────────────
    # Each group is shown only when the current (level, viz) actually consumes
    # it, and the band's flow skips a hidden group, so the row never carries an
    # irrelevant knob.
    method_cb = QComboBox()
    for key, label in _METHOD_ORDER:
        method_cb.addItem(label, key)
        idx = method_cb.count() - 1
        method_cb.setItemData(idx, _METHOD_TOOLTIPS.get(key, ""),
                              _tooltip_role())
    _select_combo_by_data(method_cb, st.colony_method)
    method_cb.setToolTip(
        "Clustering algorithm. Hover DBSCAN / Single-linkage for details."
    )
    method_cb.currentIndexChanged.connect(
        lambda _i: sp_ctrl.sp_set_colony_method(ctx, method_cb.currentData()))
    reg.register("_sp_method_cb", method_cb)
    method_cb.setToolTip(
        method_cb.toolTip()
        + "\n\nColonies are auto-detected — no manual annotation. Sweep these "
        "controls to change detection sensitivity live."
    )
    grp_colonies = band.add_scope("Colonies", method_cb)

    _EPS_TIP = (
        "Colony-membership radius ε as a multiple of the median "
        "nearest-neighbour distance in this frame. Self-scales to the "
        "well's density — the same multiplier gives roughly the same "
        "'grain' of clustering whether cells are packed or sparse.\n"
        "Larger ε → fewer, bigger colonies. Smaller ε → more, tighter "
        "colonies (and more noise cells under DBSCAN)."
    )
    eps_spin = QDoubleSpinBox()
    eps_spin.setRange(0.5, 20.0)
    eps_spin.setSingleStep(0.5)
    eps_spin.setDecimals(2)
    eps_spin.setValue(float(st.colony_eps_mult))
    eps_spin.setToolTip(_EPS_TIP)
    eps_spin.valueChanged.connect(
        lambda v: sp_ctrl.sp_set_colony_eps_mult(ctx, v))
    reg.register("_sp_eps_spin", eps_spin)
    grp_eps = band.add_scope("ε ×median NND", eps_spin)

    _MS_TIP = (
        "DBSCAN 'min-samples' — the minimum count of cells within ε "
        "required for a cell to seed a colony. Cells with fewer neighbours "
        "are marked noise (drawn grey on the spatial map).\n"
        "Ignored by Single-linkage (which never rejects a point)."
    )
    ms_spin = QSpinBox()
    ms_spin.setRange(1, 50)
    ms_spin.setValue(int(st.colony_min_samples))
    ms_spin.setToolTip(_MS_TIP)
    ms_spin.valueChanged.connect(
        lambda v: sp_ctrl.sp_set_colony_min_samples(ctx, v))
    reg.register("_sp_min_samples_spin", ms_spin)
    grp_ms = band.add_scope("Min. samples", ms_spin)

    _BND_TIP = (
        "Fraction of each colony's cells (per colony, by percentile of "
        "distance-to-hull-edge) tagged 'boundary'. Drives the "
        "Boundary-vs-Interior violin split; 20 % is a sensible default."
    )
    bnd_spin = QDoubleSpinBox()
    bnd_spin.setRange(1.0, 50.0)
    bnd_spin.setSingleStep(1.0)
    bnd_spin.setDecimals(1)
    bnd_spin.setValue(float(st.boundary_pct))
    bnd_spin.setToolTip(_BND_TIP)
    bnd_spin.valueChanged.connect(
        lambda v: sp_ctrl.sp_set_boundary_pct(ctx, v))
    reg.register("_sp_boundary_spin", bnd_spin)
    grp_bnd = band.add_scope("Boundary %", bnd_spin)

    # Summary-only controls: which FrameSummary field to project + heatmap /
    # line-plot toggle. Shown only at Summary level, alongside the colony
    # detection params (which still matter — one DBSCAN per frame).
    summary_metric_cb = QComboBox()
    summary_metric_cb.setMinimumWidth(_SUMMARY_METRIC_CB_MIN_W)
    for key, label, units in _summary_metric_catalog():
        summary_metric_cb.addItem(
            f"{label} ({units})" if units else label, key)
    _select_combo_by_data(summary_metric_cb, st.summary_metric_key)
    summary_metric_cb.setToolTip(
        "Which per-frame scalar to summarise across the (well × timepoint) "
        "grid. ICC needs a per-cell Metric selected."
    )
    summary_metric_cb.currentIndexChanged.connect(
        lambda _i: sp_ctrl.sp_set_summary_metric(
            ctx, summary_metric_cb.currentData() or "nnd_median"))
    reg.register("_sp_summary_metric_cb", summary_metric_cb)
    grp_summary = band.add_scope("Summary", summary_metric_cb)

    summary_display_cb = QComboBox()
    summary_display_cb.addItem("Heatmap", sp_state.SUMMARY_DISPLAY_HEATMAP)
    summary_display_cb.addItem("Line plot", sp_state.SUMMARY_DISPLAY_LINE)
    _select_combo_by_data(summary_display_cb, st.summary_display)
    summary_display_cb.setToolTip(
        "Heatmap: wells × timepoints matrix, coloured by the summary metric.\n"
        "Line plot: one line per well, X = timepoint, Y = summary metric."
    )
    summary_display_cb.currentIndexChanged.connect(
        lambda _i: sp_ctrl.sp_set_summary_display(
            ctx, summary_display_cb.currentData()
            or sp_state.SUMMARY_DISPLAY_HEATMAP))
    reg.register("_sp_summary_display_cb", summary_display_cb)
    grp_display = band.add_scope("Display", summary_display_cb)

    layout.addWidget(band)

    # ── level / viz visibility sync (colony vs summary vs metric-driven) ─────
    # Toggle the band's scope *groups*, not the bare controls: a group holds a
    # label and its control, and the flow lays out whichever groups are visible.
    _colony_widgets = (grp_colonies, grp_eps, grp_ms)
    _boundary_widgets = (grp_bnd,)
    _summary_widgets = (grp_summary, grp_display)

    def _sync_visibility() -> None:
        level = st.level
        viz = st.viz
        is_summary = level == sp_state.LEVEL_SUMMARY
        # Colony detection params drive Colony, Intra-colony, Summary compute
        # and per-viz colony-consuming vizs at Cell level (currently none, but
        # keep the option open by keying on level here).
        show_colonies = level in (sp_state.LEVEL_COLONY, sp_state.LEVEL_INTRA,
                                   sp_state.LEVEL_SUMMARY)
        for w in _colony_widgets:
            w.setVisible(show_colonies)
        # Boundary % is only meaningful for the boundary-vs-interior split at
        # Intra-colony level.
        show_boundary = (level == sp_state.LEVEL_INTRA
                          and viz == sp_state.VIZ_BOUNDARY_INTERIOR)
        for w in _boundary_widgets:
            w.setVisible(show_boundary)
        for w in _summary_widgets:
            w.setVisible(is_summary)

        # - Metric picker is greyed on vizs that don't consume it. Stops the
        #   tab from promising an effect it can't deliver.
        metric_enabled = viz not in sp_state.METRIC_IGNORED_VIZS
        metric_cb.setEnabled(metric_enabled)
        if metric_enabled:
            metric_cb.setToolTip(_METRIC_PICKER_TOOLTIP)
        else:
            metric_cb.setToolTip(
                _METRIC_PICKER_TOOLTIP
                + "\n\n(Disabled: the current visualisation does not use the "
                "Metric picker — NND histogram / Ripley L / Colony size are "
                "structural / statistical, not signal.)"
            )
        # Single-timepoint picker is meaningless at Summary level (all tps
        # are walked), and says so rather than only greying — a disabled
        # control that gives no reason reads as broken.
        tp_cb.setEnabled(not is_summary)
        tp_cb.setToolTip(
            _TP_PICKER_SUMMARY_TOOLTIP if is_summary else _TP_PICKER_TOOLTIP
        )
        export_csv_act.setEnabled(is_summary)

    reg.register("_sp_sync_summary_visibility", _sync_visibility)
    _sync_visibility()

    # ── the figure ───────────────────────────────────────────────────────────
    card = PlotCard(parent, figsize=(_FIG_W, _FIG_H), constrained=False,
                    header=False)
    card.setFigureTitle("")
    try:
        card.setStatsChipVisible(False)
    except Exception:
        swallowed()
    reg.register("_sp_card", card)
    reg.register("_sp_fig", card.figure)
    reg.register("_sp_canvas", card.canvas)
    from well_viewer.core.ui_helpers import apply_plot_card_decorators
    apply_plot_card_decorators(ctx, card, "Spatial")
    card.plotThemeChanged.connect(lambda _m: sp_ctrl.sp_recompute(ctx))
    from well_viewer.core.ui_helpers import wrap_with_empty_state
    layout.addWidget(wrap_with_empty_state(
        ctx.window, card, icon="waypoints",
        reason=sp_ctrl.spatial_empty_reason,
    ), 1)

    # ── refresh closures registered on the widget registry ──────────────────
    def _refresh_viz_menu() -> None:
        blocked = viz_cb.blockSignals(True)
        try:
            viz_cb.clear()
            for key in sp_state.VIZ_BY_LEVEL.get(st.level, ()):
                viz_cb.addItem(_VIZ_LABELS.get(key, key), key)
                idx = viz_cb.count() - 1
                tip = _VIZ_TOOLTIPS.get(key)
                if tip:
                    viz_cb.setItemData(idx, tip, Qt.ToolTipRole)
            _select_combo_by_data(viz_cb, st.viz)
            if viz_cb.currentIndex() < 0 and viz_cb.count():
                viz_cb.setCurrentIndex(0)
                st.viz = viz_cb.currentData()
        finally:
            viz_cb.blockSignals(blocked)

    def _refresh_metric_picker() -> None:
        blocked = metric_cb.blockSignals(True)
        try:
            metric_cb.clear()
            metric_cb.addItem("— (no metric)", "")
            for col in available_metric_columns(ctx):
                metric_cb.addItem(col, col)
            _select_combo_by_data(metric_cb, st.metric_col or "")
        finally:
            metric_cb.blockSignals(blocked)

    def _refresh_timepoints() -> None:
        blocked = tp_cb.blockSignals(True)
        try:
            tp_cb.clear()
            tp_cb.addItem("First available", None)
            try:
                for t in _loaded_timepoints(ctx):
                    tp_cb.addItem(f"{t:g} h", float(t))
            except Exception:
                swallowed()
            if st.timepoint is not None:
                _select_combo_by_data(tp_cb, st.timepoint)
        finally:
            tp_cb.blockSignals(blocked)

    reg.register("_sp_refresh_viz_menu", _refresh_viz_menu)
    reg.register("_sp_refresh_metric_picker", _refresh_metric_picker)
    reg.register("_sp_refresh_timepoints", _refresh_timepoints)

    _refresh_viz_menu()
    _refresh_metric_picker()
    _refresh_timepoints()


# ── module helpers ───────────────────────────────────────────────────────────


def _summary_metric_catalog():
    """Small indirection so the module-level lookup is import-cycle-safe."""
    from well_viewer.domain.spatial_compute import SUMMARY_METRICS
    return SUMMARY_METRICS

def _tooltip_role():
    """Qt's per-item tooltip role, as an int the combobox APIs accept."""
    return Qt.ToolTipRole


def _select_combo_by_data(combo, value) -> None:
    for i in range(combo.count()):
        d = combo.itemData(i)
        if d == value or (isinstance(d, float) and isinstance(value, (int, float))
                          and tp_close(d, value)):
            combo.setCurrentIndex(i)
            return


def _loaded_timepoints(ctx):
    """Unique finite timepoints across every well already in the dataset
    cache. Pure ``ctx.state`` reader; empty when nothing has been loaded."""
    import pandas as pd
    from well_viewer.data.data_loading import df_included_mask
    seen: set = set()
    cache = getattr(ctx.state.dataset, "cache", None) or {}
    for label, df in cache.items():
        if df is None or df.empty or "timepoint_hours" not in df.columns:
            continue
        mask = df_included_mask(df, include_gate=False)
        tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").where(mask)
        seen.update(float(t) for t in tp.dropna().unique())
    return sorted(seen)
