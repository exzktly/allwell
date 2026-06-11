"""Image Table tab — non-UI controller logic.

These functions are bound onto the runtime app (via ``runtime_app.py``)
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

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QVBoxLayout, QWidget,
)
from well_viewer.core.safe import swallowed


# ── LUT colour palette ──────────────────────────────────────────────────────
#
# Per-row tints applied to the grayscale fluorescence image. ``Gray`` keeps
# the legacy white intensity ramp. Each tint is an (r, g, b) triple in
# [0, 1]; ``Gray`` uses ``None`` so make_fluor_thumb takes its untinted
# fast path.
LUT_COLORS: Dict[str, Optional[Tuple[float, float, float]]] = {
    # Keys are mpl colormap names so the v2 LutSelector (which renders a real
    # gradient strip) can drive the picker. The value is the single (r,g,b)
    # tint applied to the grayscale fluorescence image for thumbnails; the
    # exported figure still uses a real cmap via _row_export_cmap.
    "Greys":   None,
    "Reds":    (1.0, 0.0, 0.0),
    "Greens":  (0.0, 1.0, 0.0),
    "Blues":   (0.0, 0.0, 1.0),
    "Purples": (0.56, 0.0, 1.0),
    "Oranges": (1.0, 0.55, 0.0),
}
LUT_COLOR_NAMES: List[str] = list(LUT_COLORS.keys())


def _tint_from_colormap(name: str) -> Optional[Tuple[float, float, float]]:
    """Derive an (r, g, b) tint by sampling the colormap's high end.

    Used as a fallback when the user picks an mpl colormap that isn't in our
    explicit ``LUT_COLORS`` table (e.g. ``viridis`` / ``magma`` / ``coolwarm``).
    Returns ``None`` if the colormap can't be loaded so the thumbnail falls
    back to grayscale rather than crashing.
    """
    try:
        from matplotlib import colormaps as _mpl_cmaps
    except Exception:
        return None
    try:
        cmap = _mpl_cmaps[name]
    except (KeyError, ValueError):
        return None
    try:
        r, g, b, _a = cmap(0.85)
    except Exception:
        return None
    return (float(r), float(g), float(b))


# ── Virtual "Nuc+Seg" channel ───────────────────────────────────────────────
#
# The pipeline pre-renders a per-(FOV, timepoint) overlay PNG that paints
# the segmentation mask outline on top of the nuclear channel. Exposing it
# as a virtual channel here lets the Image Table double as the old Movie
# Montage view: pick "NUC+SEG" in the channel column and Generate loads
# those overlay images instead of a raw fluorescence channel.
NUC_SEG_TOKEN = "NUC+SEG"


# ── Sidebar picker ───────────────────────────────────────────────────────────


def image_table_refresh_picker(app) -> None:
    """Push the active-well set onto the sidebar plate and update the count label."""
    plate = getattr(app, "_sidebar_image_table_plate", None)
    if plate is not None:
        plate.setEnabledWells(list(app._app_state.dataset.well_paths.keys()))
        active = sorted(
            (w for w in app._app_state.image_table.active_wells if w in app._app_state.dataset.well_paths),
            key=app._parse_rc,
        )
        plate.setSelectedWellIds(active)
    lbl = getattr(app, "_image_table_count_lbl", None)
    if lbl is not None:
        n = len(app._app_state.image_table.active_wells)
        lbl.setText(f"{n} well{'s' if n != 1 else ''} active")


def image_table_select_all(app) -> None:
    app._app_state.image_table.active_wells = set(app._app_state.dataset.well_paths.keys())
    image_table_refresh_picker(app)


def image_table_clear_active(app) -> None:
    app._app_state.image_table.active_wells = set()
    image_table_refresh_picker(app)


# ── Pipeline-info-driven dropdown options ────────────────────────────────────


def _channel_options(app) -> List[str]:
    """All channel tokens available for selection (uppercase for display).

    Sourced from the canonical ``app._app_state.channel.fluor_channels`` /
    ``app._app_state.channel.smfish_channels`` lists (built by ``_recalculate_threshold`` from
    pipeline_info.json + CSV detection — same lists every other tab uses).
    Falls back to the raw ``app._app_state.dataset.pipeline_info`` dict for the rare case
    where the tab is repopulated before the first ``_recalculate_threshold``
    pass. Adds the virtual ``NUC+SEG`` token at the end when a nuclear
    channel is known — that's where the pre-rendered segmentation overlay
    images live, and they're how the Image Table replaces the old Movie
    Montage tab.
    """
    info = app._app_state.dataset.pipeline_info or {}
    chans: List[str] = []

    fluor = list(app._app_state.channel.fluor_channels or [])
    if not fluor:
        nuclear_pi = (info.get("nuclear_token") or "").strip().lower()
        if nuclear_pi:
            fluor.append(nuclear_pi)
        for tok in info.get("fluor_tokens", []) or []:
            tok = str(tok).strip().lower()
            if tok and tok not in fluor:
                fluor.append(tok)
    for tok in fluor:
        tok = str(tok).strip().lower()
        if tok and tok not in chans:
            chans.append(tok)

    smfish = app._app_state.channel.smfish_channels or []
    for tok in smfish:
        tok = str(tok).strip().lower()
        if tok and tok not in chans:
            chans.append(tok)

    options = [c.upper() for c in chans]

    seg_tok = (getattr(app, "_seg_channel_token", "") or "").strip()
    if not seg_tok:
        seg_tok = (info.get("nuclear_token") or "").strip()
    if seg_tok:
        options.append(NUC_SEG_TOKEN)

    # Append user-defined ratio metrics as virtual channels.  The display
    # label "GFP/RFP" is what appears in the dropdown; _find_ratio_for_channel
    # resolves it back to the RatioMetric at render time.
    for r in (app._app_state.channel.ratio_metrics or []):
        lbl = r.display_label()
        if lbl and lbl not in options:
            options.append(lbl)

    return options


def _find_ratio_for_channel(app, chan: str):
    """Return the RatioMetric whose display_label matches *chan*, or None."""
    for r in (app._app_state.channel.ratio_metrics or []):
        if r.display_label().upper() == chan.strip().upper():
            return r
    return None


def _is_nuc_seg(chan: str) -> bool:
    return str(chan or "").strip().upper() == NUC_SEG_TOKEN


def _norm_token(value: object) -> str:
    """Normalize a numeric-or-string FOV/timepoint to a stable form.

    ``"1"`` and ``"1.0"`` collapse to ``"1"``; non-numeric strings are
    returned untouched. This matches the form emitted by the filename
    extractors that key the image dicts in ``find_well_images_and_masks``.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return f"{float(raw):g}"
    except (TypeError, ValueError):
        return raw


def _numeric_token(value: object) -> Optional[float]:
    """Reduce a timepoint/FOV string to a numeric key for cache-key matching.

    Tries (in order):
      1. ``parse_timepoint_hours`` — handles ``"01d02h30m"``, ``"48h"``,
         ``"2d"``, ``"30m"``, ``"T01"``, plain numbers, etc.
      2. First contiguous digit run as int (legacy fallback for ``"T01"`` →
         ``1`` when ``parse_timepoint_hours`` returns None).

    Returns ``None`` when nothing parsable is found.
    """
    from well_viewer.data.data_loading import parse_timepoint_hours
    s = str(value or "").strip()
    if not s:
        return None
    parsed = parse_timepoint_hours(s)
    if parsed is not None:
        return float(parsed)
    import re as _re
    m = _re.search(r"\d+", s)
    if not m:
        return None
    try:
        return float(int(m.group(0)))
    except ValueError:
        return None


def _tp_sort_key(tp: str):
    """Sort key for timepoint strings; uses parse_timepoint_hours so
    ``01d02h30m``-style values order chronologically."""
    from well_viewer.data.data_loading import parse_timepoint_hours
    h = parse_timepoint_hours(str(tp))
    return (h is None, h if h is not None else 0.0, str(tp))


def _cache_column_tokens(app, column: str) -> List[str]:
    """Unique normalised tokens from ``column`` across every cached DataFrame.

    ``app._app_state.dataset.cache`` values are DataFrames since the pandas migration; iterating
    one with ``for row in df`` yields *column names*, not rows. Pull the column
    out vectorised instead — and skip frames that don't carry it at all.
    """
    seen: List[str] = []
    cache = app._app_state.dataset.cache or {}
    for df in cache.values():
        if df is None or column not in getattr(df, "columns", ()):
            continue
        for val in df[column].dropna().unique():
            tok = _norm_token(val)
            if tok and tok not in seen:
                seen.append(tok)
    return seen


def _timepoint_options(app) -> List[str]:
    info = app._app_state.dataset.pipeline_info or {}
    seen: List[str] = []
    for tp in (info.get("available_timepoints") or []):
        tok = _norm_token(tp)
        if tok and tok not in seen:
            seen.append(tok)
    if seen:
        return sorted(seen, key=_tp_sort_key)
    # Fallback: derive timepoints from loaded CSV rows when pipeline_info
    # doesn't list them (older runs, custom schemas, etc.).
    return sorted(_cache_column_tokens(app, "timepoint"), key=_tp_sort_key)


def _fov_options(app) -> List[str]:
    info = app._app_state.dataset.pipeline_info or {}
    seen: List[str] = []
    for fv in (info.get("available_fovs") or []):
        tok = _norm_token(fv)
        if tok and tok not in seen:
            seen.append(tok)
    if seen:
        return seen
    return _cache_column_tokens(app, "fov")


def _well_options(app) -> List[str]:
    return sorted(app._app_state.dataset.well_paths.keys(), key=app._parse_rc)


