# A8-Teardown Epic — Retire `ctx.app` by decomposing `WellViewerApp`

> Successor to the A8 burn-down in `CODE_REVIEW_AUDIT.md`. That audit drove
> `ctx.app` from **894 → 293 (67%)** — every reduction achievable *without*
> restructuring the runtime. This epic took the remaining runtime-pinned refs
> from **293 → 51 (94% overall)** by wiring the EventBus, adding per-feature
> runtime holders, threading `ctx` through the render tree + shared leaves, and
> migrating the god-object's loose state onto typed `AppState` slices. The
> endgame — **51 → 0** and deleting the transitional `app` field from
> `ViewerContext` — is the **focused campaign** in [§ Endgame](#endgame--the-51-file-by-file-focused-campaign)
> below.
>
> **Status:** `[~]` IN PROGRESS — **51** `ctx.app` refs remain (ratchet
> `CTX_APP_BUDGET = 51`, `tests/test_ctx_app_burndown.py`). ~13 are the
> structural **floor** (the runtime stamp + sanctioned persistence unwraps +
> two shared-helper touches) that only T6 dissolves; the other ~38 are the
> convertible controller/feature/registry residue this campaign burns down.
> **Branch:** `claude/code-review-audit-framework-se53zd` (PR #597).

---

## ⚠️ HOW TO USE THIS FILE

Same legend as the audit: `` `[ ]` TODO · `[~]` IN PROGRESS · `[x]` DONE · `[!]` BLOCKED · `[-]` DROPPED ``.
Update the workstream box **and** the metric line in the same change; record the
commit SHA + proving test when `[x]`; the `ctx.app` ratchet
(`tests/test_ctx_app_burndown.py`) only moves down; never game it by relocating
a reach-through (`app = ctx.app` or pushing `ctx.app` into a callee) — the count
must fall because coupling was *removed*.

---

## Why this is a separate epic (not more of Phase 3)

Phase 3 hit a hard floor: the residual `ctx.app` refs are not lazy reach-throughs
a feature can fix locally — they are pinned by the **runtime call graph**. The
orchestration functions (`redraw_scatter_orchestrate`, `redraw_bars`, `_on_changed`,
…) are called mostly from the app-holding **controller layer**
(`core/plot_orchestrator.py`, `core/selection_controller.py`,
`core/channel_state_controller.py`, `core/tab_controller.py`, `runtime_app.py`),
which has no `ctx`. You cannot move a feature function to `ctx` while its main
callers hold `app`. Dissolving that requires changing the runtime itself — a
structural change with app-wide blast radius that deserves its own gated plan.

## The good news: the seams already exist

This epic is mostly **wiring infrastructure that was already built for it**, not
inventing new abstractions:

- **`ViewerContext` is reachable from the runtime** — `runtime_app` constructs and
  holds `self._ctx` (`app._ctx`). The controllers can obtain `ctx` today.
- **`AppState` typed slices** exist (`channel`/`selection`/`dataset`/`plot`/
  `image_view`/`stats_cache`) — the home for state the controllers pass around.
- **`WidgetRegistry`** (`ctx.widgets`) already resolves every builder widget.
- **`ViewerContext` named accessors** (design "C") give a frozen, enumerable home
  for genuine app/window services.
- **`EventBus` is fully defined but UNWIRED.** `core/event_bus.py` ships five
  capped signals — `channel_changed`, `selection_changed`, `dataset_loaded`,
  `plot_options_changed`, `redraw_requested(scope)` — whose payloads are *state
  objects, never `app`*. **`ctx.bus` currently has zero usages.** This is the
  single biggest lever: the imperative `redraw_scatter_orchestrate(app)` call the
  coordinator makes today is exactly what `redraw_requested("scatter")` was
  designed to replace.

## The residual 293, categorized (evidence-backed) — now 51

The three original categories have been almost entirely worked through; what
remains (51) is the tail of each plus the structural floor. See
[§ Endgame](#endgame--the-51-file-by-file-focused-campaign) for the current
file-by-file map.

| # | Category | 293-era | now | Technique | Workstream |
|---|----------|--------|-----|-----------|------------|
| a | Runtime-object stashes — smFISH worker bridge / extractor / figures / current arrays, scatter gate state, image-table cells | 60–80 | ~0 | Per-feature **runtime holders** (live-object namespaces, like AppState slices but non-persisted) | **T2 done** |
| b | Orchestration functions pinned to `app` by the controller layer | ~150 | ~25 | **Invert via EventBus** (T1) + **thread `ctx` through the controllers** (T3) | **T1 done, T3 tail** |
| c | Cross-cutting app operations — `_invalidate_stats_cache` (called app-wide), `_refresh_review_csv_rows`, channel/axis `_on_*` handlers | ~50 | ~13 | **Extract collaborators** / **bus events** (`stats_cache` is already a typed slice) | **T4 tail** |
| d | Structural floor — the `AppRuntime` stamp (3), sanctioned persistence unwraps (8), two shared-helper touches (2) | — | ~13 | Only **T6** (delete the field) dissolves these | **T6** |

---

## Workstreams

### T1 — Wire the EventBus (foundation) · `[x]` ALREADY IN PLACE
On inspection the bus is **already wired for the paths it should own**:
`core/bus_wiring.py` constructs a `RedrawCoordinator` (subscribes
`channel_changed` / `plot_options_changed` / `dataset_loaded` → the
`redraw_scopes_or_defer` coalescer) and a `SelectionRefreshCoordinator`
(`selection_changed`); the controllers already `emit` those signals
(`channel_state_controller`, `selection_controller`, `load_controller`, `stats`,
`fold_change_controls`, `timepoint_sync`). Connections are synchronous
`DirectConnection`, so ordering matches the direct calls they replaced.
**What T1 does NOT extend to:** the per-control `redraw_X(ctx.app)` /
`_on_changed(ctx.app)` calls in builders are **intentionally direct and
scope-local** — the distribution/heatmap scopes redraw via `redraw_app` (fans out
across *all* default plots), whereas the direct call redraws *only that scope*.
Blanket-converting them to `bus.plot_options_changed.emit(...)` would change the
redraw fan-out — a behaviour regression, not an equivalence. So these belong to
**T3** (thread `ctx`), not to bus-ification. T1's scope is complete.

### T2 — Feature runtime holders · `[x]` DONE (293 → 249)
For category (a): `core/app_runtime.py` gives each feature a `ctx.runtime.<feature>`
namespace for live objects; writing through it **stamps `app._<feature>_<name>`**
(WidgetRegistry pattern) so the ~130 legacy readers resolve unchanged while
builders stop naming `ctx.app`. **Done** across 8 features + callback stashes (293 → 249): smFISH (17 objects),
scatter (5 gate-interaction objects + set_mode/gates_changed_cb closures),
cell_browser (state/chips/lut_rows/loader/refreshers), image_browser (built),
heatmap (global_range_cache), barplot (drag_state), cell_gating (cell_areas/
fluor_data/ratio_data/ax/axes_stack/worker), multivariate (picker_refreshing +
refresh_*/sync_controls), batch_export/saved_plots (set_mode/repopulate closures).
**Excluded (persisted → belong in typed state, not a runtime holder):**
`_scatter_gates`, `_scatter_*_tp_selections`, `_image_table_cells/lut`,
`_batch_export_inline_state`, `_cell_gating_fluor_gate_edits/thresh_frac_edits/
use_scatter_*/choices`, and `_image_table_crop_tool` / `_image_browser_subtabs`
(persistence-adjacent / widget). Migrating those is a Phase-2-style *state* task,
not T2.
- **Independent of T1/T3** — low architectural risk.
- **Guardrail:** `tests/test_app_runtime.py` proves the stamp write-through and
  asserts `persistence/` never depends on `app_runtime`. Classify each field by a
  *string* search of `persistence/` (catches `getattr(app, "name")`), not just
  `app.name`, before moving it.

### T3 — Thread `ctx` through the controller layer · `[ ]` (the remaining core)
**Mechanism (validated):** the app-level callers already hold `app`, and
`app._ctx` exists, so an orchestration function can be flipped to take `ctx`
while its app-machinery callers pass `app._ctx` (which does **not** match the
`\bctx\.app\b` ratchet — it's the app handing over its own context, not a feature
reaching through) and builder callers pass `ctx`. Inside, the body migrates
`app._x` → `ctx.widgets.get`/`ctx.state`/`ctx.runtime`, leaving `ctx.app` only for
genuine god-object methods.
**Why it's the whole remaining job & not batchable headlessly:** the residual
~249 are dozens of these functions (`redraw_scatter_orchestrate` alone has ~15
`app.` touches to reclassify), each called from both builders and the runtime
(redraw_coordinator / plot_orchestrator / channel + selection controllers /
registry). Every conversion is a careful body-migration whose *only* failure mode
is a mis-classified `app._x` that still resolves via the stamp in tests but paints
the wrong thing at runtime — invisible to headless tests, visible only when a
human drives the UI. So: **one feature per PR, characterization test first,
interactive smoke after.** This is the god-object teardown proper.
- **Depends on:** T2 (runtime holders, done) so live objects have a `ctx` home.
- **Risk:** High — interactive-behaviour regressions that headless CI cannot catch.

**Pilot finding (distribution `_on_changed`, 249 → 246) — the sequencing is
LEAF-FIRST, not feature-first.** The function *bodies* use the bare `app`
parameter, which the ratchet does **not** count, so a conversion only nets down
at the *call sites*, and only if the body doesn't relocate calls into new
`ctx.app`. Measured on distribution:
  - `_on_changed` → `ctx`: **net −3** (5 builder wirings cleared; 1 residual —
    the handoff to the still-`app` renderer `redraw_distribution`).
  - `_on_tp_changed_dist` → `ctx`: **net ~0** — clearing 1 builder site is undone
    by `set_focus_tp(app)` and `app._split_active` relocating to `ctx.app`.
  - `refresh_distribution_timepoints` → `ctx`: **net ~+1** — `get_all_timepoints`
    / `current_focus_tp` relocate.
  So the per-feature handler conversions are near-zero until the **shared leaves
  they call take `ctx` first**: `core/timepoint_sync.py` (`set_focus_tp` /
  `current_focus_tp` — these convert *cleanly* to pure `ctx.state`/`ctx.bus`,
  zero residual), `data/data_loading.py` (`get_all_timepoints`), and the
  plot-render tree (`iter_plot_groups` / `_setup_axes` / plot-style /
  `_display_label_for`). Those leaves are **cross-feature** (a `timepoint_sync`
  conversion touches distribution *and* heatmap), so T3's real unit of work is
  "**convert a shared leaf across all its callers**", after which each feature's
  handlers + renderer fall out net-positive. Revised order:
  1. **Shared leaves** — `timepoint_sync` (cleanest, pure ctx.state/bus) →
     `data_loading.get_all_timepoints` → the plot-render helpers. Each is one
     coordinated PR across its callers (app-machinery callers bridge via
     `app._ctx`).
  2. **Feature handlers + renderers** — now net-positive; one feature per PR.
  3. **Shared controllers** (`plot_orchestrator` / channel / selection) last.

**Progress — shared-leaf pass 1 (246 → 245).** `timepoint_sync.set_focus_tp` /
`current_focus_tp` and `data_loading.get_all_timepoints` now take `ctx`. The
first two are **pure** (`ctx.state.plot` + `ctx.bus`, zero residual); the third
reads the well list from `ctx.state.dataset` with a single residual — the
per-well `_get_rows` row-fetch, which stays god-object-bound until a typed data
service exists (that is the next-deepest leaf, shared with `stats_compute` and
every `collect` module). Riding on the leaves, `refresh_distribution_timepoints`
(combo via `ctx.widgets`) and the multivariate timepoint combo dropped their
builder-site touches. The heatmap `_on_tp_changed` / `refresh_heatmap_timepoints`
handlers were **deliberately left on `app`**: converting them is net-positive
only once `_redraw` (plot-render tree) and `_split_active` also take `ctx`, so
they wait for pass 2. Confirms the finding: the render tree and `_get_rows` are
the true deep leaves gating the rest of T3.

**Progress — timepoint-handler modernization (245 → 244).** A new
`ctx.runtime.split` namespace gives the app-global split-view flag a ctx home
(`ctx.runtime.split.active` ↔ `app._split_active`; `split_view.py` still writes
it directly, the runtime read bridges). With that, the distribution and heatmap
timepoint handlers now read the modern infra end-to-end (combo/slider via
`ctx.widgets`, tp-value cache via `ctx.runtime.heatmap`, split via
`ctx.runtime.split`). `distribution._on_tp_changed_dist` → ctx is a clean −1;
the two heatmap handlers → ctx are net 0 (the onward `_redraw` render handoff is
the one residual, mirroring distribution `_on_changed`) but are otherwise fully
off the god-object. A latent bug from pass 1 was caught and fixed:
`features/registry.py`'s channel-change reactors still passed bare `app` to the
now-ctx refresh handlers, which their broad `except` would have swallowed —
locked by `test_timepoint_refresh_ctx_contract`.

#### T3 residuals ledger — modernized-except-for, for circle-back

Sites that were converted to `ctx` but still carry **one named god-object touch**
because a *deeper* leaf has no `ctx` home yet. Each stays here until its blocker
is cleared; clearing a blocker should zero every row that names it. `[ ]` = still
residual.

| # | Site | Residual touch | Blocker (what unblocks it) | Status |
|---|------|----------------|----------------------------|--------|
| R1 | `data/data_loading.py` `get_all_timepoints` | ~~`ctx.app._get_rows(label)`~~ → `get_rows(ctx, label)` | **DONE.** New `data_loading.get_rows(ctx, label)` is the ctx-native row-fetch service (lazy load + QC/Crop projection over `ctx.state`, feature projectors imported function-locally — the sanctioned data→feature escape hatch). The QC/Crop chain (`get_qc_params` / `apply_qc_to_cache` / `get_crop_region` / `apply_crop_to_cache`) is now ctx, pure typed state. `WellViewerApp._get_rows` delegates to `get_rows(self._ctx, …)`; `get_all_timepoints` reads `get_rows(ctx)` — residual gone. **Follow-on:** point `stats_compute` + the feature `collect` modules (which still call `app._get_rows`) at `get_rows(ctx)` in their own ctx passes. | `[x]` |
| R2 | `features/heatmap/tab_view.py` `refresh_heatmap_timepoints`, `_on_tp_changed` | `_redraw(ctx.app)` | **Heatmap render tree.** `redraw_heatmap` (≈200 lines, 44 app-touches) still takes `app`. High-risk one-feature pass: characterization test on `_heatmap_array`/`cell_well_index` first, **interactive smoke after** (headless CI can't see a mis-mapped attr that paints wrong). | `[ ]` |
| R3 | `features/distribution/tab_view.py` `_on_changed` | `redraw_distribution(ctx.app)` | **Distribution render tree.** Same shape as R2 — the feature renderer still takes `app`. Convert `redraw_distribution` (+ shared plot-render helpers `iter_plot_groups` / `_setup_axes` / plot-style / `_display_label_for`) to ctx. | `[ ]` |
| R4 | `features/qc/controller.py` `get_qc_params` | ~~`app._qc_params`~~ → `app._app_state.qc.params` | **DONE.** `_qc_params` moved off the god-object into a typed core slice `QcState` (`core/qc_state.py`, `AppState.qc`); `get_qc_params` / `set_qc_params` / persistence all read/write `app._app_state.qc.params`. The accessor now reads pure typed state, so its ctx signature-flip (`get_qc_params(ctx)` → `ctx.state.qc.params`) is a 3-line, zero-residual change that lands with R1. | `[x]` |
| R5 | `core/split_view.py` (≈4 write sites) | `app._split_active = …` (bare writes) | **Low priority / cosmetic.** Reads are modernized via `ctx.runtime.split` (R-home done). The direct writes are bare `app._split_active` (uncounted by the ratchet), harmless; move them onto `ctx.runtime.split.active` when `split_view` functions take `ctx`. | `[ ]` |

> When you clear a blocker, delete the residual touch, drop the ratchet, and tick
> the row(s). R4 → unblocks R1. R2/R3 are the render-tree passes (one feature
> each, smoke-tested). This ledger is the definitive circle-back list for the
> "modernized but for one deep leaf" sites.

#### T3 completion — approved method & leaf-first sequence

**Method (user-approved).** Render-tree conversions get a **numeric
characterization test first** (assert on the renderer's data outputs —
`_heatmap_array`, group value arrays, cell indices — so a mis-mapped attr that
resolves via the stamp but *paints wrong* is caught), then convert, then **flag
the feature "needs interactive smoke" before merge** (headless CI can't see
theme/label/visual regressions). One feature per pass, each green + committed.

**Remaining 243 → ~5, leaf-first (traced).** The feature `tab_view` counts fall
only after the shared render graph they call takes `ctx`. Order:

1. **Grouping/colour leaves** → `ctx` · **DONE** (commit e9e85bc). The pure logic
   moved *down* into `domain.selections_model` (`color_for_wells` /
   `color_for_selection` / `color_for_replicate_set` / `active_replicate_sets`);
   `sel_color` (core), `rank_color_rset` / `rank_color_wells` (grouping),
   `rep_sets_active` (barplot) delegate to it and take `ctx`. Locked by
   `test_iter_plot_groups_ctx`. (`get_all_fluor_gates` is NOT part of this —
   it bottoms out in cell_gating's loose persisted `_cell_gating_fluor_gate_edits`
   dict, i.e. a deep R4-style typed-state prerequisite; see step 3 residuals.)
2. **`iter_plot_groups(ctx)`** (`data_loading`) → `ctx` · **DONE** (e9e85bc):
   colour leaves + `get_rows(ctx)` (R1) + `ctx.state`, no feature import from the
   data layer (shared logic lifted to domain — keeps deferred-imports/layering
   guards green). Vestigial `_color_for_*` override hooks dropped.
2a. **Shared render leaves → `ctx`** (post-R6/R7 all pure state) · **DONE**:
   - `get_all_fluor_gates` / `get_fluor_gate` + the cell-gating gate accessors
     (`cell_gating_get_fluor_gate` / `_thresh_frac_on` / `_cell_gating_resolve_key`)
     — commit fe88f14 (unblocked by R6).
   - the export-style chain (`_ensure_export_style_prefs` / `_ensure_axis_prefs_store`
     / `figure_pref_key` [via `ctx.widgets`] / `figure_prefs` /
     `apply_export_style_to_current`) — commit 5c8d8a1 (unblocked by R7).

2b. **Label/threshold leaf tier → `ctx`** · **NEXT** (the newly-measured blocker).
   A render redraw still bottoms into these god-methods; until they take `ctx`,
   converting a renderer is **net-POSITIVE** (measured on distribution:
   `redraw_distribution(ctx)` would add ~6 `ctx.app` residuals — one each for the
   leaves below — while dropping only the single `_on_changed` builder site):
   - `active_channel_label` (→ its own sub-leaf `_ratio_label_for`),
   - `get_thresh_frac_on` (the `_get_thresh_frac_on` twin of the fluor accessor;
     careful — its "tab-not-built" guard falls back to `channel.threshold`, a
     *different* default from the slice's 50.0, so the guard's semantics must be
     preserved, not dropped),
   - `_well_display_label` and `replicate_display_label` (+ `_display_label_for`
     which composes them).
   Convert this tier (state + the already-ctx `rep_sets_active`) first; then the
   per-feature renderers finally convert **net-negative**.

3. **Per-feature renderer + handlers** (one pass each, characterization test on
   the data path + **paint** smoke flag — paint is the only genuinely
   headless-unverifiable part): distribution **R3** (`redraw_distribution` +
   `_setup_axes` / `_apply_card_style` / `_display_label_for` / `_render_*`) →
   heatmap **R2** (`redraw_heatmap`) → scatter (cells+agg) → barplot → lineplot →
   multivariate. (`plot_style.apply_ax_style` / `tokens_for` already take `ax` —
   no work there.)

   **Critical-path finding (measured on distribution).** Converting a render
   handler *before* these shared leaves is **net-POSITIVE on the ratchet**: e.g.
   `redraw_distribution(ctx)` drops the single `_on_changed` builder site (−1)
   but its body then names `ctx.app._get_all_fluor_gates()` **and**
   `ctx.app` for `apply_export_style_to_current` (+2) → net +1. So the render
   features must **not** go first. The real next leaves, in order:
   - **`get_all_fluor_gates` / `get_fluor_gate`** → `ctx`. **State prereq (R6)
     DONE**: the gate values moved off the QLineEdits into a typed
     `CellGatingState` core slice (`AppState.cell_gating`). The two
     `editingFinished` handlers sync edits → slice after their validate-all
     pass; persistence load writes the slice directly (and mirrors onto built
     edits); accessors / CDF axvline / saved-plots capture / the post-load
     worker kick all read the slice; new rows seed their edit text FROM the
     slice; the loose `_thresh_frac_on_saved` shadow dict is subsumed. Every
     text-changing path funnels through the handlers or the loader, so slice ↔
     widget cannot diverge (verified: zero `.text()` value-readers remain).
     Locked headless by `test_cell_gating_gate_state` — real offscreen
     QLineEdits covering sync, defaults, ratio-key resolution, handler fan-out,
     `editingFinished` wiring, and the garbage-input case (which now *keeps the
     last-committed value* instead of the old silent fall-to-default — a stray
     keystroke can no longer quietly disable a gate). Gate-value flow is data
     flow, not paint, so this is headless-verified; only a light UI sanity pass
     (type a value, see plots react) is suggested at next manual run — NOT the
     full smoke a render conversion needs. Remaining: the accessor signature
     flip (`get_all_fluor_gates(ctx)` etc.), now a pure-state 3-liner.
   - **`apply_export_style_to_current`** → `ctx`. **Storage prereq DONE** (commit
     dab9823): `_export_style_prefs` / `_export_axis_prefs` moved to a typed
     `ExportPrefsState` core slice (`AppState.export_prefs`). What remains is the
     signature conversion of the accessor chain (`_ensure_export_style_prefs` /
     `_ensure_axis_prefs_store` / `figure_pref_key` — the last resolves figs via
     `ctx.widgets`) and `apply_export_style_to_current` itself to take `ctx`; the
     accessors have many feature callers so it's a bounded signature pass, no
     longer blocked on god-object state.
   - **`_on_plot_channel_selected`** and the channel/axis handlers (overlaps T4).
   Only after these do the per-feature renderers convert **net-negative**. So the
   next concrete task is the **cell-gating fluor/thresh edits → typed state**
   migration (call it R6), mirroring R4.
4. **Non-render controller passes** (low risk, no plot tree): image_table (33),
   cell_gating (28, also A3 domain extract), batch_export, cell_browser,
   registry, saved_plots, review_csv, mv-picker, image_browser, playground.
5. **Follow-on:** point `stats_compute` + the feature `collect` modules at
   `get_rows(ctx)` (modernizes the last `app._get_rows` readers; no count move).

Legit residuals that stay: `core/app_runtime.py` (the runtime stamps `ctx.app`),
and the one shared-helper touch each in `ui_helpers` / `plot_card_actions`.

---

## Endgame — the 51, file by file (focused campaign)

> Measured `2026-07-04` on `claude/code-review-audit-framework-se53zd`
> (ratchet `CTX_APP_BUDGET = 51`). Command:
> `python -c "import re,pathlib;C=re.compile(r'\bctx\.app\b');print(sum(len(C.findall(p.read_text())) for p in pathlib.Path('well_viewer').rglob('*.py')))"`

Every remaining `ctx.app` reference, classified **FLOOR** (structural — only T6
dissolves it) or **CONVERT** (this campaign burns it down), with the technique.

| refs | File | Class | Note / technique |
|-----:|------|-------|------------------|
| 3 | `core/app_runtime.py` | **FLOOR** | The stamp back-end (`_FeatureRuntime(app, …)`). Gone when the `app` field is deleted at T6. |
| 2 | `persistence/view_state.py` | **FLOOR** | Sanctioned single `app = ctx.app` unwrap ×2 fns (`snapshot`/`restore`). Persistence MAY unwrap. |
| 2 | `persistence/cell_gating.py` | **FLOOR** | Sanctioned unwrap (`save`/`load_from_pipeline_info`). |
| 2 | `persistence/_doc.py` | **FLOOR** | `get_section`/`set_section` unwrap (the doc-store leaf). |
| 1 | `persistence/sample_definitions.py` | **FLOOR** | Sanctioned unwrap. |
| 1 | `persistence/heatmap_layouts.py` | **FLOOR** | Sanctioned unwrap. |
| 1 | `core/ui_helpers.py` | **FLOOR** | Documented shared-helper touch. |
| 1 | `core/plot_card_actions.py` | **FLOOR** | Documented shared-helper touch. |
| 5 | `features/registry.py` | **CONVERT** | 4 sub-builder handoffs (`build_stats_tab(ctx.app)`, `build_stats_group_editor`, `_build_review_csv_tab`, `build_sample_definitions_centre`) + 1. **Batch F1** — thread `ctx` into the four sub-builders (each currently takes `app`). |
| 5 | `core/channel_state_controller.py` | **CONVERT** | 5 `app = ctx.app` unwraps in the channel/axis handlers. **Batch F4** — the controller cluster; overlaps T4 (`channel_changed` bus route). |
| 3 | `features/image_browser/cell_browser/controller.py` | **CONVERT** | **Batch F2** (running worker `aea67a3`). |
| 3 | `features/grouping/cell_gating_tab_view.py` | **CONVERT** | Post-R6 tail; **Batch F3**. |
| 2 | `features/smfish/tab_view.py` | **CONVERT** | `recalculate_threshold(ctx.app)` + review-csv bridge; **Batch F5**. |
| 2 | `features/image_browser/tab_view.py` | **CONVERT** | **Batch F2** (image_browser cluster). |
| 2 | `features/image_browser/review_image/controller.py` | **CONVERT** | **Batch F2** (running worker `aea67a3`). |
| 2 | `features/heatmap/controller.py` | **CONVERT** | Render-tree tail (post-R2); **Batch F5**. |
| 2 | `features/batch_export/tab_view.py` | **CONVERT** | **Batch F5**. |
| 2 | `domain/stats_compute.py` | **CONVERT** | `getattr(ctx.app, "_aggregate_well"/"_aggregate_group")` delegate hooks → thread the aggregators through `ctx`. **Batch F4** (enabler). |
| 2 | `core/tab_controller.py` | **CONVERT** | 2 `app = ctx.app` unwraps; **Batch F4** (controller layer). |
| 2 | `core/plot_orchestrator.py` | **CONVERT** | 1 comment + `app = ctx.app` in `redraw_app`; **Batch F4**. |
| 1 | `features/scatter/callbacks.py` | **CONVERT** | **Batch F5**. |
| 1 | `features/saved_plots/controller.py` | **CONVERT** | Post-T5 tail; **Batch F5**. |
| 1 | `features/lineplot/tab_view.py` | **CONVERT** | **Batch F5**. |
| 1 | `features/image_browser/image_table/picker_view.py` | **CONVERT** | **Batch F2**. |
| 1 | `features/heatmap/tab_view.py` | **CONVERT** | **Batch F5**. |
| 1 | `domain/fold_change_controls.py` | **CONVERT** | `app = ctx.app`; **Batch F4** enabler. |
| **51** | | | **~13 FLOOR + ~38 CONVERT** |

### Campaign batches (parallelizable; one PR-shaped commit each)

The CONVERT residue clusters into five independent batches. Each is a
characterization-test-first, one-cluster conversion; the shared-leaf work that
gated them (R1/R2/R3/R4/R6/R7, colour + label + export-style leaves) is **done**,
so these now convert **net-negative**.

- **F1 · registry sub-builders (5).** Thread `ctx` into `build_stats_tab`,
  `build_stats_group_editor`, `_build_review_csv_tab`, `build_sample_definitions_centre`
  so `registry.py` stops passing `ctx.app`. Bounded signature pass; low risk.
- **F2 · image_browser cluster (8):** `cell_browser/controller` (3),
  `image_browser/tab_view` (2), `review_image/controller` (2),
  `image_table/picker_view` (1). The running worker `aea67a3` targets the
  cell_browser + review_image half — integrate its net-positive output, then
  finish tab_view + picker_view.
- **F3 · cell_gating tail (3).** `cell_gating_tab_view` post-R6 residue —
  the last handler touches now that gate values are typed state.
- **F4 · controller layer + aggregators (12):** `channel_state_controller` (5),
  `tab_controller` (2), `plot_orchestrator` (2), `stats_compute` aggregator hooks
  (2), `fold_change_controls` (1). **Overlaps T4** — thread `ctx` through the
  channel/axis handlers and the `_aggregate_well/_aggregate_group` delegates;
  the channel handlers fold into the `channel_changed` bus route. Highest-value
  cluster (unpins category b's controller tail). Do the `stats_compute` +
  `fold_change` aggregator enablers first, then the three core controllers.
- **F5 · feature render/tab tails (9):** `smfish/tab_view` (2),
  `heatmap/controller` (2), `batch_export/tab_view` (2), `scatter/callbacks` (1),
  `saved_plots/controller` (1), `lineplot/tab_view` (1), `heatmap/tab_view` (1).
  Independent one-liners riding on the now-`ctx` render tree; sweep in one pass.

After F1–F5 land, the count is at the **~13 FLOOR**, and **T6** takes it to 0.

---

### T4 — Extract cross-cutting services · `[~]`
Category (c). `_invalidate_stats_cache` operates on the already-typed
`stats_cache` slice → replace the ~20 scattered calls with a
`bus.plot_options_changed`/dedicated invalidation event, or a small `StatsCache`
collaborator held on `ctx`. `_refresh_*` become feature controllers reacting to
bus events. `_on_plot_channel_selected` / axis handlers fold into the
`channel_changed` signal.
- **Depends on:** T1 for the event route.

### T5 — Saved-plots / capture decomposition · `[x]` LARGELY DONE
The `wire_save_to_gallery` → `save_plot_from_card` → `get_store` / `capture_params`
/ `build_summary` seam is now `ctx`-native (persistence + stats capture take
`ctx`; commit `226d0af`). A single `saved_plots/controller.py` residual remains
(Batch F5 tail). The dedicated `SavedPlotsService` object is a future refactor,
not required to reach 0.

### T6 — Retire `ctx.app` · `[ ]` THE CAPSTONE
Once F1–F5 land the count at the ~13 **FLOOR**, delete the `app` field from
`ViewerContext` and dissolve the floor in one coordinated change:

1. **Persistence unwraps (8).** Replace each `app = ctx.app` in `persistence/`
   with direct `ctx.persistence` / typed-state access, or pass the concrete
   `data_dir` + slice the caller already holds. These modules never needed the
   whole app — only `app._app_state.dataset` + the persistence doc, both on `ctx`.
2. **`AppRuntime` stamp (3).** `_FeatureRuntime` holds `app` only to `setattr`
   the legacy `app._<prefix><name>` mirror. When every reader has migrated onto
   `ctx.runtime.<feature>.<name>` (a mechanical sweep the runtime holders already
   enable), the mirror write is dropped and the holder stores its own dict — no
   `app` reference.
3. **Shared-helper touches (2).** `ui_helpers` / `plot_card_actions` — inline the
   one window-service call each through a named `ViewerContext` accessor.
4. **Delete `ViewerContext.app`**, flip the ratchet from `<= N` to `== 0` (hard
   ban), and remove the strangler docstring. The frozen named-accessor set is the
   *entire* remaining, intentional surface between features and the window shell.

**Order within T6:** step 2 (runtime reader sweep) is independent and can run in
parallel with F1–F5; steps 1+3+4 are the final atomic commit that removes the
field. Everything must be green on the fast lane; the closeEvent/teardown tests
(`test_app_runtime.py`, layering guards) gate the merge.

---

## Progress

| ID | Workstream | Depends on | Risk | Status | Metric |
|----|------------|-----------|------|--------|--------|
| T1 | Wire the EventBus | — | Med | `[x]` | in place (state→redraw paths); per-control redraws are T3, not bus |
| T2 | Feature runtime holders | — | Low | `[x]` | 293 → 249 (all pure-runtime writes; persisted fields excluded) |
| T3 | Thread ctx through controllers | T2 | High | `[~]` | 249 → 51 (render tree + shared leaves R1–R7 done; F1–F5 tail remains) |
| T4 | Extract cross-cutting services | T1 | Med | `[~]` | folded into Batch F4 (channel/axis handlers + aggregators) |
| T5 | Saved-plots service | — | Med | `[x]` | capture/persistence ctx-native (`226d0af`); 1 controller tail (F5) |
| T6 | Retire `ctx.app` | F1–F5 | Low | `[ ]` | ~13 floor → 0 (delete field) |

**Focused campaign (51 → 0):** run **F1–F5 in parallel** (all shared leaves
that gated them are done, so each converts net-negative), plus the independent
**T6 step-2 runtime-reader sweep**; then the final **T6 atomic commit** deletes
`ViewerContext.app` and flips the ratchet to `== 0`. See
[§ Endgame](#endgame--the-51-file-by-file-focused-campaign).

## Ratchet evolution

- `test_ctx_app_burndown.py` counted down 293 → **51** (`CTX_APP_BUDGET = 51`);
  budget is monotone-decreasing and drops with every F-batch commit.
- **Guards in place:** `test_guardrails.py::test_tab_ctx_app_unwrap_budget == 0`
  (bans `app = ctx.app` in `features/**`), `test_deferred_imports.py` (caps
  cross-feature function-local imports for deletability), `test_app_runtime.py`
  (stamp write-through + `persistence/` never imports `app_runtime`),
  `test_monolith_budgets.py` (per-file line ceilings).
- **At T6:** delete `ViewerContext.app`, flip the ratchet from `<= N` to `== 0`
  (hard ban), and the count is pinned at 0.

## Risks & mitigations

- **Regression on a headlessly-tested desktop app** → one feature per PR;
  characterization tests before each T3 conversion; keep `app._ctx` as the
  transitional bridge only at *unconverted* call sites.
- **EventBus becomes a new god-object** → the five-signal cap is enforced by test;
  new signals require the same review bar as new named accessors.
- **Signal/slot leaks** → an auto-disconnecting subscription helper; teardown test.

## Non-goals

- No UI or behavioural change — purely structural decoupling.
- Not a rewrite of `WellViewerApp`'s *logic*, only its *ownership*: state → slices,
  live objects → runtime holders, cross-cutting ops → services/events, window
  services → named accessors. What's left on the app is the genuine window shell.

---

**Last updated:** 2026-07-04 — refreshed to the **51** state; added the
file-by-file Endgame map + the F1–F5 focused campaign and the concrete T6
floor-dissolution plan.
