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

**A fold change's window starts after its baseline.** A fold change is a value over the nanowell's baseline, so a
window over fold changes may hold only timepoints after the baseline timepoint: one that reaches the baseline would
average in its fold change of exactly 1 — a number with no noise, which damps the first windows after it towards
1 — and one that reaches before it would average in values from before the reference point. Such a value is left
out with :data:`BASELINE_REASON`. The baseline is each nanowell's own (``series_compute``: the earliest timepoint,
the first measurable one per measurement, or a named timepoint), so with a later baseline the windows start later.

**The baseline is one measured value unless it has a window of its own.** By default only the endpoint side of a
fold change is averaged. :func:`rebase` gives the baseline a separate trailing window: the mean of the measured
value (the count, the confluence, the brightness or the fluorescence — not a fold change) at the baseline timepoint
and the timepoints before it, never after it, since a later timepoint may already carry the treatment. It follows
the endpoint's rule: complete or nothing, every timepoint joinable, its shortfalls counted with
:data:`BASE_EARLY_REASON`, :data:`BASE_GAP_REASON` or :data:`BASE_IDENTITY_REASON`. The Earliest baseline has
nothing before it, and the First measurable baseline is by definition the first timepoint with a value, so every
timepoint before it has none: under either a complete window can never exist, and :func:`baseline_window_for` turns
the baseline's window off there rather than leaving every fold change undefined. Only a named timepoint can have one.
It is also off whenever the endpoint's moving average is off.

A count's ratio over a constant baseline averages the same whichever is divided first. ``log2_count_fc`` is
averaged as logs — the mean of log₂((n + 1) / (n₀ + 1)), a geometric mean of the ratios, not the log of their
mean — and :func:`describe` says so.

Qt-free.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from review.features.nanowell.collect import MEASURABLE as _MEASURABLE
from review.features.nanowell.series_compute import EARLIEST, FIRST_MEASURABLE

#: The largest window the page offers. The longest series seen so far are about a dozen timepoints.
MAX_WINDOW = 9

EARLY_REASON = "moving average: before the first full window"
GAP_REASON = "moving average: a timepoint in the window has no value"
IDENTITY_REASON = "moving average: a timepoint in the window is not joinable"
BASELINE_REASON = "moving average: the window does not start after the baseline"
BASE_EARLY_REASON = "baseline average: the window would start before the well's first timepoint"
BASE_GAP_REASON = "baseline average: a timepoint in the window has no value"
BASE_IDENTITY_REASON = "baseline average: a timepoint in the window is not joinable"
#: What the endpoint's own reasons become when they describe the baseline's window.
_BASE_REASONS = {EARLY_REASON: BASE_EARLY_REASON, GAP_REASON: BASE_GAP_REASON, IDENTITY_REASON: BASE_IDENTITY_REASON}

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


def baseline_window_for(window, baseline_window, baseline) -> int:
    """The baseline's window a figure uses: *baseline_window* clamped, and 0 when the endpoint's moving average
    (*window*) is off — the baseline's window is a refinement of it, not a control of its own — or when *baseline*
    is the Earliest, which has no timepoint before it to average, or First measurable, whose earlier timepoints are
    by definition the ones without a value, so its window could never be complete and would blank every fold
    change."""
    if not is_on(window) or baseline in (EARLIEST, FIRST_MEASURABLE):
        return 0
    return clamp_window(baseline_window)


def base_value(column: str) -> str | None:
    """The measured value *column* is a fold change of (``n`` for ``count_fc``, ``brightness_GFP`` for
    ``brightness_fc_GFP``), or ``None`` for a column that is not a fold change. Agrees with
    ``series_compute.metric_kinds`` for every ``fc`` kind (a test holds the two together)."""
    if column in ("count_fc", "log2_count_fc"):
        return "n"
    if column == "confluence_fc":
        return "c"
    for kind in ("brightness", "signal"):
        prefix = f"{kind}_fc_"
        if column.startswith(prefix) and len(column) > len(prefix):
            return f"{kind}_{column[len(prefix):]}"
    return None


