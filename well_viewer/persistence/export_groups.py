"""Batch-export group persistence in ``persistence.json``.

The Batch Export tab's user-defined export groups (one set per chart-type panel:
line / bar / scatter-cells / scatter-aggregate) are stored under the consolidated
``persistence.json`` document, keyed by panel, so they survive across sessions
without a separate Save/Load file route.

The canonical in-memory copy is ``app._batch_export_export_groups_doc`` (reached
by the panels as ``ctx.runtime.batch_export.export_groups_doc``) — a
``{panel_key: [group, ...]}`` dict mirrored by the panels whenever their group
editor changes.
"""

from __future__ import annotations

import logging

from well_viewer.persistence import _doc

_logger = logging.getLogger("well_viewer")

SECTION = "export_groups"


def save_to_data_dir(ctx) -> None:
    """Persist the batch-export groups to the consolidated persistence document."""
    doc = ctx.runtime.batch_export.export_groups_doc or None
    _doc.set_section(ctx, SECTION, doc)


def load_from_data_dir(ctx) -> None:
    """Load persisted batch-export groups and refresh any open Batch Export panels."""
    raw = _doc.get_section(ctx, SECTION)
    ctx.runtime.batch_export.export_groups_doc = raw if isinstance(raw, dict) else {}
    state = ctx.runtime.batch_export.inline_state
    if not state:
        return
    for panel in (state.get("panels") or {}).values():
        reload_groups = getattr(panel, "_reload_persisted_groups", None)
        if callable(reload_groups):
            try:
                reload_groups()
            except Exception:
                _logger.exception("Batch Export group reload failed after load")
