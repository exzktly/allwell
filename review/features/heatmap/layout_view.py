"""Heat-map layout editor — the **Layout** panel in the Heat Map's right dock.

The Heat Map's control band carries one **Layout** toggle; it opens this panel
in the plot's right dock (``ui_helpers.make_plot_with_right_dock``), the same
slide-out the Properties panel uses, so the two share one place and one look
and only one is open at a time. The panel holds the grid size (rows ×
columns), **Auto-populate from selection**, **Clear**, and the grid itself.
The user drags wells from the *rail plate map* (the sidebar's well picker)
into cells, moves wells between cells by dragging, and clears a cell by
double-clicking it (or by dragging the well back onto the plate map). The rail
plate is the canonical drag source while the Heat Map tab is focused — the
descriptor's ``plate_drag_source``, applied by
``core/tab_controller.sync_rail_plate_drag_mode`` — and the dock sits on the
far side of the figure from it, so a drag crosses the page it is building.

At the desk the panel opens with the page when the layout is still empty,
because the page has nothing to draw until the grid holds a well; once the grid
is filled it stays out of the figure's way until the toggle opens it. In phone
layout it never opens by itself (``_phone.layout_opens_with_page``): the dock
is a full-width sheet there and would cover the page the user just opened.

State of truth: ``ctx.runtime.heatmap.layouts``. The editor maintains exactly
one layout named ``SIDEBAR_LAYOUT_NAME`` and synchronises the table widget to
its ``cells`` dict on every change. The name is ``"Sidebar"`` because that is
what saved datasets already carry in ``persistence.json``; it names the layout
the editor owns, not where the editor is drawn. Dragging a well in puts
that one well in a cell, but a cell can hold several — "Auto-populate
from selection" builds one cell per *entry* when **Rep-set avg** is on,
and an entry is a visible replicate set **or** a well the user soloed
onto the plot, because a plate is very often a mixture of the two.
Those cells show as ``A01+3`` (the members in the tooltip) and
move as a unit; the well token behind a cell is carried in
``_CELL_TOKEN_ROLE`` so the summary text never reaches the model.
"""

from __future__ import annotations


from PySide6.QtCore import QEvent, QMimeData, QObject, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QDrag, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from review.features.heatmap.models import HeatmapLayout
from core.safe import swallowed


from review.core.dnd import WELL_MIME  # shared drag/drop token
from ui import theme
from widgets.spin_box import SpinBox

#: The one layout this editor owns. Persisted under this name, so it stays
#: ``"Sidebar"`` although the editor is drawn in the plot's right dock.
SIDEBAR_LAYOUT_NAME = "Sidebar"

#: The band toggle's label, and the panel's title: one word for one thing.
LAYOUT_BUTTON_TEXT = "Layout"

#: The grid shows at most this many rows and columns before it scrolls: a whole
#: 8×12 plate fits, and a 32×32 grid does not run the panel off the screen.
_MAX_SHOWN_ROWS = 8
_MAX_SHOWN_COLS = 12
#: The grid-size spin boxes' limit (and Auto-populate's growth cap).
_MAX_GRID = 32
#: Fewest columns the grid shrinks to in a narrow host before it scrolls
#: sideways instead.
_MIN_VISIBLE_COLS = 4
#: The widest single-well token a cell is sized for at the least, so an empty
#: or half-filled grid is drawn at the width a full 96-well plate needs.
_WIDEST_TOKEN = "H12"
#: Room around a cell's text, and around a row number, in pixels at the desk
#: (scaled with the UI): the cell's text is centred, so this is the gap that
#: keeps neighbouring tokens from reading as one word.
_CELL_PAD_W = 3
_CELL_PAD_H = 4
#: The least room a cell keeps either side of its text in a host too narrow
#: for the comfortable padding: the cells give up their padding down to this
#: before the grid starts to scroll.
_CELL_PAD_MIN = 1
#: How much wider than its comfortable width a cell may grow to fill a wide
#: host (phone layout's full-width sheet held sideways). Past this a short
#: grid would stretch into bars, so it stops and leaves the rest of the row.
_ROOMY_CELL_FACTOR = 2
#: Inset of the tinted tile an occupied cell is drawn as, in pixels at the
#: desk, so neighbouring tiles read as separate wells rather than one bar.
_TILE_INSET = 1


# ── Drag-aware widgets ───────────────────────────────────────────────────────

# A cell's displayed text is not always its well token: a cell holding a whole
# replicate set is summarised as ``A01+3`` (see :func:`_cell_display`). The token
# a drag carries — and the one a double-click clears — is kept beside the text in
# this role so the summary never leaks into the model.
_CELL_TOKEN_ROLE = Qt.UserRole + 1


