"""No export, count or menu keeps every well's rows alive at once (tracker M6).

The row cache bounds what *it* holds; a caller that keeps its own references to
every well's frame puts the plate back in memory regardless. The two per-cell
exports did exactly that — a dict of every exported well's frame, held for the
length of the export, plus (in batch) a queue of a sliced copy of each. These
tests count the frames alive at the moment each chunk is cut: frames are made
fresh on every read, as a read from disk makes them, and tracked by weak
reference, so a frame anything still holds is counted and one released is not.
"""

from __future__ import annotations

import gc
import weakref
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

_WELLS = ("A01", "A02", "A03", "A04", "A05")


def _frame(well, n=4):
    return pd.DataFrame({
        "gfp_mean_intensity": np.arange(n, dtype=float) + 10.0,
        "well": [well] * n, "fov": ["1"] * n, "timepoint": ["0h"] * n,
        "timepoint_hours": [0.0] * n, "nucleus_id": [str(i + 1) for i in range(n)],
        "Included": [1] * n, "QC_Pass": [1] * n, "Crop_Pass": [1] * n,
    })


class _Reads:
    """``get_rows`` as a read from disk: a new frame each call, every one tracked."""

    def __init__(self):
        self.alive: list[weakref.ref] = []
        self.peak = 0

    def get_rows(self, ctx, well):
        frame = _frame(well)
        self.alive.append(weakref.ref(frame))
        return frame

    def count(self) -> int:
        gc.collect()
        n = sum(1 for ref in self.alive if ref() is not None)
        self.peak = max(self.peak, n)
        return n


def test_the_batch_cell_export_holds_one_well_at_a_time(tmp_path, monkeypatch):
    import review.features.batch_export._cell_rows as cr
    from review.features.export import cell_scope_compute as csc
    from review.features.export.cell_scope_compute import CellScope

    reads = _Reads()
    monkeypatch.setattr(cr, "get_rows", reads.get_rows, raising=False)
    monkeypatch.setattr("review.data.data_loading.get_rows", reads.get_rows)
    monkeypatch.setattr(cr, "figure_names_map", lambda ctx: {})
    monkeypatch.setattr(cr, "_scope_block", lambda ctx, scope, **kw: ["# test"])
    real_chunk = csc.build_chunk

    def counting_chunk(frame, *a, **k):
        reads.count()
        return real_chunk(frame, *a, **k)

    monkeypatch.setattr(csc, "build_chunk", counting_chunk)
    ctx = SimpleNamespace(
        runtime=SimpleNamespace(cell_gating=SimpleNamespace(worker=None)),
        state=SimpleNamespace(
            dataset=SimpleNamespace(well_paths={w: f"/tmp/{w}" for w in _WELLS}, rows_version=1,
                                    data_dir="/tmp/ds", crop_region=None),
            selection=SimpleNamespace(selections=[]),
            cell_gating=SimpleNamespace(thresh_frac_on={}, thresh_count_on={}, scatter_gates=None,
                                        use_scatter_gates=False, gating_choices=None,
                                        thresh_choices=None, use_scatter_thresh=False)))
    scope = CellScope(plot="line", val_col="gfp_mean_intensity", ratios={}, threshold=0.0,
                      fluor_gates={}, wells=tuple(("G", w) for w in _WELLS))

    rows = cr.write_cell_rows(ctx, scope, tmp_path / "cells.csv")

    assert rows == 4 * len(_WELLS)
    assert reads.peak == 1, f"{reads.peak} wells' frames were alive at once while a chunk was cut"


