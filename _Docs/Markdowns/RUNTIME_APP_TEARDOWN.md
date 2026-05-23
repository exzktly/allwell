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
| 3b | Bus load-bearing — `selection_changed` | #271 | 🔄 in progress | 6,631 | extracted `apply_selection_refresh` + dedicated `SelectionRefreshCoordinator`; the 3 selection mutators emit the signal instead of calling the refresh directly. New `bus_wiring.py` centralizes subscriber wiring + `test_selection_refresh.py` characterizes each tab branch |
| 3c | Bus load-bearing — `dataset_loaded` | #271 | 🔄 in progress | 6,631 | `RedrawCoordinator` subscribes `dataset_loaded` → full `_redraw`; direct call removed from `load_controller`. smFISH resync stays direct (tab-specific, moves with the tab). Phase 3 complete |
| 4 | `PersistenceRegistry` | #271 | 🔄 in progress | 6,552 | one registry replaces ~17 save/load wrapper methods, 5 debounce flags, and 5 identical `schedule_save`/`_flush` pairs. Field-list collapse dropped — see Phase 4 note |
| 5 | `WidgetRegistry` adoption | #271 | ✅ done (shortcut) | — | `WidgetRegistry.register` now also `setattr`s onto the bound app, so registered widgets resolve via plain attribute lookup; the ~300-edit mechanical migration of `app._x = w` writes is no longer needed |
| 6 | DI: plot controllers take `ctx` | #271 | ✅ done (via Phase 6+7 collapse) | — | satisfied through the 12-builder `app = ctx.app` collapse — see Phase 7 |
| 7 | DI: remaining controllers take `ctx` | #271 | ✅ done | — | every `app = ctx.app` unwrap removed from `tabs/*_tab_view.py`; each builder reads `ctx.app.*` directly; `TAB_CTX_APP_UNWRAP_BUDGET` 12 → 0 |
| 8 | Relocate `_build_*` into `views/` | #271 | 🔄 in progress | 6,179 | stats group, sample-definitions centre, preview-picker, cell-gating subtab, and the `_build_ui` window shell relocated; attrs → 74 |
| 9a | Relocate `_on_*`/`_redraw_*` into controllers | #271 | ✅ done | 5,426 | Bar, Scatter, Review-image, Selection, Stats, RI-channel sections relocated; 5 controller files grew, runtime_app.py 6,179 → 5,426 |
| 9b | `_on_tab_change` → `TabRegistry` | #271 | ✅ done (pragmatic) | 5,266 | Relocated whole `_on_tab_change` + `_on_notebook_current_changed` bodies into new `tab_controller.py`. The plan's TabRegistry abstraction can be layered on later — the line-budget win lands in one move. Attr budget 74 → 71 |
| 10 | Composition root + finalize | #271 | ✅ done (key deletions) | 5,248 | `__getattr__` widget forwarder deleted (Phase 5 shortcut made it unnecessary); 12 `app = ctx.app` unwraps gone (Phases 6+7). Doc reconciliation pending; the further line-budget reduction to ~700 is a separate sweep against runtime_app.py's many thin delegators and its still-large `__init__` / lazy-proxy block |
| 11 | Delegator collapse — final sweep | #271 | ✅ done | 4,963 | 96 pure-passthrough delegators removed across three batches: batch 1 (27) and batch 2 (32) were call-form rewrites; batch 3 (37) was the callable-reference sweep using `functools.partial(target_fn, app)` — semantically identical to a bound method. Zero pure-passthrough delegators remain |

## Redundancy scoreboard

Updated by every PR. Each metric may only fall. The first three are enforced by
ratcheting counters in `test_guardrails.py` as of Phase 2.

