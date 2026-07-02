"""Image composition for the Image Table feature.

LUT colour palette + per-row tint/cmap resolution, array loading from the cache,
binary-mask / threshold rendering, the RGB cell composer, and the boundary
overlay — the lower "turn cache + state into pixels" layer of the Image Table.
Extracted from ``controller.py`` so the controller keeps event handling +
orchestration. ``controller`` re-exports every name here, so its call sites and
external importers (playground, grid_view) are unchanged.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from well_viewer.domain.tp_match import tp_close

logger = logging.getLogger(__name__)


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

NUC_SEG_TOKEN = "NUC+SEG"

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
            from well_viewer.features.image_browser.review_image.controller import (
                review_build_threshold_map, review_norm_fov,
            )
            # Mirror the live _render_threshold_binary path: the CSV row keys are
            # normalised (timepoint "0h" -> "0", fov tokens), and gating is per
            # the cell's OWN channel. Passing the raw tp/fov + no channel (as
            # this path used to) matches no rows → every cell reads as
            # below-threshold → an all-black mask.
            norm_tp = app._norm_timepoint(tp) if hasattr(app, "_norm_timepoint") else tp
            norm_fov = review_norm_fov(fov)
            chan_l = str(chan or "").strip().lower()
            fluor = {str(c).strip().lower()
                     for c in (app._app_state.channel.fluor_channels or [])}
            gate_channel = chan_l if chan_l in fluor else None
            above_by_nid = review_build_threshold_map(
                app, mask_arr, well, norm_fov, norm_tp, channel=gate_channel
            ) or {}
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
    log_scale: bool = False,
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

    # 3. Greyscale fluorescence → apply LUT + colormap (shared single-source
    #    helper; also used by the Playground renderer).
    from well_viewer.core.ui_helpers import lut_colormap_to_rgb
    af = a.astype(_np.float32)
    rgb = lut_colormap_to_rgb(af, lo, hi, cmap_name or "", log_scale=log_scale)

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

def _row_channel(app, row_idx: int) -> str:
    """The representative channel token for grid row *row_idx* (its first cell)."""
    cells = getattr(app, "_image_table_cells", None) or []
    if not (0 <= row_idx < len(cells)) or not cells[row_idx]:
        return ""
    cb = cells[row_idx][0].get("chan_cb")
    try:
        return cb.currentText().strip() if cb is not None else ""
    except Exception:
        return ""


def _shared_channel_lut(app, row_idx: int) -> Optional[Tuple[float, float]]:
    """The shared per-channel display default for row *row_idx*'s channel (S4).

    Returns ``None`` when there's no shared default (then the row auto-fits as
    before). Lets a brightness tuned in Review Image flow in as the default."""
    state = getattr(app, "_app_state", None)
    image_view = getattr(state, "image_view", None)
    if image_view is None or not hasattr(image_view, "get_lut"):
        return None
    return image_view.get_lut(_row_channel(app, row_idx))


def _parse_lut(app, row_idx: int) -> Tuple[Optional[float], Optional[float]]:
    """Read (lo, hi) for grid row *row_idx*; falls back to the shared per-channel
    default, else (None, None) for auto-fit.

    The per-row LUT table is keyed per grid row (not per channel) so two rows
    showing the same channel have independent ranges — see
    ``grid_view.build_lut_row``. A manual per-row value overrides the shared
    default; an "auto" row inherits the shared brightness-per-channel default
    (S4) when one exists, otherwise auto-fits.
    """
    luts = getattr(app, "_image_table_lut", None) or {}
    entry = luts.get(row_idx)
    lo = hi = None
    if entry:
        from well_viewer.core.ui_helpers import parse_float_edit
        lo = parse_float_edit(entry["min"], allow_auto=True)
        hi = parse_float_edit(entry["max"], allow_auto=True)

    # Row in auto mode (no manual override) → inherit the shared default.
    if lo is None and hi is None:
        shared = _shared_channel_lut(app, row_idx)
        if shared is not None:
            return shared
    return lo, hi


def _row_display_opts(app, row_idx: int) -> Tuple[bool, bool, str]:
    """Per-row display toggles: ``(use_tophat, show_boundaries, binary_mode)``.

    Read from the row's LUT/Display box widgets (``grid_view.build_lut_row``);
    defaults to all-off when the row has no box yet.
    """
    entry = (getattr(app, "_image_table_lut", None) or {}).get(row_idx) or {}
    source_w = entry.get("source")
    bound_w = entry.get("boundaries")
    bin_w = entry.get("binary")
    # "Corrected" loads the top-hat / background-subtracted output (use_tophat);
    # "Raw" loads the unprocessed fluorescence.
    use_tophat = (source_w.currentData() == "corrected") if source_w is not None else False
    show_boundaries = bool(bound_w.isChecked()) if bound_w is not None else False
    binary_mode = (bin_w.currentData() if bin_w is not None else "") or ""
    return use_tophat, show_boundaries, binary_mode


def _row_log_scale(app, row_idx: int) -> bool:
    """Whether grid row *row_idx* shows its image on a log intensity scale.

    Separate from :func:`_row_display_opts` so that helper's 3-tuple unpack call
    sites stay unchanged. Reads the row's ``"log"`` checkbox (``build_lut_row``).
    """
    entry = (getattr(app, "_image_table_lut", None) or {}).get(row_idx) or {}
    log_w = entry.get("log")
    try:
        return bool(log_w.isChecked()) if log_w is not None else False
    except Exception:
        return False

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
            return a is not None and b is not None and tp_close(a, b)

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
        # A corrupt/undecodable image renders as "no tile". Leave a breadcrumb
        # so it's diagnosable rather than silently blank (audit E15).
        logger.debug("image_table: failed to load %r: skipping tile", ref, exc_info=True)
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
        # A corrupt mask must not silently render as "no overlay" — that reads
        # as a clean QC/segmentation view (audit E15). Leave a breadcrumb.
        logger.debug("image_table: failed to load mask %r: no overlay", ref, exc_info=True)
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

def _render_with_boundary_overlay(arr_2d, sz_w, sz_h, lo, hi, cmap_name, mask_arr,
                                  log_scale: bool = False):
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
        return make_fluor_thumb(arr_2d, sz_w, sz_h, lo, hi, cmap=cmap_name, log_scale=log_scale)

    arr = _np.asarray(arr_2d, dtype=_np.float32)
    rgb = lut_colormap_to_rgb(arr, lo, hi, cmap_name, log_scale=log_scale)

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
        from well_viewer.features.image_browser.review_image.controller import (
            review_build_threshold_map, review_norm_fov,
        )
        # The CSV row keys are normalised (e.g. timepoint "0h" -> "0"); the
        # Image Table cell carries the raw image-filename token, so normalise
        # tp/fov the same way the Review Image tab does before the lookup —
        # otherwise no rows match and every cell reads as below-threshold
        # (an all-black mask) for any non-integer timepoint token.
        norm_tp = app._norm_timepoint(tp) if hasattr(app, "_norm_timepoint") else tp
        norm_fov = review_norm_fov(fov)
        # Gate each cell by ITS OWN channel's ThreshFracOn (set per-channel in
        # Sample Definitions), not whatever channel the Segmentation tab last
        # showed. Only override for real fluor channels — nuc+seg / ratio cells
        # fall back to the displayed-channel default.
        chan_l = str(chan or "").strip().lower()
        fluor = {str(c).strip().lower() for c in (app._app_state.channel.fluor_channels or [])}
        gate_channel = chan_l if chan_l in fluor else None
        above_by_nid = review_build_threshold_map(
            app, mask_arr, well, norm_fov, norm_tp, channel=gate_channel
        ) or {}
    except Exception:  # noqa: BLE001 — fall back to an all-off mask on any error
        above_by_nid = {}
    m = _np.asarray(mask_arr)
    passing = [nid for nid, ok in above_by_nid.items() if ok]
    binary = _np.isin(m, passing) if passing else _np.zeros(m.shape, dtype=bool)
    b = binary.astype(_np.uint8) * 255
    cm = crop_tool.apply_to_array(b) if crop_tool else b
    rgb = _np.stack([cm, cm, cm], axis=-1)
    return make_overlay_thumb(rgb, sz, sz, None, None)
