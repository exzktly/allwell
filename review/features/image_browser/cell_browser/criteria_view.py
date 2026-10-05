"""Cell Browser left-sidebar: the filter builder (criteria + timepoint/FOV).

Criteria rows are ``(property, min, max)`` over any measured property; the
timepoint/FOV pickers are multi-select menus (mirroring the scatter tab). The
**Apply** button runs ``controller.apply_filters``.
"""

from __future__ import annotations

from util.fov import sort_key as fov_sort_key
from review.data.data_loading import get_rows

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QScrollArea, QVBoxLayout, QWidget, QWidgetAction, QCheckBox,
)

from core.safe import swallowed
from widgets import menu_exec
from review.features.image_browser.cell_browser import controller as ctl
from review.features.image_browser.cell_browser.collect import (
    cell_browser_source_wells,
    enumerate_properties,
)
from review.features.image_browser.cell_browser.sort_view import (
    build_cell_browser_sort_section,
    cell_browser_sort_refresh,
)
from ui import theme

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_HI_W = 56
_LO_W = 56
_RM_W = 24


def build_cell_browser_sidebar(ctx, parent: QWidget) -> None:
    lay = parent.layout()
    if lay is None:
        lay = QVBoxLayout(parent)
        parent.setLayout(lay)
    lay.setContentsMargins(8, 8, 8, 8)
    lay.setSpacing(6)

    from review.core.ui_helpers import build_section_header
    lay.addWidget(build_section_header(parent, "Cell filters"))

    # ── source: scatter gate vs criteria (N1) ────────────────────────────────
    gate_cb = QCheckBox("From scatter gate", parent)
    gate_cb.setToolTip(
        "Show exactly the cells inside the gate(s) drawn on the Scatter Plot.\n"
        "Best with Scatter + Cell Browser in Split View — drawing or resizing a "
        "gate updates this gallery live. Disables the criteria filter while on "
        "(the gate is the numeric filter).")
    gate_cb.toggled.connect(lambda on: _on_gate_mode_toggled(ctx, bool(on)))
    lay.addWidget(gate_cb)
    ctx.runtime.cell_browser.gate_mode_cb = gate_cb

    # Which gate(s) to source from — several may be drawn. ``None`` ⇒ all.
    ctx.runtime.cell_browser.gate_choices = None
    gate_pick_row = QWidget(parent)
    gpl = QHBoxLayout(gate_pick_row)
    gpl.setContentsMargins(16, 0, 0, 0)
    gate_pick_btn = QPushButton("Gate", gate_pick_row)
    gate_pick_btn.setProperty("variant", "secondary")
    gate_pick_btn.setToolTip("Choose which scatter gate(s) define the cells.")
    gate_pick_btn.clicked.connect(lambda _=False: _open_gate_select(ctx))
    gpl.addWidget(gate_pick_btn)
    gate_pick_lbl = QLabel("(all)", gate_pick_row)
    gate_pick_lbl.setObjectName("Muted")
    gpl.addWidget(gate_pick_lbl)
    gpl.addStretch(1)
    lay.addWidget(gate_pick_row)
    ctx.runtime.cell_browser.gate_pick_row = gate_pick_row
    ctx.runtime.cell_browser.gate_pick_btn = gate_pick_btn
    ctx.runtime.cell_browser.gate_pick_label = gate_pick_lbl

    # Clarify how the gate composes with the other filters while it's active.
    gate_hint = QLabel(
        "Cells inside the selected gate, from the wells the gate was drawn on. "
        "Timepoints / FOVs below narrow further (optional).", parent)
    gate_hint.setObjectName("Muted")
    gate_hint.setWordWrap(True)
    lay.addWidget(gate_hint)
    ctx.runtime.cell_browser.gate_hint = gate_hint

    gate_pick_row.setVisible(False)
    gate_hint.setVisible(False)

    # ── criteria rows (scrollable, capped so it never balloons) ──────────────
    rows_scroll = QScrollArea(parent)
    rows_scroll.setWidgetResizable(True)
    rows_scroll.setFrameShape(QScrollArea.NoFrame)
    rows_scroll.setMaximumHeight(theme.Metrics.scroll_max_h)
    rows_host = QWidget()
    rows_lay = QVBoxLayout(rows_host)
    rows_lay.setContentsMargins(0, 0, 0, 0)
    rows_lay.setSpacing(4)
    rows_lay.addStretch(1)
    rows_scroll.setWidget(rows_host)
    lay.addWidget(rows_scroll)
    ctx.runtime.cell_browser.criteria_host = rows_lay
    ctx.runtime.cell_browser.criteria_rows_host = rows_host
    ctx.runtime.cell_browser.criteria_rows = []

    add_btn = QPushButton("+ Add criterion", parent)
    add_btn.setProperty("variant", "secondary")
    add_btn.clicked.connect(lambda _=False: _add_row(ctx))
    lay.addWidget(add_btn)
    ctx.runtime.cell_browser.add_criterion_btn = add_btn

    # Why the criteria editor above is greyed out. "From scatter gate" is a
    # sticky choice — it survives tab switches and dataset loads — so without
    # this line a user who ticked it earlier meets a permanently dead
    # "+ Add criterion" button with nothing on screen explaining it.
    criteria_off_hint = QLabel(
        "Criteria are ignored while “From scatter gate” is on.", parent)
    criteria_off_hint.setObjectName("Muted")
    criteria_off_hint.setWordWrap(True)
    criteria_off_hint.setVisible(False)
    lay.addWidget(criteria_off_hint)
    ctx.runtime.cell_browser.criteria_off_hint = criteria_off_hint

    # ── timepoint / FOV multi-select ─────────────────────────────────────────
    ctx.runtime.cell_browser.tp_selections = {}
    ctx.runtime.cell_browser.fov_selections = {}

    tp_row = QWidget(parent)
    trl = QHBoxLayout(tp_row)
    trl.setContentsMargins(0, 0, 0, 0)
    tp_btn = QPushButton("Timepoints", tp_row)
    tp_btn.setProperty("variant", "secondary")
    tp_btn.clicked.connect(lambda _=False: _open_multiselect(
        ctx, tp_btn, ctx.runtime.cell_browser.tp_selections, ctx.runtime.cell_browser.tp_label))
    trl.addWidget(tp_btn)
    tp_lbl = QLabel("(all)", tp_row)
    tp_lbl.setObjectName("Muted")
    trl.addWidget(tp_lbl)
    trl.addStretch(1)
    lay.addWidget(tp_row)
    ctx.runtime.cell_browser.tp_button = tp_btn
    ctx.runtime.cell_browser.tp_label = tp_lbl

    fov_row = QWidget(parent)
    frl = QHBoxLayout(fov_row)
    frl.setContentsMargins(0, 0, 0, 0)
    fov_btn = QPushButton("FOVs", fov_row)
    fov_btn.setProperty("variant", "secondary")
    fov_btn.clicked.connect(lambda _=False: _open_multiselect(
        ctx, fov_btn, ctx.runtime.cell_browser.fov_selections, ctx.runtime.cell_browser.fov_label))
    frl.addWidget(fov_btn)
    fov_lbl = QLabel("(all)", fov_row)
    fov_lbl.setObjectName("Muted")
    frl.addWidget(fov_lbl)
    frl.addStretch(1)
    lay.addWidget(fov_row)
    ctx.runtime.cell_browser.fov_button = fov_btn
    ctx.runtime.cell_browser.fov_label = fov_lbl

    # ── sort builder (well / timepoint / metric, asc/desc) ───────────────────
    build_cell_browser_sort_section(ctx, lay)

    # No "Apply filters" button here: every Cell Browser control (filters +
    # display) now defers to the single top-bar **Generate** button
    # (tab_view.build_cell_browser_tab), so a second trigger is redundant.

    # N1: let the scatter gating module drive a live re-collect when a gate is
    # drawn / edited / cleared (only acts in gate mode + when this tab is
    # visible — see controller.cell_browser_sync_scatter_gates).
    ctx.runtime.cell_browser.gates_changed_cb = (
        lambda _app=ctx: (_refresh_gate_choices(_app),
                          ctl.cell_browser_sync_scatter_gates(_app)))

    # Pin all content to the top; the capped criteria/sort scrolls absorb their
    # own overflow, and this trailing stretch takes any remaining slack so nothing
    # balloons (the frame itself is stretch 0 — the main plate owns the rail slack).
    lay.addStretch(1)

    cell_browser_sidebar_refresh(ctx)


