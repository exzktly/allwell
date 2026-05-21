"""Data-load/path lifecycle helpers for WellViewerApp."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from PySide6.QtWidgets import QMessageBox


def _resolve_fov_tp_extractor(extractor, pipeline_info: dict):
    """Return *extractor* unchanged when read_pipeline_info gave us one;
    otherwise build a separator-aware 5-field fallback from
    ``pipeline_info["separator"]`` (matching the dataset's filename
    convention rather than the hardcoded ``_`` legacy regex)."""
    if extractor is not None:
        return extractor
    sep = ""
    if isinstance(pipeline_info, dict):
        sep = str(pipeline_info.get("separator", "")).strip()
    if not sep:
        return None
    from well_viewer.image_discovery import make_default_fov_tp_extractor
    return make_default_fov_tp_extractor(sep)


def load_path(app, path: Path) -> None:
    if not path.is_dir():
        QMessageBox.critical(app, "Not a directory", f"Expected a directory:\n{path}")
        return
    app._cleanup_tmp()
    in_dir = path / "in"
    out_dir = path / "out"
    if in_dir.is_dir() and out_dir.is_dir():
        logger = logging.getLogger("well_viewer")
        logger.info("Detected in/out layout: in=%s  out=%s", in_dir, out_dir)
        logger.info("Fluor images from: %s  |  masks/overlays from: %s", in_dir, out_dir)
        app._app_state.dataset.in_dir = in_dir
        app._app_state.dataset.fov_tp_extractor, _fluor_tokens, _smfish_tokens, app._app_state.dataset.pipeline_info = app._read_pipeline_info(out_dir)
        app._app_state.dataset.fov_tp_extractor = _resolve_fov_tp_extractor(app._app_state.dataset.fov_tp_extractor, app._app_state.dataset.pipeline_info)
        app._app_state.channel.smfish_channels = _smfish_tokens
        app._load_directory(out_dir, label=f"{path.name}/out")
    else:
        app._app_state.dataset.in_dir = None
        app._app_state.dataset.fov_tp_extractor, _fluor_tokens, _smfish_tokens, app._app_state.dataset.pipeline_info = app._read_pipeline_info(path)
        app._app_state.dataset.fov_tp_extractor = _resolve_fov_tp_extractor(app._app_state.dataset.fov_tp_extractor, app._app_state.dataset.pipeline_info)
        app._app_state.channel.smfish_channels = _smfish_tokens
        app._load_directory(path)


def load_directory(app, d: Path, label=None) -> None:
    csvs_all = [p for p in sorted(d.glob("*.csv")) if not p.name.startswith(".")]
    csvs = [p for p in csvs_all if _looks_like_well_measurement_csv(p)]
    skipped = [p for p in csvs_all if p not in csvs]
    if skipped:
        logging.getLogger("well_viewer").info(
            "Skipping %d non-measurement CSV(s): %s",
            len(skipped),
            ", ".join(p.name for p in skipped[:8]) + ("…" if len(skipped) > 8 else ""),
        )
    if not csvs:
        if csvs_all:
            QMessageBox.warning(
                app,
                "No compatible CSVs",
                "CSV files were found, but none looked like well-measurement input files.\n"
                "Please select the analysis output folder containing per-well CSVs.",
            )
        else:
            QMessageBox.warning(app, "No CSVs", f"No .csv files found in:\n{d}")
        return
    # Save the outgoing dataset's view-state before swapping data dirs so
    # reopening it later restores tab / wells / channel selection.
    if app._app_state.dataset.data_dir is not None and hasattr(app, "_view_state_save_to_data_dir"):
        try:
            app._view_state_save_to_data_dir()
        except Exception:
            logging.getLogger("well_viewer").exception("view_state save on switch failed")
    # DatasetState resets the per-dataset caches (data dir, well paths, row
    # cache, persistence doc) for the switch to the new data directory.
    app._app_state.dataset.begin_load(d)
    app._app_state.selection.selected_wells.clear()
    app._app_state.selection.last_sel = None
    app._app_state.selection.prev_sel = set()
    app._bar_order = None
    # Drop the per-channel threshold (min, max) cache; the new dataset's
    # ranges may be completely different.
    app._app_state.channel.threshold_range_cache.clear()
    if hasattr(app, "_invalidate_review_image_frame_cache"):
        app._invalidate_review_image_frame_cache()
    # Drop every cached ZipFile handle on dataset swap so the new
    # dataset's per-well zips aren't read through a previous
    # dataset's handle (or, worse, a handle pointing at a since-
    # deleted file).
    try:
        from well_viewer.zipfile_cache import invalidate as _invalidate_zip_cache
        _invalidate_zip_cache()
    except Exception:
        pass
    # Signal any in-flight smFISH "Apply to All" worker to abort so
    # it doesn't overwrite the *new* dataset's CSVs with counts
    # computed against the previous dataset.
    smfish_cancel = getattr(app, "_smfish_cancel_event", None)
    if smfish_cancel is not None:
        try:
            smfish_cancel.set()
        except Exception:
            pass
    n = len(csvs)
    app._show_progress(n, f"Loading {n} CSV file(s)…")
    for i, p in enumerate(csvs, 1):
        tok = app._extract_well_token(p.stem)
        key = tok if tok else p.stem
        app._app_state.dataset.well_paths[key] = p
        app._app_state.dataset.cache[key] = app._load_well_csv(p)
        app._step_progress(i, f"Loading {i}/{n}: {p.name}")
    app._hide_progress()
    app._rebuild_all_timepoints_cache()
    app._rebuild_all_fovs_cache()
    app._build_tok_to_label()
    # Apply any sample_definitions persisted inside pipeline_info.json before
    # we prune groups against the loaded well set, so saved replicate-set and
    # group state is hydrated and then validated by the same pruning pass that
    # cleans stale wells.
    if hasattr(app, "_load_sample_definitions_from_pipeline_info"):
        try:
            app._load_sample_definitions_from_pipeline_info()
        except Exception:
            pass
    # Hydrate per-cell Segmentation-tab overrides (cell_overrides.json) before
    # any controller queries cached rows, then project them onto row['Included']
    # so all downstream stats honor user curation. Both calls are safe no-ops
    # when no patch file exists.
    if hasattr(app, "_cell_overrides_load_from_data_dir"):
        try:
            app._cell_overrides_load_from_data_dir()
            app._apply_review_overrides_to_cache()
        except Exception:
            pass
    # Hydrate user-defined line-plot draw order (line_order.json).
    if hasattr(app, "_line_order_load_from_data_dir"):
        try:
            app._line_order_load_from_data_dir()
        except Exception:
            pass
    app._refresh_sidebar_map()
    if app._preview_selected_well not in app._app_state.dataset.well_paths:
        app._preview_selected_well = None
    if hasattr(app, "_sidebar_preview_plate"):
        app._refresh_preview_picker()
    if hasattr(app, "_image_table_refresh_picker"):
        try:
            app._image_table_repopulate_dropdowns()
            app._image_table_refresh_picker()
        except Exception:
            pass
    app._label_panel_refresh()
    # Refresh the Sample Definitions tab so any newly loaded labels / groups
    # appear without requiring a tab switch.
    if hasattr(app, "_groups_centre_refresh"):
        try:
            app._groups_centre_refresh()
        except Exception:
            pass
    # Auto-load any persisted ratio definitions and heatmap layouts that live
    # in the same data directory. Both calls are safe no-ops when no file
    # exists; errors are logged and swallowed.
    if hasattr(app, "_ratios_load_from_data_dir"):
        app._ratios_load_from_data_dir()
    # Eagerly hydrate Cell Gating thresholds from ``pipeline_info.json`` so the
    # very first plot draw (line / bar / batch export) has the user's
    # per-channel and per-ratio ThreshFracOn values available. The Cell Gating
    # sub-tab is otherwise built lazily on first visit, which would leave the
    # threshold lookup falling back to its 50.0 default — silently filtering
    # every cell out of a ratio-based plot until the user clicks anything that
    # triggers the sub-tab build. ``_load_gating_from_pipeline_info`` force-
    # builds the sub-tab when needed and writes the saved values into the
    # ThreshFracOn / FluorGating edits in one pass.
    if hasattr(app, "_load_gating_from_pipeline_info"):
        try:
            app._load_gating_from_pipeline_info()
        except Exception:
            logging.getLogger("well_viewer").exception(
                "Eager Cell Gating load from pipeline_info failed"
            )
    if hasattr(app, "_heatmap_layouts_load_from_data_dir"):
        app._heatmap_layouts_load_from_data_dir()
    # Hydrate view-state (active tab, selected wells, channel, fold-change
    # scopes, per-tab combos) — must come after the cross-cutting state
    # (channels, wells, ratios, heatmap layouts) is loaded so validation
    # against the active dataset's options can drop stale values cleanly.
    if hasattr(app, "_view_state_load_from_data_dir"):
        try:
            app._view_state_load_from_data_dir()
        except Exception:
            logging.getLogger("well_viewer").exception("view_state load failed")
    if hasattr(app, "_heatmap_sidebar_table"):
        try:
            from well_viewer.views.heatmap_layout_sidebar_view import (
                refresh_heatmap_layout_sidebar,
            )
            refresh_heatmap_layout_sidebar(app)
        except Exception:
            pass
    display = label or str(d)
    app._dir_label.setText(display)
    app._set_status(f"Loaded {n} well(s) — {display}")
    app._recalculate_threshold()
    # Apply any non-default gating thresholds that were just hydrated from
    # pipeline_info.json. Running the worker once here (rather than from
    # _recalculate_threshold, which fires on every channel/metric switch)
    # keeps Included flags consistent with the loaded thresholds.
    _kick_off_gating_after_load(app)
    app._redraw()
    # Re-sync the smFISH tab so its cached well→zip map, channel list, and
    # extractor reflect the freshly loaded dataset. Without this, a dataset
    # reload (e.g., after Analyze pipeline completes) leaves the tab pointing
    # at the previous dataset's zips and shows no images until the user
    # re-selects a well.
    try:
        from well_viewer.tabs.smfish_tab_view import smfish_sync_from_app
        smfish_sync_from_app(app)
    except Exception:
        logging.getLogger("well_viewer").exception("smfish_sync_from_app failed after load")
    # Announce on the bus. Additive — every refresh above already ran; bus
    # subscribers replace the explicit calls as tabs migrate in later phases.
    app._event_bus.dataset_loaded.emit(app._app_state.dataset)


def _kick_off_gating_after_load(app) -> None:
    if not hasattr(app, "_cell_gating_area_edit"):
        return
    try:
        cell_area = float(app._cell_gating_area_edit.text())
    except (ValueError, AttributeError):
        cell_area = 0.0
    has_non_default_gate = False
    for edit in getattr(app, "_cell_gating_fluor_gate_edits", {}).values():
        try:
            if float(edit.text()) > 0.0:
                has_non_default_gate = True
                break
        except ValueError:
            pass
    if cell_area > 0.0 or has_non_default_gate:
        from well_viewer.tabs.cell_gating_tab_view import cell_gating_start_gating_worker
        cell_gating_start_gating_worker(app)


def _looks_like_well_measurement_csv(path: Path) -> bool:
    """Guard loader input: accept only per-well measurement CSV schemas."""
    try:
        with path.open(newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader, [])
    except (OSError, StopIteration):
        return False
    if not header:
        return False
    cols = [str(c).strip().lower() for c in header]
    has_timepoint = any(c in {"timepoint_hours", "timepoint"} for c in cols)
    has_measure_col = any(
        c.endswith("_mean_intensity") or c.endswith("_smfish_count")
        for c in cols
    )
    return has_timepoint and has_measure_col


def build_tok_to_label(app) -> None:
    app._app_state.dataset.tok_to_label = {tok: p.stem for tok, p in app._app_state.dataset.well_paths.items()}