def image_table_repopulate_dropdowns(app) -> None:
    """Refresh option lists for the global row, the per-row channel combos, and every cell.

    Called when pipeline info / wells become available after the tab is
    already built. Existing selections are preserved when still valid.
    """
    chan_opts = _channel_options(app)
    tp_opts = _timepoint_options(app)
    fov_opts = _fov_options(app)
    well_opts = _well_options(app)

    def _reset_combo(cb, options):
        cur = cb.currentText()
        cb.blockSignals(True)
        cb.clear()
        cb.addItems(options)
        if cur in options:
            cb.setCurrentText(cur)
        cb.blockSignals(False)

    if hasattr(app, "_image_table_global_chan_cb"):
        _reset_combo(app._image_table_global_chan_cb, chan_opts)
    if hasattr(app, "_image_table_global_tp_cb"):
        _reset_combo(app._image_table_global_tp_cb, tp_opts)
        _reset_combo(app._image_table_global_fov_cb, fov_opts)

    for cb in getattr(app, "_image_table_row_chan_cbs", None) or []:
        _reset_combo(cb, chan_opts)
    for cb in getattr(app, "_image_table_row_well_cbs", None) or []:
        _reset_combo(cb, well_opts)

    cells = getattr(app, "_image_table_cells", None) or []
    for row in cells:
        for cell in row:
            _reset_combo(cell["well_cb"], well_opts)
            _reset_combo(cell["chan_cb"], chan_opts)
            _reset_combo(cell["tp_cb"], tp_opts)
            _reset_combo(cell["fov_cb"], fov_opts)

    # The LUT row is built once at tab-construction time, when the canonical
    # channel lists may still be empty. Rebuild it now so the per-channel
    # min/max entry boxes appear once channels are known. ``build_lut_row``
    # preserves any previously-entered values keyed by channel.
    existing = set((getattr(app, "_image_table_lut", None) or {}).keys())
    if set(chan_opts) != existing:
        image_table_rebuild_lut_row(app)

    # Keep the sidebar channel multi-select in sync with the dataset's channels.
    image_table_refresh_channel_picker(app)


# ── Selector-grid build / rebuild ────────────────────────────────────────────


def image_table_apply_dimensions(app) -> None:
    """Read rows / cols spinners and rebuild the selector grid."""
    rows = int(app._image_table_rows_spin.value())
    cols = int(app._image_table_cols_spin.value())
    app._app_state.image_table.rows = rows
    app._app_state.image_table.cols = cols
    image_table_rebuild_grid(app)


def image_table_rebuild_grid(app) -> None:
    """Rebuild the selector grid (rows × cols of cell groupboxes) plus the
    per-row channel + LUT-colour column at column 0."""
    from well_viewer.features.image_table.grid_view import build_image_table_grid

    rows = int(getattr(app._app_state.image_table, "rows", 2) or 2)
    cols = int(getattr(app._app_state.image_table, "cols", 3) or 3)

    chan_opts = _channel_options(app)
    tp_opts = _timepoint_options(app)
    fov_opts = _fov_options(app)
    well_opts = _well_options(app)

    cells, row_chan_cbs, row_lut_color_cbs, row_well_cbs = build_image_table_grid(
        app,
        rows=rows,
        cols=cols,
        chan_opts=chan_opts,
        tp_opts=tp_opts,
        fov_opts=fov_opts,
        well_opts=well_opts,
        lut_color_names=LUT_COLOR_NAMES,
        on_apply_row_well=image_table_apply_row_well,
        on_apply_row_channel=image_table_apply_row_channel,
        on_generate=image_table_generate,
    )

    app._image_table_cells = cells
    app._image_table_row_chan_cbs = row_chan_cbs
    app._image_table_row_lut_color_cbs = row_lut_color_cbs
    app._image_table_row_well_cbs = row_well_cbs

    # Refresh the per-channel LUT row whenever any cell's channel changes
    # so unused channels disappear and newly assigned ones get an editor.
    for row in cells:
        for cell in row:
            cb = cell.get("chan_cb")
            if cb is None:
                continue
            cb.currentIndexChanged.connect(
                lambda _i, a=app: image_table_rebuild_lut_row(a)
            )

    # Re-render thumbnails whenever the user picks a different LUT colour.
    # The LutSelector emits ``lutChanged(name, reversed)`` on change; legacy
    # QComboBox fallbacks emit ``currentIndexChanged(int)``.
    for cb in row_lut_color_cbs:
        if cb is None:
            continue
        sig = getattr(cb, "lutChanged", None)
        if sig is not None and hasattr(sig, "connect"):
            sig.connect(lambda _n, _rv, a=app: image_table_generate(a))
        else:
            cb.currentIndexChanged.connect(
                lambda _i, a=app: image_table_generate(a)
            )

    image_table_rebuild_lut_row(app)


def _row_cmap_name(app, r: int) -> Optional[str]:
    """Return the mpl colormap name for row *r*, or None when the row is
    set to ``Greys`` (untinted grayscale fast path).

    ``make_fluor_thumb`` consumes this directly and applies the colormap
    across the full LUT [lo, hi] range, so the rendered thumbnails match
    the gradient shown by the LutSelector's strip — high intensity gets
    the cmap's high-end colour, low intensity gets the low end.
    """
    cbs = getattr(app, "_image_table_row_lut_color_cbs", None) or []
    if not (0 <= r < len(cbs)):
        return None
    cb = cbs[r]
    if hasattr(cb, "lut"):
        name = (cb.lut() or "").strip()
    else:
        name = cb.currentText().strip()
    if not name or name == "Greys":
        return None
    return name


def _row_tint(app, r: int) -> Optional[Tuple[float, float, float]]:
    """Legacy single-colour tint for row *r* (kept for export-cmap derivation).

    The Image Table thumbnails now go through ``_row_cmap_name`` so the full
    colormap maps across the LUT range; this helper still backs the export
    figure path, which constructs a black→tint LinearSegmentedColormap.
    """
    cbs = getattr(app, "_image_table_row_lut_color_cbs", None) or []
    if not (0 <= r < len(cbs)):
        return None
    cb = cbs[r]
    if hasattr(cb, "lut"):
        name = (cb.lut() or "").strip()
    else:
        name = cb.currentText().strip()
    if not name:
        return None
    if name in LUT_COLORS:
        return LUT_COLORS[name]
    return _tint_from_colormap(name)


def _row_export_cmap(app, r: int):
    """Return a matplotlib colormap matching the row's LUT selector.

    For ``Greys`` returns the built-in ``"gray"`` cmap. For any named
    colormap selectable in the row (``Reds``, ``viridis``, ``magma``,
    …) returns that colormap **by name** so the export figure picks up
    the *full* gradient — same as the live thumbnail.

    Older versions of this helper degraded to a black→tint
    LinearSegmentedColormap which produced monochrome exports that
    didn't match the multi-hue live view. Fixed.
    """
    name = _row_cmap_name(app, r)  # None when row is "Greys"
    if not name:
        return "gray"
    return name


def _export_binary_mask(app, cache, well, chan, tp, fov, binary_mode, crop_tool):
    """Compute the white-cells array for a binary-mode export cell (cropped).

    Mirrors the live ``_render_mask_binary`` / ``_render_threshold_binary``
    selection so the export matches the on-screen thumbnails.
    """
    import numpy as _np

    mask_arr = _load_mask_array(app, cache, well, tp, fov)
    if mask_arr is None:
        return None
    if binary_mode == "threshold":
        above_by_nid: Dict[int, bool] = {}
        try:
            from well_viewer.features.review_image.controller import review_build_threshold_map
            above_by_nid = review_build_threshold_map(app, mask_arr, well, fov, tp) or {}
        except Exception:  # noqa: BLE001
            above_by_nid = {}
        m = _np.asarray(mask_arr)
        passing = [nid for nid, ok in above_by_nid.items() if ok]
        bm = _np.isin(m, passing) if passing else _np.zeros(m.shape, dtype=bool)
    else:  # "mask"
        bm = _np.asarray(mask_arr) > 0
    bm = bm.astype(_np.uint8)
    if crop_tool is not None:
        bm = crop_tool.apply_to_array(bm)
    return bm