def _on_gate_mode_toggled(ctx, on: bool) -> None:
    """Switch the gallery source between the scatter gate and the criteria (N1).
    Disables the criteria editor in gate mode, reveals the gate picker + hint,
    and re-collects immediately."""
    _set_criteria_enabled(ctx, not on)
    cb = ctx.runtime.cell_browser
    for w in (cb.gate_pick_row, cb.gate_hint):
        if w is not None:
            w.setVisible(on)
    if on:
        _refresh_gate_choices(ctx)
    ctl.apply_filters(ctx)


def _set_criteria_enabled(ctx, on: bool) -> None:
    """Enable/disable the criteria editor (rows + add button), and show the
    reason while it is off."""
    host = getattr(ctx.runtime.cell_browser, "criteria_rows_host", None)
    if host is not None:
        host.setEnabled(on)
    btn = getattr(ctx.runtime.cell_browser, "add_criterion_btn", None)
    if btn is not None:
        btn.setEnabled(on)
    hint = getattr(ctx.runtime.cell_browser, "criteria_off_hint", None)
    if hint is not None:
        hint.setVisible(not on)


def _gate_names(ctx) -> list:
    """Names of the gates currently drawn on the scatter (in draw order)."""
    out = []
    for g in ctx.state.cell_gating.scatter_gates or []:
        nm = str(g.get("name") or "").strip()
        if nm:
            out.append(nm)
    return out


