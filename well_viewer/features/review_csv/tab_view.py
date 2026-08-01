"""Review CSV tab builder (Qt port)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHeaderView, QLabel, QPushButton, QTableWidget,
    QVBoxLayout, QWidget,
)
from well_viewer.core.lazy import lazy as _lazy
# Lazy so Review CSV imports without Image Browser present (keeps it deletable).
_RCSV = "well_viewer.features.image_browser.review_image.controller"
on_review_csv_row_double_click = _lazy(_RCSV, "on_review_csv_row_double_click")
refresh_review_csv = _lazy(_RCSV, "refresh_review_csv")
refresh_review_csv_rows = _lazy(_RCSV, "refresh_review_csv_rows")


from widgets.control_band import ControlBand
from widgets.labeled_combo import LabeledComboBox

def build_review_csv_tab(ctx, parent: QWidget) -> None:
    # A8 burn-down: widget handles go through the WidgetRegistry (``ctx.widgets``)
    # instead of being stashed on the app god-object. ``register`` also stamps
    # ``app._<name>`` via setattr, so the legacy ``app._review_*`` readers in
    # review_image/controller.py keep resolving unchanged. Only the residual
    # behaviour calls (refresh / double-click) still reach the app — a separate
    # later layer.
    reg = ctx.widgets

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(12, 12, 12, 12)

    ctrl = ControlBand(parent)

    well_lbl = reg.register("_review_well_lbl", QLabel("(select one well)"))
    well_lbl.setObjectName("Muted")
    ctrl.add_scope("Well", well_lbl)

    fov_cb = reg.register("_review_fov_cb", LabeledComboBox("FOV"))
    # No setFixedWidth — LabeledComboBox already sizes to exactly the painted
    # "FOV: …" text.
    fov_cb.currentIndexChanged.connect(
        lambda _i: refresh_review_csv_rows(ctx)
    )
    ctrl.add_scope("", fov_cb)

    tp_cb = reg.register("_review_tp_cb", LabeledComboBox("Timepoint"))
    # No setFixedWidth — LabeledComboBox already sizes to exactly the painted
    # "Timepoint: …" text.
    tp_cb.currentIndexChanged.connect(
        lambda _i: refresh_review_csv_rows(ctx)
    )
    ctrl.add_scope("", tp_cb)

    refresh_btn = QPushButton("Refresh", ctrl)
    refresh_btn.setProperty("variant", "secondary")
    refresh_btn.clicked.connect(lambda _=False: refresh_review_csv(ctx))
    ctrl.add_action(refresh_btn)
    layout.addWidget(ctrl)

    table = reg.register("_review_csv_table", QTableWidget(0, 0, parent))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    table.horizontalHeader().setHighlightSections(False)
    # Left-align header labels so text columns (well, channel) read in line with
    # their left-read values instead of looking right-/spread-aligned.
    table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.itemDoubleClicked.connect(
        lambda item: on_review_csv_row_double_click(ctx, item)
    )
    from well_viewer.core.ui_helpers import wrap_with_empty_state
    layout.addWidget(wrap_with_empty_state(
        ctx.window, table, icon="file-spreadsheet",
    ), 1)

    msg_lbl = reg.register("_review_csv_msg_lbl", QLabel(
        "Select a single well to inspect CSV rows.", parent,
    ))
    msg_lbl.setObjectName("Muted")
    layout.addWidget(msg_lbl)

    # Stamp a live refresh callback on the review runtime scratch so cross-feature
    # callers (e.g. smFISH's Apply-Global-Threshold writeback) can rebuild this
    # table without importing into image_browser (the deletability guardrail bans
    # a cross-feature deferred import). Same bridge mechanism E4-review used for
    # the other review-csv leaves; ``None`` until this tab is built, so a caller
    # that fires before a first visit simply no-ops (nothing on screen to refresh).
    ctx.runtime.review.refresh_csv = lambda: refresh_review_csv(ctx)
