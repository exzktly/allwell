"""StarDist prediction tiling — the peak-memory cap for large frames.

An untiled ``predict_instances`` on a ~4K frame allocates full-resolution
U-Net activations + the (H×W×n_rays) distance array at once (several GB), so a
few parallel workers exhaust host RAM. ``_stardist_n_tiles`` decides how to tile
so no block exceeds ``_STARDIST_TILE_PX`` per side; small frames stay untiled.
Pure integer logic — no TensorFlow/StarDist needed.
"""

from __future__ import annotations

import importlib

import numpy as np
import pytest

pipeline_stardist = pytest.importorskip("pipeline.stardist")


def _reload_with_tile_px(monkeypatch, value):
    monkeypatch.setenv("ALLWELL_STARDIST_TILE_PX", str(value))
    return importlib.reload(pipeline_stardist)


def test_small_frame_is_untiled(monkeypatch):
    mod = _reload_with_tile_px(monkeypatch, 2048)
    # 2048×2048 fits within one tile → None (StarDist default, untiled).
    assert mod._stardist_n_tiles(np.zeros((2048, 2048), dtype=np.float32)) is None
    assert mod._stardist_n_tiles(np.zeros((1000, 1500), dtype=np.float32)) is None


def test_large_frame_is_tiled(monkeypatch):
    mod = _reload_with_tile_px(monkeypatch, 2048)
    # ~4K×4K → 2×2 tiles (ceil(4000/2048)=2 each side).
    assert mod._stardist_n_tiles(np.zeros((4000, 4000), dtype=np.float32)) == (2, 2)
    # Non-square: ceil per axis independently.
    assert mod._stardist_n_tiles(np.zeros((4000, 2049), dtype=np.float32)) == (2, 2)
    assert mod._stardist_n_tiles(np.zeros((6200, 3000), dtype=np.float32)) == (4, 2)


def test_smaller_tile_px_tiles_more(monkeypatch):
    mod = _reload_with_tile_px(monkeypatch, 1024)
    # Same 4K frame, smaller target → more tiles → lower peak memory.
    assert mod._stardist_n_tiles(np.zeros((4000, 4000), dtype=np.float32)) == (4, 4)


def test_zero_disables_tiling(monkeypatch):
    mod = _reload_with_tile_px(monkeypatch, 0)
    assert mod._stardist_n_tiles(np.zeros((8000, 8000), dtype=np.float32)) is None


def test_invalid_env_falls_back_to_default(monkeypatch):
    mod = _reload_with_tile_px(monkeypatch, "not-an-int")
    assert mod._STARDIST_TILE_PX == 2048


@pytest.fixture(autouse=True)
def _restore_module(monkeypatch):
    # Ensure other test files see the module at its default tile setting.
    yield
    monkeypatch.delenv("ALLWELL_STARDIST_TILE_PX", raising=False)
    importlib.reload(pipeline_stardist)
