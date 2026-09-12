"""Distribution tab 'log y' toggle: persistence round-trip + renderer."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from review.features.distribution.state import DistributionState
from review.persistence import view_state as vs


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

    from review.features.distribution import controller as C

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


def _render_ctx():
    return SimpleNamespace(
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


def test_render_overlay_histogram_passes_log_y_to_hist():
    """The log-Y bars must be drawn via ``hist(log=True)`` (correct baseline),
    not by calling ``set_yscale('log')`` on 0-baseline density bars."""
    from matplotlib.figure import Figure

    from review.features.distribution import controller as C

    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    groups = [("A", "#3366cc", np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]))]

    captured = {}
    real_hist = ax.hist

    def _spy_hist(*a, **kw):
        captured["log"] = kw.get("log")
        return real_hist(*a, **kw)

    ax.hist = _spy_hist  # type: ignore[assignment]
    C._render_overlay(_render_ctx(), ax, groups, "Histogram", 8, False,
                      float("nan"), log_y=True)
    assert captured["log"] is True
    assert ax.get_yscale() == "log"


def _log_pref_ctx():
    """A ctx wired for the log-scale single-source-of-truth helpers: a
    distribution figure resolvable through ``widgets`` and an export-prefs
    slice backing ``figure_prefs``."""
    from matplotlib.figure import Figure

    fig = Figure()
    export_prefs = SimpleNamespace(style_prefs=None, axis_prefs={})
    state = SimpleNamespace(
        distribution=DistributionState(),
        export_prefs=export_prefs,
    )
    widgets = SimpleNamespace(
        get_or_none=lambda n: fig if n == "_distribution_fig" else None,
    )
    return SimpleNamespace(state=state, widgets=widgets), fig


def test_log_flag_single_source_of_truth():
    """On-plot checkbox and sidebar toggle share the per-figure ``y_log`` pref,
    so the renderer and the export-style pass can never disagree."""
    from review.core.plot_style import figure_prefs
    from review.features.distribution import controller as C

    ctx, fig = _log_pref_ctx()

    # On-plot checkbox path writes the figure pref (+ mirrors state).
    C.set_distribution_axis_log(ctx, "y", True)
    assert figure_prefs(ctx, fig)["y_log"] is True
    assert ctx.state.distribution.log_y is True
    assert C.get_distribution_axis_log(ctx, "y") is True

    # Sidebar path writes the same pref directly; the reader honours it.
    figure_prefs(ctx, fig)["y_log"] = False
    assert C.get_distribution_axis_log(ctx, "y") is False
