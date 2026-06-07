"""Image Table tab — sidebar plate-map picker (Qt port).

A multi-select well picker (a ``widgets.WellPlateSelector`` in "select" mode)
that explicitly ignores replicate sets / groups. State is held in
``app._image_table_active_wells`` (a ``set`` of well tokens).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget,
)
from well_viewer.features.image_table.controller import image_table_clear_active
from well_viewer.features.image_table.controller import image_table_on_channel_toggled
from well_viewer.features.image_table.controller import image_table_refresh_channel_picker
from well_viewer.features.image_table.controller import image_table_refresh_picker
from well_viewer.features.image_table.controller import image_table_select_all


def build_image_table_picker(app, parent: QWidget) -> None:
    """Compact plate-map picker for the Image Table tab sidebar."""
    from widgets.well_plate_selector import WellPlateSelector

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    plate = WellPlateSelector(parent)
    plate.setActionsVisible(False)          # the rail keeps its own All / Clear below
    plate.setSelectionMode("select")
    plate.setDragSelectEnabled(True)
    plate.setRowColumnSelectable(True)
    plate.setEnabledWells([])               # nothing selectable until a dataset loads
    # Match the main sidebar plate's geometry so the picker stays in the
    # same screen position when the user switches tabs.
    plate.setMinimumHeight(280)
    from PySide6.QtWidgets import QSizePolicy as _SizePolicy
    _sp = _SizePolicy(_SizePolicy.Preferred, _SizePolicy.Preferred)
    _sp.setHeightForWidth(True)
    plate.setSizePolicy(_sp)
    layout.addWidget(plate)
    app._sidebar_image_table_plate = plate

    def _on_plate_changed(ids) -> None:
        new = {w for w in ids if w in app._app_state.dataset.well_paths}
        if new == app._image_table_active_wells:
            return
        app._image_table_active_wells = new
        image_table_refresh_picker(app)
    plate.selectionChanged.connect(_on_plate_changed)

    btn_row = QWidget(parent)
    bl = QHBoxLayout(btn_row)
    bl.setContentsMargins(6, 2, 6, 2)
    bl.setSpacing(4)

    select_all = QPushButton("Select All", btn_row)
    select_all.setProperty("variant", "secondary")
    select_all.clicked.connect(lambda _=False: image_table_select_all(app))
    bl.addWidget(select_all)

    clear_btn = QPushButton("Clear", btn_row)
    clear_btn.setProperty("variant", "secondary")
    clear_btn.clicked.connect(lambda _=False: image_table_clear_active(app))
    bl.addWidget(clear_btn)
    bl.addStretch(1)
    layout.addWidget(btn_row)

    app._image_table_count_lbl = QLabel("0 wells active", parent)
    app._image_table_count_lbl.setObjectName("Muted")
    app._image_table_count_lbl.setAlignment(Qt.AlignLeft)
    layout.addWidget(app._image_table_count_lbl)

    # ── Channel multi-select ────────────────────────────────────────────────
    # Mirrors the plate picker but for channels: ticked channels feed the
    # "Channels" axis of the smart-layout builder. Populated on dataset load.
    chan_hdr = QLabel("Channels (for layout):", parent)
    chan_hdr.setObjectName("Muted")
    layout.addWidget(chan_hdr)

    chan_list = QListWidget(parent)
    chan_list.setMaximumHeight(120)
    chan_list.setToolTip(
        "Tick channels to place on the 'Channels' axis of the smart layout "
        "builder. None ticked = use all channels."
    )
    chan_list.itemChanged.connect(lambda _it: image_table_on_channel_toggled(app))
    layout.addWidget(chan_list)
    app._image_table_channel_list = chan_list

    layout.addStretch(1)

    help_lbl = QLabel(
        "Select wells (and tick channels) above, then use the Layout builder's "
        "Build / preset buttons to auto-size and fill the table.", parent,
    )
    help_lbl.setObjectName("Muted")
    help_lbl.setWordWrap(True)
    layout.addWidget(help_lbl)

    image_table_refresh_picker(app)
    image_table_refresh_channel_picker(app)
