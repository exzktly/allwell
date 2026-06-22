"""Image Table tab — centre-pane builder (Qt port).

Top-down layout (the control surface is consolidated into a few menus /
split-buttons rather than a wall of flat buttons):
- Build Layout strip: Rows▾ × Cols▾ · Channels▾ · Build▾ (split: click builds,
  menu = presets) · More▾ (Distribute Wells/Timepoints, Load Heatmap, Add row…),
  then a second line of off-axis held values (Channel/Time/FOV) + grid size.
- Selector grid (rows × cols of per-cell dropdown groupboxes)
- Per-channel LUT row (one min/max LineEdit pair + Fit button per channel)
- Action strip: Generate · Display▾ (Tophat / Boundaries / Binary radio / Crop)
  · crop status … Export▾ (split: Export; menu = Copy PNG/SVG, settings) · Zoom
- Rendered image-table area (built by the controller's Generate)
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton,
    QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from well_viewer.data.crop_tool import CropTool
from well_viewer.core.ui_helpers import btn_primary
from well_viewer.features.image_browser.image_table.controller import image_table_open_export_settings
from well_viewer.features.image_browser.image_table.controller import image_table_apply_dimensions
from well_viewer.features.image_browser.image_table.controller import image_table_apply_global
from well_viewer.features.image_browser.image_table.controller import image_table_rebuild_grid
from well_viewer.features.image_browser.image_table.controller import image_table_repopulate_dropdowns
from well_viewer.features.image_browser.image_table.controller import image_table_set_zoom
from well_viewer.features.image_browser.image_table.controller import image_table_set_thresh_frac
from widgets.stepper import Stepper
from well_viewer.features.image_browser.image_table.controller import image_table_open_add_row_dialog
from well_viewer.features.image_browser.image_table.controller import image_table_open_add_col_dialog
from functools import partial
from well_viewer.features.image_browser.image_table.controller import image_table_copy_png
from well_viewer.features.image_browser.image_table.controller import image_table_copy_svg
from well_viewer.features.image_browser.image_table.controller import image_table_distribute_timepoints
from well_viewer.features.image_browser.image_table.controller import image_table_distribute_wells
from well_viewer.features.image_browser.image_table.controller import image_table_export
from well_viewer.features.image_browser.image_table.controller import image_table_generate
from well_viewer.features.image_browser.image_table.controller import image_table_load_heatmap_layout
from well_viewer.features.image_browser.image_table.controller import image_table_build_layout
from well_viewer.features.image_browser.image_table.controller import image_table_apply_preset


def _menu_button(parent, text, *, split=False, default_action=None, tooltip="") -> QToolButton:
    """A QToolButton that opens a QMenu — styled to read like btn_secondary.

    ``split=True`` gives a split button (text section runs *default_action*, the
    arrow opens the menu); otherwise the whole button opens the menu. The caller
    populates ``btn.menu()``.
    """
    btn = QToolButton(parent)
    btn.setText(text)
    btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
    menu = QMenu(btn)
    btn.setMenu(menu)
    if split and default_action is not None:
        btn.setPopupMode(QToolButton.MenuButtonPopup)
        btn.setDefaultAction(default_action)
    else:
        btn.setPopupMode(QToolButton.InstantPopup)
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setProperty("variant", "secondary")
    btn.setProperty("hasMenu", True)  # reserve caret space (see theme.qss)
    return btn


def build_image_table_tab(ctx, parent: QWidget) -> None:
    """Construct the Image Table tab inside *parent*."""
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # State defaults — controller methods read these.
    # Image Table data/options/caches live on the typed ImageTableState
    # (``ctx.app._app_state.image_table``), constructed with AppState — no
    # loose-attr defaults needed here. Only widget-handle containers and the
    # CropTool helper are seeded on the app below.
    if not hasattr(ctx.app, "_image_table_cells"):
        ctx.app._image_table_cells = []
    if not hasattr(ctx.app, "_image_table_lut"):
        ctx.app._image_table_lut = {}
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
    il.setContentsMargins(12, 12, 12, 12)
    il.setSpacing(6)
    scroll.setWidget(inner)
    layout.addWidget(scroll, 1)

    # ── Build Layout strip ──────────────────────────────────────────────────
    # One compact framed panel: declare what varies down rows / across columns,
    # which channels, then Build. Presets live in the Build split-button's menu;
    # manual-fill + Add-row live in "More". Off-axis held values + grid size sit
    # on a second line. Build only assembles the selector grid — press Generate
    # to render.
    builder = QFrame(inner)
    builder.setObjectName("ImageTableSelector")
    builder.setFrameShape(QFrame.NoFrame)
    bv = QVBoxLayout(builder)
    bv.setContentsMargins(0, 6, 0, 8)
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

    # Row 1 — axes + channels + Build▾ + More▾.
    axes_row = QWidget(builder)
    ar = QHBoxLayout(axes_row)
    ar.setContentsMargins(0, 0, 0, 0)
    ar.setSpacing(6)

    ar.addWidget(QLabel("Rows:", axes_row))
    row_axis_cb = _axis_combo(ctx.app._app_state.image_table.row_axis)
    ar.addWidget(row_axis_cb)
    ctx.app._image_table_row_axis_cb = row_axis_cb

    ar.addWidget(QLabel("×  Columns:", axes_row))
    col_axis_cb = _axis_combo(ctx.app._app_state.image_table.col_axis)
    ar.addWidget(col_axis_cb)
    ctx.app._image_table_col_axis_cb = col_axis_cb

    # Channel multi-select dropdown (drives the "Channels" axis; default all).
    chan_btn = QToolButton(axes_row)
    chan_btn.setText("Channels: all")
    chan_btn.setPopupMode(QToolButton.InstantPopup)
    chan_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
    chan_btn.setProperty("hasMenu", True)  # reserve caret space (see theme.qss)
    chan_btn.setToolTip(
        "Pick which channels go on the Channels axis (none = all channels)."
    )
    chan_menu = QMenu(chan_btn)
    chan_btn.setMenu(chan_menu)
    ctx.app._image_table_channel_btn = chan_btn
    ctx.app._image_table_channel_menu = chan_menu
    ar.addWidget(chan_btn)

    ar.addSpacing(8)

    # Build ▾ split button: click = build with current axes; menu = presets.
    build_act = QAction("Build table", axes_row)
    build_act.triggered.connect(lambda _=False: image_table_build_layout(ctx.app))
    build_btn = _menu_button(
        axes_row, "Build table", split=True, default_action=build_act,
        tooltip="Assemble the selector grid from the chosen axes. ▾ = quick presets.",
    )
    build_btn.setProperty("variant", "primary")
    for label, (ra_, ca_) in (
        ("Channels × Time", ("channel", "timepoint")),
        ("Channels × Wells", ("channel", "well")),
        ("Wells × Time", ("well", "timepoint")),
        ("Wells × Channels", ("well", "channel")),
    ):
        act = build_btn.menu().addAction(label)
        act.triggered.connect(
            lambda _=False, r=ra_, c=ca_: image_table_apply_preset(ctx.app, r, c)
        )
    ar.addWidget(build_btn)

    # More ▾ menu: manual fill + add row.
    more_btn = _menu_button(axes_row, "More", tooltip="Manual fill helpers + Add row")
    _mm = more_btn.menu()
    _mm.addAction("Distribute Wells").triggered.connect(
        lambda _=False: image_table_distribute_wells(ctx.app))
    _mm.addAction("Distribute Timepoints").triggered.connect(
        lambda _=False: image_table_distribute_timepoints(ctx.app))
    _mm.addAction("Load Heatmap Layout").triggered.connect(
        lambda _=False: image_table_load_heatmap_layout(ctx.app))
    _mm.addSeparator()
    _mm.addAction("Add row…").triggered.connect(
        lambda _=False: image_table_open_add_row_dialog(ctx.app))
    _mm.addAction("Add column…").triggered.connect(
        lambda _=False: image_table_open_add_col_dialog(ctx.app))
    ar.addWidget(more_btn)
    ar.addStretch(1)
    bv.addWidget(axes_row)

    # Row 2 — off-axis held values (Channel/Time/FOV) + grid size.
    opts_row = QWidget(builder)
    orow = QHBoxLayout(opts_row)
    orow.setContentsMargins(0, 0, 0, 0)
    orow.setSpacing(6)
    orow.addWidget(QLabel("Hold off-axis —", opts_row))

    orow.addWidget(QLabel("Ch:", opts_row))
    chan_cb = QComboBox(opts_row)
    chan_cb.currentIndexChanged.connect(lambda _i: image_table_apply_global(ctx.app, "chan"))
    orow.addWidget(chan_cb)
    ctx.app._image_table_global_chan_cb = chan_cb

    orow.addWidget(QLabel("Time:", opts_row))
    tp_cb = QComboBox(opts_row)
    tp_cb.currentIndexChanged.connect(lambda _i: image_table_apply_global(ctx.app, "tp"))
    orow.addWidget(tp_cb)
    ctx.app._image_table_global_tp_cb = tp_cb

    orow.addWidget(QLabel("FOV:", opts_row))
    fov_cb = QComboBox(opts_row)
    fov_cb.currentIndexChanged.connect(lambda _i: image_table_apply_global(ctx.app, "fov"))
    orow.addWidget(fov_cb)
    ctx.app._image_table_global_fov_cb = fov_cb

    orow.addStretch(1)

    orow.addWidget(QLabel("Grid:", opts_row))
    rows_spin = QSpinBox(opts_row)
    rows_spin.setRange(1, 12)
    rows_spin.setValue(int(ctx.app._app_state.image_table.rows))
    # Wide enough that a two-digit value (up to 12) clears the up/down arrows
    # instead of being clipped against them.
    rows_spin.setFixedWidth(70)
    rows_spin.setToolTip("Grid rows (Build sets this automatically).")
    rows_spin.valueChanged.connect(lambda _v: image_table_apply_dimensions(ctx.app))
    orow.addWidget(rows_spin)
    ctx.app._image_table_rows_spin = rows_spin
    orow.addWidget(QLabel("×", opts_row))
    cols_spin = QSpinBox(opts_row)
    cols_spin.setRange(1, 12)
    cols_spin.setValue(int(ctx.app._app_state.image_table.cols))
    cols_spin.setFixedWidth(70)
    cols_spin.setToolTip("Grid columns (Build sets this automatically).")
    cols_spin.valueChanged.connect(lambda _v: image_table_apply_dimensions(ctx.app))
    orow.addWidget(cols_spin)
    ctx.app._image_table_cols_spin = cols_spin
    bv.addWidget(opts_row)

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
    selector_box.setFrameShape(QFrame.NoFrame)
    sb_layout = QVBoxLayout(selector_box)
    sb_layout.setContentsMargins(0, 6, 0, 8)
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

    # ── Per-row levels & display table (rebuilt by the controller) ──────────
    # An aligned table — one row per grid row — for the per-row colormap, LUT
    # min/max + Fit, and the Top-hat / Boundaries / Binary display toggles.
    lut_outer = QFrame(inner)
    lut_outer.setObjectName("Panel")
    lut_outer.setFrameShape(QFrame.NoFrame)
    lut_outer_layout = QVBoxLayout(lut_outer)
    lut_outer_layout.setContentsMargins(0, 8, 0, 8)
    lut_outer_layout.setSpacing(6)

    lut_lbl = QLabel("Per-row levels & display", lut_outer)
    lut_lbl.setObjectName("SectionTitle")
    lut_outer_layout.addWidget(lut_lbl)

    lut_container = QWidget(lut_outer)
    lut_layout = QGridLayout(lut_container)
    lut_layout.setContentsMargins(0, 0, 0, 0)
    lut_layout.setHorizontalSpacing(14)
    lut_layout.setVerticalSpacing(6)
    ctx.app._image_table_lut_container = lut_container
    lut_outer_layout.addWidget(lut_container)
    il.addWidget(lut_outer)

    sep2 = QFrame(inner)
    sep2.setObjectName("Separator")
    sep2.setFrameShape(QFrame.HLine)
    sep2.setFixedHeight(1)
    il.addWidget(sep2)

    # ── Action strip — Generate · Crop▾ · crop status … Export▾ · Zoom ──
    action_row = QWidget(inner)
    tr = QHBoxLayout(action_row)
    tr.setContentsMargins(0, 0, 0, 0)
    tr.setSpacing(6)

    tr.addWidget(btn_primary(action_row, "Generate", partial(image_table_generate, ctx.app)))

    # The per-display options (Tophat / Boundaries / Binary) are now per-row,
    # in the LUT boxes below the grid (each row independent). This menu keeps the
    # dataset-wide crop tool.
    crop_btn = _menu_button(action_row, "Crop", tooltip="Crop region tool")
    cmenu = crop_btn.menu()
    _crop = ctx.app._image_table_crop_tool
    crop_act = QAction("Crop region (drag on image)", cmenu)
    crop_act.setCheckable(True)
    crop_act.setChecked(bool(_crop.mode))
    crop_act.toggled.connect(lambda _c=False: _crop.toggle_mode())
    cmenu.addAction(crop_act)
    ctx.app._image_table_crop_act = crop_act
    reset_crop_act = cmenu.addAction("Reset crop")
    reset_crop_act.triggered.connect(lambda _=False: _crop.clear())

    def _sync_crop_menu() -> None:
        blocked = crop_act.blockSignals(True)
        try:
            crop_act.setChecked(bool(_crop.mode))
        finally:
            crop_act.blockSignals(blocked)
    cmenu.aboutToShow.connect(_sync_crop_menu)
    tr.addWidget(crop_btn)

    # Crop status, shown inline so an active crop is visible at a glance.
    tr.addWidget(_crop.make_status_label(action_row))

    # ON/OFF intensity threshold quick-edit for the active channel — drives the
    # per-row "≥ Thr" binary overlay. Edits the same ThreshFracOn value as the
    # Cell Gating tab / the plot tabs, and re-composites the generated table.
    tr.addSpacing(12)
    tr.addWidget(QLabel("ON threshold:", action_row))
    ctx.app._image_table_thresh_frac_stepper = Stepper(
        action_row, value=50.0, minimum=0.0, maximum=1e9, single_step=1.0, decimals=1)
    ctx.app._image_table_thresh_frac_stepper.setFixedWidth(96)
    ctx.app._image_table_thresh_frac_stepper.setFocusPolicy(Qt.ClickFocus)
    _it_edit = getattr(ctx.app._image_table_thresh_frac_stepper, "_edit", None)
    if _it_edit is not None:
        _it_edit.setFocusPolicy(Qt.ClickFocus)
    ctx.app._image_table_thresh_frac_stepper.setToolTip(
        "ON/OFF intensity threshold for the active channel — drives a row's "
        "≥ Thr binary overlay. Same value as the Cell Gating tab's "
        "ThreshFracOn and the plot tabs' Threshold quick-edit."
    )
    ctx.app._image_table_thresh_frac_stepper.valueChanged.connect(
        lambda v: image_table_set_thresh_frac(ctx.app, v)
    )
    tr.addWidget(ctx.app._image_table_thresh_frac_stepper)

    tr.addStretch(1)

    # Export ▾ split button: click = Export; menu = Copy / settings.
    export_act = QAction("Export", action_row)
    export_act.triggered.connect(lambda _=False: image_table_export(ctx.app))
    export_btn = _menu_button(
        action_row, "Export", split=True, default_action=export_act,
        tooltip="Export the rendered table to a file. ▾ = copy / settings.",
    )
    emenu = export_btn.menu()
    emenu.addAction("Copy PNG").triggered.connect(
        lambda _=False: image_table_copy_png(ctx.app))
    emenu.addAction("Copy SVG").triggered.connect(
        lambda _=False: image_table_copy_svg(ctx.app))
    emenu.addSeparator()
    emenu.addAction("Export settings…").triggered.connect(
        lambda _=False: image_table_open_export_settings(ctx.app))
    tr.addWidget(export_btn)

    # Zoom: − [level] +  (clicking the % resets to 100%).
    tr.addWidget(QLabel("Zoom:", action_row))
    zoom_out = QPushButton("−", action_row)
    zoom_out.setProperty("variant", "secondary")
    zoom_out.setFixedWidth(32)
    zoom_out.setToolTip("Zoom out")
    zoom_out.clicked.connect(lambda _=False: image_table_set_zoom(ctx.app, 1 / 1.25, relative=True))
    tr.addWidget(zoom_out)

    zoom_lbl = QPushButton(f"{ctx.app._app_state.image_table.zoom * 100:.0f}%", action_row)
    zoom_lbl.setFlat(True)
    zoom_lbl.setFixedWidth(52)
    zoom_lbl.setToolTip("Click to reset zoom to 100%")
    zoom_lbl.clicked.connect(lambda _=False: image_table_set_zoom(ctx.app, 1.0))
    ctx.app._image_table_zoom_lbl = zoom_lbl
    tr.addWidget(zoom_lbl)

    zoom_in = QPushButton("+", action_row)
    zoom_in.setProperty("variant", "secondary")
    zoom_in.setFixedWidth(32)
    zoom_in.setToolTip("Zoom in")
    zoom_in.clicked.connect(lambda _=False: image_table_set_zoom(ctx.app, 1.25, relative=True))
    tr.addWidget(zoom_in)

    il.addWidget(action_row)

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
