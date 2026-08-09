"""Line Graphs tab builder (Qt port) — the 3-stacked-subplot figure in a v2 PlotCard."""

from __future__ import annotations

from well_viewer.core.channel_state_controller import on_metric_selected

from well_viewer.core.channel_state_controller import on_plot_channel_selected

from well_viewer.core.plot_card_actions import (
    copy_active_card_as_svg, save_active_card_figure, save_card_to_gallery,
)

from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QScrollArea,
    QVBoxLayout, QWidget,
)

from well_viewer.core.ui_helpers import (
    add_menu_action, install_canvas_wheel_scroll, make_action_menu_button,
    make_band_controls, make_plot_with_right_dock, make_properties_button,
    plotting_band_slot)
from functools import partial
from well_viewer.core.plot_orchestrator import redraw_app
from well_viewer.features.export.service import export_plot_data
from well_viewer.features.lineplot.controller import on_fig_click

# The Line Graphs figure: four stacked subplots (mean / fraction / N(above) / CDF).
_FIG_W, _FIG_H, _DPI = 7.2, 14.0, 100



def build_line_graphs_tab(ctx, parent: QWidget) -> None:
    from widgets.plot_card import PlotCard

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    # A8 burn-down: widget handles go through the WidgetRegistry (``ctx.widgets``),
    # which stamps ``app._<name>`` so legacy ``app._chan_cb_line`` / ``_line_*``
    # readers keep resolving. Non-widget state and behaviour calls still reach app.
    reg = ctx.widgets

    plot_area, layout, export_dock = make_plot_with_right_dock(parent)
    reg.register("_line_export_dock", export_dock)
    parent = plot_area

    # ── the shared control band above the card ──────────────────────────────
    line_ctrl = plotting_band_slot(ctx, "Line Graphs")

    # Channel combo: kept as an offscreen widget so the rest of the app can
    # still read/write the per-renderer channel through ``app._chan_cb_line``
    # and ``_plot_chan_var``; Plotting's band combo is the only visible
    # channel control.
    chan_cb = reg.register("_chan_cb_line", QComboBox())
    chan_cb.addItems(["GFP"])
    chan_cb.currentIndexChanged.connect(
        lambda _i, _src=chan_cb: on_plot_channel_selected(ctx, _src)
    )
    chan_cb.hide()
    if not ctx.widgets.has("_plot_chan_var"):
        reg.register("_plot_chan_var", chan_cb)   # legacy aliases → same widget
        reg.register("_chan_var", chan_cb)

    # The visible metric selector now lives in Plotting's band
    # (``_plotting_metric_cb``). This hidden per-tab combo stays as a
    # back-compat shim for code that still reads ``_metric_var`` /
    # ``_metric_cb``; the global combo wins when present.
    from well_viewer.data.metric_labels import METRIC_ORDER as _METRIC_ORDER
    metric_frame = reg.register("_metric_selector_frame", QWidget(line_ctrl))
    mfl = QHBoxLayout(metric_frame)
    mfl.setContentsMargins(0, 0, 0, 0)
    mfl.addWidget(QLabel("Property:", metric_frame))
    metric_cb = reg.register("_metric_cb", QComboBox(metric_frame))
    metric_cb.addItems(_METRIC_ORDER)
    metric_cb.currentIndexChanged.connect(lambda _i: on_metric_selected(ctx))
    mfl.addWidget(metric_cb)
    _plotting_metric_cb = ctx.widgets.get_or_none("_plotting_metric_cb")
    reg.register("_metric_var", _plotting_metric_cb if _plotting_metric_cb is not None else metric_cb)
    metric_frame.hide()

    # Fold-change normalization collapses into a popover button (C1) so it no
    # longer needs its own row.
    from well_viewer.core.fold_change_controls_view import install_fold_change_controls
    from well_viewer.core.ui_helpers import make_popover_button
    _fc_btn, _fc_panel, _fc_lay = make_popover_button("Fold change ▾", line_ctrl)
    _fc_btn.setToolTip("Fold-change normalization (vs a control well/set or a baseline timepoint)")
    install_fold_change_controls(ctx, _fc_panel, _fc_lay, scope="line")
    line_ctrl.add_action(_fc_btn)

    # Error band (SEM/SD + Spread) collapses into a "Display ▾" popover (C1)
    # rather than a separate card-controls row beneath the figure.
    _disp_btn, _disp_panel, _disp_lay = make_popover_button("Display ▾", line_ctrl)
    _disp_btn.setToolTip("Error band (SEM/SD) and per-FOV spread")
    _disp_lay.addWidget(make_band_controls(ctx, _disp_panel, with_fov=True))
    line_ctrl.add_action(_disp_btn)

    export_btn, export_menu = make_action_menu_button("Export ▾", line_ctrl,
                                                      tooltip="Export this plot")
    add_menu_action(export_menu, "Export CSV…", lambda: export_plot_data(ctx))
    add_menu_action(export_menu, "Copy SVG", lambda: copy_active_card_as_svg(ctx))
    add_menu_action(export_menu, "Save Figure…", lambda: save_active_card_figure(ctx))
    export_menu.addSeparator()
    add_menu_action(export_menu, "Save to Saved Plots",
                    lambda: save_card_to_gallery(ctx, "_line_card"))
    line_ctrl.add_action(export_btn)
    line_ctrl.add_action(make_properties_button(ctx, line_ctrl, "line"))

    # ── the figure, in a v2 PlotCard (card chrome + MplToolbar) ──────────────
    card = PlotCard(parent, figsize=(_FIG_W, _FIG_H), constrained=False,
                    header=False)
    from well_viewer.core.ui_helpers import apply_plot_card_decorators
    apply_plot_card_decorators(ctx, card, "Line Graphs")
    # No card header. It was built, given a plot-type switcher duplicating the
    # tab bar directly above it, and then hidden.
    card.setFigureTitle("")          # the tab name is the title; keep the header lean
    # Widget handles go through the WidgetRegistry; legacy ``app._line_*``
    # reads are forwarded to it by WellViewerApp.__getattr__.
    reg.register("_line_card", card)
    fig = reg.register("_line_fig", card.figure)
    ax_mean = reg.register("_line_ax_mean", fig.add_subplot(4, 1, 1))
    reg.register("_line_ax_frac", fig.add_subplot(4, 1, 2, sharex=ax_mean))
    reg.register("_line_ax_n",    fig.add_subplot(4, 1, 3, sharex=ax_mean))
    reg.register("_line_ax_cdf",  fig.add_subplot(4, 1, 4))
    fig.subplots_adjust(
        hspace=0.55, top=0.96, bottom=0.08, left=0.13, right=0.97,
    )
    canvas = reg.register("_line_canvas", card.canvas)
    canvas.setMinimumHeight(int(_FIG_H * _DPI))
    # The canvas' pin plus the card's own chrome, measured rather than a
    # literal: this used to add a hardcoded 110 counting "header / controls /
    # toolbar rows", and the card no longer builds a header.
    card.setMinimumHeight(int(_FIG_H * _DPI) + card.chromeHeight())

    # Error band now lives in the "Display ▾" popover on the control row (C1);
    # no separate card-controls row beneath the header.

    # The Publication↔Screen toggle is wired through plot_style.apply_ax_style
    # (it consults card.plotTheme() via ax.figure._plot_card). The stats chip is
    # left hidden — its statsChanged signal isn't wired to typed state yet (the
    # error band lives on the controls row above; Mean/Median is unsupported).
    card.plotThemeChanged.connect(lambda _m: redraw_app(ctx))
    # Per-panel copy (hover a subplot → small copy icon) echoes to the status bar.
    card.panelCopied.connect(ctx.notify.set_status)
    card.setStatsChipVisible(False)

    scroll = reg.register("_line_scroll_canvas", QScrollArea(parent))
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(card)
    install_canvas_wheel_scroll(canvas, scroll)
    # Wrap the scroll area in an EmptyState stack — page 0 is the placeholder,
    # page 1 is the figure. The reason gates it on the plate selection as well as
    # on the dataset: without one, this page painted the same two-line message
    # onto each of its four stacked panels, so the sentence stuttered down the
    # page and the last copy was clipped by the status bar. The hint names the
    # panels because they are the only thing that tells you what the page will
    # produce, and a single centred line otherwise deletes that promise.
    from well_viewer.core.ui_helpers import no_selection_reason, wrap_with_empty_state
    layout.addWidget(wrap_with_empty_state(
        ctx.window, scroll,
        reason=no_selection_reason(
            hint="Four panels: mean, fraction, N, and CDF. "
                 "Click wells on the plate at the left.",
        ),
    ), 1)

    # Right-click an axes → toggle its legend. (The old left-click-drag-to-set
    # threshold on the CDF was removed — the threshold lives on the Cell Gating tab.)
    canvas.mpl_connect("button_press_event", partial(on_fig_click, ctx))
