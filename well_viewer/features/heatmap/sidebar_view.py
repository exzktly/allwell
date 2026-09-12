"""Heat-map layout configurator — sidebar variant.

Renders directly under the standard well picker on the Heat Map tab. The
user picks an R×C grid size, drags wells from the *sidebar plate-map*
into table cells, moves wells between cells by dragging, and clears a
cell by double-clicking it (or by dragging the well back onto the
sidebar plate-map). The sidebar buttons are the canonical drag source
when the Heat Map tab is active — see
``runtime_app._sync_heatmap_well_drag_mode``.

State of truth: ``app._heatmap_layouts``. The sidebar maintains exactly
one layout named ``SIDEBAR_LAYOUT_NAME`` and synchronises the table
widget to its ``cells`` dict on every change. Dragging a well in puts
that one well in a cell, but a cell can hold several — "Auto-populate
from selection" builds one cell per *entry* when **Rep-set avg** is on,
and an entry is a visible replicate set **or** a well the user soloed
onto the plot, because a plate is very often a mixture of the two.
Those cells show as ``A01+3`` (the members in the tooltip) and
move as a unit; the well token behind a cell is carried in
``_CELL_TOKEN_ROLE`` so the summary text never reaches the model.
"""

from __future__ import annotations


from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from well_viewer.features.heatmap.models import HeatmapLayout
from core.safe import swallowed


SIDEBAR_LAYOUT_NAME = "Sidebar"
from well_viewer.core.dnd import WELL_MIME  # shared drag/drop token
from widgets.separator import build_hline_separator

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_TABLE_MIN_H = 240


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
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.verticalHeader().setDefaultSectionSize(22)
        self.horizontalHeader().setDefaultSectionSize(28)
        # The sidebar slot the table lives in is short — when the user grows
        # the grid past ~7 rows the cells stop being visible. Force the
        # vertical scrollbar on so the user can always reach every cell.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)

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

def build_heatmap_layout_sidebar(ctx, parent: QWidget) -> QWidget:
    app = ctx.window  # transitional: widget-building body still stamps app._heatmap_*
    """Build the inline configurator and attach it to *parent*'s layout.

    Returns the outer frame widget so the caller can toggle its visibility.
    """
    # ``outer`` is the public frame the caller toggles visible per-tab; its
    # content lives inside a QScrollArea so the matrix stays reachable even
    # when the sidebar above eats most of the vertical room.
    outer = QWidget(parent)
    outer_layout = QVBoxLayout(outer)
    outer_layout.setContentsMargins(0, 4, 0, 4)
    outer_layout.setSpacing(0)
    outer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.MinimumExpanding)

    outer_layout.addWidget(build_hline_separator(outer))

    scroll = QScrollArea(outer)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    outer_layout.addWidget(scroll, 1)

    body = QWidget(scroll)
    body_layout = QVBoxLayout(body)
    body_layout.setContentsMargins(0, 0, 0, 0)
    body_layout.setSpacing(2)
    scroll.setWidget(body)

    # Sentence case, like every other header sharing this style: the all-caps
    # spelling was compensating for the old caption-sized eyebrow treatment and
    # reads as shouting now the headers are a real heading size.
    hdr = QLabel("Heatmap layout", body)
    hdr.setObjectName("SidebarHeader")
    hdr.setProperty("role", "header")
    body_layout.addWidget(hdr)

    # Size controls
    size_row = QWidget(body)
    size_layout = QHBoxLayout(size_row)
    size_layout.setContentsMargins(6, 0, 6, 0)
    size_layout.setSpacing(4)
    size_layout.addWidget(QLabel("Rows:", size_row))
    rows_spin = QSpinBox(size_row)
    rows_spin.setRange(1, 32)
    rows_spin.setValue(8)
    size_layout.addWidget(rows_spin)
    size_layout.addWidget(QLabel("Cols:", size_row))
    cols_spin = QSpinBox(size_row)
    cols_spin.setRange(1, 32)
    cols_spin.setValue(12)
    size_layout.addWidget(cols_spin)
    size_layout.addStretch(1)
    body_layout.addWidget(size_row)

    # Auto-populate button
    autopop_btn = QPushButton("Auto-populate from selection", body)
    autopop_btn.setProperty("variant", "secondary")
    autopop_btn.setToolTip(
        "Place the currently selected wells into this grid in plate order. "
        "Grid will grow if needed to fit all selected wells."
    )
    autopop_btn.clicked.connect(lambda _=False: _on_autopopulate(ctx))
    body_layout.addWidget(autopop_btn)

    # Hint label
    hint = QLabel(
        "Drag wells from the sidebar plate map above into cells. Drag "
        "between cells to move. Double-click a cell — or drop it back on "
        "the plate map — to remove its well.",
        body,
    )
    hint.setObjectName("Muted")
    hint.setWordWrap(True)
    body_layout.addWidget(hint)

    # Layout table
    table = _LayoutTable(
        on_drop=lambda kind, rc, tok: _on_drop_event(ctx, kind, rc, tok),
        on_clear_cell=lambda r, c: _clear_cell(ctx, r, c),
        parent=body,
    )
    # Tall enough that 8 rows (the default) are fully visible without
    # scroll, with the table's own scrollbar taking over past that.
    table.setMinimumHeight(_TABLE_MIN_H)
    table.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.MinimumExpanding)
    body_layout.addWidget(table, 1)

    # Register widget refs (also stamps ``app._heatmap_sidebar_*`` so legacy
    # attribute readers keep resolving) so ``ctx.widgets`` can find them.
    reg = ctx.widgets
    app._heatmap_sidebar_frame = reg.register("_heatmap_sidebar_frame", outer)
    reg.register("_heatmap_sidebar_table", table)
    reg.register("_heatmap_sidebar_rows_spin", rows_spin)
    reg.register("_heatmap_sidebar_cols_spin", cols_spin)

    rows_spin.valueChanged.connect(lambda _v: _on_size_changed(ctx))
    cols_spin.valueChanged.connect(lambda _v: _on_size_changed(ctx))

    parent_layout = parent.layout()
    if parent_layout is None:
        parent_layout = QVBoxLayout(parent)
    # ``stretch=1`` so this block claims leftover sidebar height when the
    # Heat Map tab is active — otherwise the sidebar's trailing
    # ``addStretch(1)`` would grab everything and the matrix gets squashed.
    parent_layout.addWidget(outer, 1)

    refresh_heatmap_layout_sidebar(ctx)
    return outer


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

