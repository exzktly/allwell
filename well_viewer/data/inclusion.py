"""Which cells an image view is allowed to draw.

``data_loading.df_included_mask`` is the app's single answer to "does this cell
count toward the analysis?" — every plot, statistic and export composes it. An
image view needs the same answer keyed differently: not a row mask but *the
nucleus ids in one frame that the analysis has dropped*, so the label image can
be filtered before anything is drawn from it.

That filtering is the point. A segmentation mask holds every object the pipeline
found, including the ones the user deleted by hand and the ones a crop excluded.
Drawing outlines straight off it shows a curated-away cell as a segmented,
counted cell — the plots disagree with the picture, and nothing on screen says
which is right. Zeroing the excluded labels out of the mask fixes the boundary
overlay, the binary mask view and the threshold view in one place, because all
three read that array.

Qt-free and feature-free, and in ``data/`` rather than under any one feature, so
every image view (the Image Table today) can ask the question without importing
another feature.
"""

from __future__ import annotations

import logging

from well_viewer.data.data_loading import df_included_mask, get_rows
from well_viewer.data.image_identity import (
    frame_token_series, normalize_fov, normalize_tp, resolve_identity_column,
)

_logger = logging.getLogger("well_viewer")

# Where the per-well result is memoised, and the counter it is keyed by.
# ``ReviewImageState.override_version`` is the app's existing "inclusion changed"
# tick: it is bumped by ``invalidate_stats_cache`` — which every path that
# rewrites an inclusion layer calls (manual deletes, the crop, QC, cell gating)
# — so a stale map cannot outlive an edit.
_ATTR = "_aw_excluded_nids"


def _inclusion_version(ctx) -> int | None:
    """The inclusion-change counter, or ``None`` when there is none to key on.

    Returns ``None`` under a build with no Review-Image slice, which makes the
    memo unusable; the caller then recomputes every time rather than risk
    serving a map from before an edit. Correctness first — the fast path below
    means the recompute is trivial for a dataset with nothing excluded anyway.
    """
    slice_ = ctx.state.feature_slice("review_image")
    if slice_ is None:
        return None
    return int(getattr(slice_, "override_version", 0))


def _build_excluded_map(df) -> dict[tuple[str, str], frozenset]:
    """``{(fov, tp): {nucleus ids the analysis drops}}`` for one well's rows."""
    import pandas as pd

    mask = df_included_mask(df)
    # The common case by far: nothing is excluded, so the frame tokens — the
    # expensive part, a per-row normalisation — are never computed at all.
    if bool(mask.all()):
        return {}
    nid_col = resolve_identity_column(
        df, "nucleus_id", "nucleus id", "nucleusId", "nucleusID")
    if nid_col is None:
        # Without ids there is no way to say *which* objects to drop. Say so:
        # the view then draws every cell, which is what it did before, and the
        # log names the well rather than leaving a silent mismatch.
        return {}
    fov_n, tp_n = frame_token_series(df)
    nids = pd.to_numeric(df[nid_col], errors="coerce")
    out: dict[tuple[str, str], set] = {}
    dropped = ~mask
    for fov, tp, nid in zip(fov_n[dropped], tp_n[dropped], nids[dropped]):
        if nid != nid:      # NaN — an id that cannot be matched to a label
            continue
        out.setdefault((str(fov), str(tp)), set()).add(int(nid))
    return {k: frozenset(v) for k, v in out.items()}


def excluded_nids_by_frame(ctx, well: str) -> dict[tuple[str, str], frozenset]:
    """Per-frame excluded nucleus ids for *well*, memoised on its row frame.

    Keyed by the inclusion-change counter, so an edit invalidates it; held on
    the frame's own ``attrs`` rather than in a module-level dict, so it dies
    with the frame it describes and cannot outlive a dataset swap.

    The stored key carries the frame's identity as well as the counter, because
    ``attrs`` is *inherited by derived frames*: ``df.copy()`` carries this memo
    across to a different frame, where the counter alone would say it is fresh
    and cells deleted in the copy would go on being drawn. Nothing in the app
    puts a derived frame into the row cache today — both writers assign a fresh
    ``load_well_csv`` result — so this guards a future one rather than a live
    bug.
    """
    try:
        df = get_rows(ctx, well)
    except Exception:
        _logger.warning(
            "inclusion: could not read rows for well %r; drawing every cell",
            well, exc_info=True)
        return {}
    if df is None or df.empty:
        return {}
    version = _inclusion_version(ctx)
    if version is not None:
        cached = df.attrs.get(_ATTR)
        if cached is not None and cached[0] == (version, id(df)):
            return cached[1]
    built = _build_excluded_map(df)
    if version is not None:
        df.attrs[_ATTR] = ((version, id(df)), built)
    return built


def excluded_nids_for_frame(ctx, well: str, fov, tp) -> frozenset:
    """The nucleus ids one frame's image must not draw as cells."""
    if not well:
        return frozenset()
    key = (normalize_fov(fov), normalize_tp(tp))
    return excluded_nids_by_frame(ctx, well).get(key, frozenset())


def drop_excluded_labels(labels, excluded):
    """Zero every label in *excluded* out of the ``labels`` array.

    Returns ``labels`` untouched when there is nothing to drop, so a dataset
    with no curation pays nothing. The array is modified in place: the loaders
    that produce it (``frame_load.load_mask_array``) build a fresh array per
    call — ``image_discovery``'s decode cache names that function as one of its
    copy-on-read consumers — and the caller is the render pass that asked for it.

    Membership goes through a lookup table indexed by label rather than
    ``np.isin``: a mask is a few million pixels and this runs once per rendered
    cell, and one indexing pass is several times cheaper than a sort-or-hash
    over the whole array. It also makes the id range explicit — an id that the
    array's own dtype cannot represent is dropped from the query instead of
    being wrapped into some other cell's label, which is a way to blank the
    wrong cell's pixels.
    """
    if labels is None or not excluded:
        return labels
    import numpy as np
    arr = np.asarray(labels)
    if arr.size == 0:
        return labels
    hi = int(arr.max(initial=0))
    wanted = [n for n in (int(v) for v in excluded) if 0 < n <= hi]
    if not wanted:
        return arr
    drop = np.zeros(hi + 1, dtype=bool)
    drop[wanted] = True
    # Negative labels are not a thing a segmentation mask carries, but an
    # unsigned index would wrap one into the table if it were.
    arr[drop[np.clip(arr, 0, hi)] & (arr > 0)] = 0
    return arr
