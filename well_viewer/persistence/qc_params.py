"""QC-layer threshold persistence (``<data_dir>/qc_params.json``).

Stores the live debris-gating thresholds edited in the QC sidebar view so the
curation survives between sessions. On load, falls back to seeding from the
dataset's ``pipeline_info.json`` (the thresholds used at run time) when no
``qc_params.json`` exists yet — so a freshly-processed dataset opens with the
run's QC settings already in place.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from well_viewer.features.qc.controller import seed_qc_from_pipeline_info
from well_viewer.features.qc.params import QCParams
from well_viewer.persistence._io import atomic_write_json

_logger = logging.getLogger("well_viewer")

_SCHEMA_VERSION = 1


def path_for(app) -> Optional[Path]:
    if app._app_state.dataset.data_dir:
        return app._app_state.dataset.data_dir / "qc_params.json"
    return None


def save_to_data_dir(app) -> None:
    path = path_for(app)
    if path is None:
        return
    params = getattr(app, "_qc_params", None)
    if params is None:
        return
    payload = {"schema_version": _SCHEMA_VERSION, "qc_params": params.to_dict()}
    try:
        atomic_write_json(path, payload)
    except Exception:
        _logger.exception("Could not write qc_params.json")


def load_from_data_dir(app) -> None:
    path = path_for(app)
    if path is None or not path.exists():
        # No persisted edits — seed from the run's pipeline_info thresholds.
        seed_qc_from_pipeline_info(app)
        return
    try:
        data = json.loads(path.read_text())
        app._qc_params = QCParams.from_dict(data.get("qc_params"))
    except Exception:
        _logger.warning("qc_params.json unreadable; seeding from pipeline_info")
        seed_qc_from_pipeline_info(app)
