"""Image Table tab — centre-pane scaffold (Qt port).

The drag-and-drop builder shelf (``builder_view.build_shelf``) is the authoring
surface: drag a root (Well/Timepoint/FOV) onto Rows/Columns, nest channels under
it with per-channel ⚙ render options, and the table renders. This module just
frames the shelf, the action strip, and the rendered grid:

- Builder shelf (root + nested channels)
- Action strip: Generate · Reset · Clear images · Crop▾ · ON-threshold · Export▾
  · Zoom · Properties (right-dock toggle)
- Rendered image-table area (built by the controller's Generate)

Per-cell tweaks come from a right-click override popover on a rendered cell
(``grid_ops.image_table_cell_override_menu``); table sizing/nesting is owned by
the shelf. The render model (``ctx.runtime.image_table.cells``) is headless plain
data — no per-cell selector grid, per-row LUT table, or held-value combos here.
"""

from __future__ import annotations

from functools import partial

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QMenu, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt

from well_viewer.data.crop_tool import CropTool
from well_viewer.core.ui_helpers import btn_primary, btn_secondary
from widgets.flow_layout import FlowLayout
from widgets.stepper import Stepper
from well_viewer.features.image_browser.image_table.controller import (
    image_table_copy_png,
    image_table_copy_svg,
    image_table_export,
    image_table_generate,
    image_table_open_export_settings,
    image_table_repopulate_dropdowns,
    image_table_rebuild_grid,
    image_table_save_to_gallery,
    image_table_set_display,
    image_table_set_spacing,
    image_table_set_thresh_frac,
    image_table_set_zoom,
)
from well_viewer.features.image_browser.image_table.build_layout import (
    image_table_clear_render,
    image_table_reset_layout,
)
from well_viewer.features.image_browser.image_table.builder_view import (
    build_shelf, builder_notify_rendered, builder_flush_pending,
)


def _menu_button(parent, text, *, tooltip="") -> QToolButton:
    """A QToolButton that opens a QMenu on any click — styled to read like
    btn_secondary. The caller populates ``btn.menu()``."""
    btn = QToolButton(parent)
    btn.setText(text)
    btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
    menu = QMenu(btn)
    btn.setMenu(menu)
    btn.setPopupMode(QToolButton.InstantPopup)
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setProperty("variant", "secondary")
    btn.setProperty("hasMenu", True)  # reserve caret space (see theme.qss)
    return btn


def _toggle_props_dock(ctx) -> None:
    # isHidden() is the dock's own explicit flag (ancestor-independent), so the
    # toggle is correct regardless of whether the tab is currently shown.
    dock = getattr(ctx.runtime.image_table, "props_dock", None)
    if dock is not None:
        dock.setVisible(dock.isHidden())


