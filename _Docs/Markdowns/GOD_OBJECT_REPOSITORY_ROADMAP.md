# Roadmap: God-Object Teardown + Repository Introduction (`well_viewer/`)

> **Living document.** Keep this updated as phases land — tick items off, lower
> the seeded ratchet numbers as they ratchet down, and adjust file:line anchors
> when they drift. It is the tracking home for the two workstreams below.
>
> **Baseline:** `main` — the build-time-feature-profiles work merged to `main`
> via PR #639, so this roadmap now builds directly on `main`. Last revised:
> 2026-08-29 (reconciled against the tree: see the top changelog entry).
> **Companion:** `_Docs/Markdowns/ARCHITECTURE.md` (the structural map this plan
> extends).

## Goal-mode completion

This roadmap is written to be driven to completion autonomously ("goal mode"):
every phase has a machine-checkable Definition of Done, and the overall goal is a
conjunction of guard states. An agent should never need to ask "is this done?" —
the ratchets and boot tests answer it.

### Terminal goal (the whole roadmap is complete when ALL hold)
1. `ViewerContext` exposes only `window` — the other 8 service accessors are
   extracted. **Proof:** `tests/test_ctx_service_accessors.py::_ALLOWED == {"window"}`.
2. Every delegator/function is `ctx`-native. **Proof:** `RAW_SELF_DELEGATOR_BUDGET == 0`
   **and** `APP_FIRST_FUNC_BUDGET == 0` in `tests/test_guardrails.py`.
3. No layer inversion: `well_viewer/data/**` and `well_viewer/core/**` carry **zero**
   `features.*` imports (module-scope *or* function-local) outside `features/registry.py`.
   **Proof:** `tests/test_data_layer_no_feature_imports.py` green at full scope.
4. All row reads flow through `WellRepository`. **Proof:** `REPO_BYPASS_BUDGET == 0`
   (minus the documented `spatial` allowlist).
5. `PersistenceRegistry` is fully `ctx`-native. **Proof:** `PERSIST_APP_PARAM_BUDGET == 0`.
6. Full pytest suite + `tests/test_app_smoke.py` + every subset-profile boot test
   (`tests/test_feature_profile.py`) green.

### Per-phase Definition of Done

| Phase | Done when | Guard that proves it |
|---|---|---|
| A0 | Both burn-down ratchets exist, seeded, hard-fail | `test_guardrails` has `RAW_SELF_DELEGATOR_BUDGET`, `APP_FIRST_FUNC_BUDGET` + their `not_slack` twins |
| A1 | 11 widget placeholder attrs gone from `__init__` | `RUNTIME_APP_ATTR_BUDGET` lowered; suite green |
| A2 | All 44 `fn(self)` delegators flipped/removed | `RAW_SELF_DELEGATOR_BUDGET == 0` |
| A3 | Every `core/`+`features/` free func is `ctx`-first | `APP_FIRST_FUNC_BUDGET == 0`; `test_frozen_ctx_guardrail` green |
| A4 | Four `__init__` reach-throughs deleted | lines gone from `runtime_app.py:294-297`; `RAW_SELF_DELEGATOR_BUDGET` lowered |
| A5 | 8 accessors extracted; only `window` left | `_ALLOWED == {"window"}`; new service unit tests green |
| B1 | `get_rows` uses `cache_projector` hook, no feature import | `test_data_layer_no_feature_imports` green for `data/`; QC/Crop-excluded profile boots + loads rows |
| B1b | `load_controller` names no feature | `test_data_layer_no_feature_imports` extended to `core/` green |
| B2 | `WellRepository` exists; `REPO_BYPASS_BUDGET` seeded | facade unit test green; ratchet present |
| B3 | Direct-cache readers routed (spatial allowlisted) | `REPO_BYPASS_BUDGET == 0` (minus allowlist) |
| B4a | `PersistenceRegistry` `ctx`-native | `PERSIST_APP_PARAM_BUDGET == 0`; app_smoke round-trip green |
| B4b | One load path — eager bulk load goes through the repository | `WellRepository.load_well` exists and `load_controller` calls no `app._load_well_csv`; `tests/test_one_load_path.py` + app_smoke green |
| B5 *(optional)* | Image repo facade owns all decode/cache | image-facade ratchet == 0; facade tests green |

### Autonomous execution protocol
Loop until the Terminal goal holds:
1. **Select** the lowest-ID phase whose Def-of-Done guard is *not* yet satisfied and
   whose ordering constraints (see Sequencing) are met. Prefer the mechanical
   first steps (A0, A1, B1, B2) — none need god-method decomposition.
2. **Implement** only that phase (one landable unit; keep the diff scoped).
3. **Verify:** run that phase's guard + the Qt-free suite; add app_smoke for any
   paint-path cluster. Lower the phase's seeded ratchet to the new measured value.
4. **Record:** tick the Progress log; commit with the phase ID in the subject.
5. **Stop / escalate** if: a guard can't be made green without a cross-cutting
   refactor larger than the phase, a paint-path regression appears only in
   app_smoke, or two consecutive attempts on the same phase fail — surface the
   blocker rather than forcing it.

## What the baseline (now on `main`) already landed (reuse these seams)

The build-time-feature-profiles work (merged to `main`, PR #639) added a
build/runtime **feature-profile system** and, more importantly for this roadmap,
generalized the feature-registry into the **canonical decoupling primitive**. Do
not reinvent what it already gives:

- **`FeatureRegistry.with_hook(attr)`** (`core/feature_registry.py:143`) is now the
  generic "core calls each feature on event X" fan-out. Any new such dispatch
  should add a `FeatureDescriptor` hook field + a `with_hook` loop — **not** a new
  module-level registry or dispatch table.
- **New ctx-native descriptor hooks:** `on_dataset_loaded(ctx)` (`:88`, fanned out
  at `load_controller.py:402-407`), `plot_card_decorator` (`:95`), and
  `view_state_io` (`:100`, feature-owned snapshot/restore). Tab `build(ctx, frame)`,
  `on_activate`/`on_channel_changed`/`on_dataset_loaded` are all `ctx`-native. The
  **one hold-out is `on_selection_changed`, still `app`-first** (`registry.py:399+`).
- **Persistence view-state decoupling is ~80% done.** `persistence/view_state.py`
  shrank ~274 lines; Image Table
  (`features/image_browser/image_table/view_state_io.py`) and Heatmap
  (`features/heatmap/view_state_io.py`) own their snapshot/restore behind the
  `view_state_io` hook, and the *entire* live view-state pipeline (`snapshot(ctx)`/`restore(ctx)`/
  `apply_pending_tab_state(ctx)`) is already `ctx`-native. Only the outer
  `save_to_data_dir(app)`/`load_from_data_dir(app)`/`reset(app)` boundary still
  takes `app`, and Bar/Distribution/Scatter view-state blocks are still inline in
  `view_state.py` rather than behind the hook.
- **Feature→dependency catalog already exists:** `profiles/full.toml`
  `[[feature]].deps` with tested pure-math consumers (`scripts/profile_build.py`:
  `compute_exclusions`, `plan_ml`). Reuse it for any dep-gating need.
- **Profiles make removability *physical*:** `core/profile.py` filters the
  descriptor list in `build_feature_registry(profile)`; a boot-under-subset test
  (`tests/test_feature_profile.py::test_app_boots_and_loads_under_subset_profile`)
  is the new gold-standard guard idiom — new decoupling work should add/extend a
  subset-profile boot assertion, matching this.
- **Still coupled (confirmed teardown targets):** `core/load_controller.py` retains
  many direct `from well_viewer.features…` imports (qc/crop/review_image/home/
  heatmap) *not* yet on the hook seam;
  `on_selection_changed` hooks + the `stats`/preview builders still name `app`.

## Context

`well_viewer/` (the Review side of the All-Well PySide6 app) already runs a
disciplined, ratchet-guarded architecture-cleanup culture: AST tests that only
ratchet metrics downward, and documented "burn-down" epics. Two structural
weaknesses remain from that lineage:

1. **The `WellViewerApp` god-object.** The `ctx.app` reach-through axis is
   *done* (`CTX_APP_BUDGET = 0`, `tests/test_ctx_app_burndown.py`). But the god
   still has mass the burn-down didn't meter: ~155 delegator methods (44 forward
   to raw `self`), ~131 `app`-first functions, 9 frozen `ViewerContext` service
   accessors that reach into the private `_window`, and 11 widget-handle
   placeholder attrs. `viewer_context.py`'s own docstring names the 9 accessors
   as "the enumerable spec for the remaining god-object teardown."

