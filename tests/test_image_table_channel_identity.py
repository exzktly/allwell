"""A cell's channel identity must survive from the axis to the loader.

Five defects in this file's scope shared one shape: the channel a cell is drawn
from is known at the call site and then dropped, so a lookup runs against a token
the loader never used. The symptoms differ enough that they were reported as five
bugs, but each is the same omission:

* the composite column resolved its raw/corrected toggle with no channel, so
  every lazily-loaded cell fetched the raw frame whatever the channel was set to;
* ``_parse_lut`` fell back to the row's *first* cell's channel even when the
  caller had named a different one, painting a mixed-channel row with one
  channel's brightness window;
* an overlay cell and a ratio cell both asked the raw-fallback record for a token
  that is not a channel — ``⊕ OVERLAY`` and ``GFP/DAPI`` — so neither could ever
  be badged, while the fallback was counted under a channel absent from the grid;
* the pixel readout loaded an overlay cell's pixels through the same sentinel and
  resolved nothing, leaving the readout permanently blank.

The assertions are at the boundary — which channel reached the loader, whether
``mark_uncorrected`` was *called* — rather than on an internal value, because
every one of these bugs left the internal value correct and the outbound call
wrong.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.image_table import (  # noqa: E402
    controller as C,
    image_compose as IC,
)
from tests._repo_scan import parse_source

OVERLAY = IC.OVERLAY_CHAN


class _ImageView:
    """Review Image's shared per-channel brightness store."""

    def __init__(self, luts):
        self._l = {str(k).upper(): v for k, v in luts.items()}

    def get_lut(self, chan):
        return self._l.get(str(chan or "").upper())


class _Ratio:
    numerator_channel = "GFP"
    denominator_channel = "DAPI"
    epsilon = 1e-6

    def display_label(self):
        return "GFP/DAPI"


def _ctx(*, channel_opts=None, cells=None, shared=None, raw_fallbacks=(),
         ratio_metrics=()):
    st = SimpleNamespace(
        raw_fallbacks=set(raw_fallbacks), scan_misses=set(),
        label_img_corner="tl", scale_bar_img_corner="br",
        label_font_px=12, label_color="white", label_bg=True,
        channel_opts=dict(channel_opts or {}), auto_lut_by_channel={},
        cells=list(cells or []), image_cache={},
    )
    state = SimpleNamespace(
        image_table=st,
        image_view=_ImageView(shared or {}),
        channel=SimpleNamespace(ratio_metrics=list(ratio_metrics)),
        dataset=SimpleNamespace(well_labels={}, well_paths={"A01": "A01.csv"}),
    )
    return SimpleNamespace(state=state, runtime=SimpleNamespace(image_table=st))


def _cell(well="A01", chan="GFP", tp="0", fov="1"):
    return {"well": well, "chan": chan, "tp": tp, "fov": fov}


# ── item 61: a row of mixed channels is not one channel's LUT ────────────────

def test_parse_lut_uses_the_named_channels_own_shared_window():
    # Row 0 holds DAPI in column 0 and GFP in column 1, and Review Image has a
    # different brightness tuned for each. Neither has a channel_opts entry.
    cells = [[_cell(chan="DAPI"), _cell(chan="GFP")]]
    ctx = _ctx(cells=cells, shared={"DAPI": (0.0, 100.0), "GFP": (0.0, 9999.0)})
    assert IC._parse_lut(ctx, 0, "DAPI") == (0.0, 100.0)
    # The bug returned DAPI's (0.0, 100.0) here — column 0's window, applied to
    # the whole row.
    assert IC._parse_lut(ctx, 0, "GFP") == (0.0, 9999.0)


def test_parse_lut_still_falls_back_to_the_row_when_no_channel_is_named():
    # The row's representative channel remains the answer for a caller that
    # names none — that is the only question it was ever the right answer to.
    cells = [[_cell(chan="DAPI"), _cell(chan="GFP")]]
    ctx = _ctx(cells=cells, shared={"DAPI": (0.0, 100.0), "GFP": (0.0, 9999.0)})
    assert IC._parse_lut(ctx, 0, None) == (0.0, 100.0)


