"""Image composition for the Image Table feature.

LUT colour palette + per-row tint/cmap resolution, array loading from the cache,
binary-mask / threshold rendering, the RGB cell composer, and the boundary
overlay — the lower "turn cache + state into pixels" layer of the Image Table.
Extracted from ``controller.py`` so the controller keeps event handling +
orchestration. ``controller`` re-exports every name here, so its call sites and
external importers (playground, grid_view) are unchanged.
"""

from __future__ import annotations

from well_viewer.core.ui_helpers import norm_timepoint

import logging
from typing import Dict, List, Optional, Tuple

from well_viewer.domain.tp_match import tp_close

logger = logging.getLogger(__name__)


LUT_COLORS: Dict[str, Optional[Tuple[float, float, float]]] = {
    # Keys are mpl colormap names so the v2 LutSelector (which renders a real
    # gradient strip) can drive the picker. The value is the single (r,g,b)
    # tint applied to the grayscale fluorescence image for thumbnails; the
    # exported figure shares the live composer (``cell_display_rgb``), so it
    # picks up the same per-channel colormap (``_row_cmap_name``).
    "Greys":   None,
    "Reds":    (1.0, 0.0, 0.0),
    "Greens":  (0.0, 1.0, 0.0),
    "Blues":   (0.0, 0.0, 1.0),
    "Purples": (0.56, 0.0, 1.0),
    "Oranges": (1.0, 0.55, 0.0),
    "Cyan":    (0.0, 1.0, 1.0),
    "Magenta": (1.0, 0.0, 1.0),
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

# The virtual channel token a cell carries when the Separate↔Overlay switch is on
# (``state.composite``): the channel axis collapses to a single entry and each such
# cell additively blends *every* selected channel (``overlay_cell_rgb``) instead of
# showing one channel. It is a sentinel — never a real channel — so the layout
# (``grid_layout._axis_dim_values``) emits it as the sole channel value and the
# renderers (live grid + export) detect it to switch to the blend path.
OVERLAY_CHAN = "⊕ OVERLAY"

# A "binary" channel variant — e.g. ``"GFP (binary)"`` — is a virtual channel that
# renders its base channel as the ThreshFracOn threshold-binary (positive cells
# white). It flows through the axis picker / product / per-cell combo like any
# channel token; the two leaf helpers that turn a token into pixels
# (``_load_array`` for the image, ``cell_display_rgb`` for the render) decode it:
# load the *base* channel, force ``binary_mode="threshold"``.
_BINARY_SUFFIX = " (binary)"


def _binary_variant(chan: str) -> str:
    """The binary-variant token for a base channel (``"GFP"`` → ``"GFP (binary)"``)."""
    return f"{chan}{_BINARY_SUFFIX}"


def _is_binary_variant(chan: str) -> bool:
    s = str(chan or "").strip().lower()
    return s.endswith(_BINARY_SUFFIX.lower()) and len(s) > len(_BINARY_SUFFIX)


def _binary_base(chan: str) -> str:
    """Strip a trailing ``" (binary)"`` (case-insensitive) → the base channel token."""
    s = str(chan or "").strip()
    if _is_binary_variant(s):
        return s[: len(s) - len(_BINARY_SUFFIX)].strip()
    return s


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

def _channel_opts_for(ctx, chan) -> Optional[dict]:
    """The builder's per-channel render spec for *chan*, or None.

    The root + nested-channels shelf stores each channel's render options
    (render/boundary/colormap/lut/log) on ``ctx.state.image_table.channel_opts``,
    keyed by the channel token exactly as it appears on the axis (e.g. ``"GFP"``
    or ``"GFP (binary)"``). When present the four ``_row_*`` helpers resolve from
    it — so display travels with the channel regardless of which axis it sits on
    — and fall back to the legacy per-row widget path when it's absent (old saved
    states, or the collapsed Advanced selector grid)."""
    if not chan:
        return None
    opts = getattr(ctx.state.image_table, "channel_opts", None) or {}
    return opts.get(chan) or opts.get(str(chan).strip().upper())


def _shared_channel_lut_for(ctx, chan) -> Optional[Tuple[float, float]]:
    """Shared per-channel brightness default (Review Image) for *chan*'s base."""
    image_view = getattr(ctx.state, "image_view", None)
    if image_view is None or not hasattr(image_view, "get_lut"):
        return None
    return image_view.get_lut(_binary_base(chan) if chan else chan)


def _row_cmap_name(ctx, r: int, chan=None) -> Optional[str]:
    """Return the mpl colormap name for row *r*, or None when the row is
    set to ``Greys`` (untinted grayscale fast path).

    ``make_fluor_thumb`` consumes this directly and applies the colormap
    across the full LUT [lo, hi] range, so the rendered thumbnails match
    the gradient shown by the LutSelector's strip — high intensity gets
    the cmap's high-end colour, low intensity gets the low end.
    """
    spec = _channel_opts_for(ctx, chan)
    if spec is None:
        return None
    name = (spec.get("colormap") or "").strip()
    return None if (not name or name == "Greys") else name


def _export_binary_mask(ctx, cache, well, chan, tp, fov, binary_mode, crop_tool):
    """Compute the white-cells array for a binary-mode export cell (cropped).

    Mirrors the live ``_render_mask_binary`` / ``_render_threshold_binary``
    selection so the export matches the on-screen thumbnails.
    """
    import numpy as _np

    mask_arr = _load_mask_array(ctx, cache, well, tp, fov)
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
            norm_tp = norm_timepoint(ctx, tp)
            norm_fov = review_norm_fov(fov)
            chan_l = str(chan or "").strip().lower()
            fluor = {str(c).strip().lower()
                     for c in (ctx.state.channel.fluor_channels or [])}
            gate_channel = chan_l if chan_l in fluor else None
            above_by_nid = review_build_threshold_map(
                ctx, mask_arr, well, norm_fov, norm_tp, channel=gate_channel
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

def _row_channel(ctx, row_idx: int) -> str:
    """The representative channel token for grid row *row_idx* (its first cell)."""
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    if not (0 <= row_idx < len(cells)) or not cells[row_idx]:
        return ""
    return (cells[row_idx][0].get("chan") or "").strip()


def _shared_channel_lut(ctx, row_idx: int) -> Optional[Tuple[float, float]]:
    """The shared per-channel display default for row *row_idx*'s channel (S4).

    Returns ``None`` when there's no shared default (then the row auto-fits as
    before). Lets a brightness tuned in Review Image flow in as the default."""
    state = ctx.state
    image_view = getattr(state, "image_view", None)
    if image_view is None or not hasattr(image_view, "get_lut"):
        return None
    return image_view.get_lut(_row_channel(ctx, row_idx))


def _auto_lut_for(ctx, chan) -> Optional[Tuple[float, float]]:
    """The pooled Auto-LUT window for *chan*'s base channel, or None.

    Populated once per Generate by the controller's pooling pass
    (``auto_lut_by_channel``) so a channel left on Auto uses ONE brightness window
    across every cell instead of each cell auto-fitting to its own min/max."""
    pooled = getattr(ctx.state.image_table, "auto_lut_by_channel", None) or {}
    if not pooled or not chan:
        return None
    return pooled.get(_binary_base(chan).strip().lower())


def _parse_lut(ctx, row_idx: int, chan=None) -> Tuple[Optional[float], Optional[float]]:
    """Read (lo, hi) for grid row *row_idx*; falls back to the shared per-channel
    default, then the pooled per-channel Auto window, else (None, None) for auto-fit.

    With a builder channel spec (``chan`` given) the range comes from the
    channel's own ``lut_min``/``lut_max`` (None ⇒ inherit the shared per-channel
    default, then the pooled Auto window, then per-cell auto-fit). Without a spec
    (``chan`` omitted / no channel_opts entry) the range inherits the shared
    per-channel default (a brightness tuned in Review Image), then the pooled Auto
    window, else auto-fits. The pooled window is what makes Auto comparable
    cell-to-cell (see ``controller._compute_auto_luts``)."""
    spec = _channel_opts_for(ctx, chan)
    if spec is not None:
        lo = spec.get("lut_min")
        hi = spec.get("lut_max")
        if lo is None and hi is None:
            shared = _shared_channel_lut_for(ctx, chan)
            if shared is not None:
                return shared
            pooled = _auto_lut_for(ctx, chan)
            if pooled is not None:
                return pooled
        return lo, hi
    # No channel spec → inherit the shared per-channel default. ``_shared_channel_lut``
    # reads the grid cell's own channel (raw row index), correct per band.
    shared = _shared_channel_lut(ctx, row_idx)
    if shared is not None:
        return shared
    pooled = _auto_lut_for(ctx, chan or _row_channel(ctx, row_idx))
    return pooled if pooled is not None else (None, None)


def _row_display_opts(ctx, row_idx: int, chan=None) -> Tuple[bool, bool, str]:
    """Per-cell display toggles: ``(use_tophat, show_boundaries, binary_mode)``.

    Resolved from the channel's builder spec (``channel_opts[chan]``): the render
    mode maps raw→(raw, …), corrected→(tophat, …), mask→binary "mask",
    threshold→binary "threshold"; ``boundary`` is the outline overlay. No spec
    (e.g. an unset cell) → all-off default.
    """
    spec = _channel_opts_for(ctx, chan)
    if spec is None:
        return False, False, ""
    render = spec.get("render", "corrected")
    use_tophat = (render == "corrected")
    show_boundaries = bool(spec.get("boundary", False))
    binary_mode = "mask" if render == "mask" else ("threshold" if render == "threshold" else "")
    return use_tophat, show_boundaries, binary_mode


def _row_log_scale(ctx, row_idx: int, chan=None) -> bool:
    """Whether the cell shows its image on a log intensity scale — the channel's
    builder spec ``log`` (``channel_opts[chan]``); no spec → False."""
    spec = _channel_opts_for(ctx, chan)
    return bool(spec.get("log", False)) if spec is not None else False

def _load_well_channel(
    ctx,
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
    info = ctx.state.dataset.pipeline_info or {}
    # Overlay images aren't keyed by a fluor channel; pass the nuclear token
    # as a sensible default so any token-based filtering inside the loader
    # has a real channel to match. The returned ``overlay`` dict comes from
    # the pipeline's pre-rendered segmentation PNGs.
    fluor_token = chan_lower
    if is_overlay:
        fluor_token = (info.get("nuclear_token") or chan_lower).strip().lower()
    try:
        fluor, overlay, mask, tophat = find_well_images_and_masks(
            ctx.state.dataset.data_dir,
            well,
            fluor_token=fluor_token,
            in_dir=ctx.state.dataset.in_dir,
            _fov_tp_extractor=ctx.state.dataset.fov_tp_extractor,
            _pipeline_info=ctx.state.dataset.pipeline_info,
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

def _match_image_ref(images: Dict, fov: str, tp: str):
    """Resolve an image ref for ``(fov, tp)``; return ``(ref, kind)``.

    ``kind`` ∈ ``{"exact", "normalized", "numeric", ""}`` (``""`` = no match),
    letting the caller annotate non-exact resolutions (audit S5).

    Tiers, in order: exact tuple key → normalized-token (``"1.0"`` ↔ ``"1"``)
    → numeric-close (``"T01"`` ↔ ``"1"``, timepoints matched with the S2
    relative tolerance). When ``fov`` is empty (empty dropdown) and MORE THAN
    ONE distinct FOV would satisfy the timepoint, prefer "no image" —
    ``(None, "ambiguous_fov")`` — rather than silently surfacing an arbitrary
    first FOV; a genuinely single-FOV dataset still resolves unambiguously.
    """
    exact = images.get((str(fov), str(tp)))
    if exact is not None:
        return exact, "exact"

    def _pick(pred):
        # Returns (val, "ok") for a unique resolution, (None, "ambiguous_fov")
        # when an empty fov spans multiple FOVs, or (None, "") for no match.
        hits = [(cf, val) for (cf, ct), val in images.items() if pred(cf, ct)]
        if not hits:
            return None, ""
        if not str(fov or "").strip():
            distinct = {cf for cf, _ in hits}
            if len(distinct) > 1:
                return None, "ambiguous_fov"
        return hits[0][1], "ok"

    target_fov = _norm_token(fov)
    target_tp = _norm_token(tp)
    val, status = _pick(lambda cf, ct:
        ((not target_tp) or _norm_token(ct) == target_tp)
        and ((not target_fov) or _norm_token(cf) == target_fov))
    if status == "ambiguous_fov":
        return None, "ambiguous_fov"
    if val is not None:
        return val, "normalized"

    # Numeric-close: cache keys like ``"T01"`` and dropdown values like ``"1"``
    # both reduce to the same number, rescuing runs where pipeline_info.json's
    # timepoints were stored numerically but the filenames carry a ``T`` prefix
    # (or vice versa). Timepoints use the S2 relative tolerance, so distinct
    # timepoints never merge.
    target_fov_n = _numeric_token(fov)
    target_tp_n = _numeric_token(tp)

    def _close(a, b) -> bool:
        return a is not None and b is not None and tp_close(a, b)

    val, status = _pick(lambda cf, ct:
        (target_tp_n is None or _close(_numeric_token(ct), target_tp_n))
        and (target_fov_n is None or _close(_numeric_token(cf), target_fov_n)))
    if status == "ambiguous_fov":
        return None, "ambiguous_fov"
    if val is not None:
        return val, "numeric"
    return None, ""


def _load_array(ctx, cache: Dict, well: str, chan_upper: str, tp: str, fov: str, *, use_tophat: bool = False):
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
    # A "GFP (binary)" token loads the same pixels as "GFP" — the binary render is
    # applied downstream (cell_display_rgb); here we just resolve the base channel.
    chan_lower = _binary_base(chan_upper).strip().lower()
    fluor = _load_well_channel(ctx, cache, well, chan_lower, use_tophat=use_tophat)
    # When tophat is requested but not produced for this well/channel, drop
    # back to raw rather than rendering "(no image)".
    if (use_tophat and not fluor and chan_lower != NUC_SEG_TOKEN.lower()):
        fluor = _load_well_channel(ctx, cache, well, chan_lower, use_tophat=False)
    if not fluor:
        return None

    ref, kind = _match_image_ref(fluor, fov, tp)
    if ref is None:
        return None
    if kind != "exact":
        # Annotate any non-exact resolution so a surfaced neighbouring
        # frame is diagnosable rather than silently misread as QC truth
        # (audit S5).
        logger.debug(
            "image_table: (%s, tp=%r, fov=%r) resolved via %s fallback",
            well, tp, fov, kind,
        )
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

def _load_mask_array(ctx, cache, well, tp, fov):
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

def _load_ratio_array(ctx, cache, well, ratio, tp, fov, *, use_tophat):
    """Compute the pixel-wise ratio for a RatioMetric.  Returns float32 or None."""
    try:
        import numpy as _np
    except Exception:
        return None
    num_chan = (ratio.numerator_channel or "").strip()
    den_chan = (ratio.denominator_channel or "").strip()
    if not num_chan or not den_chan:
        return None
    num_arr = _load_array(ctx, cache, well, num_chan.upper(), tp, fov, use_tophat=use_tophat)
    den_arr = _load_array(ctx, cache, well, den_chan.upper(), tp, fov, use_tophat=use_tophat)
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

def _render_mask_binary(ctx, cache, well, tp, fov, crop_tool, sz):
    """White-on-black image of every segmented cell (full mask)."""
    from well_viewer.core.ui_helpers import make_overlay_thumb
    import numpy as _np

    mask_arr = _load_mask_array(ctx, cache, well, tp, fov)
    if mask_arr is None:
        return None
    cm = crop_tool.apply_to_array(mask_arr) if crop_tool else mask_arr
    binary = (_np.asarray(cm) > 0).astype(_np.uint8) * 255
    rgb = _np.stack([binary, binary, binary], axis=-1)
    return make_overlay_thumb(rgb, sz, sz, None, None)

def _render_threshold_binary(ctx, cache, well, chan, tp, fov, crop_tool, sz):
    """White-on-black image of only the cells above the active ON threshold.

    Reuses the Review Image gating logic (``review_build_threshold_map``) so
    the "above threshold" definition matches the bar plot / review tab exactly
    (cell-area gating + every fluor gate + value-column > ON threshold). That
    helper also emits the ``[binary-mask]`` diagnostic line.
    """
    from well_viewer.core.ui_helpers import make_overlay_thumb
    import numpy as _np

    mask_arr = _load_mask_array(ctx, cache, well, tp, fov)
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
        norm_tp = norm_timepoint(ctx, tp)
        norm_fov = review_norm_fov(fov)
        # Gate each cell by ITS OWN channel's ThreshFracOn (set per-channel in
        # Sample Definitions), not whatever channel the Segmentation tab last
        # showed. Only override for real fluor channels — nuc+seg / ratio cells
        # fall back to the displayed-channel default.
        chan_l = str(chan or "").strip().lower()
        fluor = {str(c).strip().lower() for c in (ctx.state.channel.fluor_channels or [])}
        gate_channel = chan_l if chan_l in fluor else None
        above_by_nid = review_build_threshold_map(
            ctx, mask_arr, well, norm_fov, norm_tp, channel=gate_channel
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


# ── Composite row: additive merge of the LUT-colored rows above ─────────────


def cell_display_rgb(ctx, cache, cell, r: int, crop_tool, arr):
    """Return the uint8 (H, W, 3) RGB shown for grid cell ``cell`` in row *r*.

    Single source of truth for "what pixels does this cell display": applies the
    shared crop, resolves the row's LUT window / colormap / display toggles, and
    delegates to :func:`_compose_image_table_rgb`. Callers pass the already-loaded
    ``arr`` (the live path reuses ``last_render``; the export path lazy-loads it)
    so this helper stays free of the ratio-load / controller coupling. Returns
    ``None`` when ``arr`` is ``None`` or can't be composed.

    Mirrors the per-cell block in ``export._build_export_figure`` — extracted so
    the live thumbnail, the export figure, and the composite row all agree.
    """
    if arr is None:
        return None
    chan = cell["chan"]
    # channel_opts / the four helpers are keyed by the full axis token (incl. a
    # trailing " (binary)"); keep it before we strip to the base channel below.
    chan_token = chan
    well = cell["well"]
    tp = cell["tp"]
    fov = cell["fov"]

    _use_tophat, show_boundaries, binary_mode = _row_display_opts(ctx, r, chan_token)
    # A "GFP (binary)" cell renders the base channel's threshold-binary regardless
    # of the row's Binary box: resolve the real channel + force threshold mode.
    if _is_binary_variant(chan):
        chan = _binary_base(chan)
        binary_mode = "threshold"
    cropped = crop_tool.apply_to_array(arr) if crop_tool is not None else arr
    lo, hi = _parse_lut(ctx, r, chan_token)

    mask_arr = None
    if show_boundaries and not _is_nuc_seg(chan):
        mask_arr = _load_mask_array(ctx, cache, well, tp, fov)
        if mask_arr is not None and crop_tool is not None:
            mask_arr = crop_tool.apply_to_array(mask_arr)
    binary_mask = None
    if binary_mode in ("mask", "threshold") and not _is_nuc_seg(chan):
        binary_mask = _export_binary_mask(
            ctx, cache, well, chan, tp, fov, binary_mode, crop_tool,
        )
    return _compose_image_table_rgb(
        cropped, mask_arr,
        lo=lo, hi=hi,
        cmap_name=_row_cmap_name(ctx, r, chan_token),
        is_overlay=_is_nuc_seg(chan),
        binary_mask=binary_mask,
        show_boundaries=show_boundaries,
        log_scale=_row_log_scale(ctx, r, chan_token),
    )


def composite_additive_rgb(rgbs):
    """Additively merge a list of uint8 RGB arrays, clipped to white.

    Sums every array that matches the reference (first non-None) shape, skipping
    (and logging) mismatched shapes — grid rows may target different wells / FOVs
    / crops. Returns a uint8 (H, W, 3) array, or ``None`` when nothing usable was
    supplied. This is the standard fluorescence "merge": overlapping LUT-colored
    signals brighten toward white.
    """
    import numpy as _np

    usable = [rgb for rgb in rgbs if rgb is not None]
    if not usable:
        return None
    ref_shape = _np.asarray(usable[0]).shape
    acc = _np.zeros(ref_shape, dtype=_np.float32)
    used = 0
    for rgb in usable:
        a = _np.asarray(rgb)
        if a.shape != ref_shape:
            logger.debug(
                "image_table composite: skipping tile shape %r != reference %r",
                a.shape, ref_shape,
            )
            continue
        acc += a.astype(_np.float32)
        used += 1
    if used == 0:
        return None
    return _np.clip(acc, 0.0, 255.0).astype(_np.uint8)


def composite_column_rgb(ctx, cache, cells, crop_tool, c: int, rendered):
    """Build the additive composite RGB for grid column *c*.

    Gathers each grid row's displayed RGB (via :func:`cell_display_rgb`) for
    column *c* and merges them with :func:`composite_additive_rgb`. Reuses the
    per-generate ``rendered`` arrays when present, else lazy-loads (mirroring the
    export path). Returns a uint8 (H, W, 3) array, or ``None``.
    """
    rendered = rendered or {}
    rgbs = []
    for r, row in enumerate(cells):
        if c >= len(row):
            continue
        cell = row[c]
        arr = rendered.get((r, c))
        if arr is None:
            well = cell["well"]
            chan = cell["chan"]
            tp = cell["tp"]
            fov = cell["fov"]
            if well and chan and tp:
                use_tophat = _row_display_opts(ctx, r)[0]
                arr = _load_array(ctx, cache, well, chan, tp, fov, use_tophat=use_tophat)
        rgb = cell_display_rgb(ctx, cache, cell, r, crop_tool, arr)
        if rgb is not None:
            rgbs.append(rgb)
    return composite_additive_rgb(rgbs)


def _ratio_for_overlay(ctx, chan):
    """The RatioMetric whose display label matches *chan* (case-insensitive), else
    None. Inlined so this render layer stays free of a ``grid_layout`` import
    (which would cycle: grid_layout imports this module)."""
    for r in (ctx.state.channel.ratio_metrics or []):
        if r.display_label().upper() == str(chan or "").strip().upper():
            return r
    return None


def overlay_cell_rgb(ctx, cache, cell, r: int, crop_tool, channels):
    """Additively blend every channel in *channels* for one cell (well/tp/fov).

    The Separate↔Overlay switch collapses the channel axis: instead of one row /
    column per channel, each cell shows all selected channels merged into one
    image. Each channel renders through the SAME per-channel path
    (:func:`cell_display_rgb`), so its LUT / colormap / boundary / binary options
    travel with it, then the results are summed and clipped to white by
    :func:`composite_additive_rgb`. Returns a uint8 (H, W, 3) RGB, or ``None``.
    """
    well = cell.get("well")
    tp = cell.get("tp")
    fov = cell.get("fov")
    if not (well and tp):
        return None
    rgbs = []
    for ch in channels:
        if not ch or ch == OVERLAY_CHAN:
            continue
        use_tophat = _row_display_opts(ctx, r, ch)[0]
        ratio = _ratio_for_overlay(ctx, ch)
        if ratio is not None:
            arr = _load_ratio_array(ctx, cache, well, ratio, tp, fov, use_tophat=use_tophat)
        else:
            arr = _load_array(ctx, cache, well, ch, tp, fov, use_tophat=use_tophat)
        sub = {"well": well, "chan": ch, "tp": tp, "fov": fov}
        rgb = cell_display_rgb(ctx, cache, sub, r, crop_tool, arr)
        if rgb is not None:
            rgbs.append(rgb)
    return composite_additive_rgb(rgbs)


# ── Burned-in cell chrome: scale bar + identity label ───────────────────────
#
# Drawn into the SOURCE-resolution cell RGB (not the QPixmap / mpl axes) so the
# live thumbnail and the exported figure carry the identical mark — the same
# single-source principle the RGB composer follows. Applied at the OUTERMOST cell
# RGB only (controller._render_cell / export._build_export_figure), never inside
# cell_display_rgb, because overlay_cell_rgb calls that per channel and would stamp
# the mark on every channel before blending.

_CHROME_COLORS: Dict[str, Tuple[int, int, int]] = {
    "white": (255, 255, 255), "black": (0, 0, 0), "yellow": (255, 255, 0),
    "cyan": (0, 255, 255), "magenta": (255, 0, 255), "red": (255, 0, 0),
    "green": (0, 255, 0),
}


def _chrome_rgb(name) -> Tuple[int, int, int]:
    return _CHROME_COLORS.get(str(name or "white").strip().lower(), (255, 255, 255))


def _corner_cell(rows: int, cols: int, code: str, occupied=None):
    """Grid ``(r, c)`` for a corner *code* (tl/tr/bl/br).

    When that grid corner is blank (``occupied`` given and the corner not in it —
    ragged wrap / facet layouts), fall back to the occupied cell nearest the corner.
    Returns ``None`` when the grid is empty or nothing is occupied."""
    rows = max(0, int(rows))
    cols = max(0, int(cols))
    if rows == 0 or cols == 0:
        return None
    targets = {"tl": (0, 0), "tr": (0, cols - 1),
               "bl": (rows - 1, 0), "br": (rows - 1, cols - 1)}
    target = targets.get(str(code or "").strip().lower())
    if target is None:
        return None
    if not occupied or target in occupied:
        return target
    ty, tx = target
    return min(occupied, key=lambda rc: abs(rc[0] - ty) + abs(rc[1] - tx))


def _chrome_target_cells(rows: int, cols: int, code: str, occupied) -> set:
    """Set of ``(r, c)`` that should carry a mark for placement *code*.

    ``"all"`` → every occupied cell; a corner code → the single (fallback) corner
    cell. Empty when the code is off / unresolvable."""
    occ = set(occupied or ())
    c = str(code or "").strip().lower()
    if c == "all":
        return occ
    cell = _corner_cell(rows, cols, c, occ)
    return {cell} if cell is not None else set()


def _load_chrome_font(px: int):
    """A PIL truetype font at *px* from the bundled Inter faces, else the default
    bitmap font. Import is lazy so this module stays Qt-free at import time."""
    try:
        from PIL import ImageFont
    except Exception:
        return None
    try:
        from well_viewer.core.app_fonts import fonts_dir
        d = fonts_dir()
        for fname in ("Inter-SemiBold.ttf", "Inter-Regular.ttf", "Inter.ttf"):
            fp = d / fname
            if fp.exists():
                return ImageFont.truetype(str(fp), max(6, int(px)))
    except Exception:
        logger.debug("image_table: bundled chrome font unavailable", exc_info=True)
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def draw_cell_chrome(rgb, *, bar_px: int = 0, bar_corner: str = "br",
                     bar_color: str = "white", label_text: str = "",
                     label_corner: str = "tl", label_font_px: int = 12,
                     label_color: str = "white", label_bg: bool = True):
    """Burn a scale bar and/or an identity label into an ``(H, W, 3)`` uint8 RGB.

    Sizes are proportional to image height — bar thickness and margins scale with
    ``H``; the label font scales relative to a nominal 240 px cell — so the mark
    reads the same at the live thumbnail size and at full export resolution. The
    scale bar is ``bar_px`` long (already computed from µm/px in source pixels).
    Returns a mutated copy, or the input unchanged when there's nothing to draw."""
    import numpy as _np

    if rgb is None or (not bar_px and not label_text):
        return rgb
    a = _np.array(rgb, dtype=_np.uint8, copy=True)
    if a.ndim != 3 or a.shape[2] < 3:
        return rgb
    H, W = a.shape[:2]
    if H < 4 or W < 4:
        return rgb

    # Scale bar — a filled rectangle with a 1 px contrasting halo for legibility.
    if bar_px and bar_px > 0:
        blen = int(min(bar_px, W - 2))
        if blen >= 1:
            thick = max(2, H // 100)
            margin = max(2, H // 25)
            corner = str(bar_corner or "br").strip().lower()
            if corner in ("tl", "bl"):
                x0 = margin
                x1 = min(W, x0 + blen)
            else:
                x1 = W - margin
                x0 = max(0, x1 - blen)
            if corner in ("tl", "tr"):
                y0 = margin
                y1 = min(H, y0 + thick)
            else:
                y1 = H - margin
                y0 = max(0, y1 - thick)
            col = _chrome_rgb(bar_color)
            halo = (0, 0, 0) if col != (0, 0, 0) else (255, 255, 255)
            a[max(0, y0 - 1):min(H, y1 + 1), max(0, x0 - 1):min(W, x1 + 1)] = halo
            a[y0:y1, x0:x1] = col

    # Identity label — PIL text with an optional translucent dark box behind it.
    if label_text:
        try:
            from PIL import Image, ImageDraw
        except Exception:
            return a
        font_px = max(6, int(round(float(label_font_px) * H / 240.0)))
        font = _load_chrome_font(font_px)
        img = Image.fromarray(a)
        draw = ImageDraw.Draw(img, "RGBA")
        try:
            bbox = draw.textbbox((0, 0), label_text, font=font)
            tw, th, tox, toy = bbox[2] - bbox[0], bbox[3] - bbox[1], bbox[0], bbox[1]
        except Exception:
            tw, th, tox, toy = (len(label_text) * font_px // 2, font_px, 0, 0)
        pad = max(2, font_px // 4)
        margin = max(2, H // 40)
        corner = str(label_corner or "tl").strip().lower()
        if corner in ("tr", "br"):
            bx1 = W - margin
            bx0 = max(0, bx1 - tw - 2 * pad)
        else:
            bx0 = margin
            bx1 = min(W, bx0 + tw + 2 * pad)
        if corner in ("bl", "br"):
            by1 = H - margin
            by0 = max(0, by1 - th - 2 * pad)
        else:
            by0 = margin
            by1 = min(H, by0 + th + 2 * pad)
        if label_bg:
            draw.rectangle([bx0, by0, bx1, by1], fill=(0, 0, 0, 140))
        draw.text((bx0 + pad - tox, by0 + pad - toy), label_text,
                  fill=_chrome_rgb(label_color) + (255,), font=font)
        a = _np.asarray(img.convert("RGB"), dtype=_np.uint8)
    return a


def cell_identity_label(ctx, cell, overlay_chans=None) -> str:
    """The identity string for a cell (well · channel · T · FOV), or "" when unset.

    Single source for both the burned-in label and the tooltip / export title. An
    OVERLAY_CHAN cell names its merged channel set."""
    from well_viewer.domain.selections_model import well_display_label
    well, chan, tp, fov = (cell.get("well"), cell.get("chan"),
                           cell.get("tp"), cell.get("fov"))
    if not (well and tp):
        return ""
    if chan == OVERLAY_CHAN:
        label = "+".join(str(c).upper() for c in (overlay_chans or [])) or "overlay"
    elif chan:
        label = str(chan).upper()
    else:
        return ""
    s = f"{well_display_label(ctx, well)}  {label}  T:{tp}"
    return s + (f"  FOV:{fov}" if fov else "")


def resolve_chrome_plan(ctx, cells):
    """Resolve the per-Generate chrome plan → ``(bar_px, bar_targets, label_targets)``.

    ``bar_px`` is the scale-bar length in source pixels (0 = off); the two target
    sets are the ``(r, c)`` cells that show the bar / label, from the "which cell(s)"
    placement code (all, or a grid corner with blank-corner fallback). Empty grid or
    disabled feature → empty sets."""
    st = ctx.state.image_table
    rows = len(cells)
    cols = len(cells[0]) if rows and cells[0] else 0
    occupied = {
        (r, c)
        for r, row in enumerate(cells)
        for c, cell in enumerate(row)
        if cell.get("well") and cell.get("chan") and cell.get("tp")
    }
    bar_px = 0
    show = bool(getattr(st, "scale_bar_show", False))
    ps = float(getattr(st, "pixel_size_um", 0.0) or 0.0)
    sb = float(getattr(st, "scale_bar_um", 0.0) or 0.0)
    if show and ps > 0 and sb > 0:
        bar_px = max(1, int(round(sb / ps)))
    bar_targets = (_chrome_target_cells(rows, cols, getattr(st, "scale_bar_cells", "br"),
                                        occupied) if bar_px else set())
    label_on = str(getattr(st, "label_mode", "none")).strip().lower() == "identity"
    label_targets = (_chrome_target_cells(rows, cols, getattr(st, "label_cells", "all"),
                                          occupied) if label_on else set())
    return bar_px, bar_targets, label_targets


def cell_chrome_kwargs(ctx, r: int, c: int, cell, plan, overlay_chans=None):
    """Per-cell ``draw_cell_chrome`` kwargs for grid cell ``(r, c)``, or ``None`` when
    this cell carries no mark. *plan* is :func:`resolve_chrome_plan`'s tuple."""
    bar_px, bar_targets, label_targets = plan
    st = ctx.state.image_table
    bar = bar_px if (bar_px and (r, c) in bar_targets) else 0
    label_text = ""
    if (r, c) in label_targets:
        label_text = cell_identity_label(ctx, cell, overlay_chans)
    if not bar and not label_text:
        return None
    return dict(
        bar_px=bar,
        bar_corner=getattr(st, "scale_bar_img_corner", "br"),
        bar_color=getattr(st, "scale_bar_color", "white"),
        label_text=label_text,
        label_corner=getattr(st, "label_img_corner", "tl"),
        label_font_px=int(getattr(st, "label_font_px", 12)),
        label_color=getattr(st, "label_color", "white"),
        label_bg=bool(getattr(st, "label_bg", True)),
    )
