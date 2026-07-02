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

### T1 — Wire the EventBus (foundation) · `[ ]`
Connect the five existing signals: the controllers **publish** state-carrying
events; features **subscribe** in their builders (via `ctx.bus`). Convert the
coordinator's imperative `redraw_*_orchestrate(app)` calls into
`bus.redraw_requested.emit(scope)` with each feature subscribing for its scope.
- **Unblocks:** most of category (b) — a feature reacts to a typed event instead
  of being called with `app`.
- **Guardrail:** a test asserting every emitted signal has ≥1 subscriber and the
  catalogue stays capped at five (no new-god-object drift).
- **Risk:** Med — signal/slot **lifetime** (disconnect on teardown; cf. audit L4
  `focusChanged` leak). Mitigate with a subscription helper that auto-disconnects.

### T2 — Feature runtime holders · `[ ]`
For category (a): introduce a per-feature **runtime** dataclass for live,
non-persisted objects (workers, extractors, matplotlib figure/canvas handles,
gate geometry, decoded arrays), attached as `ctx.runtime.<feature>` (parallel to
`ctx.state.<feature>`, but explicitly *not* serialized). Migrate
`ctx.app._smfish_*` / `ctx.app._scatter_gate*` / `ctx.app._image_table_cells`
reads onto it.
- **Independent of T1/T3** — can run in parallel; low architectural risk.
- **Guardrail:** runtime holders are never touched by `persistence/` (a layering
  test), so they can't silently become persisted state.

### T3 — Thread `ctx` through the controller layer · `[ ]`
The core of the epic. `plot_orchestrator` / `selection_controller` /
`channel_state_controller` / `tab_controller` take or hold `ctx` and pass it to
feature functions; migrate each orchestration function's body to
`ctx.state`/`ctx.widgets`/`ctx.bus` and named accessors. Do it **feature by
feature behind that feature's slow suite**, leaning on T1 to have already removed
the hottest imperative calls.
- **Depends on:** T1 (fewer direct calls to convert).
- **Risk:** High — app-wide; this is where regressions hide. Mitigate: one feature
  per PR, characterization tests first, `app._ctx` as the transitional bridge at
  call sites that haven't converted yet (never inside a migrated body).

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
| T1 | Wire the EventBus | — | Med | `[ ]` | 293 → _tbd_ |
| T2 | Feature runtime holders | — | Low | `[ ]` | |
| T3 | Thread ctx through controllers | T1 | High | `[ ]` | |
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
