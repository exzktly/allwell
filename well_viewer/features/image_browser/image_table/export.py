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

from well_viewer.core.safe import swallowed

from well_viewer.features.image_browser.image_table.image_compose import (
    _load_array,
    _row_display_opts,
    cell_display_rgb,
    composite_column_rgb,
)


def _export_opts(ctx) -> Dict[str, Any]:
    """Return the image-table export option dict, seeding defaults on demand."""
    opts = getattr(ctx.state.image_table, "export_opts", None)
    if not isinstance(opts, dict):
        opts = {
            "pad_inches": 0.10,    # outer margin around the saved figure
            "cell_pad": 0.50,      # tight_layout w_pad / h_pad between cells
            "show_titles": True,
            "title_fontsize": 7,
            "dpi": 300,
            "transparent_bg": True,
        }
        ctx.state.image_table.export_opts = opts
    return opts


def image_table_open_export_settings(ctx) -> None:
    """Pop a small modal dialog to edit the image-table export options."""
    from PySide6.QtWidgets import (
        QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
        QSpinBox,
    )

    opts = _export_opts(ctx)
    dlg = QDialog(ctx.window)
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


def _max_pool_rgb(rgb, max_side: int):
    """Shrink an (H,W,3+) uint8 image so its longest side ≤ *max_side*, keeping
    the **brightest** pixel of each block (by luminance).

    Plain area-averaging (matplotlib's default when an imshow'd image is drawn
    smaller than native) collapses sparse bright-on-dark fluorescence — the few
    bright pixels get averaged with their dark neighbours and the contrast washes
    out. Max-luminance pooling preserves the peak signal (and the true colour of
    that peak pixel), so a downsampled grid still reads like the live view.
    """
    import numpy as _np

    a = _np.asarray(rgb)
    if a.ndim != 3 or a.shape[2] < 3:
        return rgb
    h, w = a.shape[:2]
    longest = max(h, w)
    if longest <= max_side:
        return rgb
    k = int(_np.ceil(longest / float(max_side)))
    if k <= 1:
        return rgb
    ph, pw = (-h) % k, (-w) % k
    if ph or pw:
        a = _np.pad(a, ((0, ph), (0, pw), (0, 0)), mode="edge")
    H, W, C = a.shape
    nbh, nbw = H // k, W // k
    blocks = a.reshape(nbh, k, nbw, k, C).transpose(0, 2, 1, 3, 4).reshape(nbh, nbw, k * k, C)
    lum = blocks[..., :3].astype(_np.float32) @ _np.array([0.299, 0.587, 0.114], _np.float32)
    idx = lum.argmax(axis=2)  # brightest pixel per block
    out = _np.take_along_axis(blocks, idx[..., None, None], axis=2)[:, :, 0, :]
    return out.astype(a.dtype)


