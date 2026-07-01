"""Status-bar context chips (F1): threshold + error-band, clickable.

The footer used to append a static ``threshold=… | band=…`` to the line-plot
status text. That readout is now two always-visible chips fed from state:
clicking the threshold chip jumps to Cell Gating; clicking the band chip
toggles SEM/SD. The chips stay in sync with state changes.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def test_chips_reflect_state(loaded_app):
    app = loaded_app
    app._refresh_status_chips()
    assert not app._status_threshold_chip.isHidden()
    assert "Threshold" in app._status_threshold_chip.text()
    # Band chip mirrors use_sem.
    expected = "SEM" if app._app_state.plot.use_sem else "SD"
    assert app._status_band_chip.text() == f"Band {expected}"


def test_band_chip_toggles_sem(loaded_app):
    app = loaded_app
    app._refresh_status_chips()
    before = app._app_state.plot.use_sem
    # Clicking the band chip flips SEM/SD and the chip text follows.
    app._status_band_chip.click()
    assert app._app_state.plot.use_sem is (not before)
    expected = "SEM" if app._app_state.plot.use_sem else "SD"
    assert app._status_band_chip.text() == f"Band {expected}"


def test_threshold_chip_follows_threshold_edit(loaded_app):
    app = loaded_app
    app._set_thresh_frac_on(77.0, app._app_state.channel.active_channel)
    # refresh_thresh_frac_quick_edit (fired by the edit) refreshes the chip.
    assert "77" in app._status_threshold_chip.text()