2. **No formal data-access abstraction.** The read path is already ~80% funneled
   through `get_rows(ctx, label)` + `DatasetState`, so a repository is *latent* —
   but `get_rows` reaches **up** into `features/qc` + `features/crop` (a layer
   inversion), direct-cache readers bypass it, two load paths diverge (lazy vs
   eager), and `PersistenceRegistry` still passes the god-object `app`.

**Outcome intended:** narrow `_window` to a pure `QMainWindow`, and give data
access a typed, feature-decoupled repository seam — both reached the same
incremental, ratchet-guarded way the codebase already improves itself.

**Key correction from design review:** the remaining god-object coupling (the 44
`fn(self)` delegators, the ~131 `app`-first functions, the 9 accessors) is **not**
currently metered by any ratchet. So each workstream's first move is to *add* the
burn-down metric — mirroring `test_ctx_app_burndown.py` — before touching product
code.

---

## Workstream A — God-object teardown

### A0 · Add the burn-down ratchets *(prerequisite, mechanical, no product code)*
Add AST counters to `tests/test_guardrails.py`, seeded at today's measured values,
warn-then-hard-fail, ratchet-down-only (mirror `test_ctx_app_burndown.py:862`'s
`test_budget_is_not_slack` twin for each):
- `RAW_SELF_DELEGATOR_BUDGET` — methods in `runtime_app.py` whose body is a single
  `return fn(self, ...)` with a **bare `self`** first arg (exclude `self._ctx`
  forwarders). **Seed = 38** (measured on main). Discriminator: `Name id == "self"`
  as the call's first positional.
- `APP_FIRST_FUNC_BUDGET` — top-level funcs in `core/` + `features/` whose first
  param is `app`. **Seed = 135** (measured: core 83 + features 52). Both ratchets
  carry a `_not_slack` twin that forces the budget down as the count drops.

**Landed** (`test_guardrails.py`): `RAW_SELF_DELEGATOR_BUDGET`,
`APP_FIRST_FUNC_BUDGET` + `_not_slack` twins + `_raw_self_delegators` /
`_app_first_free_funcs` AST helpers. ✅

*Ship alone, green. This converts both remaining god dimensions into metrics.*

### A1 · Widget-handle placeholders → `ctx.widgets` *(mechanical, low-risk, independent)*
Move the 11 `None`-seeded widget attrs (`_plot_chan_cb`, `_bar_tp_cb`,
`_preview_fov_cb`, …) out of `__init__`. Builders already call
`ctx.widgets.register(...)`, which stamps `self.__dict__` (`runtime_app.py:268-271`),
so residual `app._x` readers keep resolving.
- Files: `runtime_app.py` (delete `None` inits); the builders that populate them
  (`features/barplot/tab_view.py`, `features/preview/preview_view.py`, …).
- Reuse: `WidgetRegistry` + its `_attr_store` stamp.
- Guard: ratchet `RUNTIME_APP_ATTR_BUDGET` (68 → ~57) and the line budget down.

### A2 · Retire raw-`self` delegators *(mechanical, interleave with A3)*
Split the 44 `fn(self)` delegators by inspecting each callee:
- **A2a — callee is pure/`ctx`-ready:** flip the callee to `ctx`, delegator becomes
  `return fn(self._ctx, ...)` (e.g. `_rebuild_all`, `_toast`, `_show_progress`).
  Precedent: `_parse_rc` / `_rank_color_well` extractions in the ledger.
- **A2b — callee genuinely needs the `QMainWindow`** (dialog parenting,
  `findChildren`): route through `ctx.window`, then drop/forward `self._ctx`.
- Files: `runtime_app.py` delegators + the `core/` controllers they call.
- Guard: `RAW_SELF_DELEGATOR_BUDGET` down; `APP_FIRST_FUNC_BUDGET` down as callees
  flip. `test_frozen_ctx_guardrail.py` already bans calling a `ctx`-first function
  with raw `self`, protecting every flip.

### A3 · Migrate `app`-first functions to `ctx`-first, cluster by cluster *(the bulk)*
Same "one module per PR" cadence as the `ctx.app` ledger. Priority by risk:
1. `features/image_browser/playground/controller.py` (15, fully unmigrated,
   self-contained) — **best first cluster.**
2. `core/selection_controller.py` (25) — but **defer** `rebuild_all` (`:869`) and
   `refresh_sidebar_map_now` (`:580`); they back two frozen accessors (→ A5).
   Migrate the other 23.
3. `core/split_view.py` (12), `load_controller.py` (9), `ui_helpers.py` (9),
   `tab_controller.py` (8), `plot_orchestrator.py` (6).
4. Features tail: `stats/controller.py` (9); **`features/registry.py` `_sel_*` hooks —
   now the *confirmed* last app-first descriptor contract:** on the baseline,
   `on_activate`/`on_channel_changed`/`on_dataset_loaded`/`view_state_io` are all
   `ctx`-native and only `on_selection_changed` still takes `app` (`registry.py:399+`).
   Flip that hook contract to `ctx` in lock-step, and fix the stale
   `feature_registry.py:72` docstring that still claims "every hook takes `app`."
   Then `export/style.py` (5); `heatmap/sidebar_view.py` (5).
5. **Sample Definitions app-bridge god-seam** — **closed.** The sample definitions
   became tabs of Home, and the page builder that needed the seam
   (`grouping.replicate_panel_view.build_sample_definitions_centre`) went with the
   page. Home assembles those tab bodies from the registry — each is a sub-leaf
   descriptor whose `build` adapter holds the only import of its package — so
   `tab_controller.build_sample_definitions_app_bridge` is deleted rather than
   migrated: there is no feature routing through the god-object here any more.
- Pattern per fn: `def f(app,...)` → `def f(ctx,...)`; body reads
  `ctx.state`/`ctx.widgets`/`ctx.runtime`; callers pass `ctx` (or `app._ctx` at a
  boundary). Identical to every ledger entry.
- Guard: `APP_FIRST_FUNC_BUDGET` per cluster; `test_frozen_ctx_guardrail.py`.
- Risk: medium — feature clusters with paint paths need the app_smoke check.

### A4 · Dissolve the four `__init__` runtime reach-throughs *(needs A3.4)*
`runtime_app.py:294-297` wires `self._runtime.stats.run/.property_change` and
`.barplot.current_keys/.well_display_label` to god-methods. Once `stats` and
`barplot` are `ctx`-native (A3.4), replace each with a direct `ctx`-native call;
delete the four wiring lines and the backing `_stats_run`/`_bar_current_keys`
methods (each is one of the 44 delegators). Guard: line + `RAW_SELF_DELEGATOR`
budgets down.

### A5 · Service-accessor extraction *(the endgame; each needs its god-method decomposed first)*
Extract the 9 accessors so `ctx` holds a standalone collaborator instead of
reaching `_window`. Ordered trivial → hard:

