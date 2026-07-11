"""Unit tests for the WellRepository read facade (roadmap B2).

Pure-Python: a minimal fake ctx over a real ``DatasetState`` — no Qt, no app.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")

from well_viewer.core.dataset_state import DatasetState
from well_viewer.data import cache_projectors
from well_viewer.data.well_repository import WellRepository


def _ctx(dataset: DatasetState):
    return SimpleNamespace(state=SimpleNamespace(dataset=dataset))


def test_rows_or_none_is_cache_only():
    ds = DatasetState()
    ds.cache["A01"] = pd.DataFrame({"area_px": [1, 2]})
    repo = WellRepository(_ctx(ds))
    assert repo.rows_or_none("A01") is not None
    assert repo.rows_or_none("B02") is None  # absent → None, no load


def test_labels_and_has_data():
    ds = DatasetState()
    assert WellRepository(_ctx(ds)).has_data is False
    ds.well_paths["A01"] = "/nonexistent/A01.csv"
    ds.well_paths["B02"] = "/nonexistent/B02.csv"
    repo = WellRepository(_ctx(ds))
    assert repo.has_data is True
    assert sorted(repo.labels()) == ["A01", "B02"]


def test_all_fovs_reads_derived_cache():
    ds = DatasetState()
    ds.all_fovs_cache = ["1", "2"]
    assert WellRepository(_ctx(ds)).all_fovs() == ["1", "2"]


def test_rows_loads_and_caches_via_get_rows(tmp_path):
    csv = tmp_path / "A01.csv"
    csv.write_text("area_px,nucleus_id\n10,1\n20,2\n")
    ds = DatasetState()
    ds.well_paths["A01"] = csv
    repo = WellRepository(_ctx(ds))
    # Isolate from any globally-registered projectors so this stays a pure
    # load test (no QC/Crop projection dependency).
    saved = cache_projectors.iter_cache_projectors()
    cache_projectors.clear_cache_projectors()
    try:
        df = repo.rows("A01")
        assert len(df) == 2
        assert "A01" in ds.cache            # cached after first load
        assert repo.rows_or_none("A01") is df
    finally:
        cache_projectors.clear_cache_projectors()
        for fn in saved:
            cache_projectors.register_cache_projector(fn)
