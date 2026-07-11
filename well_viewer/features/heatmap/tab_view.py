"""Heat Map tab builder.

User-defined R×C grid colored by a chosen metric for one timepoint, with a
slider to scrub through time and click-to-select wells.
"""

from __future__ import annotations

from well_viewer.core.channel_state_controller import on_plot_channel_selected

from well_viewer.core.plot_card_actions import (
    copy_active_card_as_svg, save_active_card_figure,
)

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QInputDialog, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from well_viewer.core.ui_helpers import (
    make_plot_view_switcher,
    make_plot_with_right_dock,
)
from well_viewer.core.channel_state_controller import refresh_metric_combo_for_channel
from well_viewer.features.export.service import export_heatmap_data
from well_viewer.core.channel_state_controller import refresh_heatmap_aggregation_options
from well_viewer.core.safe import swallowed


_CMAP_OPTIONS = ["viridis", "magma", "coolwarm", "RdYlBu_r", "Greys"]
_SCALE_OPTIONS = ["Auto", "Fixed"]


from widgets.flow_layout import FlowLayout

def build_heatmap_tab(ctx, parent: QWidget) -> None:
    # Defer matplotlib + heatmap_controller imports until the tab actually
    # builds. heatmap_controller pulls in matplotlib.patches and numpy at
    # module load, so importing it lazily keeps utility entry points like
    # ``refresh_heatmap_timepoints`` cheap.
    from well_viewer.features.heatmap.controller import METRIC_OPTIONS
    # A8 burn-down: widget handles go through the WidgetRegistry (``ctx.widgets``),
    # which also stamps ``app._<name>`` so legacy ``app._heatmap_*`` readers in the
    # heatmap controller keep resolving. Non-widget state (scale mode / flags /
    # caches) and behaviour calls still reach the app — a later layer.
    reg = ctx.widgets

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    plot_area, layout, export_dock = make_plot_with_right_dock(parent)
    reg.register("_heatmap_export_dock", export_dock)
    parent = plot_area

    # ── Control row 1: channel / metric ─────────────────────────────────────
    # The layout itself is edited from the sidebar configurator on the left
    # (see ``views/heatmap_layout_sidebar_view.py``); this row is for the
    # plotting parameters only.
    ctrl1 = QWidget(parent)
    ctrl1.setObjectName("TabCtrl")
    cl1 = FlowLayout(ctrl1)
    cl1.setContentsMargins(10, 6, 10, 6)

    # Channel combo hidden — global ctxbar combo is the only visible
    # channel control. See line_graphs_tab_view.py for rationale.
    chan_cb = reg.register("_chan_cb_heatmap", QComboBox())
    chan_cb.addItems(["GFP"])
    chan_cb.setMinimumWidth(220)
    chan_cb.currentIndexChanged.connect(
        lambda _i, _src=chan_cb: on_plot_channel_selected(ctx, _src)
    )
    chan_cb.hide()

    # The global ctxbar Property combo is the single source of truth for
    # which CSV column drives the heatmap — the per-tab Property combo
    # used to live here, but the duplicate (one in the ctxbar, one in
    # this row) was confusing. Only the Aggregation combo stays local.
    cl1.addWidget(QLabel("Aggregation:", ctrl1))
    metric_cb = reg.register("_heatmap_metric_cb", QComboBox(ctrl1))
    metric_cb.addItems(METRIC_OPTIONS)
    metric_cb.currentIndexChanged.connect(lambda _i: _redraw(ctx))
    cl1.addWidget(metric_cb)

    cl1.addStretch(1)

    from widgets.icon_button import IconButton as _IconButton
    _export_btn = _IconButton("download", ctrl1, tooltip="Export CSV")
    _export_btn.clicked.connect(lambda _=False: export_heatmap_data(ctx))
    cl1.addWidget(_export_btn)
    _copy_btn = _IconButton("copy", ctrl1, tooltip="Copy SVG")
    _copy_btn.clicked.connect(lambda _=False: copy_active_card_as_svg(ctx))
    cl1.addWidget(_copy_btn)
    _save_btn = _IconButton("save", ctrl1, tooltip="Save figure")
    _save_btn.clicked.connect(lambda _=False: save_active_card_figure(ctx))
    cl1.addWidget(_save_btn)
    # Pin the figure-properties button to the right edge of the plot area (where
    # the export-style dock slides out); the FlowLayout row can't right-align it.
    from well_viewer.core.ui_helpers import wrap_ctrl_row_with_properties
    layout.addWidget(wrap_ctrl_row_with_properties(
        ctrl1, lambda: ctx.open_export_style_panel("heatmap")))

    # ── Control row 2: cmap / scale / log ───────────────────────────────────
    ctrl2 = QWidget(parent)
    ctrl2.setObjectName("TabCtrl")
    cl2 = FlowLayout(ctrl2)
    cl2.setContentsMargins(10, 0, 10, 6)

    cl2.addWidget(QLabel("Color map:", ctrl2))
    # v2: LutSelector (gradient strip + name + searchable popover + reverse-LUT
    # button) in place of the legacy QComboBox of mpl colormap names.
    from widgets.lut_selector import LutSelector
    cmap_cb = reg.register("_heatmap_cmap_cb", LutSelector(ctrl2))
    # Seed with the first option (matches the legacy default).
    _initial_cmap = _CMAP_OPTIONS[0] if _CMAP_OPTIONS else "viridis"
    _initial_rev = _initial_cmap.endswith("_r")
    cmap_cb.setLut(_initial_cmap[:-2] if _initial_rev else _initial_cmap,
                   reversed=_initial_rev)
    cmap_cb.setMaximumWidth(220)
    cmap_cb.lutChanged.connect(lambda *_a: _on_cmap_changed(ctx))
    cl2.addWidget(cmap_cb)

    cl2.addWidget(QLabel("Scale:", ctrl2))
    scale_cb = reg.register("_heatmap_scale_cb", QComboBox(ctrl2))
    scale_cb.addItems(_SCALE_OPTIONS)
    scale_cb.currentIndexChanged.connect(lambda _i: _on_scale_changed(ctx))
    cl2.addWidget(scale_cb)

    cl2.addWidget(QLabel("vmin:", ctrl2))
    vmin_edit = reg.register("_heatmap_vmin_edit", QLineEdit(ctrl2))
    vmin_edit.setFixedWidth(70)
    vmin_edit.editingFinished.connect(lambda: _on_vminmax_changed(ctx))
    cl2.addWidget(vmin_edit)

    cl2.addWidget(QLabel("vmax:", ctrl2))
    vmax_edit = reg.register("_heatmap_vmax_edit", QLineEdit(ctrl2))
    vmax_edit.setFixedWidth(70)
    vmax_edit.editingFinished.connect(lambda: _on_vminmax_changed(ctx))
    cl2.addWidget(vmax_edit)

    transpose_btn = QPushButton("⇄ Transpose", ctrl2)
    transpose_btn.setProperty("variant", "secondary")
    transpose_btn.setToolTip("Swap rows and columns of the heatmap layout")
    transpose_btn.clicked.connect(lambda _=False: _on_transpose(ctx))
    cl2.addWidget(transpose_btn)

    repset_avg_cb = reg.register("_heatmap_repset_avg_cb", QCheckBox("Rep-set avg", ctrl2))
    repset_avg_cb.setToolTip(
        "When checked, cells whose well belongs to a rep-set show the "
        "average of the whole rep-set instead of the single well."
    )
    repset_avg_cb.toggled.connect(lambda _v: _on_repset_avg_changed(ctx))
    cl2.addWidget(repset_avg_cb)

    log_scale_cb = reg.register("_heatmap_log_scale_cb", QCheckBox("Log scale", ctrl2))
    log_scale_cb.setToolTip(
        "Color the heatmap on a log scale (values ≤ 0 are masked)."
    )
    log_scale_cb.toggled.connect(lambda _v: _on_log_scale_changed(ctx))
    cl2.addWidget(log_scale_cb)

    cl2.addStretch(1)

    status_lbl = reg.register("_heatmap_status_lbl", QLabel("", ctrl2))
    status_lbl.setObjectName("Muted")
    cl2.addWidget(status_lbl)

    layout.addWidget(ctrl2)

    # ── Timepoint slider (placed directly under the option rows) ────────────
    slider_row = QWidget(parent)
    slider_row.setObjectName("TabCtrl")
    sl = FlowLayout(slider_row)
    sl.setContentsMargins(10, 4, 10, 6)
    sl.addWidget(QLabel("t (h):", slider_row))
    from widgets.styled_slider import StyledSlider as _StyledSlider
    tp_slider = reg.register("_heatmap_tp_slider", _StyledSlider(Qt.Horizontal, slider_row))
    tp_slider.setMinimum(0)
    tp_slider.setMaximum(0)
    tp_slider.valueChanged.connect(lambda _v: _on_tp_changed(ctx))
    sl.addWidget(tp_slider, 1)
    tp_label = reg.register("_heatmap_tp_label", QLabel("—", slider_row))
    tp_label.setMinimumWidth(80)
    sl.addWidget(tp_label)
    layout.addWidget(slider_row)

    sep = QFrame(parent)
    sep.setFrameShape(QFrame.HLine)
    sep.setFixedHeight(1)
    layout.addWidget(sep)

    # ── Figure (in a v2 PlotCard — card chrome + MplToolbar) ────────────────
    from widgets.plot_card import PlotCard
    card = PlotCard(parent, figsize=(8.0, 6.0), constrained=False)
    if ctx.features.get("Saved Plots") is not None:
        from well_viewer.features.saved_plots.controller import wire_save_to_gallery
        wire_save_to_gallery(ctx, card, "Heat Map")
    _sw = make_plot_view_switcher(ctx, 'Heat Map')
    if _sw is not None:
        card.setLeftHeaderWidget(_sw)
    # Hide the per-card header — the ctxbar above already carries the same controls.
    card.setHeaderVisible(False)
    card.setFigureTitle("")
    reg.register("_heatmap_card", card)
    fig = reg.register("_heatmap_fig", card.figure)
    ax = reg.register("_heatmap_ax", fig.add_subplot(1, 1, 1))
    # Reserve top margin for the (sometimes long) title so it doesn't get
    # cropped — the default ``top=0.9`` clipped titles like
    # "MCHERRY Mean Intensity — Mean above threshold — t = 12 h".
    fig.subplots_adjust(top=0.88, bottom=0.10, left=0.10, right=0.94)
    # Reserve a persistent colorbar axes via make_axes_locatable so each
    # redraw can refill it in place. Letting fig.colorbar(im, ax=ax)
    # allocate a fresh axes every redraw was the cause of the heatmap
    # plot shrinking and drifting left on every refresh — matplotlib does
    # not restore the original ax position when the colorbar is removed.
    from mpl_toolkits.axes_grid1 import make_axes_locatable
    divider = make_axes_locatable(ax)
    reg.register("_heatmap_cax", divider.append_axes("right", size="4%", pad=0.08))
    canvas = reg.register("_heatmap_canvas", card.canvas)
    # Heatmap never draws error bands or per-FOV spread — it shows a
    # single aggregated value per grid cell. The shared band controls
    # would only mislead the user, so leave the card's controls row
    # empty.
    card.setControlsWidget(None)
    # NOTE: don't trigger a redraw on theme change — heatmap_controller doesn't
    # use plot_style.apply_ax_style (it has its own inline ax styling), so a
    # redraw would wipe the widget-side theme styling that setPlotTheme already
    # applied. Let PlotCard.setPlotTheme's apply_axes_style walk stand.
    card.setStatsChipVisible(False)
    from well_viewer.core.ui_helpers import wrap_with_empty_state
    layout.addWidget(wrap_with_empty_state(
        ctx.window, card, icon="layout-grid",
    ), 1)

    # ── Mouse hooks ─────────────────────────────────────────────────────────
    # Unified press handler: label-drag first, then double-click rename, then
    # normal cell selection.  A single connection keeps ordering deterministic.
    canvas.mpl_connect("button_press_event",
                       lambda evt: _on_canvas_press(ctx, evt))
    canvas.mpl_connect("motion_notify_event",
                       lambda evt: _on_canvas_motion(ctx, evt))
    canvas.mpl_connect("button_release_event",
                       lambda evt: _on_canvas_release(ctx, evt))

    refresh_heatmap_timepoints(ctx)

    # Push any settings loaded from heatmap_layouts.json into the widgets.
    try:
        from well_viewer.persistence.heatmap_layouts import apply_persisted_settings
        apply_persisted_settings(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)

    # Refresh the global Property combo + heatmap aggregation enable
    # state so the heatmap's first render reflects the canonical
    # ``_active_metric`` (ratio channels disable the global combo and
    # collapse it to ``Calculated Val``; non-MFI properties grey out
    # threshold-based aggregations).
    # A8: these took app-method hasattr guards that were always False
    # (WellViewerApp has no _refresh_metric_combo_for_channel /
    # _refresh_heatmap_aggregation_options methods — bound nowhere), so this
    # first-render Property-combo + aggregation sync silently never ran. Both
    # core fns now take ctx and self-guard on a missing combo, so call them
    # directly (still swallow-wrapped) to realise the documented intent.
    try:
        refresh_metric_combo_for_channel(ctx)
        refresh_heatmap_aggregation_options(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)
    # Bust any caches that might hold a stale "no values" result from
    # before the tab was fully built (data load timing can leave the
    # global colormap range cache populated with NaNs, which produced
    # empty cells until the user wiggled the channel).
    ctx.runtime.heatmap.global_range_cache = None
    from well_viewer.core.selection_controller import invalidate_stats_cache
    invalidate_stats_cache(ctx)
    _redraw(ctx)


