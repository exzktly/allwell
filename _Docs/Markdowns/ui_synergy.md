# UI Synergy — Living Implementation Plan

> **Status:** IN PROGRESS — 9 / 18 shipped (+ 1 follow-up) · **Last updated:** 2026-06-17
>
> This is a **living document**. As work proceeds, update each task's checkbox and
> the per-recommendation **Status** line. Keep the summary table in sync. When a
> recommendation ships, link its PR(s) and move it to ✅ Done.
>
> **Shipped (branch `claude/ui-ux-design-review-8uktp9`):** F2, C2, S2, D4, D3, D1,
> D2, F5, **F3** — each verified by the headless render harness and/or targeted
> tests. Plus a follow-up requested mid-stream: the **Scatter cell viewer** is now a
> shared-dock **slide-out** (not a modal pop-up), mutually exclusive with the
> Properties sidebar (`mount_widget_in_plot_dock`).
>
> **S3 — parked at the user's request** (skip for now; design Q on which timepoint
> to colour by is deferred).
>
> **Remaining (architectural / behaviour-changing / higher-risk):** S1, S4, S5, C1,
> C3, C4, F1, F4 — approved by the user but each is a focused refactor of core
> behaviour/persistence/structure. Being done deliberately (one at a time, with
> tests) rather than rushed together. See each section's Status for specifics.
> **F1 note:** the threshold/band readout is line-plot-only (set in
> `lineplot/controller.py`), not a persistent global; clickable chips need cross-tab
> refresh wiring to avoid stale values — scoped, not yet built.

## Context

A design review of AllWell's Review pane found that several features which are
*conceptually the same* have drifted into separate, repeated controls — three
"group" models, a per-tab Channel/Property/Threshold bar that vanishes on
Scatter, three image views with independent LUT/stage state, and per-plot export
that Batch Export re-specifies from scratch. This document turns the review's
recommendations into a **step-by-step, status-tracked implementation plan**, with
each item explicitly constrained to the **`ARCHITECTURE.md` guidelines** (see
§"Architecture guardrails — apply to every item" below).

Status legend: ⬜ Not started · 🟦 In progress · ✅ Done · ⛔ Blocked · ➖ Won't do

---

## Architecture guardrails — apply to every item

Every recommendation below must satisfy these `ARCHITECTURE.md` rules. Each item's
**Compliance** line names the ones it most touches.

- **G1 — Placement (§3).** New tab/feature → `well_viewer/features/<feature>/{controller,renderer,tab_view}.py`. Reusable, app-agnostic widget → `widgets/<x>.py`. Cross-cutting behaviour → `well_viewer/core/`. Persistence → `well_viewer/persistence/`.
- **G2 — Design tokens (§3, §8).** Never hardcode hex/px. Chrome → `theme.Colors/Spacing/Radii`; plot/`get_color` colours → `ui/theme/styles._DARK_THEME`.
- **G3 — No new loose `self._*` on `WellViewerApp` (§5.1).** Budget = 71, ratchets down only (`tests/test_guardrails.py`). New state goes on a typed dataclass: cross-cutting → `core/<domain>_state.py`, feature-local → `features/<feature>/state.py`. Behaviour = functions taking `app`/`ctx`, not methods on `WellViewerApp`; add a 1-line proxy only if a legacy `app._method(...)` caller exists.
- **G4 — State modules import-safe (§5.9).** No `runtime_app` import from any `*_state.py`/domain module (`test_state_package_import_contract`).
- **G5 — EventBus is capped at 5 signals (§5.9).** `channel_changed`, `selection_changed`, `dataset_loaded`, `plot_options_changed`, `redraw_requested`. Do **not** add signals; reuse existing ones (payloads are state objects, never `app`). Status-bar messages go through `core/status_signal.py`, **not** the bus (`tests/test_redraw_fanout.py`).
- **G6 — Tab builders take `ctx` only (§5.3).** `TAB_CTX_APP_UNWRAP_BUDGET = 0` — no `app = ctx.app` unwrap; use `ctx.app`/`ctx.state`/`ctx.widgets`/`ctx.bus`.
- **G7 — Persistence (§5.7).** One module per JSON block under `persistence/`, registered with `PersistenceRegistry` (`app._persistence`), saved via `_io.atomic_write_json` and debounced `schedule_save`. Selections/sample-definitions already auto-save.
- **G8 — Widgets standalone (§7).** Import only `theme` + Qt (+`widgets/_support`); token-styled; DPI-aware via `fontMetrics()`; one widget per file; add a `__main__` demo; if inline QSS reads `theme.Colors`, register `_support.install_qss_refresh`.
- **G9 — Suffix conventions (§3).** `controller.py`/`renderer.py`/`tab_view.py`/`*_view.py`/`state.py` per their defined roles.
- **G10 — Selection two-step (§9.2).** `selectionChanged` = paint (cheap), `selectionDragFinished` = commit (redraw). Never trigger heavy work on every paint tick.
- **G11 — Selections on-disk contract.** v2 schema in `domain/selections_model.py` + `Markdowns/SELECTIONS_MODEL_CONTRACT.md`; preserve v1→v2 migration.
- **G12 — Keep the docs current.** Any item that changes structure — a new/relocated module, a new typed state dataclass, a new `widgets/` widget, a changed group/persistence model, a new design token — **must** update `_Docs/Markdowns/ARCHITECTURE.md` (the directory map §3, the state-object/persistence tables §5, the widget table §7, the theming notes §8) **and** the README in the same PR. This `ui_synergy.md` itself is registered in the ARCHITECTURE.md docs list when it lands.

