"""The Nanowells page's *Subset* control and its editor: which nanowells every view draws.

The band carries a combo with the Starting cells presets the page has always offered (*All nanowells*, *Starting
cells ≥1*, *1*, *1–2*, *≥2*) and *Custom subset…*, and an **Edit…** button that shows the editor under the band.
The editor holds any number of conditions — a field (the cells at the start, a metric, the ring or distance from the
centre, the address, the imaging field), the timepoint a per-timepoint field is read at, a comparison and its
value(s) — and whether a nanowell must meet *all* of them or *any*. Edits change only the conditions written in
the editor, and the line under them says they are not applied yet; **Apply** makes them the page's subset (one
redraw), **Reset** puts back the subset in use. A draft that is not a subset (a range upside down, a tile condition
with no tile) says why, and Apply waits until it is fixed.

Every control writes through :mod:`.controller`; nothing here filters. The editor is a panel in the page, never a
dialog, so nothing modal is reached from it.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QToolButton, QVBoxLayout,
    QWidget,
)

from core.safe import swallow
from review.features.nanowell import controller as nwc
from review.features.nanowell import starting_cells_compute as sc
from review.features.nanowell import subset_compute as ss
from ui import theme
from widgets.flow_layout import FlowLayout, flow_group, sync_flow_height
from widgets.owned_action import owned_action
from widgets.spin_box import DoubleSpinBox

#: The combo's entries: the Starting cells presets as the band names them, then the editor.
_PRESET_LABELS = {"any": "All nanowells", "occupied": "Starting cells ≥1 (occupied)",
                  "single": "Starting cells 1 (single cell)", "one_two": "Starting cells 1–2",
                  "two_plus": "Starting cells ≥2"}
CUSTOM_LABEL = "Custom subset…"
#: The combo's data for a saved subset is this prefix and its name.
SAVED_PREFIX = "saved:"
#: The widest a bound can be typed: far beyond any count, ring, distance or intensity a plate holds.
_LIMIT = 1e9
_DECIMALS = 3
_START_LIMIT = 999
#: The characters a combo in the editor makes room for before eliding (its list still shows every entry in full). A
#: combo sizes to its longest entry by default — a metric's whole name — which made one condition wider than a phone.
_COMBO_CHARS = 12
#: The most of its page's height the editor takes before it scrolls: the figure the subset is for keeps the rest.
#: Twenty-four conditions, or two on a phone (where one condition wraps onto two or three lines of touch-sized
#: controls), otherwise needed more than the page and were squeezed until every control was cut in half.
_ROWS_SHARE = 0.45
#: The fewest text lines of the editor shown when the page is short, before the figure gives up any more room.
_ROWS_MIN_LINES = 3

_SUBSET_TIP = ("Which nanowells every view draws and summarises. The presets keep the nanowells that held this "
               "many cells at the Baseline; Custom subset… and Edit… build any combination of conditions — starting "
               "cells, a metric at a timepoint, the ring or distance from the centre, the address, the imaging "
               "field. Every figure states how many nanowells it kept; the Array view dims the others instead of "
               "hiding them, and Save nanowell data… keeps every row and flags them.\n"
               "For clonality, use the Earliest baseline: a count taken later may follow a division.")
_EDIT_TIP = "Show the subset's conditions: add, change or remove any of them, and name and save the subset."
_APPLY_TIP = "Use the conditions as written: every view redraws with them."
_RESET_TIP = "Put back the conditions of the subset in use, discarding the edits not applied."
_NAME_TIP = ("A name for the subset as written, kept with this dataset: it is listed in the Subset box from then on, "
             "one click to use again. Saving under a name already kept replaces that subset.")
_SAVE_TIP = "Keep the subset as written under the name typed beside it, in this dataset."
_RENAME_TIP = "Give the saved subset in use the name typed beside it."
_DELETE_TIP = "Forget the saved subset in use. The figure keeps using it until you choose another."
_MATCH_TIP = ("All: a nanowell is used only when it meets every condition.\n"
              "Any: a nanowell is used when it meets at least one.")
_FIELD_TIP = ("What the condition tests: the cells at the start (the count at the Baseline), a metric, where the "
              "nanowell sits in its array, the imaging field it was cut from, or whether it lay on a tile seam "
              "(measured from a crop blended from the tiles that cover it). On a tile seam: no at every timepoint "
              "leaves the seam nanowells out; yes at any timepoint keeps only them.\n"
              "Identity verdict: the identity check's verdict on the nanowell's timepoint. Array rim fitted: whether "
              "a circular rim was fitted to the well's array at that timepoint (no for a fit that failed, and for "
              "an array whose outline is not a circle).")
_AT_TIP = ("The timepoint the value is read at: the baseline (the count's), the well's earliest or latest, a named "
           "timepoint, or at any / every timepoint of the well (every: the nanowell needs a passing value at each).")
_OP_TIP = "How the value is compared. Both bounds of a range are included."
_TILES_TIP = "The imaging fields (tiles) the condition names."
_VERDICTS_TIP = ("The identity check's verdicts the condition names. A verdict is the timepoint's, shared by every "
                 "nanowell of the well there: partial means most nanowells were confirmed and some left out, and "
                 "inconclusive that the check could not decide (for one, the array's fitted rim centre disagreed "
                 "with the square's registration by more than the fit can be trusted to). Identity contradicted is "
                 "the per-nanowell answer.")
_WINDOW_TIP = ("The nanowells a Distribution window held when it was turned into a subset (Use as Subset). The list is "
               "frozen; choose inside to keep them or outside to keep the rest, and remove the row to bring the others back.")
_GATE_TIP = ("A gate drawn on the Scatter Plot over two nanowell metrics (Observations: Nanowells, or the "
             "\"Nanowell ▸\" axes). Inside: the nanowell's point at the chosen timepoint lies in the box. The box "
             "is read from the scatter each time, so moving the gate there moves the subset.")


def _flow(parent: QWidget, h_spacing: int = 8) -> FlowLayout:
    """A row of the editor that wraps onto as many lines as its width needs, instead of cutting every control and
    label to a few letters at phone width ("Use the nanowe", "Star", "betw")."""
    flow = FlowLayout(parent, h_spacing=h_spacing)
    sync_flow_height(parent)
    return flow


def _narrow(combo: QComboBox) -> QComboBox:
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(_COMBO_CHARS)
    return combo


class _EditorScroll(QScrollArea):
    """The editor, scrolling: as tall as it needs, up to :data:`_ROWS_SHARE` of the page it sits in.

    That share is its minimum as well as its size, so the figure below — which stretches — cannot squeeze it to a
    line; and a share rather than a height, so the figure keeps the rest on any screen. The rows wrap to the width,
    never past it, so it scrolls only vertically. It asks for its content's height at its own width (the content's
    flows reserve their wrapped height, ``sync_flow_height``), and asks again when the content or the page changes
    size — a condition added, a phone turned."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._page = None
        #: The share of the page the editor may take: :data:`_ROWS_SHARE` beside the figure, all of it while the
        #: figure has stepped aside for it (:func:`share_page`).
        self.share = _ROWS_SHARE

    def setWidget(self, widget: QWidget) -> None:  # Qt override
        super().setWidget(widget)
        widget.installEventFilter(self)

    def _need(self) -> int:
        """The content's height at the width it is shown at: its wrapped lines and status sentence, measured there
        (a word-wrapped label's plain size hint is the height of a narrow paragraph, far taller than the line it is)."""
        body = self.widget()
        if body is None:
            return 0
        lay = body.layout()
        width = self.viewport().width()
        if lay is not None and lay.hasHeightForWidth() and width > 0:
            return max(lay.totalHeightForWidth(width), body.minimumSizeHint().height())
        return max(body.sizeHint().height(), body.minimumSizeHint().height())

    def _floor(self) -> int:
        return self.fontMetrics().height() * _ROWS_MIN_LINES

    def height_at(self, share: float) -> int:
        """The height the editor's content is given at *share* of its page."""
        page = self._page.height() if self._page is not None else self.window().height()
        return min(self._need(), max(self._floor(), int(page * share)))

    def _height(self) -> int:
        return self.height_at(self.share)

    def sizeHint(self) -> QSize:  # Qt override
        body = self.widget()
        return QSize(body.sizeHint().width() if body is not None else 0, self._height())

    def minimumSizeHint(self) -> QSize:  # Qt override
        # Beside the figure its share is its minimum too; alone on the page it takes whatever the page has left.
        return QSize(0, self._height() if self.share < 1 else min(self._need(), self._floor()))

    def showEvent(self, event) -> None:  # Qt override
        super().showEvent(event)
        # The page is the editor's parent: the widget whose height is shared between the editor and the figure.
        editor = self.parentWidget()
        page = editor.parentWidget() if editor is not None else None
        if page is not None and page is not self._page:
            page.installEventFilter(self)
            self._page = page
        self.updateGeometry()

    def eventFilter(self, obj, event) -> bool:  # Qt naming
        if event.type() in (QEvent.LayoutRequest, QEvent.Resize) and (obj is self.widget() or obj is self._page):
            self.updateGeometry()
        return False


