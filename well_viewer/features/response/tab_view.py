"""Response Curves tab — one figure, and the controls that scope it.

A leaf of its own rather than a mode on Line Graphs. That tab's fourth panel is
a CDF whose x is intensity, so a tab-wide x-source control would already be
wrong for a quarter of the figure, and a mode toggle would change what the
timepoint and fold-change controls beside it mean. A separate leaf also keeps
the one-line-removable property ``tests/test_feature_removal.py`` enforces.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame, QPushButton, QScrollArea, QWidget,
)

from well_viewer.core.fold_change_controls_view import (
    install_fold_change_controls,
)
from well_viewer.core.ui_helpers import (
    build_plot_save_menu, install_canvas_wheel_scroll, make_band_controls,
    make_plot_with_right_dock, make_popover_button, make_properties_button,
    plotting_band_slot,
)
# Module scope, not function-local: nothing in ``controller`` imports this
# module, so there is no cycle to work around — and a deferred feature import
# that is not breaking a cycle is one the budget guard counts for nothing.
from well_viewer.features.export.service import export_cell_data_from
from well_viewer.features.response.cell_scope_compute import cell_scope
from well_viewer.features.response.controller import (
    Y_METRIC_LABELS, export_response_csv, fit_model_labels, fit_settings,
    on_series_changed, on_x_variable_changed, open_timepoint_selector,
    redraw_response, selected_y_metric, set_fit_bound, set_fit_model,
    set_y_metric, update_timepoint_button,
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
    # The right-side dock the Properties sidebar mounts into, the same one every
    # other plotting leaf builds. Without it ``style._resolve_export_dock``
    # returns None and ``launch_export_editor`` falls back to appending the
    # sidebar into the PlotCard's own layout — inside this tab's QScrollArea,
    # below the toolbar — so the panel opened somewhere no other tab puts it.
    # Adding the key to ``open_export_style_panel``'s roster alone was half the
    # sweep: that file holds *two* rosters answering the same question, a
    # hundred lines apart.
    plot_area, layout, export_dock = make_plot_with_right_dock(parent)
    reg.register("_response_export_dock", export_dock)
    parent = plot_area
    layout.setContentsMargins(0, 0, 0, 0)

    band = plotting_band_slot(ctx, "Response Curves")

    # The x source is **scope**, not an action: it says what the figure is of,
    # like Channel and Property beside it, rather than doing something to it.
    x_combo = reg.register("_response_x_cb", LabeledComboBox("X"))
    x_combo.currentIndexChanged.connect(lambda *_: on_x_variable_changed(ctx))
    band.add_scope("", x_combo)

    # Scope: what the curves measure. Beside X because the pair of them is the
    # figure's subject — "fraction above threshold against dose" is one phrase.
    y_combo = reg.register("_response_y_cb", LabeledComboBox("Y"))
    y_combo.setToolTip(
        "What the curves plot — the channel's active Property, or the fraction "
        "of cells above the ON cutoff")
    for label, metric in Y_METRIC_LABELS:
        y_combo.addItem(label, metric)
    _index = y_combo.findData(selected_y_metric(ctx))
    y_combo.setCurrentIndex(_index if _index >= 0 else 0)
    y_combo.currentIndexChanged.connect(
        lambda *_: set_y_metric(ctx, y_combo.currentData() or ""))
    band.add_scope("", y_combo)

    # Scope, beside X and for the same reason: it says what the figure is *of*.
    # One curve per value of this variable per timepoint, so a plate of two cell
    # lines across a dose series is two curves rather than one line doubling
    # back at every dose.
    series_combo = reg.register("_response_series_cb", LabeledComboBox("Series"))
    series_combo.setToolTip(
        "Split the members into one curve per value of a second variable — "
        "colour carries the series, dashes carry the timepoint")
    series_combo.currentIndexChanged.connect(lambda *_: on_series_changed(ctx))
    band.add_scope("", series_combo)

    # The Bar tab's picker, over this tab's own selection. Self-describing —
    # the button's own text carries the count, so it needs no caption beside it.
    tp_button = reg.register("_response_tp_button",
                             QPushButton("Timepoints: 0 selected"))
    tp_button.setProperty("variant", "secondary")
    tp_button.clicked.connect(lambda _=False: open_timepoint_selector(ctx))
    band.add_scope("", tp_button)

    display_btn, display_panel, display_lay = make_popover_button("Display ▾", band)
    display_btn.setToolTip("Error bars (SEM/SD) and per-FOV spread")
    display_lay.addWidget(make_band_controls(ctx, display_panel, with_fov=True))
    band.add_action(display_btn)

    band.add_action(_build_fit_popover(ctx, band))

    # The same control set the Bar and Line tabs carry, over the same shared
    # state — a control picked here is picked there. Its own scope, so a redraw
    # requested while this leaf is on screen reaches this figure rather than
    # being deferred as though the tab were hidden.
    fc_button, fc_panel, fc_layout = make_popover_button("Fold change ▾", band)
    fc_button.setToolTip(
        "Fold-change normalization (vs a control well/set or a baseline "
        "timepoint). vs t0 needs more than one timepoint drawn.")
    install_fold_change_controls(ctx, fc_panel, fc_layout, scope="response")
    band.add_action(fc_button)

    # ``cells_cb`` is required rather than optional, deliberately: a tab cannot
    # acquire this menu without offering the cells its plot was drawn from. It
    # was missing here, and because it is keyword-only the omission was a
    # TypeError at build time rather than a menu with a gap — which is how the
    # whole tab came to raise on every real app construction.
    export_btn, _menu, _act = build_plot_save_menu(
        ctx, band, card_key="_response_card", data_label="Save plot data…",
        data_cb=lambda: _export_response_data(ctx),
        cells_cb=lambda: export_cell_data_from(ctx, cell_scope),
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

    update_timepoint_button(ctx)
    redraw_response(ctx)


def _build_fit_popover(ctx, band):
    """Model, and the two plateaus that can be pinned. One set for the figure.

    Shared across the curves rather than offered per curve: two timepoints
    fitted with independently floating tops give two EC50s that cannot be
    compared, and comparing them is the only reason to overlay them.

    An action rather than scope: it does not change what the figure is *of*.
    """
    from PySide6.QtWidgets import QLabel, QLineEdit

    from widgets.labeled_combo import LabeledComboBox

    button, panel, layout = make_popover_button("Fit ▾", band)
    button.setToolTip("Fit a response curve and report its half-maximal value")

    model, top, bottom = fit_settings(ctx)
    combo = ctx.widgets.register("_response_fit_cb", LabeledComboBox("Model"))
    for label, model_id in fit_model_labels():
        combo.addItem(label, model_id)
    index = combo.findData(model)
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.currentIndexChanged.connect(
        lambda *_: set_fit_model(ctx, combo.currentData() or ""))
    layout.addWidget(combo)

    hint = QLabel("Leave a plateau blank to fit it.", panel)
    hint.setObjectName("Muted")
    hint.setWordWrap(True)
    layout.addWidget(hint)

    for which, value in (("top", top), ("bottom", bottom)):
        edit = ctx.widgets.register(f"_response_fit_{which}",
                                    QLineEdit(panel))
        edit.setPlaceholderText(f"{which.title()} — free")
        edit.setText("" if value is None else f"{value:g}")
        edit.editingFinished.connect(
            lambda w=which, e=edit: set_fit_bound(ctx, w, e.text()))
        layout.addWidget(edit)

    return button


def _export_response_data(ctx) -> None:
    export_response_csv(ctx)
