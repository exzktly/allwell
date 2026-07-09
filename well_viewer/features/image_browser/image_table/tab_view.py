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

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMenu, QPushButton, QScrollArea, QSpinBox,
    QToolButton, QVBoxLayout, QWidget,
)

from well_viewer.data.crop_tool import CropTool
from well_viewer.core.ui_helpers import btn_primary, btn_secondary
from well_viewer.features.image_browser.image_table.grid_layout import (
    _fov_options, _timepoint_options,
)
from widgets.flow_layout import FlowLayout
from well_viewer.features.image_browser.image_table.controller import image_table_open_export_settings
from well_viewer.features.image_browser.image_table.controller import image_table_apply_dimensions
from well_viewer.features.image_browser.image_table.controller import image_table_apply_global
from well_viewer.features.image_browser.image_table.controller import image_table_rebuild_grid
from well_viewer.features.image_browser.image_table.controller import image_table_repopulate_dropdowns
from well_viewer.features.image_browser.image_table.controller import image_table_set_zoom
from well_viewer.features.image_browser.image_table.controller import image_table_set_thresh_frac
from widgets.stepper import Stepper
from well_viewer.features.image_browser.image_table.controller import image_table_open_add_row_dialog
from well_viewer.features.image_browser.image_table.grid_ops import image_table_open_add_col_dialog
from functools import partial
from well_viewer.features.image_browser.image_table.controller import image_table_copy_png
from well_viewer.features.image_browser.image_table.controller import image_table_copy_svg
from well_viewer.features.image_browser.image_table.controller import image_table_distribute_timepoints
from well_viewer.features.image_browser.image_table.controller import image_table_distribute_wells
from well_viewer.features.image_browser.image_table.controller import image_table_export
from well_viewer.features.image_browser.image_table.controller import image_table_save_to_gallery
from well_viewer.features.image_browser.image_table.controller import image_table_generate
from well_viewer.features.image_browser.image_table.controller import image_table_load_heatmap_layout
from well_viewer.features.image_browser.image_table.controller import image_table_build_layout
from well_viewer.features.image_browser.image_table.controller import image_table_apply_preset
from well_viewer.features.image_browser.image_table.controller import image_table_apply_dims_preset
from well_viewer.features.image_browser.image_table.build_layout import image_table_reset_layout
from well_viewer.features.image_browser.image_table.build_layout import image_table_clear_render
from well_viewer.features.image_browser.image_table.builder_view import (
    build_shelf, builder_notify_rendered,
)


class _ReorderList(QListWidget):
    """A single-column list whose internal-move drops commit the new order.

    QListWidget's ``InternalMove`` DnD emits rows-inserted / rows-removed (not
    ``rowsMoved``), so we hook ``dropEvent`` to fire *on_reordered* once the move
    has landed and the final top→bottom order is readable."""

    def __init__(self, parent, on_reordered) -> None:
        super().__init__(parent)
        self._on_reordered = on_reordered
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)

    def dropEvent(self, e) -> None:  # noqa: N802 (Qt override)
        super().dropEvent(e)
        self._on_reordered()