def _cell_display(wells: list[str]) -> str:
    """Cell text for *wells*: the token itself, or ``A01+3`` for a set.

    Rendering only ``wells[0]`` made every extra member invisible, so a cell
    auto-populated from a replicate set read as if the rest had been dropped.
    Mirrors the annotation the heat map itself draws on a multi-well cell.
    """
    if not wells:
        return ""
    if len(wells) == 1:
        return wells[0]
    return f"{wells[0]}+{len(wells) - 1}"


def _cell_token(item) -> str:
    """The well token a table item stands for (never its ``A01+3`` summary)."""
    token = item.data(_CELL_TOKEN_ROLE)
    if token:
        return str(token).strip()
    return (item.text() or "").strip()


class _TokenDelegate(QStyledItemDelegate):
    """Draws an occupied cell as a tinted tile with its token centred, never elided.

    Qt's own item painting keeps a focus-frame margin either side of the text
    and elides whatever does not fit inside it, which in a grid of one-token
    cells costs a third of the room and turned ``A02`` into ``A…``. The cells
    are measured to hold their widest text (``_fit_table``), so here the
    background, selection and hover come from the style as usual and the text
    is drawn over the full cell.

    An occupied cell is filled with the accent's low-emphasis tint, inset a
    pixel, so the wells already placed read at a glance against the empty
    cells around them — the grid is a map of what is placed where, and plain
    text on an even ground made it a page of labels. The tint is read from the
    theme at paint time, so it follows a theme switch without a rebuild.
    """

    def paint(self, painter, option, index) -> None:  # Qt naming
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text, opt.text = opt.text, ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        if not text:
            return
        selected = bool(opt.state & QStyle.State_Selected)
        painter.save()
        try:
            if not selected:
                inset = max(1, round(_TILE_INSET * theme.ui_scale()))
                tile = QRectF(opt.rect).adjusted(inset, inset, -inset, -inset)
                radius = min(theme.Radii.sm, tile.height() / 3)
                painter.setRenderHint(painter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(theme.Colors.accent_dim))
                painter.drawRoundedRect(tile, radius, radius)
            painter.setFont(opt.font)
            if selected:
                painter.setPen(opt.palette.color(QPalette.HighlightedText))
            else:
                painter.setPen(QColor(theme.Colors.text_primary))
            painter.drawText(opt.rect, Qt.AlignCenter, text)
        finally:
            painter.restore()


