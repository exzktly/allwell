"""Image composition for the Image Table feature.

LUT colour palette + per-row tint/cmap resolution, array loading from the cache,
binary-mask / threshold rendering, the RGB cell composer, and the boundary
overlay — the lower "turn cache + state into pixels" layer of the Image Table.
Extracted from ``controller.py`` so the controller keeps event handling +
orchestration. Address resolution and array loading are the data layer's
(``data/frame_load.py``), shared with the Cell Browser and the Playground; the
three wrappers below bind this tab's per-pass diagnostics to them.
"""

from __future__ import annotations

# Names used only in annotations. Under ``from __future__ import
# annotations`` these are never evaluated, so importing them for real would
# add startup cost and, for several of these, an import cycle.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from pathlib import Path
    from well_viewer.domain.ratio_models import RatioMetric
    import numpy as np

from well_viewer.core.ui_helpers import norm_timepoint

import logging

# Resolving a channel token and loading the pixels it names is the data layer's
# job (`data/frame_load.py`), shared with the Cell Browser and the Playground.
# This module is the renderer above it. The private aliases keep the many call
# sites here reading as they did; the two loaders are wrapped below rather than
# aliased, because the Image Table threads its per-pass diagnostics through them.
from well_viewer.data.frame_load import (  # noqa: F401
    BINARY_SUFFIX as _BINARY_SUFFIX,
    MASK_SUFFIX as _MASK_SUFFIX,
    NUC_SEG_TOKEN,
    base_channel as _base_channel,
    binary_variant as _binary_variant,
    has_suffix as _has_suffix,
    is_nuc_seg as _is_nuc_seg,
    is_variant_token,
    load_frame_array,
    load_mask_array,
    load_well_channel,
)

logger = logging.getLogger(__name__)


def _load_array(ctx, cache: dict, well: str, chan_upper: str, tp: str, fov: str,
                *, use_tophat: bool = False):
    """:func:`data.frame_load.load_frame_array`, bound to this render pass.

    The pass-scoped ``scan_misses`` / ``raw_fallbacks`` / ``partial_scans`` sets
    live on the Image Table's own state slice, which the data layer must not
    name. They are supplied here instead, so every Image Table call site keeps
    reporting what it always reported and the loader stays feature-free.
    """
    return load_frame_array(ctx, cache, well, chan_upper, tp, fov,
                            use_tophat=use_tophat,
                            raw_fallbacks=_raw_fallbacks(ctx),
                            scan_misses=_scan_misses(ctx),
                            partial_scans=_partial_scans(ctx))


def _load_well_channel(ctx, cache, well, chan_lower, *, use_tophat: bool = False):
    """:func:`data.frame_load.load_well_channel`, bound to this render pass."""
    return load_well_channel(ctx, cache, well, chan_lower,
                             use_tophat=use_tophat,
                             scan_misses=_scan_misses(ctx),
                             partial_scans=_partial_scans(ctx))


def _load_mask_array(ctx, cache, well, tp, fov):
    """:func:`data.frame_load.load_mask_array`, minus the cells that don't count.

    Every mask the Image Table draws from comes through here — the boundary
    overlay, the Binary (mask) view, the Binary (threshold) view, the mask
    channel's own LUT pass — so the inclusion filter is applied once, at the
    load, rather than at each of those four.

    A pipeline mask holds every object that was segmented, including cells the
    user deleted by hand and cells a crop excluded. Outlining those says they
    are counted when every plot, statistic and export has dropped them, so the
    labels the inclusion chokepoint rejects are zeroed out of the array here and
    the whole tab follows. The one thing this cannot reach is the pipeline's
    pre-rendered **NUC+SEG overlay** channel: those boundaries are baked into a
    saved image, so that channel still shows every segmented cell.
    """
    from well_viewer.data.inclusion import (
        drop_excluded_labels, excluded_nids_for_frame,
    )
    arr = load_mask_array(ctx, cache, well, tp, fov)
    if ctx is None:
        # The mask resolution below this call is pure — it needs only the ref
        # cache — and the tests that pin it (``test_image_table_render_resilience``)
        # pass no ctx at all. Inclusion is the one part that needs one, so with
        # no ctx there is nothing to ask and the labels stand as loaded.
        return arr
    return drop_excluded_labels(arr, excluded_nids_for_frame(ctx, well, fov, tp))


