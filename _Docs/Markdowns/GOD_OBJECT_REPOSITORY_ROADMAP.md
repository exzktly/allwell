# Roadmap: God-Object Teardown + Repository Introduction (`well_viewer/`)

> **Living document.** Keep this updated as phases land — tick items off, lower
> the seeded ratchet numbers as they ratchet down, and adjust file:line anchors
> when they drift. It is the tracking home for the two workstreams below.
>
> **Baseline:** `main` — the build-time-feature-profiles work merged to `main`
> via PR #639, so this roadmap now builds directly on `main`. Last revised:
> 2026-07-11.
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
| B4 | One load path; `PersistenceRegistry` `ctx`-native | `PERSIST_APP_PARAM_BUDGET == 0`; app_smoke round-trip green |
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
  shrank ~274 lines; Image Table now owns its snapshot/restore in
  `features/image_browser/image_table/view_state_io.py` behind the `view_state_io`
  hook, and the *entire* live view-state pipeline (`snapshot(ctx)`/`restore(ctx)`/
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
  many direct `from well_viewer.features…` imports (qc/crop/review_image/grouping/
  heatmap at `:292,308-310,318,333,378`) *not* yet on the hook seam; Sample
  Definitions still routes through an app-bridge god-seam (`registry.py:108-116`);
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
5. **Sample Definitions app-bridge god-seam** (`registry.py:108-116`) — the residual
   place a feature routes through the god-object; migrate onto the same `ctx`/hook
   mechanisms once its callback targets (`_groups_centre_refresh`, `_redraw`, …) are
   `ctx`-native (couples to A5's `NotificationService`/`SelectionCoordinator`).
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
`load_controller.py` still imports qc/crop/review_image/grouping/heatmap directly
(`:292,308-310,318,333,378`) — the post-load work not yet on the `on_dataset_loaded`
seam the baseline introduced. Migrate each to an `on_dataset_loaded(ctx)` hook on its
feature descriptor (the mechanism already exists and is proven for Image Table +
smFISH), so `load_controller` names **no** feature and imports none a profile excluded.
- Guard: extend `test_data_layer_no_feature_imports` scope to assert `load_controller`
  (and ideally all of `core/`) carries no module-scope `features.*` import beyond the
  sanctioned `features/registry.py` — this already partly holds via
  `test_architecture_contracts.py::test_core_does_not_import_features_at_module_scope`;
  tighten to catch the function-local ones this phase removes.

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
- Eager bulk path `load_controller.py:257` (`app._load_well_csv` + bulk
  `recompute_qc_all`) becomes `repo.load_well(key, path)` = `load_well_csv` + the
  **registered projectors from B1**, unifying lazy and eager so QC/Crop projection
  runs through one code path. Keep the post-loop `recompute_qc_all` (it also
  invalidates the stats cache + bumps `override_version`).
- Make `PersistenceRegistry` take `ctx`/`state`, not `app`. Bodies mostly already
  deref `app._app_state`(==`ctx.state`); follow the direction `_doc.py` /
  `cell_gating.py` already set. Convert the ~10 clean domains to `save(ctx)/load(ctx)`
  first; leave the callback-coupled `sample_definitions.py` (`app._set_status`,
  `_groups_centre_refresh`, `_plot_groups_refresh`, `_invalidate_stats_cache`,
  `_redraw`) and `view_state.reset` until their god-methods are `ctx`-native
  (WS-A A3/A5). **This directly enables A5's trivial `persistence` accessor.**
- **view_state is already ~80% done on the baseline** — do not redo it. Internals are
  fully `ctx`-native and Image Table owns its snapshot/restore behind the
  `view_state_io` hook. Remaining, narrow work: (a) move the still-inline
  Bar/Distribution/Scatter blocks in `view_state.py` (`_snapshot_bar`, `_apply_scatter`,
  …) behind the **same `view_state_io` hook** (one descriptor factory per feature —
  reuse, don't invent); (b) convert the three outer boundary functions
  `save_to_data_dir(app)`/`load_from_data_dir(app)`/`reset(app)` to `ctx`.
- Guard: existing `RUNTIME_APP_PERSIST_WRAPPER_BUDGET = 0` holds; add
  `PERSIST_APP_PARAM_BUDGET` counting `save_to_data_dir(app)` signatures (seed ~12).

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
A5 (accessor extraction) ◄──── B4 (load converge + persistence→ctx; view_state ~80% pre-done)
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
- `well_viewer/core/feature_registry.py` — `FeatureDescriptor` + `with_hook`; add the
  `cache_projector` field (B1). **This registry is the reuse seam for B1/B1b/A3.**
- `well_viewer/features/registry.py` — descriptor hook factories; `on_selection_changed`
  (last app-first hook, A3), Sample Definitions app-bridge `:108-116` (A3.5), QC/Crop
  `cache_projector` factories (B1)
- `well_viewer/data/data_loading.py` — `get_rows` layer inversion via `with_hook` (B1)
- `well_viewer/core/load_controller.py` — residual feature imports → `on_dataset_loaded`
  hooks (B1b); load-path convergence (B4)
- `well_viewer/persistence/view_state.py` + `features/*/view_state_io.py` — Bar/Dist/
  Scatter blocks → `view_state_io` hook; `save/load/reset(app)`→`ctx` (B4)
- `well_viewer/viewer_context.py` — the 9 accessors to extract (A5)
- `well_viewer/runtime_app.py` — delegators (A2), `__init__` reach-throughs (A4),
  widget attrs (A1)
- `tests/test_guardrails.py` + `tests/test_ctx_service_accessors.py` — where every new
  ratchet + accessor-freeze edit lands; `tests/test_feature_profile.py` — the
  boot-under-subset-profile guard idiom to extend for B1/B1b
- Supporting: `well_viewer/core/dataset_state.py` (repo aggregate root, B2),
  `well_viewer/persistence/registry.py` (app→ctx, B4)

---

## Progress log (goal-mode status board)

Single source of truth for "what's left." Statuses: ☐ not started · ◐ in progress ·
☑ done (Def-of-Done guard green). Goal mode picks the lowest-ID ☐ phase whose
ordering constraints are met.

| Phase | Status | Def-of-Done guard | Landed (commit / date) |
|---|---|---|---|
| A0 · burn-down ratchets | ☑ | ratchets present + seeded (38 / 135) | 2026-07-11 |
| A1 · widget attrs → registry | ☑ | `RUNTIME_APP_ATTR_BUDGET` 68→15 (actual 13) | 2026-07-11 |
| A2 · retire raw-`self` delegators | ☐ | `RAW_SELF_DELEGATOR_BUDGET == 0` | — |
| A3 · `app`→`ctx` clusters | ☐ | `APP_FIRST_FUNC_BUDGET == 0` | — |
| A4 · `__init__` reach-throughs | ☐ | 4 lines removed | — |
| A5 · service-accessor extraction | ☐ | `_ALLOWED == {"window"}` | — |
| B1 · cache-projector inversion | ☑ | `test_data_layer_no_feature_imports` green | 2026-07-11 |
| B1b · load_controller decouple | ☐ | core-scope no-feature-imports green | — |
| B2 · `WellRepository` facade | ☑ | facade test + `REPO_BYPASS_BUDGET`=1 seeded | 2026-07-11 |
| B3 · route cache readers | ☑ | `REPO_BYPASS_BUDGET == 0` (−spatial) | 2026-07-11 |
| B4 · load converge + persistence→ctx | ☐ | `PERSIST_APP_PARAM_BUDGET == 0` | — |
| B5 · image repo facade *(optional)* | ☐ | image-facade ratchet == 0 | — |

**Changelog** (newest first):

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
