"""Manual per-cell curation, projected onto the row cache.

The user's include/exclude decisions from the Segmentation tab live in
``ctx.runtime.review.included_overrides`` — a ``(well, fov, tp, nid) -> "0"/"1"``
map that is the source of truth. Nothing reads that map when it aggregates,
though: every consumer reads ``row["Included"]`` through the inclusion chokepoint
(``data_loading.df_included_mask``). :func:`apply_curation_to_cache` is what
carries the decision from the one to the other.

**Why this lives in the data layer rather than under the Review Image feature.**
It is a cache projector, in the same sense as QC's ``QC_Pass`` and the crop's
``Crop_Pass``: a verdict written onto a freshly-built frame so the chokepoint
sees a complete row. Registering it beside them (``features/registry``)
means the roster of things applied to a new frame is a single list, and a frame
rebuilt after a cache clear carries the same three layers as one built during the
dataset load. While this projection was owned by the feature it was applied only
where the feature happened to be called — the dataset load, a toggle, and the
end of a gating pass — so a well the row cache dropped and reloaded came back
with the curation silently missing and the override map still holding it.

Unlike QC and crop this writes ``Included`` itself rather than its own verdict
column, because the override *is* the inclusion decision for that cell. The
three layers never collide: QC and crop own ``QC_Pass``/``Crop_Pass`` and
document that they never touch ``Included``.
"""

from __future__ import annotations

import pandas as pd

from core.safe import swallowed
from review.data.data_loading import get_rows
from domain.image_identity import cell_key_tuples_for_dataframe


def _coerce_included(val: object) -> int:
    """An override value as the 0/1 ``Included`` writes."""
    try:
        return int(float(str(val).strip() or "1"))
    except (TypeError, ValueError):
        return 1 if str(val).strip() == "1" else 0


def curation_input_columns(ctx, label: str | None = None) -> tuple[str, ...]:
    """Nothing beyond the identity columns: the overrides key on field, timepoint
    and nucleus id and write ``Included``, all of which every narrow read carries."""
    return ()


def apply_curation_to_cache(ctx, label: str | None = None, *,
                            note_change: bool = True) -> None:
    """Project the override map onto cached ``row["Included"]``.

    With *label* given, only that well — the form the projector registry calls
    on a freshly-loaded frame; it is fetched through :func:`data_loading.get_rows`,
    which re-enters this function as a projector and terminates, because
    ``get_rows`` stores the frame in the cache *before* it runs the projectors,
    so the inner call is a hit.

    Without *label*, every **held** well the map mentions — the form the dataset
    load and the end of a gating pass use to re-assert curation over rows that
    were just rewritten from thresholds. A well the bounded row cache does not
    hold is not read: this function is also a registered projector, so the next
    read of that well applies the overrides as it loads, and reading it whole
    here only to write into a frame the cache then evicts was a full read from
    disk per curated well for nothing (``REREAD_TRACKER.md``, R9). Such a well
    still counts as changed: ``rows_version`` is bumped for it, so a remembered
    aggregate of it is not served.

    *note_change* is what the registered-projector path turns off. Writing onto
    a frame that ``load_and_project`` built a moment ago cannot invalidate a
    memoized aggregate of that well — there is none yet — while bumping
    ``rows_version`` does strand the entries every *other* well already has,
    because that counter is part of the ``aggregate_well`` cache key. A plate
    re-read one well at a time after a cache clear would then discard the
    running total once per well. QC and crop avoid the same trap by never
    bumping at all (``cache_projectors.project_verdict_column`` says why); this
    projection cannot take that route, because its other callers — the dataset
    loader above all — do not invalidate afterwards and depend on the bump.

    A no-op when the map is empty, which is also why it never *un-writes* a 0:
    clearing curation goes through ``persistence.reset_scopes.clear_curation``,
    which drops the row cache so the frames reload their original ``Included``.
    """
    overrides = ctx.runtime.review.included_overrides
    if not overrides:
        return
    by_label: dict[str, dict[tuple[str, str, str], str]] = {}
    for (lbl, fov, tp, nid), val in overrides.items():
        if label is not None and lbl != label:
            continue
        by_label.setdefault(lbl, {})[(fov, tp, nid)] = val
    unheld_curated = False
    for lbl, lookup in by_label.items():
        if lbl not in ctx.state.dataset.well_paths:
            continue
        if label is None:
            repo = getattr(ctx.state, "repo", None)
            df = repo.rows_or_none(lbl) if repo is not None else None
            if df is None:
                unheld_curated = True       # projected when it is next read
                continue
        else:
            try:
                df = get_rows(ctx, lbl)
            except Exception:
                swallowed()  # silent except
                continue
        if df is None or df.empty:
            continue
        keys = cell_key_tuples_for_dataframe(df)
        if keys is None:
            continue
        override_map = pd.Series(lookup)
        override_for_row = keys.map(override_map)
        mask = override_for_row.notna()
        if not mask.any():
            continue
        df.loc[mask, "Included"] = override_for_row[mask].map(
            _coerce_included).astype(int)
        # Manual curation just changed which cells this well contributes, so a
        # memoized aggregation of it is stale. The bump lives here rather than
        # in the callers because there are several of them and one — the dataset
        # loader — does not invalidate afterwards. The exception is the fresh
        # frame the projector path hands over; see *note_change* above.
        if note_change:
            ctx.state.dataset.note_rows_changed()
    if unheld_curated and note_change:
        ctx.state.dataset.note_rows_changed()
