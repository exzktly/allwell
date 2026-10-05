"""Two promises of the export-style pass that nothing pinned.

Row C1 of ``_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` splits
``apply_export_style_prefs`` into one pass per preference family. Mutating the
split passes against every test file that reaches ``plot_style`` (and the topic
files for each) left these two green:

* the line pass stopped skipping error-bar caps, so the user's data marker was
  stamped onto every cap — square caps at the ends of each whisker;
* the scale pass stopped re-installing a time axis's rescale after setting a
  log scale, so the axis went back to printing the raw parsed hours.

Each test below fails under its mutation, on what the figure actually shows.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

from review.core.plot_style import (  # noqa: E402
    apply_export_style_prefs,
    apply_time_rescale,
)


@pytest.fixture
def figure():
    fig, ax = plt.subplots()
    yield fig, ax
    plt.close(fig)


def test_error_bar_caps_keep_their_own_marker_and_size(figure):
    fig, ax = figure
    container = ax.errorbar([1, 2], [1, 2], yerr=[0.2, 0.2], capsize=4,
                            marker="o")
    caps = container.lines[1]
    before = [(c.get_marker(), c.get_markersize(), c.get_linewidth())
              for c in caps]

    apply_export_style_prefs(fig, {"marker_style": "s", "line_width": 3.0,
                                   "marker_size": 5.0})

    assert container.lines[0].get_marker() == "s"  # the data line is restyled
    assert [(c.get_marker(), c.get_markersize(), c.get_linewidth())
            for c in caps] == before


def test_a_rescaled_time_axis_keeps_its_labels_through_a_log_scale(figure):
    """Setting the scale resets the axis's formatter; the pass has to put the
    rescale back, or a log time axis prints the raw parsed hours."""
    fig, ax = figure
    ax.plot([1, 48], [1, 2])
    apply_time_rescale(ax, 0.5)

    apply_export_style_prefs(fig, {"x_log": True})

    assert ax.get_xscale() == "log"
    assert ax.xaxis.get_major_formatter()(48, 0) == "24"
