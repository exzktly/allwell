"""Statistics-tab UI builders (Qt port)."""

from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QLabel, QTextEdit,
    QVBoxLayout, QWidget,
)

from well_viewer.core.ui_helpers import (
    btn_primary, btn_secondary, build_hline_separator, build_section_header,
)
from well_viewer.features.stats.controller import stats_active_group
from well_viewer.features.stats.controller import stats_apply_drag
from well_viewer.features.stats.controller import stats_on_test_change
from well_viewer.features.stats.controller import stats_refresh_group_list
from well_viewer.features.stats.controller import stats_refresh_fov_indicator
from well_viewer.features.stats.controller import stats_update_tp_menu
from well_viewer.features.stats.controller import stats_refresh_map
from functools import partial
from well_viewer.features.stats.controller import stats_grp_add
from well_viewer.features.stats.controller import stats_grp_clear_all
from well_viewer.features.stats.controller import stats_resync_from_sample_defs
from well_viewer.core.channel_state_controller import refresh_stats_property_combo
from well_viewer.core.safe import swallowed


from widgets.flow_layout import FlowLayout

def build_stats_tab(ctx, parent: QWidget) -> None:
    ctx.state.stats.groups = []
    ctx.state.stats.active_grp = -1
    build_stats_results_panel(ctx, parent)


def build_stats_group_editor(ctx, parent: QWidget) -> None:
    """Build the left-hand statistics group editor.

    A8 teardown: takes ``ctx`` — registers its widget handles through
    ``ctx.widgets`` (which stamp the legacy ``app._stats_*`` names for the
    controllers) and drives the group-editor controllers ctx-natively.
    """
    from well_viewer.core.ui_helpers import make_scrollable_canvas
    from widgets.well_plate_selector import WellPlateSelector

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # Plate map first — nothing should appear above the well picker.
    plate = WellPlateSelector(parent)
    plate.setActionsVisible(False)
    plate.setSelectionMode("select")
    plate.setDragSelectEnabled(True)
    plate.setRowColumnSelectable(True)
    plate.setEnabledWells([])
    from well_viewer.core.ui_helpers import well_plate_tooltip_provider
    plate.setWellTooltipProvider(well_plate_tooltip_provider(ctx))
    # Shared sidebar-plate sizing (see ui_helpers.apply_sidebar_plate_geometry):
    # scales to the rail width, keeps aspect, no fixed min-height floor.
    from well_viewer.core.ui_helpers import apply_sidebar_plate_geometry
    apply_sidebar_plate_geometry(plate)
    layout.addWidget(plate)
    ctx.widgets.register("_stats_map_plate", plate)

    layout.addWidget(build_hline_separator(parent))
    hdr = build_section_header(
        parent,
        "COMPARISON GROUPS",
        buttons=(
            btn_secondary(parent, "Clear All", partial(stats_grp_clear_all, ctx)),
            btn_primary(parent, "+ Add", partial(stats_grp_add, ctx)),
        ),
    )
    layout.addWidget(hdr)

    # Pull the comparison groups straight from the shared Sample-Definition
    # sets (S1) — own full-width row so the narrow sidebar doesn't truncate it.
    from_defs_btn = btn_secondary(
        parent, "↻ Load groups from Sample Definitions",
        partial(stats_resync_from_sample_defs, ctx),
    )
    layout.addWidget(from_defs_btn)

    ctx.state.stats.drag_adding = True
    ctx.state.stats.drag_visited = set()

    def _commit_plate_to_group(*_a) -> None:
        grp = stats_active_group(ctx)
        if grp is None:
            stats_refresh_map(ctx)        # no active group — revert the click
            return
        new = {w for w in plate.selectedWellIds() if w in ctx.state.dataset.well_paths}
        old = {w for w in grp.wells if w in ctx.state.dataset.well_paths}
        added, removed = new - old, old - new
        if not added and not removed:
            return
        # Reuse stats_apply_drag's "well in a loaded rep-set → add/remove the whole
        # set; else solo-well add/remove" logic, one token at a time.
        for tok in added:
            ctx.state.stats.drag_adding = True
            ctx.state.stats.drag_visited = set()
            stats_apply_drag(ctx, tok)
        for tok in removed:
            ctx.state.stats.drag_adding = False
            ctx.state.stats.drag_visited = set()
            stats_apply_drag(ctx, tok)
        stats_refresh_map(ctx)
        stats_refresh_group_list(ctx)
    # selectionDragFinished fires once per click *and* once at the end of a drag.
    plate.selectionDragFinished.connect(_commit_plate_to_group)

    sep2 = QFrame(parent)
    sep2.setObjectName("Separator")
    sep2.setFrameShape(QFrame.HLine)
    sep2.setFixedHeight(1)
    layout.addWidget(sep2)

    sa, inner = make_scrollable_canvas(parent)
    layout.addWidget(sa, 1)
    ctx.widgets.register("_stats_grp_canvas", sa)
    ctx.widgets.register("_stats_grp_inner", inner)

    help_lbl = QLabel(
        "Drag wells to assign them to a group. Select a group card first.",
        parent,
    )
    help_lbl.setObjectName("Muted")
    help_lbl.setWordWrap(True)
    layout.addWidget(help_lbl)

    stats_refresh_map(ctx)
    stats_refresh_group_list(ctx)