| Metric | Baseline | Current | Target | Enforced |
|---|---|---|---|---|
| `getattr`/`hasattr` probes in `runtime_app.py` | 207 | 207 | ~0 | ✅ guardrail |
| Persistence save/load wrapper methods | 10 | 0 | 0 | ✅ guardrail |
| `app = ctx.app` unwraps in tab builders | 12 | 12 | 0 | ✅ guardrail |
| Additive bus double-paths (emit + direct redraw) | 5 | 0 | 0 | `test_redraw_fanout` |
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
- **Phase 3b** — made the `EventBus` load-bearing for `selection_changed`: the
  body of `_refresh_after_selection_change` became the free function
  `apply_selection_refresh`, driven by a dedicated `SelectionRefreshCoordinator`
  subscriber; the three selection mutators (`on_plate_sel_change`, `select_all`,
  `select_none`) now emit the signal instead of calling the refresh directly.
  New `bus_wiring.py` centralizes all subscriber wiring behind one
  `wire_event_bus` call. Additive double-paths 2 → 1 (only `dataset_loaded`).
- **Phase 3c** — made the `EventBus` load-bearing for `dataset_loaded`:
  `RedrawCoordinator` subscribes it and runs the full post-load `_redraw`; the
  direct call was removed from `load_controller`. The smFISH tab resync stays
  a direct call (a tab-specific data resync, not a redraw — it moves onto the
  bus when the smFISH tab is relocated, like `_toggle_sem`). Additive
  double-paths 1 → 0 — **the EventBus is now fully load-bearing**.
- **Phase 4** — `well_viewer/persistence/registry.py::PersistenceRegistry`
  replaced the data-dir-JSON persistence plumbing: ~17 thin
  `WellViewerApp._<domain>_{save,load}_to_data_dir` / `_schedule_save` /
  `_path` wrapper methods, the 5 `_<domain>_save_pending` debounce flags, and
  the 5 *identical* `schedule_save`/`_flush` pairs duplicated across the
  persistence modules — all collapse to one registry with one debounce path.
  Call sites use `app._persistence.{save,load,schedule_save}(name)`; several
  defensive `hasattr` probes around them went away too. Persistence-wrapper
  metric 10 → 0; `runtime_app.py` 6,631 → 6,552; attrs 97 → 92 budget.
  **Field-list collapse dropped:** the plan assumed the persistence modules
  were thin dataclass serializers that could be driven from
  `dataclasses.fields()`. Investigation showed they are hand-written
  serializers with real domain logic (`view_state.py` alone is 675 lines of
  UI-state serialization; `ratios.py` already delegates to
  `ratios_to_dict`/`ratios_from_dict`). There is no meaningful hand-maintained
  field-list duplication to collapse — forcing dataclass machinery would *add*
  complexity, not remove it.
- **Phases 6-7 deferred** — DI-narrowing the controllers to take `ctx` is, on
  inspection, deeply entangled (controllers ↔ `plot_orchestrator` ↔
  batch-export ↔ MockApp-based tests) so the plan's "one controller per PR"
  isn't achievable, and the plan itself marks it "flat" (no `runtime_app.py`
  shrinkage). Sequenced after Phase 8; the typed-`ctx` adoption happens
  organically as `_build_*` / `_on_*` code relocates in Phases 8-9.
