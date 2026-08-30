"""Response Curves tab — one figure, and the controls that scope it.

A leaf of its own rather than a mode on Line Graphs. That tab's fourth panel is
a CDF whose x is intensity, so a tab-wide x-source control would already be
wrong for a quarter of the figure, and a mode toggle would change what the
timepoint and fold-change controls beside it mean. A separate leaf also keeps
the one-line-removable property ``tests/test_feature_removal.py`` enforces.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from well_viewer.core.ui_helpers import (
    build_plot_save_menu, install_canvas_wheel_scroll, make_band_controls,
    make_popover_button, make_properties_button, plotting_band_slot,
)
# Module scope, not function-local: nothing in ``controller`` imports this
# module, so there is no cycle to work around — and a deferred feature import
# that is not breaking a cycle is one the budget guard counts for nothing.
from well_viewer.features.response.controller import (
    export_response_csv, on_x_variable_changed, redraw_response,
)

#: The figure. One axes, wide rather than tall — a dose–response is read across.
_FIG_W, _FIG_H, _DPI = 7.2, 4.6, 100


def build_response_tab(ctx, parent: QWidget) -> None:
    from widgets.labeled_combo import LabeledComboBox
    from widgets.plot_card import PlotCard

    from well_viewer.core.ui_helpers import (
        apply_plot_card_decorators, no_selection_reason, wrap_with_empty_state,
    )
    reg = ctx.widgets
    layout = parent.layout() or QVBoxLayout(parent)
    parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    band = plotting_band_slot(ctx, "Response Curves")

    # The x source is **scope**, not an action: it says what the figure is of,
    # like Channel and Property beside it, rather than doing something to it.
    x_combo = reg.register("_response_x_cb", LabeledComboBox("X"))
    x_combo.currentIndexChanged.connect(lambda *_: on_x_variable_changed(ctx))
    band.add_scope("", x_combo)

    display_btn, display_panel, display_lay = make_popover_button("Display ▾", band)
    display_btn.setToolTip("Error bars (SEM/SD) and per-FOV spread")
    display_lay.addWidget(make_band_controls(ctx, display_panel, with_fov=True))
    band.add_action(display_btn)

    export_btn, _menu, _act = build_plot_save_menu(
        ctx, band, card_key="_response_card", data_label="Save plot data…",
        data_cb=lambda: _export_response_data(ctx),
    )
    band.add_action(export_btn)
    band.add_action(make_properties_button(ctx, band, "response"))

    card = PlotCard(parent, figsize=(_FIG_W, _FIG_H), constrained=False,
                    header=False)
    apply_plot_card_decorators(ctx, card, "Response Curves")
    card.setFigureTitle("")
    reg.register("_response_card", card)
    fig = reg.register("_response_fig", card.figure)
    reg.register("_response_ax", fig.add_subplot(1, 1, 1))
    fig.subplots_adjust(top=0.94, bottom=0.28, left=0.13, right=0.97)
    canvas = reg.register("_response_canvas", card.canvas)
    canvas.setMinimumHeight(int(_FIG_H * _DPI))
    card.setMinimumHeight(int(_FIG_H * _DPI) + card.chromeHeight())
    card.setStatsChipVisible(False)

    scroll = reg.register("_response_scroll", QScrollArea(parent))
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(card)
    install_canvas_wheel_scroll(canvas, scroll)

    # The hint names what the page needs *and where to get it*, because a user
    # arriving here without a Number variable has no way to guess that the
    # answer is on a different pane's sub-tab.
    layout.addWidget(wrap_with_empty_state(
        ctx.window, scroll,
        reason=no_selection_reason(
            hint="Plots a measurement against a number you varied — dose, "
                 "density, temperature. Define it on Home → Variables as a "
                 "Number, fill it onto the plate, then pick it under X.",
        ),
    ), 1)

    redraw_response(ctx)


def _export_response_data(ctx) -> None:
    export_response_csv(ctx)
