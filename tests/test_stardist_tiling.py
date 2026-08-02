"""StarDist prediction tiling — the opt-in peak-memory cap for large frames.

An untiled ``predict_instances`` on a ~4K frame allocates full-resolution
U-Net activations + the (H×W×n_rays) distance array at once (several GB), so a
few parallel workers exhaust host RAM. Tiling is OFF by default; the per-run
threshold is installed by ``_worker_init`` from the ``--stardist_tile_px`` CLI
arg (as ``StarDistParams.tile_px``); :func:`set_tile_px` sets that one field
without disturbing the detection thresholds. ``_stardist_n_tiles`` then decides
how to tile so no block exceeds that many px per side. Pure integer logic — no
TF/StarDist needed.
"""

from __future__ import annotations

import numpy as np
import pytest

sd = pytest.importorskip("pipeline.stardist")


@pytest.fixture(autouse=True)
def _reset_tile_px():
    # Isolate each test; restore the module defaults afterwards. Resets the
    # whole params value, not just tile_px — set_tile_px deliberately preserves
    # the detection thresholds, so a test that set them would otherwise leak.
    sd.set_params(None)
    yield
    sd.set_params(None)


def test_off_by_default():
    # Fresh module state (fixture set 0) → even a huge frame is untiled.
    assert sd.get_params().tile_px == 0
    assert sd._stardist_n_tiles(np.zeros((8000, 8000), dtype=np.float32)) is None


def test_small_frame_untiled_when_enabled():
    sd.set_tile_px(2048)
    assert sd._stardist_n_tiles(np.zeros((2048, 2048), dtype=np.float32)) is None
    assert sd._stardist_n_tiles(np.zeros((1000, 1500), dtype=np.float32)) is None


def test_large_frame_tiled():
    sd.set_tile_px(2048)
    # ~4K×4K → 2×2 tiles (ceil(4000/2048)=2 each side).
    assert sd._stardist_n_tiles(np.zeros((4000, 4000), dtype=np.float32)) == (2, 2)
    # Non-square: ceil per axis independently.
    assert sd._stardist_n_tiles(np.zeros((4000, 2049), dtype=np.float32)) == (2, 2)
    assert sd._stardist_n_tiles(np.zeros((6200, 3000), dtype=np.float32)) == (4, 2)


def test_smaller_threshold_tiles_more():
    sd.set_tile_px(1024)
    assert sd._stardist_n_tiles(np.zeros((4000, 4000), dtype=np.float32)) == (4, 4)


def test_set_tile_px_zero_disables():
    sd.set_tile_px(2048)
    assert sd._stardist_n_tiles(np.zeros((4000, 4000), dtype=np.float32)) == (2, 2)
    sd.set_tile_px(0)
    assert sd._stardist_n_tiles(np.zeros((4000, 4000), dtype=np.float32)) is None


@pytest.mark.parametrize("bad", [None, "x", -5])
def test_set_tile_px_bad_values_become_off(bad):
    sd.set_tile_px(bad)
    assert sd.get_params().tile_px == 0


def test_set_tile_px_leaves_the_detection_thresholds_alone():
    """Tiling is a memory knob, not a detection one — changing it must never
    silently move the thresholds the plate is segmented with."""
    sd.set_params(sd.StarDistParams(prob_thresh=0.2, nms_thresh=0.5, tile_px=0))
    sd.set_tile_px(1024)
    assert sd.get_params() == sd.StarDistParams(prob_thresh=0.2, nms_thresh=0.5,
                                                tile_px=1024)