def _build_properties_dock(ctx, dock) -> None:
    """Fill the right-side Properties inspector: the grouped-view spacing gaps +
    label font sizes, as Label:[Stepper] rows that live-re-render. A header with a
    close button matches the figure-properties panel other tabs slide out."""
    from widgets.icon_button import IconButton
    # Wide enough that the two-column rows fit a label + a full-text dropdown
    # ("Bottom-right") without truncating, even with the scrollbar shown. Still far
    # narrower than the plotting Export panel (440), so it's a modest footprint.
    dock.setFixedWidth(288)
    panel = QFrame(dock)
    panel.setObjectName("Panel")
    pl = QVBoxLayout(panel)
    pl.setContentsMargins(12, 10, 12, 12)
    pl.setSpacing(8)

    hdr = QHBoxLayout()
    title = QLabel("Properties", panel)
    title.setObjectName("Heading")
    hdr.addWidget(title)
    hdr.addStretch(1)
    close = IconButton("x", panel, tooltip="Close")
    close.clicked.connect(lambda _=False: dock.setVisible(False))
    hdr.addWidget(close)
    pl.addLayout(hdr)

    grid = QGridLayout()
    grid.setColumnMinimumWidth(0, 104)
    grid.setColumnStretch(0, 0)
    grid.setColumnStretch(1, 1)
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(5)
    its = ctx.state.image_table
    r = [0]

    def _cap(text: str) -> None:
        cap = QLabel(text, panel)
        cap.setObjectName("Muted")
        grid.addWidget(cap, r[0], 0, 1, 2)
        r[0] += 1

    def _prop(label: str, key: str, default: int, lo: int, hi: int, tip: str) -> None:
        lab = QLabel(label, panel)
        lab.setToolTip(tip)
        grid.addWidget(lab, r[0], 0)
        st = Stepper(panel, value=float(getattr(its, key, default)),
                     minimum=float(lo), maximum=float(hi), single_step=1.0, decimals=0)
        st.setToolTip(tip)
        # The Stepper self-sizes to its value width; left-align it so it sits by
        # the label rather than stretching across the column to the panel edge.
        st.valueChanged.connect(lambda v, k=key: image_table_set_spacing(ctx, k, v))
        grid.addWidget(st, r[0], 1, Qt.AlignLeft)
        r[0] += 1

    def _propf(label: str, key: str, default: float, lo: float, hi: float,
               step: float, decimals: int, tip: str) -> None:
        lab = QLabel(label, panel)
        lab.setToolTip(tip)
        grid.addWidget(lab, r[0], 0)
        st = Stepper(panel, value=float(getattr(its, key, default)),
                     minimum=float(lo), maximum=float(hi), single_step=step,
                     decimals=decimals)
        st.setToolTip(tip)
        # The Stepper self-sizes to its value width (incl. decimals); left-align it.
        st.valueChanged.connect(lambda v, k=key: image_table_set_display(ctx, k, v))
        grid.addWidget(st, r[0], 1, Qt.AlignLeft)
        r[0] += 1

    def _combo(label: str, key: str, choices, tip: str) -> None:
        lab = QLabel(label, panel)
        lab.setToolTip(tip)
        grid.addWidget(lab, r[0], 0)
        cb = QComboBox(panel)
        cb.setToolTip(tip)
        # Fit the longest option ("Bottom-right") plus the dropdown arrow without
        # truncating the current text.
        cb.setMinimumWidth(132)
        for disp, val in choices:
            cb.addItem(disp, val)
        ix = cb.findData(getattr(its, key, None))
        if ix >= 0:
            cb.setCurrentIndex(ix)
        cb.currentIndexChanged.connect(
            lambda _i, k=key, _cb=cb: image_table_set_display(ctx, k, _cb.currentData()))
        grid.addWidget(cb, r[0], 1)
        r[0] += 1

    def _check(label: str, key: str, tip: str) -> None:
        lab = QLabel(label, panel)
        lab.setToolTip(tip)
        grid.addWidget(lab, r[0], 0)
        chk = QCheckBox(panel)
        chk.setChecked(bool(getattr(its, key, False)))
        chk.setToolTip(tip)
        chk.toggled.connect(lambda v, k=key: image_table_set_display(ctx, k, bool(v)))
        grid.addWidget(chk, r[0], 1)
        r[0] += 1

    _CELLS = [("All cells", "all"), ("Top-left", "tl"), ("Top-right", "tr"),
              ("Bottom-left", "bl"), ("Bottom-right", "br")]
    _CORNERS = [("Top-left", "tl"), ("Top-right", "tr"),
                ("Bottom-left", "bl"), ("Bottom-right", "br")]
    _COLORS = [("White", "white"), ("Black", "black"), ("Yellow", "yellow"),
               ("Cyan", "cyan"), ("Magenta", "magenta")]
    _BAR_COLORS = [("Auto (contrast)", "auto")] + _COLORS

    _cap("Spacing (px)")
    _prop("Cell gap", "cell_gap", 2, 0, 200,
          "Gap between adjacent images within a group.")
    _prop("Row-group gap", "row_group_gap", 18, 0, 400,
          "Vertical gap between row facet groups. Only applies when the Rows axis "
          "nests a root + channels (≥2 dimensions).")
    _prop("Col-group gap", "col_group_gap", 18, 0, 400,
          "Horizontal gap between column facet groups. Only applies when the "
          "Columns axis nests a root + channels (≥2 dimensions).")
    _cap("Label font (px)")
    _prop("Axis labels", "label_font_pt", 11, 6, 48,
          "Font size of the row / column axis value labels.")
    _prop("Group labels", "group_font_pt", 11, 6, 48,
          "Font size of the facet-group band labels (nested axes only).")

    _cap("Scale bar")
    _check("Show scale bar", "scale_bar_show",
           "Burn a scale bar into the rendered images (on by default). Enter the "
           "Pixel µm/px below to make it appear; untick to hide it without clearing "
           "the values.")
    _propf("Pixel µm/px", "pixel_size_um", 0.0, 0.0, 100.0, 0.05, 3,
           "Physical size of one pixel in microns — the one value the pipeline "
           "can't supply. Set it to your objective's value; the bar appears once "
           "it's > 0. 0 = no bar.")
    _propf("Bar length µm", "scale_bar_um", 50.0, 0.0, 5000.0, 1.0, 1,
           "Scale-bar length in microns. Bar pixels = length ÷ pixel size. 0 = no bar.")
    _propf("Bar thickness px", "scale_bar_thickness_px", 4, 1, 40, 1.0, 0,
           "Bar thickness (px, relative to a 240 px cell). Increase it if the bar "
           "is too thin to see once cells are downscaled.")
    _combo("Show on", "scale_bar_cells", _CELLS,
           "Which cell(s) show the scale bar: every cell, or one grid corner.")
    _combo("Position", "scale_bar_img_corner", _CORNERS,
           "Where in the image the bar sits.")
    _combo("Colour", "scale_bar_color", _BAR_COLORS,
           "Scale-bar colour. 'Auto' picks black or white per cell for contrast "
           "(so a white bar isn't lost on a light 'Greys' background).")

    _cap("Cell labels")
    _combo("Mode", "label_mode", [("None", "none"), ("Identity", "identity")],
           "Burn a well · channel · timepoint label into the image.")
    _combo("Show on", "label_cells", _CELLS,
           "Which cell(s) show the label: every cell, or one grid corner.")
    _combo("Position", "label_img_corner", _CORNERS,
           "Where in the image the label sits.")
    _propf("Font (px)", "label_font_px", 12, 6, 48, 1.0, 0,
           "Label font size (relative to a 240 px cell, so live and export match).")
    _combo("Colour", "label_color", _COLORS, "Label text colour.")
    _check("Background box", "label_bg",
           "Draw a translucent dark box behind the label for legibility.")

    # The panel grew several sections (spacing + fonts + scale bar + labels), so it
    # can be taller than a short / Split-View pane. Scroll the controls (header +
    # close stay pinned) instead of clipping the bottom rows.
    content = QWidget()
    cl = QVBoxLayout(content)
    cl.setContentsMargins(0, 0, 0, 0)
    cl.addLayout(grid)
    cl.addStretch(1)
    scroll = QScrollArea(panel)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    pl.addWidget(scroll, 1)
    dock.layout().addWidget(panel)


