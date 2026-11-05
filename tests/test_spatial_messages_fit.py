"""A per-panel message stays inside its panel.

Spatial draws one panel per well, and a well with no cells at the chosen timepoint says so in its own panel.
``Text(wrap=True)`` wraps at the edge of the *figure*, not of the axes, so in a grid of panels a sentence
that fits the figure is wider than the panel it sits in and runs into the message beside it (the phone's
432 px upright, two panels across).
"""

from __future__ import annotations

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from review.features.spatial.renderer import _msg_ax

MESSAGE = "No cells at the selected timepoint."


def test_a_message_in_a_narrow_panel_is_wrapped_to_the_panel_not_the_figure():
    fig = Figure(figsize=(4.3, 9.4), dpi=100)          # a phone upright, two panels across
    FigureCanvasAgg(fig)
    axes = list(fig.subplots(2, 2).flat)
    for ax in axes:
        _msg_ax(ax, MESSAGE)
    renderer = fig.canvas.get_renderer()
    fig.draw(renderer)
    for ax in axes:
        (text,) = ax.texts
        box, frame = text.get_window_extent(renderer), ax.get_window_extent(renderer)
        assert box.x0 >= frame.x0 - 1 and box.x1 <= frame.x1 + 1, (
            f"{text.get_text()!r} spans {box.x0:.0f}..{box.x1:.0f} px in a panel of "
            f"{frame.x0:.0f}..{frame.x1:.0f}")


def test_a_message_that_fits_its_panel_stays_on_one_line():
    fig = Figure(figsize=(12, 4), dpi=100)             # a desk: the panel is wider than the sentence
    FigureCanvasAgg(fig)
    (ax,) = [fig.add_subplot(1, 1, 1)]
    _msg_ax(ax, MESSAGE)
    assert ax.texts[0].get_text() == MESSAGE
