"""Heat Map tab builder.

User-defined R×C grid colored by a chosen metric for one timepoint, with a
slider to scrub through time and click-to-select wells.
"""

from __future__ import annotations

from well_viewer.core.channel_state_controller import on_plot_channel_selected



from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QInputDialog, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from well_viewer.core.ui_helpers import (
    build_plot_save_menu,
    make_plot_with_right_dock,
    make_popover_button,
    make_properties_button,
    plotting_band_slot,
)
from well_viewer.core.channel_state_controller import refresh_metric_combo_for_channel
from well_viewer.features.export.service import (
    export_cell_data_from, export_heatmap_data)
from well_viewer.features.heatmap.cell_scope_compute import cell_scope
from well_viewer.core.channel_state_controller import refresh_heatmap_aggregation_options
from core.safe import swallowed
from ui import theme

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_CHAN_CB_MIN_W = 220
_CMAP_CB_MAX_W = 220


_CMAP_OPTIONS = ["viridis", "magma", "coolwarm", "RdYlBu_r", "Greys"]
_SCALE_OPTIONS = ["Auto", "Fixed"]



def build_heatmap_tab(ctx, parent: QWidget) -> None:
    # Defer matplotlib + heatmap_controller imports until the tab actually
    # builds. heatmap_controller pulls in matplotlib.patches and numpy at
    # module load, so importing it lazily keeps utility entry points like
    # ``refresh_heatmap_timepoints`` cheap.
    from well_viewer.features.heatmap.controller import (
        METRIC_OPTIONS, heatmap_empty_reason,
    )
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

    # ── The control band ────────────────────────────────────────────────────
    # One row, the same one every Review tab uses. This tab used to spend three
    # rows and a separator on its controls: channel/aggregation, then
    # colour-map/scale/vmin/vmax/transpose/rep-set/log, then a full-width
    # timepoint slider. The colouring controls are appearance, not scope, so
    # they moved into a Display popover, and what is left fits one row at every
    # width. The grid layout itself is still edited from the sidebar
    # configurator on the left (``views/heatmap_layout_sidebar_view.py``).
    band = plotting_band_slot(ctx, "Heat Map")

    # Channel combo hidden — the Plotting band's channel combo is the only
    # visible channel control. See line_graphs_tab_view.py for rationale.
    chan_cb = reg.register("_chan_cb_heatmap", QComboBox())
    chan_cb.addItems(["GFP"])
    chan_cb.setMinimumWidth(_CHAN_CB_MIN_W)
    chan_cb.currentIndexChanged.connect(
        lambda _i, _src=chan_cb: on_plot_channel_selected(ctx, _src)
    )
    chan_cb.hide()

    # The Plotting band's Property combo is the single source of truth for
    # which CSV column drives the heatmap — the per-tab Property combo
    # used to live here, but the duplicate was confusing. Only the
    # Aggregation combo stays local.
    metric_cb = reg.register("_heatmap_metric_cb", QComboBox())
    metric_cb.addItems(METRIC_OPTIONS)
    metric_cb.currentIndexChanged.connect(lambda _i: _redraw(ctx))
    band.add_scope("Aggregation", metric_cb)

    # The timepoint picker — a LabeledComboBox like every other Timepoint
    # control in Review, painting its own "Timepoint: 4.0h" prefix. Was a
    # StyledSlider; unified to a combo so scrubbing to an exact timepoint
    # doesn't depend on pixel-precise dragging and the control matches its
    # siblings (Stats, Distribution, …) instead of being the one holdout.
    from widgets.labeled_combo import LabeledComboBox
    tp_cb = reg.register("_heatmap_tp_cb", LabeledComboBox("Timepoint"))
    tp_cb.addItem("—")
    tp_cb.currentIndexChanged.connect(lambda _i: _on_tp_changed(ctx))
    band.add_scope("", tp_cb)

    # ── Display popover: everything about how the grid is drawn ─────────────
    # These change the figure's appearance rather than what it is showing, and
    # they are set once and left. One click is the right price for the row.
    # Not only colour any more: the per-cell ``±`` and the sample-map tick
    # labels are appearance by the same test and live here too.
    display_btn, disp, disp_l = make_popover_button("Display ▾", band)
    display_btn.setToolTip(
        "Colour map, scale, layout, per-cell error and tick labels")

    disp_l.addWidget(QLabel("Color map:", disp))
    # v2: LutSelector (gradient strip + name + searchable popover + reverse-LUT
    # button) in place of the legacy QComboBox of mpl colormap names.
    from widgets.lut_selector import LutSelector
    cmap_cb = reg.register("_heatmap_cmap_cb", LutSelector(disp))
    # Seed with the first option (matches the legacy default).
    _initial_cmap = _CMAP_OPTIONS[0] if _CMAP_OPTIONS else "viridis"
    _initial_rev = _initial_cmap.endswith("_r")
    cmap_cb.setLut(_initial_cmap[:-2] if _initial_rev else _initial_cmap,
                   reversed=_initial_rev)
    cmap_cb.setMaximumWidth(_CMAP_CB_MAX_W)
    cmap_cb.lutChanged.connect(lambda *_a: _on_cmap_changed(ctx))
    disp_l.addWidget(cmap_cb)

    disp_l.addWidget(QLabel("Scale:", disp))
    scale_cb = reg.register("_heatmap_scale_cb", QComboBox(disp))
    scale_cb.addItems(_SCALE_OPTIONS)
    scale_cb.currentIndexChanged.connect(lambda _i: _on_scale_changed(ctx))
    disp_l.addWidget(scale_cb)

    disp_l.addWidget(QLabel("vmin:", disp))
    vmin_edit = reg.register("_heatmap_vmin_edit", QLineEdit(disp))
    vmin_edit.setFixedWidth(theme.Metrics.num_field_w)
    vmin_edit.editingFinished.connect(lambda: _on_vminmax_changed(ctx))
    disp_l.addWidget(vmin_edit)

    disp_l.addWidget(QLabel("vmax:", disp))
    vmax_edit = reg.register("_heatmap_vmax_edit", QLineEdit(disp))
    vmax_edit.setFixedWidth(theme.Metrics.num_field_w)
    vmax_edit.editingFinished.connect(lambda: _on_vminmax_changed(ctx))
    disp_l.addWidget(vmax_edit)

    transpose_btn = QPushButton("⇄ Transpose", disp)
    transpose_btn.setProperty("variant", "secondary")
    transpose_btn.setToolTip("Swap rows and columns of the heatmap layout")
    transpose_btn.clicked.connect(lambda _=False: _on_transpose(ctx))
    disp_l.addWidget(transpose_btn)

    repset_avg_cb = reg.register("_heatmap_repset_avg_cb", QCheckBox("Rep-set avg", disp))
    repset_avg_cb.setToolTip(
        "When checked, cells whose well belongs to a rep-set show the "
        "average of the whole rep-set instead of the single well."
    )
    repset_avg_cb.toggled.connect(lambda _v: _on_repset_avg_changed(ctx))
    disp_l.addWidget(repset_avg_cb)

    log_scale_cb = reg.register("_heatmap_log_scale_cb", QCheckBox("Log scale", disp))
    log_scale_cb.setToolTip(
        "Color the heatmap on a log scale (values ≤ 0 are masked)."
    )
    log_scale_cb.toggled.connect(lambda _v: _on_log_scale_changed(ctx))
    disp_l.addWidget(log_scale_cb)

    show_error_cb = reg.register("_heatmap_show_error_cb", QCheckBox("Show ± error", disp))
    show_error_cb.setToolTip(
        "Print each cell's spread across replicates under its value — the same "
        "replicates and the same SD/SEM choice the bar and line plots use, so "
        "the per-FOV toggle governs it here too. Cells with a single replicate "
        "have no spread and print none; the Cell count metric is a total rather "
        "than an average and never prints one."
    )
    show_error_cb.toggled.connect(lambda _v: _on_show_error_changed(ctx))
    disp_l.addWidget(show_error_cb)

    sample_labels_cb = reg.register(
        "_heatmap_sample_labels_cb", QCheckBox("Label ticks from sample map", disp))
    sample_labels_cb.setToolTip(
        "Name each row and column by the sample-map values every well on it "
        "shares — the dose that changes across the columns, the cell line that "
        "changes down the rows. A variable that is the same everywhere names "
        "nothing, and a row or column whose wells disagree keeps its grid label."
    )
    # Seeded before the connection, not after: this is the one toggle in the
    # popover whose state default is True, and a ``setChecked`` after the
    # ``connect`` would fire the handler during the build — persisting a
    # setting and redrawing a figure that does not exist yet.
    sample_labels_cb.setChecked(bool(ctx.state.heatmap.label_from_sample_map))
    sample_labels_cb.toggled.connect(lambda _v: _on_sample_labels_changed(ctx))
    disp_l.addWidget(sample_labels_cb)

    band.add_action(display_btn)

    export_btn, export_menu, _export_data_act = build_plot_save_menu(
        ctx, band,
        card_key="_heatmap_card",
        data_label="Save plot data…",
        data_cb=lambda: export_heatmap_data(ctx),
        cells_cb=lambda: export_cell_data_from(
            ctx, cell_scope,
            unavailable="Draw the heat map first — the export follows the grid on screen."),
    )
    band.add_action(export_btn)

    band.add_action(make_properties_button(ctx, band, "heatmap"))


    # ── Figure (in a v2 PlotCard — card chrome + MplToolbar) ────────────────
    from widgets.plot_card import PlotCard
    card = PlotCard(parent, figsize=(8.0, 6.0), constrained=False, header=False)
    from well_viewer.core.ui_helpers import apply_plot_card_decorators
    apply_plot_card_decorators(ctx, card, "Heat Map")
    # No card header. It was built, given a plot-type switcher duplicating the
    # tab bar directly above it, and then hidden — chrome that cost a widget
    # tree and showed nothing.
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
    # No shared band controls on the card. The heat map does report a spread —
    # ``Display ▾ → Show ± error`` prints each cell's, across the same
    # replicates and under the same SD/SEM choice the bar and line plots use —
    # but it draws no *band*, and both settings that govern it are app-wide and
    # already reachable from this tab's Properties → Statistics. Duplicating
    # them on the card would put two controls for one setting on one screen.
    card.setControlsWidget(None)
    # NOTE: don't trigger a redraw on theme change — heatmap_controller doesn't
    # use plot_style.apply_ax_style (it has its own inline ax styling), so a
    # redraw would wipe the widget-side theme styling that setPlotTheme already
    # applied. Let PlotCard.setPlotTheme's apply_axes_style walk stand.
    card.setStatsChipVisible(False)
    # Deliberately *not* gated on the plate selection. This page draws whatever
    # its layout holds and reads the selection only to outline cells, so gating
    # it that way would hide a fully populated saved heat map the moment the
    # user cleared the plate. What it can genuinely have nothing to show for is
    # an empty layout — see ``heatmap_empty_reason``.
    from well_viewer.core.ui_helpers import wrap_with_empty_state
    layout.addWidget(wrap_with_empty_state(
        ctx.window, card, icon="layout-grid",
        reason=heatmap_empty_reason,
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
    # A8-teardown T3: modern infrastructure — the combo via ``ctx.widgets``,
    # the timepoint-value cache via ``ctx.runtime.heatmap``, timepoints/focus via
    # the ctx leaves. The onward ``_redraw`` is the one render handoff residual
    # (the heatmap render tree still takes ``app`` — a later one-feature pass).
    cb = ctx.widgets.get_or_none("_heatmap_tp_cb")
    if cb is None:
        return
    try:
        from well_viewer.features.scatter.controller import get_all_timepoints
        tps = list(get_all_timepoints(ctx))
    except Exception:
        tps = []
    ctx.state.heatmap.tp_values = tps
    # Seed from the shared focus timepoint (N5) when one is set and present,
    # so opening Heat Map after picking a timepoint on Distribution lands on
    # the same point; else default to the first timepoint.
    from well_viewer.core.timepoint_sync import current_focus_tp, nearest_index
    focus_idx = nearest_index(tps, current_focus_tp(ctx)) if tps else None
    start_idx = focus_idx if focus_idx is not None else 0
    blocked = cb.blockSignals(True)
    try:
        cb.clear()
        if tps:
            cb.addItems([f"{v:g} h" for v in tps])
            cb.setCurrentIndex(start_idx)
        else:
            cb.addItem("—")
    finally:
        cb.blockSignals(blocked)
    _redraw(ctx)


def _on_tp_changed(ctx) -> None:
    tps: list[float] = list(ctx.state.heatmap.tp_values or [])
    cb = ctx.widgets.get_or_none("_heatmap_tp_cb")
    if not tps or cb is None:
        return
    idx = max(0, min(cb.currentIndex(), len(tps) - 1))
    # Publish the shared focus timepoint (N5). In Split View, emit so the other
    # pane (Distribution) redraws live; otherwise update the value silently and
    # redraw only this tab — keeps switching timepoints as cheap as before (no
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


def _on_show_error_changed(ctx) -> None:
    cb = ctx.widgets.get_or_none("_heatmap_show_error_cb")
    if cb is not None:
        ctx.state.heatmap.show_error = bool(cb.isChecked())
    _persist_settings(ctx)
    _redraw(ctx)


def _on_sample_labels_changed(ctx) -> None:
    cb = ctx.widgets.get_or_none("_heatmap_sample_labels_cb")
    if cb is not None:
        ctx.state.heatmap.label_from_sample_map = bool(cb.isChecked())
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
    # Record that this tick's name is the user's, which is what makes it
    # outrank the one the sample map would compose for the same tick. Without
    # it the rename is a control that runs, writes, redraws, and shows the
    # user something else — and on an annotated plate that is every rename.
    # An emptied dialog clears the mark along with the name: the tick is back
    # to the grid's own label, which the sample map may speak for again.
    layout.mark_label_typed(kind, idx, bool(new_text))
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
    # Motion over the grid drives the label-drag highlight and nothing else.
    # Hovering a cell deliberately writes no readout: the control band is a row
    # of persistent controls, and a per-cell well/value string appearing in it
    # resized the row and pushed the threshold control out of reach.
    from well_viewer.features.heatmap.controller import on_heatmap_label_drag_motion
    on_heatmap_label_drag_motion(ctx, evt)


def _on_canvas_release(ctx, evt) -> None:
    from well_viewer.features.heatmap.controller import on_heatmap_label_drag_release
    on_heatmap_label_drag_release(ctx, evt)


def _redraw(ctx) -> None:
    from well_viewer.features.heatmap.controller import redraw_heatmap
    redraw_heatmap(ctx)
