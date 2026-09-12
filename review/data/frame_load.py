"""Resolving a frame address to pixels: the layer under every image grid.

An *address* here is ``(well, channel-token, timepoint, fov)`` — what a user
picked in a dropdown or dropped on an axis — and the job of this module is to
turn one into the float32 array it names, or into the int32 segmentation mask
beside it. That is a data-layer question: it is about what the dataset holds
and how its filename tokens line up, not about how anything is drawn.

It lives here because three separate tabs ask it. The Image Table composes a
grid of these, the Cell Browser crops per-cell thumbnails out of them, and the
Playground corrects a single one. Each of those is a removable feature, and for
a long time the answer lived in one of them (``image_table/image_compose.py``)
with the other two importing it sideways — so deleting the Image Table broke
the Cell Browser, and the profile manifest's claim that each could be removed
independently was not true. Nothing here imports a feature.

**Channel tokens carry a render variant, and resolving one is addressing, not
rendering.** ``"GFP (binary)"``, ``"GFP (mask)"`` and ``"GFP (raw)"`` all load
the *same* pixels as ``"GFP"``; what differs is what the caller then draws with
them. So the suffix vocabulary and :func:`base_channel` are here, while the
rendering those suffixes select stays with the renderer.

**Two kinds of shortfall are reported rather than absorbed**, both through sets
the caller owns and passes in:

* ``scan_misses`` — a ``(well, channel, variant)`` scan that came up empty, so
  a caller rendering many cells can say it once instead of once per cell.
* ``raw_fallbacks`` — a cell that asked for the corrected (top-hat) image and
  was served the raw one. A pipeline commonly corrects a subset of a well's
  frames, and a grid that quietly mixes corrected and raw frames would look
  like a uniformly corrected one.
* ``partial_scans`` — a ``(well, channel, variant)`` whose image inventory was
  truncated: the walk over the archive or folder raised part way and returned
  what it had. The inventory that comes back is a short dict of real images,
  which is the same shape as a well that genuinely holds that few frames, so
  the grid drawn from it is short with nothing to say why.

They are parameters rather than something read off ``ctx`` because they belong
to the *caller's* pass. A caller that passes neither still gets its pixels; it
simply has no pass to report against.

Passing no ``scan_misses`` is not free, though, and the throwaway set below is
not merely inert: suppression is what stops one absent well/channel being
re-scanned per cell. Measured over ten cells against one absent channel, a
caller sharing a set scans once and a caller passing none scans ten times, each
walk emitting its own log line. The answers are identical either way — this is
wall-clock and log noise, not correctness — but a caller rendering many cells
should thread a set through rather than accept the default.
"""

from __future__ import annotations

# Names used only in annotations. Under ``from __future__ import
# annotations`` these are never evaluated, so importing them for real would
# add startup cost and, for several of these, an import cycle.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

import logging

from review.data.image_resolver import normalize_numeric_token
from domain.tp_match import tp_close

logger = logging.getLogger(__name__)


# ── Channel-token vocabulary ─────────────────────────────────────────────────

NUC_SEG_TOKEN = "NUC+SEG"

# A "binary" channel variant — e.g. ``"GFP (binary)"`` — is a virtual channel that
# renders its base channel as the ThreshFracOn threshold-binary (positive cells
# white). It flows through the axis picker / product / per-cell combo like any
# channel token; the two leaf helpers that turn a token into pixels
# (``load_frame_array`` for the image, ``cell_display_rgb`` for the render) decode it:
# load the *base* channel, force ``binary_mode="threshold"``.
BINARY_SUFFIX = " (binary)"
# The same idea for the other two non-default renders. A channel's render mode is
# part of its axis token (``state.ChannelSpec.axis_token``) because the token is
# the grid's identity for that channel: one token, one row. Without these, a
# channel dropped twice — once as its image, once as its cell mask — produced one
# token, one ``channel_opts`` key, and one row, and the second chip disappeared.
# ``corrected`` keeps the bare token so previously saved views still resolve.
MASK_SUFFIX = " (mask)"
RAW_SUFFIX = " (raw)"
VARIANT_SUFFIXES = (BINARY_SUFFIX, MASK_SUFFIX, RAW_SUFFIX)


