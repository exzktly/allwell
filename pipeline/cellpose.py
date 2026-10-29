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
            if _allow_tf32(device):
                where = f"{where}, TF32"
            if major is not None and major >= 4:
                # bfloat16 halves the weights' memory, on a GPU that computes in it; elsewhere float32 (an older
                # GPU without bfloat16, or the CPU, where it is slow).
                import torch

                bf16 = (device.type == "cuda" and bool(torch.cuda.is_bf16_supported())
                        and _torch_at_least(torch.__version__, BF16_MIN_TORCH))

                def build(half: bool):
                    return _cp_models.CellposeModel(gpu=device.type == "cuda", device=device, pretrained_model=key,
                                                    use_bfloat16=half)
                model = build(bf16)
                if bf16:
                    model, bf16 = _bfloat16_or_float32(model, lambda: build(False), key)
                where = f"{where}, {'bfloat16' if bf16 else 'float32'}"
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
        # Cellpose logs "No cell pixels found." for every image it finds no cell in. A nanowell run gives it one image
        # per nanowell, so that bare line, naming no well, nanowell or time, was the only thing a long count printed,
        # and read as the run being stuck. The count's own progress lines say what it is doing instead
        # (``nanowell_task._log_sites``), and a nanowell with no cell is recorded as one, with ``n_cells`` 0.
        # A batch of crops (``_segment_cellpose_many``) also makes Cellpose's models and core log a tqdm progress
        # line per call, which says less than the count's own lines; their warnings still pass.
        # Cellpose's models logger stays at INFO so its own timings ("network run in …", "masks created in …") reach
        # _CellposeChatter, which adds them to CELLPOSE_SECONDS and prints none of its INFO lines.
        for name in ("cellpose.dynamics", "cellpose.models", "cellpose.core"):
            logging.getLogger(name).setLevel(logging.INFO if name == "cellpose.models" else logging.WARNING)
            if not any(isinstance(f, _CellposeChatter) for f in logging.getLogger(name).filters):
                logging.getLogger(name).addFilter(_CellposeChatter())
        _CELLPOSE_MODELS[key] = model
    return model


#: Seconds this process's Cellpose calls have spent, by Cellpose's own clock: running the network on the images
#: (``network``) and turning its flows into masks (``masks``). ``_CellposeChatter`` adds to it from the two INFO lines
#: Cellpose 4 logs for a call given more than one image, and only then; ``timed`` counts the images of those calls,
#: so the nanowell count's progress lines divide each by the nanowells it actually timed
#: (``pipeline.nanowell_task._log_sites``) rather than by every one segmented, a lone crop's call included. Cellpose
#: 3 logs neither, and it stays at zero there.
CELLPOSE_SECONDS = {"network": 0.0, "masks": 0.0, "timed": 0.0}


class _CellposeChatter(logging.Filter):
    """Keeps Cellpose's own log lines out of the run log, except the warnings worth reading.

    Its INFO lines are dropped, after the two that time a call ("network run in 1.23s", "masks created in 0.45s")
    are added to ``CELLPOSE_SECONDS``. Two warnings are dropped as well, because they are expected, not wrong, on
    the nanowell count's batched crops, and would otherwise print once per batch and once per empty nanowell: that a
    stack is segmented per plane (which is the point), and that an image had no seeds (an empty nanowell, recorded
    as one). Every other warning passes."""

    EXPECTED = ("3D stack used, but stitch_threshold=0 and do_3D=False", "no seeds found in get_masks_torch")
    TIMED = (("network run in ", "network"), ("masks created in ", "masks"))

    def filter(self, record: logging.LogRecord) -> bool:
        message = str(record.getMessage())
        if record.levelno < logging.WARNING:
            for prefix, key in self.TIMED:
                if message.startswith(prefix):
                    try:
                        CELLPOSE_SECONDS[key] += float(message[len(prefix):].strip().rstrip("s"))
                    except ValueError:
                        return False                    # a timing line in a shape this version does not know
            return False
        return not message.startswith(self.EXPECTED)


#: The first Torch that runs Cellpose-SAM in bfloat16 on a GPU. Torch 2.0's CUDA kernel for 1-D linear upsampling
#: has no bfloat16 version, and Segment Anything's encoder upsamples its relative position embeddings with it, so on
#: a GPU that offers bfloat16 (an Ampere card such as the A10G; a T4 does not) every image failed with
#: "upsample_linear1d_out_frame" not implemented for 'BFloat16' — after the run had located every timepoint.
BF16_MIN_TORCH = (2, 1)


def _allow_tf32(device) -> bool:
    """Let float32 matrix maths on a CUDA *device* use TF32 tensor cores; ``True`` when it was switched on.

    Torch leaves them off for matrix products by default, so a model run in float32 — every Cellpose model on the
    pinned Torch 2.0.1, and the fallback anywhere bfloat16 fails — ran on the GPU's plain float32 units: on an A10G
    about half the throughput the same GPU gives in TF32. TF32 keeps float32's range and rounds products to a 10-bit
    mantissa, which is far finer than a segmentation network's outputs need. A model built in bfloat16 is not
    affected: its products are bfloat16. Process-wide, as the setting is; the pipeline's only Torch user is Cellpose.
    """
    if getattr(device, "type", None) != "cuda":
        return False
    import torch

    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    return True


