"""The Properties style pass styles the data and leaves alone what a renderer drew to mean something else.

``apply_export_style_prefs`` runs at the end of every redraw with the figure's preferences, whether or not the panel
is open, so anything it overwrites is overwritten by default. Each test here is one thing it used to overwrite, with
the default preferences or the one the user set, measured on the artist after the pass:

* a reference line (``axhline`` / ``axvline``) kept its dash and took no marker;
* a curve drawn without markers (a KDE, a CDF step, a fit) took none, while a series drawn with them still follows
  Marker style — including back from "None" without a redraw;
* the bar plot's "not computed" cap stayed a cap, not a filled data dot;
* a hollow marker whose hollowness is the message stayed hollow;
* a renderer's own tick angle survived the default angle of 0, a typed angle still applies, and labels above the
  axes slant away from the plot;
* a grid the renderer turned off stayed off, and a position axis refused a log scale;
* an axes cleared and reused for the next draw forgot the last draw's claims.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import numpy as np  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from review.core.plot_style import apply_export_style_prefs  # noqa: E402

#: What a user who has restyled the marks asks for: solid lines, circle markers, the grid on, both axes log.
_PREFS = {"line_style": "-", "marker_style": "o", "grid_show": True, "x_log": True, "y_log": True,
          "x_tick_angle": 0}


def _ax():
    fig = Figure(figsize=(4, 3))
    return fig, fig.add_subplot(1, 1, 1)


def test_a_reference_line_keeps_its_own_style():
    fig, ax = _ax()
    ax.plot([1, 2, 3], [1, 2, 3], marker="s")
    threshold = ax.axvline(2.0, ls="--", lw=1.0)
    level = ax.axhline(1.5, ls=":", lw=0.8)
    apply_export_style_prefs(fig, dict(_PREFS, x_log=False, y_log=False, line_width=3.0))
    assert threshold.get_linestyle() == "--" and threshold.get_linewidth() == 1.0
    assert level.get_linestyle() == ":" and level.get_marker() in ("None", None)


def test_a_curve_drawn_without_markers_takes_none_and_a_series_still_does():
    fig, ax = _ax()
    (kde,) = ax.plot(np.linspace(1, 2, 200), np.linspace(1, 2, 200), lw=1.6)
    (series,) = ax.plot([1, 2, 3], [1, 2, 3], marker="s")
    apply_export_style_prefs(fig, dict(_PREFS, x_log=False, y_log=False))
    assert kde.get_marker() in ("None", None)
    assert series.get_marker() == "o"


def test_a_series_s_markers_come_back_after_none_without_a_redraw():
    """The record is made the first time the pass meets the line, so the pass's own "None" cannot be mistaken for a
    curve the renderer drew without markers."""
    fig, ax = _ax()
    (series,) = ax.plot([1, 2, 3], [1, 2, 3], marker="s")
    apply_export_style_prefs(fig, dict(_PREFS, x_log=False, y_log=False, marker_style="none"))
    assert series.get_marker() == "None"
    apply_export_style_prefs(fig, dict(_PREFS, x_log=False, y_log=False, marker_style="D"))
    assert series.get_marker() == "D"


def test_the_bar_plot_s_not_computed_cap_stays_a_cap():
    from review.features.barplot._annotations import mark_undefined_spread

    fig, ax = _ax()
    mark_undefined_spread(ax, 1.0, 5.0, "red")
    (cap,) = ax.lines
    apply_export_style_prefs(fig, dict(_PREFS, x_log=False, y_log=False))
    assert cap.get_marker() == "_"
    assert cap.get_markersize() == 9


def test_a_hollow_marker_that_means_something_stays_hollow():
    """The tag the Response renderer puts on its off-scale points (``response/renderer.py``)."""
    fig, ax = _ax()
    off = ax.errorbar([1.0], [2.0], yerr=[0.1], fmt="o", markerfacecolor="none")
    off[0]._aw_marker_face_is_data = True
    (filled,) = ax.plot([2.0], [3.0], marker="o", markerfacecolor="none")
    apply_export_style_prefs(fig, dict(_PREFS, x_log=False, y_log=False))
    assert off[0].get_markerfacecolor() == "none"
    assert filled.get_markerfacecolor() != "none"


def _profile_axes():
    """The Multivariate cluster profile, drawn by its real renderer."""
    from review.features.multivariate.renderer import _draw_cluster_profile

    fig, ax = _ax()
    _draw_cluster_profile(ax, {"matrix": [[0.5, -0.2, 1.0], [0.1, 0.3, -0.9]], "cluster_ids": [0, 1],
                               "feature_labels": ["Area", "Intensity", "Elongation"]})
    return fig, ax


def test_a_matrix_keeps_its_grid_off_and_its_indices_linear():
    fig, ax = _profile_axes()
    apply_export_style_prefs(fig, _PREFS)
    assert not any(line.get_visible() for line in ax.get_xgridlines() + ax.get_ygridlines())
    assert ax.get_xscale() == "linear" and ax.get_yscale() == "linear"


def test_a_renderer_s_own_tick_angle_survives_the_default_and_yields_to_a_typed_one():
    from review.core.plot_style import own_xtick_angle

    fig, ax = _ax()
    ax.set_xticks([0, 1, 2])
    ax.set_xticklabels(["a", "b", "c"], rotation=45, ha="right")
    own_xtick_angle(ax, 45, "right")
    apply_export_style_prefs(fig, dict(_PREFS, x_log=False, y_log=False, x_tick_angle=0))
    assert {t.get_rotation() for t in ax.get_xticklabels()} == {45.0}
    apply_export_style_prefs(fig, dict(_PREFS, x_log=False, y_log=False, x_tick_angle=20))
    assert {t.get_rotation() for t in ax.get_xticklabels()} == {20.0}


def test_labels_above_the_axes_slant_away_from_the_plot():
    fig, ax = _ax()
    ax.xaxis.tick_top()
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["a long column", "another"])
    apply_export_style_prefs(fig, dict(_PREFS, x_log=False, y_log=False, x_tick_angle=30))
    assert {t.get_horizontalalignment() for t in ax.get_xticklabels()} == {"left"}


def test_a_reused_axes_forgets_the_last_draw_s_claims():
    from review.core.plot_style import (
        keep_grid_off, mark_position_axes, own_xtick_angle, reconfigure_stacked_axes,
    )

    fig, ax = _ax()
    keep_grid_off(ax)
    mark_position_axes(ax)
    own_xtick_angle(ax, 30)
    (again,) = reconfigure_stacked_axes(fig, 1, lambda f: [f.add_subplot(1, 1, 1)])
    assert again is ax
    for name in ("_aw_no_grid", "_categorical_xaxis", "_categorical_yaxis", "_aw_own_xtick_angle"):
        assert not hasattr(again, name), name
