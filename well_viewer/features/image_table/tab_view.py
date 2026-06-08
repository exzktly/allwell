"""Image Table tab — centre-pane builder (Qt port).

Top-down layout:
- Header row: rows / cols spinners + Apply button
- Global Options row: channel / timepoint / FOV dropdowns (propagate to cells)
- Action row: Distribute Wells, Generate, Export
- Selector grid (rows × cols of per-cell dropdown groupboxes)
- Per-channel LUT row (one min/max LineEdit pair + Auto button per channel)
- Separator
- Rendered image-table area (built by the controller's Generate)
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton,
    QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from well_viewer.data.crop_tool import CropTool
from well_viewer.core.ui_helpers import btn_primary, btn_secondary
from well_viewer.features.image_table.controller import image_table_open_export_settings
from well_viewer.features.image_table.controller import image_table_apply_dimensions
from well_viewer.features.image_table.controller import image_table_apply_global
from well_viewer.features.image_table.controller import image_table_rebuild_grid
from well_viewer.features.image_table.controller import image_table_repopulate_dropdowns
from well_viewer.features.image_table.controller import image_table_set_binary_mode
from well_viewer.features.image_table.controller import image_table_refresh_channel_picker
from well_viewer.features.image_table.controller import image_table_set_zoom
from well_viewer.features.image_table.controller import image_table_add_varied_row
from well_viewer.features.image_table.controller import image_table_on_addrow_dim_changed
from well_viewer.features.image_table.controller import image_table_toggle_boundaries
from well_viewer.features.image_table.controller import image_table_toggle_tophat
from functools import partial
from well_viewer.features.image_table.controller import image_table_copy_png
from well_viewer.features.image_table.controller import image_table_copy_svg
from well_viewer.features.image_table.controller import image_table_distribute_timepoints
from well_viewer.features.image_table.controller import image_table_distribute_wells
from well_viewer.features.image_table.controller import image_table_export
from well_viewer.features.image_table.controller import image_table_generate
from well_viewer.features.image_table.controller import image_table_load_heatmap_layout
from well_viewer.features.image_table.controller import image_table_build_layout
from well_viewer.features.image_table.controller import image_table_apply_preset


def build_image_table_tab(ctx, parent: QWidget) -> None:
    """Construct the Image Table tab inside *parent*."""
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # State defaults — controller methods read these.
    if not hasattr(ctx.app, "_image_table_active_wells"):
        ctx.app._image_table_active_wells = set()
    if not hasattr(ctx.app, "_image_table_rows"):
        ctx.app._image_table_rows = 2
    if not hasattr(ctx.app, "_image_table_cols"):
        ctx.app._image_table_cols = 3
    if not hasattr(ctx.app, "_image_table_cells"):
        ctx.app._image_table_cells = []
    if not hasattr(ctx.app, "_image_table_lut"):
        ctx.app._image_table_lut = {}
    if not hasattr(ctx.app, "_image_table_last_render"):
        ctx.app._image_table_last_render = {}
    if not hasattr(ctx.app, "_image_table_image_cache"):
        ctx.app._image_table_image_cache = {}
    if not hasattr(ctx.app, "_image_table_use_tophat"):
        ctx.app._image_table_use_tophat = False
    if not hasattr(ctx.app, "_image_table_show_boundaries"):
        ctx.app._image_table_show_boundaries = False
    if not hasattr(ctx.app, "_image_table_binary_mode"):
        ctx.app._image_table_binary_mode = ""   # "", "mask", or "threshold"
    if not hasattr(ctx.app, "_image_table_zoom"):
        ctx.app._image_table_zoom = 1.0
    if not hasattr(ctx.app, "_image_table_active_channels"):
        ctx.app._image_table_active_channels = set()
    if not hasattr(ctx.app, "_image_table_row_axis"):
        ctx.app._image_table_row_axis = "channel"
    if not hasattr(ctx.app, "_image_table_col_axis"):
        ctx.app._image_table_col_axis = "timepoint"
    # Shared square-region crop helper. on_change re-runs Generate so the
    # rendered grid follows mode toggles and crop changes automatically.
    if not hasattr(ctx.app, "_image_table_crop_tool"):
        ctx.app._image_table_crop_tool = CropTool(
            on_change=lambda: image_table_generate(ctx.app),
        )

    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    inner = QWidget()
    il = QVBoxLayout(inner)
    il.setContentsMargins(8, 8, 8, 8)
    il.setSpacing(6)
    scroll.setWidget(inner)
    layout.addWidget(scroll, 1)

    # ── Header: rows / cols spinners ────────────────────────────────────────
    hdr = QWidget(inner)
    hdr.setObjectName("TabCtrl")
    hl = QHBoxLayout(hdr)
    hl.setContentsMargins(8, 6, 8, 6)
    hl.setSpacing(6)

    hl.addWidget(QLabel("Rows:", hdr))
    rows_spin = QSpinBox(hdr)
    rows_spin.setRange(1, 12)
    rows_spin.setValue(int(ctx.app._image_table_rows))
    rows_spin.setFixedWidth(64)
    rows_spin.valueChanged.connect(lambda _v: image_table_apply_dimensions(ctx.app))
    hl.addWidget(rows_spin)
    ctx.app._image_table_rows_spin = rows_spin

    hl.addWidget(QLabel("Cols:", hdr))
    cols_spin = QSpinBox(hdr)
    cols_spin.setRange(1, 12)
    cols_spin.setValue(int(ctx.app._image_table_cols))
    cols_spin.setFixedWidth(64)
    cols_spin.valueChanged.connect(lambda _v: image_table_apply_dimensions(ctx.app))
    hl.addWidget(cols_spin)
    ctx.app._image_table_cols_spin = cols_spin
    hl.addStretch(1)
    il.addWidget(hdr)

    # ── Global Options row (Channel / Timepoint / FOV) ──────────────────────
    glob = QWidget(inner)
    glob.setObjectName("TabCtrl")
    gl = QHBoxLayout(glob)
    gl.setContentsMargins(8, 6, 8, 6)
    gl.setSpacing(6)
    gl.addWidget(QLabel("Global Options —", glob))

    gl.addWidget(QLabel("Channel:", glob))
    chan_cb = QComboBox(glob)
    chan_cb.currentIndexChanged.connect(
        lambda _i: image_table_apply_global(ctx.app, "chan")
    )
    gl.addWidget(chan_cb)
    ctx.app._image_table_global_chan_cb = chan_cb

    gl.addWidget(QLabel("Timepoint:", glob))
    tp_cb = QComboBox(glob)
    tp_cb.currentIndexChanged.connect(
        lambda _i: image_table_apply_global(ctx.app, "tp")
    )
    gl.addWidget(tp_cb)
    ctx.app._image_table_global_tp_cb = tp_cb

    gl.addWidget(QLabel("FOV:", glob))
    fov_cb = QComboBox(glob)
    fov_cb.currentIndexChanged.connect(
        lambda _i: image_table_apply_global(ctx.app, "fov")
    )
    gl.addWidget(fov_cb)
    ctx.app._image_table_global_fov_cb = fov_cb
    gl.addStretch(1)
    il.addWidget(glob)

    # ── Build Layout panel ──────────────────────────────────────────────────
    # One coherent, framed panel for assembling the selector grid:
    #   • Row 1: declare the row/column axes + which channels + Build.
    #   • Row 2: one-click presets for the common pairings.
    #   • Row 3: manual fill helpers (advanced).
    # Build only assembles the selector grid; the user presses Generate to
    # render the images.
    builder = QFrame(inner)
    builder.setObjectName("ImageTableSelector")
    builder.setFrameShape(QFrame.StyledPanel)
    bv = QVBoxLayout(builder)
    bv.setContentsMargins(8, 6, 8, 8)
    bv.setSpacing(6)

    build_title = QLabel("Build Layout", builder)
    build_title.setProperty("role", "section")
    bv.addWidget(build_title)

    _AXES = [("Channels", "channel"), ("Wells", "well"),
             ("Timepoints", "timepoint"), ("FOVs", "fov")]

    def _axis_combo(default_key: str) -> QComboBox:
        cb = QComboBox(builder)
        for label, key in _AXES:
            cb.addItem(label, key)
        idx = cb.findData(default_key)
        cb.setCurrentIndex(idx if idx >= 0 else 0)
        return cb

    # Row 1 — axes + channels + Build.
    axes_row = QWidget(builder)
    ar = QHBoxLayout(axes_row)
    ar.setContentsMargins(0, 0, 0, 0)
    ar.setSpacing(6)

    ar.addWidget(QLabel("Rows:", axes_row))
    row_axis_cb = _axis_combo(ctx.app._image_table_row_axis)
    ar.addWidget(row_axis_cb)
    ctx.app._image_table_row_axis_cb = row_axis_cb

    ar.addWidget(QLabel("×  Columns:", axes_row))
    col_axis_cb = _axis_combo(ctx.app._image_table_col_axis)
    ar.addWidget(col_axis_cb)
    ctx.app._image_table_col_axis_cb = col_axis_cb

    # Channel multi-select dropdown (drives the "Channels" axis; default all).
    chan_btn = QToolButton(axes_row)
    chan_btn.setText("Channels: all")
    chan_btn.setPopupMode(QToolButton.InstantPopup)
    chan_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
    chan_btn.setToolTip(
        "Pick which channels go on the Channels axis (none = all channels)."
    )
    chan_menu = QMenu(chan_btn)
    chan_btn.setMenu(chan_menu)
    ctx.app._image_table_channel_btn = chan_btn
    ctx.app._image_table_channel_menu = chan_menu
    ar.addWidget(chan_btn)

    ar.addSpacing(8)
    ar.addWidget(btn_primary(
        axes_row, "Build table", partial(image_table_build_layout, ctx.app),
    ))
    ar.addStretch(1)
    bv.addWidget(axes_row)

    # Row 2 — quick presets.
    preset_row = QWidget(builder)
    pr = QHBoxLayout(preset_row)
    pr.setContentsMargins(0, 0, 0, 0)
    pr.setSpacing(6)
    pr.addWidget(QLabel("Presets:", preset_row))
    for label, (ra_, ca_) in (
        ("Channels × Time", ("channel", "timepoint")),
        ("Channels × Wells", ("channel", "well")),
        ("Wells × Time", ("well", "timepoint")),
        ("Wells × Channels", ("well", "channel")),
    ):
        pr.addWidget(btn_secondary(
            preset_row, label, partial(image_table_apply_preset, ctx.app, ra_, ca_),
        ))
    pr.addStretch(1)
    bv.addWidget(preset_row)

    # Row 3 — manual fill helpers (advanced).
    manual_row = QWidget(builder)
    mr = QHBoxLayout(manual_row)
    mr.setContentsMargins(0, 0, 0, 0)
    mr.setSpacing(6)
    mr.addWidget(QLabel("Manual fill:", manual_row))
    mr.addWidget(btn_secondary(
        manual_row, "Distribute Wells", partial(image_table_distribute_wells, ctx.app),
    ))
    mr.addWidget(btn_secondary(
        manual_row, "Distribute Timepoints", partial(image_table_distribute_timepoints, ctx.app),
    ))
    mr.addWidget(btn_secondary(
        manual_row, "Load Heatmap Layout", partial(image_table_load_heatmap_layout, ctx.app),
    ))
    mr.addStretch(1)
    bv.addWidget(manual_row)

    # Row 4 — clone a row, varying one dimension (propagate-but-mutate).
    addrow_row = QWidget(builder)
    arr2 = QHBoxLayout(addrow_row)
    arr2.setContentsMargins(0, 0, 0, 0)
    arr2.setSpacing(6)
    arr2.addWidget(QLabel("Add row like", addrow_row))

    addrow_src_cb = QComboBox(addrow_row)
    addrow_src_cb.setToolTip("Existing row to clone.")
    arr2.addWidget(addrow_src_cb)
    ctx.app._image_table_addrow_src_cb = addrow_src_cb

    arr2.addWidget(QLabel("varying", addrow_row))
    addrow_dim_cb = QComboBox(addrow_row)
    for label, key in (("Channel", "channel"), ("Timepoint", "timepoint"),
                       ("FOV", "fov"), ("Well", "well")):
        addrow_dim_cb.addItem(label, key)
    arr2.addWidget(addrow_dim_cb)
    ctx.app._image_table_addrow_dim_cb = addrow_dim_cb

    addrow_mode_cb = QComboBox(addrow_row)
    addrow_mode_cb.addItem("Uniform →", "uniform")
    addrow_mode_cb.addItem("Step +1", "step")
    addrow_mode_cb.setToolTip(
        "Uniform: set the chosen dimension to one value across the new row.\n"
        "Step +1: advance each cell's value for that dimension by one."
    )
    arr2.addWidget(addrow_mode_cb)
    ctx.app._image_table_addrow_mode_cb = addrow_mode_cb

    addrow_val_cb = QComboBox(addrow_row)
    addrow_val_cb.setToolTip("New value (Uniform mode only).")
    arr2.addWidget(addrow_val_cb)
    ctx.app._image_table_addrow_val_cb = addrow_val_cb

    addrow_dim_cb.currentIndexChanged.connect(
        lambda _i: image_table_on_addrow_dim_changed(ctx.app)
    )
    addrow_mode_cb.currentIndexChanged.connect(
        lambda _i: image_table_on_addrow_dim_changed(ctx.app)
    )

    arr2.addWidget(btn_secondary(
        addrow_row, "Add row", partial(image_table_add_varied_row, ctx.app),
    ))
    arr2.addStretch(1)
    bv.addWidget(addrow_row)

    il.addWidget(builder)

    sep1 = QFrame(inner)
    sep1.setObjectName("Separator")
    sep1.setFrameShape(QFrame.HLine)
    sep1.setFixedHeight(2)
    il.addWidget(sep1)
    il.addSpacing(6)

    # ── Selector grid (rows × cols of per-cell dropdowns) ───────────────────
    # Wrap selector area in a styled frame so it is visually distinct from
    # the global-options strip above and the LUT row below.
    selector_box = QFrame(inner)
    selector_box.setObjectName("ImageTableSelector")
    selector_box.setFrameShape(QFrame.StyledPanel)
    sb_layout = QVBoxLayout(selector_box)
    sb_layout.setContentsMargins(8, 6, 8, 8)
    sb_layout.setSpacing(4)

    selector_lbl = QLabel("Selector", selector_box)
    selector_lbl.setProperty("role", "section")
    sb_layout.addWidget(selector_lbl)

    selector_host = QWidget(selector_box)
    selector_grid = QGridLayout(selector_host)
    selector_grid.setContentsMargins(0, 0, 0, 0)
    selector_grid.setSpacing(4)
    ctx.app._image_table_selector_grid = selector_grid
    sb_layout.addWidget(selector_host)
    il.addWidget(selector_box)
    il.addSpacing(6)

    # ── LUT row (rebuilt per channel by the controller) ─────────────────────
    # Wrap the row in a tinted frame so it visually reads as a global
    # control strip, not as another row of per-cell selectors.
    lut_outer = QFrame(inner)
    lut_outer.setObjectName("ImageTableLutRow")
    lut_outer.setStyleSheet(
        "QFrame#ImageTableLutRow { "
        "background-color: rgba(245, 158, 11, 0.12); "
        "border: 1px solid rgba(245, 158, 11, 0.40); "
        "border-radius: 4px; "
        "}"
    )
    lut_outer_layout = QVBoxLayout(lut_outer)
    lut_outer_layout.setContentsMargins(8, 6, 8, 6)
    lut_outer_layout.setSpacing(4)

    lut_lbl = QLabel("LUT (per channel) — applies to every cell of that channel", lut_outer)
    lut_lbl.setProperty("role", "section")
    lut_outer_layout.addWidget(lut_lbl)

    lut_container = QWidget(lut_outer)
    lut_layout = QHBoxLayout(lut_container)
    lut_layout.setContentsMargins(0, 0, 0, 0)
    lut_layout.setSpacing(6)
    ctx.app._image_table_lut_container = lut_container
    lut_outer_layout.addWidget(lut_container)
    il.addWidget(lut_outer)

    sep2 = QFrame(inner)
    sep2.setObjectName("Separator")
    sep2.setFrameShape(QFrame.HLine)
    sep2.setFixedHeight(1)
    il.addWidget(sep2)

    # ── Tools row — image-manipulation toggles on the left, output
    #    actions (Copy / Export) on the right. Generate gets its own row
    #    below so the primary action stands out.
    tools_row = QWidget(inner)
    tr = QHBoxLayout(tools_row)
    tr.setContentsMargins(0, 0, 0, 0)
    tr.setSpacing(6)

    def _wire_toggle_check(btn: QPushButton, base_label: str) -> None:
        """Prefix the button label with a ✓ glyph when checked.

        The toggle-variant QSS already changes background color on
        ``:checked``; adding the glyph makes the selected state explicit
        even at a glance and survives across themes.
        """
        def _refresh(checked: bool) -> None:
            btn.setText(f"✓ {base_label}" if checked else base_label)
        btn.toggled.connect(_refresh)
        _refresh(btn.isChecked())

    tophat_btn = QPushButton("Tophat", tools_row)
    tophat_btn.setProperty("variant", "toggle")
    tophat_btn.setCheckable(True)
    tophat_btn.setChecked(bool(ctx.app._image_table_use_tophat))
    tophat_btn.setToolTip(
        "Toggle between raw fluorescence and pre-filtered tophat images."
    )
    tophat_btn.clicked.connect(lambda _=False: image_table_toggle_tophat(ctx.app))
    _wire_toggle_check(tophat_btn, "Tophat")
    ctx.app._image_table_tophat_btn = tophat_btn
    tr.addWidget(tophat_btn)

    boundaries_btn = QPushButton("Boundaries", tools_row)
    boundaries_btn.setProperty("variant", "toggle")
    boundaries_btn.setCheckable(True)
    boundaries_btn.setChecked(bool(ctx.app._image_table_show_boundaries))
    boundaries_btn.setToolTip(
        "Overlay cell boundary outlines (white) on each fluorescence image."
    )
    boundaries_btn.clicked.connect(lambda _=False: image_table_toggle_boundaries(ctx.app))
    _wire_toggle_check(boundaries_btn, "Boundaries")
    ctx.app._image_table_boundaries_btn = boundaries_btn
    tr.addWidget(boundaries_btn)

    _mode = ctx.app._image_table_binary_mode

    binary_mask_btn = QPushButton("Binary (mask)", tools_row)
    binary_mask_btn.setProperty("variant", "toggle")
    binary_mask_btn.setCheckable(True)
    binary_mask_btn.setChecked(_mode == "mask")
    binary_mask_btn.setToolTip(
        "Show the segmentation mask as binary: every labeled cell white, "
        "background black."
    )
    binary_mask_btn.clicked.connect(
        lambda _=False: image_table_set_binary_mode(ctx.app, "mask")
    )
    _wire_toggle_check(binary_mask_btn, "Binary (mask)")
    ctx.app._image_table_binary_mask_btn = binary_mask_btn
    tr.addWidget(binary_mask_btn)

    binary_thresh_btn = QPushButton("Binary (≥ ON threshold)", tools_row)
    binary_thresh_btn.setProperty("variant", "toggle")
    binary_thresh_btn.setCheckable(True)
    binary_thresh_btn.setChecked(_mode == "threshold")
    binary_thresh_btn.setToolTip(
        "Show only cells above the active ON threshold as binary white "
        "(same gating as the bar plot / Review Image tab)."
    )
    binary_thresh_btn.clicked.connect(
        lambda _=False: image_table_set_binary_mode(ctx.app, "threshold")
    )
    _wire_toggle_check(binary_thresh_btn, "Binary (≥ ON threshold)")
    ctx.app._image_table_binary_thresh_btn = binary_thresh_btn
    tr.addWidget(binary_thresh_btn)

    crop_sep = QFrame(tools_row)
    crop_sep.setFrameShape(QFrame.VLine)
    crop_sep.setFixedWidth(1)
    tr.addWidget(crop_sep)
    tr.addWidget(ctx.app._image_table_crop_tool.make_button(tools_row))
    tr.addWidget(ctx.app._image_table_crop_tool.make_reset_button(tools_row))
    tr.addWidget(ctx.app._image_table_crop_tool.make_status_label(tools_row))

    tr.addStretch(1)

    export_settings_btn = QPushButton("⚙", tools_row)
    export_settings_btn.setProperty("variant", "secondary")
    export_settings_btn.setToolTip(
        "Export settings — outer margin, cell gap, titles, DPI, transparent BG"
    )
    export_settings_btn.setFixedWidth(32)
    export_settings_btn.clicked.connect(
        lambda _=False: image_table_open_export_settings(ctx.app)
    )
    tr.addWidget(export_settings_btn)
    tr.addWidget(btn_secondary(
        tools_row, "Copy PNG", partial(image_table_copy_png, ctx.app), icon="copy",
    ))
    tr.addWidget(btn_secondary(
        tools_row, "Copy SVG", partial(image_table_copy_svg, ctx.app), icon="copy",
    ))
    tr.addWidget(btn_secondary(
        tools_row, "Export", partial(image_table_export, ctx.app), icon="download",
    ))
    il.addWidget(tools_row)

    # ── Generate row (its own row so the primary action is unambiguous) ────
    generate_row = QWidget(inner)
    gr = QHBoxLayout(generate_row)
    gr.setContentsMargins(0, 0, 0, 0)
    gr.setSpacing(6)
    gr.addWidget(btn_primary(generate_row, "Generate", partial(image_table_generate, ctx.app)))
    gr.addStretch(1)

    # Zoom controls for the rendered table (− / level / + / reset).
    gr.addWidget(QLabel("Zoom:", generate_row))
    zoom_out = QPushButton("−", generate_row)
    zoom_out.setProperty("variant", "secondary")
    zoom_out.setFixedWidth(32)
    zoom_out.setToolTip("Zoom out")
    zoom_out.clicked.connect(lambda _=False: image_table_set_zoom(ctx.app, 1 / 1.25, relative=True))
    gr.addWidget(zoom_out)

    zoom_lbl = QLabel(f"{ctx.app._image_table_zoom * 100:.0f}%", generate_row)
    zoom_lbl.setAlignment(Qt.AlignCenter)
    zoom_lbl.setFixedWidth(48)
    ctx.app._image_table_zoom_lbl = zoom_lbl
    gr.addWidget(zoom_lbl)

    zoom_in = QPushButton("+", generate_row)
    zoom_in.setProperty("variant", "secondary")
    zoom_in.setFixedWidth(32)
    zoom_in.setToolTip("Zoom in")
    zoom_in.clicked.connect(lambda _=False: image_table_set_zoom(ctx.app, 1.25, relative=True))
    gr.addWidget(zoom_in)

    zoom_reset = QPushButton("Reset", generate_row)
    zoom_reset.setProperty("variant", "secondary")
    zoom_reset.setToolTip("Reset zoom to 100%")
    zoom_reset.clicked.connect(lambda _=False: image_table_set_zoom(ctx.app, 1.0))
    gr.addWidget(zoom_reset)

    il.addWidget(generate_row)

    # ── Rendered image table (drawn BELOW the action strip) ────────────────
    render_lbl = QLabel("Image Table:", inner)
    render_lbl.setProperty("role", "section")
    il.addWidget(render_lbl)

    # EmptyState placeholder shown until the controller populates the grid.
    # ``image_table_generate`` hides this and reveals the render host once the
    # first table is produced.
    from widgets.empty_state import EmptyState as _EmptyState
    ctx.app._image_table_empty_state = _EmptyState(
        "No image table generated",
        icon="image",
        parent=inner,
        hint="Choose wells/channels above, then press Generate.",
    )
    # Give the placeholder enough vertical room so the wrapped hint never
    # gets clipped by tight sibling rows.
    ctx.app._image_table_empty_state.setMinimumHeight(180)
    il.addWidget(ctx.app._image_table_empty_state)

    render_host = QWidget(inner)
    render_grid = QGridLayout(render_host)
    render_grid.setContentsMargins(0, 0, 0, 0)
    render_grid.setSpacing(2)
    # Pin cells to the top-left; otherwise QGridLayout distributes excess
    # space across rows/columns and the per-cell pixmaps render with large
    # gaps between them.
    render_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    ctx.app._image_table_render_grid = render_grid
    il.addWidget(render_host)

    il.addStretch(1)

    # Build the initial grid + LUT row using current pipeline_info.
    image_table_repopulate_dropdowns(ctx.app)
    image_table_rebuild_grid(ctx.app)
