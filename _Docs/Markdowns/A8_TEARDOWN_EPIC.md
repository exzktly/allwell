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

## Endgame — the enabler campaign (51 → 26) + the irreducible floor

> Measured `2026-07-04` on `claude/code-review-audit-framework-se53zd` @ `c1561e7`
> (ratchet `CTX_APP_BUDGET = 26`). Command:
> `python -c "import re,pathlib;C=re.compile(r'\bctx\.app\b');print(sum(len(C.findall(p.read_text())) for p in pathlib.Path('well_viewer').rglob('*.py')))"`

**The enabler campaign happened and worked.** Passes **E1–E6** (all `[x]`, below)
drove **51 → 26**. What they proved is the shape of the remaining wall: the
campaign's enablers (register orphan widgets, ctx-native `ScatterCellViewer`,
relocate shared toggles, migrate the review-image / heatmap-plate-sel /
image-browser / cell-gating / stats-editor service chains, the `_doc` typed-state
conversion) cleared every ref that *could* fall without touching feature
behaviour. The **26 that remain are the irreducible floor**: each is a single
`app = ctx.app` boundary unwrap that passes `app` **into a behaviour call** — a
god-method — that has no `ctx` form yet. Converting any one of them piecemeal
**net-adds** `ctx.app` (measured repeatedly). The only way past 26 is to
**decompose the underlying god-methods** (roadmap below), or **T6** (delete the
field, which forces the same decomposition).

### The final floor register — the 14 that remain (measured `2026-07-04` @ `aff9c6f`)

Every remaining ref is a **sanctioned single-unwrap core seam** bundling
irreducible behaviour, a **persistence widget-bank unwrap**, or a **feature
bridge into a hook-contract god-method**. Each is tagged with the epic that
clears it. None falls to a piecemeal conversion (all measured net-0/net-add).

| refs | Site(s) | Pinned by / epic |
|-----:|---------|------------------|
| 1 | `core/channel_state_controller.py:64` | `recalculate_threshold` seam — `app._get_rows` + the `_features` `on_channel_changed` hook (bundled, D-thresh). Clears with **feature-registry ctx-home**. |
| 1 | `core/channel_state_controller.py:949` | `set_thresh_frac_on` seam — lazy-tab `_centre_build_pending` + loose `_cell_gating_thresh_frac_edits`. Clears with **cell-gating edits-dict → typed state**. |
| 1 | `core/selection_controller.py:828` | `set_selected_wells` → `_refresh_sidebar_map`/`on_plate_sel_change` bottom into `_is_single_well_tab`(`app._features`). **feature-registry ctx-home.** |
| 4 | `core/tab_controller.py:138,164,290,313` | The D5/D6 image-browser + sample-def bundled core seams + split-focus/visible providers (take `app` by installed-callable contract). Clear as their god-service trees convert. |
| 1 | `domain/fold_change_controls.py:331` | `install_fold_change_controls` → `_ControlCombo`/`_repopulate_*` take `app`. Own conversion pass. |
| 1 | `features/batch_export/tab_view.py:194` | `_show_batch_mode` → the 3 batch panels take `app` first-positional. **A14 batch-panel decomposition.** |
| 1 | `features/saved_plots/controller.py:110` | `_rebuild_all` → `desc.on_selection_changed(app)` hook contract. **feature-registry ctx-home.** |
| 2 | `persistence/cell_gating.py:20,64` | `save/load_from_pipeline_info` — loose `_cell_gating_*` widgets + `_scatter_gates` + `_centre_build_pending`. **cell-gating → typed state.** |
| 2 | `persistence/view_state.py:95,421` | `snapshot`/`restore` — broad app-widget surface (`_current_leaf_tab`/`_snapshot_tabs`/`_set_selected_wells`). Sanctioned persistence unwrap; **T6**. |
| **14** | | **3 epics + T6** |

