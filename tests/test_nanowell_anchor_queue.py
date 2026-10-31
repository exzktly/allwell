"""The Array view's Anchor queue: every timepoint the run left unaddressed, shown one montage after another.

Requested: "After a nanowell run, there are usually a set of wells and timepoints that need manual fiducial
labeling. Build a tool that automatically shows the user the array view for all montages that need anchor
labeling, one after the other for faster labeling."

The first half pins which timepoints the queue holds and in what order, on fixtures built so the wrong rule
gives a different answer: grid records written in a non-plate order, timepoint labels whose alphabetical order is
the reverse of their time order, an anchor set and not yet used (not waiting), and an anchor the last run tried
and refused (waiting again). The second half drives the real page — ``build_nanowell_tab``, the band's buttons, a
real mouse click on the canvas — through a whole walk, ending with the selection it started with restored.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from review.features.nanowell import anchor_queue_compute as aqc
from util.nanowell_anchors import Anchor

REASON = "No accepted fiducial, array centre, or plate-rigid prediction"
#: The order the page's data puts these in: by acquisition time, t_c (09:00) before t_a (14:00) — the reverse of
#: their alphabetical order, so a queue that sorted the labels would show them backwards.
TIME_ORDER = {"t_c": 0, "t_a": 1}


def _by_time(tps):
    return sorted(tps, key=TIME_ORDER.__getitem__)


def _write_grid(folder, well, blocks):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{well}_grid.json").write_text(json.dumps({"scheme_version": 1, "well": well, "timepoints": blocks}))


def _plate(tmp_path):
    nw = tmp_path / "nanowells"
    # Written B01 first and each well's timepoints label-first, so neither the file order nor the label order is
    # the answer.
    _write_grid(nw, "B01", {"t_a": {"status": "unaddressed", "reason": REASON},
                            "t_c": {"status": "unaddressed", "reason": REASON}})
    _write_grid(nw, "A01", {"t_a": {"status": "unaddressed", "reason": REASON + "."},
                            "t_c": {"status": "ok", "origin_method": "fiducial"}})
    _write_grid(nw, "A02", {"t_a": {"status": "no_lattice", "reason": "No lattice was found"},
                            "t_c": {"status": "unaddressed", "reason": REASON}})
    _write_grid(nw, "C03", {"t_c": {"status": "unaddressed", "reason": "The anchor set by hand was refused",
                                    "manual_anchor_um": [12.0, 34.0]}})
    (nw / "D04_grid.json").write_text("{not json")
    return nw


def test_the_queue_holds_every_unaddressed_timepoint_without_an_anchor_in_plate_then_time_order(tmp_path):
    grids = aqc.grid_statuses(_plate(tmp_path))
    assert sorted(grids) == ["A01", "A02", "B01", "C03"]            # the unreadable record contributes nothing
    anchors = {("A02", "t_c"): Anchor("A02", "t_c", 5.0, 6.0),      # set, no run has used it: waits for a re-run
               ("C03", "t_c"): Anchor("C03", "t_c", 12.0, 34.0)}    # the very anchor the last run refused
    items, pending = aqc.waiting_items(grids, anchors, order=_by_time)
    assert [(i.well, i.timepoint, i.refused) for i in items] == [
        ("A01", "t_a", False), ("B01", "t_c", False), ("B01", "t_a", False), ("C03", "t_c", True)]
    assert pending == {("A02", "t_c")}
    assert items[0].reason == REASON + "."


def test_an_anchor_moved_since_the_run_refused_it_is_not_asked_for_again(tmp_path):
    grids = aqc.grid_statuses(_plate(tmp_path))
    items, pending = aqc.waiting_items(grids, {("C03", "t_c"): Anchor("C03", "t_c", 40.0, 34.0)}, order=_by_time)
    assert ("C03", "t_c") not in [(i.well, i.timepoint) for i in items]
    assert pending == {("C03", "t_c")}


def test_the_queue_says_where_it_is_and_what_it_did():
    items = [aqc.QueueItem("A01", "t_a"), aqc.QueueItem("B01", "t_c"), aqc.QueueItem("B01", "t_a")]
    queue = aqc.AnchorQueue(items)
    assert queue.position_text() == "1 of 3 · A01 at t_a"
    queue.anchored.add(("A01", "t_a"))
    queue.index = 2
    assert queue.position_text() == "3 of 3 · B01 at t_a"
    assert queue.skipped() == 1                                     # B01 at t_c, passed over
    queue.index = 3
    assert queue.finished and queue.current is None
    assert queue.summary() == ("Anchor queue finished: 1 of 3 timepoint(s) anchored, 2 skipped. "
                               "Re-run anchored wells… to address them from the anchors.")
    assert aqc.AnchorQueue(items[:1], index=1).summary() == ("Anchor queue finished: 0 of 1 timepoint(s) anchored, "
                                                            "1 skipped.")


def test_one_per_well_keeps_each_well_s_first_timepoint_and_never_walks_back():
    items = [aqc.QueueItem("A01", "t_a"), aqc.QueueItem("B01", "t_c"), aqc.QueueItem("B01", "t_a"),
             aqc.QueueItem("C03", "t_c")]
    queue = aqc.AnchorQueue(list(items), index=2)                   # on B01 at t_a, the well's second timepoint
    queue.set_per_well(True)
    assert [(i.well, i.timepoint) for i in queue.items] == [("A01", "t_a"), ("B01", "t_c"), ("C03", "t_c")]
    assert queue.current == items[3]                                # B01 at t_a is gone: on to C03, not back to B01
    queue.set_per_well(False)
    assert queue.items == items and queue.current == items[3]       # the item shown stays shown
    queue.index = 1
    queue.set_per_well(True)
    assert queue.index == 1 and queue.current == items[1]
    # A well with an anchor already waiting for a re-run needs no click of its own.
    waiting = aqc.AnchorQueue(list(items), pending_rerun=frozenset({("B01", "t_x")}))
    waiting.set_per_well(True)
    assert [(i.well, i.timepoint) for i in waiting.items] == [("A01", "t_a"), ("C03", "t_c")]


# ── the real page ────────────────────────────────────────────────────────────

pytest.importorskip("PySide6")

#: The centre of the four placed tiles (``test_nanowell_full_montage.LEFT_TOP``: 128 µm tiles at 0 and 120 µm), which
#: is where each queue step opens.
MONTAGE_CENTRE = (124.0, 124.0)
#: Where the tests click to set an anchor: near the centre, inside the 10x view each step opens at.
AT = (121.0, 127.0)


def _tiles_for(in_dir, well):
    """``in/<well>.zip`` holding the four tiles ``test_nanowell_full_montage`` places, named for *well*."""
    from tests.test_nanowell_full_montage import _tiles_zip

    source = in_dir / "_A01_tiles.zip"
    _tiles_zip(source)
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(in_dir / f"{well}.zip", "w") as dst:
        for info in src.infolist():
            dst.writestr(info.filename.replace("-A01-", f"-{well}-"), src.read(info))
    source.unlink()


def _dataset_with_unaddressed(tmp_path):
    from tests.test_nanowell_full_montage import PHASE, PX, SCHEMA, SEP, SIDE, _block as grid_block
    from tests.test_nanowell_page import _write_nanowell_dataset

    out = _write_nanowell_dataset(tmp_path)                         # A01, A02, B01 at t_c and t_a
    in_dir = out.parent / "in"
    in_dir.mkdir(exist_ok=True)
    statuses = {"A01": {"t_c": grid_block(), "t_a": grid_block("unaddressed", None, REASON)},
                "A02": {"t_c": grid_block(), "t_a": grid_block()},
                # t_a written first: the record's order is not the time order the queue must follow.
                "B01": {"t_a": grid_block("unaddressed", None, REASON),
                        "t_c": grid_block("unaddressed", None, REASON)}}
    for well, blocks in statuses.items():
        grid = json.loads((out / "nanowells" / f"{well}_grid.json").read_text())
        grid["timepoints"] = blocks
        (out / "nanowells" / f"{well}_grid.json").write_text(json.dumps(grid))
        _tiles_for(in_dir, well)
    info = json.loads((out / "pipeline_info.json").read_text())
    acq = {"pixel_size_um": {"x": PX, "y": PX}, "tile_size_px": {"x": SIDE, "y": SIDE}}
    info.update(acquisition={"t_c": acq, "t_a": acq}, schema=SCHEMA, separator=SEP, phase_token=PHASE)
    (out / "pipeline_info.json").write_text(json.dumps(info))
    return out


def _assert_opened_zoomed_on_the_montage_centre(st):
    """Requested: "by default zoom to the center of the registered whole-well montage at 10x bc the anchor will
    likely be visible there". The readout's own factor, and the centre of what the axes show."""
    from review.features.nanowell import _anchor_queue as aq
    from review.features.nanowell import controller as nwc

    left, top, right, bottom = st.drawn.region
    assert nwc.array_zoom_factor(st.drawn) == pytest.approx(aq.OPEN_ZOOM)
    assert ((left + right) / 2, (top + bottom) / 2) == pytest.approx(MONTAGE_CENTRE, abs=0.5)


