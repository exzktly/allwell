"""Per-redraw memo of the control denominator series.

A fold-change denominator is looked up as "this control's value at this
timepoint", and the helpers that answer it (``member_stats_series`` and its
fraction / N counterparts) build the control's **whole** timepoint series on
every call. Under one global control that was mostly hidden: the bar collector
hoists the lookup out of the member loop and resolves it once per timepoint.

The vs-t0 stacking blocks do not, and never did. They call
``control_*_at_for_bar`` inside the member loop, once per member per axis, each
rebuilding the entire series from scratch. Measured on a 24-member plate of
2-well replicate sets over 6 timepoints with a single control:

    vs-control only        18 full series builds
    vs-control + vs-t0    882 full series builds

Per-sample controls would multiply that by the number of distinct controls, so
this class exists before the feature does. It memoises by **label** rather than
by ``(label, timepoint)``, because a series build costs the same whether one
timepoint or all of them are wanted — so caching the series collapses both the
per-timepoint and the per-member repetition at once. The same plate above
issues 3 builds per distinct control, total, regardless of member or timepoint
count.

**A cache instance must not outlive one redraw.** It captures the threshold,
value column, spread mode and gates it was built with and has no invalidation
of its own; those are exactly the things a user changes between redraws. Build
it as a local in the collector. It is deliberately not hung off
``ctx.state.stats_cache``, whose entries are keyed and invalidated on purpose.

Qt-free (``tests/test_qt_free_layers.py``); it imports only the sibling
``fold_change`` helpers it memoises.
"""

from __future__ import annotations

from typing import Callable


class ControlSeriesCache:
    """Memoises control denominator series for the span of one redraw.

    Widens what :func:`fold_change.build_cell_scaling` already did with its
    local ``control_cache`` — one label's ``{tp: mean}`` — to any number of
    labels and all three measured quantities.
    """

    def __init__(self, ctx, *, threshold: float, val_col: str, use_sem: bool,
                 per_fov_spread: bool, fluor_gates) -> None:
        self._ctx = ctx
        self._kw = dict(
            threshold=threshold, val_col=val_col, use_sem=use_sem,
            per_fov_spread=per_fov_spread, fluor_gates=fluor_gates,
        )
        self._stats: dict[str, dict] = {}
        self._frac: dict[str, dict] = {}
        self._n: dict[str, dict] = {}
        #: Number of underlying series builds performed. Read by the perf
        #: regression test, which asserts the memo actually collapses repeats
        #: rather than trusting that it does.
        self.builds = 0

    # ── series accessors ─────────────────────────────────────────────────────

    def _memo(self, store: dict, ref, build: Callable) -> dict:
        """Memoise one series, keyed on the control reference itself.

        The key must be the reference, not a rendering of it, and the builder
        must receive the reference unchanged. Stringifying it here — which this
        did while a control was a plain string — silently handed the resolver a
        ``repr`` once references became objects, so every series came back
        empty and every bar went NaN. ``ControlRef`` is a frozen dataclass and
        hashes by value, so it is a key in its own right.
        """
        key = ref if ref is not None else ""
        if key not in store:
            self.builds += 1
            store[key] = build(self._ctx, ref, **self._kw) or {}
        return store[key]

    def stats_series(self, ref) -> dict:
        """``{t: (mean, spread)}`` for *ref*."""
        from well_viewer.domain.fold_change import member_stats_series
        return self._memo(self._stats, ref, member_stats_series)

    def frac_series(self, ref) -> dict:
        """``{t: (frac, frac_spread)}`` for *ref*."""
        from well_viewer.domain.fold_change import member_frac_series
        return self._memo(self._frac, ref, member_frac_series)

    def n_series(self, ref) -> dict:
        """``{t: (n_above, n_above_spread)}`` for *ref*."""
        from well_viewer.domain.fold_change import member_n_series
        return self._memo(self._n, ref, member_n_series)

    # ── point lookups ────────────────────────────────────────────────────────
    #
    # These mirror the ``control_*_at_for_bar`` helpers exactly, including the
    # relative-tolerance timepoint match, so a caller can swap one for the
    # other without changing which timepoint it resolves to.

    def stats_at(self, ref, t: float):
        """``(mean, spread)`` at *t*, or ``None`` when not sampled there."""
        from well_viewer.domain.fold_change import _series_at
        return _series_at(self.stats_series(ref), t)

    def frac_at(self, ref, t: float):
        """``(frac, spread)`` at *t*, or ``None`` when not sampled there."""
        from well_viewer.domain.fold_change import _series_at
        return _series_at(self.frac_series(ref), t)

    def n_at(self, ref, t: float):
        """``(n_above, spread)`` at *t*, or ``None`` when not sampled there."""
        from well_viewer.domain.fold_change import _series_at
        return _series_at(self.n_series(ref), t)

    def mean_at(self, ref, t: float):
        """The mean alone at *t* — the ``control_mean_at_for_bar`` shape."""
        got = self.stats_at(ref, t)
        return None if got is None else got[0]
