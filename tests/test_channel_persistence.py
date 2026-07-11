"""The active channel must survive a dataset reopen (view_state persistence).

Regression: ``view_state.restore`` validated the saved ``active_channel`` against
``app._app_state.channel.fluor_channels`` — but at ``view_state.load`` time that
list is still EMPTY (it's populated only later, by ``recalculate_threshold``). The
``ch in fluor_channels`` guard therefore dropped every restored channel and each
reopen reverted to the default first fluor channel. The fix sets the field
directly and lets ``update_channel_selector`` (run right after) validate it —
keeping valid channels, falling back only when the saved one is truly stale.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from well_viewer.core.channel_state_controller import set_active_channel
from well_viewer.persistence import view_state
from well_viewer.runtime_app import WellViewerApp

pytestmark = pytest.mark.qt


def _reopen(mini_dataset, qtbot):
    app = WellViewerApp()
    qtbot.addWidget(app)
    app._load_path(mini_dataset)
    return app


def test_active_channel_survives_reopen(loaded_app, mini_dataset, qtbot):
    app = loaded_app
    fluor = list(app._app_state.channel.fluor_channels)
    assert len(fluor) >= 2, f"need >=2 channels to test a non-default pick: {fluor}"
    target = fluor[1]  # a non-default channel (default is fluor[0])
    set_active_channel(app._ctx, target)
    view_state.save_to_data_dir(app._ctx)

    reopened = _reopen(mini_dataset, qtbot)
    assert reopened._app_state.channel.active_channel == target


def test_stale_saved_channel_falls_back(loaded_app, mini_dataset, qtbot):
    """A saved channel that no longer exists must fall back to a valid one, not
    stick to the stale value or crash."""
    app = loaded_app
    set_active_channel(app._ctx, app._app_state.channel.fluor_channels[1])
    view_state.save_to_data_dir(app._ctx)

    # Corrupt the persisted channel to one absent from the dataset.
    pj = Path(mini_dataset) / "persistence.json"
    doc = json.loads(pj.read_text())
    doc["view_state"]["active_channel"] = "no_such_channel"
    pj.write_text(json.dumps(doc))

    reopened = _reopen(mini_dataset, qtbot)
    got = reopened._app_state.channel.active_channel
    assert got in reopened._app_state.channel.fluor_channels
    assert got != "no_such_channel"
