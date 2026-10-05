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
- analyze/tab.py: 1176 lines, after the ``_build_*`` form-section builders moved
  verbatim into ``AnalyzeTabFormMixin`` in ``analyze/form.py`` (a behaviour-
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
# routing (+154); analyze.tab's one-line integration hook (+8). Resume ratcheting
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
#
# 4336 -> 4048: the empty-well cluster (the blank sampler, both model builders,
# the per-image field lookup and the guard that stops a run whose requested
# correction could not be built) moved to `pipeline/empty_well.py`, taking the
# new-manifest-entry cost the note above declined. Every name is re-exported,
# so no call site and no test moved with it.
# 4048 -> 3828: the StarDist pretrained-weights cache moved to
# pipeline.stardist_cache -- the pkg_resources shim, the Keras cache lookup, the
# cross-process download lock, its dead-holder reclaim and the corrupt-cache
# purge. One job, and this file could not otherwise grow at all. Genuine
# extraction -- ratchet down to the new size.
# 3828 -> 3804: field quality is measured per FOV-group and recorded per well,
# which cost this file about 140 lines of wiring, so two things moved out to
# pay for it and then some. The post-run auto-threshold pass --
# `_apply_thresholds_to_pipeline_info`, `_segmentation_tokens` and
# `run_auto_threshold_pass` -- is now `pipeline/auto_threshold_pass.py`: it
# decides which of the two estimators a run's arguments call for, merges the
# result into the sidecar without overwriting a user's value, and says what it
# managed, none of which is a threshold calculation. And composing one
# FOV-group's quality record moved beside the metrics it composes, as
# `pipeline.field_quality.group_record`. Every name is re-exported, so no call
# site moved with either.
# 3804 -> 3766: twenty-five re-exports nothing reached any more were deleted.
# 3766 -> 3733: every caller moved to the owning module and the remaining
# re-exports went, with the Cellpose ``__getattr__`` shim
# (`tests/test_process_microscopy_facade.py`).
PROCESS_MICROSCOPY_BUDGET = 3733

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
# 7326 -> 7145: merging the field-quality block across a remote fetch cost
# nineteen lines here, and the conda-environment cluster moved out to pay for
# them -- `services/remote_conda.py`. One job, asked in four places (the
# single-run launch, the batched driver, the scheduler handoff and the
# pre-flight probe) and needing the identical answer in all four: given a
# host's `remote_python`, which conda environment does the pipeline run in, is
# it named or an absolute prefix, what `conda run` argv is that, and what
# shell guard proves it exists before anything launches into it. Every name is
# re-exported, so no call site and no test moved with it.
# 7145 -> 7061: the file was exactly at its budget, so the two comments the
# unarchived-delete guard's new exemptions need at their sites could not be
# written at all -- the budget could only be met by leaving an exemption
# unexplained. The shell a granted node runs while it waits, and the clocks
# that time it, moved to `services/remote_wait_snippets.py`: one job, asked by
# the single-shot job waiting for its upload and by the batch driver waiting
# for its next batch, with one dependency and nothing that runs anything. Every
# name is re-exported, so no call site and no test moved with it.
# 7061 -> 6853: a remote Suspend needed a request file threaded through the
# launch, the batch driver and the batched loop, and the file was again exactly
# at its budget. The shell commands a run sends — the launch line, the batch
# driver, the scratch export and the batch handshake names — moved to
# `services/remote_commands.py`: pure string composition with no ssh and no
# I/O, which is also what lets the driver be tested by running it in bash.
# Every name is re-exported, so no call site and no test moved with it.
# 6853 -> 6826: the review of that change needed the request made in a
# different order and the batch upload made interruptible, and the request
# itself moved to `services/remote_suspend.py` to pay for it.
# 6826 -> 6810: an incremental run stopped borrowing earlier runs' directories,
# and the full-cache-hit predicate that decides it moved to
# `services/remote_run_id.py`, whose subject is exactly whether an earlier
# run's work may be reused.
# 6810 -> 4760: the Fetcher and end-of-run output verification moved to
# `services/remote_fetch.py`, the run record to `services/remote_run_state.py`
# and the line vocabulary to `services/remote_wire.py`; ten re-exports nothing
# reached were deleted and `__all__` states the supported facade. Tests import
# what moved from its owner (`tests/test_remote_runner_facade.py`).
# 4760 -> 3497: local input staging — layout detection, the zip-up, the
# channel filter and the incremental split — moved to
# `services/remote_staging.py`.
# 3497 -> 1356: the remote process wrappers (single launch and batched), the
# run heartbeat and their helpers moved to `services/remote_process.py`, and
# the upload cache with its rsync streaming to `services/remote_upload.py`.
# What is left is the orchestrator and the facade.
# 1356 -> 1243: `spawn` split into its phases, and the scheduler-job
# teardown and orphan sweep moved to `services/remote_jobs.py`.
# 1243 -> 1183: the last thirteen compatibility re-exports deleted once every
# consumer imported from the owning module.
REMOTE_RUNNER_BUDGET = 1183
# Set at extraction: closing a run's scheduler job and sweeping a crashed one's.
REMOTE_JOBS_BUDGET = 215
# Set at extraction: the process wrappers and the batch driver's loop.
REMOTE_PROCESS_BUDGET = 1400
# Set at extraction: the upload cache and its rsync streaming.
REMOTE_UPLOAD_BUDGET = 703
# Set at extraction: the Fetcher and its verifier, moved out of the runner.
REMOTE_FETCH_BUDGET = 2052
# Set at extraction: staging the user's input, moved out of the runner.
REMOTE_STAGING_BUDGET = 1348
# 1494 -> 1333: the smart-layout builder cluster (flat + nested grid assembly,
# per-channel LUT re-keying, the Build presets, and the row-channel styling
# helper) moved into ``image_table/build_layout.py``. The nested / compound-axis
# feature landed there rather than growing the controller, and the controller
# re-exports the names so call sites are unchanged. Genuine extraction — ratchet
# down to the new size.
IMAGE_TABLE_CONTROLLER_BUDGET = 1333
# 875 -> 831: the aggregate figure's two compact legend keys moved into
# ``scatter/_annotations.py`` beside the value-label overlay — both are text
# written over the points rather than anything deciding what a point is, the
# same seam ``barplot/_annotations.py`` draws. Genuine extraction — ratchet
# down to the new size.
SCATTER_CONTROLLER_BUDGET = 831
# The drag-to-reorder handlers (press / motion / release, the Reset Order
# button, and the event-to-data-x helper) moved into
# ``barplot/_drag_reorder.py``. A clean seam: nothing there draws a bar or
# decides a value, and the controller re-exports the names so every caller is
# unchanged. Genuine extraction — ratchet down to the new size.
# 604 -> 572: the three annotation passes over the bar panels (the cluster
# headers, the ``n/a`` undefined-spread mark, and the value-label overlay)
# moved into ``barplot/_annotations.py``. Same seam as ``_axis_fit``: nothing
# there decides what a bar *is*, only what is written over it. ``cluster_centers``
# went the other way, down into ``collect.py`` with the rest of the Qt-free
# layout maths, so the annotations module can read it without importing back up
# into this one.
# 572 -> 560: ``bar_well_display_label`` moved into ``barplot/collect.py``.
# It decides what a bar is *called*, which is the same question the member
# builders beside it already ask through ``well_display_label`` — and it now
# has to know which of the naming rungs answered, so keeping it here meant this
# module importing the naming vocabulary for one predicate.
BARPLOT_CONTROLLER_BUDGET = 560
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
# module-level field validation (the _INT_FIELDS / _FLOAT_FIELDS tables and
# _numeric_option_errors). They read the form's widgets; the tab only hosts them.
# Ratcheted to 970: run-progress rendering (the ETA cluster and
# _apply_progress_event) moved to analyze.progress.AnalyzeProgressMixin. It is a
# self-contained job with one entry point, and the tab only hosts it.
# Ratcheted to 964: the grouping-phase events (zipper_start / stage_total)
# moved to analyze.progress._apply_grouping_event, beside the rest of the
# progress rendering the tab only hosts.
# 964 -> 899: the segmentation hints and their validation
# (_refresh_segmentation_hints / _segmentation_validation_errors /
# _collapse_seg_param_stack) moved to analyze/seg_params.py, beside the
# parameter panel they drive.
# 899 -> 895: the Analyze pane's Preview page was removed. The Playground is the
# app's one interactive segmenter; a second, thinner preview here duplicated it
# for the sake of not re-typing two numbers into the form, which is not a trade
# worth a whole page. The right pane is a plain log panel again.
#
# 895 -> 855: the run-outcome path (_surface_run_failure and the headline
# shortener behind it) moved to analyze.log_pane.AnalyzeLogPaneMixin, which
# already owns the log view that path reveals.
# 855 -> 781: the Filename Schema section's helpers and its preview refresh
# (_schema_field_list / _schema_errors / _build_schema_arg / _filename_sep /
# _refresh_schema_preview, plus the _PREVIEW_TOKENS table they read) moved to
# analyze.schema_section.AnalyzeSchemaSectionMixin, beside the controls they
# write to -- which were in analyze/form.py, so the widget and the one function
# that fills it had been in different files under different budgets.
# 781 -> 737: _validate_run_request moved to analyze.run_spec.AnalyzeRunMixin,
# beside validate_for_queue and the run-time detection it now defers to. Genuine
# extraction -- the whole method moved, and its queue-aware rules live there.
ANALYZE_TAB_BUDGET = 737

# analyze/form.py carried no budget while analyze/tab.py did, so every
# addition to the pair landed here by default. Opened at its measured size so it
# ratchets down like the rest instead of becoming the new dumping ground. From
# there it only moves down; the next section added to this form should come with
# an extraction, as the pipeline_info restore (_open_pipeline_info /
# _pinfo_flat / _apply_pipeline_info → analyze.pinfo.AnalyzePipelineInfoMixin)
# and the form chrome (_section / _row / _entry / _info_icon / reveal_section →
# analyze.form_chrome.AnalyzeFormChromeMixin) and the entry-boundary field
# validation (the field tables and _numeric_option_errors →
# analyze/domain/fields.py) all did.
# 1170 -> 1107: the per-method segmentation parameter pages (the NamedPageStack
# and its StarDist / watershed / Cellpose / label-free / classical pages) moved
# to analyze.seg_params.AnalyzeSegParamsMixin, which is also where the
# StarDist detection thresholds landed rather than growing this form again.
# 1107 -> 1105: the Analyze Preview mixin was removed (see ANALYZE_TAB_BUDGET).
# 1105 -> 1086: the whole run-controls row (Run / Suspend / + Queue / Stop) moved
# to analyze.run_spec.AnalyzeRunMixin._build_run_row, beside the queue hooks the
# new button belongs to. Genuine extraction -- ratchet down to the new size.
# 1086 -> 994: the pane's output half (the log view, the raw-log toggle and the
# "Last run ·" card) moved to analyze.log_pane.AnalyzeLogPaneMixin. It is one job
# and it is not form-building. Genuine extraction -- ratchet down again.
# 994 -> 936: _build_schema_section joined the schema helpers it shares state
# with in analyze.schema_section.AnalyzeSchemaSectionMixin. Genuine extraction
# -- ratchet down. New work on that section goes in that module, which is where
# the headroom this freed is meant to be spent.
# 936 -> 729: _build_qc_section joined analyze.qc_section.AnalyzeQCSectionMixin.
# The section had one line of headroom left in this file and two controls still
# to gain, so the choice was an extraction or a raised budget, and the ratchet
# only moves down. Genuine extraction -- the whole builder moved, nothing was
# duplicated, and new Quality Control work goes in that module.
ANALYZE_TAB_FORM_BUDGET = 729


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


def test_remote_jobs_line_budget():
    n = _line_count("services/remote_jobs.py")
    assert n <= REMOTE_JOBS_BUDGET, (
        f"services/remote_jobs.py is {n} lines, over the {REMOTE_JOBS_BUDGET} "
        "budget. It owns the scheduler jobs a run holds and making sure none "
        "outlives it. The ratchet only moves down."
    )


def test_remote_staging_line_budget():
    n = _line_count("services/remote_staging.py")
    assert n <= REMOTE_STAGING_BUDGET, (
        f"services/remote_staging.py is {n} lines, over the "
        f"{REMOTE_STAGING_BUDGET} budget. It owns turning the user's input into "
        "the archives a run uploads; anything else belongs beside the stage "
        "that does it. The ratchet only moves down."
    )


def test_remote_process_line_budget():
    n = _line_count("services/remote_process.py")
    assert n <= REMOTE_PROCESS_BUDGET, (
        f"services/remote_process.py is {n} lines, over the "
        f"{REMOTE_PROCESS_BUDGET} budget. It owns the run as a process the GUI "
        "can read, wait on and stop. The ratchet only moves down."
    )


def test_remote_upload_line_budget():
    n = _line_count("services/remote_upload.py")
    assert n <= REMOTE_UPLOAD_BUDGET, (
        f"services/remote_upload.py is {n} lines, over the "
        f"{REMOTE_UPLOAD_BUDGET} budget. It owns getting input onto the host "
        "once. The ratchet only moves down."
    )


def test_remote_fetch_line_budget():
    n = _line_count("services/remote_fetch.py")
    assert n <= REMOTE_FETCH_BUDGET, (
        f"services/remote_fetch.py is {n} lines, over the {REMOTE_FETCH_BUDGET} "
        "budget. It owns bringing a run's output home and proving it arrived; "
        "anything else belongs beside the stage that does it. The ratchet only "
        "moves down."
    )


def test_image_table_controller_line_budget():
    n = _line_count("review/features/image_browser/image_table/controller.py")
    assert n <= IMAGE_TABLE_CONTROLLER_BUDGET, (
        f"image_table/controller.py is {n} lines, over the "
        f"{IMAGE_TABLE_CONTROLLER_BUDGET} budget. Split a cohesive cluster "
        "(LUT/RGB composition, grid layout, or export) into its own module. "
        "The ratchet only moves down."
    )


def test_scatter_controller_line_budget():
    n = _line_count("review/features/scatter/controller.py")
    assert n <= SCATTER_CONTROLLER_BUDGET, (
        f"scatter/controller.py is {n} lines, over the {SCATTER_CONTROLLER_BUDGET} "
        "budget. Keep pure data collection in scatter/collect.py; the ratchet "
        "only moves down."
    )


def test_barplot_controller_line_budget():
    n = _line_count("review/features/barplot/controller.py")
    assert n <= BARPLOT_CONTROLLER_BUDGET, (
        f"barplot/controller.py is {n} lines, over the {BARPLOT_CONTROLLER_BUDGET} "
        "budget. Keep pure data collection in barplot/collect.py and rendering in "
        "barplot/renderer.py; the ratchet only moves down."
    )


def test_review_image_controller_line_budget():
    n = _line_count("review/features/image_browser/review_image/controller.py")
    assert n <= REVIEW_IMAGE_CONTROLLER_BUDGET, (
        f"review_image/controller.py is {n} lines, over the "
        f"{REVIEW_IMAGE_CONTROLLER_BUDGET} budget. Extract a cohesive cluster "
        "(e.g. a collect.py) rather than appending; the ratchet only moves down."
    )


def test_analyze_tab_line_budget():
    n = _line_count("analyze/tab.py")
    assert n <= ANALYZE_TAB_BUDGET, (
        f"analyze/tab.py is {n} lines, over the {ANALYZE_TAB_BUDGET} budget. "
        "Carve form-build / validation / run / log-poll into helpers rather than "
        "appending; the ratchet only moves down."
    )


def test_analyze_tab_form_line_budget():
    n = _line_count("analyze/form.py")
    assert n <= ANALYZE_TAB_FORM_BUDGET, (
        f"analyze/form.py is {n} lines, over the "
        f"{ANALYZE_TAB_FORM_BUDGET} budget. Each form section is already its own "
        "_build_* method; extract a cohesive group of them into a sibling module "
        "rather than appending. The ratchet only moves down."
    )
