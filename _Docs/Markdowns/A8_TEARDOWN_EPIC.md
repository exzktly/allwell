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

## Endgame — the residual register (45) + the plan to zero

> Measured `2026-07-04` on `claude/code-review-audit-framework-se53zd` @ `3c7543e`
> (ratchet `CTX_APP_BUDGET = 45`). Command:
> `python -c "import re,pathlib;C=re.compile(r'\bctx\.app\b');print(sum(len(C.findall(p.read_text())) for p in pathlib.Path('well_viewer').rglob('*.py')))"`

**What the parallel campaign found.** A four-worker parallel sweep (batches
F1/F2/F4/F5) drove **51 → 45** and then hit a hard structural wall. The workers
converged on one conclusion: **the remaining refs cannot be cleared by
cluster-local sweeps.** Every leftover `ctx.app` is one of —
(1) a **FLOOR** ref (the runtime stamp / a sanctioned persistence unwrap / a
documented shared-helper touch) that *only deleting the field* (T6) dissolves, or
(2) a **bridge into an app-typed callee** — a god-method, an *unregistered*
widget handle, a non-ctx viewer/dialog constructor, or a cross-feature shared
toggle — where naively threading `ctx` would **add** `ctx.app` (F1 and F5 both
measured net-positive conversions and correctly refused them). So the path to 0
is no longer "sweep the tails"; it is a small set of **enabler passes** (E1–E5
below) that make each callee ctx-reachable, followed by **T6**.

### Residual register — every one of the 45, with its blocker

