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

# Names used only in annotations. Under ``from __future__ import
# annotations`` these are never evaluated, so importing them for real would
# add startup cost and, for several of these, an import cycle.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

import logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel, QVBoxLayout, QWidget,
)
from well_viewer.core.safe import swallow, swallowed
from well_viewer.core.ui_helpers import set_combo_values
from well_viewer.domain.viewer_state import parse_rc
from well_viewer.core.channel_state_controller import (
    refresh_thresh_frac_quick_edit,
    set_on_cutoff,
)


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
from well_viewer.features.image_browser.image_table.image_compose import (  # noqa: F401
    LUT_COLORS,
    LUT_COLOR_NAMES,
    NUC_SEG_TOKEN,
    OVERLAY_CHAN,
    _binary_base,
    _is_binary_variant,
    _is_mask_variant,
    _compose_image_table_rgb,
    _export_binary_mask,
    _format_pixel_intensity,
    _is_nuc_seg,
    _load_array,
    _load_mask_array,
    _load_ratio_array,
    _load_well_channel,
    _parse_lut,
    _row_channel,
    _row_display_opts,
    _render_with_boundary_overlay,
    _row_cmap_name,
    _row_log_scale,
    _tint_from_colormap,
    cell_display_rgb,
    cell_identity_label,
    cell_chrome_kwargs,
    composite_additive_rgb,
    composite_column_rgb,
    draw_cell_chrome,
    is_raw_fallback,
    mark_uncorrected,
    overlay_cell_rgb,
    reset_scan_misses,
    resolve_chrome_plan,
    UNCORRECTED_TOOLTIP,
)
# Faceted render placement (axis labels + group spacing) — same-feature sibling,
# module-scope like the grid_layout import above so it isn't a deferred import.
from well_viewer.features.image_browser.image_table import facet_view as _facet

_logger = logging.getLogger("well_viewer.image_table")




# ── Virtual "Nuc+Seg" channel ───────────────────────────────────────────────
#
# The pipeline pre-renders a per-(FOV, timepoint) overlay PNG that paints
# the segmentation mask outline on top of the nuclear channel. Exposing it
# as a virtual channel here lets the Image Table double as the old Movie
# Montage view: pick "NUC+SEG" in the channel column and Generate loads
# those overlay images instead of a raw fluorescence channel.


# ── Sidebar picker ───────────────────────────────────────────────────────────


def image_table_active_wells(ctx) -> list[str]:
    """The tab's active wells, loaded ones only, in plate order.

    The Image Table's picker is its own selector — it keeps its wells in
    ``state.image_table.active_wells`` and ignores replicate sets — so anything
    reading "what is selected here" (the plate repaint, the Plot Groups drawer
    that sits under it) has to come through this rather than the shared
    ``selection.selected_wells``.
    """
    return sorted(
        (w for w in ctx.state.image_table.active_wells
         if w in ctx.state.dataset.well_paths),
        key=parse_rc,
    )


def image_table_refresh_picker(ctx) -> None:
    """Push the active-well set onto the sidebar plate and update the count label."""
    plate = ctx.runtime.image_table.sidebar_plate
    if plate is not None:
        plate.setEnabledWells(list(ctx.state.dataset.well_paths.keys()))
        active = image_table_active_wells(ctx)
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


def image_table_refresh_empty_state(ctx) -> None:
    """Keep the "nothing here yet" placeholder describing the right nothing.

    This tab is generation-gated, not selection-gated — it has its own picker
    plate, so the app-wide well selection does not drive it — but the placeholder
    still has two states to tell apart. Its standing text asks the user to choose
    wells and channels above and press Generate, which with no dataset open is an
    instruction that cannot be followed: the picker reads "0 wells active" and
    the Channels panel two rows up already says "load a dataset to see channels".
    So with no dataset it says the same thing the rest of Review says, in the
    same words.
    """
    empty = getattr(ctx.runtime.image_table, "empty_state", None)
    if empty is None:
        return
    from well_viewer.core.ui_helpers import NO_DATASET_HINT, NO_DATASET_TITLE

    if ctx.state.dataset.well_paths:
        empty.setText("No image table generated")
        empty.setHint("Choose wells/channels above, then press Generate.")
    else:
        empty.setText(NO_DATASET_TITLE)
        empty.setHint(NO_DATASET_HINT)


def image_table_select_all(ctx) -> None:
    ctx.state.image_table.active_wells = set(ctx.state.dataset.well_paths.keys())
    image_table_refresh_picker(ctx)
    _rebuild_layout_for_well_change(ctx)


def image_table_clear_active(ctx) -> None:
    ctx.state.image_table.active_wells = set()
    image_table_refresh_picker(ctx)
    _rebuild_layout_for_well_change(ctx)


def _rebuild_layout_for_well_change(ctx) -> None:
    """Re-run the layout (debounced) so a Wells axis follows a well-set change.

    Invokes the callback the shelf registers on ``ctx.runtime.image_table`` at
    build time, so the controller needs no ``builder_view`` import (matching the
    row/col_dims_refresh hooks). No-op before the shelf is built."""
    cb = getattr(ctx.runtime.image_table, "selection_rebuild_cb", None)
    if callable(cb):
        cb()


