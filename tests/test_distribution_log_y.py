"""Distribution tab 'log y' toggle: persistence round-trip + renderer."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from well_viewer.features.distribution.state import DistributionState
from well_viewer.persistence import view_state as vs


def _fake_app_with_distribution(**overrides):
    """A ctx-shaped fake that doubles as its own ctx: ``state`` (typed slice +
    ``feature_slice``) and ``widgets`` (+ a ``_ctx`` self-ref) back both the
    ctx-native ``_snapshot_distribution`` and ``_apply_distribution``. The
    distribution combos aren't built, so ``widgets.get_or_none`` returns ``None``
    (both helpers skip them)."""
    slice_ = DistributionState(**overrides)
    state = SimpleNamespace(distribution=slice_,
                            feature_slice=lambda name: slice_ if name == "distribution" else None)
    app = SimpleNamespace(_app_state=state, state=state,
                          widgets=SimpleNamespace(get_or_none=lambda _n: None))
    app._ctx = app
    return app


def test_distribution_log_y_snapshot_and_restore():
    app = _fake_app_with_distribution(bins=40, log_x=False, log_y=True)
    snap = vs._snapshot_distribution(app._ctx)
    assert snap["log_y"] is True

    restored = _fake_app_with_distribution()
    vs._apply_distribution(restored._ctx, snap)
    assert restored.state.distribution.log_y is True


def test_render_overlay_sets_log_y_scale():
    from matplotlib.figure import Figure

    from well_viewer.features.distribution import controller as C

    # A8 R3: the renderer takes ctx — a minimal ctx over the state it reads.
    ctx = SimpleNamespace(
        state=SimpleNamespace(
            channel=SimpleNamespace(
                active_metric="mean_intensity",
                active_channel="gfp",
                active_val_col="gfp_mean_intensity",
                is_shape_active=lambda: False,
                ratio_metrics=[],
            ),
            plot=SimpleNamespace(show_threshold_line=False),
        ),
    )
    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    groups = [("A", "#3366cc", np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]))]

    C._render_overlay(ctx, ax, groups, "Histogram + KDE", 8, False, float("nan"),
                      log_y=True)
    assert ax.get_yscale() == "log"
    assert ax.get_xscale() == "linear"
