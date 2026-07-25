"""X-tick label alignment tracks the applied ``x_tick_angle``.

``apply_export_style_prefs`` owns the final rotation of every plot's x tick
labels (it flattens whatever a renderer drew to the ``x_tick_angle`` pref). It
must also own their horizontal alignment so the two never disagree: a horizontal
label (0°) reads centered under its bar/tick, and an angled label anchors its
right edge at the tick. Previously only the rotation was set, so a bar plot's
``ha="right"`` (drawn with its own 45° rotation) survived the flatten-to-0° pass
and left labels right-aligned under their bars.

Qt-free: builds a bare matplotlib ``Figure`` on the Agg backend.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from well_viewer.core.plot_style import (
    DEFAULT_EXPORT_STYLE_PREFS,
    apply_export_style_prefs,
)


def _fig_with_categorical_labels():
    fig, ax = plt.subplots()
    ax.bar([0, 1, 2], [1.0, 2.0, 3.0])
    ax.set_xticks([0, 1, 2])
    ax.set_xticklabels(["A01", "A02", "A03"])
    return fig, ax


def test_zero_angle_centers_labels():
    fig, ax = _fig_with_categorical_labels()
    # Simulate the bar renderer having drawn many-bar labels right-aligned.
    for tick in ax.get_xticklabels():
        tick.set_horizontalalignment("right")
    apply_export_style_prefs(fig, {**DEFAULT_EXPORT_STYLE_PREFS, "x_tick_angle": 0})
    for tick in ax.get_xticklabels():
        assert tick.get_rotation() == 0
        assert tick.get_horizontalalignment() == "center"
    plt.close(fig)


def test_nonzero_angle_right_aligns_labels():
    fig, ax = _fig_with_categorical_labels()
    apply_export_style_prefs(fig, {**DEFAULT_EXPORT_STYLE_PREFS, "x_tick_angle": 45})
    for tick in ax.get_xticklabels():
        assert tick.get_rotation() == 45
        assert tick.get_horizontalalignment() == "right"
    plt.close(fig)
