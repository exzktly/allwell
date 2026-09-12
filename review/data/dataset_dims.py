"""What a dataset offers along each dimension: wells, channels, timepoints, FOVs.

Every image grid in the app starts by asking the same four questions — which
wells are loaded, which channel tokens can be shown, which timepoints and which
fields of view exist — and each answer is a fact about the dataset rather than
about any one tab's layout. The Image Table fills its axis dropdowns from these,
and the Playground fills its single-frame selectors from the same lists so the
two cannot disagree about what a dataset contains.

Each option list has the same two-tier shape: prefer what the run recorded in
``pipeline_info.json``, and fall back to scanning the loaded CSVs when it
recorded nothing (older runs, custom schemas). The fallback is what keeps a
dataset usable when its sidecar is thin, and it is deliberately *not* the first
choice — the sidecar names every value the run produced, while the cache only
names the ones whose rows happen to be loaded.

It lives in the data layer because both callers are removable features. The
Playground used to import these out of the Image Table's controller, so a build
without the Image Table had no Playground either, whatever the profile manifest
claimed.
"""

from __future__ import annotations

from util.timepoint import sort_key as tp_sort_key
from review.data.frame_load import NUC_SEG_TOKEN, binary_variant, norm_token
from review.domain.viewer_state import parse_rc


def channel_options(ctx) -> list[str]:
    """All channel tokens available for selection (uppercase for display).

    Sourced from the canonical ``ctx.state.channel.fluor_channels`` /
    ``ctx.state.channel.smfish_channels`` lists (built by ``_recalculate_threshold`` from
    pipeline_info.json + CSV detection — same lists every other tab uses).
    Falls back to the raw ``ctx.state.dataset.pipeline_info`` dict for the rare case
    where the tab is repopulated before the first ``_recalculate_threshold``
    pass. Adds the virtual ``NUC+SEG`` token at the end when a nuclear
    channel is known — that's where the pre-rendered segmentation overlay
    images live, and they're how the Image Table replaces the old Movie
    Montage tab.
    """
    info = ctx.state.dataset.pipeline_info or {}
    chans: list[str] = []

    fluor = list(ctx.state.channel.fluor_channels or [])
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

    smfish = ctx.state.channel.smfish_channels or []
    for tok in smfish:
        tok = str(tok).strip().lower()
        if tok and tok not in chans:
            chans.append(tok)

    options = [c.upper() for c in chans]

    # Binary variants — "<CHAN> (binary)" renders that channel's ThreshFracOn
    # threshold-binary (positive cells white). Offered right after the real
    # channels so a nested row axis can mix e.g. NIR, GFP and GFP (binary). The
    # render/load path decodes the token (see ``frame_load.base_channel``).
    options += [binary_variant(c) for c in options]

    seg_tok = (ctx.state.channel.seg_channel_token or "").strip()
    if not seg_tok:
        seg_tok = (info.get("nuclear_token") or "").strip()
    if seg_tok:
        options.append(NUC_SEG_TOKEN)

    # Append user-defined ratio metrics as virtual channels.  The display
    # label "GFP/RFP" is what appears in the dropdown; _find_ratio_for_channel
    # resolves it back to the RatioMetric at render time.
    for r in (ctx.state.channel.ratio_metrics or []):
        lbl = r.display_label()
        if lbl and lbl not in options:
            options.append(lbl)

    return options


def cache_column_tokens(ctx, column: str) -> list[str]:
    """Unique normalised tokens from ``column`` across every cached DataFrame.

    ``ctx.state.dataset.cache`` values are DataFrames since the pandas migration; iterating
    one with ``for row in df`` yields *column names*, not rows. Pull the column
    out vectorised instead — and skip frames that don't carry it at all.
    """
    seen: list[str] = []
    cache = ctx.state.dataset.cache or {}
    for df in cache.values():
        if df is None or column not in getattr(df, "columns", ()):
            continue
        for val in df[column].dropna().unique():
            tok = norm_token(val)
            if tok and tok not in seen:
                seen.append(tok)
    return seen


def timepoint_options(ctx) -> list[str]:
    info = ctx.state.dataset.pipeline_info or {}
    seen: list[str] = []
    for tp in (info.get("available_timepoints") or []):
        tok = norm_token(tp)
        if tok and tok not in seen:
            seen.append(tok)
    if seen:
        return sorted(seen, key=tp_sort_key)
    # Fallback: derive timepoints from loaded CSV rows when pipeline_info
    # doesn't list them (older runs, custom schemas, etc.).
    return sorted(cache_column_tokens(ctx, "timepoint"), key=tp_sort_key)


def fov_options(ctx) -> list[str]:
    info = ctx.state.dataset.pipeline_info or {}
    seen: list[str] = []
    for fv in (info.get("available_fovs") or []):
        tok = norm_token(fv)
        if tok and tok not in seen:
            seen.append(tok)
    if seen:
        return seen
    return cache_column_tokens(ctx, "fov")


def well_options(ctx) -> list[str]:
    return sorted(ctx.state.dataset.well_paths.keys(), key=parse_rc)
