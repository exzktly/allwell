"""Observable exception suppression.

Replaces the silent ``except Exception: pass`` anti-pattern with something that
leaves a breadcrumb in the logs *without changing runtime behaviour*: the
exception is still suppressed, but a record is emitted at ``DEBUG`` (quiet by
default, visible when diagnosing). A swallowed failure should never again be
indistinguishable from "nothing happened".

Two entry points, by situation:

- :func:`swallow` — a context manager for *new* code. Wrap the risky body::

      with swallow("view_state: reapply combo selection"):
          combo.setCurrentText(saved)

  instead of ``try: ... except Exception: pass``.

- :func:`swallowed` — call *inside* an existing ``except`` block, where
  restructuring the surrounding ``try`` into a context manager would be churny
  or unsafe::

      try:
          combo.setCurrentText(saved)
      except Exception:
          swallowed("view_state: reapply combo selection")

  The call site (``module:lineno``) is derived automatically, and the in-flight
  exception's traceback is attached via ``exc_info``.

Neither helper suppresses :class:`KeyboardInterrupt` or :class:`SystemExit`:
both derive from :class:`BaseException`, not :class:`Exception`, so they
propagate normally.
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from typing import Iterator, Optional

# Single well-known logger so swallowed failures are greppable under one name.
_LOGGER = logging.getLogger("well_viewer.swallowed")


def _caller_location(depth: int) -> str:
    """Return ``module:lineno`` for the frame ``depth`` levels above this one.

    ``depth=1`` is the direct caller of this function. Best-effort: returns
    ``"?"`` if the frame cannot be resolved.
    """
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
    logger: Optional[logging.Logger] = None,
) -> None:
    """Log the exception currently being handled, from inside an ``except``.

    Call this in place of a bare ``pass``/``continue`` in an
    ``except Exception:`` block. Emits one record at ``level`` (default DEBUG)
    with the call site and the active traceback. Does nothing surprising if
    there is no active exception (``exc_info`` simply carries ``None``).
    """
    where = _caller_location(1)
    (logger or _LOGGER).log(level, "swallowed @ %s: %s", where, note or "(no note)", exc_info=True)


@contextmanager
def swallow(
    context: str = "",
    *,
    level: int = logging.DEBUG,
    logger: Optional[logging.Logger] = None,
) -> Iterator[None]:
    """Context manager that suppresses + logs any :class:`Exception` in its body.

    Equivalent to ``try: ... except Exception: <log>`` — the exception is
    swallowed and recorded at ``level`` with its traceback. Use for new code;
    for retrofitting existing ``except`` blocks prefer :func:`swallowed`.
    """
    try:
        yield
    except Exception:  # noqa: BLE001 - intentional suppression boundary
        where = _caller_location(2)
        (logger or _LOGGER).log(level, "swallowed @ %s: %s", where, context or "(no note)", exc_info=True)
