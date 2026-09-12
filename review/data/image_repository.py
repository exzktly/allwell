"""ImageRepository — the read facade over image discovery, resolution and decode.

The row path has ``WellRepository``; this is its counterpart for pixels. Three
modules make up the de-facto image repository and a feature that wants a picture
has had to know all three:

* **discovery** — ``image_discovery.find_well_images_and_masks`` walks the
  dataset's layouts and returns four ``(fov, tp) -> ImgRef`` inventories
  (fluorescence, overlay, mask, top-hat corrected);
* **resolution** — ``frame_load`` picks the ref a ``(well, channel, timepoint,
  fov)`` names, with the top-hat-to-raw fallback that a partially corrected run
  needs;
* **decode** — ``image_discovery.open_imgref_as_array`` turns a ref into a numpy
  array, memoized in a bounded LRU behind the zip-handle cache.

Every caller of the discovery step also had to assemble the same four fields off
``ctx.state.dataset`` — ``data_dir``, ``in_dir``, ``fov_tp_extractor``,
``pipeline_info`` — and get all four right; the repository reads them itself, so
a call site names the well and the channel and nothing else.

Reached as ``ctx.state.images``, attached in ``ViewerContext.__post_init__``
beside ``ctx.state.repo`` and for the same reasons: the DI bundle is the natural
wiring point, and putting it on ``AppState`` rather than on ``ViewerContext``
keeps the frozen service-accessor surface untouched.

**Two things it deliberately does not do.**

It owns no inventory cache. The three grid features pass their own dict to
``frame_load`` and their lifetimes genuinely differ: ``image_table`` and
``playground`` keep one for the session (dropped on dataset load), while
``cell_browser``'s worker keeps one **per page**, because it reads on a
background thread and a dataset swap mid-read must not leave it serving refs
into an archive that has been replaced. Folding those into one cache owned here
would quietly give the worker the session lifetime — a correctness change
wearing a refactor's clothes.

And it does not wrap ``frame_load.load_frame_array`` / ``load_mask_array``,
which are the grid features' door and already take the context and that cache
explicitly. Putting them behind a context-bound facade would add a hop without
removing one, and would break a path that is deliberately context-free:
``image_compose._load_mask_array`` is called with ``ctx=None`` by the render
tests, and reads the ctx only afterwards, for the inclusion filter.

Qt-free, like the rest of ``data/``. Thread-safe to the same degree the
functions behind it are: ``array`` and ``frame`` are called from worker threads
today, and hold no state of their own beyond the context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np

    from review.data.image_discovery import ImgRef


class ImageRepository:
    """Read port over the currently-open dataset's images."""

    def __init__(self, ctx) -> None:
        self._ctx = ctx

    # ── discovery ─────────────────────────────────────────────────────────

    def images_for_well(self, well_label: str, channel: str):
        """The ``(fluor, overlay, mask, tophat_fluor)`` inventories for a well.

        Each is a ``(fov, tp) -> ImgRef`` dict. The dataset fields the scan needs
        — the output and input directories, the filename-schema extractor and
        ``pipeline_info`` — are read from ``ctx.state.dataset`` here rather than
        assembled at the call site.
        """
        from review.data.image_discovery import find_well_images_and_masks

        ds = self._ctx.state.dataset
        return find_well_images_and_masks(
            ds.data_dir,
            well_label,
            fluor_token=channel,
            in_dir=ds.in_dir,
            _fov_tp_extractor=ds.fov_tp_extractor,
            _pipeline_info=ds.pipeline_info,
        )

    # ── decode ────────────────────────────────────────────────────────────

    @staticmethod
    def array(ref: ImgRef | None, *, greyscale: bool = False) -> np.ndarray | None:
        """Decode *ref* at full native bit depth, memoized.

        A returned array is shared by reference — **treat it as read-only**; the
        decode cache hands the same object to every caller that asks for the
        same image.

        Static because a decode needs no dataset context: a resolved ``ImgRef``
        already names the file or the zip member completely. Reached through the
        repository anyway so image access has one door, and so the cache-bypass
        guard has one place to look.
        """
        from review.data.image_discovery import open_imgref_as_array

        return open_imgref_as_array(ref, greyscale=greyscale)

    # ── lifecycle ─────────────────────────────────────────────────────────

    @staticmethod
    def invalidate() -> None:
        """Drop the decoded-array and zip-handle caches.

        Both, together: a dataset load that dropped one and kept the other would
        leave the app deciding between a stale picture and a stale archive
        handle. The zip cache also re-opens on an mtime change by itself, so this
        is belt and braces for the case where a new dataset reuses a path.
        """
        from review.data.image_discovery import invalidate_decode_cache
        from review.data.zipfile_cache import invalidate as invalidate_zip_cache

        invalidate_decode_cache()
        invalidate_zip_cache()
