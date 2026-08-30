"""Data-load/path lifecycle helpers for WellViewerApp."""

from __future__ import annotations

# Names used only in annotations. Under ``from __future__ import
# annotations`` these are never evaluated, so importing them for real would
# add startup cost and, for several of these, an import cycle.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from PySide6.QtWidgets import QProgressDialog

import csv
import logging
import time
from pathlib import Path

from PySide6.QtWidgets import QMessageBox
from well_viewer.core.channel_state_controller import recalculate_threshold, set_ratio_metrics
from well_viewer.core.ui_helpers import (
    clear_notes_editors as _clear_notes_editors,
    refresh_plot_empty_states,
    show_progress as _show_progress,
    step_progress as _step_progress,
)
from well_viewer.core.safe import swallow, swallowed
from well_viewer.data.curation import apply_curation_to_cache
from well_viewer.domain.dataset_name import dataset_display_name


# ── "Opening dataset…" responsiveness guard ─────────────────────────────────
# Opening a dataset reads pipeline_info.json, globs the directory, sniffs every
# CSV header, then loads each well CSV — all synchronous on the GUI thread. Over
# a slow network share the pre-load phase (sniffing, before any progress) can
# block for minutes with no repaint, so Windows flags the window "Not
# Responding". These helpers show a visible modal "Opening dataset…" dialog and
# pump the event loop as each file is touched, so the window stays alive and the
# user sees live progress instead of a frozen UI.

def _make_opening_dialog(ctx, text: str) -> QProgressDialog | None:
    app = ctx.window  # transitional bridge (A3)
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QProgressDialog

        dlg = QProgressDialog(text, None, 0, 0, app)  # 0,0 = busy spinner; no Cancel
        dlg.setCancelButton(None)
        dlg.setWindowTitle("Opening dataset")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)
        dlg.show()
        # Same opt-out as every other pump in this module: nothing the user
        # clicks during a dataset open should be dispatched, and this one runs
        # after ``begin_load`` has already emptied the caches.
        from PySide6.QtCore import QEventLoop
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
        app._opening_dialog = dlg
        return dlg
    except Exception:
        swallowed()  # never let the progress UI block a load (e.g. headless)
        return None


def _opening_set_phase(ctx, text: str, *, value=None, maximum=None) -> None:
    app = ctx.window  # transitional bridge (A3)
    """Update the opening dialog's label / progress and pump the event loop so
    the window repaints (and Windows doesn't mark it 'Not Responding').

    ``ExcludeUserInputEvents``, for the reason ``ui_helpers._pump`` gives: this
    is called once per CSV from ``load_directory``'s loop, immediately after the
    progress helpers that already opt out, and a bare ``processEvents()`` here
    dispatched the clicks they had just refused — so the opt-out only ever held
    on the one route that has no opening dialog. Repainting is the whole point
    of the call and is unaffected; only input dispatch is withheld, and the
    dialog carries no Cancel button (``setCancelButton(None)``) for it to make
    unreachable.
    """
    dlg = getattr(app, "_opening_dialog", None)
    if dlg is None:
        return
    try:
        from PySide6.QtCore import QEventLoop
        from PySide6.QtWidgets import QApplication
        if maximum is not None:
            dlg.setMaximum(int(maximum))
        dlg.setLabelText(text)
        if value is not None:
            dlg.setValue(int(value))
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
    except Exception:
        swallowed()