def _select(combo, value) -> None:
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return


def preset_of(subset: ss.Subset) -> str:
    """The combo entry *subset* is: a Starting cells preset, *any* for no subset, or custom."""
    if not subset.is_active():
        return "any"
    if subset.is_starting_only():
        lo, hi = subset.conditions[0].bounds()
        try:
            return sc.preset_of(*sc.normalise(lo, hi))
        except ValueError:
            return sc.CUSTOM
    return sc.CUSTOM


class _ConditionRow(QWidget):
    """One condition's controls: field, timepoint, comparison, value(s) or tiles, and a remove button."""

    def __init__(self, editor, condition: ss.Condition):
        super().__init__()
        self._editor = editor
        # The condition's controls wrap within the row, and × stays at its right edge whatever the width, so a
        # condition that takes two lines on a phone still reads as one condition with one remove button.
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        controls = QWidget(self)
        flow = _flow(controls)
        self.field_cb = QComboBox()
        self.field_cb.setToolTip(_FIELD_TIP)
        group = None
        for name, label, kind in editor.fields(condition.field):
            if kind != group and self.field_cb.count():
                self.field_cb.insertSeparator(self.field_cb.count())
            group = kind
            self.field_cb.addItem(label, name)
        self.at_cb = QComboBox()
        self.at_cb.setToolTip(_AT_TIP)
        for key, label in ss.ATS.items():
            self.at_cb.addItem(label, key)
        for tp, text in editor.timepoints(condition.at):
            self.at_cb.addItem(f"at {text}", ss.TP_PREFIX + tp)
        self.op_cb = QComboBox()
        self.op_cb.setToolTip(_OP_TIP)
        self.a_spin, self.b_spin = DoubleSpinBox(), DoubleSpinBox()
        self.and_label = QLabel("and")
        self.tiles_btn = QToolButton()
        self.tiles_btn.setToolTip(_TILES_TIP)
        self.tiles_btn.setPopupMode(QToolButton.InstantPopup)
        self.tiles_btn.setProperty("variant", "secondary")
        self.tiles_btn.setProperty("hasMenu", True)
        self.tiles_menu = QMenu(self.tiles_btn)
        self.tiles_btn.setMenu(self.tiles_menu)
        #: The category field the values menu was filled for (:meth:`_fill_tiles`).
        self._filled_for = ss.TILE
        self.gate_cb = QComboBox()
        self.gate_cb.setToolTip(_GATE_TIP)
        # A window condition is a frozen list of nanowells (``subset_compute.WINDOW``): it cannot be built here, so the
        # row names it and keeps its sites, and only inside | outside can be changed.
        self._frozen = condition if ss.is_window(condition.field) else None
        self.window_label = QLabel()
        self.window_label.setToolTip(_WINDOW_TIP)
        self.remove_btn = QToolButton()
        self.remove_btn.setText("×")
        self.remove_btn.setToolTip("Remove this condition")
        self.remove_btn.setProperty("variant", "secondary")
        for combo in (self.field_cb, self.at_cb, self.gate_cb):     # the comparisons are short: they keep their width
            _narrow(combo)
        self.window_label.setWordWrap(True)
        # The bounds as one item, so "and" never wraps away from either of them.
        self.bounds = flow_group(controls, self.a_spin, self.and_label, self.b_spin)
        for w in (self.field_cb, self.at_cb, self.op_cb, self.gate_cb, self.window_label, self.bounds,
                  self.tiles_btn):
            flow.addWidget(w)
        row.addWidget(controls, 1)
        row.addWidget(self.remove_btn, 0, Qt.AlignTop)
        self._show(condition)
        self.field_cb.currentIndexChanged.connect(self._field_changed)
        self.at_cb.currentIndexChanged.connect(lambda _i: editor.changed())
        self.op_cb.currentIndexChanged.connect(self._op_changed)
        self.a_spin.valueChanged.connect(lambda _v: editor.changed())
        self.b_spin.valueChanged.connect(lambda _v: editor.changed())
        self.gate_cb.currentIndexChanged.connect(lambda _i: editor.changed())
        self.remove_btn.clicked.connect(lambda: editor.remove(self))

    # ── what the row shows ──────────────────────────────────────────────
    def _show(self, c: ss.Condition) -> None:
        _select(self.field_cb, c.field)
        self._fill_ops(c.field, c.op)
        self._fit_spins(c.field)
        if c.a is not None:
            self.a_spin.setValue(float(c.a))
        if c.b is not None:
            self.b_spin.setValue(float(c.b))
        _select(self.at_cb, c.at or "baseline")
        self._fill_tiles(c.field, c.values if ss.is_category(c.field) else ())
        self.fill_gates(c.values[0] if ss.is_gate(c.field) and c.values else None)
        if self._frozen is not None:
            self.field_cb.setEnabled(False)
            self.window_label.setText(f"“{c.values[0] if c.values else ''}” · {len(c.sites):,} nanowells")
        self._visibility()

    def _fill_ops(self, name: str, op: str) -> None:
        blocked = self.op_cb.blockSignals(True)
        self.op_cb.clear()
        for key, label in ss.ops_for(name).items():
            self.op_cb.addItem(label, key)
        _select(self.op_cb, op if op in ss.ops_for(name) else next(iter(ss.ops_for(name))))
        self.op_cb.blockSignals(blocked)

    def _fit_spins(self, name: str) -> None:
        whole = name in ss.INTEGER_FIELDS
        for spin in (self.a_spin, self.b_spin):
            blocked = spin.blockSignals(True)
            spin.setDecimals(0 if whole else _DECIMALS)
            spin.setRange(0 if name == ss.START else -_LIMIT, _START_LIMIT if name == ss.START else _LIMIT)
            spin.blockSignals(blocked)

    def _fill_tiles(self, name, chosen) -> None:
        """Offer the values a category condition on *name* can name — the identity verdicts, or the imaging fields
        recorded — each checked when it is in *chosen*, which is kept even when no longer recorded."""
        self.tiles_menu.clear()
        identity = name == ss.IDENTITY
        values = (list(ss.IDENTITY_VERDICTS) if identity
                  else sorted(set(self._editor.tiles()) | set(chosen)))
        self.tiles_btn.setToolTip(_VERDICTS_TIP if identity else _TILES_TIP)
        for value in values:
            act = owned_action(self.tiles_menu, ss.value_label(name, value))
            act.setData(value)
            act.setCheckable(True)
            act.setChecked(value in chosen)
            act.toggled.connect(lambda _on: self._tiles_changed())
        if self.tiles_menu.isEmpty():
            self.tiles_menu.addAction("No imaging fields recorded").setEnabled(False)
        self._filled_for = name
        self._tiles_text()

    def fill_gates(self, chosen: str | None = None) -> None:
        """Offer the nanowell gates drawn now, keeping *chosen* (or the current choice) even when it is gone, so
        the row still says which gate the subset names."""
        chosen = self.gate_cb.currentData() if chosen is None else chosen
        names = list(self._editor.gates())
        if chosen and chosen not in names:
            names.append(chosen)
        if [self.gate_cb.itemData(i) for i in range(self.gate_cb.count())] == names:
            return
        blocked = self.gate_cb.blockSignals(True)
        self.gate_cb.clear()
        for name in names:
            self.gate_cb.addItem(name if name in self._editor.gates() else f"{name} (gone)", name)
        if chosen in names:
            _select(self.gate_cb, chosen)
        self.gate_cb.blockSignals(blocked)

    def _chosen_tiles(self) -> tuple:
        return tuple(a.data() for a in self.tiles_menu.actions() if a.isCheckable() and a.isChecked())

    def _tiles_text(self) -> None:
        chosen = self._chosen_tiles()
        noun = "verdicts" if self._filled_for == ss.IDENTITY else "fields"
        self.tiles_btn.setText(", ".join(ss.value_label(self._filled_for, v) for v in chosen) if 0 < len(chosen) <= 2
                               else f"{len(chosen)} {noun}" if chosen else "choose…")

    def _visibility(self) -> None:
        name = self.field_cb.currentData()
        category = ss.is_category(name)
        gate = ss.is_gate(name)
        window = self._frozen is not None
        self.window_label.setVisible(window)
        ranged = self.op_cb.currentData() in ("between", "outside")
        self.at_cb.setVisible(ss.per_timepoint(name))
        self.gate_cb.setVisible(gate)
        category = category or gate or window            # no bound to type for any of these
        self.a_spin.setVisible(not category and not ss.is_flag(name))
        self.and_label.setVisible(not category and ranged)
        self.b_spin.setVisible(not category and ranged)
        self.bounds.setVisible(not self.a_spin.isHidden())  # the flow sees the group: an empty one takes no slot
        self.tiles_btn.setVisible(ss.is_category(name))

    # ── edits ───────────────────────────────────────────────────────────
    def _field_changed(self, _index=None) -> None:
        name = self.field_cb.currentData()
        self._fill_ops(name, self.op_cb.currentData())
        self._fit_spins(name)
        if ss.is_category(name) and name != self._filled_for:
            self._fill_tiles(name, ())                       # a tile is no verdict, nor a verdict a tile
        self._visibility()
        self._editor.changed()

    def _op_changed(self, _index=None) -> None:
        if self.op_cb.currentData() in ("between", "outside") and self.b_spin.value() < self.a_spin.value():
            blocked = self.b_spin.blockSignals(True)
            self.b_spin.setValue(self.a_spin.value())     # a range starts as one value, never upside down
            self.b_spin.blockSignals(blocked)
        self._visibility()
        self._editor.changed()

    def _tiles_changed(self) -> None:
        self._tiles_text()
        self._editor.changed()

    def condition(self) -> ss.Condition:
        """The condition as written (not yet validated)."""
        if self._frozen is not None:
            return replace(self._frozen, op=self.op_cb.currentData())
        name = self.field_cb.currentData()
        whole = name in ss.INTEGER_FIELDS

        def num(spin):
            return int(round(spin.value())) if whole else float(spin.value())

        values = ((self.gate_cb.currentData(),) if self.gate_cb.currentData() else ()) if ss.is_gate(name) \
            else self._chosen_tiles()
        return ss.Condition(name, self.op_cb.currentData(), num(self.a_spin), num(self.b_spin),
                            self.at_cb.currentData() if ss.per_timepoint(name) else "", values)