def test_the_queue_walks_every_unaddressed_montage_and_a_click_moves_it_on(qtbot, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from review.features.nanowell import controller as nwc
    from tests.test_an_unaddressed_nanowell_timepoint_offers_its_anchor import _Rail
    from tests.test_nanowell_full_montage import _widget_point
    from tests.test_nanowell_page import _page
    from util.nanowell_anchors import anchors_path, read_anchors

    out = _dataset_with_unaddressed(tmp_path)
    ctx = _page(qtbot, out)
    ctx.window._notebook = _Rail(ctx)
    ctx.state.selection.selected_wells = {"A02"}
    st = ctx.state.feature_slice("nanowell")
    nwc.nw_on_activate(ctx)
    qtbot.waitUntil(lambda: st.metrics is not None and not st.computing, timeout=20000)

    def shown():
        qtbot.waitUntil(lambda: st.anchor_pending is None and not st.computing and st.drawn is not None
                        and st.anchor_mode, timeout=20000)
        return nwc.page_well(ctx), nwc.page_timepoint(ctx, nwc.page_well(ctx))

    def click():
        canvas, at = _widget_point(ctx, st, *AT)
        QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, at)

    queue_btn = ctx.widgets.get("_nw_queue_btn")
    assert queue_btn.isEnabled()
    queue_btn.click()
    assert shown() == ("A01", "t_a")
    _assert_opened_zoomed_on_the_montage_centre(st)
    assert set(ctx.state.selection.selected_wells) == {"A02", "A01"}
    assert ctx.widgets.get("_nw_queue_lbl").text() == "1 of 3 · A01 at t_a"
    assert not ctx.widgets.get("_nw_queue_back_btn").isEnabled()

    click()                                                          # anchors A01 at t_a, then moves on by itself
    qtbot.waitUntil(lambda: st.anchor_queue.index == 1, timeout=5000)
    assert shown() == ("B01", "t_c")                                 # t_c before t_a: time order, not label order
    assert set(ctx.state.selection.selected_wells) == {"A02", "B01"}  # A01 walked past, not left selected
    assert ctx.widgets.get("_nw_queue_lbl").text() == "2 of 3 · B01 at t_c"

    st.region = None                                                 # Fit, as the band's button does
    nwc.redraw(ctx)
    ctx.widgets.get("_nw_queue_skip_btn").click()
    assert shown() == ("B01", "t_a")
    _assert_opened_zoomed_on_the_montage_centre(st)                  # every step opens zoomed, not as left
    ctx.widgets.get("_nw_queue_back_btn").click()
    assert shown() == ("B01", "t_c")
    click()
    qtbot.waitUntil(lambda: st.anchor_queue.index == 2, timeout=5000)
    assert shown() == ("B01", "t_a")

    ctx.widgets.get("_nw_queue_stop_btn").click()
    assert st.anchor_queue is None and not st.anchor_mode
    assert set(ctx.state.selection.selected_wells) == {"A02"}
    assert st.array_message == ("Anchor queue finished: 2 of 3 timepoint(s) anchored, 1 skipped. "
                                "Re-run anchored wells… to address them from the anchors.")
    anchors = read_anchors(anchors_path(out.parent / "in"))
    assert sorted(anchors) == [("A01", "t_a"), ("B01", "t_c")]
    assert anchors[("B01", "t_c")].position() == pytest.approx(AT, abs=1.0)


