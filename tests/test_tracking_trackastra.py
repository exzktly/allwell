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


def _stub_trackastra(monkeypatch, *, track_return, graph_to_ctc_masks=None):
    """Inject a fake ``trackastra`` package so ``run()`` can be exercised without
    the real (heavy, GPU) dependency. ``track_return`` is whatever the fake
    model's ``track`` yields; ``graph_to_ctc_masks`` is the relabeled mask stack
    the fake ``graph_to_ctc`` returns alongside a dummy track DataFrame."""
    import sys
    import types

    calls: dict = {}

    class _FakeModel:
        @classmethod
        def from_pretrained(cls, name, device="automatic"):
            calls["model"] = (name, device)
            return cls()

        def track(self, imgs, masks, mode="greedy"):
            calls["track"] = {"mode": mode, "n": len(imgs)}
            return track_return

    model_mod = types.ModuleType("trackastra.model")
    model_mod.Trackastra = _FakeModel
    tracking_mod = types.ModuleType("trackastra.tracking")

    def _graph_to_ctc(graph, masks_original, outdir=None, **_kw):
        calls["graph_to_ctc"] = {"outdir": outdir}
        return pd.DataFrame({"track_id": [1]}), graph_to_ctc_masks

    tracking_mod.graph_to_ctc = _graph_to_ctc
    pkg = types.ModuleType("trackastra")
    monkeypatch.setitem(sys.modules, "trackastra", pkg)
    monkeypatch.setitem(sys.modules, "trackastra.model", model_mod)
    monkeypatch.setitem(sys.modules, "trackastra.tracking", tracking_mod)
    return calls


def test_run_single_graph_return_uses_graph_to_ctc(monkeypatch):
    """Regression: trackastra 0.3.x ``track()`` returns a single graph, not a
    pair. The engine must derive masks via ``graph_to_ctc`` instead of unpacking
    the graph into two (which raised 'too many values to unpack (expected 2)')."""
    m = np.array([[1, 1, 0, 2, 2]])
    relabeled = np.stack([np.array([[5, 5, 0, 6, 6]])] * 2)
    calls = _stub_trackastra(monkeypatch, track_return=object(),  # a lone graph
                             graph_to_ctc_masks=relabeled)
    frames = [
        FrameDetections(0, "0", [Detection(1, 0, 0), Detection(2, 0, 3)],
                        image=np.zeros((1, 5)), mask=m),
        FrameDetections(1, "1", [Detection(1, 0, 0), Detection(2, 0, 3)],
                        image=np.zeros((1, 5)), mask=m),
    ]
    res = tk.run(frames, EngineParams())
    assert "graph_to_ctc" in calls           # single-graph path taken
    assert res.track_id_for(0, 1) == 5 and res.track_id_for(0, 2) == 6
    assert res.n_tracks == 2


def test_run_pair_return_uses_masks_directly(monkeypatch):
    """Newer trackastra ``track()`` returns ``(graph, masks_tracked)`` — the
    engine should consume the masks directly without calling graph_to_ctc."""
    m = np.array([[1, 1, 0, 2, 2]])
    relabeled = np.stack([np.array([[7, 7, 0, 8, 8]])] * 2)
    calls = _stub_trackastra(monkeypatch, track_return=(object(), relabeled))
    frames = [
        FrameDetections(0, "0", [Detection(1, 0, 0), Detection(2, 0, 3)],
                        image=np.zeros((1, 5)), mask=m),
        FrameDetections(1, "1", [Detection(1, 0, 0), Detection(2, 0, 3)],
                        image=np.zeros((1, 5)), mask=m),
    ]
    res = tk.run(frames, EngineParams())
    assert "graph_to_ctc" not in calls       # pair path: no conversion needed
    assert res.track_id_for(0, 1) == 7 and res.track_id_for(0, 2) == 8


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