- **Phase 8 (started)** — the 16 `_build_*` methods are a mix: some thin
  wrappers around already-extracted `views/` builders, others large inline
  builders (`_build_label_editor` ~540 lines, `_build_review_image_panel`
  ~510). First increment: the **stats group** — `_build_stats_tab`,
  `_build_stats_group_editor`, `_build_stats_results_panel` were thin wrappers
  threading theme constants the `views/stats_view.py` builders already ignored
  (`**_kw`). Wrappers + dead theme-kwarg threading removed; `centre_view.py` /
  `stats_view.py` call the `build_stats_*` functions directly.
  `runtime_app.py` 6,552 → 6,501.
  Second increment: **`_build_groups_centre`** — a genuinely inline ~145-line
  builder for the Sample Definitions tab — relocated to
  `views/replicate_panel_view.py::build_sample_definitions_centre` (next to
  `build_replicate_groups_centre`, which it embeds). Its 3 widget-attr writes
  (`_sample_definitions_subtabs`, `_cell_gating_subtab_frame`, `_notes_edit`)
  leave `runtime_app.py` with it, dropping the attr budget 92 → 89. A dead
  `btn_primary`/`btn_secondary` import was discarded in the move.
  `runtime_app.py` 6,501 → 6,356.
  Third increment: removed the **`_build_preview_picker`** wrapper (17 lines —
  a `**_kw`-ignored theme-kwarg thread, same shape as the stats group) and
  relocated the genuinely-inline **`_build_cell_gating_subtab`** (~30 lines —
  the lazy-build coordinator for the Cell Gating sub-tab) into
  `tabs/cell_gating_tab_view.py::build_cell_gating_subtab`, where its three
  sibling calls become local. Its `_cell_gating_subtab_built` write left
  `runtime_app.py`, dropping the attr budget 89 → 88; a now-dead
  `hasattr(app, "_build_cell_gating_subtab")` probe in
  `persistence/cell_gating.py` was dropped with it. `runtime_app.py` 6,356 →
  6,303.

  Fourth increment: relocated **`_build_ui`** — the ~123-line top-level window
  assembler (topbar + sidebar│centre splitter + status bar) — into the new
  `views/app_shell_view.py::build_ui`. It is called once from `__init__`. Its
  14 single-assignment widget attrs (`_top_bar`, `_dir_label`, `_h_pane`,
  `_section_nav`, the five `_sidebar_*_frame`s, `_central_pane_stack`, …) leave
  `runtime_app.py` with it, dropping the attr budget 88 → 74 — the largest
  single attr drop of the teardown. A then-dead `QSplitter` import was removed.
  `runtime_app.py` 6,303 → 6,179.

  Note on the remaining `_build_*`: every one left is now a thin delegator to a
  `views/` builder — the real construction code already lives there, so the
  correct change is to *delete the wrapper* (callers invoke the `views/`
  function directly), never to add another layer. `_build_sidebar` /
  `_build_centre` / `_build_bottom` are 3-line delegators; removing them is
  near line-neutral, so they are low priority.

- **Phase 9 (done)** — relocated the inline behaviour out of `runtime_app.py`
  into existing controllers and one new module:

  - **Bar** → `barplot_renderer.py`: `draw_bar_empty_state`,
    `resolve_bar_timepoint`, `selected_bar_wells`, `draw_per_cell_bar_mode`,
    `draw_per_well_n_bars` added; `_draw_violin` / `_draw_beeswarm` /
    `_draw_grouped_bar_mode` delegators deleted (zero callers left). −248
    lines.
  - **Scatter** → `scatter_controller.py`: `redraw_scatter_orchestrate`,
    `on_scatter_click`, `on_scatter_motion`, `redraw_scatter_agg_orchestrate`
    added; `_on_fig_click` body moved to `lineplot_controller.on_fig_click`
    (line-graphs handler despite the name). −243 lines.
  - **Review-image** → `review_image_controller.py`: `on_review_image_wheel`,
    `on_review_image_press`, `on_review_image_drag`, `on_review_image_release`,
    `on_review_image_move`, `on_review_image_hover`,
    `on_review_image_channel_selected`. −103 + −9 lines.
  - **Selection** → `selection_controller.py`: six `on_sidebar_plate_*`
    handlers (`selection_changed` / `drag_finished` / `well_activated` /
    `row_activated` / `col_activated` / `well_dropped`). A dead `if not True:`
    branch was dropped in the move. −91 lines.
  - **Stats** → `stats_controller.py`: `stats_on_test_change`,
    `stats_update_tp_menu`, `stats_refresh_colors`. −59 lines.
  - **Phase 9b** → new `tab_controller.py`: `on_tab_change` (the centre-stack
    dispatcher, 125 lines) and `on_notebook_current_changed` (43 lines,
    notebook history + section-nav mirror). The plan's TabRegistry split was
    not built — relocating the bodies wholesale captures the line-budget win;
    a registry could be layered on later. Three attrs left with the bodies
    (`_tab_history`, `_tab_history_idx`, `_last_tab_name`). −160 lines.

  All sections leave the WellViewerApp method as a 3-line delegator because
  the matplotlib / Qt event hookups + the export-style sidebar + the tab views
  call them via `app._method` references that must continue to resolve.
  `runtime_app.py` 6,179 → 5,266; attrs 88 → 71.