def test_with_nothing_waiting_the_queue_says_so_and_changes_nothing(qtbot, tmp_path):
    from review.features.nanowell import _anchor_queue as aq
    from review.features.nanowell import controller as nwc
    from tests.test_nanowell_page import _page, _write_nanowell_dataset

    out = _write_nanowell_dataset(tmp_path)
    (out.parent / "in").mkdir(exist_ok=True)
    ctx = _page(qtbot, out)
    ctx.state.selection.selected_wells = {"A02"}
    st = ctx.state.feature_slice("nanowell")
    nwc.nw_on_activate(ctx)
    qtbot.waitUntil(lambda: st.metrics is not None and not st.computing, timeout=20000)
    assert aq.start(ctx) is False
    assert st.anchor_queue is None
    assert st.array_message == "No nanowell timepoint is waiting for an anchor."
    assert set(ctx.state.selection.selected_wells) == {"A02"}


def test_one_per_well_walks_each_well_s_first_timepoint_and_ends_by_itself(qtbot, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from review.features.nanowell import controller as nwc
    from tests.test_an_unaddressed_nanowell_timepoint_offers_its_anchor import _Rail
    from tests.test_nanowell_full_montage import _widget_point
    from tests.test_nanowell_page import _page
    from util.nanowell_anchors import anchors_path, read_anchors

    out = _dataset_with_unaddressed(tmp_path)
    ctx = _page(qtbot, out)
    ctx.window._notebook = _Rail(ctx)
    ctx.state.selection.selected_wells = {"A02"}
    st = ctx.state.feature_slice("nanowell")
    nwc.nw_on_activate(ctx)
    qtbot.waitUntil(lambda: st.metrics is not None and not st.computing, timeout=20000)

    def shown():
        qtbot.waitUntil(lambda: st.anchor_pending is None and not st.computing and st.drawn is not None
                        and st.anchor_mode, timeout=20000)
        return nwc.page_well(ctx), nwc.page_timepoint(ctx, nwc.page_well(ctx))

    def click():
        canvas, at = _widget_point(ctx, st, *AT)
        QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, at)

    ctx.widgets.get("_nw_queue_btn").click()
    assert shown() == ("A01", "t_a")
    per_well = ctx.widgets.get("_nw_queue_per_well_btn")
    assert not per_well.isChecked()
    per_well.click()
    assert st.anchor_queue_per_well and st.anchor_queue.per_well
    assert ctx.widgets.get("_nw_queue_lbl").text() == "1 of 2 · A01 at t_a"
    click()
    qtbot.waitUntil(lambda: st.anchor_queue.index == 1, timeout=5000)
    assert shown() == ("B01", "t_c")                                 # B01's first timepoint in time, not t_a
    click()
    qtbot.waitUntil(lambda: st.anchor_queue is None, timeout=5000)   # past the last well, the walk ends itself
    assert set(ctx.state.selection.selected_wells) == {"A02"}
    assert st.array_message == ("Anchor queue finished: 2 of 2 timepoint(s) anchored. "
                                "Re-run anchored wells… to address them from the anchors.")
    assert sorted(read_anchors(anchors_path(out.parent / "in"))) == [("A01", "t_a"), ("B01", "t_c")]

    ctx.widgets.get("_nw_queue_btn").click()                         # the next walk starts one per well too
    assert st.anchor_queue is None and st.array_message == (
        "No nanowell timepoint is waiting for an anchor (One per well is on: 1 more wait in wells that already have "
        "one). 2 timepoint(s) have an anchor that no run has used yet: Re-run anchored wells… addresses them.")
