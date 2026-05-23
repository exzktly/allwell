"""Heat Map tab builder.

User-defined R×C grid colored by a chosen metric for one timepoint, with a
slider to scrub through time and click-to-select wells.
"""

from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QPushButton, QSlider, QVBoxLayout, QWidget,
)

from well_viewer.core.ui_helpers import (
    btn_primary, btn_secondary, make_band_controls, make_plot_view_switcher,
    make_plot_with_right_dock,
)
from functools import partial
from well_viewer.core.channel_state_controller import refresh_metric_combo_for_channel
from well_viewer.features.export.service import export_heatmap_data
from well_viewer.core.channel_state_controller import refresh_heatmap_aggregation_options


_CMAP_OPTIONS = ["viridis", "magma", "coolwarm", "RdYlBu_r", "Greys"]
_SCALE_OPTIONS = ["Auto", "Fixed"]


def build_heatmap_tab(ctx, parent: QWidget) -> None:
    # Defer matplotlib + heatmap_controller imports until the tab actually
    # builds. heatmap_controller pulls in matplotlib.patches and numpy at
    # module load, so importing it lazily keeps utility entry points like
    # ``refresh_heatmap_timepoints`` cheap.
    from well_viewer.features.heatmap.controller import METRIC_OPTIONS
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    plot_area, layout, ctx.app._heatmap_export_dock = make_plot_with_right_dock(parent)
    parent = plot_area

    # ── Control row 1: channel / metric ─────────────────────────────────────
    # The layout itself is edited from the sidebar configurator on the left
    # (see ``views/heatmap_layout_sidebar_view.py``); this row is for the
    # plotting parameters only.
    ctrl1 = QWidget(parent)
    ctrl1.setObjectName("TabCtrl")
    cl1 = QHBoxLayout(ctrl1)
    cl1.setContentsMargins(10, 6, 10, 6)

    # Channel combo hidden — global ctxbar combo is the only visible
    # channel control. See line_graphs_tab_view.py for rationale.
    ctx.app._chan_cb_heatmap = QComboBox()
    ctx.app._chan_cb_heatmap.addItems(["GFP"])
    ctx.app._chan_cb_heatmap.setMinimumWidth(220)
    ctx.app._chan_cb_heatmap.currentIndexChanged.connect(
        lambda _i, _src=ctx.app._chan_cb_heatmap: ctx.app._on_plot_channel_selected(_src)
    )
    ctx.app._chan_cb_heatmap.hide()

    # The global ctxbar Property combo is the single source of truth for
    # which CSV column drives the heatmap — the per-tab Property combo
    # used to live here, but the duplicate (one in the ctxbar, one in
    # this row) was confusing. Only the Aggregation combo stays local.
    cl1.addWidget(QLabel("Aggregation:", ctrl1))
    ctx.app._heatmap_metric_cb = QComboBox(ctrl1)
    ctx.app._heatmap_metric_cb.addItems(METRIC_OPTIONS)
    ctx.app._heatmap_metric_cb.currentIndexChanged.connect(lambda _i: _redraw(ctx.app))
    cl1.addWidget(ctx.app._heatmap_metric_cb)

    cl1.addStretch(1)

    _export_btn = btn_primary(ctrl1, "", partial(export_heatmap_data, ctx.app),
                              icon="download")
    _export_btn.setToolTip("Export CSV")
    cl1.addWidget(_export_btn)
    _b = btn_secondary(ctrl1, "", lambda: ctx.app._copy_active_card_as_svg(), icon="copy")
    _b.setToolTip("Copy SVG")
    cl1.addWidget(_b)
    _b = btn_secondary(ctrl1, "", lambda: ctx.app._save_active_card_figure(), icon="save")
    _b.setToolTip("Save figure")
    cl1.addWidget(_b)
    # Properties button last in the row so it sits flush with the right
    # edge of the plot area — adjacent to where the dock slides out.
    style_btn = btn_secondary(
        ctrl1, "",
        lambda: ctx.app._open_export_style_panel("heatmap"),
        icon="sliders-horizontal",
    )
    style_btn.setToolTip("Show / hide the figure properties panel")
    cl1.addWidget(style_btn)
    layout.addWidget(ctrl1)

    # ── Control row 2: cmap / scale / log ───────────────────────────────────
    ctrl2 = QWidget(parent)
    ctrl2.setObjectName("TabCtrl")
    cl2 = QHBoxLayout(ctrl2)
    cl2.setContentsMargins(10, 0, 10, 6)

    cl2.addWidget(QLabel("Color map:", ctrl2))
    # v2: LutSelector (gradient strip + name + searchable popover + reverse-LUT
    # button) in place of the legacy QComboBox of mpl colormap names.
    from widgets.lut_selector import LutSelector
    ctx.app._heatmap_cmap_cb = LutSelector(ctrl2)
    # Seed with the first option (matches the legacy default).
    _initial_cmap = _CMAP_OPTIONS[0] if _CMAP_OPTIONS else "viridis"
    _initial_rev = _initial_cmap.endswith("_r")
    ctx.app._heatmap_cmap_cb.setLut(_initial_cmap[:-2] if _initial_rev else _initial_cmap,
                                reversed=_initial_rev)
    ctx.app._heatmap_cmap_cb.setMaximumWidth(220)
    ctx.app._heatmap_cmap_cb.lutChanged.connect(lambda *_a: _on_cmap_changed(ctx.app))
    cl2.addWidget(ctx.app._heatmap_cmap_cb)

    cl2.addWidget(QLabel("Scale:", ctrl2))
    ctx.app._heatmap_scale_cb = QComboBox(ctrl2)
    ctx.app._heatmap_scale_cb.addItems(_SCALE_OPTIONS)
    ctx.app._heatmap_scale_cb.currentIndexChanged.connect(lambda _i: _on_scale_changed(ctx.app))
    cl2.addWidget(ctx.app._heatmap_scale_cb)
    ctx.app._heatmap_scale_mode = "Auto"

    cl2.addWidget(QLabel("vmin:", ctrl2))
    ctx.app._heatmap_vmin_edit = QLineEdit(ctrl2)
    ctx.app._heatmap_vmin_edit.setFixedWidth(70)
    ctx.app._heatmap_vmin_edit.editingFinished.connect(lambda: _on_vminmax_changed(ctx.app))
    cl2.addWidget(ctx.app._heatmap_vmin_edit)

    cl2.addWidget(QLabel("vmax:", ctrl2))
    ctx.app._heatmap_vmax_edit = QLineEdit(ctrl2)
    ctx.app._heatmap_vmax_edit.setFixedWidth(70)
    ctx.app._heatmap_vmax_edit.editingFinished.connect(lambda: _on_vminmax_changed(ctx.app))
    cl2.addWidget(ctx.app._heatmap_vmax_edit)

    transpose_btn = QPushButton("⇄ Transpose", ctrl2)
    transpose_btn.setProperty("variant", "secondary")
    transpose_btn.setToolTip("Swap rows and columns of the heatmap layout")
    transpose_btn.clicked.connect(lambda _=False: _on_transpose(ctx.app))
    cl2.addWidget(transpose_btn)

    ctx.app._heatmap_repset_avg_cb = QCheckBox("Rep-set avg", ctrl2)
    ctx.app._heatmap_repset_avg_cb.setToolTip(
        "When checked, cells whose well belongs to a rep-set show the "
        "average of the whole rep-set instead of the single well."
    )
    ctx.app._heatmap_repset_avg_cb.toggled.connect(lambda _v: _on_repset_avg_changed(ctx.app))
    cl2.addWidget(ctx.app._heatmap_repset_avg_cb)
    ctx.app._heatmap_repset_avg = False

    ctx.app._heatmap_log_scale_cb = QCheckBox("Log scale", ctrl2)
    ctx.app._heatmap_log_scale_cb.setToolTip(
        "Color the heatmap on a log scale (values ≤ 0 are masked)."
    )
    ctx.app._heatmap_log_scale_cb.toggled.connect(lambda _v: _on_log_scale_changed(ctx.app))
    cl2.addWidget(ctx.app._heatmap_log_scale_cb)
    ctx.app._heatmap_log_scale = False

    cl2.addStretch(1)

    ctx.app._heatmap_status_lbl = QLabel("", ctrl2)
    ctx.app._heatmap_status_lbl.setObjectName("Muted")
    cl2.addWidget(ctx.app._heatmap_status_lbl)

    layout.addWidget(ctrl2)

    # ── Timepoint slider (placed directly under the option rows) ────────────
    slider_row = QWidget(parent)
    slider_row.setObjectName("TabCtrl")
    sl = QHBoxLayout(slider_row)
    sl.setContentsMargins(10, 4, 10, 6)
    sl.addWidget(QLabel("t (h):", slider_row))
    from widgets.styled_slider import StyledSlider as _StyledSlider
    ctx.app._heatmap_tp_slider = _StyledSlider(Qt.Horizontal, slider_row)
    ctx.app._heatmap_tp_slider.setMinimum(0)
    ctx.app._heatmap_tp_slider.setMaximum(0)
    ctx.app._heatmap_tp_slider.valueChanged.connect(lambda _v: _on_tp_changed(ctx.app))
    sl.addWidget(ctx.app._heatmap_tp_slider, 1)
    ctx.app._heatmap_tp_label = QLabel("—", slider_row)
    ctx.app._heatmap_tp_label.setMinimumWidth(80)
    sl.addWidget(ctx.app._heatmap_tp_label)
    layout.addWidget(slider_row)

    sep = QFrame(parent)
    sep.setFrameShape(QFrame.HLine)
    sep.setFixedHeight(1)
    layout.addWidget(sep)

    # ── Figure (in a v2 PlotCard — card chrome + MplToolbar) ────────────────
    from widgets.plot_card import PlotCard
    card = PlotCard(parent, figsize=(8.0, 6.0), constrained=False)
    _sw = make_plot_view_switcher(ctx.app, 'Heat Map')
    if _sw is not None:
        card.setLeftHeaderWidget(_sw)
    # Hide the per-card header — the ctxbar above already carries the same controls.
    card.setHeaderVisible(False)
    card.setFigureTitle("")
    ctx.app._heatmap_card = card
    ctx.app._heatmap_fig = card.figure
    ctx.app._heatmap_ax = ctx.app._heatmap_fig.add_subplot(1, 1, 1)
    # Reserve top margin for the (sometimes long) title so it doesn't get
    # cropped — the default ``top=0.9`` clipped titles like
    # "MCHERRY Mean Intensity — Mean above threshold — t = 12 h".
    ctx.app._heatmap_fig.subplots_adjust(top=0.88, bottom=0.10, left=0.10, right=0.94)
    # Reserve a persistent colorbar axes via make_axes_locatable so each
    # redraw can refill it in place. Letting fig.colorbar(im, ax=ax)
    # allocate a fresh axes every redraw was the cause of the heatmap
    # plot shrinking and drifting left on every refresh — matplotlib does
    # not restore the original ax position when the colorbar is removed.
    from mpl_toolkits.axes_grid1 import make_axes_locatable
    divider = make_axes_locatable(ctx.app._heatmap_ax)
    ctx.app._heatmap_cax = divider.append_axes("right", size="4%", pad=0.08)
    ctx.app._heatmap_canvas = card.canvas
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
        ctx.app, card, icon="layout-grid",
    ), 1)

    # ── Mouse hooks ─────────────────────────────────────────────────────────
    # Unified press handler: label-drag first, then double-click rename, then
    # normal cell selection.  A single connection keeps ordering deterministic.
    ctx.app._heatmap_canvas.mpl_connect("button_press_event",
                                     lambda evt: _on_canvas_press(ctx.app, evt))
    ctx.app._heatmap_canvas.mpl_connect("motion_notify_event",
                                     lambda evt: _on_canvas_motion(ctx.app, evt))
    ctx.app._heatmap_canvas.mpl_connect("button_release_event",
                                     lambda evt: _on_canvas_release(ctx.app, evt))

    refresh_heatmap_timepoints(ctx.app)

    # Push any settings loaded from heatmap_layouts.json into the widgets.
    try:
        from well_viewer.persistence.heatmap_layouts import apply_persisted_settings
        apply_persisted_settings(ctx.app)
    except Exception:
        pass

    # Refresh the global Property combo + heatmap aggregation enable
    # state so the heatmap's first render reflects the canonical
    # ``_active_metric`` (ratio channels disable the global combo and
    # collapse it to ``Calculated Val``; non-MFI properties grey out
    # threshold-based aggregations).
    try:
        if hasattr(ctx.app, "_refresh_metric_combo_for_channel"):
            refresh_metric_combo_for_channel(ctx.app)
        if hasattr(ctx.app, "_refresh_heatmap_aggregation_options"):
            refresh_heatmap_aggregation_options(ctx.app, )
    except Exception:
        pass
    # Bust any caches that might hold a stale "no values" result from
    # before the tab was fully built (data load timing can leave the
    # global colormap range cache populated with NaNs, which produced
    # empty cells until the user wiggled the channel).
    ctx.app._heatmap_global_range_cache = None
    if hasattr(ctx.app, "_invalidate_stats_cache"):
        try:
            ctx.app._invalidate_stats_cache()
        except Exception:
            pass
    _redraw(ctx.app)