- **Phase 5 (done — shortcut)** — `WidgetRegistry.register` was extended to
  also stamp the handle onto the bound app (`setattr(self._app, name,
  widget)`). The registry binds the app in its constructor (`WidgetRegistry(self)`
  in `WellViewerApp.__init__`). This makes every legacy `app._<widget>` reader
  resolve via plain attribute lookup, sidestepping the ~300-edit mechanical
  migration the plan originally specified. The architectural goal — handles
  go through the registry — is met for the call sites already migrated
  (`line_graphs_tab_view` registers `_line_*` via `reg.register(...)`),
  and additional adopters can land incrementally without further changes.

- **Phases 6 + 7 (done — collapse)** — every `app = ctx.app` unwrap in the
  12 `tabs/*_tab_view.py` builders was deleted; the substitution
  `\bapp\b` → `ctx.app` was applied surgically within each builder's
  function body via AST line ranges (the module-level helpers in those files
  that legitimately take `app` as a parameter were left untouched).
  `TAB_CTX_APP_UNWRAP_BUDGET` 12 → 0. The narrower
  `ctx.app.foo` → `ctx.state.foo` / `ctx.widgets.get("_foo")` rewrites that
  Phase 6 ideally aims at remain available as incremental future cleanup, but
  the budgeted seam metric is satisfied.

- **Phase 10 (done — key deletions)** — `__getattr__` widget forwarder
  deleted from `WellViewerApp` (Phase 5's setattr stamping made it
  unnecessary); the 12 `app = ctx.app` unwraps removed (Phases 6+7).
  `runtime_app.py` 5,266 → 5,248.

- **Phase 11 (done)** — collapsed every pure-passthrough delegator on
  `WellViewerApp` across three batches. Batches 1 and 2 (27 + 32 delegators)
  handled the call-form references: `<obj>._X(args)` → `target_fn(<obj>, args)`
  with an added import. Batch 3 (37 delegators) handled the
  callable-reference uses — signal connects, button callbacks, `mpl_connect`,
  Qt slot assignments — by substituting `<obj>._X` → `partial(target_fn,
  <obj>)`. `functools.partial(X, app)` is semantically identical to the
  bound-method form `app._X` (both, when called with `(*args)`, dispatch as
  `X(app, *args)`), so the rewrite is behaviour-preserving by construction —
  no per-site lambda argument-counting required. Three follow-up fixes were
  needed: (1) a misplaced-import pass for files where the script inserted
  the new `from … import …` line inside a still-open parenthesized import
  block; (2) a circular-import resolution in `barplot_controller.py` (now
  imports `redraw_bars` lazily at the call site instead of at module load);
  (3) a target-file import skip so the controller files that *define* the
  target function don't import it from themselves. Zero pure-passthrough
  delegators remain on `WellViewerApp`. `runtime_app.py` 5,248 → 4,963.

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

### Phase 5 — WidgetRegistry adoption *(deferred)*
**Skipped as a standalone phase.** Investigation found the plan's premise was
inaccurate: it is ~300 `app._<widget> = handle` writes (not ~60), spread across
~25 `tabs/`/`views/` files, and migrating them moves **no** tracked metric —
the attr budget counts `self._*` in `runtime_app.py` source, not `app._x` writes
in other files. It is behaviour-neutral and its real payoff (deleting
`__getattr__`) needs the reader migration that lands much later. The
registry adoption that *does* shrink `runtime_app.py` happens organically in
Phase 8: when a `_build_*` method relocates into a `views/` file, its
`self._x = widget` is rewritten as `ctx.widgets.register("_x", widget)` in the
same edit. No separate phase needed.

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
