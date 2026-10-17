"""A well's threshold-free cell population, kept so a threshold edit costs no read.

The bar, line and statistics views reduce each well to a few numbers per
timepoint: the mean and fraction of cells above the fraction-on threshold, their
median, the mean of every cell. Almost all of that work does not depend on the
threshold. Which cells count (inclusion, the debris floor, the intensity gates),
what their values are and which timepoint each belongs to are fixed until the
rows, the gates, the ratio or the value column change; only the final "which of
these are above" depends on the threshold.

Before this module existed every threshold edit redid all of it, and on a plate
larger than the row cache that meant reading each well from disk again, twice
(once for the aggregate, once for the median and mean-of-all beside it). On a
60-well plate of 1.5M-cell wells one edit took about 50 s: 102 narrow reads and
about 30 s of re-filtering, for an answer that changed only in its last step.

A :class:`WellPopulation` is that fixed part, grouped by timepoint so a query
does not rescan the well for each one:

* ``groups`` — the aggregate's partition: per timepoint, the counted cells'
  values in row order (and the thresholding on-column, and field codes when
  per-field spreads were asked for). Built from
  ``data_loading.threshold_free_arrays`` and answered with
  ``data_loading._aggregate_timepoint`` — the same two functions
  ``aggregate_with_threshold_df`` composes, so the numbers are identical, not
  merely close.
* ``extras`` — what ``stats_compute._extra_at`` reads for each of the well's
  timepoints (the median above threshold and the mean of all cells use a
  tolerance match on ``timepoint_hours`` rather than the aggregate's exact
  partition). Where a timepoint's cells are the same values in the same order as
  the aggregate group's, the arrays are shared rather than copied.

Qt-free and ctx-free: building and querying are pure functions of a frame and
their arguments. The caching — the key, the byte-bounded store and when it is
dropped — lives in ``stats_compute`` and ``core/stats_cache_state.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from domain.tp_match import tp_close_mask
from review.data.data_loading import _aggregate_timepoint, threshold_free_arrays

#: Mirrors ``stats_compute._EXTRA_TABLE_MAX_TIMEPOINTS``: a well with more
#: distinct timepoints than this is not tabulated (each entry is a pass over
#: the well), and the caller computes the timepoint it needs on its own.
EXTRA_TABLE_MAX_TIMEPOINTS = 64


@dataclass(frozen=True)
class _Group:
    """One timepoint's counted cells, in row order."""

    t: float
    vals: np.ndarray
    on: np.ndarray | None
    fov_codes: np.ndarray | None


@dataclass(frozen=True)
class _Extra:
    """What ``_extra_at`` reads at one timepoint: every matched cell's value, in row order."""

    vals: np.ndarray
    on: np.ndarray | None
    mean_all: float


@dataclass
class WellPopulation:
    """See the module docstring. ``nbytes`` is what the store budgets against."""

    groups: tuple = ()
    fov_labels: np.ndarray | None = None
    #: ``None`` when built without per-field data; a per-field query then has
    #: to rebuild with it rather than guess.
    fov_trusted: bool | None = None
    #: ``None``: the frame cannot answer the extras at all (every timepoint is
    #: ``(nan, nan)``). An empty dict with ``extras_complete`` False: too many
    #: timepoints to tabulate.
    extras: dict | None = field(default_factory=dict)
    extras_complete: bool = True
    nbytes: int = 0

    @property
    def has_fov(self) -> bool:
        return self.fov_trusted is not None


def _small_codes(codes: np.ndarray) -> np.ndarray:
    hi = int(codes.max(initial=0))
    for dtype in (np.int8, np.int16, np.int32):
        if hi <= np.iinfo(dtype).max:
            return codes.astype(dtype)
    return codes


def build_population(
    df: pd.DataFrame,
    *,
    tp_col: str,
    val_col: str,
    fluor_gates: dict | None,
    ratios: dict | None,
    on_col: str | None,
    per_fov: bool,
    extra_inputs: tuple | None,
) -> WellPopulation:
    """The population of *df* under these settings.

    *extra_inputs* is ``stats_compute._extra_inputs(df, …)`` for the same
    settings — passed in rather than recomputed so the two definitions cannot
    drift — or ``None`` when the frame cannot answer the extras.
    """
    filtered = threshold_free_arrays(
        df, tp_col=tp_col, val_col=val_col, fluor_gates=fluor_gates,
        per_fov_spread=per_fov, ratios=ratios, on_col=on_col)
    groups: list[_Group] = []
    fov_labels = None
    fov_trusted: bool | None = None
    if filtered is not None:
        tp_arr, val_arr, fov_arr, on_arr, trusted = filtered
        codes = None
        if per_fov:
            fov_trusted = bool(trusted)
            if fov_trusted and fov_arr is not None:
                raw_codes, labels = pd.factorize(fov_arr, use_na_sentinel=False)
                codes = _small_codes(np.asarray(raw_codes))
                fov_labels = np.asarray(labels, dtype=object)
        uniq, members = _split_by_value(tp_arr)
        for t, idx in zip(uniq, members):
            groups.append(_Group(
                t=t,
                vals=val_arr[idx],
                on=on_arr[idx] if on_arr is not None else None,
                fov_codes=codes[idx] if codes is not None else None,
            ))
    elif per_fov:
        fov_trusted = True
    extras, complete = _build_extras(extra_inputs, groups)
    pop = WellPopulation(groups=tuple(groups), fov_labels=fov_labels,
                         fov_trusted=fov_trusted, extras=extras,
                         extras_complete=complete)
    pop.nbytes = _measure(pop)
    return pop