# ── helpers attached or referenced from runtime_app ─────────────────────────

def refresh_heatmap_timepoints(app) -> None:
    slider = getattr(app, "_heatmap_tp_slider", None)
    label = getattr(app, "_heatmap_tp_label", None)
    if slider is None:
        return
    try:
        from well_viewer.features.scatter.controller import get_all_timepoints
        tps = list(get_all_timepoints(app))
    except Exception:
        tps = []
    app._heatmap_tp_values = tps
    blocked = slider.blockSignals(True)
    try:
        slider.setMinimum(0)
        slider.setMaximum(max(0, len(tps) - 1))
        slider.setValue(0)
    finally:
        slider.blockSignals(blocked)
    if label is not None:
        if tps:
            label.setText(f"t = {tps[0]:g} h")
        else:
            label.setText("—")
    _redraw(app)


def _on_tp_changed(app) -> None:
    tps: List[float] = list(getattr(app, "_heatmap_tp_values", []) or [])
    slider = getattr(app, "_heatmap_tp_slider", None)
    label = getattr(app, "_heatmap_tp_label", None)
    if not tps or slider is None:
        return
    idx = max(0, min(slider.value(), len(tps) - 1))
    if label is not None:
        label.setText(f"t = {tps[idx]:g} h")
    _redraw(app)


