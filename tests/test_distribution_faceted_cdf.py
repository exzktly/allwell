"""Distribution 'Faceted' layout honours CDF mode (one panel per group).

Regression: the ``is_faceted`` gate only admitted the histogram modes, so
selecting *Faceted* while in *CDF* mode silently kept the single overlaid axis.
CDF steps pile up illegibly once several groups share an axis, so CDF now
facets like the histogram modes.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np


class _Widgets:
    """Minimal WidgetRegistry stand-in backed by a dict."""

    def __init__(self, fig):
        self._m = {"_distribution_fig": fig, "_distribution_card": None}

    def get(self, name):
        return self._m.get(name)

    def get_or_none(self, name):
        return self._m.get(name)

    def register(self, name, widget):
        self._m[name] = widget
        return widget


def _faceted_ctx(fig):
    return SimpleNamespace(
        widgets=_Widgets(fig),
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


def _three_groups():
    rng = np.arange(1.0, 11.0)
    return [
        ("A", "#3366cc", rng),
        ("B", "#cc3366", rng + 2.0),
        ("C", "#33cc66", rng + 4.0),
    ]


def test_faceted_cdf_draws_one_step_panel_per_group():
    from matplotlib.figure import Figure

    from well_viewer.features.distribution import controller as C

    fig = Figure()
    fig.add_subplot(1, 1, 1)  # start from the single overlay axis the tab builds
    groups = _three_groups()

    C._render_faceted(_faceted_ctx(fig), groups, "CDF", 40, False, float("nan"))

    # One axis per group, each carrying exactly one monotone step curve.
    assert len(fig.axes) == len(groups)
    for ax in fig.axes:
        assert len(ax.lines) == 1
        ys = ax.lines[0].get_ydata()
        assert np.all(np.diff(ys) >= 0)          # cumulative → non-decreasing
        assert ax.get_ylim() == (-0.02, 1.05)    # shared 0..1 fraction window
    # Shared cumulative-fraction label on the middle panel.
    assert fig.axes[len(fig.axes) // 2].get_ylabel() == "Cumulative fraction"


def test_faceted_cdf_log_x_scales_every_panel():
    from matplotlib.figure import Figure

    from well_viewer.features.distribution import controller as C

    fig = Figure()
    fig.add_subplot(1, 1, 1)
    groups = _three_groups()

    C._render_faceted(_faceted_ctx(fig), groups, "CDF", 40, True, float("nan"))

    assert len(fig.axes) == len(groups)
    for ax in fig.axes:
        assert ax.get_xscale() == "log"