def _close_opening_dialog(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    dlg = app.__dict__.pop("_opening_dialog", None)
    if dlg is not None:
        try:
            dlg.close()
            dlg.deleteLater()
        except Exception:
            swallowed()


def _resolve_fov_tp_extractor(extractor, pipeline_info: dict) -> Callable | None:
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
    from well_viewer.data.image_discovery import make_default_fov_tp_extractor
    return make_default_fov_tp_extractor(sep)


def load_path(ctx, path: Path) -> None:
    app = ctx.window  # transitional bridge (A3)
    if not path.is_dir():
        QMessageBox.critical(app, "Not a directory", f"Expected a directory:\n{path}")
        return
    app._cleanup_tmp()
    # Visible "Opening dataset…" guard so a slow-network open never looks frozen.
    _make_opening_dialog(ctx, "Opening dataset…")
    try:
        in_dir = path / "in"
        out_dir = path / "out"
        if in_dir.is_dir() and out_dir.is_dir():
            logger = logging.getLogger("well_viewer")
            logger.info("Detected in/out layout: in=%s  out=%s", in_dir, out_dir)
            logger.info("Fluor images from: %s  |  masks/overlays from: %s", in_dir, out_dir)
            app._app_state.dataset.in_dir = in_dir
            _opening_set_phase(ctx, "Reading dataset info…")
            app._app_state.dataset.fov_tp_extractor, _fluor_tokens, _smfish_tokens, app._app_state.dataset.pipeline_info = app._read_pipeline_info(out_dir)
            app._app_state.dataset.fov_tp_extractor = _resolve_fov_tp_extractor(app._app_state.dataset.fov_tp_extractor, app._app_state.dataset.pipeline_info)
            app._app_state.channel.smfish_channels = _smfish_tokens
            app._load_directory(out_dir, label=f"{path.name}/out")
        else:
            app._app_state.dataset.in_dir = None
            _opening_set_phase(ctx, "Reading dataset info…")
            app._app_state.dataset.fov_tp_extractor, _fluor_tokens, _smfish_tokens, app._app_state.dataset.pipeline_info = app._read_pipeline_info(path)
            app._app_state.dataset.fov_tp_extractor = _resolve_fov_tp_extractor(app._app_state.dataset.fov_tp_extractor, app._app_state.dataset.pipeline_info)
            app._app_state.channel.smfish_channels = _smfish_tokens
            app._load_directory(path)
    finally:
        _close_opening_dialog(ctx)
    _remember_if_it_loaded(ctx, path)


def _remember_if_it_loaded(ctx, path: Path) -> None:
    """Add *path* to the recent-datasets list, if the load actually found wells.

    **On success only, and success means wells.** A folder that opened without
    raising and yielded nothing readable — the wrong directory, an ``out/`` the
    pipeline never wrote to — is not a dataset the user wants offered back to
    them on the next launch, and offering it would make the list a record of
    the user's mistakes rather than of their work. ``well_paths`` is the test
    because it is what every other part of Review treats as "there is a dataset
    here".

    The path stored is the one Review was *pointed at*, not the ``out`` half it
    may have descended into, so reopening from the list takes the same branch
    through :func:`load_path` that the original open took. The name stored is
    ``dataset_display_name``'s answer for the folder actually loaded, which is
    the one that knows to say the root's name rather than the literal "out".

    Failing to remember must never cost the user the dataset they just opened,
    which is already loaded and on screen by the time this runs — so a settings
    backend that will not write is logged and dropped.
    """
    dataset = ctx.state.dataset
    if not dataset.well_paths:
        return
    # One listdir, on a path already open, at the one moment the set of wells is
    # known to be current: a cache entry whose CSV has been renamed or deleted
    # can never be read or overwritten again, so nothing else would ever reclaim
    # it. Failures are the pruner's own to swallow.
    from well_viewer.data.parquet_cache import prune
    prune(Path(dataset.data_dir or path))
    with swallow("record the dataset in the recent-datasets list"):
        from well_viewer.core.recent_datasets import record_open
        loaded = dataset.data_dir or path
        record_open(ctx.window._qsettings(), path,
                    dataset_display_name(loaded), time.time())


def _reset_gating_state(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    """Clear the previous dataset's scatter-gate / cell-gating / per-cell
    override state on a dataset swap.

    The persistence loaders (``cell_gating.load_from_pipeline_info``,
    ``cell_overrides.load_from_data_dir``) early-return when the new dataset has
    no sidecar, leaving this state in place — where it silently filters the new
    dataset's cells / redefines fraction-on (audit X1). Reset it to the "no
    gating" defaults before hydration; the new dataset's own sidecar (if any)
    repopulates it afterwards.

    Epic-cellgating: the scatter-gate geometry + use-flags + per-layer gate
    selections are typed state on ``ctx.state.cell_gating``, reset here in
    lock-step with the R6 gate values.
    """
    cg = app._app_state.cell_gating
    cg.scatter_gates = []
    cg.use_scatter_gates = False
    cg.use_scatter_thresh = False
    cg.gating_choices = None
    cg.thresh_choices = None
    # The gate *values* leak the same way as the scatter-gate config: the
    # cell_gating loader merges them in additively and early-returns for a
    # dataset with no gating block, so a stale fluor gate / ThreshFracOn would
    # silently filter the new dataset's cells. Clear the typed slice; no widget
    # sync is needed — recalculate_threshold (later in the load) rebuilds the
    # Cell-Gating rows to their defaults before re-applying any persisted values,
    # and the gating accessors read this slice, not the edits.
    cg.fluor_gates.clear()
    cg.thresh_frac_on.clear()
    cg.thresh_count_on.clear()
    ctx.runtime.review.included_overrides = {}
    # Through ``feature_slice``, like every other feature slice this function
    # touches: attribute access raises for a build with Heat Map profiled out,
    # and this line runs on *every* dataset open, so it would take the whole
    # app down rather than degrade one tab.
    _heatmap = app._app_state.feature_slice("heatmap")
    if _heatmap is not None:
        _heatmap.active_layout_name = None


def _reset_sample_definitions_state(ctx) -> None:
    """Clear the previous dataset's sample-definition selection state on a swap.

    ``selections`` / ``current_selection_id`` / ``notes_text`` are only
    overwritten by the sample-definitions hydrator when the new dataset has a
    block; ``active_solo_wells`` is never reset. Left in place, they'd carry the
    previous dataset's replicate sets / groups / notes into the new one (audit
    X1 pattern, mirroring :func:`_reset_gating_state`). ``well_labels`` is reset
    in ``DatasetState.begin_load``; ``plot_groups`` is reset in the load block;
    ratio metrics, heat-map layouts and the Multivariate configuration are reset
    by their own hydrators, which clear when the new dataset has no section
    rather than returning — the channel-combo cascade a ratio change triggers
    must not run here, on the not-yet-loaded dataset.
    """
    sel = ctx.state.selection
    sel.selections = []
    sel.current_selection_id = None
    sel.active_solo_wells.clear()
    sel.notes_text = ""
    # Sync every Notes editor so none keeps showing the previous dataset's text
    # (the hydrator re-fills them only when the new dataset has notes). Mirrors
    # the clear in ``reset_scopes._clear_sample_definitions``; no-op for an
    # editor that has not been built.
    _clear_notes_editors(ctx)


def _report_incomplete_wells(ctx) -> None:
    """Announce wells whose results cover fewer FOV-groups than their input.

    The pipeline records this per well in ``pipeline_info.json``'s
    ``run_report`` block (``pipeline/run_report.py``). It exists because a well
    whose FOV-groups partly failed still writes a CSV and an ``<well>_out.zip``
    built from the groups that survived — output that is, on disk, identical in
    shape to a well that processed cleanly. Every per-field number for such a
    well then describes whatever fields happened to make it through, and the
    first symptom a user sees is an error bar that isn't there.

    So: say it at load, by name, before any plot is drawn from it.
    """
    report = (ctx.state.dataset.pipeline_info or {}).get("run_report") or {}
    incomplete = [w for w in (report.get("incomplete_wells") or [])
                  if w in ctx.state.dataset.well_paths]
    if not incomplete:
        return
    wells = report.get("wells") or {}
    missing = sum(
        int(wells.get(w, {}).get("groups_total", 0))
        - int(wells.get(w, {}).get("groups_covered", 0))
        for w in incomplete
    )
    shown = ", ".join(incomplete[:6]) + ("…" if len(incomplete) > 6 else "")
    msg = (f"{len(incomplete)} well(s) are incomplete ({shown}) — {missing} "
           "FOV-group(s) missing from their results")
    logging.getLogger("well_viewer").warning(
        "%s. Per-field statistics for these wells describe only the fields that "
        "were processed. Details: %s",
        msg,
        "; ".join(
            f"{w}: {wells.get(w, {}).get('groups_covered', 0)}"
            f"/{wells.get(w, {}).get('groups_total', 0)} FOV-groups"
            for w in incomplete[:6]
        ),
    )
    ctx.notify.set_status(msg + " — see the log.")
    ctx.notify.toast(msg, kind="warn", msec=9000)


def _report_persistence_doc_state(ctx) -> None:
    """Say out loud when this dataset's saved state could not be read.

    Two ways ``persistence.json`` fails to come back, and both of them leave
    Review looking exactly like a plate nobody has ever configured: every
    ratio, layout, group, note, curation mark and QC parameter simply absent.
    Silence there is the worst possible answer, because the natural next move
    is to define all of it again — and the writes that follow either go into a
    file the app has already decided not to touch, or over the top of the one
    copy of the work.

    - *degraded*: the file did not parse. ``_doc.write`` sets it aside as
      ``persistence.unreadable-N.json`` on the next save rather than
      overwriting it, so the work is recoverable by hand.
    - *foreign*: the file carries another build's ``schema_version``. It is
      perfectly legible, just not by this build, so it is left exactly where it
      is and this session writes nothing to it — an edit made now is lost when
      the window closes.

    Said once per load, in the status line, a toast and the log, the same way
    an incomplete run is announced.
    """
    ds = ctx.state.dataset
    if getattr(ds, "persistence_doc_foreign", False):
        msg = ("This dataset's saved state was written by a different version "
               "of All-Well — it is being left untouched, so changes made now "
               "will not be saved")
    elif getattr(ds, "persistence_doc_degraded", False):
        msg = ("This dataset's saved state could not be read — Review has "
               "started from defaults, and the unreadable file will be kept "
               "beside the new one")
    else:
        return
    logging.getLogger("well_viewer").warning("%s.", msg)
    ctx.notify.set_status(msg + " — see the log.")
    ctx.notify.toast(msg, kind="warn", msec=12000)


def _report_unusable_fov_labels(ctx) -> None:
    """Announce wells whose FOV labels don't identify their fields of view.

    Every per-field number the app produces — the bar and line error bars, the
    events-per-field panel, the Statistics tab's ``n`` — groups on the CSV's
    ``fov`` column. When a well's filenames didn't match the schema that column
    arrives as a single repeated placeholder, and the pooled result is
    indistinguishable from a genuine single-field well. The aggregation now
    reports those statistics as undefined instead of guessing, but silence about
    *why* is what let the original problem look like a plotting glitch, so this
    says it once, by name, in the status line, a toast, and the log.

    Rows are loaded lazily, so this reads the verdicts recorded by ``get_rows``
    for the wells whose frames the load pass already built.
    """
    verdicts = getattr(ctx.state.dataset, "fov_integrity", None) or {}
    bad = sorted(w for w, v in verdicts.items() if not getattr(v, "trusted", True))
    if not bad:
        return
    shown = ", ".join(bad[:6]) + ("…" if len(bad) > 6 else "")
    msg = (f"{len(bad)} well(s) have unusable FOV labels ({shown}) — per-field "
           "statistics are undefined for them")
    logging.getLogger("well_viewer").warning(
        "%s. Reasons: %s", msg,
        "; ".join(f"{w}: {verdicts[w].reason}" for w in bad[:6]),
    )
    ctx.notify.set_status(msg + ".")
    ctx.notify.toast(msg, kind="warn", msec=9000)


def load_directory(ctx, d: Path, label=None) -> None:
    app = ctx.window  # transitional bridge (A3)
    _opening_set_phase(ctx, "Scanning folder…")
    csvs_all = [p for p in sorted(d.glob("*.csv")) if not p.name.startswith(".")]
    # Sniffing opens every CSV header (a network read each) — drive it as a
    # progress loop that pumps the event loop so the window stays responsive
    # instead of silently hanging before the load even starts.
    csvs = []
    n_all = len(csvs_all)
    _opening_set_phase(ctx, f"Scanning {n_all} file(s)…", value=0, maximum=n_all or 1)
    for i, p in enumerate(csvs_all, 1):
        if _looks_like_well_measurement_csv(p):
            csvs.append(p)
        _opening_set_phase(ctx, f"Scanning {i}/{n_all}…", value=i)
    skipped = [p for p in csvs_all if p not in csvs]
    if skipped:
        logging.getLogger("well_viewer").info(
            "Skipping %d non-measurement CSV(s): %s",
            len(skipped),
            ", ".join(p.name for p in skipped[:8]) + ("…" if len(skipped) > 8 else ""),
        )
    if not csvs:
        _close_opening_dialog(ctx)  # don't stack a warning behind the modal guard
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
    # Leave Split View before swapping datasets: the load restores view-state
    # (which navigates the notebook) and runs a full redraw, both of which assume
    # the normal single-notebook layout — the detached split panes break it.
    # Guarded so the feature stays one-line removable (``_split_active`` is never
    # set when split_view isn't installed).
    if ctx.runtime.split.active:
        from well_viewer.core.split_view import (
            exit_split, sync_split_toggle_button,
        )
        exit_split(ctx)
        sync_split_toggle_button(ctx, False)
    # Save the outgoing dataset's state before swapping data dirs, so reopening
    # it later restores what the user left — and so that no debounced write is
    # still queued when the data directory changes underneath it. A pending
    # ``schedule_save`` timer that survives the swap fires against the *new*
    # dataset's directory and writes the previous dataset's ratios, layouts or
    # curation into it.
    if app._app_state.dataset.data_dir is not None:
        try:
            app._persistence.save("view_state")
        except Exception:
            logging.getLogger("well_viewer").exception("view_state save on switch failed")
        try:
            app._persistence.flush_all()
        except Exception:
            logging.getLogger("well_viewer").exception(
                "pending autosave flush on dataset switch failed")
    # DatasetState resets the per-dataset caches (data dir, well paths, row
    # cache, persistence doc) for the switch to the new data directory.
    app._app_state.dataset.begin_load(d)
    # Let this dataset speak for itself about a missing ``area_px``. The
    # warning is deduplicated per entry point so it is one line rather than one
    # per redraw, and a set never cleared makes the *second* dataset a session
    # opens the silent one — which is the one whose empty plots are harder to
    # explain, because the first dataset's warning has scrolled away.
    from well_viewer.data.data_loading import reset_missing_area_warnings
    reset_missing_area_warnings()
    app._app_state.selection.selected_wells.clear()
    app._app_state.selection.last_sel = None
    app._app_state.selection.prev_sel = set()
    app._app_state.selection.plot_groups = []
    # Per-dataset state that names wells, timepoints or intensities of the
    # plate being closed. Each slice owns the list of what that means for it;
    # ``view_state.restore`` puts back whatever the *new* dataset saved, and it
    # early-returns when the new plate has no saved block — which is why these
    # have to be cleared here rather than merely overwritten there.
    app._app_state.plot.reset_for_new_dataset()
    app._app_state.export_prefs.reset_for_new_dataset()
    app._app_state.image_view.reset_for_new_dataset()
    # Drop the per-channel threshold (min, max) cache; the new dataset's
    # ranges may be completely different.
    app._app_state.channel.threshold_range_cache.clear()
    # Drop the Image Table's persistent decoded-image cache + last render — the
    # new dataset's pixels differ, so the cross-Generate cache must not carry
    # over (it is keyed only by well/channel/variant, not by data dir). Skipped
    # when Image Browser is disabled (no image_table slice).
    _image_table = app._app_state.feature_slice("image_table")
    if _image_table is not None:
        _image_table.image_cache = {}
        _image_table.last_render = {}
        _image_table.prefetched_cells = set()
        _image_table.scan_misses = set()
        _image_table.raw_fallbacks = set()
        _image_table.partial_scans = set()
        _image_table.scale_bar_refusals = set()
        # Cleared per load, set True by view_state_io.apply_image_table when it
        # restores a saved grid for this load — tells the on_dataset_loaded reset
        # hook below (registry._loaded_image_table) not to clobber a selection
        # that was just correctly restored (view_state.restore() runs, and can
        # force-build + apply this tab, earlier in this same load).
        ctx.runtime.image_table.restored_this_load = False
    # Gate on the review_image slice, not hasattr(method): runtime_app defines
    # _invalidate_review_image_frame_cache unconditionally (a lazy shim), so the
    # hasattr is always true — the slice is the real "Image Browser present" test.
    if app._app_state.feature_slice("review_image") is not None and hasattr(
        app, "_invalidate_review_image_frame_cache"
    ):
        app._invalidate_review_image_frame_cache()
    # Drop the image caches on a dataset swap: every cached ZipFile handle, so
    # the new dataset's per-well zips aren't read through a previous dataset's
    # handle (or, worse, one pointing at a since-deleted file), and the app-wide
    # decoded-array LRU, whose pixels belong to the previous dataset. One call
    # for both, so neither can be dropped without the other.
    try:
        from well_viewer.data.image_repository import ImageRepository
        ImageRepository.invalidate()
    except Exception:
        swallowed()  # was: pass (silent except)
    # Clear the prior dataset's gate / override / cell-gating state before the
    # new dataset hydrates (audit X1 — stops cross-dataset gate leakage).
    _reset_gating_state(ctx)
    # Same X1 pattern for the sample-definition selection state (replicate sets /
    # groups / notes / solo wells): the hydrator early-returns for a dataset with
    # no sample_definitions block, so clear before it runs.
    _reset_sample_definitions_state(ctx)
    # Signal any in-flight smFISH "Apply to All" worker to abort so
    # it doesn't overwrite the *new* dataset's CSVs with counts
    # computed against the previous dataset.
    smfish_cancel = ctx.runtime.smfish.cancel_event
    if smfish_cancel is not None:
        try:
            smfish_cancel.set()
        except Exception:
            swallowed()  # was: pass (silent except)
    n = len(csvs)
    # ``allow_user_input=False`` on both pumps below: this loop empties the
    # caches first and refills them one CSV at a time, so a click dispatched
    # between two files reaches the sidebar, commit and tab handlers while the
    # dataset is half-built. The repaint is still wanted — a plate of CSVs on a
    # network share takes long enough that a frozen window reads as a hang.
    _show_progress(ctx, n, f"Loading {n} CSV file(s)…", allow_user_input=False)
    _opening_set_phase(ctx, f"Loading {n} well CSV(s)…", value=0, maximum=n)
    for i, p in enumerate(csvs, 1):
        tok = app._extract_well_token(p.stem)
        key = tok if tok else p.stem
        # The repository's load is the same one a lazy read reaches on a cache
        # miss: it registers the path, reads the CSV, runs every registered
        # projector and records the FOV verdict. This pass used to do those four
        # things itself, which is how the eager and lazy paths came to disagree
        # about which layers a cached frame carries.
        ctx.state.repo.load_well(key, p)
        _step_progress(ctx, i, f"Loading {i}/{n}: {p.name}",
                       allow_user_input=False)
        _opening_set_phase(ctx, f"Loading {i}/{n}: {p.name}", value=i)
    app._hide_progress()
    app._rebuild_all_timepoints_cache()
    app._rebuild_all_fovs_cache()
    build_tok_to_label(ctx)
    # Apply any sample_definitions persisted inside pipeline_info.json before
    # we prune groups against the loaded well set, so saved replicate-set and
    # group state is hydrated and then validated by the same pruning pass that
    # cleans stale wells.
    if hasattr(app, "_load_sample_definitions_from_pipeline_info"):
        try:
            app._load_sample_definitions_from_pipeline_info()
        except Exception:
            swallowed()  # was: pass (silent except)
    try:
        app._persistence.load("plot_groups")
    except Exception:
        swallowed()
    try:
        app._persistence.load("export_groups")
    except Exception:
        swallowed()
    if hasattr(app, "_plot_groups_refresh"):
        try:
            app._plot_groups_refresh()
        except Exception:
            swallowed()
    # Hydrate per-cell Segmentation-tab overrides (cell_overrides.json) before
    # any controller queries cached rows, then project them onto row['Included']
    # so all downstream stats honor user curation. Both calls are safe no-ops
    # when no patch file exists.
    try:
        app._persistence.load("cell_overrides")
        apply_curation_to_cache(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)
    # Hydrate QC-layer thresholds (qc_params.json, else seed from pipeline_info)
    # and project the QC_Pass verdict onto any already-cached rows. Safe no-op
    # when neither file exists.
    try:
        app._persistence.load("qc_params")
        # recompute_qc_all (not bare apply_qc_to_cache): project QC_Pass AND
        # invalidate the stats/aggregation cache + bump override_version, so the
        # post-load redraw actually reflects the just-hydrated thresholds. Bare
        # apply only wrote the column, leaving any cache built earlier in the
        # load sequence stale — the gate loaded into the fields but didn't take.
        ctx.features.call("qc.recompute_all", ctx)
        # Sync the Cell Gating QC gate row if it has been built.
        ctx.features.call("qc.refresh_gate_widgets", app)
    except Exception:
        swallowed()  # was: pass (silent except)
    # Hydrate the crop region and the frames it is scoped to
    # (persistence.json::crop_region) and project Crop_Pass onto any
    # already-cached rows. Safe no-op when unset.
    try:
        app._persistence.load("crop_region")
        ctx.features.call("crop.project_cache", ctx)
    except Exception:
        swallowed()  # was: pass (silent except)
    # Hydrate user-defined line-plot draw order (line_order.json).
    try:
        app._persistence.load("line_order")
    except Exception:
        swallowed()  # was: pass (silent except)
    app._refresh_sidebar_map()
    _preview = app._app_state.feature_slice("preview")
    if _preview is not None and _preview.selected_well not in app._app_state.dataset.well_paths:
        _preview.selected_well = None
    if hasattr(app, "_sidebar_preview_plate"):
        app._refresh_preview_picker()
    ctx.features.call("labels.grid_refresh", ctx)
    # Refresh Home's definition tabs so any newly loaded labels / groups
    # appear without requiring a tab switch.
    if hasattr(app, "_groups_centre_refresh"):
        try:
            app._groups_centre_refresh()
        except Exception:
            swallowed()  # was: pass (silent except)
    # Auto-load any persisted ratio definitions and heatmap layouts that live
    # in the same data directory. Both calls are safe no-ops when no file
    # exists; errors are logged and swallowed.
    #
    # Reset ratio metrics first: ``ratios.load_from_data_dir`` early-returns when
    # the new dataset has no ``ratios`` section, which would leave the previous
    # dataset's ratio channels defined (and computing wrong per-cell values).
    # Done here (not in the early reset block) because ``set_ratio_metrics`` runs
    # a channel-combo cascade that must see the loaded dataset — the same call the
    # ratios hydrator makes below, and ``recalculate_threshold`` rebuilds the
    # combos afterward.
    set_ratio_metrics(ctx, [])
    app._persistence.load("ratios")
    # The plate's annotation axes. Unconditional, and its loader assigns even
    # when the new dataset has no block — the failure mode being avoided is the
    # one recorded three lines above for ratios: a plot grouped by `Dose` would
    # otherwise keep drawing the previous plate's doses over this plate's
    # numbers, with the page's own masthead naming the new dataset.
    app._persistence.load("sample_variables")
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
    # Hydrate saved smFISH thresholds so the spinbox shows the value
    # that matches the spot counts already baked into the per-well CSVs.
    # The smFISH tab is built lazily, so this primarily populates the
    # in-memory cache (app._smfish_thresholds_saved); the spinbox itself
    # is restored when the tab populates its channel combo.
    app._persistence.load("heatmap_layouts")
    # Hydrate view-state (active tab, selected wells, channel, fold-change
    # scopes, per-tab combos) — must come after the cross-cutting state
    # (channels, wells, ratios, heatmap layouts) is loaded so validation
    # against the active dataset's options can drop stale values cleanly.
    try:
        app._persistence.load("view_state")
    except Exception:
        logging.getLogger("well_viewer").exception("view_state load failed")
    if hasattr(app, "_heatmap_sidebar_table"):
        try:
            ctx.features.call("heatmap.refresh_layout_sidebar", ctx)
        except Exception:
            swallowed()  # was: pass (silent except)
    display = label or str(d)
    app._dir_label.setText(display)
    from well_viewer.core.app_shell_view import refresh_dataset_button
    refresh_dataset_button(app)
    app._set_status(f"Loaded {n} well(s) — {display}")
    _report_incomplete_wells(ctx)
    _report_unusable_fov_labels(ctx)
    # After the hydrators, because the flags are set by the first ``_doc.read``
    # and the loads above are what trigger it.
    _report_persistence_doc_state(ctx)
    recalculate_threshold(ctx)
    # Apply any non-default gating thresholds that were just hydrated from
    # pipeline_info.json. Running the worker once here (rather than from
    # _recalculate_threshold, which fires on every channel/metric switch)
    # keeps Included flags consistent with the loaded thresholds.
    _kick_off_gating_after_load(ctx)
    # Re-sync the smFISH tab so its cached well→zip map, channel list, and
    # extractor reflect the freshly loaded dataset. Without this, a dataset
    # reload (e.g., after Analyze pipeline completes) leaves the tab pointing
    # at the previous dataset's zips and shows no images until the user
    # re-selects a well. (Stays a direct call — a tab-specific resync, not a
    # redraw; it moves onto the bus when the smFISH tab is relocated.)
    # Feature-owned post-load refresh, fanned out from the registry so core
    # names no feature and imports none that a build profile excluded. Each
    # feature's ``on_dataset_loaded`` hook owns its ordering + guards internally.
    for _descriptor in app._features.with_hook("on_dataset_loaded"):
        try:
            _descriptor.on_dataset_loaded(ctx)
        except Exception:
            logging.getLogger("well_viewer").exception(
                "on_dataset_loaded hook for %r failed", _descriptor.name)
    # Announce on the bus. RedrawCoordinator subscribes dataset_loaded and runs
    # the post-load plot redraw — the emit is the redraw trigger.
    app._event_bus.dataset_loaded.emit(app._app_state.dataset)
    # Sync the band's ThreshFracOn quick-edit once everything has settled (the
    # gating edits are now hydrated and the active channel is final), so the box
    # shows the loaded threshold instead of its 50.0 default.
    refresh = getattr(app, "_refresh_thresh_frac_quick_edit", None)
    if callable(refresh):
        refresh()
    # Re-run the active tab's activation. Sidebar visibility + per-tab population
    # is owned by on_tab_change -> the active leaf's on_activate; but a same-tab
    # (re)load emits no notebook currentChanged (setCurrentByName is a no-op on
    # the current page), so the active tab would otherwise keep its pre-load,
    # empty sidebar until the user switched tabs — the reported Image Table
    # "wells/channels don't load until I switch tabs / drag" bug. on_tab_change
    # reads the current leaf itself and every on_activate is idempotent, so this
    # cleanly repopulates whichever tab is active. Guarded so a refresh failure
    # never breaks the (critical) load path.
    try:
        from well_viewer.core.tab_controller import on_tab_change
        on_tab_change(ctx)
    except Exception:
        logging.getLogger("well_viewer").exception(
            "post-load active-tab refresh failed")


def _kick_off_gating_after_load(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    if not hasattr(app, "_cell_gating_fluor_gate_edits"):
        return
    # Cell-area gating is retired (now QC-owned); only a non-default
    # fluorescence gate needs the gating worker kicked off here. Values come
    # from the typed slice (R6), synced from the edits / persistence load.
    has_non_default_gate = any(
        float(val) > 0.0
        for val in app._app_state.cell_gating.fluor_gates.values()
    )
    # Scatter-gate inclusion / thresholding modes also need the pass so
    # Gate_Pass / Gate_On are computed from the persisted gates on load (neither
    # has a per-channel edit to inspect).
    use_scatter = app._app_state.cell_gating.use_scatter_gates
    use_thresh = app._app_state.cell_gating.use_scatter_thresh
    if has_non_default_gate or use_scatter or use_thresh:
        ctx.features.call("cell_gating.start_worker", ctx)
        return
    # No worker, so nothing else repaints the tab's "Included for analysis"
    # line and its funnel — and this is the commonest path, since a dataset
    # whose gates are all at their defaults takes it. Without the refresh the
    # label keeps the *previous* dataset's counts after a plate swap: a real
    # number, for the wrong plate, with nothing to distinguish it from the
    # right one. ``cell_gating_refresh_included_count`` recomputes from the row
    # cache and no-ops when the label has not been built yet.
    ctx.features.call("cell_gating.refresh_included_count", ctx)


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


def build_tok_to_label(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    app._app_state.dataset.tok_to_label = {tok: p.stem for tok, p in app._app_state.dataset.well_paths.items()}


def load_path_shim(ctx, path):
    app = ctx.window  # transitional bridge (A3)
    # Drain any pending deferred tab builds so post-load redraw paths
    # (e.g., the bar redraw in _recalculate_threshold call sites) hit
    # fully-constructed tabs instead of unbuilt placeholders.
    app._drain_pending_centre_builders()
    load_path(ctx, path)
    refresh_plot_empty_states(app)
