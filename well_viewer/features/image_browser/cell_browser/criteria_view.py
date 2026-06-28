"""Cell Browser left-sidebar: the filter builder (criteria + timepoint/FOV).

Criteria rows are ``(property, min, max)`` over any measured property; the
timepoint/FOV pickers are multi-select menus (mirroring the scatter tab). The
**Apply** button runs ``controller.apply_filters``.
"""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QScrollArea, QVBoxLayout, QWidget, QWidgetAction, QCheckBox,
)

from well_viewer.core.safe import swallowed
from well_viewer.features.image_browser.cell_browser import controller as ctl
from well_viewer.features.image_browser.cell_browser.collect import (
    cell_browser_source_wells,
    enumerate_properties,
)
from well_viewer.features.image_browser.cell_browser.sort_view import (
    build_cell_browser_sort_section,
    cell_browser_sort_refresh,
)


def build_cell_browser_sidebar(app, parent: QWidget) -> None:
    lay = parent.layout()
    if lay is None:
        lay = QVBoxLayout(parent)
        parent.setLayout(lay)
    lay.setContentsMargins(8, 8, 8, 8)
    lay.setSpacing(6)

    title = QLabel("Cell filters", parent)
    title.setObjectName("Heading")
    lay.addWidget(title)

    # ── criteria rows (scrollable) ───────────────────────────────────────────
    rows_scroll = QScrollArea(parent)
    rows_scroll.setWidgetResizable(True)
    rows_host = QWidget()
    rows_lay = QVBoxLayout(rows_host)
    rows_lay.setContentsMargins(0, 0, 0, 0)
    rows_lay.setSpacing(4)
    rows_lay.addStretch(1)
    rows_scroll.setWidget(rows_host)
    lay.addWidget(rows_scroll, 1)
    app._cell_browser_criteria_host = rows_lay
    app._cell_browser_criteria_rows = []

    add_btn = QPushButton("+ Add criterion", parent)
    add_btn.setProperty("variant", "secondary")
    add_btn.clicked.connect(lambda _=False: _add_row(app))
    lay.addWidget(add_btn)

    # ── timepoint / FOV multi-select ─────────────────────────────────────────
    app._cell_browser_tp_selections = {}
    app._cell_browser_fov_selections = {}

    tp_row = QWidget(parent)
    trl = QHBoxLayout(tp_row)
    trl.setContentsMargins(0, 0, 0, 0)
    tp_btn = QPushButton("Timepoints", tp_row)
    tp_btn.setProperty("variant", "secondary")
    tp_btn.clicked.connect(lambda _=False: _open_multiselect(
        app, tp_btn, app._cell_browser_tp_selections, app._cell_browser_tp_label))
    trl.addWidget(tp_btn)
    tp_lbl = QLabel("(all)", tp_row)
    tp_lbl.setObjectName("Muted")
    trl.addWidget(tp_lbl)
    trl.addStretch(1)
    lay.addWidget(tp_row)
    app._cell_browser_tp_button = tp_btn
    app._cell_browser_tp_label = tp_lbl

    fov_row = QWidget(parent)
    frl = QHBoxLayout(fov_row)
    frl.setContentsMargins(0, 0, 0, 0)
    fov_btn = QPushButton("FOVs", fov_row)
    fov_btn.setProperty("variant", "secondary")
    fov_btn.clicked.connect(lambda _=False: _open_multiselect(
        app, fov_btn, app._cell_browser_fov_selections, app._cell_browser_fov_label))
    frl.addWidget(fov_btn)
    fov_lbl = QLabel("(all)", fov_row)
    fov_lbl.setObjectName("Muted")
    frl.addWidget(fov_lbl)
    frl.addStretch(1)
    lay.addWidget(fov_row)
    app._cell_browser_fov_button = fov_btn
    app._cell_browser_fov_label = fov_lbl

    # ── sort builder (well / timepoint / metric, asc/desc) ───────────────────
    build_cell_browser_sort_section(app, lay)

    # No "Apply filters" button here: every Cell Browser control (filters +
    # display) now defers to the single top-bar **Generate** button
    # (tab_view.build_cell_browser_tab), so a second trigger is redundant.

    cell_browser_sidebar_refresh(app)


def _add_row(app, column: str = "") -> None:
    rows_lay = getattr(app, "_cell_browser_criteria_host", None)
    if rows_lay is None:
        return
    row = QWidget()
    rl = QHBoxLayout(row)
    rl.setContentsMargins(0, 0, 0, 0)
    rl.setSpacing(3)
    combo = QComboBox(row)
    for col, label in enumerate_properties(app):
        combo.addItem(label, userData=col)
    if column:
        idx = combo.findData(column)
        if idx >= 0:
            combo.setCurrentIndex(idx)
    combo.setMinimumWidth(120)
    lo = QLineEdit(row); lo.setPlaceholderText("min"); lo.setFixedWidth(56)
    hi = QLineEdit(row); hi.setPlaceholderText("max"); hi.setFixedWidth(56)
    rm = QPushButton("✕", row)
    rm.setFixedWidth(24)
    rm.setObjectName("Ghost")
    rl.addWidget(combo, 1)
    rl.addWidget(lo)
    rl.addWidget(hi)
    rl.addWidget(rm)
    # insert before the trailing stretch
    rows_lay.insertWidget(rows_lay.count() - 1, row)
    entry = (combo, lo, hi)
    app._cell_browser_criteria_rows.append(entry)

    def _remove(_=False) -> None:
        try:
            app._cell_browser_criteria_rows.remove(entry)
        except ValueError:
            swallowed()
        row.deleteLater()
    rm.clicked.connect(_remove)


def _open_multiselect(app, btn, selections: dict, label) -> None:
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
    menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))


def _refresh_multiselect_label(selections: dict, label) -> None:
    n = sum(1 for v in selections.values() if v)
    total = len(selections)
    label.setText("(all)" if n == total else f"({n}/{total})")


def _sort_key(s: str):
    try:
        return (0, float(s))
    except (TypeError, ValueError):
        return (1, s)


def cell_browser_sidebar_refresh(app) -> None:
    """Repopulate criteria property combos + timepoint/FOV options from the
    current dataset + selection (called on tab show / dataset load)."""
    # property combos
    props = enumerate_properties(app)
    for combo, _lo, _hi in getattr(app, "_cell_browser_criteria_rows", []):
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
    cell_browser_sort_refresh(app)

    # timepoints (union across the dataset)
    tp_sel = app._cell_browser_tp_selections
    tps = list(app._app_state.dataset.all_timepoints_cache or [])
    tp_keys = [f"{t:g}" for t in tps]
    _sync_selection_dict(tp_sel, tp_keys)
    if getattr(app, "_cell_browser_tp_label", None) is not None:
        _refresh_multiselect_label(tp_sel, app._cell_browser_tp_label)

    # FOVs (union across the source wells — the selection, else all loaded wells,
    # matching collect_cells so the picker isn't empty before a well is chosen).
    fov_sel = app._cell_browser_fov_selections
    fovs: set = set()
    for well in cell_browser_source_wells(app):
        if well not in app._app_state.dataset.well_paths:
            continue
        df = app._get_rows(well)
        if df is not None and not df.empty and "fov" in df.columns:
            fovs.update(df["fov"].astype(str).unique().tolist())
    _sync_selection_dict(fov_sel, sorted(fovs, key=_sort_key))
    if getattr(app, "_cell_browser_fov_label", None) is not None:
        _refresh_multiselect_label(fov_sel, app._cell_browser_fov_label)


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
