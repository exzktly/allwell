"""The Spatial tab's summary panel axes.

Two renderers share one x-axis meaning and reach it differently: the heat map's
x is a *category index* with the timepoint written into the tick label, while the
line variant plots the timepoint values themselves. Both must survive the shared
restyle pass and both must carry the Properties sidebar's time-rescale factor.

Figure-level, no app fixture: the renderers take a figure and plain data.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from well_viewer.core.plot_style import apply_export_style_prefs  # noqa: E402
from well_viewer.features.spatial import renderer as r  # noqa: E402

WELLS = ["A01", "B02"]
TPS = [0.0, 1.0, 2.0]
MATRIX = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])


@pytest.fixture
def fig():
    f = plt.figure()
    yield f
    plt.close(f)


def _restyle(f):
    """What runs after every spatial redraw (``render_spatial`` ends with it)."""
    apply_export_style_prefs(f, {"x_log": False, "y_log": False})


def _xticks(ax) -> list[str]:
    ax.figure.canvas.draw()
    return [t.get_text() for t in ax.get_xticklabels() if t.get_text()]


# ── the heat map: category positions, timepoints written into the labels ────

def test_heatmap_tick_labels_survive_the_restyle(fig):
    """Regression: they used to be wiped.

    The x positions are 0, 1, 2 … category indices, so the timepoint only exists
    in the tick text. ``apply_export_style_prefs`` calls ``set_xscale``, which
    resets the locator and formatter — and the grid came back labelled
    ``-0.5, 0.0, 0.5 …``, half-indices that name neither a timepoint nor a well.
    The ``_categorical_xaxis`` / ``_categorical_yaxis`` flags take the axes out
    of that pass, which is what they exist for.
    """
    r._render_summary_heatmap(fig, MATRIX, WELLS, TPS, "NND", "px")
    _restyle(fig)
    ax = fig.axes[0]
    assert _xticks(ax) == ["0", "1", "2"]
    ax.figure.canvas.draw()
    assert [t.get_text() for t in ax.get_yticklabels() if t.get_text()] == WELLS


def test_heatmap_axes_are_flagged_categorical(fig):
    r._render_summary_heatmap(fig, MATRIX, WELLS, TPS, "NND", "px")
    ax = fig.axes[0]
    assert getattr(ax, "_categorical_xaxis", False)
    assert getattr(ax, "_categorical_yaxis", False)


def test_heatmap_labels_carry_the_rescale_factor(fig):
    r._render_summary_heatmap(fig, MATRIX, WELLS, TPS, "NND", "px", 4)
    _restyle(fig)
    ax = fig.axes[0]
    assert _xticks(ax) == ["0", "4", "8"]
    assert "× 4" in ax.get_xlabel()


# ── the line variant: real timepoint values on the axis ────────────────────

def test_line_variant_ticks_are_rescaled_and_survive_the_restyle(fig):
    r._render_summary_lines(fig, MATRIX, WELLS, TPS, "NND (px)", 4)
    _restyle(fig)
    ax = fig.axes[0]
    ax.set_xticks(TPS)
    assert _xticks(ax) == ["0", "4", "8"]
    assert "× 4" in ax.get_xlabel()


def test_line_variant_plots_the_unscaled_values(fig):
    """The rescale is a label pass: the data behind it is still the hours the
    pipeline parsed out of the filenames."""
    r._render_summary_lines(fig, MATRIX, WELLS, TPS, "NND (px)", 4)
    ax = fig.axes[0]
    assert list(ax.lines[0].get_xdata()) == TPS


# ── the off state ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("render", ["heatmap", "lines"])
def test_no_marker_and_no_change_when_the_factor_is_off(fig, render):
    if render == "heatmap":
        r._render_summary_heatmap(fig, MATRIX, WELLS, TPS, "NND", "px", 1)
    else:
        r._render_summary_lines(fig, MATRIX, WELLS, TPS, "NND (px)", 1)
    _restyle(fig)
    ax = fig.axes[0]
    ax.set_xticks(TPS)
    assert _xticks(ax) == ["0", "1", "2"]
    assert ax.get_xlabel() == "Timepoint (h)"