def describe(window: int, column: str = "", baseline_window: int = 0) -> str:
    """The figure's sentence for a window that is on, or ``""`` for one that is off. For a fold change *column* it
    also says the window starts after the baseline, how the baseline was taken (*baseline_window*, as
    :func:`baseline_window_for` gives it), and for ``log2_count_fc`` that logs are averaged."""
    window = clamp_window(window)
    if not window:
        return ""
    text = (f"Moving average over {window} timepoints: each value is the mean of the nanowell's values at that "
            f"timepoint and the {window - 1} before it, only where all {window} have one.")
    if base_value(column) is None:
        return text
    text += " Each window starts after the baseline, so the baseline's own fold change of 1 is never averaged in."
    baseline_window = clamp_window(baseline_window)
    if baseline_window:
        text += (f" The baseline is the mean of the nanowell's measured values at the baseline timepoint and the "
                 f"{baseline_window - 1} before it, only where all {baseline_window} have one.")
    else:
        text += " The baseline is its one measured value, not averaged."
    if column == "log2_count_fc":
        text += " log₂ fold changes are averaged as logs (a geometric mean of the ratios)."
    return text


def _at_base_row(frame: pd.DataFrame, value: str, series: pd.Series) -> pd.Series:
    """*series* at each nanowell's baseline row for the measurement *value* (``series_compute``'s
    ``_base_tp_<value>``), broadcast to every row of the nanowell; NaN where it has no baseline row."""
    base_tp = frame[f"_base_tp_{value}"]
    mask = base_tp.notna() & frame["timepoint"].astype(str).eq(base_tp.astype(str))
    keys = [frame[k] for k in _SITE]
    return series.where(mask).groupby(keys, sort=False).transform("first")


def baseline_order(frame: pd.DataFrame, column: str) -> pd.Series | None:
    """The ``tp_order`` of each row's baseline timepoint for the fold change *column*, NaN for a nanowell with
    none; ``None`` when *column* is not a fold change (its windows have no baseline to start after)."""
    value = base_value(column)
    if value is None:
        return None
    return _at_base_row(frame, value, frame["tp_order"].astype(float))


def moving_mean(frame: pd.DataFrame, values: pd.Series, window: int, *,
                after: pd.Series | None = None) -> tuple[pd.Series, pd.Series]:
    """``(averaged, why)`` for the rows of *frame*, aligned to its index.

    *values* is the metric as numbers on *frame*'s index, NaN where undefined. ``averaged`` is the trailing mean
    over *window* timepoints of each nanowell, NaN where the window is not complete; ``why`` is, for each row that
    has a value in *values* and none in ``averaged``, which of the module's reasons left it out, and ``""``
    elsewhere. A window of 0 or 1 returns *values* unchanged.

    *after* (a fold change's :func:`baseline_order`) is a ``tp_order`` per row that the window must start strictly
    after; a row whose window would start at or before it has no value, with :data:`BASELINE_REASON` — which wins
    over every other reason, the early one included, since no window there could ever be used. NaN sets no bound
    (a nanowell with no baseline has no fold change to average anyway).
    """
    window = clamp_window(window)
    blank = pd.Series("", index=frame.index, dtype=object)
    if not window or not len(frame):
        return values.astype(float), blank
    ok = (frame["identity_ok"].fillna(True).astype(bool) if "identity_ok" in frame
          else pd.Series(True, index=frame.index))
    bound = (np.full(len(frame), np.nan) if after is None
             else pd.to_numeric(after, errors="coerce").reindex(frame.index).to_numpy(dtype=float))
    work = pd.DataFrame({"well": frame["well"].to_numpy(), "nw_q": frame["nw_q"].to_numpy(),
                         "nw_r": frame["nw_r"].to_numpy(), "tp": frame["tp_order"].to_numpy(dtype=float),
                         "v": values.to_numpy(dtype=float), "ok": ok.to_numpy(dtype=bool), "after": bound},
                        index=frame.index)
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
    # The first timepoint of the window, when it is complete: it must come after the baseline, if one is given.
    reaches_baseline = (work["tp"] - (window - 1)).le(work["after"])     # False where ``after`` is NaN
    complete = full & n_ok.eq(window) & ~reaches_baseline
    averaged = (total / window).where(complete)
    why = pd.Series("", index=work.index, dtype=object)
    lost = has & ~complete
    early = lost & (work["tp"] < window - 1)
    identity = lost & ~early & full & n_has.eq(window)
    why[lost] = GAP_REASON
    why[identity] = IDENTITY_REASON
    why[early] = EARLY_REASON
    why[lost & reaches_baseline] = BASELINE_REASON
    return averaged.reindex(frame.index), why.reindex(frame.index)


