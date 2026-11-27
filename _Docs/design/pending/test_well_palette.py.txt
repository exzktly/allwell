"""The 96 well colours (``domain/well_palette.py``): the figures the design was approved on, and no migration.

Two halves:

1. **The table of ``DESIGN_LANGUAGE_TRACKER.md`` §3.4, recomputed from the palette** — WCAG contrast on the grey
   and blue plot backgrounds and on white, and OKLab distances (×100) for normal vision and for deuteranopia and
   protanopia simulated at full severity (Machado, Oliveira & Fernandes 2009), with the same arithmetic as
   ``_Docs/design/well_palette_build.py``. On the palette it replaced it fails, naming the 19 colours under 3 : 1 on
   the grey plot background.
2. **A colour already saved in a dataset is kept, and a new selection takes the new palette.** Through the two
   functions the load path calls: ``from_block`` on the saved ``sample_definitions`` block (what
   ``review.persistence.sample_definitions`` hands the file's block to), and ``make_selection`` for a selection made
   now.
"""

from __future__ import annotations

import numpy as np
import pytest

from domain.well_palette import WELL_COLORS_TUPLE

GREY_PLOT, BLUE_PLOT, WHITE = "#0A0C10", "#131A24", "#FFFFFF"

#: The eight colours the user approved by hand, ranks 0 to 7.
APPROVED = ("#338CED", "#E76624", "#00A888", "#D28E05", "#E24D8B", "#0AAC23", "#9887EF", "#EB6263")

#: Two colours of the palette this one replaced, for the test that a saved colour survives.
OLD_RANK_0, OLD_RANK_1 = "#6AB2FB", "#06F906"

_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929], [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]])
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468], [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]])
_CVD = (np.array([[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]]),
        np.array([[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]]))


def _linear(hexes) -> np.ndarray:
    rgb = np.array([[int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)] for h in hexes])
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def _oklab(lin: np.ndarray) -> np.ndarray:
    return np.cbrt(lin @ _M1.T) @ _M2.T * 100


def _contrast(hexes, ground: str) -> np.ndarray:
    y = _linear(hexes) @ np.array([0.2126, 0.7152, 0.0722])
    yb = float(_linear([ground]) @ np.array([0.2126, 0.7152, 0.0722]))
    return (np.maximum(y, yb) + 0.05) / (np.minimum(y, yb) + 0.05)


def measure(hexes) -> dict:
    lin = _linear(hexes)
    views = [_oklab(lin)] + [_oklab(np.clip(lin @ m.T, 0, 1)) for m in _CVD]
    n = len(hexes)
    dist = [np.linalg.norm(v[:, None] - v[None], axis=2) + np.eye(n) * 1e9 for v in views]
    # A plate neighbour: the next well along a row (not across a row's end) or down a column.
    nbr = [(i, j) for i in range(n) for j in (i + 1, i + 12) if j < n and not (j == i + 1 and j % 12 == 0)]
    cvd = [min(dist[1][i, j], dist[2][i, j]) for i, j in nbr]
    out = {
        "closest anywhere, normal": round(float(dist[0].min()), 1),
        "closest neighbours, normal": round(float(min(dist[0][i, j] for i, j in nbr)), 1),
        "closest neighbours, colour-blind": round(float(min(cvd)), 1),
        "neighbours under 8, colour-blind": int(sum(d < 8 for d in cvd)),
    }
    for name, ground in (("grey plot", GREY_PLOT), ("blue plot", BLUE_PLOT), ("white", WHITE)):
        c = _contrast(hexes, ground)
        out[f"under 3:1 on {name}"] = int((c < 3).sum())
        out[f"lowest on {name}"] = round(float(c.min()), 2)
    return out


def test_the_palette_is_96_distinct_colours_starting_with_the_approved_eight():
    assert len(WELL_COLORS_TUPLE) == 96, "96 — one per plate position; the module's docstring says why"
    assert len(set(WELL_COLORS_TUPLE)) == 96
    assert WELL_COLORS_TUPLE[:8] == APPROVED


