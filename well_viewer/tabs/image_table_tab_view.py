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
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from well_viewer.crop_tool import CropTool
from well_viewer.ui_helpers import btn_primary, btn_secondary
from well_viewer.image_table_controller import image_table_open_export_settings


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
    if not hasattr(ctx.app, "_image_table_show_binary"):
        ctx.app._image_table_show_binary = False
    # Shared square-region crop helper. on_change re-runs Generate so the
    # rendered grid follows mode toggles and crop changes automatically.
    if not hasattr(ctx.app, "_image_table_crop_tool"):
        ctx.app._image_table_crop_tool = CropTool(
            on_change=lambda: ctx.app._image_table_generate(),
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
    rows_spin.valueChanged.connect(lambda _v: ctx.app._image_table_apply_dimensions())
    hl.addWidget(rows_spin)
    ctx.app._image_table_rows_spin = rows_spin

    hl.addWidget(QLabel("Cols:", hdr))
    cols_spin = QSpinBox(hdr)
    cols_spin.setRange(1, 12)
    cols_spin.setValue(int(ctx.app._image_table_cols))
    cols_spin.setFixedWidth(64)
    cols_spin.valueChanged.connect(lambda _v: ctx.app._image_table_apply_dimensions())
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
        lambda _i: ctx.app._image_table_apply_global("chan")
    )
    gl.addWidget(chan_cb)
    ctx.app._image_table_global_chan_cb = chan_cb

    gl.addWidget(QLabel("Timepoint:", glob))
    tp_cb = QComboBox(glob)
    tp_cb.currentIndexChanged.connect(
        lambda _i: ctx.app._image_table_apply_global("tp")
    )
    gl.addWidget(tp_cb)
    ctx.app._image_table_global_tp_cb = tp_cb

    gl.addWidget(QLabel("FOV:", glob))
    fov_cb = QComboBox(glob)
    fov_cb.currentIndexChanged.connect(
        lambda _i: ctx.app._image_table_apply_global("fov")
    )
    gl.addWidget(fov_cb)
    ctx.app._image_table_global_fov_cb = fov_cb
    gl.addStretch(1)
    il.addWidget(glob)

    # ── Layout actions (operate on the selector grid above) ────────────────
    layout_actions = QWidget(inner)
    la = QHBoxLayout(layout_actions)
    la.setContentsMargins(0, 0, 0, 0)
    la.setSpacing(6)
    la.addWidget(btn_secondary(
        layout_actions, "Load Heatmap Layout", ctx.app._image_table_load_heatmap_layout,
    ))
    la.addWidget(btn_secondary(
        layout_actions, "Distribute Wells", ctx.app._image_table_distribute_wells,
    ))
    la.addWidget(btn_secondary(
        layout_actions, "Distribute Timepoints", ctx.app._image_table_distribute_timepoints,
    ))
    la.addStretch(1)
    il.addWidget(layout_actions)

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
    tophat_btn.clicked.connect(lambda _=False: ctx.app._image_table_toggle_tophat())
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
    boundaries_btn.clicked.connect(lambda _=False: ctx.app._image_table_toggle_boundaries())
    _wire_toggle_check(boundaries_btn, "Boundaries")
    ctx.app._image_table_boundaries_btn = boundaries_btn
    tr.addWidget(boundaries_btn)

    binary_btn = QPushButton("Binary", tools_row)
    binary_btn.setProperty("variant", "toggle")
    binary_btn.setCheckable(True)
    binary_btn.setChecked(bool(ctx.app._image_table_show_binary))
    binary_btn.setToolTip(
        "Show segmentation mask as binary: labeled cells white, background black."
    )
    binary_btn.clicked.connect(lambda _=False: ctx.app._image_table_toggle_binary())
    _wire_toggle_check(binary_btn, "Binary")
    ctx.app._image_table_binary_btn = binary_btn
    tr.addWidget(binary_btn)

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
        tools_row, "Copy PNG", ctx.app._image_table_copy_png, icon="copy",
    ))
    _b = btn_secondary(tools_row, "", ctx.app._image_table_copy_svg, icon="copy")
    _b.setToolTip("Copy SVG")
    tr.addWidget(_b)
    tr.addWidget(btn_secondary(
        tools_row, "Export", ctx.app._image_table_export, icon="download",
    ))
    il.addWidget(tools_row)

    # ── Generate row (its own row so the primary action is unambiguous) ────
    generate_row = QWidget(inner)
    gr = QHBoxLayout(generate_row)
    gr.setContentsMargins(0, 0, 0, 0)
    gr.setSpacing(6)
    gr.addWidget(btn_primary(generate_row, "Generate", ctx.app._image_table_generate))
    gr.addStretch(1)
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
    ctx.app._image_table_repopulate_dropdowns()
    ctx.app._image_table_rebuild_grid()