LUT_COLORS: dict[str, tuple[float, float, float] | None] = {
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

LUT_COLOR_NAMES: list[str] = list(LUT_COLORS.keys())

def _tint_from_colormap(name: str) -> tuple[float, float, float] | None:
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


# The virtual channel token a cell carries when the Separate↔Overlay switch is on
# (``state.composite``): the channel axis collapses to a single entry and each such
# cell additively blends *every* selected channel (``overlay_cell_rgb``) instead of
# showing one channel. It is a sentinel — never a real channel — so the layout
# (``grid_layout._axis_dim_values``) emits it as the sole channel value and the
# renderers (live grid + export) detect it to switch to the blend path.
OVERLAY_CHAN = "⊕ OVERLAY"


def _is_binary_variant(chan: str) -> bool:
    return _has_suffix(chan, _BINARY_SUFFIX)


def _is_mask_variant(chan: str) -> bool:
    return _has_suffix(chan, _MASK_SUFFIX)


def _binary_base(chan: str) -> str:
    """Back-compatible alias for :func:`_base_channel` (external importers)."""
    return _base_channel(chan)


def _channel_opts_for(ctx, chan) -> dict | None:
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


def _shared_channel_lut_for(ctx, chan) -> tuple[float, float] | None:
    """Shared per-channel brightness default (Review Image) for *chan*'s base."""
    image_view = getattr(ctx.state, "image_view", None)
    if image_view is None or not hasattr(image_view, "get_lut"):
        return None
    return image_view.get_lut(_binary_base(chan) if chan else chan)


def _row_cmap_name(ctx, r: int, chan=None) -> str | None:
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


def _export_binary_mask(ctx, cache, well, chan, tp, fov, binary_mode, crop_tool) -> np.ndarray | None:
    """Compute the shown-cells array for a binary-mode cell (cropped).

    This **is** the live selection: the grid, the export figure and the
    clipboard copies all reach binary mode through here. Two sibling renderers
    (``_render_mask_binary`` / ``_render_threshold_binary``) used to do the same
    job for the live thumbnails and were left behind, imported and never called,
    while docstrings on both sides went on calling them "the live path" — so a
    fix applied to one of them could not affect anything. They are gone.

    The **label ids survive**: shown cells keep their own id and everything else
    is 0, rather than the array being flattened to a 0/1 membership mask. ``> 0``
    still selects exactly the same pixels, so the white silhouette is unchanged,
    but the ids are what let a cell be painted one colour per nucleus (see
    ``_compose_image_table_rgb``'s ``label_colors``); a boolean array has thrown
    away the only thing that distinguishes one nucleus from its neighbour.
    """
    import numpy as _np

    mask_arr = _load_mask_array(ctx, cache, well, tp, fov)
    if mask_arr is None:
        return None
    if binary_mode == "threshold":
        above_by_nid: dict[int, bool] = {}
        try:
            from well_viewer.features.image_browser.review_image.controller import (
                review_build_threshold_map, review_norm_fov,
            )
            # The CSV row keys are
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
            )
        except Exception:
            # ``{}`` here rendered an all-black silhouette — the *same* picture
            # a cell with genuinely no nuclei above threshold produces, which
            # is a real and common result. The failure was therefore
            # indistinguishable from the measurement. ``None`` is the value
            # this function already uses for "cannot render this cell", and the
            # caller labels it; the log line names the cell.
            logger.exception(
                "image_table: could not build the threshold map for well=%r "
                "channel=%r tp=%r fov=%r — the cell is reported unrenderable "
                "rather than drawn as an empty mask", well, chan, tp, fov,
            )
            return None
        if above_by_nid is None:
            # The threshold could not be evaluated for this field — no rows for
            # the well, or none covering this (fov, tp). Drawing it would produce
            # the all-black silhouette that means "measured, nothing passed", so
            # the cell is reported unrenderable and the caller labels it, exactly
            # as for the exception above. ``or {}`` used to stand here and made
            # the two cases the same picture.
            logger.info(
                "image_table: no threshold measurement covers well=%r channel=%r "
                "tp=%r fov=%r — the cell is reported unrenderable rather than "
                "drawn as an empty mask", well, chan, tp, fov,
            )
            return None
        m = _np.asarray(mask_arr)
        passing = [nid for nid, ok in above_by_nid.items() if ok]
        bm = _np.isin(m, passing) if passing else _np.zeros(m.shape, dtype=bool)
    else:  # "mask"
        m = _np.asarray(mask_arr)
        bm = m > 0
    labels = _np.where(bm, m, 0).astype(_np.int32)
    if crop_tool is not None:
        labels = crop_tool.apply_to_array(labels)
    return labels

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
    label_colors: bool = False,
) -> np.ndarray | None:
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

    # 2. Binary toggle wins over the fluorescence: paint the supplied cells (the
    # full mask, or the above-threshold ones) on black. ``label_colors`` gives
    # each one its own colour instead of white — all-white cells that touch read
    # as a single blob, so two nuclei sharing a border are indistinguishable from
    # one large object. The white-or-colour decision is ``core.frame_render``'s,
    # shared with the Playground so a nucleus looks the same in both panes.
    if binary_mask is not None:
        from well_viewer.core.frame_render import fill_label_rgb
        cm = _np.asarray(binary_mask, dtype=_np.int32)
        rgb = _np.zeros(cm.shape[:2] + (3,), dtype=_np.uint8)
        fill_label_rgb(rgb, cm, cm > 0, bool(label_colors))
        return rgb

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


