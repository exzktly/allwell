"""Rescaled time axes on a real matplotlib figure.

Covers the seam in ``core/plot_style.py`` rather than a whole tab: the formatter
installs, prints the multiplied value, leaves the data alone, and survives the
export-style pass — which calls ``set_xscale`` and would otherwise wipe it.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

from well_viewer.core.plot_style import (  # noqa: E402
    apply_export_style_prefs,
    apply_time_rescale,
)


@pytest.fixture
def ax():
    fig, axes = plt.subplots()
    axes.plot([0, 1, 2, 3], [1, 2, 3, 4])
    yield axes
    plt.close(fig)


def _tick_texts(axes) -> list[str]:
    axes.figure.canvas.draw()
    return [t.get_text() for t in axes.get_xticklabels() if t.get_text()]


def test_ticks_print_the_multiplied_value(ax):
    ax.set_xticks([0, 1, 2, 3])
    apply_time_rescale(ax, 4)
    assert _tick_texts(ax) == ["0", "4", "8", "12"]


def test_the_plotted_data_is_untouched(ax):
    """The whole reason this lives in a formatter: only the text changes, so
    limits, picks and exports still see the hours the pipeline parsed."""
    apply_time_rescale(ax, 4)
    ax.figure.canvas.draw()
    assert list(ax.lines[0].get_xdata()) == [0, 1, 2, 3]
    assert ax.get_xlim()[1] < 10          # still in filename units, not ×4


def test_a_factor_of_one_leaves_the_default_formatter(ax):
    ax.set_xticks([0, 1, 2, 3])
    before = _tick_texts(ax)
    apply_time_rescale(ax, 1)
    assert _tick_texts(ax) == before


def test_the_factor_is_stamped_for_the_restyle_pass(ax):
    from well_viewer.domain.time_rescale import TimeRescale
    apply_time_rescale(ax, 4)
    assert ax._aw_time_rescale == TimeRescale(factor=4)
    apply_time_rescale(ax, 1)
    assert ax._aw_time_rescale == TimeRescale(factor=1)


def test_the_export_style_pass_does_not_wipe_the_formatter(ax):
    """``apply_export_style_prefs`` runs after every redraw and calls
    ``set_xscale``, which resets an axis's locator and formatter. Without the
    stamped re-install the rescaled ticks reverted to raw hours the moment the
    Properties sidebar touched anything.
    """
    ax.set_xticks([0, 1, 2, 3])
    apply_time_rescale(ax, 4)
    assert _tick_texts(ax) == ["0", "4", "8", "12"]

    apply_export_style_prefs(ax.figure, {"x_log": False, "y_log": False})

    ax.set_xticks([0, 1, 2, 3])
    assert _tick_texts(ax) == ["0", "4", "8", "12"]


def test_an_unstamped_axis_is_left_alone_by_the_restyle(ax):
    """Only axes that asked for it get a rescale — the pass walks every axes in
    the figure, including colour bars and non-time panels."""
    ax.set_xticks([0, 1, 2, 3])
    before = _tick_texts(ax)
    apply_export_style_prefs(ax.figure, {"x_log": False, "y_log": False})
    ax.set_xticks([0, 1, 2, 3])
    assert _tick_texts(ax) == before


def test_a_rejected_factor_draws_the_unscaled_axis(ax):
    """A zero or negative factor would collapse or mirror the axis; it falls
    back rather than drawing something that cannot be right."""
    ax.set_xticks([0, 1, 2, 3])
    apply_time_rescale(ax, 0)
    assert _tick_texts(ax) == ["0", "1", "2", "3"]


def test_a_fractional_factor_prints_fractional_hours(ax):
    ax.set_xticks([0, 1, 2, 3])
    apply_time_rescale(ax, 0.5)
    assert _tick_texts(ax) == ["0", "0.5", "1", "1.5"]


# ── the offset, on a real axis ──────────────────────────────────────────────

def test_ticks_print_the_shifted_value(ax):
    from well_viewer.domain.time_rescale import TimeRescale
    ax.set_xticks([0, 1, 2, 3])
    apply_time_rescale(ax, TimeRescale(factor=4, offset=-2))
    assert _tick_texts(ax) == ["-2", "2", "6", "10"]


def test_an_offset_alone_still_installs_the_formatter(ax):
    """A shifted axis is as far from the timepoint menus as a scaled one, and
    an offset with the factor left at one is the ordinary way to ask for it."""
    from well_viewer.domain.time_rescale import TimeRescale
    ax.set_xticks([0, 1, 2, 3])
    apply_time_rescale(ax, TimeRescale(factor=1, offset=2))
    assert _tick_texts(ax) == ["2", "3", "4", "5"]


def test_the_offset_leaves_the_plotted_data_alone(ax):
    from well_viewer.domain.time_rescale import TimeRescale
    apply_time_rescale(ax, TimeRescale(factor=4, offset=-2))
    ax.figure.canvas.draw()
    assert list(ax.lines[0].get_xdata()) == [0, 1, 2, 3]
    assert ax.get_xlim()[1] < 10          # still in filename units


def test_the_whole_correction_is_stamped_for_the_restyle_pass(ax):
    """The stamp carries both halves. Re-installing a factor alone would drop
    the offset here and nowhere else — a figure that is right until the export
    style pass runs over it, which is the worst place for the two to disagree.
    """
    from well_viewer.domain.time_rescale import TimeRescale
    rs = TimeRescale(factor=4, offset=-2)
    apply_time_rescale(ax, rs)
    assert ax._aw_time_rescale == rs


def test_the_offset_survives_the_export_style_pass(ax):
    from well_viewer.domain.time_rescale import TimeRescale
    ax.set_xticks([0, 1, 2, 3])
    apply_time_rescale(ax, TimeRescale(factor=4, offset=-2))
    apply_export_style_prefs(ax.figure, {})
    ax.set_xticks([0, 1, 2, 3])
    assert _tick_texts(ax) == ["-2", "2", "6", "10"]
