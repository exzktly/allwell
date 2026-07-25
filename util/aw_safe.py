"""Observable exception suppression for the Qt-free pipeline / services side.

The mirror of :mod:`well_viewer.core.safe` for code that must NOT import
``well_viewer`` — the headless pipeline (``process_microscopy`` and the
``microscopy_*`` modules) and the ``services`` layer, which run on the remote
worker where ``well_viewer`` / Qt aren't installed. Same contract: replace a
silent ``except Exception: pass`` with a logged swallow so a hidden failure
leaves a breadcrumb, without changing runtime behaviour (still suppressed, still
quiet at DEBUG). The small duplication with ``well_viewer.core.safe`` is
deliberate — it is the cost of the pipeline's no-``well_viewer`` import contract.

- :func:`swallowed` — call inside an existing ``except`` block.
- :func:`swallow` — context manager for new code.

Neither suppresses ``KeyboardInterrupt`` / ``SystemExit`` (both are
``BaseException``, not ``Exception``).
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from collections.abc import Iterator

_LOGGER = logging.getLogger("allwell.swallowed")


def _caller_location(depth: int) -> str:
    try:
        frame = sys._getframe(depth + 1)
    except (ValueError, AttributeError):
        return "?"
    try:
        return f"{frame.f_globals.get('__name__', '?')}:{frame.f_lineno}"
    finally:
        del frame


def swallowed(
    note: str = "",
    *,
    level: int = logging.DEBUG,
    logger: logging.Logger | None = None,
) -> None:
    """Log the exception currently being handled, from inside an ``except``."""
    where = _caller_location(1)
    (logger or _LOGGER).log(level, "swallowed @ %s: %s", where, note or "(no note)", exc_info=True)


@contextmanager
def swallow(
    context: str = "",
    *,
    level: int = logging.DEBUG,
    logger: logging.Logger | None = None,
) -> Iterator[None]:
    """Context manager that suppresses + logs any :class:`Exception` in its body."""
    try:
        yield
    except Exception:  # intentional suppression boundary
        where = _caller_location(2)
        (logger or _LOGGER).log(level, "swallowed @ %s: %s", where, context or "(no note)", exc_info=True)
