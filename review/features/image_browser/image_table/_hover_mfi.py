"""The Image Table hover readout's MFI suffix: background lookups, latest wins.

The hover readout quotes the mean fluorescence intensity of the segmented cell
under the cursor. Working that out needs the cell's segmentation mask and the
well's ``{(fov, tp): {nid: MFI}}`` index, and both are reads — on a network
drive, slow ones — so they never run in the mouse handler. The first hover over
a cell hands them to one background thread and the readout says
``MFI: loading…`` until the result lands; every later move over that cell is a
dict lookup.

Three properties hold the feature together, and each one was once missing:

* **Latest wins.** A cursor sweeping across a grid enters many cells. Only the
  cell under the cursor matters, so the runner keeps **one** pending slot: a
  new lookup replaces a queued one that has not started, and the replaced
  cell's record is dropped so hovering it again asks afresh. A FIFO queue made
  the cell under the cursor wait behind every cell already left behind.
* **Nothing outlives the window.** The runner's thread is a daemon and
  :func:`shutdown` (registered on ``ctx.runtime.image_table.mfi_stop`` and
  called by ``teardown_review_pane``) drops the pending lookup, so quitting
  after a sweep does not wait for a backlog of reads to drain.
* **A result describes the dataset that is open.** Every lookup is stamped
  with ``ImageTableState.generation``, which ``clear_caches`` bumps on every
  dataset load. A lookup writes into the memo dict it was handed on the UI
  thread, so one that lands after a swap writes into the orphaned previous
  dict rather than the new one, and a cell rendered under a previous
  generation quotes no MFI at all.

When a lookup lands while the cursor rests on its cell, the tooltip is
refreshed on the UI thread through a queued signal, so ``loading…`` does not
stay up until the mouse next moves.

**The lookup never reads a frame someone else is writing.** The row cache's
held frames are rewritten in place — ``Included`` / ``Gate_Pass`` / ``Gate_On``
by the cell gating pass on its ``QThread``, the verdict columns by the Cell
Gating panel's jobs on theirs (``review.core.progress_jobs``), ``Included`` by
manual curation on the GUI thread — and pandas does not make reading a frame
safe while another thread writes it. So the background thread is never handed
a held frame, nor the shared image cache:

* on the GUI thread, at the first hover, the well's held frame (when the row
  cache holds it) is copied — only the intensity column, the identity columns
  and every verdict (``narrow_rows.verdict_copy``) — and the image cache is
  copied shallowly; the lookup runs against a scratch context
  (``narrow_rows.scratch_context``) whose row cache holds that copy alone, so a
  well it does not hold is read from disk into the scratch cache and the real
  row cache, the shared image cache and the shared population store are never
  touched from the thread. GUI-thread writers (curation, a synchronous re-gate)
  cannot run while the GUI thread copies, and a background writer that starts
  after the copy rewrites frames the lookup does not hold;
* while a background writer is at work (``progress_jobs.frames_busy``) the copy
  is not taken at all: the readout says ``MFI: waiting for the cell update…``
  and the next hover after the writer ends asks again.

A lookup that fails says ``MFI: unavailable`` rather than dropping the suffix,
which is what background reads as: a value that could not be had is not a cell
with no value.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal

from core.safe import swallowed
from review.features.image_browser.image_table.image_compose import (
    _binary_base,
    _load_mask_array,
)

_logger = logging.getLogger(__name__)

#: Sentinel :func:`cell_mfi` returns while the cell's mask and MFI index are
#: still being read in the background, so the readout can say so rather than
#: look like a cell with no MFI.
MFI_PENDING = object()
#: Sentinel :func:`cell_mfi` returns while a background writer is rewriting the
#: cached frames, so no lookup was started: the next hover asks again.
MFI_WAITING = object()
#: Sentinel :func:`cell_mfi` returns when the lookup failed — the value could
#: not be had, which is not the same as a cell (or background) with none.
MFI_UNAVAILABLE = object()


def mfi_suffix(mfi) -> str:
    """The readout's MFI suffix for what :func:`cell_mfi` returned."""
    if mfi is MFI_PENDING:
        return "  MFI: loading…"
    if mfi is MFI_WAITING:
        return "  MFI: waiting for the cell update…"
    if mfi is MFI_UNAVAILABLE:
        return "  MFI: unavailable"
    return f"  MFI:{mfi:.1f}" if mfi is not None else ""