**Three epics unlock the rest, then T6 → 0:** (1) **feature-registry ctx-home** —
give `app._features` a reviewed `ctx` accessor (clears channel:64, selection:828,
saved_plots:110 + the tab_controller providers); (2) **A14 batch-panel
decomposition** (clears batch_export:194); (3) **cell-gating edits-dict → typed
`CellGatingState`** (clears channel:949 + persistence/cell_gating ×2). The
view_state pair + `AppRuntime` stamp are the final **T6** deletions. Each epic is
interactive-smoke-gated; none should be forced with a count-gaming accessor.

### The enabler passes — DONE (51 → 26)

| Pass | What it did | Δ |
|------|-------------|---:|
| **E1** `[x]` | Registered the orphan widgets (`_plotting_metric_cb`, heatmap-sidebar trio, `_stats_channel_cb`) into `WidgetRegistry`; readers use `ctx.widgets`. | −3 |
| **E2** `[x]` | `ScatterCellViewer` + `open_scatter_cell_viewer` take `ctx` (dock via `ctx.widgets`, live viewer via `ctx.runtime.scatter`). | −1 |
| **E3** `[x]` | Relocated `toggle_sem`/`toggle_fov_replicates`/`refresh_fov_btn_state` to `core/plot_toggles.py` (also −3 deferred-import budget). | −1 |
| **E4** `[x]` | Migrated the app-pinned service chains: review-image zoom + CSV-rows (`ctx.runtime.review_image`/`review`), heatmap plate-sel + Plot-Groups drawer (`ctx.runtime.plot_groups`), QC gate chain, `_all_member_labels`. | −8 |
| **E5** `[x]` | Threaded the stats group-editor + results-panel controller chain onto `ctx` (`ctx.runtime.stats`); flipped `build_stats_group_editor`/`build_stats_results_panel`. | (in E4-batch) |
| **E6** `[x]` | `refresh_heatmap_aggregation_options` ctx-native (registered metric combo + `ctx.state`); plus doc-comment token rewords and the `_doc` persistence leaf → typed `AppState` (`read`/`write`/`path_for`/`delete`/`get_section`/`set_section` take `ctx.state`, not `ctx.app`). | −7 |

### The irreducible floor — the 26, each with why it can't fall alone

Every one is a *sanctioned* boundary unwrap (`core/`/`domain/`/`persistence/` may
unwrap once) or a `features/**` bridge, each passing `app` into a **behaviour**
god-method. The god-method's name is the unit of work to clear it.

**Core / domain boundary unwraps (9) — drive behaviour:**
| File:line | God-method it feeds |
|-----------|---------------------|
| `core/channel_state_controller.py:269` | `app._active_channel_label()` |
| `core/channel_state_controller.py:725` | `app.sender()` + `set_active_channel` |
| `core/channel_state_controller.py:1005` | `set_active_metric(app,…)` |
| `core/tab_controller.py:218,241` | installed split-focus / visible providers take `app` |
| `core/plot_orchestrator.py:194` | `redraw(app,…)` + `refresh_plot_empty_states(app)` |
| `core/selection_controller.py:828` | `app._refresh_sidebar_map()` + `on_plate_sel_change(app)` |
| `core/ui_helpers.py:563` | wires `app._toggle_sem`/`_toggle_fov_replicates` + button banks |
| `domain/fold_change_controls.py:331` | `_ControlCombo`/`_repopulate_*` take `app` |

**Persistence widget/state unwraps (6) — sanctioned in `persistence/`:**
| File:line | Why |
|-----------|-----|
| `persistence/heatmap_layouts.py:80` | writes loose `app._heatmap_*` stamps + reads `_heatmap_persisted_settings` (net-0 to register combos) |
| `persistence/view_state.py:95` | `_current_leaf_tab`/`_snapshot_tabs` read app-stashed widgets |
| `persistence/view_state.py:421` | `app._set_selected_wells` behaviour + `_view_state_absent_wells` stash |
| `persistence/cell_gating.py:20,64` | `app._get_all_fluor_gates()` + many loose `_cell_gating_*` attrs |
| `persistence/sample_definitions.py:219` | `_apply_unified_state(app,…)` needs `app._set_status` |