| Accessor(s) | Becomes | Lives in | Difficulty |
|---|---|---|---|
| `persistence` | *already* `PersistenceRegistry` | `persistence/registry.py` | **trivial** — hold registry directly. Precondition: **B4** (registry takes `ctx`, not `app`). |
| `features` | *already* `FeatureRegistry` | `features/registry.py` | **trivial** — store on `ctx` directly. |
| `set_status` + `toast` | new `NotificationService` | new `core/notifications.py` | medium — thin Qt-shell ops; extract `_set_status`/`_toast` bodies. |
| `save_figure_dialog` | new `FigureSaver` | new `core/figure_saver.py` | medium — needs `ctx.window` + `ctx.state.dataset.data_dir`. |
| `open_export_style_panel` | fold into export-style feature object | `features/export/` | medium — give it a registered runtime entry. |
| `rebuild_all` | `SelectionCoordinator.rebuild_all()` | new `core/selection_coordinator.py` (absorbs `selection_controller.py:869`) | **hard** — fans out into a dozen god-methods; A3 must make them `ctx`-native first, then coordinator is a plain sequence. |
| `refresh_sidebar_map` | `SelectionCoordinator.refresh_sidebar_map()` | same module (absorbs `:580`) | **hard** — debounced `QTimer` plate repaint; widgets already in `ctx.widgets`. |
| `window` | irreducible — stays until `_window` is a pure `QMainWindow` | `viewer_context.py` | **last** — the terminal state; when the other 8 are gone, this is the only accessor left. |

- Guard per extraction: **remove** the entry from `_ALLOWED` in
  `test_ctx_service_accessors.py` (the freeze test forces this to be reviewed) and
  add a focused unit test for the new (now Qt-free-ish) service.

---

## Workstream B — Repository introduction

### B1 · Invert the `get_rows`→features layer violation via a cache-projector registry ✅ *(foundational, low-risk, unblocks everything)*
**Landed.** `get_rows` did function-local
`from well_viewer.features.qc.controller import apply_qc_to_cache` /
`...crop.controller import apply_crop_to_cache`.

**Design note (differs from the original sketch):** QC and Crop are *support libs*
with **no `FeatureDescriptor`** (and must not be profile-removable — QC is the
fundamental always-applied inclusion layer), so the `with_hook` descriptor seam
doesn't apply. Instead this uses a dedicated **projector registry** mirroring
`core/scope_registry.register_default_scopes` — the proven "core wires the feature
callback at startup so the lower layer imports no feature" pattern.

- New `data/cache_projectors.py` — module-level list + `register_cache_projector(fn)`
  / `clear_cache_projectors()` / `iter_cache_projectors()`; `fn` is `(ctx, label)`.
- New `core/projector_registry.register_default_projectors()` — imports the QC/Crop
  controllers function-locally and registers them (QC then Crop; idempotent via
  clear-first). Wired once from `core/bus_wiring.wire_event_bus`.
- `get_rows` now runs `for _project in iter_cache_projectors(): _project(ctx, label)`
  — **zero `features/` imports in the data layer.**
- Guard: `tests/test_data_layer_no_feature_imports.py` — AST assert that no
  `well_viewer/data/**` module imports `well_viewer.features.*` at **any** nesting.
  Green. (No subset-profile extension: QC/Crop are always present, not
  profile-gated, so their projectors register regardless of profile.)
- Verified: `test_qc_integration` / `test_crop_integration` (projection through
  `get_rows` on `loaded_app`) + qc/crop/aggregate layer tests + app_smoke — 46 green.

### B1b · Move `load_controller`'s residual direct feature imports onto hooks *(extends B1, medium)*
`load_controller.py` still imports qc/crop/review_image/home/heatmap directly — the post-load work not yet on the `on_dataset_loaded`
seam the baseline introduced. Migrate each to an `on_dataset_loaded(ctx)` hook on its
feature descriptor (the mechanism already exists and is proven for Image Table +
smFISH), so `load_controller` names **no** feature and imports none a profile excluded.
- Guard: extend `test_data_layer_no_feature_imports` scope to assert `load_controller`
  (and ideally all of `core/`) carries no module-scope `features.*` import beyond the
  sanctioned `features/registry.py` — this already partly holds via
  `test_architecture_contracts.py::test_core_does_not_import_features_at_module_scope`;
  tighten to catch the function-local ones this phase removes.

> **⊘ Deferred (2026-07-11).** These are *function-local* core→feature imports —
> the **sanctioned** escape hatch (`test_layering` / `test_architecture_contracts`
> ban only *module-scope* core→feature, which is already green). Hoisting them
> onto `on_dataset_loaded` is not a simple move: `load_directory` pins each call
> to a specific point in a strictly-ordered sequence interleaved with
> `persistence.load(...)` steps (cell_overrides → qc recompute → crop → …, each
> with documented ordering constraints), whereas the `on_dataset_loaded` hook
> fires at a *single* point. Doing this safely needs an **ordered post-load hook
> phase** design — higher-risk-than-value versus the rest of the roadmap, and the
> current function-local imports are already contract-compliant. Revisit after A3
> makes the surrounding calls `ctx`-native.

### B1b · Terminal condition 3 — the `core/` → `features/` residual ✅ *(closed)*

**Landed.** All thirty sites below are converted and `CORE_FEATURE_IMPORT_BUDGET`
is **0**. The mechanism is the feature-service lookup: `FeatureDescriptor.services`
declares named callables (lazy factories, exactly as `redraw` is), core reaches
them as `ctx.features.call(key, …)` / `.query(key, …, default=…)`, and the
support libraries that carry no descriptor — QC, crop, the export-style panel —
register into the same lookup via `FeatureRegistry.register_service`.
`tests/test_feature_services_partition.py` relates the declared and called sets
in both directions, so a typo'd or orphaned key fails rather than no-opping.

Two categories turned out not to need the mechanism at all, which is worth
recording because the deferral note assumed otherwise:

- The **query** sites were pure state arithmetic wearing a feature's name.
  `_cell_gating_resolve_key` and `cell_gating_get_fluor_gate` read the typed
  `CellGatingState` slice and nothing else, so they moved to
  `domain/gating_state.py`; core reads the domain, and the tab keeps both names
  as delegates.
- The **ordered** post-load projections needed no ordered-hook phase. The
  ordering constraint was never about *where the import lived* — the loader
  still runs QC and crop at exactly the points in its sequence that it did
  before, interleaved with `persistence.load`. Only the name moved to a
  service. The deferral had conflated "this call is ordering-critical" with
  "this call must move to a hook", and only the second was ever hard.

Thirty sites, enumerated from the AST rather than from a grep, each keyed by
module, function and symbol because line numbers move as the work proceeds. The
count is metered by `CORE_FEATURE_IMPORT_BUDGET` in `tests/test_guardrails.py`,
**which is the authority** — the table below is how the work is planned and
tracked, and the header count is derived from its own rows, never typed.

Statuses: ☐ not started · ◐ in progress · ☑ done (the import is gone and the
ratchet has come down) · ⊘ declined (with the reason in the row).

Categories, which are also the mechanisms:

- **const** — the import is a constant, not behaviour. Move it to a neutral
  module, as `WELL_MIME` moved to `core/dnd`.
- **service** — a void call into a feature. Goes through the feature-service
  lookup: the feature declares a named callable on its descriptor, and core
  calls it by name through `ctx.features`, so a profile without the feature
  simply has nothing registered.
- **query** — a call that *returns a value*. The same lookup, but the call site
  must say what the answer is when the feature is absent; a void fan-out cannot
  express it, which is why these were called out as needing this mechanism.
- **build** — a widget builder. Same lookup; the builder is the service.
- **ordered** — a post-load projection pinned to a point in a strictly ordered
  sequence interleaved with `persistence.load` steps. These are the reason B1b
  was deferred: the `on_dataset_loaded` hook fires at a single point and cannot
  express the ordering.
- **bootstrap** — the wiring modules whose whole job is to hand a feature
  callback to a lower layer. Removing the import means moving the wiring into
  `features/registry.py` (the sanctioned sole importer) or naming the site in an
  allowlist with its justification and a liveness check.

