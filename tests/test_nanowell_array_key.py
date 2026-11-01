"""The Array view draws a key for its nanowell outlines: a ring swatch per way a nanowell is outlined in the view.

Asked for: "a better visual legend for what the outline colors around nanowells means in the array tab". The
colours were explained by one footer sentence naming them in words ("blue measured, green empty, …"), which called
every grey ring "not seen" although a nanowell excluded with its field, or with its rim out of focus, is grey too.

What is pinned: the key lists exactly the states the view holds, each with its count, in the colour, dash and hatch
the nanowells are drawn in; a fused nanowell is counted as fused and nowhere else, as it is drawn; and the swatches
are drawn as rings above the axes — the first version built them and drew every one at the figure's corner, where
nothing could see them.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")


def _sites(states, *, values=None, fused=()):
    from review.features.nanowell.array_compute import ArraySites

    n = len(states)
    is_fused = np.zeros(n, bool)
    is_fused[list(fused)] = True
    return ArraySites(np.arange(n, dtype=float) * 100.0, np.zeros(n), np.full(n, 35.0),
                      np.full(n, np.nan) if values is None else np.asarray(values, float), list(states), is_fused,
                      [("A01", k, 0) for k in range(n)], [f"{k},0" for k in range(n)])


def _key(sites, colour):
    from review.features.nanowell._array_key import key_entries
    from review.features.nanowell.renderer import _STATE_STYLE

    return [(e.label, e.colour, e.linestyle, e.hatch) for e in key_entries(sites, colour=colour, styles=_STATE_STYLE)]


def test_by_state_each_state_in_view_is_keyed_once_in_its_own_colour_with_its_count():
    from ui.nanowell_sites import FUSED_COLOUR, STATE_COLOURS, state_colour

    # The fused nanowell is measured: it is drawn dashed in the fused colour, so it is keyed there, not as measured.
    sites = _sites(["empty", "measured", "measured", "clipped", "measured", "field_excluded", "out_of_focus",
                    "field_excluded"], fused=[4])
    assert _key(sites, "state") == [
        ("measured · 2", STATE_COLOURS["measured"], "solid", ""),
        ("empty · 1", STATE_COLOURS["empty"], "solid", ""),
        ("clipped · 1", STATE_COLOURS["clipped"], "solid", ""),
        ("field excluded / rim out of focus · 3", state_colour("field_excluded"), "solid", ""),
        ("fused: on a tile seam · 1", FUSED_COLOUR, "dashed", ""),
    ]


def test_by_metric_only_the_nanowells_without_a_value_are_keyed_by_their_hatch_or_dash():
    from ui.nanowell_sites import FUSED_COLOUR
    from ui.theme import Colors

    grey = Colors.plot_undefined_ink
    # A measured nanowell with a value is filled, so it is not an outline the key explains; one without is.
    sites = _sites(["measured", "measured", "occluded", "clipped", "absent", "empty"],
                   values=[3.0, np.nan, np.nan, np.nan, np.nan, 0.0], fused=[5])
    assert _key(sites, "metric") == [
        ("no value · 1", grey, "solid", ""),
        ("no value: occluded · 1", grey, "solid", "////"),
        ("no value: clipped · 1", grey, "dashed", ""),
        ("no value: absent · 1", grey, "dotted", ""),
        ("fused: on a tile seam · 1", FUSED_COLOUR, "dashed", ""),
    ]


def test_the_key_is_drawn_as_ring_swatches_above_the_array_and_a_view_without_nanowells_has_none():
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.colors import to_hex
    from matplotlib.figure import Figure
    from matplotlib.patches import Circle

    from review.features.nanowell.array_compute import ArrayScene
    from review.features.nanowell.renderer import render_array
    from review.features.nanowell.state import ArrayLayers
    from ui.nanowell_sites import state_colour

    sites = _sites(["measured", "empty", "occluded"])
    scene = ArrayScene("A01", "0h", "lattice", sites, 3, 0, {"measured": 1, "empty": 1, "occluded": 1}, 100.0)
    fig = Figure(figsize=(8.0, 6.0), dpi=100)
    FigureCanvasAgg(fig)
    drawn = render_array(fig, scene, ArrayLayers(image="off", colour="state"), label="Cell count")
    fig.canvas.draw()
    (key,) = [a for a in drawn.ax.artists if a.get_gid() == "nw-array-key"]
    assert [t.get_text() for t in key.get_texts()] == ["measured · 1", "empty · 1", "occluded · 1"]
    rings = key.legend_handles
    assert all(isinstance(r, Circle) and not r.get_fill() for r in rings)
    assert [to_hex(r.get_edgecolor()) for r in rings] == [to_hex(state_colour(s)) for s in
                                                         ("measured", "empty", "occluded")]
    renderer = fig.canvas.get_renderer()
    top = drawn.ax.get_window_extent(renderer).y1
    for ring in rings:
        box = ring.get_window_extent(renderer)
        assert box.y0 >= top - 1 and box.width > 5                     # above the axes, and drawn at a size
    render_array(fig, scene, ArrayLayers(image="off", colour="state", nanowells=False), label="Cell count")
    assert not [a for a in fig.axes[0].artists if a.get_gid() == "nw-array-key"]
