"""Multivariate (higher-dimensional analysis) tab builder.

Assembles the tab's controls (mode switch, dynamic feature picker, per-mode
option rows, timepoint filter, export/copy/save) and the matplotlib ``PlotCard``,
then wires every control to the ``controller`` functions. Threads the frozen
``ViewerContext`` throughout — feature state via ``ctx.state``, live objects via
``ctx.runtime.multivariate``, and figure/canvas/widget handles via ``ctx.widgets``
(the registry stamps ``app._mv_*`` so legacy readers keep resolving), exactly like
``barplot/tab_view.py``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget,
)

from well_viewer.core.safe import swallowed
from well_viewer.features.export.style import copy_figure_to_clipboard
from well_viewer.features.multivariate import state as mv_state
from well_viewer.features.multivariate import controller as mv_ctrl
from well_viewer.features.multivariate.collect import (
    available_feature_columns, column_display_label,
)
from well_viewer.features.multivariate.feature_picker_view import build_feature_picker

_FIG_W, _FIG_H, _DPI = 7.6, 6.6, 100

_MODE_ORDER = (
    (mv_state.MODE_CORRELATION, "Correlation"),
    (mv_state.MODE_DIMRED, "Dim. Reduction"),
    (mv_state.MODE_CLUSTERING, "Clustering"),
    (mv_state.MODE_FACET, "Faceting"),
)
_COLOR_BY = (("group", "Group"), ("well", "Well"),
             ("timepoint", "Timepoint"), ("cluster", "Cluster"))
_FACET_FACTORS = (("well", "Well"), ("timepoint", "Timepoint"), ("group", "Group"))
_AGGS = ("mean", "median", "count", "std")


from widgets.flow_layout import FlowLayout

def build_multivariate_tab(ctx, parent: QWidget) -> None:
    from widgets.plot_card import PlotCard
    from widgets.icon_button import IconButton
    from widgets.segmented_control import SegmentedControl
    from well_viewer.core.ui_helpers import make_popover_button, wrap_with_empty_state

    st = ctx.state.feature_slice("multivariate")
    # A8 burn-down: widget handles go through the WidgetRegistry
    # (``ctx.widgets``), which stamps ``app._mv_*`` so legacy readers resolve.
    reg = ctx.widgets

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # ── controls row 1: mode + features + standardize + actions ──────────────
    row1 = QWidget(parent)
    row1.setObjectName("TabCtrl")
    r1 = FlowLayout(row1)
    r1.setContentsMargins(10, 6, 10, 4)

    mode_seg = SegmentedControl(row1)
    for key, label in _MODE_ORDER:
        mode_seg.addSegment(text=label, data=key)
    mode_seg.setCurrentByData(st.mode)
    mode_seg.currentChanged.connect(
        lambda idx: mv_ctrl.mv_set_mode(ctx, _MODE_ORDER[idx][0]))
    reg.register("_mv_mode_seg", mode_seg)
    r1.addWidget(mode_seg)

    feat_btn, _feat_panel, feat_lay = make_popover_button("Features ▾", row1)
    feat_btn.setToolTip("Choose which per-cell feature columns form the matrix")
    build_feature_picker(ctx, feat_lay)
    r1.addWidget(feat_btn)

    std_cb = QCheckBox("Standardize", row1)
    std_cb.setChecked(bool(st.standardize))
    std_cb.setToolTip("Z-score each feature before PCA / clustering "
                      "(essential when features span different units)")
    std_cb.toggled.connect(lambda on: mv_ctrl.mv_set_standardize(ctx, on))
    reg.register("_mv_standardize_cb", std_cb)
    r1.addWidget(std_cb)

    r1.addStretch(1)

    export_btn = IconButton("download", row1, tooltip="Export feature matrix (CSV)")
    export_btn.clicked.connect(lambda _=False: mv_ctrl.mv_export_csv(ctx))
    r1.addWidget(export_btn)
    copy_btn = IconButton("copy", row1, tooltip="Copy figure")
    copy_btn.clicked.connect(lambda _=False: _copy_figure(ctx))
    r1.addWidget(copy_btn)
    save_btn = IconButton("save", row1, tooltip="Save figure")
    save_btn.clicked.connect(lambda _=False: _save_figure(ctx))
    r1.addWidget(save_btn)
    layout.addWidget(row1)

    # ── controls row 2: per-mode options + timepoint ─────────────────────────
    row2 = QWidget(parent)
    row2.setObjectName("TabCtrl")
    r2 = FlowLayout(row2)
    r2.setContentsMargins(10, 2, 10, 6)

    # Correlation: heatmap / SPLOM.
    corr_frame = QWidget(row2)
    cf = QHBoxLayout(corr_frame)
    cf.setContentsMargins(0, 0, 0, 0)
    corr_seg = SegmentedControl(corr_frame)
    corr_seg.addSegment(text="Heatmap", data="heatmap")
    corr_seg.addSegment(text="Pair matrix", data="splom")
    corr_seg.setCurrentByData(st.corr_view)
    corr_seg.currentChanged.connect(
        lambda idx: mv_ctrl.mv_set_corr_view(ctx, "heatmap" if idx == 0 else "splom"))
    cf.addWidget(corr_seg)
    reg.register("_mv_corr_frame", corr_frame)
    r2.addWidget(corr_frame)

    # Dim-reduction: method + colour-by.
    dr_frame = QWidget(row2)
    df = QHBoxLayout(dr_frame)
    df.setContentsMargins(0, 0, 0, 0)
    df.addWidget(QLabel("Method:", dr_frame))
    method_cb = QComboBox(dr_frame)
    avail = mv_ctrl.mv_dimred_methods_available()
    for key, label in (("pca", "PCA"), ("umap", "UMAP"), ("tsne", "t-SNE")):
        method_cb.addItem(label, key)
        idx = method_cb.count() - 1
        if not avail.get(key, False):
            # Grey out unavailable methods; a tooltip points at the fix.
            method_cb.model().item(idx).setEnabled(False)
            method_cb.setItemData(
                idx, f"Install scikit-learn / umap-learn to enable {label}",
                Qt.ToolTipRole)
    _select_combo_by_data(method_cb, st.dimred_method)
    method_cb.currentIndexChanged.connect(
        lambda _i: mv_ctrl.mv_set_dimred_method(ctx, method_cb.currentData()))
    reg.register("_mv_method_cb", method_cb)
    df.addWidget(method_cb)
    df.addWidget(QLabel("Colour by:", dr_frame))
    color_cb = QComboBox(dr_frame)
    for key, label in _COLOR_BY:
        color_cb.addItem(label, key)
    _select_combo_by_data(color_cb, st.color_by)
    color_cb.currentIndexChanged.connect(
        lambda _i: mv_ctrl.mv_set_color_by(ctx, color_cb.currentData()))
    reg.register("_mv_color_cb", color_cb)
    df.addWidget(color_cb)
    reg.register("_mv_dimred_frame", dr_frame)
    r2.addWidget(dr_frame)

    # Clustering: method + count + linkage.
    cl_frame = QWidget(row2)
    clf = QHBoxLayout(cl_frame)
    clf.setContentsMargins(0, 0, 0, 0)
    clf.addWidget(QLabel("Cluster:", cl_frame))
    cmethod_cb = QComboBox(cl_frame)
    cmethod_cb.addItem("k-means", "kmeans")
    cmethod_cb.addItem("Hierarchical", "hierarchical")
    _select_combo_by_data(cmethod_cb, st.cluster_method)
    cmethod_cb.currentIndexChanged.connect(
        lambda _i: mv_ctrl.mv_set_cluster_method(ctx, cmethod_cb.currentData()))
    reg.register("_mv_cmethod_cb", cmethod_cb)
    clf.addWidget(cmethod_cb)
    clf.addWidget(QLabel("k:", cl_frame))
    kspin = QSpinBox(cl_frame)
    kspin.setRange(2, 20)
    # Reserve room for two digits + the up/down arrows; without a minimum the
    # crowded horizontal row squeezes this (width-less) spinbox until the value
    # clips.
    kspin.setMinimumWidth(64)
    kspin.setValue(int(st.cluster_count))
    kspin.valueChanged.connect(lambda v: mv_ctrl.mv_set_cluster_count(ctx, v))
    reg.register("_mv_kspin", kspin)
    clf.addWidget(kspin)
    linkage_cb = QComboBox(cl_frame)
    for m in ("ward", "average", "complete", "single"):
        linkage_cb.addItem(m, m)
    _select_combo_by_data(linkage_cb, st.linkage_method)
    linkage_cb.currentIndexChanged.connect(
        lambda _i: mv_ctrl.mv_set_linkage(ctx, linkage_cb.currentData()))
    reg.register("_mv_linkage_cb", linkage_cb)
    clf.addWidget(linkage_cb)
    reg.register("_mv_cluster_frame", cl_frame)
    r2.addWidget(cl_frame)

    # Faceting: value + row + col + agg.
    fc_frame = QWidget(row2)
    fcf = QHBoxLayout(fc_frame)
    fcf.setContentsMargins(0, 0, 0, 0)
    fcf.addWidget(QLabel("Value:", fc_frame))
    fvalue_cb = QComboBox(fc_frame)
    fvalue_cb.setMinimumWidth(140)
    fvalue_cb.currentIndexChanged.connect(
        lambda _i: mv_ctrl.mv_set_facet_value(ctx, fvalue_cb.currentData() or ""))
    reg.register("_mv_facet_value_cb", fvalue_cb)
    fcf.addWidget(fvalue_cb)
    fcf.addWidget(QLabel("Rows:", fc_frame))
    frow_cb = QComboBox(fc_frame)
    for key, label in _FACET_FACTORS:
        frow_cb.addItem(label, key)
    _select_combo_by_data(frow_cb, st.facet_row)
    frow_cb.currentIndexChanged.connect(
        lambda _i: mv_ctrl.mv_set_facet_row(ctx, frow_cb.currentData()))
    fcf.addWidget(frow_cb)
    fcf.addWidget(QLabel("Cols:", fc_frame))
    fcol_cb = QComboBox(fc_frame)
    for key, label in _FACET_FACTORS:
        fcol_cb.addItem(label, key)
    _select_combo_by_data(fcol_cb, st.facet_col)
    fcol_cb.currentIndexChanged.connect(
        lambda _i: mv_ctrl.mv_set_facet_col(ctx, fcol_cb.currentData()))
    fcf.addWidget(fcol_cb)
    agg_cb = QComboBox(fc_frame)
    for a in _AGGS:
        agg_cb.addItem(a, a)
    _select_combo_by_data(agg_cb, st.facet_agg)
    agg_cb.currentIndexChanged.connect(
        lambda _i: mv_ctrl.mv_set_facet_agg(ctx, agg_cb.currentData()))
    fcf.addWidget(agg_cb)
    reg.register("_mv_facet_frame", fc_frame)
    r2.addWidget(fc_frame)

    r2.addStretch(1)

    # Timepoint filter — shared across modes.
    r2.addWidget(QLabel("Timepoint:", row2))
    tp_cb = QComboBox(row2)
    tp_cb.addItem("All timepoints", None)
    tp_cb.currentIndexChanged.connect(
        lambda _i: mv_ctrl.mv_set_timepoints(
            ctx, [] if tp_cb.currentData() is None else [tp_cb.currentData()]))
    reg.register("_mv_tp_cb", tp_cb)
    r2.addWidget(tp_cb)
    layout.addWidget(row2)

    # ── the figure ───────────────────────────────────────────────────────────
    card = PlotCard(parent, figsize=(_FIG_W, _FIG_H), constrained=False)
    card.setHeaderVisible(False)
    card.setFigureTitle("")
    try:
        card.setStatsChipVisible(False)
    except Exception:
        swallowed()
    reg.register("_mv_card", card)
    reg.register("_mv_fig", card.figure)
    reg.register("_mv_canvas", card.canvas)
    card.plotThemeChanged.connect(lambda _m: mv_ctrl.mv_recompute(ctx))
    layout.addWidget(wrap_with_empty_state(
        ctx.window, card, icon="scatter-chart",
        text="Load a directory to explore the feature space.",
    ), 1)

    # ── closures the controller calls back into ──────────────────────────────
    def _refresh_timepoints() -> None:
        from well_viewer.data.data_loading import get_all_timepoints
        blocked = tp_cb.blockSignals(True)
        try:
            current = st.timepoints[0] if st.timepoints else None
            tp_cb.clear()
            tp_cb.addItem("All timepoints", None)
            for t in get_all_timepoints(ctx):
                tp_cb.addItem(f"{t:g} h", float(t))
            if current is not None:
                _select_combo_by_data(tp_cb, current)
        finally:
            tp_cb.blockSignals(blocked)

    def _refresh_facet_values() -> None:
        blocked = fvalue_cb.blockSignals(True)
        try:
            fvalue_cb.clear()
            for _family, cols in available_feature_columns(ctx).items():
                for col in cols:
                    fvalue_cb.addItem(column_display_label(ctx, col), col)
            if st.facet_value_col:
                _select_combo_by_data(fvalue_cb, st.facet_value_col)
            elif fvalue_cb.count():
                st.facet_value_col = fvalue_cb.currentData() or ""
        finally:
            fvalue_cb.blockSignals(blocked)

    def _sync_controls() -> None:
        mode = st.mode
        corr_frame.setVisible(mode == mv_state.MODE_CORRELATION)
        dr_frame.setVisible(mode == mv_state.MODE_DIMRED)
        cl_frame.setVisible(mode == mv_state.MODE_CLUSTERING)
        fc_frame.setVisible(mode == mv_state.MODE_FACET)
        linkage_cb.setVisible(st.cluster_method == "hierarchical")
        # Standardization does not apply to correlation (scale-invariant).
        std_cb.setEnabled(mode != mv_state.MODE_CORRELATION)
        mode_seg.setCurrentByData(mode)

    ctx.runtime.multivariate.refresh_timepoints = _refresh_timepoints
    ctx.runtime.multivariate.refresh_facet_values = _refresh_facet_values
    ctx.runtime.multivariate.sync_controls = _sync_controls

    _refresh_timepoints()
    _refresh_facet_values()
    _sync_controls()


# ── module helpers ───────────────────────────────────────────────────────────

def _select_combo_by_data(combo, value) -> None:
    for i in range(combo.count()):
        d = combo.itemData(i)
        if d == value or (isinstance(d, float) and isinstance(value, (int, float))
                          and abs(d - value) <= 1e-6):
            combo.setCurrentIndex(i)
            return


def _copy_figure(ctx) -> None:
    card = ctx.runtime.multivariate.card
    if card is None:
        return
    fig = getattr(card, "figure", None)
    if fig is None:
        return
    try:
        ctx.set_status(copy_figure_to_clipboard(fig))
    except Exception:
        swallowed()


def _save_figure(ctx) -> None:
    card = ctx.runtime.multivariate.card
    fig = getattr(card, "figure", None) if card is not None else None
    if fig is None:
        return
    try:
        ctx.save_figure_dialog(fig, "multivariate.png")
    except Exception:
        swallowed()
