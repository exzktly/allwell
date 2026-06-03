"""btrack engine adapter — Bayesian tracking with native division → lineage.

Guarded: ``btrack`` is imported only inside :func:`run`, so the app and pipeline
import this module fine without btrack installed; selecting the engine when it is
absent raises a clear error. Builds ``PyTrackObject``s from the per-frame
centroids, runs the Bayesian tracker, and maps ``track.ID / parent / root`` back
onto ``(frame, nid)`` nodes.
"""

from __future__ import annotations

from typing import List

from .base import EngineParams, FrameDetections, TrackResult, UNTRACKED

NAME = "btrack"
DISPLAY_NAME = "btrack (Bayesian + lineage)"
REQUIRES = ("btrack",)


def is_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("btrack") is not None


AVAILABLE = is_available()


def run(frames: List[FrameDetections], params: EngineParams) -> TrackResult:
    try:
        import btrack
        from btrack.btypes import PyTrackObject
    except Exception as exc:  # pragma: no cover - optional dep
        raise RuntimeError(
            "The 'btrack' engine requires the btrack package. Install it "
            f"(see requirements) — import failed: {exc}"
        ) from exc

    result = TrackResult()
    if not frames:
        return result.finalize()
    frames = sorted(frames, key=lambda f: f.frame)

    # Build PyTrackObjects; remember (frame, nid) per synthetic object id so we
    # can map track membership back to our nodes.
    objects = []
    obj_to_node: "dict[int, tuple[int, int]]" = {}
    max_r = max_c = 0.0
    oid = 0
    for fr in frames:
        for det in fr.detections:
            obj = PyTrackObject.from_dict({
                "ID": oid, "t": int(fr.frame),
                "x": float(det.col), "y": float(det.row), "z": 0.0,
            })
            objects.append(obj)
            obj_to_node[oid] = (fr.frame, int(det.nid))
            max_r = max(max_r, float(det.row))
            max_c = max(max_c, float(det.col))
            oid += 1
    if not objects:
        return result.finalize()

    with btrack.BayesianTracker() as tracker:
        # btrack's BayesianTracker MUST be configured with a motion / hypothesis
        # model before track(); left unconfigured it fails inside btrack and
        # only logs to btrack's own logger, so the app surfaces "it failed" with
        # no useful message. When the caller doesn't supply a config file, fall
        # back to btrack's bundled example cell config so tracking actually runs.
        cfg = params.extra.get("config_file")
        if not cfg:
            try:
                from btrack import datasets as _bt_datasets
                cfg = _bt_datasets.cell_config()
            except Exception:
                cfg = None
        if cfg:
            tracker.configure(cfg)
        tracker.max_search_radius = float(params.max_distance)
        if params.allow_division:
            try:
                tracker.update_method = btrack.constants.BayesianUpdates.APPROXIMATE
            except Exception:
                pass
        tracker.volume = ((0, max_c + 1), (0, max_r + 1))
        tracker.append(objects)
        tracker.track()
        try:
            tracker.optimize()
        except Exception:
            pass

        for track in tracker.tracks:
            tid = int(track.ID)
            parent = int(getattr(track, "parent", track.ID) or track.ID)
            for ref in track.refs:
                node = obj_to_node.get(int(ref))
                if node is not None:
                    result.assignments[node] = tid
            result.parents[tid] = parent if parent != tid else UNTRACKED

    return result.finalize()