def _shared_channel_lut(ctx, row_idx: int) -> tuple[float, float] | None:
    """The shared per-channel display default for row *row_idx*'s channel (S4).

    Returns ``None`` when there's no shared default (then the row auto-fits as
    before). Lets a brightness tuned in Review Image flow in as the default."""
    state = ctx.state
    image_view = getattr(state, "image_view", None)
    if image_view is None or not hasattr(image_view, "get_lut"):
        return None
    return image_view.get_lut(_row_channel(ctx, row_idx))


def _auto_lut_for(ctx, chan) -> tuple[float, float] | None:
    """The pooled Auto-LUT window for *chan*'s base channel, or None.

    Populated once per Generate by the controller's pooling pass
    (``auto_lut_by_channel``) so a channel left on Auto uses ONE brightness window
    across every cell instead of each cell auto-fitting to its own min/max."""
    pooled = getattr(ctx.state.image_table, "auto_lut_by_channel", None) or {}
    if not pooled or not chan:
        return None
    return pooled.get(_binary_base(chan).strip().lower())


def _parse_lut(ctx, row_idx: int, chan=None) -> tuple[float | None, float | None]:
    """Read (lo, hi) for grid row *row_idx*; falls back to the shared per-channel
    default, then the pooled per-channel Auto window, else (None, None) for auto-fit.

    With a builder channel spec (``chan`` given) the range comes from the
    channel's own ``lut_min``/``lut_max`` (None ⇒ inherit the shared per-channel
    default, then the pooled Auto window, then per-cell auto-fit). Without a spec
    the range inherits the shared per-channel default (a brightness tuned in Review
    Image) for *chan* — or, only when the caller named no channel at all, for the
    row's representative channel — then the pooled Auto window, else auto-fits. The
    pooled window is what makes Auto comparable cell-to-cell (see
    ``controller._compute_auto_luts``)."""
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
    # No channel spec → inherit the shared per-channel default for the channel the
    # CALLER named. ``_shared_channel_lut`` answers for the row's representative
    # channel — ``cells[row_idx][0]['chan']``, i.e. column 0's — which is only the
    # right question when no channel was named at all. Asking it for a named
    # channel handed every cell in the row column 0's brightness window, so a row
    # of mixed channels was drawn with one channel's LUT.
    shared = (_shared_channel_lut_for(ctx, chan) if chan
              else _shared_channel_lut(ctx, row_idx))
    if shared is not None:
        return shared
    pooled = _auto_lut_for(ctx, chan or _row_channel(ctx, row_idx))
    return pooled if pooled is not None else (None, None)


def _row_display_opts(ctx, row_idx: int, chan=None) -> tuple[bool, bool, str]:
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


def _row_label_colors(ctx, row_idx: int, chan=None) -> bool:
    """Whether a binary cell paints one colour per label instead of all white —
    the channel's builder spec ``label_colors`` (``channel_opts[chan]``); no spec
    → False, which is the plain white silhouette this has always drawn."""
    spec = _channel_opts_for(ctx, chan)
    return bool(spec.get("label_colors", False)) if spec is not None else False