def build_image_table_tab(ctx, parent: QWidget) -> None:
    # A8 burn-down: Qt widget handles go through the WidgetRegistry
    # (``ctx.widgets``), which stamps ``app._image_table_*`` so legacy
    # readers resolve. State lists/dicts + the crop tool stay on app.
    reg = ctx.widgets
    """Construct the Image Table tab inside *parent*."""
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # State defaults — controller methods read these.
    # Image Table data/options/caches live on the typed ImageTableState
    # (``ctx.state.image_table``), constructed with AppState — no
    # loose-attr defaults needed here. Only widget-handle containers and the
    # CropTool helper are seeded on the app below.
    # Live grid handles are the ``_image_table_`` runtime namespace; seed the
    # container defaults idempotently (ctx.runtime stamps app._image_table_*
    # so the controller's legacy readers resolve unchanged).
    _it = ctx.runtime.image_table
    if _it.cells is None:
        _it.cells = []
    # Shared square-region crop helper. on_change re-runs Generate so the
    # rendered grid follows mode toggles and crop changes automatically.
    if _it.crop_tool is None:
        _it.crop_tool = CropTool(
            on_change=lambda: image_table_generate(ctx),
        )

    # Host the tab body with a right-side dock that slides out the Properties
    # inspector — matching every other tab's figure-properties panel.
    from well_viewer.core.ui_helpers import make_plot_with_right_dock
    body_area, body_layout, props_dock = make_plot_with_right_dock(parent)
    ctx.runtime.image_table.props_dock = props_dock
    reg.register("_image_table_props_dock", props_dock)

    scroll = QScrollArea(body_area)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    inner = QWidget()
    il = QVBoxLayout(inner)
    il.setContentsMargins(12, 12, 12, 12)
    il.setSpacing(6)
    scroll.setWidget(inner)
    body_layout.addWidget(scroll, 1)
    _build_properties_dock(ctx, props_dock)

    # ── Builder shelf: root + nested channels (the sole authoring surface) ────
    # Drag a root (Well/Timepoint/FOV) onto Rows/Columns, nest channels under it,
    # set each channel's ⚙ options, then Generate. Per-cell tweaks come from a
    # right-click override popover on a rendered cell.
    shelf_host = QWidget(inner)
    build_shelf(ctx, shelf_host)
    il.addWidget(shelf_host)

    sep_shelf = QFrame(inner)
    sep_shelf.setObjectName("Separator")
    sep_shelf.setFrameShape(QFrame.HLine)
    sep_shelf.setFixedHeight(2)
    il.addWidget(sep_shelf)


    # ── Action strip — Generate · Crop▾ · crop status … Export▾ · Zoom ──
    # FlowLayout so the strip wraps instead of clipping the Export/Zoom cluster
    # when Split View narrows the pane.
    action_row = QWidget(inner)
    tr = FlowLayout(action_row)
    tr.setContentsMargins(0, 0, 0, 0)

    def _generate(_=None) -> None:
        # The one render trigger: flush any debounced shelf edit so the grid
        # reflects the latest builder state on the FIRST click (not the second),
        # draw the grid from current state, then clear the "edited — press
        # Generate" cue.
        builder_flush_pending(ctx)
        image_table_generate(ctx)
        builder_notify_rendered(ctx)

    tr.addWidget(btn_primary(action_row, "Generate", _generate))

    # Two independent clears. "Reset table" returns the builder to defaults (no
    # roots/channels, 2×3) but leaves the rendered images. "Clear images" wipes
    # only the rendered grid, keeping the layout so a re-Generate is one click away.
    reset_btn = btn_secondary(action_row, "Reset table",
                              partial(image_table_reset_layout, ctx))
    reset_btn.setToolTip(
        "Reset the builder to defaults. Leaves the rendered images below.")
    tr.addWidget(reset_btn)
    clear_img_btn = btn_secondary(action_row, "Clear images",
                                  partial(image_table_clear_render, ctx))
    clear_img_btn.setToolTip(
        "Clear the rendered image grid. Keeps the layout so Generate "
        "re-renders instantly.")
    tr.addWidget(clear_img_btn)

    # Dataset-wide crop tool (per-channel display options live on the shelf's ⚙).
    crop_btn = _menu_button(action_row, "Crop", tooltip="Crop region tool")
    cmenu = crop_btn.menu()
    _crop = ctx.runtime.image_table.crop_tool
    crop_act = QAction("Crop region (drag on image)", cmenu)
    crop_act.setCheckable(True)
    crop_act.setChecked(bool(_crop.mode))
    crop_act.toggled.connect(lambda _c=False: _crop.toggle_mode())
    cmenu.addAction(crop_act)
    reg.register("_image_table_crop_act", crop_act)
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

    # ON/OFF intensity threshold quick-edit — drives the "≥ Thr" binary overlay.
    # A channel dropdown chooses which channel it applies to (its own ThreshFracOn,
    # shared with the Cell Gating tab / plot tabs); the stepper edits that channel.
    tr.addWidget(QLabel("ON threshold:", action_row))
    thresh_chan_cb = reg.register("_image_table_thresh_chan_cb", QComboBox(action_row))
    thresh_chan_cb.setToolTip("Which channel the ON threshold applies to.")
    thresh_chan_cb.setMinimumWidth(96)
    tr.addWidget(thresh_chan_cb)
    thresh_stepper = reg.register("_image_table_thresh_frac_stepper", Stepper(
        action_row, value=50.0, minimum=0.0, maximum=1e9, single_step=1.0, decimals=1))
    thresh_stepper.setFixedWidth(96)
    thresh_stepper.setFocusPolicy(Qt.ClickFocus)
    _it_edit = getattr(thresh_stepper, "_edit", None)
    if _it_edit is not None:
        _it_edit.setFocusPolicy(Qt.ClickFocus)
    thresh_stepper.setToolTip(
        "ON/OFF intensity threshold for the chosen channel — drives its ≥ Thr "
        "binary overlay. Same value as the Cell Gating tab's ThreshFracOn and "
        "the plot tabs' Threshold quick-edit."
    )
    # The combo shows uppercase display names; the gating tokens are the
    # canonical lowercase fluor tokens, so normalise before set/get.
    def _thresh_chan() -> str | None:
        return thresh_chan_cb.currentText().strip().lower() or None

    thresh_stepper.valueChanged.connect(
        lambda v: image_table_set_thresh_frac(ctx, v, _thresh_chan())
    )

    def _sync_thresh_for_channel(_chan: str) -> None:
        from well_viewer.core.channel_state_controller import get_thresh_frac_on
        val = get_thresh_frac_on(ctx, _thresh_chan())
        blocked = thresh_stepper.blockSignals(True)
        try:
            thresh_stepper.setValue(float(val))
        finally:
            thresh_stepper.blockSignals(blocked)
    thresh_chan_cb.currentTextChanged.connect(_sync_thresh_for_channel)
    tr.addWidget(thresh_stepper)

    # Export ▾ menu button: clicking anywhere on it opens the menu (no
    # split default-action — that surprised users by firing "Export to
    # File" on a plain click instead of showing the options).
    export_btn = _menu_button(
        action_row, "Export", tooltip="Export, copy, or save this table.",
    )
    emenu = export_btn.menu()
    emenu.addAction("Export to File…").triggered.connect(
        lambda _=False: image_table_export(ctx))
    emenu.addSeparator()
    emenu.addAction("Copy PNG").triggered.connect(
        lambda _=False: image_table_copy_png(ctx))
    emenu.addAction("Copy SVG").triggered.connect(
        lambda _=False: image_table_copy_svg(ctx))
    emenu.addSeparator()
    emenu.addAction("Save to Saved Plots").triggered.connect(
        lambda _=False: image_table_save_to_gallery(ctx))
    emenu.addSeparator()
    emenu.addAction("Export settings…").triggered.connect(
        lambda _=False: image_table_open_export_settings(ctx))
    tr.addWidget(export_btn)

    # Zoom: − [level] +  (clicking the % resets to 100%).
    tr.addWidget(QLabel("Zoom:", action_row))
    zoom_out = QPushButton("−", action_row)
    zoom_out.setProperty("variant", "secondary")
    zoom_out.setFixedWidth(32)
    zoom_out.setToolTip("Zoom out")
    zoom_out.clicked.connect(lambda _=False: image_table_set_zoom(ctx, 1 / 1.25, relative=True))
    tr.addWidget(zoom_out)

    zoom_lbl = QPushButton(f"{ctx.state.image_table.zoom * 100:.0f}%", action_row)
    zoom_lbl.setFlat(True)
    # Wide enough for the max "400%" without clipping the trailing % (52 was tight).
    zoom_lbl.setFixedWidth(60)
    zoom_lbl.setToolTip("Click to reset zoom to 100%")
    zoom_lbl.clicked.connect(lambda _=False: image_table_set_zoom(ctx, 1.0))
    reg.register("_image_table_zoom_lbl", zoom_lbl)
    tr.addWidget(zoom_lbl)

    zoom_in = QPushButton("+", action_row)
    zoom_in.setProperty("variant", "secondary")
    zoom_in.setFixedWidth(32)
    zoom_in.setToolTip("Zoom in")
    zoom_in.clicked.connect(lambda _=False: image_table_set_zoom(ctx, 1.25, relative=True))
    tr.addWidget(zoom_in)

    # Properties (spacing + label fonts) live in the right-side dock; the
    # sliders button toggles it, pinned to the action strip's right edge.
    from well_viewer.core.ui_helpers import wrap_ctrl_row_with_properties
    il.addWidget(wrap_ctrl_row_with_properties(
        action_row, lambda: _toggle_props_dock(ctx),
        tooltip="Show / hide the table Properties (spacing & label fonts)"))

    # ── Rendered image table (drawn BELOW the action strip) ────────────────
    render_lbl = QLabel("Image Table:", inner)
    render_lbl.setProperty("role", "section")
    il.addWidget(render_lbl)

    # EmptyState placeholder shown until the controller populates the grid.
    # ``image_table_generate`` hides this and reveals the render host once the
    # first table is produced.
    from widgets.empty_state import EmptyState as _EmptyState
    empty_state = reg.register("_image_table_empty_state", _EmptyState(
        "No image table generated",
        icon="image",
        parent=inner,
        hint="Choose wells/channels above, then press Generate.",
    ))
    # Give the placeholder enough vertical room so the wrapped hint never
    # gets clipped by tight sibling rows.
    empty_state.setMinimumHeight(180)
    il.addWidget(empty_state)

    render_host = QWidget(inner)
    render_grid = QGridLayout(render_host)
    render_grid.setContentsMargins(0, 0, 0, 0)
    render_grid.setSpacing(2)
    reg.register("_image_table_render_grid", render_grid)
    # Align the render host to the top-left of its slot (the trailing stretch
    # below already keeps it at its sizeHint; each cell is sized to its actual
    # pixmap in image_table_generate, so rows stay tight). NOTE: a QLayout's own
    # setAlignment is ignored when it's installed directly on a widget — the
    # alignment must be applied to the host *item* here to take effect.
    il.addWidget(render_host, 0, Qt.AlignTop | Qt.AlignLeft)

    il.addStretch(1)

    # Build the initial grid + LUT row using current pipeline_info.
    image_table_repopulate_dropdowns(ctx)
    image_table_rebuild_grid(ctx)
