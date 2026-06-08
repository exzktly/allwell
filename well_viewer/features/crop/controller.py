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

import logging
from typing import Optional

from .region import CropRegion, compute_crop_pass, normalize_region

_logger = logging.getLogger("well_viewer")


def get_crop_region(app) -> Optional[CropRegion]:
    """The active dataset-wide crop rectangle, or ``None``."""
    return app._app_state.dataset.crop_region


def apply_crop_to_cache(app, label: Optional[str] = None) -> None:
    """Write the ``Crop_Pass`` column onto cached well frame(s).

    With ``label`` given, only that well; otherwise every cached well. Writes an
    explicit all-pass column when there is no crop so the chokepoint stays
    consistent across re-crops. Mirrors ``qc.controller.apply_qc_to_cache``."""
    region = get_crop_region(app)
    cache = app._app_state.dataset.cache
    labels = [label] if label is not None else list(cache.keys())
    for lbl in labels:
        df = cache.get(lbl)
        if df is None or df.empty:
            continue
        try:
            df["Crop_Pass"] = compute_crop_pass(df, region).astype(int)
        except Exception:  # never let the crop layer break the data path
            _logger.exception("Crop: failed to compute Crop_Pass for well %s", lbl)


def recompute_crop_all(app) -> None:
    """Re-project ``Crop_Pass`` onto all cached wells and bust the stats cache so
    downstream consumers recompute on the next read."""
    apply_crop_to_cache(app, None)
    if hasattr(app, "_invalidate_stats_cache"):
        try:
            app._invalidate_stats_cache()  # clears stats cache + bumps override_version
        except Exception:
            pass


def _save_redraw_refresh(app) -> None:
    """Persist the crop section (debounced), redraw the active view, and refresh
    the segmentation overlay so cropped cells dim immediately."""
    if getattr(app, "_persistence", None) is not None:
        try:
            app._persistence.schedule_save("crop_region")
        except Exception:
            pass
    if hasattr(app, "_redraw"):
        try:
            app._redraw()
        except Exception:
            pass
    try:
        from well_viewer.features.review_image.renderer import refresh_review_image
        refresh_review_image(app)
    except Exception:
        pass


def set_crop_region(app, region: Optional[CropRegion]) -> None:
    """Install a new crop rectangle (from a drawn box), recompute the verdict
    everywhere, persist, and re-render. A zero-area box is a no-op + status."""
    if region is not None:
        region = normalize_region(*region)
    if region is None:
        app._set_status("Crop region: box too small — no crop applied.")
        return
    app._app_state.dataset.crop_region = region
    recompute_crop_all(app)
    _save_redraw_refresh(app)
    r0, c0, r1, c1 = region
    app._set_status(
        f"Crop region set: keeping cells with centroid inside "
        f"{c1 - c0}×{r1 - r0}px @ ({c0},{r0}). Applies to all FOVs/wells."
    )


def clear_crop_region(app) -> None:
    """Remove the crop (no warning): cropped-out cells return to included.
    Manual deletes are untouched."""
    if app._app_state.dataset.crop_region is None:
        app._set_status("Crop region: nothing to clear.")
        return
    app._app_state.dataset.crop_region = None
    recompute_crop_all(app)
    _save_redraw_refresh(app)
    app._set_status("Crop region cleared.")


# ── draw-mode toggle ─────────────────────────────────────────────────────────


def set_crop_edit_mode(app, enabled: bool) -> None:
    """Enter / leave crop-draw mode. Mutually exclusive with the Delete (include
    edit) mode so an armed rubber band is unambiguous on release."""
    enabled = bool(enabled)
    app._app_state.review_image.crop_edit_mode = enabled
    if enabled and app._app_state.review_image.include_edit_mode:
        # Leave delete mode (also un-checks the Delete button).
        from well_viewer.features.review_image.renderer import (
            set_review_image_include_mode,
        )
        set_review_image_include_mode(app, False)
    if hasattr(app, "_apply_review_image_cursor"):
        try:
            app._apply_review_image_cursor()
        except Exception:
            pass
    btn = getattr(app, "_review_image_crop_btn", None)
    if btn is not None:
        btn.setChecked(enabled)
    if enabled:
        app._set_status(
            "Crop mode ON — drag a rectangle to keep only cells whose centroid "
            "is inside (applies to all FOVs/wells)."
        )
    else:
        app._set_status("Crop mode OFF.")


def toggle_crop_edit_mode(app) -> None:
    set_crop_edit_mode(app, not app._app_state.review_image.crop_edit_mode)


# ── reset ────────────────────────────────────────────────────────────────────


def reset_crop_and_overrides(app) -> None:
    """Return every cell to Included (even manually-deleted ones) and remove the
    crop region, after a confirmation. Dataset-wide.
    """
    from PySide6.QtWidgets import QMessageBox

    resp = QMessageBox.question(
        app,
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
    app._review_included_overrides.clear()
    app._app_state.dataset.crop_region = None

    # Clearing the override dict is not enough on its own: the cached rows still
    # carry the previously-projected Included=0, and apply_review_overrides_to_cache
    # early-returns on an empty dict (it never *un-writes* a 0). Drop the dataset
    # cache so frames reload their original Included from the source CSVs; lazy
    # _get_rows then re-applies the now-empty crop/QC layers.
    if hasattr(app, "_invalidate_dataset_cache"):
        try:
            app._invalidate_dataset_cache()
        except Exception:
            pass
    if hasattr(app, "_invalidate_stats_cache"):
        try:
            app._invalidate_stats_cache()
        except Exception:
            pass
    app._app_state.review_image.override_version += 1

    if getattr(app, "_persistence", None) is not None:
        try:
            app._persistence.schedule_save("cell_overrides")
            app._persistence.schedule_save("crop_region")
        except Exception:
            pass

    # Leave crop-draw mode if it was on.
    if app._app_state.review_image.crop_edit_mode:
        set_crop_edit_mode(app, False)

    _save_redraw_refresh(app)
    app._set_status("Reset: all cells returned to Included and crop region removed.")
