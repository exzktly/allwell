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

from util.aw_safe import swallowed

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


def ensure_model():
    """Load the session's StarDist model if it is not loaded, and return it.

    Split out of :func:`segment_frame` because the sweep needed the model and
    not a segmentation, and borrowing one by running the other cost **more than
    the whole optimisation it was there to enable**: measured on a noisy
    512x512 frame, the discarded ``segment_frame`` at the lowest threshold took
    0.42 s against 0.30 s for all nineteen resolves. The lowest threshold is
    also the most expensive one, since that is where NMS has the most
    candidates to consider.
    """
    global _GUI_STARDIST_MODEL
    missing = missing_stardist_deps()
    if missing:
        raise RuntimeError(_MISSING_DEPS_MSG.format(missing=", ".join(missing)))
    if _GUI_STARDIST_MODEL is None:
        import process_microscopy as pm
        pm._ensure_pkg_resources_shim()
        from stardist.models import StarDist2D
        _GUI_STARDIST_MODEL = pm._load_stardist_pretrained(
            StarDist2D, "2D_versatile_fluo")
    return _GUI_STARDIST_MODEL


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


def _resolve_at_thresholds(model, frame, thresholds, nms_thresh, should_stop,
                           on_progress) -> list[int] | None:
    """Counts via **one** inference plus a cheap resolve per threshold.

    StarDist's ``predict`` returns the probability map and the radial-distance
    stack, and ``_instances_from_prediction`` turns that pair into labels at
    whichever thresholds you ask for. So a sweep costs one network pass and *M*
    polygon resolutions rather than *M* network passes.

    Measured **through the shipped** :func:`sweep_frame`, not through this
    function in isolation, because the first version of that number described
    the loop and not the thing the app calls: ``sweep_frame`` used to run a
    whole discarded ``segment_frame`` to borrow the model load, at the *lowest*
    threshold, which is the most expensive one. That borrow cost 0.42 s against
    0.30 s for all nineteen resolves -- more than the optimisation it enabled --
    and turned a 10x loop into a 4.4x function.

    With the loader split out, on a 512x512 synthetic frame carrying 120
    nuclei, 19 thresholds from 0.05 to 0.95, ``2D_versatile_fluo`` on CPU:
    **0.31 s against 2.73 s for nineteen ``segment_nuclei`` calls -- 8.8x, with
    identical counts at every threshold.** The identity is the claim that
    matters; the ratio varies with the frame, and a noisy one emits more
    candidate polygons at the low-threshold end where NMS does the most work.

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
        # Logged, not silent (CLAUDE.md §5). Without a breadcrumb a StarDist
        # version bump that moves this private API makes every sweep five times
        # slower for ever and says nothing.
        swallowed()
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
            swallowed()
            return None
        counts.append(int(labels.max()) if getattr(labels, "size", 0) else 0)
        if on_progress is not None:
            on_progress(i + 1, len(thresholds))
    return counts


def sweep_frame(arr, thresholds, *, nms_thresh=None, tile_px: int = 0,
                on_progress=None, should_stop=None):
    """Nucleus count at each of *thresholds*, low to high. Returns a list.

    **The frame is prepared exactly as ``segment_nuclei`` prepares it**, through
    the shared ``pipeline.stardist.prepare_stardist_input``, before anything is
    handed to the network. That is not a detail. The first version of this
    function passed the corrected frame — raw camera counts — straight to
    ``model.predict``, which takes ``normalizer=None`` and assumes its input is
    already normalised. Measured against the Segment button on the same frame
    and the same grid, it returned 6 nuclei falling to 4 across the whole range
    where the truth was 86 falling to 0, **did not decline**, and drew a flat
    line the manual tells the reader means the threshold does not matter. The
    only evidence offered for that fast path had been gathered on an already
    normalised array, so the fixture had done the work the code did not.

    A near-constant field is refused rather than swept, through the same
    ``is_near_constant`` the pipeline uses. It is the classic trigger for
    StarDist's uncatchable ClipperLib abort, which in this process takes the
    whole application down — and a sweep is nineteen NMS passes per press, so
    nineteen times the exposure of one Segment press.

    Takes the fast path in :func:`_resolve_at_thresholds` where it can and falls
    back to one :func:`segment_frame` per threshold otherwise, which is what
    runs when tiling is asked for or when the private resolve API is not where
    this build expects it. Both paths now see the same prepared array, and on
    real frames the fast path's counts match the slow path's exactly.

    ``should_stop()`` is polled
    between thresholds and stops the sweep where it is; the counts gathered so
    far are returned rather than discarded, because a partial curve the user
    asked to stop is still evidence. ``on_progress(i, n)`` is called after each
    point.
    """
    from pipeline.stardist import is_near_constant, prepare_stardist_input

    grid = list(thresholds)
    if not grid:
        # Nothing asked for is nothing to load a model for. Without this the
        # borrow below ran a full inference to return an empty list.
        return []

    model = ensure_model()
    prepared = prepare_stardist_input(arr)
    if is_near_constant(prepared):
        # Refused, not swept. ``segment_nuclei`` returns zero labels here for
        # the same reason, and a curve of nineteen zeros would be declined by
        # ``curve_from_counts`` anyway — but only after nineteen unguarded
        # passes over exactly the input the guard exists for.
        return [0] * len(grid)

    if not tile_px:
        fast = _resolve_at_thresholds(model, prepared, grid, nms_thresh,
                                      should_stop, on_progress)
        if fast is not None:
            return fast

    counts: list[int] = []
    for i, thresh in enumerate(grid):
        if should_stop is not None and should_stop():
            break
        labels = segment_frame(arr, prob_thresh=float(thresh),
                               nms_thresh=nms_thresh, tile_px=tile_px)
        counts.append(int(labels.max()) if getattr(labels, "size", 0) else 0)
        if on_progress is not None:
            on_progress(i + 1, len(grid))
    return counts
