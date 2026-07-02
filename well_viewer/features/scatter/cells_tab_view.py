"""Scatter Plot: Cells tab builder (Qt port) — single scatter in a v2 PlotCard."""

from __future__ import annotations

from well_viewer.core.plot_card_actions import (
    copy_active_card_as_svg, save_active_card_figure,
)

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout,
    QWidget, QWidgetAction,
)

from well_viewer.core.ui_helpers import (
    make_plot_with_right_dock, make_popover_button,
    make_plot_view_switcher)
from well_viewer.features.scatter.controller import redraw_scatter_orchestrate
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

    ctrl = QWidget(parent)
    ctrl.setObjectName("TabCtrl")
    cl = QHBoxLayout(ctrl)
    cl.setContentsMargins(10, 6, 10, 6)

    # Per-axis channel + property selectors. The scatter tab overrides
    # the global ctxbar channel/property combos (those are hidden when
    # Scatter Plot is the active plotting subtab) because each axis
    # needs to render an independent column.
    from well_viewer.data.metric_labels import METRIC_ORDER as _METRIC_ORDER
    cl.addWidget(QLabel("X-axis:", ctrl))
    cl.addWidget(QLabel("Channel:", ctrl))
    ch_x_cb = reg.register("_scatter_ch_x_cb", QComboBox(ctrl))
    ch_x_cb.addItems(["GFP"])
    ch_x_cb.currentIndexChanged.connect(lambda _i: ctx.app._on_scatter_axis_change("x"))
    cl.addWidget(ch_x_cb)
    reg.register("_scatter_ch_x_var", ch_x_cb)

    cl.addWidget(QLabel("Property:", ctrl))
    metric_x_cb = reg.register("_scatter_metric_x_cb", QComboBox(ctrl))
    metric_x_cb.addItems(_METRIC_ORDER)
    metric_x_cb.currentIndexChanged.connect(lambda _i: redraw_scatter_orchestrate(ctx.app))
    cl.addWidget(metric_x_cb)
    reg.register("_scatter_metric_x_var", metric_x_cb)

    cl.addWidget(QLabel("Y-axis:", ctrl))
    cl.addWidget(QLabel("Channel:", ctrl))
    ch_y_cb = reg.register("_scatter_ch_y_cb", QComboBox(ctrl))
    ch_y_cb.addItems(["GFP"])
    ch_y_cb.currentIndexChanged.connect(lambda _i: ctx.app._on_scatter_axis_change("y"))
    cl.addWidget(ch_y_cb)
    reg.register("_scatter_ch_y_var", ch_y_cb)

    cl.addWidget(QLabel("Property:", ctrl))
    metric_y_cb = reg.register("_scatter_metric_y_cb", QComboBox(ctrl))
    metric_y_cb.addItems(_METRIC_ORDER)
    metric_y_cb.currentIndexChanged.connect(lambda _i: redraw_scatter_orchestrate(ctx.app))
    cl.addWidget(metric_y_cb)
    reg.register("_scatter_metric_y_var", metric_y_cb)

    cl.addWidget(QLabel("Timepoints:", ctrl))
    tp_button = reg.register("_scatter_tp_button", QPushButton("Select Timepoints", ctrl))
    tp_button.setProperty("variant", "secondary")
    tp_button.clicked.connect(lambda _=False: _open_timepoint_selector(ctx.app))
    cl.addWidget(tp_button)
    tp_label = reg.register("_scatter_tp_label", QLabel("(0 selected)", ctrl))
    tp_label.setObjectName("Muted")
    cl.addWidget(tp_label)
    # Hidden legacy single-tp combo — kept so export / save-figure paths that
    # still read ``_scatter_tp_var.currentText()`` see a valid timepoint string
    # (the first selected one).
    tp_cb = reg.register("_scatter_tp_cb", QComboBox(ctrl))
    tp_cb.addItems(["0"])
    tp_cb.hide()
    reg.register("_scatter_tp_var", tp_cb)
    ctx.app._scatter_tp_selections = {}

    # Secondary display options (Layout + Colour-by-time) collapse into a
    # "Display ▾" popover so the per-cell scatter control row stays readable —
    # the X/Y Channel & Property selectors are the controls that matter inline.
    _disp_btn, _disp_panel, _disp_lay = make_popover_button("Display ▾", ctrl)
    _disp_btn.setToolTip("Scatter display options: layout and colour-by-time")
    cl.addWidget(_disp_btn)

    # Layout: overlay every group on one axis (default, interactive) or stack
    # one group per axis below the other (view-only comparison) — mirrors the
    # Distribution tab's Overlay / Faceted choice.
    _disp_lay.addWidget(QLabel("Layout:", _disp_panel))
    layout_cb = reg.register("_scatter_layout_cb", QComboBox(_disp_panel))
    layout_cb.addItems(["Overlay", "Separate axes"])
    layout_cb.currentIndexChanged.connect(
        lambda _i: redraw_scatter_orchestrate(ctx.app))
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
        redraw_scatter_orchestrate(ctx.app)

    color_by_tp_btn.clicked.connect(_toggle_color_by_tp)
    _disp_lay.addWidget(color_by_tp_btn)
    # Rectangular-gate state (drawn via the Properties sidebar's Gates section):
    # a list of {x0,y0,x1,y1} data-space boxes, the arm flag, and the live
    # matplotlib RectangleSelector (rebuilt after each redraw).
    ctx.app._scatter_gates = []
    ctx.app._scatter_gate_draw_mode = False
    ctx.app._scatter_gate_selector = None
    # "Show gates" toggle (sidebar) is typed state: ctx.state.scatter.gates_hidden
    # Edge/corner editing of existing gates: the in-progress edit ({idx,handle})
    # and the cached render context for repainting just the gates while dragging.
    ctx.app._scatter_gate_edit = None
    # Per-panel gate render entries (overlay = 1, separate-axes = N), used to
    # repaint every panel's gate during an edit drag.
    ctx.app._scatter_gate_render = None
    # gating.py triggers redraws through this callback so it never has to import
    # the controller (avoids a scatter-internal import cycle).
    ctx.app._scatter_gate_redraw_cb = redraw_scatter_orchestrate
    cl.addStretch(1)

    from widgets.icon_button import IconButton as _IconButton
    _export_btn = _IconButton("download", ctrl, tooltip="Export CSV")
    _export_btn.clicked.connect(lambda _=False: export_scatter_data(ctx.app))
    cl.addWidget(_export_btn)
    _copy_btn = _IconButton("copy", ctrl, tooltip="Copy SVG")
    _copy_btn.clicked.connect(lambda _=False: copy_active_card_as_svg(ctx))
    cl.addWidget(_copy_btn)
    _save_btn = _IconButton("save", ctrl, tooltip="Save figure")
    _save_btn.clicked.connect(lambda _=False: save_active_card_figure(ctx))
    cl.addWidget(_save_btn)
    style_btn = _IconButton(
        "sliders-horizontal", ctrl,
        tooltip="Show / hide the figure properties panel",
    )
    style_btn.clicked.connect(lambda _=False: ctx.app._open_export_style_panel("scatter_cells"))
    cl.addWidget(style_btn)
    layout.addWidget(ctrl)

    card = PlotCard(parent, figsize=(8, 6), constrained=False)
    from well_viewer.features.saved_plots.controller import wire_save_to_gallery
    wire_save_to_gallery(ctx.app, card, "Scatter Plot")
    _sw = make_plot_view_switcher(ctx.app, 'Scatter Plot')
    if _sw is not None:
        card.setLeftHeaderWidget(_sw)
    # Hide the per-card header — the ctxbar above already carries the same controls.
    card.setHeaderVisible(False)
    card.setFigureTitle("")
    reg.register("_scatter_card", card)
    fig = reg.register("_scatter_fig", card.figure)
    reg.register("_ax_scatter", fig.add_subplot(1, 1, 1))
    fig.subplots_adjust(hspace=0.3, top=0.95, bottom=0.12, left=0.16, right=0.97)
    canvas = reg.register("_scatter_canvas", card.canvas)
    # No error-band controls here: the per-cell scatter draws individual points,
    # so SEM/SD has no meaning (it belongs only on the aggregate scatter).
    card.plotThemeChanged.connect(lambda _m: redraw_scatter_orchestrate(ctx.app))
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
    _scroll.setWidget(wrap_with_empty_state(ctx.app, card, icon="scatter-chart"))
    layout.addWidget(_scroll, 1)

    canvas.mpl_connect("button_press_event", partial(on_scatter_click, ctx.app))
    canvas.mpl_connect("motion_notify_event", partial(on_scatter_motion, ctx.app))
    # Gate edge/corner editing: drag handles of existing gates to resize them.
    canvas.mpl_connect("button_press_event", partial(on_gate_edit_press, ctx.app))
    canvas.mpl_connect("motion_notify_event", partial(on_gate_edit_motion, ctx.app))
    canvas.mpl_connect("button_release_event", partial(on_gate_edit_release, ctx.app))


