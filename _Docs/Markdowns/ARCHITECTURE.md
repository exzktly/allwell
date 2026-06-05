# All-Well Architecture

This document is the starting map of the All-Well codebase. It explains how the
application is organised, why files are split the way they are, and how to
read, modify, and debug the code without having to spelunk for two hours
first. Read it linearly the first time; come back to specific sections when
you're chasing a bug or planning a feature.

The current code reflects an iterative port from a Tk prototype to a Qt
desktop app (PySide6). Some of the structural decisions only make sense once
you know that — they're called out where relevant.

---

## 1. What All-Well is

All-Well is a desktop application that does two things:

1. **Analyze.** Run a fluorescence microscopy pipeline against a folder of
   raw images: nuclear segmentation (StarDist), background subtraction,
   per-cell intensity quantification, smFISH spot counting, then export of
   per-well CSVs and per-well processed-image ZIPs.
2. **Review.** Load the CSVs the pipeline produced and explore them: plate
   maps, line / bar / scatter / distribution / heat-map plots, statistics,
   image preview, batch export, etc.

The same window hosts both modes — a segmented control in the title bar
switches between them — and a single dataset folder is the unit of work that
flows from Analyze (writes it) to Review (reads it).

```
   Raw images
      │
      ▼
  Analyze tab ──▶  process_microscopy.py  ──▶  <data>/{<well>_out.zip, *.csv, pipeline_info.json}
                                                        │
                                                        ▼
                                                  Review tab
```

---

## 2. The two-mode shell

The application root (`AllWellApp` in `all_well.py`) owns a title bar and a
central pane stack. The central stack has two pages:

| Pane | Mode | Implemented by | Source file |
|------|------|----------------|-------------|
| Page 0 | **Review** | `WellViewerApp(QWidget)` | `well_viewer/runtime_app.py` |
| Page 1 | **Analyze** | `AnalyzeTab(QWidget)` | `analyze_tab.py` |

The title bar's `SegmentedControl` (`Review` / `Analyze`) drives the stack.
Both panes are constructed eagerly at startup so switching back and forth is
instant; widget trees inside them build lazily on first use (see §5.4).

A handful of things live on the application root rather than per-pane because
they're shell-wide:

- **Stylesheet.** `theme.qss()` is set as the application-wide QSS string in
  `AllWellApp._apply_stylesheet`. All chrome reads from it.
- **Help drawer + keyboard shortcuts** (`Ctrl+O`, `Ctrl+E`, `Ctrl+←`/`→`).
- **Status bar** (bottom of the window) with a `StatusDot` and key-hint
  chips.
- **App icon, window geometry persistence.**

---

## 3. Top-level directory map

```
allwell/
├── all_well.py                  ← app entry point + title bar shell
├── all_well_launcher.py         ← PyInstaller entry point + --run-pipeline dispatch
├── analyze_tab.py               ← Analyze pane (form + run controls + live log)
├── process_microscopy.py        ← the actual analysis pipeline (CLI + library)
├── WellPlateZipper.py           ← input-folder packing utility (96-well .zip set)
├── auto_threshold_core.py       ← Otsu helpers shared by pipeline + GUI (stdlib + numpy only)
├── well_token.py                ← canonical 96-well token parser (stdlib only)
├── shape_features_core.py       ← Qt-free per-cell shape-feature catalog shared by pipeline + GUI
├── theme.py                     ← v2 design tokens (Colors / Typography / Spacing / Radii)
├── well_viewer/                 ← Review pane (the bulk of the app)
├── widgets/                     ← reusable Qt widgets (no app coupling)
├── ui/theme/                    ← QSS themes + Template-string driver (palette + plot tokens)
├── services/                    ← Analyze-side service modules
├── scripts/                     ← build_executable.sh, dev helpers
├── _Docs/                       ← installer / requirements / icon SVGs + this Planning.md
├── Markdowns/                   ← live docs (this file, README, model contract, icons readme)
├── tests/                       ← pytest scaffold (added in #248; pure-Python no Qt)
└── design/                      ← HTML mockups + screenshots — visual reference only
```

The repo-root helpers `auto_threshold_core.py`, `well_token.py`, and
`shape_features_core.py` are *deliberately at repo root* (not under
`well_viewer/`): the pipeline contract forbids `well_viewer` / `widgets` / Qt
imports, so anything both the pipeline and the GUI need to agree on has to live
in a Qt-free module the pipeline can import. `shape_features_core.py` (added with
the shape-features work) is the single source of truth for the per-cell
`shape_*` morphology columns — the pipeline emits them, the GUI detects/labels
them. See §6.2 for the contract.

**Feature-stack dependencies (segmentation / QC / shape / tracking).** This work
adds a pinned ML stack to `_Docs/requirements-remote.txt` — Torch 2.0.1
(cellpose + trackastra) and btrack — deliberately held to the **Torch 2.0.1
line** because TensorFlow 2.13 caps `typing-extensions<4.6` while newer Torch
needs `>=4.8`; the whole stack (incl. lightning/kornia) is pinned to coexist.
laptrack was dropped (no version satisfies `numpy≤1.24.3` *and* `pandas≥2.0`);
the BaSiC flat-field is a pure-numpy reimplementation (no jax). `read_pipeline_info`
gains `.get`-defaulted keys (`shape_features` / `qc_options` / `phase_token` /
`schema_version`) so pre-feature sidecars load unchanged. Full rationale +
empirical resolution: `_Docs/Markdowns/TODO_segmentation_qc_shape_tracking.md`
Part 1.

The Review side (`well_viewer/`) is organised per-feature: one package per
visible tab / functional area, with cross-cutting infrastructure split out
by role. Inside `well_viewer/`:

```
well_viewer/
├── runtime_app.py                 ← WellViewerApp composition root
├── viewer_context.py              ← ViewerContext DI bundle
├── core/                          ← cross-cutting infrastructure
│   ├── app_state.py, event_bus.py
│   ├── channel_state.py, dataset_state.py, plot_state.py, selection_state.py
│   ├── app_shell_view.py, centre_view.py, sidebar_view.py, status_view.py
│   ├── widget_registry.py
│   ├── bus_wiring.py, redraw_coordinator.py, scope_registry.py
│   ├── load_controller.py, tab_controller.py, selection_controller.py,
│   │   channel_state_controller.py
│   ├── plot_orchestrator.py, plot_style.py
│   ├── ui_helpers.py, debug_flags.py, status_signal.py
├── data/                          ← IO + decode
│   ├── data_loading.py, image_discovery.py, image_resolver.py,
│   │   zipfile_cache.py, auto_threshold.py, crop_tool.py, metric_labels.py
├── domain/                        ← Qt-free models + pure logic
│   ├── batch_models.py, ratio_models.py, selections_model.py, plate_layout.py
│   ├── fold_change.py, fold_change_scopes.py, fold_change_controls.py
│   ├── gating_state.py, sample_definitions.py, viewer_state.py, stats_compute.py
├── features/                      ← one package per UI feature
│   ├── barplot/      controller.py, renderer.py, tab_view.py
│   ├── lineplot/     controller.py, tab_view.py
│   ├── scatter/      controller.py, callbacks.py, ratio_panel_view.py,
│   │                 tab_view.py, cells_tab_view.py, agg_tab_view.py
│   ├── heatmap/      controller.py, models.py, sidebar_view.py, tab_view.py
│   ├── distribution/ controller.py, tab_view.py
│   ├── stats/        controller.py, state.py, view.py
│   ├── review_image/ controller.py, renderer.py, state.py, csv_tab_view.py
│   ├── preview/      controller.py, callbacks.py, montage.py,
│   │                 preview_view.py, panel_view.py
│   ├── smfish/       controller.py, worker.py, tab_view.py
│   ├── image_table/  controller.py, tab_view.py, panel_view.py,
│   │                 grid_view.py, picker_view.py
│   ├── grouping/     controller.py, view.py, label_grid_view.py,
│   │                 label_editor_view.py, replicate_panel_view.py,
│   │                 cell_gating_tab_view.py
│   ├── export/       service.py, style.py, sidebar_view.py
│   └── batch_export/ tab_view.py, base_panel.py, bar_panel.py,
│                     scatter_panel.py, _common.py, _line_series_runner.py,
│                     well_grid_button.py
└── persistence/                   ← JSON read/write, debounced save registry
```

### File-name suffix conventions

The codebase uses a suffix-based scheme so the file name tells the reader the role. Within a feature directory the suffixes also indicate which layer the file belongs to (Qt builder vs. pure-math vs. data-only).