def _row_log_scale(ctx, row_idx: int, chan=None) -> bool:
    """Whether the cell shows its image on a log intensity scale — the channel's
    builder spec ``log`` (``channel_opts[chan]``); no spec → False."""
    spec = _channel_opts_for(ctx, chan)
    return bool(spec.get("log", False)) if spec is not None else False

def reset_scan_misses(ctx) -> None:
    """Start a render pass with clean per-pass load bookkeeping.

    Called at the top of every Generate / export run. Three records are scoped to
    a single pass and cleared here: the empty-scan set (without the reset a
    well/channel that came up empty once would stay written off for the rest of
    the session), the raw-fallback set, and the truncated-inventory set — the
    last two are counted by the status line, so both have to describe the pass
    that just ran and no earlier one. The refused-scale-bar set is cleared for
    the same reason.
    """
    st = getattr(getattr(ctx, "state", None), "image_table", None)
    if st is None:
        return
    for attr in ("scan_misses", "raw_fallbacks", "partial_scans",
                 "scale_bar_refusals"):
        rec = getattr(st, attr, None)
        if isinstance(rec, set):
            rec.clear()


def _scan_misses(ctx) -> set:
    """The current render pass's set of (well, channel, variant) scans that came
    up empty — see :func:`load_well_channel`.

    Lives on ``ctx.state.image_table.scan_misses`` and is cleared at the top of
    every ``image_table_generate`` / export run. A ctx without that field (a
    stub, or a caller outside the Image Table) gets a throwaway set, which
    suppresses nothing.
    """
    st = getattr(getattr(ctx, "state", None), "image_table", None)
    misses = getattr(st, "scan_misses", None)
    return misses if isinstance(misses, set) else set()


def _raw_fallbacks(ctx) -> set:
    """The current render pass's set of ``(well, channel, tp, fov)`` cells that
    asked for the corrected (top-hat) image and were served the raw one instead.

    Same lifetime and same stub tolerance as :func:`_scan_misses`. The Generate
    status line reports its size, so a grid that quietly mixes corrected and raw
    frames says so rather than looking like a uniformly corrected table.
    """
    st = getattr(getattr(ctx, "state", None), "image_table", None)
    rec = getattr(st, "raw_fallbacks", None)
    return rec if isinstance(rec, set) else set()


def _partial_scans(ctx) -> set:
    """The current render pass's set of ``((well, channel, variant), reason)``
    inventories that were TRUNCATED — see :func:`load_well_channel`.

    Same lifetime and same stub tolerance as :func:`_scan_misses`. Kept apart
    from that set because the two are opposite failures: a miss draws no cell at
    all, while a truncated inventory draws real cells and simply has fewer of
    them than the dataset holds — the grid looks finished, which is why the
    status line has to say otherwise.
    """
    st = getattr(getattr(ctx, "state", None), "image_table", None)
    rec = getattr(st, "partial_scans", None)
    return rec if isinstance(rec, set) else set()


def _scale_bar_refusals(ctx) -> set:
    """The current render pass's ``(well, channel, tp, fov)`` cells whose scale
    bar did not fit and was therefore not drawn — see :func:`bar_fits`.

    Same lifetime and same stub tolerance as :func:`_scan_misses`.
    """
    st = getattr(getattr(ctx, "state", None), "image_table", None)
    rec = getattr(st, "scale_bar_refusals", None)
    return rec if isinstance(rec, set) else set()


def scale_bar_refusal_report(ctx) -> list[str]:
    """The wells holding a cell whose scale bar would not fit, sorted.

    The public reader over :func:`_scale_bar_refusals`, so the status line does
    not have to know the set's tuple shape. Empty on a pass where every bar fit.
    """
    return sorted({str(key[0]) for key in _scale_bar_refusals(ctx)
                   if key and key[0]})


def can_carry_bar(rgb, bar_px: int) -> bool:
    """Will a *bar_px* bar actually be drawn into *rgb*?

    :func:`bar_fits` answers the length question; this answers the whole one, by
    also applying the array guards :func:`draw_cell_chrome` returns early on — an
    array that is not 3-channel, and one under 4 px on a side. A 200x3 crop
    passes the length test and draws nothing, which is the silent hole this whole
    change exists to close, reopened one guard along.

    Comparing the array before and after is not the alternative: an identity
    label is burned in by the same call, so a labelled cell's array changes
    whether or not its bar was drawn.
    """
    if rgb is None or int(bar_px) <= 0:
        return False
    try:
        if rgb.ndim != 3 or rgb.shape[2] < 3:
            return False
        h, w = rgb.shape[:2]
    except (AttributeError, IndexError, ValueError):
        return False
    return h >= 4 and w >= 4 and bar_fits(w, h, bar_px)