def build_stats_results_panel(ctx, parent: QWidget) -> None:
    """Build the right-hand stats controls/results panel.

    A8 teardown: takes ``ctx`` — every widget handle is registered through
    ``ctx.widgets`` (which stamps the legacy ``app._stats_*`` names the stats
    controllers read), the change/refresh callbacks route through their
    ctx-native controllers, and the Run/Property actions resolve through the
    ``ctx.runtime.stats`` namespace so the builder names no god-object.
    """
    reg = ctx.widgets
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(0)

    stats_ctrl = reg.register("_stats_ctrl", QWidget(parent))
    stats_ctrl.setObjectName("TabCtrl")  # data-gated until a dataset loads
    cl = QGridLayout(stats_ctrl)
    cl.setContentsMargins(0, 0, 0, 6)

    test_label = reg.register("_stats_test_label", QLabel("Test:", stats_ctrl))
    cl.addWidget(test_label, 0, 0, Qt.AlignLeft)

    test_cb = reg.register("_stats_test_cb", QComboBox(stats_ctrl))
    test_cb.addItems([
        "t-test (Welch, unequal var)",
        "t-test (Student, pooled var)",
        "Wilcoxon rank-sum",
        "Mann-Whitney U",
        "KS test (2 wells only)",
    ])
    test_cb.setCurrentText("t-test (Welch, unequal var)")
    test_cb.currentIndexChanged.connect(
        lambda _i: stats_on_test_change(ctx)
    )
    cl.addWidget(test_cb, 0, 1, Qt.AlignLeft)

    tp_label = reg.register("_stats_tp_label", QLabel("Timepoint:", stats_ctrl))
    cl.addWidget(tp_label, 1, 0, Qt.AlignLeft)

    tp_cb = reg.register("_stats_tp_cb", QComboBox(stats_ctrl))
    tp_cb.addItems(["—"])
    cl.addWidget(tp_cb, 1, 1, Qt.AlignLeft)

    channel_label = reg.register("_stats_channel_label", QLabel("Channel:", stats_ctrl))
    cl.addWidget(channel_label, 2, 0, Qt.AlignLeft)

    channel_cb = reg.register("_stats_channel_cb", QComboBox(stats_ctrl))
    channel_cb.addItems(["—"])
    channel_cb.currentIndexChanged.connect(
        lambda _i: stats_on_test_change(ctx)
    )
    cl.addWidget(channel_cb, 2, 1, Qt.AlignLeft)

    # Per-tab Property combo — picks which CSV column drives the test.
    # Mirrors the global ctxbar Property combo's state, kept local so it
    # appears next to the channel/statistic selectors.
    from well_viewer.data.metric_labels import METRIC_ORDER as _ST_METRIC_ORDER
    property_label = reg.register("_stats_property_label", QLabel("Property:", stats_ctrl))
    cl.addWidget(property_label, 3, 0, Qt.AlignLeft)
    property_cb = reg.register("_stats_property_cb", QComboBox(stats_ctrl))
    property_cb.addItems(_ST_METRIC_ORDER)
    property_cb.currentIndexChanged.connect(
        lambda _i: ctx.runtime.stats.property_change()
    )
    cl.addWidget(property_cb, 3, 1, Qt.AlignLeft)

    statistic_label = reg.register("_stats_statistic_label", QLabel("Statistic:", stats_ctrl))
    cl.addWidget(statistic_label, 4, 0, Qt.AlignLeft)

    statistic_cb = reg.register("_stats_statistic_cb", QComboBox(stats_ctrl))
    statistic_cb.addItems([
        "Mean (above threshold)",
        "Mean (all cells)",
        "Median (above threshold)",
        "Fraction above threshold",
    ])
    statistic_cb.setCurrentText("Mean (above threshold)")
    statistic_cb.currentIndexChanged.connect(
        lambda _i: stats_on_test_change(ctx)
    )
    cl.addWidget(statistic_cb, 4, 1, Qt.AlignLeft)

    # Keep the label/field columns tight against the left edge. Without a
    # trailing stretch column the grid split its width evenly across all
    # columns, leaving a large empty gap between every label and its field.
    cl.setColumnStretch(2, 1)
    layout.addWidget(stats_ctrl)

    # "Run test" on its own row beneath the form — previously it spanned all
    # five grid rows in a third column and rendered vertically centered (it
    # appeared to float beside the Channel row, disconnected from the form).
    run_row = QWidget(parent)
    run_row.setObjectName("TabCtrl")  # data-gated with the stats form above
    rrl = FlowLayout(run_row)
    rrl.setContentsMargins(0, 0, 0, 6)
    run_btn = btn_primary(run_row, "Run test", ctx.runtime.stats.run)
    rrl.addWidget(run_btn)
    rrl.addStretch(1)
    # Acknowledge the replicate mode the test will use (driven by the shared
    # per-FOV toggle) so the tab is explicit about the unit of replication.
    fov_indicator = reg.register("_stats_fov_indicator", QLabel("", run_row))
    fov_indicator.setObjectName("MutedCaption")
    # No word-wrap: inside a FlowLayout a wrapped label reports its longest-word
    # width as its minimum, so the flow allocates it narrow and the single-line
    # "Replicates: …" caption wraps into a tall stack even with the whole row
    # free. Left un-wrapped it keeps one line and the flow places it whole.
    rrl.addWidget(fov_indicator)
    stats_refresh_fov_indicator(ctx)
    layout.addWidget(run_row)

    sep = reg.register("_stats_sep", QFrame(parent))
    sep.setObjectName("Separator")
    sep.setFrameShape(QFrame.HLine)
    sep.setFixedHeight(1)
    layout.addWidget(sep)

    fig_frame = reg.register("_stats_fig_frame", QWidget(parent))
    ff_l = QVBoxLayout(fig_frame)
    ff_l.setContentsMargins(0, 0, 0, 0)

    from matplotlib.figure import Figure as _StatsFigure
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as _StatsFCA

    fig = reg.register("_stats_fig", _StatsFigure(figsize=(5, 2.8), dpi=96))
    reg.register("_stats_ax", fig.add_subplot(111))
    canvas_widget = reg.register("_stats_canvas_widget", _StatsFCA(fig))
    ff_l.addWidget(canvas_widget)
    layout.addWidget(fig_frame)
    # Hide the figure frame until a test that actually plots (KS) is run.
    # run_stats toggles visibility per-test; before the first run the user
    # sees the controls + result text only, no empty canvas.
    fig_frame.setVisible(False)

    res_frame = reg.register("_stats_res_frame", QWidget(parent))
    rl = QVBoxLayout(res_frame)
    rl.setContentsMargins(0, 6, 0, 6)
    result_text = reg.register("_stats_result_text", QTextEdit(res_frame))
    result_text.setReadOnly(True)
    result_text.setLineWrapMode(QTextEdit.WidgetWidth)
    # Hint in the otherwise-blank results panel before the first run.
    result_text.setPlaceholderText(
        "Select two comparison groups (left panel), choose a test, then click "
        "Run test to see results here."
    )
    rl.addWidget(result_text)
    layout.addWidget(res_frame, 1)

    stats_update_tp_menu(ctx)

    # Reshape the per-tab Property combo to match the currently-selected
    # stats channel (ratio channels collapse to ``Calculated Val``); then
    # sync the selection to the canonical ``_active_metric``.
    try:
        refresh_stats_property_combo(ctx)
        from well_viewer.data.metric_labels import METRIC_KEY_TO_LABEL
        label = METRIC_KEY_TO_LABEL.get(
            ctx.state.channel.active_metric, "Mean Intensity"
        )
        idx = property_cb.findText(label)
        if idx >= 0:
            blocked = property_cb.blockSignals(True)
            try:
                property_cb.setCurrentIndex(idx)
            finally:
                property_cb.blockSignals(blocked)
    except Exception:
        swallowed()  # was: pass (silent except)