| Suffix | Role | Examples |
|---|---|---|
| `controller.py` / `_controller.py` | Orchestrator: event handlers, business logic, state mutation. Owns the feature's behaviour. | `features/barplot/controller.py`, `core/load_controller.py`, `core/channel_state_controller.py` |
| `renderer.py` | Pure matplotlib drawing. Decoupled from Qt — takes a figure / axes and a state snapshot. | `features/barplot/renderer.py`, `features/review_image/renderer.py` |
| `tab_view.py` | Qt builder for a feature's main tab page (returns the assembled `QWidget`). | every `features/*/tab_view.py` |
| `*_view.py` | Qt builder for a sub-panel, sidebar, or sub-dialog. | `sidebar_view.py`, `panel_view.py`, `csv_tab_view.py`, `picker_view.py`, `core/app_shell_view.py` |
| `state.py` / `_state.py` | Dataclass state container; no logic, only fields + defaults. | `core/app_state.py`, `core/channel_state.py`, `features/stats/state.py` |
| `_coordinator.py` | EventBus subscriber — subscribes signals and fans them out to controllers. | `core/redraw_coordinator.py` |
| `_registry.py` | Registry pattern (deferred lookup, debounced save, etc.). | `core/widget_registry.py`, `core/scope_registry.py`, `persistence/registry.py` |
| `_orchestrator.py` | Fan-out helper across multiple features. | `core/plot_orchestrator.py` |
| `_models.py` | Plain dataclasses (no behaviour). | `domain/batch_models.py`, `domain/ratio_models.py` |
| `_compute.py` | Pure-math layer shared across plot controllers. | `domain/stats_compute.py` |
| `service.py` | Non-Qt service entry point. | `features/export/service.py` |
| `callbacks.py` | Matplotlib click / interaction handlers for a feature. | `features/scatter/callbacks.py`, `features/preview/callbacks.py` |
| `worker.py` | Background `QThread` worker. | `features/smfish/worker.py` |
| `_panel.py` | Per-plot-type panel (only inside `features/batch_export/`). | `features/batch_export/bar_panel.py`, `scatter_panel.py` |
| `_` prefix | Private helper module (not part of the package's public surface). | `persistence/_doc.py`, `_io.py`, `features/batch_export/_common.py`, `_line_series_runner.py` |

**Known exceptions** (legacy or single-purpose modules that don't fit the grid; do not propagate these naming choices to new files):

- `features/grouping/view.py` and `features/stats/view.py` use bare `view.py` instead of `tab_view.py` — both predate the convention.
- `features/preview/montage.py` is a feature-internal helper that doesn't fit a suffix.
- `features/heatmap/models.py` is the only `models.py` in `features/` (the convention under `domain/` is `_models.py`).
- `data/` modules use bare names (`data_loading.py`, `auto_threshold.py`, `crop_tool.py`, `image_discovery.py`, `image_resolver.py`, `metric_labels.py`, `zipfile_cache.py`) — each is one self-contained utility, no internal suffix scheme.
- `domain/` mixes suffixed (`stats_compute`, `fold_change_scopes`, `gating_state`) and bare (`fold_change.py`, `selections_model.py`, `viewer_state.py`, `plate_layout.py`) names.
- `persistence/` modules are named for the `pipeline_info.json` block they persist (`bar_groups.py`, `ratios.py`, `cell_gating.py`) — no `_persistence.py` suffix.
- `runtime_app.py`, `viewer_context.py` — top-level singletons; no suffix.

A few rules of thumb so you know where to put something new:

- **It runs the pipeline →** put it in / next to `process_microscopy.py`. Keep
  the pipeline self-sufficient — it must run with no dependency on
  `well_viewer/` (see §6.2).
- **It's a Review-side tab body or sidebar for an existing feature →**
  add a `<name>_view.py` / `<name>_tab_view.py` inside
  `well_viewer/features/<feature>/`.
- **It's a brand-new Review-side feature (its own tab) →** create
  `well_viewer/features/<feature>/` and put `controller.py`,
  optionally `renderer.py`, and `tab_view.py` inside.
- **It's Review-side stateful behaviour for an existing feature →** add it
  to that feature's `controller.py` (or `renderer.py` for matplotlib
  drawing). Cross-cutting behaviour (load, selection, channel, tabs) goes
  in `well_viewer/core/`. Persistence stays in `well_viewer/persistence/`.
- **It's a reusable Qt widget with no All-Well-specific knowledge →**
  `widgets/`.
- **It's a design token / colour / size →** `theme.py` (for code) and
  `ui/theme/*.qss` (for QSS); never hardcode hex values in code.

---

## 4. Startup sequence

In one diagram, end-to-end:

```
all_well_launcher.main()                       (PyInstaller bundle only)
    │
    ▼ regular Python run jumps in here:
all_well.main()
    ├── QApplication(sys.argv)
    ├── theme_v2.Typography.family ← app.font().family()      ← collapse QSS family list
    ├── app.setStyleSheet(theme.qss())                        ← global QSS
    ├── AllWellApp().__init__
    │       ├── _build_ui()
    │       │    ├── title bar (mode seg, Open, Help, …)
    │       │    └── central stack:
    │       │         ├── page 0  ←  WellViewerApp() (Review)
    │       │         │       ├── _build_ui()
    │       │         │       │    ├── topbar
    │       │         │       │    ├── splitter:
    │       │         │       │    │   ├── sidebar  ← views.sidebar_view.build_sidebar
    │       │         │       │    │   └── centre stack
    │       │         │       │    │           ← views.centre_view.build_centre
    │       │         │       │    │           ├── pages dict {title → builder}
    │       │         │       │    │           ├── eager-builds the first page
    │       │         │       │    │           └── queues the rest in `pending`
    │       │         │       │    └── status bar
    │       │         │       └── (drain timer picks up `pending` builders)
    │       │         └── page 1  ←  AnalyzeTab()
    │       ├── _install_shortcuts()
    │       └── _restore_window_state()
    ├── window.show()
    └── app.exec()
```

Two important details:

1. **Lazy tab bodies.** `build_centre` returns immediately with most centre
   pages still un-built. A `QTimer.singleShot(0, …)` drain or a tab-change
   event triggers each builder on first need. This is what keeps the first
   paint fast even though the app has a lot of widget surface.
2. **Optional `--data_dir` argument** is queued with `QTimer.singleShot(150,
   …)` so the first paint completes before the dataset starts loading. This
   keeps the UI responsive while a few hundred CSVs stream in.

---

## 5. The Review side (`well_viewer/`)

The Review pane is the bulk of the codebase. Treat the package as four
layers:

```
┌──────────────────────────────────────────────────────────────────────┐
│  features/<feature>/                                                  │
│         ← Per-feature packages. Each owns the full vertical slice    │
│           for one tab / feature: controller(s), renderer(s) where    │
│           applicable, local state, and the tab_view / panel_view    │
│           modules that build its Qt widgets.                         │
│         ── barplot, lineplot, scatter, distribution, heatmap, stats, │
│            preview, review_image, smfish, image_table, grouping,    │
│            export, batch_export                                      │
├──────────────────────────────────────────────────────────────────────┤
│  core/    ← Cross-cutting infrastructure, no single feature owns it. │
│           ── shell views: app_shell_view, centre_view, sidebar_view, │
│              status_view                                             │
│           ── cross-cutting controllers: load_controller,             │
│              selection_controller, tab_controller,                   │
│              channel_state_controller                                │
│           ── plot dispatch / styling: plot_orchestrator, plot_style  │
│           ── runtime plumbing: event_bus, app_state, bus_wiring,     │
│              redraw_coordinator, scope_registry, widget_registry,    │
│              status_signal, debug_flags, ui_helpers                  │
│           ── cross-cutting typed state: channel_state,               │
│              dataset_state, plot_state, selection_state              │
├──────────────────────────────────────────────────────────────────────┤
│  data/    ← IO + decode (Qt-free where practical)                    │
│           ── data_loading, image_discovery, image_resolver,          │
│              zipfile_cache, auto_threshold, crop_tool, metric_labels │
├──────────────────────────────────────────────────────────────────────┤
│  domain/  ← Qt-free models + pure logic, shared by multiple features │
│           ── batch_models, ratio_models, selections_model,           │
│              plate_layout, gating_state, sample_definitions,         │
│              viewer_state, stats_compute, fold_change,               │
│              fold_change_scopes, fold_change_controls                │
├──────────────────────────────────────────────────────────────────────┤
│  persistence/  ← JSON read/write + debounced save registry           │
│                  (one module per on-disk file)                       │
└──────────────────────────────────────────────────────────────────────┘

runtime_app.WellViewerApp  ← composition root that wires it all together
viewer_context.ViewerContext  ← DI bundle (state / bus / widgets / app)
```

### 5.1 `runtime_app.WellViewerApp`

The `WellViewerApp(QWidget)` class is the application's **composition root**.

Historically it was a deliberate god-object: every centre tab and sidebar
panel wrote loose attributes onto it (`app._line_fig`, `app._selected_wells`,
…) so controllers could reach state from anywhere without circular imports.
The **teardown completed** (see §5.9 and `RUNTIME_APP_TEARDOWN.md`) — `runtime_app.py`
went from **6,909 → ~1,300 lines** (−81%) and `WellViewerApp` is now what its
name always claimed: a thin composition root whose body is mostly a flat
surface of one-line proxy methods over the controllers.

What lives on `WellViewerApp` today:

- **`self._app_state`** — an `AppState` aggregate of the typed domain-state
  objects (`channel`, `selection`, `dataset`, `plot`, `stats`, `review_image`,
  `preview`). Single source of truth for runtime state; call sites read
  `self._app_state.<domain>.<field>` directly, no `@property` shims.
- **`self._event_bus` / `self._widget_registry` / `self._ctx`** — the
  `EventBus`, the widget-handle store (which now `setattr`s onto `app` on
  `register`, so legacy `app._<widget>` reads resolve via plain attribute
  lookup), and the `ViewerContext` that bundles them for injection. All three
  are load-bearing (see §5.9).
- **`__init__`** (~140 lines) — wires the bus, the persistence registry,
  the widget registry, the viewer context, the redraw / selection
  coordinators, then calls `views.app_shell_view.build_ui(self)`.
- **One-line proxy methods** (~190 of them) — every legacy `app._method(…)`
  external call site still works because the method exists as a 1-line
  delegator that forwards to the controller function. Pattern:

  ```python
  def _set_active_channel(self, channel: str) -> None: return set_active_channel(self, channel)
  ```

  Adding new behaviour: write the function on a controller, then add a
  one-line proxy here only if existing callers reference it via
  `app._method(…)`.
- **No business logic.** Every `_build_*`, `_on_*`, `_redraw_*`, `_refresh_*`,
  `_compute_*`, `_draw_*`, `_review_*`, `_sel_*` body lives in either a
  feature package (`well_viewer.features.<feature>.controller` /
  `renderer` / `*_view`) or a cross-cutting module under
  `well_viewer.core` (e.g. `selection_controller`, `tab_controller`,
  `channel_state_controller`, `plot_orchestrator`, `load_controller`,
  `ui_helpers`). The class itself owns none of it.

When you add behaviour: put state on the relevant domain object in
`well_viewer/core/<domain>_state.py` (cross-cutting) or
`well_viewer/features/<feature>/state.py` (feature-local), put logic in the
feature's `controller.py` / `renderer.py` (or under `core/` if it's
cross-cutting), and have it take `app` (or, where the call site has one,
`ctx`) rather than becoming a method on `WellViewerApp`. Do **not** add a
new loose `self._*` attribute — a CI guardrail (`tests/test_guardrails.py`)
budgets the count and it only ratchets down (current ceiling: 71 distinct
`self._*` attrs).

### 5.2 The page stack: `core/centre_view.py`

`build_centre(app, parent)` returns nothing; it mutates `app._notebook`
(a `NamedPageStack` — a `QStackedWidget` subclass with name-keyed pages)
and registers every centre page. The current page-list:

| Section | Tab | Sub-pages |
|---------|-----|-----------|
| Analysis | **Plotting** | Line Graphs / Bar Plots / Scatter Plot / Distribution / Heat Map |
| Analysis | **Statistics** | — |
| Images | **Image Table** | — |
| Images | **Segmentation** | Segmentation / smFISH |
| Data | **Review CSV** | — |
| Data | **Sample Definitions** | (includes Cell Gating as a sub-section) |
| Data | **Batch Export** | — |

Two of the top-level entries (Plotting, Segmentation) are *parent* tabs
whose body is a nested `NamedPageStack`. `WellViewerApp._current_centre_tab()`
descends through that one level of indirection so the rest of the app can
always pattern-match on a leaf name (e.g. `"Line Graphs"`, `"smFISH"`).

The left **rail navigation** (`RailNav` from `widgets/rail_nav.py`) is the
single visible selector for the top-level pages. It mirrors the notebook —
clicking a rail item drives `_notebook.setCurrentByName(name)`; programmatic
changes to the notebook drive the rail back via
`WellViewerApp._on_notebook_current_changed`. The QTabWidget tab bar from
the v1 UI is gone; the page stack is purely name-keyed.

