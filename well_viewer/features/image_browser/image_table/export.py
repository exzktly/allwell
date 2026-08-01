"""Image Table export: settings dialog, figure building, PNG/SVG save + copy.

Extracted from ``controller.py`` — the export actions and the matplotlib figure
builder. Pulls the per-cell pixels from the ``image_compose`` layer (downward
dependency), so the controller keeps event handling + orchestration.
``controller`` re-exports these names, so the toolbar wiring in ``tab_view`` is
unchanged.
"""

from __future__ import annotations

from pathlib import Path  # used by save dialogs
from typing import Any

from PySide6.QtWidgets import QFileDialog, QMessageBox

from well_viewer.core.safe import swallowed

from well_viewer.features.image_browser.image_table import facet_view as _facet
from well_viewer.features.image_browser.image_table import renderer as _renderer
from well_viewer.features.image_browser.image_table.image_compose import (
    OVERLAY_CHAN,
    _load_array,
    _row_display_opts,
    cell_chrome_kwargs,
    cell_display_rgb,
    draw_cell_chrome,
    mark_uncorrected,
    overlay_cell_rgb,
    reset_scan_misses,
    resolve_chrome_plan,
)
from well_viewer.features.image_browser.image_table.grid_layout import _axis_dim_values


# Save to Saved Plots quality presets: (cell_max_px, gallery_max_px).
# cell_max_px pre-shrinks each cell's array before compositing (bounds the
# memory a full-resolution grid would take during the build); gallery_max_px
# caps the whole assembled figure's longest side at savefig time (see
# _downsample_dpi). Both scale together per preset so raising quality doesn't
# raise the whole-figure pixel budget past what the per-cell source data can
# actually resolve, and so lowering it actually saves the compositing time,
# not just file size.
GALLERY_QUALITY_PRESETS: dict[str, tuple[int, int]] = {
    "draft": (280, 1400),
    "standard": (480, 2400),
    "high": (800, 4000),
}
GALLERY_QUALITY_LABELS: dict[str, str] = {
    "draft": "Draft (fastest)",
    "standard": "Standard",
    "high": "High (slower)",
}
DEFAULT_GALLERY_QUALITY = "standard"


def _export_opts(ctx) -> dict[str, Any]:
    """Return the image-table export option dict, seeding defaults on demand."""
    opts = getattr(ctx.state.image_table, "export_opts", None)
    if not isinstance(opts, dict):
        opts = {
            "pad_inches": 0.10,    # outer margin around the saved figure
            "cell_pad": 0.50,      # gap between adjacent grid slots
            # Off by default: the exported figure carries the same row/column axis
            # labels the grid shows, which identify every cell already.
            "show_titles": False,
            "title_fontsize": 7,
            "dpi": 300,
            "transparent_bg": True,
            # "Save to Saved Plots" resolution/speed trade-off -- see
            # GALLERY_QUALITY_PRESETS. Only the gallery-save path reads this;
            # Export to File / Copy PNG / Copy SVG always render full-res.
            "gallery_quality": DEFAULT_GALLERY_QUALITY,
        }
        ctx.state.image_table.export_opts = opts
    elif "gallery_quality" not in opts:
        opts["gallery_quality"] = DEFAULT_GALLERY_QUALITY  # older saved opts dict
    return opts


def gallery_quality_px(ctx) -> tuple[int, int]:
    """``(cell_max_px, gallery_max_px)`` for the current gallery-quality preset."""
    key = _export_opts(ctx).get("gallery_quality", DEFAULT_GALLERY_QUALITY)
    return GALLERY_QUALITY_PRESETS.get(key, GALLERY_QUALITY_PRESETS[DEFAULT_GALLERY_QUALITY])


def set_gallery_quality(ctx, key: str) -> None:
    """Set the "Save to Saved Plots" quality preset (see GALLERY_QUALITY_PRESETS)."""
    if key not in GALLERY_QUALITY_PRESETS:
        return
    _export_opts(ctx)["gallery_quality"] = key


