"""Every cause of a nanowell's exclusion draws its own mark on the Array view, under both colourings.

Asked: "on array, do a check that all the different causes for exclusion (subset selection, focus, unlabeled, etc)
all are surfaced for distinct outlines on the array". Measured before the fix: filled by the metric, *absent*, *not
located* and *unaddressed* drew one identical dotted grey ring; outlined by state, six states (absent, not located,
unaddressed, identity contradicted, field excluded, rim out of focus) drew one identical plain muted ring, and the key
named five of them on one swatch; a fused nanowell's purple dashed ring hid its state; and the veil over the
nanowells outside the *Subset* was in no key.

What is pinned, read off the drawn collections rather than off ``_STATE_STYLE`` (CLAUDE.md §7.4 #1): one nanowell of
every state, plus two measured ones without a value for different reasons, a fused one in and out of focus, and one
outside the Subset; each draws a mark — ring colour, dash pattern, hatch, veil, the words inside it — that no other
draws; and the key has an entry for each.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("matplotlib")

from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.colors import to_hex  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

#: (state, value, fused, outside the Subset, why it has no value)
CASES = [("measured", 2.0, False, False, ""),
         ("measured", np.nan, False, False, "no cells at baseline"),
         ("measured", np.nan, False, False, "baseline occluded"),
         ("occluded", np.nan, False, False, ""), ("clipped", np.nan, False, False, ""),
         ("absent", np.nan, False, False, ""), ("not_located", np.nan, False, False, ""),
         ("unaddressed", np.nan, False, False, ""), ("identity_contradicted", np.nan, False, False, ""),
         ("field_excluded", np.nan, False, False, ""), ("out_of_focus", np.nan, False, False, ""),
         ("measured", 2.0, True, False, ""), ("out_of_focus", np.nan, True, False, ""),
         ("measured", 2.0, False, True, "")]


def _render(colour: str):
    from review.features.nanowell.array_compute import ArrayScene, ArraySites
    from review.features.nanowell.renderer import render_array
    from review.features.nanowell.state import ArrayLayers

    n = len(CASES)
    sites = ArraySites(np.arange(n) * 100.0, np.zeros(n), np.full(n, 40.0), np.array([c[1] for c in CASES], float),
                       [c[0] for c in CASES], np.array([c[2] for c in CASES]), [("A01", k, 0) for k in range(n)],
                       [f"{k},0" for k in range(n)])
    scene = ArrayScene("A01", "t", "lattice", sites, n, 0, {}, 100.0)
    scene.why = {(k, 0): c[4] for k, c in enumerate(CASES) if c[4]}
    fig = Figure(figsize=(14, 4), dpi=100)
    FigureCanvasAgg(fig)
    dimmed = frozenset(("A01", k, 0) for k, c in enumerate(CASES) if c[3])
    drawn = render_array(fig, scene, ArrayLayers(image="off", colour=colour, addresses=False, values=True),
                         label="Count fold change", dimmed=dimmed)
    return drawn


def _marks(drawn) -> list[tuple]:
    """Each nanowell's mark as drawn: every collection over it (ring colour, fill, dash pattern, hatch, veil) and the
    words written inside it."""
    n = len(CASES)
    marks = {k: set() for k in range(n)}
    for coll in drawn.ax.collections:
        offsets = coll.get_offsets()
        edges, faces, styles = coll.get_edgecolors(), coll.get_facecolors(), coll.get_linestyle()
        for j, (x, _y) in enumerate(offsets):
            k = int(round(x / 100.0))
            if k not in marks:
                continue
            dash = styles[j % len(styles)][1] if styles else None
            marks[k].add((to_hex(edges[j % len(edges)]) if len(edges) else "none",
                          "veil" if coll.get_gid() == "nw-array-dimmed" else
                          "fill" if len(faces) and faces[j % len(faces)][3] > 0 else "",
                          tuple(round(v, 1) for v in dash) if dash else (), coll.get_hatch() or ""))
    words = {int(round(t.get_position()[0] / 100.0)): t.get_text() for t in drawn.ax.texts
             if t.get_gid() == "nw-array-address"}
    return [(tuple(sorted(marks[k])), words.get(k, "")) for k in range(n)]


@pytest.mark.parametrize("colour", ["metric", "state"])
def test_every_cause_draws_a_mark_no_other_draws(colour):
    marks = _marks(_render(colour))
    shared = {}
    for (state, _v, fused, outside, why), mark in zip(CASES, marks):
        shared.setdefault(mark, []).append(f"{state}{' fused' if fused else ''}{' outside' if outside else ''}"
                                           f"{' (' + why + ')' if why else ''}")
    assert [names for names in shared.values() if len(names) > 1] == [], colour


@pytest.mark.parametrize("colour", ["metric", "state"])
def test_a_fused_nanowell_keeps_its_state_s_hatch(colour):
    marks = _marks(_render(colour))
    soft_fused = next(k for k, c in enumerate(CASES) if c[0] == "out_of_focus" and c[2])
    assert any(hatch == "...." for hatch in (m[3] for m in marks[soft_fused][0])), marks[soft_fused]


@pytest.mark.parametrize("colour", ["metric", "state"])
def test_the_key_names_every_cause_on_the_array(colour):
    drawn = _render(colour)
    (key,) = [a for a in drawn.ax.artists if a.get_gid() == "nw-array-key"]
    words = " | ".join(t.get_text() for t in key.get_texts())
    for said in ("occluded", "clipped", "absent", "not located", "unaddressed", "identity contradicted",
                 "field excluded", "rim out of focus", "fused: on a tile seam", "outside the Subset (veiled) · 1"):
        assert said in words, (said, words)
    if colour == "metric":
        assert 'no cells at baseline ("0 at base") · 1' in words and 'baseline occluded ("base occl.") · 1' in words
    # One swatch per entry: nothing lumped under one ring.
    assert not any(" / " in t.get_text() for t in key.get_texts())
    # And the key fits on the figure, however many rows it takes.
    renderer = drawn.ax.figure.canvas.get_renderer()
    assert key.get_window_extent(renderer).y1 <= drawn.ax.figure.bbox.height + 0.5
