"""Segment one frame the way the pipeline would, from inside the GUI.

The Review pane's Playground can run StarDist on the frame it is displaying, so
the two detection thresholds can be chosen by looking at the result instead of by
re-running a plate. This module is that inference: it holds the GUI session's
single loaded model and calls ``pipeline.stardist.segment_nuclei`` — the function
a batch worker calls — so what the Playground shows is what a run would produce.

It lives in ``services`` rather than beside the Playground for two reasons. It is
Qt-free (``tests/test_architecture_contracts.py::test_services_have_no_qt_imports``)
and free of ``review`` imports, so it is testable and reusable without a GUI;
and keeping it out of a Qt module makes the import discipline below explicit
rather than incidental.

**TensorFlow and StarDist are imported inside :func:`segment_frame`, never at
module scope.** The Playground's controller imports its worker — and through it
this module — at GUI start-up, so a module-scope TF import would add tens of
seconds and gigabytes to launching the app for every user, including everyone who
never presses Segment.
"""

from __future__ import annotations

# The loaded model, at most once per GUI session. Module-level rather than held
# on any state object: it survives a dataset swap, must never be pickled, and
# reloading it would cost the user another 10-30 s.
_GUI_STARDIST_MODEL = None

_MISSING_DEPS_MSG = (
    "StarDist is not installed in this environment ({missing} missing), so the "
    "preview cannot segment. The thresholds still apply to a pipeline run."
)


def missing_stardist_deps() -> list[str]:
    """Which of StarDist's imports are absent — probed, never imported.

    Reported to the user by name. A preview that simply failed to draw an
    overlay would look like a bug in the segmentation rather than a missing
    install.
    """
    import importlib.util
    return [name for name in ("stardist", "csbdeep", "tensorflow")
            if importlib.util.find_spec(name) is None]


#: The two phases of a first-of-session segmentation, named for the caller to
#: display. The first is the 10-30 s one and only happens once, so a UI that
#: reports them identically leaves the user watching an unexplained stall.
STAGE_LOADING = "loading TensorFlow and the StarDist model"
STAGE_SEGMENTING = "segmenting"


def segment_frame(arr, *, prob_thresh=None, nms_thresh=None, tile_px: int = 0,
                  on_stage=None):
    """Segment one displayed frame and return its int32 label image.

    Runs the pipeline's own :func:`pipeline.stardist.segment_nuclei`, so the
    input scrubbing, the ``[0, 1]`` clamp and the near-constant-field refusal
    that make StarDist's uncatchable ClipperLib abort unreachable all apply here
    too — which matters more in a GUI process than in a pipeline worker, because
    an abort here would take the whole application down rather than one process.

    ``prob_thresh`` / ``nms_thresh`` of ``None`` mean the pretrained model's own
    values, the same spelling the CLI and the Analyze form use.

    ``on_stage(name)`` — a plain callable, so this module stays Qt-free — is
    called with :data:`STAGE_LOADING` and :data:`STAGE_SEGMENTING` as each
    begins. Only the first run of a session loads anything; after that the two
    arrive back to back and the caller simply shows the second.
    """
    missing = missing_stardist_deps()
    if missing:
        raise RuntimeError(_MISSING_DEPS_MSG.format(missing=", ".join(missing)))

    global _GUI_STARDIST_MODEL
    import process_microscopy as pm
    from pipeline.stardist import StarDistParams, segment_nuclei

    if _GUI_STARDIST_MODEL is None:
        if on_stage is not None:
            on_stage(STAGE_LOADING)
        pm._ensure_pkg_resources_shim()
        from stardist.models import StarDist2D
        # The pipeline workers' loader: recovers from a cache left truncated by
        # an interrupted download, and serialises against a pipeline run that is
        # fetching the same weights right now.
        _GUI_STARDIST_MODEL = pm._load_stardist_pretrained(
            StarDist2D, "2D_versatile_fluo")

    if on_stage is not None:
        on_stage(STAGE_SEGMENTING)
    return segment_nuclei(
        arr, model=_GUI_STARDIST_MODEL,
        params=StarDistParams(prob_thresh=prob_thresh, nms_thresh=nms_thresh,
                              tile_px=int(tile_px or 0)))


def model_prob_threshold() -> float | None:
    """The pretrained model's own probability threshold, or ``None``.

    The number the Analyze form's default is *against* — the form ships 0.2
    where the model says ~0.479 — so a plot marking only where the user is
    cannot answer whether their value is sensible. Read off the loaded model
    rather than written down here: a literal would be a fourth copy of a
    number that lives in the weights' own ``thresholds.json``, and would go
    stale the day anyone changes model.

    ``None`` until something has loaded a model this session, which is the
    honest answer rather than a guess.
    """
    thr = getattr(_GUI_STARDIST_MODEL, "thresholds", None)
    prob = getattr(thr, "prob", None)
    try:
        return float(prob) if prob is not None else None
    except (TypeError, ValueError):
        return None


