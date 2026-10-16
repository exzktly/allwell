"""The per-cell scatter draws a stated sample above its cap, and counts every cell (tracker F3b).

``collect_scatter_data(max_points=...)`` pools a selection whose rows exceed the
cap (``scatter/_pooled.py``). These tests hold the pooled path to the
whole-selection path it stands in for, on a real ``AppState`` and real Parquet
caches:

* with a cap that triggers pooling but still holds every cell, the pooled result
  equals the whole-selection result key for key — groups, colours, labels,
  values, timepoints and click metadata;
* below that, the drawn cells are a proportional sample, each a real cell with
  its own values and metadata;
* the gate percentages are the whole selection's, not the sample's;
* the figure says how many cells it drew.

The fixture is built so a sample and the whole differ where it matters: the
gate holds a minority of cells, unevenly across wells, so a percentage taken
from a proportional sample would land on a different number.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from review.data import data_loading as dl
from review.domain.batch_models import ReplicateSet
from review.features.scatter import collect as scatter_collect
from review.features.scatter.collect import collect_scatter_data
from review.features.scatter.gating import compute_gate_stats

pytest.importorskip("pyarrow.parquet")

WELLS = {"A01": 900, "A02": 300, "B01": 600}
X, Y = "gfp_mean_intensity", "mcherry_mean_intensity"


def _write(tmp_path, well: str, n: int, seed: int):
    rng = np.random.default_rng(seed)
    path = tmp_path / f"{well}.csv"
    pd.DataFrame({
        "well": [well] * n,
        "fov": [str(1 + i % 3) for i in range(n)],
        "timepoint": [("0h" if i % 2 else "24h") for i in range(n)],
        # B01's late timepoint is a hair off 24 h, as real acquisition times are:
        # it belongs to 24 h only through the app's tolerant match.
        "timepoint_hours": [(0.0 if i % 2 else (24.000001 if well == "B01" else 24.0)) for i in range(n)],
        "filename": [f"{well}_f{1 + i % 3}_t{i % 2}.tif" for i in range(n)],
        "nucleus_id": [str(i) for i in range(n)],
        "area_px": np.where(np.arange(n) % 17 == 0, np.nan, 100.0),   # some cells drop out
        X: rng.lognormal(3 + seed * 0.3, 0.8, n),                      # wells differ in scale
        Y: rng.lognormal(2, 0.5, n),
        "m0": rng.normal(size=n),
    }).to_csv(path, index=False)
    return path


@pytest.fixture
def ctx(tmp_path):
    from review.core.app_runtime import AppRuntime
    from review.core.app_state import AppState
    from review.core.event_bus import EventBus
    from review.core.widget_registry import WidgetRegistry
    from review.data.well_repository import WellRepository
    from review.data.well_summary import well_summary
    from review.features.registry import attach_feature_state, build_feature_registry
    from review.viewer_context import ViewerContext

    build_feature_registry()
    state = AppState()
    attach_feature_state(state)
    state.dataset.data_dir = tmp_path
    for i, (well, n) in enumerate(WELLS.items()):
        path = _write(tmp_path, well, n, i)
        state.dataset.well_paths[well] = path
        dl.read_well_frame(path)                               # the Parquet cache
        state.dataset.summaries[well] = well_summary(path)     # the row counts the cap reads
    state.selection.selected_wells = set(WELLS)
    store: dict = {}
    c = ViewerContext(state=state, bus=EventBus(), widgets=WidgetRegistry(None, store=store),
                      runtime=AppRuntime(None, store=store))
    state.repo = WellRepository(c)
    yield c
    for pool in c.runtime.scatter.cell_pools or ():
        pool.close()


def _collect(ctx, **kw):
    ctx.state.dataset.cache.clear()
    return collect_scatter_data(ctx, X, Y, [0.0, 24.0], well_colors=[], **kw)


@pytest.fixture(params=["wells", "groups"])
def mode(request, ctx, monkeypatch):
    if request.param == "groups":
        # Auto-looking names, so each set's legend label (its member wells)
        # differs from its key, and a label taken from the wrong one shows.
        sets = [ReplicateSet("1", ["A02", "A01"]), ReplicateSet("2", ["B01"])]
        monkeypatch.setattr(scatter_collect, "plot_replicate_sets", lambda c: list(sets))
    return request.param


def _included(whole) -> int:
    return sum(len(d["x"]) for d in whole.values())


def test_a_cap_that_holds_every_cell_draws_exactly_the_whole_selection(ctx, mode):
    whole = _collect(ctx)
    n = _included(whole)
    assert n < sum(WELLS.values()), "every row is included; the cap would not be exercised between them"
    pooled = _collect(ctx, max_points=n)                 # rows exceed it, cells do not
    assert "n_total" in next(iter(pooled.values())), "the pooled path was not taken"
    assert list(pooled) == list(whole)
    for key in whole:
        for field in ("x", "y", "tp", "color", "label", "metadata", "wells"):
            assert pooled[key][field] == whole[key][field], (key, field)
        assert pooled[key]["n_total"] == pooled[key]["n_drawn"] == len(whole[key]["x"])


def test_below_the_cells_a_proportional_sample_of_real_cells_is_drawn(ctx, mode):
    whole = _collect(ctx)
    cells = {m: (x, y, t) for d in whole.values() for m, x, y, t in zip(d["metadata"], d["x"], d["y"], d["tp"])}
    pooled = _collect(ctx, max_points=400)
    assert sum(d["n_drawn"] for d in pooled.values()) == 400
    for key, data in pooled.items():
        assert data["n_total"] == len(whole[key]["x"])
        share = data["n_drawn"] / 400
        assert abs(share - data["n_total"] / _included(whole)) < 0.01, "the sample is not proportional"
        for m, x, y, t in zip(data["metadata"], data["x"], data["y"], data["tp"]):
            assert cells[m] == (x, y, t), "a drawn point is not the cell its metadata names"
        assert sum(len(w["x"]) for w in data["wells"].values()) == data["n_drawn"]


@pytest.mark.parametrize("apply_gates", [False, True])
def test_gate_percentages_count_every_cell(ctx, mode, apply_gates):
    gate = {"name": "g", "x_col": X, "y_col": Y, "x0": 60.0, "x1": 1e6, "y0": 0.0, "y1": 8.0}
    norm = {"x0": 60.0, "x1": 1e6, "y0": 0.0, "y1": 8.0}
    gates = [gate] if apply_gates else None
    whole = _collect(ctx, gates=gates)
    pooled = _collect(ctx, gates=gates, max_points=200)
    for tps in ([0.0], [0.0, 24.0]):
        expected = compute_gate_stats(whole, norm, tps)
        assert expected, "no rows; the comparison measures nothing"
        assert compute_gate_stats(pooled, norm, tps) == expected
    # The sample alone would have said something else: that is what makes the
    # population route necessary rather than decorative.
    sampled_only = {k: {kk: v for kk, v in d.items() if kk != "population"} for k, d in pooled.items()}
    assert compute_gate_stats(sampled_only, norm, [0.0, 24.0]) != compute_gate_stats(whole, norm, [0.0, 24.0])


def test_the_figure_says_it_is_a_sample(ctx):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    from review.features.scatter._annotations import SAMPLED_NOTE_TAG, note_sampled

    pooled = _collect(ctx, max_points=400)
    total = sum(d["n_total"] for d in pooled.values())
    ax = Figure().add_subplot()
    note_sampled(pooled, ax)
    notes = [t.get_text() for t in ax.texts if getattr(t, SAMPLED_NOTE_TAG, False)]
    assert notes == [f"Drawn: a sample of 400 of {total:,} cells, in proportion per group; "
                     "gate percentages count every cell"]
    ax = Figure().add_subplot()
    note_sampled(_collect(ctx), ax)
    assert not [t for t in ax.texts if getattr(t, SAMPLED_NOTE_TAG, False)]


def test_only_the_tab_asks_for_a_sample():
    """Every export keeps every cell: of the calls to ``collect_scatter_data`` in
    the app, the tab's redraw alone passes ``max_points``. Read from the syntax
    tree, so an export that starts passing one fails here."""
    import ast

    from tests._repo_scan import REPO_ROOT, parse_source

    callers: dict[str, bool] = {}
    for path in REPO_ROOT.joinpath("review").rglob("*.py"):
        for node in ast.walk(parse_source(path)):
            name = getattr(node.func, "id", getattr(node.func, "attr", None)) if isinstance(node, ast.Call) else None
            if name and name.endswith("collect_scatter_data"):     # the exports import it under an alias
                rel = str(path.relative_to(REPO_ROOT))
                callers[rel] = callers.get(rel, False) or any(k.arg == "max_points" for k in node.keywords)
    assert callers.pop("review/features/scatter/controller.py") is True, "the tab no longer caps what it draws"
    assert callers, "no export calls the collector; the scan is looking in the wrong place"
    assert not any(callers.values()), f"an export samples: {sorted(k for k, v in callers.items() if v)}"


def test_a_well_the_gate_leaves_empty_keeps_its_breakdown_entry(ctx, mode):
    # The gate starts just above A01's largest value, so A01 keeps its pre-gate
    # cells (the denominator) and draws none.
    every = _collect(ctx)
    a01_max = max(x for d in every.values() for (w, *_), x in zip(d["metadata"], d["x"]) if w == "A01")
    gate = {"name": "g", "x_col": X, "y_col": Y, "x0": a01_max * 1.01, "x1": 1e9, "y0": 0.0, "y1": 1e9}
    whole = _collect(ctx, gates=[gate])
    empty = [w for d in whole.values() for w, e in d["wells"].items() if not e["x"]]
    assert empty, "no well is left empty by the gate; the fixture does not exercise this"
    pooled = _collect(ctx, gates=[gate], max_points=_included(whole))
    for key in whole:
        assert pooled[key]["wells"] == whole[key]["wells"], key


def test_a_small_selection_releases_the_previous_pool(ctx):
    _collect(ctx, max_points=400)
    held = list(ctx.runtime.scatter.cell_pools)
    assert held
    _collect(ctx, max_points=10**9)
    assert all(p.count() == 0 for p in held) and not ctx.runtime.scatter.cell_pools


def test_a_failed_collection_releases_its_pools(ctx, monkeypatch):
    from review.features.scatter import _pooled

    made, real = [], _pooled.CellPool

    def tracked(*a, **k):
        made.append(real(*a, **k))
        return made[-1]

    calls = {"n": 0}
    real_read = _pooled.narrow_rows

    def failing(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk went away")
        return real_read(*a, **k)

    monkeypatch.setattr(_pooled, "CellPool", tracked)
    monkeypatch.setattr(_pooled, "narrow_rows", failing)
    with pytest.raises(OSError):
        _collect(ctx, max_points=400)
    assert made and all(p._con is None and not p._parts for p in made)


def test_the_pooled_path_reads_wells_narrowly(ctx, monkeypatch):
    """It must not put whole frames in the row cache, which is the point of it."""
    _collect(ctx, max_points=400)
    assert len(ctx.state.dataset.cache) == 0


def test_a_new_collection_closes_the_previous_pool(ctx):
    _collect(ctx, max_points=400)
    first = list(ctx.runtime.scatter.cell_pools)
    _collect(ctx, max_points=400)
    assert all(p._con is None and not p._parts for p in first)