def _refresh_gate_choices(ctx) -> None:
    """Drop any chosen gate names that no longer exist; update the picker label."""
    names = set(_gate_names(ctx))
    chosen = getattr(ctx.runtime.cell_browser, "gate_choices", None)
    if isinstance(chosen, set):
        pruned = chosen & names
        # Collapse "everything selected" back to None (= all, auto-includes new).
        ctx.runtime.cell_browser.gate_choices = None if pruned == names else pruned
    _refresh_gate_pick_label(ctx)


def _refresh_gate_pick_label(ctx) -> None:
    lbl = getattr(ctx.runtime.cell_browser, "gate_pick_label", None)
    if lbl is None:
        return
    chosen = getattr(ctx.runtime.cell_browser, "gate_choices", None)
    total = len(_gate_names(ctx))
    if chosen is None:
        lbl.setText(f"(all {total})" if total else "(none drawn)")
    else:
        lbl.setText(f"({len(chosen)}/{total})")


def _open_gate_select(ctx) -> None:
    """Checkbox menu over the drawn gate names; ``None`` selection ⇒ all."""
    btn = getattr(ctx.runtime.cell_browser, "gate_pick_btn", None)
    if btn is None:
        return
    names = _gate_names(ctx)
    if not names:
        return
    chosen = getattr(ctx.runtime.cell_browser, "gate_choices", None)
    selected = set(names) if chosen is None else set(chosen)
    menu = QMenu(btn)
    checks = {}
    for nm in names:
        cb = QCheckBox(nm, menu)
        cb.setChecked(nm in selected)
        checks[nm] = cb
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(cb)
        menu.addAction(wa)

    def _commit() -> None:
        picked = {nm for nm, c in checks.items() if c.isChecked()}
        # All picked (or none) ⇒ None (= all gates, auto-includes new ones).
        ctx.runtime.cell_browser.gate_choices = None if picked == set(names) or not picked else picked
        _refresh_gate_pick_label(ctx)
        ctl.apply_filters(ctx)

    menu.aboutToHide.connect(_commit)
    menu_exec.exec_transient_menu(menu, btn.mapToGlobal(btn.rect().bottomLeft()))


def _add_row(ctx, column: str = "") -> None:
    rows_lay = getattr(ctx.runtime.cell_browser, "criteria_host", None)
    if rows_lay is None:
        return
    row = QWidget()
    rl = QHBoxLayout(row)
    rl.setContentsMargins(0, 0, 0, 0)
    rl.setSpacing(3)
    combo = QComboBox(row)
    for col, label in enumerate_properties(ctx):
        combo.addItem(label, userData=col)
    if column:
        idx = combo.findData(column)
        if idx >= 0:
            combo.setCurrentIndex(idx)
    combo.setMinimumWidth(theme.Metrics.combo_min_w_sm)
    lo = QLineEdit(row); lo.setPlaceholderText("min"); lo.setFixedWidth(_LO_W)
    hi = QLineEdit(row); hi.setPlaceholderText("max"); hi.setFixedWidth(_HI_W)
    rm = QPushButton("✕", row)
    rm.setFixedWidth(_RM_W)
    rm.setObjectName("Ghost")
    rl.addWidget(combo, 1)
    rl.addWidget(lo)
    rl.addWidget(hi)
    rl.addWidget(rm)
    # insert before the trailing stretch
    rows_lay.insertWidget(rows_lay.count() - 1, row)
    entry = (combo, lo, hi)
    ctx.runtime.cell_browser.criteria_rows.append(entry)

    def _remove(_=False) -> None:
        try:
            ctx.runtime.cell_browser.criteria_rows.remove(entry)
        except ValueError:
            swallowed()
        row.deleteLater()
    rm.clicked.connect(_remove)


