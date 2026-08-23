"""Line-budget ratchets for the two largest monoliths (P4).

Mirrors the ``test_guardrails.py`` pattern for ``runtime_app.py``: a ceiling
that only moves **down**. A change that regrows one of these files past its
budget fails CI, nudging contributors to extract cohesive modules instead of
appending. Lower a budget when a change legitimately shrinks the file; never
raise it to make room for *appended* logic.

The one sanctioned exception is an in-file **decomposition**: splitting a giant
function into a thin orchestrator + named phase helpers adds ~def/docstring
scaffolding (raw line count rises) while strictly improving structure and
changing no behaviour. T4 did this to ``process_image_group`` — verified
byte-identical (records + every output-image hash) against the real-StarDist
pipeline across no-correction / top-hat+sliding-disk / gaussian-flat-field
configs — so the budget was re-baselined +72 once, and the ratchet resumes from
there. (A *separate*-module extraction was infeasible without a circular import:
the phases depend on ~11 functions defined in ``process_microscopy`` itself.)

Baselines (set when P4 began the decomposition):
- process_microscopy.py: 4542 lines, after the T4 ``process_image_group``
  decomposition into ``_load_/_correct_/_segment_/_write_processed_/_quantify_
  image_group`` phase helpers (was 4441 inline; +101 scaffolding, behaviour
  byte-identical). The P4 module extractions — background-correction
  (``pipeline.background.py``), Cellpose (``pipeline.cellpose.py``), StarDist
  (``pipeline.stardist.py``), output-writer (``pipeline.io.py``) — had taken
  it 5129 → ~4470.
- image_table/controller.py: 1494 lines, after extracting the image_compose /
  export / grid_layout layers — was 2486.

T4 extended the same pattern to the plot controllers by splitting the pure
data-collection half into a ``collect.py`` (mirroring image_table):
- scatter/controller.py: 875 lines, after ``scatter/collect.py`` — was 1264.
- barplot/controller.py: 731 lines, after ``barplot/collect.py`` — was 1416.
- review_image/controller.py: 963 lines, after ``review_image/collect.py`` (the
  pure include/threshold-map + row-key + override-projection data layer) — was 1392.
- analyze_tab.py: 1176 lines, after the ``_build_*`` form-section builders moved
  verbatim into ``AnalyzeTabFormMixin`` in ``analyze_tab_form.py`` (a behaviour-
  preserving mixin split of the QWidget god-class) — was 1887.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Ratcheting ceilings (only ever lowered) ──────────────────────────────────
# Re-baselined for the FOV-level incremental feature: the cohesive primitives it
# needed (the done-set diff + CSV/zip merge helpers) were extracted into the
# Qt-free pipeline.io, and the GUI scope-decision into
# input_resolution_service.select_incremental_scope — so the residual growth here
# is just orchestration that has to live in the monoliths: process_microscopy's
# --incremental CLI flags, dispatch, per-task diff/merge branches and driver
# routing (+154); analyze_tab's one-line integration hook (+8). Resume ratcheting
# down from these.
# Code-review E2/E3 (+11): narrowed the image-loader catch to log skips instead
# of swallowing (E2), and back-up-and-surface a corrupt existing pipeline_info
# sidecar instead of silently clobbering user blocks (E3) — mandatory
# correctness handling, not appended features. Resume ratcheting down from 4707.
# Code-review E9 (+22): the auto-threshold estimator now counts per-channel image
# decode failures and WARNs when a biased fraction was dropped (a silently biased
# Otsu threshold otherwise). Mandatory correctness surfacing. Resume from 4729.
# Code-review A1 (+26): the divergent directory- and zip-image-group parsers were
# unified onto one shared core (_collect_image_groups) + shared parsing primitives
# (_is_nuclear_filename / _derive_fluor_filename), so segmentation-critical
# filename parsing can no longer drift between the two paths. This is the
# sanctioned "structural refactor adds def/docstring scaffolding while behaviour
# stays byte-identical" case (verified by test_image_grouping_characterization) —
# the duplication is gone even though the raw count ticks up. Resume from 4755.
# Code-review A10 (-48): parametrized the two ~130-line process_well_zips /
# process_well_folders drivers onto one shared _run_well_worker_pool (+ a shared
# _sweep_stale_scratch_dirs); the mode-specific skip/sweep/submit become
# callables. Genuine dedup — ratcheted the budget down to the new 4707.
# 4707 -> 4692: the per-well skip/force decisions (output_exists_for_well and
# its folder twin, plus the per-well force normaliser and incremental_for_well)
# moved into the stdlib-only pipeline/well_skip.py. Genuine extraction — ratchet
# down to the new size.
# 4692 -> 4745: RAISED, deliberately, for the tunable StarDist detection
# thresholds (--stardist_prob_thresh / --stardist_nms_thresh: argparse entries,
# validation, worker-init plumbing and the run-log line that states them). The
# cohesive extraction this file's rules call for is the model-cache and
# download-lock cluster (_CORRUPT_MODEL_MARKERS / _purge_stardist_cache /
# _pid_alive / _stardist_lock_is_dead / _stardist_cache_models_dir /
# _stardist_download_lock / _load_stardist_pretrained, ~200 lines) into a
# pipeline/stardist_model.py — but a new file under pipeline/ is a new entry in
# services.remote_runner._REQUIRED_REMOTE_FILES, i.e. a change to what every
# remote machine is sent, and that was explicitly out of scope for this change.
# That cluster remains the extraction to take when this budget next binds.
PROCESS_MICROSCOPY_BUDGET = 4382

# `services/remote_runner.py` is the largest module in the repo — nearly twice
# `process_microscopy.py` — and until now the only large one with nothing
# resisting its growth. That is not incidental to its defect history: every
# recurring fault in it has been a half-finished sweep, where the same fact was
# derived in N places and N was not knowable, and a module keeps acquiring
# those for exactly as long as adding one more costs nothing.
#
# The complexity ratchet gives no pressure here either. `spawn` is 394 lines
# and 12 concerns, and its cyclomatic complexity is 22 — comfortably under the
# threshold of 40 — so length is the dimension that needs a limit and this is
# it. It only moves down: introduced at 8210, then 7994, 7606, 7419 and 7338 as
# the staging, transport, argv/run-id and upload-manifest extractions each took
# what they moved, then 7326 when the provisional-sidecar stamp went to
# `pipeline/sidecar.py`, which is where the rest of that file's blocks live.
#
# The manifest came out because two guards over it were in direct tension:
# `test_remote_manifest` requires an entry for every new `pipeline/` or `util/`
# module, and this budget caps the file it lived in — so a required entry could
# fail the budget, and the budget could only be met by not adding it. A data
# declaration with no behaviour was the right thing to move.
REMOTE_RUNNER_BUDGET = 7326
# 1494 -> 1333: the smart-layout builder cluster (flat + nested grid assembly,
# per-channel LUT re-keying, the Build presets, and the row-channel styling
# helper) moved into ``image_table/build_layout.py``. The nested / compound-axis
# feature landed there rather than growing the controller, and the controller
# re-exports the names so call sites are unchanged. Genuine extraction — ratchet
# down to the new size.
IMAGE_TABLE_CONTROLLER_BUDGET = 1333
SCATTER_CONTROLLER_BUDGET = 875
BARPLOT_CONTROLLER_BUDGET = 731
# 963 -> 980: A8 ctx-seam. review_csv/tab_view stopped naming ctx.app (4 -> 0) by
# calling refresh_review_csv[_rows] / on_review_csv_row_double_click with ctx; those
# three fns gained thin ctx-adapter wrappers here that delegate to their unchanged
# app-native bodies (the Review-CSV store/nav layer isn't ctx yet). A deliberate,
# one-time +17 for the ctx boundary — not god-logic growth.
# 1022 -> 870: the canvas-gesture *actions* moved out — what a finished
# rubber-band drag does (``review_image/callbacks.py``) and the jump-to-nucleus
# view transform (``review_image/renderer.py``, next to the other zoom entry
# points). The handlers left here decide only which gesture is in progress.
REVIEW_IMAGE_CONTROLLER_BUDGET = 870
# Ratcheted back to 1176: the loose-TIFs-in-``in/`` work centralized the
# well-count + layout detection into input_resolution_service.count_expected_wells,
# removing the duplicated counter from the tab.
# Code-review X4/X6 (+30): the entry-boundary numeric validator
# (_numeric_option_errors + _INT_FIELDS) that blocks a bad radius/FFT/mesh/QC
# value up front instead of silently dropping it. Then L2 (+5): the `_alive`
# flag + guarded log-poller re-arm so it stops on app close. Resume from 1211.
# Ratcheted to 1063: the form-reading code moved to the form mixin where it
# belongs — _collect_run_options, _background_well_validation_error, and the
# module-level field parsers (_safe_qc_float / _safe_seg_float / _INT_FIELDS /
# _numeric_option_errors). They read the form's widgets; the tab only hosts them.
# Ratcheted to 970: run-progress rendering (the ETA cluster and
# _apply_progress_event) moved to analyze_progress.AnalyzeProgressMixin. It is a
# self-contained job with one entry point, and the tab only hosts it.
# Ratcheted to 964: the grouping-phase events (zipper_start / stage_total)
# moved to analyze_progress._apply_grouping_event, beside the rest of the
# progress rendering the tab only hosts.
# 964 -> 899: the segmentation hints and their validation
# (_refresh_segmentation_hints / _segmentation_validation_errors /
# _collapse_seg_param_stack) moved to analyze_tab_seg_params.py, beside the
# parameter panel they drive.
# 899 -> 895: the Analyze pane's Preview page was removed. The Playground is the
# app's one interactive segmenter; a second, thinner preview here duplicated it
# for the sake of not re-typing two numbers into the form, which is not a trade
# worth a whole page. The right pane is a plain log panel again.
#
# 895 -> 855: the run-outcome path (_surface_run_failure and the headline
# shortener behind it) moved to analyze_tab_log.AnalyzeLogPaneMixin, which
# already owns the log view that path reveals.
ANALYZE_TAB_BUDGET = 855

# analyze_tab_form.py carried no budget while analyze_tab.py did, so every
# addition to the pair landed here by default. Opened at its measured size so it
# ratchets down like the rest instead of becoming the new dumping ground. From
# there it only moves down; the next section added to this form should come with
# an extraction, as the pipeline_info restore (_open_pipeline_info /
# _pinfo_flat / _apply_pipeline_info → analyze_tab_pinfo.AnalyzePipelineInfoMixin)
# and the form chrome (_section / _row / _entry / _info_icon / reveal_section →
# analyze_tab_chrome.AnalyzeFormChromeMixin) and the entry-boundary field
# parsers (_safe_qc_float / _safe_seg_float / _INT_FIELDS /
# _numeric_option_errors → analyze_tab_fields.py) all did.
# 1170 -> 1107: the per-method segmentation parameter pages (the NamedPageStack
# and its StarDist / watershed / Cellpose / label-free / classical pages) moved
# to analyze_tab_seg_params.AnalyzeSegParamsMixin, which is also where the
# StarDist detection thresholds landed rather than growing this form again.
# 1107 -> 1105: the Analyze Preview mixin was removed (see ANALYZE_TAB_BUDGET).
# 1105 -> 1086: the whole run-controls row (Run / Suspend / + Queue / Stop) moved
# to analyze_run_spec.AnalyzeRunMixin._build_run_row, beside the queue hooks the
# new button belongs to. Genuine extraction -- ratchet down to the new size.
# 1086 -> 994: the pane's output half (the log view, the raw-log toggle and the
# "Last run ·" card) moved to analyze_tab_log.AnalyzeLogPaneMixin. It is one job
# and it is not form-building. Genuine extraction -- ratchet down again.
ANALYZE_TAB_FORM_BUDGET = 994


def _line_count(rel: str) -> int:
    return len((REPO_ROOT / rel).read_text().splitlines())


def test_process_microscopy_line_budget():
    n = _line_count("process_microscopy.py")
    assert n <= PROCESS_MICROSCOPY_BUDGET, (
        f"process_microscopy.py is {n} lines, over the "
        f"{PROCESS_MICROSCOPY_BUDGET} budget. Extract a cohesive Qt-free module "
        "(and add it to services.remote_runner._REQUIRED_REMOTE_FILES) instead "
        "of appending. The ratchet only moves down."
    )


def test_remote_runner_line_budget():
    n = _line_count("services/remote_runner.py")
    assert n <= REMOTE_RUNNER_BUDGET, (
        f"services/remote_runner.py is {n} lines, over the "
        f"{REMOTE_RUNNER_BUDGET} budget. Extract a cohesive Qt-free module — "
        "staging, rsync/ssh transport and argv/run-id derivation are each a "
        "self-contained cluster — instead of appending. The ratchet only "
        "moves down."
    )


def test_image_table_controller_line_budget():
    n = _line_count("well_viewer/features/image_browser/image_table/controller.py")
    assert n <= IMAGE_TABLE_CONTROLLER_BUDGET, (
        f"image_table/controller.py is {n} lines, over the "
        f"{IMAGE_TABLE_CONTROLLER_BUDGET} budget. Split a cohesive cluster "
        "(LUT/RGB composition, grid layout, or export) into its own module. "
        "The ratchet only moves down."
    )


def test_scatter_controller_line_budget():
    n = _line_count("well_viewer/features/scatter/controller.py")
    assert n <= SCATTER_CONTROLLER_BUDGET, (
        f"scatter/controller.py is {n} lines, over the {SCATTER_CONTROLLER_BUDGET} "
        "budget. Keep pure data collection in scatter/collect.py; the ratchet "
        "only moves down."
    )


def test_barplot_controller_line_budget():
    n = _line_count("well_viewer/features/barplot/controller.py")
    assert n <= BARPLOT_CONTROLLER_BUDGET, (
        f"barplot/controller.py is {n} lines, over the {BARPLOT_CONTROLLER_BUDGET} "
        "budget. Keep pure data collection in barplot/collect.py and rendering in "
        "barplot/renderer.py; the ratchet only moves down."
    )


def test_review_image_controller_line_budget():
    n = _line_count("well_viewer/features/image_browser/review_image/controller.py")
    assert n <= REVIEW_IMAGE_CONTROLLER_BUDGET, (
        f"review_image/controller.py is {n} lines, over the "
        f"{REVIEW_IMAGE_CONTROLLER_BUDGET} budget. Extract a cohesive cluster "
        "(e.g. a collect.py) rather than appending; the ratchet only moves down."
    )


def test_analyze_tab_line_budget():
    n = _line_count("analyze_tab.py")
    assert n <= ANALYZE_TAB_BUDGET, (
        f"analyze_tab.py is {n} lines, over the {ANALYZE_TAB_BUDGET} budget. "
        "Carve form-build / validation / run / log-poll into helpers rather than "
        "appending; the ratchet only moves down."
    )


def test_analyze_tab_form_line_budget():
    n = _line_count("analyze_tab_form.py")
    assert n <= ANALYZE_TAB_FORM_BUDGET, (
        f"analyze_tab_form.py is {n} lines, over the "
        f"{ANALYZE_TAB_FORM_BUDGET} budget. Each form section is already its own "
        "_build_* method; extract a cohesive group of them into a sibling module "
        "rather than appending. The ratchet only moves down."
    )