**Universal verification (every item):** `ruff` clean · `pytest tests/test_guardrails.py tests/test_architecture_contracts.py` green · re-render the affected view(s) with the headless harness `tests/visual/render_views.py` (writes `_Docs/visual_audit/screenshots/`) and eyeball the before/after · **ARCHITECTURE.md + README updated for any structural change (G12).**

---

## Summary table

| ID | Recommendation | Impact | Effort | Status |
|----|----------------|:------:|:------:|:------:|
| **S1** | Unify the three "group" models (Statistics scope) | ★ | M* | ✅ |
| **S2** | One persistent Analysis-Context bar | ★ | M | ✅ |
| **S3** | Live-plate heatmap overlay (merge sidebar plate ↔ Heat Map) | ★ | M | ⬜ |
| **S4** | Link the three image views (shared channel/LUT/stage) | ★ | M | ⬜ |
| **S5** | "Add to batch" from every plot card | ★ | M | ⬜ |
| **C1** | Collapse the 4-row Plotting control bar | ★ | M | 🟡 |
| **C2** | Group the Segmentation toolbar | ◐ | S | ✅ |
| **C3** | Collapsible sections on the Analyze form | ◐ | M | ⬜ |
| **C4** | Last-run summary in the idle Analyze pane | ☆ | S | ⬜ |
| **D1** | Analyze first-run guidance (schema + method hints) | ★ | M | ✅ |
| **D2** | Self-describing fold-change / scatter-gate | ◐ | S | ✅ |
| **D3** | Surface shortcuts + dim Help underlay | ◐ | S | ✅ |
| **D4** | Cell Browser sensible default state | ◐ | S | ✅ |
| **F1** | Clickable status context chips | ★ | S | ✅ |
| **F2** | Clear active-state for mode toggles | ★ | S | ✅ |
| **F3** | Channel-name casing consistency (`gfp`→`GFP`) | ★ | M | ✅ |
| **F4** | Pipeline progress legibility | ◐ | M | ⬜ |
| **F5** | Table-header alignment + empty-state hints | ☆ | S | ✅ |

\* S1 is smaller than it looks — the conversion plumbing already exists (see below).

**Recommended sequencing:** Quick wins (F1, F2, C2, C4, D4, F5) → consistency (F3) →
density (C1, C3) → discoverability (D1, D2, D3) → synergy chain (S2 → S3 → S1 → S5 → S4).

---

## S1 — Unify the three "group" models

**Status:** ⬜ Held — **behaviour change, needs green-light.** Making Statistics +
Batch Export write back to the shared `selection.selections` changes how those tabs
behave today (ephemeral Stats groups become persistent shared selections; the
standalone `export_groups.json` path is demoted). Worth doing, but it alters
user-facing semantics and persistence, so it should land deliberately with review,
not as an unattended change. Plan below is ready to execute on approval.

**Goal.** One group model used everywhere. Sample-Definition selections become the
single source of truth; Statistics "comparison groups" and Batch Export "export
groups" become pickers/editors over the *same* saved selections.

**Key finding (de-risks this).** All three already round-trip through the same root,
`app._app_state.selection.selections` (`core/selection_state.py:40`, v2 dicts per
`domain/selections_model.py`). Stats holds ephemeral `BarGroup`s but already
re-syncs from the root via `stats_sync_from_app` → `_groups_from_rep_sets`
(`features/stats/controller.py:548`, `runtime_app.py:1080`). Batch Export already
has a **linked mode** `use_sidebar_groups=True` → `_groups_from_rep_sets`
(`features/batch_export/base_panel.py:1284`). The work is to make linked mode the
default and let edits flow back, not to rebuild a model.

**Steps.**
- [ ] Make Batch Export default to `use_sidebar_groups=True`; demote the standalone
      `export_groups.json` path to an explicit "import/export groups file" action
      only (keep load for back-compat). Files: `features/batch_export/base_panel.py`,
      `tab_view.py`, `persistence/bar_groups.py`.
