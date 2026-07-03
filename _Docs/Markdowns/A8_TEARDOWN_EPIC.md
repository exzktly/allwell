# A8-Teardown Epic — Retire `ctx.app` by decomposing `WellViewerApp`

> Successor to the A8 burn-down in `CODE_REVIEW_AUDIT.md`. That audit drove
> `ctx.app` from **894 → 293 (67%)** — every reduction achievable *without*
> restructuring the runtime. This epic takes the remaining **293 → 0** and
> deletes the transitional `app` field from `ViewerContext`.
>
> **Status:** proposed / not started. **Owner:** _tbd_. **Gate:** merge PR #597 first.

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

## The residual 293, categorized (evidence-backed)

| # | Category | ~refs | Technique | Workstream |
|---|----------|------|-----------|------------|
| a | Runtime-object stashes — smFISH worker bridge / extractor / figures / current arrays, scatter gate state, image-table cells | 60–80 | Per-feature **runtime holders** (live-object namespaces, like AppState slices but non-persisted) | **T2** |
| b | Orchestration functions pinned to `app` by the controller layer | ~150 | **Invert via EventBus** (T1) + **thread `ctx` through the controllers** (T3) | **T1, T3** |
| c | Cross-cutting app operations — `_invalidate_stats_cache` (called app-wide), `_refresh_review_csv_rows`, channel/axis `_on_*` handlers | ~50 | **Extract collaborators** / **bus events** (`stats_cache` is already a typed slice) | **T4** |

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

### T4 — Extract cross-cutting services · `[ ]`
Category (c). `_invalidate_stats_cache` operates on the already-typed
`stats_cache` slice → replace the ~20 scattered calls with a
`bus.plot_options_changed`/dedicated invalidation event, or a small `StatsCache`
collaborator held on `ctx`. `_refresh_*` become feature controllers reacting to
bus events. `_on_plot_channel_selected` / axis handlers fold into the
`channel_changed` signal.
- **Depends on:** T1 for the event route.

### T5 — Saved-plots / capture decomposition · `[ ]`
The `wire_save_to_gallery` → `save_plot_from_card` → `get_store` / `capture_params`
/ `build_summary` seam becomes a `SavedPlotsService` bound to `ctx`. Deepest
single cluster; own sub-plan.

### T6 — Retire `ctx.app` · `[ ]`
When the count reaches the last handful, delete the `app` field from
`ViewerContext`, flip the ratchet from "≤ N" to "== 0 / hard ban", and remove the
strangler docstring. The named-accessor set (frozen) is the *entire* remaining,
intentional surface between features and the window shell.

---

## Progress

| ID | Workstream | Depends on | Risk | Status | Metric |
|----|------------|-----------|------|--------|--------|
| T1 | Wire the EventBus | — | Med | `[x]` | already in place (state→redraw paths); per-control redraws are T3, not bus |
| T2 | Feature runtime holders | — | Low | `[x]` | 293 → 249 (all pure-runtime writes; persisted fields excluded) |
| T3 | Thread ctx through controllers | T2 | High | `[ ]` | 249 → 0 — the remaining core; one feature per PR, interactive-validated |
| T4 | Extract cross-cutting services | T1 | Med | `[ ]` | |
| T5 | Saved-plots service | — | Med | `[ ]` | |
| T6 | Retire `ctx.app` | T1–T5 | Low | `[ ]` | → 0 |

Suggested order: **T2 ∥ T1 → T4 → T3 (feature by feature) → T5 → T6.**
(T2 is a safe parallel warm-up; T1 unlocks T3/T4; T6 is the capstone.)

## Ratchet evolution

- `test_ctx_app_burndown.py` keeps counting down from 293.
- **New guards:** (1) every EventBus signal has a subscriber + catalogue capped at
  five; (2) runtime holders untouched by `persistence/`; (3) controllers hold
  `ctx`, not `app` (a layering assertion); (4) at T6, `ViewerContext.app` removed
  and the count is pinned at 0.

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

**Last updated:** 2026-07-02