| refs | File:line | Class | Blocker → enabler |
|-----:|-----------|-------|-------------------|
| 3 | `core/app_runtime.py` | **FLOOR** | The stamp back-end (`_FeatureRuntime(app, …)`). Dissolved by **T6** (delete field). |
| 2 | `persistence/_doc.py:120,126` | **FLOOR** | `get_section`/`set_section` sanctioned unwrap. **T6** → read `ctx.state.dataset` + `ctx.persistence` directly. |
| 2 | `persistence/view_state.py:95,421` | **FLOOR** | `snapshot`/`restore` sanctioned unwrap. **T6**. |
| 2 | `persistence/cell_gating.py:20,64` | **FLOOR** | `save`/`load_from_pipeline_info` unwrap. **T6**. |
| 1 | `persistence/sample_definitions.py:219` | **FLOOR** | `_apply_unified_state(ctx.app,…)` unwrap. **T6**. |
| 1 | `persistence/heatmap_layouts.py:80` | **FLOOR** | Sanctioned unwrap. **T6**. |
| 1 | `core/ui_helpers.py:563` | **FLOOR** | Documented shared-helper touch. **T6**. |
| 1 | `core/plot_card_actions.py:5` | **FLOOR** | Doc-comment mention (regex counts comments). Free −1 via the "no literal token in comments" reword; else **T6**. |
| 1 | `features/lineplot/tab_view.py:81` | **CONVERT** | `getattr(ctx.app,"_plotting_metric_cb")` — the global metric combo is stashed raw in `core.centre_view`, never `register()`-d → **E1** (register it; read via `ctx.widgets`). |
| 1 | `features/heatmap/controller.py:935` | **CONVERT** | `refresh_heatmap_layout_sidebar` reads unregistered `_heatmap_sidebar_table/_rows_spin/_cols_spin` → **E1**. |
| 1 | `features/heatmap/tab_view.py:404` | **CONVERT** | same callee as above → **E1**. |
| 1 | `features/scatter/callbacks.py:74` | **CONVERT** | `ctx.app._open_scatter_cell_viewer(…)` — `ScatterCellViewer(dock, app, …)` is not ctx-native → **E2**. |
| 2 | `features/batch_export/tab_view.py:174,188` | **CONVERT** | `_build_spread_row`/`_show_batch_mode` need `toggle_sem`/`toggle_fov_replicates`/`refresh_fov_btn_state`, which live in lineplot/barplot → calling them is a **banned cross-feature import** → **E3** (relocate those toggles to a shared non-feature home). |
| 1 | `features/heatmap/controller.py:708` | **CONVERT** | `set_selected_wells(ctx.app,…)` body chains god-methods `on_plate_sel_change`/`_refresh_sidebar_map` → **E4**. |
| 1 | `features/saved_plots/controller.py:110` | **CONVERT** | `ctx.app._rebuild_all()` — `rebuild_all(app)` body is all god-methods → **E4**. |
| 1 | `features/smfish/tab_view.py:832` | **CONVERT** | `recalculate_threshold(ctx.app)` — body saturated with `update_channel_selector`/`_load_gating_from_pipeline_info`/`on_channel_changed` → **E4**. |
| 1 | `features/smfish/tab_view.py:846` | **CONVERT** | `getattr(ctx.app,"_refresh_review_csv")` — must stay app-bridge (deferred-imports budget) → **E4/E7** (needs the review-csv service, below). |
| 3 | `features/image_browser/review_image/controller.py:268,703,857` | **CONVERT** | review-csv row impls + `zoom_review_image_to_selected_nucleus` walk the app-pinned collect store + runtime_app-bound pixmap services → **E4** (migrate the collect store + pixmap services to ctx). |
| 1 | `features/image_browser/review_image/renderer.py:191` | **CONVERT** | `refresh_review_image(ctx.app)` — same app-pinned services → **E4**. |
| 2 | `features/image_browser/tab_view.py:51,56` | **CONVERT** | `on_image_browser_subtab_changed`/`build_image_browser_subtab` are app-typed → **E4**. |
| 1 | `features/image_browser/image_table/picker_view.py:96` | **CONVERT** | app-typed sub-panel builder → **E4**. |
| 4 | `features/grouping/cell_gating_tab_view.py:185,287,1118,1771` | **CONVERT** | `apply_review_overrides_to_cache`/`build_qc_gate_controls`/`_refresh_thresh_frac_quick_edit`/`_all_member_labels` — mixed god-methods + app-typed builders → **E4** (+A3 domain extract). |
| 3 | `features/registry.py:94,104,111` | **CONVERT** | `build_stats_group_editor`/`_build_review_csv_tab`/`build_sample_definitions_centre` — their bodies drive ~11–13 app-taking stats controllers/god-methods (converting them ADDS `ctx.app`) → **E5** (migrate the stats-editor + sample-def controller chains to ctx first). |
| 1 | `features/stats/tab_view.py:32` | **CONVERT** | `build_stats_results_panel(ctx.app,…)` — same stats-controller chain → **E5**. |
| 4 | `core/channel_state_controller.py:269,658,722,1002` | **CONVERT** | single core unwraps in `refresh_metric_combo_for_channel`/`refresh_heatmap_aggregation_options`/`on_plot_channel_selected`/`on_metric_selected`; each drives a god-method (`_active_channel_label`/`set_active_channel`/`sender()`) or the unregistered `_stats_channel_cb` → **E6** (partly cleared by E1's registration; rest fold into the `channel_changed` bus route / T4). |
| 2 | `core/tab_controller.py:218,241` | **CONVERT** | `current_centre_tab`/`visible_centre_tabs` pass `app` to installed `split_focus`/`visible_provider` callables + walk `_notebook` → **E6**. |
| 1 | `domain/fold_change_controls.py:328` | **CONVERT** | `install_fold_change_controls` stashes per-tab combos on the app + funnels through `set_fold_change_state(app,…)` → **E6**. |
| 1 | `core/plot_orchestrator.py:194` | **CONVERT** | `redraw_app` drives figure/notebook widgets + `redraw(app,…)` → **E6** (or stays as one boundary unwrap until T6). |
| **45** | | | **13 FLOOR + 32 CONVERT** |

### The plan to zero — enabler passes E1–E5, then T6

Ordered by leverage (each pass makes a callee ctx-reachable so the bridge sites
above convert **net-negative**). These are **coordinated cross-file passes, not
parallel cluster sweeps** — that is the lesson of the F1/F2/F4/F5 wave. Each is
one PR, characterization-test-first, green on the fast lane.

1. **E1 — Register the orphan widgets (≈4 refs, lowest risk, do first).**
   Three widget handles are stashed raw (`setattr(app, …)`) instead of through
   `WidgetRegistry`, so `ctx.widgets` can't resolve them: the global
   `_plotting_metric_cb` (`core.centre_view`), the heatmap-sidebar trio
   (`_heatmap_sidebar_table`/`_rows_spin`/`_cols_spin`), and `_stats_channel_cb`.
   `register()` each at its build site (it still app-stamps, so existing readers
   are unchanged), then flip the four reader sites to `ctx.widgets.get`. Clears
   lineplot:81, heatmap/controller:935, heatmap/tab_view:404, and one
   channel_state_controller unwrap.
2. **E2 — Make `ScatterCellViewer` / `open_scatter_cell_viewer` ctx-native (1).**
   Change the constructor + opener to take `ctx` (dock + state via `ctx`), drop
   the `_open_scatter_cell_viewer` god-method. Clears scatter/callbacks:74.
3. **E3 — Relocate the shared plot toggles to a non-feature home (2).**
   `toggle_sem` / `toggle_fov_replicates` / `refresh_fov_btn_state` live in
   lineplot/barplot; move them to a shared module (e.g. `core/plot_toggles.py` or
   the already-shared `features/preview` layer) so batch_export can call them
   without a cross-feature import. Clears batch_export:174,188.
4. **E4 — Migrate the app-pinned service chains to ctx (≈13, the bulk).**
   The deep one. Four sub-chains, each its own PR:
   - **Review-image services** — the collect store (`review_collect_timepoints`
     …) + pixmap services (`draw_review_image`/`render_review_image_display`),
     currently `runtime_app`-bound, take `ctx`. Clears review_image controller:268/703/857 + renderer:191 + smfish:846 (shared review-csv service, **E7**).
   - **Heatmap plate-selection chain** — `set_selected_wells` →
     `on_plate_sel_change`/`_refresh_sidebar_map` to ctx. Clears heatmap/controller:708.
   - **Image-browser sub-tab chain** — `on_image_browser_subtab_changed` /
     `build_image_browser_subtab` / the image-table picker builder to ctx. Clears
     image_browser/tab_view:51,56 + image_table/picker_view:96.
   - **cell_gating tail + saved_plots/smfish god-methods** — `apply_review_overrides_to_cache`,
     `build_qc_gate_controls`, `_refresh_thresh_frac_quick_edit`, `_all_member_labels`,
     `rebuild_all`, `recalculate_threshold`. Overlaps **A3** (extract cell_gating
     domain compute out of the view). Clears cell_gating ×4, saved_plots:110, smfish:832.
5. **E5 — Migrate the stats-editor + sample-def controller chains (4).**
   Thread `ctx` through the stats group-editor controllers (`stats_refresh_map`/
   `stats_grp_add`/`stats_apply_drag`/`stats_active_group`…) and the
   sample-definitions sub-builders, then flip `build_stats_group_editor` /
   `build_sample_definitions_centre` / `build_stats_results_panel` /
   `_build_review_csv_tab` to `ctx`. Clears registry ×3 + stats/tab_view:32.
6. **E6 — Fold the core controller residuals into the bus / accept as boundary.**
   `channel_state_controller` (post-E1) / `tab_controller` / `fold_change_controls`
   / `plot_orchestrator` — the channel/axis handlers fold into the
   `channel_changed` route (**T4**); the rest are single core-boundary unwraps
   that can remain until T6 flips the ratchet (they are `core/`, where the unwrap
   is sanctioned). ≈8 refs.
7. **T6 — Delete `ViewerContext.app`, dissolve the FLOOR (13), pin ratchet == 0.**
   The forcing function: with the field gone, the persistence unwraps *must*
   become `ctx.state`/`ctx.persistence`, the `AppRuntime` stamp is dropped (after
   a mechanical `app._<prefix><name>` → `ctx.runtime.<f>.<name>` reader sweep,
   which can run in parallel with E1–E6), and the two shared-helper touches route
   through named accessors. Then flip `assert total <= N` to `== 0`.

**Net effect:** E1→E5 clear ~28 CONVERT refs; E6 clears/parks ~8; T6 removes the
13 FLOOR and the last handful → **0**. E1–E3 are quick wins (~7 refs, low risk);
E4 is the multi-PR bulk; E5 is bounded; T6 is the capstone. Suggested order:
**E1 ∥ E2 ∥ E3 (parallel, independent) → E4 (serial sub-chains) → E5 → E6 → T6.**

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
| T3 | Thread ctx through controllers | T2 | High | `[~]` | 249 → 45 (render tree + shared leaves R1–R7 + F1/F2/F4 done) |
| T4 | Extract cross-cutting services | T1 | Med | `[~]` | folded into enabler E6 (channel/axis handlers → `channel_changed`) |
| T5 | Saved-plots service | — | Med | `[x]` | capture/persistence ctx-native (`226d0af`); 1 controller tail (E4) |
| T6 | Retire `ctx.app` | E1–E6 | Low | `[ ]` | 13 floor → 0 (delete field) |

**Campaign status (51 → 45).** The parallel four-worker sweep (F1/F2/F4/F5) drove
51 → 45 and revealed the wall: the remaining refs are **FLOOR** (T6-only) or
**bridges into app-typed callees** where naive conversion adds `ctx.app`. The
path to 0 is now the **enabler passes E1–E5 → E6 → T6** — coordinated cross-file
passes, not cluster sweeps. Full residual register + the E1–T6 plan in
[§ Endgame](#endgame--the-residual-register-45--the-plan-to-zero).

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

**Last updated:** 2026-07-04 — parallel campaign drove **51 → 45** (F1/F2/F4
integrated; F5 net-0, blocked). Replaced the F-batch plan with the definitive
**45-ref residual register** and the **enabler-pass plan (E1–E5 → E6 → T6)** the
workers' findings proved is required to reach 0.
