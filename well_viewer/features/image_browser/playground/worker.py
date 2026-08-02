"""Playground tab — off-GUI-thread compute (reference fields + live StarDist).

The ``empty_well`` / ``basic`` correction modes need a per-channel field built
from a reference well's frames. ``build_reference_fields`` is the pure (IO-only,
no Qt) compute — it loads that well's RAW frames via the same image_table /
image_discovery loaders the Playground already uses and calls the pipeline's
estimators (``estimate_background_field`` / ``estimate_basic_flatfield``), so the
math matches a batch run. ``request_reference_fields`` runs it on a daemon thread
and marshals the result back to the GUI thread through a queued-signal bridge
(the worker thread has no Qt event loop — same constraint as the smFISH worker).

``stardist_labels_for_frame`` / ``request_stardist_labels`` are the same pair for
the live segmentation preview. **TensorFlow and StarDist are imported inside the
compute function, never at module scope** — this module is imported by the
Playground controller at GUI start-up, and importing TF there would add tens of
seconds and gigabytes to launching the app for everyone, including the users who
never open this tab. The model is loaded once per session and cached in
``_GUI_STARDIST_MODEL``; the inference itself is
``pipeline.stardist.segment_nuclei``, the function a batch run calls, so what
the preview shows is what a run would produce.
"""

from __future__ import annotations

import threading

import numpy as np
from PySide6.QtCore import QObject, Signal


def build_reference_fields(ctx, well: str, chan_lower: str, *,
                           flatfield_mode: str, background_method: str,
                           max_samples: int = 30):
    """Estimate ``(ff_field, bg_field)`` from *well*'s raw frames for a channel.

    ``ff_field`` is the mean-1 illumination field (for ``basic``, or B/mean(B)
    for the combined empty-well case); ``bg_field`` is the un-normalized additive
    background (for ``empty_well`` background). Either is None when the chosen
    modes don't need it. Returns ``(None, None)`` when no frames are found.
    """
    import process_microscopy as pm
    from pipeline.flatfield import estimate_basic_flatfield, normalize_flatfield
    from well_viewer.data.image_discovery import open_imgref_as_array
    from well_viewer.features.image_browser.image_table.controller import _load_well_channel

    refs = _load_well_channel(ctx, {}, well, chan_lower, use_tophat=False)
    frames: list = []
    for ref in list(refs.values())[:max_samples]:
        try:
            arr = open_imgref_as_array(ref, greyscale=True)
        except Exception:
            arr = None
        if arr is not None:
            frames.append(np.asarray(arr, dtype=np.float32))

    ff_field = None
    bg_field = None
    if not frames:
        return ff_field, bg_field

    if background_method == "empty_well":
        bg_field = pm.estimate_background_field(frames)
    if flatfield_mode == "basic":
        ff_field = estimate_basic_flatfield(frames)
    elif flatfield_mode == "empty_well":
        # Combined empty-well: the flat-field is B/mean(B).
        if bg_field is None:
            bg_field = pm.estimate_background_field(frames)
        ff_field = normalize_flatfield(bg_field)
    return ff_field, bg_field


class _ReferenceBridge(QObject):
    """Marshal worker-thread results back to the GUI thread (queued signals)."""

    done = Signal(object)      # (well, chan_lower, ff_field, bg_field)
    failed = Signal(str)


