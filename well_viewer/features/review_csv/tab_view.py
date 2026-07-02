"""Review CSV tab builder (Qt port)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QVBoxLayout, QWidget,
)
from well_viewer.core.lazy import lazy as _lazy
# Lazy so Review CSV imports without Image Browser present (keeps it deletable).
on_review_csv_row_double_click = _lazy("well_viewer.features.image_browser.review_image.controller", "on_review_csv_row_double_click")


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

    ctrl = QWidget(parent)
    ctrl.setObjectName("TabCtrl")
    cl = QHBoxLayout(ctrl)
    cl.setContentsMargins(0, 0, 0, 6)

    cl.addWidget(QLabel("Well:", ctrl))
    well_lbl = reg.register("_review_well_lbl", QLabel("(select one well)", ctrl))
    well_lbl.setObjectName("Muted")
    cl.addWidget(well_lbl)

    cl.addSpacing(8)
    cl.addWidget(QLabel("FOV:", ctrl))
    fov_cb = reg.register("_review_fov_cb", QComboBox(ctrl))
    fov_cb.setFixedWidth(120)
    fov_cb.currentIndexChanged.connect(
        lambda _i: ctx.app._refresh_review_csv_rows()
    )
    cl.addWidget(fov_cb)

    cl.addSpacing(8)
    cl.addWidget(QLabel("Timepoint:", ctrl))
    tp_cb = reg.register("_review_tp_cb", QComboBox(ctrl))
    tp_cb.setFixedWidth(140)
    tp_cb.currentIndexChanged.connect(
        lambda _i: ctx.app._refresh_review_csv_rows()
    )
    cl.addWidget(tp_cb)

    refresh_btn = QPushButton("Refresh", ctrl)
    refresh_btn.setProperty("variant", "secondary")
    refresh_btn.clicked.connect(lambda _=False: ctx.app._refresh_review_csv())
    cl.addWidget(refresh_btn)
    cl.addStretch(1)
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
        lambda item: on_review_csv_row_double_click(ctx.app, item)
    )
    layout.addWidget(table, 1)

    msg_lbl = reg.register("_review_csv_msg_lbl", QLabel(
        "Select a single well to inspect CSV rows.", parent,
    ))
    msg_lbl.setObjectName("Muted")
    layout.addWidget(msg_lbl)