def _build_export_figure(ctx, *, cell_max_px: Optional[int] = None):
    """Render the current image-table contents to a matplotlib Figure.

    Returns ``(fig, save_kwargs)`` where *save_kwargs* are the format-agnostic
    keyword arguments to feed into ``fig.savefig`` (caller adds ``format`` and
    optionally ``dpi`` for raster outputs). Returns ``(None, None)`` when there
    is nothing to render (no cells, missing matplotlib/numpy).

    ``cell_max_px`` (gallery-save path only) pre-shrinks each cell to that
    longest-side pixel budget via brightness-preserving max-pooling + nearest
    interpolation, so a small saved snapshot keeps its contrast (see
    :func:`_max_pool_rgb`). ``None`` (file export / clipboard) renders full-res.
    """
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    if not cells:
        ctx.notify.set_status("Image Table: nothing to export.")
        return None, None
    try:
        from matplotlib.figure import Figure
    except Exception as exc:
        QMessageBox.critical(ctx.window, "Export failed", f"matplotlib unavailable: {exc}")
        return None, None
    try:
        import numpy as _np
    except Exception as exc:
        QMessageBox.critical(ctx.window, "Export failed", f"numpy unavailable: {exc}")
        return None, None

    opts = _export_opts(ctx)
    pad_inches = float(opts.get("pad_inches", 0.10))
    cell_pad = float(opts.get("cell_pad", 0.50))
    show_titles = bool(opts.get("show_titles", True))
    title_fontsize = int(opts.get("title_fontsize", 7))
    dpi = int(opts.get("dpi", 300))
    transparent_bg = bool(opts.get("transparent_bg", True))

    rows = int(getattr(ctx.state.image_table, "rows", len(cells)) or len(cells))
    cols = int(getattr(ctx.state.image_table, "cols", len(cells[0])) or len(cells[0]))
    # Composite adds one extra row at the bottom (additive merge of the rows
    # above, per column). Reserve a subplot row for it up front.
    composite = bool(getattr(ctx.state.image_table, "composite", False)) and bool(cells)
    if composite:
        rows += 1

    cache: Dict[Tuple[str, str, str], Dict] = dict(getattr(ctx.state.image_table, "image_cache", None) or {})
    rendered = getattr(ctx.state.image_table, "last_render", None) or {}
    crop_tool = getattr(ctx.runtime.image_table, "crop_tool", None)
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
        for c, cell in enumerate(row):
            arr = rendered.get((r, c))
            if arr is None:
                well = cell["well"]
                chan = cell["chan"]
                tp = cell["tp"]
                fov = cell["fov"]
                # raw vs corrected is a per-channel render option (channel_opts).
                use_tophat = _row_display_opts(ctx, r, chan)[0]
                if well and chan and tp:
                    arr = _load_array(ctx, cache, well, chan, tp, fov, use_tophat=use_tophat)
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
        for c, cell in enumerate(row):
            ax = fig.add_subplot(rows, cols, r * cols + c + 1)
            ax.set_facecolor("none" if transparent_bg else "white")
            well = cell["well"]
            chan = cell["chan"]
            tp = cell["tp"]
            fov = cell["fov"]

            arr = rendered.get((r, c))
            if arr is None and well and chan and tp:
                use_tophat = _row_display_opts(ctx, r, chan)[0]
                arr = _load_array(ctx, cache, well, chan, tp, fov, use_tophat=use_tophat)

            if arr is not None:
                # Render exactly the same RGB the live thumbnail shows —
                # full mpl colormap + show_binary / show_boundaries toggles +
                # crop — via the shared single-source helper, so anything that
                # changes the live look auto-propagates to the export.
                rgb = cell_display_rgb(ctx, cache, cell, r, crop_tool, arr)
                if rgb is not None:
                    if cell_max_px:
                        rgb = _max_pool_rgb(rgb, cell_max_px)
                        ax.imshow(rgb, aspect="equal", interpolation="nearest")
                    else:
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
                    from well_viewer.domain.selections_model import well_display_label
                    title = f"{well_display_label(ctx, well)}  {chan.upper()}  T:{tp}"
                    if fov:
                        title += f"  FOV:{fov}"
                else:
                    title = "(unset)"
                ax.set_title(title, fontsize=title_fontsize)
            ax.set_xticks([])
            ax.set_yticks([])
            for spine in ax.spines.values():
                spine.set_visible(False)

    if composite:
        comp_row = len(cells)
        for c in range(cols):
            ax = fig.add_subplot(rows, cols, comp_row * cols + c + 1)
            ax.set_facecolor("none" if transparent_bg else "white")
            comp = composite_column_rgb(ctx, cache, cells, crop_tool, c, rendered)
            if comp is not None:
                if cell_max_px:
                    comp = _max_pool_rgb(comp, cell_max_px)
                    ax.imshow(comp, aspect="equal", interpolation="nearest")
                else:
                    ax.imshow(comp, aspect="equal")
            else:
                ax.text(
                    0.5, 0.5, "(no image)",
                    ha="center", va="center", transform=ax.transAxes, fontsize=7,
                )
            if show_titles:
                ax.set_title("Composite", fontsize=title_fontsize)
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