# ── helpers attached or referenced from runtime_app ─────────────────────────

def refresh_heatmap_timepoints(ctx) -> None:
    # A8-teardown T3: modern infrastructure — slider/label via ``ctx.widgets``,
    # the timepoint-value cache via ``ctx.runtime.heatmap``, timepoints/focus via
    # the ctx leaves. The onward ``_redraw`` is the one render handoff residual
    # (the heatmap render tree still takes ``app`` — a later one-feature pass).
    slider = ctx.widgets.get_or_none("_heatmap_tp_slider")
    label = ctx.widgets.get_or_none("_heatmap_tp_label")
    if slider is None:
        return
    try:
        from well_viewer.features.scatter.controller import get_all_timepoints
        tps = list(get_all_timepoints(ctx))
    except Exception:
        tps = []
    ctx.state.heatmap.tp_values = tps
    # Seed the slider from the shared focus timepoint (N5) when one is set and
    # present, so opening Heat Map after picking a timepoint on Distribution
    # lands on the same point; else default to the first timepoint.
    from well_viewer.core.timepoint_sync import current_focus_tp, nearest_index
    focus_idx = nearest_index(tps, current_focus_tp(ctx)) if tps else None
    start_idx = focus_idx if focus_idx is not None else 0
    blocked = slider.blockSignals(True)
    try:
        slider.setMinimum(0)
        slider.setMaximum(max(0, len(tps) - 1))
        slider.setValue(start_idx)
    finally:
        slider.blockSignals(blocked)
    if label is not None:
        if tps:
            label.setText(f"t = {tps[start_idx]:g} h")
        else:
            label.setText("—")
    _redraw(ctx)