def binary_variant(chan: str) -> str:
    """The binary-variant token for a base channel (``"GFP"`` → ``"GFP (binary)"``)."""
    return f"{chan}{BINARY_SUFFIX}"


def has_suffix(chan: str, suffix: str) -> bool:
    s = str(chan or "").strip().lower()
    return s.endswith(suffix.lower()) and len(s) > len(suffix)


def base_channel(chan: str) -> str:
    """Strip a trailing render suffix (case-insensitive) → the base channel token."""
    s = str(chan or "").strip()
    for sfx in VARIANT_SUFFIXES:
        if has_suffix(s, sfx):
            return s[: len(s) - len(sfx)].strip()
    return s


def is_nuc_seg(chan: str) -> bool:
    return str(chan or "").strip().upper() == NUC_SEG_TOKEN


def is_variant_token(chan: str) -> bool:
    """Whether *chan* is any render variant rather than a plain channel token."""
    return any(has_suffix(chan, sfx) for sfx in VARIANT_SUFFIXES)


# Normalize a numeric-or-string FOV/timepoint to a stable form: ``"1"`` and
# ``"1.0"`` collapse to ``"1"``, non-numeric strings pass through. This matches
# the form emitted by the filename extractors that key the image dicts in
# ``find_well_images_and_masks``.
norm_token = normalize_numeric_token


def numeric_token(value: object) -> float | None:
    """Reduce a timepoint/FOV string to a numeric key for cache-key matching.

    Tries (in order):
      1. ``parse_timepoint_hours`` — handles ``"01d02h30m"``, ``"48h"``,
         ``"2d"``, ``"30m"``, ``"T01"``, plain numbers, etc.
      2. First contiguous digit run as int (legacy fallback for ``"T01"`` →
         ``1`` when ``parse_timepoint_hours`` returns None).

    Returns ``None`` when nothing parsable is found.
    """
    from review.data.data_loading import parse_timepoint_hours
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


# ── Address → pixels ─────────────────────────────────────────────────────────

def _record_partial_scan(partials: set, key: tuple, result) -> None:
    """Note a truncated inventory in the caller's *partials* set, once per key.

    *result* is whatever ``find_well_images_and_masks`` handed back. A
    :class:`~review.data.zip_members.ScanResult` that stopped short carries
    ``partial``/``reason``; a plain dict (a stub, or the empty fallback after a
    discovery exception) carries neither and is left alone. The set doubles as
    the dedupe for the log line — a grid of ninety cells over one truncated
    well/channel should say so once, not ninety times.
    """
    if not getattr(result, "partial", False):
        return
    reason = str(getattr(result, "reason", "") or "a scan stopped short")
    if any(k == key for k, _r in partials):
        return
    partials.add((key, reason))
    well, chan_lower, variant = key
    logger.warning(
        "frame_load: the %s image inventory for well=%r channel=%r is "
        "INCOMPLETE — %s. Cells drawn from it are real, but frames the dataset "
        "holds are missing from the grid.",
        variant, well, chan_lower, reason,
    )


