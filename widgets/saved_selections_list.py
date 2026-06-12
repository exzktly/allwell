"""SavedSelectionsList — an editable, reorderable list of saved well selections.

Built against the ``Selection`` model implemented in
``well_viewer.selections_model``: the widget holds a working ``list[dict]``
(each dict = one ``Selection``: ``id``,
``name``, ``color``, ``hidden``, ``wells``, ``replicates``, optional ``labels``/
``source``) and emits granular signals plus a coarse ``selectionsChanged(list)``
as the user edits.

Per row: drag handle (drag-to-reorder) · visibility eye · colour dot (opens a
``ColorSwatchRow`` recolour popover) · inline-renamable name · count chip
(``len(wells)``) · kebab → ``QMenu`` (Rename / Recolour / Duplicate / Hide /
Move up / Move down / Export / Delete). Clicking the row body activates it;
clicking the chevron expands it to the selection's well chips.

**Composition (opt-in via ``setComposable(True)``):** the expanded
row becomes editable — replicate sub-lists (``R1:/R2:/solo:``) with *ungroup*
(``⊟``) / *group-solo* (``⊞``) buttons, each chip a click-menu (Remove · Move to
new replicate · Move to R*k* · Make solo), and a ``+ wells…`` button that opens
a ``Popover`` hosting a ``WellPlateSelector`` (multi-select). The widget does
*not* enforce cross-selection well exclusivity (a well may live in many
selections — that's how bar-plot conditions overlap).

API
---
* ``setSelections(list[dict])`` / ``selections() -> list[dict]``
* ``setCurrentId(str)`` / ``currentId() -> str``
* ``setComposable(bool)`` / ``isComposable()``; ``setEnabledWells(iter)`` /
  ``enabledWells()``; ``setWellPlateFactory(callable|None)``
* ``setSelectionWells(id, wells)`` / ``setSelectionReplicates(id, reps|None)``
  (host → widget; no signal)
* signals: ``entryActivated(str)``, ``entryRenamed(str, str)``,
  ``entryRecoloured(str, str)``, ``entryVisibilityToggled(str, bool)``,
  ``entryDuplicated(str, str)``, ``entryDeleted(str)``,
  ``entryExportRequested(str)``, ``orderChanged(list)``,
  ``addFromSelectionRequested()``, ``importRequested()``,
  ``wellsChanged(str, list)``, ``replicatesChanged(str, list)``,
  ``selectionsChanged(list)``
"""

from __future__ import annotations

import os as _os
import sys as _sys
import uuid as _uuid

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QPoint, QTimer, Qt, Signal  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QScrollArea,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

import theme  # noqa: E402
from well_token import canonical_well_label  # noqa: E402

_HANDLE = "⠇"   # ⠇-ish drag dots
_EYE_ON = "◉"
_EYE_OFF = "○"
_KEBAB = "⋯"
_CHEV_C = "▸"
_CHEV_O = "▾"

_PLATE_ROWS = "ABCDEFGH"
_ALL_WELLS = [f"{r}{c:02d}" for r in _PLATE_ROWS for c in range(1, 13)]


def _hex6(c) -> str:
    qc = QColor(c)
    return qc.name(QColor.HexRgb).upper() if qc.isValid() else "#888888"


def _norm_token(w) -> str | None:
    return canonical_well_label(w) if isinstance(w, str) else None


def _well_rank(w) -> int:
    t = _norm_token(w)
    if t is None:
        return 1 << 30
    return _PLATE_ROWS.index(t[0]) * 12 + (int(t[1:]) - 1)


def _dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _clean_wells(seq) -> list[str]:
    if not isinstance(seq, (list, tuple)):
        return []
    return _dedup(t for t in (_norm_token(w) for w in seq) if t)


def _clean_reps(reps, *, allowed: set) -> list[list[str]] | None:
    """De-overlap + restrict to ``allowed``; ``None`` for an empty result."""
    if not reps:
        return None
    seen: set = set()
    out: list[list[str]] = []
    for sub in reps:
        if not isinstance(sub, (list, tuple)):
            continue
        kept = []
        for w in sub:
            t = _norm_token(w)
            if t is None or t in seen or t not in allowed:
                continue
            seen.add(t)
            kept.append(t)
        if kept:
            out.append(kept)
    return out or None