def _on_tp_changed(ctx) -> None:
    tps: List[float] = list(ctx.state.heatmap.tp_values or [])
    slider = ctx.widgets.get_or_none("_heatmap_tp_slider")
    label = ctx.widgets.get_or_none("_heatmap_tp_label")
    if not tps or slider is None:
        return
    idx = max(0, min(slider.value(), len(tps) - 1))
    if label is not None:
        label.setText(f"t = {tps[idx]:g} h")
    # Publish the shared focus timepoint (N5). In Split View, emit so the other
    # pane (Distribution) redraws live; otherwise update the value silently and
    # redraw only this tab — keeps slider scrubbing as cheap as before (no
    # offscreen fan-out). Other tabs pick the value up on next show.
    from well_viewer.core.timepoint_sync import set_focus_tp
    if ctx.runtime.split.active:
        set_focus_tp(ctx, tps[idx])
    else:
        set_focus_tp(ctx, tps[idx], emit=False)
        _redraw(ctx)


def _on_scale_changed(ctx) -> None:
    import math as _math
    cb = ctx.widgets.get_or_none("_heatmap_scale_cb")
    if cb is not None:
        ctx.state.heatmap.scale_mode = str(cb.currentText() or "Auto")
    # When the user picks Fixed but the vmin/vmax fields are blank, seed them
    # with the current global range so they have concrete numbers to tweak.
    if ctx.state.heatmap.scale_mode == "Fixed":
        vmin_edit = ctx.widgets.get_or_none("_heatmap_vmin_edit")
        vmax_edit = ctx.widgets.get_or_none("_heatmap_vmax_edit")
        g_vmin = ctx.runtime.heatmap.global_vmin
        g_vmax = ctx.runtime.heatmap.global_vmax
        if vmin_edit is not None and not (vmin_edit.text() or "").strip():
            if g_vmin is not None and _math.isfinite(g_vmin):
                vmin_edit.setText(f"{g_vmin:g}")
                ctx.state.heatmap.vmin = float(g_vmin)
        if vmax_edit is not None and not (vmax_edit.text() or "").strip():
            if g_vmax is not None and _math.isfinite(g_vmax):
                vmax_edit.setText(f"{g_vmax:g}")
                ctx.state.heatmap.vmax = float(g_vmax)
    _persist_settings(ctx)
    _redraw(ctx)