class SubsetEditor(QFrame):
    """The conditions, *all* or *any*, an **Add condition** button and a line saying what the subset is."""

    def __init__(self, ctx, tp_text):
        super().__init__()
        self._ctx, self._tp_text = ctx, tp_text
        self.setProperty("panel", True)
        self.setObjectName("NanowellSubsetEditor")
        #: The subset the rows describe: rebuilt from the page's state only when that is another subset.
        self.shown: ss.Subset | None = None
        self._channels: tuple = ()
        self.rows: list[_ConditionRow] = []
        #: The conditions as the rows wrote them when last built from the page's subset or applied. An edit is
        #: pending when the rows now write something else — not when they differ from the page's subset, which a
        #: row cannot always show exactly (a bound past the spin box's decimals or range), and which would then
        #: read as an edit nobody made, one Reset could never clear.
        self._built: ss.Subset | None = None
        # The whole editor scrolls, inside its share of the window: a phone held sideways leaves the page a few
        # hundred pixels, less than the header and footer alone, and a fixed part there was drawn over the rest.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = _EditorScroll(self)
        body = QWidget(self.scroll)
        lay = QVBoxLayout(body)
        # Each of the editor's lines is a flow, so at phone width it wraps rather than cutting its words and controls.
        head_host = QWidget(body)
        head = _flow(head_host)
        head.addWidget(QLabel("Use the nanowells that meet"))
        self.match_cb = QComboBox()
        self.match_cb.addItem("all", "all")
        self.match_cb.addItem("any", "any")
        self.match_cb.setToolTip(_MATCH_TIP)
        self.match_cb.currentIndexChanged.connect(lambda _i: self.changed())
        head.addWidget(self.match_cb)
        head.addWidget(QLabel("of these conditions:"))
        self.close_btn = QPushButton("Close")
        self.close_btn.setProperty("variant", "secondary")
        top = QHBoxLayout()
        top.addWidget(head_host, 1)
        top.addWidget(self.close_btn, 0, Qt.AlignTop)   # Close keeps the top-right corner however the words wrap
        lay.addLayout(top)
        names_host = QWidget(body)
        names = _flow(names_host)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Name this subset to save it")
        self.name_edit.setMaxLength(ss.MAX_NAME)
        self.name_edit.setToolTip(_NAME_TIP)
        self.name_edit.textChanged.connect(lambda _t: self._enable_names())
        self.name_edit.returnPressed.connect(self.save_named)
        self.save_btn = self._name_button("Save", _SAVE_TIP, self.save_named)
        self.rename_btn = self._name_button("Rename", _RENAME_TIP, self.rename_named)
        self.delete_btn = self._name_button("Delete", _DELETE_TIP, self.delete_named)
        names.addWidget(flow_group(names_host, "Name", self.name_edit, spacing=8))
        for button in (self.save_btn, self.rename_btn, self.delete_btn):
            names.addWidget(button)
        lay.addWidget(names_host)
        #: The saved name the subset in use had when the name box was last filled: the box is refilled only when that
        #: changes, so a name being typed is never pulled from under the cursor.
        self._named: str | None = None
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(theme.Spacing.md)         # a condition wrapped onto two lines stays apart from the next
        lay.addLayout(self.rows_box)
        foot_host = QWidget(body)
        foot = _flow(foot_host)
        self.add_btn = QPushButton("+ Add condition")
        self.add_btn.setProperty("variant", "secondary")
        self.add_btn.clicked.connect(self.add)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setProperty("variant", "secondary")
        self.clear_btn.setToolTip("Remove every condition: every nanowell is used.")
        self.clear_btn.clicked.connect(self.clear)
        foot.addWidget(self.add_btn)
        foot.addWidget(self.clear_btn)
        # Edits change the conditions written here only; Apply draws them, Reset puts back the subset in use. A plate
        # redrawn on every keystroke recomputed every view for each half-typed bound.
        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setProperty("variant", "secondary")
        self.reset_btn.setToolTip(_RESET_TIP)
        self.reset_btn.clicked.connect(self.reset)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setToolTip(_APPLY_TIP)
        self.apply_btn.clicked.connect(self.apply)
        foot.addWidget(self.reset_btn)
        foot.addWidget(self.apply_btn)          # beside Add condition and Clear: the row's four edits read together
        lay.addWidget(foot_host)
        self.status = QLabel("")
        self.status.setObjectName("InfoHint")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        lay.addStretch(1)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll)

    def _name_button(self, text: str, tip: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("variant", "secondary")
        button.setToolTip(tip)
        button.clicked.connect(slot)
        return button

    # ── names ───────────────────────────────────────────────────────────
    def current_name(self) -> str:
        """The name the subset in use is saved under, or ``""``."""
        st = self._state()
        return ss.saved_name_of(st.saved_subsets, st.subset) if st is not None else ""

    def save_named(self) -> None:
        said = nwc.save_subset(self._ctx, self.name_edit.text())
        if said:
            self._say(said, warn=True)
            return
        self._named = None                               # refill from the name now in use
        self.refresh(self._state())
        self._say(f"Saved as \u201c{self.current_name()}\u201d in this dataset.")

    def rename_named(self) -> None:
        said = nwc.rename_saved_subset(self._ctx, self.current_name(), self.name_edit.text())
        if said:
            self._say(said, warn=True)
            return
        self._named = None
        self.refresh(self._state())
        self._say(f"Renamed to \u201c{self.current_name()}\u201d.")

    def delete_named(self) -> None:
        name = self.current_name()
        if not name:
            return
        nwc.delete_saved_subset(self._ctx, name)
        self._named = None
        self.refresh(self._state())
        self._say(f"\u201c{name}\u201d is no longer saved; the figure still uses its conditions.")

    def _enable_names(self) -> None:
        """Save needs conditions and a name; Rename and Delete a saved subset in use, Rename a new name for it."""
        st = self._state()
        active = st is not None and st.subset.is_active()
        typed, name = " ".join(self.name_edit.text().split()), self.current_name()
        waiting = self.pending()
        self.save_btn.setEnabled(active and bool(typed) and not waiting)
        self.save_btn.setToolTip(_SAVE_TIP + ("\n\nApply the changes first: Save keeps the subset in use."
                                              if waiting else ""))
        self.rename_btn.setEnabled(bool(name) and bool(typed) and typed != name)
        self.delete_btn.setEnabled(bool(name))

    def _fill_name(self) -> None:
        """The name box shows the saved name in use when that changes — unless the user has typed another name
        there, which is kept (Apply then Save is how a draft is saved under a new name). ``_named`` ``None`` (after
        Save, Rename or Delete) refills it regardless."""
        name = self.current_name()
        typed = " ".join(self.name_edit.text().split())
        if name != self._named:
            keep = self._named is not None and typed and typed != self._named
            self._named = name
            if keep:
                self._enable_names()
                return
            blocked = self.name_edit.blockSignals(True)
            self.name_edit.setText(name)
            self.name_edit.blockSignals(blocked)
        self._enable_names()

    # ── what the rows may offer ─────────────────────────────────────────
    def _state(self):
        return self._ctx.state.feature_slice("nanowell")

    def fields(self, current: str) -> list:
        """The fields for the loaded dataset's channels, with *current* kept even when it names a channel this
        dataset lacks (a restored subset), so the row still shows what the subset tests."""
        choices = ss.field_choices(self._channels, gates=bool(self.gates()), sizes=nwc.size_rule(self._ctx))
        if current not in {name for name, _l, _g in choices}:
            choices.append((current, ss.field_label(current), "Other"))
        return choices

    def timepoints(self, current: str) -> list:
        st = self._state()
        data = getattr(st, "data", None)
        tps = list(data.timepoints()) if data is not None else []
        named = current[len(ss.TP_PREFIX):] if current.startswith(ss.TP_PREFIX) else None
        if named and named not in tps:
            tps.append(named)
        times = data.times_h if data is not None else {}
        return [(tp, self._tp_text(tp, times)) for tp in tps]

    def tiles(self) -> list:
        return nwc.subset_tiles(self._ctx)

    def gates(self) -> list:
        return nwc.subset_gates(self._ctx)

    # ── edits ───────────────────────────────────────────────────────────
    def written(self) -> ss.Subset:
        return ss.Subset(tuple(r.condition() for r in self.rows), self.match_cb.currentData() or "all")

    def _draft(self) -> tuple[ss.Subset | None, str]:
        """``(the subset as written, "")``, or ``(None, why it is not one)``."""
        try:
            return self.written().validated(), ""
        except ValueError as exc:
            return None, str(exc)

    def pending(self) -> bool:
        """Whether the rows have been edited since they were built from the subset in use or applied (and so wait
        for Apply or Reset)."""
        if self._built is None:
            return False
        return bool(self._draft()[1]) or self.written() != self._built

    def changed(self) -> None:
        """An edit: say what the conditions as written are and whether they wait for Apply — or why they are not a
        subset yet. Nothing is drawn until Apply."""
        draft, why = self._draft()
        if why:
            self._say(f"Not a subset yet: {why}. Apply waits until it is fixed; Reset puts back the subset in use.",
                      warn=True)
        elif self.pending():
            said = "No conditions: every nanowell." if not draft.is_active() else draft.label()
            self._say(f"Not applied yet — {said}. Apply to draw it, or Reset.", warn=True)
        else:
            st = self._state()
            self._say_subset(st.subset if st is not None else draft)
        self._enable_edits()

    def apply(self) -> None:
        """Make the conditions as written the page's subset; every view redraws once."""
        draft, why = self._draft()
        if why:
            self._say(f"Not applied: {why}.", warn=True)
            return
        self.shown = draft
        self._built = self.written()
        nwc.set_subset(self._ctx, draft)
        self._say_subset(draft)
        self._fill_name()
        self._enable_edits()

    def reset(self) -> None:
        """Rebuild the rows from the subset in use, discarding the edits not applied."""
        self.shown = None
        self.refresh(self._state())

    def _enable_edits(self) -> None:
        draft, why = self._draft()
        pending = self.pending()
        self.apply_btn.setEnabled(pending and not why)
        self.reset_btn.setEnabled(pending)
        self._enable_names()

    def add(self) -> None:
        if len(self.rows) >= ss.MAX_CONDITIONS:
            self._say(f"A subset holds at most {ss.MAX_CONDITIONS} conditions.", warn=True)
            return
        self._add_row(ss.Condition(ss.START, "ge", 1))
        self.scroll.ensureWidgetVisible(self.rows[-1])  # the new condition, where the rows scroll past their share
        self.changed()

    def remove(self, row) -> None:
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self.changed()

    def clear(self) -> None:
        for row in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows = []
        self.changed()

    def _add_row(self, condition: ss.Condition) -> None:
        row = _ConditionRow(self, condition)
        self.rows.append(row)
        self.rows_box.addWidget(row)
        row.show()                                       # now, not on the layout's queued show: it is edited at once

    # ── kept in step with the page ──────────────────────────────────────
    def refresh(self, st) -> None:
        """Show the page's subset: the rows are rebuilt only when it is not the one they already describe, or the
        dataset's channels changed, so a value being typed is never pulled from under the cursor."""
        data = getattr(st, "data", None)
        channels = tuple(data.channels) if data is not None else ()
        if st.subset == self.shown and channels == self._channels:
            # The subset in use is the one the rows started from: keep the rows, and any edits not yet applied.
            for row in self.rows:
                row.fill_gates()                         # a gate drawn, renamed or deleted on the scatter since
            self._fill_name()
            self.changed()
            return
        self._channels = channels
        for row in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows = []
        blocked = self.match_cb.blockSignals(True)
        _select(self.match_cb, st.subset.match)
        self.match_cb.blockSignals(blocked)
        for condition in st.subset.conditions:
            self._add_row(condition)
        self._built = self.written()
        self.shown = st.subset
        self._say_subset(st.subset)
        self._fill_name()
        self._enable_edits()

    def _say_subset(self, subset: ss.Subset) -> None:
        # Bound, so a condition naming a gate that is no longer drawn says so here too.
        subset = ss.bind_gates(subset, self._ctx.state.cell_gating.scatter_gates or [])
        name = self.current_name()
        said = "No conditions: every nanowell is used." if not subset.is_active() else subset.label()
        self._say(f"\u201c{name}\u201d \u2014 {said}" if name else said)

    def _say(self, text: str, *, warn: bool = False) -> None:
        self.status.setText(text)
        name = "Warn" if warn else "InfoHint"
        if self.status.objectName() != name:
            self.status.setObjectName(name)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)