| # | Module (`well_viewer/core/`) | Function | Imported symbol(s) | Category | Status | Note |
|---|---|---|---|---|---|---|
| 1 | `channel_state_controller.py` | `refresh_heatmap_aggregation_options` | `heatmap.controller: METRIC_MEAN, METRIC_FRACTION` | const | ☑ | Aggregation labels, not behaviour — move to a neutral module as `WELL_MIME` was. |
| 2 | `channel_state_controller.py` | `refresh_heatmap_aggregation_options` | `heatmap.controller: METRIC_MEAN_ALL` | const | ☑ | Same constant set, second site in the same function. |
| 3 | `channel_state_controller.py` | `_recalculate_threshold` | `home.cell_gating.controller: cell_gating_load_cell_areas, cell_gating_load_threshold_count_on, cell_gating_load_threshold_frac_on` | service | ☑ | Void loads into the gating tab after a threshold recompute. |
| 4 | `channel_state_controller.py` | `set_ratio_metrics` | `home.cell_gating.controller: cell_gating_load_cell_areas` | service | ☑ | Same service, second caller. |
| 5 | `channel_state_controller.py` | `set_active_image_channel` | `image_browser.review_image.renderer: refresh_review_image_ctx` | service | ☑ | Re-render the review image for the new channel. |
| 6 | `channel_state_controller.py` | `set_active_image_channel` | `image_browser.review_image.renderer: update_preview_ctx` | service | ☑ | Same event, sibling renderer. |
| 7 | `channel_state_controller.py` | `_write_gating_edit` | `home.cell_gating.controller: _cell_gating_resolve_key, cell_gating_on_threshold_count_on_change, cell_gating_on_threshold_frac_on_change` | query+service | ☑ | Resolves a key (returns a value), then dispatches the edit. |
| 8 | `channel_state_controller.py` | `_gating_row_key` | `home.cell_gating.controller: _cell_gating_resolve_key` | query→domain | ☑ | Pure state arithmetic: moved to `domain/gating_state.resolve_gate_key`, so core reads it from the domain and no service is needed. |
| 9 | `channel_state_controller.py` | `get_fluor_gate` | `home.cell_gating.controller: cell_gating_get_fluor_gate` | query→domain | ☑ | Pure state arithmetic: moved to `domain/gating_state.fluor_gate_for`; unset reads as 0.0 (gate off), as before. |
| 10 | `channel_state_controller.py` | `ensure_cell_gating_tab_built` | `home.cell_gating.tab_view: build_cell_gating_subtab` | build | ☑ | Lazy sub-tab construction. |
| 11 | `load_controller.py` | `load_directory` | `qc.controller: recompute_qc_all` | ordered→service | ☑ | The ordering is kept exactly where it was, in the loader's own sequence; only the *name* moved to a service, so no ordered-hook design was needed. |
| 12 | `load_controller.py` | `load_directory` | `qc.tab_view: qc_gate_refresh_widgets` | service | ☑ | Widget sync after the QC hydrate. |
| 13 | `load_controller.py` | `load_directory` | `crop.controller: apply_crop_to_cache` | ordered→service | ☑ | The ordering is kept exactly where it was, in the loader's own sequence; only the *name* moved to a service, so no ordered-hook design was needed. |
| 14 | `load_controller.py` | `load_directory` | `home.labels.tab_view: label_grid_refresh` | service | ☑ | Post-load refresh of the labels grid. |
| 15 | `load_controller.py` | `load_directory` | `heatmap.sidebar_view: refresh_heatmap_layout_sidebar` | service | ☑ | Post-load refresh of the heatmap sidebar. |
| 16 | `load_controller.py` | `_kick_off_gating_after_load` | `home.cell_gating.controller: cell_gating_refresh_included_count, cell_gating_start_gating_worker` | service | ☑ | Starts the gating pass once the dataset is up. |
| 17 | `selection_controller.py` | `on_sidebar_plate_well_dropped` | `heatmap.sidebar_view: _on_drop_event` | service | ☑ | Drop handling delegated to the sidebar that owns the plate. |
| 18 | `selection_controller.py` | `labels_apply_affix` | `home.labels.tab_view: label_grid_refresh` | service | ☑ | Same refresh service as the loader's. |
| 19 | `selection_controller.py` | `groups_centre_refresh` | `home.labels.tab_view: label_grid_refresh` | service | ☑ | Same refresh service, third caller. |
| 20 | `selection_controller.py` | `groups_centre_refresh` | `home.groups.selections_view: rep_panel_refresh` | service | ☑ | Replicate panel refresh. |
| 21 | `selection_controller.py` | `groups_centre_refresh` | `home.groups.controller: rep_refresh_map` | service | ☑ | Replicate map refresh. |
| 22 | `selection_controller.py` | `labels_clear_all` | `home.labels.tab_view: label_grid_refresh` | service | ☑ | Same refresh service, fourth caller. |
| 23 | `centre_view.py` | `build_centre` | `home.groups.panel_view: build_replicate_panel` | build | ☑ | Centre-page builder. |
| 24 | `centre_view.py` | `build_centre` | `preview.preview_view: build_preview_picker` | build | ☑ | Centre-page builder. |
| 25 | `sidebar_view.py` | `build_sidebar` | `heatmap.sidebar_view: build_heatmap_layout_sidebar` | build | ☑ | Sidebar builder. |
| 26 | `figure_saver.py` | `open_style_panel` | `export.style: open_export_style_panel` | service | ☑ | `FigureService` reaching the export-style feature. |
| 27 | `plot_card_actions.py` | `copy_active_card_as_svg` | `export.style: copy_figure_to_clipboard` | service | ☑ | Same feature, clipboard path. |
| 28 | `projector_registry.py` | `register_default_projectors` | `qc.controller: apply_qc_to_cache` | bootstrap | ☑ | The documented B1 decoupling seam — core wires the feature callback. |
| 29 | `projector_registry.py` | `register_default_projectors` | `crop.controller: apply_crop_to_cache` | bootstrap | ☑ | Same seam. |
| 30 | `scope_registry.py` | `register_default_scopes` | `registry: build_feature_registry` | bootstrap | ☑ | Imports the registry module itself, not a feature — fallback when no registry was handed in. |

### B2 · `WellRepository` facade over `DatasetState` + `get_rows` ✅ *(interface seam, no behavior change)*
**Landed.** `data/well_repository.py` — Qt-free `WellRepository` exposing
`rows(label)`, `rows_or_none(label)` (cache-only), `labels()`, `all_timepoints()`,
`all_fovs()`, `has_data`, `dataset`. No logic moves — delegates to
`data_loading.get_rows` / `get_all_timepoints` and the `DatasetState` root.
- **Holds `ctx`, not the bare `DatasetState`:** `get_rows` runs the cache
  projectors, which read the wider `ctx.state` (QC params, crop) — so the facade
  threads the context.
- **Reached as `ctx.state.repo`**, attached in `ViewerContext.__post_init__`
  (the DI bundle is the natural wiring point; keeps `runtime_app.py` untouched so
  the line ratchet holds). Added `AppState.repo` field (default `None`). **Not** a
  new `ViewerContext` accessor (frozen-accessor freeze untouched — verified).
- Guard: `tests/test_well_repository.py` (facade unit tests) + new
  `tests/test_repo_bypass.py::REPO_BYPASS_BUDGET = 1` counting single-well
  `dataset.cache.get(...)` / `dataset.cache[...]` reads outside the owner modules,
  with `spatial` allowlisted. Seed 1 = `stats_compute.py:93` (B3 → 0). `_not_slack`
  twin included.

### B3 · Route direct-cache readers through the repository *(burn-down, medium)*
- `domain/stats_compute.py:93` `_safe_rows` — currently `cache.get(lbl)` (non-lazy,
  **returns `None` for the test mock — preserve that contract**). Add a
  `rows_or_none(label)` variant on the repo for this one caller.
- crop/channel/smfish controllers — flip *reads* to the repo; leave cache *writes*
  for B5.
- `features/spatial/collect.py` — deliberately cache-only; **allowlist** it in the
  ratchet (like the `ctx.window` widget-need carve-out).
- Guard: `REPO_BYPASS_BUDGET` down; spatial on explicit allowlist.

