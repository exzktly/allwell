"""Batch Export starts with an empty group editor.

Batch-export groups are export-specific, not the Sample-Definition replicate
sets. The editor must open blank and stay blank across tab shows; replicate
groups are pulled in only when the user explicitly clicks "Sync from app"
(or Quick Setup). Regression: the panel used to auto-seed from the replicate
sets in __init__ *and* re-seed on every tab show, wiping export-specific groups.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def test_panel_starts_with_empty_groups(loaded_app):
    from well_viewer.features.batch_export import BatchExportPanel

    panel = BatchExportPanel(loaded_app._ctx, None, use_sidebar_groups=False)
    assert panel._groups == []


def test_sync_from_app_is_opt_in_seed(loaded_app):
    from well_viewer.features.batch_export import BatchExportPanel

    panel = BatchExportPanel(loaded_app._ctx, None, use_sidebar_groups=False)
    assert panel._groups == []
    # The explicit "Sync from app" path still pulls in the replicate sets.
    expected = len(loaded_app._rep_sets_loaded())
    panel._sync_from_app()
    assert len(panel._groups) == expected
