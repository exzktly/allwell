"""The Full montage window: a well's registered montage at full resolution, and the anchor set by clicking it.

Asked for alongside the anchor: "a way to render the full montage for the registered/aligned well". The fixture
is four 256-px tiles, each a different constant (so a pixel says which tile it came from), placed with an 8 µm
overlap (so the order they are drawn in shows), and one bright pixel in the first tile that only full
resolution resolves (so the level drawn shows). Values are asserted, not properties of them (CLAUDE.md §7.8).

The window tests go through the real page: the Nanowells tab built by ``build_nanowell_tab``, its **Full
montage…** button pressed, the canvas clicked.
"""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import numpy as np
import pytest
from tifffile import imwrite

from review.features.nanowell import full_montage_compute as fmc

PX = 0.5                                      # µm per image pixel
SIDE = 256                                    # tile side, px
LEFT_TOP = {"F0": (0.0, 0.0), "F1": (120.0, 0.0), "F2": (0.0, 120.0), "F3": (120.0, 120.0)}
VALUES = {"F0": 1000, "F1": 2000, "F2": 3000, "F3": 4000}
MARK = (10, 20)                               # row, column of the bright pixel in F0
SCHEMA, SEP, PHASE = "experiment:channel:well:fov:timepoint", "-", "PHASE"


def _tiles_zip(path, timepoints=("t_c", "t_a"), *, broken=()):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(".DS_Store", b"junk")
        for tp in timepoints:
            for tile, value in VALUES.items():
                if tile in broken:                                   # named like a tile, not an image
                    zf.writestr(f"exp-{PHASE}-A01-{tile}-{tp}.tif", b"not a tiff")
                    continue
                image = np.full((SIDE, SIDE), value, dtype=np.uint16)
                if tile == "F0":
                    image[MARK] = 4000
                buffer = io.BytesIO()
                imwrite(buffer, image)
                zf.writestr(f"exp-{PHASE}-A01-{tile}-{tp}.tif", buffer.getvalue())
                zf.writestr(f"__MACOSX/._exp-{PHASE}-A01-{tile}-{tp}.tif", b"appledouble")


def _block(status="ok", method="fiducial", reason=""):
    return {"status": status, "reason": reason, "origin_method": method,
            "tiles": [{"tile": t, "left_um": x, "top_um": y, "source": "registered"} for t, (x, y) in LEFT_TOP.items()]}


def _data(tmp_path, *, grids=None, in_dir=True, broken=(), tile_size=True):
    (tmp_path / "in").mkdir(exist_ok=True)
    _tiles_zip(tmp_path / "in" / "A01.zip", broken=broken)
    acq = {"pixel_size_um": {"x": PX, "y": PX}, "tile_size_px": {"x": SIDE, "y": SIDE} if tile_size else {}}
    return SimpleNamespace(grids=grids or {"A01": {"t_c": _block()}}, acquisition={"t_c": acq, "t_a": acq},
                           in_dir=tmp_path / "in" if in_dir else None, schema=SCHEMA, separator=SEP, phase_token=PHASE)


def _store(tmp_path, **kw):
    source, why = fmc.montage_source(_data(tmp_path, **kw), "A01", "t_c")
    assert source is not None, why
    return fmc.TileStore(source)


#: The well-wide stretch maps the four tiles' values to these (1st percentile 1000, 99.5th 4000).
SHADE = {"F0": 0, "F1": 85, "F2": 170, "F3": 255}
ONE_TO_ONE = ((0.0, 0.0, 248.0, 248.0), 496, 496)          # 0.5 µm per output pixel: one image pixel each


def _at(rendered, x_um, y_um, region=ONE_TO_ONE):
    (left, top, right, bottom), w, h = region
    return int(rendered.image[int((y_um - top) / (bottom - top) * h), int((x_um - left) / (right - left) * w)])


