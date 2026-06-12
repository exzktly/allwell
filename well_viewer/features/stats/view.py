"""Statistics-tab UI builders (Qt port)."""

from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QTextEdit,
    QVBoxLayout, QWidget,
)

from well_viewer.core.ui_helpers import (
    btn_primary, btn_secondary, build_hline_separator, build_section_header,
)
from well_viewer.features.stats.controller import stats_apply_drag
from well_viewer.features.stats.controller import stats_on_test_change
from well_viewer.features.stats.controller import stats_refresh_group_list
from well_viewer.features.stats.controller import stats_update_tp_menu
from well_viewer.features.stats.controller import stats_refresh_map
from functools import partial
from well_viewer.features.stats.controller import stats_grp_add
from well_viewer.features.stats.controller import stats_grp_clear_all
from well_viewer.core.channel_state_controller import refresh_stats_property_combo
from well_viewer.core.safe import swallowed


def build_stats_tab(app, parent: QWidget) -> None:
    app._app_state.stats.groups = []
    app._app_state.stats.active_grp = -1
    build_stats_results_panel(app, parent)


def build_stats_group_editor(app, parent: QWidget) -> None:
    """Build the left-hand statistics group editor."""
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
    # Match the main sidebar plate's geometry so the picker stays in the
    # same screen position when the user switches tabs.
    plate.setMinimumHeight(280)
    from PySide6.QtWidgets import QSizePolicy as _SizePolicy
    _sp = _SizePolicy(_SizePolicy.Preferred, _SizePolicy.Preferred)
    _sp.setHeightForWidth(True)
    plate.setSizePolicy(_sp)
    layout.addWidget(plate)
    app._stats_map_plate = plate

    layout.addWidget(build_hline_separator(parent))
    hdr = build_section_header(
        parent,
        "COMPARISON GROUPS",
        buttons=(
            btn_secondary(parent, "Clear All", partial(stats_grp_clear_all, app)),
            btn_primary(parent, "+ Add", partial(stats_grp_add, app)),
        ),
    )
    layout.addWidget(hdr)

    app._app_state.stats.drag_adding = True
    app._app_state.stats.drag_visited = set()

    def _commit_plate_to_group(*_a) -> None:
        grp = app._stats_active_group()
        if grp is None:
            stats_refresh_map(app)        # no active group — revert the click
            return
        new = {w for w in plate.selectedWellIds() if w in app._app_state.dataset.well_paths}
        old = {w for w in grp.wells if w in app._app_state.dataset.well_paths}
        added, removed = new - old, old - new
        if not added and not removed:
            return
        # Reuse stats_apply_drag's "well in a loaded rep-set → add/remove the whole
        # set; else solo-well add/remove" logic, one token at a time.
        for tok in added:
            app._app_state.stats.drag_adding = True
            app._app_state.stats.drag_visited = set()
            stats_apply_drag(app, tok)
        for tok in removed:
            app._app_state.stats.drag_adding = False
            app._app_state.stats.drag_visited = set()
            stats_apply_drag(app, tok)
        stats_refresh_map(app)
        stats_refresh_group_list(app)
    # selectionDragFinished fires once per click *and* once at the end of a drag.
    plate.selectionDragFinished.connect(_commit_plate_to_group)

    sep2 = QFrame(parent)
    sep2.setObjectName("Separator")
    sep2.setFrameShape(QFrame.HLine)
    sep2.setFixedHeight(1)
    layout.addWidget(sep2)

    sa, inner = make_scrollable_canvas(parent)
    layout.addWidget(sa, 1)
    app._stats_grp_canvas = sa
    app._stats_grp_inner = inner

    help_lbl = QLabel(
        "Drag wells to assign them to a group. Select a group card first.",
        parent,
    )
    help_lbl.setObjectName("Muted")
    help_lbl.setWordWrap(True)
    layout.addWidget(help_lbl)

    stats_refresh_map(app)
    stats_refresh_group_list(app)