def image_table_open_export_settings(ctx) -> None:
    """Pop a small modal dialog to edit the image-table export options."""
    from PySide6.QtWidgets import (
        QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
        QFormLayout, QSpinBox,
    )

    opts = _export_opts(ctx)
    dlg = QDialog(ctx.window)
    dlg.setWindowTitle("Image Table — Export settings")
    form = QFormLayout(dlg)

    quality_cb = QComboBox(dlg)
    quality_keys = list(GALLERY_QUALITY_PRESETS)
    for key in quality_keys:
        cell_px, gallery_px = GALLERY_QUALITY_PRESETS[key]
        quality_cb.addItem(f"{GALLERY_QUALITY_LABELS[key]} — {gallery_px}px", key)
    quality_cb.setCurrentIndex(quality_keys.index(
        opts.get("gallery_quality", DEFAULT_GALLERY_QUALITY)))
    quality_cb.setToolTip(
        "Resolution → speed trade-off for “Save to Saved Plots” only "
        "(Export to File / Copy PNG / Copy SVG always render full resolution)."
    )
    form.addRow("Save to Saved Plots quality:", quality_cb)

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
        "Gap between adjacent cells and label strips, as a fraction of the font size."
    )
    form.addRow("Cell gap:", cell_spin)

    titles_cb = QCheckBox(dlg)
    titles_cb.setChecked(bool(opts.get("show_titles", False)))
    titles_cb.setToolTip(
        "Repeat the well/channel/timepoint label above every image. The exported "
        "figure already carries the grid's row and column axis labels, so this is "
        "off unless you want the per-cell titles as well."
    )
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
        "gallery_quality": quality_cb.currentData(),
        "pad_inches": float(pad_spin.value()),
        "cell_pad": float(cell_spin.value()),
        "show_titles": bool(titles_cb.isChecked()),
        "title_fontsize": int(title_size_spin.value()),
        "dpi": int(dpi_spin.value()),
        "transparent_bg": bool(transparent_cb.isChecked()),
    })


