"""Image Table selector grid + per-channel LUT row view builders.

These are pure view-construction helpers extracted from
``well_viewer.image_table_controller`` so the controller module can stay
focused on data loading, applying user choices, and exporting.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)
from well_viewer.core.safe import swallowed


def build_image_table_grid(
    app,
    *,
    rows: int,
    cols: int,
    chan_opts: List[str],
    tp_opts: List[str],
    fov_opts: List[str],
    well_opts: List[str],
    lut_color_names: List[str],
    on_apply_row_well,
    on_apply_row_channel,
    on_generate,
) -> Tuple[
    List[List[Dict[str, Any]]],
    List[QComboBox],
    List[QComboBox],
    List[QComboBox],
]:
    """Populate ``app._image_table_selector_grid`` with the selector cells.

    Returns ``(cells, row_chan_cbs, row_lut_color_cbs, row_well_cbs)``. The
    caller (controller) wires those onto ``app`` and re-renders the LUT row.
    """
    from well_viewer.core.ui_helpers import clear_layout

    layout = app._image_table_selector_grid
    clear_layout(layout)

    prev_lut_cbs: List[QComboBox] = list(
        getattr(app, "_image_table_row_lut_color_cbs", []) or []
    )

    cells: List[List[Dict[str, Any]]] = []
    row_chan_cbs: List[QComboBox] = []
    row_lut_color_cbs: List[QComboBox] = []
    row_well_cbs: List[QComboBox] = []

    for r in range(rows):
        row_box = QGroupBox(f"Row {r + 1}")
        row_box.setObjectName("ImageTableRowOptions")
        row_box.setStyleSheet(
            "QGroupBox#ImageTableRowOptions { "
            "background-color: rgba(99, 102, 241, 0.10); "
            "border: 1px solid rgba(99, 102, 241, 0.35); "
            "border-radius: 4px; margin-top: 8px; "
            "} "
            "QGroupBox#ImageTableRowOptions::title { "
            "subcontrol-origin: margin; left: 8px; padding: 0 4px; "
            "}"
        )
        rbl = QVBoxLayout(row_box)
        rbl.setContentsMargins(6, 8, 6, 6)
        rbl.setSpacing(4)

        well_lbl = QLabel("Well:", row_box)
        well_lbl.setStyleSheet("font-size: 10px;")
        rbl.addWidget(well_lbl)
        row_well_cb = QComboBox(row_box)
        row_well_cb.addItems(well_opts)
        rbl.addWidget(row_well_cb)
        row_well_cb.currentIndexChanged.connect(
            lambda _i, ridx=r: on_apply_row_well(app, ridx)
        )

        chan_lbl = QLabel("Channel:", row_box)
        chan_lbl.setStyleSheet("font-size: 10px;")
        rbl.addWidget(chan_lbl)
        row_chan_cb = QComboBox(row_box)
        row_chan_cb.addItems(chan_opts)
        rbl.addWidget(row_chan_cb)
        row_chan_cb.currentIndexChanged.connect(
            lambda _i, ridx=r: on_apply_row_channel(app, ridx)
        )

        col_lbl = QLabel("LUT:", row_box)
        col_lbl.setStyleSheet("font-size: 10px;")
        rbl.addWidget(col_lbl)
        # v2: LutSelector (gradient strip + searchable popover). The
        # controller's _row_tint translates the selector's mpl colormap
        # name back to the per-row (r,g,b) tint via LUT_COLORS.
        from widgets.lut_selector import LutSelector as _LutSelector
        row_color_cb = _LutSelector(row_box)
        _initial = lut_color_names[0] if lut_color_names else "Greys"
        if r < len(prev_lut_cbs):
            prev = prev_lut_cbs[r]
            prev_name = prev.lut() if hasattr(prev, "lut") else prev.currentText()
            if prev_name:
                _initial = prev_name
        row_color_cb.setLut(_initial, reversed=False)
        rbl.addWidget(row_color_cb)
        # ``lutChanged`` is wired by image_table_controller.image_table_rebuild_grid
        # to ``image_table_generate`` so the rendered thumbnails re-tint as soon
        # as the user picks a new colormap.

        layout.addWidget(row_box, r, 0)
        row_chan_cbs.append(row_chan_cb)
        row_lut_color_cbs.append(row_color_cb)
        row_well_cbs.append(row_well_cb)

        cell_row: List[Dict[str, Any]] = []
        for c in range(cols):
            cell = build_selector_cell(
                r, c, well_opts, chan_opts, tp_opts, fov_opts,
            )
            layout.addWidget(cell["frame"], r, c + 1)
            cell_row.append(cell)
        cells.append(cell_row)

    return cells, row_chan_cbs, row_lut_color_cbs, row_well_cbs


def build_selector_cell(
    r: int, c: int,
    well_opts: List[str], chan_opts: List[str],
    tp_opts: List[str], fov_opts: List[str],
) -> Dict[str, Any]:
    """Build one (row, col) groupbox containing four dropdowns."""
    box = QGroupBox(f"({r + 1}, {c + 1})")
    inner = QVBoxLayout(box)
    inner.setContentsMargins(6, 8, 6, 6)
    inner.setSpacing(2)

    def _row(label_text: str, options: List[str]) -> QComboBox:
        rl = QHBoxLayout()
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        lbl = QLabel(label_text, box)
        lbl.setFixedWidth(54)
        rl.addWidget(lbl)
        cb = QComboBox(box)
        cb.addItems(options)
        rl.addWidget(cb, 1)
        inner.addLayout(rl)
        return cb

    well_cb = _row("Well:", well_opts)
    chan_cb = _row("Channel:", chan_opts)
    tp_cb = _row("Timepoint:", tp_opts)
    fov_cb = _row("FOV:", fov_opts)

    return {
        "frame": box,
        "well_cb": well_cb,
        "chan_cb": chan_cb,
        "tp_cb": tp_cb,
        "fov_cb": fov_cb,
    }


def build_lut_row(
    app,
    *,
    on_generate,
    on_auto_lut,
) -> None:
    """Rebuild the per-row LUT + display editors below the selector grid.

    One box per grid **row** (keyed by row index), not per channel — so two
    rows showing the same channel have independent min/max, Fit, and display
    toggles (Tophat / Boundaries / Binary). Mirrors the per-row colormap
    (``image_compose._row_cmap_name``); the render + export paths read each
    cell's settings from its row's box.
    """
    from well_viewer.core.ui_helpers import btn_secondary, clear_layout

    container = getattr(app, "_image_table_lut_container", None)
    if container is None:
        return
    layout = container.layout()

    # Preserve prior per-row state (min/max text, auto flag, display toggles)
    # across rebuilds, keyed by row index.
    prev: Dict[int, dict] = {}
    for r, entry in (getattr(app, "_image_table_lut", None) or {}).items():
        try:
            prev[r] = {
                "min": entry["min"].text(),
                "max": entry["max"].text(),
                "auto": bool(entry.get("auto", True)),
                "tophat": bool(entry["tophat"].isChecked()) if entry.get("tophat") else False,
                "boundaries": bool(entry["boundaries"].isChecked()) if entry.get("boundaries") else False,
                "binary": entry["binary"].currentData() if entry.get("binary") else "",
            }
        except Exception:
            swallowed()  # silent except
            continue

    clear_layout(layout)
    app._image_table_lut = {}

    cells = getattr(app, "_image_table_cells", None) or []
    row_chan_cbs = getattr(app, "_image_table_row_chan_cbs", None) or []

    def _row_channel(r: int) -> str:
        if 0 <= r < len(row_chan_cbs):
            t = (row_chan_cbs[r].currentText() or "").strip()
            if t:
                return t
        row = cells[r] if 0 <= r < len(cells) else []
        for cell in row:
            cb = cell.get("chan_cb")
            t = (cb.currentText() or "").strip() if cb is not None else ""
            if t:
                return t
        return ""

    for r in range(len(cells)):
        chan = _row_channel(r)
        title = f"Row {r + 1}" + (f" · {chan.upper()}" if chan else "")
        box = QGroupBox(title, container)
        bl = QVBoxLayout(box)
        bl.setContentsMargins(6, 8, 6, 4)
        bl.setSpacing(3)
        p = prev.get(r, {})

        # ── min / max / Fit ──────────────────────────────────────────────
        lut_row = QHBoxLayout()
        lut_row.setSpacing(4)

        def _mk_edit(text: str) -> QLineEdit:
            le = QLineEdit(text, box)
            le.setFixedWidth(64)
            # Empty = auto-scale per image; a fitted number fills in on Fit /
            # Generate. Editing marks the row a manual override so Generate
            # stops overwriting it; "Fit" re-enables tracking.
            le.setPlaceholderText("auto")
            le.textEdited.connect(
                lambda _t, rr=r: app._image_table_lut.get(rr, {}).__setitem__("auto", False)
            )
            return le

        lut_row.addWidget(QLabel("min:", box))
        min_edit = _mk_edit(p.get("min", ""))
        lut_row.addWidget(min_edit)
        lut_row.addWidget(QLabel("max:", box))
        max_edit = _mk_edit(p.get("max", ""))
        lut_row.addWidget(max_edit)
        fit_btn = btn_secondary(box, "Fit", lambda rr=r: on_auto_lut(app, rr))
        fit_btn.setToolTip("Re-fit min/max to this row's pooled image range.")
        lut_row.addWidget(fit_btn)
        lut_row.addStretch(1)
        bl.addLayout(lut_row)

        # ── display: Tophat / Boundaries / Binary (per row) ──────────────
        disp_row = QHBoxLayout()
        disp_row.setSpacing(8)
        tophat_cb = QCheckBox("Tophat", box)
        tophat_cb.setChecked(bool(p.get("tophat", False)))
        tophat_cb.setToolTip("Show the background-subtracted (top-hat) image for this row.")
        tophat_cb.toggled.connect(lambda _c=False: on_generate(app))
        disp_row.addWidget(tophat_cb)
        bound_cb = QCheckBox("Boundaries", box)
        bound_cb.setChecked(bool(p.get("boundaries", False)))
        bound_cb.setToolTip("Overlay white cell-boundary outlines for this row.")
        bound_cb.toggled.connect(lambda _c=False: on_generate(app))
        disp_row.addWidget(bound_cb)
        disp_row.addWidget(QLabel("Binary:", box))
        bin_cb = QComboBox(box)
        for _lbl, _key in (("None", ""), ("Mask", "mask"), ("≥ Thr", "threshold")):
            bin_cb.addItem(_lbl, userData=_key)
        prev_bin = p.get("binary", "") or ""
        for i in range(bin_cb.count()):
            if bin_cb.itemData(i) == prev_bin:
                bin_cb.setCurrentIndex(i)
                break
        bin_cb.currentIndexChanged.connect(lambda _i=0: on_generate(app))
        disp_row.addWidget(bin_cb)
        disp_row.addStretch(1)
        bl.addLayout(disp_row)

        layout.addWidget(box)
        app._image_table_lut[r] = {
            "min": min_edit, "max": max_edit, "auto": bool(p.get("auto", True)),
            "tophat": tophat_cb, "boundaries": bound_cb, "binary": bin_cb,
            "chan": chan,
        }

    layout.addStretch(1)