def _on_scale_changed(app) -> None:
    import math as _math
    cb = getattr(app, "_heatmap_scale_cb", None)
    if cb is not None:
        app._heatmap_scale_mode = str(cb.currentText() or "Auto")
    # When the user picks Fixed but the vmin/vmax fields are blank, seed them
    # with the current global range so they have concrete numbers to tweak.
    if app._heatmap_scale_mode == "Fixed":
        vmin_edit = getattr(app, "_heatmap_vmin_edit", None)
        vmax_edit = getattr(app, "_heatmap_vmax_edit", None)
        g_vmin = getattr(app, "_heatmap_global_vmin", None)
        g_vmax = getattr(app, "_heatmap_global_vmax", None)
        if vmin_edit is not None and not (vmin_edit.text() or "").strip():
            if g_vmin is not None and _math.isfinite(g_vmin):
                vmin_edit.setText(f"{g_vmin:g}")
                app._heatmap_vmin = float(g_vmin)
        if vmax_edit is not None and not (vmax_edit.text() or "").strip():
            if g_vmax is not None and _math.isfinite(g_vmax):
                vmax_edit.setText(f"{g_vmax:g}")
                app._heatmap_vmax = float(g_vmax)
    _persist_settings(app)
    _redraw(app)


def _on_vminmax_changed(app) -> None:
    import math as _math
    try:
        app._heatmap_vmin = float(app._heatmap_vmin_edit.text() or "nan")
    except (TypeError, ValueError):
        app._heatmap_vmin = float("nan")
    try:
        app._heatmap_vmax = float(app._heatmap_vmax_edit.text() or "nan")
    except (TypeError, ValueError):
        app._heatmap_vmax = float("nan")
    # If the user typed a usable number, force scale mode to Fixed so the
    # value actually takes effect — otherwise the heatmap silently ignores
    # vmin/vmax while Scale stays on Auto.
    if _math.isfinite(app._heatmap_vmin) or _math.isfinite(app._heatmap_vmax):
        app._heatmap_scale_mode = "Fixed"
        cb = getattr(app, "_heatmap_scale_cb", None)
        if cb is not None and cb.currentText() != "Fixed":
            blocked = cb.blockSignals(True)
            try:
                cb.setCurrentText("Fixed")
            finally:
                cb.blockSignals(blocked)
    _persist_settings(app)
    _redraw(app)


