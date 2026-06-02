"""trackastra engine adapter (§D / PR5e) — guarded; testable conversion."""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

pd = pytest.importorskip("pandas")

from well_viewer.features.tracking.engines import (
    all_engines, get_engine, run_tracking, EngineParams,
)
from well_viewer.features.tracking.engines.base import Detection, FrameDetections
from well_viewer.features.tracking.engines import trackastra_engine as tk


def test_registry_includes_trackastra():
    names = {e.name for e in all_engines()}
    assert "trackastra" in names
    info = get_engine("trackastra")
    assert "trackastra" in info.requires
    assert isinstance(info.available, bool)


def test_result_from_tracked_masks():
    m = np.array([[1, 1, 0, 2, 2]])
    tracked = np.array([[5, 5, 0, 6, 6]])
    frames = [
        FrameDetections(0, "0", [Detection(1, 0, 0), Detection(2, 0, 3)], image=None, mask=m),
        FrameDetections(1, "1", [Detection(1, 0, 0), Detection(2, 0, 3)], image=None, mask=m),
    ]
    res = tk.result_from_tracked_masks(frames, np.stack([tracked, tracked]))
    assert res.track_id_for(0, 1) == 5 and res.track_id_for(1, 1) == 5
    assert res.track_id_for(0, 2) == 6 and res.track_id_for(1, 2) == 6
    assert res.n_tracks == 2


def test_trackastra_guarded():
    if importlib.util.find_spec("trackastra") is not None:
        pytest.skip("trackastra installed")
    frames = [FrameDetections(0, "0", [Detection(1, 0, 0)],
                              image=np.zeros((4, 4)), mask=np.ones((4, 4)))]
    with pytest.raises(RuntimeError, match="trackastra"):
        run_tracking("trackastra", frames, EngineParams())


def test_ilp_available_is_bool():
    assert isinstance(tk.ilp_available(), bool)
    assert tk.LINKING_MODES == ("greedy_nodiv", "greedy", "ilp")


def test_build_frames_image_loader():
    from well_viewer.features.tracking.model import build_frames_from_df
    df = pd.DataFrame({"timepoint": ["0", "0"], "nucleus_id": [1, 2],
                       "shape_centroid_row": [1, 2], "shape_centroid_col": [1, 2]})
    frames, _ = build_frames_from_df(
        df, frame_image_loader=lambda tp: (np.full((3, 3), 7), np.full((3, 3), 9)))
    assert frames[0].image is not None and frames[0].mask is not None
    assert int(np.asarray(frames[0].mask)[0, 0]) == 9

    # a loader that raises → frame carries None (engine then errors clearly)
    def _bad(tp):
        raise ValueError("nope")
    frames2, _ = build_frames_from_df(df, frame_image_loader=_bad)
    assert frames2[0].image is None and frames2[0].mask is None
