"""Regression: a restored active channel must win over the plot combo's default.

On dataset reopen, ``view_state.restore`` sets the active channel and then
``update_channel_selector`` runs — but the plot channel combo still shows its
just-repopulated default text. The selector used to prefer that current text
over the restored active label, so every reopen reverted to the first channel.
The fix flips the priority; this drives the real selector on a loaded app.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.qt

from well_viewer.core.channel_state_controller import (
    set_active_channel,
    update_channel_selector,
)


def test_restored_active_channel_beats_combo_default(loaded_app):
    app = loaded_app
    fluors = list(app._app_state.channel.fluor_channels or [])
    if len(fluors) < 2:
        pytest.skip("need ≥2 channels to tell active from default")

    var = getattr(app, "_plot_chan_var", None)
    if var is None:
        pytest.skip("plot channel combo not built")

    # Make a non-default channel active (what restore would have set).
    set_active_channel(app._ctx, fluors[1])
    active_lbl = app._active_channel_label()
    labels = [var.itemText(i) for i in range(var.count())]
    default_lbl = next((s for s in labels if s not in (active_lbl, "—")), None)
    if not default_lbl:
        pytest.skip("no distinct default label")

    # Simulate the load-time state: combo still shows the default text while the
    # active channel is the restored one (block signals so we don't re-drive
    # set_active_channel from the combo handler).
    blocked = var.blockSignals(True)
    try:
        var.setCurrentText(default_lbl)
    finally:
        var.blockSignals(blocked)
    assert var.currentText() == default_lbl

    update_channel_selector(app._ctx)

    # The restored active channel wins — not the stale default combo text.
    assert var.currentText() == active_lbl