def _open_timepoint_selector(app) -> None:
    """Pop up a QMenu of checkboxes below the per-cell Timepoints button.

    Mirrors the aggregate scatter's multi-tp picker so a single config
    pattern covers both tabs.
    """
    menu = QMenu(app._scatter_tp_button)

    checkboxes: dict[str, QCheckBox] = {}
    for tp_str in sorted(app._scatter_tp_selections.keys(), key=float):
        cb = QCheckBox(tp_str, menu)
        cb.setChecked(bool(app._scatter_tp_selections[tp_str]))
        checkboxes[tp_str] = cb

        def _make_handler(tp=tp_str, checkbox=cb):
            def on_toggled(checked: bool) -> None:
                app._scatter_tp_selections[tp] = bool(checked)
                app._update_scatter_tp_selection_display()
                redraw_scatter_orchestrate(app)
            return on_toggled

        cb.toggled.connect(_make_handler())
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(cb)
        menu.addAction(wa)

    menu.addSeparator()

    def _set_all(value: bool) -> None:
        for tp_str, cb in checkboxes.items():
            cb.blockSignals(True)
            try:
                cb.setChecked(value)
            finally:
                cb.blockSignals(False)
            app._scatter_tp_selections[tp_str] = value
        app._update_scatter_tp_selection_display()
        redraw_scatter_orchestrate(app)

    all_action = QAction("All", menu)
    all_action.triggered.connect(lambda _=False: _set_all(True))
    menu.addAction(all_action)
    none_action = QAction("None", menu)
    none_action.triggered.connect(lambda _=False: _set_all(False))
    menu.addAction(none_action)

    btn = app._scatter_tp_button
    menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