class _DropdownPanel(QFrame):
    """A frameless dropdown panel that dismisses when it loses activation.

    Unlike a ``Qt.Popup`` (or a ``QMenu``) it does **not** grab the mouse, so a
    child ``QListWidget``'s drag-reorder can actually start — a Qt.Popup's global
    mouse grab silently blocks the drag. Dismissal is handled manually on
    ``WindowDeactivate`` (click-away) and Esc, matching a popup's feel."""

    def __init__(self, anchor: QWidget) -> None:
        # Parent to the anchor (not its window) so it stays a QObject child of the
        # button — the window flags still make it a top-level, and Qt uses the
        # anchor's window as the transient parent for stacking.
        super().__init__(anchor,
                         Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setObjectName("Panel")
        self.setAttribute(Qt.WA_StyledBackground, True)

    def show_under(self, anchor: QWidget) -> None:
        self.adjustSize()
        self.move(anchor.mapToGlobal(QPoint(0, anchor.height())))
        self.show()
        self.raise_()
        self.activateWindow()

    def event(self, e) -> bool:  # noqa: N802 (Qt override)
        if e.type() == QEvent.WindowDeactivate:
            self.hide()
        return super().event(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802 (Qt override)
        if e.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)


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
    if _it.lut is None:
        _it.lut = {}
    # Shared square-region crop helper. on_change re-runs Generate so the
    # rendered grid follows mode toggles and crop changes automatically.
    if _it.crop_tool is None:
        _it.crop_tool = CropTool(
            on_change=lambda: image_table_generate(ctx),
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

    # ── Builder shelf: root + nested channels (the primary authoring surface) ─
    # Drag a root (Well/Timepoint/FOV) onto Rows/Columns, nest channels under it,
    # set each channel's ⚙ options; the table renders live. Replaces the old
    # Build-Layout dropdown strip (now demoted to Advanced, below).
    shelf_host = QWidget(inner)
    build_shelf(ctx, shelf_host)
    il.addWidget(shelf_host)

    sep_shelf = QFrame(inner)
    sep_shelf.setObjectName("Separator")
    sep_shelf.setFrameShape(QFrame.HLine)
    sep_shelf.setFixedHeight(2)
    il.addWidget(sep_shelf)

    # ── Advanced (collapsed): the legacy Build-Layout controls, the per-cell
    # dropdown selector grid, and the per-row LUT/display table. Kept mounted so
    # the render path (reads the cell combos), persistence, and their tests keep
    # working, and so irregular per-cell tables can still be hand-edited. The
    # shelf above is the primary surface; this is the power/override panel.
    adv_toggle = QToolButton(inner)
    adv_toggle.setCheckable(True)
    adv_toggle.setToolButtonStyle(Qt.ToolButtonTextOnly)
    adv_toggle.setProperty("variant", "secondary")
    adv_toggle.setText("▸ Advanced — selector grid & per-cell overrides")
    il.addWidget(adv_toggle)

    advanced = QFrame(inner)
    advanced.setObjectName("Panel")
    advanced.setVisible(False)
    adv_layout = QVBoxLayout(advanced)
    adv_layout.setContentsMargins(0, 6, 0, 8)
    adv_layout.setSpacing(6)
    il.addWidget(advanced)

    def _toggle_advanced(on: bool) -> None:
        advanced.setVisible(bool(on))
        adv_toggle.setText(("▾ " if on else "▸ ") + "Advanced — selector grid & per-cell overrides")
    adv_toggle.toggled.connect(_toggle_advanced)

    # The legacy Build-Layout strip + selector grid + LUT table live inside the
    # Advanced panel now (``builder`` parents to it, and the ``il.addWidget`` calls
    # below target ``adv_layout``).
    builder = QFrame(advanced)
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
    _AXIS_LABELS = {key: label for label, key in _AXES}

    # Row 1 — axes + channels + Build▾ + More▾.
    axes_row = QWidget(builder)
    ar = QHBoxLayout(axes_row)
    ar.setContentsMargins(0, 0, 0, 0)
    ar.setSpacing(6)

    def _make_dims_picker(which: str) -> QToolButton:
        """Ordered multi-select picker for a row/col axis, reordered by dragging.

        Check dimensions to include them on the axis; drag a row by its ⋮⋮ grip to
        set the nesting order (top→bottom = outer › inner). A single checked dim is
        the classic flat axis. A dimension already on the *other* axis is shown
        greyed-out and un-checkable here (a dim can only vary on one axis) — uncheck
        it there to free it. State (``row_dims``/``col_dims`` + the legacy
        ``row_axis``/``col_axis`` outer-dim mirror) is the source of truth; the
        picker keeps both in sync so the flat build path and saved view-state
        round-trip unchanged.

        The dropdown is a frameless ``_DropdownPanel`` (a non-grabbing top-level)
        rather than a ``QMenu``/``Qt.Popup``: those grab the mouse, which blocks the
        list's internal drag-reorder from ever starting."""
        st = ctx.state.image_table
        state_list = f"{which}_dims"
        axis_attr = f"{which}_axis"
        default_axis = getattr(st, axis_attr)
        head = "Rows" if which == "row" else "Columns"
        other_head = "Columns" if which == "row" else "Rows"

        btn = QToolButton(axes_row)
        btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        btn.setProperty("variant", "secondary")
        btn.setToolTip(
            "Choose which dimensions vary on this axis, then drag rows by the ⋮⋮ "
            "grip to set their nesting order (outer › inner)."
        )

        popup = _DropdownPanel(btn)
        pl = QVBoxLayout(popup)
        pl.setContentsMargins(8, 8, 8, 8)
        pl.setSpacing(6)
        cap = QLabel("Check to include · drag ⋮⋮ to order (outer → inner)", popup)
        cap.setProperty("role", "muted")
        pl.addWidget(cap)

        def _other_dims() -> set:
            """Dimensions occupied by the *other* axis (its effective dim list)."""
            eff = (st.effective_col_dims() if which == "row"
                   else st.effective_row_dims())
            return set(eff)

        def _current() -> list:
            dims = list(getattr(st, state_list) or [])
            return dims if dims else [getattr(st, axis_attr)]

        def _refresh() -> None:
            names = " › ".join(_AXIS_LABELS.get(d, d) for d in _current())
            btn.setText(f"{head}: {names}  ▾")

        def _selected_order() -> list:
            """Checked (checkable) dims in top→bottom list order (outer→inner)."""
            out = []
            for i in range(listw.count()):
                it = listw.item(i)
                if (it.flags() & Qt.ItemIsUserCheckable
                        and it.checkState() == Qt.Checked):
                    out.append(it.data(Qt.UserRole))
            return out

        def _commit() -> None:
            prev = list(getattr(st, state_list) or [])
            dims = _selected_order()
            setattr(st, state_list, dims)
            if dims:
                new_axis = dims[0]
            else:
                # Nothing checked → fall back to a dim that isn't claimed by the
                # other axis and isn't the one just unchecked (so freeing a dim to
                # move it doesn't immediately snap back), then relax if needed.
                other = _other_dims()
                new_axis = (
                    next((k for _l, k in _AXES if k not in other and k not in prev), None)
                    or next((k for _l, k in _AXES if k not in other), default_axis)
                )
            setattr(st, axis_attr, new_axis)
            setattr(ctx.runtime.image_table, axis_attr, new_axis)
            _refresh()

        listw = _ReorderList(popup, _commit)
        listw.setFixedWidth(260)
        pl.addWidget(listw)

        def _rebuild_list() -> None:
            listw.blockSignals(True)
            listw.clear()
            active = _current()
            blocked = _other_dims() - set(active)   # dims held by the other axis
            # Checked dims first, in their nesting order; the rest after (canonical
            # _AXES order) so ticking one appends it at the inner end.
            ordered = active + [k for _l, k in _AXES if k not in active]
            for key in ordered:
                label = _AXIS_LABELS.get(key, key)
                it = QListWidgetItem()
                it.setData(Qt.UserRole, key)
                if key in blocked:
                    # Greyed + un-checkable + un-draggable: it's on the other axis.
                    it.setText(f"      {label}   (on {other_head})")
                    it.setFlags(Qt.ItemIsSelectable)
                    it.setToolTip(f"Already on the {other_head} axis — uncheck it "
                                  f"there to move it here.")
                else:
                    it.setText(f"⋮⋮    {label}")
                    it.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled
                                | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
                    it.setCheckState(Qt.Checked if key in active else Qt.Unchecked)
                listw.addItem(it)
            listw.blockSignals(False)

        listw.itemChanged.connect(lambda _it: _commit())

        def _open_popup() -> None:
            _rebuild_list()
            popup.show_under(btn)

        btn.clicked.connect(_open_popup)

        _refresh()
        # A8: the ordered-dims picker is a widget handle → through the registry
        # (stamps app._image_table_{which}_dims_btn). Replaces the old single-select
        # axis combos.
        reg.register(f"_image_table_{which}_dims_btn", btn)
        # Let the Build▾ presets / Reset table refresh this button's text after they
        # mutate state.
        setattr(ctx.runtime.image_table, f"{which}_dims_refresh", _refresh)
        return btn

    def _make_filter_picker(head: str, opts_fn, state_attr: str, tooltip: str) -> QToolButton:
        """Self-refreshing checkable filter for a dimension's on-axis values.

        Mirrors the Channels dropdown for Timepoints / FOVs: the checked set is the
        subset that appears when that dimension is on the Rows/Columns axis (empty =
        all — a no-op). Keeps the nested product from exploding when a dataset has
        many timepoints / FOVs. Self-contained (rebuilds on ``aboutToShow``) so it
        needs no controller repopulate hook."""
        btn = QToolButton(axes_row)
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        btn.setProperty("hasMenu", True)  # reserve caret space (see theme.qss)
        btn.setProperty("variant", "secondary")
        btn.setToolTip(tooltip)
        menu = QMenu(btn)
        btn.setMenu(menu)

        def _state() -> set:
            return getattr(ctx.state.image_table, state_attr, None) or set()

        def _refresh_text() -> None:
            opts = opts_fn(ctx)
            sel = [o for o in opts if o in _state()]
            if not sel or len(sel) == len(opts):
                btn.setText(f"{head}: all")
            elif len(sel) <= 2:
                btn.setText(f"{head}: " + ", ".join(sel))
            else:
                btn.setText(f"{head}: {len(sel)} selected")

        def _toggle(val: str, on: bool) -> None:
            sel = set(_state())
            sel.add(val) if on else sel.discard(val)
            setattr(ctx.state.image_table, state_attr, sel)
            _refresh_text()

        def _set_all(on: bool) -> None:
            setattr(ctx.state.image_table, state_attr,
                    set(opts_fn(ctx)) if on else set())
            _rebuild()

        def _rebuild() -> None:
            menu.clear()
            opts = opts_fn(ctx)
            setattr(ctx.state.image_table, state_attr,
                    {v for v in _state() if v in opts})  # prune stale
            menu.addAction("Select all").triggered.connect(
                lambda _=False: _set_all(True))
            menu.addAction("Clear").triggered.connect(
                lambda _=False: _set_all(False))
            menu.addSeparator()
            for val in opts:
                act = menu.addAction(val)
                act.setCheckable(True)
                act.setChecked(val in _state())
                act.toggled.connect(lambda on, v=val: _toggle(v, on))
            _refresh_text()

        _rebuild()
        menu.aboutToShow.connect(_rebuild)
        _refresh_text()
        # Let "Reset table" redraw this button's label after it clears the filter
        # (mirrors the {which}_dims_refresh hook). Keyed by the state attr, e.g.
        # active_timepoints_refresh / active_fovs_refresh.
        setattr(ctx.runtime.image_table, f"{state_attr}_refresh", _refresh_text)
        return btn

    ar.addWidget(QLabel("Rows:", axes_row))
    ar.addWidget(_make_dims_picker("row"))

    ar.addWidget(QLabel("×  Columns:", axes_row))
    ar.addWidget(_make_dims_picker("col"))

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
    reg.register("_image_table_channel_btn", chan_btn)
    reg.register("_image_table_channel_menu", chan_menu)
    ar.addWidget(chan_btn)

    # Timepoint / FOV value filters — bound the nested product when those dims are
    # on an axis (mirror the Channels dropdown). Empty = all.
    tp_filter_btn = _make_filter_picker(
        "Time", _timepoint_options, "active_timepoints",
        "Filter which timepoints appear when Timepoints is a Rows/Columns "
        "dimension (none = all).")
    reg.register("_image_table_tp_filter_btn", tp_filter_btn)
    ar.addWidget(tp_filter_btn)

    fov_filter_btn = _make_filter_picker(
        "FOV", _fov_options, "active_fovs",
        "Filter which FOVs appear when FOVs is a Rows/Columns dimension "
        "(none = all).")
    reg.register("_image_table_fov_filter_btn", fov_filter_btn)
    ar.addWidget(fov_filter_btn)

    ar.addSpacing(8)

    # Build ▾ split button: click = build with current axes; menu = presets.
    build_act = QAction("Build table", axes_row)
    build_act.triggered.connect(lambda _=False: image_table_build_layout(ctx))
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
            lambda _=False, r=ra_, c=ca_: image_table_apply_preset(ctx, r, c)
        )
    # Nested (compound-axis) presets — rows and/or columns carry more than one
    # dimension, expanded as a mixed-radix product. "Wells › Channels × Time" is
    # the headline case: a time-series of every channel for every well.
    build_btn.menu().addSeparator()
    for label, (rd_, cd_) in (
        ("Wells › Channels × Time", (["well", "channel"], ["timepoint"])),
        ("Channels › Wells × Time", (["channel", "well"], ["timepoint"])),
        ("Wells × Channels › Time", (["well"], ["channel", "timepoint"])),
        ("FOV montage per Well", (["well"], ["fov"])),
    ):
        act = build_btn.menu().addAction(label)
        act.triggered.connect(
            lambda _=False, r=rd_, c=cd_: image_table_apply_dims_preset(ctx, r, c)
        )
    ar.addWidget(build_btn)

    # More ▾ menu: manual fill + add row/column.
    more_btn = _menu_button(axes_row, "More", tooltip="Manual fill helpers + Add row/column")
    _mm = more_btn.menu()
    _mm.addAction("Distribute Wells").triggered.connect(
        lambda _=False: image_table_distribute_wells(ctx))
    _mm.addAction("Distribute Timepoints").triggered.connect(
        lambda _=False: image_table_distribute_timepoints(ctx))
    _mm.addAction("Load Heatmap Layout").triggered.connect(
        lambda _=False: image_table_load_heatmap_layout(ctx))
    _mm.addSeparator()
    _mm.addAction("Add row…").triggered.connect(
        lambda _=False: image_table_open_add_row_dialog(ctx))
    _mm.addAction("Add column…").triggered.connect(
        lambda _=False: image_table_open_add_col_dialog(ctx))
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
    chan_cb.currentIndexChanged.connect(lambda _i: image_table_apply_global(ctx, "chan"))
    orow.addWidget(chan_cb)
    reg.register("_image_table_global_chan_cb", chan_cb)

    orow.addWidget(QLabel("Time:", opts_row))
    tp_cb = QComboBox(opts_row)
    tp_cb.currentIndexChanged.connect(lambda _i: image_table_apply_global(ctx, "tp"))
    orow.addWidget(tp_cb)
    reg.register("_image_table_global_tp_cb", tp_cb)

    orow.addWidget(QLabel("FOV:", opts_row))
    fov_cb = QComboBox(opts_row)
    fov_cb.currentIndexChanged.connect(lambda _i: image_table_apply_global(ctx, "fov"))
    orow.addWidget(fov_cb)
    reg.register("_image_table_global_fov_cb", fov_cb)

    orow.addStretch(1)

    # Group size: wrap the Columns axis into vertically-stacked bands (0 = off).
    orow.addWidget(QLabel("Group:", opts_row))
    group_spin = QSpinBox(opts_row)
    group_spin.setRange(0, 12)
    group_spin.setValue(int(getattr(ctx.state.image_table, "group_size", 0) or 0))
    group_spin.setSpecialValueText("Off")  # shown when value == 0
    group_spin.setFixedWidth(70)
    group_spin.setToolTip(
        "Wrap the Columns axis into vertically-stacked bands of this many "
        "columns; the Rows axis (e.g. channels) repeats per band. 0 = off "
        "(one band). Applies on Build."
    )

    def _on_group_changed(v: int) -> None:
        ctx.state.image_table.group_size = int(v)
        image_table_build_layout(ctx)

    group_spin.valueChanged.connect(_on_group_changed)
    orow.addWidget(group_spin)
    reg.register("_image_table_group_spin", group_spin)

    orow.addWidget(QLabel("Grid:", opts_row))
    rows_spin = QSpinBox(opts_row)
    # Grouped/tiled layouts stack many bands, so rows can exceed 12 (matches
    # controller._ROW_MAX). Columns stay ≤ 12 (a band is at most 12 wide).
    rows_spin.setRange(1, 60)
    rows_spin.setValue(int(ctx.state.image_table.rows))
    # Wide enough that a two-digit value clears the up/down arrows
    # instead of being clipped against them.
    rows_spin.setFixedWidth(70)
    rows_spin.setToolTip("Grid rows (Build sets this automatically).")
    rows_spin.valueChanged.connect(lambda _v: image_table_apply_dimensions(ctx))
    orow.addWidget(rows_spin)
    reg.register("_image_table_rows_spin", rows_spin)
    orow.addWidget(QLabel("×", opts_row))
    cols_spin = QSpinBox(opts_row)
    cols_spin.setRange(1, 12)
    cols_spin.setValue(int(ctx.state.image_table.cols))
    cols_spin.setFixedWidth(70)
    cols_spin.setToolTip("Grid columns (Build sets this automatically).")
    cols_spin.valueChanged.connect(lambda _v: image_table_apply_dimensions(ctx))
    orow.addWidget(cols_spin)
    reg.register("_image_table_cols_spin", cols_spin)
    bv.addWidget(opts_row)

    adv_layout.addWidget(builder)

    sep1 = QFrame(inner)
    sep1.setObjectName("Separator")
    sep1.setFrameShape(QFrame.HLine)
    sep1.setFixedHeight(2)
    adv_layout.addWidget(sep1)
    adv_layout.addSpacing(6)

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
    reg.register("_image_table_selector_grid", selector_grid)
    sb_layout.addWidget(selector_host)
    adv_layout.addWidget(selector_box)
    adv_layout.addSpacing(6)

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
    reg.register("_image_table_lut_container", lut_container)
    lut_outer_layout.addWidget(lut_container)
    adv_layout.addWidget(lut_outer)

    sep2 = QFrame(inner)
    sep2.setObjectName("Separator")
    sep2.setFrameShape(QFrame.HLine)
    sep2.setFixedHeight(1)
    il.addWidget(sep2)

    # ── Action strip — Generate · Crop▾ · crop status … Export▾ · Zoom ──
    # FlowLayout so the strip wraps instead of clipping the Export/Zoom cluster
    # when Split View narrows the pane.
    action_row = QWidget(inner)
    tr = FlowLayout(action_row, h_spacing=6, v_spacing=4)
    tr.setContentsMargins(0, 0, 0, 0)

    def _generate(_=None) -> None:
        # The one render trigger: draw the grid from current state, then clear the
        # shelf's "edited — press Generate" cue.
        image_table_generate(ctx)
        builder_notify_rendered(ctx)

    tr.addWidget(btn_primary(action_row, "Generate", _generate))

    # Composite: append a merged bottom row (each cell additively sums the
    # LUT-colored rows above it). A checkable toggle button styled like the rest
    # of the toolbar (variant="toggle") so it sits flush beside Generate/Crop at
    # the same size — not a stray QCheckBox. Re-renders in place when a table
    # already exists, else just stores the flag for the next Generate.
    comp_btn = reg.register("_image_table_composite_chk", QPushButton("Composite", action_row))
    comp_btn.setCheckable(True)
    comp_btn.setProperty("variant", "toggle")
    comp_btn.setChecked(bool(getattr(ctx.state.image_table, "composite", False)))
    comp_btn.setToolTip(
        "Append a merged row: each cell additively sums the LUT-colored "
        "channels above it in that column."
    )

    def _on_composite_toggled(checked: bool) -> None:
        ctx.state.image_table.composite = bool(checked)
        if getattr(ctx.state.image_table, "last_render", None):
            image_table_generate(ctx)

    comp_btn.toggled.connect(_on_composite_toggled)
    tr.addWidget(comp_btn)

    # Two independent clears. "Reset table" returns the Build Layout generator to
    # defaults (empty selector, Channels × Time, 2×3, no selection/nesting) but
    # leaves the rendered images. "Clear images" wipes only the rendered grid,
    # keeping the selector so a re-Generate is one click away.
    reset_btn = btn_secondary(action_row, "Reset table",
                              partial(image_table_reset_layout, ctx))
    reset_btn.setToolTip(
        "Reset the Build Layout to defaults (empty selector, Channels × Time, "
        "2×3). Leaves the rendered images below.")
    tr.addWidget(reset_btn)
    clear_img_btn = btn_secondary(action_row, "Clear images",
                                  partial(image_table_clear_render, ctx))
    clear_img_btn.setToolTip(
        "Clear the rendered image grid. Keeps the selector layout so Generate "
        "re-renders instantly.")
    tr.addWidget(clear_img_btn)

    # The per-display options (Tophat / Boundaries / Binary) are now per-row,
    # in the LUT boxes below the grid (each row independent). This menu keeps the
    # dataset-wide crop tool.
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

    # ON/OFF intensity threshold quick-edit for the active channel — drives the
    # per-row "≥ Thr" binary overlay. Edits the same ThreshFracOn value as the
    # Cell Gating tab / the plot tabs, and re-composites the generated table.
    tr.addWidget(QLabel("ON threshold:", action_row))
    thresh_stepper = reg.register("_image_table_thresh_frac_stepper", Stepper(
        action_row, value=50.0, minimum=0.0, maximum=1e9, single_step=1.0, decimals=1))
    thresh_stepper.setFixedWidth(96)
    thresh_stepper.setFocusPolicy(Qt.ClickFocus)
    _it_edit = getattr(thresh_stepper, "_edit", None)
    if _it_edit is not None:
        _it_edit.setFocusPolicy(Qt.ClickFocus)
    thresh_stepper.setToolTip(
        "ON/OFF intensity threshold for the active channel — drives a row's "
        "≥ Thr binary overlay. Same value as the Cell Gating tab's "
        "ThreshFracOn and the plot tabs' Threshold quick-edit."
    )
    thresh_stepper.valueChanged.connect(
        lambda v: image_table_set_thresh_frac(ctx, v)
    )
    tr.addWidget(thresh_stepper)

    # Export ▾ split button: click = Export; menu = Copy / settings.
    export_act = QAction("Export", action_row)
    export_act.triggered.connect(lambda _=False: image_table_export(ctx))
    export_btn = _menu_button(
        action_row, "Export", split=True, default_action=export_act,
        tooltip="Export the rendered table to a file. ▾ = copy / settings.",
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
    zoom_lbl.setFixedWidth(52)
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

    il.addWidget(action_row)

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
    # Pin cells to the top-left; otherwise QGridLayout distributes excess
    # space across rows/columns and the per-cell pixmaps render with large
    # gaps between them.
    render_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    reg.register("_image_table_render_grid", render_grid)
    il.addWidget(render_host)

    il.addStretch(1)

    # Build the initial grid + LUT row using current pipeline_info.
    image_table_repopulate_dropdowns(ctx)
    image_table_rebuild_grid(ctx)
