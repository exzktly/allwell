"""The occupancy key's title stays inside the figure, however narrow the figure is.

Reported from a phone held upright, Nanowells ▸ Distribution: the key at the figure's top read
"aseline (earliest):" — its title, "Cells at baseline (earliest):", is right-aligned against the key's first dot and
ran left over the figure's own edge. A phone draws plot text at true phone sizes by raising the figure's DPI, so a
432 px figure is about 185 pt wide, less than the key's dots and its title need side by side. On a figure too narrow
for both the title is written above the dots, wrapped to the figure's width, and the key is that many rows taller.

Measured on drawn figures (Agg), through the two calls a figure makes: ``_head_room`` for the key's band and
``_occupancy_key`` to draw it.
"""

from __future__ import annotations

import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from review.features.nanowell import renderer as nwr

#: A phone held upright (432 px at the phone's raised DPI), the same held sideways, and a desk's plot card.
PHONE_UPRIGHT_IN = (2.57, 4.6)
PHONE_SIDEWAYS_IN = (5.5, 2.0)
DESK_IN = (11.0, 6.0)

PLAIN = dict(title="Cells at baseline", baseline="earliest")
#: The longest the Distribution key's title gets: a Subset that removes whole classes names them faint.
FILTERED = dict(title="Cells at baseline", baseline="first measurable", starting=(1, 2))
TRAJECTORIES = dict(title="Thick lines · cells at baseline")


def _drawn_key(size_in, *, title, baseline="", starting=(None, None)):
    """``(figure, title text, dot-label texts, key axes)`` of a key drawn as a figure draws it."""
    fig = Figure(figsize=size_in, dpi=168)
    FigureCanvasAgg(fig)
    text = nwr._key_title(title, baseline, starting)
    _top, bottom, height = nwr._head_room(fig, floor_top=0.86, key_text=text)
    nwr._occupancy_key(fig, title, baseline=baseline, starting=starting, at=(bottom, height))
    fig.canvas.draw()
    key = fig.axes[-1]
    legend = [t for t in key.texts if t.get_gid() == "nw-legend"]
    (heading,) = [t for t in legend if t.get_text().replace("\n", " ") == text]
    labels = [t for t in legend if t is not heading]
    assert [t.get_text() for t in labels] == ["0", "1", "2", "3+", "?"]
    return fig, heading, labels, key


@pytest.mark.parametrize("size_in", [PHONE_UPRIGHT_IN, PHONE_SIDEWAYS_IN, DESK_IN], ids=["upright", "sideways", "desk"])
@pytest.mark.parametrize("words", [PLAIN, FILTERED, TRAJECTORIES], ids=["plain", "filtered", "trajectories"])
def test_the_key_s_title_is_inside_the_figure_and_clear_of_its_dots(size_in, words):
    fig, heading, labels, key = _drawn_key(size_in, **words)
    renderer = fig.canvas.get_renderer()
    box = heading.get_window_extent(renderer)
    width, height = fig.canvas.get_width_height()
    assert box.x0 >= 0 and box.x1 <= width, (
        f"the key's title {heading.get_text()!r} spans x {box.x0:.0f}..{box.x1:.0f} px of a {width} px figure")
    assert box.y1 <= height, f"the key's title ends at y {box.y1:.0f} px, above a {height} px figure"
    first = labels[0].get_window_extent(renderer)
    beside = box.x1 <= first.x0
    above = box.y0 >= max(t.get_window_extent(renderer).y1 for t in labels) - 1
    assert beside or above, "the key's title is written over its dots' labels"
    # The key keeps to its own band: nothing of it below the axes ``_head_room`` gave it.
    band = key.get_window_extent(renderer)
    assert box.y0 >= band.y0 - 1 and min(t.get_window_extent(renderer).y0 for t in labels) >= band.y0 - 1


def test_on_an_upright_phone_the_title_is_above_the_dots_with_every_word():
    fig, heading, labels, _key = _drawn_key(PHONE_UPRIGHT_IN, **PLAIN)
    renderer = fig.canvas.get_renderer()
    assert heading.get_text().split() == "Cells at baseline (earliest):".split()
    assert heading.get_window_extent(renderer).y0 >= labels[0].get_window_extent(renderer).y1 - 1


def test_at_a_desk_the_title_stays_on_the_dots_row():
    """The desk's key is one row, as it was: the stacked form is only for a figure with no room beside the dots."""
    fig, heading, labels, _key = _drawn_key(DESK_IN, **FILTERED)
    assert "\n" not in heading.get_text()
    assert heading.get_position()[1] == labels[0].get_position()[1]
    assert nwr._key_title_lines(fig, heading.get_text()) == []