def refresh_heatmap_layout_sidebar(ctx) -> None:
    """Re-render the table from the sidebar heatmap layout."""
    table: _LayoutTable = ctx.widgets.get_or_none("_heatmap_sidebar_table")
    rows_spin: QSpinBox = ctx.widgets.get_or_none("_heatmap_sidebar_rows_spin")
    cols_spin: QSpinBox = ctx.widgets.get_or_none("_heatmap_sidebar_cols_spin")
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

    # Shrink the cell font when the grid grows past the sidebar's natural
    # column width — keeps wells like ``H12`` legible at 12+ cols. Sizes
    # were knocked down ~2 pt across the board so cell text stays inside
    # the cell at narrow sidebar widths.
    cell_font = table.font()
    if layout.cols <= 8:
        cell_font.setPointSize(8)
    elif layout.cols <= 12:
        cell_font.setPointSize(7)
    elif layout.cols <= 16:
        cell_font.setPointSize(6)
    elif layout.cols <= 24:
        cell_font.setPointSize(5)
    else:
        cell_font.setPointSize(5)
    table.setFont(cell_font)
    hdr_font = table.horizontalHeader().font()
    hdr_font.setPointSize(max(6, cell_font.pointSize() - 1))
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


def _on_size_changed(ctx) -> None:
    rows_spin: QSpinBox = getattr(ctx.window, "_heatmap_sidebar_rows_spin", None)
    cols_spin: QSpinBox = getattr(ctx.window, "_heatmap_sidebar_cols_spin", None)
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
    refresh_heatmap_layout_sidebar(ctx)


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
    refresh_heatmap_layout_sidebar(ctx)


def _clear_cell(ctx, r: int, c: int) -> None:
    layout = _ensure_sidebar_layout(ctx)
    if (r, c) in layout.cells:
        layout.assign(r, c, [])
        _persist_and_redraw(ctx)
        refresh_heatmap_layout_sidebar(ctx)


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
      sidebar grid can only render as its first well and the map drew as a
      pooled mean — the toggle looked like it did nothing and the other
      members looked dropped.

    With no groups defined the producer answers with one entry per selected
    well, which is the plain-mode behaviour unchanged; ``fallback_to_all=False``
    keeps "nothing selected" meaning an empty plan rather than the whole plate.
    """
    from well_viewer.data.data_loading import plot_group_well_lists

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
        # Grow rows so capacity ≥ needed; cap at 32 to match the spinbox.
        new_rows = min(32, max(rows, -(-needed // max(1, cols))))
        if new_rows * cols < needed:
            # Still not enough; grow cols too.
            new_cols = min(32, max(cols, -(-needed // max(1, new_rows))))
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
    refresh_heatmap_layout_sidebar(ctx)


def _persist_and_redraw(ctx) -> None:
    try:
        ctx.persistence.schedule_save("heatmap_layouts")
    except Exception:
        swallowed()  # was: pass (silent except)
    if ctx.widgets.get_or_none("_heatmap_canvas") is not None:
        try:
            from well_viewer.features.heatmap.controller import redraw_heatmap
            redraw_heatmap(ctx)
        except Exception:
            swallowed()  # was: pass (silent except)
