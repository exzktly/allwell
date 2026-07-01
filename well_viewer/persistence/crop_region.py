"""Crop-region persistence — ``crop_region`` section of ``persistence.json``.

Stores the dataset-wide crop rectangle drawn on the Segmentation tab so the crop
survives between sessions. The region is a normalized ``(row0, col0, row1, col1)``
tuple in source-mask pixels (see :mod:`well_viewer.features.crop.region`), stored
as a JSON list; an absent / cleared crop removes the section entirely.

Lives in the consolidated ``persistence.json`` document (like ``ratios`` /
``qc_params`` / ``view_state``), not a standalone sidecar. New domain — no legacy
migration.
"""

from __future__ import annotations

import logging

from well_viewer.persistence import _doc

_logger = logging.getLogger("well_viewer")

_SECTION = "crop_region"


def save_to_data_dir(app) -> None:
    if not app._app_state.dataset.data_dir:
        return
    region = app._app_state.dataset.crop_region
    _doc.set_section(app, _SECTION, list(region) if region else None)


def load_from_data_dir(app) -> None:
    if not app._app_state.dataset.data_dir:
        return
    data = _doc.get_section(app, _SECTION)
    if isinstance(data, (list, tuple)) and len(data) == 4:
        try:
            app._app_state.dataset.crop_region = tuple(int(v) for v in data)
            return
        except (TypeError, ValueError):
            _logger.warning("Ignoring malformed crop_region section: %r", data)
    app._app_state.dataset.crop_region = None
