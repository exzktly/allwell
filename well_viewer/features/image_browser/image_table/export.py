"""Image Table export: settings dialog, figure building, PNG/SVG save + copy.

Extracted from ``controller.py`` — the export actions and the matplotlib figure
builder. Pulls the per-cell pixels from the ``image_compose`` layer (downward
dependency), so the controller keeps event handling + orchestration.
``controller`` re-exports these names, so the toolbar wiring in ``tab_view`` is
unchanged.
"""

from __future__ import annotations

from pathlib import Path  # noqa: F401  (used by save dialogs)
from typing import Any, Dict, Optional, Tuple  # noqa: F401

from PySide6.QtWidgets import QFileDialog, QMessageBox  # noqa: F401

from well_viewer.features.image_browser.image_table.image_compose import (
    _compose_image_table_rgb,
    _export_binary_mask,
    _is_nuc_seg,
    _load_array,
    _load_mask_array,
    _parse_lut,
    _row_display_opts,
    _row_cmap_name,
)


def _export_opts(app) -> Dict[str, Any]:
    """Return the image-table export option dict, seeding defaults on demand."""
    opts = getattr(app._app_state.image_table, "export_opts", None)
    if not isinstance(opts, dict):
        opts = {
            "pad_inches": 0.10,    # outer margin around the saved figure
            "cell_pad": 0.50,      # tight_layout w_pad / h_pad between cells
            "show_titles": True,
            "title_fontsize": 7,
            "dpi": 300,
            "transparent_bg": True,
        }
        app._app_state.image_table.export_opts = opts
    return opts


def image_table_open_export_settings(app) -> None:
    """Pop a small modal dialog to edit the image-table export options."""
    from PySide6.QtWidgets import (
        QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
        QSpinBox,
    )

    opts = _export_opts(app)
    dlg = QDialog(app)
    dlg.setWindowTitle("Image Table — Export settings")
    form = QFormLayout(dlg)

    pad_spin = QDoubleSpinBox(dlg)
    pad_spin.setRange(0.0, 4.0)
    pad_spin.setSingleStep(0.05)
    pad_spin.setDecimals(2)
    pad_spin.setSuffix(" in")
    pad_spin.setValue(float(opts.get("pad_inches", 0.10)))
    pad_spin.setToolTip("Outer margin around the saved figure (pad_inches).")
    form.addRow("Outer margin:", pad_spin)

    cell_spin = QDoubleSpinBox(dlg)
    cell_spin.setRange(0.0, 4.0)
    cell_spin.setSingleStep(0.05)
    cell_spin.setDecimals(2)
    cell_spin.setValue(float(opts.get("cell_pad", 0.50)))
    cell_spin.setToolTip(
        "Gap between adjacent cells (passed to tight_layout as w_pad / h_pad)."
    )
    form.addRow("Cell gap:", cell_spin)

    titles_cb = QCheckBox(dlg)
    titles_cb.setChecked(bool(opts.get("show_titles", True)))
    titles_cb.setToolTip("Show the well/channel/timepoint label above each image.")
    form.addRow("Show titles:", titles_cb)

    title_size_spin = QSpinBox(dlg)
    title_size_spin.setRange(4, 24)
    title_size_spin.setValue(int(opts.get("title_fontsize", 7)))
    form.addRow("Title font size:", title_size_spin)

    dpi_spin = QSpinBox(dlg)
    dpi_spin.setRange(72, 1200)
    dpi_spin.setSingleStep(25)
    dpi_spin.setValue(int(opts.get("dpi", 300)))
    dpi_spin.setToolTip("Raster DPI used for PNG output.")
    form.addRow("PNG DPI:", dpi_spin)

    transparent_cb = QCheckBox(dlg)
    transparent_cb.setChecked(bool(opts.get("transparent_bg", True)))
    transparent_cb.setToolTip(
        "Save with a transparent background. Disable for an opaque white BG."
    )
    form.addRow("Transparent bg:", transparent_cb)

    btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    form.addRow(btns)

    if dlg.exec() != QDialog.Accepted:
        return

    opts.update({
        "pad_inches": float(pad_spin.value()),
        "cell_pad": float(cell_spin.value()),
        "show_titles": bool(titles_cb.isChecked()),
        "title_fontsize": int(title_size_spin.value()),
        "dpi": int(dpi_spin.value()),
        "transparent_bg": bool(transparent_cb.isChecked()),
    })


