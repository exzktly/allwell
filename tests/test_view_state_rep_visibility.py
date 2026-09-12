"""Saved-plot restore brings back rep-set visibility (group / replicate mode).

In replicate mode the plate and the plots are driven by which rep-sets are
*visible* (non-hidden), not by ``selected_wells``. ``view_state.snapshot`` must
capture that visibility and ``restore`` must re-apply it, so a plot saved while
using replicate sets restores the groups it was showing.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


def _repsets(app):
    app._app_state.selection.selections = [
        {"id": "1", "name": "G1", "wells": ["A01", "A02"], "hidden": False},
        {"id": "2", "name": "G2", "wells": ["B01", "B02"], "hidden": False},
    ]


@pytest.mark.slow
def test_snapshot_restore_roundtrips_hidden_repsets(loaded_app):
    app = loaded_app
    from review.persistence import view_state
    _repsets(app)
    app._notebook.setCurrentByName("Plotting")

    # Save with G2 hidden.
    app._app_state.selection.selections[1]["hidden"] = True
    snap = view_state.snapshot(app._ctx)
    assert snap["selection_visibility"]["hidden_ids"] == ["2"]

    # Later: un-hide G2, then restore the saved view → G2 hidden again.
    app._app_state.selection.selections[1]["hidden"] = False
    view_state.restore(app._ctx, dict(snap))
    assert app._app_state.selection.selections[1]["hidden"] is True
    assert sorted(r.name for r in app._rep_sets_active()) == ["G1"]


@pytest.mark.slow
def test_restore_tolerates_missing_visibility_block(loaded_app):
    """Older saved plots carry no selection_visibility → restore leaves the
    current hidden flags untouched (no crash)."""
    app = loaded_app
    from review.persistence import view_state
    _repsets(app)
    app._app_state.selection.selections[0]["hidden"] = True
    view_state.restore(app._ctx, {"selected_wells": []})  # no visibility block
    assert app._app_state.selection.selections[0]["hidden"] is True
