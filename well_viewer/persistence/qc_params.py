"""QC-layer threshold persistence — ``qc_params`` section of ``persistence.json``.

Stores the live debris-gating thresholds edited in the QC sidebar view so the
curation survives between sessions. When no persisted edits exist, this loader
clears ``app._app_state.qc.params`` and the QC controller's accessor lazily seeds it from
the dataset's ``pipeline_info.json`` (the thresholds used at run time) — so a
freshly-processed dataset opens with the run's QC settings already in place,
without this data-only module importing feature code.

Lives in the consolidated ``persistence.json`` document (like ``ratios`` /
``heatmap_layouts`` / ``line_order`` / ``view_state``), not a standalone sidecar.
A legacy standalone ``qc_params.json`` is absorbed into the section on first load
and then deleted.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from well_viewer.domain.qc_params import QCParams
from well_viewer.persistence import _doc

_logger = logging.getLogger("well_viewer")

_SECTION = "qc_params"
_LEGACY_FILENAME = "qc_params.json"


def save_to_data_dir(app) -> None:
    if not app._app_state.dataset.data_dir:
        return
    params = app._app_state.qc.params
    if params is None:
        return
    _doc.set_section(app._ctx, _SECTION, params.to_dict())


def load_from_data_dir(app) -> None:
    if not app._app_state.dataset.data_dir:
        return
    data = _doc.get_section(app._ctx, _SECTION)
    if data is None:
        data = _absorb_legacy_sidecar(app)
    if data is None:
        # No persisted edits — clear any stale params from a previously open
        # dataset. The QC controller's accessor (``get_qc_params``) lazily
        # re-seeds from this dataset's ``pipeline_info`` thresholds on first
        # read (the very next step in the load sequence is ``apply_qc_to_cache``,
        # which reads through that accessor). Keeping the seed there — rather
        # than calling a *feature* function from this persistence module —
        # is what lets ``persistence`` stay data-only (see test_layering).
        app._app_state.qc.params = None
        return
    app._app_state.qc.params = QCParams.from_dict(data)


def _legacy_path(app) -> Optional[Path]:
    d = app._app_state.dataset.data_dir
    return Path(d) / _LEGACY_FILENAME if d else None


def _absorb_legacy_sidecar(app) -> Optional[dict]:
    """Migrate a legacy standalone ``qc_params.json`` into the persistence doc.

    The old sidecar wrapped the params as ``{"schema_version": …, "qc_params":
    {…}}``, so (unlike the raw-format sidecars handled by ``_doc``'s generic
    migrator) it must be unwrapped. Write the inner dict into the ``qc_params``
    section, then delete the legacy file — but only after the section is written,
    so a crash mid-migration leaves it for a retry. Returns the inner dict, or
    None when the legacy file is absent / unreadable.
    """
    legacy = _legacy_path(app)
    if legacy is None or not legacy.exists():
        return None
    try:
        inner = (json.loads(legacy.read_text()) or {}).get("qc_params")
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Legacy %s unreadable during migration: %s", legacy, exc)
        return None
    if not isinstance(inner, dict):
        return None
    _doc.set_section(app._ctx, _SECTION, inner)      # absorb into persistence.json
    try:
        legacy.unlink()
    except OSError as exc:
        _logger.warning("Could not remove legacy %s: %s", legacy, exc)
    else:
        _logger.info("Migrated legacy %s into persistence.json", legacy.name)
    return inner