def _build_export_figure(app):
    """Render the current image-table contents to a matplotlib Figure.

    Returns ``(fig, save_kwargs)`` where *save_kwargs* are the format-agnostic
    keyword arguments to feed into ``fig.savefig`` (caller adds ``format`` and
    optionally ``dpi`` for raster outputs). Returns ``(None, None)`` when there
    is nothing to render (no cells, missing matplotlib/numpy).
    """
    cells = getattr(app, "_image_table_cells", None) or []
    if not cells:
        app._set_status("Image Table: nothing to export.")
        return None, None
    try:
        from matplotlib.figure import Figure
    except Exception as exc:
        QMessageBox.critical(app, "Export failed", f"matplotlib unavailable: {exc}")
        return None, None
    try:
        import numpy as _np
    except Exception as exc:
        QMessageBox.critical(app, "Export failed", f"numpy unavailable: {exc}")
        return None, None

    opts = _export_opts(app)
    pad_inches = float(opts.get("pad_inches", 0.10))
    cell_pad = float(opts.get("cell_pad", 0.50))
    show_titles = bool(opts.get("show_titles", True))
    title_fontsize = int(opts.get("title_fontsize", 7))
    dpi = int(opts.get("dpi", 300))
    transparent_bg = bool(opts.get("transparent_bg", True))

    rows = int(getattr(app._app_state.image_table, "rows", len(cells)) or len(cells))
    cols = int(getattr(app._app_state.image_table, "cols", len(cells[0])) or len(cells[0]))

    cache: Dict[Tuple[str, str, str], Dict] = dict(getattr(app._app_state.image_table, "image_cache", None) or {})
    rendered = getattr(app._app_state.image_table, "last_render", None) or {}
    crop_tool = getattr(app, "_image_table_crop_tool", None)
    # Display toggles (top-hat / boundaries / binary) + LUT are per ROW —
    # mirror the live thumbnail, reading each from the cell's row box so the
    # export matches what's on screen.

    bg_face = "none" if transparent_bg else "white"
    # Probe the first available cell to learn the image aspect ratio. With
    # aspect="equal" axes, a non-square image shrinks the axes box inside
    # its gridspec cell, which makes the visible inter-row gap differ from
    # the inter-column gap. Sizing the cell to the actual image aspect
    # keeps the axes filling the cell so row/column spacing match.
    img_h: Optional[int] = None
    img_w: Optional[int] = None
    for r, row in enumerate(cells):
        use_tophat, _sb, _bm = _row_display_opts(app, r)
        for c, cell in enumerate(row):
            arr = rendered.get((r, c))
            if arr is None:
                well = cell["well_cb"].currentText().strip()
                chan = cell["chan_cb"].currentText().strip()
                tp = cell["tp_cb"].currentText().strip()
                fov = cell["fov_cb"].currentText().strip()
                if well and chan and tp:
                    arr = _load_array(app, cache, well, chan, tp, fov, use_tophat=use_tophat)
            if arr is not None and crop_tool is not None:
                arr = crop_tool.apply_to_array(arr)
            if arr is not None:
                a = _np.asarray(arr)
                if a.ndim >= 2 and a.shape[0] > 0 and a.shape[1] > 0:
                    img_h, img_w = int(a.shape[0]), int(a.shape[1])
                    break
        if img_h is not None:
            break

    cell_w = 2.6
    if img_h and img_w:
        cell_h = cell_w * (img_h / img_w)
    else:
        cell_h = cell_w
    # Reserve extra inches per row for the per-axes title when enabled.
    if show_titles:
        cell_h += 0.2
    fig = Figure(
        figsize=(max(2.4, cols * cell_w), max(2.4, rows * cell_h)),
        dpi=dpi,
        facecolor=bg_face,
    )
    fig.patch.set_alpha(0.0 if transparent_bg else 1.0)

    for r, row in enumerate(cells):
        use_tophat, show_boundaries, binary_mode = _row_display_opts(app, r)
        for c, cell in enumerate(row):
            ax = fig.add_subplot(rows, cols, r * cols + c + 1)
            ax.set_facecolor("none" if transparent_bg else "white")
            well = cell["well_cb"].currentText().strip()
            chan = cell["chan_cb"].currentText().strip()
            tp = cell["tp_cb"].currentText().strip()
            fov = cell["fov_cb"].currentText().strip()

            arr = rendered.get((r, c))
            if arr is None and well and chan and tp:
                arr = _load_array(app, cache, well, chan, tp, fov, use_tophat=use_tophat)
            if arr is not None and crop_tool is not None:
                arr = crop_tool.apply_to_array(arr)

            if arr is not None:
                lo, hi = _parse_lut(app, r)
                # Render exactly the same RGB the live thumbnail shows:
                # full mpl colormap (not the black→tint approximation
                # this path used before) + show_binary / show_boundaries
                # toggles + crop. The shared helper is the single source
                # of truth — anything that changes the live look auto-
                # propagates to the export.
                mask_arr = None
                if show_boundaries and not _is_nuc_seg(chan):
                    mask_arr = _load_mask_array(app, cache, well, tp, fov)
                    if mask_arr is not None and crop_tool is not None:
                        mask_arr = crop_tool.apply_to_array(mask_arr)
                binary_mask = None
                if binary_mode in ("mask", "threshold") and not _is_nuc_seg(chan):
                    binary_mask = _export_binary_mask(
                        app, cache, well, chan, tp, fov, binary_mode, crop_tool,
                    )
                rgb = _compose_image_table_rgb(
                    arr, mask_arr,
                    lo=lo, hi=hi,
                    cmap_name=_row_cmap_name(app, r),
                    is_overlay=_is_nuc_seg(chan),
                    binary_mask=binary_mask,
                    show_boundaries=show_boundaries,
                )
                if rgb is not None:
                    ax.imshow(rgb, aspect="equal")
                else:
                    ax.text(
                        0.5, 0.5, "(unrenderable)",
                        ha="center", va="center", transform=ax.transAxes, fontsize=7,
                    )
            else:
                ax.text(
                    0.5, 0.5, "(no image)",
                    ha="center", va="center", transform=ax.transAxes, fontsize=7,
                )

            if show_titles:
                if well and chan and tp:
                    title = f"{well}  {chan.upper()}  T:{tp}"
                    if fov:
                        title += f"  FOV:{fov}"
                else:
                    title = "(unset)"
                ax.set_title(title, fontsize=title_fontsize)
            ax.set_xticks([])
            ax.set_yticks([])
            for spine in ax.spines.values():
                spine.set_visible(False)
    try:
        fig.tight_layout(w_pad=cell_pad, h_pad=cell_pad)
    except Exception:
        fig.tight_layout()

    save_kwargs: Dict[str, Any] = dict(
        bbox_inches="tight", pad_inches=pad_inches,
        facecolor=bg_face, transparent=transparent_bg,
    )
    save_kwargs["_dpi"] = dpi  # caller decides whether to forward (raster only)
    return fig, save_kwargs