def _compose_image_table_rgb(
    arr,
    mask_arr,
    *,
    lo,
    hi,
    cmap_name,
    is_overlay: bool,
    binary_mask=None,
    show_boundaries: bool,
):
    """Render the cell's display state to a uint8 RGB array.

    Single source of truth for the image-table cell pixel content,
    consumed by both the live thumbnail path (which wraps the RGB
    into a QPixmap) and the export figure path (which ``imshow``s it
    directly). Honours the row's mpl colormap, the per-cell LUT range,
    ``show_boundaries``, and ``binary_mask`` — a precomputed array of the
    cells to paint white (the full segmentation mask for "Binary (mask)"
    or the above-threshold cells for "Binary (threshold)"), or None.

    Returns ``None`` when the input array can't be coerced — the
    caller falls back to whatever placeholder it shows for missing
    data.
    """
    import numpy as _np
    from matplotlib import colormaps as _mpl_cmaps

    if arr is None:
        return None

    a = _np.asarray(arr)

    # 1. Pre-rendered RGB overlays (NUC+SEG channel, or any 3-D input)
    # — pass-through with min/max normalisation so the imshow doesn't
    # auto-rescale.
    if is_overlay or a.ndim >= 3:
        rgb = a[:, :, :3] if a.ndim == 3 else a
        if rgb.dtype != _np.uint8:
            rgb_f = rgb.astype(_np.float32)
            rmin = float(rgb_f.min())
            rmax = float(rgb_f.max())
            if rmax <= rmin:
                rmax = rmin + 1.0
            rgb = ((_np.clip(rgb_f, rmin, rmax) - rmin)
                   / (rmax - rmin) * 255.0).astype(_np.uint8)
        return rgb

    # 2. Binary toggle wins over the fluorescence: paint a black-and-white
    # silhouette of the supplied cells (full mask, or above-threshold cells).
    if binary_mask is not None:
        cm = _np.asarray(binary_mask)
        binary = (cm > 0).astype(_np.uint8) * 255
        return _np.stack([binary, binary, binary], axis=-1)

    # 3. Greyscale fluorescence → apply LUT then colormap.
    af = a.astype(_np.float32)
    alo = lo if lo is not None else float(af.min())
    ahi = hi if hi is not None else float(af.max())
    if ahi <= alo:
        ahi = alo + 1.0
    norm = _np.clip((af - alo) / (ahi - alo), 0.0, 1.0)
    if cmap_name:
        try:
            rgb = (_mpl_cmaps[cmap_name](norm)[..., :3] * 255).astype(_np.uint8).copy()
        except Exception:
            gray = (norm * 255).astype(_np.uint8)
            rgb = _np.stack([gray, gray, gray], axis=-1)
    else:
        gray = (norm * 255).astype(_np.uint8)
        rgb = _np.stack([gray, gray, gray], axis=-1)

    # 4. Boundary overlay: paint white pixels at the segmentation
    # boundaries (same padded-diff scheme used by the live renderer).
    if show_boundaries and mask_arr is not None:
        center_int = _np.asarray(mask_arr, dtype=_np.int32)
        if center_int.shape[:2] == af.shape[:2]:
            padded = _np.pad(center_int, 1, mode="constant", constant_values=0)
            center = padded[1:-1, 1:-1]
            boundary = (center > 0) & (
                (center != padded[:-2, 1:-1]) |
                (center != padded[2:, 1:-1]) |
                (center != padded[1:-1, :-2]) |
                (center != padded[1:-1, 2:])
            )
            rgb[boundary] = [255, 255, 255]

    return rgb


def image_table_apply_global(app, field: str) -> None:
    """Copy a global dropdown's current value into every cell's matching combo.

    ``field`` is one of "chan", "tp", "fov". For "chan" the per-row channel
    combos are also synced and the LUT row is rebuilt so the host's per-
    channel min/max boxes track the new global channel.
    """
    if field == "chan":
        src = getattr(app, "_image_table_global_chan_cb", None)
        key = "chan_cb"
    elif field == "tp":
        src = getattr(app, "_image_table_global_tp_cb", None)
        key = "tp_cb"
    elif field == "fov":
        src = getattr(app, "_image_table_global_fov_cb", None)
        key = "fov_cb"
    else:
        return
    if src is None:
        return
    value = src.currentText()
    if not value:
        return
    cells = getattr(app, "_image_table_cells", None) or []
    for row in cells:
        for cell in row:
            cb = cell.get(key)
            if cb is None:
                continue
            cb.blockSignals(True)
            if cb.findText(value) >= 0:
                cb.setCurrentText(value)
            cb.blockSignals(False)
    if field == "chan":
        for cb in getattr(app, "_image_table_row_chan_cbs", None) or []:
            cb.blockSignals(True)
            if cb.findText(value) >= 0:
                cb.setCurrentText(value)
            cb.blockSignals(False)
        image_table_rebuild_lut_row(app)


def image_table_apply_row_well(app, row_idx: int) -> None:
    """Copy the per-row well selector's value into every cell in that row.

    Mirrors ``image_table_apply_row_channel`` for the well axis: lets the
    user pick a single well for an entire row without clicking each cell.
    """
    row_cbs = getattr(app, "_image_table_row_well_cbs", None) or []
    cells = getattr(app, "_image_table_cells", None) or []
    if not (0 <= row_idx < len(row_cbs)) or not (0 <= row_idx < len(cells)):
        return
    value = row_cbs[row_idx].currentText()
    if not value:
        return
    for cell in cells[row_idx]:
        cb = cell.get("well_cb")
        if cb is None:
            continue
        cb.blockSignals(True)
        if cb.findText(value) >= 0:
            cb.setCurrentText(value)
        cb.blockSignals(False)


def image_table_apply_row_channel(app, row_idx: int) -> None:
    """Copy the per-row channel selector's value into every cell in that row.

    Cell-level channel dropdowns remain usable for one-off overrides; this
    only fires when the per-row combo changes.
    """
    row_cbs = getattr(app, "_image_table_row_chan_cbs", None) or []
    cells = getattr(app, "_image_table_cells", None) or []
    if not (0 <= row_idx < len(row_cbs)) or not (0 <= row_idx < len(cells)):
        return
    value = row_cbs[row_idx].currentText()
    if not value:
        return
    for cell in cells[row_idx]:
        cb = cell.get("chan_cb")
        if cb is None:
            continue
        cb.blockSignals(True)
        if cb.findText(value) >= 0:
            cb.setCurrentText(value)
        cb.blockSignals(False)
    image_table_rebuild_lut_row(app)


def image_table_distribute_timepoints(app) -> None:
    """Walk the available timepoints and assign one to each column, per row.

    Each row gets the same timepoint sequence across its cells, so multi-row
    tables can compare different wells/channels at matching timepoints. Each
    row is anchored on its own first cell's well/channel/FOV (preserving any
    per-row well/channel selections made via ``Distribute Wells`` or the
    per-row dropdowns) so distinct rows keep their own well assignments.
    """
    cells = getattr(app, "_image_table_cells", None) or []
    if not cells:
        app._set_status("Image Table: build a grid first (set rows/cols).")
        return

    tps = _timepoint_options(app)
    if not tps:
        app._set_status("Image Table: no timepoints in pipeline_info.json.")
        return

    def _set(cb, text):
        if not text:
            return
        cb.blockSignals(True)
        try:
            if cb.findText(text) >= 0:
                cb.setCurrentText(text)
        finally:
            cb.blockSignals(False)

    assignments = 0
    for row in cells:
        if not row:
            continue
        anchor = row[0]
        anchor_well = anchor["well_cb"].currentText().strip()
        anchor_chan = anchor["chan_cb"].currentText().strip()
        anchor_fov = anchor["fov_cb"].currentText().strip()
        for c, cell in enumerate(row):
            if c >= len(tps):
                break
            _set(cell["well_cb"], anchor_well)
            _set(cell["chan_cb"], anchor_chan)
            _set(cell["fov_cb"], anchor_fov)
            _set(cell["tp_cb"], tps[c])
            assignments += 1

    image_table_rebuild_lut_row(app)
    app._set_status(
        f"Image Table: distributed {min(len(tps), len(cells[0]) if cells else 0)} "
        f"timepoint(s) across {len(cells)} row(s) ({assignments} cells filled)."
    )


# ── Smart layout builder ──────────────────────────────────────────────────────
#
# Declare what varies down the ROWS and across the COLUMNS (Channels / Wells /
# Timepoints / FOVs); one Build auto-sizes the grid to the selection and fills
# every cell, holding the off-axis dimensions fixed. This replaces the manual
# "set rows/cols, then assign every dropdown" workflow for the common views
# (channels × timepoints, channels × wells, wells × timepoints, …).

_AXIS_LABEL = {"channel": "Channels", "well": "Wells",
               "timepoint": "Timepoints", "fov": "FOVs"}
_AXIS_MAX = 12  # grid is capped at 12×12; longer axes are truncated


def _axis_values(app, axis: str) -> List[str]:
    """Ordered value list an axis expands into, drawn from the live selections."""
    if axis == "channel":
        opts = _channel_options(app)
        sel = getattr(app._app_state.image_table, "active_channels", None) or set()
        return [c for c in opts if c in sel] if sel else list(opts)
    if axis == "well":
        sel = sorted(getattr(app._app_state.image_table, "active_wells", set()) or set(),
                     key=app._parse_rc)
        return sel if sel else _well_options(app)
    if axis == "timepoint":
        return _timepoint_options(app)
    if axis == "fov":
        return _fov_options(app)
    return []


def _fixed_off_axis(app, on_axis: set) -> Dict[str, str]:
    """Pick a single held value for each dimension NOT on a row/col axis.

    Prefers the matching Global Options dropdown (so the user can change it and
    re-Build), else the first selected/available value."""
    def _global(attr: str) -> str:
        cb = getattr(app, attr, None)
        return cb.currentText().strip() if cb is not None else ""

    def _first(seq) -> str:
        return seq[0] if seq else ""

    fixed = {"well": "", "channel": "", "timepoint": "", "fov": ""}
    if "well" not in on_axis:
        sel = sorted(getattr(app._app_state.image_table, "active_wells", set()) or set(),
                     key=app._parse_rc)
        fixed["well"] = sel[0] if sel else _first(_well_options(app))
    if "channel" not in on_axis:
        fixed["channel"] = _global("_image_table_global_chan_cb") or _first(_axis_values(app, "channel"))
    if "timepoint" not in on_axis:
        fixed["timepoint"] = _global("_image_table_global_tp_cb") or _first(_timepoint_options(app))
    if "fov" not in on_axis:
        fixed["fov"] = _global("_image_table_global_fov_cb") or _first(_fov_options(app))
    return fixed


def _current_axis(app, which: str) -> str:
    cb = getattr(app, f"_image_table_{which}_axis_cb", None)
    if cb is not None:
        data = cb.currentData()
        if data:
            return str(data)
    # state fallback / default
    return getattr(app, f"_image_table_{which}_axis",
                   "channel" if which == "row" else "timepoint")


