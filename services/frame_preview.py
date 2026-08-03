"""Segment one frame the way the pipeline would, from inside the GUI.

The Review pane's Playground can run StarDist on the frame it is displaying, so
the two detection thresholds can be chosen by looking at the result instead of by
re-running a plate. This module is that inference: it holds the GUI session's
single loaded model and calls ``pipeline.stardist.segment_nuclei`` — the function
a batch worker calls — so what the Playground shows is what a run would produce.

It lives in ``services`` rather than beside the Playground for two reasons. It is
Qt-free (``tests/test_architecture_contracts.py::test_services_have_no_qt_imports``)
and free of ``well_viewer`` imports, so it is testable and reusable without a GUI;
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
