"""Cellpose segmentation for the pipeline (Torch-backed, isolated on purpose).

Extracted from ``process_microscopy.py`` so the only Torch-importing segmenter
lives behind one module boundary. **Cellpose (and therefore Torch) is imported
lazily**, inside :func:`_cellpose_model`, so merely importing this module does
*not* load Torch — that matters because TensorFlow (StarDist) and Torch cannot
always share a process. The default StarDist path never imports this code's
dependencies; only a Cellpose run does.

``process_microscopy``'s segmentation dispatch imports :func:`_segment_cellpose`
from here at call time, which is what lets this module be absent from a
StarDist-only build.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os

import numpy as np

log = logging.getLogger("process_microscopy")

# Per-worker Cellpose models, keyed by model type (cyto3/cyto2/cyto/nuclei), so a
# run can choose a model without silently reusing another type's cache. Lazy.
_CELLPOSE_MODELS: dict[str, object] = {}


def _cellpose_model(model_type: str = "cyto3"):
    """Return the per-worker Cellpose model for *model_type*, loading it once.

    Guarded import so the app / pipeline run without cellpose installed; the
    error only surfaces when a Cellpose method is actually selected."""
    key = str(model_type or "cyto3").strip() or "cyto3"
    model = _CELLPOSE_MODELS.get(key)
    if model is None:
        try:
            from cellpose import models as _cp_models
        except Exception as exc:  # pragma: no cover - depends on optional dep
            raise RuntimeError(
                "Cellpose segmentation requires the 'cellpose' package. Install "
                f"it (see requirements) — import failed: {exc}"
            ) from exc
        device, where = _cellpose_device()
        model = _cp_models.CellposeModel(
            model_type=key, gpu=device.type == "cuda", device=device)
        # Said once per worker and model, because it is the only place the run
        # states where segmentation actually happened: the main process can
        # count GPUs, but only Torch in the worker knows whether it can use one.
        log.info("Cellpose %s on %s (worker %d).", key, where, os.getpid())
        _CELLPOSE_MODELS[key] = model
    return model


def _cellpose_device():
    """``(torch.device, description)`` for this worker's Cellpose model.

    A CUDA device whenever Torch can see one — ``--cpu_only`` is StarDist's
    switch and does not apply (``pipeline.parallelism`` says why). With several GPUs the
    workers are spread across them by their pool ordinal. Apple's MPS is not
    used: Cellpose there stays on CPU as before. When no GPU is usable the
    description names what Torch reported, so a node that has a GPU the
    install cannot reach (a CPU-only Torch build, a driver too old for its
    CUDA) says so in the log instead of quietly running on CPU.
    """
    import torch

    if torch.cuda.is_available():
        n = max(1, torch.cuda.device_count())
        ordinal = getattr(mp.current_process(), "_identity", ()) or (1,)
        index = (ordinal[-1] - 1) % n
        return (torch.device(f"cuda:{index}"),
                f"GPU cuda:{index} ({torch.cuda.get_device_name(index)})")
    built_for = getattr(torch.version, "cuda", None) or "none (CPU-only build)"
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "<unset>")
    return torch.device("cpu"), (
        f"CPU — Torch {torch.__version__} sees no CUDA device "
        f"(built for CUDA {built_for}; CUDA_VISIBLE_DEVICES={visible})")


def _segment_cellpose(
    image: np.ndarray,
    *,
    model_type: str = "cyto3",
    diameter: float = 0.0,
    flow_threshold: float = 0.4,
    cellprob_threshold: float = 0.0,
    nucleus_image: np.ndarray | None = None,
) -> np.ndarray:
    """Segment *image* with Cellpose. Works on any channel (fluorescent or
    brightfield) — the caller chooses the input. ``diameter=0`` passes none, so
    Cellpose segments at its model's own scale (30 px for cyto3) without
    resizing; a diameter *d* resizes the image by ``30 / d`` first, which is
    what makes a small *d* slow. Nothing estimates the size on this path.

    When *nucleus_image* is given, the two channels are stacked and Cellpose runs
    in cyto+nucleus mode (``channels=[1, 2]``); otherwise grayscale
    (``channels=[0, 0]``). ``flow_threshold`` / ``cellprob_threshold`` tune the
    mask recovery / probability cut.
    """
    model = _cellpose_model(model_type)
    diam = float(diameter) if diameter and diameter > 0 else None
    seg = np.asarray(image)
    if nucleus_image is not None:
        stacked = np.stack([seg, np.asarray(nucleus_image)], axis=-1)  # (Y, X, 2)
        result = model.eval(stacked, diameter=diam, channels=[1, 2],
                            flow_threshold=flow_threshold,
                            cellprob_threshold=cellprob_threshold)
    else:
        result = model.eval(seg, diameter=diam, channels=[0, 0],
                            flow_threshold=flow_threshold,
                            cellprob_threshold=cellprob_threshold)
    masks = result[0]  # eval returns (masks, flows, styles[, diams])
    return np.asarray(masks, dtype=np.int32)
