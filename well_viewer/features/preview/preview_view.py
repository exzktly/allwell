"""Preview-tab UI helpers (Qt port).

A single-well plate-map picker (a ``widgets.WellPlateSelector`` in single-select
mode) — clicking a well loads its images; clicking the selected well clears it.
State is ``app._app_state.preview.selected_well`` (a token or ``None``).
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
from well_viewer.core import debug_flags as _debug_flags
from well_viewer.data.data_loading import extract_well_token as _extract_well_token
from well_viewer.features.image_browser.review_image.renderer import stage_review_image

_logger = logging.getLogger("well_viewer")


def build_preview_picker(app, parent: QWidget) -> None:
    """Compact single-well plate-map preview picker in the sidebar."""
    from widgets.well_plate_selector import WellPlateSelector

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    plate = WellPlateSelector(parent)
    plate.setActionsVisible(False)
    plate.setSelectionMode("select")
    plate.setSingleSelectionMode(True)
    plate.setRowColumnSelectable(False)
    plate.setEnabledWells([])
    # Match the main sidebar plate's geometry so the picker stays in the
    # same screen position when the user switches tabs.
    plate.setMinimumHeight(280)
    from PySide6.QtWidgets import QSizePolicy as _SizePolicy
    _sp = _SizePolicy(_SizePolicy.Preferred, _SizePolicy.Preferred)
    _sp.setHeightForWidth(True)
    plate.setSizePolicy(_sp)
    layout.addWidget(plate)
    app._sidebar_preview_plate = plate

    def _on_plate_changed(ids) -> None:
        tok = next((w for w in ids if w in app._app_state.dataset.well_paths), None)
        if _debug_flags.review_image_load_debug_enabled():
            _logger.debug(
                "preview picker click: ids=%r -> tok=%r (prev selected=%r)",
                list(ids), tok, app._app_state.preview.selected_well,
            )
        if tok == app._app_state.preview.selected_well:
            return
        app._app_state.preview.selected_well = tok
        app._refresh_preview_picker()
        # Deferred control model: stage the well change (and reflect it in the
        # top-bar label) but don't reload/redraw — the image renders on the
        # next Generate, which reloads this well's refs for the active channel.
        if hasattr(app, "_review_image_well_lbl"):
            app._review_image_well_lbl.setText(
                (_extract_well_token(tok) or tok) if tok else "No well selected")
        stage_review_image(app)
    plate.selectionChanged.connect(_on_plate_changed)

    app._preview_sel_lbl = QLabel("No well selected", parent)
    app._preview_sel_lbl.setObjectName("Muted")
    app._preview_sel_lbl.setAlignment(Qt.AlignLeft)
    layout.addWidget(app._preview_sel_lbl)

    layout.addStretch(1)

    help_lbl = QLabel("Click one well to load its images", parent)
    help_lbl.setObjectName("Muted")
    help_lbl.setWordWrap(True)
    layout.addWidget(help_lbl)

    app._refresh_preview_picker()


def refresh_preview_picker(app, **_kw) -> None:
    plate = getattr(app, "_sidebar_preview_plate", None)
    sel = app._app_state.preview.selected_well
    if plate is not None:
        plate.setEnabledWells(list(app._app_state.dataset.well_paths.keys()))
        plate.setSelectedWellIds([sel] if sel and sel in app._app_state.dataset.well_paths else [])
    if hasattr(app, "_preview_sel_lbl"):
        app._preview_sel_lbl.setText(f"Selected: {sel}" if sel else "No well selected")
