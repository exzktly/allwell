"""The Image Table must degrade cell by cell, and never substitute a picture.

Three failure modes used to turn one bad cell — or one bad moment on the source
drive — into a table that looked finished but wasn't:

* an exception anywhere in a Generate pass abandoned every remaining cell, and
  because the grid is cleared first, the user was left with a half-drawn (or
  entirely empty) table and no message;
* a scan that came up empty was written into the cross-Generate image cache, so
  a drive that stalled for a moment left those wells reading "(no image)" for
  the rest of the session, whatever happened afterwards;
* a mask / threshold cell whose segmentation mask couldn't be resolved fell
  through to the plain intensity image — indistinguishable from a cell with no
  signal above threshold, and the reason a threshold view came out half
  switched (the mask lookup resolved fewer keys than the image lookup, so some
  cells changed and their neighbours did not).

The pure-seam tests come first; the two that need a real grid use the full app.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.image_table import image_compose as IC


class _Ref:
    """Stand-in ImgRef — only its identity matters to these tests."""

    def __init__(self, name: str) -> None:
        self.name = name


def _ctx(**dataset):
    ds = dict(data_dir=None, in_dir=None, pipeline_info={}, fov_tp_extractor=None)
    ds.update(dataset)
    return SimpleNamespace(
        state=SimpleNamespace(
            dataset=SimpleNamespace(**ds),
            image_table=SimpleNamespace(scan_misses=set()),
        ),
    )


# ── An empty scan must not be cached across passes ───────────────────────────


def test_empty_scan_is_not_pinned_and_retries_on_the_next_pass(monkeypatch):
    """A well/channel that scans empty is retried by the next Generate.

    Caching the empty dict in ``image_cache`` (which survives until the dataset
    is reloaded) is what made a momentary read failure permanent.
    """
    import well_viewer.data.image_discovery as idisc

    calls = {"n": 0}
    ref = _Ref("A01_gfp.tif")

    def _scan(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:          # the drive is stalled
            return {}, {}, {}, {}
        return {("1", "0"): ref}, {}, {("1", "0"): _Ref("mask.tif")}, {}

    monkeypatch.setattr(idisc, "find_well_images_and_masks", _scan)

    ctx = _ctx()
    cache: dict = {}
    assert IC._load_well_channel(ctx, cache, "A01", "gfp") == {}
    assert ("A01", "gfp", "raw") not in cache, "an empty scan must not be cached"

    # Within the same pass the miss is remembered, so one Generate scans once.
    assert IC._load_well_channel(ctx, cache, "A01", "gfp") == {}
    assert calls["n"] == 1

    IC.reset_scan_misses(ctx)        # the next Generate starts a new pass
    assert IC._load_well_channel(ctx, cache, "A01", "gfp") == {("1", "0"): ref}
    assert calls["n"] == 2


def test_empty_mask_scan_does_not_pin_the_well(monkeypatch):
    """One channel scanning up no masks must not write masks off for the well."""
    import well_viewer.data.image_discovery as idisc

    mask_ref = _Ref("A01_labels.tif")
    results = {
        "gfp": ({("1", "0"): _Ref("gfp.tif")}, {}, {}, {}),
        "dapi": ({("1", "0"): _Ref("dapi.tif")}, {}, {("1", "0"): mask_ref}, {}),
    }
    monkeypatch.setattr(idisc, "find_well_images_and_masks",
                        lambda *_a, fluor_token="", **_kw: results[fluor_token])

    ctx = _ctx()
    cache: dict = {}
    IC._load_well_channel(ctx, cache, "A01", "gfp")
    assert ("A01", "__mask__", "raw") not in cache
    IC._load_well_channel(ctx, cache, "A01", "dapi")
    assert cache[("A01", "__mask__", "raw")] == {("1", "0"): mask_ref}


# ── The mask lookup must resolve exactly what the image lookup resolves ──────


def test_mask_resolves_through_the_same_matcher_as_the_image(monkeypatch):
    """``"T01"`` vs ``"1"`` resolves for the image, so it must for the mask too.

    When it didn't, the cell showed its image but silently lost the binary /
    boundary render layered on top of it.
    """
    mask = np.zeros((4, 4), dtype=np.int32)
    mask[1:3, 1:3] = 5
    monkeypatch.setattr("well_viewer.data.image_discovery.open_imgref_as_array",
                        lambda ref, greyscale=False: mask)

    images = {("1", "T01"): _Ref("m.tif")}
    cache = {("A01", "__mask__", "raw"): images}
    # The numeric tier is what rescues this pairing on the image side.
    assert IC._match_image_ref(images, "1", "1")[1] == "numeric"
    out = IC._load_mask_array(None, cache, "A01", "1", "1")
    assert out is not None and int(out.max()) == 5


def test_mask_refuses_an_ambiguous_empty_fov(monkeypatch):
    """An empty FOV spanning several fields must resolve to no mask, not to an
    arbitrary one — the image lookup already refuses it, and a mismatched mask
    draws one field's outlines over another field's pixels."""
    monkeypatch.setattr("well_viewer.data.image_discovery.open_imgref_as_array",
                        lambda ref, greyscale=False: np.ones((4, 4), dtype=np.int32))

    images = {("1", "0"): _Ref("f1.tif"), ("2", "0"): _Ref("f2.tif")}
    cache = {("A01", "__mask__", "raw"): images}
    assert IC._match_image_ref(images, "", "0") == (None, "ambiguous_fov")
    assert IC._load_mask_array(None, cache, "A01", "0", "") is None


# ── A binary render never falls back to the intensity image ─────────────────


def test_binary_cell_without_a_mask_renders_nothing(monkeypatch):
    """``cell_display_rgb`` reports "can't render" rather than showing the
    intensity image the user did not ask for."""
    monkeypatch.setattr(IC, "_row_display_opts",
                        lambda ctx, r, chan=None: (False, False, "threshold"))
    monkeypatch.setattr(IC, "_export_binary_mask", lambda *a, **k: None)

    cell = {"well": "A01", "chan": "GFP", "tp": "0", "fov": "1"}
    arr = np.arange(16, dtype=np.float32).reshape(4, 4)
    assert IC.cell_display_rgb(_ctx(), {}, cell, 0, None, arr) is None


def test_binary_cell_with_a_mask_still_renders(monkeypatch):
    """The guard above must not touch the case where the mask does resolve."""
    monkeypatch.setattr(IC, "_row_display_opts",
                        lambda ctx, r, chan=None: (False, False, "threshold"))
    bm = np.zeros((4, 4), dtype=np.uint8)
    bm[1:3, 1:3] = 1
    monkeypatch.setattr(IC, "_export_binary_mask", lambda *a, **k: bm)

    cell = {"well": "A01", "chan": "GFP", "tp": "0", "fov": "1"}
    arr = np.arange(16, dtype=np.float32).reshape(4, 4)
    rgb = IC.cell_display_rgb(_ctx(), {}, cell, 0, None, arr)
    assert rgb is not None
    assert set(np.unique(rgb).tolist()) == {0, 255}


# ── The prefetch must not write off a decode that failed ────────────────────


def test_prefetch_marks_only_the_keys_that_decoded(monkeypatch):
    """A failed warm stays unmarked so the next Generate tries it again."""
    from well_viewer.features.image_browser.image_table import controller as C

    monkeypatch.setattr(C, "_row_display_opts", lambda ctx, r, chan=None: (False, False, ""))
    monkeypatch.setattr(C, "_find_ratio_for_channel", lambda ctx, chan: None)
    monkeypatch.setattr(C, "_is_nuc_seg", lambda chan: False)

    def _load(ctx, cache, well, chan, tp, fov, *, use_tophat=False):
        if well == "A02":
            raise OSError("drive stalled")
        return np.zeros((2, 2), dtype=np.float32)

    monkeypatch.setattr(C, "_load_array", _load)

    warmed: set = set()
    ctx = SimpleNamespace(state=SimpleNamespace(
        image_table=SimpleNamespace(prefetched_cells=warmed)))
    cells = [[{"well": "A01", "chan": "GFP", "tp": "0", "fov": "1"},
              {"well": "A02", "chan": "GFP", "tp": "0", "fov": "1"}]]
    C._prefetch_cell_arrays(ctx, cells, {})

    assert ("A01", "gfp", "raw", "0", "1") in warmed
    assert ("A02", "gfp", "raw", "0", "1") not in warmed


# ── The Generate pass survives a cell that raises ───────────────────────────


def _image_labels(grid):
    """Every rendered cell's image QLabel, in grid order."""
    from PySide6.QtWidgets import QLabel

    out = []
    for i in range(grid.count()):
        w = grid.itemAt(i).widget()
        if w is None or isinstance(w, QLabel):
            continue          # facet axis labels are added directly
        lbl = w.findChild(QLabel)
        if lbl is not None:
            out.append(lbl)
    return out