def scale_bar_refusal_clause(ctx) -> str:
    """The one sentence describing this pass's refused scale bars, or ``""``.

    One wording, not one per surface: the Generate line and the saved-figure line
    report the same fact — these wells carry no bar, and here are the two
    controls that would change it. Only the framing around it differs, and that
    is the caller's sentence rather than this clause.
    """
    wells = scale_bar_refusal_report(ctx)
    if not wells:
        return ""
    return (f" No scale bar on {len(wells)} well{'' if len(wells) == 1 else 's'} "
            f"({', '.join(wells)}) — the bar is longer than those cells can hold; "
            "shorten Bar length µm or widen the crop.")


def apply_cell_chrome(ctx, rgb, chrome_kwargs, cell):
    """Burn one cell's chrome in, recording a scale bar that would not fit.

    The one place a cell's chrome is drawn, so the refusal is reported exactly
    once per cell and in exactly one wording. :func:`draw_cell_chrome` decides
    whether the bar fits and is a pure array function with no idea which cell it
    holds; this knows the cell and owns the render pass, which is the split
    ``frame_load``'s ``scan_misses`` / ``raw_fallbacks`` already use.

    *chrome_kwargs* is :func:`cell_chrome_kwargs`' output, or ``None`` for a cell
    that carries no chrome at all — in which case *rgb* comes back untouched.
    """
    if rgb is None or not chrome_kwargs:
        return rgb
    bar_px = int(chrome_kwargs.get("bar_px") or 0)
    # Asked for and not drawn, for any of the reasons ``can_carry_bar`` knows —
    # the length, the channel count, a degenerate side. Decided before the draw,
    # because afterwards an identity label makes a refused cell look identical to
    # a drawn one.
    if bar_px > 0 and not can_carry_bar(rgb, bar_px):
        _scale_bar_refusals(ctx).add(
            (cell.get("well"), str(cell.get("chan") or "").lower(),
             cell.get("tp"), cell.get("fov")))
    return draw_cell_chrome(rgb, **chrome_kwargs)


def partial_scan_clause(ctx) -> str:
    """The one sentence describing this pass's truncated inventories, or ``""``.

    The sibling of :func:`scale_bar_refusal_clause`, and one wording for the
    same reason. Two hand-written sentences for one fact drift: the Generate
    line counted the wells and named the scans' reasons, while the saved-PNG
    line listed the wells without either — so the artifact that outlives the
    session carried *less* than the transient line, which is backwards.

    Neutral about what the artifact is. "This grid" and "this file" is the
    caller's framing; the fact is that frames the dataset holds are not in what
    was just produced, and that is the same fact in both.

    **Every surface that can produce a short artifact says it.** A grid drawn
    from a truncated inventory is made entirely of real images, so nothing else
    counts the gap and the result reads as complete — which is §7's rule
    exactly: degraded output must never be indistinguishable from good output.
    """
    wells, reasons = partial_scan_report(ctx)
    if not wells:
        return ""
    return (f" WARNING: the image list for {len(wells)} well"
            f"{'' if len(wells) == 1 else 's'} ({', '.join(wells)}) is "
            f"INCOMPLETE — frames the dataset holds are missing from this "
            f"artifact: {'; '.join(reasons) or 'a scan stopped short'}.")


def partial_scan_report(ctx) -> tuple[list[str], list[str]]:
    """``(wells, reasons)`` for the truncated inventories of the current pass.

    The public reader over :func:`_partial_scans`, so the status line does not
    have to know the set's tuple shape. Both lists are de-duplicated and stable
    (sorted wells, reasons in first-seen order); empty on a clean pass.
    """
    rec = _partial_scans(ctx)
    wells = sorted({str(key[0]) for key, _reason in rec})
    reasons = list(dict.fromkeys(str(r) for _k, r in rec if r))
    return wells, reasons


def _load_ratio_array(ctx, cache, well, ratio, tp, fov, *, use_tophat) -> Path | None:
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