def _open_multiselect(ctx, btn, selections: dict, label) -> None:
    """Popup a checkbox menu over *selections* (str → bool); update *label*."""
    menu = QMenu(btn)
    checks = {}
    for key in sorted(selections.keys(), key=_sort_key):
        cb = QCheckBox(key, menu)
        cb.setChecked(bool(selections[key]))
        checks[key] = cb

        def _make(k=key, c=cb):
            def _toggled(on: bool) -> None:
                selections[k] = bool(on)
                _refresh_multiselect_label(selections, label)
            return _toggled
        cb.toggled.connect(_make())
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(cb)
        menu.addAction(wa)
    menu.addSeparator()

    def _set_all(val: bool) -> None:
        for k, c in checks.items():
            c.blockSignals(True)
            c.setChecked(val)
            c.blockSignals(False)
            selections[k] = val
        _refresh_multiselect_label(selections, label)

    a_all = QAction("All", menu); a_all.triggered.connect(lambda _=False: _set_all(True))
    a_none = QAction("None", menu); a_none.triggered.connect(lambda _=False: _set_all(False))
    menu.addAction(a_all); menu.addAction(a_none)
    menu_exec.exec_transient_menu(menu, btn.mapToGlobal(btn.rect().bottomLeft()))


def _refresh_multiselect_label(selections: dict, label) -> None:
    n = sum(1 for v in selections.values() if v)
    total = len(selections)
    label.setText("(all)" if n == total else f"({n}/{total})")


def _sort_key(s: str):
    # Both call sites feed numeric-ish tokens (timepoint keys "24"/"1.5", FOV
    # tokens); the shared FOV key orders bare numbers identically and also reads
    # a prefixed FOV ("f10") numerically instead of lexicographically.
    return fov_sort_key(s)


def cell_browser_sidebar_refresh(ctx) -> None:
    """Repopulate criteria property combos + timepoint/FOV options from the
    current dataset + selection (called on tab show / dataset load)."""
    # property combos
    props = enumerate_properties(ctx)
    for combo, _lo, _hi in (ctx.runtime.cell_browser.criteria_rows or []):
        cur = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for col, label in props:
            combo.addItem(label, userData=col)
        if cur:
            idx = combo.findData(cur)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    # sort-key field combos (well / timepoint / fov / metrics)
    cell_browser_sort_refresh(ctx)

    # gate picker (prune removed gates, refresh the count label)
    _refresh_gate_choices(ctx)

    # timepoints (union across the dataset)
    tp_sel = ctx.runtime.cell_browser.tp_selections
    tps = list(ctx.state.dataset.all_timepoints_cache or [])
    tp_keys = [f"{t:g}" for t in tps]
    _sync_selection_dict(tp_sel, tp_keys)
    if getattr(ctx.runtime.cell_browser, "tp_label", None) is not None:
        _refresh_multiselect_label(tp_sel, ctx.runtime.cell_browser.tp_label)

    # FOVs (union across the source wells — the selection, else all loaded wells,
    # matching collect_cells so the picker isn't empty before a well is chosen).
    fov_sel = ctx.runtime.cell_browser.fov_selections
    fovs: set = set()
    for well in cell_browser_source_wells(ctx):
        if well not in ctx.state.dataset.well_paths:
            continue
        df = get_rows(ctx, well)
        if df is not None and not df.empty and "fov" in df.columns:
            fovs.update(df["fov"].astype(str).unique().tolist())
    _sync_selection_dict(fov_sel, sorted(fovs, key=_sort_key))
    if getattr(ctx.runtime.cell_browser, "fov_label", None) is not None:
        _refresh_multiselect_label(fov_sel, ctx.runtime.cell_browser.fov_label)


def _sync_selection_dict(sel: dict, keys) -> None:
    """Keep *sel* (key→bool) in step with the available *keys*; new keys default
    to **deselected**, vanished keys are dropped, existing choices preserved.

    The timepoint/FOV pickers are a strict include-list (collect_cells shows no
    cells when options exist but none are checked), and start fully deselected —
    so the gallery is empty until the user picks the timepoints / FOVs to show."""
    keyset = set(keys)
    for k in list(sel.keys()):
        if k not in keyset:
            del sel[k]
    for k in keys:
        sel.setdefault(k, False)