### B4 · Converge the two load paths + persistence `app`→`ctx` *(medium-high, needs B2)*

This phase has two independent halves. **B4a (persistence → `ctx`) has landed;
B4b (load-path convergence) has not been started.**

- **B4b — landed.** The eager bulk path built its frames itself
  (`app._load_well_csv`, a direct `dataset.cache[key] = frame`, its own
  `record_fov_verdict`) while a cache miss went through `get_rows`. Both now go
  through `data_loading.load_and_project`, reached from the loader as
  `ctx.state.repo.load_well(key, path)` — the repository's first write entry
  point. The divergence this closed was not hypothetical: the loader applied
  manual per-cell curation as well as QC and crop, while the lazy path ran only
  the two registered projectors, so a well the row cache dropped and reloaded
  came back with the user's exclusions gone from `Included` and the override map
  still holding them. `data/curation.py` now owns that projection and is
  registered beside QC and crop, so the roster of what a fresh frame gets is one
  list. The post-load hydration sequence is unchanged and still ordered: the
  wells are read before `persistence.load` has the settings, so the bulk
  re-projections after it stay where they are.
- **B4a — landed.** Make `PersistenceRegistry` take `ctx`/`state`, not `app`. Bodies mostly already
  deref `app._app_state`(==`ctx.state`); follow the direction `_doc.py` /
  `cell_gating.py` already set. Convert the ~10 clean domains to `save(ctx)/load(ctx)`
  first; leave the callback-coupled `sample_definitions.py` (`app._set_status`,
  `_groups_centre_refresh`, `_plot_groups_refresh`, `_invalidate_stats_cache`,
  `_redraw`) and `view_state.reset` until their god-methods are `ctx`-native
  (WS-A A3/A5). **This directly enables A5's trivial `persistence` accessor.**