def load_well_channel(
    ctx,
    cache: dict[tuple[str, str, str], dict],
    well: str,
    chan_lower: str,
    *,
    use_tophat: bool = False,
    scan_misses: set | None = None,
    partial_scans: set | None = None,
) -> dict:
    """Return the (fov, tp) → ImgRef dict for (well, channel), with caching.

    For the special ``"nuc+seg"`` virtual channel the returned dict is the
    pre-rendered segmentation overlay refs keyed the same way; everything
    else returns the fluorescence dict for the requested channel token.

    When ``use_tophat`` is True, the pre-filtered tophat dict is returned
    instead. Tophat is unavailable for the overlay channel — it falls back
    to the raw fluor lookup. Cells whose (fov, tp) are missing from the
    tophat dict also fall back to raw at lookup time (handled in
    ``load_frame_array``).

    **Only non-empty results are cached.** ``cache`` outlives every Generate
    (it is dropped on dataset load, not per render), so storing an empty scan
    in it pins "this well/channel has no images" for the rest of the session —
    and a scan can come up empty for reasons that are not permanent: a drive
    that stalled or briefly unmounted, a directory being rewritten by a
    running pipeline, a transient OSError inside the walk. Once pinned, the
    affected cells read "(no image)" forever while their neighbours render,
    which is exactly the half-empty grid this avoids. An empty result is
    instead recorded in the caller's *scan_misses* set, so one render pass
    still scans a genuinely-absent well/channel only once, and the next
    Generate retries it. A caller that passes no set gets a throwaway one,
    which suppresses nothing beyond this call.

    **A truncated scan is reported, not absorbed.** ``find_well_images_and_masks``
    returns :class:`~review.data.zip_members.ScanResult` inventories that
    know whether their walk reached the end of the archive or folder. A
    truncated one holds real images and is a perfectly ordinary dict, so a grid
    built from it is simply short — indistinguishable from a well that holds
    that few frames. Each such ``(well, channel, variant)`` is added to the
    caller's *partial_scans* set (which also dedupes the warning), so a pass
    rendering many cells can say once that its grid is missing frames the
    dataset holds. It is recorded on cache hits as well as misses, because the
    flag rides on the cached inventory and every cell drawn from it is equally
    affected.
    """
    from review.data.image_discovery import find_well_images_and_masks

    is_overlay = chan_lower == NUC_SEG_TOKEN.lower()
    variant = "tophat" if (use_tophat and not is_overlay) else "raw"
    key = (well, chan_lower, variant)
    partials = partial_scans if partial_scans is not None else set()
    if key in cache:
        _record_partial_scan(partials, key, cache[key])
        return cache[key]
    misses = scan_misses if scan_misses is not None else set()
    if key in misses:
        return {}
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
        # Not fatal — the caller renders "(no image)" for this cell — but never
        # silent, and never cached: the retry below is what lets a stalled drive
        # recover on the next Generate instead of leaving a permanent hole.
        logger.warning(
            "frame_load: image discovery failed for well=%r channel=%r; "
            "will retry on the next Generate", well, chan_lower, exc_info=True,
        )
        fluor, overlay, mask, tophat = {}, {}, {}, {}
    # Cache the mask dict (keyed by a sentinel channel token) so
    # boundary/binary overlays can access it without a second IO round-trip.
    # An empty scan is not cached — the first channel to be scanned would
    # otherwise pin "this well has no masks" for the session, and every binary /
    # boundary cell in the well would silently lose its overlay.
    if mask:
        cache.setdefault((well, "__mask__", "raw"), mask)
    if is_overlay:
        result = overlay
    elif use_tophat:
        result = tophat
    else:
        result = fluor
    _record_partial_scan(partials, key, result)
    if result:
        cache[key] = result
    else:
        misses.add(key)
        logger.info(
            "frame_load: no %s images found for well=%r channel=%r "
            "(retried on the next Generate)", variant, well, chan_lower,
        )
    # Stash the raw fluor dict alongside the tophat dict so load_frame_array can
    # fall back to raw for any (fov, tp) the tophat scan missed.
    if use_tophat and not is_overlay and fluor:
        cache.setdefault((well, chan_lower, "raw"), fluor)
    return result


