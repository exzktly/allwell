"""Cell Gating's size classes, drawn: the Segmentation view outlines each kept cell in its class's colour.

Asked for: cells are told apart by size and named (Home ▸ Cell Gating ▸ Size classes), and those names should be
visible on the image — a different outline colour per class. The rule used to be counted per nanowell only and its
panel was hidden on any other dataset; it is now shown on every dataset, and the Segmentation view reads it.

The fixtures are built so the wrong implementations draw something different (CLAUDE.md §7.8):

* the label map gives every cell the **same** number of pixels while the table's areas differ, so classing by the
  mask's own pixel count instead of the debris gate's area column would put every cell in one class;
* the first class is the **larger** cells, so classes assigned in nucleus-id order would swap the colours;
* two cells sit in the gap between the ranges and must keep the ordinary kept-cell outline, never be forced into
  a class.

One test goes through the app's real sequence — the Segmentation panel built, a field drawn, then the rule changed
through ``qc.controller.set_size_classes`` (the call Cell Gating's panel makes) — and reads the pixels and the
status line, because a rule that is set and a view that is not drawn again is the failure that matters.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from review.domain.size_classes import SizeClassRule, area_column, classify_rows

#: First class the larger cells, so id order and class order disagree.
RULE = SizeClassRule.of(("Large", 245, None), ("Small", None, 225))
#: The mini dataset's areas are 200 + 10·cell: cells 1–2 Small (210, 220), 3–4 in neither (230, 240), 5–6 Large.
EXPECTED = {1: 1, 2: 1, 5: 0, 6: 0}
KEPT_RED = (255, 64, 64)          # ``ReviewImageState.boundary_color``'s default


def _rgb(hex_colour: str) -> tuple:
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _inks():
    from ui import theme

    return [_rgb(ink) for ink, _name in theme.size_class_inks()]


def _band_mask():
    """Six cells 1–6, one horizontal band each, every one the same 6 × 40 pixels."""
    mask = np.zeros((48, 48), np.int32)
    for k in range(6):
        mask[k * 8 + 1:k * 8 + 7, 4:44] = k + 1
    return mask


def _band_colours(rgb) -> dict:
    """Each cell's set of outline colours, read on its band's top edge (all outline pixels)."""
    return {k + 1: set(map(tuple, rgb[k * 8 + 1, 4:44].tolist())) for k in range(6)}


# ── the domain helpers ────────────────────────────────────────────────────────


def test_the_area_column_is_the_debris_gate_s_own_first_choice():
    assert area_column(["area_px", "shape_area"]) == "shape_area"
    assert area_column(["area_px"]) == "area_px"
    assert area_column(["gfp_mean_intensity"]) is None


def test_rows_are_not_classified_without_a_complete_rule_or_an_area_column():
    rows = pd.DataFrame({"area_px": [210.0, 250.0]})
    assert classify_rows(rows, RULE).tolist() == [1, 0]
    assert classify_rows(rows, SizeClassRule.of(("Large", 245, None))) is None      # incomplete
    assert classify_rows(pd.DataFrame({"x": [1.0]}), RULE) is None                 # nothing to judge on


# ── the per-field map ─────────────────────────────────────────────────────────


def _collect_ctx(df):
    return SimpleNamespace(state=SimpleNamespace(dataset=SimpleNamespace(
        cache={"A01": df}, well_paths={"A01": "x.csv"}, pipeline_info={})))


def test_the_map_classes_each_cell_of_the_field_by_its_table_area():
    from review.features.image_browser.review_image.collect import review_build_size_class_map

    df = pd.DataFrame({"fov": ["1"] * 6 + ["2"], "timepoint": ["0"] * 7, "nucleus_id": [1, 2, 3, 4, 5, 6, 1],
                       "area_px": [210.0, 220.0, 230.0, 240.0, 250.0, 260.0, 999.0]})
    out = review_build_size_class_map(_collect_ctx(df), _band_mask(), "A01", "1", "0", RULE)
    assert out == EXPECTED                       # field 2's cell 1 (999 px) does not leak into field 1


def test_a_table_without_an_area_column_is_unclassified_not_empty():
    from review.features.image_browser.review_image.collect import review_build_size_class_map

    df = pd.DataFrame({"fov": ["1"], "timepoint": ["0"], "nucleus_id": [1], "gfp_mean_intensity": [5.0]})
    assert review_build_size_class_map(_collect_ctx(df), _band_mask(), "A01", "1", "0", RULE) is None


# ── the painter and its key ───────────────────────────────────────────────────


def test_the_painter_re_inks_only_the_classed_kept_cells():
    from review.features.image_browser.review_image.renderer import _paint_size_class_outlines

    center = _band_mask()
    boundary = center > 0                      # every cell pixel counts as outline here; the colours are the point
    rgb = np.zeros((48, 48, 3), np.uint8)
    rgb[boundary] = KEPT_RED
    keep = boundary & (center != 6)            # cell 6 excluded: not a kept outline, never re-inked
    _paint_size_class_outlines(rgb, keep, center, (RULE, EXPECTED))
    large, small = _inks()
    assert _band_colours(rgb) == {1: {small}, 2: {small}, 3: {KEPT_RED}, 4: {KEPT_RED}, 5: {large}, 6: {KEPT_RED}}


def test_the_key_names_each_class_its_colour_and_its_count_and_says_when_nothing_could_be_classed():
    from review.features.image_browser.review_image.renderer import _size_class_legend_html
    from ui import theme

    kept = {n: n != 6 for n in range(1, 7)}
    key = _size_class_legend_html((RULE, EXPECTED), kept, on=True)
    large, small = (ink for ink, _name in theme.size_class_inks())
    assert f'<span style="color:{large}">■</span> Large (1)' in key       # cell 6 is not kept
    assert f'<span style="color:{small}">■</span> Small (2)' in key
    assert "in neither, the usual outline (2)" in key
    assert "no cell area" in _size_class_legend_html((RULE, None), kept, on=True)
    assert _size_class_legend_html((RULE, EXPECTED), kept, on=False) == ""
    assert _size_class_legend_html(None, kept, on=True) == ""


# ── through the real panel, in the app's order ────────────────────────────────


@pytest.fixture
def seg(qtbot, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from review.features.image_browser.review_image import renderer as rr
    from review.features.preview.panel_view import build_review_image_panel
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.mini_dataset import build_mini_dataset

    root = build_mini_dataset(tmp_path / "mini")
    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, root)
    frame = QWidget()
    QVBoxLayout(frame)
    window.setCentralWidget(frame)
    build_review_image_panel(ctx, frame)
    ctx.state.preview.selected_well = "A01"
    fov_menu = ctx.widgets.get("_review_image_fov_menu")
    fov_menu.clear()
    fov_menu.addItems(["1"])
    # The mini dataset has tables and no images: the field's pixels are handed to the real refresh here.
    arrays = {"F": np.full((48, 48), 100.0, np.float32), "M": _band_mask()}
    monkeypatch.setattr(rr, "review_resolve_image_refs", lambda _ctx, fov_raw, tp_raw: ("F", "M"))
    monkeypatch.setattr(ctx.state.images, "array", lambda ref, greyscale=True: arrays[ref])
    return ctx


def _status(ctx) -> str:
    return ctx.widgets.get("_review_image_status").text()


def test_setting_the_classes_on_cell_gating_redraws_segmentation_with_each_class_in_its_colour(seg):
    from review.features.image_browser.review_image import renderer as rr
    from review.features.qc.controller import get_qc_params, set_size_classes

    rr.refresh_review_image_ctx(seg)
    assert {c for cs in _band_colours(seg.runtime.review_image.base_rgb).values() for c in cs} == {KEPT_RED}
    assert "Size classes" not in _status(seg)

    set_size_classes(seg, replace(get_qc_params(seg), size_classes=RULE))       # what the panel's _apply calls

    large, small = _inks()
    assert _band_colours(seg.runtime.review_image.base_rgb) == {
        1: {small}, 2: {small}, 3: {KEPT_RED}, 4: {KEPT_RED}, 5: {large}, 6: {large}}
    assert "Large (2)" in _status(seg) and "Small (2)" in _status(seg)
    assert "in neither, the usual outline (2)" in _status(seg)


def test_an_incomplete_rule_draws_every_kept_cell_alike(seg):
    from review.features.image_browser.review_image import renderer as rr
    from review.features.qc.controller import get_qc_params, set_size_classes

    set_size_classes(seg, replace(get_qc_params(seg), size_classes=SizeClassRule.of(("Large", 245, None))))
    rr.refresh_review_image_ctx(seg)
    assert {c for cs in _band_colours(seg.runtime.review_image.base_rgb).values() for c in cs} == {KEPT_RED}
    assert "Size classes" not in _status(seg)


# ── the panel, on a dataset that is not a nanowell one ────────────────────────


def test_the_panel_names_the_outlines_and_not_nanowell_counts_on_a_plain_dataset():
    from review.features.qc.size_class_view import status_text

    params = SimpleNamespace(enabled=False, min_area=None, max_area=None)
    plain, problem = status_text(RULE, params, [], nanowell=False)
    assert not problem
    assert plain == ("Each kept cell is classed as Large, Small or neither; the Segmentation view outlines Large in "
                     "green and Small in blue.")
    assert "nanowell" not in status_text(SizeClassRule(), params, [], nanowell=False)[0]
    nanowell, _ = status_text(RULE, params, [], nanowell=True)
    assert nanowell.startswith("Each nanowell's kept cells are counted as Large, Small or neither")
    assert "outlines Large in green and Small in blue, as do the nanowell crops" in nanowell


def test_the_panel_is_shown_and_titled_for_a_plain_dataset(qtbot, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QWidget

    from review.domain.qc_params import QCParams
    from review.features.qc import size_class_view as scv

    parent = QWidget()
    qtbot.addWidget(parent)
    row, w = scv.build_row(parent, QCParams(), 0, 400)
    parent.show()
    ctx = SimpleNamespace(state=SimpleNamespace(dataset=SimpleNamespace(pipeline_info={})))
    monkeypatch.setattr(scv, "is_nanowell_dataset", lambda _ctx: False)
    scv.refresh(ctx, w, QCParams())
    assert row.isVisible()
    assert w["size_classes_title"].text() == scv.TITLE_PLAIN
    monkeypatch.setattr(scv, "is_nanowell_dataset", lambda _ctx: True)
    scv.refresh(ctx, w, QCParams())
    assert w["size_classes_title"].text() == scv.TITLE_NANOWELL


def test_the_class_inks_stand_apart_from_every_other_outline_the_overlay_draws():
    """The two class inks share the picture with the kept-cell red, the focused-cell yellow and, with Why removed
    on, every exclusion layer's ink: each must be told from all of them and from each other at the same floor the
    exclusion inks are held to, and be bright enough as a one-pixel stroke."""
    from review.features.image_browser.review_image.renderer import _layer_inks
    from tests.test_exclusion_layers_overlay import _MIN_LAYER_DE, _MIN_LAYER_LUM, _delta_e, _rel_luminance
    from ui import theme

    others = {**{k: _rgb(v) for k, v in _layer_inks().items()},
              "kept": KEPT_RED, "focused": _rgb(theme.Colors.focused_cell)}
    inks = _inks()
    close = [(k, name, round(_delta_e(ink, rgb), 1)) for k, ink in enumerate(inks) for name, rgb in others.items()
             if _delta_e(ink, rgb) < _MIN_LAYER_DE]
    assert not close, close
    assert _delta_e(*inks) >= _MIN_LAYER_DE
    assert all(_rel_luminance(ink) >= _MIN_LAYER_LUM - 1e-3 for ink in inks)