def test_the_palette_measures_what_the_tracker_says():
    assert measure(WELL_COLORS_TUPLE) == {
        "under 3:1 on grey plot": 0, "lowest on grey plot": 3.34,
        "under 3:1 on blue plot": 3, "lowest on blue plot": 2.99,
        "under 3:1 on white": 18, "lowest on white": 2.75,
        "closest neighbours, normal": 14.0,
        "closest neighbours, colour-blind": 10.1,
        "neighbours under 8, colour-blind": 0,
        "closest anywhere, normal": 4.0,
    }


def test_every_colour_reads_on_the_grey_plot_background():
    """The one floor every colour meets; named per colour, so a change says which well it broke."""
    c = _contrast(WELL_COLORS_TUPLE, GREY_PLOT)
    low = [(rank, WELL_COLORS_TUPLE[rank], round(float(v), 2)) for rank, v in enumerate(c) if v < 3]
    assert not low, f"colours under 3 : 1 on the grey plot background: {low}"


def test_a_colour_saved_in_a_dataset_is_kept_and_a_new_selection_takes_the_new_palette():
    from domain.selections_format import from_block, make_selection, to_block

    saved = to_block([make_selection(name="Control", wells=["A01", "A02"], color=OLD_RANK_0, sel_id="s1"),
                      make_selection(name="Drug", wells=["A02"], color=OLD_RANK_1, sel_id="s2")])
    selections = from_block(saved)[0]
    assert [s["color"] for s in selections] == [OLD_RANK_0, OLD_RANK_1], "a saved colour was replaced on load"
    made_now = make_selection(name="New", wells=["A01"])
    assert made_now["color"] == WELL_COLORS_TUPLE[0] == "#338CED"
    assert make_selection(name="New B", wells=["A02"])["color"] == WELL_COLORS_TUPLE[1]


@pytest.mark.parametrize("ground", [GREY_PLOT, BLUE_PLOT])
def test_the_measuring_stick_reproduces_the_old_palettes_figures(ground):
    """Checked against the palette this one replaced, whose figures §3.4 also records: 19 colours under 3 : 1 on the
    grey plot background (lowest 1.37), 22 on the blue (lowest 1.22)."""
    from tests.test_well_palette import OLD_PALETTE

    got = measure(OLD_PALETTE)
    want = {GREY_PLOT: (19, 1.37), BLUE_PLOT: (22, 1.22)}[ground]
    key = "grey plot" if ground == GREY_PLOT else "blue plot"
    assert (got[f"under 3:1 on {key}"], got[f"lowest on {key}"]) == want


#: The palette this one replaced, kept here only as the measuring stick's check.
OLD_PALETTE = (
    "#6AB2FB", "#06F906", "#0606F9", "#DF2020", "#138613", "#D406F9", "#FAAC38", "#6E2277",
    "#6AFBE5", "#2E719E", "#EC79B8", "#FAFA38", "#774C22", "#131386", "#05C783", "#806AFB",
    "#B37D19", "#C7058D", "#5C05C7", "#2E9E9E", "#950437", "#FA7238", "#AAC705", "#224877",
    "#9C39C6", "#D4DD88", "#AA88DD", "#779E2E", "#F906BC", "#0553C7", "#068CF9", "#20D6DF",
    "#E56AFB", "#FA387C", "#DD9D88", "#777222", "#79EC7F", "#227762", "#6938FA", "#05C705",
    "#B34F19", "#D16167", "#C7A005", "#471386", "#B0F906", "#950495", "#0667F9", "#1929B3",
    "#7F4660", "#7F0000", "#007F43", "#33337F", "#EAC081", "#7F0C51", "#70ADCC", "#EAE15D",
    "#EA965D", "#A55B9B", "#7ACC33", "#7F3333", "#70CCA1", "#5D7F46", "#EA3A52", "#A5875B",
    "#DC81EA", "#46747F", "#CC14CC", "#9BA55B", "#EACB00", "#8ECC70", "#5B60A5", "#A56A5B",
    "#EA7D00", "#EA5DC5", "#00EA6D", "#CCB351", "#CC337A", "#CC708E", "#BBEA5D", "#A5A510",
    "#6A42A5", "#6DEA00", "#00A579", "#EA795D", "#21A500", "#1482CC", "#7F3B00", "#00EABB",
    "#A5425C", "#6C467F", "#42A54F", "#A43AEA", "#81EAA4", "#3A46EA", "#17C0EA", "#EA5D83",
)
