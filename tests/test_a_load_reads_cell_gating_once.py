"""A real load reads none of Cell Gating's wells; showing the tab reads them once, narrowly.

Tracker R2 (``_Docs/Markdowns/REREAD_TRACKER.md``). The unit half is
``tests/test_cell_gating_reads_narrowly.py``; this is the caller's sequence
(CLAUDE.md §7.7 #1), which no unit test sees: ``load_controller`` calls
``set_ratio_metrics`` twice, the second can switch the channel and run
``recalculate_threshold``, the load runs ``recalculate_threshold`` itself, and the
statistics cache the per-well memo lives in is cleared in between. Before the fix,
a reopen of the mini dataset with a ratio and a saved gate made 36 whole-frame
reads from Cell Gating's controller alone.

The load rebuilds the tab's controls and leaves the reading owed: the CDFs and
optimizer lists are a first-frame read of every well for figures only the Cell
Gating tab draws, so they are read when that tab is shown, not by the load (the
reporter's rule: read a value only when it is needed).

The reopen runs with a row cache of one byte, so at most one well is held and
every other read reaches the disk, as on a plate larger than the budget. What the
tab shows once opened — the CDF arrays, the optimizer's timepoints and its
Min/Max fit — is held to the whole-frame computation, so a tab that never paid
its owed reading, or paid it before the ratios were in, fails on the values.
"""

from __future__ import annotations

import json
import traceback

import numpy as np
import pytest

pytest.importorskip("pyarrow.parquet")

_CG = "home/cell_gating/controller.py"


def _cell_gating_reader(stack) -> str | None:
    """The innermost Cell Gating controller function on *stack*, except the gating
    pass's own worker (its reads are R7's, and it runs on another thread)."""
    names = [f.name for f in stack if f.filename.replace("\\", "/").endswith(_CG)]
    if not names or "_run_inner" in names:
        return None
    return names[-1]


def _settle(app, qtbot) -> None:
    worker = getattr(app._ctx.runtime.cell_gating, "worker", None)
    if worker is not None:
        qtbot.waitUntil(lambda: not worker.isRunning(), timeout=30_000)
    qtbot.wait(50)


