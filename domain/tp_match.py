"""When two timepoints are the same measurement.

Timepoints travel through the interface as text: combo items, bar keys and
saved-plot records all spell an hour value with ``:.4g``, four significant
figures. A value parsed back from that text is therefore up to **half a unit of
its fourth significant figure** away from the float it came from (2/3 h comes
back as 0.6667, 3e-5 off), so an exact comparison, or a fixed ``1e-6``, loses
the match and the consumer reports "no data".

The tolerance here is exactly that rounding error and nothing more: two
timepoints match when they differ by no more than half a unit in the fourth
significant figure of the larger one (0.005 h at 24 h, 0.05 h at 100 h). It is
deliberately **not** a fixed fraction of the hour value. A relative tolerance of
one part in a thousand, which this module used to apply, is 6 minutes at 100 h:
on a time-lapse with 5-minute frames every consumer pooled three frames into
one from the fourth day on, and a Subset with one timepoint ticked wrote three.
The rounding bound is the smallest tolerance the text round trip needs, so it
is also the one that merges the fewest real frames.

Every timepoint comparison in the app goes through this module, including the
fold-change lookups in ``review/domain/fold_change.py``.
"""
from __future__ import annotations

import math

import numpy as np

#: Significant figures the interface spells an hour value with (``:.4g``).
TP_SIG_FIGS = 4

#: Slack on the half-unit bound, so a value that sits exactly on the rounding
#: boundary still matches after the float arithmetic that got it there.
_SLACK = 1.0 + 1e-9


def tp_tolerance(a: float, b: float) -> float:
    """The largest difference at which *a* and *b* are one timepoint, in hours.

    Half a unit of the fourth significant figure of the larger magnitude. Values
    under one hour take the bound of one hour (0.0005 h, under two seconds), so
    a timepoint of zero has a tolerance at all.
    """
    scale = max(1.0, abs(float(a)), abs(float(b)))
    if not math.isfinite(scale):
        return 0.0
    return 0.5 * 10.0 ** (math.floor(math.log10(scale)) - (TP_SIG_FIGS - 1)) * _SLACK


def tp_close(a: float, b: float) -> bool:
    """True if two scalar timepoints are the same measurement."""
    a = float(a)
    b = float(b)
    return abs(a - b) <= tp_tolerance(a, b)


def tp_close_mask(values, target: float) -> np.ndarray:
    """Boolean mask of which ``values`` are the timepoint ``target``."""
    arr = np.asarray(values, dtype=float)
    tgt = float(target)
    with np.errstate(invalid="ignore", divide="ignore"):
        scale = np.maximum(1.0, np.maximum(np.abs(arr), abs(tgt)))
        tol = 0.5 * 10.0 ** (np.floor(np.log10(scale)) - (TP_SIG_FIGS - 1)) * _SLACK
        return np.abs(arr - tgt) <= tol
