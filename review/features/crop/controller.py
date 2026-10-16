"""App-facing crop layer: hold the crop rectangle and the frames it applies to,
project ``Crop_Pass`` onto the row cache, drive the draw-mode toggle, and own the
Reset / Clear-crop actions.

The crop verdict composes with manual curation and QC at the inclusion
chokepoint (:func:`review.data.data_loading.df_included_mask`): a row counts
only when ``Included == 1`` *and* ``QC_Pass == 1`` *and* ``Crop_Pass == 1``. This
module owns the ``Crop_Pass`` column and the ``DatasetState.crop_region`` field;
it never touches ``Included`` (manual curation / cell-gating keep that), so
moving or clearing the crop can never clobber hand-curated include/exclude edits.

Mirrors :mod:`review.features.qc.controller`.
"""

from __future__ import annotations


from .region import (
    CropRegion, FrameScope, compute_crop_pass, frame_in_scope, normalize_region,
)
from review.core.frame_scope import (
    ScopeSpec, describe_scope, refresh_scope_button,
)
from core.safe import swallowed
from review.data.cache_projectors import project_verdict_column
from review.data.data_loading import held_wells_note

# The three ``DatasetState`` fields the crop scope is spread over, in the order
# the popover stacks them. One list so the view, the persistence block and the
# reset cannot come to disagree about which fields make up a scope.
SCOPE_FIELDS = ("crop_scope_wells", "crop_scope_fovs", "crop_scope_tps")


def get_crop_region(ctx) -> CropRegion | None:
    """The active crop rectangle, or ``None``.

    A8 residuals-ledger R1: pure typed state (``ctx.state.dataset.crop_region``),
    so it takes ``ctx``."""
    return ctx.state.dataset.crop_region


def get_crop_scope(ctx) -> FrameScope:
    """The frames the crop is armed for, as a :class:`FrameScope`.

    Un-armed fields stay ``None`` — every value of that field — so the default
    scope is the whole dataset and an un-scoped crop behaves exactly as it did
    before the control existed."""
    ds = ctx.state.dataset
    return FrameScope(
        wells=None if ds.crop_scope_wells is None else frozenset(ds.crop_scope_wells),
        fovs=None if ds.crop_scope_fovs is None else frozenset(ds.crop_scope_fovs),
        tps=None if ds.crop_scope_tps is None else frozenset(ds.crop_scope_tps),
    )


def crop_input_columns(ctx, label: str | None = None) -> tuple[str, ...]:
    """The columns :func:`apply_crop_to_cache`'s verdict reads beyond the identity
    columns (the scope's field and timepoint), for ``narrow_rows``."""
    from .region import CENTROID_COL_COL, CENTROID_ROW_COL

    if get_crop_region(ctx) is None:
        return ()
    return (CENTROID_ROW_COL, CENTROID_COL_COL)


def apply_crop_to_cache(ctx, label: str | None = None) -> list[str]:
    """Write the ``Crop_Pass`` column onto cached well frame(s).

    With ``label`` given, only that well; otherwise every cached well. Writes an
    explicit all-pass column when there is no crop so the chokepoint stays
    consistent across re-crops. Shares the cache walk with
    ``qc.controller.apply_qc_to_cache`` via
    :func:`review.data.cache_projectors.project_verdict_column`, which is
    also where the well label comes from — the frame need not carry a well
    column, and the crop scope is partly a per-well decision.

    A8 residuals-ledger R1: takes ``ctx`` — region + scope + row cache from typed
    state, so the crop projection is a pure ``ctx`` operation the row-fetch data
    service can call.

    Returns the labels whose verdict could not be computed, exactly as
    ``qc.controller.apply_qc_to_cache`` does — ``project_verdict_column`` drops
    ``Crop_Pass`` from those frames, which reads downstream as "no crop applies
    to this well". :func:`recompute_crop_all` is where that reaches the user."""
    region = get_crop_region(ctx)
    scope = get_crop_scope(ctx)
    return project_verdict_column(
        ctx, label,
        column="Crop_Pass",
        verdict=lambda df, well: compute_crop_pass(df, region, scope, well),
        layer="Crop",
    )


def _appended(warning: str) -> str:
    """*warning* as a clause to hang off the end of a status line, or ``""``."""
    return f"  {warning}" if warning else ""