def _apply_row_channel_styling(app, channels: List[str]) -> None:
    """When rows are channels, mirror each channel onto its row-0 channel combo
    and give each row a distinct colormap so the channels read apart."""
    row_chan = getattr(app, "_image_table_row_chan_cbs", None) or []
    row_lut = getattr(app, "_image_table_row_lut_color_cbs", None) or []
    palette = [n for n in LUT_COLOR_NAMES if n != "Greys"] or LUT_COLOR_NAMES
    for r, chan in enumerate(channels):
        if r < len(row_chan) and row_chan[r] is not None:
            cb = row_chan[r]
            cb.blockSignals(True)
            if cb.findText(chan) >= 0:
                cb.setCurrentText(chan)
            cb.blockSignals(False)
        if palette and r < len(row_lut) and row_lut[r] is not None and hasattr(row_lut[r], "setLut"):
            sel = row_lut[r]
            sel.blockSignals(True)
            try:
                sel.setLut(palette[r % len(palette)])
            except Exception:  # noqa: BLE001 — colormap styling is best-effort
                swallowed()  # was: pass (silent except)
            sel.blockSignals(False)


def _layout_cell_values(row_axis, col_axis, row_vals, col_vals, fixed, r, c) -> Dict[str, str]:
    """Per-cell (well, channel, timepoint, fov) for the smart layout: off-axis
    dims held at *fixed*, the row axis driven by *r*, the col axis by *c*."""
    vals = dict(fixed)
    vals[row_axis] = row_vals[r]
    vals[col_axis] = col_vals[c]
    return vals


def image_table_build_layout(app) -> None:
    """Auto-size the grid to the chosen row/col axes and fill every cell."""
    row_axis = _current_axis(app, "row")
    col_axis = _current_axis(app, "col")
    if row_axis == col_axis:
        app._set_status("Image Table: choose two different axes for rows and columns.")
        return

    row_vals = _axis_values(app, row_axis)
    col_vals = _axis_values(app, col_axis)
    if not row_vals:
        app._set_status(f"Image Table: nothing on the {_AXIS_LABEL[row_axis]} axis "
                        f"(select some in the sidebar).")
        return
    if not col_vals:
        app._set_status(f"Image Table: nothing on the {_AXIS_LABEL[col_axis]} axis "
                        f"(select some in the sidebar).")
        return

    row_vals = row_vals[:_AXIS_MAX]
    col_vals = col_vals[:_AXIS_MAX]
    rows, cols = len(row_vals), len(col_vals)

    # Size the grid (sync the spinners) and rebuild the empty selector cells.
    app._app_state.image_table.rows = rows
    app._app_state.image_table.cols = cols
    for attr, val in (("_image_table_rows_spin", rows), ("_image_table_cols_spin", cols)):
        spin = getattr(app, attr, None)
        if spin is not None:
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)
    image_table_rebuild_grid(app)

    fixed = _fixed_off_axis(app, {row_axis, col_axis})
    key_of = {"well": "well_cb", "channel": "chan_cb",
              "timepoint": "tp_cb", "fov": "fov_cb"}

    def _set(cb, text):
        if cb is None or not text:
            return
        cb.blockSignals(True)
        try:
            if cb.findText(text) >= 0:
                cb.setCurrentText(text)
        finally:
            cb.blockSignals(False)

    cells = getattr(app, "_image_table_cells", None) or []
    for r, row in enumerate(cells):
        for c, cell in enumerate(row):
            vals = _layout_cell_values(row_axis, col_axis, row_vals, col_vals, fixed, r, c)
            for dim, ckey in key_of.items():
                _set(cell.get(ckey), vals[dim])

    if row_axis == "channel":
        _apply_row_channel_styling(app, row_vals)

    image_table_rebuild_lut_row(app)
    app._set_status(
        f"Image Table: built {rows}×{cols} selector grid — rows = {_AXIS_LABEL[row_axis]}, "
        f"cols = {_AXIS_LABEL[col_axis]}. Press Generate to render."
    )


def image_table_apply_preset(app, row_axis: str, col_axis: str) -> None:
    """Set the row/col axis dropdowns to a common pairing, then Build."""
    for which, axis in (("row", row_axis), ("col", col_axis)):
        cb = getattr(app, f"_image_table_{which}_axis_cb", None)
        if cb is not None:
            idx = cb.findData(axis)
            cb.blockSignals(True)
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            cb.blockSignals(False)
        setattr(app, f"_image_table_{which}_axis", axis)
    image_table_build_layout(app)


# ── Channel multi-select (sidebar) ────────────────────────────────────────────


def _update_channel_btn_text(app) -> None:
    """Reflect the active-channel set on the layout-row dropdown button."""
    btn = getattr(app, "_image_table_channel_btn", None)
    if btn is None:
        return
    opts = _channel_options(app)
    sel = getattr(app._app_state.image_table, "active_channels", None) or set()
    ordered = [c for c in opts if c in sel]
    if not ordered or len(ordered) == len(opts):
        btn.setText("Channels: all")
    elif len(ordered) <= 2:
        btn.setText("Channels: " + ", ".join(ordered))
    else:
        btn.setText(f"Channels: {len(ordered)} selected")


def image_table_refresh_channel_picker(app) -> None:
    """Rebuild the layout-row channel dropdown menu from the dataset's
    channels, preserving valid prior selections."""
    from PySide6.QtGui import QAction

    menu = getattr(app, "_image_table_channel_menu", None)
    if menu is None:
        return
    opts = _channel_options(app)
    active = getattr(app._app_state.image_table, "active_channels", None) or set()
    app._app_state.image_table.active_channels = {c for c in active if c in opts}

    menu.clear()
    act_all = menu.addAction("Select all")
    act_all.triggered.connect(lambda _=False: image_table_set_all_channels(app, True))
    act_none = menu.addAction("Clear")
    act_none.triggered.connect(lambda _=False: image_table_set_all_channels(app, False))
    menu.addSeparator()
    for ch in opts:
        act = QAction(ch, menu)
        act.setCheckable(True)
        # Set state BEFORE wiring so building the menu doesn't fire toggled.
        act.setChecked(ch in app._app_state.image_table.active_channels)
        act.toggled.connect(lambda checked, c=ch: image_table_toggle_channel(app, c, checked))
        menu.addAction(act)
    _update_channel_btn_text(app)


def image_table_toggle_channel(app, channel: str, checked: bool) -> None:
    """Add/remove a single channel from the active set (menu item toggled)."""
    sel = set(getattr(app._app_state.image_table, "active_channels", None) or set())
    if checked:
        sel.add(channel)
    else:
        sel.discard(channel)
    app._app_state.image_table.active_channels = sel
    _update_channel_btn_text(app)


def image_table_set_all_channels(app, on: bool) -> None:
    """Select-all / clear from the dropdown header actions."""
    app._app_state.image_table.active_channels = set(_channel_options(app)) if on else set()
    image_table_refresh_channel_picker(app)  # rebuild to refresh checkmarks


# ── Add row (clone an existing row, vary one dimension) ───────────────────────


def _all_dim_options(app, dim: str) -> List[str]:
    """Every available value for a dimension (not selection-filtered)."""
    if dim == "channel":
        return _channel_options(app)
    if dim == "well":
        return _well_options(app)
    if dim == "timepoint":
        return _timepoint_options(app)
    if dim == "fov":
        return _fov_options(app)
    return []


_DIM_KEY = {"well": "well_cb", "channel": "chan_cb",
            "timepoint": "tp_cb", "fov": "fov_cb"}


def _snapshot_cells(app) -> List[List[Dict[str, str]]]:
    """Capture every cell's (well, channel, timepoint, fov) selections."""
    snap: List[List[Dict[str, str]]] = []
    for row in (getattr(app, "_image_table_cells", None) or []):
        snap.append([
            {dim: cell[key].currentText().strip() for dim, key in _DIM_KEY.items()}
            for cell in row
        ])
    return snap


def _write_cell(cell, vals: Dict[str, str]) -> None:
    for dim, key in _DIM_KEY.items():
        cb = cell.get(key)
        txt = vals.get(dim, "")
        if cb is None or not txt:
            continue
        cb.blockSignals(True)
        try:
            if cb.findText(txt) >= 0:
                cb.setCurrentText(txt)
        finally:
            cb.blockSignals(False)


