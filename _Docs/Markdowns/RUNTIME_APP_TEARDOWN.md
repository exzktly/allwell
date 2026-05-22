# `runtime_app.py` God-Object Teardown — Plan & Progress

> **Living document.** This is both the plan for completing the `runtime_app.py`
> teardown and the running progress tracker. Every PR in the teardown updates
> the **Progress** table and the **Redundancy scoreboard** below. It is the
> single source of truth for "where are we."

## Context

`well_viewer/runtime_app.py` was a 6,909-line god-object (`WellViewerApp`). A
multi-phase teardown was started but only **half-completed**: the typed-state
extraction landed, but the decoupling layer (event bus, dependency injection,
file collapse) was scaffolded and then abandoned — new infrastructure that isn't
load-bearing sitting beside an unshrunk monolith.

This effort finishes the teardown so `runtime_app.py` becomes a ~600–800-line
composition root, the `EventBus` actually drives redraws, and controllers depend
on typed seams (`ViewerContext` / state objects) instead of the god-object.

A **co-equal goal is the elimination of redundancy** — duplicate dispatch,
defensive probing, hand-maintained field lists, dead code, and additive
double-code-paths the god-object accreted. Relocating a method without
collapsing the duplication around it is explicitly *not* success.

## Progress

Status: ⬜ not started · 🔄 in progress · ✅ merged

| Phase | Step | PR | Status | `runtime_app.py` lines | Notes |
|---|---|---|---|---|---|
| — | Baseline (`main` @ `7f03297`) | — | — | 6,909 | audited starting point |
| 1 | Helper extraction + dead-code deletion | #271 | 🔄 in progress | 6,628 | moved 6 helpers to `ui_helpers.py`; deleted 5 dead-code blocks; collapsed 2 redundant wrappers |
| 2 | Bus regression harness + catalogue cleanup | #271 | 🔄 in progress | 6,628 | new `test_redraw_fanout.py` (one-redraw-per-action harness); `event_bus` trimmed to 5 signals + documented contract; 3 ratcheting redundancy guardrails added |
| 3a | Bus load-bearing — redraw signals | #271 | 🔄 in progress | 6,631 | new `redraw_coordinator.py`; `channel_changed` + `plot_options_changed` drive redraws via a real subscriber, paired direct calls removed. `_toggle_sem` kept off the bus until Phase 9 (its redraw covers Distribution / Heat Map, which the coalescer omits) |
| 3b | Bus load-bearing — `selection_changed` | — | ⬜ | — | |
| 3c | Bus load-bearing — `dataset_loaded` | — | ⬜ | — | |
| 4 | `PersistenceRegistry` + field-list collapse | — | ⬜ | — | |
| 5 | `WidgetRegistry` adoption | — | ⬜ | — | one PR per builder file |
| 6 | DI: plot controllers take `ctx` | — | ⬜ | — | one PR per controller |
| 7 | DI: remaining controllers take `ctx` | — | ⬜ | — | one PR per controller |
| 8 | Relocate `_build_*` into `views/` | — | ⬜ | — | 3–4 PRs by file |
| 9a | Relocate `_on_*`/`_redraw_*` into controllers | — | ⬜ | — | one PR per section |
| 9b | `_on_tab_change` → `TabRegistry` | — | ⬜ | — | |
| 10 | Composition root + finalize | — | ⬜ | — | drop `__getattr__` / `ctx.app` |

## Redundancy scoreboard

Updated by every PR. Each metric may only fall. The first three are enforced by
ratcheting counters in `test_guardrails.py` as of Phase 2.

| Metric | Baseline | Current | Target | Enforced |
|---|---|---|---|---|
| `getattr`/`hasattr` probes in `runtime_app.py` | 207 | 207 | ~0 | ✅ guardrail |
| Persistence save/load wrapper methods | 10 | 10 | 0 | ✅ guardrail |
| `app = ctx.app` unwraps in tab builders | 12 | 12 | 0 | ✅ guardrail |
| Additive bus double-paths (emit + direct redraw) | 5 | 2 | 0 | `test_redraw_fanout` |
| `_on_tab_change` dispatch branches | 8 | 8 | 0 | — |