def match_image_ref(images: dict, fov: str, tp: str):
    """Resolve an image ref for ``(fov, tp)``; return ``(ref, kind)``.

    ``kind`` ∈ ``{"exact", "normalized", "numeric", ""}`` (``""`` = no match),
    letting the caller annotate non-exact resolutions (audit S5).

    Tiers, in order: exact tuple key → normalized-token (``"1.0"`` ↔ ``"1"``)
    → numeric-close (``"T01"`` ↔ ``"1"``, timepoints matched with the S2
    relative tolerance).

    An empty axis matches everything, so when one is empty (an empty dropdown)
    and MORE THAN ONE distinct value on that axis would satisfy the request,
    this prefers "no image" — ``(None, "ambiguous_fov")`` /
    ``(None, "ambiguous_tp")`` — over silently surfacing whichever entry the
    dict happened to yield first. The rule applies to both axes: it was written
    for FOV, and an empty *timepoint* had the same free-for-all match with no
    refusal behind it, so an arbitrary timepoint's frame came back reported as a
    successful ``"normalized"`` resolution. A genuinely single-FOV or
    single-timepoint dataset still resolves unambiguously.
    """
    exact = images.get((str(fov), str(tp)))
    if exact is not None:
        return exact, "exact"

    def _pick(pred):
        # Returns (val, "ok") for a unique resolution, (None, "ambiguous_fov") /
        # (None, "ambiguous_tp") when an unspecified axis spans more than one
        # value, or (None, "") for no match.
        hits = [(cf, ct, val) for (cf, ct), val in images.items() if pred(cf, ct)]
        if not hits:
            return None, ""
        if not str(fov or "").strip() and len({cf for cf, _, _ in hits}) > 1:
            return None, "ambiguous_fov"
        if not str(tp or "").strip() and len({ct for _, ct, _ in hits}) > 1:
            return None, "ambiguous_tp"
        return hits[0][2], "ok"

    target_fov = norm_token(fov)
    target_tp = norm_token(tp)
    val, status = _pick(lambda cf, ct:
        ((not target_tp) or norm_token(ct) == target_tp)
        and ((not target_fov) or norm_token(cf) == target_fov))
    if status.startswith("ambiguous"):
        return None, status
    if val is not None:
        return val, "normalized"

    # Numeric-close: cache keys like ``"T01"`` and dropdown values like ``"1"``
    # both reduce to the same number, rescuing runs where pipeline_info.json's
    # timepoints were stored numerically but the filenames carry a ``T`` prefix
    # (or vice versa). Timepoints use the S2 relative tolerance, so distinct
    # timepoints never merge.
    target_fov_n = numeric_token(fov)
    target_tp_n = numeric_token(tp)

    def _close(a, b) -> bool:
        return a is not None and b is not None and tp_close(a, b)

    val, status = _pick(lambda cf, ct:
        (target_tp_n is None or _close(numeric_token(ct), target_tp_n))
        and (target_fov_n is None or _close(numeric_token(cf), target_fov_n)))
    if status.startswith("ambiguous"):
        return None, status
    if val is not None:
        return val, "numeric"
    return None, ""


def load_frame_array(ctx, cache: dict, well: str, chan_upper: str, tp: str,
                     fov: str, *, use_tophat: bool = False,
                     raw_fallbacks: set | None = None,
                     scan_misses: set | None = None,
                     partial_scans: set | None = None) -> np.ndarray | None:
    """Load the float32 array for one (well, channel, timepoint, fov).

    The dropdown emits tokens normalized via ``norm_token`` (``"1.0"`` →
    ``"1"``) but the cache built by ``find_well_images_and_masks`` is keyed
    on RAW filename tokens via ``make_schema_extractor``. Try the direct
    tuple first, then fall back to a normalized comparison against every
    cache key so the lookup succeeds whichever side ends up holding the
    integer-as-float form.

    When ``fov`` is empty (e.g. pipeline_info has no available_fovs and the
    dropdown is empty), match the first available FOV for the requested
    timepoint so single-FOV datasets still render.

    The corrected (top-hat) variant falls back to raw **per cell**, not only
    when the whole well/channel has no top-hat set. A pipeline commonly corrects
    a subset of a well's frames — it was interrupted, it was re-run over fewer
    timepoints, or the correction was only ever asked for on some fields — and
    the resulting dict is non-empty but does not cover every ``(fov, tp)`` the
    grid asks for. Resolving only against it left exactly those cells with no
    image at all while their neighbours drew, even though the raw frame for the
    same cell was already sitting in ``cache[(well, channel, "raw")]``. Each
    fallback is recorded in the caller's *raw_fallbacks* set so the pass can
    say how many cells it served uncorrected; a caller that passes none simply
    has no pass to report against.

    *partial_scans* is threaded through to :func:`load_well_channel` unchanged —
    see its docstring; a truncated inventory belongs to the whole
    ``(well, channel, variant)``, not to the one cell that happened to ask first.
    """
    # A "GFP (binary)" / "GFP (mask)" / "GFP (raw)" token loads the same pixels as
    # "GFP" — the render is applied downstream (cell_display_rgb); here we just
    # resolve the base channel.
    chan_lower = base_channel(chan_upper).strip().lower()
    is_overlay = chan_lower == NUC_SEG_TOKEN.lower()
    fluor = load_well_channel(ctx, cache, well, chan_lower,
                              use_tophat=use_tophat, scan_misses=scan_misses,
                              partial_scans=partial_scans)
    ref, kind = match_image_ref(fluor, fov, tp) if fluor else (None, "")
    # An unresolved top-hat cell drops back to raw rather than rendering
    # "(no image)". ``kind == "ambiguous_fov"`` is a deliberate refusal (an empty
    # FOV spanning several fields), not a gap in the corrected set, so it is left
    # to stand instead of being retried against a dict with the same ambiguity.
    if ref is None and not kind.startswith("ambiguous") and use_tophat and not is_overlay:
        raw = load_well_channel(ctx, cache, well, chan_lower,
                                use_tophat=False, scan_misses=scan_misses,
                                partial_scans=partial_scans)
        if raw:
            ref, kind = match_image_ref(raw, fov, tp)
            if ref is not None:
                if raw_fallbacks is not None:
                    raw_fallbacks.add((well, chan_lower, str(tp), str(fov)))
                logger.info(
                    "frame_load: no corrected image for well=%r channel=%r "
                    "tp=%r fov=%r — showing the raw frame",
                    well, chan_lower, tp, fov,
                )
    if ref is None:
        if kind.startswith("ambiguous"):
            # A refusal is a decision, and the cell it produces looks exactly
            # like a frame that was never captured. Say which axis was left
            # open and how many values it spanned, at a level the log drawer
            # shows, so "(no image)" is diagnosable rather than mysterious.
            logger.warning(
                "frame_load: well=%r channel=%r has several frames matching "
                "tp=%r fov=%r — refusing to pick one arbitrarily (%s). Choose "
                "a %s to see it.",
                well, chan_lower, tp, fov, kind,
                "field" if kind.endswith("fov") else "timepoint",
            )
        return None
    if kind != "exact":
        # Annotate any non-exact resolution so a surfaced neighbouring
        # frame is diagnosable rather than silently misread as QC truth
        # (audit S5).
        logger.debug(
            "frame_load: (%s, tp=%r, fov=%r) resolved via %s fallback",
            well, tp, fov, kind,
        )
    try:
        from review.data.image_discovery import open_imgref_as_array
        # Overlay PNGs are RGB — load them in colour so the segmentation
        # outline survives. Fluor channels load greyscale as before.
        greyscale = not is_nuc_seg(chan_upper)
        return open_imgref_as_array(ref, greyscale=greyscale)
    except Exception:
        # A corrupt/undecodable image renders as "no tile". Leave a breadcrumb
        # so it's diagnosable rather than silently blank (audit E15).
        logger.debug("frame_load: failed to load %r: skipping tile", ref, exc_info=True)
        return None