class _SelectionRow(QFrame):
    activated = Signal(str)
    visibilityToggled = Signal(str, bool)   # (id, hidden)
    recolourRequested = Signal(str, QWidget)
    renameCommitted = Signal(str, str)
    kebabRequested = Signal(str, QWidget)
    expandToggled = Signal(str, bool)
    dragMoveBy = Signal(str, int)            # (id, net ± rows) — emitted on release
    compositionEdited = Signal(str, list, object)   # (id, wells, replicates|None)

    def __init__(self, sel: dict, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SelectionRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._id = sel["id"]
        self._expanded = False
        self._wells: list[str] = list(sel.get("wells") or [])
        self._replicates = sel.get("replicates")
        self._composable = False
        self._compact = False  # rail mode: hide drag/eye/kebab chrome, read-only.
        self._enabled_wells: list[str] = []
        self._plate_factory = None
        self._compose_pop = None
        self._compose_btn: QToolButton | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        top = QWidget(self)
        row = QHBoxLayout(top)
        row.setContentsMargins(theme.Spacing.sm, theme.Spacing.xs,
                               theme.Spacing.sm, theme.Spacing.xs)
        row.setSpacing(theme.Spacing.sm)

        self._chev = QToolButton(top)
        self._chev.setObjectName("RowGhost")
        self._chev.setText(_CHEV_C)
        self._chev.setCursor(Qt.PointingHandCursor)
        self._chev.clicked.connect(self._toggle_expand)
        row.addWidget(self._chev)

        self._handle = QLabel(_HANDLE, top)
        self._handle.setObjectName("RowHandle")
        self._handle.setCursor(Qt.OpenHandCursor)
        self._handle.setToolTip("Drag to reorder")
        self._drag_y0: float | None = None
        self._drag_steps = 0
        self._handle.mousePressEvent = self._handle_press     # type: ignore[assignment]
        self._handle.mouseMoveEvent = self._handle_move       # type: ignore[assignment]
        self._handle.mouseReleaseEvent = self._handle_release  # type: ignore[assignment]
        row.addWidget(self._handle)

        self._eye = QToolButton(top)
        self._eye.setObjectName("RowGhost")
        self._eye.setCheckable(True)
        self._eye.setCursor(Qt.PointingHandCursor)
        self._eye.clicked.connect(
            lambda: self.visibilityToggled.emit(self._id, self._eye.isChecked()))
        row.addWidget(self._eye)

        self._dot = QToolButton(top)
        self._dot.setObjectName("RowDot")
        self._dot.setCursor(Qt.PointingHandCursor)
        self._dot.setToolTip("Recolour")
        self._dot.clicked.connect(
            lambda: self.recolourRequested.emit(self._id, self._dot))
        row.addWidget(self._dot)

        self._name = QLabel(top)
        self._name.setObjectName("RowName")
        self._name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row.addWidget(self._name, 1)

        self._editor: QLineEdit | None = None

        self._chip = QLabel(top)
        self._chip.setObjectName("RowCount")
        self._chip.setAlignment(Qt.AlignCenter)
        row.addWidget(self._chip)

        self._kebab = QToolButton(top)
        self._kebab.setObjectName("RowGhost")
        self._kebab.setText(_KEBAB)
        self._kebab.setCursor(Qt.PointingHandCursor)
        self._kebab.clicked.connect(
            lambda: self.kebabRequested.emit(self._id, self._kebab))
        row.addWidget(self._kebab)

        outer.addWidget(top)

        self._chips_host = QWidget(self)
        ch = QHBoxLayout(self._chips_host)
        ch.setContentsMargins(theme.Spacing.xl + theme.Spacing.sm, 0,
                              theme.Spacing.sm, theme.Spacing.sm)
        self._chips_inner = ch
        self._chips_host.setVisible(False)
        outer.addWidget(self._chips_host)

        self._top = top
        self.setStyleSheet(self._qss())
        top.mouseDoubleClickEvent = self._begin_rename  # type: ignore[assignment]
        top.mousePressEvent = self._on_body_press        # type: ignore[assignment]

    # ── compact rail mode ────────────────────────────────────────────────
    def setCompact(self, on: bool) -> None:
        """Compact (read-only) variant for the left-rail Saved list.

        Hides the drag handle, visibility eye, and kebab; keeps the colour
        dot but disables the recolour click; the count chip remains visible
        but reads as the meta-text strip from the mockup. The row is still
        clickable to activate the selection.
        """
        on = bool(on)
        if on == self._compact:
            return
        self._compact = on
        self._handle.setVisible(not on)
        self._eye.setVisible(not on)
        self._kebab.setVisible(not on)
        # Dot stays visible (it's the colour cue); just disable its click.
        self._dot.setEnabled(not on)
        self._dot.setCursor(Qt.ArrowCursor if on else Qt.PointingHandCursor)
        # Hide the chip when compact (mockup shows meta as plain text — we keep
        # the chip but reduce visual weight via the QSS [compact="true"] rule).
        self.setProperty("compact", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def isCompact(self) -> bool:
        return self._compact

    # ── styling ──────────────────────────────────────────────────────────
    def _qss(self) -> str:
        c, r = theme.Colors, theme.Radii
        return f"""
        QFrame#SelectionRow {{ background: transparent; border-radius: {r.sm}px;
            border-left: 3px solid transparent; }}
        QFrame#SelectionRow[current="true"] {{
            background-color: {c.accent_dim};
            border-left: 3px solid {c.accent};
        }}
        QFrame#SelectionRow[current="true"] QLabel#RowName {{
            color: {c.text_primary};
            font-weight: 700;
        }}
        QToolButton#RowGhost {{ border: none; background: transparent; color: {c.text_secondary};
            padding: 0 2px; }}
        QToolButton#RowGhost:hover {{ color: {c.text_primary}; }}
        QToolButton#RowDot {{ border: 1px solid {c.border}; border-radius: 7px;
            min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px; }}
        QLabel#RowHandle {{ color: {c.text_faint}; }}
        QLabel#RowName {{ color: {c.text_primary}; }}
        QLabel#RowName[hiddenRow="true"] {{ color: {c.text_muted}; }}
        QLabel#RowCount {{ color: {c.text_secondary}; background-color: {c.panel_elevated};
            border: 1px solid {c.border_subtle}; border-radius: {r.xs}px; padding: 0 6px; }}
        QLabel#RepLabel {{ color: {c.text_faint}; font-size: {theme.Typography.caption_size}px; }}
        QToolButton#RowChip {{ color: {c.text_primary}; background-color: {c.panel_elevated};
            border: 1px solid {c.border_subtle}; border-radius: {r.xs}px; padding: 1px 6px; }}
        QToolButton#RowChip:hover {{ border-color: {c.accent}; }}
        QToolButton#RepBtn {{ border: 1px solid {c.border_subtle}; border-radius: {r.xs}px;
            background: transparent; color: {c.text_secondary}; padding: 0 4px; }}
        QToolButton#RepBtn:hover {{ color: {c.text_primary}; border-color: {c.border}; }}
        """

    # ── populate / refresh ───────────────────────────────────────────────
    def update_from(self, sel: dict, *, current: bool, composable: bool = False,
                    enabled_wells=None, plate_factory=None) -> None:
        self._id = sel["id"]
        self._composable = bool(composable)
        if enabled_wells is not None:
            self._enabled_wells = list(enabled_wells)
        self._plate_factory = plate_factory
        hidden = bool(sel.get("hidden"))
        self._name.setText(str(sel.get("name", "")))
        f = self._name.font(); f.setStrikeOut(hidden); self._name.setFont(f)
        self._wells = list(sel.get("wells") or [])
        self._replicates = sel.get("replicates")
        self._chip.setText(str(len(self._wells)))
        col = _hex6(sel.get("color"))
        self._dot.setStyleSheet(
            f"QToolButton#RowDot {{ background-color: {col}; border: 1px solid {theme.Colors.border};"
            f" border-radius: 7px; min-width:14px; max-width:14px; min-height:14px; max-height:14px; }}")
        self._eye.blockSignals(True)
        self._eye.setChecked(hidden)
        self._eye.setText(_EYE_OFF if hidden else _EYE_ON)
        self._eye.setToolTip("Hidden — click to show" if hidden else "Visible — click to hide")
        self._eye.blockSignals(False)
        self.setProperty("current", "true" if current else "false")
        self._name.setProperty("hiddenRow", "true" if hidden else "false")
        # Hidden rows look dimmed via the RowName[hiddenRow="true"] QSS
        # selector (colour swap). Per-widget ``opacity:`` in QSS is a no-op
        # on QLabel — Qt only honours opacity via QGraphicsOpacityEffect —
        # so don't try to set it here.
        self.setStyleSheet(self._qss())
        if self._expanded:
            self._fill_chips()
        self.style().unpolish(self); self.style().polish(self)

    # ── expanded body ────────────────────────────────────────────────────
    def _clear_chips(self) -> None:
        while self._chips_inner.count():
            it = self._chips_inner.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _fill_chips(self) -> None:
        self._clear_chips()
        box = QWidget(self._chips_host)
        bv = QVBoxLayout(box)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(2)
        if not self._composable:
            self._build_readonly(bv, box)
        else:
            self._build_composable(bv, box)
        self._chips_inner.addWidget(box)
        self._chips_inner.addStretch(1)

    def _build_readonly(self, bv: QVBoxLayout, parent: QWidget) -> None:
        try:
            from widgets.chip_group import ChipGroup
            cg = ChipGroup(exclusive=False)
            for w in self._wells:
                cg.addChip(w, data=w)
            cg.setEnabled(False)
            bv.addWidget(cg)
        except Exception:
            lbl = QLabel(", ".join(self._wells) or "(no wells)", parent)
            lbl.setObjectName("Caption")
            lbl.setWordWrap(True)
            bv.addWidget(lbl)

    def _chip_strip(self, label: str, wells, *, in_sub: int | None,
                    parent: QWidget, trailing: QWidget | None = None) -> QWidget:
        w = QWidget(parent)
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(3)
        if label:
            lab = QLabel(label, w)
            lab.setObjectName("RepLabel")
            h.addWidget(lab)
        for tok in wells:
            h.addWidget(self._make_chip(tok, in_sub=in_sub, parent=w))
        h.addStretch(1)
        if trailing is not None:
            h.addWidget(trailing)
        return w

    def _build_composable(self, bv: QVBoxLayout, parent: QWidget) -> None:
        reps = self._replicates if isinstance(self._replicates, list) else None
        if not self._wells:
            lbl = QLabel("(no wells — use “+ wells…”)", parent)
            lbl.setObjectName("Caption")
            bv.addWidget(lbl)
        elif reps is not None:
            assigned: set = set()
            for k, sub in enumerate(reps):
                ung = QToolButton(parent)
                ung.setObjectName("RepBtn")
                ung.setText("⊟")
                ung.setToolTip("Ungroup this replicate (its wells become solo)")
                ung.clicked.connect(lambda _=False, kk=k: self._ungroup_sublist(kk))
                bv.addWidget(self._chip_strip(f"R{k + 1}:", sub, in_sub=k,
                                              parent=parent, trailing=ung))
                assigned |= set(sub)
            solo = [w for w in self._wells if w not in assigned]
            if solo:
                grp = QToolButton(parent)
                grp.setObjectName("RepBtn")
                grp.setText("⊞")
                grp.setToolTip("Bundle the solo wells into a new replicate")
                grp.clicked.connect(lambda _=False: self._group_solo())
                bv.addWidget(self._chip_strip("solo:", solo, in_sub=None,
                                              parent=parent, trailing=grp))
        else:
            mk = QToolButton(parent)
            mk.setObjectName("RepBtn")
            mk.setText("⊞")
            mk.setToolTip("Treat all of these wells as one condition's replicates")
            mk.clicked.connect(lambda _=False: self._make_replicate_all())
            bv.addWidget(self._chip_strip("wells:", self._wells, in_sub=None,
                                          parent=parent, trailing=mk))
        # + wells…
        addw = QToolButton(parent)
        addw.setObjectName("RepBtn")
        addw.setText("+ wells…")
        addw.setCursor(Qt.PointingHandCursor)
        addw.clicked.connect(self._open_compose_popover)
        self._compose_btn = addw
        roww = QWidget(parent)
        hl = QHBoxLayout(roww)
        hl.setContentsMargins(0, 2, 0, 0)
        hl.addWidget(addw)
        hl.addStretch(1)
        bv.addWidget(roww)

    def _make_chip(self, token: str, *, in_sub: int | None, parent: QWidget) -> QToolButton:
        b = QToolButton(parent)
        b.setObjectName("RowChip")
        b.setText(token)
        b.setCursor(Qt.PointingHandCursor)

        def _menu():
            m = QMenu(b)
            m.addAction("✕ Remove").triggered.connect(
                lambda _=False, t=token: self._remove_well(t))
            reps = self._replicates if isinstance(self._replicates, list) else []
            m.addSeparator()
            m.addAction("→ Move to new replicate").triggered.connect(
                lambda _=False, t=token: self._move_well_to_new(t))
            for j in range(len(reps)):
                if j == in_sub:
                    continue
                m.addAction(f"→ Move to R{j + 1}").triggered.connect(
                    lambda _=False, t=token, jj=j: self._move_well_to_sub(t, jj))
            if in_sub is not None:
                m.addAction("→ Make solo").triggered.connect(
                    lambda _=False, t=token: self._make_well_solo(t))
            m.exec(b.mapToGlobal(QPoint(0, b.height())))
        b.clicked.connect(_menu)
        return b

    # ── composition mutations (mutate _wells/_replicates, then emit) ─────
    def _emit_composition(self) -> None:
        self.compositionEdited.emit(self._id, list(self._wells),
                                    [list(s) for s in self._replicates]
                                    if isinstance(self._replicates, list) else None)

    def _normalised_reps(self):
        reps = self._replicates if isinstance(self._replicates, list) else None
        if reps is None:
            return None
        out = [list(s) for s in reps if s]
        return out or None

    def _remove_well(self, token: str) -> None:
        self._wells = [w for w in self._wells if w != token]
        if isinstance(self._replicates, list):
            self._replicates = [[w for w in s if w != token] for s in self._replicates]
        self._replicates = self._normalised_reps()
        self._fill_chips()
        self._emit_composition()

    def _move_well_to_new(self, token: str) -> None:
        if token not in self._wells:
            return
        reps = [[w for w in s if w != token] for s in (self._replicates or [])]
        reps.append([token])
        self._replicates = [s for s in reps if s] or None
        self._fill_chips()
        self._emit_composition()

    def _move_well_to_sub(self, token: str, k: int) -> None:
        if token not in self._wells or not isinstance(self._replicates, list):
            return
        new = []
        for i, s in enumerate(self._replicates):
            s2 = [w for w in s if w != token]
            if i == k and token not in s2:
                s2.append(token)
            new.append(s2)
        self._replicates = [s for s in new if s] or None
        self._fill_chips()
        self._emit_composition()

    def _make_well_solo(self, token: str) -> None:
        if not isinstance(self._replicates, list):
            return
        self._replicates = [[w for w in s if w != token] for s in self._replicates]
        self._replicates = self._normalised_reps()
        self._fill_chips()
        self._emit_composition()

    def _ungroup_sublist(self, k: int) -> None:
        if not isinstance(self._replicates, list) or not (0 <= k < len(self._replicates)):
            return
        self._replicates = [s for i, s in enumerate(self._replicates) if i != k] or None
        self._fill_chips()
        self._emit_composition()

    def _group_solo(self) -> None:
        assigned = {w for s in (self._replicates or []) for w in s}
        solo = [w for w in self._wells if w not in assigned]
        if not solo:
            return
        self._replicates = (self._replicates or []) + [solo]
        self._fill_chips()
        self._emit_composition()

    def _make_replicate_all(self) -> None:
        if isinstance(self._replicates, list) or not self._wells:
            return
        self._replicates = [list(self._wells)]
        self._fill_chips()
        self._emit_composition()

    def _open_compose_popover(self) -> None:
        from widgets.popover import Popover
        plate = None
        if callable(self._plate_factory):
            try:
                plate = self._plate_factory()
            except Exception:
                plate = None
        if plate is None:
            from widgets.well_plate_selector import WellPlateSelector
            plate = WellPlateSelector()
        try:
            plate.setActionsVisible(False)
        except Exception:
            pass
        enabled = list(self._enabled_wells) or list(_ALL_WELLS)
        plate.setEnabledWells(enabled)
        try:
            plate.setSelectionMode("select")
            plate.setRowColumnSelectable(True)
            plate.setSingleSelectionMode(False)
            plate.setDragMode("rect")   # rubber-band rectangle, not freeform paint
        except Exception:
            pass
        plate.setSelectedWellIds([w for w in self._wells if w in set(enabled)])
        state = {"ids": [w for w in self._wells if w in set(enabled)]}
        try:
            plate.selectionChanged.connect(lambda ids: state.__setitem__("ids", list(ids)))
        except Exception:
            pass

        content = QWidget()
        cv = QVBoxLayout(content)
        cv.setContentsMargins(theme.Spacing.sm, theme.Spacing.sm,
                              theme.Spacing.sm, theme.Spacing.sm)
        cv.setSpacing(theme.Spacing.sm)
        cv.addWidget(QLabel("Choose wells for this selection"))
        cv.addWidget(plate, 1)
        br = QHBoxLayout()
        br.addStretch(1)
        cancel = QPushButton("Cancel")
        okb = QPushButton("OK")
        okb.setObjectName("Primary")
        br.addWidget(cancel)
        br.addWidget(okb)
        cv.addLayout(br)

        pop = Popover(self)
        pop.setContentWidget(content)
        self._compose_pop = pop
        enabled_set = set(enabled)

        def _commit():
            picked = sorted({w for w in state["ids"] if w in enabled_set}, key=_well_rank)
            self._wells = picked
            if isinstance(self._replicates, list):
                pset = set(picked)
                self._replicates = self._normalised_reps_pruned(pset)
            pop.close()
            self._fill_chips()
            self._emit_composition()

        okb.clicked.connect(_commit)
        cancel.clicked.connect(pop.close)
        anchor = self._compose_btn or self._chev
        pop.popup(anchor, side="bottom", align="start")

    def _normalised_reps_pruned(self, allowed: set):
        reps = self._replicates if isinstance(self._replicates, list) else None
        if reps is None:
            return None
        out = [[w for w in s if w in allowed] for s in reps]
        out = [s for s in out if s]
        return out or None

    # ── interactions ─────────────────────────────────────────────────────
    def _on_body_press(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.activated.emit(self._id)

    def _toggle_expand(self) -> None:
        self._expanded = not self._expanded
        self._chev.setText(_CHEV_O if self._expanded else _CHEV_C)
        if self._expanded:
            self._fill_chips()
        else:
            self._clear_chips()
        self._chips_host.setVisible(self._expanded)
        self.expandToggled.emit(self._id, self._expanded)

    def _begin_rename(self, _event=None) -> None:
        if self._editor is not None:
            return
        self._editor = QLineEdit(self._name.text(), self._top)
        self._editor.setObjectName("RowEditor")
        self._editor.selectAll()
        lay = self._top.layout()
        idx = lay.indexOf(self._name)
        self._name.setVisible(False)
        lay.insertWidget(idx, self._editor, 1)
        self._editor.setFocus()
        self._editor.editingFinished.connect(self._finish_rename)

    def _finish_rename(self) -> None:
        if self._editor is None:
            return
        new = self._editor.text().strip()
        ed = self._editor
        self._editor = None
        ed.deleteLater()
        self._name.setVisible(True)
        if new and new != self._name.text():
            self.renameCommitted.emit(self._id, new)

    def trigger_rename(self) -> None:
        self._begin_rename()

    # ── drag-handle reorder ──────────────────────────────────────────────
    def _handle_press(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_y0 = event.globalPosition().y()
            self._drag_steps = 0
            self._handle.setCursor(Qt.ClosedHandCursor)

    def _handle_move(self, event) -> None:
        if self._drag_y0 is None:
            return
        h = max(1.0, float(self.height()))
        self._drag_steps = int(round((event.globalPosition().y() - self._drag_y0) / h))

    def _handle_release(self, _event) -> None:
        steps = self._drag_steps
        self._drag_y0 = None
        self._drag_steps = 0
        self._handle.setCursor(Qt.OpenHandCursor)
        if steps:
            self.dragMoveBy.emit(self._id, steps)


class SavedSelectionsList(QWidget):
    entryActivated = Signal(str)
    entryRenamed = Signal(str, str)
    entryRecoloured = Signal(str, str)
    entryVisibilityToggled = Signal(str, bool)     # (id, hidden)
    entryDuplicated = Signal(str, str)             # (new_id, src_id)
    entryDeleted = Signal(str)
    entryExportRequested = Signal(str)
    orderChanged = Signal(list)                    # [id, …] in stored order
    addFromSelectionRequested = Signal()
    importRequested = Signal()
    wellsChanged = Signal(str, list)               # (id, wells)
    replicatesChanged = Signal(str, list)          # (id, replicates) — [] for "no structure"
    selectionsChanged = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SavedSelectionsList")
        self._selections: list[dict] = []
        self._current_id: str = ""
        self._rows: dict[str, _SelectionRow] = {}
        self._recolour_pop = None
        self._composable = False
        self._compact = False  # rail-side compact / read-only render.
        self._enabled_wells: list[str] = list(_ALL_WELLS)
        self._well_plate_factory = None

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(theme.Spacing.sm)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("SavedSelScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body = QWidget()
        self._body.setObjectName("SavedSelBody")
        self._body.setAttribute(Qt.WA_StyledBackground, True)
        self._vbox = QVBoxLayout(self._body)
        self._vbox.setContentsMargins(theme.Spacing.xs, theme.Spacing.xs,
                                      theme.Spacing.xs, theme.Spacing.xs)
        self._vbox.setSpacing(2)
        self._vbox.addStretch(1)
        self._scroll.setWidget(self._body)
        v.addWidget(self._scroll, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(theme.Spacing.sm)
        self._from_btn = QPushButton("From selection")
        self._from_btn.clicked.connect(self.addFromSelectionRequested.emit)
        self._import_btn = QPushButton("Import…")
        self._import_btn.clicked.connect(self.importRequested.emit)
        footer.addWidget(self._from_btn)
        footer.addWidget(self._import_btn)
        footer.addStretch(1)
        v.addLayout(footer)

        self.setStyleSheet(self._qss())

    def _qss(self) -> str:
        c, r = theme.Colors, theme.Radii
        return f"""
        QScrollArea#SavedSelScroll {{ background: transparent; }}
        QWidget#SavedSelBody {{ background-color: {c.panel}; border: 1px solid {c.border_subtle};
            border-radius: {r.sm}px; }}
        """

    # ── model API ────────────────────────────────────────────────────────
    def setSelections(self, selections) -> None:
        self._selections = [self._sanitise(dict(s)) for s in selections]
        self._ensure_unique()
        if self._current_id not in {s["id"] for s in self._selections}:
            self._current_id = self._selections[0]["id"] if self._selections else ""
        self._rebuild()

    def updateSelections(self, selections) -> None:
        """Like ``setSelections`` but, when the id-list (and order) is unchanged,
        updates each row in place instead of rebuilding — so an expanded /
        in-progress-edit row isn't collapsed by an upstream refresh."""
        sels = [self._sanitise(dict(s)) for s in selections]
        new_ids = [s["id"] for s in sels]
        if new_ids and new_ids == [s["id"] for s in self._selections]:
            self._selections = sels
            if self._current_id not in set(new_ids):
                self._current_id = new_ids[0]
            self._refresh_rows()
        else:
            self.setSelections(sels)

    def selections(self) -> list[dict]:
        return [dict(s) for s in self._selections]

    def setCurrentId(self, sel_id: str) -> None:
        if sel_id in {s["id"] for s in self._selections} and sel_id != self._current_id:
            self._current_id = sel_id
            self._refresh_current()

    def currentId(self) -> str:
        return self._current_id

    # ── composition config ───────────────────────────────────────────────
    def setComposable(self, on: bool) -> None:
        on = bool(on)
        if on != self._composable:
            self._composable = on
            self._rebuild()

    def isComposable(self) -> bool:
        return self._composable

    def setCompact(self, on: bool) -> None:
        """Rail-side compact / read-only mode.

        Hides per-row drag handle, eye, and kebab; disables recolour and
        rename; the row still emits ``entryActivated`` on click. Use this in
        the left rail's "Saved [N]" list (mockup §2.5). Composable mode is
        forced off whenever compact mode is on.
        """
        on = bool(on)
        if on == self._compact:
            return
        self._compact = on
        if on and self._composable:
            self._composable = False
        for row in self._rows.values():
            row.setCompact(on)
        # The "From selection" / "Import…" footer buttons exist to manage
        # the editable list in Sample Definitions; in the left rail's
        # read-only mirror they're noise.
        try:
            self._from_btn.setVisible(not on)
            self._import_btn.setVisible(not on)
        except Exception:
            pass

    def isCompact(self) -> bool:
        return self._compact

    def setEnabledWells(self, wells) -> None:
        self._enabled_wells = _clean_wells(wells) or list(_ALL_WELLS)
        self._refresh_rows()

    def enabledWells(self) -> list[str]:
        return list(self._enabled_wells)

    def setWellPlateFactory(self, factory) -> None:
        self._well_plate_factory = factory if callable(factory) else None
        self._refresh_rows()

    def setSelectionWells(self, sel_id: str, wells) -> None:
        s = self._by_id(sel_id)
        if s is None:
            return
        s["wells"] = _clean_wells(wells)
        s["replicates"] = _clean_reps(s.get("replicates"), allowed=set(s["wells"]))
        self._refresh_rows()

    def setSelectionReplicates(self, sel_id: str, reps) -> None:
        s = self._by_id(sel_id)
        if s is None:
            return
        s["replicates"] = _clean_reps(reps, allowed=set(s.get("wells") or []))
        self._refresh_rows()

    # ── helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _sanitise(s: dict) -> dict:
        s.setdefault("id", "")
        s.setdefault("name", "Selection")
        s["color"] = _hex6(s.get("color", "#5B9BF8"))
        s["hidden"] = bool(s.get("hidden"))
        s["wells"] = list(s.get("wells") or [])
        if "replicates" not in s:
            s["replicates"] = None
        s.setdefault("source", "user")
        return s

    def _mint_id(self) -> str:
        taken = {s["id"] for s in self._selections}
        while True:
            i = _uuid.uuid4().hex[:8]
            if i not in taken:
                return i

    def _ensure_unique(self) -> None:
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        for s in self._selections:
            if not s["id"] or s["id"] in seen_ids:
                s["id"] = self._mint_id_excluding(seen_ids)
            seen_ids.add(s["id"])
            s["name"] = self._unique_name(s["name"], taken=seen_names)
            seen_names.add(s["name"])

    def _mint_id_excluding(self, taken: set[str]) -> str:
        while True:
            i = _uuid.uuid4().hex[:8]
            if i not in taken and all(x["id"] != i for x in self._selections):
                return i

    def _unique_name(self, name: str, *, exclude_id: str | None = None,
                     taken: set[str] | None = None) -> str:
        name = name.strip() or "Selection"
        if taken is None:
            taken = {s["name"] for s in self._selections if s["id"] != exclude_id}
        if name not in taken:
            return name
        base = f"{name}_v2"
        if base not in taken:
            return base
        i = 2
        while f"{base} {i}" in taken:
            i += 1
        return f"{base} {i}"

    def _by_id(self, sel_id: str) -> dict | None:
        for s in self._selections:
            if s["id"] == sel_id:
                return s
        return None

    def _index_of(self, sel_id: str) -> int:
        for i, s in enumerate(self._selections):
            if s["id"] == sel_id:
                return i
        return -1

    def _display_order(self) -> list[dict]:
        return ([s for s in self._selections if not s["hidden"]]
                + [s for s in self._selections if s["hidden"]])

    def _row_kwargs(self) -> dict:
        return dict(composable=self._composable, enabled_wells=self._enabled_wells,
                    plate_factory=self._well_plate_factory)

    # ── view build ───────────────────────────────────────────────────────
    def _rebuild(self) -> None:
        while self._vbox.count() > 1:
            it = self._vbox.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._rows = {}
        order = self._display_order()
        if not order:
            # Fill the empty panel with a centered hint instead of leaving a
            # large void above the marooned "From selection" / "Import" footer.
            self._vbox.insertStretch(0)
            hint = QLabel(
                "No groups yet.\nUse “+ Add” above or “From selection” below "
                "to create one."
            )
            hint.setObjectName("Muted")
            hint.setAlignment(Qt.AlignCenter)
            hint.setWordWrap(True)
            self._vbox.insertWidget(self._vbox.count() - 1, hint)
        for s in order:
            row = _SelectionRow(s, self._body)
            row.activated.connect(self._on_activated)
            row.visibilityToggled.connect(self._on_visibility)
            row.recolourRequested.connect(self._on_recolour_requested)
            row.renameCommitted.connect(self._on_renamed)
            row.kebabRequested.connect(self._on_kebab)
            row.dragMoveBy.connect(self._on_drag_move)
            row.compositionEdited.connect(self._on_composition_edited)
            row.update_from(s, current=(s["id"] == self._current_id), **self._row_kwargs())
            if self._compact:
                row.setCompact(True)
            self._vbox.insertWidget(self._vbox.count() - 1, row)
            self._rows[s["id"]] = row

    def _refresh_rows(self) -> None:
        for s in self._selections:
            row = self._rows.get(s["id"])
            if row is not None:
                row.update_from(s, current=(s["id"] == self._current_id), **self._row_kwargs())

    def _refresh_current(self) -> None:
        for sid, row in self._rows.items():
            s = self._by_id(sid)
            if s is not None:
                row.update_from(s, current=(sid == self._current_id), **self._row_kwargs())

    def _emit_changed(self) -> None:
        self.selectionsChanged.emit(self.selections())

    # ── row callbacks ────────────────────────────────────────────────────
    def _on_activated(self, sel_id: str) -> None:
        if sel_id != self._current_id:
            self._current_id = sel_id
            self._refresh_current()
        self.entryActivated.emit(sel_id)

    def _on_visibility(self, sel_id: str, hidden: bool) -> None:
        s = self._by_id(sel_id)
        if s is None:
            return
        s["hidden"] = bool(hidden)
        self._rebuild()
        self.entryVisibilityToggled.emit(sel_id, s["hidden"])
        self._emit_changed()

    def _on_renamed(self, sel_id: str, new_name: str) -> None:
        s = self._by_id(sel_id)
        if s is None:
            return
        final = self._unique_name(new_name, exclude_id=sel_id)
        if final == s["name"]:
            return
        s["name"] = final
        self._refresh_rows()
        self.entryRenamed.emit(sel_id, final)
        self._emit_changed()

    def _on_composition_edited(self, sel_id: str, wells, reps) -> None:
        s = self._by_id(sel_id)
        if s is None:
            return
        s["wells"] = list(wells)
        s["replicates"] = ([list(x) for x in reps] if isinstance(reps, (list, tuple)) and reps
                           else None)
        row = self._rows.get(sel_id)
        if row is not None:
            row.update_from(s, current=(sel_id == self._current_id), **self._row_kwargs())
        self.wellsChanged.emit(sel_id, list(s["wells"]))
        self.replicatesChanged.emit(sel_id, [list(x) for x in (s["replicates"] or [])])
        self._emit_changed()

    def _on_recolour_requested(self, sel_id: str, anchor: QWidget) -> None:
        s = self._by_id(sel_id)
        if s is None:
            return
        from widgets.popover import Popover
        from widgets.color_swatch_row import ColorSwatchRow
        pop = Popover(self)
        recents = [x["color"] for x in self._selections if x["id"] != sel_id][:8]
        sw = ColorSwatchRow(allow_custom=True, recents=recents)
        sw.setCurrentColor(s["color"])
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(theme.Spacing.sm, theme.Spacing.sm,
                              theme.Spacing.sm, theme.Spacing.sm)
        cl.addWidget(QLabel("Recolour"))
        cl.addWidget(sw)
        pop.setContentWidget(content)

        def _picked(c: QColor):
            s["color"] = _hex6(c)
            self._refresh_rows()
            self.entryRecoloured.emit(sel_id, s["color"])
            self._emit_changed()
        sw.colorPicked.connect(_picked)
        self._recolour_pop = pop
        pop.popup(anchor, side="bottom", align="start")

    def _on_kebab(self, sel_id: str, anchor: QWidget) -> None:
        s = self._by_id(sel_id)
        if s is None:
            return
        m = QMenu(self)
        a_rename = m.addAction("Rename")
        a_recolour = m.addAction("Recolour…")
        a_dup = m.addAction("Duplicate")
        a_hide = m.addAction("Show" if s["hidden"] else "Hide")
        m.addSeparator()
        idx = self._index_of(sel_id)
        a_up = m.addAction("Move up")
        a_down = m.addAction("Move down")
        a_up.setEnabled(idx > 0)
        a_down.setEnabled(0 <= idx < len(self._selections) - 1)
        m.addSeparator()
        a_export = m.addAction("Export…")
        a_delete = m.addAction("Delete")
        chosen = m.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))
        if chosen is None:
            return
        if chosen is a_rename:
            row = self._rows.get(sel_id)
            if row is not None:
                row.trigger_rename()
        elif chosen is a_recolour:
            self._on_recolour_requested(sel_id, anchor)
        elif chosen is a_dup:
            self._duplicate(sel_id)
        elif chosen is a_hide:
            self._on_visibility(sel_id, not s["hidden"])
        elif chosen is a_up:
            self._move(sel_id, -1)
        elif chosen is a_down:
            self._move(sel_id, +1)
        elif chosen is a_export:
            self.entryExportRequested.emit(sel_id)
        elif chosen is a_delete:
            self._delete(sel_id)

    # ── mutations ────────────────────────────────────────────────────────
    def _duplicate(self, sel_id: str) -> None:
        s = self._by_id(sel_id)
        if s is None:
            return
        new = dict(s)
        new["id"] = self._mint_id()
        new["name"] = self._unique_name(f"{s['name']} copy")
        new["source"] = "user"
        idx = self._index_of(sel_id)
        self._selections.insert(idx + 1, new)
        self._rebuild()
        self.entryDuplicated.emit(new["id"], sel_id)
        self.orderChanged.emit([x["id"] for x in self._selections])
        self._emit_changed()

    def _delete(self, sel_id: str) -> None:
        idx = self._index_of(sel_id)
        if idx < 0:
            return
        self._selections.pop(idx)
        if self._current_id == sel_id:
            self._current_id = self._selections[min(idx, len(self._selections) - 1)]["id"] if self._selections else ""
        self._rebuild()
        self.entryDeleted.emit(sel_id)
        self.orderChanged.emit([x["id"] for x in self._selections])
        self._emit_changed()

    def _move(self, sel_id: str, step: int) -> None:
        idx = self._index_of(sel_id)
        if idx < 0:
            return
        new_idx = max(0, min(len(self._selections) - 1, idx + step))
        if new_idx == idx:
            return
        self._selections.insert(new_idx, self._selections.pop(idx))
        self._rebuild()
        self.orderChanged.emit([x["id"] for x in self._selections])
        self._emit_changed()

    def _on_drag_move(self, sel_id: str, steps: int) -> None:
        QTimer.singleShot(0, lambda: self._move(sel_id, steps))


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QHBoxLayout as _HB, QLabel, QVBoxLayout, QWidget as _QW,
    )

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("SavedSelectionsList — demo")
    pad = theme.Spacing.lg
    lay = QVBoxLayout(root)
    lay.setContentsMargins(pad, pad, pad, pad)
    lay.setSpacing(theme.Spacing.md)

    title = QLabel("SavedSelectionsList (editable + composable)")
    title.setObjectName("Title")
    lay.addWidget(title)

    lst = SavedSelectionsList()
    lst.setEnabledWells(_ALL_WELLS)
    lst.setComposable(True)
    tr = theme.Colors.trace
    lst.setSelections([
        {"id": "aaaa1111", "name": "Control", "color": tr[0], "hidden": False,
         "wells": ["A01", "A02", "A03", "B01", "B02", "B03", "C03"],
         "replicates": [["A01", "A02", "A03"], ["B01", "B02", "B03"]], "source": "bar_group"},
        {"id": "bbbb2222", "name": "Drug A — 1µM", "color": tr[1], "hidden": False,
         "wells": ["C01", "C02", "C03"], "replicates": [["C01", "C02", "C03"]], "source": "rep_set"},
        {"id": "cccc3333", "name": "Drug A — 10µM", "color": tr[2], "hidden": False,
         "wells": ["D01", "D02", "D03"], "replicates": None, "source": "user"},
        {"id": "dddd4444", "name": "Untreated", "color": theme.Colors.text_muted, "hidden": True,
         "wells": ["E01", "E02"], "replicates": None, "source": "import"},
    ])
    lst.setCurrentId("bbbb2222")
    lst.setMinimumHeight(300)
    lay.addWidget(lst, 1)

    row = _HB()
    cb = QCheckBox("composable")
    cb.setChecked(True)
    cb.toggled.connect(lst.setComposable)
    row.addWidget(cb)
    row.addStretch(1)
    lay.addLayout(row)

    echo = QLabel("(expand a row → edit chips / replicates / + wells…)")
    echo.setObjectName("Secondary")
    echo.setWordWrap(True)
    lay.addWidget(echo)
    lst.entryActivated.connect(lambda i: echo.setText(f"activated → {i}"))
    lst.entryRenamed.connect(lambda i, n: echo.setText(f"renamed {i} → {n}"))
    lst.entryRecoloured.connect(lambda i, c: echo.setText(f"recoloured {i} → {c}"))
    lst.entryVisibilityToggled.connect(lambda i, h: echo.setText(f"{i} hidden={h}"))
    lst.orderChanged.connect(lambda ids: echo.setText("order → " + ", ".join(ids)))
    lst.entryDeleted.connect(lambda i: echo.setText(f"deleted → {i}"))
    lst.wellsChanged.connect(lambda i, w: echo.setText(f"wells[{i}] → {w}"))
    lst.replicatesChanged.connect(lambda i, r: echo.setText(f"replicates[{i}] → {r}"))
    lst.addFromSelectionRequested.connect(lambda: echo.setText("from-selection requested"))
    lst.importRequested.connect(lambda: echo.setText("import requested"))

    root.resize(480, 480)
    root.show()
    _sys.exit(app.exec())