def _area_pool_rgb(rgb, max_side: int):
    """Shrink an (H,W,3+) uint8 image so its longest side ≤ *max_side*, averaging
    each block.

    Area-averaging is what the live thumbnail already does — ``make_overlay_thumb``
    hands the RGB to Qt's smooth transform — so pooling the same way is what keeps a
    saved snapshot reading at the grid's brightness. Peak-preserving alternatives
    (taking each block's brightest pixel) dilate sparse fluorescence and make the
    saved image visibly brighter and denser than the table it came from.
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
    out = blocks.astype(_np.float32).mean(axis=2)
    return _np.clip(_np.rint(out), 0, 255).astype(a.dtype)


def _facet_plan_for_export(ctx, cells):
    """The facet plan the exported figure mirrors, or ``None`` for a plain grid.

    ``None`` when the plan can't be computed, in which case the figure falls back to
    a uniform grid — the axis labels are chrome, and losing them beats failing the
    whole export.
    """
    try:
        return _facet.plan_facets(ctx, cells)
    except Exception:
        swallowed()  # chrome is decoration; never block an export on it
        return None


def _export_grid(fig_factory, plan, n_rows: int, n_cols: int, *,
                 cell_w: float, cell_h: float, pad_in: float):
    """``(fig, gridspec, slot_of)`` for the export grid.

    *slot_of* maps a logical ``(row, col)`` to the gridspec key its cell axes
    occupies — the facet plan's placement when there is one (leaving room for the
    label strips and the inter-group gaps), the plain ``(row, col)`` otherwise.
    """
    if plan is None:
        fig = fig_factory((max(2.4, n_cols * cell_w), max(2.4, n_rows * cell_h)))
        total_w, total_h = n_cols * cell_w, n_rows * cell_h
        gs = fig.add_gridspec(
            n_rows, n_cols, left=0.0, right=1.0, bottom=0.0, top=1.0,
            wspace=(pad_in * n_cols / total_w) if total_w > 0 else 0.0,
            hspace=(pad_in * n_rows / total_h) if total_h > 0 else 0.0,
        )
        return fig, gs, (lambda r, c: (r, c))

    fig = fig_factory(_renderer.facet_figsize(
        plan, n_rows, n_cols, cell_w_in=cell_w, cell_h_in=cell_h, pad_in=pad_in))
    gs = _renderer.make_facet_gridspec(
        fig, plan, n_rows, n_cols,
        cell_w_in=cell_w, cell_h_in=cell_h, pad_in=pad_in)
    return fig, gs, (lambda r, c: (plan.grid_row_of[r], plan.grid_col_of[c]))


def _build_export_figure(ctx, *, cell_max_px: int | None = None, progress=None):
    """Render the current image-table contents to a matplotlib Figure.

    Returns ``(fig, save_kwargs)`` where *save_kwargs* are the format-agnostic
    keyword arguments to feed into ``fig.savefig`` (caller adds ``format`` and
    optionally ``dpi`` for raster outputs). Returns ``(None, None)`` when there
    is nothing to render (no cells, missing matplotlib/numpy).

    ``cell_max_px`` (gallery-save path only) pre-shrinks each cell to that
    longest-side pixel budget (see :func:`_area_pool_rgb`), bounding the memory a
    96-cell plate of full-resolution RGB would otherwise take. ``None`` (file export
    / clipboard) renders full-res.

    ``progress``, if given, is called as ``progress(done, total)`` after each
    cell is loaded/rendered/drawn — the per-cell loop below is the actual cost
    (disk reads + colormap/LUT application per cell), so this is the only point
    fine-grained progress is available. A caller uses it to drive a
    ``QProgressDialog`` and pump the event loop, since this whole function runs
    synchronously on the GUI thread.

    Placement and labelling mirror the live grid: the same ``plan_facets`` output
    that lays out the Qt widgets drives the gridspec here, and ``renderer.py`` draws
    the plan's axis labels, group strips and corner label onto the figure.
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
    show_titles = bool(opts.get("show_titles", False))
    title_fontsize = int(opts.get("title_fontsize", 7))
    dpi = int(opts.get("dpi", 300))
    transparent_bg = bool(opts.get("transparent_bg", True))

    # Shape comes from the cells themselves, not ``state.rows`` / ``state.cols``:
    # the facet plan indexes off ``cells``, so a stale state value would place a
    # cell outside the grid it was planned for.
    rows = len(cells)
    cols = max((len(row) for row in cells), default=0)
    # Overlay: the channel axis has collapsed to the OVERLAY_CHAN sentinel and each
    # such cell blends every selected channel (matching the live grid). Resolve the
    # channel set once, off the collapse.
    overlay_on = bool(getattr(ctx.state.image_table, "composite", False))
    overlay_chans = _axis_dim_values(ctx, "channel", collapse_overlay=False) if overlay_on else []

    # Share the SAME persistent cache image_table_generate writes into (not a
    # throwaway copy) so a well/channel this export scans for the first time
    # is available to a later Generate too, and vice versa -- a save run
    # right after a Generate should not re-scan or re-decode anything.
    cache = ctx.state.image_table.image_cache
    if not isinstance(cache, dict):
        cache = {}
        ctx.state.image_table.image_cache = cache
    # Empty scans are suppressed for this pass only, so an export that runs while
    # the source drive is stalled doesn't write those wells off for the session.
    reset_scan_misses(ctx)
    # Warm every distinct cell's image in parallel first (same helper
    # image_table_generate's prefetch pass uses) -- otherwise an export run
    # without a prior Generate decodes every cell sequentially below, on the
    # GUI thread, one at a time.
    from well_viewer.features.image_browser.image_table.controller import (
        _prefetch_cell_arrays,
    )
    _prefetch_cell_arrays(ctx, cells, cache)
    rendered = getattr(ctx.state.image_table, "last_render", None) or {}
    crop_tool = getattr(ctx.runtime.image_table, "crop_tool", None)
    # Scale bar + identity labels are burned into the same RGB the live grid uses,
    # so the export matches the screen (single-source parity).
    chrome_plan = resolve_chrome_plan(ctx, cells)
    # Display toggles (top-hat / boundaries / binary) + LUT are per ROW —
    # mirror the live thumbnail, reading each from the cell's row box so the
    # export matches what's on screen.

    bg_face = "none" if transparent_bg else "white"
    # Probe the first available cell to learn the image aspect ratio. With
    # aspect="equal" axes, a non-square image shrinks the axes box inside
    # its gridspec cell, which makes the visible inter-row gap differ from
    # the inter-column gap. Sizing the cell to the actual image aspect
    # keeps the axes filling the cell so row/column spacing match.
    img_h: int | None = None
    img_w: int | None = None
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

    def _make_figure(size):
        f = Figure(figsize=size, dpi=dpi, facecolor=bg_face)
        f.patch.set_alpha(0.0 if transparent_bg else 1.0)
        return f

    pad_in = _renderer.cell_pad_inches(cell_pad)
    plan = _facet_plan_for_export(ctx, cells)
    fig, gs, slot_of = _export_grid(_make_figure, plan, rows, cols,
                                    cell_w=cell_w, cell_h=cell_h, pad_in=pad_in)

    total_cells = sum(len(row) for row in cells)
    done_cells = 0
    for r, row in enumerate(cells):
        for c, cell in enumerate(row):
            ax = fig.add_subplot(gs[slot_of(r, c)])
            ax.set_facecolor("none" if transparent_bg else "white")
            well = cell["well"]
            chan = cell["chan"]
            tp = cell["tp"]
            fov = cell["fov"]
            is_overlay = chan == OVERLAY_CHAN

            if is_overlay:
                # Blend every selected channel into one image — the same path the
                # live grid uses (overlay_cell_rgb), so export matches the screen.
                rgb = overlay_cell_rgb(ctx, cache, cell, r, crop_tool, overlay_chans) \
                    if (well and tp) else None
            else:
                arr = rendered.get((r, c))
                if arr is None and well and chan and tp:
                    use_tophat = _row_display_opts(ctx, r, chan)[0]
                    arr = _load_array(ctx, cache, well, chan, tp, fov, use_tophat=use_tophat)
                # Render exactly the same RGB the live thumbnail shows —
                # full mpl colormap + show_binary / show_boundaries toggles +
                # crop — via the shared single-source helper, so anything that
                # changes the live look auto-propagates to the export.
                rgb = cell_display_rgb(ctx, cache, cell, r, crop_tool, arr) \
                    if arr is not None else None

            # Burn the scale bar / label into the source-res RGB, before any
            # gallery downscale, so the bar and text scale with the pixels.
            if rgb is not None:
                ck = cell_chrome_kwargs(ctx, r, c, cell, chrome_plan, overlay_chans)
                if ck:
                    rgb = draw_cell_chrome(rgb, **ck)
                # A figure that dropped the uncorrected mark would leave the app
                # carrying exactly the ambiguity the live grid no longer has.
                rgb = mark_uncorrected(ctx, cell, rgb)

            if rgb is not None:
                if cell_max_px:
                    rgb = _area_pool_rgb(rgb, cell_max_px)
                ax.imshow(rgb, aspect="equal")
            else:
                ax.text(
                    0.5, 0.5, "(no image)",
                    ha="center", va="center", transform=ax.transAxes, fontsize=7,
                )

            if show_titles:
                if is_overlay and well and tp:
                    from well_viewer.domain.selections_model import well_display_label
                    chan_lbl = "+".join(c2.upper() for c2 in overlay_chans) or "overlay"
                    title = f"{well_display_label(ctx, well)}  {chan_lbl}  T:{tp}"
                    if fov:
                        title += f"  FOV:{fov}"
                elif well and chan and tp:
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

            done_cells += 1
            if progress is not None:
                progress(done_cells, total_cells)

    if plan is not None:
        _renderer.draw_facet_labels(fig, gs, plan)

    save_kwargs: dict[str, Any] = dict(
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
                    *, max_px: int = 2400, floor: int = 60) -> int:
    """Pick a render dpi so the figure's longest side is ≤ *max_px* pixels,
    never above *orig_dpi* and never below *floor*."""
    longest = max(float(w_in), float(h_in), 0.1)
    return max(floor, min(int(orig_dpi), int(max_px / longest)))