def centre_crop(arr, size: int):
    """The central ``size × size`` square of *arr*, or *arr* when it is smaller.

    The sweep's affordability fallback. Kept here rather than in the caller so
    the crop the readout names and the crop the sweep measured are the same
    number by construction.
    """
    import numpy as np

    a = np.asarray(arr)
    size = int(size)
    if size <= 0 or a.ndim < 2:
        return a
    h, w = a.shape[0], a.shape[1]
    if h <= size and w <= size:
        return a
    top = max(0, (h - size) // 2)
    left = max(0, (w - size) // 2)
    return a[top:top + min(size, h), left:left + min(size, w)]


def _resolve_at_thresholds(model, frame, thresholds, nms_thresh, should_stop,
                           on_progress) -> list[int] | None:
    """Counts via **one** inference plus a cheap resolve per threshold.

    StarDist's ``predict`` returns the probability map and the radial-distance
    stack, and ``_instances_from_prediction`` turns that pair into labels at
    whichever thresholds you ask for. So a sweep costs one network pass and *M*
    polygon resolutions rather than *M* network passes.

    Measured on 512x512 synthetic frames, 19 thresholds from 0.05 to 0.95,
    ``2D_versatile_fluo`` on CPU, against the same nineteen full
    ``predict_instances`` calls:

    * 120 nuclei -- 3.05 s full, 0.30 s split: **10.1x**
    * 90 nuclei  -- 2.55 s full, 0.50 s split: **5.1x**

    The count vectors were **identical at every threshold** in both runs, which
    is the claim that matters; the ratio is the one that varies, and both
    numbers are recorded rather than the better one. Those frames are clean
    synthetic signal, so the resolve cost is a floor: a noisy real frame emits
    more candidate polygons at the low-threshold end, where NMS does the most
    work. Expect less than 5x there, not more than 10x.

    Returns ``None`` rather than raising when the split API is not available in
    this StarDist build -- ``_instances_from_prediction`` is private, so a
    version bump may move or rename it, and the caller falls back to the slow
    shape instead of failing. Tiling is deliberately not routed here: the
    measurement above was taken untiled, and a fast path that has never been
    measured in the configuration it is used in is the branch-that-exists
    problem this module already has one comment about.
    """
    import numpy as np

    try:
        pred = model.predict(frame)
        prob, dist = pred[0], pred[1]
    except Exception:
        return None

    counts: list[int] = []
    for i, thresh in enumerate(thresholds):
        if should_stop is not None and should_stop():
            break
        try:
            labels, _ = model._instances_from_prediction(
                np.asarray(frame).shape, prob, dist,
                prob_thresh=float(thresh), nms_thresh=nms_thresh)
        except Exception:
            # Part way through is still a failure of the whole fast path: a
            # curve half of whose points came from one code path and half from
            # another is not one measurement.
            return None
        counts.append(int(labels.max()) if getattr(labels, "size", 0) else 0)
        if on_progress is not None:
            on_progress(i + 1, len(thresholds))
    return counts


def sweep_frame(arr, thresholds, *, nms_thresh=None, tile_px: int = 0,
                crop_px: int = 0, on_progress=None, should_stop=None):
    """Nucleus count at each of *thresholds*, low to high. Returns a list.

    Takes the fast path in :func:`_resolve_at_thresholds` where it can -- one
    inference, *M* resolves, measured at 5-10x on clean frames -- and falls
    back to one full :func:`segment_frame` per threshold otherwise, which is
    what runs when tiling is asked for or when the private resolve API is not
    where this build expects it.

    The slow path goes through ``segment_frame``, so a sweep and the Segment
    button cannot disagree about what a threshold produces; the fast path was
    measured against that same call and returned identical counts at every
    threshold.

    ``crop_px`` runs the sweep on a centre crop, which is what makes a large
    frame bearable on the slow path. The caller passes the same number to
    :func:`review.domain.threshold_sweep.curve_from_counts`, so the readout
    states the crop it was actually measured on.

    ``should_stop()`` is polled between thresholds and stops the sweep where it
    is; the counts gathered so far are returned rather than discarded, because
    a partial curve the user asked to stop is still evidence. ``on_progress(i,
    n)`` is called after each point.
    """
    missing = missing_stardist_deps()
    if missing:
        raise RuntimeError(_MISSING_DEPS_MSG.format(missing=", ".join(missing)))

    frame = centre_crop(arr, crop_px) if crop_px else arr
    grid = list(thresholds)

    if not tile_px:
        # Loading the model is what ``segment_frame`` does on its first call;
        # borrow it rather than duplicating the loader, then use the model
        # directly for the split API.
        segment_frame(frame, prob_thresh=grid[0] if grid else None,
                      nms_thresh=nms_thresh)
        fast = _resolve_at_thresholds(_GUI_STARDIST_MODEL, frame, grid,
                                      nms_thresh, should_stop, on_progress)
        if fast is not None:
            return fast

    counts: list[int] = []
    for i, thresh in enumerate(grid):
        if should_stop is not None and should_stop():
            break
        labels = segment_frame(frame, prob_thresh=float(thresh),
                               nms_thresh=nms_thresh, tile_px=tile_px)
        counts.append(int(labels.max()) if getattr(labels, "size", 0) else 0)
        if on_progress is not None:
            on_progress(i + 1, len(grid))
    return counts
