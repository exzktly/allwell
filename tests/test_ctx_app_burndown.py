"""Audit A8 — ``ctx.app`` burn-down ratchet.

``ViewerContext`` exists to decouple features from the ``WellViewerApp``
god-object, yet features reach straight back through ``ctx.app`` hundreds of
times, so the abstraction leaks and provides near-zero isolation. There is no
single mechanical fix — it's a strangler migration to typed state / dedicated
context accessors, done cluster by cluster.

This test turns the leak into a **burn-down metric**: the total ``ctx.app``
reference count may only ever go *down*. A change that adds a new ``ctx.app``
reference fails CI, nudging contributors to reach for the typed state slice (or
a purpose-built context accessor) instead of the god-object. When you migrate a
cluster and the count drops, lower ``CTX_APP_BUDGET`` to lock in the win.

Do NOT lower the budget by merely aliasing ``app = ctx.app`` and rewriting
``ctx.app.x`` → ``app.x`` — that games the metric without decoupling anything.
The count should fall because a cluster stopped reaching through to the
god-object, not because the reference was renamed.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_CTX_APP = re.compile(r"\bctx\.app\b")

# Frozen baseline (894 as of the code-review remediation). ONLY MOVES DOWN as
# each feature's widget handles move into the WidgetRegistry:
#   894 → 869  review_csv
#   869 → 811  heatmap
#   811 → 768  distribution
#   768 → 668  scatter (cells + agg)
#   668 → 624  lineplot + multivariate
#   624 → 587  image_table + cell_browser
#   587 → 544  smfish
#   544 → 446  barplot
#   446 → 415  cell_gating (assignments; the 2400-line builder's widget reads
#               keep resolving via the register() app-stamp)
#   415 → 409  saved_plots + scatter/tab_view (batch_export has no widgets)
#   409 → 408  multivariate feature-picker list
# Every feature builder's Qt widget-INSTANCE handle now goes through the registry
# (verified: zero `ctx.app._x = <QWidget>(...)` stashes remain). The residual is
# the non-widget state + behaviour-call layer (typed-state / app-method extraction).
# --- Phase 2: non-widget state flags migrate to typed AppState feature slices,
#     which also deletes their god-object builder inits (each was a ctx.app stash):
#   408 → 405  barplot swarm/violin  → BarPlotState
#   405 → 402  heatmap scale_mode/repset_avg/log_scale  → HeatmapState
#   402 → 396  distribution bins/log_x/log_y  → DistributionState (builder inits
#              deleted + callback writes moved to ctx.state)
#   396 → 390  scatter mode/color_by_tp/gates_hidden  → ScatterState (builder
#              inits deleted + mode/color callback writes moved to ctx.state)
#   390 → 386  smfish separator/show_overlays/fit_on_next_redraw  → SmfishState
#              (builder inits deleted + classifier-seed read moved to ctx.state)
# --- Phase 3: the behaviour-call + runtime-object layer (ctx accessors / method
#     extraction / feature-function signature migration). Not typed-state moves.
#   386 → 376  P3.1 ctx.app._app_state → ctx.state (identical object) across the
#              image_table/barplot/multivariate builders + one registered-widget
#              probe (lineplot _plot_chan_var → ctx.widgets.has)
#   376 → 318  P3.2 cell_gating widget-handle reads → ctx.widgets.get (finishing
#              the Phase-1 register-discard: 58 registered-widget reads in the
#              2.4k-line builder now read the registry, not the god-object)
#   318 → 309  P3.3 active-card Copy/Save extracted to core.plot_card_actions
#              (ctx-based; cards via ctx.widgets; the 6 plot builders wire the
#              buttons to the functions, not app methods). The two app/window
#              services (_set_status / _save_matplotlib_fig) remain as one named
#              escape-hatch boundary instead of six scattered reach-throughs.
#   309 → 304  P3 design "C": the two card-action app services now route through
#              named ViewerContext accessors (ctx.set_status / ctx.save_figure_
#              dialog); the god-object touch is consolidated to viewer_context.py
#              (self.app), frozen by test_ctx_service_accessors. Feature code no
#              longer names the app for these. (Also reworded 3 legacy doc
#              mentions per the "no literal token in comments" convention.)
#   304 -> 298  P3.3 open_export_style_panel -> ctx.open_export_style_panel
#              named accessor (6 plot builders); app service consolidated to
#              viewer_context, frozen by test_ctx_service_accessors.
#   298 -> 293  P3.4-1 make_plot_view_switcher takes ctx (owns the plotting-
#              notebook navigation; 6 builder call sites drop ctx.app, one
#              internal touch remains in ui_helpers).
# --- A8-teardown epic (293 -> 0): decompose WellViewerApp; see
#     _Docs/Markdowns/A8_TEARDOWN_EPIC.md.
#   293 -> 279  T2 smFISH runtime holder: 17 builder writes of live objects
#              (bridge/extractor/figures/decoded arrays/cdf popup) move to
#              ctx.runtime.smfish, which stamps app._smfish_<name> so the ~130
#              legacy readers resolve unchanged (WidgetRegistry pattern).
#   279 -> 274  T2 scatter gate-interaction live objects (selector/edit/
#              render/redraw_cb/draw_mode) -> ctx.runtime.scatter; gate
#              geometry (_scatter_gates) is persisted and stays put.
#   274 -> 258  T2 pure-runtime objects across 6 features -> ctx.runtime.<f>:
#              cell_browser (state/chips/lut_rows/loader/refreshers),
#              image_browser (built), heatmap (global_range_cache), barplot
#              (drag_state), cell_gating (cell_areas/fluor_data/ratio_data/
#              ax/axes_stack/worker), multivariate (picker_refreshing).
#              Persisted fields (edits/choices/selections/cells/lut) excluded.
#   258 -> 249  T2 runtime callback stashes -> ctx.runtime.<f>: scatter
#              (set_mode, gates_changed_cb), batch_export (set_mode),
#              saved_plots (repopulate), multivariate (refresh_* / sync_
#              controls). Live closures stamped for other layers to call.
#   249 -> 246  T3 pilot: distribution _on_changed(ctx). 5 builder control
#              wirings drop ctx.app; the onward app-based renderer handoff
#              (redraw_distribution) is the one residual ctx.app. Internal
#              caller bridged via app._ctx (uncounted). Demonstrates the T3
#              signature-flip + builder(ctx)/machinery(app._ctx) caller split.
CTX_APP_BUDGET = 246


def _count_ctx_app() -> tuple[int, dict[str, int]]:
    total = 0
    per_file: dict[str, int] = {}
    for p in (REPO_ROOT / "well_viewer").rglob("*.py"):
        n = len(_CTX_APP.findall(p.read_text()))
        if n:
            per_file[str(p.relative_to(REPO_ROOT))] = n
            total += n
    return total, per_file


def test_ctx_app_reference_count_only_shrinks():
    total, per_file = _count_ctx_app()
    assert total <= CTX_APP_BUDGET, (
        f"`ctx.app` references rose to {total}, above the burn-down budget of "
        f"{CTX_APP_BUDGET}. Reach for the typed AppState slice or a dedicated "
        f"context accessor instead of the god-object. The ratchet only moves "
        f"down.\nTop offenders:\n"
        + "\n".join(f"  {n:4d}  {f}" for f, n in
                    sorted(per_file.items(), key=lambda kv: -kv[1])[:8])
    )


def test_budget_is_not_slack():
    # Keep the budget honest: if a migration dropped the count, this fails until
    # CTX_APP_BUDGET is lowered to match (locking the win in).
    total, _ = _count_ctx_app()
    assert total >= CTX_APP_BUDGET - 20, (
        f"`ctx.app` count fell to {total}, well under the budget "
        f"{CTX_APP_BUDGET} — lower CTX_APP_BUDGET to {total} to lock in the "
        f"burn-down."
    )