def image_table_save_to_gallery(ctx) -> None:
    """Render the current image grid (downsampled) and save it to Saved Plots.

    Microscopy grids can be enormous at full export resolution, so the saved
    snapshot is capped to a longest-side pixel budget — enough to read the
    grid in the gallery / PDF report without bloating the per-dataset store.
    The exact budget is user-tunable (``GALLERY_QUALITY_PRESETS`` via
    ``gallery_quality_px`` — Draft/Standard/High, set from the Export ▾ menu's
    "Save quality" submenu or the "Export settings…" dialog), since a large
    plate walk at the default preset can still be slow enough to want faster
    (or, for a figure headed straight to a report, sharper) than the default.

    This whole render is synchronous on the GUI thread (per-cell disk reads +
    colormap/LUT application, then a final savefig pass), so a large grid can
    take long enough to look hung. Shows a modal progress dialog driven by
    ``_build_export_figure``'s per-cell callback and pumps the event loop after
    each update, the same pattern ``load_controller._make_opening_dialog`` uses
    for the equally-blocking dataset-open path.
    """
    import io

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QProgressDialog

    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    total_cells = sum(len(row) for row in cells)

    dlg = None
    if total_cells:
        try:
            dlg = QProgressDialog("Rendering image table…", None, 0, total_cells, ctx.window)
            dlg.setWindowTitle("Save to Saved Plots")
            dlg.setWindowModality(Qt.WindowModal)
            dlg.setCancelButton(None)
            dlg.setMinimumDuration(0)
            dlg.setAutoClose(False)
            dlg.setAutoReset(False)
            dlg.setValue(0)
            dlg.show()
            QApplication.processEvents()
        except Exception:
            swallowed()  # never let the progress UI block the save itself
            dlg = None

    def _report(done: int, total: int) -> None:
        if dlg is None:
            return
        try:
            dlg.setLabelText(f"Rendering image table… ({done}/{total})")
            dlg.setValue(done)
            QApplication.processEvents()
        except Exception:
            swallowed()

    cell_max_px, gallery_max_px = gallery_quality_px(ctx)
    try:
        # Pre-shrink each cell to bound the memory a full-resolution grid would
        # take. The pooling averages each block, exactly as the live thumbnail's
        # smooth scale does, so the saved snapshot reads at the same brightness
        # as the grid. Both this cap and the final downsample below come from
        # the same user-tunable quality preset (Export ▾ → Save quality, or
        # Export settings…) so raising/lowering it actually moves the speed/
        # detail trade-off instead of just one half of it.
        fig, save_kwargs = _build_export_figure(ctx, cell_max_px=cell_max_px, progress=_report)
        if fig is None:
            ctx.notify.set_status("Image table is empty — nothing to save.")
            return

        if dlg is not None:
            dlg.setLabelText("Saving to gallery…")
            QApplication.processEvents()

        orig_dpi = int(save_kwargs.pop("_dpi", 200) or 200)
        save_kwargs.pop("format", None)

        try:
            w_in, h_in = (float(v) for v in fig.get_size_inches())
            dpi_ds = _downsample_dpi(orig_dpi, w_in, h_in, max_px=gallery_max_px)
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
    finally:
        if dlg is not None:
            try:
                dlg.close()
                dlg.deleteLater()
            except Exception:
                swallowed()
