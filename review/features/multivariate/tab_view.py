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

from review.core.ui_helpers import build_plot_save_menu
from review.features.export.service import export_cell_data_from
from review.features.multivariate.cell_scope_compute import cell_scope
from core.safe import swallowed
from review.features.multivariate import state as mv_state
from review.features.multivariate import controller as mv_ctrl
from review.features.multivariate.collect import (
    available_feature_columns, column_display_label,
)
from review.features.multivariate.feature_picker_view import build_feature_picker
from review.domain.tp_match import tp_close

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


from widgets.control_band import ControlBand
from widgets.labeled_combo import LabeledComboBox
from ui import theme

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_KSPIN_MIN_W = 64

def _muted(text: str, parent) -> QLabel:
    """A control label in the band's own idiom — muted, no trailing colon.

    ``ControlBand.add_scope`` styles the labels it creates this way; the per-mode
    option groups build their own inner labels, and without this they read as a
    different weight and punctuation from the ones beside them in the same row.
    """
    lbl = QLabel(text, parent)
    lbl.setObjectName("Muted")
    return lbl


def build_multivariate_tab(ctx, parent: QWidget) -> None:
    from widgets.plot_card import PlotCard
    from widgets.segmented_control import SegmentedControl
    from review.core.ui_helpers import (
        make_plot_with_right_dock, make_popover_button, no_selection_reason,
        wrap_with_empty_state,
    )

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

    # The Properties sidebar needs a dock to slide out of, exactly as the other
    # plot tabs have. Without one this tab had no Properties button at all, and
    # its figure was the only one in the pane that could not be styled.
    plot_area, layout, export_dock = make_plot_with_right_dock(parent)
    reg.register("_mv_export_dock", export_dock)
    parent = plot_area

    # ── the control band ────────────────────────────────────────────────────
    # One row where there were two: mode / features / standardize on the first
    # and the per-mode option groups plus the timepoint filter on the second.
    # Only one mode's option group is ever visible, and the band's flow skips a
    # hidden group, so the scope zone carries the mode's own controls and no
    # more.
    band = ControlBand(parent)

    mode_seg = SegmentedControl()
    for key, label in _MODE_ORDER:
        mode_seg.addSegment(text=label, data=key)
    mode_seg.setCurrentByData(st.mode)
    mode_seg.currentChanged.connect(
        lambda idx: mv_ctrl.mv_set_mode(ctx, _MODE_ORDER[idx][0]))
    reg.register("_mv_mode_seg", mode_seg)
    band.add_scope("Mode", mode_seg)

    feat_btn, _feat_panel, feat_lay = make_popover_button("Features ▾", band)
    feat_btn.setToolTip("Choose which per-cell feature columns form the matrix")
    build_feature_picker(ctx, feat_lay)
    band.add_action(feat_btn)

    std_cb = QCheckBox("Standardize")
    std_cb.setChecked(bool(st.standardize))
    std_cb.setToolTip("Z-score each feature before PCA / clustering "
                      "(essential when features span different units)")
    std_cb.toggled.connect(lambda on: mv_ctrl.mv_set_standardize(ctx, on))
    reg.register("_mv_standardize_cb", std_cb)
    band.add_scope("", std_cb)

    from review.core.ui_helpers import (
        make_properties_button,
    )
    export_btn, export_menu, _export_data_act = build_plot_save_menu(
        ctx, band,
        card_key="_mv_card",
        data_label="Save plot data…",
        data_cb=lambda: mv_ctrl.mv_export_csv(ctx),
        data_tooltip="The feature matrix as analysed",
        cells_cb=lambda: export_cell_data_from(
            ctx, cell_scope,
            unavailable="Tick at least one feature column first."),
    )
    band.add_action(export_btn)
    band.add_action(make_properties_button(ctx, band, "multivariate"))

    # ── per-mode option groups + the timepoint filter ───────────────────────
    # Correlation: heatmap / SPLOM.
    corr_frame = QWidget()
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
    band.add_scope("", corr_frame)

    # Dim-reduction: method + colour-by.
    dr_frame = QWidget()
    df = QHBoxLayout(dr_frame)
    df.setContentsMargins(0, 0, 0, 0)
    df.addWidget(_muted("Method", dr_frame))
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
    df.addWidget(_muted("Colour by", dr_frame))
    color_cb = QComboBox(dr_frame)
    for key, label in _COLOR_BY:
        color_cb.addItem(label, key)
    _select_combo_by_data(color_cb, st.color_by)
    color_cb.currentIndexChanged.connect(
        lambda _i: mv_ctrl.mv_set_color_by(ctx, color_cb.currentData()))
    reg.register("_mv_color_cb", color_cb)
    df.addWidget(color_cb)
    reg.register("_mv_dimred_frame", dr_frame)
    band.add_scope("", dr_frame)

    # Clustering: method + count + linkage.
    cl_frame = QWidget()
    clf = QHBoxLayout(cl_frame)
    clf.setContentsMargins(0, 0, 0, 0)
    clf.addWidget(_muted("Cluster", cl_frame))
    cmethod_cb = QComboBox(cl_frame)
    cmethod_cb.addItem("k-means", "kmeans")
    cmethod_cb.addItem("Hierarchical", "hierarchical")
    _select_combo_by_data(cmethod_cb, st.cluster_method)
    cmethod_cb.currentIndexChanged.connect(
        lambda _i: mv_ctrl.mv_set_cluster_method(ctx, cmethod_cb.currentData()))
    reg.register("_mv_cmethod_cb", cmethod_cb)
    clf.addWidget(cmethod_cb)
    clf.addWidget(_muted("k", cl_frame))
    kspin = QSpinBox(cl_frame)
    kspin.setRange(2, 20)
    # Reserve room for two digits + the up/down arrows; without a minimum the
    # crowded horizontal row squeezes this (width-less) spinbox until the value
    # clips.
    kspin.setMinimumWidth(_KSPIN_MIN_W)
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
    band.add_scope("", cl_frame)

    # Faceting: value + row + col + agg.
    fc_frame = QWidget()
    fcf = QHBoxLayout(fc_frame)
    fcf.setContentsMargins(0, 0, 0, 0)
    fcf.addWidget(_muted("Value", fc_frame))
    fvalue_cb = QComboBox(fc_frame)
    fvalue_cb.setMinimumWidth(theme.Metrics.combo_min_w)
    fvalue_cb.currentIndexChanged.connect(
        lambda _i: mv_ctrl.mv_set_facet_value(ctx, fvalue_cb.currentData() or ""))
    reg.register("_mv_facet_value_cb", fvalue_cb)
    fcf.addWidget(fvalue_cb)
    fcf.addWidget(_muted("Rows", fc_frame))
    frow_cb = QComboBox(fc_frame)
    for key, label in _FACET_FACTORS:
        frow_cb.addItem(label, key)
    _select_combo_by_data(frow_cb, st.facet_row)
    frow_cb.currentIndexChanged.connect(
        lambda _i: mv_ctrl.mv_set_facet_row(ctx, frow_cb.currentData()))
    fcf.addWidget(frow_cb)
    fcf.addWidget(_muted("Cols", fc_frame))
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
    band.add_scope("", fc_frame)

    # Timepoint filter — shared across modes. Self-describing (LabeledComboBox
    # paints its own "Timepoint: …" prefix), so ``add_scope`` adds it alone —
    # no separate caption to strand on a different line when the wide Faceting
    # option group ahead of it wraps.
    tp_cb = LabeledComboBox("Timepoint")
    tp_cb.addItem("All timepoints", None)
    tp_cb.currentIndexChanged.connect(
        lambda _i: mv_ctrl.mv_set_timepoints(
            ctx, [] if tp_cb.currentData() is None else [tp_cb.currentData()]))
    reg.register("_mv_tp_cb", tp_cb)
    band.add_scope("", tp_cb)
    layout.addWidget(band)

    # ── the figure ───────────────────────────────────────────────────────────
    card = PlotCard(parent, figsize=(_FIG_W, _FIG_H), constrained=False,
                    header=False)
    card.setFigureTitle("")
    try:
        card.setStatsChipVisible(False)
    except Exception:
        swallowed()
    reg.register("_mv_card", card)
    reg.register("_mv_fig", card.figure)
    reg.register("_mv_canvas", card.canvas)
    from review.core.ui_helpers import apply_plot_card_decorators
    apply_plot_card_decorators(ctx, card, "Multivariate")
    card.plotThemeChanged.connect(lambda _m: mv_ctrl.mv_recompute(ctx))
    # No ``text=`` override: this was the one site wording the no-dataset state
    # differently from the other eleven, and the point of the shared placeholder
    # is that the same state reads the same everywhere.
    #
    # The selection gate is what separates two causes that were previously
    # indistinguishable here — with four wells selected and no feature columns
    # this tab showed the *identical* "Select one or more feature columns" line
    # it showed with no wells at all. Now: no wells gets the placeholder, and
    # that message means what it says.
    layout.addWidget(wrap_with_empty_state(
        ctx.window, card, icon="scatter-chart",
        reason=no_selection_reason(
            hint="Click wells on the plate at the left, "
                 "then pick columns under Features.",
        ),
    ), 1)

    # ── closures the controller calls back into ──────────────────────────────
    def _refresh_timepoints() -> None:
        from review.data.data_loading import get_all_timepoints
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
        # Reflect the authoritative state onto the persisted input widgets. Without
        # this, a programmatic state change (view-state restore on dataset load, or
        # any non-combo mutation) recomputes the figure with the new value while the
        # control keeps showing its build-time one — e.g. the method combo reading
        # "PCA" while the axes render t-SNE. Signals are blocked so re-selecting a
        # control here doesn't re-enter ``_apply`` / trigger a redundant recompute.
        for combo, value in ((method_cb, st.dimred_method),
                             (color_cb, st.color_by),
                             (cmethod_cb, st.cluster_method)):
            combo.blockSignals(True)
            _select_combo_by_data(combo, value)
            combo.blockSignals(False)
        std_cb.blockSignals(True)
        std_cb.setChecked(bool(st.standardize))
        std_cb.blockSignals(False)

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
                          and tp_close(d, value)):
            combo.setCurrentIndex(i)
            return