def cell_display_rgb(ctx, cache, cell, r: int, crop_tool, arr) -> np.ndarray | None:
    """Return the uint8 (H, W, 3) RGB shown for grid cell ``cell`` in row *r*.

    Single source of truth for "what pixels does this cell display": applies the
    shared crop, resolves the row's LUT window / colormap / display toggles, and
    delegates to :func:`_compose_image_table_rgb`. Callers pass the already-loaded
    ``arr`` (the live path passes the frame it just loaded; the export path and the
    composite column lazy-load theirs, which is a decode-cache hit) so this helper
    stays free of the ratio-load / controller coupling. Returns
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
    # A variant token carries its own render: "GFP (binary)" is the base channel's
    # threshold-binary and "GFP (mask)" its cell mask, whatever the row says.
    # Resolve the real channel and let the token settle the mode — the spec above
    # agrees when there is one, and this is what a token with no spec (the legacy
    # Advanced selector, an older saved state) resolves through.
    if is_variant_token(chan):
        if _is_binary_variant(chan):
            binary_mode = "threshold"
        elif _is_mask_variant(chan):
            binary_mode = "mask"
        else:                                   # "(raw)" — an intensity render
            binary_mode = ""
        chan = _base_channel(chan)
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
        if binary_mask is None:
            # No segmentation mask for this cell. Falling through would render
            # the intensity image instead — indistinguishable from a cell that
            # simply has no signal above threshold, and the reason a mask that
            # resolves for only some cells reads as "half the grid ignored the
            # switch". Report it as unrenderable and let the caller label it.
            logger.warning(
                "image_table: no segmentation mask for well=%r channel=%r "
                "tp=%r fov=%r — cannot render the %s view for this cell",
                well, chan, tp, fov, binary_mode,
            )
            return None
    return _compose_image_table_rgb(
        cropped, mask_arr,
        lo=lo, hi=hi,
        cmap_name=_row_cmap_name(ctx, r, chan_token),
        is_overlay=_is_nuc_seg(chan),
        binary_mask=binary_mask,
        show_boundaries=show_boundaries,
        log_scale=_row_log_scale(ctx, r, chan_token),
        label_colors=_row_label_colors(ctx, r, chan_token),
    )


def composite_additive_rgb(rgbs) -> np.ndarray | None:
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


def _ratio_for_overlay(ctx, chan) -> RatioMetric | None:
    """The RatioMetric whose display label matches *chan* (case-insensitive), else
    None. Inlined so this render layer stays free of a ``grid_layout`` import
    (which would cycle: grid_layout imports this module).

    Resolved defensively because this module's render helpers are also driven by
    callers that carry no channel state at all (the render-resilience tests pass
    a bare stub, and some pass no ctx); a context without ratio metrics simply
    has no ratio to find, which is not a failure worth an exception."""
    metrics = getattr(getattr(getattr(ctx, "state", None), "channel", None),
                      "ratio_metrics", None)
    for r in (metrics or []):
        if r.display_label().upper() == str(chan or "").strip().upper():
            return r
    return None


def overlay_cell_rgb(ctx, cache, cell, r: int, crop_tool, channels) -> np.ndarray | None:
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

_CHROME_COLORS: dict[str, tuple[int, int, int]] = {
    "white": (255, 255, 255), "black": (0, 0, 0), "yellow": (255, 255, 0),
    "cyan": (0, 255, 255), "magenta": (255, 0, 255), "red": (255, 0, 0),
    "green": (0, 255, 0),
}


def _chrome_rgb(name) -> tuple[int, int, int]:
    return _CHROME_COLORS.get(str(name or "white").strip().lower(), (255, 255, 255))


def _corner_cell(rows: int, cols: int, code: str, occupied=None) -> tuple | None:
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


def _load_chrome_font(px: int) -> Any:
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


def bar_margin(h: int) -> int:
    """The inset a burned-in scale bar keeps from the image edge, in pixels."""
    return max(2, int(h) // 25)


def bar_fits(w: int, h: int, bar_px: int) -> bool:
    """Can a *bar_px*-long scale bar be drawn at its true length in a *w* x *h* image?

    The one answer, so :func:`draw_cell_chrome` and :func:`apply_cell_chrome`
    cannot disagree about which cells carry a bar. A bar that does not fit is
    **not drawn short**: the length is what states the physical scale, so a
    clamped bar is a wrong number burned into the picture, indistinguishable from
    a correct one (§7 of ``CLAUDE.md``). The caller reports the refusal; this
    only decides it.
    """
    return int(bar_px) > 0 and int(bar_px) <= int(w) - 2 * bar_margin(h)


def draw_cell_chrome(rgb, *, bar_px: int = 0, bar_corner: str = "br",
                     bar_color: str = "white", bar_thickness_px: int = 0,
                     label_text: str = "",
                     label_corner: str = "tl", label_font_px: int = 12,
                     label_color: str = "white", label_bg: bool = True):
    """Burn a scale bar and/or an identity label into an ``(H, W, 3)`` uint8 RGB.

    Sizes are proportional to image height — the bar length is ``bar_px`` source
    pixels; margins scale with ``H``; the label font and the bar *thickness* scale
    relative to a nominal 240 px cell — so a mark reads the same at the live
    thumbnail size and at full export resolution. ``bar_thickness_px`` is that
    nominal thickness (≤ 0 → a sensible auto default); a too-thin bar can vanish
    when the cell is downscaled, so it's user-tunable. Returns a mutated copy, or
    the input unchanged when there's nothing to draw.

    **A bar longer than the image is not drawn at all** (:func:`bar_fits`). It
    used to be clamped to the image width, which drew a 50 µm bar as 37.7 µm on a
    narrow crop and presented it as 50 — a wrong number burned into an exported
    figure. Nothing here reports the refusal, because this function does not know
    which cell it is drawing; :func:`apply_cell_chrome` is the wrapper that does.
    """
    import numpy as _np

    if rgb is None or (not bar_px and not label_text):
        return rgb
    a = _np.array(rgb, dtype=_np.uint8, copy=True)
    if a.ndim != 3 or a.shape[2] < 3:
        return rgb
    H, W = a.shape[:2]
    if H < 4 or W < 4:
        return rgb

    # Scale bar — a filled rectangle with a contrasting halo for legibility.
    # A bar too long for the image is REFUSED, never shortened to fit: see
    # :func:`bar_fits`.
    if bar_fits(W, H, bar_px):
        blen = int(bar_px)
        if blen >= 1:
            # Thickness scales with the cell (like the label font) so it's stable
            # across live/export; user value is px-at-240, ≤0 falls back to auto.
            if bar_thickness_px and bar_thickness_px > 0:
                thick = max(1, int(round(float(bar_thickness_px) * H / 240.0)))
            else:
                thick = max(2, H // 100)
            margin = bar_margin(H)
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
            # Colour: "auto" (default) picks black or white for maximum contrast
            # against the pixels the bar covers — essential because the default
            # "Greys" colormap renders dim fluorescence as a near-white background,
            # where a fixed white bar is invisible. A named colour is honoured as-is.
            if str(bar_color or "").strip().lower() in ("", "auto"):
                region = a[y0:min(H, y1 + 1), max(0, x0 - 1):min(W, x1 + 1), :3]
                bright = float(region.mean()) > 140.0 if region.size else False
                col = (0, 0, 0) if bright else (255, 255, 255)
            else:
                col = _chrome_rgb(bar_color)
            # A contrasting halo (≥2 px) frames the bar so it reads even against a
            # same-toned background.
            halo = (0, 0, 0) if col != (0, 0, 0) else (255, 255, 255)
            pad = max(1, thick // 3)
            a[max(0, y0 - pad):min(H, y1 + pad), max(0, x0 - pad):min(W, x1 + pad)] = halo
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


def cell_chrome_kwargs(ctx, r: int, c: int, cell, plan, overlay_chans=None) -> dict | None:
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
        bar_thickness_px=int(getattr(st, "scale_bar_thickness_px", 4)),
        label_text=label_text,
        label_corner=getattr(st, "label_img_corner", "tl"),
        label_font_px=int(getattr(st, "label_font_px", 12)),
        label_color=getattr(st, "label_color", "white"),
        label_bg=bool(getattr(st, "label_bg", True)),
    )


#: Burned into any cell served the raw frame in place of the corrected one.
#: Short because it shares the cell with the scale bar and the identity label.
UNCORRECTED_MARK = "raw"

#: Spelled out in the cell's tooltip, where there is room for the reason.
UNCORRECTED_TOOLTIP = "shown uncorrected — no corrected image for this field"


def cell_source_channels(ctx, cell, overlay_chans=None) -> list[str]:
    """Every REAL channel whose frame *cell* draws from.

    A cell's ``chan`` is what the axis calls it, and for two kinds of cell that
    is not a channel the loader ever reads:

    * an :data:`OVERLAY_CHAN` cell blends the whole selected channel set
      (``overlay_cell_rgb``), which the caller supplies as *overlay_chans*
      because only the layout knows it;
    * a ratio cell's token is a :class:`RatioMetric` display label, and the
      loader reads the metric's numerator and denominator
      (:func:`_load_ratio_array`).

    Everything else draws from its own channel. Returns ``[]`` for an unset cell.
    Used wherever a per-cell answer has to be looked up in a record the loader
    keyed by the channel it actually opened — see :func:`is_raw_fallback`.
    """
    chan = (cell.get("chan") or "") if hasattr(cell, "get") else ""
    if not chan:
        return []
    if chan == OVERLAY_CHAN:
        return [str(c) for c in (overlay_chans or []) if c and c != OVERLAY_CHAN]
    ratio = _ratio_for_overlay(ctx, chan)
    if ratio is not None:
        return [c for c in ((ratio.numerator_channel or "").strip(),
                            (ratio.denominator_channel or "").strip()) if c]
    return [chan]


def is_raw_fallback(ctx, cell, overlay_chans=None) -> bool:
    """Whether *cell* was served the raw frame after asking for the corrected one.

    Reads the current render pass's :func:`_raw_fallbacks` set, whose keys
    ``_load_array`` writes as ``(well, base_channel_lower, str(tp), str(fov))``.
    The set is populated by whichever pass loaded the cell first — the pooled
    Auto-LUT pass runs before the render loop — so this answers correctly for
    both.

    The lookup is per SOURCE channel (:func:`cell_source_channels`), not per axis
    token. An overlay cell and a ratio cell are both drawn from channels whose
    names appear nowhere on the grid, so keying on the cell's own token asked the
    record for a channel the loader never wrote and answered "corrected" for a
    cell that is not: the fallback was counted under a channel the user cannot
    see and badged on nothing. True when ANY source channel fell back, because a
    blend or a ratio carrying one uncorrected input is an uncorrected image.
    """
    well = (cell.get("well") or "") if hasattr(cell, "get") else ""
    if not well:
        return False
    tp = str(cell.get("tp") or "")
    fov = str(cell.get("fov") or "")
    rec = _raw_fallbacks(ctx)
    return any((well, _binary_base(ch).strip().lower(), tp, fov) in rec
               for ch in cell_source_channels(ctx, cell, overlay_chans))


def _mark_corner(st) -> str:
    """A cell corner the scale bar and the identity label have not claimed."""
    taken = {getattr(st, "label_img_corner", "tl"),
             getattr(st, "scale_bar_img_corner", "br")}
    for corner in ("tr", "bl", "tl", "br"):
        if corner not in taken:
            return corner
    return "tr"


def mark_uncorrected(ctx, cell, rgb, overlay_chans=None):
    """Burn :data:`UNCORRECTED_MARK` into *rgb* when *cell* fell back to raw.

    A cell whose corrected (top-hat) image is missing still draws — it draws the
    raw frame, which carries the camera offset and the background the correction
    would have removed. That is a different intensity domain from its corrected
    neighbours, so one LUT window cannot suit both: a window tuned on the
    corrected population pushes the raw cells past its ceiling, where they
    saturate and stop responding to any further LUT edit. The grid then looks
    like a table that ignores its own brightness control on part of itself.

    The count already reaches the Generate status line, but a count does not say
    *which* cells and does not survive the next status message. This puts the
    fact on the cell, through the same ``draw_cell_chrome`` the scale bar and the
    identity label use, so the live thumbnail and the exported figure carry it
    identically. Returns *rgb* untouched for a cell that got what it asked for.
    """
    if rgb is None or not is_raw_fallback(ctx, cell, overlay_chans):
        return rgb
    st = ctx.state.image_table
    return draw_cell_chrome(
        rgb,
        label_text=UNCORRECTED_MARK,
        label_corner=_mark_corner(st),
        label_font_px=int(getattr(st, "label_font_px", 12)),
        label_color=getattr(st, "label_color", "white"),
        label_bg=True,
    )