def _fill_combo(combo, names: list, listed: list) -> None:
    """The Subset combo's entries: the Starting cells presets, the subsets saved in this dataset by name, then
    *Custom subset…*. *listed* is the names the combo lists now, kept by the control: the entries are rebuilt only
    when the saved names change, so a refresh never empties the open list."""
    if listed == names and combo.count():
        return
    listed[:] = names
    combo.clear()
    for key, _label, _lo, _hi in sc.PRESETS:
        combo.addItem(_PRESET_LABELS[key], key)
    if names:
        combo.insertSeparator(combo.count())
        for name in names:
            combo.addItem(name, SAVED_PREFIX + name)
        combo.insertSeparator(combo.count())
    combo.addItem(CUSTOM_LABEL, sc.CUSTOM)


def share_page(editor: SubsetEditor, figure: QWidget, redraw) -> None:
    """While *editor* is shown on a page too short for it and *figure* both — a phone held sideways, where the figure's
    own minimum and the footer under it already fill the page — the figure steps aside and the editor takes the page;
    Close brings the figure back, drawn again (*redraw*: a hidden figure is not drawn). Decided by the page's height,
    not by phone layout, so a short window at a desk is treated the same; on a page with room for both, nothing moves.
    """
    page = editor.parentWidget()
    lay = page.layout() if page is not None else None
    if lay is None:
        return
    stepped_aside = [False]

    def too_short() -> bool:
        # Computed, not read from the editor's minimum size: its layout caches that, and it lags the share just set.
        need = editor.scroll.height_at(_ROWS_SHARE) + 2 * editor.frameWidth() + figure.minimumSizeHint().height()
        shown = [lay.itemAt(i).widget() for i in range(lay.count())]
        others = [w for w in shown if w is not None and w not in (editor, figure) and not w.isHidden()]
        need += sum(w.minimumSizeHint().height() for w in others)
        need += lay.spacing() * (len(others) + 1)
        return need > page.height()

    def place() -> None:
        aside = not editor.isHidden() and too_short()
        editor.scroll.share = 1.0 if aside else _ROWS_SHARE
        editor.scroll.updateGeometry()
        if aside != stepped_aside[0]:
            stepped_aside[0] = aside
            figure.setVisible(not aside)
            if not aside:
                QTimer.singleShot(0, figure, redraw)     # with the figure as context: dropped if the page is deleted

    class _Watch(QObject):
        def eventFilter(self, obj, event) -> bool:  # Qt naming
            if event.type() in (QEvent.Show, QEvent.Hide, QEvent.Resize):
                with swallow("Nanowells: share the page between the Subset editor and the figure"):
                    place()
            return False

    watch = _Watch(editor)
    editor.installEventFilter(watch)
    page.installEventFilter(watch)