- **view_state was already ~80% done on the baseline** — do not redo it. Internals are
  fully `ctx`-native, and Image Table (`features/image_browser/image_table/view_state_io.py`)
  and Heatmap (`features/heatmap/view_state_io.py`) own their snapshot/restore behind the
  `view_state_io` hook. Of the two remaining narrow items, (b) has landed and (a) has not:
  - **(a) — still open.** Move the inline Bar/Distribution/Scatter blocks in
    `view_state.py` (`_snapshot_bar`, `_snapshot_distribution`, `_snapshot_scatter`,
    `_apply_bar`, `_apply_distribution`, `_apply_scatter`) behind the **same
    `view_state_io` hook** (one descriptor factory per feature — reuse, don't invent).
    Nothing meters this, so it is invisible to the ratchets: it is tracked only here.
  - **(b) — landed.** The three outer boundary functions
    `save_to_data_dir(app)`/`load_from_data_dir(app)`/`reset(app)` now take `ctx`
    (`PERSIST_APP_PARAM_BUDGET == 0`).
- Guard: existing `RUNTIME_APP_PERSIST_WRAPPER_BUDGET = 0` holds; add
  `PERSIST_APP_PARAM_BUDGET` counting `save_to_data_dir(app)` signatures (seed ~12).

> **Attempt log (2026-07-11, reverted).** A first pass migrated all ~21 persistence
> public `save/load/reset(app)` funcs + the `PersistenceRegistry` to `ctx` (bridge)
> and made the registry pass `ctx`. **Production compiled and the frozen guard was
> clean**, but the change fanned out into ~6 persistence *test* files whose
> mocks each need bespoke ctx-shaping (the migrated funcs read `ctx.window._app_state`
> *and* pass `ctx` to `_doc.set_section`, so a mock must expose `.window` **and**
> `.state`). A scripted bridge-injection also over-matched the word `app` inside
> comments of already-`ctx`-native `view_state` internals. Rather than rush the
> per-test surgery at the tail of a long session, the whole attempt was reverted to
> keep the tree green. **Next time:** migrate persistence in a focused pass —
> (1) do it per-file with clean-`ctx` bodies (`ctx.state.…`, not the `app=ctx.window`
> bridge, since persistence rarely needs the window); (2) give the shared test mocks
> a real `_ctx` helper (like `test_split_view`'s) instead of self-refs; (3) add the
> `PERSIST_APP_PARAM_BUDGET` ratchet last.

### B5 · Image repository facade *(optional, later, isolated)*
Unify the de-facto image repository (`find_well_images_and_masks` discovery →
`resolve_channel_frame_refs` precedence → `open_imgref_as_array` decode, backed by
`ZipFileCache`/`GLOBAL_CACHE` + `_DecodedArrayCache`) behind
`data/image_repository.py`. `ImgRef` (value object) + `image_identity.py` (identity
SSOT) unchanged. Orthogonal to the row path; do only if the row repository proves
the pattern. Guard: facade tests + a ratchet on direct `GLOBAL_CACHE` /
`_DecodedArrayCache` reach-ins outside the facade.

---

## Sequencing & risk

```
A0 (ratchets) ──┐
B1 (cache_projector hook) ─┼─ independent low-risk first steps (B1 reuses with_hook)
A1 (widget attrs) ─────────┘
        │
        ├──► B1b (load_controller feature imports → on_dataset_loaded hooks)
        ▼
B2 (WellRepository facade) ── unblocks B3/B4
        │
A2 (self-delegators) ⇄ A3 (app-first clusters; on_selection_changed = last hook) ── interleaved PRs
        │                         │
        ▼                         ▼
A4 (__init__ reach-throughs)   B3 (route cache readers)
        │                         │
        ▼                         ▼
A5 (accessor extraction) ◄──── B4a (persistence→ctx) ; B4b (load converge)
        │
        ▼
A5 `window` = terminal state ; B5 (image repo) optional/last
```

- **Mechanical / low-risk first steps:** A0, A1, B1, B2 — none need god-method
  decomposition. Start here. B1 is now a small hook add (reuses `with_hook`), not a
  new registry.
- **Ordering constraints:** B1 before B1b/B3/B4; B2 before B3/B4; A3(stats+barplot)
  before A4; A3+A4 before the `rebuild_all`/`refresh_sidebar_map` extractions in A5;
  B4(persistence→ctx) before A5's `persistence` accessor.
- **Already partly landed by the baseline** (smaller than originally scoped): B1's
  registration mechanism (`with_hook`), B4's view_state decoupling (~80%), and the
  removability/profile guard idiom.
- **Only pieces needing god-method decomposition first:** the two coordinator
  accessors in A5 (their bodies fan out into a dozen god-methods A3 must convert).
  Everything else is signature-flipping.

## Verification
- **WS-A:** after each cluster, run the Qt-free pytest suite — the
  `test_guardrails` / `test_ctx_*` / `test_frozen_ctx_guardrail` AST guards run
  headless and catch signature/reach regressions instantly. Add the app_smoke test
  (`tests/test_app_smoke.py`) for any cluster touching a paint path
  (heatmap/scatter/distribution renders — headless tests can't see pixels).
- **WS-B:** pure-Python unit tests for each new facade (`WellRepository`,
  `NotificationService`, projector registry), modeled on the existing
  `DatasetState` / `PersistenceRegistry` tests. The new
  `test_data_layer_no_feature_imports` + `REPO_BYPASS_BUDGET` AST guards prove the
  inversion statically. An app_smoke dataset-open round-trip validates B4 (lazy +
  eager parity, QC/Crop projection, persistence hydration).

## Critical files
- `well_viewer/core/feature_registry.py` — `FeatureDescriptor` + `with_hook`. **This
  registry is the reuse seam for B1b and any future fan-out hook.** B1 did *not* add a
  `cache_projector` descriptor field: QC and Crop have no `FeatureDescriptor`, so it used
  the standalone projector registry instead (see the B1 section).
- `well_viewer/features/registry.py` — descriptor hook factories. Every hook is
  `ctx`-native now, `on_selection_changed` included (A3, landed); a new fan-out for B1b
  would be added here.
- `well_viewer/data/data_loading.py` — `get_rows`, whose layer inversion is landed (B1):
  it iterates `iter_cache_projectors()` and imports no feature.
- `well_viewer/data/cache_projectors.py` + `well_viewer/core/projector_registry.py` — the
  B1 seam itself; `projector_registry` holds 2 of the 31 remaining function-local
  core→feature imports, by design.
- `well_viewer/core/load_controller.py` — the largest remaining cluster: 7 function-local
  feature imports → `on_dataset_loaded` hooks (B1b), and the eager bulk load path
  (`app._load_well_csv`) → `repo.load_well` (B4b).
- `well_viewer/data/well_repository.py` — read facade today; B4b adds `load_well` to it.
- `well_viewer/persistence/view_state.py` + `features/*/view_state_io.py` — the inline
  Bar/Distribution/Scatter blocks still to move behind the `view_state_io` hook (B4a
  sub-item (a), open). `save/load/reset` already take `ctx`.
- `well_viewer/viewer_context.py` — A5 is complete: `window` is the only accessor left,
  and the other 8 services are held fields (`persistence`, `features`, `notify`,
  `figures`, `coordinator`). A new accessor here is a regression.
- `well_viewer/runtime_app.py` — A1/A2/A4 are complete: no raw-`self` delegators, no
  widget placeholder attrs. Watch the line + attr budgets when touching it.
- `tests/test_guardrails.py` + `tests/test_ctx_service_accessors.py` — where every new
  ratchet + accessor-freeze edit lands; `tests/test_feature_profile.py` — the
  boot-under-subset-profile guard idiom to extend for B1/B1b
- Supporting: `well_viewer/core/dataset_state.py` (repo aggregate root, B2),
  `well_viewer/persistence/registry.py` (app→ctx, B4)

---

## Progress log (goal-mode status board)

Single source of truth for "what's left." Statuses: ☐ not started · ◐ in progress ·
☑ done (Def-of-Done guard green) · ⊘ deferred (rationale in the phase section).
Goal mode picks the lowest-ID ☐ phase whose ordering constraints are met.

| Phase | Status | Def-of-Done guard | Landed (commit / date) |
|---|---|---|---|
| A0 · burn-down ratchets | ☑ | ratchets present + seeded (38 / 135) | 2026-07-11 |
| A1 · widget attrs → registry | ☑ | `RUNTIME_APP_ATTR_BUDGET` 68→15 (actual 13) | 2026-07-11 |
| A2 · retire raw-`self` delegators | ☑ | `RAW_SELF_DELEGATOR_BUDGET == 0` (38 → **0**) | 2026-07-11 (final 5 in `b20c100`) |
| A3 · `app`→`ctx` clusters | ☑ | `APP_FIRST_FUNC_BUDGET == 0` (135 → **0**) | all clusters 2026-07-11 |
| A4 · `__init__` reach-throughs | ☑ | `RAW_SELF_DELEGATOR_BUDGET == 0` | 2026-07-11 — the 4 `__init__` runtime wirings stay (pure-logic barplot/stats **test-injection seam**), but their backing methods are **not** raw-`self` delegators, so A2's `== 0` holds; no god-object forwarder remains. |
| A5 · service-accessor extraction | ☑ | `_ALLOWED == {"window"}` | 2026-07-11 (`3f0aa01`..`8904663`) — persistence/features→held fields; set_status/toast→`ctx.notify`; save_figure_dialog/open_export_style_panel→`ctx.figures`; rebuild_all/refresh_sidebar_map→`ctx.coordinator` |
| B1 · cache-projector inversion | ☑ | `test_data_layer_no_feature_imports` green | 2026-07-11 |
| B1b · `core/` feature decouple | ☑ | `CORE_FEATURE_IMPORT_BUDGET == 0`; all 30 tracker rows closed | 2026-08-29 |
| B2 · `WellRepository` facade | ☑ | facade test + `REPO_BYPASS_BUDGET`=1 seeded | 2026-07-11 |
| B3 · route cache readers | ☑ | `REPO_BYPASS_BUDGET == 0` (−spatial) | 2026-07-11 |
| B4a · persistence → ctx | ☑ | `PERSIST_APP_PARAM_BUDGET == 0` | 2026-07-11 |
| B4b · load-path convergence | ☑ | `WellRepository.load_well` exists, `load_controller` calls no `app._load_well_csv`, curation is a registered projector | 2026-08-29 |
| B5 · image repo facade *(optional)* | ☐ | image-facade ratchet == 0 | — |

**Terminal-goal status (2026-08-29): conditions 1–5 all hold and are
guard-proven.** Workstream A is complete (`_ALLOWED == {"window"}`,
`RAW_SELF_DELEGATOR_BUDGET == 0`, `APP_FIRST_FUNC_BUDGET == 0`); the repository
work is complete through B4 (`REPO_BYPASS_BUDGET == 0`,
`PERSIST_APP_PARAM_BUDGET == 0`, one load path); and **condition 3 now holds
too — `CORE_FEATURE_IMPORT_BUDGET == 0`.** `well_viewer/core/` and
`well_viewer/data/` import no feature at any nesting, module-scope or
function-local, and `features/registry.py` is the sole importer it was always
declared to be. Condition **6** (full suite green) is decided per-run by CI
rather than by this document.

What closed condition 3 was the feature-service lookup (see the B1b section):
descriptors declare named callables, core calls them by name, and the support
libraries that own no tab register into the same lookup. The two wiring modules
that used to live in `core/` are gone — the row-cache projectors and the
support services are wired inside `build_feature_registry`, and
`register_default_scopes` now takes the registry rather than building one.

**One phase remains open, and it is the optional one:** **B5** (image repository
facade — there is no `data/image_repository.py` and no facade ratchet). Nothing
depends on it, and the roadmap's terminal goal does not include it.

**Changelog** (newest first):

- 2026-08-29 — **B4b COMPLETE — one load path, and the divergence it was hiding.**
  The eager bulk load now goes through `ctx.state.repo.load_well(key, path)`, which
  delegates to the new `data_loading.load_and_project` — the single way a frame enters
  the row cache, for the loader's cold pass and a lazy cache miss alike. It registers
  the CSV path, reads it, runs every registered projector and records the FOV verdict,
  so neither path can acquire a step the other lacks.
  - **The paths had already diverged, and it was reachable.** The loader applied manual
    per-cell curation on top of QC and crop; `get_rows` ran only the two registered
    projectors. `persistence/reset_scopes.py` (reset QC parameters) and
    `core/channel_state_controller.py` (stale-cache recovery) both drop the row cache
    without clearing `runtime.review.included_overrides`, so wells came back from disk
    with the user's exclusions gone from `Included` while the override map still held
    them — excluded cells silently back in every plot, export and statistic. Reproduced
    before the fix, and `tests/test_one_load_path.py` fails without it.
  - **`data/curation.py`** now owns that projection (moved out of
    `features/image_browser/review_image/collect.py`) and is registered beside QC and
    crop, so the projector roster is the whole list of what a fresh frame gets rather
    than three things the loader happens to remember. The move also freed Cell Gating
    from its lazy hop into Image Browser and took the loader's feature import with it:
    `CORE_FEATURE_IMPORT_BUDGET` 31 → **30**.
  - `well_viewer/data/image_identity.py` gained `cell_key_tuples_for_dataframe`, the one
    implementation of the `(fov, tp, nid)` row key both the projector and Review Image's
    own lookups use; `collect.review_row_keys_series` is now the tab-side name for it.
  - `runtime_app._load_well_csv` is deleted — a pass-through with one caller, which was
    that loop.
  - Guard housekeeping: `tests/test_row_cache_writers.py`'s roster moves with the sites
    (`data/curation.py::Included`, `data/data_loading.py::__replaces_frame__`; the
    `load_controller` and `review_image/collect.py` entries are gone). The cache write is
    newly *visible* to that scan — the loader spelled it through a local name the
    detector could not attribute to the row cache, and `load_and_project` spells it
    `dataset.cache[label]`.

- 2026-08-29 — **Reconciled the roadmap against the tree (no product code).** Every
  phase status in the progress log was re-checked by reading the code and running the
  Def-of-Done guards (`test_guardrails`, `test_ctx_service_accessors`, `test_repo_bypass`,
  `test_ctx_app_burndown`, `test_data_layer_no_feature_imports` — 20 passed). No phase
  claimed done turned out undone, and the drift was one-directional: the roadmap
  *understated* progress. Corrections made here:
  - **Core→feature imports: the doc said 33, the guard says 31.**
    `CORE_FEATURE_IMPORT_BUDGET` reached 31 when the dead `ui_helpers.on_theme_change`
    (which held the last function-local imports of `features.grouping.tab_view` and
    `features.stats.controller`) was deleted. That step is recorded in the guard's own
    comment but never reached this file, and the repository's visible history begins at a
    squashed import commit, so it carries no date here. The terminal-status section now
    quotes the number with a per-module breakdown **and** says outright that the guard,
    not this file, is the authority for the count.
  - **B4 was ticked ☑ on its persistence half alone.** Split into **B4a** (persistence →
    `ctx`, done) and **B4b** (load-path convergence, **not started**): the eager bulk path
    still calls `app._load_well_csv` and writes `dataset.cache` directly, and
    `WellRepository` is still read-only, so B4b will add the first write entry point to it.
    The old `load_controller.py:257` anchor had drifted and is described by behaviour now
    rather than by line number.
  - **B4a's view_state sub-item (a) is open**, and nothing meters it: the
    Bar/Distribution/Scatter snapshot/apply blocks are still inline in `view_state.py`.
    Recorded explicitly, since only this file tracks it. Heatmap has joined Image Table in
    owning a `view_state_io.py`, which the baseline section now says.
  - **Stale "Critical files" instructions removed.** The list still told a future agent to
    add a `cache_projector` field to `feature_registry.py` (B1 deliberately used a
    standalone projector registry instead), still called `on_selection_changed` the last
    app-first hook (migrated), and still described `viewer_context.py` as holding 9
    accessors to extract (one left, by design). Each entry now states what is true now and
    which open phase, if any, will touch it.
  - **B5 confirmed not started** — no `data/image_repository.py`, no facade ratchet — and
    **B1b confirmed still ⊘ deferred**, which is the mechanism terminal condition 3 waits
    on.

- 2026-07-12 — **Cond. 3 metered + first conversion (34 → 33 core→feature imports).**
  - Added the **`CORE_FEATURE_IMPORT_BUDGET`** ratchet (seed **33**, down-only + `_not_slack`
    twin) in `tests/test_guardrails.py` — the function-local core→feature residual is now a
    metered budget that can only shrink, locking in progress on terminal condition #3.
  - Moved the shared `WELL_MIME` drag/drop token to a neutral `core/dnd` module so
    `tab_controller` no longer imports `features/heatmap` for a protocol constant (34 → 33).
  - Documented the two subclasses that need a service-lookup registry before they can be
    inverted (query-style feature calls; the `projector_registry`/`scope_registry` seams),
    so the ordered-hook epic (B1b) has a clear remaining scope.

- 2026-07-11 — **WORKSTREAM A COMPLETE + cond. 3 advanced (42 → 33 core→feature imports).**
  - **A5 (service-accessor extraction) COMPLETE — `_ALLOWED == {"window"}`.** Extracted
    the 8 remaining accessors into held collaborators: `persistence`/`features` →
    direct ctx fields (`3f0aa01`); `set_status`/`toast` → `NotificationService`
    (`ctx.notify`, `65ccb42`); `save_figure_dialog`/`open_export_style_panel` →
    `FigureService` (`ctx.figures`, `135fc14`); `rebuild_all`/`refresh_sidebar_map` →
    `SelectionCoordinator` (`ctx.coordinator`, `8904663`). `_window` is now reachable
    only as the `QMainWindow`-qua-widget — the terminal teardown state. Also hardened
    `__post_init__` to tolerate lightweight state stubs (fixes latent B2 test breaks).
  - **A2/A4 COMPLETE — `RAW_SELF_DELEGATOR_BUDGET == 0`** (`b20c100`). Deleted three
    now-dead delegators (`_ask_name_dialog`, `_review_norm_fov`, `_open_export_style_panel`
    — the last orphaned by A5c), renamed `_mute_color`'s misnamed `self` param (a
    `@staticmethod` false-positive), and flipped `drain_pending_centre_builders` to
    `ctx`. The four `__init__` runtime wirings stay (barplot/stats test-injection seam)
    but are not raw-`self` forwarders, so `== 0` holds.
  - **Terminal conditions 1, 2, 4, 5 now all hold and are guard-proven.**
  - **Cond. 3 (full `core/` feature-import decouple) started — redraw hook (`6c42f8a`).**
    Added a `redraw` descriptor hook + `FeatureRegistry.redraw(ctx, name)`; routed 8
    plot-render dispatches (plot_orchestrator / tab_controller / selection_controller /
    channel_state_controller) through it. **42 → 33** function-local core→feature imports.
    *Remaining 33 are heterogeneous and need per-category mechanisms (a genuine ordered-
    hook / decoupling design epic, exactly B1b's deferral rationale):* cell_gating
    handlers (7), grouping label-grid/tab/controller refreshes (7), heatmap sidebar_view
    **builder + `WELL_MIME` constant + drop-handler + refresh** (5), review_image renderer
    (2) + controller (1), heatmap.controller (2), export.style window-panel (2 — incl. the
    A5c `FigureService`), qc/crop/review post-load **ordered** cache projections in
    `load_controller` (interleaved with `persistence.load`, ordering-critical — needs the
    ordered post-load hook phase), `projector_registry` B1-wiring (2), preview builder (1),
    stats (1). Each is the sanctioned function-local escape hatch today
    (`test_layering`/`test_architecture_contracts` ban only module-scope, already green).
- 2026-07-11 — **B4 (persistence half) COMPLETE — `PERSIST_APP_PARAM_BUDGET` = 0.**
  Redid the reverted attempt surgically: migrated exactly the 23
  registry-facing entry points (`save_to_data_dir`/`load_from_data_dir`/`reset`/
  `save_notes`/`autosave_to_pipeline_info` across 11 modules) to `ctx`-first via the
  bridge — AST-verifying real `app` usage so ctx-native `view_state` internals were
  left untouched — and made `PersistenceRegistry` hold + pass `ctx`
  (`build_persistence_registry(self._ctx)`, `_vs.reset(self._ctx)`). Shaped the 6
  persistence test files' mocks to double as their own ctx (`.window`+`.state`) or
  pass `loaded_app._ctx`; fixed a **pre-existing** latent test bug
  (`labels_apply_affix(app)` → `app._ctx`). New `test_persistence_is_ctx_native`
  ratchet. app_smoke (dataset load/save round-trip) + persistence suite green.
  *(The load-path-convergence sub-item — routing the eager bulk load through
  `repo.load_well` — remains open.)*
- 2026-07-11 — **A3 COMPLETE — `APP_FIRST_FUNC_BUDGET` = 0.** Final clusters:
  channel_state_controller (bridge), ui_helpers empty-state/batch-export hosts +
  leaf renderers (playground/review-image/qc/image-browser/status) + the
  construction builder chain (build_ui/sidebar/centre/preview + wire_event_bus/
  register_default_scopes) + export/style — the genuine window/widget helpers were
  renamed `app`→`win` (the QMainWindow is their legitimate endpoint), the logic
  functions bridged to `ctx`. **Every free function in `core/`+`features/` is now
  either `ctx`-first or takes a typed `win`/`host` — zero god-object-first
  signatures.** `RAW_SELF_DELEGATOR_BUDGET` 38→5 along the way (A2 substantially
  done; the residue are window-forwarding delegators that A5 removes).
- 2026-07-11 — **A3 — registry `on_selection_changed` hooks (the "last app-first hook"):**
  migrated the 8 `_sel_*` hooks in `features/registry.py` to `ctx`-first (bridge),
  flipped the 2 `desc.on_selection_changed(app)` dispatch sites in
  `selection_controller` to `ctx`, and corrected the stale `feature_registry`
  docstring ("every hook takes `app`" → `ctx`). Now **every** `FeatureDescriptor`
  hook is ctx-native. `APP_FIRST_FUNC_BUDGET` 34→26.
- 2026-07-11 — **A3 — load_controller cluster:** migrated the 9 `app`-first funcs in
  `core/load_controller.py` (load_directory, load_path(_shim), the opening-dialog
  helpers, reset/kick-off gating, build_tok_to_label) to `ctx`-first via the
  `app = ctx.window` bridge; fixed the `load_path_shim` / `_load_directory_controller`
  delegators (`self._ctx`) and the `_reset_gating_state` unit test's mock.
  `APP_FIRST_FUNC_BUDGET` 43→34. app_smoke (dataset load) + frozen-ctx + guards green.
- 2026-07-11 — **A3 — selection_controller cluster (largest):** migrated all 25
  `app`-first funcs in `core/selection_controller.py` to `ctx`-first via the
  `app = ctx.window` bridge (incl. the A5-targeted `rebuild_all` /
  `refresh_sidebar_map_now` — bridging now doesn't block their later extraction).
  Threaded internal cross-calls, the `SelectionRefreshCoordinator`'s
  `self._app._ctx` call, the 7 `partial(on_sidebar_plate_*, app)` sidebar wirings +
  the deselect-all `select_none` closure (through `app._ctx`), and the 4 runtime_app
  delegators (`self._ctx`). The now-working frozen-ctx guard enumerated the missed
  direct sites. `APP_FIRST_FUNC_BUDGET` 68→43.
- 2026-07-11 — **A3 — split_view cluster:** migrated the 12 `app`-first funcs in
  `core/split_view.py` (enter/exit/focus split, focus-filter install, toggle/combo
  handlers, nav-intercept, install_split_view) to `ctx`-first via the
  `app = ctx.window` bridge; threaded internal cross-calls, the `_SplitFocusFilter`
  class's `self._app._ctx` calls, the install-time closures, and the two external
  callers (`app_shell_view`, `load_controller`) through ctx. Updated 3 split test
  files to pass `app._ctx`. `APP_FIRST_FUNC_BUDGET` 80→68.
- 2026-07-11 — **A3+A2 — tab_controller cluster:** migrated the 8 `app`-first
  dispatch/nav funcs in `core/tab_controller.py` (notebook/section-nav/tab-history/
  sidebar-visibility/central-pane/build-centre) via the `app = ctx.window` bridge,
  threaded internal cross-calls + the `partial(on_notebook_current_changed, …)`
  wiring through ctx, and flipped 7 runtime_app delegators to `self._ctx`.
  `APP_FIRST_FUNC_BUDGET` 88→80, `RAW_SELF_DELEGATOR_BUDGET` 21→14.
- 2026-07-11 — **A3 — plot_orchestrator save-figure cluster:** migrated the 6
  `app`-first save-figure funcs in `core/plot_orchestrator.py` to `ctx`-first
  (`app = ctx.window` bridge; cross-calls threaded through ctx) and flipped the 5
  runtime_app save delegators to `self._ctx`. `APP_FIRST_FUNC_BUDGET` 94→88.
- 2026-07-11 — **A3+A2 — ui_helpers notification/theme cluster:** migrated 8
  `app`-first funcs in `core/ui_helpers.py` (`toast`, `show_progress`,
  `step_progress`, `toggle_log`, `apply_theme`, `on_theme_change` via the
  `app = ctx.window` bridge; `norm_timepoint` clean ctx — already ctx-called
  everywhere; `set_widget_text` → generic `host` param). Flipped 7 `runtime_app`
  delegators to `self._ctx`. `APP_FIRST_FUNC_BUDGET` 102→94,
  `RAW_SELF_DELEGATOR_BUDGET` 28→21. app_smoke + guards green.
- 2026-07-11 — **A3 — heatmap-sidebar cluster:** migrated the 5 `app`-first funcs in
  `features/heatmap/sidebar_view.py` to `ctx`-first (helpers were already ctx-native;
  `build_heatmap_layout_sidebar` uses the `app = ctx.window` bridge for widget
  building). Updated the two core callers (`core/sidebar_view`, `selection_controller`)
  to pass `app._ctx`. `APP_FIRST_FUNC_BUDGET` 107→102. Heatmap tests + smoke + guards green.
- 2026-07-11 — **A3+A2 — stats & grouping clusters:** migrated the 11 `app`-first
  funcs in `features/stats/controller.py` and the 2 in `features/grouping/controller.py`
  to `ctx`-first (clean ctx for state readers; `app = ctx.window` bridge for
  widget/figure-heavy `run_stats`/`draw_ks_cdf`/`stats_refresh_colors`), and flipped
  their `runtime_app` delegators to forward `self._ctx` — which also drops them from
  the raw-`self` count. `APP_FIRST_FUNC_BUDGET` 120→107, `RAW_SELF_DELEGATOR_BUDGET`
  38→28. Stats tests (46) + grouping colors + guards green.
- 2026-07-11 — **A3 (partial) — playground cluster:** migrated the 15 `app`-first
  functions in `features/image_browser/playground/controller.py` to `ctx`-first
  (widget-I/O helpers take `win=ctx.window`; frame-key/ref-bridge/pan-drag moved to
  a new `ctx.runtime.playground` namespace in `AppRuntime`). Updated tab_view +
  test call sites. `APP_FIRST_FUNC_BUDGET` 135→120. Playground tests + ctx guards green.
- 2026-07-11 — **B1b deferred:** load-ordering constraints make the
  `on_dataset_loaded` hoist higher-risk-than-value; the function-local core→feature
  imports are the sanctioned escape hatch (module-scope ban already green). Rationale
  in the B1b section.
- 2026-07-11 — **B3 landed:** routed the last single-well cache read
  (`stats_compute._safe_rows`) through `repo.rows_or_none`; `REPO_BYPASS_BUDGET`
  1→0. Mock-path stats tests (112) + app_smoke green (real repo path).
- 2026-07-11 — **B2 landed:** `WellRepository` read facade
  (`data/well_repository.py`) over `get_rows` + `DatasetState`, attached as
  `ctx.state.repo` via `ViewerContext.__post_init__` (+ `AppState.repo` field).
  Guards: `test_well_repository` + `test_repo_bypass` (`REPO_BYPASS_BUDGET=1`,
  spatial allowlisted). app_smoke + ctx-freeze + line budget green.
- 2026-07-11 — **B1 landed:** cache-projector registry inverts the
  `get_rows`→`features/qc`+`crop` layer violation. New `data/cache_projectors.py`
  + `core/projector_registry.py` (wired in `bus_wiring`); `get_rows` iterates
  registered projectors, no `features/` import. New guard
  `test_data_layer_no_feature_imports` green; 46 QC/Crop/aggregate/smoke tests green.
  (Used a projector registry, not a `with_hook` descriptor hook — QC/Crop are
  support libs without descriptors.)
- 2026-07-11 — **A1 landed:** removed 10 widget-handle `None`-seeds from
  `runtime_app.__init__` (builders register them via `WidgetRegistry`; readers
  already use `get_or_none`/`has`/`getattr(...,None)`). Attrs 23→13,
  `RUNTIME_APP_ATTR_BUDGET` 68→15, line budget 1298→1296. app_smoke + widget/registry
  tests green.
- 2026-07-11 — **A0 landed:** burn-down ratchets added (test-only).
- 2026-07-11 — Rebased onto `main` after build-time-feature-profiles merged (PR #639);
  baseline references updated branch → `main`.
- 2026-07-11 — Roadmap authored + goal-mode framing added; both workstreams scoped;
  no product code yet.
