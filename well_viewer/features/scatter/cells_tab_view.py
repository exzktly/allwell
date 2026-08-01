"""Scatter Plot: Cells tab builder (Qt port) — single scatter in a v2 PlotCard."""

from __future__ import annotations

from well_viewer.core.plot_card_actions import (
    copy_active_card_as_svg, save_active_card_figure, save_card_to_gallery,
)

from PySide6.QtWidgets import (
    QComboBox, QLabel, QPushButton, QVBoxLayout,
    QWidget,
)

from well_viewer.core.ui_helpers import (
    add_menu_action, make_action_menu_button, make_plot_with_right_dock,
    make_popover_button, make_properties_button, plotting_band_slot)
from well_viewer.features.scatter.controller import redraw_scatter_orchestrate
from well_viewer.features.scatter.tp_selector_view import open_timepoint_selector
from well_viewer.features.scatter.controller import on_scatter_axis_change
from well_viewer.features.scatter.controller import update_scatter_tp_selection_display
from functools import partial
from well_viewer.features.export.service import export_scatter_data
from well_viewer.features.scatter.callbacks import on_scatter_click
from well_viewer.features.scatter.callbacks import on_scatter_motion
from well_viewer.features.scatter.gating import (
    on_gate_edit_motion, on_gate_edit_press, on_gate_edit_release,
)


