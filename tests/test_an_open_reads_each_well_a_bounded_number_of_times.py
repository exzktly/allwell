"""A ratchet on how often one dataset open reads each well from disk.

``_Docs/Markdowns/REREAD_TRACKER.md``: on a plate larger than the row cache,
one open of an 8-well plate made 165 whole-frame reads — about twenty per well
— because every plate-wide pass asked ``get_rows`` for each well's whole frame
and the cache could not keep them. Each pass now reads narrowly and remembers a
small per-well answer. This test opens the mini dataset the way the reporter's
plate behaves (a one-byte row cache, so nothing stays held; a saved gate, which
starts a gating pass; two sample groups, so every plate-wide plot has something
to collect) and counts both kinds of read per well.

**The budgets only move down**, like the other ratchets in this suite. A new
pass that reads every well whole fails here, which is the point: it would
reproduce the report one pass at a time. If a change lowers the counts, lower
the budgets with it.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.slow

#: Whole-frame reads (``data_loading.read_well_frame``) per well, per open,
#: including the gating pass the open starts.
FULL_READS_PER_WELL_BUDGET = 1.5
#: Every Parquet read (whole or narrow, ``parquet_cache.read_cached``) per well.
PARQUET_READS_PER_WELL_BUDGET = 8.0


def _save_recipe(dataset) -> None:
    from domain.selections_format import make_selection

    info_path = dataset / "pipeline_info.json"
    info = json.loads(info_path.read_text())
    info["cell_gating"] = {"fluor_gates": {"gfp": 130.0}}
    info["sample_definitions"] = {
        "schema_version": 2, "well_labels": {}, "notes": "", "plot_groups": [],
        "selections": [make_selection(name="ctrl", wells=["A01", "A02"]),
                       make_selection(name="drug", wells=["B01", "B02"])]}
    info_path.write_text(json.dumps(info))


def _count(monkeypatch) -> dict:
    from review.data import data_loading, parquet_cache

    counts = {"full": 0, "parquet": 0}

    def wrap(module, name, kind):
        real = getattr(module, name)

        def counted(*a, **k):
            counts[kind] += 1
            return real(*a, **k)
        monkeypatch.setattr(module, name, counted)

    wrap(data_loading, "read_well_frame", "full")
    wrap(parquet_cache, "read_cached", "parquet")
    return counts


def test_one_open_reads_each_well_a_bounded_number_of_times(viewer_app, mini_dataset, qtbot, monkeypatch):
    app = viewer_app
    _save_recipe(mini_dataset)
    app._load_path(mini_dataset)                     # first open fills the Parquet caches
    worker = app._ctx.runtime.cell_gating.worker
    if worker is not None:
        worker.wait()
    qtbot.waitUntil(lambda: app._app_state.plot.held_for == "", timeout=30_000)
    app._app_state.dataset.cache.set_budget(1)
    counts = _count(monkeypatch)

    app._load_path(mini_dataset)                     # the reopen being measured
    worker = app._ctx.runtime.cell_gating.worker
    assert worker is not None, "the saved gate started no pass; this measures less than the report"
    worker.wait()
    qtbot.waitUntil(lambda: app._app_state.plot.held_for == "", timeout=30_000)

    wells = len(app._app_state.dataset.well_paths)
    full, parquet = counts["full"] / wells, counts["parquet"] / wells
    print(f"per well: {full:.2f} whole-frame reads, {parquet:.2f} Parquet reads")
    assert full <= FULL_READS_PER_WELL_BUDGET, (
        f"one open read each well whole {full:.2f} times (budget {FULL_READS_PER_WELL_BUDGET}). "
        "A pass that reads every well whole is the defect REREAD_TRACKER.md removed; read it "
        "narrowly (plate_scan.every_well(columns=...)) instead of raising the budget.")
    assert parquet <= PARQUET_READS_PER_WELL_BUDGET, (
        f"one open made {parquet:.2f} Parquet reads per well (budget {PARQUET_READS_PER_WELL_BUDGET}).")