def _torch_at_least(version: str, minimum: tuple[int, int]) -> bool:
    """Whether a Torch ``__version__`` (``"2.0.1+cu117"``) is at least *minimum* (major, minor); ``False`` when it
    cannot be read."""
    try:
        major, minor = (int(part) for part in str(version).split("+")[0].split(".")[:2])
    except ValueError:
        return False
    return (major, minor) >= minimum


def _bfloat16_or_float32(model, float32, key: str):
    """``(model, used bfloat16)``: *model*, built in bfloat16, if it can segment a small blank image; else the model
    *float32* builds, with a warning that says why.

    The version gate (``BF16_MIN_TORCH``) covers the one failure known; this covers the next, on the first image
    rather than mid-run, since an operation without a bfloat16 kernel fails on every image the same way."""
    try:
        model.eval(np.zeros((64, 64), np.float32), normalize=False)
    except RuntimeError as exc:
        if "BFloat16" not in str(exc):
            raise
        log.warning("Cellpose %s cannot run in bfloat16 with this Torch (%s); using float32, which takes twice the "
                    "GPU memory.", key, exc)
        return float32(), False
    return model, True


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
    lowhigh: tuple[float, float] | None = None,
    max_size_fraction: float | None = None,
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

    *lowhigh* fixes the intensities scaled to 0 and 1, in place of the image's
    own 1st and 99th percentiles: a crop of a larger image is then scaled as the
    whole image is. The image is scaled here and Cellpose is told not to
    normalise (``normalize=False``), rather than handed the range as
    ``normalize={"lowhigh": …}``: Cellpose pads a greyscale image with empty
    channels and applies a given range to every channel, so the empty ones
    became the constant ``-lo / (hi - lo)`` (about -1 on camera counts) instead
    of 0 — an input no model was trained on, which Cellpose-SAM answered with no
    cells. Its own percentile path leaves a flat channel at 0, as here. Only a
    single image takes a range; with a *nucleus_image* it is refused.

    *max_size_fraction*, when given, replaces Cellpose 4's cap on a mask's share of the image (0.4): a nanowell
    crop is given 1.0, since an object filling its nanowell fills most of the crop. Cellpose 3 is not told.
    """
    model = _cellpose_model(model_type)
    diam = float(diameter) if diameter and diameter > 0 else None
    seg = np.asarray(image)
    extra: dict = {}
    if lowhigh is not None:
        if nucleus_image is not None:
            raise ValueError("A fixed intensity range is for a single image, not one with a nucleus channel")
        lo, hi = float(lowhigh[0]), float(lowhigh[1])
        seg = (seg.astype(np.float32) - lo) / (hi - lo if hi > lo else 1.0)
        extra = {"normalize": False}
    major = cellpose_major()
    if major is not None and major >= 4:
        if max_size_fraction is not None:
            extra["max_size_fraction"] = float(max_size_fraction)
        if nucleus_image is not None:
            stacked = np.stack([seg, np.asarray(nucleus_image)], axis=-1)  # (Y, X, 2)
            result = model.eval(stacked, diameter=diam, channel_axis=-1, flow_threshold=flow_threshold,
                                cellprob_threshold=cellprob_threshold, **extra)
        else:
            result = model.eval(seg, diameter=diam, flow_threshold=flow_threshold,
                                cellprob_threshold=cellprob_threshold, **extra)
        return np.asarray(result[0], dtype=np.int32)
    if nucleus_image is not None:
        stacked = np.stack([seg, np.asarray(nucleus_image)], axis=-1)  # (Y, X, 2)
        result = model.eval(stacked, diameter=diam, channels=[1, 2],
                            flow_threshold=flow_threshold,
                            cellprob_threshold=cellprob_threshold)
    else:
        result = model.eval(seg, diameter=diam, channels=[0, 0],
                            flow_threshold=flow_threshold,
                            cellprob_threshold=cellprob_threshold, **extra)
    masks = result[0]  # eval returns (masks, flows, styles[, diams])
    return np.asarray(masks, dtype=np.int32)


def _segment_cellpose_many(
    images,
    *,
    lowhigh: tuple[float, float],
    model_type: str = DEFAULT_MODEL,
    diameter: float = 0.0,
    flow_threshold: float = 0.4,
    cellprob_threshold: float = 0.0,
    max_size_fraction: float | None = None,
) -> list[np.ndarray]:
    """Segment several single-channel *images* in one Cellpose 4 call; one label image per input, its shape.

    One call per nanowell crop gave Cellpose one image at a time, and each crop, however small, became a whole
    network tile run alone: on a Tesla T4 that was 2.4 s a nanowell. Given a batch, Cellpose 4 runs up to
    ``batch_size`` (8) images' tiles in each network pass, and computes each image's masks on its own. The images
    are centred in a common shape padded with the value that scales to 0 — what Cellpose pads a small image with
    anyway — scaled to *lowhigh* here, as :func:`_segment_cellpose` does, and cut back after. The network sees each
    crop within a pixel or two of where a call of its own puts it, so its masks can differ from that call's by
    those pixels' effect, not by the crop's content. Cellpose 3 has no such batching: there each image is its own
    call, as before.

    The network runs ``NETWORK_BATCH`` tiles a pass. Cellpose then builds each image's masks one at a time, which on
    an A10G took 0.27 s a nanowell against the network's 0.05 s, so on the Cellpose ``pipeline.cellpose_masks`` was
    proven against it is asked for the flows only (``compute_masks=False``) and the masks are built there for the
    whole batch at once. On any other Cellpose 4, Cellpose builds them.
    """
    images = [np.asarray(im) for im in images]
    if not images:
        return []
    major = cellpose_major()
    if major is None or major < 4 or len(images) == 1:
        return [_segment_cellpose(im, model_type=model_type, diameter=diameter, flow_threshold=flow_threshold,
                                  cellprob_threshold=cellprob_threshold, lowhigh=lowhigh,
                                  max_size_fraction=max_size_fraction) for im in images]
    model = _cellpose_model(model_type)
    lo, hi = float(lowhigh[0]), float(lowhigh[1])
    h = max(im.shape[0] for im in images)
    w = max(im.shape[1] for im in images)
    # Each image is centred in the common shape, as Cellpose centres a small image in its padding, so a crop sits
    # where a call of its own would put it.
    at = [((h - im.shape[0]) // 2, (w - im.shape[1]) // 2) for im in images]
    stack = np.zeros((len(images), h, w, 1), np.float32)              # 0: the padding Cellpose itself would add
    for i, (im, (y, x)) in enumerate(zip(images, at)):
        stack[i, y:y + im.shape[0], x:x + im.shape[1], 0] = (im.astype(np.float32) - lo) / (hi - lo if hi > lo else 1.0)
    diam = float(diameter) if diameter and diameter > 0 else None
    capped = {} if max_size_fraction is None else {"max_size_fraction": float(max_size_fraction)}
    from pipeline import cellpose_masks

    ours = cellpose_masks.supported(cellpose_masks.installed_version())
    result = model.eval(stack, diameter=diam, flow_threshold=flow_threshold, cellprob_threshold=cellprob_threshold,
                        normalize=False, batch_size=NETWORK_BATCH, compute_masks=not ours, **capped)
    CELLPOSE_SECONDS["timed"] += len(images)              # a call of several images is one Cellpose 4 times
    if ours:
        masks = _batched_masks(model, result[1], len(images), (h, w), diameter=diam, flow_threshold=flow_threshold,
                               cellprob_threshold=cellprob_threshold, max_size_fraction=max_size_fraction)
    else:
        masks = np.asarray(result[0], dtype=np.int32).reshape(len(images), h, w)
    return [masks[i, y:y + im.shape[0], x:x + im.shape[1]] for i, (im, (y, x)) in enumerate(zip(images, at))]


#: Network tiles Cellpose runs per pass for a batch of nanowell crops (its ``eval(batch_size=…)``; its own default
#: is 8). A crop at the default scale is one 256-pixel tile, so 32 runs a whole batch of ``CROP_BATCH`` crops in one
#: pass. Cellpose-SAM's activations for 32 tiles are a few GB at most, against an A10G's 24 GB shared by the
#: ``CELLPOSE_WORKERS_PER_GPU`` workers. Batching changes only how many tiles share a pass, not what each tile is.
NETWORK_BATCH = 32


def _batched_masks(model, flows, n: int, shape, *, diameter, flow_threshold, cellprob_threshold,
                   max_size_fraction) -> np.ndarray:
    """``(n, H, W)`` labels from the flows Cellpose's ``eval(compute_masks=False)`` returned for a batch, built
    together by ``pipeline.cellpose_masks`` rather than one image at a time, with Cellpose's own defaults for what
    the caller does not set. Times itself into ``CELLPOSE_SECONDS["masks"]``, since Cellpose logs no mask time for
    a call that builds none."""
    import inspect
    import time

    from pipeline import cellpose_masks

    started = time.perf_counter()
    defaults = inspect.signature(model.eval).parameters
    dP = np.asarray(flows[1], dtype=np.float32).reshape(2, n, *shape)
    cellprob = np.asarray(flows[2], dtype=np.float32).reshape(n, *shape)
    labels = cellpose_masks.batch_masks(
        dP, cellprob, niter=cellpose_masks.default_niter(diameter), cellprob_threshold=cellprob_threshold,
        flow_threshold=flow_threshold, min_size=defaults["min_size"].default,
        max_size_fraction=(defaults["max_size_fraction"].default if max_size_fraction is None
                           else float(max_size_fraction)),
        device=model.device)
    CELLPOSE_SECONDS["masks"] += time.perf_counter() - started
    return np.stack([np.asarray(m, dtype=np.int32) for m in labels])
