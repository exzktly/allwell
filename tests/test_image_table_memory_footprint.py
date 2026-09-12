"""A rendered Image Table must not pin the grid's pixels.

Decoded frames used to be held in three places at once: the bounded LRU in
``data/image_discovery``, ``state.image_table.last_render``, and a ``_raw_arr``
attribute on every rendered cell's label. The last two are unbounded and outlive
the pass, so the LRU's byte budget bounded nothing — emptying it released no
memory at all — and a grid's resident pixels grew with (cells × frame size).
At the 2048×2048 frames microscopy produces that is ~16 MB a cell, so a plate
walk is well over a gigabyte, and the failures that follow (allocations failing
under pressure) look exactly like the reported symptom: images that load for
some cells and not others, "when loading a lot of images".

The cache is now the only owner. These tests pin that: nothing else keeps a
frame alive, the label keeps a shape instead, and the readout that needed the
pixels fetches them on demand.
"""
from __future__ import annotations

import gc
import weakref
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

# A QLabel cannot be constructed without an application object.
_app = QApplication.instance() or QApplication([])


# ── The label carries a shape, not an image ─────────────────────────────────


def test_label_source_shape_prefers_the_shape_and_falls_back_to_an_array():
    from review.data.crop_tool import label_source_shape

    lbl = QLabel()
    assert label_source_shape(lbl) is None

    lbl._raw_arr = np.zeros((7, 9), dtype=np.float32)
    assert label_source_shape(lbl) == (7, 9), "labels holding an array still work"

    lbl._raw_shape = (4, 5)
    assert label_source_shape(lbl) == (4, 5), "an explicit shape wins"


# ── The pixel readout fetches on demand, holding one frame ──────────────────


def test_hover_array_fetches_on_demand_and_holds_only_the_current_cell(monkeypatch):
    from review.features.image_browser.image_table import controller as C

    loads: list = []

    def _load(ctx, cache, well, chan, tp, fov, *, use_tophat=False):
        loads.append((well, tp))
        return np.zeros((4, 4), dtype=np.float32)

    monkeypatch.setattr(C, "_load_array", _load)
    monkeypatch.setattr(C, "_row_display_opts", lambda ctx, r, chan=None: (False, False, ""))
    monkeypatch.setattr(C, "_find_ratio_for_channel", lambda ctx, chan: None)
    monkeypatch.setattr(C, "_HOVER_ARRAY", ((), None), raising=False)

    ctx = SimpleNamespace(state=SimpleNamespace(image_table=SimpleNamespace(image_cache={})))
    assert C._hover_array(ctx, "A01", "GFP", "0", "1", 0) is not None
    C._hover_array(ctx, "A01", "GFP", "0", "1", 0)
    assert loads == [("A01", "0")], "moving within one cell must not re-decode"

    C._hover_array(ctx, "A02", "GFP", "0", "1", 0)
    assert len(loads) == 2, "a different cell fetches its own frame"


# ── Nothing outside the cache keeps a frame alive ───────────────────────────


def test_a_rendered_grid_pins_no_frames(image_table_app, monkeypatch):
    """The decisive one: after a Generate, the only reference to a cell's frame
    is whatever the caches hold — the grid itself keeps none."""
    from review.features.image_browser.image_table import controller as C

    ctx = image_table_app._ctx
    frames: list = []

    def _load(ctx_, cache, well, chan, tp, fov, *, use_tophat=False):
        arr = np.zeros((8, 8), dtype=np.float32)
        frames.append(weakref.ref(arr))
        return arr

    monkeypatch.setattr(C, "_load_array", _load)
    monkeypatch.setattr(C, "_HOVER_ARRAY", ((), None), raising=False)
    ctx.state.image_table.active_wells = {"A01", "A02"}
    C.image_table_apply_dims_preset(ctx, ["well"], ["timepoint"])
    C.image_table_generate(ctx)

    assert frames, "the render pass loaded nothing to check"
    assert all(v is None for v in ctx.state.image_table.last_render.values()), \
        "the render model records which cells drew, never their pixels"

    gc.collect()
    alive = [ref for ref in frames if ref() is not None]
    assert not alive, (
        f"{len(alive)} of {len(frames)} frames are still referenced after the "
        "render pass — a grid that pins its pixels grows without bound"
    )


def test_rendered_cells_carry_a_shape_not_an_array(image_table_app, monkeypatch):
    from PySide6.QtWidgets import QLabel

    from review.features.image_browser.image_table import controller as C

    ctx = image_table_app._ctx
    monkeypatch.setattr(C, "_load_array",
                        lambda *a, **k: np.zeros((8, 8), dtype=np.float32))
    ctx.state.image_table.active_wells = {"A01"}
    C.image_table_apply_dims_preset(ctx, ["well"], ["timepoint"])
    C.image_table_generate(ctx)

    grid = ctx.runtime.image_table.render_grid
    drawn = 0
    for i in range(grid.count()):
        w = grid.itemAt(i).widget()
        if w is None or isinstance(w, QLabel):
            continue
        lbl = w.findChild(QLabel)
        if lbl is None or lbl.pixmap() is None or lbl.pixmap().isNull():
            continue
        drawn += 1
        assert not hasattr(lbl, "_raw_arr"), "a cell must not hold its frame"
        assert lbl._raw_shape == (8, 8)
    assert drawn, "expected at least one drawn cell"


# ── The cache budget is a real ceiling now, so it has to fit the machine ────


def test_decode_budget_is_bounded_and_ram_aware():
    from review.data import image_discovery as idisc

    budget = idisc._default_max_decode_bytes()
    assert 512 * (1 << 20) <= budget <= 2 * (1 << 30), (
        "the decode cache is the only owner of decoded pixels, so its budget is "
        "the app's pixel ceiling — it has to stay inside what a desktop can hold"
    )
    assert idisc.DEFAULT_MAX_DECODE_BYTES == budget