- [ ] Make Statistics group edits (drag-to-assign, +Add) write back to
      `selection.selections` instead of the ephemeral `StatsState.groups`: route
      `stats_apply_drag` / `stats_grp_add` (`features/stats/controller.py:448,523`)
      through the same `selection_controller` mutators the Groups panel uses
      (`sel_set_composition`, etc.), then re-derive the `BarGroup` view via
      `_groups_from_rep_sets`. Keep `BarGroup`/`ReplicateSet` as a **read-only view
      adapter** over the dicts (they're a rendering layer).
- [ ] In Statistics' and Batch Export's sidebars, surface an inline "+ New group"
      that calls the shared selection-create path so users don't leave the tab.
- [ ] Confirm autosave: edits now persist via the existing
      `persistence/sample_definitions.py` registry hook (debounced, atomic) — no new
      persistence module needed.

**Reuse.** `SavedSelectionsList` (`widgets/saved_selections_list.py`),
`selection_controller.sel_*`, `grouping/controller.rep_sets_loaded`,
`selections_model.to_block/migrate_v1`.

**Compliance.** G3 (don't add loose attrs — mutate `selection.selections` via
controllers), G7 (reuse `sample_definitions` autosave; don't add a parallel store),
G11 (preserve v2 schema + v1 migration), G1 (logic in feature `controller.py`),
G12 (rewrites the group story — update ARCHITECTURE.md §5.6/§5.7 + SELECTIONS_MODEL_CONTRACT.md).

**Verification.** Define a group in Sample Definitions → it appears pre-filled in
Statistics and Batch Export; edit in Statistics → persists and shows in the Groups
panel after reload. Run `tests/` selection/stats suites + the v1→v2 migration test.

---

## S2 — One persistent Analysis-Context bar

**Status:** ✅ Done (scatter behaviour) — the bar is already owned once at the Plotting
container; the remaining issue was its disappearance on Scatter. Now the global
Channel/Property/Threshold controls stay **visible but dimmed** on Scatter with an
inline italic note "Scatter uses per-axis channels below" instead of vanishing
(`core/centre_view.py` `_refresh_channel_chip` + new `_plotting_scatter_note`).
Verified by rendering the Scatter tab.

**Goal.** Hoist Channel · Property · Threshold into one persistent bar under the
sub-tab strip, applied to every plotting tab. On Scatter, **dim** the global bar with
a one-line note instead of hiding it (today it vanishes — reads as a glitch).

**Where it is today.** Built in `core/centre_view.py` `_build_plotting()`:
`app._plotting_channel_cb` (line 284), `app._plotting_metric_cb` (323),
`app._plotting_thresh_frac_stepper` (362). Global→per-renderer mirroring via
`_on_global_channel` (296) → `app._on_plot_channel_selected`. Scatter hide logic in
`_refresh_channel_chip` (440, esp. 462–477). State lives in `core/channel_state.py`
(`active_channel`, `active_metric`, `threshold`).

**Steps.**
- [ ] Keep the single global combos/stepper as the *only* channel UI; ensure the bar
      is owned once at the Plotting container level (it already is) and visually
      persists across sub-tab switches.
- [ ] Replace the scatter `setVisible(not is_scatter)` block (`centre_view.py:462`)
      with `setEnabled(False)` + a small caption ("Scatter uses per-axis channels
      below" — use an existing muted `QLabel`, token-styled). Keep per-axis X/Y
      selectors in the Scatter tab as today.
- [ ] Confirm the bottom status strip's threshold/band echo is driven from the same
      `ChannelState`/`PlotState` so it stays in lockstep (ties into F1).

**Reuse.** Existing combos + `Stepper`; `channel_state.ChannelState`;
`channel_state_controller` (`set_active_channel`, `recalculate_threshold`).

**Compliance.** G5 (reuse `channel_changed`; don't add signals), G3 (no new attrs —
the combos already exist), G2 (caption styled via tokens), G6 (read `ctx.state.channel`).

**Verification.** Switch across Bar/Line/Distribution/Heat Map/Scatter — the bar
stays put; Scatter shows it dimmed with the note. Channel change still re-renders all
tabs (`tests/test_redraw_fanout.py`).

---

## S3 — Live-plate heatmap overlay

**Status:** ⬜ Held — **open design question.** The reusable value function
(`heatmap.controller._cell_value`) needs a **timepoint** (`target_t`) and a
**colour-scale source**; for an always-on sidebar overlay it's ambiguous which
timepoint to show when a dataset has several, and what range to normalise against.
Needs a decision (see end-of-turn question) before building. Plan below otherwise
ready; the plate-tinting API (`WellPlateSelector.set_well_colors` /
`set_colored_wells_lit`) already exists.

**Goal.** Let the sidebar plate optionally tint each well by the current
(channel, property) value — a mini sequential heatmap — toggled by a small "data
overlay" switch. Selection and the most plate-shaped result become one surface; the
Heat Map tab stays as the colorbar/export zoom-in.

**Where it is today.** `widgets/well_plate_selector.py` already supports per-well
color overrides: `set_well_color(well_id, color)` (278), `set_well_colors(mapping)`
(273), and a lit/raised mode `set_colored_wells_lit(on)` (371) — the same machinery
the RunDashboard uses. Heat Map value→color logic: `features/heatmap/controller.py`
`_cell_value()` (172–248) and `_compute_global_range()` (279–348, returns
`(vmin,vmax)`). Colorbar widget: `widgets/gradient_strip.py` (`setSamples(fn,n)`,
`colorAt(t)`).

**Steps.**
- [ ] Extract the value→color mapping (value, vmin, vmax, cmap → QColor) into a small
      pure helper reusable by both the heatmap and the sidebar. Put it where the
      heatmap value logic lives (`features/heatmap/`) or a Qt-free helper if it stays
      colour-token driven; the sidebar then imports the mapping, not the renderer.
- [ ] Add a "data overlay" `ToggleSwitch` to the sidebar (`core/sidebar_view.py`);
      when on, compute per-well values for the active channel/property via the
      heatmap helper and call `plate.set_well_colors(...)` + `set_colored_wells_lit(True)`.
- [ ] Recompute the overlay on `channel_changed` / `plot_options_changed` /
      `dataset_loaded` (existing bus signals) — **not** on selection paint ticks (G10).
- [ ] Show a compact `GradientStrip` legend under the plate when overlay is on.
- [ ] Persist the toggle in `view_state` (`persistence/view_state.py`).

**Reuse.** `WellPlateSelector.set_well_colors/set_colored_wells_lit`,
`heatmap.controller._cell_value/_compute_global_range`, `GradientStrip`, `ToggleSwitch`.

**Compliance.** G5 (subscribe existing bus signals), G10 (overlay recompute off the
paint path), G2 (colours from the cmap/tokens), G3 (toggle state on `PlotState`/
`view_state`, not a loose attr), G8 (no widget changes needed — public API exists).

**Verification.** Toggle overlay → sidebar wells tint by GFP mean; change channel/
property → retint; drag-select stays smooth (no per-tick recompute); legend matches
the Heat Map tab's colours for the same metric.

---

## S4 — Link the three image views

**Status:** ⬜ Held — **architectural (shared state).** Introduces a new cross-feature
`ImageViewState` that Review Image / Image Table / Cell Browser all read; touches
three features' render paths. Best done deliberately with review. Plan ready.

**Goal.** Segmentation Review Image, Image Table, and Cell Browser share one
channel/LUT/stage(Raw·Flat-field·Corrected) state and cross-select, so they feel like
panes of one workspace.

**Where it is today.** Cross-nav already exists: Cell Browser
`on_cell_clicked` (`features/cell_browser/controller.py:187`) sets
`review_image.selected_nucleus`, calls `update_preview` + `refresh_review_image` +
`zoom_review_image_to_selected_nucleus`. But state is **not** shared: Review Image owns
`image_source` + `lut_by_channel` (`features/review_image/state.py:55,69`); Image Table
keeps LUT per-`_ImagePanel` (`features/image_table/panel_view.py:55`); they don't share
channel/stage.

**Steps.**
- [ ] Decide the shared seam: promote `image_source` + active `channel` + per-channel
      LUT into a single image-view state read by all three. Cleanest: a small
      `ImageViewState` (shared) that `ReviewImageState`/`ImageTableState` reference, or
      have Image Table read `review_image.lut_by_channel`/`channel`. Keep it on a typed
      dataclass under `core/` (cross-cutting across three features) per G3/G4.
- [ ] Route Image Table's per-row LUT/colormap and Review Image's LUT through the
      shared state; keep per-row *overrides* where genuinely needed but default to the
      shared values.
- [ ] When any view changes channel/stage/LUT, refresh the others lazily (only the
      visible one repaints; others refresh on tab show via `tab_controller`).
- [ ] Verify the existing Cell Browser → Segmentation jump still works and now also
      carries the shared channel/stage.

**Reuse.** `review_image/renderer.refresh_review_image/update_preview/zoom_*`,
`ReviewImageState`, `ImageTableState`, `PreviewState`, `tab_controller.on_tab_change`.

**Compliance.** G3/G4 (new shared state = an import-safe dataclass under `core/`, no
loose attrs, no `runtime_app` import), G1 (per-feature controllers mutate it), G6,
G12 (new shared dataclass — update ARCHITECTURE.md §5.9 state-object table).

**Verification.** Set channel/LUT/stage in Review Image → Image Table reflects it on
next show; click a Cell Browser cell → jumps with the shared channel/stage; render all
three views headlessly.

---

## S5 — "Add to batch" from every plot card

**Status:** ⬜ Held — **depends on S1** (unified groups) for the spec's group field.
S2 (its other dependency) is done. Build after S1 lands.

**Goal.** An "+ Add to batch" action on each plot card pushes the current plot's full
spec into the Batch Export queue, so Batch Export reviews figures you already dialed
in rather than re-entering every parameter.

**Where it is today.** Per-tab export cluster built per feature, e.g.
`features/barplot/tab_view.py:93-104` (`export_bar_plot_data`, `_copy_active_card_as_svg`,
`_save_active_card_figure`, `_open_export_style_panel`). Batch Export modes +
inline state in `features/batch_export/tab_view.py:136-188`
(`_batch_export_set_mode`, `_batch_export_inline_state`). The capturable "spec" =
`ChannelState` (channel/metric/threshold) + `PlotState` (SEM/SD, per-FOV, fold-change,
bar mode/pooling) + scatter per-axis combos + the (now-unified, S1) groups.

**Steps.**
- [ ] Define a small `PlotSpec` snapshot dataclass (which mode + the state fields
      above) in `features/batch_export/` (or `domain/batch_models.py`). Build it from
      `ctx.state` — no new app attrs (G3).
- [ ] Add an "Add to batch" `IconButton` to each plot tab's control row (next to
      Export/Copy/Save), wired to a `batch_export.controller` function that appends the
      snapshot to the batch queue and sets the matching mode.
- [ ] Show a `Toast` ("Added to Batch Export") for feedback (`widgets/toast.py`).
- [ ] In Batch Export, render queued specs as removable rows.

**Reuse.** `widgets/icon_button.py`, `widgets/toast.py`, `ChannelState`/`PlotState`,
batch_export mode dispatch, S1's unified groups.

**Compliance.** G1 (logic in `batch_export/controller.py`), G3 (spec from typed state),
G6, G2, G12 (new model/widget surface — update ARCHITECTURE.md §5.6/§7 as applicable).

**Verification.** Configure a bar plot, "Add to batch", open Batch Export → the entry
matches; run export → output matches the on-screen figure.

---

## C1 — Collapse the 4-row Plotting control bar

**Status:** ⬜ Held — **changes the plotting interaction model.** Moving fold-change +
display controls behind popovers is a real UX shift (extra click to reach common
controls) and risks the fold-change cross-tab sync (`_sync_widgets_to_state`) when
its widgets move into a popover. Worth a deliberate review. Plan ready.

**Goal.** Reduce Bar Plots' four stacked control rows to the primary context bar (S2)
+ popovers, freeing vertical room.

**Steps.**
- [ ] Move Fold-change (Control/Baseline) into a `Popover` button reading the current
      state. Source: `domain/fold_change_controls.install_fold_change_controls`
      (shared by Bar + Line) — wrap its widgets in the popover without changing its
      `_sync_widgets_to_state` cross-tab sync.
- [ ] Move Timepoints + Beeswarm/Violin/Smooth + Error-band into a "Display"
      `Popover`/`CollapsibleSection`. Files: `features/barplot/tab_view.py:115-200`.
- [ ] Keep keyboard/tab order sensible; ensure greyed-until-data behaviour (the
      `TabCtrl` object-name gating) still applies.

**Reuse.** `widgets/popover.py`, `widgets/collapsible_section.py`,
`widgets/segmented_control.py`, `fold_change_controls`.

**Compliance.** G8 (popover/collapsible are existing standalone widgets), G9, G6, G2.

**Verification.** Bar/Line tabs show one control row + popovers; fold-change still
mirrors across Bar↔Line; plot area is taller. Re-render headlessly.

---

## C2 — Group the Segmentation toolbar

**Status:** ✅ Done

**Steps.**
- [x] Raw·Flat-field·Corrected is already a real `SegmentedControl` mapped to
      `ReviewImageState.image_source` (`features/preview/panel_view.py`) — no change needed.
- [x] Added thin `vline` separators (`_toolbar_separator`) splitting the row into three
      clusters: {Delete Cell · Crop · Clear · Reset} | {Raw · Flat-field · Corrected} |
      {Fit · − · +}. `features/preview/panel_view.py`.

**Reuse.** `widgets/segmented_control.py`, existing `Separator` styling in `theme.qss()`.
**Compliance.** G8, G2, G9. **Verification.** Render Segmentation → Review Image; stage
toggle still switches source; clusters visually grouped.

---

## C3 — Collapsible sections on the Analyze form

**Status:** ⬜ Held — **reworks the shared `_section` builder** used by every Analyze
section, interacting with the form's dependency-driven row show/hide and the
QScrollArea. A core-surface restructure better landed with review. Plan ready.

**Steps.**
- [ ] Wrap Schema / Channels / Folders / QC blocks in `CollapsibleSection`s
      (`analyze_tab_form.py` `_section`), defaulting QC/preprocessing to collapsed
      ("Advanced"). Beginners see Schema + Folders + Run.
- [ ] Animate the show/hide of dependency-driven rows (radius/FFT/mesh, background
      well) so users notice them appear (today they snap in/out).

**Reuse.** `widgets/collapsible_section.py`, existing `_section`/`_row`/`_info_icon`.
**Compliance.** G2, G8, G1 (Analyze-side; keep services Qt-light per §6.4).
**Verification.** Render Analyze; expand/collapse; dependent rows animate.

---

## C4 — Last-run summary in the idle Analyze pane

**Status:** ⬜ Held — **low value.** The audit itself rated the idle right-pane gap
"acceptable (fills during a run)". A proper summary card also needs last-run
metadata plumbing (dataset/well-count/method/duration). Deferred as lowest-priority.

**Steps.**
- [ ] In the idle Pipeline Output pane (`90-analyze.png` lower half), show a summary
      card (dataset, well count, method, duration, "Open results") populated from the
      last `pipeline_info.json` / `RunDashboard` final state. Files: `analyze_tab.py`,
      `widgets/run_dashboard.py`.

**Reuse.** `RunDashboard`, `read_pipeline_info`, `EmptyState` styling.
**Compliance.** G2, G8. **Verification.** Idle Analyze shows the card; "Open results"
loads the dataset into Review.

---

## D1 — Analyze first-run guidance

**Status:** ✅ Done (core); minor extras noted

**Steps.**
- [x] Segmentation-method hints: enriched the existing `_segmentation_hint_lbl`
      (`analyze_tab._refresh_segmentation_hints`) so each method leads with *when to
      use this* (and added the previously-missing `stardist_nuclei` default).
- [x] Schema helper: already substantial — a **live example filename** preview
      (`_refresh_schema_preview` → `_schema_preview_lbl`), a red error label
      (`_schema_err_lbl`), Run/Browse disabled until the schema is valid, and a
      "Define the filename schema above first." lock hint. No change needed.
- [ ] *Optional extras (not done, low value):* a per-combo inline error icon on the
      specific offending position, and a "drop a filename to infer the schema"
      affordance. The existing red label + disabled controls already communicate.

**Reuse.** `_info_icon`, `_refresh_segmentation_hints` (NamedPageStack per-method panel).
**Compliance.** G2, G1. **Verification.** Invalid schema shows the inline icon; method
hints render.

---

## D2 — Self-describing fold-change / scatter-gate

**Status:** ✅ Done (controls); scatter on-plot gate hint left as optional

**Steps.**
- [x] Fold-change Control/Baseline combos already carry tooltips
      (`domain/fold_change_controls.py`).
- [x] Added explanatory tooltips to the previously-bare bar shape toggles —
      Group by Well, **Pool Replicates**, Pool rset cells, Beeswarm, Violin, and the
      Smooth slider (with its Violin-only dependency) — `features/barplot/tab_view.py`.
- [ ] *Optional:* a persistent on-plot "Gate active" hint on Scatter
      (`scatter/gating.py` already draws per-gate "% inside" labels). Lower value;
      not done.

**Reuse.** `_info_icon` pattern, `widgets/kbd_hint.py` styling. **Compliance.** G2.
**Verification.** Hints visible; gate hint appears when a gate exists.

---

## D3 — Surface shortcuts + dim Help underlay

**Status:** ✅ Done

**Steps.**
- [x] Underlay dim: already resolved since the audit — the Help drawer now uses the
      v2 `Drawer`, whose `_Backdrop` paints a 0.35-alpha scrim. Verified by rendering
      `95-dialog-help.png` (left content clearly dimmed). No change needed.
- [x] Shortcut consistency: the chrome advertised two shortcuts that weren't wired —
      the status-bar "Search ⌘K" hint (no global search exists) and the help drawer's
      "⌘W Close window". Fixed by making the claims true: wired `Ctrl+W` (close) and
      `Ctrl+/` (toggle help) in `_install_shortcuts`, replaced the phantom "Search ⌘K"
      status hint with a real "Help ⌘/", and listed ⌘/ in the help drawer. `all_well.py`.
      Tested: `test_app_smoke.py` green.

**Reuse.** `widgets/drawer.py`, `widgets/kbd_hint.py`. **Compliance.** G8, G2.
**Verification.** Render Help drawer (`95-dialog-help.png`) — underlay dimmed; shortcuts
list complete.

---

## D4 — Cell Browser sensible default state

**Status:** ✅ Done (aligned with the deliberate deselected-default)

**Note:** the timepoint/FOV deselected-default is **intentional** (recent
`cb-default-deselected` work + the explicit comment in `criteria_view._sync_selection_dict`).
Flipping it would revert a maintainer decision, so instead of changing the default
we made the empty state *instructive*.

**Steps.**
- [x] When `apply_filters` produces an empty set, set a status that names the exact
      next click — "No timepoints selected — click Timepoints to choose which to show."
      / "No FOVs selected — …" / "No cells match … relax the criteria"
      (`controller._empty_gallery_hint`).
- [x] Show busy feedback on "Apply filters": the button disables + reads "Loading…"
      while the worker composes a page, re-enabling on `on_page_finished`
      (`controller._set_apply_busy`, button stored as `app._cell_browser_apply_btn`).
      Tested: `tests/test_cell_browser_tab.py`, `test_cell_browser_collect.py` green.

**Reuse.** `widgets/empty_state.py`, existing async worker signals (`cell_ready`,
`page_finished`). **Compliance.** G1, G2, G3 (defaults on the feature state).
**Verification.** Open Cell Browser → gallery populates or shows a clear CTA; Apply
shows progress.

---

## F1 — Clickable status context chips

**Status:** ⬜ Held — **needs a restructure decision.** The `N well(s) | nuclei |
threshold | band` metric line is composed ad-hoc and pushed to the shared
general-purpose `_status_lbl` (e.g. `lineplot/controller.py:516`), which also shows
transient messages. Decomposing it into clickable chips means either restructuring
that shared label across several controller call sites, or adding a parallel chip
strip (duplication). Which approach is a decision (see end-of-turn question).

**Goal.** Turn `4 well(s) | 72 nuclei | threshold=50.00 | band=SEM` + the redundant
"Ready." into clickable `SelectionChip`s that jump to their editor; drop the duplicate
"Ready." text.

**Where it is today.** `core/status_view.py` `build_bottom(app)` (45): `app._status_lbl`
(QLabel "Muted"), progress bar, log button. Status text set via `core/status_signal.py`
(not the bus — G5). `widgets/status_dot.py`, `widgets/selection_chip.py` exist.

**Steps.**
- [ ] Replace the static metric text with `SelectionChip`s; the threshold chip opens
      Cell Gating / the threshold control, the band chip opens the error-band control.
- [ ] Remove the duplicate "Ready." (keep one status surface; status dot carries state).
- [ ] Keep updates flowing through `status_signal` (G5), driven by `ChannelState`/
      `PlotState`/`SelectionState`.

**Reuse.** `widgets/selection_chip.py`, `widgets/status_dot.py`, `status_signal`.
**Compliance.** G5 (status off the bus), G3 (no new attrs — chips read typed state),
G2, G8. **Verification.** Chips reflect live state; clicking the threshold chip opens
its editor; only one "ready/status" surface remains.

---

## F2 — Clear active-state for mode toggles

**Status:** ✅ Done

**Steps.**
- [x] Added a shared QSS rule in `theme.qss()` for checkable toggle buttons: the
      `:checked` / `variant="toggle_active"` state now gets an **accent ring + tinted
      fill** (`accent_dim` bg, `accent` border), with an accent hover. This was the
      high-leverage fix — it covers every `variant="toggle"` button at once (Delete
      Cell, Crop Region, Outline, Binary, Beeswarm, Violin, Group-by-Well), not just
      the two Segmentation toggles. Verified: "Outline: On" now reads clearly armed vs
      "Binary: Off". Token-based (G2). File: `theme.py`.

**Reuse.** `widgets/toggle_switch.py`, `theme.Colors.accent`. **Compliance.** G2, G8.
**Verification.** "Delete mode" is unmistakably on; render Segmentation.

---

## F3 — Channel-name casing consistency (`gfp`→`GFP`)

**Status:** ✅ Done — robust approach (user chose "no old-state reset")

Fluor channels now display upper-case (`GFP`) in the Scatter (per-cell + aggregate)
and Statistics channel combos, matching the rest of the app. Done robustly so old
saved state keeps working:
- **Display:** `scatter.controller.update_scatter_plot_options` and
  `stats.controller` build the channel-combo options upper-case (the canonical
  lower-case token list is kept separately for the smFISH-channel test).
- **Columns stay canonical:** `col_for_scatter_axis` / `col_for_scatter_entry`
  lower-case the fluor token when composing the CSV column key, so columns remain
  `gfp_mean_intensity` (exact match, not relying on case-folding). Ratio/shape labels
  are unaffected (resolved via `_label_to_channel_key` before the fluor path).
- **Persistence back-compat:** `view_state._set_combo_if_valid` gained a
  case-insensitive fallback, so an old saved axis selection stored as `gfp` restores
  onto the new `GFP` item instead of resetting to the first channel.
- Chart titles already upper-cased (`stats._channel_label_for_val_col`); Pipeline
  Info left showing the raw stored token (it's a raw-sidecar dump).
- Tests: scatter+stats suites (79) green; updated `test_shape_features` (findText
  "GFP") and `test_thresh_frac_quick_edit` (S2 dim-not-hide); budgets held.

**Goal.** Display fluor tokens uppercase everywhere while leaving stored tokens
untouched (CSV columns / persistence keep the raw token).

**Steps.**
- [ ] Add a single display↔token mapping helper (e.g. in `data/metric_labels.py` — the
      existing label module — or a small `ui_helpers` function): `display_channel(tok)`
      / `token_from_display(s)`.
- [ ] Apply at every combo-population + label site (~8 read sites: global channel combo,
      Scatter X/Y, Statistics, Pipeline Info, Cell Gating, plot titles). Do **not**
      change stored tokens, CSV column construction (`col_for_scatter_entry`,
      `f"{entry}_{metric}"`), or persisted view-state values.

**Reuse.** `data/metric_labels.py`, `ui_helpers.set_combo_values*`.
**Compliance.** G1/G2, G11 (no change to persisted tokens; resolve through the helper),
G12 (new label helper — note in ARCHITECTURE.md §5.6).
**Verification.** All tabs show `GFP`; CSV columns + persisted state unchanged; load an
old dataset and confirm no key drift.

---

## F4 — Pipeline progress legibility

**Status:** ⬜ Held — **touches the pipeline + runner.** A determinate "grouping"
progress signal means emitting events from `WellPlateZipper.py` through
`services/pipeline_runner.ProgressTracker` (must keep the pipeline GUI-free, §6.2).
Moderate-risk plumbing better landed with review. The log "summary level" half is
safer but pairs naturally with the grouping-progress work.

**Steps.**
- [ ] Default the Analyze log to a "summary" level with a "show raw log" toggle
      (`analyze_tab.py` log view; `services/pipeline_runner.ProgressTracker` already
      emits structured events the `RunDashboard` consumes).
- [ ] Give the WellPlateZipper "grouping" phase a determinate progress signal so long
      waits don't look hung (emit a `zipper`/grouping progress event;
      `WellPlateZipper.py`, surfaced via `ProgressTracker`).

**Reuse.** `RunDashboard`, `ProgressTracker`. **Compliance.** §6.4 (keep services
Qt-light), §6.2 (don't pull GUI into pipeline). **Verification.** Run on `mini_dataset`;
log defaults to summary; grouping shows progress.

---

## F5 — Table-header alignment + empty-state hints

**Status:** ✅ Done

**Steps.**
- [x] Left-aligned the horizontal header labels on the Review CSV table
      (`features/review_image/csv_tab_view.py`) and the Ratios table
      (`features/scatter/ratio_panel_view.py`) via
      `setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)`, so headers read in line
      with their left-read text values. Verified by render.
- [x] Empty-state hints: Statistics already shows "Select two comparison groups …
      click Run test"; the Ratios empty grid is a low-value QTableWidget overlay
      (audit rated it [L] "low value") — left as-is.

**Reuse.** `widgets/empty_state.py`. **Compliance.** G2, G8. **Verification.** Render
Review CSV + Ratios; headers left-aligned, empty states hinted.

---

## Maintenance

- Update the **Status** line + checkboxes per item as work lands; keep the summary
  table in sync; link PRs.
- **Keep the docs current (G12).** Every PR that lands an item updates, in the same
  PR: (a) this file's status; (b) `_Docs/Markdowns/ARCHITECTURE.md` for any structural
  change — directory map (§3), state-object table (§5.9), persistence table (§5.7),
  widget table (§7), theming notes (§8); and (c) the README where user-facing
  behaviour or setup changes. Specifically: S1 rewrites the group story (§5.6/§5.7
  tables + SELECTIONS_MODEL_CONTRACT.md), S3/S4 add shared state dataclasses (§5.9
  table), S5/new widgets touch the widget table (§7), F3 adds a label helper (§5.6).
- Re-run the visual harness after each shipped item and refresh the relevant
  `_Docs/visual_audit/screenshots/*.png` if the view changed.
- If any item turns out to need a new EventBus signal, a new loose `self._*` attr, or
  a pipeline→GUI import, **stop** — that conflicts with G5/G3/G2 and needs a design
  note here first.
