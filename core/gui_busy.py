"""Work the GUI thread is in the middle of, and what must wait until it ends.

Some GUI-thread work repaints as it goes by pumping the event loop with
``QEventLoop.ExcludeUserInputEvents`` — the Image Table's Generate does, so its
progress bar moves. That keeps clicks and keys out, and nothing else: timers,
posted events and queued signals are still delivered, in the middle of work
that has half rebuilt what they act on. A phone refit timer started a second
Generate into the grid the first was filling; a finished run's deferred dataset
load could reset the dataset under one.

So such work runs inside :func:`busy`, and a caller that must not interleave
with it asks :func:`after_busy` to run it once the work is over (at once, when
nothing is). Waiting calls are keyed, so a timer that fires ten times during one
Generate asks for one more Generate, not ten. They run on a later event-loop
turn after the outermost :func:`busy` ends, never inside its stack.

Application infrastructure with no pane knowledge: the shell's dataset hand-off
and Review's Image Table both use it, and neither knows about the other.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from core.safe import swallow

_depth = 0
_waiting: dict[str, Callable[[], None]] = {}


def is_busy() -> bool:
    """Whether GUI-thread work that pumps the event loop is under way."""
    return _depth > 0


@contextmanager
def busy() -> Iterator[None]:
    """Mark the enclosed GUI-thread work as under way; release what waited when the outermost ends."""
    global _depth
    _depth += 1
    try:
        yield
    finally:
        _depth -= 1
        if _depth == 0 and _waiting:
            _release()


def after_busy(key: str, fn: Callable[[], None]) -> bool:
    """Run *fn* now when nothing is busy, else once the busy work ends; ``True`` when it ran now.

    A later call with the same *key* replaces an earlier one still waiting.
    """
    if _depth == 0:
        fn()
        return True
    _waiting[str(key)] = fn
    return False


def pending(key: str) -> Callable[[], None] | None:
    """The call waiting under *key*, if any."""
    return _waiting.get(str(key))


def _release() -> None:
    from PySide6.QtCore import QTimer

    calls = list(_waiting.values())
    _waiting.clear()
    for fn in calls:
        QTimer.singleShot(0, _guarded(fn))


def _guarded(fn: Callable[[], None]) -> Callable[[], None]:
    # An exception escaping a QTimer callback aborts the process under
    # PySide6 >= 6.5, so a waited call that raises costs that call only.
    def run() -> None:
        with swallow("a call deferred past busy GUI work"):
            fn()
    return run
