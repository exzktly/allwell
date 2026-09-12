"""Regression: the FOV-replicates toggle shim threads ``self._ctx``.

``toggle_fov_replicates`` moved to ``core.plot_toggles`` and is ctx-native
(A8 enabler E3), but the ``ReviewApp._toggle_fov_replicates`` shim kept
passing the raw app (``toggle_fov_replicates(self)``) — the sibling
``_toggle_sem`` shim already passed ``self._ctx``. With the app handle,
``toggle_fov_replicates`` hit ``app.state`` (AttributeError) before flipping
anything, so the toggle silently did nothing.
"""
from __future__ import annotations


def test_toggle_fov_replicates_flips_state(loaded_app):
    app = loaded_app
    before = bool(app._app_state.plot.use_fov_replicates)
    app._toggle_fov_replicates()  # must thread self._ctx, not the raw app
    assert bool(app._app_state.plot.use_fov_replicates) != before
    app._toggle_fov_replicates()
    assert bool(app._app_state.plot.use_fov_replicates) == before