# ── item 60: raw vs corrected travels with the CHANNEL, not the row ──────────
#
# The finding was found in ``composite_column_rgb``, which asked
# ``_row_display_opts(ctx, r)`` with no channel — that resolves no spec at all,
# so every cell of a mixed-channel grid loaded raw whatever its channel was set
# to. That function has since been deleted (it was unreachable from the app: the
# Overlay switch went to per-cell blending in "Fix Image Table Overlay to blend
# channels into one cell", and nothing has called the column compositor since),
# so the behavioural test that pinned it had nothing left to drive.
#
# The RULE outlives the function, and every live caller obeys it. This guard is
# what the deleted test was really for, applied to the callers that can actually
# run: a ``_row_display_opts`` call with no channel argument is the defect, and
# there are eleven such call sites across three modules to keep honest.

_IMAGE_TABLE_DIR = (
    Path(__file__).resolve().parents[1]
    / "well_viewer" / "features" / "image_browser" / "image_table"
)


def test_every_render_mode_lookup_names_the_channel_it_is_for():
    import ast

    offenders = []
    for path in sorted(_IMAGE_TABLE_DIR.glob("*.py")):
        tree = parse_source(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name != "_row_display_opts":
                continue
            # (ctx, row_idx, chan) — the definition itself is the one "call"
            # shape with a default, and it is not a call at all.
            has_chan = len(node.args) >= 3 or any(
                kw.arg == "chan" for kw in node.keywords)
            if not has_chan:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "these _row_display_opts calls name no channel, so they resolve the "
        "row's representative spec instead of the cell's own and load a raw "
        f"frame for a channel set to corrected: {offenders}"
    )


# ── items 57 / 59: a cell is badged for the channels it is drawn from ───────

def test_a_ratio_cell_is_a_raw_fallback_when_an_input_fell_back():
    ctx = _ctx(raw_fallbacks={("A01", "gfp", "0", "1")}, ratio_metrics=[_Ratio()])
    ratio_cell = _cell(chan="GFP/DAPI")
    assert IC.cell_source_channels(ctx, ratio_cell) == ["GFP", "DAPI"]
    assert IC.is_raw_fallback(ctx, ratio_cell) is True


def test_a_ratio_cell_whose_inputs_were_corrected_is_not():
    ctx = _ctx(raw_fallbacks={("B02", "gfp", "0", "1")}, ratio_metrics=[_Ratio()])
    assert IC.is_raw_fallback(ctx, _cell(chan="GFP/DAPI")) is False


def test_an_overlay_cell_is_a_raw_fallback_when_a_member_fell_back():
    ctx = _ctx(raw_fallbacks={("A01", "gfp", "0", "1")})
    ov = _cell(chan=OVERLAY)
    assert IC.is_raw_fallback(ctx, ov, ["GFP", "DAPI"]) is True
    # A member set the fallback does not name is a corrected blend.
    assert IC.is_raw_fallback(ctx, ov, ["DAPI"]) is False


def test_mark_uncorrected_burns_the_badge_into_an_overlay_cell():
    ctx = _ctx(raw_fallbacks={("A01", "gfp", "0", "1")})
    before = np.full((64, 64, 3), 20, dtype=np.uint8)
    after = IC.mark_uncorrected(ctx, _cell(chan=OVERLAY), before, ["GFP", "DAPI"])
    assert after is not None
    assert bool((np.asarray(after) != before).any()), "no mark was drawn"


def test_render_cell_calls_mark_uncorrected_for_an_overlay_cell(monkeypatch):
    """The boundary assertion: the badge is only on screen if the call is made.

    ``is_raw_fallback`` answering True proves nothing on its own — the overlay
    branch of ``_render_cell`` returned without ever asking.
    """
    calls = []
    real = IC.mark_uncorrected

    def spy(ctx_, cell_, rgb, overlay_chans=None):
        calls.append((cell_.get("chan"), list(overlay_chans or [])))
        return real(ctx_, cell_, rgb, overlay_chans)

    monkeypatch.setattr(C, "mark_uncorrected", spy)
    monkeypatch.setattr(C, "_load_array",
                        lambda *a, **k: np.full((8, 8), 10, dtype=np.uint16))
    monkeypatch.setattr(C, "overlay_cell_rgb",
                        lambda *a, **k: np.full((8, 8, 3), 10, dtype=np.uint8))
    ctx = _ctx(raw_fallbacks={("A01", "gfp", "0", "1")})
    C._render_cell(ctx, {}, _cell(chan=OVERLAY), 0, None, 64, ["GFP", "DAPI"],
                   lambda rgb, w, h, lo, hi: "PIX")
    assert calls == [(OVERLAY, ["GFP", "DAPI"])]


