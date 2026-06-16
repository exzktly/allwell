"""Save / load Cell Gating parameters inside ``pipeline_info.json``.

The pipeline writes pipeline_info.json as part of every analysis run. This
module overlays user-curated cell gating thresholds onto that file without
disturbing the pipeline-owned keys: it reads the existing JSON, sets /
replaces the ``cell_gating`` block (or removes it when every value is back
at its default), and writes the file back.

Schema written under the ``cell_gating`` key:

    {
        "fluor_gates":      {"gfp": 50.0, "mcherry": 0.0},
        "thresh_frac_on":   {"gfp": 60.0, "mcherry": 50.0},
        "use_scatter_gates": true,
        "scatter_gates":    [{"x0": .., "y0": .., "x1": .., "y1": ..,
                              "x_col": "gfp_mean_intensity",
                              "y_col": "mcherry_mean_intensity"}],
    }

Only non-default values are persisted; channels at default are omitted. The
``scatter_gates`` / ``use_scatter_gates`` pair is written only when the
scatter-gate inclusion mode is on and at least one gate exists.
When every parameter is at default, the block is removed entirely so the
sidecar stays tidy.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, Optional

from well_viewer.persistence._io import atomic_write_json


_logger = logging.getLogger("well_viewer.domain.gating_state")

PIPELINE_INFO_FILENAME = "pipeline_info.json"
CELL_GATING_KEY = "cell_gating"


def pipeline_info_available(out_dir: Path) -> bool:
    """``True`` when *out_dir* has a writable pipeline_info.json sidecar."""
    return _resolve_pipeline_info_path(out_dir).exists()

DEFAULT_FLUOR_GATE = 0.0
DEFAULT_THRESH_FRAC_ON = 50.0

_EPS = 1e-9


def _resolve_pipeline_info_path(out_dir: Path) -> Path:
    """Return the pipeline_info.json path, falling back to parent dir."""
    primary = out_dir / PIPELINE_INFO_FILENAME
    if primary.exists():
        return primary
    parent = out_dir.parent / PIPELINE_INFO_FILENAME
    if parent.exists():
        return parent
    return primary  # may not exist; caller decides


def _is_default(value: float, default: float) -> bool:
    try:
        return math.isclose(float(value), float(default), abs_tol=_EPS)
    except (TypeError, ValueError):
        return True


def build_gating_block(
    fluor_gates: Dict[str, float],
    thresh_frac_on: Dict[str, float],
    scatter_gates: Optional[list] = None,
    use_scatter_gates: bool = False,
    scatter_gate_choices: Optional[list] = None,
    use_scatter_thresh: bool = False,
) -> Dict[str, Any]:
    """Snapshot non-default gating values as a JSON-friendly dict.

    Returns an empty dict when every value is at its default; callers can
    use that to decide whether to remove the block from the sidecar.

    ``scatter_gates`` (the rectangular gates drawn on the Scatter Plot, each a
    ``{x0,y0,x1,y1,x_col,y_col}`` dict) and ``use_scatter_gates`` are persisted
    only when the inclusion mode is on *and* gates exist — unlike the scalar
    thresholds there is no "default" value to diff against, so the on-mode flag
    is the gate that keeps default state out of the sidecar.
    """
    block: Dict[str, Any] = {}
    fg_out: Dict[str, float] = {}
    for ch, val in (fluor_gates or {}).items():
        if not _is_default(val, DEFAULT_FLUOR_GATE):
            fg_out[str(ch)] = float(val)
    if fg_out:
        block["fluor_gates"] = fg_out

    tfo_out: Dict[str, float] = {}
    for ch, val in (thresh_frac_on or {}).items():
        if not _is_default(val, DEFAULT_THRESH_FRAC_ON):
            tfo_out[str(ch)] = float(val)
    if tfo_out:
        block["thresh_frac_on"] = tfo_out

    # Persist the gates when EITHER scatter-gate layer (inclusion / fraction-on
    # thresholding) is on — both consume the same selected gate(s).
    if (use_scatter_gates or use_scatter_thresh) and scatter_gates:
        block["scatter_gates"] = [dict(g) for g in scatter_gates]
        # The selected filtering gates (by name); omitted ⇒ all gates (union).
        # An explicit (possibly empty) list selects exactly those names.
        if scatter_gate_choices is not None:
            block["scatter_gate_choices"] = [str(n) for n in scatter_gate_choices]
        if use_scatter_gates:
            block["use_scatter_gates"] = True
        if use_scatter_thresh:
            block["use_scatter_thresh"] = True

    return block


def save_gating_to_pipeline_info(
    out_dir: Path,
    gating_block: Dict[str, Any],
) -> Optional[Path]:
    """Merge ``gating_block`` into the pipeline_info.json sidecar.

    When ``gating_block`` is empty, removes any existing ``cell_gating``
    key so default-valued state is not persisted.

    Returns the written path, or ``None`` when there is no pipeline_info.json
    to update or the write failed. Callers wanting to distinguish the two
    no-write cases can call :func:`pipeline_info_available` first.
    """
    info_path = _resolve_pipeline_info_path(out_dir)
    if not info_path.exists():
        _logger.warning(
            "pipeline_info.json not found at %s — gating thresholds will not "
            "be persisted across sessions for this dataset.", info_path,
        )
        return None
    try:
        existing = json.loads(info_path.read_text())
        if not isinstance(existing, dict):
            _logger.warning("%s is not a JSON object; not updating.", info_path)
            return None
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Could not read %s: %s", info_path, exc)
        return None

    if gating_block:
        existing[CELL_GATING_KEY] = gating_block
    else:
        existing.pop(CELL_GATING_KEY, None)

    try:
        atomic_write_json(info_path, existing)
    except OSError as exc:
        _logger.warning("Could not write %s: %s", info_path, exc)
        return None
    return info_path


def read_gating_params(out_dir: Path) -> Optional[Dict[str, Any]]:
    """Return the saved cell_gating block, or None when absent."""
    info_path = _resolve_pipeline_info_path(out_dir)
    if not info_path.exists():
        return None
    try:
        data = json.loads(info_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Could not read %s: %s", info_path, exc)
        return None
    if not isinstance(data, dict):
        return None
    block = data.get(CELL_GATING_KEY)
    if not isinstance(block, dict):
        return None
    return block
