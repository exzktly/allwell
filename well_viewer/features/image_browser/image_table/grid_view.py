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
    QWidget,
)
from well_viewer.core.safe import swallowed


def build_image_table_grid(
    ctx,
    *,
    rows: int,
    cols: int,
    chan_opts: List[str],
    tp_opts: List[str],
    fov_opts: List[str],
    well_opts: List[str],
    on_apply_row_well,
    on_apply_row_channel,
    on_remove_row=None,
    on_remove_col=None,
) -> Tuple[
    List[List[Dict[str, Any]]],
    List[QComboBox],
    List[QComboBox],
]:
    """Populate ``ctx.runtime.image_table.selector_grid`` with the selector cells.

    Returns ``(cells, row_chan_cbs, row_well_cbs)``. The per-row colormap / LUT
    levels / display toggles live in the separate per-row table built by
    :func:`build_lut_row`, not here.
    """
    from well_viewer.core.ui_helpers import btn_secondary, clear_layout

    layout = ctx.runtime.image_table.selector_grid
    clear_layout(layout)

    cells: List[List[Dict[str, Any]]] = []
    row_chan_cbs: List[QComboBox] = []
    row_well_cbs: List[QComboBox] = []

    # Optional column-header row of "×" remove buttons. When present it shifts
    # every data row down by one (grid row 0 becomes the header).
    grid_row_offset = 0
    if on_remove_col is not None:
        grid_row_offset = 1
        layout.addWidget(QLabel(""), 0, 0)  # placeholder over row-controls
        for c in range(cols):
            col_header = QWidget()
            col_h = QHBoxLayout(col_header)
            col_h.setContentsMargins(2, 2, 2, 2)
            col_h.setSpacing(2)
            lbl = QLabel(f"Col {c + 1}", col_header)
            lbl.setObjectName("Muted")
            lbl.setStyleSheet("font-size: 9px;")
            col_h.addWidget(lbl)
            col_h.addStretch(1)
            remove_btn = btn_secondary(col_header, "×", lambda c_=c: on_remove_col(ctx, c_))
            remove_btn.setFixedSize(24, 24)
            remove_btn.setToolTip(f"Remove column {c + 1}")
            col_h.addWidget(remove_btn)
            layout.addWidget(col_header, 0, c + 1)

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

        # Make clear this tinted box is a row-wide shortcut, not another cell:
        # its Well/Channel apply to every cell in the row.
        hint_lbl = QLabel("Apply to whole row", row_box)
        hint_lbl.setObjectName("Muted")
        hint_lbl.setStyleSheet("font-size: 9px;")
        hint_lbl.setWordWrap(True)
        rbl.addWidget(hint_lbl)

        # Per-row "×" remove button.
        if on_remove_row is not None:
            remove_row_btn = btn_secondary(row_box, "× Remove row", lambda r_=r: on_remove_row(ctx, r_))
            remove_row_btn.setToolTip(f"Remove row {r + 1}")
            rbl.addWidget(remove_row_btn)

        well_lbl = QLabel("Well:", row_box)
        well_lbl.setStyleSheet("font-size: 10px;")
        rbl.addWidget(well_lbl)
        row_well_cb = QComboBox(row_box)
        row_well_cb.addItems(well_opts)
        rbl.addWidget(row_well_cb)
        row_well_cb.currentIndexChanged.connect(
            lambda _i, ridx=r: on_apply_row_well(ctx, ridx)
        )

        chan_lbl = QLabel("Channel:", row_box)
        chan_lbl.setStyleSheet("font-size: 10px;")
        rbl.addWidget(chan_lbl)
        row_chan_cb = QComboBox(row_box)
        row_chan_cb.addItems(chan_opts)
        rbl.addWidget(row_chan_cb)
        row_chan_cb.currentIndexChanged.connect(
            lambda _i, ridx=r: on_apply_row_channel(ctx, ridx)
        )

        layout.addWidget(row_box, r + grid_row_offset, 0)
        row_chan_cbs.append(row_chan_cb)
        row_well_cbs.append(row_well_cb)

        cell_row: List[Dict[str, Any]] = []
        for c in range(cols):
            cell = build_selector_cell(
                r, c, well_opts, chan_opts, tp_opts, fov_opts,
            )
            layout.addWidget(cell["frame"], r + grid_row_offset, c + 1)
            cell_row.append(cell)
        cells.append(cell_row)

    return cells, row_chan_cbs, row_well_cbs


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
        lbl.setFixedWidth(72)  # wide enough for "Timepoint:" (was 54 → "Timepoi…")
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
    ctx,
    *,
    on_auto_lut,
    lut_color_names=None,
) -> None:
    """Rebuild the per-row levels + display **table** below the selector grid.

    One grid ROW per image-table row (keyed by row index): an aligned table with
    columns ``Row N · CHAN | Color | Min | Max | Fit | Source | Boundaries | Binary``.
    The checkbox labels live in the column header so the checkboxes are text-less
    and centred (no overlap). All controls are passive — they stage state read by
    the render/export paths (``image_compose._parse_lut`` / ``_row_display_opts``
    / ``_row_cmap_name``); only the explicit **Generate** button renders. The Color
    selectors are stored in ``ctx.runtime.image_table.row_lut_color_cbs`` (this module
    owns them; ``build_image_table_grid`` no longer builds a colour column).
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QGridLayout
    from well_viewer.core.ui_helpers import btn_secondary, clear_layout
    from widgets.lut_selector import LutSelector

    container = getattr(ctx.runtime.image_table, "lut_container", None)
    if container is None:
        return
    layout = container.layout()
    if not isinstance(layout, QGridLayout):
        return

    # Preserve prior per-row state (min/max text, auto flag, display toggles,
    # colormap) across rebuilds, keyed by row index. Read the colour selectors
    # *before* clear_layout destroys them.
    prev: Dict[int, dict] = {}
    for r, entry in (getattr(ctx.runtime.image_table, "lut", None) or {}).items():
        try:
            prev[r] = {
                "min": entry["min"].text(),
                "max": entry["max"].text(),
                "auto": bool(entry.get("auto", True)),
                "source": entry["source"].currentData() if entry.get("source") else "raw",
                "boundaries": bool(entry["boundaries"].isChecked()) if entry.get("boundaries") else False,
                "binary": entry["binary"].currentData() if entry.get("binary") else "",
                "log": bool(entry["log"].isChecked()) if entry.get("log") else False,
            }
        except Exception:
            swallowed()  # silent except
            continue
    prev_colors: Dict[int, str] = {}
    for r, cb in enumerate(getattr(ctx.runtime.image_table, "row_lut_color_cbs", None) or []):
        try:
            name = cb.lut() if hasattr(cb, "lut") else cb.currentText()
            if name:
                prev_colors[r] = name
        except Exception:
            swallowed()  # silent except

    clear_layout(layout)
    ctx.runtime.image_table.lut = {}
    color_cbs: List[Any] = []

    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    row_chan_cbs = getattr(ctx.runtime.image_table, "row_chan_cbs", None) or []
    default_cmap = (lut_color_names[0] if lut_color_names else "Greys") or "Greys"

    # LUT-group map (grid row → LUT index). Grouped/tiled layouts collapse the
    # editor table to one row per row-axis value (channel), shared across bands;
    # non-grouped → identity. ``n_lut`` editor rows, each labelled by the channel
    # of its first mapped grid row.
    lut_map = getattr(ctx.runtime.image_table, "lut_row_map", None) or list(range(len(cells)))
    n_lut = (max(lut_map) + 1) if lut_map else 0
    grouped = n_lut < len(cells)

    def _rep_grid_row(li: int) -> int:
        try:
            return lut_map.index(li)
        except ValueError:
            return li

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

    # ── header row ───────────────────────────────────────────────────────
    headers = ("", "Color", "Min", "Max", "", "Source", "Boundaries", "Binary", "Log")
    for c, text in enumerate(headers):
        if not text:
            continue
        h = QLabel(text, container)
        h.setObjectName("Muted")
        if c in (2, 3) or c >= 5:
            h.setAlignment(Qt.AlignCenter)
        layout.addWidget(h, 0, c)

    def _checkbox_cell(checked: bool) -> "tuple":
        wrap = QWidget(container)
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        cb = QCheckBox(wrap)
        cb.setChecked(bool(checked))
        wl.addStretch(1)
        wl.addWidget(cb)
        wl.addStretch(1)
        return wrap, cb

    for r in range(n_lut):
        grow = r + 1  # grid row 0 is the header
        chan = _row_channel(_rep_grid_row(r))
        p = prev.get(r, {})

        # Grouped: label by channel (one shared LUT row per channel). Otherwise
        # keep the per-grid-row "Row N · CHAN" label.
        if grouped:
            label_text = chan.upper() if chan else f"Group {r + 1}"
        else:
            label_text = f"Row {r + 1}" + (f" · {chan.upper()}" if chan else "")
        name_lbl = QLabel(label_text, container)
        layout.addWidget(name_lbl, grow, 0)

        # Color (per-row colormap) — moved here from the master grid. Passive:
        # it stages the colormap; the next Generate re-tints.
        color_cb = LutSelector(container)
        color_cb.setLut(prev_colors.get(r, default_cmap), reversed=False)
        try:
            color_cb.setFixedWidth(120)
        except Exception:
            swallowed()
        layout.addWidget(color_cb, grow, 1)
        color_cbs.append(color_cb)

        def _mk_edit(text: str) -> QLineEdit:
            le = QLineEdit(text, container)
            le.setFixedWidth(70)
            le.setAlignment(Qt.AlignCenter)
            # Empty = auto-scale per image; a fitted number fills in on Fit.
            # Editing marks the row a manual override so Generate / Fit stops
            # overwriting it; "Fit" re-enables tracking. No auto-render.
            le.setPlaceholderText("auto")
            le.textEdited.connect(
                lambda _t, rr=r: ctx.runtime.image_table.lut.get(rr, {}).__setitem__("auto", False)
            )
            return le

        min_edit = _mk_edit(p.get("min", ""))
        layout.addWidget(min_edit, grow, 2)
        max_edit = _mk_edit(p.get("max", ""))
        layout.addWidget(max_edit, grow, 3)

        fit_btn = btn_secondary(container, "Fit", lambda rr=r: on_auto_lut(ctx, rr))
        fit_btn.setToolTip("Re-fit min/max to this row's pooled image range (applies on Generate).")
        try:
            fit_btn.setFixedWidth(46)
        except Exception:
            swallowed()
        layout.addWidget(fit_btn, grow, 4)

        # Image stage to show for this row: Raw (unprocessed) vs Corrected (the
        # saved top-hat / background-subtracted output — same "corrected" the
        # Review Image tab shows). Replaces the old single Top-hat checkbox.
        source_cb = QComboBox(container)
        for _lbl, _key in (("Raw", "raw"), ("Corrected", "corrected")):
            source_cb.addItem(_lbl, userData=_key)
        prev_src = p.get("source", "raw") or "raw"
        for i in range(source_cb.count()):
            if source_cb.itemData(i) == prev_src:
                source_cb.setCurrentIndex(i)
                break
        source_cb.setToolTip(
            "Image stage shown for this row:\n"
            "• Raw — unprocessed fluorescence.\n"
            "• Corrected — the saved top-hat / background-subtracted image "
            "(falls back to raw when no corrected image exists)."
        )
        source_cb.setFixedWidth(110)
        layout.addWidget(source_cb, grow, 5)
        bound_wrap, bound_cb = _checkbox_cell(p.get("boundaries", False))
        bound_cb.setToolTip("Overlay white cell-boundary outlines for this row.")
        layout.addWidget(bound_wrap, grow, 6)

        bin_cb = QComboBox(container)
        for _lbl, _key in (("None", ""), ("Mask", "mask"), ("≥ Thr", "threshold")):
            bin_cb.addItem(_lbl, userData=_key)
        prev_bin = p.get("binary", "") or ""
        for i in range(bin_cb.count()):
            if bin_cb.itemData(i) == prev_bin:
                bin_cb.setCurrentIndex(i)
                break
        bin_cb.setFixedWidth(96)
        layout.addWidget(bin_cb, grow, 7)

        # Log-scale display toggle (per row) — staged like the other display
        # options; the next Generate / export renders the intensity on a log
        # scale (image_compose._row_log_scale reads it).
        log_wrap, log_cb = _checkbox_cell(p.get("log", False))
        log_cb.setToolTip("Show this row's image on a log10 intensity scale.")
        layout.addWidget(log_wrap, grow, 8)

        ctx.runtime.image_table.lut[r] = {
            "min": min_edit, "max": max_edit, "auto": bool(p.get("auto", True)),
            "source": source_cb, "boundaries": bound_cb, "binary": bin_cb,
            "log": log_cb, "chan": chan,
        }

    ctx.runtime.image_table.row_lut_color_cbs = color_cbs
    # Keep the label + control columns hugging the left; let a trailing spacer
    # column absorb the extra width. Previously column 0 (the "Row N · CHAN"
    # label) was stretched, which shoved every control to the far right and left
    # a large empty gap between each row label and its controls.
    layout.setColumnStretch(0, 0)
    layout.setColumnStretch(len(headers), 1)
    layout.setHorizontalSpacing(14)
    layout.setVerticalSpacing(6)