def build_stats_results_panel(app, parent: QWidget) -> None:
    """Build the right-hand stats controls/results panel."""
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    app._stats_hdr = QWidget(parent)
    app._stats_hdr.setObjectName("Sidebar")
    hl = QHBoxLayout(app._stats_hdr)
    hl.setContentsMargins(12, 4, 12, 4)
    app._stats_hdr_label = QLabel("STATISTICAL TEST", app._stats_hdr)
    app._stats_hdr_label.setProperty("role", "section")
    hl.addWidget(app._stats_hdr_label)
    hl.addStretch(1)
    layout.addWidget(app._stats_hdr)

    app._stats_ctrl = QWidget(parent)
    app._stats_ctrl.setObjectName("TabCtrl")  # data-gated until a dataset loads
    cl = QGridLayout(app._stats_ctrl)
    cl.setContentsMargins(12, 6, 12, 6)

    app._stats_test_label = QLabel("Test:", app._stats_ctrl)
    cl.addWidget(app._stats_test_label, 0, 0, Qt.AlignLeft)

    app._stats_test_cb = QComboBox(app._stats_ctrl)
    app._stats_test_cb.addItems([
        "t-test (Fisher)",
        "Wilcoxon rank-sum",
        "Mann-Whitney U",
        "KS test (2 wells only)",
    ])
    app._stats_test_cb.setCurrentText("t-test (Fisher)")
    app._stats_test_cb.currentIndexChanged.connect(
        lambda _i: stats_on_test_change(app)
    )
    cl.addWidget(app._stats_test_cb, 0, 1, Qt.AlignLeft)

    app._stats_tp_label = QLabel("Timepoint:", app._stats_ctrl)
    cl.addWidget(app._stats_tp_label, 1, 0, Qt.AlignLeft)

    app._stats_tp_cb = QComboBox(app._stats_ctrl)
    app._stats_tp_cb.addItems(["—"])
    cl.addWidget(app._stats_tp_cb, 1, 1, Qt.AlignLeft)

    app._stats_channel_label = QLabel("Channel:", app._stats_ctrl)
    cl.addWidget(app._stats_channel_label, 2, 0, Qt.AlignLeft)

    app._stats_channel_cb = QComboBox(app._stats_ctrl)
    app._stats_channel_cb.addItems(["—"])
    app._stats_channel_cb.currentIndexChanged.connect(
        lambda _i: stats_on_test_change(app)
    )
    cl.addWidget(app._stats_channel_cb, 2, 1, Qt.AlignLeft)

    # Per-tab Property combo — picks which CSV column drives the test.
    # Mirrors the global ctxbar Property combo's state, kept local so it
    # appears next to the channel/statistic selectors.
    from well_viewer.data.metric_labels import METRIC_ORDER as _ST_METRIC_ORDER
    app._stats_property_label = QLabel("Property:", app._stats_ctrl)
    cl.addWidget(app._stats_property_label, 3, 0, Qt.AlignLeft)
    app._stats_property_cb = QComboBox(app._stats_ctrl)
    app._stats_property_cb.addItems(_ST_METRIC_ORDER)
    app._stats_property_cb.currentIndexChanged.connect(
        lambda _i: app._on_stats_property_change()
    )
    cl.addWidget(app._stats_property_cb, 3, 1, Qt.AlignLeft)

    app._stats_statistic_label = QLabel("Statistic:", app._stats_ctrl)
    cl.addWidget(app._stats_statistic_label, 4, 0, Qt.AlignLeft)

    app._stats_statistic_cb = QComboBox(app._stats_ctrl)
    app._stats_statistic_cb.addItems([
        "Mean (above threshold)",
        "Mean (all cells)",
        "Median (above threshold)",
        "Fraction above threshold",
    ])
    app._stats_statistic_cb.setCurrentText("Mean (above threshold)")
    app._stats_statistic_cb.currentIndexChanged.connect(
        lambda _i: stats_on_test_change(app)
    )
    cl.addWidget(app._stats_statistic_cb, 4, 1, Qt.AlignLeft)

    # Keep the label/field columns tight against the left edge. Without a
    # trailing stretch column the grid split its width evenly across all
    # columns, leaving a large empty gap between every label and its field.
    cl.setColumnStretch(2, 1)
    layout.addWidget(app._stats_ctrl)

    # "Run test" on its own row beneath the form — previously it spanned all
    # five grid rows in a third column and rendered vertically centered (it
    # appeared to float beside the Channel row, disconnected from the form).
    run_row = QWidget(parent)
    run_row.setObjectName("TabCtrl")  # data-gated with the stats form above
    rrl = QHBoxLayout(run_row)
    rrl.setContentsMargins(12, 0, 12, 6)
    run_btn = btn_primary(run_row, "Run test", app._stats_run)
    rrl.addWidget(run_btn)
    rrl.addStretch(1)
    layout.addWidget(run_row)

    app._stats_sep = QFrame(parent)
    app._stats_sep.setObjectName("Separator")
    app._stats_sep.setFrameShape(QFrame.HLine)
    app._stats_sep.setFixedHeight(1)
    layout.addWidget(app._stats_sep)

    app._stats_fig_frame = QWidget(parent)
    ff_l = QVBoxLayout(app._stats_fig_frame)
    ff_l.setContentsMargins(0, 0, 0, 0)

    from matplotlib.figure import Figure as _StatsFigure
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as _StatsFCA

    app._stats_fig = _StatsFigure(figsize=(5, 2.8), dpi=96)
    app._stats_ax = app._stats_fig.add_subplot(111)
    app._stats_canvas_widget = _StatsFCA(app._stats_fig)
    ff_l.addWidget(app._stats_canvas_widget)
    layout.addWidget(app._stats_fig_frame)
    # Hide the figure frame until a test that actually plots (KS) is run.
    # run_stats toggles visibility per-test; before the first run the user
    # sees the controls + result text only, no empty canvas.
    app._stats_fig_frame.setVisible(False)

    app._stats_res_frame = QWidget(parent)
    rl = QVBoxLayout(app._stats_res_frame)
    rl.setContentsMargins(12, 6, 12, 6)
    app._stats_result_text = QTextEdit(app._stats_res_frame)
    app._stats_result_text.setReadOnly(True)
    app._stats_result_text.setLineWrapMode(QTextEdit.WidgetWidth)
    # Hint in the otherwise-blank results panel before the first run.
    app._stats_result_text.setPlaceholderText(
        "Select two comparison groups (left panel), choose a test, then click "
        "Run test to see results here."
    )
    rl.addWidget(app._stats_result_text)
    layout.addWidget(app._stats_res_frame, 1)

    stats_update_tp_menu(app)

    # Reshape the per-tab Property combo to match the currently-selected
    # stats channel (ratio channels collapse to ``Calculated Val``); then
    # sync the selection to the canonical ``_active_metric``.
    try:
        refresh_stats_property_combo(app)
        from well_viewer.data.metric_labels import METRIC_KEY_TO_LABEL
        label = METRIC_KEY_TO_LABEL.get(
            app._app_state.channel.active_metric, "Mean Intensity"
        )
        idx = app._stats_property_cb.findText(label)
        if idx >= 0:
            blocked = app._stats_property_cb.blockSignals(True)
            try:
                app._stats_property_cb.setCurrentIndex(idx)
            finally:
                app._stats_property_cb.blockSignals(blocked)
    except Exception:
        swallowed()  # was: pass (silent except)
