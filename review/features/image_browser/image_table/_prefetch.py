"""The Image Table's parallel decode: every distinct image a Generate (or a Save) will draw, read ahead of the
render loop on a thread pool, with its progress reported as each read completes.

Out of ``controller.py``, which re-exports :func:`_prefetch_cell_arrays` so its callers and tests resolve the same
name. Qt-free: the progress it reports goes to whatever object it is handed.
"""

from __future__ import annotations

from core.safe import swallowed
from review.features.image_browser.image_table.grid_layout import _find_ratio_for_channel
from review.features.image_browser.image_table.image_compose import _is_nuc_seg, _load_array, _row_display_opts


def _prefetch_cell_arrays(ctx, cells, cache, progress=None) -> None:
    """Decode every distinct (well, channel, timepoint, fov) image in parallel.

    The first Generate after a dataset load (or after the cache is invalidated)
    has to read every cell's image off disk; doing that sequentially on the GUI
    thread is the slow part. Threads help because ``zipfile``/``tifffile``/``PIL``
    release the GIL for the C-level inflate+decode, and the per-zip-handle
    serialization in ``data/zipfile_cache.py`` keeps concurrent reads safe (same
    well → one handle, serialized; different wells → parallel).

    Warms every distinct cell's own (timepoint, fov) — not just one
    representative per well+channel — because a grid that varies timepoint or
    FOV per cell (the Image Table's core purpose: a facet per timepoint, a
    facet per FOV, a full-plate walk, …) otherwise leaves most cells' actual
    pixel decode to happen later, sequentially, on the GUI thread inside
    ``_compute_auto_luts`` and the render loop — exactly the two passes this
    prefetch exists to make unnecessary. Dedupes against
    ``ctx.state.image_table.prefetched_cells`` (not ``cache``, which holds
    per-well+channel ref-dicts, not a per-image marker), so a second Generate
    on an unchanged grid submits nothing to the thread pool at all rather than
    just cheaply re-hitting ``_load_array``'s own caches. Ratio channels are
    skipped (they compose from per-channel arrays, which this still warms).
    Best-effort: any failure leaves the sequential in-loop load as the
    fallback.

    *progress*, if given (``_load_progress.LoadProgress``), is told how many
    images there are to read and ticked as each one completes — on this, the
    GUI, thread, which waits on the pool here, so the bar can repaint between
    completions while the reads themselves run in the workers.
    """
    warmed = ctx.state.image_table.prefetched_cells
    todo: dict = {}
    for r, row in enumerate(cells):
        for cell in row:
            well = cell["well"]
            chan = cell["chan"]
            tp = cell["tp"]
            fov = cell["fov"]
            if not (well and chan and tp):
                continue
            if _find_ratio_for_channel(ctx, chan) is not None:
                continue
            # raw vs corrected is a per-channel render option (channel_opts).
            use_tophat = _row_display_opts(ctx, r, chan)[0]
            th = bool(use_tophat) and not _is_nuc_seg(chan)
            variant = "tophat" if th else "raw"
            key = (well, chan.lower(), variant, str(tp), str(fov))
            if key in warmed or key in todo:
                continue
            todo[key] = (well, chan, tp, fov, th)
    if len(todo) < 2:
        return  # nothing to gain from a pool for 0–1 reads
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _warm(spec) -> bool:
        well, chan, tp, fov, th = spec
        try:
            return _load_array(ctx, cache, well, chan, tp, fov, use_tophat=th) is not None
        except Exception:
            swallowed()  # in-loop load (with its own handling) is the fallback
            return False

    keys = list(todo)
    ok: list = [False] * len(keys)
    if progress is not None:
        progress.phase("Loading images", len(keys))
    try:
        with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 4))) as ex:
            futures = {ex.submit(_warm, todo[k]): i for i, k in enumerate(keys)}
            for fut in as_completed(futures):
                ok[futures[fut]] = fut.result()
                if progress is not None:
                    progress.tick()
    except Exception:
        import logging as _logging
        _logging.getLogger("review.image_table").debug(
            "image_table: parallel prefetch failed", exc_info=True)
    # Mark only the keys that actually decoded. Marking a failure would skip it
    # on every later Generate too, which is wrong for the failures that are not
    # permanent -- a drive that stalled mid-pass, a file still being written.
    # The cost of retrying a genuinely missing image is one more scan per
    # Generate; the cost of not retrying is a cell that never comes back.
    warmed.update(k for k, good in zip(keys, ok) if good)
