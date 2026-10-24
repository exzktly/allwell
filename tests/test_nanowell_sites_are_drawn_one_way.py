"""A nanowell is drawn one way before a run and after it (``ui/nanowell_sites.py``).

Analyze's Test detection window and the Array view of Review's Nanowells page draw the same nanowells over the
same kind of montage, a person comparing the one against the other. Each carried its own palette, label
threshold and outline painter, and they had drifted: Review's full-resolution window drew no fused site
differently from any other. Held here: the shared pieces behave as stated, both views draw with them, and neither
grows a private copy again.
"""

from __future__ import annotations

import ast
import math

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("PySide6")

from tests._repo_scan import parsed_sources, rel  # noqa: E402
from util.nanowell_address import coordinate_text  # noqa: E402


# ── the address text ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("q, r, text", [
    (1, 2, "1,2"), (0, 0, "0,0"), (-3, 1, "-3,1"),
    (1.0, 2.0, "1,2"),                         # a measurements table reads integers back as floats
    (np.int64(4), np.int64(-1), "4,-1"),
    (math.nan, 2.0, None), (1.0, math.nan, None),
    (None, 2, None), (1, None, None),
])
def test_the_address_text_is_the_integers_and_nothing_for_a_site_without_one(q, r, text):
    assert coordinate_text(q, r) == text


def _metrics():
    return pd.DataFrame({
        "well": ["A01"] * 4, "timepoint": ["0h"] * 4, "reason": [""] * 4, "n": [1.0, math.nan, 0.0, 2.0],
        "well_x_um": [100.0, 300.0, 500.0, 700.0], "well_y_um": [100.0] * 4, "radius_um": [50.0] * 4,
        "nw_q": [1.0, math.nan, 0.0, 2.0], "nw_r": [2.0, math.nan, 0.0, -1.0],
        "state": ["measured", "unaddressed", "empty", "occluded"],
        # As ``collect._bool`` leaves the column: object dtype holding True / False / NaN.
        "fused": pd.Series([True, math.nan, False, np.True_], dtype="object"),
    })


def _data():
    from types import SimpleNamespace

    return SimpleNamespace(grids={}, acquisition={}, in_dir=None)


def test_the_array_view_s_sites_carry_the_address_and_the_fused_flag_from_the_table():
    from review.features.nanowell.array_compute import array_scene

    scene = array_scene(_metrics(), _data(), "A01", "0h", "n", positions="measured", anchors=False)
    sites = scene.sites
    assert list(zip(sites.labels, sites.state, sites.fused)) == [
        ("1,2", "measured", True), ("", "unaddressed", False), ("0,0", "empty", False), ("2,-1", "occluded", True)]
    assert sites.sites == [("A01", 1, 2), ("A01", None, None), ("A01", 0, 0), ("A01", 2, -1)]


# ── the painter ──────────────────────────────────────────────────────────────
def _paint(qapp, **kw):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QImage, QPainter

    from ui.nanowell_sites import paint_site

    image = QImage(100, 100, QImage.Format_RGB32)
    image.fill(QColor(0, 0, 0))
    painter = QPainter(image)
    paint_site(painter, QPointF(50.0, 50.0), kw.pop("radius", 20.0), kw.pop("state", "empty"), **kw)
    painter.end()
    return image


def _colours(image) -> set[int]:
    return {image.pixelColor(x, y).rgb() for x in range(image.width()) for y in range(image.height())}


def test_an_outline_is_drawn_in_its_state_s_colour_and_a_fused_one_in_the_fused_colour(qapp):
    from PySide6.QtGui import QColor

    from ui.nanowell_sites import FUSED_COLOUR, STATE_COLOURS

    black = QColor(0, 0, 0).rgb()
    assert _colours(_paint(qapp, state="occluded")) == {black, QColor(STATE_COLOURS["occluded"]).rgb()}
    assert _colours(_paint(qapp, state="occluded", fused=True)) == {black, QColor(FUSED_COLOUR).rgb()}
    # A state with no colour of its own is drawn muted, never in another state's colour or not at all.
    from ui.theme import Colors
    assert _colours(_paint(qapp, state="not_located")) == {black, QColor(Colors.text_muted).rgb()}


def test_the_address_is_written_only_where_the_outline_has_room_for_it(qapp):
    from ui.nanowell_sites import MIN_LABEL_DIAMETER_PX

    roomy = MIN_LABEL_DIAMETER_PX / 2 + 1
    cramped = MIN_LABEL_DIAMETER_PX / 2 - 1
    assert _paint(qapp, radius=roomy, label="12,3") != _paint(qapp, radius=roomy)
    assert _paint(qapp, radius=cramped, label="12,3") == _paint(qapp, radius=cramped)
    assert _paint(qapp, radius=roomy, label="12,3", show_label=False) == _paint(qapp, radius=roomy)