def _on_vminmax_changed(ctx) -> None:
    import math as _math
    vmin_edit = ctx.widgets.get_or_none("_heatmap_vmin_edit")
    vmax_edit = ctx.widgets.get_or_none("_heatmap_vmax_edit")
    try:
        ctx.state.heatmap.vmin = float((vmin_edit.text() if vmin_edit else "") or "nan")
    except (TypeError, ValueError):
        ctx.state.heatmap.vmin = float("nan")
    try:
        ctx.state.heatmap.vmax = float((vmax_edit.text() if vmax_edit else "") or "nan")
    except (TypeError, ValueError):
        ctx.state.heatmap.vmax = float("nan")
    # If the user typed a usable number, force scale mode to Fixed so the
    # value actually takes effect — otherwise the heatmap silently ignores
    # vmin/vmax while Scale stays on Auto.
    if _math.isfinite(ctx.state.heatmap.vmin) or _math.isfinite(ctx.state.heatmap.vmax):
        ctx.state.heatmap.scale_mode = "Fixed"
        cb = ctx.widgets.get_or_none("_heatmap_scale_cb")
        if cb is not None and cb.currentText() != "Fixed":
            blocked = cb.blockSignals(True)
            try:
                cb.setCurrentText("Fixed")
            finally:
                cb.blockSignals(blocked)
    _persist_settings(ctx)
    _redraw(ctx)


