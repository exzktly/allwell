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
widget to its ``cells`` dict on every change. Each cell holds at most
one well token in this UI; the underlying model still permits
multi-well cells, which remain accessible to programmatic / future
editor uses.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

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

from well_viewer.heatmap_models import HeatmapLayout


SIDEBAR_LAYOUT_NAME = "Sidebar"
WELL_MIME = "application/x-well-token"


# ── Drag-aware widgets ───────────────────────────────────────────────────────

class _LayoutTable(QTableWidget):
    """The R×C grid. Drag source AND drop sink for well tokens."""

    def __init__(self, on_drop, on_clear_cell, parent: Optional[QWidget] = None) -> None:
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

    def mimeTypes(self) -> List[str]:
        return [WELL_MIME]

    def startDrag(self, _supportedActions) -> None:
        item = self.currentItem()
        if item is None:
            return
        token = (item.text() or "").strip()
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
        token = (item.text() if item else "").strip()
        if token:
            self._on_clear_cell(idx.row(), idx.column())
            return
        super().mouseDoubleClickEvent(event)


# ── Builder ──────────────────────────────────────────────────────────────────

def build_heatmap_layout_sidebar(app, parent: QWidget) -> QWidget:
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

    sep = QFrame(outer)
    sep.setFrameShape(QFrame.HLine)
    sep.setFixedHeight(1)
    outer_layout.addWidget(sep)

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

    hdr = QLabel("HEATMAP LAYOUT", body)
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
    autopop_btn.clicked.connect(lambda _=False: _on_autopopulate(app))
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
        on_drop=lambda kind, rc, tok: _on_drop_event(app, kind, rc, tok),
        on_clear_cell=lambda r, c: _clear_cell(app, r, c),
        parent=body,
    )
    # Tall enough that 8 rows (the default) are fully visible without
    # scroll, with the table's own scrollbar taking over past that.
    table.setMinimumHeight(240)
    table.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.MinimumExpanding)
    body_layout.addWidget(table, 1)

    # Stash widget refs on the app so callbacks/refresh can find them.
    app._heatmap_sidebar_frame = outer
    app._heatmap_sidebar_table = table
    app._heatmap_sidebar_rows_spin = rows_spin
    app._heatmap_sidebar_cols_spin = cols_spin

    rows_spin.valueChanged.connect(lambda _v: _on_size_changed(app))
    cols_spin.valueChanged.connect(lambda _v: _on_size_changed(app))

    parent_layout = parent.layout()
    if parent_layout is None:
        parent_layout = QVBoxLayout(parent)
    # ``stretch=1`` so this block claims leftover sidebar height when the
    # Heat Map tab is active — otherwise the sidebar's trailing
    # ``addStretch(1)`` would grab everything and the matrix gets squashed.
    parent_layout.addWidget(outer, 1)

    refresh_heatmap_layout_sidebar(app)
    return outer


# ── State helpers ────────────────────────────────────────────────────────────

def _ensure_sidebar_layout(app) -> HeatmapLayout:
    """Return (creating if needed) the single ``Sidebar`` layout."""
    layouts: List[HeatmapLayout] = list(getattr(app, "_heatmap_layouts", []) or [])
    for lay in layouts:
        if lay.name == SIDEBAR_LAYOUT_NAME:
            return lay
    lay = HeatmapLayout(name=SIDEBAR_LAYOUT_NAME, rows=8, cols=12)
    layouts.append(lay)
    app._heatmap_layouts = layouts
    app._active_heatmap_layout_name = SIDEBAR_LAYOUT_NAME
    return lay


def _find_cell_for_token(layout: HeatmapLayout, token: str) -> Optional[Tuple[int, int]]:
    for (r, c), wells in layout.cells.items():
        if token in wells:
            return (r, c)
    return None


# ── Refresh + drop handlers ──────────────────────────────────────────────────

def refresh_heatmap_layout_sidebar(app) -> None:
    """Re-render the table from ``app._heatmap_layouts``."""
    table: _LayoutTable = getattr(app, "_heatmap_sidebar_table", None)
    rows_spin: QSpinBox = getattr(app, "_heatmap_sidebar_rows_spin", None)
    cols_spin: QSpinBox = getattr(app, "_heatmap_sidebar_cols_spin", None)
    if table is None:
        return

    layout = _ensure_sidebar_layout(app)

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
                wells = layout.cells.get((r, c), [])
                token = wells[0] if wells else ""
                item = QTableWidgetItem(token)
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


def _on_size_changed(app) -> None:
    rows_spin: QSpinBox = getattr(app, "_heatmap_sidebar_rows_spin", None)
    cols_spin: QSpinBox = getattr(app, "_heatmap_sidebar_cols_spin", None)
    if rows_spin is None or cols_spin is None:
        return
    layout = _ensure_sidebar_layout(app)
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
    _persist_and_redraw(app)
    refresh_heatmap_layout_sidebar(app)


def _on_drop_event(app, kind: str, rc: Optional[Tuple[int, int]], token: str) -> None:
    """Handle a successful drop. Updates state and re-renders both widgets."""
    layout = _ensure_sidebar_layout(app)
    src = _find_cell_for_token(layout, token)
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
        layout.assign(target_r, target_c, [token])
    _persist_and_redraw(app)
    refresh_heatmap_layout_sidebar(app)


def _clear_cell(app, r: int, c: int) -> None:
    layout = _ensure_sidebar_layout(app)
    if (r, c) in layout.cells:
        layout.assign(r, c, [])
        _persist_and_redraw(app)
        refresh_heatmap_layout_sidebar(app)


def _on_autopopulate(app) -> None:
    """Lay the currently selected wells into the grid in plate order.

    Grows the grid (capped at 32×32, the spinbox limit) when the selection
    has more wells than the current grid can hold.
    """
    layout = _ensure_sidebar_layout(app)
    selected = list(app._app_state.selection.selected_wells or [])
    if not selected:
        return
    parse_rc = getattr(app, "_parse_rc", None)
    if callable(parse_rc):
        try:
            wells = sorted(selected, key=parse_rc)
        except Exception:
            wells = sorted(selected)
    else:
        wells = sorted(selected)

    rows, cols = layout.rows, layout.cols
    capacity = rows * cols
    needed = len(wells)
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
    for i, w in enumerate(wells[: rows * cols]):
        r = i // cols
        c = i % cols
        layout.assign(r, c, [w])

    _persist_and_redraw(app)
    refresh_heatmap_layout_sidebar(app)


def _persist_and_redraw(app) -> None:
    try:
        app._persistence.schedule_save("heatmap_layouts")
    except Exception:
        pass
    if hasattr(app, "_heatmap_canvas"):
        try:
            from well_viewer.heatmap_controller import redraw_heatmap
            redraw_heatmap(app)
        except Exception:
            pass