def image_table_export(app) -> None:
    """Export the current image table to PNG/PDF/SVG with no background."""
    fig, save_kwargs = _build_export_figure(app)
    if fig is None:
        return

    initial_dir = str(app._app_state.dataset.data_dir) if app._app_state.dataset.data_dir else ""
    initial = (
        str(Path(initial_dir) / "image_table.png") if initial_dir else "image_table.png"
    )
    out, _ = QFileDialog.getSaveFileName(
        app, "Export image table", initial,
        "PNG (*.png);;PDF (*.pdf);;SVG (*.svg);;All files (*.*)",
    )
    if not out:
        return
    fmt = Path(out).suffix.lstrip(".").lower() or "png"
    dpi = save_kwargs.pop("_dpi")
    try:
        kw = dict(save_kwargs, format=fmt)
        if fmt == "png":
            kw["dpi"] = dpi
        fig.savefig(out, **kw)
        app._set_status(f"Image table saved → {Path(out).name}")
    except Exception as exc:
        QMessageBox.critical(app, "Export failed", str(exc))


def image_table_copy_png(app) -> None:
    """Render the image table and place a PNG of it on the system clipboard."""
    import io
    from PySide6.QtGui import QGuiApplication, QImage

    fig, save_kwargs = _build_export_figure(app)
    if fig is None:
        return
    dpi = save_kwargs.pop("_dpi")
    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="png", dpi=dpi, **save_kwargs)
    except Exception as exc:
        QMessageBox.critical(app, "Copy failed", str(exc))
        return
    img = QImage.fromData(buf.getvalue(), "PNG")
    if img.isNull():
        QMessageBox.critical(app, "Copy failed", "Could not decode rendered PNG.")
        return
    QGuiApplication.clipboard().setImage(img)
    app._set_status("Image table copied to clipboard (PNG).")


def image_table_copy_svg(app) -> None:
    """Render the image table and copy it to the clipboard, preferring vector.

    On macOS the figure is written natively to ``NSPasteboard`` so Illustrator
    pastes true editable vector; elsewhere a Qt clipboard payload is used (see
    ``copy_figure_to_clipboard``).
    """
    from well_viewer.features.export.style import copy_figure_to_clipboard

    fig, save_kwargs = _build_export_figure(app)
    if fig is None:
        return
    save_kwargs.pop("_dpi", None)
    save_kwargs.pop("format", None)

    status = copy_figure_to_clipboard(
        fig, svg_kwargs=save_kwargs, pdf_kwargs=save_kwargs,
    )
    if status.startswith("Copy failed"):
        QMessageBox.critical(app, "Copy failed", "Could not render the image table.")
        return
    app._set_status("Image table " + status[0].lower() + status[1:])