# ── the pixels ───────────────────────────────────────────────────────────────
def test_each_pixel_comes_from_the_tile_registration_placed_there_and_a_later_tile_covers_the_overlap(tmp_path):
    store = _store(tmp_path)
    store.load(list(VALUES), level=0)
    shown = store.render(*ONE_TO_ONE)
    for tile, (x, y) in LEFT_TOP.items():
        assert _at(shown, x + 60, y + 60) == SHADE[tile], tile
    assert _at(shown, 124.0, 60.0) == SHADE["F1"]           # in F0 and F1: F1 is drawn later
    assert shown.covered.all() and not shown.wants and not shown.unread


def test_full_resolution_shows_the_one_bright_pixel_and_the_held_coarse_level_averages_it_away(tmp_path):
    x, y = (MARK[1] + 0.5) * PX, (MARK[0] + 0.5) * PX
    store = _store(tmp_path)
    store.load(list(VALUES))
    coarse = store.render(*ONE_TO_ONE)
    assert _at(coarse, x, y) == 15                           # a quarter-resolution mean: 1000 + 3000/16
    assert coarse.wants == {(tile, 0) for tile in VALUES}    # and the view says it wants full resolution
    store.load(["F0"], level=0)
    fine = store.render(*ONE_TO_ONE)
    assert _at(fine, x, y) == 255 and ("F0", 0) not in fine.wants


def test_a_zoomed_out_view_needs_no_more_than_the_coarse_levels(tmp_path):
    store = _store(tmp_path)
    store.load(list(VALUES))
    region = ((0.0, 0.0, 248.0, 248.0), 62, 62)               # 4 µm, eight image pixels, per output pixel
    shown = store.render(*region)
    assert not shown.wants and _at(shown, 180.0, 180.0, region) == SHADE["F3"]


def test_fine_levels_are_let_go_least_recently_drawn_first_but_never_one_the_view_is_drawing(tmp_path, monkeypatch):
    tile_bytes = SIDE * SIDE * 2                              # one full-resolution 16-bit tile
    monkeypatch.setattr(fmc, "FINE_BYTES_KEPT", 2 * tile_bytes)
    store = _store(tmp_path)
    store.load(["F0", "F1", "F2"], level=0)                   # nothing drawn yet: F0, the oldest, goes
    shown = store.render(*ONE_TO_ONE)
    assert shown.wants == {("F0", 0)} and shown.unread == {"F3"}
    # That view drew all four, so reading what it asked for evicts none of them, although that is over the
    # budget: evicting a tile in view would have the view ask for it again, and the two would trade it for ever.
    store.load(["F0", "F3"], level=0)
    assert not store.render(*ONE_TO_ONE).wants


def test_a_view_at_half_resolution_asks_for_that_level_and_not_for_full(tmp_path):
    store = _store(tmp_path)
    store.load(list(VALUES))
    region = ((0.0, 0.0, 248.0, 248.0), 248, 248)             # 1 µm, two image pixels, per output pixel
    assert store.render(*region).wants == {(tile, 1) for tile in VALUES}
    store.load(list(VALUES), level=1)
    assert not store.render(*region).wants


def test_a_tile_never_read_is_not_drawn_and_is_asked_for(tmp_path):
    store = _store(tmp_path)
    store.load(["F1", "F2", "F3"], level=0)
    shown = store.render(*ONE_TO_ONE)
    assert shown.unread == {"F0"} and not shown.covered[0, 0] and _at(shown, 60.0, 60.0) == 0


def test_an_unreadable_tile_is_named_skipped_and_never_asked_for_again_and_the_rest_are_read(tmp_path, monkeypatch):
    """Review finding: one corrupt tile stopped the read of every tile after it, and each repaint asked for it
    again, for ever."""
    reads = []
    real = fmc.TileStore._read
    monkeypatch.setattr(fmc.TileStore, "_read", lambda self, tile: reads.append(tile.tile) or real(self, tile))
    store = _store(tmp_path, broken=("F1",))
    store.load(list(VALUES))
    assert set(store.failed) == {"F1"} and store.loaded() == 3
    shown = store.render(*ONE_TO_ONE)
    assert "F1" not in shown.unread and "F1" not in {t for t, _ in shown.wants}
    assert _at(shown, 180.0, 180.0) == 255 and _at(shown, 180.0, 60.0) == 0      # F3 drawn; F1's area empty
    store.load(["F1"], level=0)
    assert reads.count("F1") == 1