**Feature → god-method bridges (11) — `features/**` may not unwrap; convert net-adds:**
| File:line | God-method |
|-----------|-----------|
| `features/smfish/tab_view.py:832,846` | `recalculate_threshold` / `_refresh_review_csv` |
| `features/registry.py:103,110` | `_build_review_csv_tab` / `build_sample_definitions_centre` |
| `features/image_browser/tab_view.py:51,56` | `on_image_browser_subtab_changed` / `build_image_browser_subtab` |
| `features/image_browser/review_image/renderer.py:191` | `refresh_review_image` |
| `features/image_browser/review_image/controller.py:269` | review-csv double-click |
| `features/saved_plots/controller.py:110` | `_rebuild_all` |
| `features/grouping/cell_gating_tab_view.py:1118` | `_refresh_thresh_frac_quick_edit` |
| `features/batch_export/tab_view.py:194` | `_show_batch_mode` (constructs app-typed batch panels) |

### The plan to zero — god-method decomposition (D-series) → T6

The 26 are pinned by ~15 distinct god-methods. Clearing each is a **behaviour
extraction**, not a signature flip — and per the user-approved method, each is
**one PR, characterization-test-first, flagged "needs interactive smoke" before
merge** (headless CI cannot see a mis-mapped attr that resolves via the stamp but
paints wrong). This is the "god-object teardown proper", higher-risk than the
enabler passes. Grouped by cohesion:

1. **D1 — channel/metric behaviour** (`_active_channel_label`, `set_active_channel`,
   `set_active_metric`, `sender()` routing) → `ctx`/`channel_changed` bus. Clears
   `channel_state_controller` ×3. Overlaps **T4**.
2. **D2 — plate-selection + sidebar** (`_refresh_sidebar_map`, `on_plate_sel_change`)
   → ctx. Clears `selection_controller:828` (+ heatmap already on the boundary).
3. **D3 — redraw orchestrator** (`redraw(app)`, `refresh_plot_empty_states`,
   `refresh_fov_btn_state` banks) → the render tree is already largely ctx; finish
   `plot_orchestrator:194` + `ui_helpers:563`.
4. **D4 — review-image draw web** (`refresh_review_image`/`draw_review_image` +
   the review-csv double-click + `recalculate_threshold`/`_refresh_review_csv`
   shared service). Clears smfish ×2, review_image renderer+controller. The
   deepest single cluster.
5. **D5 — image-browser sub-tab tree** (`build_image_browser_subtab`,
   `on_image_browser_subtab_changed`, `build_review_image_panel`) → ctx. Clears
   image_browser/tab_view ×2.
6. **D6 — sample-def + review-csv builders** (`build_sample_definitions_centre`,
   `_build_review_csv_tab` + the sample-def sub-builder tree) → ctx. Clears
   registry ×2, and unblocks the sample_definitions + cell_gating persistence
   unwraps that call `_apply_unified_state`.
7. **D7 — saved-plots gallery rebuild** (`_rebuild_all` → `SavedPlotsService`, T5).
   Clears saved_plots:110.
8. **D8 — cell-gating quick-edit + fold-change combos** (`_refresh_thresh_frac_quick_edit`,
   the `_ControlCombo` fold-change stash). Clears cell_gating:1118, fold_change:331.
9. **D9 — batch-export panels** (`_show_batch_mode` → panels take `ctx`, part of the
   `batch_export/base_panel` decomposition, **A14**). Clears batch_export:194.
10. **D10 — tab-focus providers + view-state snapshot** (the split providers,
    `_snapshot_tabs`, `_set_selected_wells`) → ctx. Clears tab_controller ×2,
    view_state ×2, heatmap_layouts (register the combos).
11. **T6 — Delete `ViewerContext.app`.** Once D1–D10 land the count at ~0–3, rename
    the field so it's reachable only through the frozen `ctx.window`/named
    accessors, dissolve the `AppRuntime` stamp (after a mechanical
    `app._<prefix><name>` → `ctx.runtime.<f>.<name>` reader sweep), route the
    persistence unwraps through `ctx.state`, and flip the ratchet `<= N` → `== 0`.