def _on_transpose(ctx) -> None:
    from well_viewer.features.heatmap.sidebar_view import (
        _ensure_sidebar_layout, _persist_and_redraw, refresh_heatmap_layout_sidebar,
    )
    layout = _ensure_sidebar_layout(ctx)
    layout.transpose()
    _persist_and_redraw(ctx)
    refresh_heatmap_layout_sidebar(ctx)


def _on_repset_avg_changed(ctx) -> None:
    cb = ctx.widgets.get_or_none("_heatmap_repset_avg_cb")
    if cb is not None:
        ctx.state.heatmap.repset_avg = bool(cb.isChecked())
    _persist_settings(ctx)
    _redraw(ctx)


def _on_log_scale_changed(ctx) -> None:
    cb = ctx.widgets.get_or_none("_heatmap_log_scale_cb")
    if cb is not None:
        ctx.state.heatmap.log_scale = bool(cb.isChecked())
    _persist_settings(ctx)
    _redraw(ctx)


def _on_cmap_changed(ctx) -> None:
    cb = ctx.widgets.get_or_none("_heatmap_cmap_cb")
    if cb is not None:
        if hasattr(cb, "lut"):
            name = cb.lut() or ""
            if hasattr(cb, "isReversed") and cb.isReversed():
                name = f"{name}_r"
        else:
            name = str(cb.currentText() or "")
        ctx.state.heatmap.cmap_name = name
    _persist_settings(ctx)
    _redraw(ctx)