class LatestOnlyRunner:
    """One daemon thread and a single pending slot: the newest job wins.

    :meth:`submit` replaces a job that is still waiting (calling its
    ``on_dropped`` on the submitting thread) rather than queueing behind it; a
    job already running finishes. :meth:`shutdown` drops the pending job and
    stops the thread after the running one, and later submits raise.
    """

    def __init__(self, name: str = "image-table-mfi") -> None:
        self._name = name
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._pending: tuple | None = None
        self._closed = False
        self._thread: threading.Thread | None = None

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    def submit(self, job, on_dropped=None) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("the hover MFI runner is shut down")
            dropped, self._pending = self._pending, (job, on_dropped)
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._loop, name=self._name, daemon=True)
                self._thread.start()
        self._wake.set()
        _call_dropped(dropped)

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            dropped, self._pending = self._pending, None
        self._wake.set()
        _call_dropped(dropped)

    def _loop(self) -> None:
        while True:
            self._wake.wait()
            with self._lock:
                self._wake.clear()
                if self._closed:
                    return
                item, self._pending = self._pending, None
            if item is None:
                continue
            try:
                item[0]()
            except Exception:
                swallowed("image table: hover MFI lookup")


def _call_dropped(item) -> None:
    if item is not None and item[1] is not None:
        try:
            item[1]()
        except Exception:
            swallowed("image table: dropping a superseded hover MFI lookup")


_RUNNER: LatestOnlyRunner | None = None


def _submit_mfi(job, on_dropped=None) -> None:
    """Run *job* on the hover-MFI runner. A seam: the tests run *job* inline."""
    global _RUNNER
    if _RUNNER is None:
        _RUNNER = LatestOnlyRunner()
    _RUNNER.submit(job, on_dropped)


def shutdown() -> None:
    """Drop the pending lookup and stop the runner (app teardown)."""
    global _RUNNER
    runner, _RUNNER = _RUNNER, None
    if runner is not None:
        runner.shutdown()


class _Landed(QObject):
    """Carries "a lookup for this label landed" from the runner to the UI thread.

    Built on the UI thread (first hover), so a queued connection delivers the
    emit from the runner's thread on the UI thread's event loop.
    """

    landed = Signal(object)


_NOTIFIER: _Landed | None = None


def _notifier() -> _Landed | None:
    global _NOTIFIER
    if _NOTIFIER is None:
        try:
            n = _Landed()
            n.landed.connect(refresh_resting_readout)
            _NOTIFIER = n
        except Exception:
            swallowed("image table: hover MFI notifier")
    return _NOTIFIER


def _cursor_rests_on(label) -> bool:
    """Whether the mouse is over *label* now. A seam for the tests."""
    under = getattr(label, "underMouse", None)
    return bool(under()) if callable(under) else False


def refresh_resting_readout(label) -> None:
    """Re-show *label*'s readout if the cursor is still on it (UI thread)."""
    try:
        from shiboken6 import isValid
        if hasattr(label, "metaObject") and not isValid(label):
            return
    except ImportError:
        pass
    if not _cursor_rests_on(label):
        return
    filt = getattr(label, "_image_table_event_filter", None)
    if filt is None:
        return
    from PySide6.QtGui import QCursor
    try:
        gp = QCursor.pos()
        lp = label.mapFromGlobal(gp)
        filt._show_tip(label, gp, filt._hover_text(label, int(lp.x()), int(lp.y())))
    except Exception:
        swallowed("image table: refreshing the hover readout")


def _generation(ctx) -> int:
    st = getattr(getattr(ctx, "state", None), "image_table", None)
    return int(getattr(st, "generation", 0) or 0)


def _register_stopper(ctx) -> None:
    runtime = getattr(getattr(ctx, "runtime", None), "image_table", None)
    if runtime is not None:
        try:
            runtime.mfi_stop = shutdown
        except Exception:
            swallowed("image table: registering the hover MFI stopper")


def _frames_busy(ctx) -> bool:
    """Whether a background writer may be rewriting the cached frames now."""
    if getattr(getattr(ctx, "runtime", None), "jobs", None) is None:
        return False
    from review.core import progress_jobs
    return progress_jobs.frames_busy(ctx)


def _lookup_context(ctx, well: str, chan: str):
    """The context the background lookup reads through (built on the UI thread).

    A scratch context whose row cache holds a private copy of *well*'s held
    frame (or nothing, when the row cache does not hold it), and whose
    population store is its own, so the thread reads no frame another thread
    may be rewriting and stores nothing under a key read after an edit it did
    not see. A context with no dataset (a stub) is returned as given.
    """
    state = getattr(ctx, "state", None)
    dataset = getattr(state, "dataset", None)
    if dataset is None:
        return ctx
    from review.data.narrow_rows import scratch_context, verdict_copy
    from review.data.population_store import PopulationStore

    mfi_column = _review_collect().mfi_column

    rows = getattr(dataset, "cache", None)
    held = rows.get(well) if rows is not None else None
    private: dict = {}
    if held is not None:
        col = mfi_column(ctx, _binary_base(chan))
        private[well] = verdict_copy(held, {col} if col else set())
    return scratch_context(ctx, private, populations=PopulationStore())