def _on_transpose(app) -> None:
    from well_viewer.views.heatmap_layout_sidebar_view import (
        _ensure_sidebar_layout, _persist_and_redraw, refresh_heatmap_layout_sidebar,
    )
    layout = _ensure_sidebar_layout(app)
    layout.transpose()
    _persist_and_redraw(app)
    refresh_heatmap_layout_sidebar(app)


def _on_repset_avg_changed(app) -> None:
    cb = getattr(app, "_heatmap_repset_avg_cb", None)
    if cb is not None:
        app._heatmap_repset_avg = bool(cb.isChecked())
    _persist_settings(app)
    _redraw(app)


def _on_log_scale_changed(app) -> None:
    cb = getattr(app, "_heatmap_log_scale_cb", None)
    if cb is not None:
        app._heatmap_log_scale = bool(cb.isChecked())
    _persist_settings(app)
    _redraw(app)


def _on_cmap_changed(app) -> None:
    cb = getattr(app, "_heatmap_cmap_cb", None)
    if cb is not None:
        if hasattr(cb, "lut"):
            name = cb.lut() or ""
            if hasattr(cb, "isReversed") and cb.isReversed():
                name = f"{name}_r"
        else:
            name = str(cb.currentText() or "")
        app._heatmap_cmap_name = name
    _persist_settings(app)
    _redraw(app)