class _LayoutTable(QTableWidget):
    """The R×C grid. Drag source AND drop sink for well tokens."""

    def __init__(self, on_drop, on_clear_cell, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Named for the compact header / cell padding ``ui/theme.py`` gives it.
        self.setObjectName("HeatmapLayoutTable")
        self.setItemDelegate(_TokenDelegate(self))
        self._on_drop = on_drop
        self._on_clear_cell = on_clear_cell
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        # Drops also have to reach the table's viewport, not just the
        # frame, or the table swallows the WELL_MIME mid-flight.
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # Fixed cells: the panel is sized to the grid (``_fit_table``), so a
        # stretching header would only redistribute the same width.
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        # Past the size caps the grid scrolls, so every cell stays reachable.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)

        #: The size that shows the whole grid up to the caps (``_fit_table``).
        self._natural = QSize(0, 0)
        self._narrowest = 0
        #: Height the horizontal scrollbar takes when the host is narrower than
        #: the grid and the columns scroll (0 when the grid already reserves it).
        self._hbar_room = 0
        #: Column widths with the least, the comfortable and the roomiest
        #: padding, and the width the row header, frame and any vertical bar
        #: take beside them.
        self._cell_w = (0, 0, 0)
        self._chrome_w = 0
        #: The column width and spare pixels last applied (``None``: apply in
        #: full next time).
        self._applied_w: tuple[int, int] | None = None

    def _fit_to_width(self) -> None:
        """Share the width the table was given among the shown columns.

        A host wider than the grid's comfortable width (phone layout's sheet,
        held sideways) widens the cells, up to ``_ROOMY_CELL_FACTOR`` times
        their comfortable width — the table's maximum width stops it there. A
        narrower host first takes the padding back, down to ``_CELL_PAD_MIN``,
        so a whole plate still shows without scrolling; past that the columns
        scroll, and the horizontal bar is given room of its own below the rows
        rather than covering the last.
        """
        if self._natural.isEmpty():
            return
        tight, _comfy, roomy = self._cell_w
        shown = min(self.columnCount(), _MAX_SHOWN_COLS)
        width = self.width() if self.width() > 0 else self._natural.width()
        avail = width - self._chrome_w
        per = max(tight, min(roomy, avail // max(1, shown)))
        # The pixels an even share leaves over go one each to the first
        # columns, so the grid meets the frame instead of ending a strip of
        # bare header short of it. Not when scrolling: the share is the floor.
        spare = max(0, avail - per * shown) if per < roomy else 0
        spare = min(spare, shown - 1)
        hh = self.horizontalHeader()
        if self._applied_w != (per, spare):
            self._applied_w = (per, spare)
            hh.setMinimumSectionSize(per)
            hh.setDefaultSectionSize(per)
            for c in range(self.columnCount()):
                hh.resizeSection(c, per + (1 if c < spare else 0))
        h = self._natural.height()
        if self.columnCount() <= _MAX_SHOWN_COLS and per * shown > avail:
            h += self._hbar_room
        if self.minimumHeight() != h or self.maximumHeight() != h:
            self.setFixedHeight(h)

    def resizeEvent(self, event) -> None:  # Qt naming
        super().resizeEvent(event)
        self._fit_to_width()

    def _aw_rescale(self) -> None:
        """Re-measure the cells after a UI scale (``ui.phone_shell.rescale_widgets``).

        The cell font comes from the application stylesheet, which a scale
        change rewrites; the cell sizes were measured from the old font and
        would elide every token at a larger one.
        """
        _fit_table(self, self.rowCount(), self.columnCount())

    # The panel sizes the grid from its hints: wide enough for every column up
    # to the cap, never narrower than a few columns, scrolling for the rest.
    # Qt's own hint for a table is a fixed 256x192 whatever it holds.
    def sizeHint(self) -> QSize:  # Qt naming
        return QSize(self._natural) if not self._natural.isEmpty() else super().sizeHint()

    def minimumSizeHint(self) -> QSize:  # Qt naming
        if self._natural.isEmpty():
            return super().minimumSizeHint()
        return QSize(min(self._narrowest, self._natural.width()), self._natural.height())

    def mimeTypes(self) -> list[str]:
        return [WELL_MIME]

    def startDrag(self, _supportedActions) -> None:
        item = self.currentItem()
        if item is None:
            return
        token = _cell_token(item)
        if not token:
            return
        mime = QMimeData()
        mime.setData(WELL_MIME, token.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction | Qt.CopyAction)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(WELL_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(WELL_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        md = event.mimeData()
        if not md.hasFormat(WELL_MIME):
            event.ignore()
            return
        token = bytes(md.data(WELL_MIME)).decode("utf-8").strip()
        if not token:
            event.ignore()
            return
        idx = self.indexAt(event.position().toPoint())
        if not idx.isValid():
            event.ignore()
            return
        rc = (idx.row(), idx.column())
        # Use IgnoreAction so QAbstractItemView's framework code doesn't
        # try to "complete" the drop with its own model insertion after
        # we've already updated the layout — that was leaving the cell
        # blank in the table view even though the layout model and the
        # heatmap plot were correct.
        event.setDropAction(Qt.IgnoreAction)
        event.accept()
        # Defer the model update + table rebuild until Qt finishes its
        # drag/drop bookkeeping for this event. Rebuilding the table from
        # inside dropEvent left the dropped cell visually empty on some
        # platforms.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._on_drop("cell", rc, token))

    def mouseDoubleClickEvent(self, event) -> None:
        idx = self.indexAt(event.position().toPoint())
        if not idx.isValid():
            super().mouseDoubleClickEvent(event)
            return
        item = self.item(idx.row(), idx.column())
        token = _cell_token(item) if item is not None else ""
        if token:
            self._on_clear_cell(idx.row(), idx.column())
            return
        super().mouseDoubleClickEvent(event)


# ── Builder ──────────────────────────────────────────────────────────────────

def build_heatmap_layout_editor(ctx, band_parent: QWidget, dock: QWidget | None) -> QWidget:
    """Build the Layout panel into the plot's right *dock*; return its band toggle.

    The panel goes into *dock* hidden — the dock is the slide-out the
    Properties panel shares, and ``mount_widget_in_plot_dock`` shows one of
    them at a time. The returned **Layout** toggle (parented to *band_parent*,
    added by the caller with ``band.add_action``) opens and closes it, and its
    checked state follows whether the panel is on screen, so it also unchecks
    when Properties takes the dock or the panel's own ✕ closes it.
    """
    panel = _LayoutPanel(ctx, dock)
    if dock is not None and dock.layout() is not None:
        dock.layout().addWidget(panel)
    panel.hide()

    # A text toggle beside Display ▾ and Save ▾, filled like them; the theme's
    # ``toggle`` variant rings it in the accent while the panel is open.
    button = QPushButton(LAYOUT_BUTTON_TEXT, band_parent)
    button.setCheckable(True)
    button.setProperty("variant", "toggle")
    button.setToolTip(
        "Show / hide the layout editor — the grid of wells this heat map draws")
    # Click-and-act, like Properties beside it: phone layout's Controls sheet
    # puts it in the Actions grid.
    button.setProperty("bandRole", "action")
    button.clicked.connect(lambda _=False: toggle_layout_panel(ctx))
    panel.installEventFilter(_ButtonFollowsPanel(panel, button))

    reg = ctx.widgets
    reg.register("_heatmap_layout_button", button)
    refresh_heatmap_layout_panel(ctx)
    return button


class _ButtonFollowsPanel(QObject):
    """Keeps the band's Layout toggle checked exactly while the panel shows.

    The panel is hidden by more than the toggle: the ✕ in its header hides the
    dock, Properties opening hides it as a dock sibling, and in phone layout
    the sheet it is lent to closes from its dimmed strip. Each of those reaches
    the panel as a hide event (a hidden parent hides its children), so the
    toggle reads the panel's own visibility rather than tracking its clicks.
    """

    def __init__(self, panel: QWidget, button) -> None:
        super().__init__(panel)
        self._panel = panel
        self._button = button

    def eventFilter(self, obj, ev) -> bool:  # Qt naming
        if ev.type() in (QEvent.Show, QEvent.Hide):
            _sync_button(self._button, self._panel)
        return False


def _panel_shown(panel: QWidget) -> bool:
    """Whether *panel* is open: visible as far as its window is concerned.

    Relative to the window rather than ``isVisible()``, so the answer does not
    depend on whether the window itself is on screen yet (a page built and
    opened before the first show, or a window that is minimised).
    """
    return panel.isVisibleTo(panel.window())


def _sync_button(button, panel: QWidget) -> None:
    try:
        on = _panel_shown(panel)
        if button.isChecked() != on:
            blocked = button.blockSignals(True)
            try:
                button.setChecked(on)
            finally:
                button.blockSignals(blocked)
    except RuntimeError:
        swallowed("heatmap: the Layout toggle outlived its panel")


def open_layout_panel(ctx) -> None:
    """Show the Layout panel in the plot's right dock (a sheet in phone layout)."""
    from review.core.ui_helpers import mount_widget_in_plot_dock
    panel = ctx.widgets.get_or_none("_heatmap_layout_panel")
    dock = ctx.widgets.get_or_none("_heatmap_export_dock")
    if panel is None or dock is None:
        return
    mount_widget_in_plot_dock(dock, panel, theme.Metrics.dock_w)
    button = ctx.widgets.get_or_none("_heatmap_layout_button")
    if button is not None:
        _sync_button(button, panel)


def toggle_layout_panel(ctx) -> None:
    """The band toggle's action: close the panel when it shows, else open it."""
    from review.core.ui_helpers import hide_plot_dock
    panel = ctx.widgets.get_or_none("_heatmap_layout_panel")
    if panel is None:
        return
    if _panel_shown(panel):
        hide_plot_dock(ctx.widgets.get_or_none("_heatmap_export_dock"))
        button = ctx.widgets.get_or_none("_heatmap_layout_button")
        if button is not None:
            _sync_button(button, panel)
    else:
        open_layout_panel(ctx)


def open_layout_panel_if_empty(ctx) -> None:
    """Open the panel with the page when its grid is still empty, at the desk.

    An empty grid is the one state in which the page has nothing to draw and
    the panel is the only way forward, so it is put in front of the user
    rather than behind a toggle. A filled grid leaves the figure its full
    width. In phone layout nothing opens: the dock is a full-width sheet there
    and would cover the page just opened (``_phone.layout_opens_with_page``).
    """
    from review.features.heatmap._phone import layout_opens_with_page
    if not layout_opens_with_page():
        return
    if not ctx.state.dataset.well_paths:
        return
    if _ensure_sidebar_layout(ctx).cells:
        return
    open_layout_panel(ctx)


class _LayoutPanel(QWidget):
    """The Layout panel: a titled dock panel in the Properties panel's chrome.

    Header (title and ✕) over a scrolling body: the grid size, the two bulk
    actions, the grid, and one line saying how to use it. The body scrolls so
    the panel fits a phone held sideways, where the sheet it is lent to is
    shorter than the grid and its controls.
    """

    def __init__(self, ctx, dock: QWidget | None) -> None:
        super().__init__(dock)
        self.setObjectName("HeatmapLayoutPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # The same width as every other right-hand dock panel (Properties,
        # the Scatter cell viewer), so opening one after the other does not
        # move the figure's edge. Phone layout's sheet lifts it while lent.
        self.setFixedWidth(theme.Metrics.dock_w)
        sp = theme.Spacing
        root = QVBoxLayout(self)
        root.setContentsMargins(sp.sm, sp.sm, sp.sm, sp.sm)
        root.setSpacing(sp.sm)
        root.addWidget(self._build_header(ctx, dock))

        scroll = QScrollArea(self)
        scroll.setObjectName("HeatmapLayoutScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(sp.xs, 0, sp.xs, 0)
        body_l.setSpacing(sp.md)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        body_l.addWidget(self._build_size_row(ctx, body))

        actions = QWidget(body)
        actions.setObjectName("HeatmapLayoutRow")
        actions_l = QHBoxLayout(actions)
        actions_l.setContentsMargins(0, 0, 0, 0)
        actions_l.setSpacing(sp.sm)
        autopop_btn = QPushButton("Auto-populate from selection", actions)
        autopop_btn.setProperty("variant", "primary")
        autopop_btn.setToolTip(
            "Place the wells the plate shows as selected into this grid in plate "
            "order. The grid grows if it needs more cells to hold them."
        )
        autopop_btn.clicked.connect(lambda _=False: _on_autopopulate(ctx))
        actions_l.addWidget(autopop_btn, 1)
        clear_btn = QPushButton("Clear", actions)
        clear_btn.setProperty("variant", "secondary")
        clear_btn.setToolTip("Empty every cell of the grid; its size and labels stay")
        clear_btn.clicked.connect(lambda _=False: _on_clear_all(ctx))
        actions_l.addWidget(clear_btn, 0)
        body_l.addWidget(actions)

        table = _LayoutTable(
            on_drop=lambda kind, rc, tok: _on_drop_event(ctx, kind, rc, tok),
            on_clear_cell=lambda r, c: _clear_cell(ctx, r, c),
            parent=body,
        )
        table.setToolTip(
            "Drag wells from the plate map into cells. Drag between cells to "
            "move. Double-click a cell — or drop it back on the plate map — to "
            "remove its well."
        )
        body_l.addWidget(table)

        # Auto-populate first: it is the one way in that works by touch too.
        hint = QLabel(
            "Select wells on the plate and press Auto-populate, or drag wells "
            "from the plate map into a cell. Drag a cell to move it; "
            "double-click it, or drag it back onto the plate, to empty it. "
            "Double-click a row or column label on the heat map to rename it.",
            body,
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        body_l.addWidget(hint)
        body_l.addStretch(1)

        reg = ctx.widgets
        reg.register("_heatmap_layout_panel", self)
        reg.register("_heatmap_layout_table", table)
        reg.register("_heatmap_layout_autopop_btn", autopop_btn)
        reg.register("_heatmap_layout_clear_btn", clear_btn)

    def _build_header(self, ctx, dock: QWidget | None) -> QWidget:
        """The title row and its close button, as the Properties panel has."""
        from review.core.ui_helpers import hide_plot_dock
        from widgets.icon_button import IconButton
        hdr = QWidget(self)
        hdr.setObjectName("HeatmapLayoutHeader")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(theme.Spacing.xs, 0, 0, 0)
        title = QLabel(LAYOUT_BUTTON_TEXT, hdr)
        title.setObjectName("Heading")
        hl.addWidget(title)
        hl.addStretch(1)
        close_btn = IconButton("x", hdr)
        close_btn.setToolTip("Hide this panel")
        close_btn.clicked.connect(lambda _=False: hide_plot_dock(dock))
        hl.addWidget(close_btn)
        return hdr

    def _build_size_row(self, ctx, body: QWidget) -> QWidget:
        """``Grid  [rows] × [columns]`` — the grid's size, on one line."""
        row = QWidget(body)
        row.setObjectName("HeatmapLayoutRow")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.Spacing.sm)
        cap = QLabel("Grid", row)
        cap.setObjectName("Secondary")
        lay.addWidget(cap)
        rows_spin = SpinBox(row)
        rows_spin.setRange(1, _MAX_GRID)
        rows_spin.setValue(8)
        rows_spin.setToolTip("Rows in the heat-map grid")
        lay.addWidget(rows_spin)
        times = QLabel("×", row)
        times.setObjectName("Secondary")
        lay.addWidget(times)
        cols_spin = SpinBox(row)
        cols_spin.setRange(1, _MAX_GRID)
        cols_spin.setValue(12)
        cols_spin.setToolTip("Columns in the heat-map grid")
        lay.addWidget(cols_spin)
        unit = QLabel("rows × columns", row)
        unit.setObjectName("Muted")
        lay.addWidget(unit)
        lay.addStretch(1)
        reg = ctx.widgets
        reg.register("_heatmap_layout_rows_spin", rows_spin)
        reg.register("_heatmap_layout_cols_spin", cols_spin)
        rows_spin.valueChanged.connect(lambda _v: _on_size_changed(ctx))
        cols_spin.valueChanged.connect(lambda _v: _on_size_changed(ctx))
        return row


def _fit_table(table: QTableWidget, rows: int, cols: int) -> None:
    """Size the cells to the tokens they show and the grid to its cells.

    The cell font is the application stylesheet's (``ui/theme.py``,
    ``QTableWidget#HeatmapLayoutTable``), which follows the UI scale, so the
    cells are measured from that font rather than from fixed pixels: a phone's
    larger text then widens the cells with it instead of eliding every token to
    "…". A cell is as wide as the widest text in the grid, and never narrower
    than a 96-well token, so a set summarised as ``A01+3`` is not cut either.

    The grid is sized to what it holds rather than stretched without limit: an
    8×12 plate shows whole in the dock, a wider host widens the cells up to
    ``_ROOMY_CELL_FACTOR`` times, and a larger grid scrolls inside a box of at
    most ``_MAX_SHOWN_ROWS`` × ``_MAX_SHOWN_COLS`` cells.
    """
    table.ensurePolished()
    scale = theme.ui_scale()
    fm = table.fontMetrics()
    texts = [_WIDEST_TOKEN]
    for r in range(rows):
        for c in range(cols):
            item = table.item(r, c)
            if item is not None and item.text():
                texts.append(item.text())
    text_w = max(fm.horizontalAdvance(t) for t in texts)
    cell_w = text_w + round(2 * _CELL_PAD_W * scale)
    tight_w = text_w + round(2 * _CELL_PAD_MIN * scale)
    roomy_w = cell_w * _ROOMY_CELL_FACTOR
    cell_h = fm.height() + round(2 * _CELL_PAD_H * scale)
    vh, hh = table.verticalHeader(), table.horizontalHeader()
    vh.ensurePolished()
    hh.ensurePolished()
    vh.setMinimumSectionSize(cell_h)
    vh.setDefaultSectionSize(cell_h)
    for r in range(rows):
        vh.resizeSection(r, cell_h)
    hdr_fm = hh.fontMetrics()
    # Row numbers run to two digits ("32"); the header's own size hint can be
    # taken before it has polished its font, so it is measured here instead.
    row_hdr_w = hdr_fm.horizontalAdvance("88") + round(2 * _CELL_PAD_W * scale)
    col_hdr_h = hdr_fm.height() + round(2 * _CELL_PAD_H * scale)
    vh.setFixedWidth(row_hdr_w)
    hh.setFixedHeight(col_hdr_h)
    frame = 2 * table.frameWidth()
    shown_cols = min(cols, _MAX_SHOWN_COLS)
    grid_w = shown_cols * cell_w
    grid_h = min(rows, _MAX_SHOWN_ROWS) * cell_h
    # Room for the scrollbar a capped axis brings with it, so the last column
    # or row is not drawn under it. The bar's own hint, not the style's metric:
    # phone layout's stylesheet widens the bars to a thumb's width.
    bar_w = table.verticalScrollBar().sizeHint().width()
    bar_h = table.horizontalScrollBar().sizeHint().height()
    extra_w = bar_w if rows > _MAX_SHOWN_ROWS else 0
    extra_h = bar_h if cols > _MAX_SHOWN_COLS else 0
    chrome_w = row_hdr_w + frame + extra_w
    table._natural = QSize(chrome_w + grid_w, col_hdr_h + grid_h + frame + extra_h)
    table._narrowest = chrome_w + min(cols, _MIN_VISIBLE_COLS) * tight_w
    table._cell_w = (tight_w, cell_w, roomy_w)
    table._chrome_w = chrome_w
    table._hbar_room = 0 if extra_h else bar_h
    # Forget the last width share so the new cell sizes are applied in full.
    table._applied_w = None
    table._fit_to_width()
    table.setMaximumWidth(chrome_w + shown_cols * roomy_w)
    table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    table.updateGeometry()


# ── State helpers ────────────────────────────────────────────────────────────

def _ensure_sidebar_layout(ctx) -> HeatmapLayout:
    """Return (creating if needed) the single ``Sidebar`` layout."""
    layouts: list[HeatmapLayout] = list(ctx.runtime.heatmap.layouts or [])
    for lay in layouts:
        if lay.name == SIDEBAR_LAYOUT_NAME:
            return lay
    lay = HeatmapLayout(name=SIDEBAR_LAYOUT_NAME, rows=8, cols=12)
    layouts.append(lay)
    ctx.runtime.heatmap.layouts = layouts
    ctx.state.heatmap.active_layout_name = SIDEBAR_LAYOUT_NAME
    return lay


def _find_cell_for_token(layout: HeatmapLayout, token: str) -> tuple[int, int] | None:
    for (r, c), wells in layout.cells.items():
        if token in wells:
            return (r, c)
    return None


# ── Refresh + drop handlers ──────────────────────────────────────────────────

def refresh_heatmap_layout_panel(ctx) -> None:
    """Re-render the table from the editor's heatmap layout.

    A no-op until the Heat Map tab has been built: the panel is built with the
    tab, lazily, and every caller may run before that.
    """
    table: _LayoutTable = ctx.widgets.get_or_none("_heatmap_layout_table")
    rows_spin: QSpinBox = ctx.widgets.get_or_none("_heatmap_layout_rows_spin")
    cols_spin: QSpinBox = ctx.widgets.get_or_none("_heatmap_layout_cols_spin")
    if table is None:
        return

    layout = _ensure_sidebar_layout(ctx)

    if rows_spin is not None and rows_spin.value() != layout.rows:
        rows_spin.blockSignals(True)
        try:
            rows_spin.setValue(layout.rows)
        finally:
            rows_spin.blockSignals(False)
    if cols_spin is not None and cols_spin.value() != layout.cols:
        cols_spin.blockSignals(True)
        try:
            cols_spin.setValue(layout.cols)
        finally:
            cols_spin.blockSignals(False)

    table.blockSignals(True)
    try:
        table.clear()
        table.setRowCount(layout.rows)
        table.setColumnCount(layout.cols)
        row_labels = layout.row_labels or [str(i + 1) for i in range(layout.rows)]
        col_labels = layout.col_labels or [str(i + 1) for i in range(layout.cols)]
        table.setHorizontalHeaderLabels(col_labels[: layout.cols])
        table.setVerticalHeaderLabels(row_labels[: layout.rows])
        for r in range(layout.rows):
            for c in range(layout.cols):
                wells = list(layout.cells.get((r, c), []))
                token = wells[0] if wells else ""
                item = QTableWidgetItem(_cell_display(wells))
                item.setData(_CELL_TOKEN_ROLE, token)
                if len(wells) > 1:
                    item.setToolTip(", ".join(wells))
                item.setTextAlignment(Qt.AlignCenter)
                # Every cell accepts drops (so the user can drop wells from
                # the sidebar plate into empty cells too — previously empty
                # cells lacked ``ItemIsDropEnabled`` and silently rejected
                # the QDrag). Populated cells additionally support drag-out
                # for cell ↔ cell rearrangement.
                flags = (Qt.ItemIsSelectable | Qt.ItemIsEnabled
                         | Qt.ItemIsDropEnabled)
                if token:
                    flags |= Qt.ItemIsDragEnabled
                item.setFlags(flags)
                table.setItem(r, c, item)
    finally:
        table.blockSignals(False)
    _fit_table(table, layout.rows, layout.cols)


def _on_size_changed(ctx) -> None:
    rows_spin: QSpinBox = ctx.widgets.get_or_none("_heatmap_layout_rows_spin")
    cols_spin: QSpinBox = ctx.widgets.get_or_none("_heatmap_layout_cols_spin")
    if rows_spin is None or cols_spin is None:
        return
    layout = _ensure_sidebar_layout(ctx)
    new_rows = int(rows_spin.value())
    new_cols = int(cols_spin.value())
    if new_rows == layout.rows and new_cols == layout.cols:
        return
    # Drop labels when shrinking past their length so resize stays clean.
    if layout.row_labels and new_rows < len(layout.row_labels):
        layout.row_labels = layout.row_labels[:new_rows]
    if layout.col_labels and new_cols < len(layout.col_labels):
        layout.col_labels = layout.col_labels[:new_cols]
    layout.resize(new_rows, new_cols)
    _persist_and_redraw(ctx)
    refresh_heatmap_layout_panel(ctx)


def _on_drop_event(ctx, kind: str, rc: tuple[int, int] | None, token: str) -> None:
    """Handle a successful drop. Updates state and re-renders both widgets."""
    layout = _ensure_sidebar_layout(ctx)
    src = _find_cell_for_token(layout, token)
    # A cell can hold a whole replicate set (auto-populate builds those when
    # Rep-set avg is on), and the drag only carries its first well. Moving a
    # cell moves everything in it — assigning the bare token would silently drop
    # the rest of the set. A drag from the plate palette has no source cell, so
    # there the token is the whole payload.
    moving = list(layout.cells.get(src, [])) if src is not None else [token]
    if kind == "palette":
        if src is not None:
            layout.assign(src[0], src[1], [])
    elif kind == "cell" and rc is not None:
        target_r, target_c = rc
        # If the target already holds a well, swap with the source (when
        # source was a cell) or push the existing tenant to the palette.
        existing = list(layout.cells.get((target_r, target_c), []))
        if src is not None and src != (target_r, target_c):
            layout.assign(src[0], src[1], existing)  # tenant goes back to source
        layout.assign(target_r, target_c, moving)
    _persist_and_redraw(ctx)
    refresh_heatmap_layout_panel(ctx)


def _clear_cell(ctx, r: int, c: int) -> None:
    layout = _ensure_sidebar_layout(ctx)
    if (r, c) in layout.cells:
        layout.assign(r, c, [])
        _persist_and_redraw(ctx)
        refresh_heatmap_layout_panel(ctx)


def _on_clear_all(ctx) -> None:
    """Empty every cell; the grid's size and its row / column names stay."""
    layout = _ensure_sidebar_layout(ctx)
    if not layout.cells:
        return
    layout.cells.clear()
    _persist_and_redraw(ctx)
    refresh_heatmap_layout_panel(ctx)


def _sorted_by_plate(ctx, wells) -> list[str]:
    """*wells* in plate order (A01, A02, …), tolerating an unparseable token."""
    src = list(wells)
    parse_rc = getattr(ctx.window, "_parse_rc", None) if hasattr(ctx, "window") else None
    if callable(parse_rc):
        try:
            return sorted(src, key=parse_rc)
        except Exception:
            swallowed("heatmap: plate-order sort; falling back to token order")
    return sorted(src)


def _autopopulate_cells(ctx) -> list[list[str]]:
    """The list of cells "Auto-populate from selection" should build, in order.

    **What the selection *is* comes from
    ``data_loading.plot_group_well_lists``, never from a second reading of the
    stores.** That function's own docstring says which wells a plot is about is
    decided there and nowhere else, and warns that a second implementation of
    the rule "produces a plausible result with the wrong wells in it: right
    schema, right-looking counts, no error". This tab was that second
    implementation, and it went wrong in exactly that way on a **mixed plate**.

    Replicate mode does not mean every well is in a group. The plate expresses
    its active set as *the visible groups' wells **plus** the wells toggled on
    individually* (``selection.active_solo_wells``) — that is what
    ``on_sidebar_plate_well_activated`` writes when a click lands on a well no
    group claims, and what the plate paints as selected. Reading the replicate
    sets alone answered with the grouped half and dropped the other, so a plate
    of two replicate pairs and three solo wells auto-populated four cells out
    of seven: the solo wells were simply not in the grid, with nothing said and
    a full-looking layout to show for it.

    The **Rep-set avg** toggle still decides the *shape* of the plan, because
    it is what decides whether a cell means a set or a well:

    * checked — one cell per entry: a replicate set becomes ONE cell holding
      all of its loaded wells and shows the set's average, and a solo well
      becomes a one-well cell beside it, which is the same mixture of
      set-averages and single wells the bar chart draws for that plate.
    * unchecked — every well of every entry becomes its own cell, in plate
      order. Grouping them anyway put a whole set in one cell, which the
      layout grid can only render as its first well and the map drew as a
      pooled mean — the toggle looked like it did nothing and the other
      members looked dropped.

    With no groups defined the producer answers with one entry per selected
    well, which is the plain-mode behaviour unchanged; ``fallback_to_all=False``
    keeps "nothing selected" meaning an empty plan rather than the whole plate.
    """
    from review.data.data_loading import plot_group_well_lists

    entries = [wells for _name, wells, _rset
               in plot_group_well_lists(ctx, fallback_to_all=False) if wells]
    if not entries:
        return []
    if bool(ctx.state.heatmap.repset_avg):
        return [list(wells) for wells in entries]
    return [[w] for w in _sorted_by_plate(ctx, dict.fromkeys(
        w for wells in entries for w in wells))]


def _on_autopopulate(ctx) -> None:
    """Lay the current selection into the grid, one cell per selected entry.

    Grows the grid (capped at 32×32, the spinbox limit) when the selection
    has more entries than the current grid can hold. See
    :func:`_autopopulate_cells` for what counts as an entry in each mode.
    """
    layout = _ensure_sidebar_layout(ctx)
    cells = _autopopulate_cells(ctx)
    if not cells:
        return

    rows, cols = layout.rows, layout.cols
    capacity = rows * cols
    needed = len(cells)
    if needed > capacity:
        # Grow rows so capacity ≥ needed; capped to match the spinbox.
        new_rows = min(_MAX_GRID, max(rows, -(-needed // max(1, cols))))
        if new_rows * cols < needed:
            # Still not enough; grow cols too.
            new_cols = min(_MAX_GRID, max(cols, -(-needed // max(1, new_rows))))
            layout.resize(new_rows, new_cols)
        else:
            layout.resize(new_rows, cols)
        rows, cols = layout.rows, layout.cols

    layout.cells.clear()
    for i, cell_wells in enumerate(cells[: rows * cols]):
        r = i // cols
        c = i % cols
        layout.assign(r, c, list(cell_wells))

    _persist_and_redraw(ctx)
    refresh_heatmap_layout_panel(ctx)


def _persist_and_redraw(ctx) -> None:
    try:
        ctx.persistence.schedule_save("heatmap_layouts")
    except Exception:
        swallowed()  # was: pass (silent except)
    if ctx.widgets.get_or_none("_heatmap_canvas") is not None:
        try:
            from review.features.heatmap.controller import redraw_heatmap
            redraw_heatmap(ctx)
        except Exception:
            swallowed()  # was: pass (silent except)
