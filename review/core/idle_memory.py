"""Give Review's caches back when the app has been in the background for a while.

Review keeps what it has read so a redraw does not read it again: the well rows
(``review/data/row_cache.py``, up to a quarter of physical memory), the
threshold-free populations built from them (``review/data/population_store.py``,
up to an eighth), the decoded image pixels (``review/data/image_discovery.py``,
up to 15% of memory and at most 2 GiB) and the nanowell frames the Image Table
read. All of it grows with the dataset, and all of it used to be held for as
long as the dataset stayed open, whether or not anyone was looking at the
window. On Windows memory a process holds and does not touch is moved to the
page file, so a large plate left open in the background held gigabytes of page
file that only quitting the app gave back.

So once the application has been inactive — no window of it in the foreground,
which includes a locked screen — for :func:`idle_minutes` minutes, those caches
are emptied. Nothing is lost: every one of them is a cache of something on
disk, and each is refilled the way a bounded cache already refills after an
eviction — ``data_loading.get_rows`` reads a well back (about 0.22 s from its
Parquet cache), a population is rebuilt from the rows, a frame is decoded
again. The first redraw after coming back pays for what it needs, and only
that. What the live readouts count over the held wells says so in their own
text while the wells are being read back (``data_loading.held_wells_note``).

Not done while something is still filling the caches — a dataset load holding
the plots, or a gating pass running — because emptying them under that work
would only make it read the same wells twice; the timer starts again instead.
A pinned well (one a projector is working on) is kept.

``ALLWELL_IDLE_RELEASE_MIN`` sets the minutes (default :data:`DEFAULT_IDLE_MIN`);
``0`` turns the release off.
"""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication

from core.safe import swallow

_logger = logging.getLogger("review")

#: Minutes in the background before the caches are released, unless the environment says otherwise.
DEFAULT_IDLE_MIN = 5.0
IDLE_ENV = "ALLWELL_IDLE_RELEASE_MIN"

_installed: _IdleReleaser | None = None


def idle_minutes() -> float:
    """Minutes of inactivity before a release; ``0`` when the release is turned off."""
    raw = os.environ.get(IDLE_ENV, "").strip()
    if not raw:
        return DEFAULT_IDLE_MIN
    try:
        value = float(raw)
    except ValueError:
        _logger.warning("%s=%r is not a number of minutes; using %s", IDLE_ENV, raw, DEFAULT_IDLE_MIN)
        return DEFAULT_IDLE_MIN
    return max(0.0, value)


def busy(ctx) -> bool:
    """True while a load or a gating pass is still filling the caches."""
    from review.core import plot_hold
    if plot_hold.held_for(ctx):
        return True
    worker = getattr(getattr(getattr(ctx, "runtime", None), "cell_gating", None), "worker", None)
    return worker is not None and worker.isRunning()


def _drop_image_table_frames(ctx) -> int:
    """Drop the pixel arrays the Image Table holds, keeping its file inventories; return the bytes dropped.

    The inventories (which files hold which well's frames) are kept: they are
    small, and re-scanning them means re-walking every well's archives.
    """
    import numpy as np

    cache = getattr(getattr(getattr(ctx, "state", None), "image_table", None), "image_cache", None)
    if not isinstance(cache, dict):
        return 0
    freed = 0
    for key in list(cache):
        value = cache.get(key)
        if isinstance(value, np.ndarray):
            freed += int(value.nbytes)
            del cache[key]
        elif isinstance(key, tuple) and key and key[0] == "nanowell-frame-of":
            del cache[key]
    return freed


def release_idle_memory(ctx) -> int:
    """Empty Review's dataset caches; return the bytes they held. See the module docstring."""
    from review.data.image_discovery import invalidate_decode_cache

    freed = 0
    wells = 0
    state = ctx.state
    with swallow("idle release: row cache"):
        wells, rows = state.dataset.cache.trim()
        freed += rows
    with swallow("idle release: populations"):
        freed += state.stats_cache.populations.trim()
    with swallow("idle release: decoded images"):
        freed += invalidate_decode_cache()
    with swallow("idle release: image table frames"):
        freed += _drop_image_table_frames(ctx)
    if freed:
        _logger.info("App idle for %g min: released %.0f MiB of cached data (%d well(s) of rows); "
                     "it is read back from disk when next needed", idle_minutes(), freed / 2**20, wells)
    return freed


class _IdleReleaser(QObject):
    """Starts a one-shot timer when the application goes inactive, stops it when it comes back."""

    def __init__(self, ctx, app: QGuiApplication, minutes: float) -> None:
        super().__init__(app)
        self.ctx = ctx
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(int(minutes * 60_000))
        self.timer.timeout.connect(self._fire)
        app.applicationStateChanged.connect(self._on_state)
        if app.applicationState() != Qt.ApplicationActive:
            self.timer.start()

    def _on_state(self, state) -> None:
        if state == Qt.ApplicationActive:
            self.timer.stop()
        elif not self.timer.isActive():
            self.timer.start()

    def _fire(self) -> None:
        with swallow("idle release"):
            if busy(self.ctx):
                self.timer.start()
                return
            release_idle_memory(self.ctx)


def install(ctx) -> None:
    """Arm the idle release for *ctx*'s window. Idempotent; called after each dataset load."""
    global _installed
    if _installed is not None and _installed.ctx is ctx:
        return
    minutes = idle_minutes()
    app = QGuiApplication.instance()
    if minutes <= 0 or app is None:
        return
    if _installed is not None:
        with swallow("idle release: replace"):
            _installed.timer.stop()
            _installed.deleteLater()
    _installed = _IdleReleaser(ctx, app, minutes)