@pytest.mark.slow
def test_the_cell_export_holds_one_well_at_a_time(loaded_app, qtbot, monkeypatch, tmp_path):
    """The tab's export, through the real app, its event loop and its writer thread."""
    from PySide6.QtWidgets import QFileDialog

    from review.data import data_loading
    from review.features.export import cell_scope_compute as csc
    from review.features.export.service import export_cell_data_from
    from review.features.lineplot.cell_scope_compute import cell_scope

    ctx = loaded_app._ctx
    wells = sorted(ctx.state.dataset.well_paths)
    ctx.state.selection.selected_wells = set(wells)
    ctx.state.dataset.cache.clear()
    ctx.state.dataset.cache.set_budget(1)          # holds only the most recent well

    alive: list[weakref.ref] = []
    real_read = data_loading.read_well_frame

    def tracked_read(path):
        frame = real_read(path)
        alive.append(weakref.ref(frame))
        return frame

    monkeypatch.setattr(data_loading, "read_well_frame", tracked_read)
    peak = {"n": 0}
    real_chunk = csc.build_chunk

    def counting_chunk(frame, *a, **k):
        gc.collect()
        peak["n"] = max(peak["n"], sum(1 for ref in alive if ref() is not None))
        return real_chunk(frame, *a, **k)

    monkeypatch.setattr(csc, "build_chunk", counting_chunk)
    out = tmp_path / "cells.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    export_cell_data_from(ctx, cell_scope)
    qtbot.waitUntil(lambda: out.exists(), timeout=30000)

    assert len(alive) >= len(wells), "the wells were not read from disk; nothing was measured"
    assert peak["n"] == 1, f"{peak['n']} wells' frames were alive at once while a chunk was cut"
    body = [ln for ln in out.read_text().splitlines() if not ln.startswith("#")]
    assert len(body) > len(wells), "the export wrote no cells"


# ── the smaller holders ─────────────────────────────────────────────────────


def test_naming_the_plotted_wells_reads_no_rows(monkeypatch):
    from review.data import data_loading
    from review.features.export.cell_scope_compute import plotted_wells

    monkeypatch.setattr(data_loading, "plot_group_well_lists",
                        lambda ctx, fallback_to_all=True: [("A01", ["A01"], None), ("G", ["B01", "B02"], object())])
    monkeypatch.setattr(data_loading, "get_rows", lambda *a: pytest.fail("rows were read to name wells"))
    assert plotted_wells(SimpleNamespace()) == (("A01", "A01"), ("G", "B01"), ("G", "B02"))


def test_the_line_status_counts_from_the_summaries(monkeypatch):
    from review.data.well_summary import WellSummary
    from review.features.lineplot import controller

    monkeypatch.setattr(controller, "get_rows", lambda *a: pytest.fail("a well was read to count it"))
    ctx = SimpleNamespace(state=SimpleNamespace(dataset=SimpleNamespace(
        summaries={"A01": WellSummary(n_rows=1_104_480), "A02": WellSummary(n_rows=12)})))
    assert controller.line_status_text(ctx, [], set(), [], ["A01", "A02"]) == "2 well(s)  |  1,104,492 nuclei"


def test_a_pooled_group_is_not_held_twice(monkeypatch):
    """The generator yields the pooled copy; the list of the wells' own frames
    must already be gone, or the group is held twice while it is drawn."""
    from review.data import data_loading

    reads = _Reads()
    monkeypatch.setattr(data_loading, "get_rows", reads.get_rows)
    monkeypatch.setattr(data_loading, "plot_group_well_lists",
                        lambda ctx, fallback_to_all=True: [("G", ["A01", "A02", "A03"], None)])
    monkeypatch.setattr("review.domain.selections_model.color_for_wells", lambda names: "#000000")
    ctx = SimpleNamespace(state=SimpleNamespace(selection=SimpleNamespace(selections=[])))
    for _name, _colour, pooled in data_loading.iter_plot_groups(ctx):
        assert len(pooled) == 12
        assert reads.count() == 0, "the wells' frames are still alive beside the pooled copy"