def test_a_reopen_reads_cell_gating_s_wells_once_and_narrowly(viewer_app, mini_dataset, qtbot, monkeypatch):
    from review.data import data_loading as dl
    from review.data import parquet_cache
    from review.features.home.cell_gating import controller as cg
    from review.features.home.cell_gating.gating_compute import (
        _distinct_timepoints,
        _valid_values_for_channel,
    )

    info_path = mini_dataset / "pipeline_info.json"
    info = json.loads(info_path.read_text())
    info["cell_gating"] = {"fluor_gates": {"gfp": 105.0}}
    info_path.write_text(json.dumps(info))
    (mini_dataset / "ratios.json").write_text(json.dumps(
        [{"name": "g_over_m", "numerator_channel": "gfp", "denominator_channel": "mcherry"}]))

    app = viewer_app
    app._load_path(mini_dataset)              # builds the tab and fills the Parquet cache
    _settle(app, qtbot)

    reads: list = []
    real_cached, real_full = parquet_cache.read_cached, dl.read_well_frame

    def cached(path, columns=None):
        reads.append(("parquet", _cell_gating_reader(traceback.extract_stack()), columns))
        return real_cached(path, columns=columns)

    def full(path, *a, **k):
        reads.append(("full", _cell_gating_reader(traceback.extract_stack()), None))
        return real_full(path, *a, **k)

    monkeypatch.setattr(parquet_cache, "read_cached", cached)
    monkeypatch.setattr(dl, "read_well_frame", full)
    ctx = app._ctx
    ctx.state.dataset.cache.set_budget(1)
    app._load_path(mini_dataset)
    _settle(app, qtbot)
    during_load = [(kind, who, cols) for kind, who, cols in reads if who is not None]
    assert during_load == [], f"the load read wells for Cell Gating: {during_load}"
    assert ctx.runtime.cell_gating.areas_owed, "the load neither read nor owed the reading"

    sub = ctx.runtime.home.subtabs                # the user opens Cell Gating
    sub.setCurrentIndex(next(i for i in range(sub.count()) if sub.tabText(i) == "Cell Gating"))
    _settle(app, qtbot)
    monkeypatch.undo()

    ours = [(kind, who, cols) for kind, who, cols in reads if who is not None]
    wells = sorted(ctx.state.dataset.well_paths)
    assert len(wells) == 4
    assert not [r for r in ours if r[0] == "full"], f"whole-frame reads from Cell Gating: {ours}"
    by_reader: dict = {}
    for _kind, who, cols in ours:
        assert cols is not None, f"{who} read every column"
        by_reader[who] = by_reader.get(who, 0) + 1
    assert 1 <= by_reader.get("_read_first_frames", 0) <= len(wells), by_reader
    assert by_reader.get("_well_timepoints", 0) <= 2, by_reader
    assert by_reader.get("_well_value_extent", 0) <= 2, by_reader
    assert set(by_reader) <= {"_read_first_frames", "_well_timepoints", "_well_value_extent"}, by_reader

    # What the tab shows is the whole-frame answer, with the loaded ratio in it.
    rt = ctx.runtime.cell_gating
    ratios = list(ctx.state.channel.ratio_metrics)
    assert [rm.name for rm in ratios] == ["g_over_m"]
    channels = tuple(ctx.state.channel.fluor_channels)
    ctx.state.dataset.cache.set_budget(10 ** 9)
    want_areas, want_fluor, want_ratio = [], {}, {}
    for well in cg._cell_gating_source_wells(ctx):
        found = cg._first_frame_values(dl.get_rows(ctx, well), channels, ratios)
        if found is None:
            continue
        want_areas.extend(float(a) for a in found[0])
        for k, v in found[1].items():
            want_fluor.setdefault(k, []).extend(float(x) for x in v)
        for k, v in found[2].items():
            want_ratio.setdefault(k, []).extend(float(x) for x in v)
    assert want_areas and want_ratio, "nothing to compare; an empty tab would pass"
    assert (rt.cell_areas, rt.fluor_data, rt.ratio_data) == (want_areas, want_fluor, want_ratio)
    assert not rt.areas_owed
    assert rt.status_label.text().startswith(f"Loaded {len(want_areas)} cells")

    well_a, well_b = rt.opt_well_a_combo.currentText(), rt.opt_well_b_combo.currentText()
    combo = rt.opt_timepoint_combo
    listed = [combo.itemData(i) for i in range(1, combo.count())]
    expected_tps = sorted({tp for w in (well_a, well_b) for tp in _distinct_timepoints(dl.get_rows(ctx, w))},
                          key=lambda t: (0, t[1]) if t[0] == "hours" else (1, str(t[1])))
    assert len(expected_tps) == 3 and listed == expected_tps
    key = rt.opt_channel_combo.currentData()
    timepoint = combo.currentData()
    pooled = np.concatenate([
        _valid_values_for_channel(dl.get_rows(ctx, w), key, ctx.state.channel.ratio_index, timepoint)
        for w in (well_a, well_b)])
    assert rt.opt_min_edit.text() == f"{float(pooled.min()):.6g}"
    assert rt.opt_max_edit.text() == f"{float(pooled.max()):.6g}"


