"""Global status-light state registry.

The bottom-left ``StatusDot`` in the all-well window reflects the app's
mood at a glance:

* ``success`` (green)  — idle, dataset loaded.
* ``neutral`` (grey)   — idle, no dataset loaded.
* ``accent``  (blue)   — at least one background worker is running
                          (e.g. ``GatingWorker``, ``_AutoThresholdWorker``,
                          ``_load_path``).
* ``warn``    (yellow) — a synchronous GUI repaint is in flight (the app
                          may feel briefly unresponsive).
* ``danger``  (red)    — the most recent operation failed; auto-clears
                          after ``DANGER_HOLD_SECS`` seconds.

The actual ``StatusDot`` widget lives on ``AllWellApp``; this module is
the plumbing that lets any module deep in the package push/pop a busy /
warn scope and trigger the danger pulse, without taking a hard
dependency on ``allwell.py`` (and without circular imports).

``AllWellApp`` calls :func:`register_driver` at startup with a callable
that accepts a state string. Until that runs every call here is a
silent no-op, so unit tests / standalone widget demos that don't
construct the main window are unaffected.

Reference counts let multiple workers / repaints overlap cleanly: the
effective state is picked in priority order
``danger > accent > warn > (success | neutral)`` so a brief warn
during a redraw doesn't mask an in-progress worker, and a failure
sticks out red over everything else.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional


_logger = logging.getLogger("well_viewer.core.status_signal")


# Priority order — higher wins.
SUCCESS = "success"
NEUTRAL = "neutral"
WARN = "warn"
ACCENT = "accent"
DANGER = "danger"

DANGER_HOLD_SECS = 5.0


_lock = threading.Lock()
_busy_count: int = 0
_warn_count: int = 0
_danger_until: float = 0.0
_driver: Optional[Callable[[str], None]] = None
_idle_state: str = NEUTRAL


def register_driver(driver: Optional[Callable[[str], None]],
                    *, initial_state: str = NEUTRAL) -> None:
    """Install (or remove, with ``None``) the callback that pushes the
    chosen state string at the live ``StatusDot``. Re-fires once on
    install so the driver picks up any state pushed before it was
    registered."""
    global _driver, _idle_state
    with _lock:
        _driver = driver
        _idle_state = initial_state
    refresh()


def set_idle_state(state: str) -> None:
    """Configure the colour used when nothing is busy / warn / danger.

    ``AllWellApp._update_dataset_chip`` calls this with ``success`` once
    a dataset is loaded so the dot turns green when idle, and with
    ``neutral`` when the dataset is cleared so the dot returns to grey.
    """
    global _idle_state
    with _lock:
        _idle_state = state
    refresh()


def _effective_state_locked() -> str:
    if _danger_until > time.time():
        return DANGER
    if _busy_count > 0:
        return ACCENT
    if _warn_count > 0:
        return WARN
    return _idle_state


def refresh() -> None:
    """Recompute the effective state and push it to the driver."""
    with _lock:
        state = _effective_state_locked()
        driver = _driver
    if driver is None:
        return
    try:
        driver(state)
    except Exception as exc:
        _logger.debug("status_signal driver raised %s", exc)


def busy_push() -> None:
    """Mark one more background operation in flight."""
    global _busy_count
    with _lock:
        _busy_count += 1
    refresh()


def busy_pop() -> None:
    """Mark one background operation as finished."""
    global _busy_count
    with _lock:
        _busy_count = max(0, _busy_count - 1)
    refresh()


def warn_push() -> None:
    """Mark one synchronous repaint / heavy GUI operation in flight."""
    global _warn_count
    with _lock:
        _warn_count += 1
    refresh()


def warn_pop() -> None:
    """Mark one synchronous repaint / heavy GUI operation as finished."""
    global _warn_count
    with _lock:
        _warn_count = max(0, _warn_count - 1)
    refresh()


def signal_failed(hold_secs: float = DANGER_HOLD_SECS) -> None:
    """Flip the dot red. Auto-clears after *hold_secs* seconds via the
    driver-owned timer set up at :func:`register_driver` time."""
    global _danger_until
    with _lock:
        _danger_until = max(_danger_until, time.time() + float(hold_secs))
    refresh()


class busy_scope:  # noqa: N801 — used as a context manager
    """``with busy_scope(): …`` push/pop a busy count for the duration."""

    def __enter__(self):
        busy_push()
        return self

    def __exit__(self, *exc) -> None:
        busy_pop()


class warn_scope:  # noqa: N801
    """``with warn_scope(): …`` push/pop a warn count for the duration."""

    def __enter__(self):
        warn_push()
        return self

    def __exit__(self, *exc) -> None:
        warn_pop()
