"""Preview-tab UI helpers (Qt port).

A single-well plate-map picker (a ``widgets.WellPlateSelector`` in single-select
mode) — clicking a well loads its images; clicking the selected well clears it.
State is ``app._app_state.preview.selected_well`` (a token or ``None``).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
from well_viewer.core.lazy import lazy as _lazy
# Lazy so the preview picker imports without Image Browser present (keeps it deletable).
update_preview = _lazy("well_viewer.features.image_browser.review_image.renderer", "update_preview")


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
    app._sidebar_preview_plate = app._ctx.widgets.register("_sidebar_preview_plate", plate)

    def _on_plate_changed(ids) -> None:
        _preview = app._app_state.feature_slice("preview")
        if _preview is None:
            return
        tok = next((w for w in ids if w in app._app_state.dataset.well_paths), None)
        if tok == _preview.selected_well:
            return
        _preview.selected_well = tok
        app._refresh_preview_picker()
        update_preview(app, tok)
    plate.selectionChanged.connect(_on_plate_changed)

    app._preview_sel_lbl = app._ctx.widgets.register(
        "_preview_sel_lbl", QLabel("No well selected", parent))
    app._preview_sel_lbl.setObjectName("Muted")
    app._preview_sel_lbl.setAlignment(Qt.AlignLeft)
    layout.addWidget(app._preview_sel_lbl)

    layout.addStretch(1)

    help_lbl = QLabel("Click one well to load its images", parent)
    help_lbl.setObjectName("Muted")
    help_lbl.setWordWrap(True)
    layout.addWidget(help_lbl)

    app._refresh_preview_picker()


def refresh_preview_picker(ctx, **_kw) -> None:
    _preview = ctx.state.feature_slice("preview")
    if _preview is None:  # Image Browser disabled — no preview state
        return
    plate = ctx.widgets.get_or_none("_sidebar_preview_plate")
    sel = _preview.selected_well
    if plate is not None:
        well_paths = ctx.state.dataset.well_paths
        plate.setEnabledWells(list(well_paths.keys()))
        plate.setSelectedWellIds([sel] if sel and sel in well_paths else [])
    lbl = ctx.widgets.get_or_none("_preview_sel_lbl")
    if lbl is not None:
        lbl.setText(f"Selected: {sel}" if sel else "No well selected")
