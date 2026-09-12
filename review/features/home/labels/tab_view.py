"""Editable 96-well label grid for the Home → Well Labels tab.

Replaces the legacy 96-row stack of ``Well | QLineEdit`` rows. Each well
is a small editable text field laid out in a real 8×12 plate grid; the
row letters (A–H) and column numbers (1–12) are clickable and select
the whole row/column. Add Prefix / Add Suffix at the top apply to
whichever cells are currently selected — when no cell is selected they
fall back to the sidebar's selected wells (legacy behaviour) so the
buttons stay useful for users who haven't discovered the grid selection
yet.

API
---
* ``LabelGrid(app, parent=None)``
* ``setEnabledWells(iter)`` — only those wells become editable + selectable.
* ``selectedTokens() -> list[str]`` — read by the runtime's Add-Affix logic.
* ``refresh()`` — pull values back from ``app._app_state.dataset.well_labels``.

The widget is owned by the Home → Well Labels tab and
stashed on the app as ``app._lbl_grid``.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QVBoxLayout, QWidget,
)

from ui import theme
from widgets.separator import build_hline_separator

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_CORNER_W = 40
_CORNER_H = 26
_H_H = 26
_RH_W = 40
_CELL_H = 26


_ROW_LETTERS = "ABCDEFGH"
_N_ROWS = 8
_N_COLS = 12


class _LabelCell(QLineEdit):
    """One well's label entry. The line edit doubles as a selection target —
    clicking the cell toggles its selection; the visible chrome
    (border / background) flips via a dynamic ``selected`` property.
    """

    selectionToggled = Signal(str)

    def __init__(self, token: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("LabelCell")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty("selected", False)
        self._token = token
        self.setPlaceholderText(token.lower())
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(_CELL_H)
        # Click on the cell BORDER toggles selection; clicks inside the
        # text area still focus the line edit for editing.
        self.installEventFilter(self)

    def token(self) -> str:
        return self._token

    def setSelected(self, on: bool) -> None:
        if bool(on) == self.property("selected"):
            return
        self.setProperty("selected", bool(on))
        self.style().unpolish(self)
        self.style().polish(self)


class _HeaderLabel(QLabel):
    """Clickable A–H row letter or 1–12 column number."""

    clicked = Signal()

    def __init__(self, text: str, parent: QWidget) -> None:
        super().__init__(text, parent)
        self.setObjectName("LabelGridHeader")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("active", False)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)

    def setActive(self, on: bool) -> None:
        self.setProperty("active", bool(on))
        self.style().unpolish(self)
        self.style().polish(self)


class LabelGrid(QWidget):
    """8×12 editable well-label grid + selectable row/column headers."""

    selectionChanged = Signal(list)

    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LabelGrid")
        self._app = app
        self._cells: dict[str, _LabelCell] = {}
        self._row_hdrs: list[_HeaderLabel] = []
        self._col_hdrs: list[_HeaderLabel] = []
        self._selected: set[str] = set()
        # Set by textEdited (a real user edit, not a programmatic refresh); a
        # commit (Enter / focus-out) persists only when set, so a freeform label
        # isn't rewritten to pipeline_info.json on every keystroke.
        self._dirty: bool = False
        # Start with no wells enabled — the host calls setEnabledWells() with
        # ``app._app_state.dataset.well_paths.keys()`` once a dataset is loaded. Treating None
        # as "all enabled" is only useful for the standalone __main__ demo.
        self._enabled: set[str] | None = set()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.Spacing.sm)

        grid_host = QFrame(self)
        grid_host.setObjectName("LabelGridHost")
        grid_host.setAttribute(Qt.WA_StyledBackground, True)
        outer.addWidget(grid_host, 1)
        g = QGridLayout(grid_host)
        g.setContentsMargins(theme.Spacing.md, theme.Spacing.md,
                             theme.Spacing.md, theme.Spacing.md)
        g.setHorizontalSpacing(theme.Spacing.xs)
        g.setVerticalSpacing(theme.Spacing.xs)

        # Empty corner (0,0).
        corner = QLabel("", grid_host)
        corner.setFixedSize(_CORNER_W, _CORNER_H)
        g.addWidget(corner, 0, 0)

        # Column headers 1..12 in row 0.
        for c in range(_N_COLS):
            h = _HeaderLabel(str(c + 1), grid_host)
            h.setFixedHeight(_H_H)
            h.clicked.connect(lambda c=c: self._toggle_column(c))
            self._col_hdrs.append(h)
            g.addWidget(h, 0, c + 1)

        # Row headers + cells.
        for r in range(_N_ROWS):
            rh = _HeaderLabel(_ROW_LETTERS[r], grid_host)
            rh.setFixedWidth(_RH_W)
            rh.clicked.connect(lambda r=r: self._toggle_row(r))
            rh.setEnabled(False)
            self._row_hdrs.append(rh)
            g.addWidget(rh, r + 1, 0)
            for c in range(_N_COLS):
                tok = f"{_ROW_LETTERS[r]}{c + 1:02d}"
                cell = _LabelCell(tok, grid_host)
                cell.setEnabled(False)
                # textChanged keeps the in-memory label current (per keystroke);
                # textEdited flags a real user edit; editingFinished (Enter /
                # focus-out) is the commit that persists to pipeline_info.json.
                cell.textChanged.connect(lambda txt, t=tok: self._on_text_changed(t, txt))
                cell.textEdited.connect(lambda *_: setattr(self, "_dirty", True))
                cell.editingFinished.connect(self._on_commit)
                self._cells[tok] = cell
                g.addWidget(cell, r + 1, c + 1)
        for c in range(_N_COLS):
            self._col_hdrs[c].setEnabled(False)

        for c in range(_N_COLS):
            g.setColumnStretch(c + 1, 1)
        for r in range(_N_ROWS):
            g.setRowStretch(r + 1, 1)

        # Selection clicks: middle-click or shift-click on a cell toggles
        # the cell's selection. We use eventFilter on each cell to catch
        # the cell border vs. text-area distinction cleanly.
        for cell in self._cells.values():
            cell.installEventFilter(self)

        self.setStyleSheet(self._build_qss())

    # ── API ──────────────────────────────────────────────────────────────
    def setEnabledWells(self, tokens) -> None:
        """Restrict editing / selection to *tokens*.

        Passing ``None`` enables every cell (development demo path). Passing
        an empty iterable disables every cell — the loaded-dataset case
        before a dataset is opened, or when the dataset contains no wells.
        """
        if tokens is None:
            self._enabled = None
        else:
            self._enabled = {str(t).upper() for t in tokens}
        for tok, cell in self._cells.items():
            on = self._enabled is None or tok in self._enabled
            cell.setEnabled(on)
            # A disabled cell shouldn't keep its selection state — clear so
            # row/col header logic stays consistent.
            if not on and tok in self._selected:
                self._selected.discard(tok)
                cell.setSelected(False)
        for r, rh in enumerate(self._row_hdrs):
            any_on = self._enabled is None or any(
                f"{_ROW_LETTERS[r]}{c + 1:02d}" in self._enabled
                for c in range(_N_COLS)
            )
            rh.setEnabled(any_on)
        for c, ch in enumerate(self._col_hdrs):
            any_on = self._enabled is None or any(
                f"{_ROW_LETTERS[r]}{c + 1:02d}" in self._enabled
                for r in range(_N_ROWS)
            )
            ch.setEnabled(any_on)
        self._sync_header_actives()

    def selectedTokens(self) -> list[str]:
        return sorted(self._selected)

    def setSelectedTokens(self, tokens) -> None:
        """Select exactly *tokens* (the enabled ones), replacing the selection.

        The counterpart of :meth:`selectedTokens`, added so the Sample Map tab's
        layer switch can carry a selection from the Variables layer into this
        one — both mean "the wells I am about to act on", so switching between
        them must not silently drop what the user had picked. Emits
        ``selectionChanged`` once, not once per cell.
        """
        wanted = {str(t) for t in (tokens or ())}
        if self._enabled is not None:
            wanted &= set(self._enabled)
        if wanted == self._selected:
            return
        self._selected = wanted
        for tok, cell in self._cells.items():
            cell.setSelected(tok in wanted)
        self._sync_header_actives()
        self.selectionChanged.emit(self.selectedTokens())

    def refresh(self) -> None:
        """Re-load every cell from ``app._app_state.dataset.well_labels`` without firing
        textChanged."""
        labels = self._app._app_state.dataset.well_labels or {}
        for tok, cell in self._cells.items():
            blocked = cell.blockSignals(True)
            try:
                cell.setText(labels.get(tok, ""))
            finally:
                cell.blockSignals(blocked)

    # ── internals ────────────────────────────────────────────────────────
    def _on_text_changed(self, tok: str, text: str) -> None:
        val = (text or "").strip()
        app = self._app
        if val:
            app._app_state.dataset.well_labels[tok] = val
        else:
            app._app_state.dataset.well_labels.pop(tok, None)
        if hasattr(app, "_invalidate_stats_cache"):
            app._invalidate_stats_cache()

    def _on_commit(self) -> None:
        """Persist labels when a cell edit is committed (Enter / focus-out).

        Saves only when the user actually changed a cell (the dirty flag), so
        tabbing / clicking through cells doesn't churn pipeline_info.json. The
        registered ``sample_definitions`` autosave is debounced and no-ops
        without a data dir.
        """
        if not self._dirty:
            return
        self._dirty = False
        p = getattr(self._app, "_persistence", None)
        if p is not None:
            p.schedule_save("sample_definitions")

    def _toggle_cell(self, tok: str) -> None:
        if self._enabled is not None and tok not in self._enabled:
            return
        if tok in self._selected:
            self._selected.discard(tok)
        else:
            self._selected.add(tok)
        self._cells[tok].setSelected(tok in self._selected)
        self._sync_header_actives()
        self.selectionChanged.emit(self.selectedTokens())

    def _toggle_tokens(self, tokens) -> None:
        """Toggle a whole line of wells: clear it when every enabled well in the
        line is already selected, otherwise select them all."""
        if self._enabled is not None:
            tokens = [t for t in tokens if t in self._enabled]
        all_on = bool(tokens) and all(t in self._selected for t in tokens)
        for t in tokens:
            if all_on:
                self._selected.discard(t)
            else:
                self._selected.add(t)
            self._cells[t].setSelected(t in self._selected)
        self._sync_header_actives()
        self.selectionChanged.emit(self.selectedTokens())

    def _toggle_row(self, r: int) -> None:
        self._toggle_tokens([f"{_ROW_LETTERS[r]}{c + 1:02d}" for c in range(_N_COLS)])

    def _toggle_column(self, c: int) -> None:
        self._toggle_tokens([f"{_ROW_LETTERS[r]}{c + 1:02d}" for r in range(_N_ROWS)])

    def _sync_header_actives(self) -> None:
        for r, rh in enumerate(self._row_hdrs):
            tokens = [f"{_ROW_LETTERS[r]}{c + 1:02d}" for c in range(_N_COLS)
                      if self._enabled is None or
                      f"{_ROW_LETTERS[r]}{c + 1:02d}" in self._enabled]
            rh.setActive(bool(tokens) and all(t in self._selected for t in tokens))
        for c, ch in enumerate(self._col_hdrs):
            tokens = [f"{_ROW_LETTERS[r]}{c + 1:02d}" for r in range(_N_ROWS)
                      if self._enabled is None or
                      f"{_ROW_LETTERS[r]}{c + 1:02d}" in self._enabled]
            ch.setActive(bool(tokens) and all(t in self._selected for t in tokens))

    def eventFilter(self, obj, event) -> bool:
        # Toggle selection on Shift+click anywhere on the cell, or on a
        # plain click on the cell's frame edge (outside the text area).
        from PySide6.QtCore import QEvent
        # PySide6 can dispatch a non-QEvent here (e.g. a QStandardItem from a
        # QCompleter popup); bail before ``event.type()`` / ``super()``.
        if not isinstance(event, QEvent):
            return False
        if event.type() == QEvent.MouseButtonPress and isinstance(obj, _LabelCell):
            if event.button() == Qt.LeftButton and (event.modifiers() & Qt.ShiftModifier):
                self._toggle_cell(obj.token())
                return True
        return super().eventFilter(obj, event)

    def _build_qss(self) -> str:
        c, r, t = theme.Colors, theme.Radii, theme.Typography
        return f"""
        #LabelGridHost {{
            background-color: {c.panel};
            border: 1px solid {c.border_subtle};
            border-radius: {r.md}px;
        }}
        QLabel#LabelGridHeader {{
            color: {c.text_muted};
            font-family: {t.family_mono};
            font-size: 11px;
            font-weight: 600;
            background: transparent;
            border-radius: {r.xs}px;
        }}
        QLabel#LabelGridHeader:hover {{
            color: {c.accent};
            background-color: {c.panel_elevated};
        }}
        QLabel#LabelGridHeader[active="true"] {{
            color: {c.accent};
            background-color: {c.accent_dim};
        }}
        QLineEdit#LabelCell {{
            background-color: {c.panel_elevated};
            border: 1px solid {c.border_subtle};
            border-radius: {r.xs}px;
            color: {c.text_primary};
            padding: 2px 4px;
            font-size: 11px;
        }}
        QLineEdit#LabelCell:focus {{
            border-color: {c.accent};
        }}
        QLineEdit#LabelCell[selected="true"] {{
            border-color: {c.accent};
            background-color: {c.accent_dim};
        }}
        QLineEdit#LabelCell:disabled {{
            color: {c.text_faint};
            background-color: {c.rail};
        }}
        """


def build_label_grid(ctx, parent: QWidget) -> None:
    """Mount the LabelGrid in the Well Labels sub-tab and wire it up."""
    from review.core.ui_helpers import btn_secondary
    from review.core.selection_controller import labels_apply_affix
    app = ctx.window

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
        layout.setSpacing(0)
    # The same left inset the other two Sample Map layers carry, for the reason
    # written at ``groups/tab_view.build_replicate_groups_centre``: the rail's
    # collapse toggle floats over the pane's left edge. The grid below pays it
    # already, inside its own card, but the help text and the affix row above
    # it were flush at x=0 — measured, and found by the guard written for
    # Replicates rather than reported, because the sentence is muted grey and
    # the toggle sits over its middle rather than its start.
    #
    # Set unconditionally rather than only when this function creates the
    # layout: under Sample Map the caller supplies the page and its layout, so
    # the branch above never runs and the margins inside it never applied.
    layout.setContentsMargins(theme.Spacing.md, 0, 0, 0)

    # ── top help + affix toolbar ─────────────────────────────────────
    head = QWidget(parent)
    hl = QHBoxLayout(head)
    hl.setContentsMargins(0, 8, 0, 6)
    hl.setSpacing(theme.Spacing.sm)
    help_lbl = QLabel(
        "Custom names used in legends + axis labels. Type into a cell to "
        "edit one well; click row/column headers or Shift-click cells to "
        "select multiple, then use Add Prefix / Add Suffix.",
    )
    help_lbl.setObjectName("Muted")
    help_lbl.setWordWrap(True)
    hl.addWidget(help_lbl, 1)

    prefix_btn = btn_secondary(head, "Add Prefix",
                               lambda: labels_apply_affix(ctx, where="prefix"))
    prefix_btn.setToolTip(
        "Prepend text to the labels of every cell currently selected in the "
        "grid. Falls back to the sidebar's selected wells if no cell is "
        "selected here."
    )
    hl.addWidget(prefix_btn)

    suffix_btn = btn_secondary(head, "Add Suffix",
                               lambda: labels_apply_affix(ctx, where="suffix"))
    suffix_btn.setToolTip(
        "Append text to the labels of every cell currently selected in the "
        "grid. Falls back to the sidebar's selected wells if no cell is "
        "selected here."
    )
    hl.addWidget(suffix_btn)
    layout.addWidget(head)

    layout.addWidget(build_hline_separator(parent))

    # ── the grid itself ──────────────────────────────────────────────
    grid = LabelGrid(app, parent)
    grid.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    layout.addWidget(grid, 1)
    ctx.widgets.register("_lbl_grid", grid)


def label_grid_refresh(ctx) -> None:
    """Re-load the grid cells from ``ctx.state.dataset.well_labels`` and update the
    enabled set from the loaded well paths."""
    grid = ctx.widgets.get_or_none("_lbl_grid")
    if grid is None:
        return
    enabled = list((ctx.state.dataset.well_paths or {}).keys())
    grid.setEnabledWells(enabled)
    grid.refresh()


# ── standalone visual test ──────────────────────────────────────────────────