def image_table_add_varied_row(app, *, src_idx: int, dim: str, mode: str, value: str) -> None:
    """Append a new row cloned from row *src_idx*, varying one dimension.

    Uniform mode sets *dim* to *value* across the whole new row; Step mode
    advances each cell's value for *dim* by one (clamped to the last available
    value). Other dimensions are copied as-is. Builds the selector row only —
    press Generate to render. Driven by :func:`image_table_open_add_row_dialog`.
    """
    cells = getattr(app, "_image_table_cells", None) or []
    if not cells:
        app._set_status("Image Table: build a table first, then add a row.")
        return
    dim = dim or "channel"
    mode = mode or "uniform"
    value = (value or "").strip()

    snap = _snapshot_cells(app)
    if not (0 <= src_idx < len(snap)):
        src_idx = len(snap) - 1
    src_row = snap[src_idx]
    opts = _all_dim_options(app, dim)

    new_row: List[Dict[str, str]] = []
    for cell_vals in src_row:
        nv = dict(cell_vals)
        if mode == "uniform":
            if value:
                nv[dim] = value
        else:  # step: advance this cell's value by one
            cur = cell_vals.get(dim, "")
            if opts:
                try:
                    i = opts.index(cur)
                except ValueError:
                    i = -1
                nv[dim] = opts[min(i + 1, len(opts) - 1)] if i >= 0 else opts[0]
        new_row.append(nv)

    # Grow the grid by one row, rebuild the empty cells, then restore the old
    # rows plus the freshly-built one.
    new_rows = len(snap) + 1
    app._app_state.image_table.rows = new_rows
    spin = getattr(app, "_image_table_rows_spin", None)
    if spin is not None:
        spin.blockSignals(True)
        spin.setValue(new_rows)
        spin.blockSignals(False)
    image_table_rebuild_grid(app)

    full = snap + [new_row]
    for r, row in enumerate(getattr(app, "_image_table_cells", None) or []):
        if r >= len(full):
            break
        for c, cell in enumerate(row):
            if c < len(full[r]):
                _write_cell(cell, full[r][c])

    # When the varied dimension is the channel and the whole new row shares one
    # channel (uniform), mirror it onto the row's channel combo + colormap.
    if dim == "channel" and mode == "uniform" and value:
        _apply_row_channel_styling_for_row(app, new_rows - 1, value)

    image_table_rebuild_lut_row(app)
    verb = f"set {dim} = {value}" if mode == "uniform" else f"stepped {dim}"
    app._set_status(
        f"Image Table: added row {new_rows} cloned from row {src_idx + 1} "
        f"({verb}). Press Generate to render."
    )


def image_table_open_add_row_dialog(app) -> None:
    """Modal dialog to clone an existing row, varying one dimension.

    Replaces the inline Add-row combo strip — collects source row / dimension /
    mode / value here (value list is per-dimension, enabled only in Uniform
    mode) and calls :func:`image_table_add_varied_row`.
    """
    from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout

    cells = getattr(app, "_image_table_cells", None) or []
    if not cells:
        app._set_status("Image Table: build a table first, then add a row.")
        return

    dlg = QDialog(app)
    dlg.setWindowTitle("Image Table — Add row")
    form = QFormLayout(dlg)

    src_cb = QComboBox(dlg)
    for i in range(len(cells)):
        src_cb.addItem(f"Row {i + 1}", i)
    src_cb.setCurrentIndex(src_cb.count() - 1)
    src_cb.setToolTip("Existing row to clone.")
    form.addRow("Clone row:", src_cb)

    dim_cb = QComboBox(dlg)
    for label, key in (("Channel", "channel"), ("Timepoint", "timepoint"),
                       ("FOV", "fov"), ("Well", "well")):
        dim_cb.addItem(label, key)
    form.addRow("Vary:", dim_cb)

    mode_cb = QComboBox(dlg)
    mode_cb.addItem("Uniform → one value", "uniform")
    mode_cb.addItem("Step +1 per cell", "step")
    mode_cb.setToolTip(
        "Uniform: set the chosen dimension to one value across the new row.\n"
        "Step +1: advance each cell's value for that dimension by one."
    )
    form.addRow("Mode:", mode_cb)

    val_cb = QComboBox(dlg)
    val_cb.setToolTip("New value (Uniform mode only).")
    form.addRow("Value:", val_cb)

    def _refresh_values() -> None:
        dim = dim_cb.currentData() or "channel"
        prev = val_cb.currentText().strip()
        val_cb.blockSignals(True)
        val_cb.clear()
        opts = _all_dim_options(app, dim)
        val_cb.addItems(opts)
        if prev in opts:
            val_cb.setCurrentText(prev)
        val_cb.blockSignals(False)
        val_cb.setEnabled((mode_cb.currentData() or "uniform") == "uniform")

    dim_cb.currentIndexChanged.connect(lambda _i: _refresh_values())
    mode_cb.currentIndexChanged.connect(lambda _i: _refresh_values())
    _refresh_values()

    btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    form.addRow(btns)

    if dlg.exec() != QDialog.Accepted:
        return
    image_table_add_varied_row(
        app,
        src_idx=int(src_cb.currentData() or 0),
        dim=str(dim_cb.currentData() or "channel"),
        mode=str(mode_cb.currentData() or "uniform"),
        value=val_cb.currentText().strip(),
    )


def _apply_row_channel_styling_for_row(app, row_idx: int, channel: str) -> None:
    """Set one row's channel combo + a distinct colormap (used by Add-row)."""
    row_chan = getattr(app, "_image_table_row_chan_cbs", None) or []
    row_lut = getattr(app, "_image_table_row_lut_color_cbs", None) or []
    palette = [n for n in LUT_COLOR_NAMES if n != "Greys"] or LUT_COLOR_NAMES
    if 0 <= row_idx < len(row_chan) and row_chan[row_idx] is not None:
        cb = row_chan[row_idx]
        cb.blockSignals(True)
        if cb.findText(channel) >= 0:
            cb.setCurrentText(channel)
        cb.blockSignals(False)
    if palette and 0 <= row_idx < len(row_lut) and row_lut[row_idx] is not None \
            and hasattr(row_lut[row_idx], "setLut"):
        sel = row_lut[row_idx]
        sel.blockSignals(True)
        try:
            sel.setLut(palette[row_idx % len(palette)])
        except Exception:  # noqa: BLE001
            swallowed()  # was: pass (silent except)
        sel.blockSignals(False)


def image_table_toggle_tophat(app) -> None:
    """Flip the raw/tophat source flag and re-render.

    Bound to the Display ▾ menu's checkable Tophat action. When ON,
    ``_load_array`` reads from the pre-filtered tophat dict; when OFF, it reads
    raw fluor as before.
    """
    app._app_state.image_table.use_tophat = not bool(getattr(app._app_state.image_table, "use_tophat", False))
    image_table_refresh_display_menu(app)
    # Tossing the cache so the next Generate fetches the right dict.
    app._app_state.image_table.image_cache = {}
    image_table_generate(app)


def image_table_load_heatmap_layout(app) -> None:
    """Adopt the Heat Map tab's well configuration (rows, cols, per-cell well).

    Pulls the single sidebar layout maintained by the heat-map tab
    (``views.heatmap_layout_sidebar_view``), resizes the Image Table's
    selector grid to match, and writes each cell's well into the
    corresponding per-cell well dropdown. Cells that the heat map leaves
    empty are cleared on the Image Table side — otherwise they would stick
    on the first well token (the ``QComboBox.addItems`` default of A1)
    that ``image_table_rebuild_grid`` had just put there.
    """
    from well_viewer.features.heatmap.sidebar_view import (
        SIDEBAR_LAYOUT_NAME,
    )

    layouts = list(getattr(app, "_heatmap_layouts", []) or [])
    layout = next((l for l in layouts if l.name == SIDEBAR_LAYOUT_NAME), None)
    if layout is None:
        app._set_status("Image Table: no Heat Map layout to load.")
        return

    rows_spin = getattr(app, "_image_table_rows_spin", None)
    cols_spin = getattr(app, "_image_table_cols_spin", None)
    if rows_spin is None or cols_spin is None:
        return

    target_rows = min(rows_spin.maximum(), max(rows_spin.minimum(), int(layout.rows)))
    target_cols = min(cols_spin.maximum(), max(cols_spin.minimum(), int(layout.cols)))
    clamped = (target_rows != int(layout.rows)) or (target_cols != int(layout.cols))

    # Update spinners without triggering the rebuild twice; we call
    # image_table_apply_dimensions explicitly below so rows/cols + grid
    # land in sync regardless of whether the spinner values actually changed.
    for spin, val in ((rows_spin, target_rows), (cols_spin, target_cols)):
        blocked = spin.blockSignals(True)
        try:
            spin.setValue(val)
        finally:
            spin.blockSignals(blocked)
    app._app_state.image_table.rows = target_rows
    app._app_state.image_table.cols = target_cols
    image_table_rebuild_grid(app)

    cells = getattr(app, "_image_table_cells", None) or []

    # Clear every freshly-rebuilt cell's well first so heat-map blanks
    # come through as blanks instead of the A1 default.
    for row in cells:
        for cell in row:
            cb = cell.get("well_cb")
            if cb is None:
                continue
            cb.blockSignals(True)
            try:
                cb.setCurrentIndex(-1)
            finally:
                cb.blockSignals(False)

    placed = 0
    for (r, c), wells in layout.cells.items():
        if not (0 <= r < len(cells)) or not (0 <= c < len(cells[r])):
            continue
        token = (wells[0] if wells else "").strip()
        if not token:
            continue
        cb = cells[r][c].get("well_cb")
        if cb is None:
            continue
        cb.blockSignals(True)
        try:
            if cb.findText(token) >= 0:
                cb.setCurrentText(token)
                placed += 1
        finally:
            cb.blockSignals(False)

    msg = (
        f"Image Table: loaded Heat Map layout ({target_rows}×{target_cols}, "
        f"{placed} well(s) placed)."
    )
    if clamped:
        msg += (
            f" Clamped from {int(layout.rows)}×{int(layout.cols)} to fit "
            f"the Image Table grid limits."
        )
    app._set_status(msg)


