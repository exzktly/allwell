"""Heat-map layout editor — the grid at the right of the Heat Map's control band.

The Heat Map tab adds this panel to its band's action zone, so it sits at the
right-hand end of the controls row. The user picks an R×C grid size, drags
wells from the *rail plate map* (the sidebar's well picker) into table cells,
moves wells between cells by dragging, and clears a cell by double-clicking it
(or by dragging the well back onto the plate map). The rail plate is the
canonical drag source while the Heat Map tab is focused — the descriptor's
``plate_drag_source``, applied by ``core/tab_controller.sync_rail_plate_drag_mode``.

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


from PySide6.QtCore import QMimeData, QSize, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from review.features.heatmap.models import HeatmapLayout
from core.safe import swallowed


from review.core.dnd import WELL_MIME  # shared drag/drop token
from ui import theme
from widgets.flow_layout import FlowLayout, sync_flow_height
from widgets.spin_box import SpinBox

#: The one layout this editor owns. Persisted under this name, so it stays
#: ``"Sidebar"`` although the editor now sits in the control band.
SIDEBAR_LAYOUT_NAME = "Sidebar"

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
#: One grid cell. Wide enough for a well token (``H12``) at the cell font.
_CELL_W = 26
_CELL_H = 18
#: The largest the grid is drawn before it scrolls: a whole 8×12 plate fits,
#: and a 32×32 grid does not push the plot off the page.
_GRID_MAX_W = 12 * _CELL_W
_GRID_MAX_H = 8 * _CELL_H
#: The grid-size spin boxes' limit (and Auto-populate's growth cap).
_MAX_GRID = 32
#: Fewest columns the grid shrinks to in a narrow host (phone layout's Controls
#: sheet) before it scrolls sideways instead.
_MIN_VISIBLE_COLS = 4


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


class _LayoutTable(QTableWidget):
    """The R×C grid. Drag source AND drop sink for well tokens."""

    def __init__(self, on_drop, on_clear_cell, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Named for the compact header / cell padding ``ui/theme.py`` gives it.
        self.setObjectName("HeatmapLayoutTable")
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

    # The band's action zone and the phone sheet both size the grid from its
    # hints: wide enough for every column up to the cap, never narrower than a
    # few columns, scrolling for the rest. Qt's own hint for a table is a fixed
    # 256x192 whatever it holds.
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

def build_heatmap_layout_panel(ctx, parent: QWidget) -> QWidget:
    """Build the compact layout editor the Heat Map's control band carries.

    Returns the panel; the caller adds it to the band's action zone, so it sits
    at the right of the row beside Display / Save / Properties and is shown and
    hidden with the rest of the Heat Map's slot. Nothing here adds it to a
    layout. The panel is taller than one band row, so the band grows to hold it
    while the Heat Map is the leaf on screen (the action zone is top-aligned,
    which keeps the buttons beside it on the first line).
    """
    panel = QWidget(parent)
    panel.setObjectName("HeatmapLayoutPanel")
    # Never wider than the grid and its header line want, and never squeezed
    # vertically: the band reads the action zone's height hint, and a panel
    # given less would clip grid rows.
    panel.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(theme.Spacing.xs)

    # One header line: what the grid is, its size, and the one-click fill. It
    # is a flow, so a host narrower than the line (phone layout's Controls
    # sheet) wraps the button under the size controls instead of clipping it.
    head = _HeaderLine(panel)
    head_l = FlowLayout(head)
    head_l.setContentsMargins(0, 0, 0, 0)
    head_l.setSpacing(theme.Spacing.xs)
    sync_flow_height(head)
    size_group = QWidget(head)
    size_l = QHBoxLayout(size_group)
    size_l.setContentsMargins(0, 0, 0, 0)
    size_l.setSpacing(theme.Spacing.xs)
    cap = QLabel("Layout", size_group)
    cap.setObjectName("Muted")
    size_l.addWidget(cap)
    rows_spin = SpinBox(size_group)
    rows_spin.setRange(1, _MAX_GRID)
    rows_spin.setValue(8)
    rows_spin.setToolTip("Rows in the heat-map grid")
    size_l.addWidget(rows_spin)
    size_l.addWidget(QLabel("×", size_group))
    cols_spin = SpinBox(size_group)
    cols_spin.setRange(1, _MAX_GRID)
    cols_spin.setValue(12)
    cols_spin.setToolTip("Columns in the heat-map grid")
    size_l.addWidget(cols_spin)
    head_l.addWidget(size_group)
    autopop_btn = QPushButton("Auto-populate from selection", head)
    autopop_btn.setProperty("variant", "secondary")
    autopop_btn.setToolTip(
        "Place the currently selected wells into this grid in plate order. "
        "Grid will grow if needed to fit all selected wells."
    )
    autopop_btn.clicked.connect(lambda _=False: _on_autopopulate(ctx))
    head_l.addWidget(autopop_btn)
    lay.addWidget(head)

    # The grid itself. Its instructions live in the tooltip: a band row has no
    # room for a paragraph, and the grid is self-evident once a well is in it.
    table = _LayoutTable(
        on_drop=lambda kind, rc, tok: _on_drop_event(ctx, kind, rc, tok),
        on_clear_cell=lambda r, c: _clear_cell(ctx, r, c),
        parent=panel,
    )
    table.setToolTip(
        "Drag wells from the plate map in the sidebar into cells. Drag "
        "between cells to move. Double-click a cell — or drop it back on "
        "the plate map — to remove its well."
    )
    lay.addWidget(table)

    reg = ctx.widgets
    reg.register("_heatmap_layout_panel", panel)
    reg.register("_heatmap_layout_table", table)
    reg.register("_heatmap_layout_rows_spin", rows_spin)
    reg.register("_heatmap_layout_cols_spin", cols_spin)

    rows_spin.valueChanged.connect(lambda _v: _on_size_changed(ctx))
    cols_spin.valueChanged.connect(lambda _v: _on_size_changed(ctx))

    refresh_heatmap_layout_panel(ctx)
    return panel


class _HeaderLine(QWidget):
    """The editor's header line: hinted at its one-line width, wrapping below it.

    A ``FlowLayout`` hints only its widest item, so a panel sized from its
    children would always be given too little width for one line and wrap at
    the desk too. This hints the line laid end to end; a narrower host still
    gets the wrap.
    """

    def sizeHint(self) -> QSize:  # Qt naming
        lay = self.layout()
        if lay is None:
            return super().sizeHint()
        items = [lay.itemAt(i) for i in range(lay.count())]
        shown = [it for it in items if it.widget() is None or not it.widget().isHidden()]
        w = sum(it.sizeHint().width() for it in shown)
        w += max(0, len(shown) - 1) * lay.horizontalSpacing()
        h = max((it.sizeHint().height() for it in shown), default=0)
        return QSize(w, h)


def _fit_table(table: QTableWidget, rows: int, cols: int) -> None:
    """Size the grid to its cells, up to the caps; scroll past them.

    The panel lives in a row of controls, so it is sized to what it holds
    rather than stretched: an 8x12 plate shows whole, and a 32x32 grid scrolls
    inside a box no larger than the caps instead of pushing the plot off the
    page. Cell sizes follow the UI scale (1.0 at the desk), like the cell font
    ``refresh_heatmap_layout_panel`` sets, so phone layout's larger text still
    fits its cells.
    """
    scale = theme.ui_scale()
    cell_w, cell_h = round(_CELL_W * scale), round(_CELL_H * scale)
    max_w, max_h = round(_GRID_MAX_W * scale), round(_GRID_MAX_H * scale)
    vh, hh = table.verticalHeader(), table.horizontalHeader()
    vh.setMinimumSectionSize(cell_h)
    hh.setMinimumSectionSize(cell_w)
    vh.setDefaultSectionSize(cell_h)
    hh.setDefaultSectionSize(cell_w)
    frame = 2 * table.frameWidth()
    grid_w = min(cols * cell_w, max_w)
    grid_h = min(rows * cell_h, max_h)
    # Room for the scrollbar the capped axis brings with it, so the last
    # column / row is not hidden under it.
    bar = table.style().pixelMetric(table.style().PixelMetric.PM_ScrollBarExtent)
    extra_w = bar if rows * cell_h > max_h else 0
    extra_h = bar if cols * cell_w > max_w else 0
    # The row header holds at most two digits ("32"); its size hint can be
    # taken before the header has polished its font, so floor it to that.
    row_hdr_w = max(vh.sizeHint().width(),
                    vh.fontMetrics().horizontalAdvance("88") + 2 * theme.Spacing.xs)
    vh.setFixedWidth(row_hdr_w)
    chrome_w = row_hdr_w + frame + extra_w
    natural = QSize(chrome_w + grid_w, hh.sizeHint().height() + grid_h + frame + extra_h)
    table._natural = natural
    table._narrowest = chrome_w + min(cols, _MIN_VISIBLE_COLS) * cell_w + bar
    table.setFixedHeight(natural.height())
    table.setMaximumWidth(natural.width())
    table.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
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

    # Shrink the cell font as the grid gains columns — keeps wells like
    # ``H12`` inside their cell at 12+ cols.
    # The sizes are the desk's, times the UI scale (1.0 at the desk): phone
    # layout scales a font a widget set on itself, and reads one set while its
    # larger text is on as already scaled — an unscaled 8 pt set there would be
    # divided down to about 3 pt on the way back to the desk.
    cell_pt = 8 if layout.cols <= 8 else 7 if layout.cols <= 12 else (
        6 if layout.cols <= 16 else 5)
    scale = theme.ui_scale()
    cell_font = table.font()
    cell_font.setPointSizeF(cell_pt * scale)
    table.setFont(cell_font)
    hdr_font = table.horizontalHeader().font()
    hdr_font.setPointSizeF(max(6, cell_pt - 1) * scale)
    table.horizontalHeader().setFont(hdr_font)
    table.verticalHeader().setFont(hdr_font)

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