**Honesty note.** The enabler campaign deliberately stopped at 26 rather than
force the count down by aliasing `app = ctx.window` for behaviour calls — that
would game the ratchet (the docstring bans it) without decoupling anything.
`ctx.window` is the honest home only for genuine Qt-widget/parenting needs. The
26 fall only when their god-methods actually take `ctx` — that is D1–D10, real
behaviour extraction, each interactive-smoke-tested before merge.

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
| T3 | Thread ctx through controllers | T2 | High | `[~]` | 249 → 14 (R1–R7 + F1/F2/F4 + E1–E6 + D-series done; 14 floor remains) |
| T4 | Extract cross-cutting services | T1 | Med | `[~]` | folded into D1 (channel/axis → `channel_changed`) |
| T5 | Saved-plots service | — | Med | `[~]` | capture/persistence ctx-native (`226d0af`); `_rebuild_all` → D7 |
| T6 | Retire `ctx.app` | D1–D10 | High | `[ ]` | 26 floor → 0 (decompose god-methods, then delete field) |

**Campaign status (51 → 26 = 97% overall).** The parallel sweep (F1/F2/F4) plus
the enabler passes **E1–E6** (register orphan widgets, ctx-native
`ScatterCellViewer`, relocate shared toggles, migrate the review-image /
heatmap-plate-sel / image-browser / stats-editor service chains, `_doc` → typed
`AppState`) drove **51 → 26**. That is the **irreducible floor**: each remaining
ref passes `app` into a *behaviour* god-method with no `ctx` form, so converting
it piecemeal net-adds `ctx.app`. Reaching 0 now needs **god-method decomposition
(D1–D10) → T6** — real behaviour extraction, each interactive-smoke-tested. Full
floor register + the D-series roadmap in
[§ Endgame](#endgame--the-enabler-campaign-51--26--the-irreducible-floor).

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

**Last updated:** 2026-07-04 (latest) — the god-method decomposition (D-series)
**ran to the irreducible floor: 51 → 14 (98.4% overall)**, all pushed and green.
Landed net-negative: **D1** channel/metric, **D4** review-image draw web, **D5**
image-browser sub-tabs, **D6** sample-def/review-csv builders, **D-core** redraw
orchestrator, **D-persist** heatmap persistence, **D-thresh** channel/metric/
threshold/status-chip chain, plus the ui_helpers band controls and the
cell-gating quick-edit — each via the "bundle N god-services behind ONE core
seam" tactic or a full leaf-first conversion. **Two latent crashes fixed en
route** (image-table threshold stepper `set_thresh_frac_on(ctx)` mismatch;
all-black review-image threshold thumbnails). **Correctly declined as net-0**
(would game the ratchet): **D-selection** + **D7** (`saved_plots._rebuild_all`)
— both pinned to the `app._features` feature-registry hook contract (`on_selection_changed(app)`),
which needs a deliberate architecture decision to give the registry a ctx home;
**D9** (`_show_batch_mode`) — blocked on the A14 batch-panel decomposition
(~2900 lines). **The 14 that remain are the genuine floor** — see the register
below; each is a sanctioned single-unwrap core seam bundling irreducible
behaviour, or a persistence widget-bank unwrap, cleared only by **T6** (delete
the field) or the three named epics (feature-registry ctx-home, A14, cell-gating
edits-dict → typed state). Superseding earlier same-day notes below.

**Prior note (2026-07-04):** enabler passes **E1–E6** drove **51 → 26**
(97% overall). Recorded the irreducible-floor register (each of the 26 tied to a
behaviour god-method) and the **D1–D10 god-method-decomposition roadmap → T6** as
the only honest path to 0.

**Last updated:** 2026-07-04 — parallel campaign drove **51 → 45** (F1/F2/F4
integrated; F5 net-0, blocked). Replaced the F-batch plan with the definitive
**45-ref residual register** and the **enabler-pass plan (E1–E5 → E6 → T6)** the
workers' findings proved is required to reach 0.