def build_subset_control(ctx, band, tp_text) -> SimpleNamespace:
    """*Subset*: the preset combo and **Edit…** in the band, and the editor (a panel the page places under the
    band). The combo is outlined in the accent while a subset is set, so one restored with the page's settings is
    never forgotten. *tp_text* names a timepoint as the page's Timepoint control does."""
    reg = ctx.widgets
    combo = QComboBox()
    combo.setToolTip(_SUBSET_TIP)
    #: The saved names the combo lists now (``_fill_combo``).
    listed: list = []
    _fill_combo(combo, [], listed)
    edit_btn = QToolButton()
    edit_btn.setText("Edit…")
    edit_btn.setCheckable(True)
    edit_btn.setToolTip(_EDIT_TIP)
    edit_btn.setProperty("variant", "secondary")
    editor = SubsetEditor(ctx, tp_text)
    editor.hide()

    def show_editor(on: bool) -> None:
        editor.setVisible(on)
        blocked = edit_btn.blockSignals(True)
        edit_btn.setChecked(on)
        edit_btn.blockSignals(blocked)

    def chosen(_index=None) -> None:
        key = combo.currentData()
        if isinstance(key, str) and key.startswith(SAVED_PREFIX):
            nwc.apply_saved_subset(ctx, key[len(SAVED_PREFIX):])
            return
        if key == sc.CUSTOM:
            show_editor(True)                            # shows the subset that is set, changing nothing
            return
        _key, _label, lo, hi = next(p for p in sc.PRESETS if p[0] == key)
        nwc.set_starting_cells(ctx, lo, hi)

    def refresh(st) -> None:
        blocked = combo.blockSignals(True)
        try:
            _fill_combo(combo, list(st.saved_subsets), listed)
            name = ss.saved_name_of(st.saved_subsets, st.subset)
            preset = SAVED_PREFIX + name if name else preset_of(st.subset)
            if combo.currentData() != sc.CUSTOM or preset != "any" or st.subset.is_active():
                _select(combo, preset)
            combo.setToolTip(_SUBSET_TIP + ("" if not st.subset.is_active() else f"\n\nNow: {st.subset.label()}"))
            active = st.subset.is_active()
            if combo.property("filterActive") != active:
                combo.setProperty("filterActive", active)
                combo.style().unpolish(combo)
                combo.style().polish(combo)
        finally:
            combo.blockSignals(blocked)
        editor.refresh(st)

    combo.currentIndexChanged.connect(chosen)
    edit_btn.toggled.connect(show_editor)
    editor.close_btn.clicked.connect(lambda: show_editor(False))
    reg.register("_nw_subset_cb", combo)
    reg.register("_nw_subset_edit_btn", edit_btn)
    reg.register("_nw_subset_editor", editor)
    group = band.add_scope("Subset", combo, edit_btn)
    return SimpleNamespace(group=group, refresh=refresh, editor=editor, signalling=())

