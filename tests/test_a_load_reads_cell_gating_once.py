"""A real load reads Cell Gating's wells once, narrowly, after the last input is final.

Tracker R2 (``_Docs/Markdowns/REREAD_TRACKER.md``). The unit half is
``tests/test_cell_gating_reads_narrowly.py``; this is the caller's sequence
(CLAUDE.md §7.7 #1), which no unit test sees: ``load_controller`` calls
``set_ratio_metrics`` twice, the second can switch the channel and run
``recalculate_threshold``, the load runs ``recalculate_threshold`` itself, and the
statistics cache the per-well memo lives in is cleared in between. Before the fix,
a reopen of the mini dataset with a ratio and a saved gate made 36 whole-frame
reads from Cell Gating's controller alone.

The reopen runs with a row cache of one byte, so at most one well is held and
every other read reaches the disk, as on a plate larger than the budget. What the
tab shows afterwards — the CDF arrays, the optimizer's timepoints and its Min/Max
fit — is held to the whole-frame computation, so a load that never paid its owed
reading, or paid it before the ratios were in, fails on the values.
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