def _persist_settings(ctx) -> None:
    """Save heatmap visual settings to disk (debounced via the registry)."""
    try:
        ctx.persistence.schedule_save("heatmap_layouts")
    except Exception:
        swallowed()  # was: pass (silent except)


def _on_label_double_click(ctx, evt) -> None:
    """Edit a row/col tick label inline when double-clicked."""
    if not getattr(evt, "dblclick", False):
        return
    ax = ctx.widgets.get_or_none("_heatmap_ax")
    if ax is None:
        return
    # Collect tick labels to hit-test against pixel positions.
    canvas = ctx.widgets.get_or_none("_heatmap_canvas")
    if canvas is None:
        return
    renderer = canvas.get_renderer()
    target = None  # ("row", index) or ("col", index)
    for i, lbl in enumerate(ax.get_xticklabels()):
        try:
            bb = lbl.get_window_extent(renderer=renderer)
        except Exception:
            swallowed()  # silent except
            continue
        if bb.contains(evt.x, evt.y):
            target = ("col", i)
            break
    if target is None:
        for i, lbl in enumerate(ax.get_yticklabels()):
            try:
                bb = lbl.get_window_extent(renderer=renderer)
            except Exception:
                swallowed()  # silent except
                continue
            if bb.contains(evt.x, evt.y):
                target = ("row", i)
                break
    if target is None:
        return
    from well_viewer.features.heatmap.sidebar_view import (
        _ensure_sidebar_layout, _persist_and_redraw,
    )
    layout = _ensure_sidebar_layout(ctx)
    kind, idx = target
    current = ""
    if kind == "row":
        labels = list(layout.row_labels or [str(i + 1) for i in range(layout.rows)])
        if 0 <= idx < len(labels):
            current = labels[idx]
        prompt = f"Row {idx + 1} label:"
    else:
        labels = list(layout.col_labels or [str(i + 1) for i in range(layout.cols)])
        if 0 <= idx < len(labels):
            current = labels[idx]
        prompt = f"Column {idx + 1} label:"
    new_text, ok = QInputDialog.getText(
        ctx.window,
        "Edit label", prompt, text=current,
    )
    if not ok:
        return
    new_text = new_text.strip()
    if kind == "row":
        if not layout.row_labels:
            layout.row_labels = [str(i + 1) for i in range(layout.rows)]
        # Pad if shorter than rows.
        while len(layout.row_labels) < layout.rows:
            layout.row_labels.append(str(len(layout.row_labels) + 1))
        layout.row_labels[idx] = new_text or str(idx + 1)
    else:
        if not layout.col_labels:
            layout.col_labels = [str(i + 1) for i in range(layout.cols)]
        while len(layout.col_labels) < layout.cols:
            layout.col_labels.append(str(len(layout.col_labels) + 1))
        layout.col_labels[idx] = new_text or str(idx + 1)
    _persist_and_redraw(ctx)


def _on_canvas_press(ctx, evt) -> None:
    from well_viewer.features.heatmap.controller import on_heatmap_label_drag_press, on_heatmap_click
    # Label drag takes priority; double-click rename is next; cell select last.
    if on_heatmap_label_drag_press(ctx, evt):
        return
    if getattr(evt, "dblclick", False):
        _on_label_double_click(ctx, evt)
        return
    on_heatmap_click(ctx, evt)


def _on_canvas_motion(ctx, evt) -> None:
    from well_viewer.features.heatmap.controller import on_heatmap_label_drag_motion, on_heatmap_motion
    on_heatmap_label_drag_motion(ctx, evt)
    if not ctx.runtime.heatmap.label_drag:
        on_heatmap_motion(ctx, evt)


def _on_canvas_release(ctx, evt) -> None:
    from well_viewer.features.heatmap.controller import on_heatmap_label_drag_release
    on_heatmap_label_drag_release(ctx, evt)


def _redraw(ctx) -> None:
    from well_viewer.features.heatmap.controller import redraw_heatmap
    redraw_heatmap(ctx)