def recompute_crop_all(ctx) -> str:
    """Re-project ``Crop_Pass`` onto all cached wells and bust the stats cache so
    downstream consumers recompute on the next read.

    Returns the warning naming any well whose verdict could not be computed, or
    ``""``. Every crop action writes its own outcome to the status line one or
    two calls after this returns, which would erase the warning
    :func:`report_verdict_failures` just put there — so each of them appends
    this string to its own message. The sibling of
    ``qc.controller.recompute_qc_all``, whose caller writes no status of its own
    and therefore needs no such thread-through.
    """
    from review.core.notifications import report_verdict_failures
    from review.core.selection_controller import invalidate_stats_cache
    failed = apply_crop_to_cache(ctx, None)
    # A well whose Crop_Pass had to be dropped is now cropped by nothing, while
    # the outline on the canvas says otherwise.
    warning = report_verdict_failures(
        ctx, layer="Crop", column="Crop_Pass", failed=failed)
    try:
        invalidate_stats_cache(ctx)  # clears stats cache + bumps override_version
    except Exception:
        swallowed()  # was: pass (silent except)
    return warning


def _save_redraw_refresh(ctx) -> None:
    """Persist the crop section (debounced), redraw the active view, and refresh
    the segmentation overlay so cropped cells dim immediately."""
    from review.core.plot_orchestrator import redraw_app
    try:
        if ctx.persistence is not None:
            ctx.persistence.schedule_save("crop_region")
    except Exception:
        swallowed()  # was: pass (silent except)
    try:
        redraw_app(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)
    try:
        # Sanctioned deferred import: crop and review_image are peer features
        # with a genuine bidirectional interaction (crop is a draw-mode of the
        # review-image canvas, and the canvas click handler delegates back to
        # crop). review_image.controller imports crop.controller, so this stays
        # function-local to break that cycle (counted by the deferred-import
        # ratchet in tests/test_deferred_imports.py).
        from review.features.image_browser.review_image.renderer import refresh_review_image_ctx
        refresh_review_image_ctx(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)


def set_crop_region(ctx, region: CropRegion | None) -> None:
    """Install a new crop rectangle (from a drawn box), recompute the verdict
    everywhere, persist, and re-render. A zero-area box is a no-op + status."""
    if region is not None:
        region = normalize_region(*region)
    if region is None:
        ctx.notify.set_status("Crop region: box too small — no crop applied.")
        return
    ctx.state.dataset.crop_region = region
    # Appended to whichever outcome line this function ends on, so the
    # re-projection warning is not overwritten by it — see recompute_crop_all.
    warning = _appended(recompute_crop_all(ctx))
    _save_redraw_refresh(ctx)

    # Report the crop's actual effect so a silent no-op is never mistaken for a
    # working crop. The decisive case: a dataset without per-cell centroid
    # columns can't be cropped (compute_crop_pass fails open), so warn loudly.
    removed, total_incl, has_centroids, n_wells = _crop_effect_counts(ctx)
    r0, c0, r1, c1 = region
    where = f"{c1 - c0}×{r1 - r0}px @ ({c0},{r0})"
    scoped = " (scoped)" if get_crop_scope(ctx).is_armed else ""
    if n_wells and not has_centroids:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(
            ctx.window, "Crop can't filter cells",
            "This dataset has no per-cell centroid columns "
            "(shape_centroid_row / shape_centroid_col), which the crop needs to "
            "decide which cells fall inside the box.\n\n"
            "The crop outline is shown, but no cells are excluded and the plots "
            "are unchanged. Re-run the pipeline with shape features enabled to "
            "use the crop.",
        )
        ctx.notify.set_status(
            f"Crop region set ({where}) — but this dataset has no centroid "
            f"columns, so no cells were excluded.{warning}"
        )
        return
    ctx.notify.set_status(
        f"Crop region set ({where}{scoped}): excluded {removed:,} of "
        f"{total_incl:,} otherwise-included cell(s) across {n_wells} loaded "
        f"well(s){held_wells_note(ctx)}.{warning}"
    )


def _crop_effect_counts(ctx):
    """Aggregate the crop's effect across the currently-cached wells.

    The held wells, which on a plate larger than the row-cache budget are not all
    of them; the status lines say so through ``data_loading.held_wells_note``.

    Returns ``(removed, otherwise_included, has_centroids, n_wells)`` — the same
    semantics as :func:`region.crop_effect_for_df`, summed over loaded wells
    (``has_centroids`` is True when *any* loaded well carries the columns).
    """
    from .region import crop_effect_for_df

    region = get_crop_region(ctx)
    scope = get_crop_scope(ctx)
    removed = total_incl = n_wells = 0
    has_centroids = False
    for label, df in ctx.state.dataset.cache.items():
        if df is None or len(df) == 0:
            continue
        n_wells += 1
        r, incl, hc = crop_effect_for_df(df, region, scope, label)
        removed += r
        total_incl += incl
        has_centroids = has_centroids or hc
    return removed, total_incl, has_centroids, n_wells