def test_one_failing_cell_does_not_abandon_the_rest_of_the_grid(
        image_table_app, monkeypatch):
    from well_viewer.features.image_browser.image_table import controller as C

    ctx = image_table_app._ctx

    def _load(ctx_, cache, well, chan, tp, fov, *, use_tophat=False):
        if well == "A02":
            raise OSError("simulated transient read error")
        return np.linspace(0, 255, 64, dtype=np.float32).reshape(8, 8)

    monkeypatch.setattr(C, "_load_array", _load)
    ctx.state.image_table.active_wells = {"A01", "A02"}
    C.image_table_apply_dims_preset(ctx, ["well"], ["timepoint"])
    C.image_table_generate(ctx)   # must not raise

    labels = _image_labels(ctx.runtime.image_table.render_grid)
    drawn = [lb for lb in labels if lb.pixmap() is not None and not lb.pixmap().isNull()]
    failed = [lb for lb in labels if lb.text() == "(render failed)"]
    assert drawn, "the readable wells must still render"
    assert failed, "the unreadable well must say so, not vanish"
    assert len(drawn) + len(failed) == len(labels)


def test_a_pre_pass_failure_still_draws_the_table(image_table_app, monkeypatch):
    """The brightness-pooling pass runs before any cell renders — if it raises,
    the table must still draw (the grid has already been cleared by then)."""
    from well_viewer.features.image_browser.image_table import controller as C

    ctx = image_table_app._ctx
    monkeypatch.setattr(C, "_load_array",
                        lambda *a, **k: np.zeros((8, 8), dtype=np.float32))

    def _boom(*_a, **_kw):
        raise ValueError("simulated pooling failure")

    monkeypatch.setattr(C, "_compute_auto_luts", _boom)
    ctx.state.image_table.active_wells = {"A01", "A02"}
    C.image_table_apply_dims_preset(ctx, ["well"], ["timepoint"])
    C.image_table_generate(ctx)   # must not raise

    labels = _image_labels(ctx.runtime.image_table.render_grid)
    assert any(lb.pixmap() is not None and not lb.pixmap().isNull() for lb in labels)
