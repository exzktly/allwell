"""trackastra engine adapter — transformer tracking (guarded, §D / PR5e).

trackastra consumes raw images **and** integer label masks per frame (assembled
into ``(T, Y, X)`` stacks, loaded from ``<well>_out.zip`` by the worker), so the
adapter requires ``FrameDetections.image`` / ``.mask`` to be populated. The heavy
``trackastra`` / ``torch`` imports are guarded — the engine is registered and
shown in the picker even when absent, and errors only when selected.

The mask→TrackResult conversion (:func:`result_from_tracked_masks`) is pure
numpy so it is unit-tested without trackastra installed; the model call itself is
dev-env-verified.
"""

from __future__ import annotations

import importlib.util
from typing import List

from .base import EngineParams, FrameDetections, TrackResult

NAME = "trackastra"
DISPLAY_NAME = "trackastra (transformer)"
REQUIRES = ("trackastra", "torch")
LINKING_MODES = ("greedy_nodiv", "greedy", "ilp")
DEFAULT_MODEL = "general_2d"


def is_available() -> bool:
    return importlib.util.find_spec("trackastra") is not None


def ilp_available() -> bool:
    """The ``ilp`` linking mode needs the optional ``motile`` solver."""
    return importlib.util.find_spec("motile") is not None


AVAILABLE = is_available()


def result_from_tracked_masks(frames: List[FrameDetections], masks_tracked) -> TrackResult:
    """Map trackastra's relabeled mask stack back onto ``(frame, nid)`` nodes.

    For each frame, every original detection ``nid`` (located via the frame's
    original ``mask``) is assigned the **majority** track label found under it in
    the corresponding tracked mask. Pure numpy — no trackastra needed.
    """
    import numpy as np

    result = TrackResult()
    ordered = sorted(frames, key=lambda f: f.frame)
    for i, fr in enumerate(ordered):
        if fr.mask is None or i >= len(masks_tracked):
            continue
        om = np.asarray(fr.mask)
        tm = np.asarray(masks_tracked[i])
        if om.shape != tm.shape:
            continue
        for det in fr.detections:
            sel = om == det.nid
            if not sel.any():
                continue
            vals = tm[sel]
            vals = vals[vals > 0]
            if vals.size:
                track_id = int(np.bincount(vals).argmax())
                result.assignments[(fr.frame, int(det.nid))] = track_id
    return result.finalize()


def run(frames: List[FrameDetections], params: EngineParams) -> TrackResult:
    try:
        import numpy as np
        from trackastra.model import Trackastra
    except Exception as exc:  # pragma: no cover - optional dep
        raise RuntimeError(
            "The 'trackastra' engine requires the trackastra + torch packages. "
            f"Install them (see requirements) — import failed: {exc}"
        ) from exc

    if not frames:
        return TrackResult().finalize()

    extra = params.extra or {}
    mode = str(extra.get("linking_mode", "greedy"))
    if mode == "ilp" and not ilp_available():
        raise RuntimeError(
            "trackastra 'ilp' linking mode needs the optional 'motile' solver. "
            "Install motile or choose 'greedy' / 'greedy_nodiv'.")

    ordered = sorted(frames, key=lambda f: f.frame)
    imgs, masks = [], []
    for fr in ordered:
        if fr.image is None or fr.mask is None:
            raise RuntimeError(
                "trackastra needs a raw image and a label mask per frame; the "
                "worker must load them from <well>_out.zip before tracking.")
        imgs.append(np.asarray(fr.image))
        masks.append(np.asarray(fr.mask))
    imgs = np.stack(imgs)
    masks = np.stack(masks)

    device = str(extra.get("device", "automatic"))
    model_name = str(extra.get("model", DEFAULT_MODEL))
    model = Trackastra.from_pretrained(model_name, device=device)
    _graph, masks_tracked = model.track(imgs, masks, mode=mode)
    return result_from_tracked_masks(ordered, np.asarray(masks_tracked))