def test_the_remembered_cdfs_stay_within_their_share_of_the_budget():
    from review.core.stats_cache_state import StatsCacheState
    from review.domain import stats_compute
    from review.data.row_cache import RowCache

    ctx = SimpleNamespace(state=SimpleNamespace(
        stats_cache=StatsCacheState(),
        dataset=SimpleNamespace(rows_version=3, cache=RowCache(budget_bytes=4 * 8_000)),
        channel=SimpleNamespace(ratio_index={})))
    cache = ctx.state.stats_cache.cache
    cache[("well_cdf", "OLD", "v", (), None, 2, None)] = np.zeros(10)       # a stale version
    for i in range(5):
        stats_compute._make_room_for_cdf(ctx, cache, 8_000)
        cache[("well_cdf", f"W{i}", "v", (), None, 3, None)] = np.zeros(1_000)   # 8,000 bytes each
    kept = [k[1] for k in cache if k[0] == "well_cdf"]
    assert "OLD" not in kept, "an array from an older rows_version can never be read again"
    assert kept == ["W4"], kept                                               # a quarter of 32,000 bytes
    cache[("well_agg", "W0")] = (1, 2)
    stats_compute._make_room_for_cdf(ctx, cache, 8_000)
    assert ("well_agg", "W0") in cache, "only CDF arrays are the memo's to drop"


def test_a_stale_cdf_goes_even_when_there_is_room():
    from review.core.stats_cache_state import StatsCacheState
    from review.data.row_cache import RowCache
    from review.domain import stats_compute

    ctx = SimpleNamespace(state=SimpleNamespace(
        stats_cache=StatsCacheState(),
        dataset=SimpleNamespace(rows_version=3, cache=RowCache(budget_bytes=10**9)),
        channel=SimpleNamespace(ratio_index={})))
    cache = ctx.state.stats_cache.cache
    cache[("well_cdf", "OLD", "v", (), None, 2, None)] = np.zeros(10)
    cache[("well_cdf", "NOW", "v", (), None, 3, None)] = np.zeros(10)
    stats_compute._make_room_for_cdf(ctx, cache, 80)
    assert [k[1] for k in cache] == ["NOW"]


def test_the_distribution_export_writes_what_the_string_version_wrote(tmp_path, monkeypatch):
    """Values are kept as floats until written; the file must be byte-identical to
    the one written from the per-cell strings it used to hold."""
    from review.data import data_loading
    from review.features.export import service as svc

    values = {"A01": np.array([1.0, -0.0, 123456.789012345, 1e-7, -3.25]), "G2": np.array([0.1234565, 7.0])}
    monkeypatch.setattr(data_loading, "iter_plot_groups",
                        lambda ctx: iter((name, "#000", pd.DataFrame()) for name in values))
    monkeypatch.setattr(data_loading, "_all_fluor_values_filtered", lambda df, **kw: values[next(it)])
    it = iter(values)
    out = tmp_path / "dist.csv"
    monkeypatch.setattr(svc, "ask_save_csv", lambda *a, **k: str(out))
    monkeypatch.setattr(svc, "get_all_fluor_gates", lambda ctx: {})
    monkeypatch.setattr(svc, "figure_names_map", lambda ctx: {})
    monkeypatch.setattr(svc, "_well_paths_keys", lambda ctx: {})
    monkeypatch.setattr(svc, "well_name_for", lambda name, *a, **k: f"name-{name}")
    tp = SimpleNamespace(currentText=lambda: "24")
    ctx = SimpleNamespace(
        state=SimpleNamespace(channel=SimpleNamespace(active_val_col="gfp_mean_intensity",
                                                      ratio_index={}, active_channel="gfp")),
        widgets=SimpleNamespace(get_or_none=lambda key: tp),
        notify=SimpleNamespace(set_status=lambda text: None), window=None)

    svc.export_distribution_data(ctx)

    expected = pd.concat([pd.DataFrame({"group": name, "well_name": f"name-{name}", "timepoint_h": "24",
                                        "gfp_mean_intensity": [f"{v:.6f}" for v in vals]})
                          for name, vals in values.items()], ignore_index=True).to_csv(index=False)
    assert out.read_text() == expected