def test_the_cell_tooltip_names_the_uncorrected_overlay():
    ctx = _ctx(raw_fallbacks={("A01", "gfp", "0", "1")})
    title = C._cell_title(ctx, _cell(chan=OVERLAY), ["GFP", "DAPI"])
    assert IC.UNCORRECTED_TOOLTIP in title


# ── item 62: the overlay cell's pixel readout resolves something ─────────────

def test_the_readout_loads_a_real_channel_for_an_overlay_cell(monkeypatch, qtbot=None):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent, QPixmap
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication([])
    loaded = []
    monkeypatch.setattr(
        C, "_hover_array",
        lambda ctx_, well, chan, tp, fov, r: (
            loaded.append(chan), np.full((8, 8), 77, dtype=np.uint16))[1])

    ctx = _ctx()
    label = QLabel()
    pm = QPixmap(64, 64)
    pm.fill()
    label.setPixmap(pm)
    label.setFixedSize(64, 64)
    label._raw_shape = (8, 8)
    label._crop = None
    C._install_cell_events(ctx, label, None, "A01", OVERLAY, "0", "1", 0,
                           readout_chan="GFP")
    app.sendEvent(label, QMouseEvent(QEvent.MouseMove, QPointF(32, 32),
                                     Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    # The bug passed the ``⊕ OVERLAY`` sentinel, which names no image, so the
    # load resolved nothing and the readout stayed empty for every overlay cell.
    assert loaded == ["GFP"]


def test_an_ordinary_cell_reads_its_own_channel(monkeypatch):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent, QPixmap
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication([])
    loaded = []
    monkeypatch.setattr(
        C, "_hover_array",
        lambda ctx_, well, chan, tp, fov, r: (
            loaded.append(chan), np.full((8, 8), 77, dtype=np.uint16))[1])

    ctx = _ctx()
    label = QLabel()
    pm = QPixmap(64, 64)
    pm.fill()
    label.setPixmap(pm)
    label.setFixedSize(64, 64)
    label._raw_shape = (8, 8)
    label._crop = None
    C._install_cell_events(ctx, label, None, "A01", "GFP", "0", "1", 0)
    app.sendEvent(label, QMouseEvent(QEvent.MouseMove, QPointF(32, 32),
                                     Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    assert loaded == ["GFP"]


# ── item 102: a pre-rendered RGB cell is not a second render path ────────────

def test_a_pre_rendered_rgb_cell_gets_the_chrome_and_the_badge(monkeypatch):
    """Live and exported cells must be the same picture.

    The pre-rendered-RGB shortcut skipped ``draw_cell_chrome`` and
    ``mark_uncorrected`` and thumbnailed through the row's LUT window, while
    ``export._build_export_figure`` sent the identical cell through the shared
    composer plus both marks at a fixed 0..255.
    """
    chrome, marks, thumbs = [], [], []
    real_mark = IC.mark_uncorrected

    monkeypatch.setattr(C, "draw_cell_chrome",
                        lambda rgb, **k: (chrome.append(k), rgb)[1])
    monkeypatch.setattr(
        C, "mark_uncorrected",
        lambda c_, cell_, rgb, ov=None: (marks.append(cell_.get("chan")),
                                         real_mark(c_, cell_, rgb, ov))[1])
    arr = np.zeros((16, 16, 3), dtype=np.uint8)
    arr[..., 0] = 200
    monkeypatch.setattr(C, "_load_array", lambda *a, **k: arr)

    ctx = _ctx(raw_fallbacks={("A01", "gfp", "0", "1")})

    def thumb(a, w, h, lo, hi):
        thumbs.append((lo, hi))
        return "PIX"

    C._render_cell(ctx, {}, _cell(chan="GFP"), 0, None, 64, [], thumb,
                   chrome_kwargs={"label_text": "A01"})
    assert len(chrome) == 1, "the scale bar / identity label was dropped"
    assert marks == ["GFP"], "the uncorrected badge was never asked for"
    assert thumbs == [(0.0, 255.0)], "a different contrast than the export's"
