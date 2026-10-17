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


def cell_mfi(ctx, label, chan: str, well: str, tp: str, fov: str,
             img_x: int, img_y: int) -> float | object | None:
    """MFI of the segmented cell under full-image ``(img_x, img_y)``.

    Returns the value, ``None`` for background / overlay / ratio / nuc-seg tiles
    (no intensity column), any decode/lookup failure, or a cell rendered for a
    previous dataset; or :data:`MFI_PENDING` while the lookup is in flight.
    Runs in the mouse handler on the UI thread, so it never touches the disk.
    """
    st = getattr(getattr(ctx, "state", None), "image_table", None)
    gen = _generation(ctx)
    rec = getattr(label, "_mfi_rec", None)
    if rec is None:
        rec = {"done": False, "mask": None, "frame": None, "gen": gen}
        label._mfi_rec = rec  # type: ignore[attr-defined]
        # Captured here, on the UI thread: a swap replaces both containers, so
        # a lookup that lands after it writes only into the orphaned old ones.
        cache = getattr(st, "image_cache", None)
        cache = cache if isinstance(cache, dict) else {}
        memo = getattr(st, "mfi_index", None)
        memo = memo if isinstance(memo, dict) else {}
        notifier = _notifier()

        def _job() -> None:
            try:
                import numpy as _np
                mask = _load_mask_array(ctx, cache, well, tp, fov)
                if mask is not None:
                    rec["mask"] = _np.asarray(mask)
                    rec["frame"] = mfi_frame(ctx, well, chan, tp, fov, memo=memo)
            except Exception:
                swallowed()  # best-effort; the tooltip drops MFI on failure
            finally:
                rec["done"] = True
                if notifier is not None:
                    notifier.landed.emit(label)

        def _dropped() -> None:
            # Superseded before it ran: forget the record, so hovering this cell
            # again asks afresh instead of showing "loading…" for ever.
            if getattr(label, "_mfi_rec", None) is rec:
                label._mfi_rec = None  # type: ignore[attr-defined]

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
    from review.features.image_browser.review_image.collect import (
        review_mfi_index,
        review_norm_fov,
    )
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