def _persist_settings(app) -> None:
    """Save heatmap visual settings to disk (debounced via the registry)."""
    try:
        app._persistence.schedule_save("heatmap_layouts")
    except Exception:
        pass


def _on_label_double_click(app, evt) -> None:
    """Edit a row/col tick label inline when double-clicked."""
    if not getattr(evt, "dblclick", False):
        return
    ax = getattr(app, "_heatmap_ax", None)
    if ax is None:
        return
    # Collect tick labels to hit-test against pixel positions.
    canvas = getattr(app, "_heatmap_canvas", None)
    if canvas is None:
        return
    renderer = canvas.get_renderer()
    target = None  # ("row", index) or ("col", index)
    for i, lbl in enumerate(ax.get_xticklabels()):
        try:
            bb = lbl.get_window_extent(renderer=renderer)
        except Exception:
            continue
        if bb.contains(evt.x, evt.y):
            target = ("col", i)
            break
    if target is None:
        for i, lbl in enumerate(ax.get_yticklabels()):
            try:
                bb = lbl.get_window_extent(renderer=renderer)
            except Exception:
                continue
            if bb.contains(evt.x, evt.y):
                target = ("row", i)
                break
    if target is None:
        return
    from well_viewer.views.heatmap_layout_sidebar_view import (
        _ensure_sidebar_layout, _persist_and_redraw,
    )
    layout = _ensure_sidebar_layout(app)
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
        app if isinstance(app, QWidget) else None,
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
    _persist_and_redraw(app)


def _on_canvas_press(app, evt) -> None:
    from well_viewer.features.heatmap.controller import on_heatmap_label_drag_press, on_heatmap_click
    # Label drag takes priority; double-click rename is next; cell select last.
    if on_heatmap_label_drag_press(app, evt):
        return
    if getattr(evt, "dblclick", False):
        _on_label_double_click(app, evt)
        return
    on_heatmap_click(app, evt)


def _on_canvas_motion(app, evt) -> None:
    from well_viewer.features.heatmap.controller import on_heatmap_label_drag_motion, on_heatmap_motion
    on_heatmap_label_drag_motion(app, evt)
    if not getattr(app, "_heatmap_label_drag", None):
        on_heatmap_motion(app, evt)


def _on_canvas_release(app, evt) -> None:
    from well_viewer.features.heatmap.controller import on_heatmap_label_drag_release
    on_heatmap_label_drag_release(app, evt)


def _redraw(app) -> None:
    from well_viewer.features.heatmap.controller import redraw_heatmap
    redraw_heatmap(app)
