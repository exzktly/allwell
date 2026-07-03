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
    QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout,
    QWidget,
)

from well_viewer.core.safe import swallowed
from well_viewer.features.export.style import copy_figure_to_clipboard
from well_viewer.features.spatial import state as sp_state
from well_viewer.features.spatial import controller as sp_ctrl
from well_viewer.features.spatial.collect import available_metric_columns

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
    from widgets.icon_button import IconButton
    from widgets.segmented_control import SegmentedControl

    st = ctx.state.feature_slice("spatial")
    reg = ctx.widgets

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # ── row 1: level + viz + metric + timepoint + actions ────────────────────
    row1 = QWidget(parent)
    row1.setObjectName("TabCtrl")
    r1 = QHBoxLayout(row1)
    r1.setContentsMargins(10, 6, 10, 4)

    level_seg = SegmentedControl(row1)
    for key, label in _LEVEL_ORDER:
        level_seg.addSegment(text=label, data=key)
    level_seg.setCurrentByData(st.level)
    level_seg.setToolTip(_LEVEL_TOOLTIP)
    level_seg.currentChanged.connect(
        lambda idx: sp_ctrl.sp_set_level(ctx, _LEVEL_ORDER[idx][0]))
    reg.register("_sp_level_seg", level_seg)
    r1.addWidget(level_seg)

    r1.addWidget(QLabel("Viz:", row1))
    viz_cb = QComboBox(row1)
    viz_cb.setMinimumWidth(180)
    viz_cb.setToolTip(
        "Visualisation of the current level. Hover any option in the "
        "dropdown for the specific interpretation."
    )
    viz_cb.currentIndexChanged.connect(
        lambda _i: sp_ctrl.sp_set_viz(ctx, viz_cb.currentData() or ""))
    reg.register("_sp_viz_cb", viz_cb)
    r1.addWidget(viz_cb)

    r1.addWidget(QLabel("Metric:", row1))
    metric_cb = QComboBox(row1)
    metric_cb.setMinimumWidth(160)
    metric_cb.setToolTip(
        "Per-cell CSV column used as the signal in colour maps, radial "
        "profiles, per-colony violins, and the boundary/interior split.\n"
        "Any <channel>_<property> intensity / count column, any shape_* "
        "morphology column, and any user-defined ratio channel is eligible."
    )
    metric_cb.currentIndexChanged.connect(
        lambda _i: sp_ctrl.sp_set_metric(ctx, metric_cb.currentData() or ""))
    reg.register("_sp_metric_cb", metric_cb)
    r1.addWidget(metric_cb)

    r1.addWidget(QLabel("Timepoint:", row1))
    tp_cb = QComboBox(row1)
    tp_cb.addItem("First available", None)
    tp_cb.setToolTip(
        "Spatial analysis is per frame — pick which timepoint to analyse. "
        "'First available' uses each well's smallest finite timepoint."
    )
    tp_cb.currentIndexChanged.connect(
        lambda _i: sp_ctrl.sp_set_timepoint(ctx, tp_cb.currentData()))
    reg.register("_sp_tp_cb", tp_cb)
    r1.addWidget(tp_cb)

    # Summary-only controls: metric key + display mode. Hidden on other levels.
    summary_metric_label = QLabel("Summary metric:", row1)
    r1.addWidget(summary_metric_label)
    summary_metric_cb = QComboBox(row1)
    summary_metric_cb.setMinimumWidth(180)
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
    r1.addWidget(summary_metric_cb)

    summary_display_label = QLabel("Display:", row1)
    r1.addWidget(summary_display_label)
    summary_display_cb = QComboBox(row1)
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
    r1.addWidget(summary_display_cb)

    r1.addStretch(1)

    recompute_btn = IconButton(
        "check", row1,
        tooltip=(
            "Force a fresh compute.\n"
            "Per-frame levels (Cell / Colony / Intra-colony) auto-recompute "
            "on every control change, so the button is only strictly needed "
            "at Summary level — where colony parameter / metric / well-"
            "selection changes are queued until you click it (one DBSCAN "
            "per (well × timepoint) frame is expensive to run on every "
            "keystroke)."
        ))
    recompute_btn.clicked.connect(lambda _=False: sp_ctrl.sp_recompute(ctx))
    reg.register("_sp_recompute_btn", recompute_btn)
    r1.addWidget(recompute_btn)

    export_csv_btn = IconButton(
        "download", row1,
        tooltip="Export Summary matrix as CSV (Summary level only)")
    export_csv_btn.clicked.connect(
        lambda _=False: sp_ctrl.sp_export_summary_csv(ctx))
    reg.register("_sp_export_csv_btn", export_csv_btn)
    r1.addWidget(export_csv_btn)

    copy_btn = IconButton("copy", row1, tooltip="Copy figure")
    copy_btn.clicked.connect(lambda _=False: _copy_spatial_figure(ctx))
    r1.addWidget(copy_btn)
    save_btn = IconButton("save", row1, tooltip="Save figure")
    save_btn.clicked.connect(lambda _=False: _save_spatial_figure(ctx))
    r1.addWidget(save_btn)

    layout.addWidget(row1)

    def _sync_summary_only_visibility() -> None:
        is_summary = st.level == sp_state.LEVEL_SUMMARY
        summary_metric_label.setVisible(is_summary)
        summary_metric_cb.setVisible(is_summary)
        summary_display_label.setVisible(is_summary)
        summary_display_cb.setVisible(is_summary)
        export_csv_btn.setEnabled(is_summary)
        # Single-timepoint picker is meaningless at Summary level (all tps
        # are walked). Keep it enabled but hint that it's ignored via tooltip.
        tp_cb.setEnabled(not is_summary)
    reg.register("_sp_sync_summary_visibility", _sync_summary_only_visibility)
    _sync_summary_only_visibility()

    # ── row 2: colony detection controls ─────────────────────────────────────
    row2 = QWidget(parent)
    row2.setObjectName("TabCtrl")
    r2 = QHBoxLayout(row2)
    r2.setContentsMargins(10, 2, 10, 6)

    colonies_label = QLabel("Colonies:", row2)
    colonies_label.setToolTip(
        "Colonies are auto-detected — no manual annotation. Adjust the "
        "controls in this row to sweep detection sensitivity live."
    )
    r2.addWidget(colonies_label)
    method_cb = QComboBox(row2)
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
    r2.addWidget(method_cb)

    eps_label = QLabel("ε ×median NND:", row2)
    eps_label.setToolTip(
        "Colony-membership radius ε as a multiple of the median "
        "nearest-neighbour distance in this frame. Self-scales to the "
        "well's density — the same multiplier gives roughly the same "
        "'grain' of clustering whether cells are packed or sparse.\n"
        "Larger ε → fewer, bigger colonies. Smaller ε → more, tighter "
        "colonies (and more noise cells under DBSCAN)."
    )
    r2.addWidget(eps_label)
    eps_spin = QDoubleSpinBox(row2)
    eps_spin.setRange(0.5, 20.0)
    eps_spin.setSingleStep(0.5)
    eps_spin.setDecimals(2)
    eps_spin.setValue(float(st.colony_eps_mult))
    eps_spin.setToolTip(eps_label.toolTip())
    eps_spin.valueChanged.connect(
        lambda v: sp_ctrl.sp_set_colony_eps_mult(ctx, v))
    reg.register("_sp_eps_spin", eps_spin)
    r2.addWidget(eps_spin)

    ms_label = QLabel("Min. samples:", row2)
    ms_label.setToolTip(
        "DBSCAN 'min-samples' — the minimum count of cells within ε "
        "required for a cell to seed a colony. Cells with fewer neighbours "
        "are marked noise (drawn grey on the spatial map).\n"
        "Ignored by Single-linkage (which never rejects a point)."
    )
    r2.addWidget(ms_label)
    ms_spin = QSpinBox(row2)
    ms_spin.setRange(1, 50)
    ms_spin.setValue(int(st.colony_min_samples))
    ms_spin.setToolTip(ms_label.toolTip())
    ms_spin.valueChanged.connect(
        lambda v: sp_ctrl.sp_set_colony_min_samples(ctx, v))
    reg.register("_sp_min_samples_spin", ms_spin)
    r2.addWidget(ms_spin)

    bnd_label = QLabel("Boundary %:", row2)
    bnd_label.setToolTip(
        "Fraction of each colony's cells (per colony, by percentile of "
        "distance-to-hull-edge) tagged 'boundary'. Drives the "
        "Boundary-vs-Interior violin split; 20 % is a sensible default."
    )
    r2.addWidget(bnd_label)
    bnd_spin = QDoubleSpinBox(row2)
    bnd_spin.setRange(1.0, 50.0)
    bnd_spin.setSingleStep(1.0)
    bnd_spin.setDecimals(1)
    bnd_spin.setValue(float(st.boundary_pct))
    bnd_spin.setToolTip(bnd_label.toolTip())
    bnd_spin.valueChanged.connect(
        lambda v: sp_ctrl.sp_set_boundary_pct(ctx, v))
    reg.register("_sp_boundary_spin", bnd_spin)
    r2.addWidget(bnd_spin)

    r2.addStretch(1)

    layout.addWidget(row2)

    # ── the figure ───────────────────────────────────────────────────────────
    card = PlotCard(parent, figsize=(_FIG_W, _FIG_H), constrained=False)
    card.setHeaderVisible(False)
    card.setFigureTitle("")
    try:
        card.setStatsChipVisible(False)
    except Exception:
        swallowed()
    reg.register("_sp_card", card)
    reg.register("_sp_fig", card.figure)
    reg.register("_sp_canvas", card.canvas)
    card.plotThemeChanged.connect(lambda _m: sp_ctrl.sp_recompute(ctx))
    layout.addWidget(card, 1)

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
                          and abs(d - value) <= 1e-6):
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


def _copy_spatial_figure(ctx) -> None:
    card = ctx.widgets.get_or_none("_sp_card")
    fig = getattr(card, "figure", None) if card is not None else None
    if fig is None:
        return
    try:
        status = copy_figure_to_clipboard(fig)
        ctx.set_status(status)
    except Exception:
        swallowed()


def _save_spatial_figure(ctx) -> None:
    card = ctx.widgets.get_or_none("_sp_card")
    fig = getattr(card, "figure", None) if card is not None else None
    if fig is None:
        return
    try:
        ctx.save_figure_dialog(fig, "spatial.png")
    except Exception:
        swallowed()