def image_table_distribute_wells(app) -> None:
    """Walk active wells row-wise and assign one to each selector cell.

    Special case: when the table has exactly twice as many cells as there
    are active wells, the assignment is duplicated — each well appears in
    two cells (typically the top half then the bottom half, when the
    table is wider than tall). This is convenient for laying out two
    rows of complementary channels for the same set of wells.
    """
    cells = getattr(app, "_image_table_cells", None) or []
    if not cells:
        app._set_status("Image Table: build a grid first (set rows/cols and click Apply).")
        return
    active = sorted(app._app_state.image_table.active_wells, key=app._parse_rc)
    if not active:
        app._set_status("Image Table: no active wells in the sidebar picker.")
        return

    flat_cells = [(r, c, cell) for r, row in enumerate(cells) for c, cell in enumerate(row)]
    total_cells = len(flat_cells)
    n_active = len(active)
    duplicated = total_cells == 2 * n_active

    assignments = 0
    for flat_idx, (_r, _c, cell) in enumerate(flat_cells):
        if duplicated:
            tok = active[flat_idx % n_active]
        elif flat_idx < n_active:
            tok = active[flat_idx]
        else:
            break
        cb = cell["well_cb"]
        cb.blockSignals(True)
        if cb.findText(tok) >= 0:
            cb.setCurrentText(tok)
        cb.blockSignals(False)
        assignments += 1

    if duplicated:
        app._set_status(
            f"Image Table: distributed {n_active} well(s) twice "
            f"({assignments} cells filled) for complementary-channel layout."
        )
    else:
        app._set_status(
            f"Image Table: distributed {assignments} well(s) into the selector grid."
        )


# ── LUT row ──────────────────────────────────────────────────────────────────


def _assigned_channels(app) -> List[str]:
    """Channels currently selected in any cell of the image-table grid.

    Order follows ``_channel_options`` so the LUT row stays stable as the
    user edits assignments. Empty when no grid has been built yet.
    """
    cells = getattr(app, "_image_table_cells", None) or []
    used: set[str] = set()
    for row in cells:
        for cell in row:
            cb = cell.get("chan_cb")
            if cb is None:
                continue
            text = (cb.currentText() or "").strip().upper()
            if text:
                used.add(text)
    return [c for c in _channel_options(app) if c in used]


def image_table_rebuild_lut_row(app) -> None:
    """Rebuild the per-channel LUT editors (one min/max pair per channel).

    Only the channels currently assigned to a cell in the grid get a LUT
    box; unused channels are hidden.
    """
    from well_viewer.features.image_table.grid_view import build_lut_row

    build_lut_row(
        app,
        chan_opts=_assigned_channels(app),
        on_generate=image_table_generate,
        on_auto_lut=image_table_auto_lut,
    )


def _parse_lut(app, chan: str) -> Tuple[Optional[float], Optional[float]]:
    """Read (lo, hi) for a channel; returns (None, None) when 'auto'."""
    luts = getattr(app, "_image_table_lut", None) or {}
    entry = luts.get(chan.upper())
    if not entry:
        return None, None

    def _val(le: QLineEdit) -> Optional[float]:
        text = (le.text() or "").strip().lower()
        if not text or text == "auto":
            return None
        try:
            return float(text)
        except ValueError:
            return None

    return _val(entry["min"]), _val(entry["max"])


# ── Image loading + caching ──────────────────────────────────────────────────


def _load_well_channel(
    app,
    cache: Dict[Tuple[str, str, str], Dict],
    well: str,
    chan_lower: str,
    *,
    use_tophat: bool = False,
) -> Dict:
    """Return the (fov, tp) → ImgRef dict for (well, channel), with caching.

    For the special ``"nuc+seg"`` virtual channel the returned dict is the
    pre-rendered segmentation overlay refs keyed the same way; everything
    else returns the fluorescence dict for the requested channel token.

    When ``use_tophat`` is True, the pre-filtered tophat dict is returned
    instead. Tophat is unavailable for the overlay channel — it falls back
    to the raw fluor lookup. Cells whose (fov, tp) are missing from the
    tophat dict also fall back to raw at lookup time (handled in
    ``_load_array``).
    """
    from well_viewer.data.image_discovery import find_well_images_and_masks

    is_overlay = chan_lower == NUC_SEG_TOKEN.lower()
    variant = "tophat" if (use_tophat and not is_overlay) else "raw"
    key = (well, chan_lower, variant)
    if key in cache:
        return cache[key]
    info = app._app_state.dataset.pipeline_info or {}
    # Overlay images aren't keyed by a fluor channel; pass the nuclear token
    # as a sensible default so any token-based filtering inside the loader
    # has a real channel to match. The returned ``overlay`` dict comes from
    # the pipeline's pre-rendered segmentation PNGs.
    fluor_token = chan_lower
    if is_overlay:
        fluor_token = (info.get("nuclear_token") or chan_lower).strip().lower()
    try:
        fluor, overlay, mask, tophat = find_well_images_and_masks(
            app._app_state.dataset.data_dir,
            well,
            fluor_token=fluor_token,
            in_dir=app._app_state.dataset.in_dir,
            _fov_tp_extractor=app._app_state.dataset.fov_tp_extractor,
            _pipeline_info=app._app_state.dataset.pipeline_info,
        )
    except Exception:
        fluor, overlay, mask, tophat = {}, {}, {}, {}
    # Cache the mask dict (keyed by a sentinel channel token) so
    # boundary/binary overlays can access it without a second IO round-trip.
    cache.setdefault((well, "__mask__", "raw"), mask)
    if is_overlay:
        result = overlay
    elif use_tophat:
        result = tophat
    else:
        result = fluor
    cache[key] = result
    # Stash the raw fluor dict alongside the tophat dict so _load_array can
    # fall back to raw for any (fov, tp) the tophat scan missed.
    if use_tophat and not is_overlay:
        cache.setdefault((well, chan_lower, "raw"), fluor)
    return result


def _load_array(app, cache: Dict, well: str, chan_upper: str, tp: str, fov: str, *, use_tophat: bool = False):
    """Load the float32 array for one (well, channel, timepoint, fov).

    The dropdown emits tokens normalized via ``_norm_token`` (``"1.0"`` →
    ``"1"``) but the cache built by ``find_well_images_and_masks`` is keyed
    on RAW filename tokens via ``make_schema_extractor``. Try the direct
    tuple first, then fall back to a normalized comparison against every
    cache key so the lookup succeeds whichever side ends up holding the
    integer-as-float form.

    When ``fov`` is empty (e.g. pipeline_info has no available_fovs and the
    dropdown is empty), match the first available FOV for the requested
    timepoint so single-FOV datasets still render.
    """
    chan_lower = chan_upper.strip().lower()
    fluor = _load_well_channel(app, cache, well, chan_lower, use_tophat=use_tophat)
    # When tophat is requested but not produced for this well/channel, drop
    # back to raw rather than rendering "(no image)".
    if (use_tophat and not fluor and chan_lower != NUC_SEG_TOKEN.lower()):
        fluor = _load_well_channel(app, cache, well, chan_lower, use_tophat=False)
    if not fluor:
        return None

    ref = fluor.get((str(fov), str(tp)))
    if ref is None:
        target_fov = _norm_token(fov)
        target_tp = _norm_token(tp)
        for (cache_fov, cache_tp), val in fluor.items():
            tp_match = (not target_tp) or _norm_token(cache_tp) == target_tp
            fov_match = (not target_fov) or _norm_token(cache_fov) == target_fov
            if tp_match and fov_match:
                ref = val
                break
    if ref is None:
        # Final fallback: numeric-only match. Cache keys like ``"T01"`` and
        # dropdown values like ``"1"`` both reduce to integer 1 here, which
        # rescues runs where pipeline_info.json's available_timepoints were
        # stored numerically while the on-disk filenames carry a ``T``
        # prefix (or vice versa).
        target_fov_n = _numeric_token(fov)
        target_tp_n = _numeric_token(tp)

        def _close(a, b) -> bool:
            return a is not None and b is not None and abs(a - b) < 1e-6

        for (cache_fov, cache_tp), val in fluor.items():
            tp_n = _numeric_token(cache_tp)
            fov_n = _numeric_token(cache_fov)
            tp_match = target_tp_n is None or _close(tp_n, target_tp_n)
            fov_match = target_fov_n is None or _close(fov_n, target_fov_n)
            if tp_match and fov_match:
                ref = val
                break
    if ref is None:
        return None
    try:
        from well_viewer.data.image_discovery import open_imgref_as_array
        # Overlay PNGs are RGB — load them in colour so the segmentation
        # outline survives. Fluor channels load greyscale as before.
        greyscale = not _is_nuc_seg(chan_upper)
        return open_imgref_as_array(ref, greyscale=greyscale)
    except Exception:
        return None


def _load_mask_array(app, cache, well, tp, fov):
    """Return the int32 segmentation mask for (well, tp, fov), or None.

    The mask dict is populated as a side effect of any prior ``_load_array``
    call for the same well, so this will usually hit the cache immediately.
    """
    mask_dict = cache.get((well, "__mask__", "raw"))
    if not mask_dict:
        return None
    ref = mask_dict.get((str(fov), str(tp)))
    if ref is None:
        target_fov = _norm_token(fov)
        target_tp = _norm_token(tp)
        for (cache_fov, cache_tp), val in mask_dict.items():
            tp_match = (not target_tp) or _norm_token(cache_tp) == target_tp
            fov_match = (not target_fov) or _norm_token(cache_fov) == target_fov
            if tp_match and fov_match:
                ref = val
                break
    if ref is None:
        return None
    try:
        import numpy as _np
        from well_viewer.data.image_discovery import open_imgref_as_array
        arr = open_imgref_as_array(ref, greyscale=True)
        if arr is None:
            return None
        return _np.rint(_np.asarray(arr)).astype(_np.int32)
    except Exception:
        return None