def build_scatter_cells_tab(ctx, parent: QWidget) -> None:
    from widgets.plot_card import PlotCard

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    # A8 burn-down: widget handles go through the WidgetRegistry (``ctx.widgets``),
    # which also stamps ``app._<name>`` so legacy ``app._scatter_*`` readers keep
    # resolving. Non-widget state (gates / color-by-tp flag / selections) and
    # behaviour calls still reach the app — a later layer.
    reg = ctx.widgets

    plot_area, layout, export_dock = make_plot_with_right_dock(parent)
    reg.register("_scatter_export_dock", export_dock)
    parent = plot_area

    ctrl = plotting_band_slot(ctx, "Scatter Plot", "cells")

    # Per-axis channel + property selectors. The scatter tab overrides
    # Plotting's band channel/property combos (those are hidden when
    # Scatter Plot is the active plotting subtab) because each axis
    # needs to render an independent column.
    from well_viewer.data.metric_labels import METRIC_ORDER as _METRIC_ORDER
    ch_x_cb = reg.register("_scatter_ch_x_cb", QComboBox(ctrl))
    ch_x_cb.addItems(["GFP"])
    ch_x_cb.currentIndexChanged.connect(lambda _i: on_scatter_axis_change(ctx, "x"))
    ctrl.add_scope("X-axis · channel", ch_x_cb)
    reg.register("_scatter_ch_x_var", ch_x_cb)

    metric_x_cb = reg.register("_scatter_metric_x_cb", QComboBox(ctrl))
    metric_x_cb.addItems(_METRIC_ORDER)
    metric_x_cb.currentIndexChanged.connect(lambda _i: redraw_scatter_orchestrate(ctx))
    ctrl.add_scope("property", metric_x_cb)
    reg.register("_scatter_metric_x_var", metric_x_cb)

    ch_y_cb = reg.register("_scatter_ch_y_cb", QComboBox(ctrl))
    ch_y_cb.addItems(["GFP"])
    ch_y_cb.currentIndexChanged.connect(lambda _i: on_scatter_axis_change(ctx, "y"))
    ctrl.add_scope("Y-axis · channel", ch_y_cb)
    reg.register("_scatter_ch_y_var", ch_y_cb)

    metric_y_cb = reg.register("_scatter_metric_y_cb", QComboBox(ctrl))
    metric_y_cb.addItems(_METRIC_ORDER)
    metric_y_cb.currentIndexChanged.connect(lambda _i: redraw_scatter_orchestrate(ctx))
    ctrl.add_scope("property", metric_y_cb)
    reg.register("_scatter_metric_y_var", metric_y_cb)

    # Self-describing — the button's own text carries the "Timepoints: …
    # selected" readout (see update_scatter_tp_selection_display), so it needs
    # no separate caption or count label beside it.
    tp_button = reg.register("_scatter_tp_button", QPushButton("Timepoints: 0 selected", ctrl))
    tp_button.setProperty("variant", "secondary")
    tp_button.clicked.connect(lambda _=False: _open_timepoint_selector(ctx))
    ctrl.add_scope("", tp_button)
    # Hidden legacy single-tp combo — kept so export / save-figure paths that
    # still read ``_scatter_tp_var.currentText()`` see a valid timepoint string
    # (the first selected one).
    tp_cb = reg.register("_scatter_tp_cb", QComboBox(ctrl))
    tp_cb.addItems(["0"])
    tp_cb.hide()
    reg.register("_scatter_tp_var", tp_cb)
    ctx.runtime.scatter.tp_selections = {}
    # Stash the per-cell tp-selection summary refresher so view_state restore can
    # repaint the button's readout without importing this feature module.
    ctx.runtime.scatter.tp_display_refresh = update_scatter_tp_selection_display

    # Secondary display options (Layout + Colour-by-time) collapse into a
    # "Display ▾" popover so the per-cell scatter control row stays readable —
    # the X/Y Channel & Property selectors are the controls that matter inline.
    _disp_btn, _disp_panel, _disp_lay = make_popover_button("Display ▾", ctrl)
    _disp_btn.setToolTip(
        "Scatter display options: layout, colour-by-time, and the gate filter")
    ctrl.add_action(_disp_btn)

    # Layout: overlay every group on one axis (default, interactive) or stack
    # one group per axis below the other (view-only comparison) — mirrors the
    # Distribution tab's Overlay / Faceted choice.
    _disp_lay.addWidget(QLabel("Layout:", _disp_panel))
    layout_cb = reg.register("_scatter_layout_cb", QComboBox(_disp_panel))
    layout_cb.addItems(["Overlay", "Separate axes"])
    layout_cb.currentIndexChanged.connect(
        lambda _i: redraw_scatter_orchestrate(ctx))
    _disp_lay.addWidget(layout_cb)
    reg.register("_scatter_layout_var", layout_cb)

    # "Color by time": colour each point by its timepoint instead of by well
    # identity, so a well's cloud shows its evolution over time. With more than
    # one well selected the view auto-stacks into one panel per well (overlaying
    # several time-coloured clouds on one axis would be unreadable).
    color_by_tp_btn = reg.register(
        "_scatter_color_by_tp_btn", QPushButton("Color by time: Off", ctrl))
    color_by_tp_btn.setProperty("variant", "toggle")
    color_by_tp_btn.setCheckable(True)
    color_by_tp_btn.setToolTip(
        "Colour each point by its timepoint. With multiple wells selected, "
        "wells are shown as stacked panels.")

    def _toggle_color_by_tp(checked: bool) -> None:
        on = bool(checked)
        ctx.state.scatter.color_by_tp = on
        # State-label + matching highlight (label and accent always agree).
        color_by_tp_btn.setText(
            "Color by time: On" if on else "Color by time: Off")
        redraw_scatter_orchestrate(ctx)

    color_by_tp_btn.clicked.connect(_toggle_color_by_tp)
    _disp_lay.addWidget(color_by_tp_btn)

    # "Apply gates" — in Display ▾ with the other view options. Default off:
    # the per-cell scatter is where gates are drawn, so it shows the full cloud
    # with the boxes over it (see collect.scatter_points_gated). The axes title
    # carries a "· gate-filtered" tag whenever it is on, which is what keeps a
    # filter tucked inside a popover from being forgotten — and what travels
    # into a copied or saved figure, where no control does.
    apply_gates_btn = reg.register(
        "_scatter_apply_gates_btn", QPushButton("Apply gates: Off", _disp_panel))
    apply_gates_btn.setProperty("variant", "toggle")
    apply_gates_btn.setCheckable(True)
    apply_gates_btn.setToolTip(
        "Plot only the cells inside the gate boxes shown on this plot.\n"
        "Off (default): the full cloud, with the boxes drawn over it, so you "
        "can see and resize what you selected.\n"
        "This filters the view only — it does not change which cells any other "
        "tab counts. That is the Cell Gating tab's job.")

    def _toggle_apply_gates(checked: bool) -> None:
        on = bool(checked)
        ctx.state.scatter.apply_gates_to_points = on
        apply_gates_btn.setText("Apply gates: On" if on else "Apply gates: Off")
        redraw_scatter_orchestrate(ctx)

    apply_gates_btn.clicked.connect(_toggle_apply_gates)
    _disp_lay.addWidget(apply_gates_btn)
    # Rectangular-gate state (drawn via the Properties sidebar's Gates section):
    # a list of {x0,y0,x1,y1} data-space boxes, the arm flag, and the live
    # matplotlib RectangleSelector (rebuilt after each redraw).
    ctx.state.cell_gating.scatter_gates = []   # gate geometry — persisted, stays on app/state
    # Live gate-interaction objects → runtime holder (stamps app._scatter_gate_*).
    ctx.runtime.scatter.gate_draw_mode = False
    ctx.runtime.scatter.gate_selector = None
    # "Show gates" toggle (sidebar) is typed state: ctx.state.scatter.gates_hidden
    # Edge/corner editing of existing gates: the in-progress edit ({idx,handle})
    # and the cached render context for repainting just the gates while dragging.
    ctx.runtime.scatter.gate_edit = None
    # Per-panel gate render entries (overlay = 1, separate-axes = N), used to
    # repaint every panel's gate during an edit drag.
    ctx.runtime.scatter.gate_render = None
    # gating.py triggers redraws through this callback so it never has to import
    # the controller (avoids a scatter-internal import cycle).
    ctx.runtime.scatter.gate_redraw_cb = redraw_scatter_orchestrate

    # Export / copy / save collapse into one "Export ▾" menu (they were trailing
    # icon buttons — the first controls clipped when the pane narrows). The X/Y
    # selectors, Timepoints and Display ▾ stay inline.
    _export_btn, _export_menu = make_action_menu_button(
        "Export ▾", ctrl, tooltip="Export, copy, and save")
    add_menu_action(_export_menu, "Export CSV…", lambda: export_scatter_data(ctx))
    add_menu_action(_export_menu, "Copy SVG", lambda: copy_active_card_as_svg(ctx))
    add_menu_action(_export_menu, "Save figure…", lambda: save_active_card_figure(ctx))
    _export_menu.addSeparator()
    add_menu_action(_export_menu, "Save to Saved Plots",
                    lambda: save_card_to_gallery(ctx, "_scatter_card"))
    ctrl.add_action(_export_btn)
    ctrl.add_action(make_properties_button(ctx, ctrl, "scatter_cells"))

    card = PlotCard(parent, figsize=(8, 6), constrained=False, header=False)
    from well_viewer.core.ui_helpers import apply_plot_card_decorators
    apply_plot_card_decorators(ctx, card, "Scatter Plot")
    card.setFigureTitle("")
    reg.register("_scatter_card", card)
    fig = reg.register("_scatter_fig", card.figure)
    reg.register("_ax_scatter", fig.add_subplot(1, 1, 1))
    # top/bottom leave room for the (single-line) axes title and the x-axis
    # label — at top=0.95 / bottom=0.12 the large title clipped at the figure's
    # top edge and the x-label clipped at the bottom.
    fig.subplots_adjust(hspace=0.3, top=0.90, bottom=0.15, left=0.16, right=0.97)
    canvas = reg.register("_scatter_canvas", card.canvas)
    # No error-band controls here: the per-cell scatter draws individual points,
    # so SEM/SD has no meaning (it belongs only on the aggregate scatter).
    card.plotThemeChanged.connect(lambda _m: redraw_scatter_orchestrate(ctx))
    card.setStatsChipVisible(False)
    from well_viewer.core.ui_helpers import wrap_with_empty_state
    # Wrap the card in a vertical scroll area: in "Separate axes" mode the card
    # is given a per-panel minimum height (see _setup_scatter_axes) so stacked
    # panels keep a readable size and scroll instead of squishing. Overlay mode
    # leaves the card minimum at 0, so it just fills the viewport (no scroll).
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtWidgets import QFrame as _QFrame, QScrollArea as _QScrollArea
    _scroll = _QScrollArea(parent)
    _scroll.setWidgetResizable(True)
    _scroll.setFrameShape(_QFrame.NoFrame)
    _scroll.setHorizontalScrollBarPolicy(_Qt.ScrollBarAlwaysOff)
    _scroll.setWidget(wrap_with_empty_state(ctx.window, card, icon="scatter-chart"))
    layout.addWidget(_scroll, 1)

    canvas.mpl_connect("button_press_event", partial(on_scatter_click, ctx))
    canvas.mpl_connect("motion_notify_event", partial(on_scatter_motion, ctx))
    # Gate edge/corner editing: drag handles of existing gates to resize them.
    canvas.mpl_connect("button_press_event", partial(on_gate_edit_press, ctx))
    canvas.mpl_connect("motion_notify_event", partial(on_gate_edit_motion, ctx))
    canvas.mpl_connect("button_release_event", partial(on_gate_edit_release, ctx))


def _open_timepoint_selector(ctx) -> None:
    """Pop up the per-cell scatter's multi-timepoint picker."""
    open_timepoint_selector(
        ctx,
        selections=ctx.runtime.scatter.tp_selections,
        button_name="_scatter_tp_button",
        refresh_display=update_scatter_tp_selection_display,
        redraw=redraw_scatter_orchestrate,
    )
