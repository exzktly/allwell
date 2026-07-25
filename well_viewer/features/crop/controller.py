"""App-facing crop layer: hold the dataset-wide crop rectangle, project
``Crop_Pass`` onto the row cache, drive the draw-mode toggle, and own the
Reset / Clear-crop actions.

The crop verdict composes with manual curation and QC at the inclusion
chokepoint (:func:`well_viewer.data.data_loading.df_included_mask`): a row counts
only when ``Included == 1`` *and* ``QC_Pass == 1`` *and* ``Crop_Pass == 1``. This
module owns the ``Crop_Pass`` column and the ``DatasetState.crop_region`` field;
it never touches ``Included`` (manual curation / cell-gating keep that), so
moving or clearing the crop can never clobber hand-curated include/exclude edits.

Mirrors :mod:`well_viewer.features.qc.controller`.
"""

from __future__ import annotations

from typing import Optional

from .region import CropRegion, compute_crop_pass, normalize_region
from well_viewer.core.safe import swallowed
from well_viewer.data.cache_projectors import project_verdict_column


def get_crop_region(ctx) -> Optional[CropRegion]:
    """The active dataset-wide crop rectangle, or ``None``.

    A8 residuals-ledger R1: pure typed state (``ctx.state.dataset.crop_region``),
    so it takes ``ctx``."""
    return ctx.state.dataset.crop_region


def apply_crop_to_cache(ctx, label: Optional[str] = None) -> None:
    """Write the ``Crop_Pass`` column onto cached well frame(s).

    With ``label`` given, only that well; otherwise every cached well. Writes an
    explicit all-pass column when there is no crop so the chokepoint stays
    consistent across re-crops. Shares the cache walk with
    ``qc.controller.apply_qc_to_cache`` via
    :func:`well_viewer.data.cache_projectors.project_verdict_column`.

    A8 residuals-ledger R1: takes ``ctx`` — region + row cache from typed state,
    so the crop projection is a pure ``ctx`` operation the row-fetch data service
    can call."""
    region = get_crop_region(ctx)
    project_verdict_column(
        ctx, label,
        column="Crop_Pass",
        verdict=lambda df: compute_crop_pass(df, region),
        layer="Crop",
    )


def recompute_crop_all(ctx) -> None:
    """Re-project ``Crop_Pass`` onto all cached wells and bust the stats cache so
    downstream consumers recompute on the next read."""
    from well_viewer.core.selection_controller import invalidate_stats_cache
    apply_crop_to_cache(ctx, None)
    try:
        invalidate_stats_cache(ctx)  # clears stats cache + bumps override_version
    except Exception:
        swallowed()  # was: pass (silent except)


def _save_redraw_refresh(ctx) -> None:
    """Persist the crop section (debounced), redraw the active view, and refresh
    the segmentation overlay so cropped cells dim immediately."""
    from well_viewer.core.plot_orchestrator import redraw_app
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
        from well_viewer.features.image_browser.review_image.renderer import refresh_review_image_ctx
        refresh_review_image_ctx(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)


def set_crop_region(ctx, region: Optional[CropRegion]) -> None:
    """Install a new crop rectangle (from a drawn box), recompute the verdict
    everywhere, persist, and re-render. A zero-area box is a no-op + status."""
    if region is not None:
        region = normalize_region(*region)
    if region is None:
        ctx.notify.set_status("Crop region: box too small — no crop applied.")
        return
    ctx.state.dataset.crop_region = region
    recompute_crop_all(ctx)
    _save_redraw_refresh(ctx)

    # Report the crop's actual effect so a silent no-op is never mistaken for a
    # working crop. The decisive case: a dataset without per-cell centroid
    # columns can't be cropped (compute_crop_pass fails open), so warn loudly.
    removed, total_incl, has_centroids, n_wells = _crop_effect_counts(ctx)
    r0, c0, r1, c1 = region
    where = f"{c1 - c0}×{r1 - r0}px @ ({c0},{r0})"
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
            "columns, so no cells were excluded."
        )
        return
    ctx.notify.set_status(
        f"Crop region set ({where}): excluded {removed:,} of {total_incl:,} "
        f"otherwise-included cell(s) across {n_wells} loaded well(s)."
    )


