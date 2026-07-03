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
#   246 -> 245  T3 shared-leaf pass: the timepoint leaves take ctx —
#              timepoint_sync.set_focus_tp / current_focus_tp go pure
#              (ctx.state.plot + ctx.bus, zero residual) and
#              data_loading.get_all_timepoints takes ctx (well list from
#              ctx.state.dataset; the lone _get_rows row-fetch is the residual
#              until a typed data-service lands). That lets the distribution
#              timepoint refresh handler and the multivariate timepoint combo
#              read ctx (combo via ctx.widgets), dropping their builder-site
#              touches; the other callers (heatmap/scatter/distribution) bridge
#              through app._ctx (uncounted) pending their own handler passes.
#   245 -> 244  T3 timepoint-handler modernization: the distribution + heatmap
#              timepoint handlers now read the modern infra end-to-end — combo/
#              slider via ctx.widgets, the tp-value cache via ctx.runtime.heatmap,
#              and the split-view mode flag via a new ctx.runtime.split namespace
#              (_split_active gets a ctx home; split_view.py still writes it
#              directly, the runtime read bridges). distribution _on_tp_changed_
#              dist -> ctx is a clean -1 (split was its only residual); the two
#              heatmap handlers -> ctx net 0 (the onward _redraw render handoff
#              is the one residual, mirroring distribution _on_changed) but are
#              fully off the god-object otherwise. Also fixes a latent bug the
#              first pass introduced: registry.py's channel reactors still passed
#              bare app to the now-ctx refresh handlers, silently swallowed by
#              their except — now app._ctx, locked by test_timepoint_refresh_
#              ctx_contract. Fully zeroing the heatmap handlers waits on the
#              redraw_heatmap render-tree conversion (High-risk one-feature pass).
#   244 -> 243  R1 ctx-native row-fetch data service: new data_loading.get_rows(
#              ctx, label) does the lazy load + QC/Crop projection over ctx.state
#              (QC/crop projectors get_qc_params/apply_qc_to_cache/get_crop_region/
#              apply_crop_to_cache flipped to ctx, pure typed state after R4).
#              WellViewerApp._get_rows delegates to it; get_all_timepoints reads
#              get_rows(ctx) instead of ctx.app._get_rows -> its last residual is
#              gone. The service is reusable by stats_compute + every collect
#              module (their own ctx passes come later).
#   243 -> 241  R6 cell-gating gate values -> typed CellGatingState core slice
#              (AppState.cell_gating). The FluorGating/ThreshFracOn values move
#              off the QLineEdits: the editingFinished handlers sync edits ->
#              slice, persistence load writes the slice, accessors/CDF/saved-
#              plots/load-kick read the slice; edits become the input surface,
#              seeded FROM the slice at build. The {channel -> QLineEdit} maps
#              register via ctx.widgets (widget containers) -> -2. Subsumes the
#              loose _thresh_frac_on_saved shadow dict. Locked by
#              test_cell_gating_gate_state (real QLineEdits, offscreen: sync,
#              defaults, ratio-key resolution, garbage-keeps-last-committed,
#              handler fan-out, editingFinished wiring).
#   241 -> 241  Shared render-leaf passes (0-count enablers, all pure typed state
#              post-R6/R7): R7 export-prefs -> ExportPrefsState; get_all_fluor_gates
#              + the cell-gating gate accessors -> ctx; the export-style chain
#              (_ensure_export_style_prefs / figure_prefs [figs via ctx.widgets] /
#              apply_export_style_to_current) -> ctx; the label tier
#              (active_channel_label / ratio_label_for / well_display_label /
#              replicate_display_label) -> ctx; get_thresh_frac_on -> ctx
#              (guard reframed, behavior-preserving: channel.threshold has no
#              writers). Count held because ~40+ consumers call each via the
#              uncounted god-method — but every shared leaf a plot renderer
#              touches is now ctx, which is what lets a render feature convert
#              net-negative.
#   241 -> 239  R3 distribution render tree -> ctx: redraw_distribution + its
#              helpers (_setup_axes / _apply_card_style / _apply_export_style /
#              _sync_distribution_tp_to_focus / _display_label_for / _render_
#              overlay / _render_faceted / _retint_axes_text / _xlabel_for /
#              _title_for) all take ctx — widgets via ctx.widgets, state via
#              ctx.state, every shared leaf now ctx-native (zero god-object
#              residual). _on_changed drops its ctx.app; plot_orchestrator
#              bridges via app._ctx. FIRST net-negative render conversion.
#              (Data path covered by test_distribution_log_y / iter_plot_groups
#              char test; the paint — theme/label pixels — is the only
#              headless-unverifiable part and wants an interactive smoke.)
#   239 -> 233  R-leaf + R2 heatmap (enabler +2, heatmap render -8). The shared
#              stats aggregation leaf
#              (aggregate_well / aggregate_group / _thresh_on_col) now takes ctx:
#              rows via the ctx-native get_rows, ratios via ctx.state, gate-on
#              flags via ctx.runtime; the ~26 app-native callers (barplot /
#              fold_change / lineplot / export / batch_export + the internal
#              stats_compute reducers) bridge via app._ctx (uncounted). Only the
#              two pure-logic delegate lookups (getattr(ctx.app, "_aggregate_*"))
#              cost +2. That unblocked R2: the heatmap render tree
#              (redraw_heatmap + active_layout / _selected_* / sync_slider /
#              _expand_repset_wells / _repset_name_for_wells / _pool_filtered_mean
#              / _cell_value / _resolve_color_scale / _compute_global_range /
#              _metric_axis_label) -> ctx — widgets via ctx.widgets, runtime
#              state via ctx.runtime.heatmap, aggregation via the ctx leaf; the
#              redraw-path tab_view handlers (_redraw / _on_cmap|scale|vminmax|
#              repset|log_changed) drop their ctx.app wiring. Net -8. Residuals:
#              _persist_settings' ctx.app._persistence, and active_layout's
#              ctx.app._active_heatmap_layout_name (no runtime-prefix home yet —
#              a typed-state follow-up). Interactive click/drag/transpose handlers
#              stay app-native (wired via ctx.app); their consumed caches are
#              stamped by ctx.runtime.heatmap so they keep resolving. Data path
#              covered by test_heatmap_cell_value_ctx; the imshow/colorbar paint
#              wants an interactive smoke.
#   233 -> 214  Headless cross-cutting cleanup toward 0. wire_save_to_gallery ->
#              ctx (6 plot tabs drop the reach-through; the capture subtree still
#              bridges via ctx.app inside the one helper). Feature runtime-attr
#              reach-throughs routed onto ctx.runtime.<feature> (stamps the same
#              app._<prefix>_<name>, behavior-identical): cell_gating scatter-gate
#              flags + gate-choice selections, scatter tp-selection / gate-geometry
#              inits, scatter-agg tp-selections, batch_export inline state,
#              image_browser subtabs.
#   214 -> 211  More cross-cutting: panelCopied status hook -> the ctx.set_status
#              accessor (lineplot/barplot); mv feature-picker refresh guard ->
#              ctx.runtime.multivariate (read-only flag).
#   211 -> 202  smFISH tab fully ctx-native. Every _smfish_* handler + the
#              sync/build helpers take ctx; the ~30 live handles + flags read
#              through ctx.runtime.smfish, state via ctx.state, well-token/CSV
#              load via the domain/data free functions (extract_well_token /
#              load_well_csv). External callers (registry activate, load_controller
#              post-load) bridge via app._ctx. Residual (10): the smFISH god-service
#              tail — _redraw / _invalidate_stats_cache / _parse_rc /
#              _refresh_review_csv / persistence save+restore / recalculate_threshold
#              — bridged through ctx.app pending their own ctx conversion.
#   202 -> 201  Extract the pure WellViewerApp._parse_rc god-method to a free
#              function domain.viewer_state.parse_rc (a well-label sort key that
#              never touched self); the delegate now forwards to it and the smFISH
#              well-sort drops its ctx.app._parse_rc. First core god-method fully
#              decomposed (the delegate is now a pure passthrough, deletable once
#              its bare-app callers migrate).
#   201 -> 187  Multivariate feature → ctx. The controller (_apply/_state/
#              mv_recompute/mv_set_*/mv_export_csv/mv_on_*), the collect layer
#              (collect_feature_matrix/available_feature_columns/
#              column_display_label), the renderer (render_multivariate), the
#              feature-picker, and the figure copy/save handlers all take ctx —
#              state via ctx.state, live handles via ctx.runtime.multivariate,
#              _get_rows/rank-colour/parse_rc via the ctx-native leaves + domain
#              color_for_replicate_set (no feature import), status/save via the
#              ctx.set_status / ctx.save_figure_dialog accessors. Residuals: the
#              genuine app services (_persistence, _rep_sets_active,
#              _rank_color_well, refresh_plot_empty_states, QMessageBox/QFileDialog
#              main-window parents) + wrap_with_empty_state, bridged via ctx.app.
#   187 -> 186  Extract the pure WellViewerApp._rank_color_well god-method to
#              domain.selections_model.color_for_well (WELL_COLORS[well_rank%N]);
#              the delegate forwards to it and multivariate collect drops its
#              ctx.app._rank_color_well. Second pure core god-method decomposed.
#   186 -> 179  Add the ``image_table`` AppRuntime namespace (prefix
#              ``_image_table_``) and route the Image Table tab's live-grid
#              container inits (cells / lut / crop_tool) through it instead of
#              hasattr/assign on ctx.app. Unblocks the full image_table controller
#              conversion (its ~30 fns + helper files) as a later pass.
#   179 -> 178  Migrate the odd-prefix app._active_heatmap_layout_name onto the
#              typed HeatmapState.active_layout_name slice — reader (active_layout)
#              via ctx.state, writers (sidebar set / load reset) via the slice,
#              dead runtime_app init removed. Clears heatmap controller's last
#              ctx.app residual.
#   178 -> 177  Multivariate collect drops its ctx.app._rep_sets_active bridge —
#              rep_sets_active is just active_replicate_sets over ctx.state, so
#              collect calls the domain resolver directly (no god-object).
CTX_APP_BUDGET = 177


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