def test_a_plate_placed_from_a_scan_pattern_takes_its_tile_size_from_the_image(tmp_path):
    """Review finding: with no acquisition record the sidecar states no tile size, and the window refused every
    such plate — the very plates converted without a record."""
    source, why = fmc.montage_source(_data(tmp_path, tile_size=False), "A01", "t_c")
    assert source is not None, why
    assert source.tile_size_um == (SIDE * PX, SIDE * PX)


def test_a_read_in_flight_stops_when_the_view_moves_on(tmp_path):
    store = _store(tmp_path)
    store.cancelled = True
    store.load(list(VALUES))
    assert store.loaded() == 0


@pytest.mark.parametrize("change, says", [
    (dict(grids={"A01": {"t_c": {"status": "bad_grid"}}}), "no tile placements for A01 at t_c (bad_grid)"),
    (dict(in_dir=False), "in/ folder is not beside its results"),
])
def test_what_cannot_be_drawn_says_why(tmp_path, change, says):
    source, why = fmc.montage_source(_data(tmp_path, **change), "A01", "t_c")
    assert source is None and says in why


def test_the_view_maps_a_pixel_to_the_well_and_back():
    region = (100.0, 50.0, 300.0, 150.0)
    x, y = fmc.to_well(region, 400, 200, 100.0, 50.0)
    assert (x, y) == (150.0, 75.0) and fmc.to_view(region, 400, 200, x, y) == (100.0, 50.0)
    zoomed = fmc.zoom_region(region, 0.5, (150.0, 75.0))
    assert zoomed == (125.0, 62.5, 225.0, 112.5)             # the point under the pointer stays put


# ── the window, through the real page ────────────────────────────────────────
pytest.importorskip("PySide6")


@pytest.fixture
def montage_page(qtbot, tmp_path):
    from tests.test_nanowell_page import _page, _write_nanowell_dataset

    out = _write_nanowell_dataset(tmp_path, counts={"A01": {"t_c": [1, 2, 0, 1], "t_a": [2, 4, 3, 1]}})
    _tiles_zip(out.parent / "in" / "A01.zip")
    grid = json.loads((out / "nanowells" / "A01_grid.json").read_text())
    grid["timepoints"] = {"t_c": _block(), "t_a": _block("unaddressed", None, "No accepted fiducial, array "
                                                                              "centre, or plate-rigid prediction")}
    (out / "nanowells" / "A01_grid.json").write_text(json.dumps(grid))
    info = json.loads((out / "pipeline_info.json").read_text())
    acq = {"pixel_size_um": {"x": PX, "y": PX}, "tile_size_px": {"x": SIDE, "y": SIDE}}
    info.update(acquisition={"t_c": acq, "t_a": acq}, schema=SCHEMA, separator=SEP, phase_token=PHASE)
    (out / "pipeline_info.json").write_text(json.dumps(info))
    ctx = _page(qtbot, out)
    ctx.state.selection.selected_wells = {"A01"}
    from tests.test_nanowell_page import _loaded

    _loaded(qtbot, ctx)
    from review.features.nanowell import controller as nwc

    nwc.set_view(ctx, "array_qc")
    nwc.set_timepoint(ctx, "t_a")
    return ctx, out.parent / "in"


def _open(qtbot, ctx):
    from PySide6.QtCore import Qt

    button = ctx.widgets.get("_nw_montage_btn")
    assert button.isVisible() and button.isEnabled()
    qtbot.mouseClick(button, Qt.LeftButton)
    window = ctx.widgets.get("_nw_full_montage")
    qtbot.waitUntil(lambda: window.store is not None and window.store.loaded() == len(VALUES), timeout=20000)
    return window