def rebase(frame: pd.DataFrame, column: str, reasons: pd.Series, baseline_window: int,
           ) -> tuple[pd.DataFrame, pd.Series]:
    """*frame* with the fold change *column* measured from a baseline averaged over *baseline_window* timepoints,
    and *reasons* (``series_compute.exclusion_reason`` for the unchanged column) brought up to date. Unchanged when
    the window is off or *column* is not a fold change.

    Each nanowell's baseline becomes the trailing mean of its measured value over the baseline timepoint and the
    ``baseline_window - 1`` before it (:func:`moving_mean`: complete and joinable, or undefined). The fold change at
    each timepoint is then worked out against that mean by the same formula and the same rules as
    ``series_compute.site_metrics`` — a row whose own value is missing or not joinable has none, a ratio needs a
    baseline above zero — and each value left undefined says why: the row's own reason, the baseline row's own
    (a baseline unmeasurable, missing, not joinable or not in the well), the window's (:data:`BASE_EARLY_REASON`,
    :data:`BASE_GAP_REASON`, :data:`BASE_IDENTITY_REASON`), or a mean at zero ("seeded later" / "empty at
    baseline" for a count, as for a single baseline)."""
    value = base_value(column)
    baseline_window = clamp_window(baseline_window)
    if value is None or not baseline_window or not len(frame):
        return frame, reasons
    measured = pd.to_numeric(frame[value], errors="coerce").astype(float)
    ok = (frame["identity_ok"].fillna(True).astype(bool) if "identity_ok" in frame
          else pd.Series(True, index=frame.index))
    windowed, why = moving_mean(frame, measured, baseline_window)
    mean = _at_base_row(frame, value, windowed)
    base_why = _at_base_row(frame, value, why.where(why != ""))
    base_measured = _at_base_row(frame, value, measured)
    base_identity = frame[f"_base_identity_{value}"].eq(True)
    # The baseline row itself must be a usable baseline; when it is not, the single baseline's reason still holds.
    base_row_ok = base_measured.notna() & base_identity
    if column == "log2_count_fc":
        new = np.log2((measured + 1) / (mean + 1)).where(measured.notna() & mean.notna() & ok)
    else:
        usable = measured.notna() & mean.notna() & mean.gt(0)
        new = (measured / mean.where(usable)).where(usable & ok)
    out = reasons.copy()
    lost = new.isna()
    row_bad = measured.isna() | ~ok | (~frame["state"].isin(_MEASURABLE) if "state" in frame else False)
    out[~lost] = ""
    window_bad = lost & ~row_bad & base_row_ok & mean.isna()
    out[window_bad] = base_why[window_bad].map(lambda r: _BASE_REASONS.get(r, BASE_GAP_REASON))
    at_zero = lost & ~row_bad & base_row_ok & mean.notna()
    if value == "n":
        out[at_zero & measured.gt(0)] = "seeded later"
        out[at_zero & ~measured.gt(0)] = "empty at baseline"
    elif value == "c":
        out[at_zero] = "no confluence at baseline"
    else:
        out[at_zero] = "no signal above background at baseline"
    frame = frame.copy()
    frame[column] = new
    return frame, out


def averaged(frame: pd.DataFrame, column: str, values: pd.Series, window: int) -> tuple[pd.Series, pd.Series]:
    """:func:`moving_mean` of *values* (the metric *column* as numbers), with a fold change's windows starting
    after its baseline (:func:`baseline_order`)."""
    return moving_mean(frame, values, window, after=baseline_order(frame, column) if is_on(window) else None)


def apply(frame: pd.DataFrame, column: str, reasons: pd.Series, window: int,
          ) -> tuple[pd.DataFrame, pd.Series]:
    """*frame* with *column* replaced by its moving average (:func:`averaged`), and *reasons* with a reason for
    each value it left out (an exclusion reason already there is kept). Unchanged when *window* is off."""
    if not is_on(window):
        return frame, reasons
    values = pd.to_numeric(frame[column], errors="coerce")
    means, why = averaged(frame, column, values, window)
    frame = frame.copy()
    frame[column] = means
    reasons = reasons.copy()
    reasons[why != ""] = why[why != ""]
    return frame, reasons
