"""Image Table tab — sidebar plate-map picker (Qt port).

A multi-select well picker (a ``widgets.WellPlateSelector`` in "select" mode)
that explicitly ignores replicate sets / groups. State is held in
``app._app_state.image_table.active_wells`` (a ``set`` of well tokens).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from well_viewer.features.image_browser.image_table.controller import image_table_clear_active
from well_viewer.features.image_browser.image_table.controller import image_table_refresh_picker
from well_viewer.features.image_browser.image_table.controller import image_table_select_all


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
        if new == app._app_state.image_table.active_wells:
            return
        app._app_state.image_table.active_wells = new
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

    layout.addStretch(1)

    help_lbl = QLabel(
        "Select wells here, choose channels in the Build Layout row, then press "
        "Build table followed by Generate.", parent,
    )
    help_lbl.setObjectName("Muted")
    help_lbl.setWordWrap(True)
    layout.addWidget(help_lbl)

    image_table_refresh_picker(app)