def test_the_button_opens_the_well_and_timepoint_array_qc_shows_and_says_why_it_is_unaddressed(montage_page, qtbot):
    ctx, _in_dir = montage_page
    window = _open(qtbot, ctx)
    assert window.windowTitle() == "Full montage — A01" and window.timepoint == "t_a"
    text = window.status.text()
    assert text.startswith("A01 at t_a: unaddressed. No accepted fiducial")
    assert "Turn on Set anchor and click the central square" in text
    assert not window.clear_btn.isEnabled() and window.anchor_btn.isEnabled()


def test_a_click_with_set_anchor_on_writes_the_anchor_where_it_landed_and_clear_removes_it(montage_page, qtbot):
    from PySide6.QtCore import QPoint, Qt

    from util.nanowell_anchors import anchors_path, read_anchors

    ctx, in_dir = montage_page
    window = _open(qtbot, ctx)
    canvas = window.canvas
    qtbot.mouseClick(canvas, Qt.LeftButton, pos=QPoint(40, 40))           # Set anchor off: a click sets nothing
    assert not anchors_path(in_dir).exists()
    qtbot.mouseClick(window.anchor_btn, Qt.LeftButton)
    qtbot.mouseClick(canvas, Qt.LeftButton, pos=QPoint(40, 40))
    expected = fmc.to_well(canvas.region, canvas.width(), canvas.height(), 40, 40)
    anchors = read_anchors(anchors_path(in_dir))
    assert list(anchors) == [("A01", "t_a")]
    assert anchors[("A01", "t_a")].position() == pytest.approx(expected, abs=0.01)
    assert canvas.anchor == pytest.approx(expected, abs=0.01)
    assert "not used yet: Apply anchors addresses the well from it" in window.status.text()
    qtbot.mouseClick(window.clear_btn, Qt.LeftButton)
    assert not anchors_path(in_dir).exists() and canvas.anchor is None


def test_apply_anchors_runs_the_re_address_and_re_run_is_limited_to_the_wells_it_refused(montage_page, qtbot,
                                                                                         monkeypatch):
    """The real step on this page's dataset, whose rows a run did not write: it refuses A01 and says why, and
    *Re-run in Analyze…* then opens Analyze on A01 alone, forced (tracker H6)."""
    from PySide6.QtCore import Qt

    import review.core.analyze_handoff as handoff
    from util.nanowell_anchors import anchors_path, set_anchor

    ctx, in_dir = montage_page
    window = _open(qtbot, ctx)
    assert not window.apply_btn.isEnabled() and not window.rerun_btn.isEnabled()      # nothing anchored yet
    set_anchor(anchors_path(in_dir), "A01", "t_a", (60.0, 60.0))
    window._refresh_status()
    assert window.apply_btn.isEnabled() and not window.rerun_btn.isEnabled()
    reloads, calls = [], []
    monkeypatch.setattr(handoff, "reload_dataset", lambda ctx: reloads.append(ctx) or True)
    monkeypatch.setattr(handoff, "open_in_analyze", lambda ctx, **kw: calls.append(kw) or True)
    qtbot.mouseClick(window.apply_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: "Not addressed: A01 at t_a" in window.status.text(), timeout=20000)
    assert reloads == [ctx] and window.rerun_btn.isEnabled()
    qtbot.mouseClick(window.rerun_btn, Qt.LeftButton)
    # Incremental off: every well is forced anyway, and a remote nanowell run refuses Incremental.
    assert calls == [{"force_wells": ["A01"], "only_wells": ["A01"], "incremental": False}]