def image_table_export(ctx) -> None:
    """Export the current image table to PNG/PDF/SVG with no background."""
    fig, save_kwargs = _build_export_figure(ctx)
    if fig is None:
        return

    initial_dir = str(ctx.state.dataset.data_dir) if ctx.state.dataset.data_dir else ""
    initial = (
        str(Path(initial_dir) / "image_table.png") if initial_dir else "image_table.png"
    )
    out, _ = QFileDialog.getSaveFileName(ctx.window, "Export image table", initial,
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
        ctx.notify.set_status(f"Image table saved → {Path(out).name}")
    except Exception as exc:
        QMessageBox.critical(ctx.window, "Export failed", str(exc))


def image_table_copy_png(ctx) -> None:
    """Render the image table and place a PNG of it on the system clipboard."""
    import io
    from PySide6.QtGui import QGuiApplication, QImage

    fig, save_kwargs = _build_export_figure(ctx)
    if fig is None:
        return
    dpi = save_kwargs.pop("_dpi")
    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="png", dpi=dpi, **save_kwargs)
    except Exception as exc:
        QMessageBox.critical(ctx.window, "Copy failed", str(exc))
        return
    img = QImage.fromData(buf.getvalue(), "PNG")
    if img.isNull():
        QMessageBox.critical(ctx.window, "Copy failed", "Could not decode rendered PNG.")
        return
    QGuiApplication.clipboard().setImage(img)
    ctx.notify.set_status("Image table copied to clipboard (PNG).")


def image_table_copy_svg(ctx) -> None:
    """Render the image table and copy it to the clipboard, preferring vector.

    On macOS the figure is written natively to ``NSPasteboard`` so Illustrator
    pastes true editable vector; elsewhere a Qt clipboard payload is used (see
    ``copy_figure_to_clipboard``).
    """
    from well_viewer.features.export.style import copy_figure_to_clipboard

    fig, save_kwargs = _build_export_figure(ctx)
    if fig is None:
        return
    save_kwargs.pop("_dpi", None)
    save_kwargs.pop("format", None)

    status = copy_figure_to_clipboard(
        fig, svg_kwargs=save_kwargs, pdf_kwargs=save_kwargs,
    )
    if status.startswith("Copy failed"):
        QMessageBox.critical(ctx.window, "Copy failed", "Could not render the image table.")
        return
    ctx.notify.set_status("Image table " + status[0].lower() + status[1:])


def _downsample_dpi(orig_dpi: int, w_in: float, h_in: float,
                    *, max_px: int = 1600, floor: int = 60) -> int:
    """Pick a render dpi so the figure's longest side is ≤ *max_px* pixels,
    never above *orig_dpi* and never below *floor*."""
    longest = max(float(w_in), float(h_in), 0.1)
    return max(floor, min(int(orig_dpi), int(max_px / longest)))


def image_table_save_to_gallery(ctx) -> None:
    """Render the current image grid (downsampled) and save it to Saved Plots.

    Microscopy grids can be enormous at full export resolution, so the saved
    snapshot is capped to a modest longest-side pixel budget — enough to read
    the grid in the gallery / PDF report without bloating the per-dataset store.
    """
    import io

    # Pre-shrink each cell with brightness-preserving max-pooling so the small
    # saved snapshot keeps its contrast (plain dpi downscaling area-averages and
    # washes out sparse fluorescence).
    fig, save_kwargs = _build_export_figure(ctx, cell_max_px=320)
    if fig is None:
        ctx.notify.set_status("Image table is empty — nothing to save.")
        return
    orig_dpi = int(save_kwargs.pop("_dpi", 200) or 200)
    save_kwargs.pop("format", None)

    # Downsample: cap the longest side to ~1600 px.
    try:
        w_in, h_in = (float(v) for v in fig.get_size_inches())
        dpi_ds = _downsample_dpi(orig_dpi, w_in, h_in)
    except Exception:
        dpi_ds = min(orig_dpi, 110)

    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="png", dpi=dpi_ds, **save_kwargs)
    except Exception as exc:
        QMessageBox.critical(ctx.window, "Save failed", str(exc))
        return
    finally:
        try:
            import matplotlib.pyplot as _plt
            _plt.close(fig)
        except Exception:
            swallowed()  # figure cleanup is best-effort

    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    rows = len(cells)
    cols = len(cells[0]) if rows and cells[0] else 0
    label = f"Image grid ({rows}×{cols})" if rows and cols else "Image grid"

    from well_viewer.features.saved_plots.controller import save_plot_from_card
    status = save_plot_from_card(ctx, "Image Table", buf.getvalue(), label)
    ctx.notify.set_status(status)
