"""Axis-view export prefs (log scale, limits) are scoped per figure, not global.

Regression: editing log axes / axis limits in one plot's Properties sidebar
leaked onto every other tab because they shared the single app-wide
``_export_style_prefs`` dict.
"""
from __future__ import annotations

from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure  # noqa: E402

from well_viewer.core import plot_style as PS  # noqa: E402


class _Widgets:
    """Minimal ctx.widgets shim: resolves a registered name off the app attrs
    (the plot figs the export-style chain keys prefs by)."""
    def __init__(self, app):
        self._app = app

    def get_or_none(self, name):
        return getattr(self._app, name, None)


def _app():
    from well_viewer.core.export_prefs_state import ExportPrefsState
    app = SimpleNamespace(
        _line_fig=Figure(), _bar_fig=Figure(),
        _app_state=SimpleNamespace(export_prefs=ExportPrefsState()))
    # A8 teardown: the export-style chain (figure_prefs / _ensure_* /
    # figure_pref_key / apply_export_style_to_current) takes ctx.
    app._ctx = SimpleNamespace(state=app._app_state, widgets=_Widgets(app), app=app)
    return app


def test_axis_prefs_do_not_leak_between_figures():
    app = _app()
    line = PS.figure_prefs(app._ctx, app._line_fig)
    bar = PS.figure_prefs(app._ctx, app._bar_fig)

    line["y_log"] = True
    line["y_lim_min"] = "0.5"

    # Bar keeps the neutral defaults — no leak.
    assert bar["y_log"] is False
    assert bar["y_lim_min"] in ("", None)
    # Line keeps its own values.
    assert line["y_log"] is True
    assert line["y_lim_min"] == "0.5"


def test_global_style_keys_stay_shared():
    app = _app()
    line = PS.figure_prefs(app._ctx, app._line_fig)
    bar = PS.figure_prefs(app._ctx, app._bar_fig)
    # A non-axis (publication style) key is shared across figures.
    line["axis_label_size"] = 22
    assert bar["axis_label_size"] == 22


def test_axis_writes_route_to_per_figure_store_not_global():
    app = _app()
    PS.figure_prefs(app._ctx, app._line_fig)["x_log"] = True
    # Global style dict must NOT carry the axis edit…
    assert PS._ensure_export_style_prefs(app._ctx).get("x_log") in (False, None)
    # …it lives in the per-figure store under the stable key.
    assert app._app_state.export_prefs.axis_prefs["line"]["x_log"] is True


def test_apply_uses_per_figure_axis_state():
    app = _app()
    ax = app._line_fig.add_subplot(1, 1, 1)
    ax.plot([1, 2, 3], [1, 10, 100])
    PS.figure_prefs(app._ctx, app._line_fig)["y_log"] = True
    PS.apply_export_style_to_current(app._ctx, app._line_fig)
    assert app._line_fig.axes[0].get_yscale() == "log"
    # A different figure with no per-figure override stays linear.
    ax2 = app._bar_fig.add_subplot(1, 1, 1)
    ax2.plot([1, 2, 3], [1, 10, 100])
    PS.apply_export_style_to_current(app._ctx, app._bar_fig)
    assert app._bar_fig.axes[0].get_yscale() == "linear"
