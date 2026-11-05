"""Neighbouring tick labels are kept clear of each other (``review.core.tick_fit``).

Reported by the page-render sweep: the Heat Map's column numbers drew "10 11 12"
as "101112" on a phone, and the Multivariate correlation matrix's long feature
names touched at the desk as well. The fit shrinks the labels a step at a time,
then stands an x-axis's labels on end, and never drops or elides one.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from review.core.tick_fit import SHRINK_STEPS, fit_tick_labels

FEATURES = ["GFP Mean Intensity", "MCHERRY Mean Intensity", "Area"]


def _figure(width_in: float, labels: list[str], size: float):
    fig = Figure(figsize=(width_in, 3), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_xlim(-0.5, len(labels) - 0.5)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=size)
    return fig, ax


def _touching(fig, ax) -> bool:
    renderer = fig.canvas.get_renderer()
    ax.apply_aspect()
    boxes = [t.get_window_extent(renderer) for t in ax.get_xticklabels()]
    return any(min(a.x1, b.x1) - max(a.x0, b.x0) > 1.5 and min(a.y1, b.y1) - max(a.y0, b.y0) > 1.5
               for a, b in zip(boxes, boxes[1:]))


def test_numbers_that_touch_are_shrunk_until_they_clear():
    labels = [str(i) for i in range(1, 13)]
    fig, ax = _figure(2.2, labels, size=14)
    assert _touching(fig, ax), "the fixture must collide to prove anything"
    fit_tick_labels(fig)
    assert not _touching(fig, ax)
    size = ax.get_xticklabels()[0].get_fontsize()
    assert 14 * SHRINK_STEPS[-1] <= size < 14
    assert [t.get_text() for t in ax.get_xticklabels()] == labels
    assert ax.get_xticklabels()[0].get_rotation() == 0.0


def test_long_names_that_shrinking_cannot_save_are_broken_at_their_spaces():
    fig, ax = _figure(2.4, FEATURES, size=12)
    assert _touching(fig, ax)
    fit_tick_labels(fig)
    assert not _touching(fig, ax)
    shown = [t.get_text() for t in ax.get_xticklabels()]
    assert any("\n" in t for t in shown)
    assert [t.split() for t in shown] == [f.split() for f in FEATURES]
    assert all(t.get_rotation() == 0.0 for t in ax.get_xticklabels())


def test_a_name_with_no_space_to_break_at_is_stood_on_end():
    names = ["GFP_MeanIntensity", "MCHERRY_MeanIntensity", "Area_Total"]
    fig, ax = _figure(3.0, names, size=12)
    assert _touching(fig, ax)
    fit_tick_labels(fig)
    assert not _touching(fig, ax)
    assert all(t.get_rotation() == 90.0 for t in ax.get_xticklabels())
    assert [t.get_text() for t in ax.get_xticklabels()] == names


def test_a_figure_that_grows_gets_its_own_labels_back():
    fig, ax = _figure(3.0, FEATURES, size=12)
    fit_tick_labels(fig)
    fig.set_size_inches(12.0, 3.0)
    fit_tick_labels(fig)
    assert [t.get_text() for t in ax.get_xticklabels()] == FEATURES
    assert ax.get_xticklabels()[0].get_fontsize() == 12.0


def test_labels_that_already_clear_are_left_exactly_as_they_were():
    fig, ax = _figure(8.0, FEATURES, size=8)
    fit_tick_labels(fig)
    label = ax.get_xticklabels()[0]
    assert (label.get_fontsize(), label.get_rotation()) == (8.0, 0.0)


def test_a_figure_that_grows_gets_its_own_size_back():
    labels = [str(i) for i in range(1, 13)]
    fig, ax = _figure(2.2, labels, size=14)
    fit_tick_labels(fig)
    assert ax.get_xticklabels()[0].get_fontsize() < 14
    fig.set_size_inches(9.0, 3.0)
    fit_tick_labels(fig)
    assert ax.get_xticklabels()[0].get_fontsize() == 14.0


def test_a_second_fit_changes_nothing():
    fig, ax = _figure(3.0, FEATURES, size=12)
    fit_tick_labels(fig)
    first = [(t.get_fontsize(), t.get_rotation()) for t in ax.get_xticklabels()]
    fit_tick_labels(fig)
    assert [(t.get_fontsize(), t.get_rotation()) for t in ax.get_xticklabels()] == first


def test_a_renderer_that_sets_its_own_labels_again_is_fitted_afresh():
    """A redraw rewrites the labels at the renderer's size; the fit must not
    mistake the new size for its own and restore an old one."""
    labels = [str(i) for i in range(1, 13)]
    fig, ax = _figure(2.2, labels, size=14)
    fit_tick_labels(fig)
    ax.set_xticklabels(labels, fontsize=11)
    fit_tick_labels(fig)
    assert not _touching(fig, ax)
    assert ax.get_xticklabels()[0].get_fontsize() <= 11


def test_y_labels_cut_off_by_the_figure_edge_are_broken_at_their_spaces():
    """Not neighbours touching: the labels of a correlation matrix on a phone are
    wider than the margin they sit in, and the figure's edge cut them off."""
    fig = Figure(figsize=(4.06, 4), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    fig.subplots_adjust(left=0.28)
    ax.set_yticks(range(3))
    ax.set_yticklabels(FEATURES, fontsize=16)
    renderer = fig.canvas.get_renderer()

    def left_edge() -> float:
        return min(t.get_window_extent(renderer).x0 for t in ax.get_yticklabels())

    assert left_edge() < 0, "the fixture must be cut off to prove anything"
    fit_tick_labels(fig)
    assert left_edge() >= -1.5
    assert [t.get_text().split() for t in ax.get_yticklabels()] == [f.split() for f in FEATURES]
