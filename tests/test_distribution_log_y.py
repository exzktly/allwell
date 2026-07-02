"""Distribution tab 'log y' toggle: persistence round-trip + renderer."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from well_viewer.features.distribution.state import DistributionState
from well_viewer.persistence import view_state as vs


def _fake_app_with_distribution(**overrides):
    """A SimpleNamespace app whose ``_app_state`` exposes a distribution slice
    via ``feature_slice`` — matches the typed-state contract (A8 migration)."""
    slice_ = DistributionState(**overrides)
    state = SimpleNamespace(distribution=slice_,
                            feature_slice=lambda name: slice_ if name == "distribution" else None)
    return SimpleNamespace(_app_state=state)


def test_distribution_log_y_snapshot_and_restore():
    app = _fake_app_with_distribution(bins=40, log_x=False, log_y=True)
    snap = vs._snapshot_distribution(app)
    assert snap["log_y"] is True

    restored = _fake_app_with_distribution()
    vs._apply_distribution(restored, snap)
    assert restored._app_state.distribution.log_y is True


def test_render_overlay_sets_log_y_scale():
    from matplotlib.figure import Figure

    from well_viewer.features.distribution import controller as C

    app = SimpleNamespace(
        _app_state=SimpleNamespace(
            channel=SimpleNamespace(
                active_metric="mean_intensity",
                active_channel="gfp",
                active_val_col="gfp_mean_intensity",
                is_shape_active=lambda: False,
            ),
        ),
        _get_thresh_frac_on=lambda _ch: float("nan"),
    )
    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    groups = [("A", "#3366cc", np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]))]

    C._render_overlay(app, ax, groups, "Histogram + KDE", 8, False, float("nan"),
                      log_y=True)
    assert ax.get_yscale() == "log"
    assert ax.get_xscale() == "linear"
