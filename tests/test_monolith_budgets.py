"""Line-budget ratchets for the two largest monoliths (P4).

Mirrors the ``test_guardrails.py`` pattern for ``runtime_app.py``: a ceiling
that only moves **down**. A change that regrows one of these files past its
budget fails CI, nudging contributors to extract cohesive modules instead of
appending. Lower a budget when a change legitimately shrinks the file; never
raise it.

Baselines (set when P4 began the decomposition):
- process_microscopy.py: 4468 lines, after extracting the background-correction
  cluster (``microscopy_background.py``), the Cellpose segmenters
  (``microscopy_cellpose.py``), and the StarDist segmenters + model state
  (``microscopy_stardist.py``) and the output writers
  (``microscopy_io.py``) — was 5129.
- image_table/controller.py: 2486 lines (extraction pending — the budget caps
  it where it is so it can't grow before the split lands).
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Ratcheting ceilings (only ever lowered) ──────────────────────────────────
PROCESS_MICROSCOPY_BUDGET = 4468
IMAGE_TABLE_CONTROLLER_BUDGET = 2486


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
