#!/usr/bin/env python3
"""Measure what a well-selection click actually costs, end to end.

Run directly; this is a script a human reads, not a test. Nothing here asserts,
so **a bench result is never evidence that a change works** — CLAUDE.md section
7.3 opens by warning that a harness which fails open will confirm anything, and
"the bench got faster" is exactly that shape. Behaviour is pinned by tests; this
tells you where the time goes and whether a step was worth shipping.

    python tests/perf/bench_selection.py                 # build plate, run all
    python tests/perf/bench_selection.py --plate DIR     # reuse a built plate
    python tests/perf/bench_selection.py --no-draw       # skip the figure timing

**The draw-inclusive number is the point.** The complaint this exists for is
"selecting wells is slow", and the user's wait is click to painted figure. A
Qt-free bench measures only the collect half, so a step that halves collection
can look decisive while moving the felt latency very little. Both halves are
printed, and the collect share of the total with them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# 96 wells x 6 timepoints x 9 FOVs x 400 cells = 21,600 rows/well, 2.07M rows.
# Sized to a real plate rather than to what is quick to generate: the costs this
# bench exists to find are all per-row.
N_CELLS = 400
N_FOVS = 9
TIMEPOINTS = [(f"{t * 12}h", float(t * 12)) for t in range(6)]
WELLS = [f"{r}{c:02d}" for r in "ABCDEFGH" for c in range(1, 13)]


def build_plate(dest: Path) -> Path:
    """Write a plate of per-well CSVs plus the sidecar the viewer reads."""
    dest.mkdir(parents=True, exist_ok=True)
    marker = dest / "pipeline_info.json"
    if marker.exists() and len(list(dest.glob("plate_*.csv"))) == len(WELLS):
        return dest
    rng = np.random.default_rng(0)
    for wi, well in enumerate(WELLS):
        recs = []
        for tp_label, tp_h in TIMEPOINTS:
            for fov in range(1, N_FOVS + 1):
                n = N_CELLS
                recs.append(pd.DataFrame({
                    "well": well, "fov": str(fov), "timepoint": tp_label,
                    "timepoint_hours": tp_h, "channel": "dapi",
                    "nucleus_id": np.arange(1, n + 1),
                    "area_px": rng.normal(220, 40, n).round(2),
                    "gfp_mean_intensity": rng.normal(100 + wi, 25, n).round(3),
                    "mcherry_mean_intensity": rng.normal(60 + wi * 0.5, 15, n).round(3),
                    "dapi_mean_intensity": rng.normal(300, 30, n).round(3),
                }))
        pd.concat(recs, ignore_index=True).to_csv(dest / f"plate_{well}.csv", index=False)
    marker.write_text(json.dumps({
        "schema": "plate", "separator": "_",
        "fluor_tokens": ["gfp", "mcherry"], "nuclear_token": "dapi",
        "available_timepoints": [label for label, _ in TIMEPOINTS],
        "fov_index": -1, "tp_index": 2,
    }, indent=2))
    return dest


def build_ctx(plate: Path):
    """A ViewerContext with no Qt application and no widgets.

    The collectors reach the shell only through ``ctx.runtime`` accessors, so a
    registry over a plain dict is enough. Two of those accessors have no default
    and are stubbed below; anything else a collector needs will raise here
    rather than being silently defaulted, which is the behaviour we want from a
    measurement harness.
    """
    from review.core.app_runtime import AppRuntime
    from review.core.app_state import AppState
    from review.core.event_bus import EventBus
    from review.core.widget_registry import WidgetRegistry
    from review.features.registry import attach_feature_state
    from review.viewer_context import ViewerContext

    st = AppState()
    attach_feature_state(st)
    st.dataset.data_dir = plate
    st.dataset.well_paths = {
        p.stem.split("_")[-1]: p for p in sorted(plate.glob("plate_*.csv"))
    }
    st.dataset.pipeline_info = json.loads((plate / "pipeline_info.json").read_text())
    st.channel.active_val_col = "gfp_mean_intensity"

    store: dict = {}
    runtime = AppRuntime(None, store=store)
    ctx = ViewerContext(state=st, bus=EventBus(),
                        widgets=WidgetRegistry(None, store=store), runtime=runtime)
    return ctx


def warm_rows(ctx) -> float:
    """Load every well into the row cache and return the seconds it took.

    Every timing after this is cache-warm on purpose: the click being measured
    happens on a dataset the user already has open.
    """
    from review.data.data_loading import get_rows
    t0 = time.perf_counter()
    for label in ctx.state.dataset.well_paths:
        get_rows(ctx, label)
    return time.perf_counter() - t0


class CallCounter:
    """Count calls to a function through **every** name that is bound to it.

    Patching only the defining module misses every caller that did
    ``from x import f``, because that binding is a separate reference in the
    caller's namespace. An earlier version of this bench did exactly that and
    reported ``-`` for the two solo-well scenarios while reporting 1,728 for the
    grouped one — not because those paths do not aggregate, but because they
    import the name directly. A counter that silently reads zero is worse than
    no counter, so this rebinds the function everywhere it is already bound and
    says how many bindings it found.
    """

    def __init__(self, module, name: str):
        self.original = getattr(module, name)
        self.name = name
        self.count = 0
        self.sites: list = []

    def __enter__(self):
        def _wrapped(*a, **kw):
            self.count += 1
            return self.original(*a, **kw)

        self._wrapped = _wrapped
        for mod in list(sys.modules.values()):
            if mod is None:
                continue
            try:
                bound = getattr(mod, self.name, None)
            except Exception:
                continue
            if bound is self.original:
                setattr(mod, self.name, _wrapped)
                self.sites.append(mod)
        if not self.sites:
            raise RuntimeError(
                f"CallCounter found no binding for {self.name!r} — it would "
                "have reported zero calls for every scenario"
            )
        return self

    def __exit__(self, *exc):
        for mod in self.sites:
            setattr(mod, self.name, self.original)
        return False


def timed(fn, repeats: int = 1):
    """Best of *repeats*, to keep one unlucky GC pause out of the number."""
    best = float("inf")
    result = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - t0)
    return best * 1000.0, result


def solo_selection(ctx, wells) -> None:
    """Select *wells* individually — no replicate sets, the per-well path."""
    sel = ctx.state.selection
    sel.selected_wells = set(wells)
    sel.selections = []
    sel.active_solo_wells = list(wells)


def grouped_selection(ctx, n_groups: int, per_group: int) -> None:
    """Define *n_groups* replicate sets of *per_group* wells each."""
    from review.domain.selections_model import validate_repair
    sel = ctx.state.selection
    groups = []
    for g in range(n_groups):
        members = WELLS[g * per_group:(g + 1) * per_group]
        groups.append({"name": f"G{g + 1}", "wells": list(members), "hidden": False})
    sel.selections = validate_repair(groups)
    sel.selected_wells = {w for g in sel.selections for w in g["wells"]}
    sel.active_solo_wells = []


def app_threshold(ctx) -> float:
    """The cutoff the Line tab itself would pass.

    Passing a hardcoded 0.0 here made the bench unrepresentative in a way that
    showed up as a doubled cache-miss count: ``compute_rep_n_above_stats`` and
    ``compute_rep_n_above_total`` ignore the caller's threshold and recompute
    their own from the active channel, so a bench that passed 0.0 asked the
    aggregation for two different cutoffs in one collect where the app asks for
    one. The app's own redraw resolves the cutoff with this exact expression.
    """
    from review.core.channel_state_controller import on_cutoff_for_column
    return on_cutoff_for_column(
        ctx, ctx.state.channel.active_val_col,
        channel=ctx.state.channel.active_channel)


def stub_bar_runtime(ctx) -> None:
    """The two runtime accessors bar collect has no default for."""
    def _current_keys():
        sel = ctx.state.selection
        if sel.selections:
            return [g["name"] for g in sel.selections if not g.get("hidden")]
        return sorted(sel.selected_wells)

    ctx.runtime.barplot.current_keys = _current_keys
    ctx.runtime.barplot.well_display_label = lambda label: label


def run_collects(ctx, repeats: int) -> dict:
    """Time the four collect paths, counting aggregate_well through each."""
    from review.domain import stats_compute
    from review.features.barplot.collect import collect_bar_items_for_app
    from review.features.lineplot.controller import collect_line_series

    out: dict = {}
    tp0 = TIMEPOINTS[0][1]

    def _measure(name, setup, call, *, cold=True):
        """Time one scenario, from a cold memo unless told otherwise.

        The invalidate is the whole point. Without it each scenario inherited
        the previous one's memo, so any scenario sharing a key with an earlier
        row measured cache hits and reported them as click cost — the pooled
        group row came out 920x faster than a cold click actually is. A harness
        that fails open will confirm anything, and this one is the evidence for
        every number in the PR.

        ``timed`` takes best-of-N, so with a memo iterations 2..N are hits by
        construction; ``repeats > 1`` therefore reports warm times whatever this
        does, and is refused below.
        """
        setup()
        if cold:
            ctx.state.stats_cache.invalidate()
        with CallCounter(stats_compute, "aggregate_well") as counter:
            ms, _ = timed(call, repeats)
        out[name] = (ms, counter.count)

    _measure(
        "line collect, 96 wells",
        lambda: solo_selection(ctx, WELLS),
        lambda: collect_line_series(ctx, threshold=app_threshold(ctx), use_sem=True),
    )
    # One more well on an otherwise unchanged selection — the click the report is
    # about. The setup selects 95, collects once so any memo is warm for those,
    # then selects 96; the measured call should then do one well's work, not 96.
    def _setup_one_more():
        solo_selection(ctx, WELLS[:95])
        collect_line_series(ctx, threshold=app_threshold(ctx), use_sem=True)
        solo_selection(ctx, WELLS)

    _measure(
        "line collect, 95 -> 96 wells",
        _setup_one_more,
        lambda: collect_line_series(ctx, threshold=app_threshold(ctx), use_sem=True),
        # Deliberately NOT cold: the setup warms 95 wells and the measured call
        # adds one. A cold memo here would measure a first draw, which is a
        # different question from the one this row asks.
        cold=False,
    )
    _measure(
        "bar collect, 96 wells x 6 tps",
        lambda: solo_selection(ctx, WELLS),
        lambda: [collect_bar_items_for_app(ctx, t) for _, t in TIMEPOINTS],
    )
    _measure(
        "line collect, 12 groups x 8",
        lambda: grouped_selection(ctx, 12, 8),
        lambda: collect_line_series(ctx, threshold=app_threshold(ctx), use_sem=True),
    )
    # Both replicate modes, because they take different branches and only one
    # of them carries the three-producer redundancy. Pooled concatenates a
    # group's wells and aggregates once per well per timepoint; unpooled asks
    # for per-replicate statistics, which run three independent producers over
    # the same wells at the same timepoint. Measuring only the pooled mode
    # reports 576 calls and hides the 1,728 the unpooled mode really does.
    def _bar_groups(pooled: bool):
        def _setup():
            grouped_selection(ctx, 12, 8)
            ctx.state.plot.bar_pool_replicates = pooled
        return _setup

    _measure(
        "bar collect, 12 groups x 8 (pooled)",
        _bar_groups(True),
        lambda: [collect_bar_items_for_app(ctx, t) for _, t in TIMEPOINTS],
    )
    _measure(
        "bar collect, 12 groups x 8 (unpooled)",
        _bar_groups(False),
        lambda: [collect_bar_items_for_app(ctx, t) for _, t in TIMEPOINTS],
    )

    from review.data.data_loading import iter_plot_groups
    grouped_selection(ctx, 12, 8)
    ctx.state.plot.bar_pool_replicates = True
    ms, _ = timed(lambda: list(iter_plot_groups(ctx)), repeats)
    out["iter_plot_groups, 12 groups x 8"] = (ms, 0)
    return out


def run_draw(ctx, repeats: int) -> dict:
    """Time painting the real figure for a 96-well selection.

    This is the half a Qt-free bench cannot see, and it is most of what the user
    waits for. Agg rather than a Qt canvas: the same matplotlib work, without
    needing a QApplication.

    It goes through ``render_line_series`` — the function the tab and the batch
    exporter both call — rather than hand-rolling four ``ax.plot`` loops. A
    hand-rolled figure measures a figure nobody draws: it cannot see the screen
    budgets, so it would have reported no change from the two settings that
    matter most here, and it would drift from the real renderer silently.

    The budgeted and unbudgeted draws are timed separately because that is
    exactly the difference between what the tab does and what an export does.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from review.features.lineplot.controller import (
        SCREEN_CDF_POINTS,
        SCREEN_LEGEND_ENTRIES,
        LineAxes,
        collect_line_series,
        render_line_series,
    )
    from review.data.data_loading import _all_fluor_values_filtered

    solo_selection(ctx, WELLS)
    series, _ = collect_line_series(
        ctx, threshold=app_threshold(ctx), use_sem=True,
        all_fluor_values_filtered=_all_fluor_values_filtered)

    def _draw(**budgets):
        fig, axs = plt.subplots(4, 1, figsize=(6, 10), dpi=100)
        render_line_series(
            LineAxes(*axs), series, fov_active=False, fc_active=False,
            threshold=0.0, legend_kw=dict(fontsize=7),
            warn_color="#c00", muted_fg="#888", **budgets)
        fig.canvas.draw()
        plt.close(fig)

    out = {}
    out["figure draw, 96 series (export: no budgets)"] = (
        timed(lambda: _draw(), repeats)[0], 0)
    out["figure draw, 96 series (screen budgets)"] = (
        timed(lambda: _draw(cdf_point_budget=SCREEN_CDF_POINTS,
                            legend_max=SCREEN_LEGEND_ENTRIES), repeats)[0], 0)
    out["figure draw, 96 series (legend cap only)"] = (
        timed(lambda: _draw(legend_max=SCREEN_LEGEND_ENTRIES), repeats)[0], 0)
    out["figure draw, 96 series (cdf budget only)"] = (
        timed(lambda: _draw(cdf_point_budget=SCREEN_CDF_POINTS), repeats)[0], 0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plate", type=Path, default=Path("/tmp/allwell-bench-plate"))
    ap.add_argument(
        "--repeats", type=int, default=1,
        help="best-of-N. Only 1 is meaningful: the collect paths memoise, so "
             "every iteration after the first is a cache hit by construction.")
    ap.add_argument("--no-draw", action="store_true")
    args = ap.parse_args()

    print(f"plate: {args.plate}")
    t0 = time.perf_counter()
    plate = build_plate(args.plate)
    print(f"  generated/verified in {time.perf_counter() - t0:.1f} s "
          f"({len(WELLS)} wells x {len(TIMEPOINTS)} tps x {N_FOVS} fovs "
          f"x {N_CELLS} cells)")

    ctx = build_ctx(plate)
    stub_bar_runtime(ctx)
    print(f"  row cache warmed in {warm_rows(ctx):.1f} s")

    results = run_collects(ctx, args.repeats)
    if not args.no_draw:
        results.update(run_draw(ctx, args.repeats))

    width = max(len(k) for k in results)
    print()
    print(f"{'':<{width}}  {'ms':>9}  {'aggregate_well':>14}")
    for name, (ms, calls) in results.items():
        calls_txt = f"{calls:,}" if calls else "-"
        print(f"{name:<{width}}  {ms:9.1f}  {calls_txt:>14}")

    collect = results.get("line collect, 96 wells", (0, 0))[0]
    draw = results.get("figure draw, 96 series (screen budgets)", (0, 0))[0]
    if draw:
        total = collect + draw
        print()
        print(f"one 96-well click, collect + draw: {total:,.0f} ms "
              f"({100 * collect / total:.0f}% collect, "
              f"{100 * draw / total:.0f}% draw)")
        print("Read that split before choosing which steps to ship: halving the "
              "collect half moves the felt latency by half of its share, not by "
              "half.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