# ── Pipeline-info-driven dropdown options ────────────────────────────────────


# The layout/dimension layer (axis math, per-dim option lists, channel
# enumeration, cell snapshot) lives in ``grid_layout``. Re-exported so call
# sites + external importers (playground) are unchanged.
from well_viewer.features.image_browser.image_table.grid_layout import (  # noqa: F401
    _DIM_KEY,
    _all_dim_options,
    _axis_dim_values,
    _axis_values,
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
        # ``set_combo_values`` blocks signals while it repopulates, so the
        # stepper's own ``currentTextChanged`` sync never fires here — without
        # this the box would keep showing its construction-time 50.0 while the
        # picked channel's real ON threshold was something else entirely.
        refresh_thresh_frac_quick_edit(ctx)


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
from well_viewer.features.image_browser.image_table.build_layout import (  # noqa: F401
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


_DISPLAY_FLOAT_KEYS = ("pixel_size_um", "scale_bar_um")
_DISPLAY_INT_KEYS = ("label_font_px",)
_DISPLAY_INT_MIN1_KEYS = ("scale_bar_thickness_px",)
_DISPLAY_STR_KEYS = ("scale_bar_cells", "scale_bar_img_corner", "scale_bar_color",
                     "label_mode", "label_cells", "label_img_corner", "label_color")
_DISPLAY_BOOL_KEYS = ("label_bg", "scale_bar_show")


def image_table_set_display(ctx, key: str, value) -> None:
    """Set a scale-bar / cell-label display property and re-render if a table
    exists. Mirrors :func:`image_table_set_spacing`, but for the burned-in-chrome
    options (pixel size, bar length/placement/colour, label mode/placement/style)."""
    if key in _DISPLAY_FLOAT_KEYS:
        setattr(ctx.state.image_table, key, max(0.0, float(value)))
    elif key in _DISPLAY_INT_KEYS:
        setattr(ctx.state.image_table, key, max(6, int(round(float(value)))))
    elif key in _DISPLAY_INT_MIN1_KEYS:
        setattr(ctx.state.image_table, key, max(1, int(round(float(value)))))
    elif key in _DISPLAY_STR_KEYS:
        setattr(ctx.state.image_table, key, str(value))
    elif key in _DISPLAY_BOOL_KEYS:
        setattr(ctx.state.image_table, key, bool(value))
    else:
        return
    # Re-render whenever cells are assembled so the first tweak takes effect live.
    if getattr(ctx.runtime.image_table, "cells", None):
        image_table_generate(ctx)


def image_table_set_thresh_frac(ctx, value, channel=None) -> None:
    """Edit a channel's ON cutoff from the Image Table controls row, then
    re-composite so its "≥ Thr" binary overlay reflects it. ``channel`` picks
    which channel (from the ON-cutoff dropdown); ``None`` falls back to the
    active channel.

    Writes through ``set_on_cutoff`` (the single source of truth shared with the
    Cell Gating tab and the other quick-edits), which routes to the intensity or
    the spot-count cutoff depending on the selected Property — the same rule the
    stepper's own caption and value follow, so the box never edits a different
    number than the one it shows. Re-renders only when a table has already been
    generated — the cached images are reused, so it's the same cheap
    re-composite the zoom control does; before the first Generate it just stores
    the value.
    """
    set_on_cutoff(ctx, value, channel)
    if getattr(ctx.state.image_table, "last_render", None):
        image_table_generate(ctx)


# ── Per-cell mouse handling: hover pixel readout + crop drag ────────────────




def _cell_mfi(ctx, label, chan: str, well: str, tp: str, fov: str,
              img_x: int, img_y: int) -> float | None:
    """MFI of the segmented cell under full-image ``(img_x, img_y)``, or None.

    Decodes the tile's mask and builds its ``{base_chan}_mean_intensity`` nid→MFI
    map lazily on first hover (cached on ``label``), so the readout matches the
    Segmentation tab's cell-under-cursor MFI. Returns None for background,
    overlay/ratio/nuc-seg tiles (no intensity column), or any decode/lookup
    failure. Module-level (not nested in ``_install_cell_events``) to keep that
    builder under the cyclomatic-complexity ratchet."""
    if not hasattr(label, "_mfi_by_nid"):
        label._mask_arr = None  # type: ignore[attr-defined]
        label._mfi_by_nid = {}  # type: ignore[attr-defined]
        try:
            import numpy as _np
            # One deferred feature import (collect owns both helpers) so this
            # stays off the deferred-import budget's growth.
            from well_viewer.features.image_browser.review_image.collect import (
                review_build_mfi_map,
                review_norm_fov,
            )
            from well_viewer.core.ui_helpers import norm_timepoint
            cache = ctx.state.image_table.image_cache
            if not isinstance(cache, dict):
                cache = {}
            mask = _load_mask_array(ctx, cache, well, tp, fov)
            if mask is not None:
                label._mask_arr = _np.asarray(mask)  # type: ignore[attr-defined]
                # The CSV row keys are normalised; the cell carries the raw
                # image-filename tokens, so normalise before the lookup (as the
                # binary-mask render does). Gate on the cell's OWN base channel,
                # stripping any " (binary)" variant suffix.
                label._mfi_by_nid = review_build_mfi_map(  # type: ignore[attr-defined]
                    ctx, label._mask_arr, well,
                    review_norm_fov(fov), norm_timepoint(ctx, tp),
                    channel=_binary_base(chan),
                ) or {}
        except Exception:
            swallowed()  # decode/lookup best-effort; tooltip drops MFI on failure
    mask = getattr(label, "_mask_arr", None)
    if mask is None:
        return None
    try:
        nid = int(mask[int(img_y), int(img_x)])
    except Exception:
        swallowed()  # out-of-bounds / non-numeric mask cell → no MFI
        return None
    if nid <= 0:
        return None
    return (getattr(label, "_mfi_by_nid", None) or {}).get(nid)


# Rendered cells carry their source image's shape, not the image — the same
# contract CropTool reads, so both resolve it through one helper.
from well_viewer.data.crop_tool import label_source_shape as _label_source_shape  # noqa: E402


# The one cell whose pixels the hover readout is currently holding:
# ``((well, chan, tp, fov, row), array)``. One frame, replaced as the cursor
# moves to the next cell — enough that dragging across a single cell decodes at
# most once, without the grid holding every cell's frame open.
_HOVER_ARRAY: tuple = ((), None)


def _hover_array(ctx, well: str, chan: str, tp: str, fov: str, r: int) -> np.ndarray | None:
    """The full-resolution array behind one cell, fetched on demand.

    Normally a decode-cache hit; a miss re-reads that one frame. Returns None if
    it can't be loaded (the readout then shows nothing, as it did when the array
    was missing before).
    """
    global _HOVER_ARRAY
    key = (well, chan, tp, fov, r)
    cached_key, cached_arr = _HOVER_ARRAY
    if cached_key == key and cached_arr is not None:
        return cached_arr
    cache = ctx.state.image_table.image_cache
    if not isinstance(cache, dict):
        return None
    arr = None
    with swallow("image table: hover readout load"):
        use_tophat = _row_display_opts(ctx, r, chan)[0]
        ratio = _find_ratio_for_channel(ctx, chan)
        if ratio is not None:
            arr = _load_ratio_array(ctx, cache, well, ratio, tp, fov, use_tophat=use_tophat)
        else:
            arr = _load_array(ctx, cache, well, chan, tp, fov, use_tophat=use_tophat)
    _HOVER_ARRAY = (key, arr)
    return arr


def _install_cell_events(
    ctx, label, crop_tool, well: str, chan: str, tp: str, fov: str, r: int = 0,
    readout_chan: str | None = None,
) -> None:
    """Wire press/move/release on a cell's image label.

    Replaces ``CropTool.install_events`` so the same label can both serve
    as the crop-tool's drag target and report the intensity of the pixel
    under the cursor on every move.

    *chan* is what the cell is CALLED — for an overlay cell that is the
    :data:`OVERLAY_CHAN` sentinel, which names no image on disk. *readout_chan*
    is the channel whose pixels the readout actually reads (the representative
    member the caller also handed ``_render_cell``); it defaults to *chan* for
    every ordinary cell. Passing the sentinel to the loader resolved nothing, so
    the readout was empty over the entire overlay grid — a control that silently
    does nothing rather than one that says which channel it is quoting.

    The pixel readout re-fetches the cell's array on demand rather than the
    label holding one: a grid pinning every cell's full-resolution frame is
    memory that grows with the grid and that the decode cache's budget cannot
    bound (see ``image_table_generate``). The fetch is normally a decode-cache
    hit, and the result is held for the cell currently under the cursor
    (:func:`_hover_array`) so moving within one cell never decodes twice.
    """
    from PySide6.QtCore import QObject, QEvent
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtWidgets import QToolTip

    readout = readout_chan or chan
    label.setMouseTracking(True)
    if crop_tool is not None:
        label.setCursor(_Qt.CrossCursor)

    def _cell_array(_lbl):
        # A label that carries its own array (Segmentation's single review image)
        # is read directly; a grid cell carries only its shape, so its pixels are
        # fetched on demand.
        own = getattr(_lbl, "_raw_arr", None)
        if own is not None:
            return own
        return _hover_array(ctx, well, readout, tp, fov, r)

    def _show_tip(_lbl, global_pt, text: str) -> None:
        if not text or global_pt is None:
            QToolTip.hideText()
            return
        QToolTip.showText(global_pt, text, _lbl)

    def _label_to_image_xy(_lbl, lx: int, ly: int) -> tuple | None:
        """Map label-local coords to source-image (x, y). Returns None when
        cursor is outside the rendered pixmap area.

        Needs the source image's *shape* only, which the label carries — the
        pixels themselves are fetched on demand by the readout."""
        pm = _lbl.pixmap()
        shape = _label_source_shape(_lbl)
        if shape is None or pm is None:
            return None
        if hasattr(pm, "isNull") and pm.isNull():
            return None
        pw, ph = pm.width(), pm.height()
        if pw <= 0 or ph <= 0:
            return None
        full_h, full_w = shape
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

    def _hover_text(_lbl, lx: int, ly: int) -> str:
        """Pixel readout for label-local ``(lx, ly)``; '' when the cursor is off
        the rendered image or the label carries no array. Includes the combo
        identity as a prefix, so it's a superset of the static cell tooltip."""
        xy = _label_to_image_xy(_lbl, lx, ly)
        if xy is None:
            return ""
        arr = _cell_array(_lbl)
        if arr is None:
            return ""
        x, y = xy
        intensity = _format_pixel_intensity(arr, y, x)
        if not intensity:
            return ""
        from well_viewer.domain.selections_model import well_display_label
        # An overlay cell shows a blend but can only quote ONE channel's pixel, so
        # the readout names the channel the number came from beside the cell's own
        # label — a value with no channel on it is not a measurement.
        chan_lbl = chan.upper()
        if readout != chan:
            chan_lbl = f"{chan_lbl} ({readout.upper()})"
        prefix = f"{well_display_label(ctx, well)} {chan_lbl} T:{tp}"
        if fov:
            prefix += f" FOV:{fov}"
        mfi = _cell_mfi(ctx, _lbl, readout, well, tp, fov, x, y)
        suffix = f"  MFI:{mfi:.1f}" if mfi is not None else ""
        return f"{prefix}  ({x}, {y}) = {intensity}{suffix}"

    def _update_hover(_lbl, ev) -> None:
        try:
            pos = ev.position()
            try:
                gp = ev.globalPosition().toPoint()
            except Exception:
                gp = _lbl.mapToGlobal(pos.toPoint())
            _show_tip(_lbl, gp, _hover_text(_lbl, int(pos.x()), int(pos.y())))
        except Exception:
            QToolTip.hideText()

    class _CellEventFilter(QObject):
        def eventFilter(self, obj, ev):
            t = ev.type()
            if t == QEvent.MouseMove:
                _update_hover(obj, ev)
                if crop_tool is not None:
                    crop_tool.update_drag(ev)
            elif t == QEvent.ToolTip:
                # The label carries a static combo-title tooltip (setToolTip).
                # Without this branch, Qt shows *that* when the cursor rests,
                # replacing the live pixel readout — so the intensity value
                # appears only while the mouse moves and vanishes the moment it
                # stops. Re-show the readout (a superset of the combo title) at
                # the hover point and swallow the event so the static tooltip
                # never fires; the value now stays put while the cursor is still.
                try:
                    lp = ev.pos()
                    text = _hover_text(obj, int(lp.x()), int(lp.y()))
                except Exception:
                    text = ""
                if text:
                    try:
                        gp = ev.globalPos()
                    except Exception:
                        gp = None
                    _show_tip(obj, gp, text)
                    return True
                return False
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
    """Decode every distinct (well, channel, timepoint, fov) image in parallel.

    The first Generate after a dataset load (or after the cache is invalidated)
    has to read every cell's image off disk; doing that sequentially on the GUI
    thread is the slow part. Threads help because ``zipfile``/``tifffile``/``PIL``
    release the GIL for the C-level inflate+decode, and the per-zip-handle
    serialization in ``data/zipfile_cache.py`` keeps concurrent reads safe (same
    well → one handle, serialized; different wells → parallel).

    Warms every distinct cell's own (timepoint, fov) — not just one
    representative per well+channel — because a grid that varies timepoint or
    FOV per cell (the Image Table's core purpose: a facet per timepoint, a
    facet per FOV, a full-plate walk, …) otherwise leaves most cells' actual
    pixel decode to happen later, sequentially, on the GUI thread inside
    ``_compute_auto_luts`` and the render loop — exactly the two passes this
    prefetch exists to make unnecessary. Dedupes against
    ``ctx.state.image_table.prefetched_cells`` (not ``cache``, which holds
    per-well+channel ref-dicts, not a per-image marker), so a second Generate
    on an unchanged grid submits nothing to the thread pool at all rather than
    just cheaply re-hitting ``_load_array``'s own caches. Ratio channels are
    skipped (they compose from per-channel arrays, which this still warms).
    Best-effort: any failure leaves the sequential in-loop load as the
    fallback.
    """
    warmed = ctx.state.image_table.prefetched_cells
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
            key = (well, chan.lower(), variant, str(tp), str(fov))
            if key in warmed or key in todo:
                continue
            todo[key] = (well, chan, tp, fov, th)
    if len(todo) < 2:
        return  # nothing to gain from a pool for 0–1 reads
    import os
    from concurrent.futures import ThreadPoolExecutor

    def _warm(spec) -> bool:
        well, chan, tp, fov, th = spec
        try:
            return _load_array(ctx, cache, well, chan, tp, fov, use_tophat=th) is not None
        except Exception:
            swallowed()  # in-loop load (with its own handling) is the fallback
            return False

    keys = list(todo)
    ok: list = []
    try:
        with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 4))) as ex:
            ok = list(ex.map(_warm, [todo[k] for k in keys]))
    except Exception:
        import logging as _logging
        _logging.getLogger("well_viewer.image_table").debug(
            "image_table: parallel prefetch failed", exc_info=True)
    # Mark only the keys that actually decoded. Marking a failure would skip it
    # on every later Generate too, which is wrong for the failures that are not
    # permanent -- a drive that stalled mid-pass, a file still being written.
    # The cost of retrying a genuinely missing image is one more scan per
    # Generate; the cost of not retrying is a cell that never comes back.
    warmed.update(k for k, good in zip(keys, ok) if good)