# ── scope ────────────────────────────────────────────────────────────────────
#
# The crop is armed with the same wells / FOVs / timepoints control the
# Segmentation tab's drag-delete uses (``core/frame_scope.py``). Only two things
# differ, and both are in the spec: the fields live on ``DatasetState`` (they are
# persisted with the region, because they are part of what a saved crop means),
# and an un-armed field means *every* value rather than the displayed one — so a
# crop nobody has scoped filters the whole plate, as it always has.
#
# The spec lives here rather than beside the popover because every path that
# changes a scoped crop is on this side: the popover writes through it, and so do
# the Reset actions and the dataset swap, which must relabel the button or it
# goes on advertising a scope the state no longer holds.

CROP_SCOPE_BTN = "_review_crop_scope_btn"


def _on_crop_scope_changed(ctx) -> None:
    """Re-project, persist and redraw after the armed frames change.

    The scope decides which cells the crop excludes, so changing it changes
    every downstream number exactly as moving the box would — it takes the same
    recompute path, and says what it did.

    Data first, chrome second. This can run from a teardown (a popover destroyed
    with a tick still settling), where the button is gone; relabelling first
    made the widget the gate on the re-projection ever happening.
    """
    warning = _appended(recompute_crop_all(ctx))
    _save_redraw_refresh(ctx)
    refresh_crop_scope_button(ctx)
    if get_crop_region(ctx) is None:
        # Arming frames before drawing a box is a legitimate order of
        # operations, but "excluded 0 cells" would read as a broken crop.
        ctx.notify.set_status(
            f"Crop scope set — draw a crop rectangle to apply it.{warning}"
        )
        return
    removed, total_incl, _has_centroids, n_wells = _crop_effect_counts(ctx)
    # ``total_incl`` and ``n_wells`` span every loaded well, in scope or not —
    # they are the population the crop is being measured against, not the
    # population it reaches. Saying "loaded" rather than "scoped" keeps the two
    # numbers from reading as a contradiction of the scope the button states.
    ctx.notify.set_status(
        f"Crop scope updated: now excluding {removed:,} of {total_incl:,} "
        f"otherwise-included cell(s) across {n_wells} loaded well(s)"
        f"{held_wells_note(ctx)}.{warning}"
    )


CROP_SCOPE = ScopeSpec(
    state_attr="dataset",
    fields=SCOPE_FIELDS,
    title="Crop scope",
    unarmed_text="all frames",
    unarmed_field_words=("all wells", "all FOVs", "all TPs"),
    button_key=CROP_SCOPE_BTN,
    seed_current_frame=False,
    hint="Apply the crop to:",
    tooltip=(
        "Which frames the crop rectangle filters.\n"
        "• A cell is excluded when its centroid is outside the box and its "
        "frame is in scope.\n"
        "• Cells in every other frame are left alone, crop or no crop.\n"
        "The scope is saved with the crop and applies until you reset it."
    ),
    reset_label="Reset to all frames",
    reset_tooltip=(
        "Widen the crop back to every well, FOV and timepoint in the dataset."
    ),
    reset_status="Crop scope: all frames.",
    plate_status_ok="Crop scope: {n} well(s) from the plate selection.",
    plate_status_empty="Crop scope: no wells are selected on the plate.",
    on_change=lambda ctx: _on_crop_scope_changed(ctx),
)


def refresh_crop_scope_button(ctx) -> None:
    """Re-label the Crop-scope button. Safe when the tab was never built."""
    refresh_scope_button(ctx, CROP_SCOPE)


def crop_covers_frame(ctx, well: str, fov, tp) -> bool:
    """Whether the crop, as scoped, reaches any cell of one displayed frame.

    The question the Segmentation canvas asks before it decides whether to draw
    the crop outline solid or dashed. It lives here rather than in the renderer
    so the answer comes from the same place the ``Crop_Pass`` verdict does — the
    scope, and the well's own rows — instead of from a second comparison of the
    same tokens, which disagreed with the verdict in both directions.
    """
    from review.data.data_loading import get_rows
    scope = get_crop_scope(ctx)
    if not scope.is_armed:
        return True
    df = None
    if well and well in ctx.state.dataset.well_paths:
        try:
            df = get_rows(ctx, well)
        except Exception:
            # A well whose rows will not load still deserves its outline drawn;
            # ``frame_in_scope`` falls back to the tokens when it gets nothing.
            swallowed()
    return frame_in_scope(df, scope, well, fov, tp)