def _load_ratio_array(app, cache, well, ratio, tp, fov, *, use_tophat):
    """Compute the pixel-wise ratio for a RatioMetric.  Returns float32 or None."""
    try:
        import numpy as _np
    except Exception:
        return None
    num_chan = (ratio.numerator_channel or "").strip()
    den_chan = (ratio.denominator_channel or "").strip()
    if not num_chan or not den_chan:
        return None
    num_arr = _load_array(app, cache, well, num_chan.upper(), tp, fov, use_tophat=use_tophat)
    den_arr = _load_array(app, cache, well, den_chan.upper(), tp, fov, use_tophat=use_tophat)
    if num_arr is None or den_arr is None:
        return None
    num_f = _np.asarray(num_arr, dtype=_np.float32)
    den_f = _np.asarray(den_arr, dtype=_np.float32)
    eps = float(ratio.epsilon) if getattr(ratio, "epsilon", None) else 1e-6
    return num_f / (den_f + eps)


def _render_with_boundary_overlay(arr_2d, sz_w, sz_h, lo, hi, cmap_name, mask_arr):
    """Render a greyscale/ratio array with white cell-boundary outlines.

    Applies the colormap and LUT to *arr_2d*, then paints white pixels
    wherever the boundary of *mask_arr* falls (same padded-diff approach used
    in the review image renderer). Returns a QPixmap or None on failure.
    """
    from well_viewer.core.ui_helpers import (
        lut_colormap_to_rgb, make_fluor_thumb, make_overlay_thumb,
    )
    try:
        import numpy as _np
    except Exception:
        return make_fluor_thumb(arr_2d, sz_w, sz_h, lo, hi, cmap=cmap_name)

    arr = _np.asarray(arr_2d, dtype=_np.float32)
    rgb = lut_colormap_to_rgb(arr, lo, hi, cmap_name)

    center_int = _np.asarray(mask_arr, dtype=_np.int32)
    if center_int.shape[:2] == arr.shape[:2]:
        padded = _np.pad(center_int, 1, mode="constant", constant_values=0)
        center = padded[1:-1, 1:-1]
        boundary = (center > 0) & (
            (center != padded[:-2, 1:-1]) |
            (center != padded[2:, 1:-1]) |
            (center != padded[1:-1, :-2]) |
            (center != padded[1:-1, 2:])
        )
        rgb[boundary] = [255, 255, 255]

    return make_overlay_thumb(rgb, sz_w, sz_h, None, None)


def image_table_toggle_boundaries(app) -> None:
    """Flip the boundary-overlay flag and re-render (Display ▾ menu action)."""
    app._app_state.image_table.show_boundaries = not bool(
        getattr(app._app_state.image_table, "show_boundaries", False)
    )
    image_table_refresh_display_menu(app)
    app._app_state.image_table.image_cache = {}
    image_table_generate(app)


def image_table_refresh_display_menu(app) -> None:
    """Sync the Display ▾ menu's QAction checked states from app state.

    Driven from the toggle/binary handlers and from view_state restore so the
    menu always mirrors ``_image_table_use_tophat`` / ``_show_boundaries`` /
    ``_image_table_binary_mode`` (signals blocked to avoid re-entrancy)."""
    def _set(attr, checked) -> None:
        act = getattr(app, attr, None)
        if act is not None and hasattr(act, "setChecked"):
            blocked = act.blockSignals(True)
            try:
                act.setChecked(bool(checked))
            finally:
                act.blockSignals(blocked)

    _set("_image_table_tophat_act", getattr(app._app_state.image_table, "use_tophat", False))
    _set("_image_table_boundaries_act", getattr(app._app_state.image_table, "show_boundaries", False))
    mode = getattr(app._app_state.image_table, "binary_mode", "") or ""
    _set("_image_table_binary_act_none", mode == "")
    _set("_image_table_binary_act_mask", mode == "mask")
    _set("_image_table_binary_act_thresh", mode == "threshold")


_ZOOM_MIN = 0.33
_ZOOM_MAX = 4.0


def image_table_set_zoom(app, factor: float, *, relative: bool = False) -> None:
    """Set (or step) the rendered-table zoom factor and re-render.

    ``relative=True`` multiplies the current zoom by *factor* (zoom in/out
    buttons); otherwise *factor* is the absolute level (1.0 = 240 px cells).
    """
    cur = float(getattr(app._app_state.image_table, "zoom", 1.0) or 1.0)
    new = cur * factor if relative else factor
    new = max(_ZOOM_MIN, min(_ZOOM_MAX, new))
    app._app_state.image_table.zoom = new
    lbl = getattr(app, "_image_table_zoom_lbl", None)
    if lbl is not None:
        lbl.setText(f"{new * 100:.0f}%")
    # Only re-render if a table has already been generated; otherwise just
    # store the level for the next Generate.
    if getattr(app._app_state.image_table, "last_render", None):
        image_table_generate(app)


def image_table_set_binary_mode(app, mode: str) -> None:
    """Set the binary display mode and re-render.

    ``mode`` is one of:
      * ``""``         — off (normal fluorescence / overlay).
      * ``"mask"``     — every segmented cell drawn white on black.
      * ``"threshold"``— only cells above the active ON threshold drawn white
        (reuses the Review Image gating logic via
        ``review_build_threshold_map``).

    Driven by the Display ▾ menu's exclusive Binary radio group (None / Mask /
    ≥ Threshold), so the mode is set absolutely rather than toggled.
    """
    if mode not in ("", "mask", "threshold"):
        return
    app._app_state.image_table.binary_mode = mode
    image_table_refresh_display_menu(app)
    image_table_generate(app)


# ── Per-cell mouse handling: hover pixel readout + crop drag ────────────────


def _format_pixel_intensity(arr, y: int, x: int) -> str:
    """Format the intensity of pixel ``(y, x)`` in ``arr`` for the hover label.

    Greyscale arrays render as a single number; RGB / RGBA overlays render
    as comma-separated channels. Integer-typed arrays print as ints, floats
    as 4-significant-figure decimals so 16-bit microscopy data and 0..1
    floats both stay readable.
    """
    try:
        import numpy as np
    except Exception:
        return ""
    a = np.asarray(arr)
    h, w = a.shape[:2]
    if not (0 <= y < h and 0 <= x < w):
        return ""
    px = a[y, x]
    is_int = np.issubdtype(a.dtype, np.integer)

    def _fmt(v) -> str:
        if is_int:
            return str(int(v))
        try:
            return f"{float(v):.4g}"
        except Exception:
            return str(v)

    if a.ndim == 2:
        return _fmt(px)
    return ",".join(_fmt(v) for v in np.atleast_1d(px).tolist())


