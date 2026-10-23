"""A nanowell is drawn one way before a run and after it (``ui/nanowell_sites.py``).

Analyze's Test detection window and Review's Full montage window draw the same nanowells over the same kind of
montage, a person comparing the one against the other. Each carried its own palette, label threshold and outline
painter, and they had drifted: the Full montage window drew no fused site differently from any other. Held here: the
shared pieces behave as stated, both windows draw through them, and neither grows a private copy again.
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


def test_the_full_montage_s_sites_carry_the_address_and_the_fused_flag_from_the_table():
    from review.features.nanowell.full_montage_compute import site_overlays

    metrics = pd.DataFrame({
        "well": ["A01"] * 4, "timepoint": ["0h"] * 4,
        "well_x_um": [10.0, 20.0, 30.0, 40.0], "well_y_um": [1.0, 2.0, 3.0, 4.0], "radius_um": [50.0] * 4,
        "nw_q": [1.0, math.nan, 0.0, 2.0], "nw_r": [2.0, math.nan, 0.0, -1.0],
        "state": ["measured", "unaddressed", "empty", "empty"],
        # As ``collect._bool`` leaves the column: object dtype holding True / False / NaN.
        "fused": pd.Series([True, math.nan, False, np.True_], dtype="object"),
    })
    sites = site_overlays(metrics, "A01", "0h")
    assert [(s.label, s.state, s.fused) for s in sites] == [
        ("1,2", "measured", True), ("", "unaddressed", False), ("0,0", "empty", False), ("2,-1", "empty", True)]


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


# ── both windows draw through it ─────────────────────────────────────────────
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


def test_the_full_montage_window_hands_every_site_to_the_shared_painter(qapp, monkeypatch):
    from PySide6.QtGui import QImage, QPainter

    from review.features.nanowell import full_montage_view as fmv
    from review.features.nanowell.full_montage_compute import SiteOverlay

    calls = []
    monkeypatch.setattr(fmv, "paint_site", lambda painter, centre, radius, state, label, *, fused:
                        calls.append((round(centre.x()), round(centre.y()), radius, state, label, fused)))
    canvas = fmv.MontageCanvas()
    canvas.resize(400, 300)
    canvas.region = (0.0, 0.0, 800.0, 600.0)                          # 2 µm per screen pixel
    canvas.sites = [SiteOverlay(100.0, 60.0, 40.0, "1,2", "measured", fused=True),
                    SiteOverlay(300.0, 200.0, 40.0, "", "clipped")]
    image = QImage(400, 300, QImage.Format_RGB32)
    painter = QPainter(image)
    canvas._draw_sites(painter)
    painter.end()
    assert calls == [(50, 30, 20.0, "measured", "1,2", True), (150, 100, 20.0, "clipped", "", False)]
    canvas.deleteLater()


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
