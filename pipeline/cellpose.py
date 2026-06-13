"""Cellpose segmentation for the pipeline (Torch-backed, isolated on purpose).

Extracted from ``process_microscopy.py`` so the only Torch-importing segmenter
lives behind one module boundary. **Cellpose (and therefore Torch) is imported
lazily**, inside :func:`_cellpose_model`, so merely importing this module does
*not* load Torch — that matters because TensorFlow (StarDist) and Torch cannot
always share a process. The default StarDist path never imports this code's
dependencies; only a Cellpose run does.

``process_microscopy`` re-exports these names, so ``pm._segment_cellpose(...)``
and the segmentation dispatch are unchanged.
"""

from __future__ import annotations

import numpy as np

# Per-worker Cellpose models, keyed by model type (cyto3/cyto2/cyto/nuclei), so a
# run can choose a model without silently reusing another type's cache. Lazy.
_CELLPOSE_MODELS: "dict[str, object]" = {}


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
        model = _cp_models.CellposeModel(model_type=key)
        _CELLPOSE_MODELS[key] = model
    return model


def _segment_cellpose(
    image: np.ndarray,
    *,
    model_type: str = "cyto3",
    diameter: float = 0.0,
    flow_threshold: float = 0.4,
    cellprob_threshold: float = 0.0,
    nucleus_image: "np.ndarray | None" = None,
) -> np.ndarray:
    """Segment *image* with Cellpose. Works on any channel (fluorescent or
    brightfield) — the caller chooses the input. ``diameter=0`` auto-estimates.

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
