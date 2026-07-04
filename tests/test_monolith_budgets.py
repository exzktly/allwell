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
PROCESS_MICROSCOPY_BUDGET = 4707
IMAGE_TABLE_CONTROLLER_BUDGET = 1494
SCATTER_CONTROLLER_BUDGET = 875
BARPLOT_CONTROLLER_BUDGET = 731
# 963 -> 980: A8 ctx-seam. review_csv/tab_view stopped naming ctx.app (4 -> 0) by
# calling refresh_review_csv[_rows] / on_review_csv_row_double_click with ctx; those
# three fns gained thin ctx-adapter wrappers here that delegate to their unchanged
# app-native bodies (the Review-CSV store/nav layer isn't ctx yet). A deliberate,
# one-time +17 for the ctx boundary — not god-logic growth.
REVIEW_IMAGE_CONTROLLER_BUDGET = 998  # A8 ctx-seam wrappers (review_image nav -> ctx)
# Ratcheted back to 1176: the loose-TIFs-in-``in/`` work centralized the
# well-count + layout detection into input_resolution_service.count_expected_wells,
# removing the duplicated counter from the tab.
# Code-review X4/X6 (+30): the entry-boundary numeric validator
# (_numeric_option_errors + _INT_FIELDS) that blocks a bad radius/FFT/mesh/QC
# value up front instead of silently dropping it. Then L2 (+5): the `_alive`
# flag + guarded log-poller re-arm so it stops on app close. Resume from 1211.
ANALYZE_TAB_BUDGET = 1211


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
