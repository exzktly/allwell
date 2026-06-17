"""Review CSV tab builder (Qt port)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QVBoxLayout, QWidget,
)
from well_viewer.features.review_image.controller import on_review_csv_row_double_click


def build_review_csv_tab(ctx, parent: QWidget) -> None:
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
    ctx.app._review_well_lbl = QLabel("(select one well)", ctrl)
    ctx.app._review_well_lbl.setObjectName("Muted")
    cl.addWidget(ctx.app._review_well_lbl)

    cl.addSpacing(8)
    cl.addWidget(QLabel("FOV:", ctrl))
    ctx.app._review_fov_cb = QComboBox(ctrl)
    ctx.app._review_fov_cb.setFixedWidth(120)
    ctx.app._review_fov_cb.currentIndexChanged.connect(
        lambda _i: ctx.app._refresh_review_csv_rows()
    )
    cl.addWidget(ctx.app._review_fov_cb)

    cl.addSpacing(8)
    cl.addWidget(QLabel("Timepoint:", ctrl))
    ctx.app._review_tp_cb = QComboBox(ctrl)
    ctx.app._review_tp_cb.setFixedWidth(140)
    ctx.app._review_tp_cb.currentIndexChanged.connect(
        lambda _i: ctx.app._refresh_review_csv_rows()
    )
    cl.addWidget(ctx.app._review_tp_cb)

    refresh_btn = QPushButton("Refresh", ctrl)
    refresh_btn.setProperty("variant", "secondary")
    refresh_btn.clicked.connect(lambda _=False: ctx.app._refresh_review_csv())
    cl.addWidget(refresh_btn)
    cl.addStretch(1)
    layout.addWidget(ctrl)

    ctx.app._review_csv_table = QTableWidget(0, 0, parent)
    ctx.app._review_csv_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    ctx.app._review_csv_table.horizontalHeader().setHighlightSections(False)
    # Left-align header labels so text columns (well, channel) read in line with
    # their left-read values instead of looking right-/spread-aligned.
    ctx.app._review_csv_table.horizontalHeader().setDefaultAlignment(
        Qt.AlignLeft | Qt.AlignVCenter)
    ctx.app._review_csv_table.verticalHeader().setVisible(False)
    ctx.app._review_csv_table.setAlternatingRowColors(True)
    ctx.app._review_csv_table.setShowGrid(False)
    ctx.app._review_csv_table.setSelectionBehavior(QTableWidget.SelectRows)
    ctx.app._review_csv_table.setEditTriggers(QTableWidget.NoEditTriggers)
    ctx.app._review_csv_table.itemDoubleClicked.connect(
        lambda item: on_review_csv_row_double_click(ctx.app, item)
    )
    layout.addWidget(ctx.app._review_csv_table, 1)

    ctx.app._review_csv_msg_lbl = QLabel(
        "Select a single well to inspect CSV rows.", parent,
    )
    ctx.app._review_csv_msg_lbl.setObjectName("Muted")
    layout.addWidget(ctx.app._review_csv_msg_lbl)