def load_mask_array(ctx, cache, well, tp, fov) -> np.ndarray | None:
    """Return the int32 segmentation mask for (well, tp, fov), or None.

    The mask dict is populated as a side effect of any prior ``load_frame_array``
    call for the same well, so this will usually hit the cache immediately.

    Resolution goes through :func:`match_image_ref` — the *same* matcher the
    fluorescence lookup uses. The two must agree cell for cell: a mask lookup
    that resolved fewer keys than the image lookup produced cells whose image
    loaded but whose mask did not, and a binary / boundary render with no mask
    used to fall back to the plain intensity image, so a threshold view came
    out half switched. It also inherits the ambiguous-FOV refusal, so an empty
    FOV no longer quietly returns an arbitrary field's mask (which would draw
    one field's outlines over another field's pixels).
    """
    mask_dict = cache.get((well, "__mask__", "raw"))
    if not mask_dict:
        return None
    ref, kind = match_image_ref(mask_dict, fov, tp)
    if ref is None:
        logger.debug(
            "frame_load: no mask for well=%r tp=%r fov=%r (%s)",
            well, tp, fov, kind or "no match",
        )
        return None
    if kind != "exact":
        logger.debug(
            "frame_load: mask (%s, tp=%r, fov=%r) resolved via %s fallback",
            well, tp, fov, kind,
        )
    try:
        import numpy as _np
        from review.data.image_discovery import open_imgref_as_array
        arr = open_imgref_as_array(ref, greyscale=True)
        if arr is None:
            return None
        return _np.rint(_np.asarray(arr)).astype(_np.int32)
    except Exception:
        # A corrupt mask must not silently render as "no overlay" — that reads
        # as a clean QC/segmentation view (audit E15). Leave a breadcrumb.
        logger.debug("frame_load: failed to load mask %r: no overlay", ref, exc_info=True)
        return None