def request_reference_fields(ctx, well: str, chan_lower: str, *,
                             flatfield_mode: str, background_method: str,
                             on_done, on_failed=None, max_samples: int = 30):
    """Build reference fields on a daemon thread; call ``on_done(well,
    chan_lower, ff_field, bg_field)`` on the GUI thread when finished. Returns
    the bridge (keep a reference alive for the duration)."""
    bridge = _ReferenceBridge()

    def _disconnect() -> None:
        # One-shot: drop both connections after the bridge fires so the bridge
        # (and the widget/app captured by on_done/on_failed) can be released
        # instead of leaking a fresh connected QObject on every call (audit
        # E23). Disconnecting an already-empty signal raises, so guard it.
        for sig in (bridge.done, bridge.failed):
            try:
                sig.disconnect()
            except (TypeError, RuntimeError):
                pass

    def _handle_done(payload) -> None:
        try:
            on_done(*payload)
        finally:
            _disconnect()

    def _handle_failed(msg: str) -> None:
        try:
            if on_failed is not None:
                on_failed(msg)
        finally:
            _disconnect()

    bridge.done.connect(_handle_done)
    bridge.failed.connect(_handle_failed)

    def _run() -> None:
        try:
            ff, bg = build_reference_fields(
                ctx, well, chan_lower, flatfield_mode=flatfield_mode,
                background_method=background_method, max_samples=max_samples)
            bridge.done.emit((well, chan_lower, ff, bg))
        except Exception as exc:  # never raise off the GUI thread
            bridge.failed.emit(str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return bridge


# ── live StarDist preview ────────────────────────────────────────────────────

# The pretrained model, loaded at most once per GUI session. Held here rather
# than in PlaygroundState because it is not data: it survives a dataset swap,
# must never be pickled, and reloading it would cost the user another 10-30 s.
_GUI_STARDIST_MODEL = None

_MISSING_DEPS_MSG = (
    "StarDist is not installed in this environment ({missing} missing), so the "
    "preview cannot run. The thresholds above still apply to a pipeline run."
)


def _missing_stardist_deps() -> list[str]:
    """Which of StarDist's imports are absent, without importing any of them."""
    import importlib.util
    return [name for name in ("stardist", "csbdeep", "tensorflow")
            if importlib.util.find_spec(name) is None]


def stardist_labels_for_frame(arr, *, prob_thresh=None, nms_thresh=None,
                              tile_px: int = 0):
    """Segment one displayed frame and return its int32 label image.

    Runs the pipeline's own :func:`pipeline.stardist.segment_nuclei`, so the
    input scrubbing, the ``[0, 1]`` clamp and the near-constant-field refusal
    that make StarDist's uncatchable ClipperLib abort unreachable all apply here
    too — which matters more in the GUI than in a worker, because an abort here
    would take the whole application down rather than one pipeline process.
    """
    missing = _missing_stardist_deps()
    if missing:
        raise RuntimeError(_MISSING_DEPS_MSG.format(missing=", ".join(missing)))

    global _GUI_STARDIST_MODEL
    import process_microscopy as pm
    from pipeline.stardist import StarDistParams, segment_nuclei

    if _GUI_STARDIST_MODEL is None:
        pm._ensure_pkg_resources_shim()
        from stardist.models import StarDist2D
        # Same loader the pipeline workers use: it recovers from a cache left
        # truncated by an interrupted download and serialises against a
        # concurrently-running pipeline that is fetching the same weights.
        _GUI_STARDIST_MODEL = pm._load_stardist_pretrained(
            StarDist2D, "2D_versatile_fluo")

    return segment_nuclei(
        arr, model=_GUI_STARDIST_MODEL,
        params=StarDistParams(prob_thresh=prob_thresh, nms_thresh=nms_thresh,
                              tile_px=int(tile_px or 0)))


class _SegmentationBridge(QObject):
    """Marshal live-segmentation results back to the GUI thread."""

    done = Signal(object)      # the label image
    failed = Signal(str)


def request_stardist_labels(arr, *, prob_thresh=None, nms_thresh=None,
                            tile_px: int = 0, on_done, on_failed=None):
    """Segment *arr* on a daemon thread; call ``on_done(labels)`` on the GUI
    thread when finished. Returns the bridge (keep a reference alive)."""
    bridge = _SegmentationBridge()

    def _disconnect() -> None:
        # One-shot, for the same reason _ReferenceBridge is: without it every
        # press of Segment leaks a connected QObject holding the callbacks (and
        # through them the app).
        for sig in (bridge.done, bridge.failed):
            try:
                sig.disconnect()
            except (TypeError, RuntimeError):
                pass

    def _handle_done(labels) -> None:
        try:
            on_done(labels)
        finally:
            _disconnect()

    def _handle_failed(msg: str) -> None:
        try:
            if on_failed is not None:
                on_failed(msg)
        finally:
            _disconnect()

    bridge.done.connect(_handle_done)
    bridge.failed.connect(_handle_failed)

    def _run() -> None:
        try:
            bridge.done.emit(stardist_labels_for_frame(
                arr, prob_thresh=prob_thresh, nms_thresh=nms_thresh,
                tile_px=tile_px))
        except Exception as exc:  # never raise off the GUI thread
            bridge.failed.emit(str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return bridge