def _crop_effect_counts(ctx):
    """Aggregate the crop's effect across the currently-cached wells.

    Returns ``(removed, otherwise_included, has_centroids, n_wells)`` — the same
    semantics as :func:`region.crop_effect_for_df`, summed over loaded wells
    (``has_centroids`` is True when *any* loaded well carries the columns).
    """
    from .region import crop_effect_for_df

    region = get_crop_region(ctx)
    removed = total_incl = n_wells = 0
    has_centroids = False
    for df in ctx.state.dataset.cache.values():
        if df is None or len(df) == 0:
            continue
        n_wells += 1
        r, incl, hc = crop_effect_for_df(df, region)
        removed += r
        total_incl += incl
        has_centroids = has_centroids or hc
    return removed, total_incl, has_centroids, n_wells


def clear_crop_region(ctx) -> None:
    """Remove the crop (no warning): cropped-out cells return to included.
    Manual deletes are untouched."""
    if ctx.state.dataset.crop_region is None:
        ctx.notify.set_status("Crop region: nothing to clear.")
        return
    ctx.state.dataset.crop_region = None
    recompute_crop_all(ctx)
    _save_redraw_refresh(ctx)
    ctx.notify.set_status("Crop region cleared.")


# ── draw-mode toggle ─────────────────────────────────────────────────────────


def set_crop_edit_mode(ctx, enabled: bool) -> None:
    """Enter / leave crop-draw mode. Mutually exclusive with the Delete (include
    edit) mode so an armed rubber band is unambiguous on release."""
    enabled = bool(enabled)
    ctx.state.review_image.crop_edit_mode = enabled
    # One deferred import for both review-image touches (keeps the crop↔
    # review_image cycle broken without adding a second deferred-import hit —
    # see the note in _save_redraw_refresh).
    from well_viewer.features.image_browser.review_image.renderer import (
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
        ctx.notify.set_status(
            "Crop mode ON — drag a rectangle to keep only cells whose centroid "
            "is inside (applies to all FOVs/wells)."
        )
    else:
        ctx.notify.set_status("Crop mode OFF.")


def toggle_crop_edit_mode(ctx) -> None:
    set_crop_edit_mode(ctx, not ctx.state.review_image.crop_edit_mode)


# ── reset ────────────────────────────────────────────────────────────────────


def reset_crop_and_overrides(ctx) -> None:
    """Return every cell to Included (even manually-deleted ones) and remove the
    crop region, after a confirmation. Dataset-wide.
    """
    from PySide6.QtWidgets import QMessageBox

    resp = QMessageBox.question(
        ctx.window,
        "Reset crop and curation",
        "Resetting will:\n"
        "  • Return ALL cells to the Included state, including cells you "
        "manually deleted.\n"
        "  • Remove the crop region.\n\n"
        "This applies to every well and cannot be undone. Continue?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if resp != QMessageBox.Yes:
        return

    # (a) drop every manual include/exclude override, (b) drop the crop region.
    from well_viewer.core.selection_controller import invalidate_stats_cache
    ctx.runtime.review.included_overrides.clear()
    ctx.state.dataset.crop_region = None

    # Clearing the override dict is not enough on its own: the cached rows still
    # carry the previously-projected Included=0, and apply_review_overrides_to_cache
    # early-returns on an empty dict (it never *un-writes* a 0). Drop the dataset
    # cache so frames reload their original Included from the source CSVs; lazy
    # _get_rows then re-applies the now-empty crop/QC layers.
    ctx.state.dataset.cache.clear()
    try:
        invalidate_stats_cache(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)
    ctx.state.review_image.override_version += 1

    try:
        if ctx.persistence is not None:
            ctx.persistence.schedule_save("cell_overrides")
            ctx.persistence.schedule_save("crop_region")
    except Exception:
        swallowed()  # was: pass (silent except)

    # Leave crop-draw mode if it was on.
    if ctx.state.review_image.crop_edit_mode:
        set_crop_edit_mode(ctx, False)

    _save_redraw_refresh(ctx)
    ctx.notify.set_status("Reset: all cells returned to Included and crop region removed.")