def cell_mfi(ctx, label, chan: str, well: str, tp: str, fov: str,
             img_x: int, img_y: int) -> float | object | None:
    """MFI of the segmented cell under full-image ``(img_x, img_y)``.

    Returns the value; ``None`` for background / overlay / ratio / nuc-seg
    tiles (no intensity column), a frame with no mask, or a cell rendered for a
    previous dataset; :data:`MFI_PENDING` while the lookup is in flight;
    :data:`MFI_WAITING` while a background writer is rewriting the frames (no
    lookup started); :data:`MFI_UNAVAILABLE` when the lookup failed. Runs in
    the mouse handler on the UI thread, so it never touches the disk.
    """
    st = getattr(getattr(ctx, "state", None), "image_table", None)
    gen = _generation(ctx)
    rec = getattr(label, "_mfi_rec", None)
    if rec is None:
        if _frames_busy(ctx):
            return MFI_WAITING
        rec = {"done": False, "mask": None, "frame": None, "gen": gen,
               "failed": False}
        label._mfi_rec = rec  # type: ignore[attr-defined]
        # Captured here, on the UI thread: a swap replaces both containers, so
        # a lookup that lands after it writes only into the orphaned old ones.
        # The image cache is copied: the thread's mask scan writes into it, and
        # the shared one is the UI-thread Generate's.
        cache = getattr(st, "image_cache", None)
        cache = dict(cache) if isinstance(cache, dict) else {}
        memo = getattr(st, "mfi_index", None)
        memo = memo if isinstance(memo, dict) else {}
        notifier = _notifier()
        try:
            lookup_ctx = _lookup_context(ctx, well, chan)
        except Exception:
            swallowed("image table: copying the hovered well's rows")
            rec["done"] = rec["failed"] = True
            lookup_ctx = None

        def _job() -> None:
            try:
                import numpy as _np
                mask = _load_mask_array(lookup_ctx, cache, well, tp, fov)
                if mask is not None:
                    rec["mask"] = _np.asarray(mask)
                    rec["frame"] = mfi_frame(lookup_ctx, well, chan, tp, fov, memo=memo)
            except Exception:
                # Said on the readout ("MFI: unavailable"), not only here.
                swallowed("image table: hover MFI lookup")
                rec["failed"] = True
            finally:
                rec["done"] = True
                if notifier is not None:
                    notifier.landed.emit(label)

        def _dropped() -> None:
            # Superseded before it ran: forget the record, so hovering this cell
            # again asks afresh instead of showing "loading…" for ever.
            if getattr(label, "_mfi_rec", None) is rec:
                label._mfi_rec = None  # type: ignore[attr-defined]

        if lookup_ctx is not None:
            try:
                _submit_mfi(_job, on_dropped=_dropped)
                _register_stopper(ctx)
            except Exception:
                swallowed()  # runner shut down (window closing): no MFI
                rec["done"] = True
    if rec.get("gen", gen) != gen:
        return None        # this cell was drawn for a dataset no longer open
    if not rec["done"]:
        return MFI_PENDING
    if rec.get("failed"):
        return MFI_UNAVAILABLE
    mask = rec["mask"]
    if mask is None:
        return None
    try:
        nid = int(mask[int(img_y), int(img_x)])
    except Exception:
        swallowed()  # out-of-bounds / non-numeric mask cell → no MFI
        return None
    if nid <= 0:
        return None
    frame = rec["frame"]
    return frame.get(nid) if frame else None


def _review_collect():
    """``review_image.collect``, imported on first use (the one deferred import
    of it here: it pulls the Segmentation tab's data layer, which a hover that
    never lands on a cell does not need)."""
    from review.features.image_browser.review_image import collect
    return collect


def mfi_frame(ctx, well: str, chan: str, tp: str, fov: str, *, memo: dict | None = None) -> dict:
    """``{nid: MFI}`` for one frame of *well*, from the memoized per-well index.

    The index (``{(fov, tp): {nid: MFI}}`` for *chan*'s base channel, any
    " (binary)" variant suffix stripped) is built on first use and held in
    *memo* — ``ctx.state.image_table.mfi_index`` unless the caller captured one
    — which ``clear_caches`` replaces on a dataset swap; a ctx without that slot
    (a stub) builds it every call. The CSV row keys are normalised and the cell
    carries the raw image-filename tokens, so both are normalised before the
    lookup, as the binary-mask render does.
    """
    collect = _review_collect()
    review_mfi_index, review_norm_fov = collect.review_mfi_index, collect.review_norm_fov
    from review.core.ui_helpers import norm_timepoint
    base = _binary_base(chan)
    if memo is None:
        st = getattr(getattr(ctx, "state", None), "image_table", None)
        memo = getattr(st, "mfi_index", None)
        if not isinstance(memo, dict):
            memo = {}
    key = (well, str(base).strip().lower())
    if key not in memo:
        memo[key] = review_mfi_index(ctx, well, channel=base)
    return memo[key].get((review_norm_fov(fov), norm_timepoint(ctx, tp))) or {}