def test_the_owed_reading_waits_for_the_load_s_gating_pass(viewer_app, mini_dataset, qtbot, monkeypatch):
    """The user is on Home › Cell Gating when the load ends, and the load started a
    gating pass. The tab's activation is the load's own ``on_tab_change``, which
    runs after the pass has started; reading the first frames then filters the
    held frames on the GUI thread while ``GatingWorker`` rewrites ``Included`` /
    ``Gate_Pass`` / ``Gate_On`` on the same objects from another thread. The
    reading must wait for the pass to end, and then happen — not stay owed."""
    import threading

    from review.features.home.cell_gating import controller as cg
    from review.features.home.controller import current_subtab, show_home_subtab

    info_path = mini_dataset / "pipeline_info.json"
    app = viewer_app
    app._load_path(mini_dataset)
    _settle(app, qtbot)
    ctx = app._ctx
    app._notebook.setCurrentByName("Home")
    show_home_subtab(ctx, "Cell Gating")
    assert current_subtab(ctx) == "Cell Gating"
    info = json.loads(info_path.read_text())
    info["cell_gating"] = {"fluor_gates": {"gfp": 105.0}}
    info_path.write_text(json.dumps(info))         # the saved gate starts a pass on reload

    go = threading.Event()
    real_inner = cg.GatingWorker._run_inner

    def held_pass(self):
        if threading.current_thread() is not threading.main_thread():
            assert go.wait(60), "the test never released the gating pass"
        return real_inner(self)

    monkeypatch.setattr(cg.GatingWorker, "_run_inner", held_pass)
    reads: list = []
    real_read = cg._read_first_frames

    def spied(c):
        w = c.runtime.cell_gating.worker
        reads.append(bool(w is not None and w.isRunning() and not getattr(w, "ended", False)))
        return real_read(c)

    monkeypatch.setattr(cg, "_read_first_frames", spied)
    try:
        app._load_path(mini_dataset)
        worker = ctx.runtime.cell_gating.worker
        assert worker is not None and worker.isRunning(), "the saved gate started no pass; this measures nothing"
        assert current_subtab(ctx) == "Cell Gating" and app._notebook.currentName() == "Home"
        assert True not in reads, (
            "Cell Gating read the first frames on the GUI thread while the gating pass "
            f"was rewriting them (reads, True = pass running: {reads})")
        assert ctx.runtime.cell_gating.areas_owed
    finally:
        go.set()
    _settle(app, qtbot)
    for _ in range(200):
        if not ctx.runtime.cell_gating.areas_owed:
            break
        qtbot.wait(50)
    assert not ctx.runtime.cell_gating.areas_owed, (
        "the pass ended and the reading the tab deferred was never paid")
    assert reads and True not in reads, reads
    assert ctx.runtime.cell_gating.cell_areas, "the owed reading never happened after the pass"


def test_a_pass_the_user_started_pays_the_reading_the_tab_deferred(viewer_app, mini_dataset, qtbot, monkeypatch):
    """No load hold this time: the reading is owed, the user starts a gating pass
    by editing a gate elsewhere, then opens Cell Gating while it runs. The tab
    defers the reading; with no hold to lift, the pass's own end must pay it, or
    the tab sits on "Reading cells…" until it is next re-entered."""
    import threading

    from review.features.home.cell_gating import controller as cg
    from review.features.home.controller import current_subtab, show_home_subtab

    app = viewer_app
    app._load_path(mini_dataset)
    _settle(app, qtbot)
    ctx = app._ctx
    app._notebook.setCurrentByName("Home")
    show_home_subtab(ctx, "Sample Map")
    app._load_path(mini_dataset)
    _settle(app, qtbot)
    assert ctx.runtime.cell_gating.areas_owed, "nothing owed; this measures nothing"

    go = threading.Event()
    real_inner = cg.GatingWorker._run_inner

    def held_pass(self):
        if threading.current_thread() is not threading.main_thread():
            assert go.wait(60), "the test never released the gating pass"
        return real_inner(self)

    monkeypatch.setattr(cg.GatingWorker, "_run_inner", held_pass)
    reads: list = []
    real_read = cg._read_first_frames

    def spied(c):
        w = c.runtime.cell_gating.worker
        reads.append(bool(w is not None and w.isRunning() and not getattr(w, "ended", False)))
        return real_read(c)

    monkeypatch.setattr(cg, "_read_first_frames", spied)
    try:
        ctx.state.cell_gating.fluor_gates["gfp"] = 105.0
        cg.cell_gating_start_gating_worker(ctx)
        assert ctx.runtime.cell_gating.worker.isRunning()
        assert app._app_state.plot.held_for == ""
        show_home_subtab(ctx, "Cell Gating")
        assert current_subtab(ctx) == "Cell Gating"
        assert reads == [], f"the tab read while the pass was writing: {reads}"
    finally:
        go.set()
    for _ in range(200):
        if not ctx.runtime.cell_gating.areas_owed:
            break
        qtbot.wait(50)
    assert not ctx.runtime.cell_gating.areas_owed, (
        "the pass ended and the reading the tab deferred was never paid")
    assert reads == [False], reads
    assert ctx.runtime.cell_gating.cell_areas