def clear_crop_region(ctx) -> None:
    """Remove the crop and the frames it was scoped to (no warning).

    Cropped-out cells return to included; manual deletes are untouched.

    The scope goes with the box for the reason ``clear_curation`` gives for
    doing the same: a scope left armed behind a cleared crop silently narrows
    the *next* box the user draws to frames they thought they had cleared, and
    it would not survive a restart either (the section is dropped once there is
    no region to save), so the two would then disagree.
    """
    ds = ctx.state.dataset
    was_scoped = get_crop_scope(ctx).is_armed
    if ds.crop_region is None and not was_scoped:
        ctx.notify.set_status("Crop region: nothing to clear.")
        return
    ds.crop_region = None
    ds.reset_crop_scope()
    refresh_crop_scope_button(ctx)
    warning = _appended(recompute_crop_all(ctx))
    _save_redraw_refresh(ctx)
    ctx.notify.set_status(
        ("Crop region and scope cleared." if was_scoped
         else "Crop region cleared.") + warning
    )


# ── draw-mode toggle ─────────────────────────────────────────────────────────


def set_crop_edit_mode(ctx, enabled: bool) -> None:
    """Enter / leave crop-draw mode. Mutually exclusive with the Delete (include
    edit) mode so an armed rubber band is unambiguous on release."""
    enabled = bool(enabled)
    ctx.state.review_image.crop_edit_mode = enabled
    # One deferred import for both review-image touches (keeps the crop↔
    # review_image cycle broken without adding a second deferred-import hit —
    # see the note in _save_redraw_refresh).
    from review.features.image_browser.review_image.renderer import (
        apply_review_image_cursor, set_review_image_include_mode,
    )
    if enabled and ctx.state.review_image.include_edit_mode:
        # Leave delete mode (also un-checks the Delete button).
        set_review_image_include_mode(ctx, False)
    try:
        apply_review_image_cursor(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)
    btn = ctx.runtime.review_image.crop_btn
    if btn is not None:
        btn.setChecked(enabled)
    if enabled:
        # Name the armed scope rather than a fixed "all FOVs/wells": with the
        # Crop scope narrowed, that sentence would be describing a crop the user
        # is not about to draw.
        ctx.notify.set_status(
            "Crop mode ON — drag a rectangle to keep only cells whose centroid "
            f"is inside. {describe_scope(ctx, CROP_SCOPE)}."
        )
    else:
        ctx.notify.set_status("Crop mode OFF.")


def toggle_crop_edit_mode(ctx) -> None:
    set_crop_edit_mode(ctx, not ctx.state.review_image.crop_edit_mode)


# ── reset ────────────────────────────────────────────────────────────────────


def reset_crop_and_overrides(ctx) -> None:
    """Confirm, then run :func:`reset_crop_and_overrides_now`. Dataset-wide."""
    from PySide6.QtWidgets import QMessageBox

    resp = QMessageBox.question(
        ctx.window,
        "Reset crop and curation",
        "Resetting will:\n"
        "  • Return ALL cells to the Included state, including cells you "
        "manually deleted.\n"
        "  • Remove the crop region and the frames it was scoped to.\n\n"
        "This applies to every well and cannot be undone. Continue?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if resp != QMessageBox.Yes:
        return
    reset_crop_and_overrides_now(ctx)


def reset_crop_and_overrides_now(ctx) -> None:
    """Return every cell to Included (even manually-deleted ones) and remove the
    crop region, without asking. Dataset-wide.

    The state and disk half is the Curation reset scope
    (``persistence/reset_scopes._clear_curation``) — the same function the title
    bar's Reset analysis runs, so this menu action and that checkbox cannot come
    to mean different things. What is added here is what only this entry point
    needs: leaving crop-draw mode, the redraw, and the status line.

    Split from the confirming wrapper above so the reset scope can run it behind
    its own single confirmation instead of stacking a second dialog on the first.
    """
    from review.core.selection_controller import invalidate_stats_cache
    from review.persistence.reset_scopes import clear_curation

    clear_curation(ctx)
    # ``clear_curation`` widens the scope back to every frame; the button that
    # advertises it is this side's to relabel.
    refresh_crop_scope_button(ctx)
    try:
        invalidate_stats_cache(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)

    # Leave crop-draw mode if it was on.
    if ctx.state.review_image.crop_edit_mode:
        set_crop_edit_mode(ctx, False)

    _save_redraw_refresh(ctx)
    ctx.notify.set_status("Reset: all cells returned to Included and crop region removed.")
