"""Line-budget ratchets for the two largest monoliths (P4).

Mirrors the ``test_guardrails.py`` pattern for ``runtime_app.py``: a ceiling
that only moves **down**. A change that regrows one of these files past its
budget fails CI, nudging contributors to extract cohesive modules instead of
appending. Lower a budget when a change legitimately shrinks the file; never
raise it.

Baselines (set when P4 began the decomposition):
- process_microscopy.py: ~4470 lines, after extracting the background-correction
  (``microscopy_background.py``), Cellpose (``microscopy_cellpose.py``), StarDist
  (``microscopy_stardist.py``) and output-writer (``microscopy_io.py``) layers —
  was 5129. (P2.7 added +2 for the aw_safe import + a logged swallow.)
- image_table/controller.py: 1494 lines, after extracting the image_compose /
  export / grid_layout layers — was 2486.

T4 extended the same pattern to the plot controllers by splitting the pure
data-collection half into a ``collect.py`` (mirroring image_table):
- scatter/controller.py: 875 lines, after ``scatter/collect.py`` — was 1264.
- barplot/controller.py: 731 lines, after ``barplot/collect.py`` — was 1416.
review_image/controller.py is budgeted at its current size to stop it regrowing
while its collect/render split lands incrementally.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Ratcheting ceilings (only ever lowered) ──────────────────────────────────
PROCESS_MICROSCOPY_BUDGET = 4470
IMAGE_TABLE_CONTROLLER_BUDGET = 1494
SCATTER_CONTROLLER_BUDGET = 875
BARPLOT_CONTROLLER_BUDGET = 731
REVIEW_IMAGE_CONTROLLER_BUDGET = 1392
ANALYZE_TAB_BUDGET = 1887


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
    n = _line_count("well_viewer/features/image_table/controller.py")
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
    n = _line_count("well_viewer/features/review_image/controller.py")
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