def _cell_override_menu(ctx, r: int, c: int, global_pos) -> None:
    """Per-cell override popover (delegates to grid_ops; lazy to avoid a cycle)."""
    from well_viewer.features.image_browser.image_table.grid_ops import (
        image_table_cell_override_menu,
    )
    image_table_cell_override_menu(ctx, r, c, global_pos)


def _cell_title(ctx, cell, overlay_chans) -> str:
    """Tooltip for one image-table cell (identity lives on the axes, not per cell).

    An OVERLAY_CHAN cell names the merged channel set (e.g. "GFP+DAPI"); a normal
    cell names its single channel; anything without a well+timepoint is "(unset)".
    Delegates to the shared :func:`cell_identity_label` (also the burned-in label
    source) so tooltip / burned label / export title all read identically.

    A cell served the raw frame in place of the corrected one says so in full
    words here; the burned-in mark has to stay short enough to sit in a corner."""
    title = cell_identity_label(ctx, cell, overlay_chans) or "(unset)"
    if is_raw_fallback(ctx, cell, overlay_chans):
        return f"{title}\n{UNCORRECTED_TOOLTIP}"
    return title


def _render_cell(ctx, cache, cell, r, crop_tool, sz, overlay_chans, make_overlay_thumb,
                 chrome_kwargs=None):
    """Load + render one cell → ``(pixmap, representative_array, ok, note)``.

    ``ok`` gates whether the cell shows an image and installs interaction. For an
    OVERLAY_CHAN cell the pixmap is the additive blend of every channel in
    *overlay_chans* (``overlay_cell_rgb``) and ``ok`` follows that blend; otherwise
    it's the single channel's display (``cell_display_rgb`` / the pre-rendered
    overlay thumbnailer) and ``ok`` follows the loaded array. The representative
    array is the first overlay channel's (or the cell's own) so crop-drag
    coordinate conversion still has a source.

    ``note`` is the placeholder text for a cell that produced no pixmap — it
    names the reason ("(no image)" when nothing decoded, "(no mask)" when the
    cell asks for a mask / threshold view the segmentation output can't supply),
    so a partly-rendered grid says which cells are missing what instead of
    quietly showing a different picture than the one that was asked for.

    ``chrome_kwargs`` (when given) burns the scale bar / identity label into the
    composed RGB before it's thumbnailed, so the live thumbnail matches the export
    figure (which burns the same chrome into its own RGB)."""
    well, chan, tp, fov = cell["well"], cell["chan"], cell["tp"], cell["fov"]
    if chan == OVERLAY_CHAN:
        if not (well and tp and overlay_chans):
            return None, None, False, "(no image)"
        # A representative single-channel array so crop-drag coordinate conversion
        # has a source; the DISPLAY is the blend of every channel.
        arr = _load_array(ctx, cache, well, overlay_chans[0], tp, fov,
                          use_tophat=_row_display_opts(ctx, r, overlay_chans[0])[0])
        rgb = overlay_cell_rgb(ctx, cache, cell, r, crop_tool, overlay_chans)
        if rgb is not None and chrome_kwargs:
            rgb = draw_cell_chrome(rgb, **chrome_kwargs)
        # A blend built from an uncorrected member IS an uncorrected image, and it
        # is the one kind of cell whose channel is not on the grid to be read off
        # the axis. Without the member list the mark could never resolve, so the
        # overlay branch drew a cell the status line was counting as uncorrected
        # while nothing on the grid said which cell that was.
        rgb = mark_uncorrected(ctx, cell, rgb, overlay_chans)
        pix = make_overlay_thumb(rgb, sz, sz, 0.0, 255.0) if rgb is not None else None
        return pix, arr, rgb is not None, "(no image)"

    # Which base image to load (raw vs corrected) is a per-channel render option.
    use_tophat, _bnd, binary_mode = _row_display_opts(ctx, r, chan)
    if _is_binary_variant(chan):
        binary_mode = "threshold"
    elif _is_mask_variant(chan):
        binary_mode = "mask"
    arr = None
    if well and chan and tp:
        ratio = _find_ratio_for_channel(ctx, chan)
        if ratio is not None:
            arr = _load_ratio_array(ctx, cache, well, ratio, tp, fov, use_tophat=use_tophat)
        else:
            arr = _load_array(ctx, cache, well, chan, tp, fov, use_tophat=use_tophat)
    if arr is None and binary_mode:
        # A mask / ≥threshold cell draws its segmentation, never the fluorescence
        # pixels — ``_compose_image_table_rgb`` returns the silhouette before it
        # reads a single intensity value. Requiring the frame anyway made a whole
        # row read "(no image)" on a dataset whose output zip carries the mask and
        # the corrected frame but no raw one, which is both a cell that could have
        # been drawn and a placeholder naming the wrong missing thing. The mask
        # doubles as the cell's representative array: it has the source shape, so
        # the crop-drag conversion and the pixel readout stay honest.
        arr = _load_mask_array(ctx, cache, well, tp, fov)
    if arr is None:
        return None, None, False, "(no mask)" if binary_mode else "(no image)"
    # A mask / threshold cell that can't resolve its mask is reported as such
    # (cell_display_rgb returns None rather than rendering the intensity image
    # the user did not ask for).
    note = "(no mask)" if binary_mode in ("mask", "threshold") else "(render failed)"
    # cell_display_rgb resolves render / boundary / LUT / colormap so live and
    # exported thumbnails always agree. A pre-rendered RGB that isn't NUC+SEG goes
    # straight to the overlay thumbnailer (cell_display_rgb only treats NUC+SEG as
    # overlay).
    if getattr(arr, "ndim", 2) >= 3 and not _is_nuc_seg(chan):
        cropped = crop_tool.apply_to_array(arr) if crop_tool else arr
        lo, hi = _parse_lut(ctx, r, chan)
        pix = make_overlay_thumb(cropped, sz, sz, lo, hi)
    else:
        rgb = cell_display_rgb(ctx, cache, cell, r, crop_tool, arr)
        if rgb is not None and chrome_kwargs:
            rgb = draw_cell_chrome(rgb, **chrome_kwargs)
        # After the chrome, so the mark is not painted over by a scale bar or an
        # identity label sharing the cell.
        rgb = mark_uncorrected(ctx, cell, rgb)
        # Pin the window to the full 0..255 byte range (as the overlay branch
        # above does). ``cell_display_rgb`` has already applied the channel's LUT
        # and colormap, so letting make_overlay_thumb auto-fit its own min/max
        # would re-stretch a finished image — visibly so on a colormap whose ends
        # aren't black and white (viridis bottoms out around 68, tops out at 253),
        # where the live thumbnail then disagreed with the exported cell.
        pix = make_overlay_thumb(rgb, sz, sz, 0.0, 255.0) if rgb is not None else None
    return pix, arr, True, note


