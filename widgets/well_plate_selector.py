"""WellPlateSelector — the 8×12 microplate selector.

Composes a custom-painted plate grid (clickable A–H row letters and 1–12 column
numbers act as whole-row / whole-column controls) with an optional action row
(``All 96`` / ``Invert`` / ``Clear``). Selected/decorated wells render as "lit
chips": a radial gradient + a faint top sheen.

Beyond the basic flat selection set, the widget supports the decoration /
interaction surface an embedding app needs:

* **Per-well enabled state** — ``setEnabledWells`` / ``setWellEnabled``. Disabled
  wells recede visually and are ignored by clicks, drags and hover.
* **Per-well colour overrides** — ``setWellColors`` / ``setWellColor`` /
  ``setWellState`` so the host can paint app-defined states (selected = accent,
  replicate-set membership, …) regardless of the widget's own selection set.
* **Drag-to-select** — press and drag across wells to toggle a run in one
  gesture (``setDragSelectEnabled``); ``selectionDragFinished`` fires once at the
  end so consumers can do a single refresh.
* **Selection mode** — ``"select"`` (the widget owns a selection set) or
  ``"passive"`` (clicks/headers only *emit* signals; the host owns state).
* **Row/column header signals** — ``rowHeaderActivated(str)`` /
  ``columnHeaderActivated(str)`` (row letter ``"A"``…, zero-padded column
  ``"01"``…), plus ``setRowColumnSelectable`` to disable header interaction.
* **Single-selection mode** — ``setSingleSelectionMode`` keeps at most one well
  selected.
* **Tooltips** — the hovered well's ID, or a custom ``setWellTooltipProvider``.

API (selection)
---------------
* ``selectedWells`` — Qt property: list of well IDs like ``"A01"`` (sorted).
  Also ``selectedWellIds()`` / ``setSelectedWellIds(iterable)``.
* ``selectAll()`` / ``clearSelection()`` / ``invertSelection()`` (operate on
  enabled wells).
* ``selectionChanged(list)`` — emitted with the new sorted well-ID list.

All metrics derive from the widget font / available size — no hardcoded device
pixels — so the plate is DPI / font-scaling aware. Styled from ``theme`` tokens.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import (  # noqa: E402
    Property, QEvent, QMimeData, QPoint, QRectF, QSize, Qt, Signal,
)
from PySide6.QtGui import (  # noqa: E402
    QBrush, QColor, QDrag, QFont, QLinearGradient, QPainter, QPen, QRadialGradient,
)
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QHBoxLayout, QPushButton, QSizePolicy, QToolTip,
    QVBoxLayout, QWidget,
)

import theme  # noqa: E402
from widgets._support import with_alpha  # noqa: E402

_ROW_LETTERS = "ABCDEFGH"
_N_ROWS = 8
_N_COLS = 12


def _well_id(row: int, col: int) -> str:
    return f"{_ROW_LETTERS[row]}{col + 1:02d}"


def _row_letter(row: int) -> str:
    return _ROW_LETTERS[row]


def _col_label(col: int) -> str:
    return f"{col + 1:02d}"


def _parse_well_id(well_id) -> tuple[int, int] | None:
    if isinstance(well_id, tuple) and len(well_id) == 2:
        r, c = well_id
        if isinstance(r, int) and isinstance(c, int) and 0 <= r < _N_ROWS and 0 <= c < _N_COLS:
            return r, c
        return None
    wid = str(well_id).strip().upper()
    if len(wid) < 2 or wid[0] not in _ROW_LETTERS:
        return None
    try:
        col = int(wid[1:]) - 1
    except ValueError:
        return None
    row = _ROW_LETTERS.index(wid[0])
    if 0 <= row < _N_ROWS and 0 <= col < _N_COLS:
        return row, col
    return None


def _to_qcolor(value) -> QColor | None:
    if value is None:
        return None
    c = QColor(value)
    return c if c.isValid() else None


class _PlateGrid(QWidget):
    """The painted grid + headers. Decoration / interaction state lives here."""

    selectionChanged = Signal()
    selectionDragFinished = Signal()
    rowHeaderActivated = Signal(str)       # row letter, e.g. "A"
    columnHeaderActivated = Signal(str)    # zero-padded column, e.g. "01"
    wellActivated = Signal(str)            # well id, e.g. "A07" (passive mode)
    wellDropped = Signal(str, str)         # (well id under cursor, payload token)
    cornerToggled = Signal(bool)           # corner all/none in passive mode (True=all)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PlateGrid")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

        # Selection set: (row, col) -> trace index (recomputed on change).
        self._selected: dict[tuple[int, int], int] = {}
        # Decoration overrides.
        self._enabled: set[tuple[int, int]] | None = None   # None ⇒ all enabled
        self._colors: dict[tuple[int, int], QColor] = {}
        # Interaction config.
        self._mode = "select"                  # "select" | "passive"
        self._drag_select = True
        self._rc_selectable = True
        self._single_select = False
        self._tooltip_provider = None          # Callable[[str], str] | None
        self._markers: dict[tuple[int, int], str] = {}  # per-well glyph overlay
        self._colored_wells_lit = False        # paint colour-override wells raised/lit
        # Drag-source / drop-sink config (heatmap-layout DnD).
        self._drag_mime: str | None = None     # when set, a press starts a QDrag
        self._accept_mime: str | None = None   # when set, the grid accepts drops
        self._drag_press_pos: QPoint | None = None
        self._drag_press_cell: tuple[int, int] | None = None
        # Transient hover / drag-select state.
        self._hover_cell: tuple[int, int] | None = None
        self._hover_row: int | None = None
        self._hover_col: int | None = None
        self._hover_corner = False
        self._drag_active = False
        self._drag_adding = True
        self._drag_visited: set[tuple[int, int]] = set()
        # Rectangle ("rubber-band") drag-select state (when _drag_mode == "rect").
        self._drag_mode = "paint"              # "paint" | "rect"
        self._rect_start: tuple[int, int] | None = None
        self._rect_cur: tuple[int, int] | None = None
        self._rect_base: dict[tuple[int, int], int] = {}
        self._rect_polarity = "add"            # "add" | "remove"

    # ── geometry (all font / size relative) ──────────────────────────────
    def _label_extent(self) -> float:
        return self.fontMetrics().height() * 1.7

    def _metrics(self) -> tuple[float, float, float, float]:
        lab = self._label_extent()
        avail_w = max(1.0, self.width() - lab - 2.0)
        avail_h = max(1.0, self.height() - lab - 2.0)
        cell = max(1.0, min(avail_w / _N_COLS, avail_h / _N_ROWS))
        total_w = lab + cell * _N_COLS
        total_h = lab + cell * _N_ROWS
        ox = (self.width() - total_w) / 2.0 + lab
        oy = (self.height() - total_h) / 2.0 + lab
        return lab, cell, ox, oy

    def _cell_rect(self, row: int, col: int) -> QRectF:
        _lab, cell, ox, oy = self._metrics()
        return QRectF(ox + col * cell, oy + row * cell, cell, cell)

    def _well_rect(self, row: int, col: int) -> QRectF:
        cr = self._cell_rect(row, col)
        d = cr.width() * 0.80
        return QRectF(cr.center().x() - d / 2.0, cr.center().y() - d / 2.0, d, d)

    def _corner_rect(self) -> QRectF:
        """Small circle in the top-left header intersection. Acts as the
        select-all / select-none toggle (multi-select plates only)."""
        lab, _cell, ox, oy = self._metrics()
        # Square sub-region of the corner header cell. Size is a bit smaller
        # than the row-label box so the circle reads as a discrete control.
        d = lab * 0.55
        cx = max(0.0, ox - lab) + lab / 2.0
        cy = max(0.0, oy - lab) + lab / 2.0
        return QRectF(cx - d / 2.0, cy - d / 2.0, d, d)

    def _corner_active(self) -> bool:
        # Shown in multi-select ("select") mode, and also in "passive" mode
        # (replicate-set / group plotting) where it emits ``cornerToggled`` for
        # the host to show/hide all groups — so the all/none toggle stays
        # available even when per-well plate selection is disabled.
        return self._mode in ("select", "passive") and not self._single_select

    def _enabled_keys(self) -> set[tuple[int, int]]:
        if self._enabled is None:
            return {(r, c) for r in range(_N_ROWS) for c in range(_N_COLS)}
        return set(self._enabled)

    def _corner_all_selected(self) -> bool:
        keys = self._enabled_keys()
        if not keys:
            return False
        return keys.issubset(set(self._selected))

    def sizeHint(self) -> QSize:
        u = max(20, round(self.fontMetrics().height() * 1.7))
        lab = round(self._label_extent())
        return QSize(lab + u * _N_COLS, lab + u * _N_ROWS)

    def minimumSizeHint(self) -> QSize:
        # A small floor so the plate can shrink to fit a narrow container (e.g.
        # the ~300px review rail) instead of forcing horizontal overflow. The
        # grid paints fully responsively — ``_metrics`` scales the cell size to
        # whatever width/height it is given — so the minimum only needs to keep
        # the 12x8 grid minimally legible, NOT reserve the preferred size.
        # ``sizeHint`` carries the (larger) preferred size and the Expanding
        # size policy fills the available width when there is room; this floor
        # is what the layout squeezes toward when space is tight. The cell edge
        # is a fixed 12px (not font-scaled) so the minimum stays well under the
        # rail width on HiDPI too, where a font-derived floor would balloon.
        u = 12
        lab = round(self.fontMetrics().height() * 1.2)
        return QSize(lab + u * _N_COLS, lab + u * _N_ROWS)

    # Lock the plate's aspect ratio: at any given width the layout knows the
    # minimum height that keeps the wells circular and the row/column labels
    # legible. Prevents the Sample Definitions tab's stacked plate + groups
    # from crushing the plate when the centre column is short.
    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        lab = self._label_extent()
        # Cell size that fits the offered width (label column + 12 cells).
        cell = max(1.0, (max(1.0, float(w)) - lab) / _N_COLS)
        return int(round(lab + cell * _N_ROWS))

    # ── enabled / colour decoration ──────────────────────────────────────
    def _is_enabled(self, key: tuple[int, int]) -> bool:
        return self._enabled is None or key in self._enabled

    def set_enabled_wells(self, ids) -> None:
        if ids is None:
            new = None
        else:
            new = set()
            for w in ids:
                parsed = _parse_well_id(w)
                if parsed is not None:
                    new.add(parsed)
        if new == self._enabled:
            return
        self._enabled = new
        sel_before = dict(self._selected)
        # Drop selection / colours / hover for now-disabled wells.
        if new is not None:
            self._selected = {k: v for k, v in self._selected.items() if k in new}
            self._colors = {k: v for k, v in self._colors.items() if k in new}
            if self._hover_cell is not None and self._hover_cell not in new:
                self._hover_cell = None
        self._recolor()
        self.update()
        if self._selected != sel_before:
            # Disabling a well dropped it from the selection — tell consumers.
            self.selectionChanged.emit()

    def set_well_enabled(self, well_id, enabled: bool) -> None:
        parsed = _parse_well_id(well_id)
        if parsed is None:
            return
        if self._enabled is None:
            self._enabled = {(r, c) for r in range(_N_ROWS) for c in range(_N_COLS)}
        if enabled:
            self._enabled.add(parsed)
        else:
            self._enabled.discard(parsed)
            self._selected.pop(parsed, None)
            self._colors.pop(parsed, None)
        self._recolor()
        self.update()

    def set_well_colors(self, mapping) -> None:
        for w, col in (mapping or {}).items():
            self.set_well_color(w, col, _defer=True)
        self.update()

    def set_well_color(self, well_id, color, *, _defer: bool = False) -> None:
        parsed = _parse_well_id(well_id)
        if parsed is None:
            return
        qc = _to_qcolor(color)
        if qc is None:
            self._colors.pop(parsed, None)
        else:
            self._colors[parsed] = qc
        if not _defer:
            self.update()

    def clear_well_colors(self) -> None:
        if self._colors:
            self._colors.clear()
            self.update()

    def set_well_state(self, well_id, state: str) -> None:
        """Convenience: ``"selected"`` / ``"neutral"`` / ``"disabled"`` /
        ``"color:#hex"`` / ``"muted:#hex"`` mapped to the enabled/colour
        primitives. (The host can always use those primitives directly.)"""
        parsed = _parse_well_id(well_id)
        if parsed is None:
            return
        s = str(state)
        if s == "disabled":
            self.set_well_enabled(well_id, False)
            return
        self.set_well_enabled(well_id, True)
        if s == "selected":
            self.set_well_color(well_id, theme.Colors.accent)
        elif s == "neutral":
            self.set_well_color(well_id, None)
        elif s.startswith("color:"):
            self.set_well_color(well_id, s[len("color:"):])
        elif s.startswith("muted:"):
            base = _to_qcolor(s[len("muted:"):])
            self.set_well_color(well_id, base.darker(220) if base else None)

    # ── interaction config ───────────────────────────────────────────────
    def set_mode(self, mode: str) -> None:
        if mode in ("select", "passive"):
            self._mode = mode

    def mode(self) -> str:
        return self._mode

    def set_drag_select_enabled(self, on: bool) -> None:
        self._drag_select = bool(on)

    def set_drag_mode(self, mode: str) -> None:
        """``"paint"`` (toggle each cell the cursor crosses — the default) or
        ``"rect"`` (rubber-band: drag a rectangle, all enclosed enabled cells
        are added — or removed, if the press cell was already selected)."""
        if mode in ("paint", "rect"):
            self._drag_mode = mode

    def drag_mode(self) -> str:
        return self._drag_mode

    def set_row_column_selectable(self, on: bool) -> None:
        self._rc_selectable = bool(on)
        if not on and (self._hover_row is not None or self._hover_col is not None):
            self._hover_row = self._hover_col = None
            self.update()

    def set_single_selection_mode(self, on: bool) -> None:
        self._single_select = bool(on)
        if on and len(self._selected) > 1:
            keep = next(iter(sorted(self._selected)))
            self._selected = {keep: 0}
            self._changed()

    def set_tooltip_provider(self, provider) -> None:
        self._tooltip_provider = provider

    def set_well_marker(self, well_id, glyph) -> None:
        """Overlay a short glyph (e.g. a status icon) centred on a well, or
        clear it with ``glyph=None``."""
        parsed = _parse_well_id(well_id)
        if parsed is None:
            return
        if glyph:
            self._markers[parsed] = str(glyph)
        else:
            self._markers.pop(parsed, None)
        self.update()

    def clear_well_markers(self) -> None:
        if self._markers:
            self._markers.clear()
            self.update()

    def set_colored_wells_lit(self, on: bool) -> None:
        on = bool(on)
        if on != self._colored_wells_lit:
            self._colored_wells_lit = on
            self.update()

    def set_drag_mime(self, mime: str | None) -> None:
        """When *mime* is set, pressing a well and dragging past the platform
        drag distance starts a ``QDrag`` carrying that well's id under *mime*.

        Clicking still selects: the press only arms the drag, and a release that
        never reached the drag distance applies the ordinary click behaviour
        (toggle in ``select`` mode, ``wellActivated`` in ``passive`` mode).
        Press-and-drag paint/rubber-band selection is the one gesture the drag
        source takes over, since it is the same gesture as dragging a well out.
        """
        self._drag_mime = mime or None
        if not self._drag_mime:
            self._drag_press_pos = None
            self._drag_press_cell = None

    def set_accept_drop_mime(self, mime: str | None) -> None:
        """When *mime* is set, the grid accepts drops carrying *mime* and emits
        ``wellDropped(well_id_under_cursor, payload)`` on drop."""
        self._accept_mime = mime or None
        self.setAcceptDrops(bool(self._accept_mime))

    # ── selection helpers ────────────────────────────────────────────────
    def _recolor(self) -> None:
        palette_n = len(theme.Colors.trace)
        for i, key in enumerate(sorted(self._selected)):
            self._selected[key] = i % palette_n

    def _changed(self) -> None:
        self._recolor()
        self.updateGeometry()
        self.update()
        self.selectionChanged.emit()

    def selected_ids(self) -> list[str]:
        return [_well_id(r, c) for (r, c) in sorted(self._selected)]

    def set_ids(self, ids) -> None:
        new: dict[tuple[int, int], int] = {}
        for w in ids or ():
            parsed = _parse_well_id(w)
            if parsed is not None and self._is_enabled(parsed):
                new[parsed] = 0
        if self._single_select and len(new) > 1:
            new = {next(iter(sorted(new))): 0}
        if new == self._selected:
            return
        self._selected = new
        self._changed()

    def _add(self, key: tuple[int, int]) -> None:
        if self._single_select:
            self._selected = {key: 0}
        else:
            self._selected[key] = 0

    def toggle_well(self, row: int, col: int, *, force=None) -> None:
        key = (row, col)
        if not self._is_enabled(key):
            return
        present = key in self._selected
        want = (not present) if force is None else bool(force)
        if want == present:
            return
        if want:
            self._add(key)
        else:
            self._selected.pop(key, None)
        self._changed()

    def toggle_row(self, row: int) -> None:
        keys = [(row, c) for c in range(_N_COLS) if self._is_enabled((row, c))]
        if not keys:
            return
        if all(k in self._selected for k in keys):
            for k in keys:
                self._selected.pop(k, None)
        else:
            if self._single_select:
                self._selected = {keys[0]: 0}
            else:
                for k in keys:
                    self._selected[k] = 0
        self._changed()

    def toggle_col(self, col: int) -> None:
        keys = [(r, col) for r in range(_N_ROWS) if self._is_enabled((r, col))]
        if not keys:
            return
        if all(k in self._selected for k in keys):
            for k in keys:
                self._selected.pop(k, None)
        else:
            if self._single_select:
                self._selected = {keys[0]: 0}
            else:
                for k in keys:
                    self._selected[k] = 0
        self._changed()

    def set_all(self, on: bool) -> None:
        if on:
            new = {(r, c): 0 for r in range(_N_ROWS) for c in range(_N_COLS)
                   if self._is_enabled((r, c))}
            if self._single_select and len(new) > 1:
                new = {next(iter(sorted(new))): 0}
        else:
            new = {}
        if new == self._selected:
            return
        self._selected = new
        self._changed()

    def invert(self) -> None:
        new = {(r, c): 0
               for r in range(_N_ROWS) for c in range(_N_COLS)
               if self._is_enabled((r, c)) and (r, c) not in self._selected}
        if self._single_select and len(new) > 1:
            new = {next(iter(sorted(new))): 0}
        self._selected = new
        self._changed()

    # ── input ────────────────────────────────────────────────────────────
    def _hit(self, x: float, y: float):
        """('corner',) | ('well', r, c) | ('row', r) | ('col', c) | None."""
        _lab, cell, ox, oy = self._metrics()
        if self._corner_active() and self._corner_rect().contains(x, y):
            return ("corner",)
        col = int((x - ox) // cell) if x >= ox else -1
        row = int((y - oy) // cell) if y >= oy else -1
        in_cols = 0 <= col < _N_COLS
        in_rows = 0 <= row < _N_ROWS
        if y < oy and in_cols:
            return ("col", col)
        if x < ox and in_rows:
            return ("row", row)
        if in_rows and in_cols:
            return ("well", row, col)
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        hit = self._hit(event.position().x(), event.position().y())
        if hit is None:
            return
        kind = hit[0]
        if kind == "corner":
            target = not self._corner_all_selected()
            if self._mode == "select":
                self.set_all(target)
                # Mirror the redraw-trigger semantics of a click-finish drag so
                # downstream consumers re-render immediately.
                self.selectionDragFinished.emit()
            elif self._mode == "passive":
                # Group mode: the plate is passive, so let the host toggle group
                # visibility (it decides all-vs-none from the actual groups).
                self.cornerToggled.emit(target)
            return
        if kind == "row":
            if not self._rc_selectable:
                return
            if self._mode == "select":
                self.toggle_row(hit[1])              # emits selectionChanged first
            self.rowHeaderActivated.emit(_row_letter(hit[1]))
            return
        if kind == "col":
            if not self._rc_selectable:
                return
            if self._mode == "select":
                self.toggle_col(hit[1])             # emits selectionChanged first
            self.columnHeaderActivated.emit(_col_label(hit[1]))
            return
        # well
        r, c = hit[1], hit[2]
        if not self._is_enabled((r, c)):
            return
        if self._drag_mime:
            # Drag-source mode: capture the press and arm a possible QDrag, but
            # defer the selection action to the release. A press that never
            # travels past the platform drag distance is an ordinary click, so
            # the plate keeps working as a well picker while it doubles as a
            # drag source.
            self._drag_press_pos = event.position().toPoint()
            self._drag_press_cell = (r, c)
            event.accept()
            return
        self._press_well(r, c)

    def _press_well(self, r: int, c: int) -> None:
        """Apply the selection behaviour of a left-press on well ``(r, c)``.

        Split out of :meth:`mousePressEvent` because drag-source mode replays it
        from :meth:`mouseReleaseEvent`, once the gesture is known to be a click
        rather than the start of a QDrag.
        """
        if not self._is_enabled((r, c)):
            return
        if self._mode == "passive":
            self.wellActivated.emit(_well_id(r, c))
            return
        # select mode
        if self._drag_select and not self._single_select:
            if self._drag_mode == "rect":
                self._drag_active = True
                self._rect_polarity = "remove" if (r, c) in self._selected else "add"
                self._rect_base = dict(self._selected)
                self._rect_start = (r, c)
                self._rect_cur = (r, c)
                self._apply_rect()
                self.update()
            else:
                self._drag_active = True
                self._drag_adding = (r, c) not in self._selected
                self._drag_visited = {(r, c)}
                self.toggle_well(r, c, force=self._drag_adding)
        else:
            self.toggle_well(r, c)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (self._drag_mime and self._drag_press_pos is not None
                and (event.buttons() & Qt.LeftButton)):
            moved = (event.position().toPoint() - self._drag_press_pos).manhattanLength()
            if moved >= QApplication.startDragDistance():
                cell = self._drag_press_cell
                self._drag_press_pos = None
                self._drag_press_cell = None
                if cell is not None:
                    wid = _well_id(cell[0], cell[1])
                    mime = QMimeData()
                    mime.setData(self._drag_mime, wid.encode("utf-8"))
                    drag = QDrag(self)
                    drag.setMimeData(mime)
                    drag.exec(Qt.MoveAction | Qt.CopyAction)
                event.accept()
                return

        pos = event.position()
        hit = self._hit(pos.x(), pos.y())
        cell = row = col = None
        corner = False
        if hit is not None:
            if hit[0] == "corner":
                corner = True
            elif hit[0] == "well" and self._is_enabled((hit[1], hit[2])):
                cell = (hit[1], hit[2])
            elif hit[0] == "row" and self._rc_selectable:
                row = hit[1]
            elif hit[0] == "col" and self._rc_selectable:
                col = hit[1]
        if (cell, row, col, corner) != (
            self._hover_cell, self._hover_row, self._hover_col, self._hover_corner,
        ):
            self._hover_cell, self._hover_row, self._hover_col = cell, row, col
            self._hover_corner = corner
            self.update()

        if self._drag_active and (event.buttons() & Qt.LeftButton):
            if self._drag_mode == "rect":
                if cell is not None and cell != self._rect_cur:
                    self._rect_cur = cell
                    self._apply_rect()
                    self.update()
            elif cell is not None and cell not in self._drag_visited:
                self._drag_visited.add(cell)
                self.toggle_well(cell[0], cell[1], force=self._drag_adding)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        # A drag-source press defers its selection action until the gesture is
        # resolved: ``mouseMoveEvent`` clears the armed cell the moment it
        # starts a QDrag, so a cell still armed here belongs to a plain click
        # and gets the press behaviour now. ``_press_well`` may set
        # ``_drag_active``, which the block below then finishes exactly as it
        # would for a press-driven selection.
        pending = self._drag_press_cell
        self._drag_press_pos = None
        self._drag_press_cell = None
        if pending is not None:
            self._press_well(pending[0], pending[1])
        if self._drag_active:
            self._drag_active = False
            self._drag_visited = set()
            self._rect_start = None
            self._rect_cur = None
            self._rect_base = {}
            self.update()
            self.selectionDragFinished.emit()
        super().mouseReleaseEvent(event)

    def _apply_rect(self) -> None:
        """Recompute ``_selected`` for the current rubber-band rectangle."""
        if self._rect_start is None or self._rect_cur is None:
            return
        r0, c0 = self._rect_start
        r1, c1 = self._rect_cur
        box = {(r, c)
               for r in range(min(r0, r1), max(r0, r1) + 1)
               for c in range(min(c0, c1), max(c0, c1) + 1)
               if self._is_enabled((r, c))}
        base = dict(self._rect_base)
        if self._rect_polarity == "add":
            for k in box:
                base.setdefault(k, 0)
        else:
            for k in box:
                base.pop(k, None)
        if base != self._selected:
            self._selected = base
            self._changed()

    # ── drop sink (heatmap-layout DnD) ───────────────────────────────────
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._accept_mime and event.mimeData().hasFormat(self._accept_mime):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._accept_mime and event.mimeData().hasFormat(self._accept_mime):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if not (self._accept_mime and event.mimeData().hasFormat(self._accept_mime)):
            super().dropEvent(event)
            return
        try:
            pt = event.position().toPoint()
        except AttributeError:  # very old Qt fallback
            pt = event.pos()
        hit = self._hit(pt.x(), pt.y())
        well_id = _well_id(hit[1], hit[2]) if (hit is not None and hit[0] == "well") else ""
        token = bytes(event.mimeData().data(self._accept_mime)).decode("utf-8", "ignore").strip()
        if token:
            self.wellDropped.emit(well_id, token)
        event.acceptProposedAction()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        if (self._hover_cell, self._hover_row, self._hover_col, self._hover_corner) != (
            None, None, None, False,
        ):
            self._hover_cell = self._hover_row = self._hover_col = None
            self._hover_corner = False
            self.update()

    def event(self, ev) -> bool:  # noqa: N802
        if ev.type() == QEvent.ToolTip:
            # A ToolTip can be delivered while a *modal* dialog (e.g. the
            # Open-directory picker) owns the event loop. Driving QToolTip from a
            # widget whose window isn't the active one then crashes with a native
            # access violation on Windows. Only handle the tooltip when this
            # plate's window is the active window; otherwise defer to the base
            # handler (which shows nothing).
            if not self.isVisible() or self.window() is not QApplication.activeWindow():
                return super().event(ev)
            pos = ev.pos()
            hit = self._hit(pos.x(), pos.y())
            if hit is not None and hit[0] == "well":
                wid = _well_id(hit[1], hit[2])
                text = wid
                if self._tooltip_provider is not None:
                    try:
                        text = str(self._tooltip_provider(wid)) or wid
                    except Exception:
                        text = wid
                QToolTip.showText(ev.globalPos(), text, self)
            elif hit is not None and hit[0] == "corner":
                text = "Clear selection" if self._corner_all_selected() else "Select all wells"
                QToolTip.showText(ev.globalPos(), text, self)
            else:
                QToolTip.hideText()
            ev.accept()
            return True
        return super().event(ev)

    # ── painting ─────────────────────────────────────────────────────────
    def _paint_lit(self, p: QPainter, wr: QRectF, base: QColor, hovered: bool) -> None:
        c = theme.Colors
        base = QColor(base)
        grad = QRadialGradient(
            wr.center().x() - wr.width() * 0.15,
            wr.center().y() - wr.height() * 0.20,
            wr.width() * 0.75,
        )
        grad.setColorAt(0.0, base.lighter(150))
        grad.setColorAt(0.55, base)
        grad.setColorAt(1.0, base.darker(135))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor(c.text_primary) if hovered else base.darker(170),
                      max(1.0, wr.width() * 0.04)))
        p.drawEllipse(wr)
        hl = QRectF(wr).adjusted(
            wr.width() * 0.16, wr.height() * 0.10,
            -wr.width() * 0.16, -wr.height() * 0.46,
        )
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(with_alpha(theme.Colors.ink_light, 0.30), max(1.0, wr.width() * 0.06)))
        p.drawArc(hl, 20 * 16, 140 * 16)

    @staticmethod
    def _mix_with_rail(tint: QColor, t: float = 0.30) -> QColor:
        """Blend *tint* with the ``rail`` token at ``t`` (0..1).

        ``t = 0`` returns pure rail (= the plain depressed look),
        ``t = 1`` returns the pure group colour. We use a low value so
        the recessed look stays dominant and the colour reads as a
        muted hint of the group rather than the full saturation.
        """
        c = theme.Colors
        rail = QColor(c.rail)
        try:
            tr, tg, tb, _ = tint.getRgb()
        except Exception:
            return rail
        rr, rg, rb, _ = rail.getRgb()
        t = max(0.0, min(1.0, float(t)))
        return QColor(
            int(round(rr * (1 - t) + tr * t)),
            int(round(rg * (1 - t) + tg * t)),
            int(round(rb * (1 - t) + tb * t)),
        )

    def _paint_depressed(self, p: QPainter, wr: QRectF, hovered: bool,
                         tint: QColor | None = None) -> None:
        """Paint an unselected well so it reads as a *recessed* circle.

        Uses the classic CSS ``inset`` box-shadow look:
        the fill is a vertical *linear* gradient — dark at the top
        (rim shadows the upper inside of the well) and lighter at the
        bottom (light from above reaches the floor of the recess) —
        and an inner shadow arc reinforces the dark band at the top.
        Selected / coloured wells still go through ``_paint_lit`` for the
        raised sphere look.

        When ``tint`` is supplied (the well belongs to a group but isn't
        the active selection), the recessed fill is rebased on a muted
        blend of the group colour with ``rail`` so the well still hints
        at its group membership.
        """
        c = theme.Colors
        # Wells sit on the ``panel_elevated`` plate surface; the recessed
        # fill needs to be visibly darker so the eye reads it as below
        # that surface. Default base is the app's deepest token
        # (``rail``); when a group ``tint`` is provided we blend it with
        # rail so the recess keeps its sunken feel but reads as
        # group-coloured.
        if tint is None:
            base = QColor(c.rail)
        else:
            base = self._mix_with_rail(QColor(tint), 0.30)
        grad = QLinearGradient(wr.topLeft(), wr.bottomLeft())
        grad.setColorAt(0.0, base.darker(165))
        grad.setColorAt(0.55, base.darker(115))
        grad.setColorAt(1.0, base.lighter(112))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor(c.border_strong if hovered else c.border_subtle),
                      max(1.0, wr.width() * 0.05)))
        p.drawEllipse(wr)
        # Top inner-shadow: a dark arc just inside the rim across the top
        # half of the well — this is the cue the eye reads as "shadow cast
        # by the well's lip into the recess".
        inner = QRectF(wr).adjusted(
            wr.width() * 0.06, wr.height() * 0.06,
            -wr.width() * 0.06, -wr.height() * 0.06,
        )
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(with_alpha(theme.Colors.ink_dark, 0.55),
                      max(1.0, wr.width() * 0.10)))
        # Qt arc angles are sixteenths of a degree, 0° at +x, CCW positive;
        # 30°→150° draws the upper half of the ellipse (the rim's shaded
        # underside).
        p.drawArc(inner, 30 * 16, 120 * 16)
        # Faint bright arc on the bottom to mirror the lit floor.
        p.setPen(QPen(with_alpha(theme.Colors.ink_light, 0.10),
                      max(1.0, wr.width() * 0.06)))
        p.drawArc(inner, 210 * 16, 120 * 16)

    def paintEvent(self, _event) -> None:  # noqa: N802
        c = theme.Colors
        traces = theme.Colors.trace
        lab, cell, ox, oy = self._metrics()

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setFont(self.font())

        # Top-left corner select-all / select-none toggle. Drawn only when
        # the plate accepts multi-select interaction; otherwise the corner
        # is left blank.
        if self._corner_active():
            cr = self._corner_rect()
            all_sel = self._corner_all_selected()
            if all_sel:
                fill = QColor(c.accent)
                pen_col = QColor(c.accent)
            else:
                fill = QColor(c.panel)
                pen_col = QColor(c.border_subtle)
            if self._hover_corner:
                pen_col = QColor(c.accent)
            p.setPen(QPen(pen_col, max(1.0, cr.width() * 0.10)))
            p.setBrush(fill)
            p.drawEllipse(cr)
            if all_sel:
                # Small inner dot to read as "filled / checked".
                inner = QRectF(cr).adjusted(
                    cr.width() * 0.32, cr.height() * 0.32,
                    -cr.width() * 0.32, -cr.height() * 0.32,
                )
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(c.panel))
                p.drawEllipse(inner)

        # Column numbers (1–12) along the top.
        for ci in range(_N_COLS):
            rect = QRectF(ox + ci * cell, oy - lab, cell, lab)
            hot = self._rc_selectable and self._hover_col == ci
            p.setPen(QColor(c.text_primary if hot else c.text_muted))
            p.drawText(rect, int(Qt.AlignCenter), str(ci + 1))

        # Row letters (A–H) down the left.
        for ri in range(_N_ROWS):
            rect = QRectF(ox - lab, oy + ri * cell, lab, cell)
            hot = self._rc_selectable and self._hover_row == ri
            p.setPen(QColor(c.text_primary if hot else c.text_muted))
            p.drawText(rect, int(Qt.AlignCenter), _ROW_LETTERS[ri])

        # Wells.
        for ri in range(_N_ROWS):
            for ci in range(_N_COLS):
                key = (ri, ci)
                wr = self._well_rect(ri, ci)
                if not self._is_enabled(key):
                    p.setBrush(QColor(c.panel))
                    p.setPen(QPen(QColor(c.border_subtle), max(1.0, wr.width() * 0.04)))
                    p.drawEllipse(wr)
                    continue
                hovered = (
                    self._hover_cell == key
                    or (self._rc_selectable and (self._hover_row == ri or self._hover_col == ci))
                )
                group_color = self._colors.get(key)
                in_selection = key in self._selected
                # Two visual states for wells with data:
                #   • selected → raised ``_paint_lit`` (group colour if
                #     set, else the rank-cycled trace colour).
                #   • unselected → recessed ``_paint_depressed`` with a
                #     muted tint (group colour if set, else the app's
                #     accent) so the well always reads as a faint hue
                #     rather than a dark recess.
                lit = in_selection or (self._colored_wells_lit and group_color is not None)
                if lit:
                    base = group_color if group_color is not None else QColor(
                        traces[self._selected[key] % len(traces)]
                    )
                    self._paint_lit(p, wr, base, hovered)
                else:
                    tint = group_color if group_color is not None else QColor(c.accent)
                    self._paint_depressed(p, wr, hovered, tint=tint)
                glyph = self._markers.get(key)
                if glyph:
                    f = QFont(self.font())
                    f.setPixelSize(max(7, int(wr.height() * 0.62)))
                    p.setFont(f)
                    # contrast against the well fill (coloured wells are bright)
                    p.setPen(QColor(c.ink_dark if group_color is not None else c.text_primary))
                    p.drawText(wr, int(Qt.AlignCenter), glyph)
                    p.setFont(self.font())

        # Rubber-band rectangle while a "rect" drag-select is in progress.
        if (self._drag_mode == "rect" and self._rect_start is not None
                and self._rect_cur is not None):
            r0, c0 = self._rect_start
            r1, c1 = self._rect_cur
            tl = self._cell_rect(min(r0, r1), min(c0, c1)).topLeft()
            br = self._cell_rect(max(r0, r1), max(c0, c1)).bottomRight()
            p.setBrush(with_alpha(QColor(c.accent), 0.14))
            p.setPen(QPen(QColor(c.accent), 1.4, Qt.DashLine))
            p.drawRect(QRectF(tl, br))
        p.end()


class WellPlateSelector(QWidget):
    """8×12 plate selector with header controls and All / Invert / Clear actions."""

    selectionChanged = Signal(list)
    selectionDragFinished = Signal()
    rowHeaderActivated = Signal(str)
    columnHeaderActivated = Signal(str)
    wellActivated = Signal(str)
    wellDropped = Signal(str, str)         # (well id under cursor, payload token)
    cornerToggled = Signal(bool)           # corner all/none in passive mode (True=all)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WellPlateSelector")
        self.setAttribute(Qt.WA_StyledBackground, True)

        m = theme.Spacing.sm
        root = QVBoxLayout(self)
        root.setContentsMargins(m, m, m, m)
        root.setSpacing(theme.Spacing.sm)

        self._grid = _PlateGrid(self)
        self._grid.selectionChanged.connect(self._on_grid_changed)
        self._grid.selectionDragFinished.connect(self.selectionDragFinished)
        self._grid.rowHeaderActivated.connect(self.rowHeaderActivated)
        self._grid.columnHeaderActivated.connect(self.columnHeaderActivated)
        self._grid.wellActivated.connect(self.wellActivated)
        self._grid.wellDropped.connect(self.wellDropped)
        self._grid.cornerToggled.connect(self.cornerToggled)
        root.addWidget(self._grid, 1)

        self._actions = QHBoxLayout()
        self._actions.setSpacing(theme.Spacing.sm)
        self._btn_all = QPushButton("All 96")
        self._btn_all.setToolTip("Select all loaded wells")
        self._btn_invert = QPushButton("Invert")
        self._btn_invert.setToolTip("Invert the current selection")
        self._btn_clear = QPushButton("Clear")
        self._btn_clear.setObjectName("Danger")
        self._btn_clear.setToolTip("Clear the selection")
        for b in (self._btn_all, self._btn_invert, self._btn_clear):
            b.setCursor(Qt.PointingHandCursor)
            self._actions.addWidget(b)
        self._actions.addStretch(1)
        self._actions_host = QWidget(self)
        self._actions_host.setLayout(self._actions)
        root.addWidget(self._actions_host)

        self._btn_all.clicked.connect(self.selectAll)
        self._btn_invert.clicked.connect(self.invertSelection)
        self._btn_clear.clicked.connect(self.clearSelection)

        self.setStyleSheet(self._build_qss())

    # ── selection API (enabled-aware) ────────────────────────────────────
    def selectAll(self) -> None:
        self._grid.set_all(True)

    def clearSelection(self) -> None:
        self._grid.set_all(False)

    def invertSelection(self) -> None:
        self._grid.invert()

    def selectedWellIds(self) -> list[str]:
        return self._grid.selected_ids()

    def setSelectedWellIds(self, ids) -> None:
        self._grid.set_ids(ids)

    def _get_selected(self) -> list:
        return self._grid.selected_ids()

    def _set_selected(self, ids) -> None:
        self._grid.set_ids(ids)

    selectedWells = Property(list, _get_selected, _set_selected)

    # ── decoration API ───────────────────────────────────────────────────
    def setEnabledWells(self, ids) -> None:
        """Set the full set of usable wells (``None`` ⇒ all 96 enabled)."""
        self._grid.set_enabled_wells(ids)

    def setWellEnabled(self, well_id, enabled: bool = True) -> None:
        self._grid.set_well_enabled(well_id, enabled)

    def setWellColors(self, mapping) -> None:
        """``{well_id: color|None}`` — explicit per-well fill overrides."""
        self._grid.set_well_colors(mapping)

    def setWellColor(self, well_id, color) -> None:
        self._grid.set_well_color(well_id, color)

    def clearWellColors(self) -> None:
        self._grid.clear_well_colors()

    def setWellState(self, well_id, state: str) -> None:
        self._grid.set_well_state(well_id, state)

    def setWellMarker(self, well_id, glyph) -> None:
        """Overlay a glyph (e.g. a status icon) on a well; ``None`` clears it."""
        self._grid.set_well_marker(well_id, glyph)

    def clearWellMarkers(self) -> None:
        self._grid.clear_well_markers()

    def setColoredWellsLit(self, on: bool) -> None:
        """Paint wells that have an explicit colour override in the raised/lit
        style (instead of the dim recessed tint) — for status maps where the
        colour is the signal, not a faint membership hint."""
        self._grid.set_colored_wells_lit(on)

    # ── interaction config ───────────────────────────────────────────────
    def setSelectionMode(self, mode: str) -> None:
        """``"select"`` (widget owns the selection) or ``"passive"`` (clicks /
        headers only emit signals)."""
        self._grid.set_mode(mode)

    def selectionMode(self) -> str:
        return self._grid.mode()

    def setDragSelectEnabled(self, on: bool) -> None:
        self._grid.set_drag_select_enabled(on)

    def setDragMode(self, mode: str) -> None:
        """``"paint"`` (default — toggle each cell crossed) or ``"rect"``
        (rubber-band: drag a rectangle; enclosed enabled cells are added, or
        removed if the press cell was already selected)."""
        self._grid.set_drag_mode(mode)

    def dragMode(self) -> str:
        return self._grid.drag_mode()

    def setRowColumnSelectable(self, on: bool) -> None:
        self._grid.set_row_column_selectable(on)

    def setSingleSelectionMode(self, on: bool) -> None:
        self._grid.set_single_selection_mode(on)

    def setWellTooltipProvider(self, provider) -> None:
        self._grid.set_tooltip_provider(provider)

    def setDragMime(self, mime: str | None) -> None:
        """Make wells drag *sources*: a press+drag exports the well id under
        *mime* (and clicks stop toggling selection). Pass ``None`` to disable."""
        self._grid.set_drag_mime(mime)

    def setAcceptDropMime(self, mime: str | None) -> None:
        """Accept drops carrying *mime*; emit ``wellDropped(well_id, payload)``
        on drop. Pass ``None`` to disable."""
        self._grid.set_accept_drop_mime(mime)

    def setActionsVisible(self, visible: bool) -> None:
        """Show/hide the built-in All / Invert / Clear button row (a host with
        its own action buttons can hide it)."""
        self._actions_host.setVisible(bool(visible))

    # ── internals ────────────────────────────────────────────────────────
    def _on_grid_changed(self) -> None:
        self.selectionChanged.emit(self._grid.selected_ids())

    def _build_qss(self) -> str:
        c, r = theme.Colors, theme.Radii
        return f"""
        #WellPlateSelector {{
            background-color: {c.panel};
            border: 1px solid {c.border_subtle};
            border-radius: {r.md}px;
        }}
        #PlateGrid {{ background: transparent; }}
        """


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget as _QW,
    )

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("WellPlateSelector — demo")
    pad = theme.Spacing.lg
    lay = QVBoxLayout(root)
    lay.setContentsMargins(pad, pad, pad, pad)
    lay.setSpacing(theme.Spacing.md)

    title = QLabel("WellPlateSelector")
    title.setObjectName("Title")
    lay.addWidget(title)

    plate = WellPlateSelector()
    # Only some wells "have data".
    enabled = [f"{r}{c:02d}" for r in "ABCDEF" for c in range(1, 11)]
    plate.setEnabledWells(enabled)
    plate.setSelectedWellIds(["A01", "A02", "B01", "C03", "C04", "D06"])
    # Tag a couple of wells with explicit colours (e.g. replicate-set membership).
    plate.setWellColors({"E07": theme.Colors.trace[3], "E08": theme.Colors.trace[3],
                         "F09": theme.Colors.trace[1]})
    plate.setWellState("F10", "muted:" + theme.Colors.trace[1])
    plate.setWellTooltipProvider(lambda wid: f"Well {wid} — example tooltip")
    lay.addWidget(plate, 1)

    opts = QHBoxLayout()
    drag_cb = QCheckBox("drag-select")
    drag_cb.setChecked(True)
    drag_cb.toggled.connect(plate.setDragSelectEnabled)
    single_cb = QCheckBox("single-select")
    single_cb.toggled.connect(plate.setSingleSelectionMode)
    rc_cb = QCheckBox("row/col selectable")
    rc_cb.setChecked(True)
    rc_cb.toggled.connect(plate.setRowColumnSelectable)
    rect_cb = QCheckBox("rubber-band drag (rect)")
    rect_cb.toggled.connect(lambda on: plate.setDragMode("rect" if on else "paint"))
    opts.addWidget(drag_cb)
    opts.addWidget(single_cb)
    opts.addWidget(rc_cb)
    opts.addWidget(rect_cb)
    opts.addStretch(1)
    lay.addLayout(opts)

    echo = QLabel()
    echo.setObjectName("Secondary")
    echo.setWordWrap(True)

    def _show(ids):
        echo.setText(f"{len(ids)} selected: {', '.join(ids) if ids else '(none)'}")

    plate.selectionChanged.connect(_show)
    plate.selectionDragFinished.connect(lambda: echo.setText(echo.text() + "  [drag finished]"))
    plate.rowHeaderActivated.connect(lambda r: echo.setText(f"row header → {r}"))
    plate.columnHeaderActivated.connect(lambda c: echo.setText(f"column header → {c}"))
    _show(plate.selectedWellIds())
    lay.addWidget(echo)

    root.resize(640, 520)
    root.show()
    _sys.exit(app.exec())
