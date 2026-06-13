"""Playground tab — reference-well field estimation (empty_well / basic).

The ``empty_well`` / ``basic`` correction modes need a per-channel field built
from a reference well's frames. ``build_reference_fields`` is the pure (IO-only,
no Qt) compute — it loads that well's RAW frames via the same image_table /
image_discovery loaders the Playground already uses and calls the pipeline's
estimators (``estimate_background_field`` / ``estimate_basic_flatfield``), so the
math matches a batch run. ``request_reference_fields`` runs it on a daemon thread
and marshals the result back to the GUI thread through a queued-signal bridge
(the worker thread has no Qt event loop — same constraint as the smFISH worker).
"""

from __future__ import annotations

import threading

import numpy as np
from PySide6.QtCore import QObject, Signal


def build_reference_fields(app, well: str, chan_lower: str, *,
                           flatfield_mode: str, background_method: str,
                           max_samples: int = 30):
    """Estimate ``(ff_field, bg_field)`` from *well*'s raw frames for a channel.

    ``ff_field`` is the mean-1 illumination field (for ``basic``, or B/mean(B)
    for the combined empty-well case); ``bg_field`` is the un-normalized additive
    background (for ``empty_well`` background). Either is None when the chosen
    modes don't need it. Returns ``(None, None)`` when no frames are found.
    """
    import process_microscopy as pm
    from core.flatfield import estimate_basic_flatfield, normalize_flatfield
    from well_viewer.data.image_discovery import open_imgref_as_array
    from well_viewer.features.image_table.controller import _load_well_channel

    refs = _load_well_channel(app, {}, well, chan_lower, use_tophat=False)
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


def request_reference_fields(app, well: str, chan_lower: str, *,
                             flatfield_mode: str, background_method: str,
                             on_done, on_failed=None, max_samples: int = 30):
    """Build reference fields on a daemon thread; call ``on_done(well,
    chan_lower, ff_field, bg_field)`` on the GUI thread when finished. Returns
    the bridge (keep a reference alive for the duration)."""
    bridge = _ReferenceBridge()
    bridge.done.connect(lambda payload: on_done(*payload))
    if on_failed is not None:
        bridge.failed.connect(on_failed)

    def _run() -> None:
        try:
            ff, bg = build_reference_fields(
                app, well, chan_lower, flatfield_mode=flatfield_mode,
                background_method=background_method, max_samples=max_samples)
            bridge.done.emit((well, chan_lower, ff, bg))
        except Exception as exc:  # never raise off the GUI thread
            bridge.failed.emit(str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return bridge