def _compute_auto_luts(ctx, cells, cache, crop_tool) -> None:
    """Pool ONE Auto-LUT window per channel across the whole grid.

    A channel left on Auto otherwise auto-fits each cell to its own min/max, so the
    same channel's brightness isn't comparable cell-to-cell. Here we compute a single
    (min, max) per base channel over every Auto cell of that channel and stash it on
    ``auto_lut_by_channel``; ``_parse_lut`` then returns it for those cells (live +
    export). The map is cleared first, so ``_parse_lut`` still reports the
    pre-pooling (Auto) state while we decide which cells qualify. Binary / mask /
    NUC+SEG / overlay / pre-rendered-RGB cells carry no LUT and are skipped."""
    import numpy as _np

    st = ctx.state.image_table
    st.auto_lut_by_channel = {}
    pooled: dict[str, list] = {}  # base_chan_lower -> [min, max]
    for r, row in enumerate(cells):
        for cell in row:
            chan = cell["chan"]
            well, tp, fov = cell["well"], cell["tp"], cell["fov"]
            if not (well and chan and tp):
                continue
            if chan == OVERLAY_CHAN or _is_nuc_seg(chan) or _is_binary_variant(chan):
                continue
            use_tophat, _bnd, binary_mode = _row_display_opts(ctx, r, chan)
            if binary_mode in ("mask", "threshold"):
                continue
            # Only pool channels actually on Auto (no explicit / shared window). The
            # map is empty here, so _parse_lut gives the pre-pooling answer.
            if _parse_lut(ctx, r, chan) != (None, None):
                continue
            # Per cell, so one unreadable frame costs the pooling that cell's
            # contribution and nothing more — pooling the rest still beats
            # dropping every channel back to per-cell auto-fit.
            try:
                ratio = _find_ratio_for_channel(ctx, chan)
                if ratio is not None:
                    arr = _load_ratio_array(ctx, cache, well, ratio, tp, fov,
                                            use_tophat=use_tophat)
                else:
                    arr = _load_array(ctx, cache, well, chan, tp, fov,
                                      use_tophat=use_tophat)
            except Exception:
                swallowed()  # the render loop reports this cell
                continue
            if arr is None or getattr(arr, "ndim", 2) >= 3:
                continue
            # A cell served the raw frame is not on the corrected population's
            # scale — it still carries the camera offset and the background the
            # top-hat would have removed. Pooling it in stretches the window by
            # that offset and renders every genuinely corrected cell of the
            # channel near-black. Pool the corrected population only; a raw cell
            # falls through to its own auto-fit, which is the honest answer for a
            # measurement that is not comparable to the rest of the grid.
            if is_raw_fallback(ctx, cell):
                continue
            a = crop_tool.apply_to_array(arr) if crop_tool is not None else arr
            a = _np.asarray(a, dtype=_np.float32)
            if a.size == 0:
                continue
            lo, hi = float(a.min()), float(a.max())
            base = _binary_base(chan).strip().lower()
            cur = pooled.get(base)
            if cur is None:
                pooled[base] = [lo, hi]
            else:
                cur[0] = min(cur[0], lo)
                cur[1] = max(cur[1], hi)
    st.auto_lut_by_channel = {k: (v[0], v[1]) for k, v in pooled.items()}


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
        ctx.notify.set_status("Image Table: build a grid first (set rows/cols and click Apply).")
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
    # Empty scans are remembered for THIS pass only (data/frame_load.load_well_channel),
    # so one Generate scans a missing well/channel once and the next Generate
    # gives a drive that had stalled another chance.
    reset_scan_misses(ctx)
    rendered: dict[tuple[int, int], Any] = {}
    # Cells that produced no picture, and the subset of those whose render raised.
    unrendered = 0
    failed = 0
    crop_tool = getattr(ctx.runtime.image_table, "crop_tool", None)
    # Per-cell thumbnail size, scaled by the zoom factor (240 px = 1.0×).
    zoom = float(getattr(ctx.state.image_table, "zoom", 1.0) or 1.0)
    sz = max(80, int(round(240 * zoom)))

    # Every pass below this point is wrapped: the grid has already been cleared,
    # so an exception escaping any of them leaves the user staring at an empty
    # pane with the previous table gone. Each one degrades to a usable default
    # instead — a lost brightness pooling or a lost scale bar beats no images.
    with swallow("image table: parallel prefetch"):
        _prefetch_cell_arrays(ctx, cells, cache)

    # Pool one Auto-LUT window per channel across the grid so Auto cells of the same
    # channel are brightness-comparable (must run before any cell renders, since
    # _parse_lut reads the pooled map). Falling back means per-cell auto-fit.
    with swallow("image table: pooled auto-LUT"):
        _compute_auto_luts(ctx, cells, cache, crop_tool)

    # Scale-bar length + which cells carry the bar / identity label, resolved once.
    chrome_plan = (0, set(), set())
    with swallow("image table: chrome plan"):
        chrome_plan = resolve_chrome_plan(ctx, cells)

    # Faceted placement: group cells by the builder's nesting, label the row/col
    # axes once, and space groups apart (falls back to a plain grid when absent).
    plan = None
    with swallow("image table: facet plan"):
        plan = _facet.plan_facets(ctx, cells)

    def _pos(r, c):
        if plan is None:
            return r, c
        return plan.grid_row_of[r], plan.grid_col_of[c]

    # When Overlay is on, the channel axis has collapsed to the OVERLAY_CHAN
    # sentinel and each cell blends the whole selected-channel set (resolved once,
    # off the collapse). Empty until a channel-overlay cell is actually hit.
    overlay_chans: list[str] = []

    for r, row in enumerate(cells):
        for c, cell in enumerate(row):
            is_overlay = cell["chan"] == OVERLAY_CHAN
            # Overlay collapses the channel axis to a single sentinel cell that
            # blends the whole selected-channel set; resolve it once (off the
            # collapse) on first sight.
            if is_overlay and not overlay_chans:
                overlay_chans = _axis_dim_values(ctx, "channel", collapse_overlay=False)

            # One bad cell must not take the pass down with it. The grid is
            # already cleared by the time this loop runs, so an exception here
            # used to abandon every remaining cell — a corrupt frame, a read
            # that failed mid-pass, or a shape the composer rejects left a
            # half-drawn (or entirely empty) table with no message saying why.
            try:
                chrome_kwargs = cell_chrome_kwargs(ctx, r, c, cell, chrome_plan,
                                                   overlay_chans)
                pix, arr, ok, note = _render_cell(
                    ctx, cache, cell, r, crop_tool, sz, overlay_chans,
                    make_overlay_thumb, chrome_kwargs)
            except Exception:
                _logger.warning(
                    "image_table: cell (row=%d, col=%d) well=%r channel=%r tp=%r "
                    "fov=%r failed to render", r, c, cell.get("well"),
                    cell.get("chan"), cell.get("tp"), cell.get("fov"), exc_info=True,
                )
                failed += 1
                pix, arr, ok, note = None, None, False, "(render failed)"
            # The render model records WHICH cells drew, never their pixels.
            # Holding each cell's full-resolution frame here (and on the label
            # below) pinned the whole grid's pixels for as long as the table was
            # on screen — memory that scales with the grid and that the decode
            # cache's budget cannot bound, because the pins live outside it. The
            # consumers that used to read an array from here (export, the
            # composite column) already lazy-load on a None, and that load is a
            # decode-cache hit.
            rendered[(r, c)] = None

            cell_widget = QWidget()
            cl = QVBoxLayout(cell_widget)
            cl.setContentsMargins(2, 2, 2, 2)
            cl.setSpacing(2)

            img_label = QLabel(cell_widget)
            img_label.setToolTip(_cell_title(ctx, cell, overlay_chans))
            img_label.setAlignment(Qt.AlignCenter)
            if ok:
                # CropTool + the pixel readout need the FULL source image's
                # SHAPE and the active crop, so label-pixel → image-pixel coord
                # conversion works even when the label already shows a crop.
                # Only the shape: holding the array here would pin every cell's
                # frame for the life of the grid (see the render-model note above).
                shape = getattr(arr, "shape", None)
                img_label._raw_shape = (  # type: ignore[attr-defined]
                    (int(shape[0]), int(shape[1])) if shape and len(shape) >= 2 else None)
                img_label._crop = crop_tool.crop if crop_tool else None  # type: ignore[attr-defined]
                # The readout quotes pixels, and an overlay cell's own token names
                # no image — it reads the same representative member ``_render_cell``
                # already used for the crop-drag source, so the two agree on which
                # frame the cell's coordinates belong to.
                _install_cell_events(ctx, img_label, crop_tool,
                                     cell["well"], cell["chan"], cell["tp"],
                                     cell["fov"], r,
                                     readout_chan=(overlay_chans[0]
                                                   if cell["chan"] == OVERLAY_CHAN
                                                   and overlay_chans else None))
                # Right-click a rendered cell → one-off per-cell override popover
                # (writes the cell combos the renderer reads, then re-renders).
                img_label.setContextMenuPolicy(Qt.CustomContextMenu)
                img_label.customContextMenuRequested.connect(
                    lambda pos, rr=r, cc=c, _l=img_label:
                    _cell_override_menu(ctx, rr, cc, _l.mapToGlobal(pos))
                )
                if pix is not None:
                    img_label.setPixmap(pix)
                    # Pin the label to the ACTUAL pixmap size. make_overlay_thumb
                    # scales KeepAspectRatio, so a non-square image is shorter/
                    # narrower than sz; a fixed sz×sz square would letterbox it and
                    # leave large blank bands that read as huge gaps between rows.
                    img_label.setFixedSize(pix.width(), pix.height())
                else:
                    unrendered += 1
                    img_label.setText(note)
                    img_label.setFixedSize(sz, sz)
            else:
                unrendered += 1
                img_label.setText(note)
                img_label.setFixedSize(sz, sz)
            cl.addWidget(img_label)

            gr, gc = _pos(r, c)
            grid.addWidget(cell_widget, gr, gc)

    # Axis labels + inter-group spacing that mirror the builder's nesting.
    if plan is not None:
        _facet.render_facet_chrome(grid, plan)

    ctx.state.image_table.last_render = rendered
    ctx.state.image_table.image_cache = cache
    # Count the cells that actually show a picture — not the ones whose source
    # array loaded. A cell that loaded its image but could not render the view
    # it was asked for (no mask for a threshold cell) is a hole in the grid, and
    # the status line is the only place that says so without reading the log.
    n = len(rendered) - unrendered
    msg = f"Image Table: generated {n} of {len(rendered)} cell(s)."
    if unrendered:
        msg += f" {unrendered} could not be drawn"
        msg += f" ({failed} raised)." if failed else " — see the log."
    # Cells served the raw frame because the corrected (top-hat) set doesn't
    # cover them. They DO draw, so the count above says nothing about them — and
    # a grid silently mixing corrected and raw frames is exactly the kind of
    # degraded output that reads as a finished one.
    #
    # Counted over the CELLS, not over the loader's record. The record is keyed by
    # the channel actually opened, so an overlay cell blending three fallen-back
    # channels put three entries in it and a ratio cell two — a number larger than
    # the grid, describing loads the user cannot point at, next to a badge drawn
    # once per cell. This counts exactly the cells that carry the mark.
    n_raw = sum(1 for row in cells for cell in row
                if is_raw_fallback(ctx, cell, overlay_chans))
    if n_raw:
        msg += f" {n_raw} shown uncorrected (no corrected image)."
    ctx.notify.set_status(msg)
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
from well_viewer.features.image_browser.image_table.export import (  # noqa: F401
    _export_opts,
    image_table_open_export_settings,
    _build_export_figure,
    image_table_export,
    image_table_copy_png,
    image_table_copy_svg,
    image_table_save_to_gallery,
)




# ── Export (transparent background, labels above each image) ─────────────────








