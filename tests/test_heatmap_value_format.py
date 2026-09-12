"""Heat Map cell numbers honour the Properties panel's printf format, and the
colour bar keeps the scale its norm gave it.

Two independent behaviours, both pure enough to test without building the app:

* ``_format_cell_value`` picks a format per metric and magnitude, unless the user
  supplied one — an unusable format falls back rather than dropping the number.
* ``apply_export_style_prefs`` walks every axes in a figure, which includes a
  colour bar. Under Log scale that walk used to reset the bar to a linear scale,
  discarding the log locator and formatter matplotlib had installed, so the
  colours were log-mapped while the legend beside them was labelled linearly.
"""
from __future__ import annotations

import math

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

import numpy as np
from matplotlib.colors import LogNorm
from matplotlib.figure import Figure

from review.core.plot_style import (
    DEFAULT_EXPORT_STYLE_PREFS, apply_export_style_prefs,
)
from review.features.heatmap.controller import (
    METRIC_COUNT, METRIC_FRACTION, _format_cell_value,
)


# ── the automatic format (no user format) ────────────────────────────────────

def test_automatic_format_reads_the_metric_and_the_magnitude():
    assert _format_cell_value(12.4, METRIC_COUNT) == "12"
    assert _format_cell_value(0.5137, METRIC_FRACTION) == "0.51"
    assert _format_cell_value(1234.5, "Mean above threshold") == "1234"
    assert _format_cell_value(4.567, "Mean above threshold") == "4.57"
    assert _format_cell_value(0.004567, "Mean above threshold") == "0.00457"


def test_non_finite_values_draw_nothing():
    assert _format_cell_value(float("nan"), METRIC_COUNT) == ""
    assert _format_cell_value(math.inf, "Mean above threshold", "%.2f") == ""


# ── the user's printf format ─────────────────────────────────────────────────

def test_user_format_overrides_the_automatic_choice():
    assert _format_cell_value(4.5678, "Mean above threshold", "%.3f") == "4.568"
    assert _format_cell_value(1234.5, "Mean above threshold", "%.1e") == "1.2e+03"
    assert _format_cell_value(4.9, "Mean above threshold", "%d") == "4"
    # It applies to every metric, including the ones with their own default.
    assert _format_cell_value(0.5, METRIC_FRACTION, "%.4f") == "0.5000"
    assert _format_cell_value(12.0, METRIC_COUNT, "%.2f") == "12.00"


def test_blank_format_keeps_the_automatic_choice():
    assert _format_cell_value(4.567, "Mean above threshold", "") == "4.57"


def test_unusable_format_falls_back_instead_of_raising():
    # A bad conversion character, and a specifier the value can't satisfy: both
    # keep the number on screen rather than blanking the cell.
    assert _format_cell_value(4.567, "Mean above threshold", "%q") == "4.57"
    assert _format_cell_value(4.567, "Mean above threshold", "%(k)s") == "4.57"


def test_the_pref_ships_with_a_blank_default():
    # Blank = today's automatic behaviour, so an existing dataset looks unchanged
    # until the user types a format.
    assert DEFAULT_EXPORT_STYLE_PREFS["heatmap_value_format"] == ""


# ── the colour bar keeps its norm's scale ────────────────────────────────────

def _log_colorbar_figure():
    fig = Figure(figsize=(4, 3))
    ax = fig.add_subplot(1, 1, 1)
    data = np.array([[1.0, 10.0], [100.0, 1000.0]])
    im = ax.imshow(data, norm=LogNorm(vmin=1.0, vmax=1000.0))
    cbar = fig.colorbar(im, ax=ax)
    return fig, cbar.ax


def _prefs(**over):
    p = dict(DEFAULT_EXPORT_STYLE_PREFS)
    p.update(over)
    return p


def test_tagged_colorbar_keeps_its_log_scale():
    fig, cax = _log_colorbar_figure()
    setattr(cax, "_aw_colorbar", True)
    # axis_target "All" is the state the panel starts in, and the one in which
    # the style walk reaches every axes including the colour bar.
    apply_export_style_prefs(fig, _prefs(axis_target="All", y_log=False))
    assert cax.get_yscale() == "log"


def test_tagged_colorbar_gets_no_gridlines():
    fig, cax = _log_colorbar_figure()
    setattr(cax, "_aw_colorbar", True)
    apply_export_style_prefs(fig, _prefs(axis_target="All", grid_show=True))
    assert not any(line.get_visible() for line in cax.get_ygridlines())


def test_an_untagged_axes_is_still_restyled():
    # The opt-out must be narrow: a plain axes still follows the y_log pref.
    fig = Figure(figsize=(4, 3))
    ax = fig.add_subplot(1, 1, 1)
    ax.plot([1, 2, 3], [1, 10, 100])
    apply_export_style_prefs(fig, _prefs(axis_target="All", y_log=True))
    assert ax.get_yscale() == "log"


def test_an_untagged_colorbar_would_have_been_relinearised():
    # Pins the mechanism the tag exists to defeat, so a future refactor that
    # drops the tag fails loudly here instead of silently mislabelling the bar.
    fig, cax = _log_colorbar_figure()
    apply_export_style_prefs(fig, _prefs(axis_target="All", y_log=False))
    assert cax.get_yscale() == "linear"
