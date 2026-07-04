"""Batch-export group persistence in ``persistence.json``.

The Batch Export tab's user-defined export groups (one set per chart-type panel:
line / bar / scatter-cells / scatter-aggregate) are stored under the consolidated
``persistence.json`` document, keyed by panel, so they survive across sessions
without a separate Save/Load file route.

The canonical in-memory copy is ``app._export_groups_doc`` — a ``{panel_key:
[group, ...]}`` dict mirrored by the panels whenever their group editor changes.
"""

from __future__ import annotations

import logging

from well_viewer.persistence import _doc

_logger = logging.getLogger("well_viewer")

SECTION = "export_groups"


def save_to_data_dir(app) -> None:
    """Persist the batch-export groups to the consolidated persistence document."""
    doc = getattr(app, "_export_groups_doc", None) or None
    _doc.set_section(app._ctx, SECTION, doc)


def load_from_data_dir(app) -> None:
    """Load persisted batch-export groups and refresh any open Batch Export panels."""
    raw = _doc.get_section(app._ctx, SECTION)
    app._export_groups_doc = raw if isinstance(raw, dict) else {}
    state = getattr(app, "_batch_export_inline_state", None)
    if not state:
        return
    for panel in (state.get("panels") or {}).values():
        reload_groups = getattr(panel, "_reload_persisted_groups", None)
        if callable(reload_groups):
            try:
                reload_groups()
            except Exception:
                _logger.exception("Batch Export group reload failed after load")
