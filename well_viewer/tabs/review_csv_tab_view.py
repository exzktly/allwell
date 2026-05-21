"""Review CSV tab builder (Qt port)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QVBoxLayout, QWidget,
)


def build_review_csv_tab(ctx, parent: QWidget) -> None:
    app = ctx.app  # transitional DI seam
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(10, 10, 10, 10)

    ctrl = QWidget(parent)
    ctrl.setObjectName("TabCtrl")
    cl = QHBoxLayout(ctrl)
    cl.setContentsMargins(8, 6, 8, 6)

    cl.addWidget(QLabel("Well:", ctrl))
    app._review_well_lbl = QLabel("(select one well)", ctrl)
    app._review_well_lbl.setObjectName("Muted")
    cl.addWidget(app._review_well_lbl)

    cl.addSpacing(8)
    cl.addWidget(QLabel("FOV:", ctrl))
    app._review_fov_cb = QComboBox(ctrl)
    app._review_fov_cb.setFixedWidth(120)
    app._review_fov_cb.currentIndexChanged.connect(
        lambda _i: app._refresh_review_csv_rows()
    )
    cl.addWidget(app._review_fov_cb)

    cl.addSpacing(8)
    cl.addWidget(QLabel("Timepoint:", ctrl))
    app._review_tp_cb = QComboBox(ctrl)
    app._review_tp_cb.setFixedWidth(140)
    app._review_tp_cb.currentIndexChanged.connect(
        lambda _i: app._refresh_review_csv_rows()
    )
    cl.addWidget(app._review_tp_cb)

    refresh_btn = QPushButton("Refresh", ctrl)
    refresh_btn.setProperty("variant", "secondary")
    refresh_btn.clicked.connect(lambda _=False: app._refresh_review_csv())
    cl.addWidget(refresh_btn)
    cl.addStretch(1)
    layout.addWidget(ctrl)

    app._review_csv_table = QTableWidget(0, 0, parent)
    app._review_csv_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    app._review_csv_table.horizontalHeader().setHighlightSections(False)
    app._review_csv_table.verticalHeader().setVisible(False)
    app._review_csv_table.setAlternatingRowColors(True)
    app._review_csv_table.setShowGrid(False)
    app._review_csv_table.setSelectionBehavior(QTableWidget.SelectRows)
    app._review_csv_table.setEditTriggers(QTableWidget.NoEditTriggers)
    app._review_csv_table.itemDoubleClicked.connect(
        lambda item: app._on_review_csv_row_double_click(item)
    )
    layout.addWidget(app._review_csv_table, 1)

    app._review_csv_msg_lbl = QLabel(
        "Select a single well to inspect CSV rows.", parent,
    )
    app._review_csv_msg_lbl.setObjectName("Muted")
    layout.addWidget(app._review_csv_msg_lbl)