def test_apply_anchors_says_what_it_addressed_and_shows_the_well_as_it_now_is(montage_page, qtbot, monkeypatch):
    from PySide6.QtCore import Qt

    import pipeline.nanowell_readdress as rd
    import review.core.analyze_handoff as handoff
    from util.nanowell_anchors import anchors_path, set_anchor

    ctx, in_dir = montage_page
    window = _open(qtbot, ctx)
    set_anchor(anchors_path(in_dir), "A01", "t_a", (60.0, 60.0))
    window._refresh_status()
    grid_path = in_dir.parent / "out" / "nanowells" / "A01_grid.json"

    def readdressed(out_dir, in_dir):
        grid = json.loads(grid_path.read_text())
        grid["timepoints"]["t_a"].update(status="ok", reason="", origin_method="manual",
                                         readdressed={"origin_method": "manual"})
        grid_path.write_text(json.dumps(grid))
        # A well anchored elsewhere that the step refused: Re-run is limited to it, not to every anchored well.
        return rd.Summary(addressed={"A01/t_a": "manual"}, refused={"B03/t_c": "its rows were laid out elsewhere"})

    calls = []
    monkeypatch.setattr(rd, "readdress_dataset", readdressed)
    monkeypatch.setattr(handoff, "reload_dataset", lambda ctx: True)
    monkeypatch.setattr(handoff, "open_in_analyze", lambda ctx, **kw: calls.append(kw) or True)
    qtbot.mouseClick(window.apply_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: not window._applying and window.store is not None
                    and window.store.loaded() == len(VALUES), timeout=20000)
    qtbot.wait(200)                       # the tile reads' own status redraws, after which the outcome must remain
    assert "Addressed from the run's saved measurements" in window.status.text()
    text = window.status.text()
    assert text.startswith("A01 at t_a: ok, origin from the anchor set by hand.")
    assert "applied to the run's saved measurements" in text and "A01 at t_a (origin from the anchor set by hand)" in text
    assert "Not addressed: B03 at t_c: its rows were laid out elsewhere." in text
    qtbot.mouseClick(window.rerun_btn, Qt.LeftButton)
    assert calls == [{"force_wells": ["B03"], "only_wells": ["B03"], "incremental": False}]


def test_the_view_keeps_square_pixels_through_a_resize_before_and_after_the_user_moves_it(montage_page, qtbot):
    ctx, _in_dir = montage_page
    window = _open(qtbot, ctx)
    canvas = window.canvas

    def scales():
        left, top, right, bottom = canvas.region
        return (right - left) / canvas.width(), (bottom - top) / canvas.height()

    window.resize(900, 500)
    qtbot.waitUntil(lambda: canvas.width() > 700)
    sx, sy = scales()
    assert sx == pytest.approx(sy)                            # refitted, not stretched to the new shape
    canvas.zoom(0.5)
    before = scales()
    window.resize(600, 700)
    qtbot.waitUntil(lambda: canvas.height() > 550)
    assert scales() == pytest.approx(before)                  # the zoom the user chose survives the resize


def test_set_anchor_is_offered_only_where_an_anchor_can_change_the_result(montage_page, qtbot):
    ctx, _in_dir = montage_page
    window = _open(qtbot, ctx)
    assert window.anchor_btn.isEnabled()                      # unaddressed
    window.source = SimpleNamespace(**{**window.source.__dict__, "status": "no_lattice"})
    window._refresh_status()
    assert not window.anchor_btn.isEnabled()                  # no lattice: no origin is chosen at all


def test_moving_to_another_timepoint_stops_the_old_reads_and_closing_lets_the_tiles_go(montage_page, qtbot):
    ctx, _in_dir = montage_page
    window = _open(qtbot, ctx)
    first = window.store
    window.tp_cb.setCurrentIndex(window.tp_cb.findData("t_c"))
    assert first.cancelled and window.store is not first
    second = window.store
    window.close()
    assert second.cancelled and window.store is None and window.canvas.store is None


def test_reload_dataset_reopens_the_dataset_root_by_the_route_an_open_takes(qtbot, tmp_path):
    """After *Apply anchors* the dataset is opened again through the window's ``_load_path``, on the folder holding
    ``in/`` and ``out/`` (``dataset_root``), deferred like every open."""
    from types import SimpleNamespace

    from review.core.analyze_handoff import reload_dataset

    opened = []
    window = SimpleNamespace(_load_path=opened.append)
    ctx = SimpleNamespace(window=window, state=SimpleNamespace(dataset=SimpleNamespace(in_dir=tmp_path / "in",
                                                                                      data_dir=tmp_path / "out")))
    assert reload_dataset(ctx)
    qtbot.waitUntil(lambda: opened == [tmp_path], timeout=2000)
    assert not reload_dataset(SimpleNamespace(window=SimpleNamespace(), state=ctx.state))
