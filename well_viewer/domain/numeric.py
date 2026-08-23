"""One answer to "is this a number I can use?".

Every numeric guard in the plotting and export paths used to be spelled
``isinstance(value, (int, float)) and math.isfinite(value)``. That is False for
every numpy scalar — ``numpy.float32``, ``numpy.float64``, ``numpy.int64`` —
and the values reaching those guards come out of numpy reductions several call
levels away, so the test asked which class had produced a number rather than
whether it was one.

The failure is silent and always in the same direction: a real spread becomes
"no error bar", a real mean becomes a placeholder. That is the
computed-as-zero-versus-not-computed conflation the repository's own design
rule is about, and CLAUDE.md §7.5 #8 records this exact class in the
fold-change module.

Qt-free and dependency-free, so the pipeline side can use it too.
"""

from __future__ import annotations

import math

__all__ = ["is_number", "is_numeric"]


def is_number(value) -> bool:
    """Whether *value* is a real, finite scalar number.

    ``float(value)`` is the honest test — "can this be used as a number?" — but
    on its own it is *wider* than the ``isinstance`` spelling in one way that
    matters: ``float(numpy.array([1.0]))`` succeeds, so a one-element array
    would quietly pass as a scalar. An array reaching a scalar slot is a bug,
    and converting it silently is how such a bug survives, so anything sized is
    rejected first. A numpy scalar has ``ndim == 0`` and no ``__len__``, which
    is exactly the line wanted here.

    Booleans pass, as they did under ``isinstance(value, (int, float))``:
    ``isinstance(True, int)`` is True. Nothing in these paths produces one, and
    narrowing that here would be a second, unmeasured change.
    """
    if hasattr(value, "__len__") or getattr(value, "ndim", 0):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def is_numeric(value) -> bool:
    """Whether *value* is a scalar number at all — **NaN and infinity included**.

    The companion to :func:`is_number`, and the distinction is load-bearing.
    ``is_number`` answers "is this a usable measurement?"; this one answers "is
    this a number rather than a string, None or an array?". A NaN spread means
    *undefined*, and folding it to 0.0 says the replicates agreed exactly — so
    a site that lets NaN through on purpose must not be converted to the
    stricter predicate while its class test is being widened for numpy scalars.
    """
    if hasattr(value, "__len__") or getattr(value, "ndim", 0):
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True
