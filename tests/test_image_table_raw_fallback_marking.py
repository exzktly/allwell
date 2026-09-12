"""A cell served the raw frame says so, and does not distort the pooled Auto LUT.

The Image Table's default render is "corrected", and a cell whose top-hat frame
the correction pass never produced falls back to the raw one
(``test_image_table_tophat_fallback.py``). That fallback makes the cell *draw*,
which is right — but it draws a different measurement. A corrected image is
``img - grey_opening(img)`` floored at zero, so its background sits near 0; the
raw frame still carries the camera offset and the background.

One LUT window cannot suit both populations. A window tuned on the corrected
cells pushes the raw ones entirely above its ceiling, where the shared clip in
``make_overlay_thumb`` saturates them — and every further LUT edit that keeps
them out of range produces a byte-identical image. The cells re-render; they
cannot change. A plate-mode grid where the correction covered part of the plate
therefore reads as a table that ignores its own brightness control on a block of
itself, with nothing on the grid to say why.

The count already reaches the Generate status line. These tests pin the two
things that were missing: the fact is on the cell (live and exported), and the
uncomparable population is kept out of the pooled Auto window that every
Auto-LUT cell of that channel shares.

Also covered: plate mode displays exactly one channel, so the LUT panel offers a
row for that one and names the others rather than showing editors that repaint
nothing.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from review.features.image_browser.image_table import image_compose as IC

CHAN = "GFP"


def _st(**over):
    st = SimpleNamespace(
        raw_fallbacks=set(), scan_misses=set(),
        label_img_corner="tl", scale_bar_img_corner="br",
        label_font_px=12, label_color="white", label_bg=True,
        channel_opts={}, auto_lut_by_channel={},
    )
    for k, v in over.items():
        setattr(st, k, v)
    return st


def _ctx(st=None):
    return SimpleNamespace(state=SimpleNamespace(image_table=st or _st()))


def _cell(well="A01", chan=CHAN, tp="0", fov="1"):
    return {"well": well, "chan": chan, "tp": tp, "fov": fov}


def _rgb(value=40):
    return np.full((64, 64, 3), value, dtype=np.uint8)


# ── which cells count as uncorrected ────────────────────────────────────────

def test_a_recorded_cell_is_a_raw_fallback():
    ctx = _ctx(_st(raw_fallbacks={("A01", "gfp", "0", "1")}))
    assert IC.is_raw_fallback(ctx, _cell()) is True


def test_a_cell_the_correction_covered_is_not():
    ctx = _ctx(_st(raw_fallbacks={("A01", "gfp", "0", "1")}))
    assert IC.is_raw_fallback(ctx, _cell(well="B02")) is False
    assert IC.is_raw_fallback(ctx, _cell(tp="5")) is False
    assert IC.is_raw_fallback(ctx, _cell(fov="2")) is False


def test_the_binary_variant_shares_its_base_channels_record():
    # _load_array keys the record by the BASE channel, because "GFP (binary)"
    # loads the same pixels; the mark has to resolve the same way or a binary
    # cell served raw would go unmarked.
    ctx = _ctx(_st(raw_fallbacks={("A01", "gfp", "0", "1")}))
    assert IC.is_raw_fallback(ctx, _cell(chan="GFP (binary)")) is True


def test_an_unset_cell_is_not_a_fallback():
    ctx = _ctx(_st(raw_fallbacks={("", "", "", "")}))
    assert IC.is_raw_fallback(ctx, _cell(well="", chan="")) is False


# ── the mark lands on the pixels ────────────────────────────────────────────

def test_an_uncorrected_cell_is_marked():
    ctx = _ctx(_st(raw_fallbacks={("A01", "gfp", "0", "1")}))
    before = _rgb()
    after = IC.mark_uncorrected(ctx, _cell(), before)
    assert not np.array_equal(before, after), "the mark must change the pixels"


def test_a_corrected_cell_is_returned_untouched():
    ctx = _ctx()
    before = _rgb()
    after = IC.mark_uncorrected(ctx, _cell(), before)
    assert after is before


def test_marking_none_stays_none():
    ctx = _ctx(_st(raw_fallbacks={("A01", "gfp", "0", "1")}))
    assert IC.mark_uncorrected(ctx, _cell(), None) is None


def test_the_mark_avoids_the_corners_the_chrome_already_uses():
    # The scale bar and the identity label claim their own corners from state;
    # a mark painted on top of either would be unreadable exactly where it
    # matters most.
    st = _st(label_img_corner="tl", scale_bar_img_corner="br")
    assert IC._mark_corner(st) in ("tr", "bl")
    st2 = _st(label_img_corner="tr", scale_bar_img_corner="bl")
    assert IC._mark_corner(st2) in ("tl", "br")


def test_the_mark_survives_alongside_the_identity_label():
    # Marking runs after draw_cell_chrome, so both are present rather than one
    # overwriting the other.
    ctx = _ctx(_st(raw_fallbacks={("A01", "gfp", "0", "1")}))
    plain = _rgb()
    labelled = IC.draw_cell_chrome(plain, label_text="A01", label_corner="tl")
    marked = IC.mark_uncorrected(ctx, _cell(), labelled)
    assert not np.array_equal(labelled, marked)
    # The label's own corner is untouched by the mark.
    assert np.array_equal(labelled[:16, :16], marked[:16, :16])


# ── the pooled Auto window excludes the uncomparable population ─────────────

def test_pooled_auto_lut_ignores_a_raw_fallback_cell(monkeypatch):
    """The whole point: a raw cell's offset must not stretch the window every
    corrected cell of that channel then shares."""
    from review.features.image_browser.image_table import controller as CT

    corrected = np.full((8, 8), 100.0, dtype=np.float32)
    raw = np.full((8, 8), 9000.0, dtype=np.float32)   # camera offset + background

    cells = [[_cell(well="A01", tp="0"), _cell(well="A02", tp="0")]]
    ctx = _ctx()
    ctx.runtime = SimpleNamespace(image_table=SimpleNamespace(cells=cells))
    ctx.state.image_table.raw_fallbacks = {("A02", "gfp", "0", "1")}

    monkeypatch.setattr(CT, "_row_display_opts", lambda *_a, **_kw: (True, False, ""))
    monkeypatch.setattr(CT, "_parse_lut", lambda *_a, **_kw: (None, None))
    monkeypatch.setattr(CT, "_find_ratio_for_channel", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        CT, "_load_array",
        lambda _ctx, _cache, well, *_a, **_kw: raw if well == "A02" else corrected,
    )

    CT._compute_auto_luts(ctx, cells, {}, None)
    assert ctx.state.image_table.auto_lut_by_channel == {"gfp": (100.0, 100.0)}, (
        "the raw cell's 9000-count background must not reach the pooled window"
    )


def test_pooled_auto_lut_still_pools_when_nothing_fell_back(monkeypatch):
    from review.features.image_browser.image_table import controller as CT

    arrays = {"A01": np.full((8, 8), 100.0, dtype=np.float32),
              "A02": np.full((8, 8), 300.0, dtype=np.float32)}
    cells = [[_cell(well="A01", tp="0"), _cell(well="A02", tp="0")]]
    ctx = _ctx()
    ctx.runtime = SimpleNamespace(image_table=SimpleNamespace(cells=cells))

    monkeypatch.setattr(CT, "_row_display_opts", lambda *_a, **_kw: (True, False, ""))
    monkeypatch.setattr(CT, "_parse_lut", lambda *_a, **_kw: (None, None))
    monkeypatch.setattr(CT, "_find_ratio_for_channel", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        CT, "_load_array",
        lambda _ctx, _cache, well, *_a, **_kw: arrays[well],
    )

    CT._compute_auto_luts(ctx, cells, {}, None)
    assert ctx.state.image_table.auto_lut_by_channel == {"gfp": (100.0, 300.0)}


# ── plate mode offers a LUT row only for the channel it displays ────────────

def _spec(channels, *, plate=False):
    from review.features.image_browser.image_table.state import (
        Axis, ChannelSpec, TableSpec,
    )
    specs = [ChannelSpec(channel=c, render=r) for c, r in channels]
    return TableSpec(rows=Axis(root="well"), cols=Axis(channels=specs), plate=plate)


def test_plate_mode_offers_one_lut_row():
    from review.features.image_browser.image_table import builder_view as BV

    spec = _spec([("GFP", "corrected"), ("DAPI", "corrected")], plate=True)
    assert list(BV._table_channels(spec)) == ["GFP"]


def test_plate_mode_names_the_channels_it_does_not_show():
    from review.features.image_browser.image_table import builder_view as BV

    spec = _spec([("GFP", "corrected"), ("DAPI", "corrected")], plate=True)
    assert BV._plate_hidden_channels(spec) == ["DAPI"]


def test_a_hidden_channel_with_no_display_range_is_named_only_once():
    from review.features.image_browser.image_table import builder_view as BV

    spec = _spec([("GFP", "corrected"), ("DAPI", "mask")], plate=True)
    assert BV._plate_hidden_channels(spec) == [], (
        "a mask channel is already named as having no display range"
    )
    assert "DAPI (mask)" in BV._lut_inert_channels(spec)


def test_outside_plate_mode_every_channel_keeps_its_row():
    from review.features.image_browser.image_table import builder_view as BV

    spec = _spec([("GFP", "corrected"), ("DAPI", "corrected")], plate=False)
    assert list(BV._table_channels(spec)) == ["GFP", "DAPI"]
    assert BV._plate_hidden_channels(spec) == []