def _install_cell_events(
    app, label, crop_tool, well: str, chan: str, tp: str, fov: str,
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


def _render_mask_binary(app, cache, well, tp, fov, crop_tool, sz):
    """White-on-black image of every segmented cell (full mask)."""
    from well_viewer.core.ui_helpers import make_overlay_thumb
    import numpy as _np

    mask_arr = _load_mask_array(app, cache, well, tp, fov)
    if mask_arr is None:
        return None
    cm = crop_tool.apply_to_array(mask_arr) if crop_tool else mask_arr
    binary = (_np.asarray(cm) > 0).astype(_np.uint8) * 255
    rgb = _np.stack([binary, binary, binary], axis=-1)
    return make_overlay_thumb(rgb, sz, sz, None, None)


def _render_threshold_binary(app, cache, well, chan, tp, fov, crop_tool, sz):
    """White-on-black image of only the cells above the active ON threshold.

    Reuses the Review Image gating logic (``review_build_threshold_map``) so
    the "above threshold" definition matches the bar plot / review tab exactly
    (cell-area gating + every fluor gate + value-column > ON threshold). That
    helper also emits the ``[binary-mask]`` diagnostic line.
    """
    from well_viewer.core.ui_helpers import make_overlay_thumb
    import numpy as _np

    mask_arr = _load_mask_array(app, cache, well, tp, fov)
    if mask_arr is None:
        return None
    above_by_nid: Dict[int, bool] = {}
    try:
        from well_viewer.features.review_image.controller import review_build_threshold_map
        above_by_nid = review_build_threshold_map(app, mask_arr, well, fov, tp) or {}
    except Exception:  # noqa: BLE001 — fall back to an all-off mask on any error
        above_by_nid = {}
    m = _np.asarray(mask_arr)
    passing = [nid for nid, ok in above_by_nid.items() if ok]
    binary = _np.isin(m, passing) if passing else _np.zeros(m.shape, dtype=bool)
    b = binary.astype(_np.uint8) * 255
    cm = crop_tool.apply_to_array(b) if crop_tool else b
    rgb = _np.stack([cm, cm, cm], axis=-1)
    return make_overlay_thumb(rgb, sz, sz, None, None)


def _refresh_lut_fields_from_render(app, rendered) -> None:
    """Pool the actual min/max per channel from the just-rendered arrays and
    write the numbers into the LUT boxes, except rows the user has overridden.

    A channel's entry carries an ``auto`` flag (True until the user edits its
    min/max); only auto rows get refreshed, so manual overrides stick.
    """
    luts = getattr(app, "_image_table_lut", None) or {}
    cells = getattr(app, "_image_table_cells", None) or []
    if not luts or not cells:
        return
    try:
        import numpy as _np
    except Exception:
        return

    pooled: Dict[str, Tuple[float, float]] = {}
    for r, row in enumerate(cells):
        for c, cell in enumerate(row):
            arr = rendered.get((r, c))
            if arr is None or getattr(arr, "ndim", 2) >= 3:
                continue  # skip empty cells and pre-rendered RGB overlays
            chan = cell["chan_cb"].currentText().strip().upper()
            if not chan:
                continue
            a = _np.asarray(arr, dtype=_np.float32)
            mn, mx = float(a.min()), float(a.max())
            if chan in pooled:
                p0, p1 = pooled[chan]
                pooled[chan] = (min(p0, mn), max(p1, mx))
            else:
                pooled[chan] = (mn, mx)

    for chan, entry in luts.items():
        if not entry.get("auto", True):
            continue  # user-edited — leave alone
        rng = pooled.get(chan.upper())
        if rng is None:
            continue
        lo, hi = rng
        if hi <= lo:
            hi = lo + 1.0
        for key, val in (("min", lo), ("max", hi)):
            le = entry.get(key)
            if le is None:
                continue
            blocked = le.blockSignals(True)
            try:
                le.setText(f"{val:.4g}")
            finally:
                le.blockSignals(blocked)


# ── Generate (render image table below the selector) ─────────────────────────


def image_table_generate(app) -> None:
    """Load images for every cell and lay out the rendered image table."""
    from well_viewer.core.ui_helpers import make_fluor_thumb, make_overlay_thumb
    from well_viewer.core.ui_helpers import clear_layout

    cells = getattr(app, "_image_table_cells", None) or []
    grid = app._image_table_render_grid
    clear_layout(grid)
    if not cells:
        app._set_status("Image Table: build a grid first (set rows/cols and click Apply).")
        return

    # Once the user has triggered a Generate, hide the EmptyState placeholder.
    empty = getattr(app, "_image_table_empty_state", None)
    if empty is not None:
        empty.setVisible(False)

    cache: Dict[Tuple[str, str, str], Dict] = {}
    rendered: Dict[Tuple[int, int], Any] = {}
    crop_tool = getattr(app, "_image_table_crop_tool", None)
    use_tophat = bool(getattr(app._app_state.image_table, "use_tophat", False))
    show_boundaries = bool(getattr(app._app_state.image_table, "show_boundaries", False))
    binary_mode = getattr(app._app_state.image_table, "binary_mode", "") or ""
    # Per-cell thumbnail size, scaled by the zoom factor (240 px = 1.0×).
    zoom = float(getattr(app._app_state.image_table, "zoom", 1.0) or 1.0)
    sz = max(80, int(round(240 * zoom)))

    for r, row in enumerate(cells):
        for c, cell in enumerate(row):
            well = cell["well_cb"].currentText().strip()
            chan = cell["chan_cb"].currentText().strip()
            tp = cell["tp_cb"].currentText().strip()
            fov = cell["fov_cb"].currentText().strip()

            arr = None
            ratio = _find_ratio_for_channel(app, chan) if chan else None
            if well and chan and tp:
                if ratio is not None:
                    arr = _load_ratio_array(app, cache, well, ratio, tp, fov, use_tophat=use_tophat)
                else:
                    arr = _load_array(app, cache, well, chan, tp, fov, use_tophat=use_tophat)
            rendered[(r, c)] = arr

            lo, hi = _parse_lut(app, chan)
            cell_widget = QWidget()
            cl = QVBoxLayout(cell_widget)
            cl.setContentsMargins(2, 2, 2, 2)
            cl.setSpacing(2)

            if well and chan and tp:
                header_text = f"{well}  {chan.upper()}  T:{tp}"
                if fov:
                    header_text += f"  FOV:{fov}"
            else:
                header_text = "(unset)"
            header = QLabel(header_text, cell_widget)
            header.setAlignment(Qt.AlignCenter)
            cl.addWidget(header)

            img_label = QLabel(cell_widget)
            img_label.setAlignment(Qt.AlignCenter)
            # Pin to the rendered pixmap size so the surrounding QLabel doesn't
            # expand and leave large gaps around the image.
            img_label.setFixedSize(sz, sz)
            if arr is not None:
                # Apply the shared crop (no-op when not set) and render.
                cropped = crop_tool.apply_to_array(arr) if crop_tool else arr
                is_overlay = _is_nuc_seg(chan) or getattr(arr, "ndim", 2) >= 3
                if is_overlay:
                    # Pre-rendered RGB overlay: tint and per-channel LUT
                    # don't apply, so feed it straight to the overlay
                    # thumbnailer (which still respects lo/hi when set).
                    pix = make_overlay_thumb(cropped, sz, sz, lo, hi)
                elif binary_mode == "mask":
                    # Every segmented cell white on black.
                    pix = _render_mask_binary(app, cache, well, tp, fov, crop_tool, sz)
                    if pix is None:
                        pix = make_fluor_thumb(cropped, sz, sz, lo, hi, cmap=_row_cmap_name(app, r))
                elif binary_mode == "threshold":
                    # Only cells above the active ON threshold white on black.
                    pix = _render_threshold_binary(app, cache, well, chan, tp, fov, crop_tool, sz)
                    if pix is None:
                        pix = make_fluor_thumb(cropped, sz, sz, lo, hi, cmap=_row_cmap_name(app, r))
                elif show_boundaries:
                    # Overlay white cell-boundary outlines on the fluorescence image.
                    mask_arr = _load_mask_array(app, cache, well, tp, fov)
                    if mask_arr is not None:
                        cm = crop_tool.apply_to_array(mask_arr) if crop_tool else mask_arr
                        pix = _render_with_boundary_overlay(
                            cropped, sz, sz, lo, hi,
                            _row_cmap_name(app, r), cm,
                        )
                    else:
                        pix = make_fluor_thumb(cropped, sz, sz, lo, hi, cmap=_row_cmap_name(app, r))
                else:
                    pix = make_fluor_thumb(
                        cropped, sz, sz, lo, hi,
                        cmap=_row_cmap_name(app, r),
                    )
                # CropTool needs the FULL source array + the active crop on
                # the label so label-pixel → image-pixel coord conversion
                # works correctly even when the label already shows a crop.
                img_label._raw_arr = arr  # type: ignore[attr-defined]
                img_label._crop = crop_tool.crop if crop_tool else None  # type: ignore[attr-defined]
                _install_cell_events(app, img_label, crop_tool, well, chan, tp, fov)
                if pix is not None:
                    img_label.setPixmap(pix)
                else:
                    img_label.setText("(render failed)")
            else:
                img_label.setText("(no image)")
            cl.addWidget(img_label)

            grid.addWidget(cell_widget, r, c)

    app._app_state.image_table.last_render = rendered
    app._app_state.image_table.image_cache = cache
    # Fill the per-channel LUT min/max boxes with the actual pooled numbers so
    # the user sees real values instead of "auto" (skips manually-edited rows).
    _refresh_lut_fields_from_render(app, rendered)
    n = sum(1 for v in rendered.values() if v is not None)
    app._set_status(f"Image Table: generated {n} of {len(rendered)} cell(s).")
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
                        cell["well_cb"].currentText().strip(),
                        cell["chan_cb"].currentText().strip(),
                        cell["tp_cb"].currentText().strip(),
                        cell["fov_cb"].currentText().strip(),
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


def image_table_auto_lut(app, channel: str) -> None:
    """Pool min/max across loaded cells for one channel; write result and redraw."""
    chan_upper = channel.upper()
    cells = getattr(app, "_image_table_cells", None) or []
    rendered = getattr(app._app_state.image_table, "last_render", None) or {}
    cache = getattr(app._app_state.image_table, "image_cache", None)
    if cache is None:
        cache = {}
        app._app_state.image_table.image_cache = cache
    use_tophat = bool(getattr(app._app_state.image_table, "use_tophat", False))

    try:
        import numpy as _np
    except Exception:
        app._set_status("Image Table: numpy unavailable; cannot compute auto LUT.")
        return

    pooled_min: Optional[float] = None
    pooled_max: Optional[float] = None
    for r, row in enumerate(cells):
        for c, cell in enumerate(row):
            if cell["chan_cb"].currentText().strip().upper() != chan_upper:
                continue
            arr = rendered.get((r, c))
            if arr is None:
                # Lazy-load if Generate hasn't been clicked yet.
                well = cell["well_cb"].currentText().strip()
                tp = cell["tp_cb"].currentText().strip()
                fov = cell["fov_cb"].currentText().strip()
                if well and tp and fov:
                    arr = _load_array(app, cache, well, chan_upper, tp, fov, use_tophat=use_tophat)
            if arr is None:
                continue
            a = _np.asarray(arr, dtype=_np.float32)
            mn = float(a.min())
            mx = float(a.max())
            pooled_min = mn if pooled_min is None else min(pooled_min, mn)
            pooled_max = mx if pooled_max is None else max(pooled_max, mx)

    if pooled_min is None or pooled_max is None:
        app._set_status(
            f"Image Table: no loaded images for channel {chan_upper}; cannot auto-LUT."
        )
        return
    if pooled_max <= pooled_min:
        pooled_max = pooled_min + 1.0

    luts = getattr(app, "_image_table_lut", None) or {}
    entry = luts.get(chan_upper)
    if entry is None:
        return
    # "Fit" re-enables auto-tracking so the boxes follow the data again.
    entry["auto"] = True
    for key, val in (("min", pooled_min), ("max", pooled_max)):
        le = entry.get(key)
        if le is None:
            continue
        blocked = le.blockSignals(True)
        try:
            le.setText(f"{val:.4g}")
        finally:
            le.blockSignals(blocked)
    image_table_generate(app)


# ── Export (transparent background, labels above each image) ─────────────────


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


# ── Export (transparent background, labels above each image) ─────────────────


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
    use_tophat = bool(getattr(app._app_state.image_table, "use_tophat", False))
    # Mirror the live thumbnail's overlay toggles so the export matches
    # what the user sees on screen.
    binary_mode = getattr(app._app_state.image_table, "binary_mode", "") or ""
    show_boundaries = bool(getattr(app._app_state.image_table, "show_boundaries", False))

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
                lo, hi = _parse_lut(app, chan)
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
