"""Retry shield for file operations that land on a volume which faults under load.

A network-mounted volume is not a reliable file store while a plate is being
processed. macOS's SMB client does not re-open server-side handles when a
session drops and reconnects, so every descriptor opened before the drop fails
with ``OSError: [Errno 9] Bad file descriptor``; NFS answers ``ESTALE`` in the
same situation, and a saturated link surfaces as ``EIO`` or a reset connection.
None of those are permanent: the mount is healthy again within a second or two,
and the operation succeeds on a retry.

This module is the single definition of "try it again with a backoff, then fail
with a message the user can act on". Both sides of All-Well need it and neither
can import the other's:

* the **pipeline** writes its results on the user's output volume
  (``pipeline.io.retry_write`` wraps this, keeping the pipeline's own wording),
* the **remote client** in ``services/`` stages the plate off the user's input
  volume and writes every fetched well back onto the output volume, and must
  stay free of the imaging stack that ``pipeline.io`` pulls in.

Stdlib-only and Qt-free for that reason.

The wrapped callable must be **idempotent**: every caller here stages to a temp
path and commits with ``os.replace``, so re-running an attempt is safe. Only
``OSError`` is retried — a programming error or a ``KeyboardInterrupt`` must
surface on the first attempt.
"""

from __future__ import annotations

import errno
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

__all__ = [
    "DEFAULT_ATTEMPTS",
    "DEFAULT_BASE_DELAY_S",
    "VOLUME_ADVICE",
    "retry_io",
    "volume_hint",
]

_LOGGER = logging.getLogger("allwell.retry_io")

T = TypeVar("T")

DEFAULT_ATTEMPTS = 5
DEFAULT_BASE_DELAY_S = 0.5

#: Generic tail for the exhausted-budget message. Callers that know more about
#: which volume is involved pass their own.
VOLUME_ADVICE = (
    "A repeated failure here usually means the volume is unreliable under "
    "parallel load — on a network mount (SMB/NFS) a dropped session "
    "invalidates open file handles and every subsequent operation on them "
    "fails. Point the path at local storage, or reduce the parallel load, "
    "and re-run."
)

# Errnos a dropped/loaded network session produces, mapped to the protocol they
# point at. Used only to enrich the diagnostic — every OSError is retried
# regardless, because the classification is a hint and not a contract.
_SESSION_ERRNO_HINTS: dict[int, str] = {
    errno.EBADF: (
        "errno 9 (EBADF) is what macOS's SMB client returns for a handle "
        "opened before the session dropped and reconnected — the volume is "
        "almost certainly an SMB network mount"
    ),
    errno.ESTALE: (
        "errno 116 (ESTALE) is an NFS handle invalidated by the server — the "
        "volume is a network mount"
    ),
    errno.EIO: (
        "errno 5 (EIO) is a low-level I/O failure — a failing disk, or a "
        "network mount whose link dropped mid-operation"
    ),
    errno.ECONNRESET: (
        "the connection to the volume was reset — it is a network mount"
    ),
    errno.ETIMEDOUT: (
        "the volume stopped answering in time — it is a network mount, or one "
        "backed by sleeping external storage"
    ),
}


# Errnos that describe a condition retrying cannot change: the path is wrong,
# the permissions are wrong, or the volume is full. Backing off five times to
# fail with the same error costs 7.5 s per call site and buries the real cause
# under a retry story — these surface immediately and unchanged instead, which
# is also what keeps a caller that treats a missing source as an ordinary
# ``OSError`` (``Fetcher._materialize``) responsive.
_PERMANENT_ERRNOS = frozenset({
    errno.EACCES,
    errno.EDQUOT,
    errno.EEXIST,
    errno.EINVAL,
    errno.EISDIR,
    errno.ELOOP,
    errno.ENAMETOOLONG,
    errno.ENOENT,
    errno.ENOSPC,
    errno.ENOTDIR,
    errno.ENOTEMPTY,
    errno.EPERM,
    errno.EROFS,
    errno.EXDEV,
})


def volume_hint(exc: BaseException) -> str:
    """One sentence naming the likely storage cause behind *exc*, or ``""``.

    Only recognised errnos produce a hint; anything else returns the empty
    string so callers can append it unconditionally.
    """
    code = getattr(exc, "errno", None)
    if code is None:
        return ""
    return _SESSION_ERRNO_HINTS.get(code, "")


def retry_io(
    fn: Callable[[], T],
    *,
    what: str,
    cleanup: Callable[[], Any] | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float | None = None,
    advice: str = VOLUME_ADVICE,
    log_fn: Callable[[str], None] | None = None,
) -> T:
    """Run *fn*, retrying an ``OSError`` with an exponential backoff.

    *what* names the target in both the retry warnings and the final message.
    *cleanup* (if given) runs after every failed attempt, including the last, so
    a partial staging file is never left behind. *log_fn* receives one line per
    retry — pass the remote runner's log sink to make the retries visible in the
    run log; without it the retries go to the ``allwell.retry_io`` logger.

    Once the budget is exhausted the failure is re-raised as an ``OSError``
    naming the target, the attempt count, the original error and *advice*, with
    the original exception kept as ``__cause__``. Callers therefore report an
    actionable failure rather than a bare errno.

    *base_delay* defaults to :data:`DEFAULT_BASE_DELAY_S`, read at call time so a
    test can patch the module constant to 0 rather than sit through the backoff.

    An error retrying cannot fix — a missing path, wrong permissions, a full
    volume (:data:`_PERMANENT_ERRNOS`) — is re-raised unchanged on the first
    attempt, after *cleanup* runs.
    """
    attempts = max(1, attempts)
    if base_delay is None:
        base_delay = DEFAULT_BASE_DELAY_S
    for attempt in range(attempts):
        try:
            return fn()
        except OSError as exc:
            if cleanup is not None:
                cleanup()
            if getattr(exc, "errno", None) in _PERMANENT_ERRNOS:
                raise
            if attempt + 1 >= attempts:
                hint = volume_hint(exc)
                hint_txt = f" {hint[0].upper()}{hint[1:]}." if hint else ""
                raise OSError(
                    f"Failed to write {what} after {attempts} attempts "
                    f"({exc}).{hint_txt} {advice}"
                ) from exc
            msg = (
                f"write {what} failed ({exc}); retry {attempt + 1}/"
                f"{attempts - 1} after backoff"
            )
            if log_fn is not None:
                log_fn(f"[retry] {msg}\n")
            else:
                _LOGGER.warning("%s", msg)
            time.sleep(base_delay * (2 ** attempt))
    raise AssertionError("unreachable: the loop either returns or raises")
