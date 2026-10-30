"""The moving average over time: each nanowell's metric averaged over a trailing window of timepoints.

Off (a window of 0 or 1), every value is the one measured at its timepoint. With a window of *N*, the value a
nanowell has at a timepoint is the mean of its own values at that timepoint and at the *N − 1* timepoints of its
well before it, in the well's timepoint order (``tp_order``). The window counts timepoints, not hours: with
unevenly spaced acquisitions it spans a different length of time at each step, and the figure says it is in
timepoints.

**A window is used only when it is complete.** A value is defined only when the nanowell has a value at every
timepoint of its window and every one of them is joinable over time (``identity_ok``; a moving average is a
comparison over time, which a value that may belong to another nanowell takes no part in). It is never averaged
over fewer timepoints than were asked for, since such a value would draw exactly like a full one. Every value
it leaves undefined keeps a reason, so the figures count it among their exclusions:

* a value already undefined at its own timepoint keeps the reason it had;
* :data:`EARLY_REASON` — the window would start before the well's first timepoint (the first *N − 1*);
* :data:`IDENTITY_REASON` — a timepoint in the window is not joinable to the nanowell's others;
* :data:`GAP_REASON` — a timepoint in the window has no value.

Only the metrics with a value per timepoint have one (``distribution_compute.PER_NANOWELL`` have none).

Qt-free.
"""

from __future__ import annotations

import pandas as pd

#: The largest window the page offers. The longest series seen so far are about a dozen timepoints.
MAX_WINDOW = 9

EARLY_REASON = "moving average: before the first full window"
GAP_REASON = "moving average: a timepoint in the window has no value"
IDENTITY_REASON = "moving average: a timepoint in the window is not joinable"

_SITE = ["well", "nw_q", "nw_r"]


def clamp_window(window) -> int:
    """*window* as the page stores it: 0 (off) or a whole number from 2 to :data:`MAX_WINDOW`."""
    if isinstance(window, bool) or not isinstance(window, int) or window < 2:
        return 0
    return min(window, MAX_WINDOW)


def max_window() -> int:
    """:data:`MAX_WINDOW`, for a caller outside this feature (Batch Export) that reaches it through a lazy proxy,
    which can only call."""
    return MAX_WINDOW


def is_on(window) -> bool:
    return clamp_window(window) >= 2


def describe(window: int) -> str:
    """The figure's sentence for a window that is on, or ``""`` for one that is off."""
    window = clamp_window(window)
    if not window:
        return ""
    return (f"Moving average over {window} timepoints: each value is the mean of the nanowell's values at that "
            f"timepoint and the {window - 1} before it, only where all {window} have one.")


def moving_mean(frame: pd.DataFrame, values: pd.Series, window: int) -> tuple[pd.Series, pd.Series]:
    """``(averaged, why)`` for the rows of *frame*, aligned to its index.

    *values* is the metric as numbers on *frame*'s index, NaN where undefined. ``averaged`` is the trailing mean
    over *window* timepoints of each nanowell, NaN where the window is not complete; ``why`` is, for each row that
    has a value in *values* and none in ``averaged``, which of the module's reasons left it out, and ``""``
    elsewhere. A window of 0 or 1 returns *values* unchanged.
    """
    window = clamp_window(window)
    blank = pd.Series("", index=frame.index, dtype=object)
    if not window or not len(frame):
        return values.astype(float), blank
    ok = (frame["identity_ok"].fillna(True).astype(bool) if "identity_ok" in frame
          else pd.Series(True, index=frame.index))
    work = pd.DataFrame({"well": frame["well"].to_numpy(), "nw_q": frame["nw_q"].to_numpy(),
                         "nw_r": frame["nw_r"].to_numpy(), "tp": frame["tp_order"].to_numpy(dtype=float),
                         "v": values.to_numpy(dtype=float), "ok": ok.to_numpy(dtype=bool)}, index=frame.index)
    work = work.sort_values(_SITE + ["tp"], kind="stable")
    groups = work.groupby(_SITE, sort=False)
    has = work["v"].notna()
    joinable = has & work["ok"]
    # Rolling over rows, then checking the rows span exactly *window* consecutive timepoints, so a timepoint
    # with no row at all counts as a gap rather than letting the window reach past it.
    span = work["tp"] - groups["tp"].shift(window - 1)
    full = span.eq(window - 1)
    keys = [work[k] for k in _SITE]

    def trailing_sum(series: pd.Series) -> pd.Series:
        rolled = series.astype(float).groupby(keys, sort=False).rolling(window).sum()
        return rolled.reset_index(level=list(range(len(_SITE))), drop=True).reindex(work.index)

    n_has = trailing_sum(has)
    n_ok = trailing_sum(joinable)
    total = trailing_sum(work["v"].where(joinable, 0.0))
    complete = full & n_ok.eq(window)
    averaged = (total / window).where(complete)
    why = pd.Series("", index=work.index, dtype=object)
    lost = has & ~complete
    early = lost & (work["tp"] < window - 1)
    identity = lost & ~early & full & n_has.eq(window)
    why[lost] = GAP_REASON
    why[identity] = IDENTITY_REASON
    why[early] = EARLY_REASON
    return averaged.reindex(frame.index), why.reindex(frame.index)


def apply(frame: pd.DataFrame, column: str, reasons: pd.Series, window: int,
          ) -> tuple[pd.DataFrame, pd.Series]:
    """*frame* with *column* replaced by its moving average, and *reasons* with a reason for each value it left
    out (an exclusion reason already there is kept). Unchanged when *window* is off."""
    if not is_on(window):
        return frame, reasons
    values = pd.to_numeric(frame[column], errors="coerce")
    averaged, why = moving_mean(frame, values, window)
    frame = frame.copy()
    frame[column] = averaged
    reasons = reasons.copy()
    reasons[why != ""] = why[why != ""]
    return frame, reasons