### 5.3 Centre tab builders: `features/<feature>/tab_view.py`

Each module exposes a single `build_<name>_tab(ctx, parent)` function —
`ctx` is a `ViewerContext`. After the Phase 6/7 DI narrowing, each builder
references `ctx.app` / `ctx.state` / `ctx.widgets` directly rather than
unwrapping into a local `app` (the guardrail `TAB_CTX_APP_UNWRAP_BUDGET = 0`
enforces this). The function:

1. Creates a `make_plot_with_right_dock(parent)` host (returns
   `(plot_area, layout, right_dock)`) — the right-side dock is reserved for
   the Properties / Export Style sidebar.
2. Adds the per-tab controls row (channel ctxbar, metric combo, timepoint
   combo, action buttons — Export CSV, Copy SVG, Save figure, **Properties**).
3. Mounts a `PlotCard` from `widgets/plot_card.py` (matplotlib figure +
   custom toolbar inside a styled panel).
4. Stashes the figure / canvas on `app` so controllers can reach them
   later (`app._line_fig`, `app._bar_canvas`, …).
5. Wires signals to the feature's controller / renderer entry points
   (e.g. matplotlib clicks → `features.scatter.controller.on_scatter_click`,
   button presses → `features.barplot.renderer.redraw_bars`).

Each feature package owns the full vertical slice for its tab — the
tab-builder, the controller, the renderer (where applicable), and any
panel-shaped sub-views the tab embeds. `centre_view` (under `core/`) just
calls the per-feature `build_<name>_tab` from a single registration loop.

One module sits in `domain/` rather than under a feature package:
`domain/fold_change_controls.py` exposes
`install_fold_change_controls(app, parent, layout, scope=…)` — the
Control + Baseline dropdown pair installed on both the Bar Plots and
Line Graphs control rows. It lives in `domain/` because it's shared
between two features and owns the cross-tab widget sync
(`_sync_widgets_to_state`) that mirrors each state change into the
other tab's widget instances, so the shared `app._fc_*` state stays
visually in lockstep across tab switches.

### 5.4 The left sidebar

`core/sidebar_view.build_sidebar(app, parent)` builds the well picker
(`widgets/well_plate_selector.WellPlateSelector`) plus the **Select all /
Select none** buttons, the selection-count chip, and the group hint label.
Selection signals route through `well_viewer/core/selection_controller.py`
functions (`on_sidebar_plate_selection_changed`, `…_drag_finished`,
`…_well_activated`, `…_row_activated`, `…_col_activated`,
`…_well_dropped`); `WellViewerApp._on_sidebar_plate_*` are 1-line proxies
to those functions, kept so the sidebar's `signal.connect(app._on_…)` wires
still resolve.

Other sidebar bodies live with their feature: the preview picker is
`features/preview/preview_view.py`, the image-table picker is
`features/image_table/picker_view.py`, the Sample-Definitions group editor
is `features/grouping/view.py`, and the Statistics group editor is
`features/stats/view.py`. They're stacked inside the same sidebar QWidget
and shown/hidden by `core/tab_controller.on_tab_change` based on the
active tab.

### 5.5 Controllers

Every non-trivial behaviour lives in either its feature's `controller.py` /
`renderer.py` (under `well_viewer/features/<feature>/`) or in
`well_viewer/core/` for cross-cutting responsibilities. The convention is:

- Functions (`def foo(app, …)`) that take the `WellViewerApp` — or, where
  the call site has one, a `ViewerContext` — plus any extra args.
- Read and mutate runtime state through the typed domain-state objects:
  `app._app_state.channel`, `app._app_state.selection`, … (or
  `ctx.state.<domain>`). The state modules (under `well_viewer/core/` for
  cross-cutting, `well_viewer/features/<feature>/state.py` for feature-local)
  are import-safe — they never reach back into `runtime_app`, so a
  controller can import them directly with no circular dependency (the
  `test_state_package_import_contract` guardrail enforces that boundary).
  Prefer that to reaching for loose attributes.
- Don't import `WellViewerApp` itself — that would be circular. The remaining
  un-extracted behaviour is reached via the `app` argument.
- One module per *behaviour domain*: a feature's `controller.py` /
  `renderer.py`, not a per-tab handler module. The behaviour outlives the
  tab name.

Cross-cutting controllers (in `well_viewer/core/`):

| Module | Role |
|--------|------|
| `core/load_controller.py` | Open a dataset folder, scan CSVs, populate caches, schedule first redraw. |
| `core/selection_controller.py` | Plate-selection events; commits a selection change into the redraw pipeline. |
| `core/tab_controller.py` | `on_tab_change` dispatch, tab history, scope flush. |
| `core/channel_state_controller.py` | Channel-state machinery: `recalculate_threshold`, `set_active_channel`, `update_channel_selector`, `refresh_metric_combo_for_channel`. Owns the per-channel `(min, max)` cache that turns repeat channel toggles from an O(total cells) scan into an O(1) lookup. |
| `core/plot_orchestrator.py` | Default-scope fan-out (line + distribution + heat map). Per-tab scopes (bar / scatter / scatter-agg) have their own entry points that consume scope-specific state. |
| `core/plot_style.py` | `apply_ax_style` — token-aware axes styling for matplotlib. |
| `core/bus_wiring.py`, `core/redraw_coordinator.py` | Wire the `EventBus` signals to the redraw coalescer. |
| `core/scope_registry.py` | Register each feature's fold-change scope with its redraw callable (`features.barplot.renderer.redraw_bars`, `core.plot_orchestrator.redraw_app`). Called once from `wire_event_bus` during construction so the domain-layer `fold_change_scopes` module stays free of `features/` imports. |
| `core/ui_helpers.py` | Cross-cutting widget helpers (combo population, button factories, theme application). |

Per-feature controllers / renderers (under `well_viewer/features/`):

| Feature package | Role |
|-----------------|------|
| `features/lineplot/controller.py` | Line Graphs: redraw the 3-stacked-subplot figure. |
| `features/barplot/{controller,renderer}.py` | Bar Plots: redraw + bar-draw math (split because batch export reuses the math). |
| `features/scatter/{controller,callbacks}.py` | Scatter Plot (cells + aggregate). The per-axis fluor-gate (`fluor_gate_x` / `fluor_gate_y`) is bypassed when an axis column ends with `_smfish_count` — the gate is a fluorescence-intensity floor and would strip every integer-count cell on a count axis. The mixed-axis case (one count, one intensity) still gates the intensity axis. |
| `features/heatmap/{controller,models}.py` | Heat Map: layout + redraw. |
| `features/distribution/controller.py` | Distribution tab: histogram / KDE / violin. |
| `features/stats/controller.py` | Statistics tab: groupwise tests, KS CDF. |
| `features/image_table/controller.py` | Image Table grid: per-cell config, distribute, export. |
| `features/preview/{controller,callbacks,montage}.py` | Preview-panel helpers; `montage.py` is the legacy Movie Montage popout, folded into Image Table — kept for image-LUT helpers. |
| `data/image_identity.py` | **Single source of truth for image filenames, cell identity, and the suffix/role catalog.** Owns three concerns deliberately co-located so they cannot drift: (1) `CellKey` — `(well, fov, timepoint, nucleus_id)` with one canonical normalisation chain; every site that joins CSV-row data to image-derived data builds keys via `CellKey.from_row` / `cell_keys_for_dataframe` (the smFISH writer, `cell_overrides`, `review_image`'s row keys, and `_process_well`'s counts construction all consume it). (2) `parse_image_filename` / `build_image_filename` — exact inverses driven by a `PipelineSchema` loaded once from `pipeline_info.json` via `load_schema(...)`. No parallel regex fallbacks; single-FOV pipelines are modelled by `has_fov=False` rather than a special-case extractor synthesising `"1"` (the empty-FOV-to-`"1"` rule is enforced uniformly by `normalize_fov`). (3) `ROLE_CATALOG` — declarative `RoleSpec` per role (`fluor`/`tophat`/`mask`/`overlay`/`smfish`) mapping suffix, output extensions, channel-scoping, and precedence; `classify_filename_role` (read side) and `role_output_filename` (write side, called from `process_microscopy.build_processed_output_path`) both consume it. The full time-format matrix (`T01`/`24`/`24h`/`1.0`/`day3`/`02d04h30m`/`timepoint_hours` float) collapses to a single canonical form via `normalize_tp`. Stdlib-only at import; pandas is lazy inside `normalize_fov_series` and `cell_keys_for_dataframe` so `process_microscopy.py` can import the module without adding pandas as a pipeline dependency. |
| `features/smfish/{controller,worker}.py` | smFISH spot detection + the background worker. Apply Global Threshold rewrites every per-well CSV's `<channel>_smfish_count` column, then in its after-csv callback persists the threshold to `pipeline_info.json::smfish_thresholds` via `persistence/smfish.py`, drops the per-well DataFrame cache + the aggregation stats cache, redraws plots, and refreshes the Review CSV table — without those last three steps, downstream plots and the review table kept showing pre-apply counts. The after-csv callback is routed through `_WorkerBridge.after_csv` (a queued `Signal(object)`) rather than invoked directly from `apply_global_threshold_async`'s daemon thread — the worker thread has no Qt event loop, so the previous `QTimer.singleShot(0, …)`-from-worker scheme silently dropped the refresh on the floor and the user had to close and reopen the app to see the new counts. The writer takes the explicit `well_to_csv` map the loader populated (not a `out_dir.glob("*_<well>.csv")` lookup, which silently skipped wells whose CSVs had no underscore prefix), drops any case-equivalent column before reassigning (so a stale `W3Cy5_smfish_count` from an older pipeline run is overwritten cleanly), and builds both producer (`_process_well`) and consumer (`_write_counts_to_csvs`) keys via `data/image_identity.CellKey` — the unified identity module enforces the same well/fov/tp/nid normalisation chain on both sides, so empty-fov, `"-1"`, letter-prefixed tp like `"T01"`, padding like `"001"`, and `.0` float artefacts on nid all collapse identically. A `matched == 0` WARN fires from the writer when every lookup misses so future `(fov, timepoint, nucleus_id)` format drift names itself in the log drawer. |
| `features/review_image/{controller,renderer}.py` | Segmentation tab review-image + Review-CSV jump-to-image. |
| `features/grouping/controller.py` | Sample-Definitions group editor logic. |
| `features/export/{service,style}.py` | CSV export (`service.py`) + the floating Export Style sidebar's prefs + apply pipeline (`style.py`); `style.py` also owns `copy_figure_to_clipboard` (Copy SVG → native macOS pasteboard via pyobjc, Qt-clipboard fallback). |

Shared domain helpers (under `well_viewer/domain/`):

