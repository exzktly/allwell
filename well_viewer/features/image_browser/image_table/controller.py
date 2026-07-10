"""Image Table tab — non-UI controller logic.

These functions are bound onto the runtime ctx (via ``runtime_app.py``)
and drive the Image Table tab:

- multi-select sidebar picker (ignores groupings / replicate sets)
- selector grid build / rebuild (rows × cols of dropdowns per cell)
- "Distribute Wells" — assigns active wells row-wise into cells
- Per-row channel + LUT-color selectors (column 0 of the selector grid)
- Global Options propagation (timepoint / FOV → every cell)
- Generate — loads images and lays them out below the selector
- Per-channel auto-LUT
- Export — matplotlib figure with labels above each image, transparent BG
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel, QVBoxLayout, QWidget,
)
from well_viewer.core.safe import swallowed
from well_viewer.core.ui_helpers import set_combo_values
from well_viewer.domain.viewer_state import parse_rc
from well_viewer.core.channel_state_controller import set_thresh_frac_on


# ── LUT colour palette ──────────────────────────────────────────────────────
#
# Per-row tints applied to the grayscale fluorescence image. ``Gray`` keeps
# the legacy white intensity ramp. Each tint is an (r, g, b) triple in
# [0, 1]; ``Gray`` uses ``None`` so make_fluor_thumb takes its untinted
# fast path.
# The image-composition layer (LUT palette, tint/cmap resolution, array
# loading, mask/threshold rendering, RGB composer, overlay) lives in
# ``image_compose``. Re-exported so call sites + external importers
# (playground, grid_view) are unchanged.
from well_viewer.features.image_browser.image_table.image_compose import (  # noqa: E402,F401
    LUT_COLORS,
    LUT_COLOR_NAMES,
    NUC_SEG_TOKEN,
    _compose_image_table_rgb,
    _export_binary_mask,
    _format_pixel_intensity,
    _is_nuc_seg,
    _load_array,
    _load_mask_array,
    _load_ratio_array,
    _load_well_channel,
    _norm_token,
    _numeric_token,
    _parse_lut,
    _row_channel,
    _row_display_opts,
    _render_mask_binary,
    _render_threshold_binary,
    _render_with_boundary_overlay,
    _row_cmap_name,
    _row_log_scale,
    _tint_from_colormap,
    cell_display_rgb,
    composite_additive_rgb,
    composite_column_rgb,
)
# Faceted render placement (axis labels + group spacing) — same-feature sibling,
# module-scope like the grid_layout import above so it isn't a deferred import.
from well_viewer.features.image_browser.image_table import facet_view as _facet  # noqa: E402




# ── Virtual "Nuc+Seg" channel ───────────────────────────────────────────────
#
# The pipeline pre-renders a per-(FOV, timepoint) overlay PNG that paints
# the segmentation mask outline on top of the nuclear channel. Exposing it
# as a virtual channel here lets the Image Table double as the old Movie
# Montage view: pick "NUC+SEG" in the channel column and Generate loads
# those overlay images instead of a raw fluorescence channel.


# ── Sidebar picker ───────────────────────────────────────────────────────────


def image_table_refresh_picker(ctx) -> None:
    """Push the active-well set onto the sidebar plate and update the count label."""
    plate = ctx.runtime.image_table.sidebar_plate
    if plate is not None:
        plate.setEnabledWells(list(ctx.state.dataset.well_paths.keys()))
        active = sorted(
            (w for w in ctx.state.image_table.active_wells if w in ctx.state.dataset.well_paths),
            key=parse_rc,
        )
        # Tint each well its canonical plate-rank colour so the picker matches
        # the main sidebar / plots ("the plate is the legend") instead of the
        # widget's selection-order fallback palette.
        from well_viewer.domain.selections_model import color_for_well
        plate.clearWellColors()
        plate.setWellColors({tok: color_for_well(tok) for tok in active})
        plate.setSelectedWellIds(active)
    lbl = getattr(ctx.runtime.image_table, "count_lbl", None)
    if lbl is not None:
        n = len(ctx.state.image_table.active_wells)
        lbl.setText(f"{n} well{'s' if n != 1 else ''} active")


def image_table_select_all(ctx) -> None:
    ctx.state.image_table.active_wells = set(ctx.state.dataset.well_paths.keys())
    image_table_refresh_picker(ctx)


def image_table_clear_active(ctx) -> None:
    ctx.state.image_table.active_wells = set()
    image_table_refresh_picker(ctx)


# ── Pipeline-info-driven dropdown options ────────────────────────────────────


# The layout/dimension layer (axis math, per-dim option lists, channel
# enumeration, cell snapshot) lives in ``grid_layout``. Re-exported so call
# sites + external importers (playground) are unchanged.
from well_viewer.features.image_browser.image_table.grid_layout import (  # noqa: E402,F401
    _DIM_KEY,
    _all_dim_options,
    _axis_values,
    _cache_column_tokens,
    _channel_options,
    _current_axis,
    _current_dims,
    _find_ratio_for_channel,
    _fixed_off_axis,
    _fov_options,
    _layout_cell_values,
    _layout_cell_values_nested,
    _product_values,
    _snapshot_cells,
    _timepoint_options,
    _tp_sort_key,
    _well_options,
    _write_cell,
)




















def image_table_repopulate_dropdowns(ctx) -> None:
    """Refresh the option lists that are still widget-backed.

    Called when pipeline info / wells become available after the tab is already
    built. The per-cell render model is headless plain data now (filled by the
    layout math), so the only combo left to repopulate is the action strip's
    ON-threshold channel picker.
    """
    chan_opts = _channel_options(ctx)

    # ON-threshold channel picker (action strip) — real fluor channels only.
    thresh_cb = ctx.widgets.get_or_none("_image_table_thresh_chan_cb")
    if thresh_cb is not None:
        set_combo_values(thresh_cb, [c for c in chan_opts if " (binary)" not in c])


# ── Per-cell render model build / rebuild ────────────────────────────────────


def image_table_rebuild_grid(ctx) -> None:
    """Rebuild the headless per-cell render model — a ``rows × cols`` grid of
    plain ``{"well", "chan", "tp", "fov"}`` value dicts.

    ``ctx.runtime.image_table.cells`` is the render contract that
    :func:`image_table_generate` reads; it holds no Qt widgets. The drag-and-drop
    shelf is the authoring surface — the layout math (``build_layout``) fills
    these dicts, and a right-click on a rendered cell overrides one of them."""
    rows = int(getattr(ctx.state.image_table, "rows", 2) or 2)
    cols = int(getattr(ctx.state.image_table, "cols", 3) or 3)
    ctx.runtime.image_table.cells = [
        [{"well": "", "chan": "", "tp": "", "fov": ""} for _c in range(cols)]
        for _r in range(rows)
    ]












# ── Smart layout builder ──────────────────────────────────────────────────────
#
# The flat + nested grid assembly (auto-size the grid to the chosen row/col axes
# and fill every cell, holding off-axis dims fixed) lives in ``build_layout.py``.
# Re-exported here so call sites (tab_view, view_state restore, presets) and the
# playground importer resolve the same names unchanged.
from well_viewer.features.image_browser.image_table.build_layout import (  # noqa: E402,F401
    _AXIS_LABEL,
    _AXIS_MAX,
    _ROW_MAX,
    _build_nested_layout,
    _refresh_dims_pickers,
    image_table_apply_dims_preset,
    image_table_apply_preset,
    image_table_apply_spec,
    image_table_build_layout,
)


# ── Image loading + caching ──────────────────────────────────────────────────












_ZOOM_MIN = 0.33
_ZOOM_MAX = 4.0


def image_table_set_zoom(ctx, factor: float, *, relative: bool = False) -> None:
    """Set (or step) the rendered-table zoom factor and re-render.

    ``relative=True`` multiplies the current zoom by *factor* (zoom in/out
    buttons); otherwise *factor* is the absolute level (1.0 = 240 px cells).
    """
    cur = float(getattr(ctx.state.image_table, "zoom", 1.0) or 1.0)
    new = cur * factor if relative else factor
    new = max(_ZOOM_MIN, min(_ZOOM_MAX, new))
    ctx.state.image_table.zoom = new
    lbl = getattr(ctx.runtime.image_table, "zoom_lbl", None)
    if lbl is not None:
        lbl.setText(f"{new * 100:.0f}%")
    # Only re-render if a table has already been generated; otherwise just
    # store the level for the next Generate.
    if getattr(ctx.state.image_table, "last_render", None):
        image_table_generate(ctx)


def image_table_set_spacing(ctx, key: str, value) -> None:
    """Set a rendered-table display property in px and re-render if a table
    exists: the spacing gaps ``cell_gap`` / ``row_group_gap`` / ``col_group_gap``
    (min 0) or the label font sizes ``label_font_pt`` / ``group_font_pt`` (min 6)."""
    if key not in ("cell_gap", "row_group_gap", "col_group_gap",
                   "label_font_pt", "group_font_pt"):
        return
    lo = 6 if key.endswith("_pt") else 0
    setattr(ctx.state.image_table, key, max(lo, int(round(float(value)))))
    # Re-render whenever a table has been built (cells assembled) — not only after
    # a first Generate — so the very first Properties tweak takes effect live.
    if getattr(ctx.runtime.image_table, "cells", None):
        image_table_generate(ctx)


def image_table_set_thresh_frac(ctx, value, channel=None) -> None:
    """Edit a channel's ON/OFF intensity threshold (ThreshFracOn) from the Image
    Table controls row, then re-composite so its "≥ Thr" binary overlay reflects
    it. ``channel`` picks which channel (from the ON-threshold dropdown); ``None``
    falls back to the active channel.

    Writes through ``set_thresh_frac_on`` (the single source of truth shared with
    the Cell Gating tab and the other quick-edits). Re-renders only when a table
    has already been generated — the cached images are reused, so it's the same
    cheap re-composite the zoom control does; before the first Generate it just
    stores the value.
    """
    set_thresh_frac_on(ctx, value, channel)
    if getattr(ctx.state.image_table, "last_render", None):
        image_table_generate(ctx)


# ── Per-cell mouse handling: hover pixel readout + crop drag ────────────────




def _install_cell_events(
    ctx, label, crop_tool, well: str, chan: str, tp: str, fov: str,
) -> None:
    """Wire press/move/release on a cell's image label.

    Replaces ``CropTool.install_events`` so the same label can both serve
    as the crop-tool's drag target and report the intensity of the pixel
    under the cursor on every move.
    """
    from PySide6.QtCore import QObject, QEvent
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtWidgets import QToolTip

    label.setMouseTracking(True)
    if crop_tool is not None:
        label.setCursor(_Qt.CrossCursor)

    def _show_tip(_lbl, ev, text: str) -> None:
        if not text:
            QToolTip.hideText()
            return
        try:
            gp = ev.globalPosition().toPoint()
        except Exception:
            try:
                gp = _lbl.mapToGlobal(ev.position().toPoint())
            except Exception:
                QToolTip.hideText()
                return
        QToolTip.showText(gp, text, _lbl)

    def _label_to_image_xy(_lbl, lx: int, ly: int):
        """Map label-local coords to source-image (x, y). Returns None when
        cursor is outside the rendered pixmap area."""
        try:
            import numpy as _np
        except Exception:
            return None
        pm = _lbl.pixmap()
        arr = getattr(_lbl, "_raw_arr", None)
        if arr is None or pm is None:
            return None
        if hasattr(pm, "isNull") and pm.isNull():
            return None
        pw, ph = pm.width(), pm.height()
        if pw <= 0 or ph <= 0:
            return None
        a = _np.asarray(arr)
        full_h, full_w = a.shape[:2]
        crop = getattr(_lbl, "_crop", None)
        if crop is not None:
            y0, x0, y1, x1 = crop
            view_h = max(1, int(y1) - int(y0))
            view_w = max(1, int(x1) - int(x0))
        else:
            y0 = x0 = 0
            view_h, view_w = full_h, full_w
        lw, lh = _lbl.width(), _lbl.height()
        offset_x = (lw - pw) // 2
        offset_y = (lh - ph) // 2
        if not (offset_x <= lx < offset_x + pw and offset_y <= ly < offset_y + ph):
            return None
        px = lx - offset_x
        py = ly - offset_y
        img_x = int(x0 + px * view_w / pw)
        img_y = int(y0 + py * view_h / ph)
        img_x = max(0, min(full_w - 1, img_x))
        img_y = max(0, min(full_h - 1, img_y))
        return (img_x, img_y)

    def _update_hover(_lbl, ev) -> None:
        try:
            arr = getattr(_lbl, "_raw_arr", None)
            if arr is None:
                _show_tip(_lbl, ev, "")
                return
            pos = ev.position()
            xy = _label_to_image_xy(_lbl, int(pos.x()), int(pos.y()))
            if xy is None:
                _show_tip(_lbl, ev, "")
                return
            x, y = xy
            intensity = _format_pixel_intensity(arr, y, x)
            if not intensity:
                _show_tip(_lbl, ev, "")
                return
            prefix = f"{well} {chan.upper()} T:{tp}"
            if fov:
                prefix += f" FOV:{fov}"
            _show_tip(_lbl, ev, f"{prefix}  ({x}, {y}) = {intensity}")
        except Exception:
            QToolTip.hideText()

    class _CellEventFilter(QObject):
        def eventFilter(self, obj, ev):
            t = ev.type()
            if t == QEvent.MouseMove:
                _update_hover(obj, ev)
                if crop_tool is not None:
                    crop_tool.update_drag(ev)
            elif t == QEvent.MouseButtonPress:
                if crop_tool is not None:
                    crop_tool.begin_drag(obj, ev)
            elif t == QEvent.MouseButtonRelease:
                if crop_tool is not None:
                    crop_tool.end_drag(ev)
            elif t == QEvent.Leave:
                QToolTip.hideText()
            return False

    filt = _CellEventFilter(label)
    label.installEventFilter(filt)
    # Keep a reference so the filter isn't garbage-collected.
    label._image_table_event_filter = filt  # type: ignore[attr-defined]


# ── Binary render helpers (mask vs. above-threshold) ─────────────────────────






# ── Generate (render image table below the selector) ─────────────────────────


def _prefetch_cell_arrays(ctx, cells, cache) -> None:
    """Decode the grid's unique (well, channel, variant) images in parallel.

    The first Generate after a dataset load (or after the cache is invalidated)
    has to read every cell's image off disk; doing that sequentially on the GUI
    thread is the slow part. One ``_load_array`` call loads (and caches) all
    FOVs/timepoints for a well+channel, so the unique work is far smaller than
    the cell count. Threads help because ``zipfile``/``tifffile``/``PIL`` release
    the GIL for the C-level inflate+decode, and the per-zip-handle serialization
    in ``data/zipfile_cache.py`` keeps concurrent reads safe (same well → one
    handle, serialized; different wells → parallel). Ratio channels are skipped
    (they compose from per-channel arrays, which this still warms). Best-effort:
    any failure leaves the sequential in-loop load as the fallback.
    """
    todo: dict = {}
    for r, row in enumerate(cells):
        for cell in row:
            well = cell["well"]
            chan = cell["chan"]
            tp = cell["tp"]
            fov = cell["fov"]
            if not (well and chan and tp):
                continue
            if _find_ratio_for_channel(ctx, chan) is not None:
                continue
            # raw vs corrected is a per-channel render option (channel_opts).
            use_tophat = _row_display_opts(ctx, r, chan)[0]
            th = bool(use_tophat) and not _is_nuc_seg(chan)
            variant = "tophat" if th else "raw"
            key = (well, chan.lower(), variant)
            if key in cache:
                continue
            todo.setdefault(key, (well, chan, tp, fov, th))
    if len(todo) < 2:
        return  # nothing to gain from a pool for 0–1 reads
    import os
    from concurrent.futures import ThreadPoolExecutor

    def _warm(spec) -> None:
        well, chan, tp, fov, th = spec
        try:
            _load_array(ctx, cache, well, chan, tp, fov, use_tophat=th)
        except Exception:
            swallowed()  # in-loop load (with its own handling) is the fallback

    try:
        with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 4))) as ex:
            list(ex.map(_warm, list(todo.values())))
    except Exception:
        import logging as _logging
        _logging.getLogger("well_viewer.image_table").debug(
            "image_table: parallel prefetch failed", exc_info=True)


def _cell_override_menu(ctx, r: int, c: int, global_pos) -> None:
    """Per-cell override popover (delegates to grid_ops; lazy to avoid a cycle)."""
    from well_viewer.features.image_browser.image_table.grid_ops import (
        image_table_cell_override_menu,
    )
    image_table_cell_override_menu(ctx, r, c, global_pos)


def image_table_generate(ctx) -> None:
    """Load images for every cell and lay out the rendered image table."""
    from well_viewer.core.ui_helpers import make_overlay_thumb
    from well_viewer.core.ui_helpers import clear_layout

    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    grid = ctx.runtime.image_table.render_grid
    clear_layout(grid)
    # Reset any spacing left by a prior layout (row/col minimums are sticky on a
    # QGridLayout even after its items are cleared), then apply the cell gap.
    for _r in range(grid.rowCount()):
        grid.setRowMinimumHeight(_r, 0)
    for _c in range(grid.columnCount()):
        grid.setColumnMinimumWidth(_c, 0)
    grid.setSpacing(max(0, int(getattr(ctx.state.image_table, "cell_gap", 2))))
    if not cells:
        ctx.set_status("Image Table: build a grid first (set rows/cols and click Apply).")
        return

    # Once the user has triggered a Generate, hide the EmptyState placeholder.
    empty = getattr(ctx.runtime.image_table, "empty_state", None)
    if empty is not None:
        empty.setVisible(False)

    # Persist the decoded-image cache across Generates so crop / zoom / LUT /
    # threshold re-renders re-composite from memory instead of re-reading and
    # re-decoding every cell from its zip. Invalidated on dataset load
    # (load_controller) and keyed by variant, so Raw/Corrected stays correct.
    cache = ctx.state.image_table.image_cache
    if not isinstance(cache, dict):
        cache = {}
        ctx.state.image_table.image_cache = cache
    rendered: Dict[Tuple[int, int], Any] = {}
    crop_tool = getattr(ctx.runtime.image_table, "crop_tool", None)
    # Per-cell thumbnail size, scaled by the zoom factor (240 px = 1.0×).
    zoom = float(getattr(ctx.state.image_table, "zoom", 1.0) or 1.0)
    sz = max(80, int(round(240 * zoom)))

    _prefetch_cell_arrays(ctx, cells, cache)

    # Faceted placement: group cells by the builder's nesting, label the row/col
    # axes once, and space groups apart (falls back to a plain grid when absent).
    plan = _facet.plan_facets(ctx, cells)

    def _pos(r, c):
        if plan is None:
            return r, c
        return plan.grid_row_of[r], plan.grid_col_of[c]

    for r, row in enumerate(cells):
        for c, cell in enumerate(row):
            well = cell["well"]
            chan = cell["chan"]
            tp = cell["tp"]
            fov = cell["fov"]

            # Which base image to load (raw vs corrected) is resolved PER CHANNEL
            # (its render option), so raw/corrected can differ per cell — e.g.
            # channels nested on the columns axis. Falls back to the per-row
            # widget for legacy states via _row_display_opts.
            use_tophat = _row_display_opts(ctx, r, chan)[0]
            arr = None
            ratio = _find_ratio_for_channel(ctx, chan) if chan else None
            if well and chan and tp:
                if ratio is not None:
                    arr = _load_ratio_array(ctx, cache, well, ratio, tp, fov, use_tophat=use_tophat)
                else:
                    arr = _load_array(ctx, cache, well, chan, tp, fov, use_tophat=use_tophat)
            rendered[(r, c)] = arr

            cell_widget = QWidget()
            cl = QVBoxLayout(cell_widget)
            cl.setContentsMargins(2, 2, 2, 2)
            cl.setSpacing(2)

            # Identity now lives on the row/col axes (see facet_view), so the
            # cell carries no repeated title — just a tooltip for the exact combo.
            if well and chan and tp:
                title = f"{well}  {chan.upper()}  T:{tp}" + (f"  FOV:{fov}" if fov else "")
            else:
                title = "(unset)"

            img_label = QLabel(cell_widget)
            img_label.setToolTip(title)
            img_label.setAlignment(Qt.AlignCenter)
            # Pin to the rendered pixmap size so the surrounding QLabel doesn't
            # expand and leave large gaps around the image.
            img_label.setFixedSize(sz, sz)
            if arr is not None:
                # One composer for the live grid, the composite row and export:
                # cell_display_rgb resolves the channel's render / boundary / LUT
                # / colormap (from channel_opts, or the per-row widgets for legacy
                # states) so live and exported thumbnails always agree. A
                # pre-rendered RGB that isn't NUC+SEG goes straight to the overlay
                # thumbnailer (cell_display_rgb only treats NUC+SEG as overlay).
                if getattr(arr, "ndim", 2) >= 3 and not _is_nuc_seg(chan):
                    cropped = crop_tool.apply_to_array(arr) if crop_tool else arr
                    lo, hi = _parse_lut(ctx, r, chan)
                    pix = make_overlay_thumb(cropped, sz, sz, lo, hi)
                else:
                    rgb = cell_display_rgb(ctx, cache, cell, r, crop_tool, arr)
                    pix = make_overlay_thumb(rgb, sz, sz) if rgb is not None else None
                # CropTool needs the FULL source array + the active crop on
                # the label so label-pixel → image-pixel coord conversion
                # works correctly even when the label already shows a crop.
                img_label._raw_arr = arr  # type: ignore[attr-defined]
                img_label._crop = crop_tool.crop if crop_tool else None  # type: ignore[attr-defined]
                _install_cell_events(ctx, img_label, crop_tool, well, chan, tp, fov)
                # Right-click a rendered cell → one-off per-cell override popover
                # (writes the cell combos the renderer reads, then re-renders).
                img_label.setContextMenuPolicy(Qt.CustomContextMenu)
                img_label.customContextMenuRequested.connect(
                    lambda pos, rr=r, cc=c, _l=img_label:
                    _cell_override_menu(ctx, rr, cc, _l.mapToGlobal(pos))
                )
                if pix is not None:
                    img_label.setPixmap(pix)
                else:
                    img_label.setText("(render failed)")
            else:
                img_label.setText("(no image)")
            cl.addWidget(img_label)

            gr, gc = _pos(r, c)
            grid.addWidget(cell_widget, gr, gc)

    # Axis labels + inter-group spacing that mirror the builder's nesting.
    if plan is not None:
        _facet.render_facet_chrome(grid, plan)

    # Composite row: append one extra bottom row whose cells additively merge
    # the LUT-colored RGB of every row above them in the same column.
    if getattr(ctx.state.image_table, "composite", False) and cells:
        _facet.append_composite_row(ctx, cells, grid, cache, crop_tool, sz, rendered, plan)

    ctx.state.image_table.last_render = rendered
    ctx.state.image_table.image_cache = cache
    # Fill the per-channel LUT min/max boxes with the actual pooled numbers so
    # the user sees real values instead of "auto" (skips manually-edited rows).
    n = sum(1 for v in rendered.values() if v is not None)
    ctx.set_status(f"Image Table: generated {n} of {len(rendered)} cell(s).")
    if n == 0 and rendered:
        # Zero hits across the whole grid means key drift somewhere — log
        # both the (tp, fov) values the user picked and a sample of the
        # available cache keys for the first cached well so the format
        # mismatch is visible.
        try:
            import logging as _logging
            _log = _logging.getLogger("well_viewer.image_table")
            requested = []
            for r, row in enumerate(cells):
                for c, cell in enumerate(row):
                    requested.append((
                        r, c,
                        cell["well"],
                        cell["chan"],
                        cell["tp"],
                        cell["fov"],
                    ))
            for key, fluor in cache.items():
                sample_keys = list(fluor.keys())[:8]
                _log.warning(
                    "Image Table generated 0/%d; well=%r channel=%r "
                    "requested cells (r, c, well, chan, tp, fov)=%r "
                    "available keys (sample, up to 8): %r",
                    len(rendered), key[0], key[1], requested[:4], sample_keys,
                )
                break
        except Exception:
            swallowed()  # was: pass (silent except)


# ── Export (transparent background, labels above each image) ─────────────────


# The export cluster (settings dialog, figure builder, PNG/SVG save+copy)
# lives in ``export``. Re-exported so the tab_view toolbar wiring is unchanged.
from well_viewer.features.image_browser.image_table.export import (  # noqa: E402,F401
    _export_opts,
    image_table_open_export_settings,
    _build_export_figure,
    image_table_export,
    image_table_copy_png,
    image_table_copy_svg,
    image_table_save_to_gallery,
)




# ── Export (transparent background, labels above each image) ─────────────────