Dead code / redundancy eliminated outside the tracked metrics:
- **Phase 1** — deleted 5 dead-code blocks (`save_json_file`, `load_json_file`,
  `_Tooltip`, `BatchSubset`, `_SubsetEntry` — defined, never used) and collapsed
  2 redundant wrappers (`make_scrollable_canvas`, `ask_name_dialog`) that merely
  re-wrapped `ui_helpers` equivalents.
- **Phase 2** — trimmed the `EventBus` catalogue from 7 signals to 5, deleting
  the two unused signals (`threshold_changed`, `status_message`); added the
  three redundancy guardrails above so every later phase is held to the goal.
- **Phase 3a** — made the `EventBus` load-bearing for the redraw-only signals
  `channel_changed` and `plot_options_changed`: new `redraw_coordinator.py`
  subscribes both to the scope coalescer; the paired direct
  `redraw_scopes_or_defer` calls were removed from `set_active_channel` and
  `set_fold_change_state`. `_toggle_sem` (the SEM/SD toggle, the other
  `plot_options_changed` emitter) was taken *off* the bus — its redraw covers
  Distribution / Heat Map, tabs the coalescer does not, so it keeps its direct
  redraws until Phase 9 relocates it to a controller. Additive double-paths
  5 → 2 (only `selection_changed` and `dataset_loaded` remain).

## Guiding rules

1. One PR per phase-step; app works + full `pytest tests/` green after each.
2. The guardrail ratchet only moves down — every line/attr-removing PR lowers
   the budgets in `tests/test_guardrails.py`.
3. Behaviour moves, not just data — a relocated controller reads `ctx.state`,
   never `app`.
4. The bus only *triggers*; the existing `redraw_scopes_or_defer` coalescer
   (`fold_change_scopes.py:114`) stays the *throttle*. Never reimplement
   coalescing.
5. Bus migration is add-subscriber → prove parity → delete the direct call, in
   *separate commits* — never add and delete together.
6. Delete `getattr`/`hasattr` probes as each domain gains guaranteed DI.
7. **Every PR keeps all three docs current** — this doc (progress + scoreboard),
   `ARCHITECTURE.md` (the §5.x sections the phase touched, matching the landed
   code — no "in progress" lag), and `README_WellViewer.md` (reviewed every PR,
   updated only if user-facing behaviour changed; the PR states the outcome).
8. Collapse, don't relocate — a PR that moves a method must also collapse the
   duplication around it. Moving N copies of a pattern is a regression.

## Phases

Ordering rationale: `WidgetRegistry` adoption + DI narrowing come **before** body
relocation, so each `_build_*`/`_on_*` method is rewritten **once** (final shape,
reads `ctx`), not twice.

### Phase 1 — Finish helper extraction *(shrinks file)*
Move the `self`-free module-level helpers from `runtime_app.py` into the existing
`well_viewer/ui_helpers.py` (`make_fluor_thumb`, `make_overlay_thumb`,
`safe_int_or_none`, `bind_drag`, `wells_multiselect_listbox`,
`selected_list_values`); delete dead code; collapse redundant wrappers. Create
this document. Lower the line budget.

### Phase 2 — Bus regression harness + catalogue cleanup *(flat)*
- New `tests/test_redraw_fanout.py` — counters on the scope redraw methods
  assert exactly one redraw per visible scope per user action.
- `event_bus.py` — keep `channel_changed`, `selection_changed`,
  `dataset_loaded`, `plot_options_changed`, `redraw_requested(str)`; drop
  `threshold_changed` (folds into `channel_changed`) and `status_message`
  (`status_signal.py` owns it).
- Add the ratcheting redundancy-metric counters to `test_guardrails.py`.

