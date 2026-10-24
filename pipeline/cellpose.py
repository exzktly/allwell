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

class CellposeUnavailable(RuntimeError):
    """Cellpose could not be loaded at all: the package or Torch would not import, or the model could not be built
    (its weights not downloadable, say). Distinct from a segmentation that loaded and then failed on an image, so a
    caller that can do without counting (Test detection) can tell the two apart; it says so by the attribute below
    rather than by import, because a feature-subset build may not ship this module at all."""

    segmenter_unavailable = True


# Per-worker Cellpose models, keyed by model name, so a run can choose a model
# without silently reusing another model's cache. Lazy.
_CELLPOSE_MODELS: dict[str, object] = {}

#: The models each major version of Cellpose ships, by the name a run asks for. The two families do not overlap and
#: cannot be installed side by side: Cellpose 4 replaced every Cellpose 3 model with Cellpose-SAM, and its
#: ``CellposeModel`` ignores ``model_type`` and answers an unknown ``pretrained_model`` with its own default — both
#: with no more than a log line, so asking Cellpose 4 for ``cyto3`` would segment with Cellpose-SAM under cyto3's
#: name. :func:`_cellpose_model` refuses a model the installed version does not ship instead.
CELLPOSE_3_MODELS = ("cyto3", "cyto2", "cyto", "nuclei", "livecell_cp3")
CELLPOSE_4_MODELS = ("cpsam",)
#: The model a run uses when it names none: Cellpose-SAM (Cellpose 4, ``requirements-remote.txt``).
DEFAULT_MODEL = "cpsam"


def cellpose_major(version: str | None = None) -> int | None:
    """The installed Cellpose's major version (or *version*'s), ``None`` when it is not installed. Read from the
    package metadata, so asking imports neither Cellpose nor Torch."""
    if version is None:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as installed

        try:
            version = installed("cellpose")
        except PackageNotFoundError:
            return None
    head = str(version).split(".")[0]
    return int(head) if head.isdigit() else None


def model_refusal(model: str, major: int | None) -> str | None:
    """Why *model* cannot run on Cellpose *major*, or ``None`` when it can (or the version is not known)."""
    if major is None:
        return None
    if major >= 4 and model not in CELLPOSE_4_MODELS:
        if model in CELLPOSE_3_MODELS:
            return (f"{model} is a Cellpose 3 model and the installed Cellpose is version {major}, which ships only "
                    f"{', '.join(CELLPOSE_4_MODELS)}; choose {DEFAULT_MODEL}, or install cellpose 3 to use {model}")
        return f"{model!r} is not a Cellpose {major} model; it ships {', '.join(CELLPOSE_4_MODELS)}"
    if major < 4 and model not in CELLPOSE_3_MODELS:
        if model in CELLPOSE_4_MODELS:
            return (f"{model} needs Cellpose 4 and the installed Cellpose is version {major}; install the pinned "
                    "cellpose (requirements-remote.txt) or choose a Cellpose 3 model")
        return f"{model!r} is not a Cellpose {major} model; it ships {', '.join(CELLPOSE_3_MODELS)}"
    return None


def _cellpose_model(model_type: str = DEFAULT_MODEL):
    """Return the per-worker Cellpose model for *model_type*, loading it once.

    Guarded import so the app / pipeline run without cellpose installed; the
    error only surfaces when a Cellpose method is actually selected. A model the
    installed version does not ship is refused (:func:`model_refusal`), never
    swapped for another."""
    key = str(model_type or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    model = _CELLPOSE_MODELS.get(key)
    if model is None:
        try:
            from cellpose import models as _cp_models
        except Exception as exc:  # pragma: no cover - depends on optional dep
            # Any failure, not only an ImportError: a Torch library that will not load raises OSError, and that is
            # as much "Cellpose is not available" as a missing package.
            raise CellposeUnavailable(
                "Cellpose segmentation requires the 'cellpose' package. Install "
                f"it (see requirements) — import failed: {type(exc).__name__}: {exc}"
            ) from exc
        major = cellpose_major()
        refusal = model_refusal(key, major)
        if refusal:
            raise CellposeUnavailable(f"Cellpose's {key} model cannot be used: {refusal}.")
        try:
            device, where = _cellpose_device()
            if major is not None and major >= 4:
                # bfloat16 halves the weights' memory, on a GPU that computes in it; elsewhere float32 (an older
                # GPU without bfloat16, or the CPU, where it is slow).
                import torch

                bf16 = device.type == "cuda" and bool(torch.cuda.is_bf16_supported())
                model = _cp_models.CellposeModel(gpu=device.type == "cuda", device=device, pretrained_model=key,
                                                 use_bfloat16=bf16)
            else:
                model = _cp_models.CellposeModel(
                    model_type=key, gpu=device.type == "cuda", device=device)
        except Exception as exc:  # pragma: no cover - depends on optional dep
            # The model's weights are fetched on first use: offline, that fails here, before any image is seen.
            raise CellposeUnavailable(
                f"Cellpose's {key} model could not be loaded — {type(exc).__name__}: {exc}") from exc
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
    model_type: str = DEFAULT_MODEL,
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

    Cellpose 4 takes no ``channels``: it reads up to three channels in any order,
    so the two are stacked on the last axis and named by ``channel_axis``.
    """
    model = _cellpose_model(model_type)
    diam = float(diameter) if diameter and diameter > 0 else None
    seg = np.asarray(image)
    major = cellpose_major()
    if major is not None and major >= 4:
        if nucleus_image is not None:
            stacked = np.stack([seg, np.asarray(nucleus_image)], axis=-1)  # (Y, X, 2)
            result = model.eval(stacked, diameter=diam, channel_axis=-1, flow_threshold=flow_threshold,
                                cellprob_threshold=cellprob_threshold)
        else:
            result = model.eval(seg, diameter=diam, flow_threshold=flow_threshold,
                                cellprob_threshold=cellprob_threshold)
        return np.asarray(result[0], dtype=np.int32)
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
