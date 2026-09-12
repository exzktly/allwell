"""Playground tab — off-GUI-thread compute (reference fields + live StarDist).

The ``empty_well`` / ``basic`` correction modes need a per-channel field built
from a reference well's frames. ``build_reference_fields`` is the pure (IO-only,
no Qt) compute — it loads that well's RAW frames through the shared data-layer
loaders (``data/frame_load.py``, ``data/image_discovery.py``) and calls the pipeline's
estimators (``estimate_background_field`` / ``estimate_basic_flatfield``), so the
math matches a batch run.

Both requests run on a daemon thread and marshal their result back through
``core.worker_bridge.run_off_thread`` — the shared one-shot queued-signal bridge
(a worker thread has no Qt event loop, so this is the only way a result reaches
the GUI).

The segmentation itself lives in ``services.frame_preview``, not here, because
the Playground is a *removable* feature and no other pane may import it: the
session's single loaded StarDist model has to outlive any one tab, and a model
that costs 10-30 s and 1-2 GB to load must never be loaded twice.
"""

from __future__ import annotations

import numpy as np

from core.worker_bridge import WorkerBridge, run_off_thread

# Re-exported so this module stays the Playground's single worker entry point.
_ReferenceBridge = WorkerBridge
_SegmentationBridge = WorkerBridge


def build_reference_fields(ctx, well: str, chan_lower: str, *,
                           flatfield_mode: str, background_method: str,
                           max_samples: int = 30, report=None):
    """Estimate ``(ff_field, bg_field)`` from *well*'s raw frames for a channel.

    ``ff_field`` is the mean-1 illumination field (for ``basic``, or B/mean(B)
    for the combined empty-well case); ``bg_field`` is the un-normalized additive
    background (for ``empty_well`` background). Either is None when the chosen
    modes don't need it. Returns ``(None, None)`` when no frames are found.

    ``report(done, total)`` — when given — is called once per frame decoded,
    which is where nearly all of the wall-clock goes. It is a plain callable, so
    this stays a pure compute function with no idea a progress bar exists.
    """
    import process_microscopy as pm
    from pipeline.flatfield import estimate_basic_flatfield, normalize_flatfield
    from well_viewer.data.frame_load import load_well_channel
    from well_viewer.data.image_repository import ImageRepository

    # The data layer's loader, not the Image Table's wrapper around it. The
    # wrapper only binds that tab's pass-scoped ``scan_misses``, which this
    # worker has no business writing -- and reaching for it made the Playground
    # unopenable in a build with the Image Table profiled out.
    refs = load_well_channel(ctx, {}, well, chan_lower, use_tophat=False)
    sample = list(refs.values())[:max_samples]
    total = len(sample)
    frames: list = []
    for i, ref in enumerate(sample, start=1):
        try:
            arr = ImageRepository.array(ref, greyscale=True)
        except Exception:
            arr = None
        if arr is not None:
            frames.append(np.asarray(arr, dtype=np.float32))
        if report is not None:
            # Counted per frame ATTEMPTED, not per frame kept: a bar that stalls
            # on unreadable files would read as a hang.
            report(i, total)

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


def request_reference_fields(ctx, well: str, chan_lower: str, *,
                             flatfield_mode: str, background_method: str,
                             on_done, on_failed=None, on_progress=None,
                             max_samples: int = 30):
    """Build reference fields on a daemon thread; call ``on_done(well,
    chan_lower, ff_field, bg_field)`` on the GUI thread when finished. Returns
    the bridge (keep a reference alive for the duration).

    With ``on_progress(done, total)`` the sampling reports frame-by-frame, on
    the GUI thread, through the bridge's queued signal.
    """
    def _compute(reporter=None):
        ff, bg = build_reference_fields(
            ctx, well, chan_lower, flatfield_mode=flatfield_mode,
            background_method=background_method, max_samples=max_samples,
            report=None if reporter is None else reporter.progress)
        return (well, chan_lower, ff, bg)

    return run_off_thread(_compute,
                          on_done=lambda payload: on_done(*payload),
                          on_failed=on_failed, on_progress=on_progress)


# ── live StarDist preview ────────────────────────────────────────────────────

def stardist_labels_for_frame(arr, *, prob_thresh=None, nms_thresh=None,
                              tile_px: int = 0, on_stage=None):
    """Segment one displayed frame and return its int32 label image.

    A thin delegate to ``services.frame_preview.segment_frame``, which holds the
    session's model and runs the pipeline's own ``segment_nuclei``. Kept as a
    name here because this is the Playground's worker seam.
    """
    from services.frame_preview import segment_frame
    return segment_frame(arr, prob_thresh=prob_thresh, nms_thresh=nms_thresh,
                         tile_px=tile_px, on_stage=on_stage)


def _missing_stardist_deps() -> list[str]:
    """Which of StarDist's imports are absent, without importing any of them."""
    from services.frame_preview import missing_stardist_deps
    return missing_stardist_deps()


def request_stardist_labels(arr, *, prob_thresh=None, nms_thresh=None,
                            tile_px: int = 0, on_done, on_failed=None,
                            on_stage=None):
    """Segment *arr* on a daemon thread; call ``on_done(labels)`` on the GUI
    thread when finished. Returns the bridge (keep a reference alive).

    ``on_stage(name)`` hears the phase names — the model load is the 10-30 s
    one and only happens once a session, so it is worth distinguishing from the
    segmentation that follows it.
    """
    # Resolved through the module global, not captured at import, so a test (or
    # a future caller) monkeypatching ``stardist_labels_for_frame`` is honoured.
    def _compute(reporter=None):
        return stardist_labels_for_frame(
            arr, prob_thresh=prob_thresh, nms_thresh=nms_thresh,
            tile_px=tile_px,
            on_stage=None if reporter is None else reporter.stage)

    return run_off_thread(_compute, on_done=on_done, on_failed=on_failed,
                          on_stage=on_stage)