| Module | Role |
|--------|------|
| `domain/fold_change.py` | Fold-change normalization helpers (vs control well/rep-set, vs each member's first timepoint). Consumed by line / bar plot, batch export, and `features/export/service.py`. |
| `domain/stats_compute.py` | `well_aggregate_stats`, `compute_rep_stats`, `aggregate_well`, `aggregate_group` — the pure-math layer the bar / line / scatter controllers share. `aggregate_well` / `aggregate_group` delegate to `app._aggregate_well` / `app._aggregate_group` when those methods exist, which is the hook the pure-logic test `MockApp` uses to short-circuit aggregation; the production `WellViewerApp` does not define them, so the DataFrame path is taken at runtime. |
| `domain/batch_models.py`, `domain/ratio_models.py`, `domain/selections_model.py` | Plain dataclass models. |

Data-layer helpers (under `well_viewer/data/`):

| Module | Role |
|--------|------|
| `data/auto_threshold.py` | Otsu-based per-channel default threshold estimator. Delegates the shared helpers (`parse_tp_hours`, `pick_endpoint_timepoints`, `sample_cell_and_bg`) to the top-level `auto_threshold_core.py` so the pipeline and the GUI agree exactly. |
| `data/zipfile_cache.py` | Process-wide LRU of open `ZipFile` handles, lock-guarded so `read_member_bytes` / `scan_zip_members` share one open file across the thousands of per-member reads the Image Table / heatmap / smFISH-worker paths issue. Invalidated on dataset swap. |

### 5.6 The data layer

The Review pane reads two things off disk:

1. **Per-well CSV files** produced by `process_microscopy.py`. One CSV per
   well, one row per (cell × FOV × timepoint).
2. **Per-well image ZIPs** (`<well>_out.zip`) — masks, top-hat-corrected
   fluorescence, overlays, smFISH-processed channels. Used by Image Table,
   Segmentation, smFISH, and the heatmap.

Plus the sidecar:

3. **`pipeline_info.json`** — schema fields, channel tokens, available
   timepoints, saved selections, persisted gating thresholds, etc.

The relevant modules:

| Module | Job |
|--------|-----|
| `data/data_loading.py` | CSV parsing, channel detection, threshold-aware aggregation, per-well array helpers. Measurement columns are normalised to lower-case at read time so per-channel lookups are case-insensitive; for `smfish_count` metrics the bar / line / heatmap / scatter-aggregate paths pass `threshold=-1.0` (not `0`) so zero-spot cells contribute to the mean and the population count. `resolve_value_series` emits a deduped WARN naming the missing column when both the direct and case-folded lookups miss, surfacing schema-mismatch bugs that would otherwise show up as silently-empty plots. |
| `data/image_discovery.py` | Scans zips / loose directories for masks, overlays, tophat images. Classifies by suffix + schema. |
| `data/image_resolver.py` | Filename → kind classifier (`mask` / `overlay` / `fluor_processed` / `smfish`). |
| `domain/viewer_state.py` | `read_pipeline_info` + the `make_schema_extractor(sep, fov_idx, tp_idx)` factory used everywhere the app needs a `(fov, tp)` from a filename. |
| `domain/plate_layout.py` | `PLATE_ROWS`, `PLATE_COLS`, the 48-colour well palette. |
| `domain/selections_model.py` | Saved-selections schema v2: shape, validators, the v1→v2 migration. The on-disk contract lives in `Markdowns/SELECTIONS_MODEL_CONTRACT.md`. |
| `domain/sample_definitions.py` | Read / write the `sample_definitions` block inside `pipeline_info.json`. |
| `domain/batch_models.py` | `BarGroup` + `ReplicateSet` — plain dataclasses used by every plot tab + batch export. |
| `domain/ratio_models.py` | Channel-ratio definitions (`gfp/mcherry`-style virtual channels). |
| `features/heatmap/models.py` | Heat-map-specific layout dataclasses (used by the heatmap controller + persistence). |

### 5.7 Persistence

Every "save state into the dataset folder" path lives under
`well_viewer/persistence/`. One module per JSON file:

| File on disk | Module | What it holds |
|--------------|--------|---------------|
| `pipeline_info.json::cell_gating` | `cell_gating.py` | Cell-area + per-channel `fluor_gates` + `thresh_frac_on`. |
| `pipeline_info.json::smfish_thresholds` | `smfish.py` | Per-channel smFISH spot-detection threshold (`{channel: float}`). Saved only on a successful Apply Global Threshold so the value the spinbox shows matches the spot counts baked into the per-well CSVs. Restored on dataset open and on channel-combo switch. |
| `pipeline_info.json::sample_definitions` | `sample_definitions.py` (top level) | Saved selections (`_selections`). |
| `persistence.json::ratios` | `ratios.py` | User-defined ratio channels. |
| `persistence.json::heatmap_layouts` | `heatmap_layouts.py` | Saved heatmap layouts + cmap/scale state. |
| `persistence.json::line_order` | `line_order.py` | Line plot replicate-set / well draw order. |
| `persistence.json::view_state` | `view_state.py` | Active tab, selected wells, channel/metric, fold-change scopes, per-tab combos, export-style prefs. Saved on dataset switch + app close. |
| `cell_overrides.json` | `cell_overrides.py` | Per-cell `Included` overrides set on the Segmentation tab (kept separate — large + write-heavy). |
| `bar_groups.json` | `bar_groups.py` | User-picked file for bar-plot group state (no auto-save). |

`persistence.json` is a single consolidated viewer-state document managed by
`_doc.py`. On first load it migrates any legacy `ratios.json`,
`heatmap_layouts.json`, or `line_order.json` sidecars into the unified file
and deletes them.

Persistence modules expose `load_from_data_dir(app)` / `save_to_data_dir(app)`
function pairs. They are registered, by name, with the **`PersistenceRegistry`**
(`well_viewer/persistence/registry.py`, built once in `WellViewerApp.__init__`
as `app._persistence`); call sites use `app._persistence.{save,load,
schedule_save}(name)`. The registry replaced ~17 thin `WellViewerApp` wrapper
methods (teardown Phase 4).

**Atomic writes.** Every save goes through
`well_viewer/persistence/_io.atomic_write_json` (or `atomic_write_text` for
the smFISH worker's per-well CSV writer): tmp file + fsync + `os.replace`.
A crash, signal, or full-disk mid-write can no longer leave a truncated
sidecar that the next viewer load reads as empty state and silently
clobbers. The tmp filename is uniquified (`<path>.<pid>.<rand>.tmp`) so
concurrent writers / retry-cleanup races can't collide on a single fixed
`<path>.tmp` name. Both `open(tmp, "w")` and the final `os.replace` retry
on `PermissionError` with exponential backoff (5 attempts, 100/200/400/800/1600 ms)
so the Windows AV-scanner / Excel-viewer / OneDrive-sync race doesn't surface
`[WinError 5] Access is denied` mid Apply-Global-Threshold. The smFISH writer
catches per-well `PermissionError` so one locked well's CSV (e.g. user has
it open in Excel) doesn't kill the rest of the run — the locked well is
skipped with a WARN naming the file + likely cause. The registry owns the **one** debounced-save path — `schedule_save`
with a shared pending-set — so signal storms (ratio-panel field edits, heatmap
layout drag-and-drop, cell-override toggles) coalesce into one disk write per
500 ms burst; this replaced five identical per-domain `schedule_save`/`_flush`
pairs and their `_<name>_save_pending` flags. `cell_overrides.json` v2 stamps
each row with the well's
`<well>_out.zip` mtime so a pipeline re-run drops the now-stale overrides
on load instead of re-binding them to unrelated cells.

### 5.8 The Export Style sidebar

Each plot tab pre-allocates a right-side dock via
`make_plot_with_right_dock(parent)`. The dock is hidden by default and a
**Properties** button on the tab's controls row toggles it. When opened, the
dock hosts an `ExportStyleSidebar` (from
`features/export/sidebar_view.py`) backed by a single per-app dict
(`app._export_style_prefs`). The widget binds to that dict via
`_bind_getter_setter`, so changes flow:

```
Properties widget → app._export_style_prefs
                        ↓
                  apply_export_style_prefs(fig, prefs)
                        ↓
                  canvas.draw_idle()
```

`features/export/style.py` owns the prefs dict + the apply pipeline. Every
redraw path ends with a call to
`features.export.style.apply_export_style_to_current(app, fig, canvas)` so
the sidebar's toggles (grid on/off, axis label size, log-scale, …) survive
a re-render.

### 5.9 Typed state, the event bus, and the DI context

The `runtime_app` god-object teardown introduced four pieces that, together,
let controllers and tabs depend on typed seams instead of the loose
`WellViewerApp` namespace. The state modules — cross-cutting ones under
`well_viewer/core/` and per-feature ones under
`well_viewer/features/<feature>/state.py` — are **import-safe**: they
import only stdlib, numpy/pandas, the Qt-free domain models
(`domain/ratio_models`, `domain/selections_model`, `domain/batch_models`,
`data/data_loading`) and — for `core/event_bus.py` only — `PySide6.QtCore`.
None of them imports `runtime_app`, so any controller can import them with
no circular dependency. `tests/test_guardrails.py` enforces that boundary.

**Typed domain-state objects.** One `@dataclass` per behaviour domain, each
owning its own invariants (behaviour lives *in* the type, not just data):

| Object | Owned by | Lives in |
|--------|----------|----------|
| `ChannelState` | active channel / metric / ratio catalogue / threshold range — `set_active_channel` keeps `active_val_col` consistent | `core/channel_state.py` |
| `SelectionState` | plate selection + the saved-selections ("v2") model; composes `selections_model` | `core/selection_state.py` |
| `DatasetState` | data dir, well paths, row cache, canonical timepoint / FOV lists, `pipeline_info` | `core/dataset_state.py` |
| `PlotState` | SEM/SD, per-FOV, legend, draw order, fold-change normalization | `core/plot_state.py` |
| `StatsState` | Statistics-tab comparison groups + result cache | `features/stats/state.py` |
| `ReviewImageState` / `PreviewState` | Review-image view transform + caches; preview/montage image maps | `features/review_image/state.py` |

`AppState` (`core/app_state.py`) is a plain aggregate of all of the above —
*not* a mega-dataclass with behaviour. `WellViewerApp` builds one as
`self._app_state`. The cross-cutting state objects live in `core/`; each
feature's local state lives next to its controller / renderer in the same
feature package.

**`EventBus` (`state/event_bus.py`).** A `QObject` carrying a small, capped
catalogue of exactly five cross-cutting signals — `channel_changed`,
`selection_changed`, `dataset_loaded`, `plot_options_changed`, and
`redraw_requested`. Signal payloads are the relevant *state object*, never
`app` (`redraw_requested` carries a scope name). The catalogue and its contract
are documented in the module docstring; status-bar messages are deliberately
*not* on the bus — `status_signal.py` owns those. As of teardown Phase 3 the
bus is **load-bearing**: every signal has a real subscriber. Subscribers are
constructed and connected by `bus_wiring.py`'s `wire_event_bus`, called once
from `WellViewerApp.__init__`:

- `core/redraw_coordinator.py`'s `RedrawCoordinator` subscribes
  `channel_changed` and `plot_options_changed` (forwarded to the scope
  coalescer) and `dataset_loaded` (a full post-load `_redraw`).
- `SelectionRefreshCoordinator` (`core/selection_controller.py`) subscribes
  `selection_changed` and runs `apply_selection_refresh` — sidebar refresh
  plus a per-tab view refresh.

The paired direct calls in `set_active_channel`, `set_fold_change_state`, the
three selection mutators, and `load_controller` have been removed — the emit
*is* the trigger. Two refreshes are deliberately kept off the bus until the
owning code relocates in a later phase: `_toggle_sem` (the SEM/SD toggle — its
redraw covers Distribution / Heat Map, which the coalescer does not) and the
smFISH tab resync after a dataset load (a tab-specific data resync, not a
redraw). The `tests/test_redraw_fanout.py` and `tests/test_selection_refresh.py`
harnesses pin the invariants migration must preserve.

**`WidgetRegistry` (`core/widget_registry.py`).** A name-keyed store for Qt
widget handles (figures, canvases, axes). `register("_line_fig", fig)`
stashes the handle by name *and* `setattr`s it onto the bound app, so legacy
`app._line_fig` readers resolve via plain attribute lookup — no
`__getattr__` forwarder is needed. (The Phase 5 plan originally specified
~300 mechanical edits to migrate every reader to `ctx.widgets.get(…)`; this
setattr shortcut sidestepped that without losing the registry as the
canonical store.)

**`ViewerContext` (`viewer_context.py`).** A frozen dataclass bundling
`state` / `bus` / `widgets` plus a residual `app` field for the remaining
proxy methods on `WellViewerApp`. `WellViewerApp.__init__` builds `self._ctx`
and passes it to every tab builder. The `app = ctx.app` unwrap that used to
appear at the top of every tab builder is gone (Phases 6+7 — 12 unwraps
collapsed); builders read `ctx.app.<field>` directly where they still need
the app, or `ctx.state.<domain>.<field>` / `ctx.bus` for the typed seams.
The `TAB_CTX_APP_UNWRAP_BUDGET` guardrail is now zero.

**Teardown — complete.** All twelve phases of the teardown shipped on PR #271:

| Phase | Outcome |
|---|---|
| 1 | Helper extraction + dead-code deletion (`ui_helpers` + the six helpers it absorbed) |
| 2 | `EventBus` catalogue trimmed to 5 signals + `tests/test_redraw_fanout.py` regression harness |
| 3 | `EventBus` made load-bearing — `RedrawCoordinator` + `SelectionRefreshCoordinator` subscribe every signal; paired direct calls retired |
| 4 | `PersistenceRegistry` replaced ~17 wrapper methods + 5 debounce flags |
| 5 | `WidgetRegistry.register` setattrs onto app — sidesteps the 300-edit mechanical migration |
| 6 + 7 | Every `app = ctx.app` unwrap removed from per-feature `tab_view.py` builders |
| 8 | `_build_*` builders relocated into per-feature `*_view.py` modules and `core/app_shell_view.py` |
| 9a | Bar / Scatter / Review-image / Selection / Stats / RI-channel handler bodies relocated into their controllers |
| 9b | `_on_tab_change` + `_on_notebook_current_changed` relocated into `tab_controller` |
| 10 | `__getattr__` widget forwarder deleted (Phase 5 made it unnecessary); the 12 `ctx.app` unwraps gone |
| 11 | Pure-passthrough delegator collapse — call-form sweep (59 deleted) + callable-reference sweep (37 collapsed via `functools.partial`) |
| 11+ | Post-Phase 11 sweeps relocated every remaining inline body, then compacted the resulting delegator surface |

The net effect on `runtime_app.py`:

- **6,909 → ~1,300 lines** (−81%)
- distinct `self._*` attrs: **166 → ~71** (−57%)
- `RUNTIME_APP_PERSIST_WRAPPER_BUDGET` and `TAB_CTX_APP_UNWRAP_BUDGET` both
  ratcheted to **0**
- Every `_build_*` / `_on_*` / `_redraw_*` / `_refresh_*` / `_compute_*` /
  `_draw_*` / `_review_*` / `_sel_*` body lives in a controller or view
  module; what remains on `WellViewerApp` is `__init__`, the bus / widget /
  context wiring, and ~190 one-line proxy methods preserving legacy
  `app._method(…)` call sites.

`tests/test_guardrails.py` holds the ratcheting line-count / attribute-count
ceiling at the current floor; it can only ratchet down from here.

---

## 6. The Analyze side

### 6.1 `analyze_tab.py`

A single-file Qt form that builds the input panel (folder pickers, channel
tokens, top-hat radii, segmentation method, smFISH options, worker count)
plus a live-streaming log panel and a Run / Stop button. The Run path:

1. Validate the form.
2. Call `services.pipeline_service.find_pipeline_script()` to locate
   `process_microscopy.py` (or the frozen-bundle launcher).
3. Call `services.pipeline_service.build_pipeline_args(pipeline, input_dir,
   output_dir, opts)` to assemble argv.
4. `services.pipeline_service.spawn_pipeline(args)` returns a
   `subprocess.Popen` running the pipeline in its own process group / session
   so Stop can signal the entire tree.
5. The Analyze tab streams stdout/stderr into its log view (`tail -f`-style).
6. On completion, the dataset folder is offered to the Review tab.

### 6.2 `process_microscopy.py`

The pipeline. **Self-sufficient by design** — no import from `well_viewer/`,
`widgets/`, or the Qt UI. The pipeline must run in environments that don't
have the GUI installed (CI workers, headless boxes).

That is also why the auto-threshold estimator was inlined into
`process_microscopy.py` (`_estimate_thresholds_standalone`,
`_apply_thresholds_to_pipeline_info`, `_sample_cell_and_bg`,
`_pick_endpoint_timepoints`) — earlier versions imported the equivalent from
`well_viewer.auto_threshold`, which crashed the pass on hosts without the
viewer. The viewer's copy is still used by the GUI's "Auto-threshold"
button on the Cell Gating tab; the pipeline's copy is now the standalone
fallback.

Inputs / outputs / contract:

```
Input dir (one of)
├── A01.zip / A02.zip / … H12.zip          ← per-well zip mode (preferred)
└── flat directory of TIFs                  ← legacy mode

Output dir
├── <well>_out.zip                          ← masks + tophat + overlays per well
├── <prefix>_<well>.csv                     ← per-well measurement CSV
├── pipeline_info.json                      ← schema / channels / timepoints / cell_gating
└── (transient tmp_<well>/ directories deleted on success)
```

**Per-cell shape features.** `compute_shape_features(labels, selected)` runs one
`skimage.regionprops_table` pass per FOV and merges `shape_<feature>` columns
(area/eccentricity/solidity/perimeter/…, plus the **always-on**
`shape_centroid_row/col` that cell tracking links across timepoints) into each
cell record; the selection comes from `--shape_features` and is recorded in
`pipeline_info.json`. The feature catalog lives in the Qt-free repo-root
`shape_features_core.py` so the pipeline (emit) and the GUI (detect/label) can't
drift. On the Review side `data_loading.detect_shape_features` finds the columns
and the channel dropdown surfaces them as **sectioned virtual channels** (a
"Shape Features" group via `ui_helpers.set_combo_values_sectioned`), with the
metric combo collapsed and all-cells (`threshold=-1`) aggregation — they are
channel-independent, so they bypass the per-channel intensity gate.

### 6.3 `all_well_launcher.py`

PyInstaller entry point. Two responsibilities:

1. Set the matplotlib backend to `QtAgg` before any `pyplot` import.
2. Re-dispatch to `process_microscopy.main()` when invoked with
   `--run-pipeline` as the first argument. The Analyze tab uses that path
   when running inside a frozen `.app` bundle — there's no Python
   interpreter on `sys.executable`, so re-exec'ing the launcher with the
   sentinel argv routes the same Python process into the pipeline.

### 6.4 `services/`

Small dependency-light service modules used by `analyze_tab.py`:

| Module | What |
|--------|------|
| `pipeline_service.py` | `find_pipeline_script` / `build_pipeline_args` / `spawn_pipeline`. |
| `pipeline_runner.py` | Asynchronous subprocess + stdout-streaming wrapper used by Analyze. |
| `input_resolution_service.py` | Validates the input folder shape (zip set vs flat). |

These modules deliberately have no Qt imports beyond what the Analyze tab
itself uses — keeping them potentially reusable for a CLI driver.

---

## 7. The widget library (`widgets/`)

Every widget under `widgets/` is:

- **Standalone.** It only imports from `theme` and Qt (plus `widgets._support`
  for tiny RGB helpers). It does *not* know anything about
  `WellViewerApp` or any tab.
- **Token-styled.** Sizes derive from font metrics; colours come from
  `theme.Colors` / QSS object names. No hardcoded device pixels.
- **DPI-aware.** Geometry is computed from `fontMetrics()`, so the widgets
  scale correctly at 1×, 1.5×, 2×.
- **Singly responsible.** One widget per file. The file may contain a
  helper class or two but never another reusable widget.

Notable members worth knowing about by name:

| Widget | What it is |
|--------|-----------|
| `PlotCard` | The `QFrame` chrome around every matplotlib figure. Provides a custom toolbar (`MplToolbar`), Screen↔Publication mode toggle, and stats chip slot. |
| `WellPlateSelector` | The 8×12 plate selector used by the sidebar and the sample-definitions editor. Supports per-well colour overrides, drag-to-select, drag mime types for the heatmap layout. |
| `NamedPageStack` (in `views/centre_view.py`, not `widgets/` — but morally a widget) | A `QStackedWidget` keyed by page name. The replacement for the legacy `QTabWidget` + custom tab bar. |
| `RailNav` | The left-rail section navigator. |
| `SegmentedControl`, `ChipGroup`, `Stepper`, `StyledSlider`, `ToggleSwitch` | Form controls; each exposes `bindingAdapter()` returning `(getter, setter, change_signal)` for the Export Style sidebar's binding loop. |
| `SavedSelectionsList` | The rich saved-selection list with composition (drag-to-reorder, replicate sub-lists, recolour popovers). |
| `LutSelector`, `GradientStrip`, `ColorPickerPopover` | The colour-map picker + its preview strip + the freeform colour-picker popover. |
| `Popover`, `Drawer`, `Toast`, `EmptyState` | Generic overlay primitives. |
| `TitleBar`, `WindowResizeGrips`, `_window_chrome` | Frameless-window chrome (currently disabled by default — see `_window_chrome.should_use_frameless`). |
| `MplToolbar` | The slim toolbar shown under every matplotlib figure (home / pan / zoom / save + coords readout). |
| `BrandTile`, `IconButton`, `StatusDot`, `KbdHint`, `SelectionChip`, `PillTabBar` | Misc small bits. |

If you find yourself building something that isn't All-Well-specific, write
it as a new `widgets/<x>.py` module. Each widget gets a `__main__` block
that opens a small demo window so you can iterate visually with
`python widgets/foo.py`.

---

## 8. Theming

The app has two complementary theme surfaces. The previous version of this
doc claimed `ui/theme/` was "dormant scaffolding"; that was wrong — both
systems are load-bearing, and they cover different concerns:

1. **`theme.py`** (repo root) — Python-side design tokens for the v2
   redesign. `Colors`, `Typography`, `Spacing`, `Radii`, plus a `qss()`
   builder that produces the full application stylesheet by
   string-templating the tokens into `theme.qss`'s inline template. The
   `widgets/` package and most of the app shell read from
   `theme.Colors.*`; the v2 dark palette is hardcoded here.
2. **`ui/theme/`** — a separate palette + QSS-template system used by
   matplotlib-side plot styling, the Analyze tab, a handful of
   batch-export panels, and the per-theme `.qss` files for an
   in-progress theme switcher (`dark.qss` / `light.qss` / `amber.qss` /
   `beige.qss`). Plot tokens live here (`PLOT_BG`, `TXT_PRI`, `TXT_MUT`,
   `FM_UI`, `WELL_COLOR_1..48`); call `get_color("ACCENT")` for the
   active theme's value. The well palette is sourced from
   `theme.WELL_COLORS_TUPLE` so both systems hand out identical
   per-well colours.

**Why two:** the v2 design (theme.py) was added on top of an existing
palette system (ui/theme). The two stabilised side-by-side rather than
being merged — overlapping conceptual tokens (panel / text-primary /
accent) have *different* hex values in each system, picked
independently for each palette's aesthetic. They are not currently
reconciled.

When you add a colour or size:

- For widgets in `widgets/`, the app shell, or the v2-styled chrome:
  add to `theme.Colors` / `theme.Spacing` / etc.
- For plot styling, the Analyze tab, or anything reading via
  `get_color()`: add to `ui/theme/styles._DARK_THEME` (and the other
  palettes when relevant) and reference it from QSS via
  `${TOKEN_NAME}` in the `.qss` template.
- **Do not** hardcode hex / px values inline.

The publication-export plot surface uses `theme.CPub` and feeds the
target figure's axes (`figure.facecolor`, `axes.facecolor`, etc.) when
a `PlotCard` flips into Publication mode. (PR #249 changed this from a
global `matplotlib.rcParams.update` to per-figure styling so toggling
one PlotCard no longer changes the rcParams seen by later figures.)

**Widgets with per-instance QSS.** Many widgets in `widgets/` set their
own stylesheet at construction (`self.setStyleSheet(self._build_qss())`
or `self.setStyleSheet(self._qss())`) — that's load-bearing for
per-widget object-name scoping, but it freezes the colour tokens at
construction time. Today the global QSS is static and this is fine; if
a runtime theme switcher ever ships (the `ui/theme/theme_manager.py`
scaffold is there for exactly that), widgets need to rebuild their
inline QSS when Qt fires `QEvent.StyleChange`.

`widgets/_support.install_qss_refresh(widget, qss_factory)` is the
opt-in helper: it installs a single event filter that catches
`StyleChange` and calls `widget.setStyleSheet(qss_factory())`. Idempotent.
PlotCard uses it as the canonical example; other widgets should adopt it
when their inline QSS depends on `theme.Colors` values.

---

## 9. Key data flows

### 9.1 Opening a dataset

```
User clicks Open…  or  Ctrl+O  or  --data_dir argv
        │
        ▼
WellViewerApp._load_path(path)        ← runtime_app.py
        │
        ▼
load_controller.load_path(app, path)  ← well_viewer/core/load_controller.py
        ├── detect in/out layout
        ├── app._read_pipeline_info(out_dir)  ← schema, fluor tokens, smFISH tokens
        ├── app._fov_tp_extractor ← make_schema_extractor(sep, fov_idx, tp_idx)
        └── load_directory(app, dir)
              ├── glob *.csv, validate header, load each into pandas
              ├── populate app._well_paths, app._cache
              ├── _build_tok_to_label, _rebuild_all_timepoints_cache, …
              ├── hydrate persisted state (sample_definitions, ratios,
              │   heatmap_layouts, cell_overrides, line_order, cell_gating)
              ├── _refresh_sidebar_map           ← view side
              ├── _recalculate_threshold        ← invokes _update_channel_selector
              └── app._redraw()                  ← plot_orchestrator.redraw
```

### 9.2 Selecting wells

```
User clicks/drags on the plate widget
        │
        ▼
WellPlateSelector.selectionChanged       ← widget signal
        │
        ▼
WellViewerApp._on_sidebar_plate_selection_changed(ids)
        ├── updates app._selected_wells
        └── app._refresh_sidebar_map()   ← updates colours; no plot redraw yet

User releases mouse / clicks "Select all" / "Select none"
        │
        ▼
selectionDragFinished → WellViewerApp._on_sidebar_plate_drag_finished
                                or
              click handler → app._on_plate_sel_change()
        │
        ▼
selection_controller.on_plate_sel_change(app)
        ├── snapshots prev_sel / last_sel
        └── _refresh_after_selection_change(app)
              ├── dispatches by active tab name:
              │     Bar Plots      → redraw_bars(app)
              │     Scatter Plot   → scatter_redraw_active(app)
              │     Review CSV     → app._refresh_review_csv()
              │     smFISH         → smfish_sync_from_app(app)
              │     Sample Defs    → cell_gating CDF refresh (no plot redraw)
              │     anything else  → app._redraw()
```

That **two-step "selection-changed (paint) → drag-finished (commit)"** split
is important. Drag-paint events fire many times per second; they should not
trigger a redraw on every cell. Click handlers (`Select all`, `Select none`,
row/column header) must explicitly call `_on_plate_sel_change()` to commit.

### 9.3 Switching channels

```
Global ctxbar QComboBox.currentIndexChanged
        │
        ▼
WellViewerApp._on_plot_channel_selected(source=ctxbar)
        └── _set_active_channel(channel)
              ├── update app._active_channel + app._active_val_col
              ├── sync every per-renderer combo
              │     (each tab keeps a hidden _chan_cb_<x> for back-compat)
              ├── app._recalculate_threshold()
              └── bus.channel_changed.emit(channel_state)
                    ↳ RedrawCoordinator → redraw_scopes_or_defer(app):
                       runs the visible scope's redraw callable
                       (line: redraw_app, bar: redraw_bars) and
                       marks the rest dirty for redraw on tab switch

WellViewerApp._update_channel_selector() (called from _recalculate_threshold)
        ├── populates every channel combo from app._fluor_channels + ratios
        ├── picks a sensible default if the current channel disappeared
        └── snaps the global combo's currentIndex to the active label
            (this last step is what prevents the "drop-down doesn't match
            the plot on first draw" bug)
```

### 9.4 Redrawing a plot

```
app._redraw()              ← runtime_app.py
   └── plot_orchestrator.redraw(app, …)
         ├── lineplot_redraw(app, …)              ← always
         ├── if Distribution tab built: redraw_distribution(app)
         ├── if Heat Map tab built:     redraw_heatmap(app)
         └── for each open ExportStyleSidebar: refresh order list

Each per-tab redraw ends with:
   apply_export_style_to_current(app, fig, canvas)
       ↳ figure_export_editor.apply_export_style_prefs(fig, prefs)
       ↳ canvas.draw_idle()

Bar Plots, Scatter Plot, and Scatter Aggregate redraws are *not* invoked
from `plot_orchestrator.redraw` — they have their own entry points
(`features.barplot.renderer.redraw_bars`, `scatter_redraw_active`)
called from the selection path directly and from the channel-change
path via the fold-change scope registry (see §5.9 "Scope registry").
```

### 9.5 Applying fold-change normalization

Two independent axes, both optional, both stacking. State lives on the
`WellViewerApp` so the Bar Plots and Line Graphs tabs share it; the
batch-export panels keep their own panel-local mirror so a batch job
isn't tied to whatever the plot tab has set.

The math has one important invariant: the **numerator** (each bar's /
curve's value) and the **denominator** (control mean and t0 baseline)
use the **same statistic**. For a rep-set member that's
"mean-of-per-well-means" (`_compute_rep_stats`), or per-FOV pool when
Aggregate-FOVs is on (`_compute_rep_per_fov_stats`); for a single-well
member it's `_aggregate_well`. Mixing statistics gave biased ratios in
the original implementation. The helpers in `fold_change.py` enforce
this — see `member_mean_series` for the dispatch.

Stacking when both axes are active is **ΔΔCt-style**:
```
   Y(t) = (X(t) / C(t)) / (X(0) / C(0))
```
because `vs t0` is applied to the post-control values. This is not the
same as `(X(t)/X(0)) / (C(t)/C(0))` in general. Documented on
`normalize_pts`.

Error propagation: each fold-change denominator (control mean, t0
baseline) is divided through with its own spread, and the resulting
relative error combines in quadrature:

```
   (σ_Y / Y)² = (σ_X / X)² + (σ_C / C)² + (σ_B / B)²
```

`scale_bar_value` and `normalize_pts` accept the denominator spreads
via `control_spread` / `t0_spread` / `control_stats`. Callers that
don't have a meaningful denominator uncertainty can omit them; that
recovers the legacy "treat denominator as exact" `spread / factor`
behaviour and is the backwards-compatible default.

```
                            app._fc_vs_control_on   ─┐
User → Control combo  ─→    app._fc_control_label   ─┼─→ fold_change.fold_change_state(app)
                            app._fc_vs_t0_on        ─┘
User → Baseline combo ─→ (sets _fc_vs_t0_on)

domain/fold_change_controls.set_fold_change_state(app, *, …, initiating_scope)
        │   mirrors the shape of channel_state_controller.set_active_channel:
        │   updates state → _sync_widgets_to_state →
        │     redraw_scopes_or_defer(app) (scope coalescer: visible scope's
        │     callable runs now, the rest are marked dirty)
        ▼
Line tab — lineplot_controller.redraw_line_plots(…) →
            collect_line_series(app, …) → list[LineSeries]
   1. Single source of truth, mirrors the bar plot's
      ``collect_bar_items``. Each member becomes a ``LineSeries``
      with a list of ``LinePoint``s (full AggPoint shape so the
      renderer and CSV writers consume the same record).
   2. fold_change.member_stats_series(app, fc_ctrl_lbl, …) →
      {t: (control_mean, control_spread)} — mean-of-per-well-means
      for rep-set controls (per-FOV pool when Aggregate-FOVs is on),
      single-well agg for well controls. Spreads thread through
      normalize_pts via quadrature for proper error propagation.
   3. normalize_pts(pts, control_stats=…, use_t0=…,
      miss_sink=fc_misses) is called inside collect_line_series.
      Drag-order from ``_line_order_rsets`` / ``_line_order_wells``
      is applied before the per-trace work.
   4. The renderer just iterates the resulting LineSeries and calls
      ax.plot. The batch line CSV / figure go through
      ``features.batch_export._line_series_runner.collect_line_series_for_group``
      — same data shape, pool-of-cells stat for the batch
      numerator (matches the prior batch behaviour).
   5. After redraw, app._set_status(…) surfaces a one-line warning
      naming any timepoints where the control had no sample.

Bar tab — barplot_controller.collect_bar_items(app, target_t,
                                                miss_sink=fc_misses)
          → list[BarItem]  (single source of truth)
   1. fold_change.control_mean_at_for_bar(app, label, target_t, …)
      → single float (or None). Uses the same stat as the bar's
      numerator (mean-of-per-well-means for rep-set, per-FOV pool
      when Aggregate-FOVs is on).
   2. For each bar's (mean, spread): scale_bar_value(…).
   3. t0 baseline = member_first_tp_value(app, member_label, …) for
      rep-sets (same stat) or first_tp_value(pts) for per-well bars.
   4. If vs-control was requested but the resolver returned None,
      every bar is forced to NaN (no silent fall-back to raw values)
      and the target_t is recorded in miss_sink for the status
      warning surfaced by barplot_renderer.draw_grouped_bar_mode.

Bar tab violin / beeswarm — runtime_app._draw_per_cell_bar_mode →
fold_change.build_cell_scaling(…) → {well: factor}, where each cell
value is divided by factor before plotting. factor = control_mean ×
t0_mean (per-well). NaN factor → that well's column rendered as a
placeholder. Title gains the suffix when scaling is active.

Both renderers append a "(fold change vs <ctrl>, vs t0)" suffix to the
mean axis title / ylabel via fold_change.fold_change_suffix.

Float-tolerance: _match_control_mean uses relative tolerance
abs(a-b) < 1e-3 · max(1, |a|, |b|) — the bar tp combo formats with
:.4g so a fractional tp like 2/3 ≈ 0.6667 parses back ~3e-5 off the
original float, which the prior absolute 1e-6 tolerance missed.

CSV output (additive in both line and bar paths):
   On-tab:
     export_service.export_plot_data        (line)
     export_service.export_bar_plot_data    (bar)
   Batch:
     features/batch_export/base_panel._run_group     (line)
     features/batch_export/bar_panel._run_batch      (bar)

   The mean_<ch>_<metric> / err_<band>_<ch>_<metric> columns always
   carry the RAW values. When fold-change is active, four columns are
   appended:
       fold_change_mean             — normalized mean
       fold_change_<sd|sem>         — normalized spread
       fold_change_mode             — "control" | "t0" | "control+t0"
       fold_change_control          — the chosen well / rep-set name

   The bar CSV is implemented via two calls to collect_bar_items
   (or collect_bar_items_for_group): one with FC_STATE_OFF for the
   raw columns and one with the active state for the fold-change
   columns. The aggregation helpers are cached so the second call
   is cheap. Pre-refactor the bar CSV used to overwrite the raw mean
   column with the normalized value when fold-change was active —
   that was inconsistent with the line CSV's additive schema and
   has been corrected.

Cross-tab widget sync:
   Each tab installs its own QComboBox instances via
   domain/fold_change_controls.install_fold_change_controls(scope=…).
   The combo handlers funnel every state mutation through
   set_fold_change_state, which calls _sync_widgets_to_state to
   mirror the new state into the other tab's widget set (with
   signals blocked). The smart-repopulation check skips the
   clear-and-readd cycle when the combo's popup is currently
   visible, so user interaction is never yanked out of focus.

   Scope registry. Each tab that owns a fold-change combo set
   declares itself via a FoldChangeScope (name, ctrl combo attr,
   baseline combo attr, redraw callable, tab names). The dataclass
   plus dispatch primitives (register_fold_change_scope,
   redraw_scopes_or_defer, flush_dirty_scopes) live in the
   pure-logic ``well_viewer.domain.fold_change_scopes`` module
   so the helpers can be unit-tested without Qt — and so the
   domain layer stays free of imports from ``features/``.

   Registration of the two default scopes (bar, line) lives in
   ``well_viewer.core.scope_registry.register_default_scopes``,
   which imports the actual redraw callables
   (``features.barplot.renderer.redraw_bars``,
   ``core.plot_orchestrator.redraw_app``) and binds them to the
   scopes. It is called once from ``core/bus_wiring.wire_event_bus``
   during construction. Adding a third scope (e.g. distribution /
   scatter) is a single register_fold_change_scope call inside that
   function — ``_sync_widgets_to_state`` and ``set_fold_change_state``
   iterate the registry rather than hard-coding scope names. The
   callable-valued ``redraw`` field replaced a string method name
   in PR #284 — the previous string-dispatch silently no-op'd when
   a referenced shim was deleted from the runtime app, which broke
   the bar redraw on channel change for the duration of the bug.

   Redraw deferral. State mutations route through
   ``redraw_scopes_or_defer(app)``: the currently-visible scope's
   ``redraw`` callable runs immediately, every other scope is
   marked dirty. ``tab_controller.on_notebook_current_changed``
   calls ``flush_dirty_scopes(app)``, which redraws any dirty
   scope whose tab just became visible.
   ``channel_state_controller.set_active_channel`` and
   ``set_fold_change_state`` both route through the bus, whose
   ``RedrawCoordinator`` calls this same chokepoint — so every
   redraw-triggering action shares one consistent visibility-aware
   policy. The dirty set lives on ``PlotState.fc_dirty_scopes``
   (``app._app_state.plot.fc_dirty_scopes``).

When a rep-set name collides with a loaded well token, the well's
combo entry is suffixed with " (well)"; resolve_control_wells
accepts both bare and suffixed forms, and set_fold_change_state
strips the suffix before storage so the underlying state stays a
bare token. This is the only collision-resolution mechanism — the
Sample Definitions UI does not currently prevent the collision at
input time.

### 9.6 Running the pipeline

```
Analyze tab Run button
        │
        ▼
services.pipeline_service.find_pipeline_script()
services.pipeline_service.build_pipeline_args(…)
services.pipeline_service.spawn_pipeline(args) → subprocess.Popen
        │
        ▼ stdout stream → Analyze tab log view
process_microscopy.main()
        ├── per-well loop (ProcessPoolExecutor)
        │     ├── extract zip → tmp dir
        │     ├── segment + quantify
        │     ├── write per-well CSV + image outputs
        │     └── compress to <well>_out.zip
        ├── write pipeline_info.json
        └── _estimate_thresholds_standalone(output_dir, …)
              └── _apply_thresholds_to_pipeline_info(output_dir, thresholds, log)
                    ↳ pipeline_info.json["cell_gating"]["thresh_frac_on"]
        │
        ▼
Process exits. Analyze tab offers the dataset to Review.
```

---

## 10. Cross-cutting concerns

### 10.1 Logging

- Every module uses
  `logging.getLogger("well_viewer")` (the viewer) or
  `logging.getLogger("well_viewer.auto_threshold")` (per-domain children).
- `all_well._attach_log_ring_buffer` captures every record into a ring
  buffer that the Help drawer's log tab and the Analyze tab's live log
  view both read from.

### 10.2 Status signal

`well_viewer/core/status_signal.py` is a tiny pub-sub for the bottom status
bar's `StatusDot`. Long-running operations call
`with status_signal.warn_scope(): …` so the dot turns amber while they run,
and call `status_signal.signal_failed()` on exceptions so it turns red and
auto-clears after `DANGER_HOLD_SECS`.

### 10.3 Debug flags

`well_viewer/core/debug_flags.py` exposes module-level booleans
(`REVIEW_TAB_DEBUG`, `REVIEW_BAR_DEBUG`, `REVIEW_SCATTER_DEBUG`,
`ANALYZE_TAB_DEBUG`, `MOVIE_MONTAGE_DEBUG`, `REVIEW_IMAGE_CHSW_DEBUG`).
`all_well.main()` wires the top-of-file constants in `all_well.py` into
these flags so toggling them is a one-line edit. Each flag gates `logger.debug`
calls inside its domain; off by default.

### 10.4 Build / packaging

- **Source run:** `python all_well.py [--data_dir …]`.
- **macOS .app:** `_Docs/_Installation/build_all_well.sh` (mamba env +
  PyInstaller), spec at `_Docs/_Installation/all_well.spec`.
- **Pipeline-only run:** `python process_microscopy.py --input_dir … --output_dir …`.
  No GUI deps required.

---

## 11. Recipes

### 11.1 Add a new plotting tab

1. Create `well_viewer/features/<name>/tab_view.py` with a
   `build_<name>_tab(ctx, parent)` function. Model it on
1. Create `well_viewer/features/<name>/` with `__init__.py`, `controller.py`
   (and, if you're rendering matplotlib, `renderer.py`), and `tab_view.py`.
   The `tab_view.build_<name>_tab(ctx, parent)` minimum:
   - `make_plot_with_right_dock(parent)` for the export dock.
   - A controls row with `btn_secondary("Properties", …,
     icon="sliders-horizontal")` as the rightmost widget.
   - A `PlotCard` that hosts your figure(s).
   - Register the figure / canvas / dock / card in the widget registry:
     `ctx.widgets.register("_<name>_fig", fig)`, etc. (`register` also
     setattrs the widget onto the app, so legacy `app._<name>_fig` reads
     resolve via plain attribute lookup). Do **not** assign new loose
     `app._*` attributes.
2. In `controller.py`, write a `redraw` pure function. Read state from the
   typed objects (`ctx.state.plot`, `ctx.state.dataset`, …); import
   `well_viewer.core.<...>_state` / `well_viewer.features.<name>.state`
   freely — they are import-safe. Don't import `WellViewerApp`.
3. Wire the controller's `redraw` into the redraw pipeline. Either:
   - Add a branch to `core/plot_orchestrator.redraw` (if it should redraw on
     every selection change); or
   - Add an `app._redraw_<name>()` shim on `WellViewerApp` and call it from
     the relevant tab-change / selection-change paths in
     `core/selection_controller`.
4. Register the tab in `core/centre_view.py`'s `groups` list (it sits
   under "Analysis" / "Images" / "Data" sections) and create a
   `_build_<name>()` closure that calls your `build_<name>_tab`.
5. Add an icon to `_SECTION_ICONS` in `runtime_app.py` if you want it to
   appear in the rail nav.
6. End your redraw with
   `features.export.style.apply_export_style_to_current(app, fig, canvas)`
   so the Properties sidebar's toggles apply.

### 11.2 Add a sidebar widget for a specific tab

1. Add `<name>_view.py` (or extend an existing `*_view.py`) in the owning
   feature package — `well_viewer/features/<feature>/` — with a
   `build_<name>(app, parent)` function. Pure cross-cutting sidebar bodies
   (e.g. the main plate picker) live in `well_viewer/core/sidebar_view.py`.
2. In `runtime_app._build_ui`, allocate a `QWidget` for it and hide it by
   default:
   ```python
   self._sidebar_<name>_frame = QWidget()
   QVBoxLayout(self._sidebar_<name>_frame).setContentsMargins(0, 0, 0, 0)
   sidebar_layout.addWidget(self._sidebar_<name>_frame, 1)
   self._sidebar_<name>_frame.hide()
   ```
3. In `core/tab_controller.on_tab_change`, show your frame on the relevant
   tab and hide every other sibling frame.
4. Build the frame body lazily — usually called from the tab's
   `_build_<tab>()` closure in `core/centre_view.py`.

### 11.3 Persist some state to disk

1. Create `well_viewer/persistence/<name>.py` with a `load_from_data_dir(app)`
   and `save_to_data_dir(app)` pair.
2. The file lives at `<data_dir>/<name>.json` (or as a key under
   `pipeline_info.json` if it's pipeline-coupled).
3. Add `app._<name>_load_from_data_dir = <name>.load_from_data_dir`-style
   bindings in `WellViewerApp.__init__` if you want the shim style, or
   call the module functions directly.
4. Hydrate at load time (`load_controller.load_directory` already calls
   each persistence module's loader at the end). Persist on user-driven
   edits, never on every signal — debounce with a `QTimer.singleShot(0, …)`
   if many small edits arrive together.

### 11.4 Add a new channel-ratio metric or aggregation

1. The ratio model is in `domain/ratio_models.py` (`RatioMetric` dataclass)
   and the persistence layer in `persistence/ratios.py`.
2. Aggregations are configured in `data/data_loading.aggregate_with_threshold_df`
   and `_aggregate_arrays`. Add the new metric as a column or in the
   AggPoint tuple, then teach `bar_metric_row` / `line_metric_row` in
   `features/export/service.py` how to format it for CSV export.

### 11.5 Hook a new keyboard shortcut

Add a `QShortcut(QKeySequence("Ctrl+X"), self, activated=self._handler)`
line in `AllWellApp._install_shortcuts` (`all_well.py`). The help drawer's
shortcut list is in `_toggle_help_drawer` in the same file — keep it
in sync.

---

## 12. Debugging recipes

### 12.1 "Where is this widget defined?"

The Review pane is built bottom-up; every widget is stored on
`WellViewerApp` as `_<noun>`. Grep first for the attribute name:

```bash
grep -rn '_line_fig\b' well_viewer/ widgets/
```

That returns both the producer (a `tabs/*_tab_view.py`) and every consumer.
For widgets in the rail nav, search `centre_view.py`. For controls in the
title bar, search `all_well.py`.

### 12.2 "The plot is showing the wrong data"

In order of probability:

1. **Stale `_active_channel`.** Check `_update_channel_selector` ran with
   the current dataset's channel list and that the global ctxbar combo's
   current text matches `_active_channel.upper()`.
2. **A controller didn't get the redraw entry-point call.** Check the
   tab-name branches in
   `selection_controller._refresh_after_selection_change`.
3. **The selection was painted but not committed.** Make sure the path that
   changed `_selected_wells` also called `app._on_plate_sel_change()` (the
   Select all / Select none buttons used to skip this).

### 12.3 "The pipeline ran but Review can't see the channels"

`process_microscopy.py` writes `pipeline_info.json` with `fluor_tokens`,
`schema`, `separator`, and `available_timepoints`. The viewer reads those
in `domain/viewer_state.read_pipeline_info`. If the file is missing, the viewer
falls back to detecting channels from a sample CSV's column names
(`data/data_loading.detect_fluor_channels`). Check both:

```bash
cat <data>/pipeline_info.json | python -m json.tool | grep -A1 fluor_tokens
head -1 <data>/<one>_measurements.csv
```

### 12.4 "Auto-threshold reports 'timepoint unknown'"

`well_viewer/features/preview/controller.classify_member` parses `(fov, tp)` from
each image filename. It prefers the schema-derived fields from
`pipeline_info.json` (`pipeline_fields_extractor`) and only falls back to
the legacy 5-field regex when those are absent. If you see "unknown", the
pipeline_info schema is either missing or doesn't include `fov` / `tp`
fields. Make sure `--filename_schema` was correct at pipeline-run time.

### 12.5 "Tab body doesn't appear when I click the rail"

Centre pages build lazily. The first time you click a page, its builder
runs from `centre_view._on_tab_change`. If the builder raises an exception,
the log captures it — open the help drawer's log tab or run
`python all_well.py` from a terminal and watch stderr. The most common
failure is a missing widget attribute on `WellViewerApp` because two
builders touch a shared attribute in the wrong order. Use the
`_centre_lazy_only_titles` set in `centre_view.py` to control which
builders never run from the background drain.

### 12.6 "The figure's Properties panel doesn't stick"

Every figure-styling toggle writes into the shared dict
`app._export_style_prefs` (see `figure_export_editor.py`). Every controller
ends its redraw with `apply_export_style_to_current(app, fig, canvas)`.
If a new redraw entry point you added is "losing" the user's toggles, it's
missing that call at the end.

### 12.7 "PyInstaller .app launches but freezes / can't find a module"

Three places to check:

1. The spec file `_Docs/_Installation/all_well.spec` has explicit
   `hiddenimports` for every non-trivial dependency (numpy internals,
   skimage submodules, stardist, csbdeep, …). Add yours there.
2. The launcher `all_well_launcher.py` sets `matplotlib.use("QtAgg")`
   before `pyplot` import — if matplotlib isn't picking up Qt, this is
   why.
3. The build script `_Docs/_Installation/build_all_well.sh` enumerates
   every required file. Missing inputs fail loudly.

---

## 13. The `Markdowns/` library

The `Markdowns/` folder is intentionally small — the long tail of port-time
plans, status updates, phase write-ups, gap analyses, and migration plans
was retired once the port finished and is preserved in git history rather
than as live files. What remains:

| File | What it's for |
|------|---------------|
| `ARCHITECTURE.md` | This document — the developer-oriented architectural overview. |
| `README_WellViewer.md` | User-facing README — install, run, troubleshoot. |

The on-disk shape of `pipeline_info.json::sample_definitions` (the saved
selections schema) is described in the `well_viewer/domain/selections_model.py`
module docstring; that module is the live source of truth for the
contract.

`design/` (sibling of `Markdowns/`) holds the **non-Markdown** design
artifacts — the HTML mockups (`mockup-decoded.html`,
`All-Well Redesign v2 _standalone_.html`), screenshots, and decoded mockup
assets — for visual reference only.

---

## 14. Glossary

| Term | Meaning |
|------|---------|
| **Pane** | One of the two top-level pages: Review or Analyze. |
| **Tab** | A centre-stack page inside the Review pane (Plotting, Statistics, …). |
| **Sub-tab** | A leaf page inside a parent tab (e.g. Plotting → Line Graphs). |
| **Sidebar** | The left rail in the Review pane. Hosts the rail nav + the well picker + per-tab control panels. |
| **Ctxbar** | The horizontal contextual control row above each plot card. Hosts channel / metric / timepoint combos + action buttons. |
| **PlotCard** | The framed container around each matplotlib figure. Provides the screen↔publication toggle, the custom toolbar, and the stats chip slot. |
| **Properties sidebar / Export Style sidebar** | The right-side dock that slides out when the user clicks the Properties button on a plot card. Backed by `app._export_style_prefs`. |
| **Selection** | A named, persisted subset of wells. Lives in `app._selections` (schema v2). Aliased as "Sample Definition" in the UI. |
| **Replicate set** | A group of wells treated as replicates for a single condition. Members of a `BarGroup`. |
| **`pipeline_info.json`** | The sidecar file written by `process_microscopy.py` next to the per-well CSVs. Holds schema, channel tokens, available timepoints, persisted selections, gating thresholds. |
| **Well token** | Two-letter / two-digit well identifier (`A01` … `H12`). Always uppercase, always zero-padded. |
| **fov, tp** | Field-of-view and timepoint extracted from a filename via the schema-aware extractor in `viewer_state.make_schema_extractor`. |

---

## 15. Conventions and gotchas

- **Never hardcode colours.** Use `theme.Colors.*` or QSS object names.
- **Never block the UI thread.** Heavy operations spawn workers (see
  `well_viewer/features/smfish/worker.py` and the `services.pipeline_runner`
  pattern). Streaming progress messages route through `logging` →
  `status_signal` → the bottom `StatusDot`.
- **Plate selections are well IDs (strings), not (row, col) tuples.**
  `_selected_wells: set[str]`. Convert to / from tuples at the
  edge with `data_loading.parse_well_token`.
- **Selections survive a tab switch.** Whenever you read or write
  `_selected_wells`, you're touching app-wide state — clobbering it in a
  tab-local handler will leak across tabs.
- **Don't call controllers' `redraw` directly from a widget signal.**
  Route through the shim on `WellViewerApp`. The shim knows how to
  defer / batch / no-op when the relevant tab hasn't been built yet.
- **Lazy tab builds are real.** Don't assume `app._heatmap_fig` exists; do
  `if hasattr(app, "_heatmap_canvas"): …`.
- **Pipeline runs in a separate process.** Don't try to share Python
  objects between Analyze and the running pipeline; everything passes
  through argv → stdout text streaming → the dataset folder.
- **`Markdowns/` is documentation, not code.** It is kept intentionally
  small post-port (this file, the user README, the selections-model
  contract, and the icons readme). The port-time plans and status updates
  were removed once the port shipped; they remain in git history if
  you ever need them.

If you read all the way to here and the structure still doesn't make sense,
the right next move is to open `well_viewer/runtime_app.py` and scroll
through `_build_ui` (around line 934) — that's where every widget tree
hanging off `WellViewerApp` gets stitched together, and every name you'll
need to grep for is in plain sight.

---

## Version history

The 5.5.x series layers new analysis capabilities on top of the 5.4 line.
Newest first:

- **5.5.1 (PR2).** Shape features flow pipeline→CSV→Review as sectioned virtual channels. Analyze's selector is now a compact `widgets/checkable_dropdown.CheckableDropdown` (replacing the wide chip grid); the Review channel combo builds sectioned entries via `set_combo_values_sectioned`, fixing shape metrics dropping out on plot sub-tab switches.

- **5.5.0 (PR1).** `shape_features_core` added as the shared feature catalogue; the PyInstaller spec `collect_all`s torch/cellpose/btrack/trackastra; viewer read paths gained backwards-compat defaults (see the bw-compat test harness).