def _split_by_value(values: np.ndarray) -> tuple[np.ndarray, list[np.ndarray]]:
    """``np.unique(values)`` and, for each, the positions holding it in ascending order.

    The same partition as ``[np.flatnonzero(values == u) for u in np.unique(values)]``
    in one hashing pass and one stable sort of small integer codes, instead of a
    float sort plus a full scan per distinct value — a well holds a dozen
    timepoints over a million cells. ``-0.0`` is folded into ``0.0`` first, as
    ``np.unique`` treats them as one value. *values* must hold no NaN.
    """
    folded = values + 0.0
    codes, uniq = pd.factorize(folded, sort=True)
    order = np.argsort(codes, kind="stable")
    bounds = np.cumsum(np.bincount(codes, minlength=len(uniq)))[:-1]
    return np.asarray(uniq), np.split(order, bounds)


def _build_extras(inputs: tuple | None, groups: list[_Group]) -> tuple[dict | None, bool]:
    if inputs is None:
        return None, True
    base, tp, val, on = inputs
    rows = np.flatnonzero(base)
    times, members = _split_by_value(tp[rows])
    if times.size > EXTRA_TABLE_MAX_TIMEPOINTS:
        return {}, False
    by_t = {float(g.t): g for g in groups}
    out: dict[float, _Extra] = {}
    for u in times:
        # ``_extra_at`` matches by tolerance: every counted row whose timepoint
        # is close to *u*. Closeness is a property of the value, so it is
        # decided once per distinct value and the rows follow from the groups;
        # rows are re-sorted so values keep the frame's row order.
        with np.errstate(invalid="ignore"):
            close = tp_close_mask(times, float(u))
        picked = [members[i] for i in np.flatnonzero(close)]
        idx = rows[picked[0] if len(picked) == 1 else np.sort(np.concatenate(picked))]
        vals = val[idx]
        on_sub = on[idx] if on is not None else None
        # Share the aggregate group's arrays when they hold the same values in
        # the same order: every reduction then reads identical input, and a
        # plate whose two timepoint definitions agree (the usual case) pays
        # for its values once.
        g = by_t.get(float(u))
        if g is not None and np.array_equal(g.vals, vals) and (
                (on_sub is None and g.on is None)
                or (on_sub is not None and g.on is not None and np.array_equal(g.on, on_sub))):
            vals, on_sub = g.vals, g.on
        out[float(u)] = _Extra(vals=vals, on=on_sub,
                               mean_all=float(vals.mean()) if vals.size else float("nan"))
    return out, True


def _measure(pop: WellPopulation) -> int:
    seen: set[int] = set()
    total = 0

    def add(arr) -> None:
        nonlocal total
        if arr is None or id(arr) in seen:
            return
        seen.add(id(arr))
        total += int(arr.nbytes)

    for g in pop.groups:
        add(g.vals)
        add(g.on)
        add(g.fov_codes)
    for e in (pop.extras or {}).values():
        add(e.vals)
        add(e.on)
    if pop.fov_labels is not None:
        add(pop.fov_labels)
        total += sum(len(str(x)) for x in pop.fov_labels) + 64 * len(pop.fov_labels)
    return total + 256 * (len(pop.groups) + len(pop.extras or {})) + 512


def aggregate(pop: WellPopulation, *, threshold: float, use_sem: bool,
              per_fov_spread: bool) -> list:
    """``aggregate_with_threshold_df``'s points, answered from *pop*.

    A per-field query needs a population built with ``per_fov=True``; the
    caller checks :attr:`WellPopulation.has_fov` first.
    """
    if per_fov_spread and not pop.has_fov:
        raise ValueError("per-field aggregate asked of a population built without field labels")
    trusted = bool(pop.fov_trusted) if per_fov_spread else True
    result = []
    for g in pop.groups:
        above_mask = g.on if g.on is not None else (g.vals > threshold)
        f = None
        if per_fov_spread and trusted:
            f = pop.fov_labels[g.fov_codes]
        result.append(_aggregate_timepoint(
            g.t, g.vals, above_mask, f, use_sem=use_sem,
            per_fov_spread=per_fov_spread, fov_trusted=trusted))
    return result


class ExtraTable:
    """``{t: (median_above, mean_all)}`` for a population at one threshold, computed per lookup.

    A redraw asks for one or two timepoints of a twelve-timepoint well; a
    median is a partition of every matched cell, so each is computed the first
    time it is asked for and remembered. :meth:`get` answers ``None`` for a
    timepoint the population did not tabulate, which sends the caller to its
    own computation, as a dict lookup miss did.
    """

    def __init__(self, pop: WellPopulation, threshold: float) -> None:
        self._extras = pop.extras or {}
        self._threshold = threshold
        self._done: dict[float, tuple[float, float]] = {}

    def get(self, t, default=None):
        hit = self._done.get(t)
        if hit is not None:
            return hit
        e = self._extras.get(t)
        if e is None:
            return default
        above = e.vals[e.on] if e.on is not None else e.vals[e.vals > self._threshold]
        hit = ((float(np.median(above)) if above.size else float("nan")), e.mean_all)
        self._done[t] = hit
        return hit


def extra_table(pop: WellPopulation, threshold: float) -> ExtraTable | None:
    """The lazy ``{t: (median_above, mean_all)}`` table for *pop* at *threshold*.

    ``None`` when the frame could not answer at all; a timepoint missing from
    the table (or an incomplete one) sends the caller to its own computation.
    """
    if pop.extras is None:
        return None
    return ExtraTable(pop, threshold)