### Phase 3 — Make the EventBus load-bearing *(flat)* — 3 sub-PRs
Per signal: add subscriber → prove parity with `test_redraw_fanout` → delete the
direct call, in *separate commits*. Synchronous Qt connections.
- **3a** — redraw-only signals (`channel_changed`, both `plot_options_changed`):
  new `well_viewer/redraw_coordinator.py` subscriber calls `redraw_scopes_or_defer`.
- **3b** — `selection_changed`: extract `_refresh_after_selection_change`'s body
  into `apply_selection_refresh(app)` in `selection_controller.py`; dedicated
  subscriber (not `redraw_coordinator`); characterization-test each tab.
- **3c** — `dataset_loaded`: lowest frequency, last.

### Phase 4 — PersistenceRegistry + field-list collapse *(shrinks attrs)*
Replace the 10 save/load wrapper methods and 5 `_*_save_pending` debounce flags
with one `well_viewer/persistence/registry.py::PersistenceRegistry`. Drive each
persistence module's save/load from `dataclasses.fields()` / `asdict()` so the
field list exists once.

### Phase 5 — WidgetRegistry adoption *(flat)*
Migrate the ~60 `app._<widget> = handle` writes in `tabs/*` and `views/*`
builders to `ctx.widgets.register(...)`. One PR per builder file.

### Phase 6 — DI: plot controllers take `ctx` *(flat)*
`lineplot`/`barplot`/`scatter`/`distribution`/`heatmap` controllers +
`plot_orchestrator`: `(app, …)` → `(ctx, …)`; delete the `app = ctx.app` unwrap
in the matching tab builder; delete that controller's probes. One PR each.

### Phase 7 — DI: remaining controllers take `ctx` *(flat)*
Same for `load_controller`, `channel_state_controller`, `selection_controller`,
`grouping_controller`, `image_table_controller`.

### Phase 8 — Relocate `_build_*` into `views/` *(shrinks file)*
The 16 `_build_*` methods → `views/` modules as `build_x(ctx, parent)`. 3–4 PRs
grouped by destination file.

### Phase 9 — Relocate behaviour + TabRegistry *(shrinks file)*
- **9a** — `_on_*`/`_redraw_*`/`_refresh_*` bodies → controllers, one section per
  PR, biggest first (Bar ~1,320 lines; Review-image ~620; Scatter ~550;
  Montage/preview ~500; Image Table ~250; Statistics ~235).
- **9b** — `_on_tab_change` → `TabRegistry` (only the refresh bodies move; the
  sidebar-frame `setVisible` calls stay in `WellViewerApp`).

### Phase 10 — Composition root + finalize *(shrinks file)*
Delete the `__getattr__` forwarder and `ctx.app`; `runtime_app.py` → ~600–800
lines; final `ARCHITECTURE.md` / `README_WellViewer.md` reconciliation; set
`test_guardrails.py` to final budgets.

## Guardrails

- `RUNTIME_APP_LINE_BUDGET` (ratchets down), `RUNTIME_APP_ATTR_BUDGET`, and the
  `state/` import contract — all hard-fail in `tests/test_guardrails.py`.
- Phase 2 adds ratcheting redundancy-metric counters; Phase 3 adds a `ctx.app`
  counter ratcheted toward zero.
- **Ratchet realism:** only Phases 1, 4, 8, 9, 10 genuinely shrink
  `runtime_app.py`; Phases 2, 3, 5, 6, 7 are coupling-only (their work lands in
  other files).

## Verification

- **Per PR:** full `pytest tests/` green; characterization snapshots unchanged;
  `test_redraw_fanout` green from Phase 2 onward.
- **Manual golden path:** `python all_well.py --data_dir <dataset>` — open a
  dataset, drag-select wells, switch channels, toggle fold-change, visit every
  tab; one user action → one redraw per visible tab.
- **Redundancy + documentation check:** the redundancy guardrails hold; all
  three docs are current.

## Branch & PR conventions

All work on `claude/analyze-runtime-app-refactoring-K8Mis`. One PR per
phase-step (~22–26 PRs). Each PR: code + tests + guardrail-budget update +
three-doc sync. The app works after every PR — the user can pause after any
phase.