# ── both views draw with it ─────────────────────────────────────────────
def test_test_detection_draws_a_fused_site_in_the_fused_colour(qapp):
    from PySide6.QtGui import QColor

    from analyze.nanowell_preview import render_montage
    from pipeline.nanowell_preview import PreviewResult
    from ui.nanowell_sites import FUSED_COLOUR

    result = PreviewResult("ok", montage=np.zeros((100, 200), np.uint8), um_per_px=4.0, origin_um=(0.0, 0.0))
    result.sites = [{"address": "0,0", "state": "empty", "fused": True, "n_cells": None,
                     "well_x_um": 400.0, "well_y_um": 200.0, "radius_um": 50.0}]
    image = render_montage(result, 1.0).toImage()
    assert QColor(FUSED_COLOUR).rgb() in _colours(image)


def _array_figure(*, colour: str, px_per_um: float):
    """The Array view of :func:`_metrics`, drawn by state or by metric with addresses, at *px_per_um*."""
    from matplotlib.figure import Figure

    from review.features.nanowell.array_compute import array_scene
    from review.features.nanowell.renderer import render_array
    from review.features.nanowell.state import ArrayLayers

    fig = Figure(figsize=(8.0, 6.0), dpi=100)
    scene = array_scene(_metrics(), _data(), "A01", "0h", "n", positions="measured", anchors=False)
    drawn = render_array(fig, scene, ArrayLayers(image="off", colour=colour, addresses=True, values=False), label="Cell count")
    width = drawn.size_px[0] / px_per_um
    centre = 400.0
    return fig, render_array(fig, scene, ArrayLayers(image="off", colour=colour, addresses=True, values=False), label="Cell count",
                             region=(centre - width / 2, 0.0, centre + width / 2, 200.0))


def test_the_array_view_outlines_by_state_dashes_a_fused_site_and_labels_where_there_is_room():
    from matplotlib.colors import to_hex

    from ui.nanowell_sites import FUSED_COLOUR, LABEL_COLOUR, MIN_LABEL_DIAMETER_PX, state_colour

    roomy = (MIN_LABEL_DIAMETER_PX + 2) / 100.0                       # px per µm: a 100 µm nanowell just fits it
    fig, drawn = _array_figure(colour="state", px_per_um=roomy)
    plain, fused = drawn.ax.collections
    assert [to_hex(c) for c in plain.get_edgecolors()] == [to_hex(state_colour(s)) for s in ("unaddressed", "empty")]
    assert [to_hex(c) for c in fused.get_edgecolors()] == [to_hex(FUSED_COLOUR)]
    assert fused.get_linestyle()[0][1] is not None                     # dashed
    labels = [t for t in drawn.ax.texts if t.get_gid() == "nw-array-address"]
    assert [t.get_text() for t in labels] == ["1,2", "0,0", "2,-1"]    # the unaddressed site has nothing to write
    assert {to_hex(t.get_color()) for t in labels} == {to_hex(LABEL_COLOUR)}
    _fig, cramped = _array_figure(colour="state", px_per_um=(MIN_LABEL_DIAMETER_PX - 2) / 100.0)
    assert not [t for t in cramped.ax.texts if t.get_gid() == "nw-array-address"]


def test_filled_by_the_metric_a_fused_site_is_still_dashed_in_the_fused_colour():
    from matplotlib.colors import to_hex

    from ui.nanowell_sites import FUSED_COLOUR

    _fig, drawn = _array_figure(colour="metric", px_per_um=0.5)
    dashed = [c for c in drawn.ax.collections if c.get_array() is None and len(c.get_edgecolors())
              and to_hex(c.get_edgecolors()[0]) == to_hex(FUSED_COLOUR)]
    assert len(dashed) == 1 and len(dashed[0].get_offsets()) == 2


# ── and neither keeps a copy of its own ──────────────────────────────────────
#: The names the shared module owns. A second assignment of any of them anywhere is a second palette.
_OWNED = {"STATE_COLOURS", "FUSED_COLOUR", "MIN_LABEL_DIAMETER_PX"}
_OWNER = "ui/nanowell_sites.py"


def test_no_module_but_the_shared_one_defines_the_nanowell_palette():
    found = []
    for path, tree in parsed_sources():
        if rel(path) == _OWNER:
            continue
        for node in ast.walk(tree):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target] if isinstance(node, ast.AnnAssign) else [])
            for target in targets:
                if isinstance(target, ast.Name) and target.id in _OWNED:
                    found.append(f"{rel(path)}:{node.lineno} {target.id}")
    assert not found, ("These redefine what ui/nanowell_sites.py owns; import it instead, so a nanowell reads the "
                       "same before a run and after it:\n  " + "\n  ".join(found))
