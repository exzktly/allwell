"""The Image Table's inline progress bar for a Generate: how many images are read, then how many are drawn.

A Generate runs on the GUI thread — the decode on a pool the GUI thread waits on (``_prefetch``), then one cell at a
time — so without this a large table on a slow drive left the window still and silent for as long as it took. The bar
sits above the table, where the images are about to appear, and says which pass it is in and how far it has got.

Two choices keep it from being noise or a hazard:

* it **appears only once a Generate has run for** :data:`SHOW_AFTER_S`. Most Generates are re-renders from the
  decode cache (a zoom, a LUT, a threshold) and finish in a moment; a bar flashing on each of those would be clutter;
* it **repaints without dispatching user input** (``QEventLoop.ExcludeUserInputEvents``), at most every
  :data:`PUMP_EVERY_S`. A click handled mid-Generate would run against a table halfway rebuilt — the reason
  ``ui_helpers._pump`` gives the dataset load the same treatment — and pumping per cell would cost more than the
  cells. Excluding user input does not exclude timers or queued signals, so a Generate runs inside
  ``core.gui_busy.busy``: a Generate asked for meanwhile, and the shell's deferred dataset load, wait for it to end
  (:func:`one_at_a_time`, ``AllWellApp._load_in_review_later``).
"""

from __future__ import annotations

import time

#: Seconds a Generate runs before its bar appears.
SHOW_AFTER_S = 0.3
#: Shortest interval between two repaints while the bar is up.
PUMP_EVERY_S = 0.05


class LoadProgress:
    """One Generate's progress, shown on *bar* and *label* (either may be ``None``: then it reports nowhere)."""

    def __init__(self, bar=None, label=None, *, clock=time.monotonic, pump=None) -> None:
        self._bar = bar
        self._label = label
        self._clock = clock
        self._pump = pump if pump is not None else _pump_paint_only
        self._start = clock()
        self._last_pump = float("-inf")
        self._shown = False
        self._text = ""
        self._done = 0
        self._total = 0

    @property
    def shown(self) -> bool:
        return self._shown

    def phase(self, text: str, total: int) -> None:
        """Start a pass of *total* steps named *text* ("Loading images", "Drawing images")."""
        self._text = str(text)
        self._total = max(0, int(total))
        self._done = 0
        self._update(force=True)

    def tick(self, n: int = 1) -> None:
        self._done = min(self._total, self._done + int(n)) if self._total else self._done + int(n)
        self._update(force=self._done >= self._total)

    def finish(self) -> None:
        """Hide the bar: the Generate is over, whether or not it was ever shown."""
        for widget in (self._bar, self._label):
            if widget is not None:
                widget.setVisible(False)
        self._shown = False

    def text(self) -> str:
        return f"{self._text}… {self._done} of {self._total}" if self._total else f"{self._text}…"

    def _update(self, *, force: bool = False) -> None:
        if self._bar is None:
            return
        now = self._clock()
        if not self._shown:
            if now - self._start < SHOW_AFTER_S:
                return
            self._shown = True
            self._bar.setVisible(True)
            if self._label is not None:
                self._label.setVisible(True)
            force = True
        if not force and now - self._last_pump < PUMP_EVERY_S:
            return
        self._bar.setRange(0, max(1, self._total))
        self._bar.setValue(self._done)
        if self._label is not None:
            self._label.setText(self.text())
        self._last_pump = now
        self._pump()


def _pump_paint_only() -> None:
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)


#: The ``core.gui_busy`` key a Generate asked for mid-Generate waits under.
_GENERATE_KEY = "image_table_generate"


def one_at_a_time(generate):
    """Wrap ``image_table_generate`` so a Generate never starts inside another.

    The bar's pump still delivers timers and queued signals, so a Generate can be
    asked for while one is drawing (a phone refit timer is one route). The ask
    waits until the busy work ends (``core.gui_busy``), and any number of asks
    run as one Generate, re-walking missing folders when any of them asked to.
    """
    import functools

    from core import gui_busy

    @functools.wraps(generate)
    def guarded(ctx, *, retry_missing: bool = False) -> None:
        if gui_busy.is_busy():
            waiting = gui_busy.pending(_GENERATE_KEY)
            retry = bool(retry_missing) or bool(getattr(waiting, "retry_missing", False))

            def _later() -> None:
                guarded(ctx, retry_missing=retry)
            _later.retry_missing = retry  # type: ignore[attr-defined]
            gui_busy.after_busy(_GENERATE_KEY, _later)
            return
        with gui_busy.busy():
            guarded.unguarded(ctx, retry_missing=retry_missing)
    guarded.unguarded = generate  # type: ignore[attr-defined]   # the body itself, read per call
    return guarded


def begin(ctx) -> LoadProgress:
    """A fresh progress for a Generate, on the Image Table's bar (none in a build without the tab's widgets)."""
    widgets = getattr(ctx, "widgets", None)
    get = getattr(widgets, "get_or_none", None)
    bar = get("_image_table_load_bar") if get else None
    label = get("_image_table_load_label") if get else None
    progress = LoadProgress(bar, label)
    progress.finish()                                   # a previous Generate that raised left its bar up
    return progress
