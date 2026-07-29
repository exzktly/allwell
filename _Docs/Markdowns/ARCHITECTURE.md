# All-Well Architecture

This document is the starting map of the All-Well codebase. It explains how the
application is organised, why files are split the way they are, and how to
read, modify, and debug the code without having to spelunk for two hours
first. Read it linearly the first time; come back to specific sections when
you're chasing a bug or planning a feature.

The current code reflects an iterative port from a Tk prototype to a Qt
desktop app (PySide6). Some of the structural decisions only make sense once
you know that — they're called out where relevant.

**This map describes the app as it stands today**, at whatever version the
repo-root `version.py` currently carries — the version string lives there and
nowhere else (§3), so this file never has to be edited for a release. It is
surfaced on the title-bar version pill. Bump `version.py` on a release — nothing
else carries the app version. This document is **not** a changelog: it never
records which release a feature landed in (see *Change history* at the end).

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

## 2. The three-mode shell

The application root (`AllWellApp` in `allwell.py`) owns a title bar and a
central pane stack. The central stack has three pages:

| Pane | Mode | Implemented by | Source file |
|------|------|----------------|-------------|
| Page 0 | **Review** | `WellViewerApp(QWidget)` | `well_viewer/runtime_app.py` |
| Page 1 | **Analyze** | `AnalyzeTab(QWidget)` | `analyze_tab.py` |
| Page 2 | **Datasets** | `DatasetsTab(QWidget)` | `datasets_tab.py` |

The title bar's `SegmentedControl` (`Review` / `Analyze` / `Datasets`) drives the
stack; segment index maps straight to stack index. The stack lives inside
`WellViewerApp` (`_central_pane_stack`) so Review's left rail stays put; Analyze
and Datasets are mounted onto it by `WellViewerApp.mountAnalyzePane` /
`mountDatasetsPane` (both go through `_mount_secondary_pane`, which places a pane
at its fixed index — Analyze=1, Datasets=2 — without clobbering the others). The
index↔name mapping is the single tuple `tab_controller.PANE_MODES`
(`("review", "analyze", "datasets")`), read by `currentMode`,
`on_central_pane_changed`, and the titlebar seg handlers. All three panes are
constructed eagerly at startup so switching is instant; widget trees inside them
build lazily on first use (see §5.4).

Both Review and Datasets carry a left sidebar; Datasets' is a plain page
navigator (a `RailNav` over a `NamedPageStack`, no well picker), which is why the
Datasets pane owns its own `_left_sidebar` the way `AnalyzeTab` does. The
collapse toggle drives whichever sidebar belongs to the active mode, through
`tab_controller.apply_sidebar_visibility`. The Datasets pane itself is described
in §6b.

A handful of things live on the application root rather than per-pane because
they're shell-wide:

- **Stylesheet.** `theme.qss()` is set as the application-wide QSS string in
  `AllWellApp._apply_stylesheet`. All chrome reads from it. The active theme is
  chosen once at startup (dev-facing `ALLWELL_THEME` env var / `--theme` flag) —
  see §8.
- **Help drawer + keyboard shortcuts** — `Ctrl+O` (open), `Ctrl+E` (export the
  active figure), `Ctrl+←`/`→` (tab history), `Ctrl+/` (help drawer), `Ctrl+L`
  (log drawer), `Ctrl+B` (collapse the sidebar), `Ctrl+W` (close). All are
  installed in `AllWellApp._install_shortcuts`, and
  `tests/test_shortcut_advertising.py` holds them in sync with what the help
  drawer and status bar advertise.
- **Application Log drawer** — a bottom-edge, full-width `Drawer` opened by the
  status-bar log tray (`_log_btn`). Its ring handler attaches to the
  `well_viewer` logger (which sets `propagate = False`) at startup, so it
  captures history before first open and appends live.
- **Status bar** (bottom of the window) with a status-message label and
  key-hint chips.
- **App icon, window geometry persistence.**

---

## 3. Top-level directory map

```
allwell/
├── allwell.py                  ← app entry point + title bar shell (+ version pill)
├── version.py                   ← single source of truth for the app version (read by the pill + the PyInstaller spec)
├── mpl_bootstrap.py             ← restrict matplotlib to bundled fonts (called first in main; see §4)
├── all_well_launcher.py         ← PyInstaller entry point + --run-pipeline dispatch
├── analyze_tab.py               ← Analyze pane (form + run controls + live log)
├── analyze_tab_form.py          ← Analyze form builders + _collect_run_options (mixin)
├── analyze_ome.py               ← packed OME-TIFF input: form section + de-interleave (mixin)
├── datasets_tab.py              ← Datasets pane (page-rail shell; see §6b)
├── datasets/                    ← Qt-free dataset operations + their feature views
│   └── concatenate/            ← Concatenate datasets feature (compute/apply/worker/view/controller/dashboard)
├── process_microscopy.py        ← analysis pipeline orchestrator + CLI (decomposed; see §6.2)
├── pipeline/                    ← Qt-free analysis-pipeline modules (re-exported by pm)
│   ├── background.py            ← background/illumination-correction math
│   ├── auto_threshold.py       ← Otsu / threshold helpers (GUI Cell-Gating reads these too)
│   ├── threshold_optimize.py    ← two-population threshold sweep (shared with Cell Gating)
│   ├── flatfield.py            ← flat-field illumination estimators (GUI validation view too)
│   ├── shape_features.py       ← per-cell shape-feature catalog (pipeline emits, GUI labels)
│   ├── cellpose.py             ← Cellpose segmenters (Torch imported lazily)
│   ├── stardist.py             ← StarDist segmenters + per-worker model state (TF lazy)
│   ├── ome_explode.py          ← de-interleave a packed OME-TIFF into loose per-plane
│   │                              TIFs (Z max-projected; positions → wells via §OME)
│   └── io.py                   ← atomic output writers (images / CSVs / overlay)
│                                 + the scratch base & staging-dir lifecycle (§6.5)
├── util/                        ← general Qt-free utilities (no pipeline/GUI specificity)
│   ├── well_token.py           ← canonical 96-well token parser (single source of truth:
│   │                              process_microscopy, WellPlateZipper, analyze_tab and
│   │                              input_resolution_service all parse well names through it)
│   ├── plate_grid.py           ← serpentine (boustrophedon) index → 96-well label mapping
│   │                              (used by the OME-TIFF de-interleave)
│   ├── schema.py               ← filename-schema vocabulary + parse/validate
│   │                              (shared by process_microscopy and WellPlateZipper)
│   ├── timepoint.py            ← timepoint token → hours + the shared sort key
│   │                              (pipeline, services and well_viewer all use it)
│   ├── aw_safe.py              ← swallow()/swallowed() (logged exception suppression)
│   └── zip_write.py            ← atomic_zip(): the one verified writer for the
│                                  per-well archives (pipeline, services, remote)
├── WellPlateZipper.py           ← input-folder packing utility (96-well .zip set)
├── theme.py                     ← v2 design tokens (Colors / Typography / Spacing / Radii)
├── well_viewer/                 ← Review pane (the bulk of the app)
├── widgets/                     ← reusable Qt widgets (no app coupling)
├── ui/theme/                    ← legacy remnant: two colours with no theme.Colors home yet
├── services/                    ← Analyze-side service modules
├── scripts/                     ← build_executable.sh / .ps1, dev helpers
├── _Docs/                       ← installer / requirements / visual-audit screenshots
│   └── Markdowns/               ← live docs (this file, README_WellViewer.md, the roadmap)
├── tests/                       ← pytest suite (+ tests/visual/ render harness)
└── design/                      ← HTML mockups — visual reference only
```

The `pipeline/` package (the Qt-free analysis-pipeline modules:
`pipeline.auto_threshold`, `pipeline.background`, `pipeline.flatfield`,
`pipeline.shape_features`, `pipeline.cellpose`, `pipeline.stardist`,
`pipeline.io`) and the `util/` package (`util.well_token`, `util.aw_safe`,
`util.zip_write`, `util.retry_io`)
are *deliberately at repo root* (not under `well_viewer/`): the pipeline
contract forbids `well_viewer` / `widgets` / Qt imports, so anything both the
pipeline and the GUI need to agree on has to live in a Qt-free module the
pipeline can import. `pipeline/` holds the analysis-domain modules — some used
only by the pipeline (`background`, `cellpose`, `stardist`, `io`), others the
GUI also reads (`auto_threshold` for Cell-Gating, `flatfield` /
`shape_features` for validation/labelling); the GUI is free to import
`pipeline.*`, only the reverse is forbidden. `util/` holds general Qt-free
helpers with no pipeline- or GUI-specific knowledge. **This contract is
enforced** by
`tests/test_architecture_contracts.py::test_pipeline_is_self_sufficient`
(no `well_viewer`/`widgets`/Qt import at any nesting) and
`tests/test_remote_manifest.py` (every repo-root module the pipeline imports is
in the remote upload manifest). `pipeline.shape_features`
(added with the shape-features work) is the single source of truth for the
per-cell `shape_*` morphology columns — the pipeline emits them, the GUI
detects/labels them. `pipeline.flatfield` holds the flat-field illumination
estimators (`estimate_gaussian_flatfield` / `estimate_basic_flatfield` /
`apply_flatfield`, mean-1 normalized): the pipeline applies them at run time, and
the Segmentation tab's "Flat-field" validation view recomputes them on demand.
See §6.2 for the contract.

**Feature-stack dependencies (segmentation / QC / shape / tracking).** This work
adds a pinned ML stack to `_Docs/requirements-remote.txt` — Torch 2.0.1
(cellpose + trackastra) and btrack — deliberately held to the **Torch 2.0.1
line** because TensorFlow 2.13 caps `typing-extensions<4.6` while newer Torch
needs `>=4.8`; the whole stack (incl. lightning/kornia) is pinned to coexist.
laptrack was dropped (no version satisfies `numpy≤1.24.3` *and* `pandas≥2.0`);
the BaSiC flat-field is a pure-numpy reimplementation (no jax). `read_pipeline_info`
gains `.get`-defaulted keys (`shape_features` / `qc_options` / `phase_token` /
`schema_version`) so pre-feature sidecars load unchanged.

**Multivariate (Review-side) dependencies.** The Multivariate tab's UMAP / t-SNE
projections add `scikit-learn==1.3.2` + `umap-learn==0.5.5` to `_Docs/requirements.txt`
(GUI-only — **not** `requirements-remote.txt`; the headless pipeline never runs Review
analysis). Pinned to coexist with the binding caps above (they hold `numpy` at 1.24.3
and `typing-extensions` at 4.5.0 — verified by a full `pip install` resolution). PCA
(numpy SVD), correlation, and k-means / hierarchical clustering (`scipy.cluster`) need
nothing beyond the existing numpy+scipy; UMAP / t-SNE are imported lazily and gated by a
`MethodUnavailable` probe, so the tab still runs (PCA + correlation + clustering) if
those libs are absent.

The Review side (`well_viewer/`) is organised per-feature: one package per
visible tab / functional area, with cross-cutting infrastructure split out
by role. Inside `well_viewer/`:

```
well_viewer/
├── runtime_app.py                 ← WellViewerApp composition root
├── viewer_context.py              ← ViewerContext DI bundle
├── core/                          ← cross-cutting infrastructure
│   ├── app_state.py, event_bus.py, app_runtime.py, widget_registry.py
│   ├── channel_state.py, dataset_state.py, plot_state.py, selection_state.py,
│   │   cell_gating_state.py, export_prefs_state.py, image_view_state.py,
│   │   qc_state.py, stats_cache_state.py   ← cross-cutting typed state slices
│   ├── app_shell_view.py, centre_view.py, sidebar_view.py, status_view.py
│   ├── split_view.py                 ← optional side-by-side double-pane view (one-line removable; §5.10)
│   ├── feature_registry.py           ← FeatureDescriptor + build_feature_registry (§5.3)
│   ├── profile.py                    ← build-profile manifest loader (stdlib-only)
│   ├── lazy.py                       ← call-time proxy so core never imports a feature
│   ├── bus_wiring.py, redraw_coordinator.py, selection_coordinator.py,
│   │   scope_registry.py, projector_registry.py
│   ├── load_controller.py, tab_controller.py, selection_controller.py,
│   │   channel_state_controller.py
│   ├── plot_orchestrator.py, plot_style.py, plot_toggles.py,
│   │   plot_card_actions.py, figure_saver.py
│   ├── timepoint_sync.py             ← shared "focus timepoint" across plotting tabs
│   ├── safe.py                       ← swallow()/swallowed() (GUI side; §15)
│   ├── ui_helpers.py, notifications.py, dnd.py, app_fonts.py, debug_flags.py
├── data/                          ← IO + decode
│   ├── data_loading.py, image_discovery.py, image_resolver.py,
│   │   zipfile_cache.py, zip_members.py, auto_threshold.py, crop_tool.py,
│   │   metric_labels.py, cache_projectors.py, well_repository.py
│   ├── image_identity.py             ← single source of truth for filenames + CellKey
├── domain/                        ← Qt-free models + pure logic
│   ├── batch_models.py, ratio_models.py, selections_model.py, plate_layout.py
│   ├── fold_change.py, fold_change_scopes.py, fold_change_controls.py
│   ├── gating_state.py, sample_definitions.py, viewer_state.py, stats_compute.py
│   ├── bar_groups.py, plot_groups.py, metric_registry.py, qc_params.py, tp_match.py
│   ├── multivariate_compute.py       ← PCA/correlation/clustering/faceting math (Qt-free)
│   ├── spatial_compute.py            ← spatial/neighbourhood math (Qt-free)
├── features/                      ← one package per UI feature
│   ├── barplot/      controller.py, renderer.py, tab_view.py
│   ├── lineplot/     controller.py, tab_view.py
│   ├── scatter/      controller.py, collect.py, callbacks.py, ratio_panel_view.py,
│   │                 tab_view.py, cells_tab_view.py, agg_tab_view.py
│   ├── heatmap/      controller.py, models.py, sidebar_view.py, tab_view.py
│   ├── distribution/ controller.py, tab_view.py
│   ├── stats/        controller.py, state.py, tab_view.py
│   ├── multivariate/ controller.py, collect.py, renderer.py, state.py,
│   │                 tab_view.py, feature_picker_view.py   ← higher-dimensional analysis
│   │                 (correlation / PCA·UMAP·t-SNE / clustering / faceting over the
│   │                  per-cell feature space; math in domain/multivariate_compute.py)
│   ├── image_browser/ tab_view.py, controller.py   ← unified image-review shell
│   │   │             (one rail entry → Segmentation / Image Table / Cell Browser
│   │   │              / Playground sub-tabs; descends like the Plotting stack)
│   │   ├── review_image/ controller.py, renderer.py, state.py, collect.py,
│   │   │                 box_select.py
│   │   │                 (collect.review_build_mfi_map → per-cell MFI-on-hover
│   │   │                  tooltips, shared by Segmentation + Image Table)
│   │   ├── image_table/  controller.py, tab_view.py, builder_view.py,
│   │   │                 state.py, grid_layout.py, grid_ops.py,
│   │   │                 picker_view.py, image_compose.py, facet_view.py,
│   │   │                 renderer.py, build_layout.py, export.py
│   │   │                 (controller = orchestration; the compose/layout/export
│   │   │                  layers are extracted to hold its line budget — see §5.5.
│   │   │                  builder_view = the root+nested-channels drag-and-drop shelf,
│   │   │                  the sole authoring surface; the render model is headless
│   │   │                  plain data — see §5.5. grid_ops = the right-click per-cell
│   │   │                  override popover. renderer = the export figure's faceted
│   │   │                  chrome in matplotlib (Qt-free, paired with export.py).
│   │   │                  image_compose also burns the optional
│   │   │                  scale bar + per-cell identity label into the source RGB
│   │   │                  (draw_cell_chrome) so live grid and export match;
│   │   │                  scale bar on by default, editable thickness, auto-contrast
│   │   │                  colour. A sidebar well-selection change / Select
│   │   │                  All / Clear reruns the layout via builder_view.schedule_
│   │   │                  selection_rebuild, and dropping a root/channel on Rows/Columns
│   │   │                  exits Plate mode)
│   │   ├── cell_browser/ controller.py, collect.py, state.py, tab_view.py,
│   │   │                 criteria_view.py, sort_view.py, worker.py
│   │   └── playground/   controller.py, renderer.py, state.py, tab_view.py,
│   │                     worker.py   ← single-frame background-correction testbed
│   │                     (a single-well tab: the frame's well is picked on the
│   │                      ever-present sidebar plate — Playground registers
│   │                      single_well_selection + an on_selection_changed hook,
│   │                      no in-tab plate of its own)
│   ├── review_csv/   tab_view.py   ← Data-tab CSV inspector (was review_image/csv_tab_view.py)
│   ├── preview/      controller.py, preview_view.py, panel_view.py
│   ├── smfish/       controller.py, worker.py, tab_view.py
│   ├── grouping/     controller.py, tab_view.py, label_grid_view.py,
│   │                 replicate_panel_view.py, pipeline_info_view.py
│   │   └── cell_gating/   tab_view.py, controller.py, renderer.py,
│   │                      opt_curve_view.py, gating_compute.py
│   │                      ← the Cell Gating sub-tab, split by layer: the tab
│   │                        builder wires controls to the controller, which
│   │                        owns the two QThread workers, the ThreshFracOn
│   │                        optimizers and the scatter-gate plumbing; the CDF
│   │                        panel and the optimizer-curve window each draw on
│   │                        their own, over Qt-free math in gating_compute
│   ├── spatial/      controller.py, collect.py, renderer.py, state.py, tab_view.py
│   ├── saved_plots/  controller.py, collect.py, state.py, tab_view.py, _report.py
│   ├── crop/         controller.py, region.py        ← support lib (always present)
│   ├── qc/           controller.py, params.py, tab_view.py   ← support lib
│   ├── export/       service.py, style.py, sidebar_view.py
│   ├── batch_export/ tab_view.py, base_panel.py, bar_panel.py,
│   │                 scatter_panel.py, service.py, _common.py,
│   │                 _line_series_runner.py, well_grid_button.py
│   └── registry.py   ← the FEATURES list: the sole importer of features/* (§5.3)
└── persistence/                   ← JSON read/write, debounced save registry
```

### File-name suffix conventions

The codebase uses a suffix-based scheme so the file name tells the reader the role. Within a feature directory the suffixes also indicate which layer the file belongs to (Qt builder vs. pure-math vs. data-only).

| Suffix | Role | Examples |
|---|---|---|
| `controller.py` / `_controller.py` | Orchestrator: event handlers, business logic, state mutation. Owns the feature's behaviour. | `features/barplot/controller.py`, `core/load_controller.py`, `core/channel_state_controller.py` |
| `renderer.py` | Pure matplotlib drawing. Decoupled from Qt — takes a figure / axes and a state snapshot. | `features/barplot/renderer.py`, `features/image_browser/review_image/renderer.py` |
| `collect.py` | Pure data collection — the half of a plot feature that walks the loaded wells and returns plain dicts/arrays, with no matplotlib axes or Qt. Paired with a `renderer.py`; importable downward by exporters without pulling the renderer. | `features/scatter/collect.py` |
| `tab_view.py` | Qt builder for a feature's main tab page (returns the assembled `QWidget`). | every `features/*/tab_view.py` |
| `*_view.py` | Qt builder for a sub-panel, sidebar, or sub-dialog. | `sidebar_view.py`, `panel_view.py`, `criteria_view.py`, `picker_view.py`, `core/app_shell_view.py` |
| `state.py` / `_state.py` | Dataclass state container; no logic, only fields + defaults. | `core/app_state.py`, `core/channel_state.py`, `features/stats/state.py` |
| `_coordinator.py` | EventBus subscriber — subscribes signals and fans them out to controllers. | `core/redraw_coordinator.py` |
| `_registry.py` | Registry pattern (deferred lookup, debounced save, etc.). | `core/widget_registry.py`, `core/scope_registry.py`, `persistence/registry.py` |
| `_orchestrator.py` | Fan-out helper across multiple features. | `core/plot_orchestrator.py` |
| `_models.py` | Plain dataclasses (no behaviour). | `domain/batch_models.py`, `domain/ratio_models.py` |
| `_compute.py` | Pure-math layer shared across plot controllers. | `domain/stats_compute.py` |
| `service.py` | Non-Qt service entry point. | `features/export/service.py` |
| `callbacks.py` | Matplotlib click / interaction handlers for a feature. | `features/scatter/callbacks.py` |
| `worker.py` | Background `QThread` worker. | `features/smfish/worker.py` |
| `_panel.py` | Per-plot-type panel (only inside `features/batch_export/`). | `features/batch_export/bar_panel.py`, `scatter_panel.py` |
| `_` prefix | Private helper module (not part of the package's public surface). | `persistence/_doc.py`, `_io.py`, `features/batch_export/_common.py`, `_line_series_runner.py` |

**Known exceptions** (legacy or single-purpose modules that don't fit the grid; do not propagate these naming choices to new files):

- `features/export/` (service.py + style.py) and `features/review_csv/` (tab_view.py) intentionally have **no `controller.py`**: `export/` is a stateless service layer (figure-style application + save), and `review_csv/` is a thin read-only CSV inspector tab with no behaviour to orchestrate. Adding an empty controller would be ceremony, not structure.
- `features/heatmap/models.py` is the only `models.py` in `features/` (the convention under `domain/` is `_models.py`).
- `data/` modules use bare names (`data_loading.py`, `auto_threshold.py`, `crop_tool.py`, `image_discovery.py`, `image_resolver.py`, `metric_labels.py`, `zipfile_cache.py`) — each is one self-contained utility, no internal suffix scheme.
- `domain/` mixes suffixed (`stats_compute`, `fold_change_scopes`, `gating_state`) and bare (`fold_change.py`, `selections_model.py`, `viewer_state.py`, `plate_layout.py`) names.
- `persistence/` modules are named for the `pipeline_info.json` block they persist (`bar_groups.py`, `ratios.py`, `cell_gating.py`) — no `_persistence.py` suffix.
- `features/image_browser/image_table/view_state_io.py` and `features/heatmap/view_state_io.py` — feature-owned `view_state` (de)serialization modules (the `view_state_io` descriptor hook, §5.3); a persistence-IO role, not a view/compute one.
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
- **It's a design token / colour / size →** `theme.py` (chrome QSS + tokens,
  including `CPub` for publication-mode plot colour and `WELL_COLORS_TUPLE`
  for categorical well/trace identity — the single source of truth for
  almost everything); never hardcode hex values in code. `ui/theme/styles.py`
  is a two-token legacy remnant (see §8) — new colours never go there.

---

## 4. Startup sequence

In one diagram, end-to-end:

```
all_well_launcher.main()                       (PyInstaller bundle only)
    │
    ▼ regular Python run jumps in here:
allwell.main()
    ├── mpl_bootstrap.configure()   ← FIRST, before anything imports matplotlib:
    │       restricts font_manager.findSystemFonts to the bundled fonts (+ DejaVu)
    │       and pins MPLCONFIGDIR, so the system-font scan (minutes on a Windows 365
    │       Cloud PC) never runs. Defensive — failure leaves matplotlib untouched.
    ├── QApplication(sys.argv)
    ├── app_fonts.register_qt_fonts() → theme_v2.Typography.family/_mono
    │       ← register bundled Inter / JetBrains Mono (static instances),
    │         falling back to app.font().family() if unavailable
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
│         ── barplot, lineplot, scatter, distribution, heatmap,       │
│            stats, multivariate, spatial, image_browser (review_    │
│            image / image_table / cell_browser / playground),        │
│            smfish, review_csv, grouping, saved_plots, preview,      │
│            crop, qc, export, batch_export                            │
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
│              debug_flags, ui_helpers                                 │
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
viewer_context.ViewerContext  ← DI bundle (state / bus / widgets / runtime / collaborators + ctx.window)
```

### 5.1 `runtime_app.WellViewerApp`

The `WellViewerApp(QWidget)` class is the application's **composition root**: a
thin shell that wires the app together and forwards `app._method(…)` call sites
to controller functions. It holds no business logic — state lives on typed
domain objects and logic lives in controllers (see below).

What lives on `WellViewerApp`:

- **`self._app_state`** — an `AppState` aggregate of the typed domain-state
  objects (`channel`, `selection`, `dataset`, `plot`, `stats`, `review_image`,
  `preview`). Single source of truth for runtime state; call sites read
  `self._app_state.<domain>.<field>` directly, no `@property` shims.
- **`self._event_bus` / `self._attr_store` / `self._widget_registry` /
  `self._runtime` / `self._ctx`** — the `EventBus`; a shared `name → object`
  attribute-store dict; the widget-handle store (`WidgetRegistry`, which
  `setattr`s onto `app` on `register` *and* writes the shared store, so both
  `app._<widget>` and `ctx.runtime` reads resolve); the per-feature runtime
  namespaces (`AppRuntime`, backed by that same shared store, no `app` ref); and
  the `ViewerContext` that bundles state / bus / widgets / runtime for injection.
  All are load-bearing (see §5.9).
- **`__init__`** (~140 lines) — wires the bus, the persistence registry,
  the widget registry, the viewer context, the redraw / selection
  coordinators, then calls `views.app_shell_view.build_ui(self)`.
- **One-line proxy methods** (~90 of them) — each `app._method(…)` call site
  resolves to a 1-line delegator that forwards to the controller function.
  A delegator exists only for as long as some call site still goes through
  `app`; once the `ctx` migration rewires the callers to the controller
  function directly, the proxy is dead weight and is deleted rather than left
  standing. `tests/test_guardrails.py` meters the class at 113 methods and 876
  lines, and both ratchets only move down. Pattern:

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
| Analysis | **Plotting** | Bar Plots / Line Graphs / Scatter Plot / Distribution / Heat Map |
| Analysis | **Statistics** | — |
| Analysis | **Multivariate** | — (higher-dimensional analysis: correlation / PCA·UMAP·t-SNE / clustering / faceting) |
| Analysis | **Spatial** | — (neighbourhood / colony-level spatial analysis; §5.11) |
| Images | **Image Browser** | Segmentation / Image Table / Cell Browser / Playground |
| Images | **smFISH** | — |
| Data | **Review CSV** | — |
| Data | **Sample Definitions** | Cell Gating / Groups / Well Labels / Ratios / Pipeline Info / Notes |
| Data | **Saved Plots** | — (the plot gallery; decorates every plot card with a "save to gallery" button) |
| Data | **Batch Export** | — |

**Plotting** is a `NamedPageStack`; **Image Browser** (`QTabWidget`: the unified
home for the disparate image-review surfaces — the review-image **Segmentation**
panel, the **Image Table** grid, the filtered **Cell Browser** gallery, and the
**Playground** single-frame correction testbed — each sub-tab body + sidebar
lazily built on first show; see `features/image_browser/{tab_view,controller}.py`)
and **Sample Definitions** (`QTabWidget` led by Cell Gating) also nest.
`WellViewerApp._current_centre_tab()` descends through the Plotting *and* Image
Browser levels so the rest of the app can pattern-match on a leaf name (e.g.
`"Bar Plots"`, `"Segmentation"`) — that one-level descent is exactly what keeps
the `on_tab_change` leaf dispatch working unchanged after the consolidation.
smFISH stays its own rail entry (it is not an image-*review* surface), following
the standard one-package-per-tab layout
(`features/smfish/{controller,worker,tab_view}.py`).

The left **rail navigation** (`RailNav` from `widgets/rail_nav.py`) is the
single visible selector for the top-level pages. It mirrors the notebook —
clicking a rail item drives `_notebook.setCurrentByName(name)`; programmatic
changes to the notebook drive the rail back via
`WellViewerApp._on_notebook_current_changed`. There is no visible tab bar;
the page stack is purely name-keyed.

`NamedPageStack` also exposes `detachPage(name)` / `reattachPage(name, widget)`
(backed by a canonical `_page_order`) so a page can be temporarily moved out of
the stack and put back in its original slot — the reparenting primitive behind
**Split View** (§5.10).

### 5.3 Centre tab builders: `features/<feature>/tab_view.py`

Each module exposes a single `build_<name>_tab(ctx, parent)` function —
`ctx` is a `ViewerContext`. Each builder references `ctx.state` / `ctx.widgets` /
`ctx.runtime` (and the held collaborators `ctx.notify` / `ctx.figures` /
`ctx.coordinator` / `ctx.persistence` / `ctx.features`, plus the lone window
accessor `ctx.window`) directly — there is no `ctx.app` (the `CTX_APP_BUDGET = 0`
ratchet hard-bans it).
The function:

1. Creates a `make_plot_with_right_dock(parent)` host (returns
   `(plot_area, layout, right_dock)`) — the right-side dock is reserved for
   the Properties / Export Style sidebar.
2. Adds the per-tab controls row (channel ctxbar, metric combo, timepoint
   combo, an **Export** dropdown, **Properties**).
3. Mounts a `PlotCard` from `widgets/plot_card.py` (matplotlib figure +
   custom toolbar inside a styled panel).
4. Stashes the figure / canvas on `app` so controllers can reach them
   later (`app._line_fig`, `app._bar_canvas`, …).

Every `PlotCard`-based tab (Bar Plot, Line Graphs, Scatter — cells and
aggregate, Distribution, Heat Map, Spatial, Multivariate) uses one
`make_action_menu_button`/`add_menu_action` **Export** dropdown
(`core/ui_helpers.py`) instead of separate flat icon buttons, with four
standard entries: an export-data action specific to the tab, "Copy SVG"
(`core.plot_card_actions.copy_active_card_as_svg`), "Save Figure…"
(`save_active_card_figure`, a Save-As dialog), and "Save to Saved Plots"
(`core.plot_card_actions.save_card_to_gallery(ctx, "<card_attr>")`, which
calls the card's `saveFigureToGallery()` — a public wrapper around the
hover-save button's whole-figure capture, added so a persistent menu
action can trigger it without hover-axis context). A tab's `PlotCard` must
also call `ui_helpers.apply_plot_card_decorators(ctx, card, "<Tab Name>")`
for the hover-based gallery save and Saved Plots registration to exist at
all, and its widget-registry attribute name must be listed in **both**
`allwell.py::_on_present_toggled`'s `card_attrs` (the global Screen/
Publication toggle) and `core/plot_card_actions.py::_CARDS` (Copy/Save's
"first visible card" resolution) — these are hardcoded allowlists, not
auto-discovered from the feature registry, so a new plot tab that skips
either silently gets no Screen/Publication toggle or no global Copy/Save,
exactly the bug that shipped for Spatial and Multivariate.
5. Wires signals to the feature's controller / renderer entry points
   (e.g. matplotlib clicks → `features.scatter.controller.on_scatter_click`,
   button presses → `features.barplot.renderer.redraw_bars`).

Each feature package owns the full vertical slice for its tab — the
tab-builder, the controller, the renderer (where applicable), and any
panel-shaped sub-views the tab embeds. `centre_view` (under `core/`) just
calls the per-feature `build_<name>_tab` from a single registration loop.

#### The feature registry (`core/feature_registry.py` + `features/registry.py`)

Core never imports a feature directly. Every feature is declared once as a
`FeatureDescriptor` in the `FEATURES` list in `features/registry.py` — the
**single sanctioned importer** of `well_viewer.features.*` (enforced by
`tests/test_architecture_contracts.py::
test_no_module_scope_imports_of_removable_features`). A descriptor bundles
everything core used to hard-code:

- `build(ctx, frame)` — the tab body (a lazy wrapper over `build_<name>_tab`);
- `group` / `lazy_only` — placement + drain policy the page-stack reads;
- `states` — the `AppState` slices the feature owns (`StateSpec(attr, factory)`),
  attached at construction by `attach_feature_state` so `core/app_state.py`
  imports no feature state class (the import-inversion);
- `on_activate` / `on_selection_changed` / `on_channel_changed` /
  `on_dataset_loaded` — dispatch hooks (the bodies that used to live in
  `tab_controller.on_tab_change`, `selection_controller._refresh_tab_view`,
  `channel_state_controller.recalculate_threshold`'s fan-out, and
  `load_controller`'s per-feature post-load refresh). Core fans each out over
  `registry.with_hook("<name>")` in declaration order, so it names no feature;
- `redraw` — a plot feature's figure-redraw callable; core plot orchestration
  (`plot_orchestrator.redraw_app`, tab activation) triggers a redraw by name via
  `registry.redraw(ctx, "<tab>")`, so it names no renderer (Bar Plots / Line
  Graphs / Scatter Plot / Distribution / Heat Map register it);
- `fold_change_scope_factory` — the feature's fold-change redraw scope
  (`core/scope_registry` registers these from the descriptors);
- `plot_card_decorator` — a feature that decorates every plot card (Saved Plots'
  "save to gallery" hover button); plot tabs apply *all* registered decorators
  via `ui_helpers.apply_plot_card_decorators`, so no tab imports Saved Plots;
- `view_state_io` — a feature that owns its own `view_state` snapshot/restore
  pair (Image Table and Heat Map); `persistence/view_state.py` fans this out and
  so never names the feature (each `view_state_io.py` is imported only when its
  feature is present). Heat Map's captures the colormap / scale / vmin·vmax /
  toggles / active layout by reusing the layout-sidecar serializer. Image
  Table's `apply_image_table` restores `ctx.state.image_table`'s row/col dims,
  channel options, and per-cell values, then calls `builder_view.builder_refresh`
  so the drag-and-drop shelf's own widget tree reflects the restored spec right
  away — the shelf caches its built spec (`ctx.runtime.image_table.builder_spec`)
  and only rebuilds when something invalidates it, so a restore that skipped
  this call left the shelf showing whatever it was built with on the tab's first
  construction (typically its "nothing chosen yet" fallback: every channel, the
  last timepoint) even though the underlying state was already correct. It also
  sets `ctx.runtime.image_table.restored_this_load = True`, reset to `False` at
  the top of every `load_controller.load_directory` call — `registry.
  _loaded_image_table`'s post-load reset checks this flag and skips wiping the
  grid back to fresh-tab defaults when a saved one was just restored earlier in
  the same load;
- `per_well_selection` / `single_well_selection` — selection-behaviour flags
  that replaced the hard-coded tab-name sets in `selection_controller`.

`parent` links a sub-leaf to its composite container (Plotting / Image Browser /
Sample Definitions); `registry.sub_leaves(parent)` enumerates a container's
sub-tabs from the descriptor list, so a container never hard-codes its leaf names
and dropping a leaf is a one-line/manifest edit.

`WellViewerApp.__init__` builds the registry (`app._features`) before `AppState`;
`centre_view`, `tab_controller`, `selection_controller`, `channel_state_controller`,
and `scope_registry` all read it. Every hook callable imports its feature lazily,
so building the registry pulls no heavy deps.

**To remove a feature, comment out its line in `FEATURES`.** Its rail entry,
dispatch, state slice, fold-change scope, and persistence drop out together, with
no other edit and no startup error — the same one-line-removable property §5.10
gives Split View, generalized to **every** Review feature.

Every removable feature also tolerates having its package **deleted** from disk,
not just commented out. Three mechanisms make this hold (and three guards keep it
from regressing):

- **Incidental feature-state access from core** is read through
  `AppState.feature_slice(name)`, which returns `None` when the feature is
  disabled (the slice was never attached) — so e.g. Image Browser's
  `preview` / `review_image` / `image_table` accesses in dataset-load,
  stats-cache-invalidation, and channel-switch no-op cleanly.
- **Genuinely-shared state / utilities don't live on a feature slice.** The
  group-statistics result cache is core (`core/stats_cache_state.py`,
  `AppState.stats_cache`), not the Statistics feature's `StatsState`; the
  dataset-level `get_all_timepoints` lives in `data/data_loading.py`, not
  `scatter`.
- **No module-scope import of a removable feature exists** outside
  `features/registry.py`. Core/`runtime_app`/inter-feature delegates are bound
  through the `core/lazy.py` proxy (resolves on first call) or imported
  function-locally, so importing any module never imports a removable feature.
  Support libs (`export`, `preview`, `crop`, `qc`) are always present.

Guards: `test_no_module_scope_imports_of_removable_features` (repo-wide import
rule), `test_disabling_a_leaf_feature_drops_it_everywhere`, and
`tests/test_feature_removal.py` (full-app boot+load comment-out *and*
`sys.meta_path` deletion tests for Image Browser, Statistics, scatter, barplot).

#### Build profiles — running / building / shipping a feature subset

The one-line-removable property above is the mechanism a **profile** drives at
scale: the full app is the single source of truth, and any thin subset is a
*profile* of it, not a fork. A profile is a small TOML manifest under `profiles/`
naming the top-level features (and an optional per-parent sub-tab allowlist) to
keep; `well_viewer/core/profile.py` loads it (stdlib-only — safe to import from
the pipeline side too). It applies at three levels, all sharing the one
`Profile.includes` predicate so they never disagree:

- **Runtime** — `build_feature_registry(profile)` keeps only the admitted
  descriptors, so `ALLWELL_PROFILE=thin python -m allwell` runs the subset off
  the *full* tree with nothing deleted. The active profile is the
  `ALLWELL_PROFILE` env var, or a baked `active_profile.txt` marker in a frozen
  bundle, else the full app.
- **Binary** — `all_well.spec` reads the same profile: it omits excluded
  features' source from the bundle and **drops their unique heavy deps
  automatically** from the catalog (`profiles/full.toml`'s per-feature `deps`).
  Both matter — adding a dep to PyInstaller `excludes` only prunes the module
  graph, so the spec *also* skips that package's `collect_all` (which is what
  keeps its `.so`/`.dylib` payload out). A StarDist-only `thin` binary thus drops
  `torch`/`torchvision`/`cellpose` (Cellpose) and `sklearn`/`umap`/`pynndescent`
  (Multivariate). The pure decision math is `scripts/profile_build.py`, which
  also owns `bundle_name(version, profile)` — the unit-tested helper that names
  the output `AllWell-{version}` for the full app or `AllWell-{version}-{profile}`
  for a subset (e.g. `AllWell-<version>-thin`), so a full build and a thin build are
  distinguishable by name. `all_well.spec` uses it for the
  COLLECT/BUNDLE output name and the macOS `CFBundleDisplayName`, while keeping
  the internal executable name `AllWell` so `CFBundleExecutable` and the
  re-exec/launcher paths are unaffected.
- **Source** — `scripts/derive_profile.py --apply` physically deletes the
  excluded features' paths/tests for a shippable stripped tree.

Two seams keep this honest beyond the registry: **cross-build data safety** — a
`persistence.json` written by another build round-trips untouched (unknown
sections / `view_state` tabs / `pipeline_info` blocks are preserved, never
rewritten) — and the pipeline's **lazy Cellpose import** (`process_microscopy`
resolves `pipeline/cellpose.py` via a PEP 562 module `__getattr__`), so a
StarDist-only build can omit that file entirely. Guarded by
`tests/test_feature_profile.py` (subset boots + loads), `test_derive_profile.py`
(the strip plan), and `test_profile_build.py` (binary dep-gating).

**Defining a profile.** A named build *is* a file: `profiles/<name>.toml`. The
`<name>` is the filename (minus `.toml`), and the same `ALLWELL_PROFILE=<name>`
env var selects it for both running and building — there is nothing else to
register. To author one, write the manifest and list the features you want:

```toml
# profiles/myapp.toml  →  select with  ALLWELL_PROFILE=myapp
name = "myapp"                                    # must match the filename

# STRICT ALLOWLIST of top-level tabs: list EVERY tab to keep.
# Anything omitted is dropped from the build.
features = ["Plotting", "Image Browser", "Sample Definitions"]

# OPTIONAL. Only NARROWS a parent already listed in `features` — keep just
# these sub-tabs of it. Omit a parent here and ALL its sub-tabs are kept.
[subtabs]
"Image Browser" = ["Segmentation", "Image Table"]
```

The two rules that decide what ends up in the build:

- **`features` is a strict allowlist** of top-level tabs — list *every* tab you
  want; anything not listed is removed. (An empty/missing `features` means the
  full app — that is what the absence of a profile gives you.)
- **`[subtabs]` only narrows**, never adds. A parent that appears there keeps
  *only* the sub-tabs you list; a parent you leave out of `[subtabs]` keeps all
  of its sub-tabs. Listing a sub-tab under a parent that isn't in `features` has
  no effect (the whole parent is already gone).

**Valid names** — the source of truth is `well_viewer/features/registry.py`
`_features()` (entries with a `group=` are top-level; entries with a `parent=`
are that parent's sub-leaves), so this list can't silently drift from the code:

| Put in `features` (top-level tabs) | Put in `[subtabs]` (parent → sub-leaves) |
| --- | --- |
| `Plotting` | `Plotting` → `Bar Plots`, `Line Graphs`, `Scatter Plot`, `Distribution`, `Heat Map` |
| `Statistics` | |
| `Multivariate` | |
| `Spatial` | |
| `Image Browser` | `Image Browser` → `Segmentation`, `Image Table`, `Cell Browser`, `Playground` |
| `smFISH` | |
| `Review CSV` | |
| `Sample Definitions` | `Sample Definitions` → `Cell Gating` |
| `Saved Plots` | |
| `Batch Export` | |

**The one non-tab name: `Cellpose`.** It's a pipeline unit, not a Qt tab, so it
lives only in `features` (never `[subtabs]`). List `"Cellpose"` to keep the
Torch-backed segmenter; omit it and a subset *binary* drops
`torch`/`torchvision`/`cellpose` (StarDist stays the default segmenter). This is
why `thin` and `plotting_only`, which don't list it, are StarDist-only.

**End to end.** Write `profiles/myapp.toml` as above, then:

```bash
ALLWELL_PROFILE=myapp python allwell.py            # run the subset off the full tree
ALLWELL_PROFILE=myapp scripts/build_executable.sh  # build a subset binary
```

For a physically stripped source tree (not just a runtime/binary filter), feed
the same profile to `scripts/derive_profile.py --apply`, which deletes the
excluded features' `paths`/`tests` per `profiles/full.toml`.

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

The same module builds the **Plot Groups** drawer. Both halves of the drawer —
capturing a group and loading one — branch on which mode the sidebar plate is
in, because the two modes keep "the current selection" in different stores.

What a new group captures depends on which sidebar's Add was pressed, so
`build_plot_groups_drawer` takes a `wells_source` callable and each caller
supplies its own. The default is `_wells_for_new_plot_group`, the plotting
sidebar's plate: in group-visibility mode (replicate sets defined and no
well-editing pane on screen, the same condition
`selection_controller._plate_passive_in_group_mode` resolves) the plate paints
set visibility and clicks toggle a set's `hidden` flag, so what is on screen is
the union of every visible set's wells plus `active_solo_wells` — the same
active set the plots draw from. `selection.selected_wells` is not consulted in
that mode at all: nothing keeps it current there, so it holds whatever a
per-well tab or an earlier Plot Group load last wrote. In per-well mode the
plate *is* a well selector and `selected_wells` is the answer.

The Image Table's sidebar passes `image_table_active_wells` instead, because its
picker is a second, independent selector — it ignores replicate sets and writes
`state.image_table.active_wells`, never `selection.selected_wells`. Threading
the reader in from the caller keeps core out of the business of knowing where a
feature's sidebar keeps its wells; branching inside the builder would have meant
core naming a feature's state.

Loading a group writes the saved wells through `set_selected_wells` — per-well
tabs and the Image Table read them from there — then reconciles replicate
visibility through the pure `plot_group_visibility_plan`, because in replicate
mode the plotting tabs draw one trace per *visible* replicate set and never read
`selection.selected_wells`. Every set the group's wells touch is shown, the rest
hidden, and wells belonging to no set become `active_solo_wells`. This is
visibility only — no replicate set's membership is edited, so Plot Groups never
mutate Sample Definitions.

Plot Groups have two homes on disk: the `plot_groups` section of
`persistence.json` (written on every drawer edit) and a copy inside the
`sample_definitions` block of `pipeline_info.json` (written whenever sample
definitions autosave). The `pipeline_info.json` hydrator runs first on load and
`persistence/plot_groups.load_from_data_dir` runs second; an *absent*
`persistence.json` section leaves the hydrated groups in place rather than
clearing them, so a folder shared or copied without the sidecar keeps its
groups.

Other sidebar bodies live with their feature: the preview picker is
`features/preview/preview_view.py`, the image-table picker is
`features/image_browser/image_table/picker_view.py`, the Sample-Definitions group editor
is `features/grouping/replicate_panel_view.build_replicate_panel`, and the
Statistics group editor is `features/stats/tab_view.build_stats_group_editor`. They're stacked inside the same sidebar QWidget
and shown/hidden by `core/tab_controller.on_tab_change` based on the
active tab.

The top-bar collapse toggle (`Ctrl+B` / the panel button) routes through
`tab_controller.apply_sidebar_visibility`, which drives **whichever** sidebar
belongs to the current mode from one flag: the Review rail (`_sidebar_root`) in
Review mode, and each secondary pane's own `_left_sidebar` (the Analyze form
sidebar via `app._analyze_pane`, the Datasets page rail via `app._datasets_pane`)
in its mode — so the same button collapses the left panel symmetrically in every
mode.

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
| `core/channel_state_controller.py` | Channel-state machinery: `recalculate_threshold`, `set_active_channel`, `update_channel_selector`, `refresh_metric_combo_for_channel`. Owns the per-channel `(min, max)` cache that turns repeat channel toggles from an O(total cells) scan into an O(1) lookup. **Ordering contract:** `recalculate_threshold` publishes the freshly-detected `smfish_channels` set *before* it calls `update_channel_selector`, because that call ends in the Property-combo refresh and the combo reads the set to decide whether `smFISH Count` is selectable — publishing it afterwards leaves the item greyed out against count columns that already exist. Every combo asks that question through `domain/metric_registry.channel_supports_smfish`, which case-folds both sides: the global combo passes the normalised token (`gfp`) while the scatter, stats, and batch-export combos pass the displayed label (`GFP`), and a raw membership test disabled the item permanently on the upper-case side. |
| `core/plot_orchestrator.py` | Default-scope fan-out (line + distribution + heat map). Per-tab scopes (bar / scatter / scatter-agg) have their own entry points that consume scope-specific state. |
| `core/plot_style.py` | `apply_ax_style` — token-aware axes styling for matplotlib. `apply_export_style_prefs` then stamps the Properties-sidebar prefs (fonts, line/marker, grid, legend) over every artist, which means it can overwrite something a renderer meant as data. Three per-artist opt-outs bound it: `_aw_no_style_override` (skip the artist entirely — decorative lines like significance brackets), `_scatter_only_artist` (keep markers-only; don't join the points with a line), and `_aw_marker_is_data` (keep the marker *shape*; size and open-vs-filled still apply). The last is what the aggregate scatter sets, because there the shape encodes well identity and the legend is keyed on it — without it every point took the pref's shape while the legend kept advertising one per well. There are per-*axes* opt-outs too: `_categorical_xaxis` / `_categorical_yaxis` keep the log-scale pref off an axis whose ticks are category labels, `_aw_force_xlog` / `_aw_force_ylog` let an axis pin its own log state against the shared pref, and **`_aw_colorbar` takes an axes out of the pass entirely from the tick-rotation step onward**. A colour bar is a legend for its mappable's norm, not a plot axis: `fig.axes` includes it all the same, so without the flag the walk reset a `LogNorm` bar to a linear scale — discarding the log locator and formatter matplotlib installed, leaving the colours log-mapped beside linear labels — and drew gridlines and marker-edge styling over the bar's mesh. Typography and colours are applied before the opt-out, so a flagged bar still tracks the plot theme. Every colour bar the app creates (Heat Map, Spatial, Multivariate) sets it; the Heat Map re-stamps it each redraw because it refills a persistent `cax` through `cla()`. |
| `core/bus_wiring.py`, `core/redraw_coordinator.py` | Wire the `EventBus` signals to the redraw coalescer. |
| `core/scope_registry.py` | Register each feature's fold-change scope with its redraw callable (`features.barplot.renderer.redraw_bars`, `core.plot_orchestrator.redraw_app`). Called once from `wire_event_bus` during construction so the domain-layer `fold_change_scopes` module stays free of `features/` imports. |
| `core/status_view.py` | The status/log footer, and `_GUILogHandler`, which routes `well_viewer` log records into the drawer's `QTextEdit` via a queued `QTimer.singleShot` (records can arrive off the GUI thread). Two rules keep it from outliving its widget badly. **It never logs from its own failure path.** A failed append is caught as `RuntimeError` and handled silently — deliberately *not* `swallowed()`, whose DEBUG record would propagate straight back to the logger this handler is attached to, fail in the same place, and log again; a redraw's `processEvents()` drains that queue forever, which is a hang, not a slow run. **A handler whose widget is gone detaches itself** from every logger holding it, because nothing removes it on teardown — in a process that builds more than one window (the test suite) the dead handlers otherwise stay subscribed and every record fans out to widgets that cannot receive it. The document is capped at `_LOG_MAX_BLOCKS`, so the drawer is a bounded tail rather than an unbounded archive. |
| `core/ui_helpers.py` | Cross-cutting widget helpers (combo population, button factories, theme application). |

Per-feature controllers / renderers (under `well_viewer/features/`):

| Feature package | Role |
|-----------------|------|
| `features/lineplot/controller.py` | Line Graphs: redraw the 3-stacked-subplot figure. |
| `features/barplot/{controller,renderer}.py` | Bar Plots: redraw + bar-draw math (split because batch export reuses the math). |
| `features/scatter/{controller,callbacks}.py` | Scatter Plot (cells + aggregate). The per-axis fluor-gate (`fluor_gate_x` / `fluor_gate_y`) comes from the Cell Gating tab's per-channel floors, looked up by the axis's channel — which the scatter holds as the upper-cased dropdown label, so `_cell_gating_resolve_key` matches case-insensitively (a case-sensitive lookup returned the 0.0 "gate off" default and the tab's gate silently did nothing here). It is bypassed when an axis column ends with `_smfish_count` — the gate is a fluorescence-intensity floor and would strip every integer-count cell on a count axis. The mixed-axis case (one count, one intensity) still gates the intensity axis. The interactive rectangular gates live in `scatter/gating.py` (a documented naming exception): each box is stamped at draw time with its axis metric columns (`x_col`/`y_col`) and a `name` (auto-assigned `Gate N`, shown on the plot, editable in the Properties sidebar's gate list via `rename_gate`, which captions each row with those two columns via `data/metric_labels.gate_axes_label` so a gate list spanning several metric pairs is readable), so beyond the on-plot "% inside" labels its `compute_gate_inclusion` (matplotlib-free, reuses `_in_gate` + `resolve_value_series`) lets the Cell Gating tab turn the selected gate(s) into the `Gate_Pass` inclusion layer. The gating (inclusion) and thresholding (fraction-on) layers each have their OWN *Gates* multi-select checkbox menu, so they can be configured independently (`cell_gating_selected_scatter_gates(ctx, mode)`); each selection is a set of names on the typed slice, `ctx.state.cell_gating.gating_choices` / `.thresh_choices` (`None` ⇒ all gates, auto-including ones drawn later). Each box's stamped `x_col`/`y_col` also drive on-axes rendering: `gating.draw_gates` / `gate_edge_at` only show and hit-test a gate when `ctx.runtime.scatter.axes_cols` (the columns currently on the axes, recorded by `paint_gates`) match it — a gate drawn on a different X/Y pair is hidden, since its box coords are meaningless there. Drawing / editing / clearing a gate fans out through `gating._notify_gates_changed` to the callback the Cell Gating tab registers on `ctx.runtime.scatter.gates_changed_cb` (`cell_gating_sync_scatter_gates`), which refreshes the pickers and recomputes inclusion immediately (signature-guarded); the per-cell scatter opts out of `Gate_Pass` by default, so its cloud never shrinks under its own gates. That opt-out is the tab's **Apply gates** toggle (`ScatterState.apply_gates_to_points`, in the `Display ▾` popover with the other view options): on, the plot shows only the gated population. It filters on the gate *geometry*, not on the `Gate_Pass` column — `redraw_scatter` passes `collect_scatter_data(gates=gates_for_axes(ctx, col_x, col_y))` and `_collect_well` ANDs in `compute_gate_inclusion`. That is deliberate and load-bearing: `Gate_Pass` is written by the Cell Gating worker, asynchronously and against *its own* gate selection, so it can describe a different set of boxes than the ones on screen — a gate drawn or resized since the last pass, a selection that omits it, or gates belonging to another metric pair whose boxes this plot hides. Each of those produced the same symptom: survivors in one corner, an empty box in another. Computing membership here makes "the points you see are the points inside the boxes you see" true by construction. `gates_for_axes` applies the same column-pair rule `_gate_on_current_axes` uses for drawing, minus unstamped gates (`compute_gate_inclusion` skips those, so including them would contribute an all-false mask and blank the plot — a box drawn but not filtering beats an empty cloud). `collect.scatter_points_gated` gates the whole thing on the toggle plus one more condition: no gate being drawn or edge-dragged. That matters because a gate release fans out to a recompute and a redraw, so a live filter would erase every point outside the box the instant it was drawn, leaving nothing to judge or resize the gate against. The toggle is otherwise self-contained — it does not consult `use_scatter_gates`, and filtering here changes nothing about what any other view counts. `collect.gate_filter_title_suffix` tags the axes title with `· gate-filtered` while it bites, since the toggle itself does not travel into a copied or saved figure. A second, independent Cell Gating toggle applies the same selected gate as a **fraction-on threshold** instead of (or as well as) inclusion: the worker writes a per-cell `Gate_On` column and `aggregate_with_threshold_df(on_col="Gate_On")` (selected via `stats_compute._thresh_on_col`) makes gate membership the "on" population for fraction-on, replacing the `value > ThreshFracOn` cutoff. The per-cell tab's **Color by time** toggle (`_scatter_color_by_tp`) colours each point by its timepoint instead of by well identity (one `Line2D` per well-timepoint in a shared viridis scale from `collect.timepoint_color_map`, which the aggregate scatter reuses); it overrides density shading, and with >1 well selected it forces the "Separate axes" stacked layout (`_render_scatter_faceted`) so each well's time-coloured cloud gets its own panel. |
| `features/heatmap/{controller,models}.py` | Heat Map: layout + redraw. A canvas click selects the cell's wells; Shift adds and Ctrl toggles. `_event_modifiers` resolves those from the Qt `QMouseEvent` behind `event.guiEvent` — PySide6 hands back a `KeyboardModifier` flag object that `int()` rejects, so the bits are read through `.value` — and falls back to matplotlib's own `event.key` when no GUI event is attached. `features/heatmap/sidebar_view.py` holds the layout editor; its **Auto-populate from selection** plans cells through the pure `_autopopulate_cells`, which reads whichever store the plate's current mode expresses the selection in — `selection.selections` in replicate mode and `selection.selected_wells` in plain mode (one cell per well, in plate order). Reading only the latter laid out whatever stale individual wells were left in it whenever replicate groups were defined, since replicate mode leaves that store empty. In replicate mode the shape of the plan then follows `heatmap.repset_avg`, which is what decides whether a cell means a set or a well: on, one cell per *visible* set holding all of its loaded wells; off, one cell per member well in plate order. A cell holding several wells renders as `A01+3` (members in the tooltip) with its well token in `_CELL_TOKEN_ROLE`, so the summary never reaches the model, and a drag moves the cell's whole well list rather than the single token the drag carried. **Cell numbers.** `_format_cell_value` picks a format from the metric and the magnitude — counts whole, fractions to two places, intensities scaled to their size — unless the Properties panel's **Value format** (`heatmap_value_format`, a printf-style string) overrides it; a format the value cannot satisfy falls back to the automatic choice rather than blanking the cell. The drawn cell and the hover readout share it, so the number under the cursor matches the one on the grid. Because the format changes annotation *text* rather than style, the Properties row triggers a real redraw instead of the auto-wired restyle. **Log scale** routes the values through a `LogNorm` (raw data reaches `imshow`, so the cell numbers stay in raw units) and masks non-positive entries; when the data carries no usable positive range it says so on the status line instead of quietly rendering linear, which was indistinguishable from the toggle doing nothing. |
| `features/distribution/controller.py` | Distribution tab: histogram / KDE / violin. Log-X/Log-Y is a **single per-figure axis pref** (`x_log` / `y_log`) that the on-plot checkboxes and the Properties-sidebar toggles both drive — they previously wrote two independent stores and the export pass clobbered the button. Log-Y histograms pass `log=log_y` straight into `ax.hist` rather than computing a density from 0 and log-scaling afterwards (which corrupted the bar heights). |
| `features/stats/controller.py` | Statistics tab: groupwise tests, KS CDF. The replicate unit is the **one rule** `domain/stats_compute.replicate_unit` — shared with the bar/line error bars so the test's `n` and the plotted spread never disagree. In **FOV mode** every FOV **across all of a group's wells** is an independent replicate (`n` = total FOVs, pooled across wells); otherwise each well is one replicate. Pooling FOVs across wells is pseudo-replication (smaller p-values than a well-level test) — an explicitly chosen mode. |
| `features/image_browser/image_table/controller.py` | Image Table grid: render-model assembly + Generate/export orchestration. The "turn cache + state into pixels" work is split into sibling modules it re-exports (so call sites and external importers are unchanged) and which `test_monolith_budgets.py` ratchets: `image_compose.py` (LUT palette + per-channel cmap/LUT resolution, array loading, mask/threshold rendering, the RGB cell composer `cell_display_rgb`, the boundary overlay, and the channel-overlay blender `overlay_cell_rgb`), `grid_layout.py` (axis/cell layout math + the per-dimension well/channel/FOV/timepoint option lists), `build_layout.py` (flat + nested grid assembly + `image_table_apply_spec`), and `facet_view.py` (faceted render placement — axis labels + group spacing). **One facet plan, two surfaces.** `facet_view.plan_facets` computes the placement and the label text from the builder's nesting; `facet_view.render_facet_chrome` turns that plan into the live grid's `QLabel` axis strips, and `renderer.py` replays the *same* plan onto the export figure's `GridSpec`. So Export to File, Copy PNG, Copy SVG and Save to Saved Plots all carry the row/column axis labels, the rotated group strips, the corner label and the inter-group gaps the user sees on screen — the per-cell titles (`show_titles`) are an Export-settings opt-in on top, off by default because the axis labels already identify every cell. `renderer.py` is Qt-free: it duck-types the plan through its argument rather than importing `facet_view`, so the geometry is unit-testable without building a widget, and it restates the label palette in dark-on-light because the figure is saved transparent and lands on a white page where the app's dark chrome colours are unreadable. The export gridspec **pins its own margins and solves `wspace`/`hspace` for an absolute inch pad** (matplotlib spreads one uniform separator between adjacent slots, sized as `wspace × usable/n` before the ratios apply). It deliberately does not use `tight_layout` or `constrained_layout`: both redistribute space to fit each axes' content, which flattens exactly the group gaps the layout exists to express. **Brightness parity.** The Saved Plots snapshot pre-shrinks each cell to a pixel budget with an *area-average* pooler (`export._area_pool_rgb`) — the same averaging Qt's smooth transform applies to the live thumbnail — so the saved image reads at the grid's brightness; peak-preserving pooling dilates sparse fluorescence and makes the snapshot brighter than its source. Symmetrically, the live thumbnail passes an explicit `(0.0, 255.0)` window to `make_overlay_thumb`, because `cell_display_rgb` has already applied the channel's LUT and colormap and re-fitting a finished image to its own min/max distorts any colormap whose ends are not black and white. **Decode caching is two-tier and shared across every export path.** `ctx.state.image_table.image_cache` holds the per-`(well, chan_lower, variant)` ref-dicts (which files exist for a well+channel); `data/image_discovery.py`'s module-global `_DECODE_CACHE` holds the actual decoded pixel arrays, keyed by `(resolved_path, greyscale, mtime_ns)`. `export._build_export_figure` writes into the *same* `image_cache` object `image_table_generate` uses (never a throwaway copy), and calls the identical threaded `controller._prefetch_cell_arrays` warm-up Generate uses before rendering — so a Save to Saved Plots or Export to File run benefits from a prior Generate's decode work and vice versa, and a save without a prior Generate still decodes in parallel rather than one cell at a time on the GUI thread. `_prefetch_cell_arrays` dedupes across calls via `state.image_table.prefetched_cells`, a persistent `set` of `(well, chan_lower, variant, tp, fov)` tuples already submitted to the thread pool — a *per-cell* key, unlike `image_cache`'s per-well+channel ref-dicts, because a grid faceted by timepoint or FOV needs every distinct cell warmed, not just one representative per well+channel. Because `export.py` needs one function out of `controller.py` (which already imports `export` at module scope as its re-export point), that one edge is a function-scoped import inside `_build_export_figure` — the standard cycle-break pattern the codebase uses elsewhere in this feature (§ `test_deferred_imports.py`'s budget). **Save to Saved Plots quality is a tunable preset**, not a fixed constant: `GALLERY_QUALITY_PRESETS` (`export.py`) maps `draft`/`standard`/`high` to a `(cell_max_px, gallery_max_px)` pair — the per-cell pre-shrink budget and the assembled grid's longest-side cap — so a single user choice (the Export dropdown's quick-pick submenu, or the "Export settings…" dialog) scales both together; scaling only one independently would either cap quality at the other's bound or waste compute. Export to File / Copy PNG / Copy SVG always render full resolution — only the gallery-save path reads this setting. **Headless plain-data render model.** `ctx.runtime.image_table.cells` is a `rows × cols` grid of plain `{"well","chan","tp","fov"}` string dicts — the render contract, no Qt widgets. The renderer (`image_table_generate`), the layout math, the per-cell override popover (`grid_ops.py`), and `persistence/view_state.py` all read/write those strings directly; off-axis held values live on `state.fixed_vals` (read by `grid_layout._fixed_off_axis`) and grid size on `state.rows`/`state.cols`, so there are no held-value combos or grid-size spinners. A **grouped/tiled** well layout (`group_size`, 0 = off) bands each channel across multiple rows; it persists through `persistence/view_state.py` (`group_size` / `group_row_span`). **Builder shelf.** The **sole** authoring surface is `builder_view.py` — a root + nested-channels drag-and-drop tree: each axis gets a root (Well/Timepoint/FOV) with channels nested under it, each channel carrying its own render options (raw/corrected · mask · ≥thr · boundary · colormap/LUT) via a ⚙ menu; a derived **Fixed** panel auto-lists the off-axis roots with value pickers; a **Separate ↔ Overlay** switch (`state.composite`) collapses a root's channel axis to a single entry and additively blends every selected channel into each cell — "blend this root's channels into one cell" (the layout emits the `image_compose.OVERLAY_CHAN` sentinel as the sole channel value via `grid_layout._axis_dim_values`; `image_compose.overlay_cell_rgb` blends it in both the live grid and export, and the flag round-trips through `view_state`); wells come from the sidebar plate. The shelf edits a Qt-free `TableSpec` (`state.py`) and calls `build_layout.image_table_apply_spec`, which flattens it onto the existing `row_dims`/`col_dims`/`active_*`/`composite`/`group_size` state (+ a per-channel `channel_opts` block) and drives the layout + a debounced `image_table_generate`. Per-channel display is **position-independent**: the four `image_compose._row_*` helpers resolve options from `channel_opts` keyed by the cell's channel, and the live generate path is unified onto `cell_display_rgb` so the live grid, the channel-overlay blend, and export always agree. A ⚙ "≥threshold" reuses main's `"<CHAN> (binary)"` token. Per-cell tweaks come from right-clicking a rendered cell (`grid_ops.image_table_cell_override_menu`, which writes the cell dict). **Plate (96-well) layout.** A **Plate A–H × 1–12** toggle in the shelf header lays each selected well's image out in true plate positions with correct axis labels, using two *virtual* layout dims — `well_row` (A–H) and `well_col` (1–12), enumerated from the plate selection in `grid_layout` (`_well_row_options` / `_well_col_options`). They never get their own render-model cell key: `grid_layout.compose_plate_well` resolves each `(row, col)` back to the real `well` token (blank when that position is absent), and `facet_view._cv` derives them back from the composed well so the A–H / 1–12 headers label every band — unlike the `group_size`/Wrap trick, whose facet headers only reflect the first band. It rides the existing single-dim `_build_nested_layout` path (`row_dims=["well_row"]`, `col_dims=["well_col"]`); the channel is held fixed (`TableSpec.plate` flag round-trips through `spec_to_state_fields` / `spec_from_state`), and it's mutually exclusive with Wrap *and* with channel Overlay (plate mode writes `composite=False`). |
| `features/preview/controller.py` | Preview-panel helpers (well-picker sync for the image tabs). The image pixel-value hover tooltip lives in `review_image/controller.py` next to its only caller. |
| `data/image_identity.py` | **Single source of truth for image filenames, cell identity, and the suffix/role catalog.** Owns three concerns deliberately co-located so they cannot drift: (1) `CellKey` — `(well, fov, timepoint, nucleus_id)` with one canonical normalisation chain; every site that joins CSV-row data to image-derived data builds keys via `CellKey.from_row` / `cell_keys_for_dataframe` (the smFISH writer, `cell_overrides`, `review_image`'s row keys, and `_process_well`'s counts construction all consume it). (2) `parse_image_filename` / `build_image_filename` — exact inverses driven by a `PipelineSchema` loaded once from `pipeline_info.json` via `load_schema(...)`. No parallel regex fallbacks; single-FOV pipelines are modelled by `has_fov=False` rather than a special-case extractor synthesising `"1"` (the empty-FOV-to-`"1"` rule is enforced uniformly by `normalize_fov`). (3) `ROLE_CATALOG` — declarative `RoleSpec` per role (`fluor`/`tophat`/`mask`/`overlay`/`smfish`) mapping suffix, output extensions, channel-scoping, and precedence; `classify_filename_role` and `role_output_filename` both consume it. (As of the §6.2 self-sufficiency fix this catalog is **reader-side only** — the pipeline writer, `process_microscopy.build_processed_output_path`, uses explicit `suffix=` strings rather than importing `well_viewer`. If catalog-driven *writing* is revived, move `ROLE_CATALOG` into a Qt-free `*_core` module first so the pipeline can import it without breaking the contract.) The full time-format matrix (`T01`/`24`/`24h`/`1.0`/`day3`/`02d04h30m`/`timepoint_hours` float) collapses to a single canonical form via `normalize_tp`. Stdlib-only at import; pandas is lazy inside `normalize_fov_series` and `cell_keys_for_dataframe` so `process_microscopy.py` can import the module without adding pandas as a pipeline dependency. |
| `features/smfish/{controller,worker}.py` | smFISH spot detection + the background worker. Apply Global Threshold rewrites every per-well CSV's `<channel>_smfish_count` column, then in its after-csv callback persists the threshold to `pipeline_info.json::smfish_thresholds` via `persistence/smfish.py`, drops the per-well DataFrame cache + the aggregation stats cache, redraws plots, and refreshes the Review CSV table — without those last three steps, downstream plots and the review table kept showing pre-apply counts. The after-csv callback is routed through `_WorkerBridge.after_csv` (a queued `Signal(object)`) rather than invoked directly from `apply_global_threshold_async`'s daemon thread — the worker thread has no Qt event loop, so the previous `QTimer.singleShot(0, …)`-from-worker scheme silently dropped the refresh on the floor and the user had to close and reopen the app to see the new counts. Progress crosses the same bridge: the worker's `progress_cb(done, total)` fires once per well in the counting pass and again per well in the CSV-write pass, `_WorkerBridge.progress` (a queued `Signal(int, int)`) marshals it onto the GUI thread, and the tab drives the shared status-row `_progress_bar` — the widget a dataset load steps — through `ctx.widgets.get_or_none`, so the bar is simply absent rather than fatal in a build without it. The bar is shown when the run is launched and hidden by the `done` signal, which every terminal path emits: success, cancel, a crash caught by `_run_guarded`, and the empty-channel guard (that guard returning on a status line alone left the tab's `apply_running` flag set, so the button rejected every later click as "already running"). The write loop reports from a `finally`, so a well skipped for an unresolvable or locked CSV still advances the bar instead of parking it short of its maximum. The writer takes the explicit `well_to_csv` map the loader populated (not a `out_dir.glob("*_<well>.csv")` lookup, which silently skipped wells whose CSVs had no underscore prefix), drops any case-equivalent column before reassigning (so a stale `W3Cy5_smfish_count` from an older pipeline run is overwritten cleanly), and builds both producer (`_process_well`) and consumer (`_write_counts_to_csvs`) keys via `data/image_identity.CellKey` — the unified identity module enforces the same well/fov/tp/nid normalisation chain on both sides, so empty-fov, `"-1"`, letter-prefixed tp like `"T01"`, padding like `"001"`, and `.0` float artefacts on nid all collapse identically. A `matched == 0` WARN fires from the writer when every lookup misses so future `(fov, timepoint, nucleus_id)` format drift names itself in the log drawer. |
| `features/image_browser/review_image/{controller,renderer}.py` | Segmentation tab review-image + Review-CSV jump-to-image. A **Raw / Flat-field / Corrected** `SegmentedControl` (`ReviewImageState.image_source`) picks which image stage the canvas shows: "Corrected" (the saved final = flat-field + background, default), "Raw" (unprocessed), or "Flat-field" — the raw frame with *only* the selected flat-field applied, recomputed live via `core.flatfield` (`flatfield_field_for`, cached per channel) to validate the flat-field step before any background subtraction. The Segmentation and Image Table control rows each host an **ON-threshold quick-edit** `Stepper` (mirroring the Plotting ctxbar) that edits the active channel's `ThreshFracOn` via `set_thresh_frac_on` and re-renders so the binary "≥ ON threshold" overlay updates live; `channel_state_controller.refresh_thresh_frac_quick_edit` keeps all three steppers in sync on channel switch / edit. Double-clicking a Review-CSV row jumps to that cell: the handler switches to the Segmentation sub-tab, sets `preview.selected_well`, renders, drives the FOV and timepoint combos, and zooms to the nucleus. It ends by re-running the sidebar well picker's refresh — the sub-tab activation hook already refreshed it, but that ran before the row's well was chosen, so without the second pass the jump lands on the right image while the sidebar still reads "No well selected" with no well lit on the plate. The Review-CSV table normalises its FOV combo values through the same `review_norm_fov` chain the cell-click navigation uses (the combo is non-editable, so a raw-vs-normalised mismatch would silently miss `setCurrentText` and strand the filter), and `refresh_review_csv_rows` hard-caps the materialised rows (5000) — a `QTableWidget` builds one heavyweight item per cell, so an unbounded build (the no-match fallback, or a multi-well selection spanning every FOV/timepoint) could OOM-kill the process. The Segmentation control strip (`preview/panel_view.py`) is consolidated into a single `widgets.FlowLayout` row of **Edit / View / LUT / Colors ▾** menu-buttons (built with the shared `ui_helpers.make_action_menu_button` / `add_menu_action` helpers) so it wraps instead of clipping when Split View halves the pane; every widget keeps its registered name / runtime attribute, so the renderer and controllers reach them exactly as before. Zoom is the exception to the popover treatment: **− / readout / + / Fit** sit inline on that row, since zoom is in constant use while reviewing segmentation. The readout (`_review_image_zoom_lbl`) shows the factor relative to fit-to-window and is refreshed by `renderer.refresh_review_image_zoom_label` from inside `render_review_image_display` — the single point every zoom path funnels through, so the buttons, the scroll wheel, and the Review-CSV jump's zoom-to-nucleus all keep it honest. |
| `features/image_browser/playground/{controller,renderer,worker}.py` | Segmentation-page **Playground** sub-tab: an interactive testbed for the pipeline's background-flattening / subtraction methods on a single raw frame chosen by well/channel/timepoint/FOV. Loads the raw frame + precomputed mask via the Image Table loaders (`_load_array` / `_load_mask_array`) and applies the chosen flat-field + background method through `process_microscopy.correct_single_frame` — the **same** per-frame orchestration the batch worker's `_correct` uses (extracted so pipeline and Playground share one code path; a parity test pins the equivalence), so no method is reimplemented. The `empty_well` / `basic` reference fields are estimated from a chosen background well on a daemon thread via a queued-signal bridge (smFISH pattern); the correction itself stays synchronous on the GUI thread, so `playground_on_change` memoizes its result (`state.last_correction_key`/`last_corrected_arr`, keyed on the decoded frame's identity — safe because `image_discovery`'s decode cache returns the same array object on a hit — plus every correction parameter) and skips recomputing when nothing that affects the output changed; `build_playground_tab` triggers exactly one render on first open (via `registry._activate_playground`, which always follows the tab's construction in the same dispatch), not its own redundant call plus the activation hook's. Display reuses the LUT/colormap (`ui_helpers.lut_colormap_to_rgb`) and a binary/boundary overlay; per-source LUT memory, click-drag pan, frame-local binary threshold (defaults from but never writes back to the app-wide `ThreshFracOn`), and a QC size-cleanup preview are Playground-local. |
| `features/image_browser/cell_browser/{controller,collect,worker,tab_view,criteria_view}.py` | Cell Browser gallery: a filtered grid of per-cell crops composed off-thread (`worker.CellImageLoader`, reusing the Image Table loaders + zip cache). Sources cells from the active **plate selection** (`collect.cell_browser_source_wells`). **Deferred render:** every control — filters (criteria/timepoint/FOV/sort), channel chips, the per-channel `LUT ▾` popover (LUT min/max + a colour swatch editing `tint_by_channel`), and the **Raw / Corrected** source `SegmentedControl` (`CellBrowserState.use_tophat`, threaded through `load_page` → `compose_cell_rgb` → `_load_array`) — only *stages* state; the single top-bar **Generate** button (`controller.apply_filters`) re-collects and re-composites. There is no separate "Apply filters" button. **Gate-source mode** (the sidebar's *From scatter gate* checkbox, `CellBrowserState.scatter_gate_mode`) swaps the numeric filter for the scatter gate(s): `criteria_view._set_criteria_enabled` disables the criteria rows + *Add criterion* and reveals a muted line stating why. The checkbox is deliberately sticky — nothing resets it on tab switch or dataset load — so that line is the only on-screen account of an editor that is otherwise inertly greyed out. |
| `features/grouping/controller.py` | Sample-Definitions group editor logic. |
| `features/export/{service,style}.py` | CSV export (`service.py`) + the floating Export Style sidebar's prefs + apply pipeline (`style.py`); `style.py` also owns `copy_figure_to_clipboard` (Copy SVG → native macOS pasteboard via pyobjc, Qt-clipboard fallback). |

Shared domain helpers (under `well_viewer/domain/`):

| Module | Role |
|--------|------|
| `domain/fold_change.py` | Fold-change normalization helpers (vs control well/rep-set, vs each member's first timepoint). Consumed by line / bar plot, batch export, and `features/export/service.py`. |
| `domain/stats_compute.py` | `well_aggregate_stats`, `compute_rep_stats`, `aggregate_well`, `aggregate_group` — the pure-math layer the bar / line / scatter controllers share. `aggregate_well` / `aggregate_group` resolve an optional delegate through `ctx.runtime.aggregate.well` / `.group`, which is the hook the pure-logic test `MockApp` uses to short-circuit aggregation (its `_aggregate_well` / `_aggregate_group` methods surface through the runtime's back-compat view); the production `WellViewerApp` provides no such delegate, so the value is `None` and the DataFrame path is taken at runtime. The result cache is keyed on everything that can move a number, including `_gate_cache_sig` — the scatter-gate state for **both** layers. Thresholding is in there because gate membership defines the on-population; inclusion is in there because "Use scatter gates" replaces the per-channel intensity floors, and the `fluor_gates` already in the key do not move when that checkbox is toggled. Mode-on-with-no-gates still differs from mode-off (the floors are bypassed either way), so the flag itself is part of the signature, not just the gate geometry. It returns `None` when neither layer is active, leaving a plain threshold-mode session's keys byte-identical. |
| `domain/batch_models.py`, `domain/ratio_models.py`, `domain/selections_model.py` | Plain dataclass models. |

Data-layer helpers (under `well_viewer/data/`):

| Module | Role |
|--------|------|
| `data/auto_threshold.py` | Otsu-based per-channel default threshold estimator. Delegates the shared helpers (`parse_tp_hours`, `pick_endpoint_timepoints`, `sample_cell_and_bg`) to the top-level `pipeline.auto_threshold` so the pipeline and the GUI agree exactly. The Cell Gating tab's *two-well* optimizer shares its numeric core the same way, through `pipeline.threshold_optimize` — the sweep, the relative-fraction-on-gap objective and the range/steps validation live there, so the Analyze side's control-well pass and the Review side's "Optimize ThreshFracOn" button reduce to one implementation. `gating_compute` re-exports those names and keeps only what depends on the viewer: the fold-change layer (which would otherwise drag `well_viewer.domain.fold_change` into `pipeline/`) and the DataFrame/ratio plumbing. |
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
| `data/data_loading.py` | CSV parsing, channel detection, threshold-aware aggregation, per-well array helpers. `df_included_mask` is the single inclusion chokepoint — effective inclusion is `Included==1 AND QC_Pass==1 AND Crop_Pass==1 AND Gate_Pass==1` (the optional `QC_Pass` / `Crop_Pass` / `Gate_Pass` layers default to pass when absent), and every consumer (overlay, plots, stats, aggregation, export) composes it so QC debris-gating, the Segmentation crop, and scatter-gate inclusion apply uniformly. The `Included` layer is set by the Cell Gating tab's `GatingWorker` from the per-channel FluorGating intensity floors; that tab reports the composed result live — `cell_gating.controller.cell_gating_refresh_included_count` walks the row cache through this same chokepoint and renders `gating_compute.format_inclusion_counts` into a muted line under the gate controls ("Included for analysis: N / M cells · K failed QC"). It recomputes rather than reading the worker's own tally, for two reasons: the worker counts the gating layer alone, so pairing that number with a QC count gives a line whose figures do not reconcile (most QC failures sit inside the gating layer's kept set); and a dataset whose gates are all at their defaults skips the pass entirely on load, which would leave a worker-fed label blank on the commonest path. It refreshes on pass completion, on failure (flagged as such), while a pass is in flight (marked `updating…`), on Cell Gating tab activation — the catch-all covering crop edits, manual per-cell deletes and that default cold load — and from `ctx.runtime.qc.changed_cb`, a feature-agnostic seam `qc.recompute_qc_all` fires after rewriting `QC_Pass` so neither feature imports the other; the separate `Gate_Pass` layer carries the scatter-gate inclusion (the union of the Scatter Plot's rectangular gates via `scatter.gating.compute_gate_inclusion`) and, when "Use scatter gates" is on, *replaces* the intensity floors (the worker sets `Included=1`, `Gate_Pass=`membership). Keeping it a separate layer lets the **per-cell scatter** default to `df_included_mask(df, include_gate=False)` so it keeps showing the full cloud (and the gates over it) instead of filtering itself by its own gates, while every other view filters via the default. That tab's **Apply gates** toggle does *not* switch this layer on: it filters on the gate geometry instead (`collect_scatter_data(gates=…)`), because this column can describe a different set of boxes than the plot is drawing. `gates` defaults to `None` for every other caller (batch export, the CSV export service), so a batch job can never inherit the live tab's choice. Measurement columns are normalised to lower-case at read time so per-channel lookups are case-insensitive; `smfish_count` metrics honour the channel's `ThreshFracOn` cutoff just like intensities (a cell is "on" when its spot count exceeds the cutoff — set `ThreshFracOn=0` for the classic "any detected spot" population), and only shape/morphology metrics pass `threshold=-1.0` to keep every cell (they have no per-channel ON cutoff). The `aggregate_with_threshold_df` contract is unchanged — `value > threshold` defines the "on" population — only which threshold the controllers feed it for counts changed. `resolve_value_series` emits a deduped WARN naming the missing column when both the direct and case-folded lookups miss, surfacing schema-mismatch bugs that would otherwise show up as silently-empty plots. |
| `data/image_discovery.py` | Scans zips / loose directories for masks, overlays, tophat images. Classifies by suffix + schema. |
| `data/image_resolver.py` | Filename → kind classifier (`mask` / `overlay` / `fluor_processed` / `smfish`). Also the single home for `normalize_numeric_token`, the FOV/timepoint token normaliser that collapses `1`, `01` and `1.0` to one key. Every site that compares such a token routes through it: `review_image/collect.review_norm_fov` (the review side's public name, kept because the image-table and review-image call sites are written against it), `core/dataset_state._norm_fov` and `image_table/image_compose._norm_token` are aliases of it. Tokens reach the viewer from filenames, CSV columns and combo text, so a second copy that formats differently silently strands a filter. |
| `data/cache_projectors.py` | Registry of the inclusion-verdict projectors, plus `project_verdict_column` — the one cache walk they all perform (resolve the target wells, skip empty / missing frames, write the int column, log a computation failure against its layer rather than raising). `features/qc` and `features/crop` register here and share that walk; the registry is what lets `data_loading.get_rows` apply them without the data layer importing `features/`, and the shared walk lives here for the same reason — neither feature may depend on the other. |
| `domain/viewer_state.py` | `read_pipeline_info` + the `make_schema_extractor(sep, fov_idx, tp_idx)` factory used everywhere the app needs a `(fov, tp)` from a filename. |
| `domain/plate_layout.py` | `PLATE_ROWS`, `PLATE_COLS`, the 96-colour well palette (one colour per plate position — a shorter, submultiple-of-12 palette repeats colours down each column). |
| `domain/selections_model.py` | Saved-selections schema v2: shape, validators, the v1→v2 migration. The on-disk contract is documented in the module's own docstring. |
| `domain/sample_definitions.py` | Read / write the `sample_definitions` block inside `pipeline_info.json`. |
| `domain/batch_models.py` | `BarGroup` + `ReplicateSet` — plain dataclasses used by every plot tab + batch export. |
| `domain/ratio_models.py` | Channel-ratio definitions (`gfp/mcherry`-style virtual channels). |
| `features/heatmap/models.py` | Heat-map-specific layout dataclasses (used by the heatmap controller + persistence). |

### 5.7 Persistence

Every "save state into the dataset folder" path lives under
`well_viewer/persistence/`. One module per JSON file:

| File on disk | Module | What it holds |
|--------------|--------|---------------|
| `pipeline_info.json::cell_gating` | `cell_gating.py` | Cell-area + per-channel `fluor_gates` + `thresh_frac_on`, plus the scatter-gate inclusion state (`use_scatter_gates` + `scatter_gates`, each gate carrying its `x_col`/`y_col`) written only when that mode is on. |
| `pipeline_info.json::smfish_thresholds` | `smfish.py` | Per-channel smFISH spot-detection threshold (`{channel: float}`). Saved only on a successful Apply Global Threshold so the value the spinbox shows matches the spot counts baked into the per-well CSVs. Restored on dataset open and on channel-combo switch. |
| `pipeline_info.json::sample_definitions` | `sample_definitions.py` (top level) | Saved selections + replicate sets / groups + well labels + notes. **Auto-saves** on edit via the registry (`autosave_to_pipeline_info`, a silent variant — no dialogs/toast, no-op without a data dir) in addition to the explicit Save button; well-label cells and the Notes box commit on `editingFinished` / focus-out (not per keystroke), discrete rep-set/group edits via `rebuild_all`. |
| `persistence.json::crop_region` | `crop_region.py` | Dataset-wide crop rectangle drawn on the Segmentation tab (`DatasetState.crop_region`, source-mask pixel tuple). Feeds the `Crop_Pass` inclusion layer; an absent / cleared crop removes the section. |
| `persistence.json::ratios` | `ratios.py` | User-defined ratio channels. |
| `persistence.json::heatmap_layouts` | `heatmap_layouts.py` | Saved heatmap layouts + cmap/scale state. |
| `persistence.json::line_order` | `line_order.py` | Line plot replicate-set / well draw order. |
| `persistence.json::view_state` | `view_state.py` | Active tab, selected wells, channel/metric, fold-change scopes, per-tab combos, export-style prefs. Saved on dataset switch + app close. |
| `persistence.json::qc_params` | `qc_params.py` | QC debris-gating thresholds edited on the Sample Definitions → Cell Gating tab (the fundamental always-applied inclusion layer); seeded from `pipeline_info.json` `qc_options` when unset. |
| `cell_overrides.json` | `cell_overrides.py` | Per-cell `Included` overrides set on the Segmentation tab (kept separate — large + write-heavy). |
| `bar_groups.json` | `bar_groups.py` | User-picked file for bar-plot group state (no auto-save). |

`persistence.json` is a single consolidated viewer-state document managed by
`_doc.py`. On first load it migrates any legacy `ratios.json`,
`heatmap_layouts.json`, or `line_order.json` sidecars into the unified file
and deletes them. `qc_params` migrates its own legacy `qc_params.json` (which
wraps its data, so it can't use the generic migrator) inside `qc_params.py`.

Persistence modules expose `load_from_data_dir(app)` / `save_to_data_dir(app)`
function pairs. They are registered, by name, with the **`PersistenceRegistry`**
(`well_viewer/persistence/registry.py`, built once in `WellViewerApp.__init__`
as `app._persistence`); call sites use `app._persistence.{save,load,
schedule_save}(name)`.

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
layout drag-and-drop, cell-override toggles, sample-definition edits, crop
drags) coalesce into one disk write per 500 ms burst; this replaced five
identical per-domain `schedule_save`/`_flush` pairs and their
`_<name>_save_pending` flags. Auto-saving domains: `ratios`,
`heatmap_layouts`, `cell_overrides`, `line_order`, `view_state`, `qc_params`,
`notes`, `sample_definitions`, and `crop_region` (`bar_groups.json` is the lone
explicit user-picked file with no autosave). `cell_overrides.json` v2 stamps
each row with the well's
`<well>_out.zip` mtime so a pipeline re-run drops the now-stale overrides
on load instead of re-binding them to unrelated cells.

**Resetting saved state.** The header's `rotate-ccw` button resets only the
*view* (`view_state.reset` — selected wells, fold-change scopes, per-tab combos).
A second header button (`trash-2`, top-right next to the Presentation/plot
screen↔publication toggle) does a **full** reset: `_doc.delete(app)` deletes the
whole `persistence.json` (every section above) and clears the in-memory cache,
then `AllWellApp._on_reset_saves_clicked` reloads the dataset fresh. It's the
"my saved state is wedged" escape hatch.

`persistence.json` is not the whole of the user's state, though, so the handler
clears the sidecar's user-curated blocks first: `persistence.cell_gating.clear_from_pipeline_info`
(fluorescence gates, ThreshFracOn cutoffs, the drawn scatter gates and their
per-layer selections) and `persistence.smfish.clear_thresholds`. Those blocks
live in `pipeline_info.json` so they travel with the dataset, which meant a
reset that only deleted `persistence.json` left every gate standing and the
reload restored them from the sidecar as if the reset had skipped them. Each
helper resets the live typed slice *before* touching the file, so nothing in the
reload can write the old values back. The pipeline's own keys in the sidecar —
including the sample-definition labels — are left intact, as are the dataset's
data files. The handler is synchronous, so no debounced save can race between
the delete and the reload.

### 5.8 The Export Style sidebar

Each plot tab pre-allocates a right-side dock via
`make_plot_with_right_dock(parent)`. The dock is hidden by default and a
**Properties** button on the tab's controls row toggles it. When opened, the
dock hosts an `ExportStyleSidebar` (from `features/export/sidebar_view.py`)
backed by a `plot_style.figure_prefs(ctx, fig)` view. The widget binds to that
view via `_bind_getter_setter`, so changes flow:

```
Properties widget → figure_prefs(ctx, fig)  ── shared style ─→ export_prefs.style_prefs
                        │                   └─ scoped keys ──→ export_prefs.axis_prefs[fig_key]
                        ↓
                  apply_export_style_prefs(fig, prefs)
                        ↓
                  canvas.draw_idle()
```

**What is shared and what is per figure.** Typography, grid, legend and ticks
are deliberately app-wide: one setting gives a whole figure set a consistent
look for a paper. Two families are per figure instead, keyed by
`figure_pref_key` (`line` / `bar` / `scatter_cells` / `scatter_agg` / `heatmap`
/ `distribution`) — see `plot_style.FIGURE_SCOPED_PREF_KEYS`:

- `AXIS_SCOPED_PREF_KEYS` — limits, log scaling, and the per-axis bucket
  bookkeeping. A bar chart's y-limit means nothing on the scatter tab, so an
  unset axis key reads the neutral default and never consults the shared dict.
- `MARK_SCOPED_PREF_KEYS` — line width/style and marker size/style/edge width.
  The right mark size is a property of what the plot draws: five aggregate
  points want an 8pt marker, a 50,000-cell per-cell cloud wants 2pt, and
  sharing one value meant restyling the Scatter tab's aggregate sub-tab
  restyled its per-cell sub-tab into illegibility. Unlike the axis keys, an
  unset mark key falls back to the shared dict before the default — nothing
  writes those globals now, so that is the value saved before marks became
  per-figure, and it keeps applying until the user gives a figure its own.

`features/export/style.py` owns the prefs dict + the apply pipeline. Every
redraw path ends with a call to
`features.export.style.apply_export_style_to_current(app, fig, canvas)` so
the sidebar's toggles (grid on/off, axis label size, log-scale, …) survive
a re-render. The low-level pass that flattens each figure's x-tick labels to
the `x_tick_angle` preference lives in `core/plot_style.apply_export_style_prefs`;
it is the single chokepoint every plot tab routes through, so it **syncs
horizontal alignment with the applied angle** — `ha="center"` at 0°,
`ha="right"` at any non-zero angle.

**Per-panel axis limits.** The bar figure is a 3-panel stack (Value / Fraction
/ Events), so axis limits are stored and applied **per-panel** — read from each
panel's own prefs bucket (like the font sizes), not as a single flat `y_lim` —
so a limit set on the Fraction or Events panel sticks instead of that panel
auto-scaling.

### 5.9 Typed state, the event bus, and the DI context

Five pieces let controllers and tabs depend on typed seams instead of the loose
`WellViewerApp` namespace: the typed **state** objects, the **event bus**, the
**widget registry**, the per-feature **runtime** namespaces, and the
**`ViewerContext`** that bundles them. The state modules — cross-cutting ones under
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
| `PlotState` | SEM/SD, per-FOV, legend, draw order, fold-change normalization, the bar **significance-bracket** toggle (`show_significance`, N7), and the shared **focus timepoint** (`focus_timepoint`, N5 — the one cross-tab timepoint the Heat Map slider / Distribution combo read/write, synced via `core/timepoint_sync.py`) | `core/plot_state.py` |
| `StatsState` | Statistics-tab comparison groups + the structured **pairwise** results (`pairwise`, N7 — fed to the Bar plot's significance brackets). The group-statistics result cache lives in core (`AppState.stats_cache`), not here. | `features/stats/state.py` |
| `ReviewImageState` / `PreviewState` | Review-image view transform + caches; preview image maps (`fluor` / `overlay` / `mask`) shared by the Segmentation review-image and Image Table | `features/image_browser/review_image/state.py` |
| `ImageViewState` | Cross-view image display shared by the image viewers. The per-channel display LUT (`lut_by_channel`), so a brightness tuned in Review Image is the default an *auto* Image Table row inherits (per-row edits override). The **Cell Browser** participates too: its `LUT ▾` popover edits the same `lut_by_channel` (blank = auto per crop), and a per-channel **display colour** (`tint_by_channel`, `get_tint`/`set_tint`) overrides the additive composite tint a channel is painted with (`worker.resolve_channel_tint` — override wins, else the fixed per-index palette) | `core/image_view_state.py` |
| `PlaygroundState` | Playground frame selection + chosen correction method/params, per-source LUT memory, frame-local binary threshold, QC size cleanup, and zoom/pan + caches | `features/image_browser/playground/state.py` |

`AppState` (`core/app_state.py`) is a plain aggregate of all of the above —
*not* a mega-dataclass with behaviour. `WellViewerApp` builds one as
`self._app_state`. The cross-cutting state objects live in `core/`; each
feature's local state lives next to its controller / renderer in the same
feature package.

**`EventBus` (`core/event_bus.py`).** A `QObject` carrying a small, capped
catalogue of exactly five cross-cutting signals — `channel_changed`,
`selection_changed`, `dataset_loaded`, `plot_options_changed`, and
`redraw_requested`. Signal payloads are the relevant *state object*, never
`app` (`redraw_requested` carries a scope name). The catalogue and its contract
are documented in the module docstring. The bus is **load-bearing**:
every signal has a real subscriber. Subscribers are
constructed and connected by `bus_wiring.py`'s `wire_event_bus`, called once
from `WellViewerApp.__init__`:

- `core/redraw_coordinator.py`'s `RedrawCoordinator` subscribes
  `channel_changed` and `plot_options_changed` (forwarded to the scope
  coalescer) and `dataset_loaded` (a full post-load `_redraw`).
- `SelectionRefreshCoordinator` (`core/selection_controller.py`) subscribes
  `selection_changed` and runs `apply_selection_refresh` — sidebar refresh
  plus a per-tab view refresh.

The emit *is* the trigger (no paired direct call alongside it). Two refreshes
deliberately stay off the bus: `_toggle_sem` (the SEM/SD toggle — its redraw
covers Distribution / Heat Map, which the coalescer does not) and the smFISH tab
resync after a dataset load (a tab-specific data resync, not a redraw). The
`tests/test_redraw_fanout.py` and `tests/test_selection_refresh.py` harnesses
pin these invariants.

**`WidgetRegistry` (`core/widget_registry.py`).** A name-keyed store for Qt
widget handles (figures, canvases, axes). `register("_line_fig", fig)`
stashes the handle by name *and* `setattr`s it onto the bound app, so
`app._line_fig` readers resolve via plain attribute lookup — no `__getattr__`
forwarder is needed, and the registry stays the canonical store. Its backing
dict is **shared with `AppRuntime`** (below), so a handle registered as
`_<feature>_<name>` is also reachable through `ctx.runtime.<feature>.<name>`.

**`AppRuntime` (`core/app_runtime.py`).** Per-feature namespaces for *live,
non-persisted* session objects that aren't typed state — worker bridges, decoded
image arrays, popup figures/canvases, transient toggle banks (SEM/FOV), and live
callback handles. `ctx.runtime.<feature>.<name>` reads/writes a plain
`name → object` **attribute-store** dict (keyed `_<feature>_<name>`) and holds
**no reference to the god-object** (A8 T6): the real app injects one store
*shared with `WidgetRegistry`* so the widget and runtime views resolve one
another's entries. These objects are never serialized — `persistence/` must not
import `app_runtime` (`tests/test_app_runtime.py`). (Legacy / pure-logic test
call sites that construct `AppRuntime(app)` get a back-compat `_AppAttrStore`
getattr/setattr view over that app instead of a standalone dict.)

The stamped-callback namespaces are also how two features notify each other
without either importing the other — the producer fires a **generically named**
slot and the consumer registers for it, so both stay one-line removable. The
`qc` namespace is the pattern at its cleanest: `qc.recompute_qc_all` fires
`ctx.runtime.qc.changed_cb` after rewriting `QC_Pass`, naming no listener; the
Cell Gating tab registers there to keep its inclusion readout current. Delete
either feature and the other degrades to a dead assignment or a `None` callback.
(A namespace's prefix is shared with the widget registry, so
`ctx.runtime.qc.gate_widgets` aliases the registered `_qc_gate_widgets` dict —
by design, not a collision.)

**`ViewerContext` (`viewer_context.py`).** A frozen dataclass — the single typed
handle tab builders and controllers receive **instead of** the `WellViewerApp`
god-object. It bundles `state` (typed `AppState`), `bus` (`EventBus`), `widgets`
(`WidgetRegistry`), and `runtime` (`AppRuntime`), plus a handful of **held
collaborator objects** that own what used to be god-object methods: `ctx.persistence`
(the persistence registry), `ctx.features` (the feature registry), `ctx.notify`
(status-bar + toast notifications), `ctx.figures` (Save-As dialog + export-style
panel), and `ctx.coordinator` (the master redraw + sidebar-map refresh). **There is
no `app` field** (A8 teardown, T6 — the `ctx.app` reference count was driven 293 → 0
), and after the A5 teardown there is **exactly one** frozen
named accessor left onto the window shell: `ctx.window` (the god-object *qua*
`QMainWindow`, for dialog parenting / `isinstance` guards / `findChildren` walks).
Everything else features once reached as a `ctx.<verb>()` method — `set_status`,
`toast`, `save_figure_dialog`, `open_export_style_panel`, `rebuild_all`,
`refresh_sidebar_map` — is now a method **on one of the held collaborators above**
(e.g. `ctx.notify.set_status(...)`, `ctx.figures.save(...)`,
`ctx.coordinator.rebuild_all()`), so the window is no longer a behaviour dependency,
only a widget. That single remaining accessor is the *entire* intentional surface
between features and the window shell — never a generic `ctx.app` escape hatch and
never a `__getattr__` passthrough. `WellViewerApp.__init__` builds `self._ctx` and
passes it to every tab builder and controller; feature code reads
`ctx.state.<domain>.<field>` / `ctx.bus` / `ctx.widgets` / `ctx.runtime`, the held
collaborators, and `ctx.window`, nothing else.

`tests/test_ctx_app_burndown.py` hard-bans any `ctx.app` (`CTX_APP_BUDGET = 0`);
`tests/test_ctx_service_accessors.py` freezes the accessor set (adding one is a
reviewed act); `tests/test_guardrails.py` holds `runtime_app.py` to ratcheting
line-count, `self._*` attribute-count and defensive-`getattr`-probe ceilings (it
can only ratchet down), plus the state-package import boundary above.

### 5.10 Split View (optional, one-line removable)

**What:** show two *different* top-level tabs side-by-side, both updating live
(selection / channel / plot-option / dataset changes refresh **both** panes).
Two instances of the *same* tab are out of scope (feature figures/state are
singletons). Entry is a top-bar **"Split view"** toggle (`columns-2` glyph) + a
right-pane dropdown; the left pane is whatever tab was active.

**Self-contained by design.** All active machinery lives in one module,
`core/split_view.py`, wired by a *single* call — `install_split_view(app)` in
`app_shell_view.build_ui`. **Comment out that one line and the feature is gone**
with no side effects: no toggle button is created, so `app._split_active` is
never set, the provider hooks below stay `None`, and every resolver collapses to
the ordinary single-tab behaviour. (Guard: `test_split_view.py::
test_not_installed_is_single_tab`.)

**How it works:**

- **Reparenting.** `enter_split(app, a, b)` force-builds both deferred bodies,
  then `NamedPageStack.detachPage`es the two top-level pages into
  `app._split_pane` (a hidden `QSplitter` sibling of `_notebook`, created by
  `install_split_view`) and hides the notebook. `exit_split` reverses it,
  restoring the original tab order via `reattachPage`.
- **Provider hooks (the decoupling seam).** `tab_controller` exposes three
  module globals — `split_focus_provider`, `split_visible_provider`,
  `split_nav_intercept` — all `None` until `install_split_view` registers them.
  `current_centre_tab` returns the **focused** pane's leaf (so the sidebar +
  per-tab dispatch follow the last-clicked pane); `visible_centre_tabs` /
  `is_tab_visible` return **both** panes' leaves. The live-refresh chokepoints
  already key off these (`fold_change_scopes.visible_scope_names`,
  `selection_controller._visible_tabs`, the barplot/lineplot `_is_tab_visible`
  self-guards), which is why both panes stay live — and why, uninstalled, they
  fan out to exactly one tab. `selection_controller.rebuild_all` likewise guards
  **each** plot redraw (Bar / Line / Distribution / Heat Map / Scatter) behind
  `_is_tab_visible`: a group edit on the Sample Definitions tab no longer
  tears down and redraws 3–5 off-screen figures — plus the from-scratch stats
  recompute that fed them — on every well click; an off-screen plot is re-rendered
  from current state by its `on_activate` hook the moment the user switches to it.
- **Focus + lifecycle.** A `_SplitFocusFilter` (installed on the QApplication on
  enter, removed on exit) makes a click in either pane the focused one. It
  **defers** the actual focus switch with `QTimer.singleShot(0, …)`: `focus_split`
  reparents the panes and re-runs the visible-tab refresh, so doing it inside
  `MouseButtonPress` delivery (e.g. a click that lands while Image Table
  "Generate" is recompositing the focused pane) would mutate the widget tree
  mid-dispatch and crash — running it on the next event-loop turn lets the click
  finish first. Split auto-exits on a Review/Analyze switch (connected to
  `app.modeChanged`), on rail navigation (`split_nav_intercept` in
  `_on_section_nav_changed`), and at the **start of a dataset load**
  (`load_controller.load_directory`) — the load restores view-state + redraws
  against the single-notebook layout, so leaving split first avoids driving
  detached pages (the reported open-dataset crash).

### 5.11 The Spatial feature

The **Spatial** tab (`well_viewer/features/spatial/`) is the Review-side
analysis of **per-cell physical positions in the well plane**. It's the
only feature that consumes the pipeline's always-on
`shape_centroid_row` / `shape_centroid_col` columns for anything analytical
— the Multivariate tab treats those centroids as ordinary shape features
in z-scored feature space, not as physical coordinates. Spatial is a
one-file-per-role package that mirrors Multivariate (see §5.3 recipe): a
`SpatialState` slice, a Qt-free `collect.py` walker, a `controller.py`
that owns state mutation + the compute→render pipeline, a `renderer.py`
that draws the six MVP visualisations, and a `tab_view.py` builder. Its
per-tab controls live in the top-of-canvas rows; the only right-dock
occupant is the shared **Properties (Export-Style) panel** — the
`tab_view` pre-allocates the dock (`make_plot_with_right_dock`, registered
`_spatial_export_dock`) and a Properties button toggles
`ctx.figures.open_style_panel("spatial")`, so the Spatial figure now styles
identically to every other plotting tab (`render_spatial` re-applies the
export-style prefs at the end of each recompute so edits survive it).

**Multi-well faceting.** The per-frame levels (Cell / Colony /
Intra-colony) now draw **one panel per selected well** — a faceted grid
capped at 12 panels (with a note when the selection exceeds the cap) —
rather than collapsing the plate selection to a single sorted-first
"active well". `SpatialState` grows a `frames` list (one payload per well)
and a `frames_truncated` overflow count alongside the existing
`last_result` (still the home of the single Summary matrix); `reset_result`
clears them. The **Intra-colony** level now *always* colours the map by
normalised radial position `r/R`, so toggling Colony↔Intra-colony always
re-colours (Cell/Colony keep metric / colony-id colouring).

**Where the math lives.** All numeric algorithms sit in
`well_viewer/domain/spatial_compute.py` — Qt-free, matplotlib-free,
numpy + scipy only (`scipy.spatial.cKDTree` / `ConvexHull` /
`scipy.cluster.hierarchy` — no new third-party dep). The compute layer
returns small dataclasses (`RipleyResult`, `ColonyLabels`,
`ColonyStats`, `IntraColonyGeometry`, `RadialProfile`,
`VarianceDecomposition`) which the renderer consumes directly. This is
the same split as `domain/multivariate_compute.py` and lets the pure-
Python pytest suite exercise every algorithm without a display.

**When it runs.** Everything is Review-side and on demand. Colony
detection is parameter-driven (method / ε / min-samples / boundary
percentile) so it can't be baked into the pipeline CSV — freezing the ε
would defeat the sweep the user is going to want to do. Per-cell
distance metrics are ~milliseconds via `cKDTree` even at ~10⁴
centroids, cheap enough to compute on tab activation. Ripley's L is a
visualisation choice (envelope size, radial grid), also Review-time. A
future pipeline hook for the deterministic-and-cheap
`spatial_nnd` / `spatial_knn_mean_d` / `spatial_local_density_k5`
columns is documented in the plan file but deliberately not shipped —
schema version bump not warranted yet.

**Scales of analysis.** Spatial classifies everything by **scale** —
sub-cellular, cell, colony, intra-colony — and the tab's Level
segmented control switches between three of them (sub-cellular is
served by the existing shape columns via the Metric picker rather than
a dedicated Level):

| Level | What each cell contributes | Vizs available |
|---|---|---|
| Cell | its centroid `(x, y)` in pixel space | Spatial map, NND histogram, Ripley L |
| Colony | membership in an auto-detected colony | Spatial map (colour by colony id), Colony size histogram, Per-colony distribution |
| Intra-colony | `d_centroid` / `d_edge` / `r/R` inside its colony | Spatial map (colour by r/R), Radial profile, Boundary vs Interior |

**Colony detection.** Colonies are computed, not manually annotated
(`spatial_compute.detect_colonies`). Two methods behind a Method
dropdown:

- **DBSCAN** (default). ε defaults to `eps_mult · median(NND)`, so the
  same multiplier gives roughly the same "grain" regardless of the
  well's density. Cells with fewer than `min_samples` neighbours in
  their ε-ball are marked noise (label `-1`, drawn grey on the map).
- **Single-linkage hierarchical**. Cut the dendrogram at ε; equivalent
  to DBSCAN with `min_samples=1`. Simpler mental model, but doesn't
  reject outliers.

Aggregates per colony are stacked in `ColonyStats`: cell count, convex-
hull area, packing density (`size / hull_area`), covariance-ellipse
eccentricity (`√(1 − λ_min / λ_max)` of the centroid covariance) and
orientation, and the mean centroid. `IntraColonyGeometry` adds per-cell
`d_centroid`, `d_edge` (min distance to the hull's edge segments —
positive for interior points), normalized `r/R`, and an `is_boundary`
flag (bottom-N-percentile of `d_edge` within each colony).

**Inter- vs intra-colony variance.** The Per-colony distribution viz
answers "how much of the signal's variability lives between colonies
vs inside each one?" by drawing one violin per colony (sorted by
median) and stamping the ANOVA-style variance decomposition on the
axes:

- `σ²_between = (1/N) Σ_c n_c · (μ_c − μ)²` — variance of colony
  means, weighted by colony size.
- `σ²_within = (1/N) Σ_c Σ_i (x_ci − μ_c)²` — pooled within-colony
  variance.
- `σ²_total = var(x)` — population variance; equals
  `σ²_between + σ²_within` by construction.
- `ICC = σ²_between / σ²_total` — the intraclass correlation
  coefficient. **1** means variability is almost entirely between
  colonies; **0** means colony identity carries no information about
  the signal.

Noise cells (label `-1`) and non-finite metric values are dropped from
the decomposition, so ICC always reports on the analysed population,
not the raw frame.

**How the compute + render pipeline fires.** The controller's single
entry point is `sp_recompute(ctx)`. Every setter (`sp_set_level`,
`sp_set_viz`, `sp_set_metric`, `sp_set_colony_eps_mult`, …) ends by
calling it. The pipeline:

1. Take the well set from `ctx.state.selection.selected_wells` (the
   per-frame levels now facet **every** selected well into `SpatialState.frames`,
   capped at 12; the Summary level still walks the whole selection into one
   matrix).
2. `collect_spatial_frame(ctx, well, …)` reads each well's DataFrame
   *directly from `ctx.state.dataset.cache`* (the tab is downstream of
   whatever plotting / image tab first loaded the well, so a
   cache-only lookup is correct) and returns
   `{points, metric, timepoint, well, n_cells}`.
3. Cell-level distance metrics (`nearest_neighbour_distance`) always
   compute — they're cheap and the Metric ▾ popover can gate them off.
4. Colony detection + colony stats + intra-colony geometry compute
   only when the current level *or* viz demands it (the
   Per-colony-distribution viz demands colonies even at Cell level).
5. Ripley's L, radial profiles, and the variance decomposition are
   viz-gated — they only run when the corresponding viz is active.
6. `render_spatial(ctx)` reads the cached payload off
   `SpatialState.last_result` and dispatches on `st.viz`. Every viz
   ends with `canvas.draw_idle()`.

The whole recompute stays under a few milliseconds on the ~1–10 k
centroids a single well/timepoint holds, so control changes repaint
live.

**How the tab satisfies the `ctx.app` burn-down ratchet.** The Spatial
package is the first Review feature written with **zero
`ctx.app` references** — every setter / lifecycle hook takes the
`ViewerContext` directly, widget handles resolve via `ctx.widgets`,
state via `ctx.state.feature_slice`, per-well DataFrames via the
dataset-state cache, and Copy/Save through the held collaborators
`ctx.notify.set_status` / `ctx.figures.save`. The registry
adapters in `features/registry.py` translate the legacy
`_activate_spatial(app)` / `_sel_spatial(app)` / `_channel_spatial(app)`
signatures into `sp_on_activate(app._ctx)` calls; that single
translation is the only place the two worlds meet.

**Tooltips are load-bearing.** Every control on the tab carries a hover
tooltip whose text mirrors the interpretation given in the README's
Spatial section — the module-level `_LEVEL_TOOLTIP`,
`_VIZ_TOOLTIPS`, and `_METHOD_TOOLTIPS` dictionaries in
`tab_view.py` are the single source of truth. `viz_cb.setItemData(idx,
tip, Qt.ToolTipRole)` puts a distinct tooltip on each dropdown option
so hovering `Per-colony distribution` explains the ICC without the
user having to open the docs.

**The Summary level and CSV export.** In addition to the per-frame Cell /
Colony / Intra-colony levels, the tab also exposes a **Summary** level
whose only viz — *Summary panel* — walks the *whole* well selection ×
every loaded timepoint and calls
`spatial_compute.summarise_frame(points, metric, colony_params)` on each
frame. Each call returns a `FrameSummary` dataclass of scalars: cell
count, colony count, median NND, median k-NN mean distance, median local
density, mean colony size, median hull area, median packing density, and
the between-vs-within ICC on the picked metric (`NaN` when no metric is
selected). `collect.collect_summary_matrix` stacks those into a
`(n_wells, n_timepoints)` object array; the renderer picks the chosen
`summary_metric_key` field and draws either a wells × timepoints imshow
heatmap or one line-per-well `matplotlib.pyplot.plot` — selectable via
the `summary_display` combo (`SUMMARY_DISPLAY_HEATMAP` /
`SUMMARY_DISPLAY_LINE`). Same colony-detection params as the per-frame
vizs, so the Summary matrix agrees with what the user sees when they
switch back to Cell / Colony / Intra-colony and scrub through
(well, tp) pairs. This is the only viz that consumes more than one
frame's worth of data.

**CSV export** lives in the top row alongside Copy / Save Figure and is
enabled only when Level = Summary. It calls
`controller.sp_export_summary_csv`, which flattens the matrix via
`collect.summary_matrix_to_csv_rows` — one dict per (well, timepoint)
with every `FrameSummary` field materialised as its own column — and
writes the result via a `QFileDialog.getSaveFileName` dialog. Output
format is *long*, not wide (row = one frame; columns include every
summary at once), so downstream pandas / Excel work isn't restricted
to whichever summary is currently plotted. `metric_col` (the ICC column
used at capture time) is echoed in every row so post-hoc filtering
can distinguish ICCs computed with different signals.

---

## 6. The Analyze side

### 6.1 `analyze_tab.py` / `analyze_tab_form.py`

The Qt form that builds the input panel (folder pickers, channel tokens,
top-hat radii, segmentation method, smFISH options, worker count) plus a
live-streaming log panel and a Run / Stop button. The form-building code was
extracted into `analyze_tab_form.py` (a mixin consumed by `AnalyzeTab`) during
the Code-Diet pass so `analyze_tab.py` stays under its line budget; the two are
edited together. The split follows one rule: code that *reads the form's
widgets* lives with the widgets, so `_collect_run_options`, the well-field
validators and the entry-boundary numeric parsers (`_safe_qc_float`,
`_safe_seg_float`, `_numeric_option_errors`) are all in the mixin, and the tab
keeps only the run/log/dashboard orchestration. Both files carry a line budget
(`test_monolith_budgets.py`). Verbose inline helper paragraphs are collapsed
behind hover `ⓘ` icons (`_info_icon` / `_section(info=…)`).

The optional sections fold by default; **Auto-threshold** is one of them, and it
owns every threshold-related control: a checkbox driving the pipeline's
`--no_auto_threshold` (which previously had no UI), a **Method** combo choosing
between Otsu and the control-well optimizer, and the two well fields that feed
`--pos_control_well` / `--neg_control_well`.

The method is an explicit selection rather than being inferred from whether the
well fields are filled, so switching back to Otsu does not mean clearing the
wells and losing them — the fields hide but keep their text, and
`build_pipeline_args` emits the flags only under the control method, so dormant
text cannot silently override the choice. On the pipeline side there is no
method flag: naming both wells *is* the method, which is also how
`_apply_pipeline_info` infers the combo back from a sidecar.

Under the control method the pair is validated together — both wells, each a
valid 96-well position, distinct, and present in the input folder — by
`_auto_threshold_validation_error`, which blocks the run the same way the
background-well check does. Under Otsu it does not read the fields at all.

The Run path:

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

**Single packed OME-TIFF input (`analyze_ome.py`).** Besides per-well
zips/folders and loose TIFs, the Analyze pane accepts one OME-TIFF holding every
timepoint, channel, Z-plane and stage position of a plate scan. This path is a
third mixin, `AnalyzeOmeMixin`, kept in `analyze_ome.py` (out of both monoliths,
which sit on their line budgets): it owns the collapsible **Single OME-TIFF
Input** form section (imaged well-grid dimensions, origin well, serpentine
direction, an optional explicit FOVs-per-well, and a per-channel role picker),
the `OME file…` picker, pre-run validation, and the run-directory resolver
`_resolve_run_dirs_for_runner`. The key idea is that the file is
**de-interleaved into ordinary loose TIFs before anything else runs**, so the
whole downstream stack — `WellPlateZipper` grouping, `process_microscopy`,
`pipeline_info.json`, the Review viewer — is untouched.

Channel roles are **explicit, not guessed by order**: after the file is read, the
role picker lists each OME channel name with a role dropdown (Segmentation /
Fluorescence / Phase-brightfield / smFISH / Ignore), defaulting to Fluorescence.
The picker *drives the existing Channel-Tokens widgets* (`_apply_ome_channel_roles`
→ `_nuclear_token_edit` + `_set_fluor_rows`), so everything that reads them
(`_collect_run_options`, `_phase_token`, `_segmentation_validation_errors`) is
unchanged; the manual fluorescent-channel list is hidden and the
segmentation-channel field is read-only while an OME file is active.

The resolver runs `services.input_resolution_service.explode_ome_if_present`
first (a no-op unless `opts['ome_input']`), which calls
`pipeline.ome_explode.explode_ome_tiff`. It reads the OME-XML for dimension
order and channel names, **max-projects Z**, maps each stage position to a
96-well label via `util.plate_grid` (serpentine order + the imaged grid, one
well × FOV per position; FOVs-per-well is entered explicitly and cross-checked,
or derived), and writes `<experiment>_<channel>_<well>_f<fov>_<timepoint>.tif`
into a sibling `in/` folder.

Two source structures are handled (`read_ome_layout` → `OmeLayout.single_image`),
because OME allows only X/Y/Z/C/T inside one `<Image>` — there is no position
dimension:

- **Multi-image** — several `<Image>` elements (one `tifffile` series each), one
  per stage position; each series' `T` axis is that position's timepoints.
- **Single-image packed** — one `<Image>` whose `T` axis packs *every* position
  (and any real timepoints) into a flat frame sequence. A **frame-order** choice
  (`ome_frame_order`: `single` / `pos_in_time` / `time_in_pos`) says how that
  sequence splits: `n_positions = wells × FOVs`, `n_timepoints = n_frames /
  n_positions`. Planes are read **one page at a time** (`_packed_plane` ravels
  the T/C/Z indices against the series' leading-axis sizes, honouring the file's
  `DimensionOrder`) because a packed plate array is routinely tens–hundreds of
  GB — far too large to `asarray` whole. The resolver then hands that `in/` to `resolve_input_output` exactly as
for any loose-TIF drop. Because the explode is client-side, remote runs receive
the loose TIFs and need no OME awareness; `pipeline/ome_explode.py` and
`util/plate_grid.py` are still listed in the remote manifest so it stays a
complete mirror of `pipeline/` + `util/` (`test_remote_manifest.py`).

### 6.2 `process_microscopy.py`

The pipeline. **Self-sufficient by design** — no import from `well_viewer/`,
`widgets/`, or the Qt UI (enforced by
`test_architecture_contracts.py::test_pipeline_is_self_sufficient`). The pipeline
must run in environments that don't have the GUI installed (CI workers, headless
boxes).

**Decomposition.** `process_microscopy.py` is the orchestrator + CLI; the heavy
cohesive clusters were extracted into the Qt-free **`pipeline/` package** (at
repo root, so the pipeline can import it without the GUI) and **re-exported** so
`pm.<name>` call sites (and the remote worker) are unchanged:
`pipeline.background` (background/illumination math), `pipeline.cellpose`
(Cellpose segmenters; Torch lazy), `pipeline.stardist` (StarDist segmenters +
per-worker model state behind `set_model`/`set_fov_label`; TensorFlow lazy), and
`pipeline.io` (atomic image/CSV/overlay writers). The two ML runtimes stay
behind lazy imports, so importing `process_microscopy` pulls in **neither TF nor
Torch** — they load only when a worker actually segments. Each `pipeline`
module is in the remote manifest (`test_remote_manifest.py`) and the file sizes
are ratcheted by `test_monolith_budgets.py`.

That is also why the auto-threshold estimator was inlined into
`process_microscopy.py` (`_estimate_thresholds_standalone`,
`_apply_thresholds_to_pipeline_info`, `_sample_cell_and_bg`,
`_pick_endpoint_timepoints`) — earlier versions imported the equivalent from
`well_viewer.auto_threshold`, which crashed the pass on hosts without the
viewer. The viewer's copy is still used by the GUI's "Auto-threshold"
button on the Cell Gating tab; the pipeline's copy is now the standalone
fallback.

**The end-of-run threshold pass** is `run_auto_threshold_pass`, one call at the
tail of `main()`, with three modes:

| condition | what it writes |
|---|---|
| `--no_auto_threshold` | nothing |
| no control wells | Otsu on a pooled per-cell / background-pixel distribution, per channel (`_estimate_thresholds_standalone`) |
| `--pos_control_well` **and** `--neg_control_well` | per channel, the threshold maximising the relative fraction-on gap between those two wells (`pipeline.threshold_optimize.estimate_thresholds_from_controls`) |

In both non-skip modes the segmentation channel — `--nuclear_token`, already
remapped onto `--phase_token` for the label-free methods by the time the pass
runs, plus `--cytoplasm_token` under seeded watershed — is written **0** and
excluded from the estimate. It defines cell boundaries rather than being
something to gate on, so any estimate for it is noise. Zero is not the same as
absent: absent means the viewer's 50.0 default, whereas 0 classifies every cell
with a positive intensity as ON, which is how the channel is neutralised.
`_apply_thresholds_to_pipeline_info` never overwrites a value already in the
sidecar, so all of this is a default the user can override — including the zero.

The control-well mode reads the per-well CSVs the run has just written, not the
`*_out.zip` images: those CSV values are exactly what the Cell Gating tab plots,
so a pipeline-chosen threshold lands where the user will later see it on the CDF.
`sample_cell_and_bg` is deliberately *not* reused here — it pools cell means with
matched background pixels, which is right for an Otsu split against background
and wrong for a two-well comparison, where background pixels land identically in
both wells and dilute the gap being maximised.

The objective is symmetric, so it cannot tell the two control wells apart; the
pass logs a warning when the positive control ends up with the *lower*
fraction-on, which is the only signal that the two wells were entered the wrong
way round.

Batched remote runs suppress the per-batch pass (partial-plate data would write
misleading defaults) and re-run it once locally over the full downloaded output
— `services/remote_runner._run_local_auto_threshold`, which rebuilds an args
object from the pipeline argv and calls the same `run_auto_threshold_pass` so
the two paths cannot drift.

Inputs / outputs / contract:

```
Input dir (one of; grouped per 96-well position upstream)
├── A01.zip / A02.zip / … H12.zip          ← per-well zip mode (preferred)
└── A01/ / A02/ / … H12/                    ← per-well folder mode
                                              (compressed to <well>.zip during the run)

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
`pipeline.shape_features` so the pipeline (emit) and the GUI (detect/label) can't
drift. On the Review side `data_loading.detect_shape_features` finds the columns
and the channel dropdown surfaces them as **sectioned virtual channels** (a
"Shape Features" group via `ui_helpers.set_combo_values_sectioned`), with the
metric combo collapsed and all-cells (`threshold=-1`) aggregation — they are
channel-independent, so they bypass the per-channel intensity gate.

**Quality-control stage.** Two optional QC steps run inside `process_image_group`.
**Flat-field correction** (`--flatfield {none,empty_well,gaussian,basic}`, via
`apply_flatfield` with a mean-1 field) runs *before* top-hat: `gaussian`
self-estimates per image, while `basic` (a **pure-numpy BaSiC** — median stack +
smooth, no jax) and `empty_well` use a per-channel field precomputed once in
`main()` by `build_flatfield_models`. **Debris removal** (`--qc_remove_debris` +
min/max area + min-solidity, via `filter_debris`) drops out-of-bounds label
regions, **reusing the same `regionprops` pass** as the shape features. The
settings are recorded as a `qc_options` block in `pipeline_info.json`
(`read_pipeline_info` defaults it to `{}` on legacy sidecars).

**Segmentation routines + parameters.** `--segmentation_method` selects the
segmenter in `process_image_group`'s dispatch; the Analyze "Channel Tokens"
section shows a **per-method parameter panel** (a `NamedPageStack` swapped by
`_refresh_segmentation_hints`) so only the chosen routine's controls appear. All
routines segment the **Segmentation channel** (`--nuclear_token`, which may be any
fluorescent or brightfield channel) unless noted:
- `stardist_nuclei` / `stardist_seeded_watershed_cell` — StarDist (needs a
  fluorescent stain); watershed adds a cytoplasm token + min-nucleus-area.
  **Tiling.** Large frames blow up StarDist's
  peak memory, so `pipeline.stardist` can tile the prediction:
  `--stardist_tile_px` (an Analyze-tab spinner with an ⓘ bubble, default `0` =
  **off** = predict the whole frame; also env-tunable) sets the **max pixels
  per tile side**, and `set_tile_px` (read once per worker in `_worker_init`,
  mirroring `set_model`) makes each worker split any frame larger than that
  into an `n_tiles` grid (a 4K frame → 2×2 ≈ ¼ the peak); small frames stay
  untiled (`n_tiles=None`) so nothing slows needlessly. `n_tiles` is logged per
  FOV. The tile-size math is pure integer logic covered by
  `tests/test_stardist_tiling.py`.
- `cellpose` — **general** Cellpose, not phase-locked. Exposes model
  (cyto3/cyto2/cyto/nuclei), diameter, `flow_threshold`, `cellprob_threshold`,
  and an optional second **nucleus channel** (`channels=[seg, nuc]`). Models are
  cached per type in `_CELLPOSE_MODELS`; `_worker_init` skips the StarDist/TF load.
- `classical_threshold` — Otsu + distance-transform watershed (no model), general.
- `cellpose_labelfree` — the **only** label-free method: a Cellpose `cyto3`
  brightfield preset that segments the channel marked **Phase**. The Phase
  channel is designated by a per-row checkbox (`analyze_tab._phase_token()`,
  mutually exclusive, not also smFISH) → `--phase_token`; `main()` remaps the
  phase token onto the nuclear slot (only for this method) so all per-FOV grouping
  is reused. Phase is a first-class role (`image_identity.Role.PHASE`, suffix
  `_phase`) recorded as `phase_token` in `pipeline_info.json`.

**Phase-channel correction passthrough.** A channel designated **Phase**
now skips *all* background correction (flat-field + subtraction) for every
well/method — brightfield/phase has no fluorescence background to remove, and
running the correction on it only injected artefacts. The token flows to the
workers as a new `--phase_token` arg threaded through `shared_kwargs`;
`_correct_image_group` takes a `phase_token` param and short-circuits that
channel.

**Worker-pool memory + failure accounting.** Two properties of how the
parent process supervises the per-well worker pool:
- **Don't hoard records (~34 GB leak).** `_run_well_worker_pool` used to
  `extend` every per-cell record of the whole run into one parent-side list only
  to `len()` it for the final summary — at scale that pinned gigabytes in the
  orchestrator. It now keeps a running integer `total_nuclei` and lets each
  well's records free as the loop advances, so the parent holds at most one
  well's worth transiently (per-well CSV writing stays worker-side).
  `tests/test_well_worker_pool.py` pins the count.
- **Per-FOV failure traces.** A failed FOV-group persists its full traceback to
  `<output_dir>/errors/<well>_<fov>_pid<N>.txt` (`_dump_fov_failure_trace`,
  mirroring `_dump_well_failure_trace`), and `classify_log_line` treats
  "failed" as ERROR so it surfaces in the GUI live log. Whole-well vs
  partial-FOV failures are accounted separately, with a per-well breakdown and
  PARTIAL warnings that point at `--incremental`. Transient short/zero NAS
  writes are shielded by `pipeline.io._safe_imwrite`'s retry-with-backoff, which
  also triages disk-full vs transient rather than always claiming disk-full.

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
| `pipeline_service.py` | `find_pipeline_script` / `build_pipeline_args` / `spawn_pipeline`. `build_pipeline_args` emits each flag only when present and non-zero (including `--stardist_tile_px` (>0 only) and `--phase_token`); it is decomposed into a small table-driven emitter to satisfy the complexity ratchet, behaviour unchanged (`tests/test_pipeline_arg_validation.py`). |
| `pipeline_runner.py` | Asynchronous subprocess + stdout-streaming wrapper used by Analyze. Hosts `ProgressTracker`, a stateless log-line parser that turns the pipeline's stdout into structured run events — coarse `("stage", name)`, per-well `("well_stage", (well, stage))` (`upload`/`active`/`process`/`threshold`/`download`), per-well FOV sub-progress `("well_fov", (well, done, total))`, and `("zipper_well", well)` — which drive the live `RunDashboard`. `download` fires only once **both** the output zip and the well's CSV(s) have landed (a server-side threshold or a zip-only fetch must not read as "downloaded"). |
| `remote_runner.py` | The remote-execution path: uploads the input zip-set to a worker host, runs the pipeline there, and the `Fetcher` pulls each well's `<well>_out.zip` + CSVs back. All rsync calls funnel through `_rsync_with_retry` (transient exit codes 10/11/12/30/35 retried on the `_RSYNC_RETRY_DELAYS = (2,4,8)`s schedule, slept in ≤0.25s chunks so the Stop button stays responsive; 11 is *error in file I/O*, what a fault on the local end of a transfer looks like). Every write the client itself makes on a user volume — staging copies off the input dir, `Fetcher._materialize`, the `pipeline_info.json` rewrite — goes through `util.retry_io.retry_io` for the same reason (§6.5). `_zip_is_complete` (`zipfile.is_zipfile`) guards the final fetch so a timed-out-but-materialised rsync isn't mistaken for a still-pending one — the bug where the last download "just sat there." **Which conda env the pipeline runs in** is `_conda_env_selector`'s answer, resolved once per runner from the host's `remote_python` and used by both the argv builder and the shell composer so they cannot disagree: left at its default the field is a *placeholder* and the env is resolved by name (`conda run -n allwell`), which is what makes a host with a pre-existing system conda work at all; set by the user to an `<env>/bin/python` it selects that prefix (`conda run -p <env>`). `_conda_env_guard` then verifies the env exists on the remote — inside the scheduler allocation when there is one, since the compute node is where it has to exist — and aborts with a `[remote]`-prefixed message naming the host, conda's base, the envs it can see, and the fix. Without it the run reached `conda run` and died on conda's bare `EnvironmentLocationNotFound`, which in a scrolling log is indistinguishable from an error the pipeline raised. A cache hit execs `true` and skips the check. **"Does the env exist" is conda's own question** — a prefix is an environment iff it holds `conda-meta/history` — and the shell that answers it, `CONDA_ENV_SHELL_HELPERS`, is shared with `scripts/_bootstrap_lib.py` so the runner's check and the bootstrap's create-or-skip decision cannot disagree. See §6.6 for why that mattered. **Scheduled compute (HPC lobby/login nodes):** when the host's `compute_launcher` is set (e.g. an `srun …` line), `_compose_remote_cmd` wraps *only* the pipeline command into a scheduler allocation (`srun … bash -lc '<conda>; exec python …'`) so the work lands on a compute node the cluster schedules while staging/cache/fetch/cleanup stay on the login node against the shared filesystem; the run records `$SLURM_JOB_ID` and `close_remote_job` (`scancel`) guarantees the allocation is torn down on completion / failure / cancel. Empty `compute_launcher` = direct-on-host execution (the default). The `remotes.toml` schema is the live contract in `services/remote_config.py`. |
| `remote_config.py` | Per-host remote definitions loaded from `~/.config/allwell/remotes.toml` (or `$ALLWELL_REMOTES`). Each `[remote.<name>]` entry is an SSH alias + `remote_python` / staging dirs / cache budget, plus the optional **`compute_launcher`** (the `srun …` scheduler line consumed by `remote_runner`) and **`jupyterhub_config_path`** (where to find `jupyterhub_config.py` on the remote). Hosts are picked + edited in `analyze_remote_dialog.py`, which can **Fetch** the cluster's discrete compute options by reading the remote's `jupyterhub_config.py` spawner `profile_list` over SSH (no JupyterHub webpage needed) — picking a profile fills in the whole scheduler command. |
| `input_resolution_service.py` | Validates the input folder shape (zip set vs flat) and drives the loose-TIF **grouping (zipper) phase** — `run_zipper` shells out to `WellPlateZipper.py`, which discovers every TIF with one `os.walk` and copies (or, with `--move-source-tifs`, moves) each into its per-well subfolder. `group_by_well` filters discovered entries through `os.path.isfile` (which follows symlinks) so a **broken symlink** — a link whose target is on an unmounted/moved volume, still listed by `os.walk` among `files` because it is a non-directory entry — is skipped at discovery with a per-file warning rather than being handed to `folder_well`'s symlink-following `shutil.copy2`, where opening the missing target raised a bare `FileNotFoundError` that crashed the subprocess and failed the whole plate. This matches the rest of the pipeline's discovery, which already treats a broken link as absent (`process_microscopy.organize_loose_tifs_into_well_folders` and `find_image_groups` both gate on `Path.is_file()` / an existence check). |
| `env.py` | `validate_env` — a pure startup check of the `ALLWELL_*` overrides (`ALLWELL_SCRATCH_DIR` / `ALLWELL_REMOTES` / `ALLWELL_SEG_DEBUG`), returning warnings for any that are set but unusable so a mistyped path is not a silent fallback. |
| `ssh_key_setup.py` | Ed25519 keypair generation at `~/.ssh/allwell_<alias>` + the `ssh-copy-id` walkthrough behind the remote-host wizard. |
| `subprocess_kill.py` | Kills a spawned pipeline's whole process group / session (what Stop uses). |

These modules deliberately have no Qt imports beyond what the Analyze tab
itself uses — keeping them potentially reusable for a CLI driver.

### 6.5 Scratch, staging, and where each write lands

A run touches two classes of storage, and keeping them apart is what makes it
survive an unreliable output volume.

**Scratch (bulk, transient).** `pipeline.io.scratch_base_for(output_dir)` decides
where per-well working files go: the extracted input TIFs, every intermediate
image, and the per-FOV checkpoints. Resolution order is `$ALLWELL_SCRATCH_DIR`
(set by `--scratch_dir` or the Analyze tab's *Scratch disk* field) →
**`~/.allwell/scratch`** (the default) → `output_dir` (last resort, warned).

The default used to be `output_dir`, and the Analyze field auto-seeded
`<input>/Temp`, so *both* put the whole run's bulk I/O on user-chosen volumes.
That is the bug behind **"many wells fail with `OSError: [Errno 9] Bad file
descriptor` on local runs but never on Slurm"**: each FOV-group writes ~6 images,
each staged tmp → fsync → rename (three filesystem operations apiece), times
every FOV × timepoint × well, times N worker processes — all funnelled through
one SMB session when the volume is a NAS. macOS's `smbfs` does not re-open
server-side handles across a session reconnect, so every descriptor opened
before the drop returns `EBADF`. Slurm never sees it: inputs are rsynced to the
cluster and the pipeline runs against cluster-local storage. (NFS would give
`ESTALE`, so the errno identifies the protocol.) The scratch base is a dedicated
subdirectory, never `~` itself, because `_sweep_stale_scratch_dirs` *deletes*
what its `_tmp_*` glob finds.

Both modes honour it. Folder mode used to pin its staging to `output_dir` so the
closing commit could be a same-filesystem `rename`; `pipeline.io._commit_staging_dir`
now falls back to a copy on `EXDEV`, so the staging dir is free to live anywhere.
`_sweep_stale_scratch_dirs_all` sweeps the scratch base *and* `output_dir` (when
they differ) so staging left by pre-change runs is still reclaimed.

**Results (small, durable).** Only two writes per well must land on `output_dir`:
the per-well CSV and `<well>_out.zip`. Both carry the same retry-with-backoff
shield `_safe_imwrite` has always had, via `pipeline.io.retry_write` — they stage
to `__aw_tmp_` and commit with `os.replace`, so re-running is safe, and a
transient fault costs a retry instead of the whole well.
`_AtomicTextHandle.__exit__` guards its `flush()`/`close()` and aborts the commit
rather than renaming a short file over a good CSV.

**One retry shield, both sides of the app.** The loop itself is
`util.retry_io.retry_io`: run the operation, and on an `OSError` clean up the
staging file, back off and try again, up to five attempts. It lives in `util/`
because both halves of All-Well need it and neither can import the other's —
`pipeline.io` wraps it as `retry_write` (keeping the pipeline's own wording,
which names `--workers` and `--incremental`), and the `services/` remote client
calls it directly, where importing `pipeline.io` would drag numpy/tifffile into
a module that must stay imaging-stack-free. Two behaviours are worth knowing:

- **An error retrying cannot fix is re-raised unchanged, immediately.** A
  missing path, wrong permissions, a full volume (`_PERMANENT_ERRNOS`) fail on
  the first attempt, so a caller that treats a missing source as an ordinary
  `OSError` stays responsive and the real cause isn't buried under a retry
  story.
- **The exhausted-budget message names the storage.** `volume_hint` maps the
  errno to the protocol behind it — `EBADF` to macOS SMB, `ESTALE` to NFS,
  `EIO`/`ECONNRESET`/`ETIMEDOUT` to a link that dropped — and the caller adds
  its own advice tail. The user gets "this volume is a network mount and it
  dropped" instead of `[Errno 9]`.

**A remote run does the same I/O on the same volumes.** The pipeline executes on
the cluster, but the *client* still reads the whole plate off the user's input
volume and writes every fetched well back onto the output volume, so the same
shield covers each of those crossings:

| Where | What it protects |
|---|---|
| `remote_runner._copy_into_staging` | Copying each `<WELL>.zip` off the input volume into the upload staging dir — a whole plate, back to back, and an unguarded fault here failed the run before a byte was uploaded. |
| `remote_runner._zip_well_folder` | Packing a leftover well folder read off the input volume. |
| `input_resolution_service` | The mixed-input consolidation and the loose-image append, both of which rewrite archives *on* the input volume. |
| `Fetcher._materialize` | Publishing `<well>_out.zip` into the output dir — the remote run's equivalent of the pipeline's out-zip write. |
| `refresh_pipeline_info_available_from_outputs` | The `pipeline_info.json` rewrite, so one blip can't cost Review its timepoint/FOV lists. |

The per-well CSV pull is rsync's write rather than Python's, so it is covered by
the rsync retry policy instead: `_RSYNC_RETRY_CODES` includes **11** (*error in
file I/O*), which is what a destination-side `EBADF` surfaces as. Permission-class
failures (23, 3) still fail immediately. `Fetcher._rsync_glob` returns
`(listed, pulled)` so the two outcomes are reported apart — "the remote produced
no CSVs" and "the remote has them and the local write failed" used to print the
same line, which sent every diagnosis at the wrong machine.

**Well archives are published, not written in place.** Every per-well archive —
the `in/<WELL>.zip` inputs and the `out/<well>_out.zip` outputs — is built
through `util.zip_write.atomic_zip`, the single writer shared by
`process_microscopy.compress_images_to_zip`, `pipeline.io.merge_images_into_zip`,
`input_resolution_service` (the mixed-input consolidation and the loose-image
append) and `remote_runner` (upload staging and the two upload-filter rewrites).
It stages at `__aw_tmp_<name>.pid<N>`, fsyncs, re-reads the staged file's central
directory to confirm it holds exactly the members that were written, and only
then `os.replace`s it into position and fsyncs the parent directory. Two
invariants come out of that, and both matter because users open these archives
by hand as often as All-Well opens them:

- *A published archive is readable.* A run killed mid-write — or a share that
  drops — leaves the staging file, never a truncated `<WELL>.zip`.
- *A published archive has members.* A zero-member zip is structurally valid, so
  Python and `unzip` read it as an empty archive, but macOS Archive Utility
  refuses to expand it ("*it is in an unsupported format*") and the pipeline
  reads it as a well whose images vanished. `atomic_zip` raises `ZipWriteError`
  rather than publish one, so callers with nothing to pack must not create the
  file: a well folder with no TIFs is left alone by the consolidation step, and
  a well that produced no output images gets no `<well>_out.zip`.

The reading side stays tolerant — a `<WELL>.zip` that won't open is skipped by
the samplers and surfaces as a failed well from the worker pool — but nothing
All-Well writes should ever land in that state.

**Remote is pinned, not defaulted.** `remote_runner._build_remote_argv` emits an
explicit `--scratch_dir=<remote_output>` and drops any local `--scratch_dir`, so
Slurm runs keep their pre-change behaviour — a cluster home is typically a
small-quota NFS share, and the remote output dir already lives in the host's
configured staging area.

The run logs its resolved scratch base on the `Scratch  :` line; if that ever
reads back as the output dir on a network mount, that is the failure
configuration.

### 6.6 The remote conda environment, and one definition of "it exists"

A remote run needs exactly one thing installed on the host: a conda environment
holding the pipeline's dependencies. Two pieces of code care whether it is
there — `remote_runner`'s pre-launch guard, and `scripts/_bootstrap_lib.py`,
which creates it — and they answer with the same shell,
`remote_runner.CONDA_ENV_SHELL_HELPERS` (`aw_env_prefix` / `aw_env_is_valid` /
`aw_env_stale_dir`). That sharing is the point of the module-level constant:
when the two disagreed, the disagreement had no way out.

**Validity is conda's own test.** A prefix is an environment iff it holds
`conda-meta/history`. The bootstrap used to ask a different question — "is the
name in `conda env list`" — and the gap between the two is a real state on a
cluster: a `conda create` that dies part-way (a full home-directory quota is the
usual cause) leaves a directory at the prefix with no `conda-meta/history`.
conda will neither run in that directory nor create over it, so every run failed
with `EnvironmentLocationNotFound: not a conda environment: <prefix>` *and*
every re-bootstrap failed behind it with `prefix already exists`. Nothing the
user could reach from the app repaired it.

Both sides now detect it, and **neither deletes it**. All-Well does not remove
data from a user's cluster account as a side effect of an action they asked for
something else with — the contents of that directory are unreadable to us, and a
wrong guess about which directory is disposable is not recoverable. What the two
sides owe the user is the diagnosis and the exact command:

- The **guard** distinguishes "no environment" from "a directory that is not an
  environment", names the directory, names the likely cause (quota), and prints
  the `rm -rf <prefix>` line to run. A run is not the time to modify the host.
- The **bootstrap** stops before the `conda create` that would fail, with the
  same `ACTION REQUIRED` note. Continuing would only surface conda's `prefix
  already exists`, which states the problem without stating the remedy.

Deletion happens only where the user asked for deletion: `--force`, whose entire
documented purpose is to clear a suspect install. Even there each removal is
echoed before it happens, and a prefix that does not match `*/envs/<name>` is
refused rather than removed, so an unexpected path from conda cannot turn the
flag into an arbitrary `rm -rf`.

**Where the env actually lives.** `conda create -n allwell` lands in the first
writable envs dir, which on a host with a pre-existing system conda (an HPC
module, `/opt/conda`) is `~/.conda/envs/allwell` and not under `~/miniforge3`.
`--force` resolves the prefix through conda before removing it; removing
`~/miniforge3` alone left the env untouched, which made the flag's own promise
("use when the remote env is suspected corrupt") false on exactly the hosts
most likely to need it.

The scripts are built by `env_setup_script()` / `force_clean_script()` rather
than inline in the ssh callers, so `tests/test_bootstrap_env_repair.py` can run
them against a stub conda and a scratch `HOME` — the create-or-stop decision is
the part that strands users when it is wrong, and an ssh call hides it. The stub
refuses `conda create` over an existing prefix the way conda does; without that,
tests of this behaviour pass against the broken script too.

---

## 6b. The Datasets side

The Datasets pane (`datasets_tab.py`) is the third top-level mode. Structurally
it mirrors `AnalyzeTab`: a self-contained `QWidget` whose left `_left_sidebar` is
a `RailNav` page navigator (no well picker) over a `NamedPageStack`, with one
page per feature. Features are declared in one local tuple (`_FEATURES`) — a
`_Feature(name, icon, build)` per entry — so adding a Datasets feature is a
one-line change, without pulling in Review's well/selection-scoped
`FeatureRegistry`/`ViewerContext` machinery. The rail's `currentChanged` drives
`NamedPageStack.setCurrentByName`.

The one feature today is **Concatenate datasets** (`datasets/concatenate/`),
which appends one dataset's timepoints after another's. Two datasets of the same
plate, imaged in separate sessions, are stitched into one longer timelapse:
dataset 2's timepoints are relabelled to continue after dataset 1's last
timepoint, and the union of both datasets' wells is written to a fresh
destination folder (both originals untouched).

The package splits along the standard suffix contract, and deliberately keeps
everything that isn't Qt out of the two `QThread`/view modules so the whole merge
is unit-testable without the app (`tests/test_concatenate_compute.py`):

| Module | Role |
|--------|------|
| `concatenate_compute.py` | Pure planner. Reads + validates both `pipeline_info.json` (refuses on differing schema/separator/indices/nuclear-token/segmentation-method, and on dataset 1 and dataset 2 being the same folder), detects dataset 1's timepoint label format, computes the hour offset, enumerates wells, and returns a `MergePlan`. Also owns the structural filename-rewrite gate (`rewrite_member_name`) and the in-progress-marker restart check. |
| `apply.py` | Qt-free executor. Given a `MergePlan`, copies dataset 1's per-well inputs/CSVs/QC-zips as-is, re-packs dataset 2's with every timepoint-derived value shifted, stitches shared wells (CSV columns unioned like `pipeline/io.merge_records_into_csv`), carries over `cell_overrides.json`, verifies the result, and writes the merged sidecar. `write_zip` writes at `compresslevel=1` — a pure container-level merge of already-dense microscopy image data gains little from deeper DEFLATE effort, so the default level 6 was pure CPU waste — and tracks which member names collided and were dropped rather than silently discarding them; a nonzero drop count marks that well `"warned"` and fails `_verify` outright, since a name that "looks fine" in the merged archive can be the wrong dataset's bytes under it (see Verification below). Progress via a `log` callback and a `progress(well, stage)` callback (`"merging"` → `"merged"`/`"warned"`) that drives the live dashboard. |
| `worker.py` | Thin `ConcatenateWorker(QThread)`; runs planning + `execute_plan` off the UI thread, marshalling progress/result/errors through queued signals (`logLine`, `wellsPlanned`, `wellStage`, `succeeded`, `failed`), and forwards `isInterruptionRequested` as a cancellation check. |
| `view.py` | `ConcatenatePage(QWidget)` — a one-line equation row (dataset 1 + dataset 2 = destination; compact `_FolderField`s showing a basename with the full path on hover, each with its own inline validity hint) above a `ConcatDashboard`, with the raw text log demoted to a collapsible "Show raw log" checkbox (mirrors `analyze_tab_form.py`'s log panel). Emits `runRequested` / `openInReviewRequested`. |
| `controller.py` | `ConcatenateController` — wires the page to the worker, drives the dashboard's per-well and per-run state at each signal, distinguishes a cancelled run from a failed one, and confirms before resuming into a destination with leftovers from an interrupted attempt. |
| `dashboard.py` | `ConcatDashboard`, built on `widgets/plate_progress.py::PlateProgress` (shared with Analyze's `RunDashboard` — see §7). Two independent state axes: **per well** (`pending`/`merging`/`merged`/`warned` — image data survived on one side with no matching measurements CSV) and **per run**, a phase rail (Validate → Merge → Verify → Finalize) with a terminal Done/Failed/Cancelled outcome painted on the headline. The per-run axis exists because `apply._verify` and the cell-override carry-over run *after* every well already shows green — without it, a verification failure would leave the plate looking like a complete success. |

**A measurement row's timepoint is carried by three values, not one**, and a
zip member's timepoint lives in its filename — a fourth. The `timepoint` CSV
column is a label string; `timepoint_hours` is the numeric column every
plotting/grouping code path in `well_viewer` actually reads
(`dataset_state.rebuild_timepoints`, `data_loading.get_all_timepoints`, …); and
`filename` names the image the row was measured from, which `well_viewer`'s
Cell Browser and Scatter tabs use to look up that image. All four are kept in
lock-step for dataset-2 rows and members — a merge that touches only the label
string leaves the numeric column pointing at dataset 2's original,
pre-concatenation hours, so its rows would plot underneath dataset 1's instead
of after them and its images would fail to look up by the stale filename. To
avoid a fifth desync, new labels are never chosen independently of the numeric
value: `render_timepoint` produces the label and `parse_hours` on that same
label produces `timepoint_hours`, so the two can't disagree.

**The timepoint rule.** Timepoints are hour-valued labels read through
`util.timepoint.parse_hours`. Dataset 2's first timepoint is relabelled to
`ds1_last + ds1_interval`, where `ds1_interval` is the median gap between dataset
1's sorted unique timepoints; every other dataset-2 timepoint keeps its relative
spacing (one additive hour offset). `detect_timepoint_format` infers dataset 1's
own label convention (compound `DDdHHhMMm`, `Xh`, `Xd`, `Xmin`, or a bare number)
from its own labels and `render_timepoint` renders every new dataset-2 label in
that same convention — dataset 1's own labels are never rewritten. An
unrecognised convention (an ordinal format like `T01`) falls back to plain `Xh`
with a status-window note. A single-timepoint dataset 1 (no interval) or a
non-time timepoint label is refused with a user-facing message.

**The filename-rewrite gate.** Positionally rewriting a split-on-separator
filename field is unsafe on its own: `parse_hours` reads a plain digit-ending
token as an hour value (`parse_hours("F001") == 1.0`), so a rewrite applied to
the wrong field would silently succeed rather than fail loudly.
`rewrite_member_name` rewrites a zip member's timepoint field only when the name
is structurally conformant to the schema — a recognised image extension,
exactly as many separator-delimited fields as the schema declares once any role
suffix (`_labels`, `_overlay`, `_tophat_<chan>`, `_smfish_<chan>`, …) is
stripped via `well_viewer.data.image_identity.parse_image_filename` /
`build_image_filename`, and a timepoint field that parses to a finite hour
value. A name that fails any check is copied unchanged rather than guessed at,
and the merge reports how many members that happened to.

**Restart detection.** `execute_plan` isn't incremental — every well is
reconstructed fresh from the two source datasets on each call — so a re-run
into the same destination only needs to know whether it's safe to clear what's
there first. `execute_plan` writes
`concatenate_compute.IN_PROGRESS_MARKER` (`.aw_concat_in_progress`) as its
*first* action and removes it only after `pipeline_info.json` is written and
verified — that marker, not the mere absence of a finished sidecar, is the sole
signal that content under `in`/`out` came from an interrupted attempt.
`destination_is_complete_dataset` (sidecar present) makes `build_merge_plan`
refuse outright — the folder holds a finished dataset, whether from a completed
prior concatenation or an unrelated dataset the user pointed at by mistake.
`destination_has_marker` without a sidecar means the interrupted-attempt case;
`MergePlan.dest_is_restart` carries that verdict to `execute_plan`, which clears
the destination's `in`/`out` folders before rebuilding — but only after the
controller has confirmed with the user, since destination content the marker
*doesn't* explain (e.g. a plate that's been zipped but never analyzed) is
refused rather than assumed to be safe to delete.

**Verification.** Before writing the sidecar, `apply._verify` first checks
whether any zip member collided by name during the merge and was dropped by
`write_zip` — a dataset-2 member whose filename fails the structural
rewrite gate (see above) keeps its original, unshifted name, and if that
name coincides with a dataset-1 member (most easily reproduced by pointing
both datasets at the same folder), the merge would otherwise keep only one
side's bytes under a name that still "looks fine" in the archive, with
nothing else able to tell the difference — a dataset-2 row's `filename`
check below only confirms the name *exists* somewhere, not whose bytes are
under it. Any dropped member fails verification immediately, naming the
well and file. It then checks that the merge actually wrote *any*
measurement rows at all — a well's images (`in/<WELL>.zip`, `<WELL>_out.zip`)
existing without a matching `measurements_<WELL>.csv` is individually
legitimate (that well may have segmented zero cells at every timepoint), but
if it's true for *every* well in *both* datasets — most often because
`out_csvs()`'s well-token matching failed to recognise a real CSV's name
rather than because every well is genuinely empty — the old code had nothing
left to check and let the merge report success over a destination with
copied images and zero rows of data. Per well, it further checks that every
row's `timepoint` and `timepoint_hours` agree (via
`well_viewer.domain.tp_match.tp_close`), that dataset-2's shifted hours never
collide with or fall before dataset 1's, that every dataset-2 row's `filename`
is actually present in the merged `in/<WELL>.zip`, and that every distinct hour
across the merged CSVs is listed in the merged `available_timepoints`. Any
image-without-CSV well/dataset pair is also named in the status window as a
note (not necessarily fatal — see above) so it's visible instead of silently
dropped. A verification failure raises before the sidecar is written, so a
broken merge never leaves a destination that looks finished.

On success the page offers to open the merged dataset in Review, routed through
`DatasetsTab._open_in_review → AllWellApp._open_dataset_in_review`, which loads
the destination root into Review and switches to it — the same path
`_on_analyze_pipeline_complete` uses.

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
| `WellPlateSelector` | The 8×12 plate selector used by the sidebar and the sample-definitions editor. Supports per-well colour overrides, drag-to-select, and drag mime types for the heatmap layout (a drag source arms on press and applies the ordinary click on release, so clicking still selects — see §9.2). A non-interactive "lit" mode (`setColoredWellsLit`) paints colour-override wells with the raised/lit style instead of the depressed one, plus a `setWellMarker` glyph layer — both used by the run dashboard's live plate map. Per-well rank colours are now pushed on **every** tab (not just the plotting tabs), so a given well reads as the same colour everywhere in the app. |
| `PlateProgress` (`widgets/plate_progress.py`) | The shared chrome behind both live run dashboards: a header (title/status/eta/collapse toggle), a headline label, one overall completion bar, and a 3-column body — a subclass-owned left "rail" slot, the plate map (+ optional per-well sub-progress rails), and a legend. `RunDashboard` and `ConcatDashboard` each subclass it, building their own rail content and well-colour rules into the shared layout so the two can't visually drift apart. |
| `RunDashboard` | A thin `PlateProgress` subclass — the live, visual pipeline-run view shown above the Analyze log: a left stage rail (`group → upload → process → threshold → download`, the upload/download rungs only for remote runs) plus a centre plate map that colours each well by its furthest-reached stage and overlays a gear glyph on the actively-computing well with its `FOV done/total` sub-progress. Driven entirely by `pipeline_runner.ProgressTracker` events; tracks each stage as an independent well-set (so a server-side `threshold` can't make `download` read N/N before anything is fetched) and goes blue=computed-on-server vs green=downloaded-locally on remote runs. The raw colour log stays underneath, collapsible. |
| `NamedPageStack` (in `core/centre_view.py`, not `widgets/` — but morally a widget) | A `QStackedWidget` keyed by page name; supports `detachPage`/`reattachPage` (Split View). |
| `RailNav` | The left-rail section navigator. |
| `SegmentedControl`, `ChipGroup`, `Stepper`, `StyledSlider`, `ToggleSwitch` | Form controls; each exposes `bindingAdapter()` returning `(getter, setter, change_signal)` for the Export Style sidebar's binding loop. |
| `SavedSelectionsList` | The rich saved-selection list with composition (drag-to-reorder, per-group flat well editing, recolour popovers). A group is one condition — its wells are its implicit replicate set, so there are no replicate sub-partitions / solo strips. |
| `LutSelector`, `GradientStrip`, `ColorPickerPopover` | The colour-map picker + its preview strip + the freeform colour-picker popover. |
| `lut_cmaps.lut_cmap` | Qt-free resolver that maps a LUT name to a matplotlib colormap: single-hue names (Reds/Greens/Blues/Purples/Oranges) become a **black→colour** `LinearSegmentedColormap` (so a dark background reads as "off"), Greys stays black→white, everything else is the builtin (`_r`-aware). Routed through `ui_helpers.make_fluor_thumb` / `lut_colormap_to_rgb` and `LutSelector._cmap_stops`, so thumbnails, the Image Table compose/composite/export paths, Playground, and the picker strip all flip together. |
| `Popover`, `Drawer`, `Toast`, `EmptyState` | Generic overlay primitives. `Drawer` slides in from `edge="right"` (default — the Help drawer) or `edge="bottom"` (a full-width panel sized by `height_hint`/`height_fraction` — the Application Log drawer, whose long lines the narrow right panel clipped). |
| `FlowLayout` | A `QLayout` (not a widget) that reflows its children onto multiple lines instead of clipping — the standard Qt Flow Layout adapted to PySide6. Control rows that carry more widgets than fit at a narrow width (the plotting / image-review "TabCtrl" rows) use it so **Split View** halving the pane grows the row taller rather than hiding controls off the right edge. Four rules go with it. (1) A flow row skips hidden widgets when it places items, so *which* widgets are visible is an input to the layout, not merely a blanked slot — `_FlowItemVisibility` watches show/hide on every managed widget and invalidates the layout, without which a row that swaps one option group for another (the Multivariate tab's per-mode row) keeps the positions computed for the previous visible set and paints the newly-shown group on top of them. (2) A label and the control it names must go in as one `flow_group` item, or the row can wrap between them. (3) A host whose row may wrap needs `sync_flow_height`, since a stacked page sizes the row from its one-line minimum and clips the second line otherwise. (4) Spacing is fixed — 14px horizontal, `theme.Spacing.xs` vertical — **not** derived from the platform style, and no call site overrides it. Deriving it asks `pixelMetric(PM_Layout*Spacing)`, which Qt documents as returning -1 on styles that implement the size-class-aware `layoutSpacing()` instead. Measured, the two styles are exact opposites: QMacStyle reports `pixelMetric = -1, layoutSpacing = 14`, Fusion reports `pixelMetric = 6, layoutSpacing = -1`. The layout clamps with `max(0, …)`, so on macOS every row that relied on the derived value packed its controls edge to edge, while CI (on Fusion) saw 6 and looked fine. The 14 is QMacStyle's own button-row spacing, adopted app-wide because it is the one number measured against a real control row — it is deliberately off the `theme.Spacing` scale. Passing `-1` still derives, but now asks both APIs and falls back to the defaults rather than to nothing. |
| `MplToolbar` | The slim toolbar shown under every matplotlib figure (home / pan / zoom / save + coords readout). |
| `WrapCheckBox` | A checkbox whose label word-wraps, for the Analyze form's long-labelled options. A plain `QCheckBox` reports its full text width as its minimum, which pins the whole scroll-area sidebar that wide; this composites a text-less indicator with a wrapped `QLabel`. Because the label wraps, the composite's height depends on its width — and a `QWidget` inherits neither its child's `heightForWidth` nor a size policy advertising one, so it implements both. Without them a parent column sizes the row from a single line and a label that wraps to two spills over whatever sits below it. The indicator↔label gap is the app-wide checkbox `spacing` token (`theme.Spacing.sm`), not a hand-picked constant — the composite draws its own label, so a smaller gap read tighter to the box than every native checkbox beside it, and with no Fusion pin the app renders under the platform style (native QMacStyle on macOS, Fusion only on CI), where that shortfall put the first word on top of the indicator. Same lesson as `FlowLayout`'s spacing rule: a control gap comes from the token scale, never from an off-scale constant or the platform style. |
| `BrandTile`, `IconButton`, `StatusDot`, `KbdHint`, `SelectionChip` | Misc small bits. |

If you find yourself building something that isn't All-Well-specific, write
it as a new `widgets/<x>.py` module. Each widget gets a `__main__` block
that opens a small demo window so you can iterate visually with
`python widgets/foo.py`.

---

## 8. Theming

The app has two complementary theme surfaces:

1. **`theme.py`** (repo root) — Python-side design tokens for the v2
   redesign. `Colors`, `Typography`, `Spacing`, `Radii`, `Metrics` (control
   padding — themeable for density), plus a `qss()` builder that produces the
   full application stylesheet. The `widgets/` package and most of the app shell
   read from `theme.Colors.*`; the default (`blue`) palette is the class
   values here. This is the single source of the app chrome QSS. `theme.py`
   *also* holds the **theme registry** (`THEMES` / `apply_theme` /
   `select_theme_from_env`) — see "Selecting a theme" below.
2. **`ui/theme/`** — a legacy remnant, down to exactly two colours with no
   `theme.Colors` equivalent yet: `CLR_DANGER` and `CLR_SUCCESS_DARK`, read via
   `ui.theme.get_color(name)` by batch-export's progress label (their hex
   doesn't match `theme.Colors.danger`/`.success` closely enough to be a safe
   direct swap). Everything else this module used to carry — surfaces,
   borders, text, accent, warn, the well palette, the Publication-mode plot
   palette — has migrated to `theme.Colors` / `theme.CPub` /
   `theme.WELL_COLORS_TUPLE` directly. If a future change gives the remaining
   two tokens a `theme.Colors` home too, `ui/theme/` can be deleted outright.

`ui/theme/` is colour tokens only: it holds `styles.py` and nothing else — no
stylesheet driver, no theme-manager class, no per-theme `.qss` files. The theme
registry lives in `theme.py` (below), and `theme.qss()` is the one stylesheet
builder.

### Selecting a theme

A **theme** is a bundle of token overrides applied onto the token classes
(`Colors` / `Metrics` / `Radii`) *before the UI is built*. Because `qss()` reads
those classes at call time and every `widgets/*` builder reads `theme.Colors.*`
at construction, applying a theme up-front re-skins the whole app with **no
per-widget rewiring**.

- `theme.THEMES` maps a name → sparse override bundle. Built-ins:
  `blue` (the default; the class values are its palette) and
  `grey` (a **colour-only** variant — neutral charcoal-grey chrome, azure
  accent, near-black plot canvas). The version pill reads the `Colors.brand` /
  `Colors.brand_fg` tokens and is transparent with white text in both themes.
  Both share the flat, tight control feel: the `Radii`/`Metrics` defaults
  (small radii + crisp `control_border` + compact padding) are app-wide, so the
  `grey` bundle overrides only `Colors`. (Its look was designed against
  real Nikon NIS-Elements AR screenshots.)
- `theme.apply_theme(name)` resets every themeable attribute to its captured
  default, applies the bundle, and fires post-apply hooks
  (`register_theme_hook`). `select_theme_from_env()` reads the **`ALLWELL_THEME`**
  env var (falling back to `blue` on unset/unknown). `allwell.main()`
  calls it once, before building `AllWellApp`; `--theme <name>` overrides the env
  var. `tests/visual/render_views.py` honours it too, so
  `ALLWELL_THEME=grey … render_views.py` audits any theme.
- **Build-time default (soft).** `ALLWELL_THEME=<name> scripts/build_executable.sh`
  bakes `allwell_theme.txt` at the frozen bundle root (mirroring the profile
  marker); `all_well_launcher._apply_baked_theme` reads it and `setdefault`s the
  env var, so the shipped exe starts on that theme while a runtime `ALLWELL_THEME`
  / `--theme` still overrides. The `.spec` validates the name against
  `theme.available_themes()` and skips baking an unknown one (build stays `blue`).
- **Startup-only, by design.** There is no in-app live switch — a runtime swap
  would need every cached inline QSS rebuilt on `QEvent.StyleChange` (see the
  per-instance-QSS note below), which hit a ~20 s repaint cascade. Selection is
  a dev-facing env var / flag.
- `WELL_COLORS_TUPLE` (categorical data identity) and `CPub` (white-bg
  publication export) are deliberately **theme-independent**.
- `ui/theme/styles.py` registers `refresh_from_theme` as a theme hook, so its
  `get_color()` palette (`CLR_DANGER`/`CLR_SUCCESS_DARK` only, batch-export's
  progress label) re-derives from the freshly-applied `Colors`.

When you add a colour or size: add it to `theme.Colors` / `theme.CPub` (for a
publication-mode plot colour) / `theme.Spacing` / `theme.Metrics` / etc. To
let a theme override it, add the attribute to the relevant bundle in
`THEMES`. **Do not** hardcode hex / px values inline, and never add a new
entry to `ui/theme/styles.py` — that module is a two-token legacy remnant
(see above), not a place to route new colours.

### Dividers

Every hairline in the app — the rule under the topbar, the rules between
Analyze's form sections, the vertical rules inside the run dashboard — is a
`QFrame` carrying one of four opt-in names, all matched by a single rule in
`theme.qss()`: `#Separator` (the Review shell's name), `#HRule` / `#VRule`
(the Analyze pane's and `widgets/run_dashboard.py`'s), and the
`hline`/`vline` dynamic properties. They all paint `Colors.border_subtle` with
`border: none`, and `#HRule` / `#VRule` are pinned to one pixel in the QSS so a
caller need not also call `setFixedHeight(1)`.

The `border: none` is the load-bearing part. A `QFrame` whose shape is
`HLine`/`VLine` otherwise paints Qt's default etched line, which is derived
from the widget palette rather than the theme and reads as a near-white bar on
the dark surfaces. Give a new divider one of the four names; never leave a bare
`setFrameShape(QFrame.HLine)` unnamed.

The sidebar│centre divider is the `QSplitter` handle, styled by
`QSplitter::handle` (also `border_subtle`, 6 px). Leave the width to the
stylesheet — a `setHandleWidth` call on an individual splitter makes that one
divider a different weight from the rest.

The publication-export plot surface uses `theme.CPub` and feeds the
target figure's axes (`figure.facecolor`, `axes.facecolor`, etc.) when
a `PlotCard` flips into Publication mode. Styling is per-figure (not a global
`matplotlib.rcParams.update`), so toggling one PlotCard doesn't change the
rcParams seen by later figures.

**Widgets with per-instance QSS.** Many widgets in `widgets/` set their
own stylesheet at construction (`self.setStyleSheet(self._build_qss())`
or `self.setStyleSheet(self._qss())`) — that's load-bearing for
per-widget object-name scoping, but it freezes the colour tokens at
construction time. This is why theme selection is **startup-only**: the theme
is applied before these widgets build, so they read the themed `theme.Colors.*`
at construction. A *runtime* switch would additionally need every such widget to
rebuild its inline QSS when Qt fires `QEvent.StyleChange`.

`widgets/_support.install_qss_refresh(widget, qss_factory)` is the opt-in helper
for that future: it installs a single event filter that catches `StyleChange`
and calls `widget.setStyleSheet(qss_factory())` (idempotent). It is currently
**defined but unused** — PlotCard deliberately reverted it (`plot_card.py`) after
the event filter's own `setStyleSheet` re-fired `StyleChange` recursively and
caused a ~20 s load stall. Any future live-switch work must adopt it (with the
equality guard) across the ~20 widgets that freeze inline QSS.

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
              ├── exit Split View if active (§5.10) — restore the single
              │   notebook before view-state restore + redraw touch it
              ├── glob *.csv, validate header, load each into pandas
              ├── reset prior-dataset state BEFORE hydration (see below)
              ├── populate app._well_paths, app._cache
              ├── _build_tok_to_label, _rebuild_all_timepoints_cache, …
              ├── hydrate persisted state (sample_definitions, ratios,
              │   heatmap_layouts, cell_overrides, line_order, cell_gating)
              ├── _refresh_sidebar_map           ← view side
              ├── _recalculate_threshold        ← invokes _update_channel_selector
              └── app._redraw()                  ← plot_orchestrator.redraw
```

**Cross-dataset state reset on swap.** The per-dataset persistence
loaders **early-return** (or merge additively) when the new dataset's
`pipeline_info` / sidecar block is absent, so on a dataset swap any in-memory
field left in place would survive and silently apply to the new data. The load
path therefore clears this state to its empty defaults **before** hydration, at
the existing load-path chokepoints — the new dataset's own sidecar (if any)
repopulates afterwards:

- `DatasetState.begin_load` clears `well_labels`.
- `_reset_sample_definitions_state` (`core/load_controller.py`) clears
  `selections` / `current_selection_id` / `notes_text` (+ the Notes editor
  widget) / `active_solo_wells`.
- `_reset_gating_state` clears the scatter-gate config **and** the gate values
  (`fluor_gates` / `thresh_frac_on`); `recalculate_threshold` later in the load
  rebuilds the Cell-Gating rows before re-applying any persisted values.
- Ratio metrics reset at their hydration point (their channel-combo cascade must
  run against the loaded dataset); stale Review-Image / Preview / Playground
  image caches drop via the `on_dataset_loaded` hooks.

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
selection_controller.on_plate_sel_change(ctx)
        ├── commits the plate change (SelectionState diffs prev_sel → last_sel)
        └── emits bus.selection_changed
              └── SelectionRefreshCoordinator → apply_selection_refresh(ctx)
                    ├── app._refresh_sidebar_map()
                    └── _refresh_tab_view(ctx, tab) for each visible leaf
                          ├── the feature's registry `on_selection_changed` hook:
              │     Bar Plots      → redraw_bars(app)
              │     Scatter Plot   → scatter_redraw_active(app)
              │     Review CSV     → app._refresh_review_csv()
              │     smFISH         → smfish_sync_from_app(app)
              │     Sample Defs    → cell_gating CDF refresh (no plot redraw)
              │     anything else  → app._redraw()
```

The per-tab dispatch keys off `_current_centre_tab()`, so in **Split View**
(§5.10) it runs once per *visible* leaf — `apply_selection_refresh` loops
`selection_controller._visible_tabs(app)` (→ `visible_centre_tabs`), de-duping
the shared `app._redraw`; channel/plot-option redraws fan out the same way via
`fold_change_scopes.visible_scope_names`. Off split both resolve to the single
active tab, so the flow above is unchanged.

That **two-step "selection-changed (paint) → drag-finished (commit)"** split
is important. Drag-paint events fire many times per second; they should not
trigger a redraw on every cell. Click handlers (`Select all`, `Select none`,
row/column header) must explicitly call `_on_plate_sel_change()` to commit.

**Group (replicate-set) mode vs per-well mode.** When the dataset has
selections (replicate sets / groups) defined, `_refresh_sidebar_map_now` puts
the plate in `"passive"` mode on most tabs: per-well plate clicks are ignored
and the plate paints group membership; clicks instead toggle a group's `hidden`
flag (plotting tabs) or its visibility. The exceptions are
`selection_controller._PER_WELL_TAB_NAMES` = **Sample Definitions, Review CSV,
Cell Browser** — these stay in `"select"` mode and honour `selected_wells` even with
groups defined (the Cell Browser sources its gallery from `selected_wells`, so
single replicate wells must be pickable there). Because the plate is passive in
group mode, the plate's **corner all/none toggle** (only shown in `"select"`
mode) would vanish; it now also shows in passive mode and emits `cornerToggled`,
which `on_sidebar_plate_corner_toggled` routes to `select_all` / `select_none`
(toggle every group visible/hidden). Corollary on the plot side: in group mode
`selected_wells` is **stale** (clicks don't write it), so `ordered_bar_keys`
(and the FOV-pooled collector) must NOT fall back to `selected_wells` when no
group is visible — groups defined but all hidden ⇒ no bars (otherwise "Deselect
all" resurrected every well).

**Drag-source mode (Heat Map).** While the Heat Map tab is active,
`tab_controller.sync_heatmap_well_drag_mode` sets `WELL_MIME` as the plate's
drag mime so a well can be dragged into the heat-map layout grid. The plate
stays a working well picker under that mode: `_PlateGrid.mousePressEvent` only
*arms* the drag (recording `_drag_press_pos` / `_drag_press_cell`) and defers
the selection action, and `mouseReleaseEvent` replays it through `_press_well`
whenever the pointer never travelled far enough for `mouseMoveEvent` to hand off
to a `QDrag`. So a click selects, deselects, or activates exactly as it does on
every other tab; only press-and-drag paint/rubber-band selection is taken over,
because that is the same gesture as dragging a well out. Row/column headers and
the corner all/none toggle are resolved before the drag-source branch and behave
identically either way.

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
   4. The renderer is the shared ``lineplot.controller.render_line_series``
      (over a ``LineAxes`` bundle): the on-tab redraw and the batch export
      figure BOTH call it, so the four-panel layout (mean / fraction /
      N(above) / CDF) can never drift between them. The batch line CSV /
      figure collect via
      ``features.batch_export._line_series_runner.collect_line_series_for_group``
      — a thin orchestrator for a mixed ``BarGroup`` that reuses the tab's OWN
      statistic per member: rep-set members via ``compute_rep_stats`` /
      ``compute_rep_per_fov_stats`` (mean-of-per-well-means, per-FOV), solo
      wells via ``aggregate_well`` (within-well) so a solo stays byte-for-byte
      identical to its on-tab per-well export. The old pool-of-cells batch
      numerator (a documented divergence here) is gone. The bar batch path is
      the same shape: ``barplot.collect.collect_bar_items_for_group`` pools
      multi-well members via ``compute_rep_stats`` and keeps solos on
      ``aggregate_well``.
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
   ``redraw`` field is a callable (not a string method name), so a renamed
   or missing target surfaces directly instead of silently no-op'ing at
   dispatch time.

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
        ▼ stdout stream → ProgressTracker.parse() → RunDashboard events
                         → Analyze tab log view (collapsible, underneath)
process_microscopy.main()
        ├── per-well loop (ProcessPoolExecutor)
        │     ├── extract zip (or compress input folder) → tmp dir
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

### 9.7 Adding new timepoints / FOVs to an existing dataset (incremental)

The common workflow "analyze the first half of a movie, then the rest" (or
"add a few more FOVs"): drop the **new** image files into the already-grouped
input and re-run. Only the genuinely-new FOV-groups are processed; their
results are **merged** into the existing per-well CSV and `<well>_out.zip` —
nothing already done is re-segmented. Per-FOV quantification is independent
(no cross-FOV/cross-timepoint state), so this is exact, not an approximation.

**What counts as "new":** a FOV-group is one `(fov, timepoint)` — i.e. the
nuclear file plus its fluor channels for that field+time. "New" = a
`(fov, timepoint)` not already present in the well's existing CSV.

**Local, step by step:**

1. **Drop the files.** Put the new TIFs alongside the existing per-well zips in
   `in/` (e.g. the second half's timepoints, or a new well's images). Run.
2. **Append** — `input_resolution_service.append_loose_images_to_zips` folds
   each loose TIF into its matching `<WELL>.zip` (creating `<WELL>.zip` for a
   brand-new well), then deletes the loose source. Duplicate member names keep
   the existing member (the loose copy is skipped, not overwritten).
3. **Scope decision** — `select_incremental_scope` sets `opts["incremental"]`
   when anything was appended, *unless* adding the new data would invalidate a
   model for already-processed work, in which case it falls back to a full
   `--force` reprocess:
   - a `basic` flat-field/background is sampled across **all** images, so adding
     any data changes it for every well → always force;
   - an `empty_well` model is per-(channel, timepoint). Adding a blank for a
     **new** timepoint leaves every existing timepoint's correction (and its
     already-processed FOVs) unchanged → stays incremental. But adding a blank
     at a timepoint the background well **already had a blank for** changes that
     timepoint's field, so the already-processed wells at it would be stale →
     force. The decision is self-contained in the input: it compares the
     appended blanks' timepoints against the background well's existing zip
     timepoints (`_loose_timepoints_for_wells` / `_zip_timepoints`).
4. **Args** — `build_pipeline_args` emits `--incremental` and
   `--incremental_prior_dir <output_dir>` (where the prior per-well CSVs live).
5. **Diff + process** — for each well, `process_well_zip_task` builds the
   done-set (`pipeline.io.incremental_done_fov_keys`): the `(fov, timepoint)`
   pairs from the prior per-well CSV, **unioned** with those recovered from the
   processed-output artefacts — `<well>_out.zip` members and the uncompressed
   output folder, whose names keep the input stem. The output fallback matters
   when a well has its `<well>_out.zip` but no sibling CSV (a remote-only
   materialise, or a CSV that was moved/lost): without it the done-set would be
   empty and every already-processed FOV would be needlessly re-segmented. It
   processes only the groups whose `(fov, timepoint)` isn't in the done-set, and:
   - a well with **no** new groups → logs `Well X — up to date (no new FOVs).`
     and returns (no rewrite);
   - a well with new groups → segments just those, then **merges**:
     `merge_records_into_csv` appends the new rows (dedup by
     `(well, fov, timepoint, nucleus_id)`) and `merge_images_into_zip` adds the
     new images to `<well>_out.zip`;
   - a brand-new well (no prior CSV) → processes in full.
6. **Globals refreshed** — `write_pipeline_info` rewrites `pipeline_info.json`
   with the refreshed `available_timepoints` / `available_fovs` scan while
   preserving curation (`cell_gating`, `sample_definitions`, `ratios`,
   `notes`); the auto-threshold pass re-runs but never overwrites an existing
   `cell_gating.thresh_frac_on` (so established thresholds are stable).
7. **Review** — stats/plots are recomputed live from the per-well CSVs on
   reload, so the new timepoints/FOVs appear with no extra step.

**Remote** keeps the client smart and the remote a dumb compute engine, so only
genuinely-new bytes cross the wire (see §6.4 / `services/remote_runner.py`):
`stage_local_zips` **splits** before upload — for each touched well it keeps
only the new-FOV members (`_filter_new_fovs_in_staging`, done-set from the local
CSV **unioned** with the local `<well>_out.zip`) and drops wells whose local
output already covers every input FOV (emitting `[remote] ✓ X: up to date
(local)`); the remote argv has `--incremental` stripped (it just processes what
it received); and on fetch the partial outputs are **merged** into the local
full CSV / `<well>_out.zip` with the same `pipeline.io` helpers.

**Dashboard:** up-to-date, merged, and new wells each reach a correct terminal
state — `ProgressTracker` maps the `up to date` / `merging N new FOV(s)` /
`SKIP well … output already exists` markers to per-well done/processed events
(§6.4 covers the separator-tolerant parsing that keeps this working when a
remote UTF-8 stream is decoded on a cp1252 Windows client).

---

## 10. Cross-cutting concerns

### 10.1 Logging

- Every module uses
  `logging.getLogger("well_viewer")` (the viewer) or
  `logging.getLogger("well_viewer.auto_threshold")` (per-domain children).
- `allwell._attach_log_ring_buffer` captures every record into a ring
  buffer that the Help drawer's log tab and the Analyze tab's live log
  view both read from.

### 10.2 Debug flags

`well_viewer/core/debug_flags.py` exposes module-level booleans
(`REVIEW_TAB_DEBUG`, `REVIEW_BAR_DEBUG`, `REVIEW_SCATTER_DEBUG`,
`ANALYZE_TAB_DEBUG`, `REVIEW_IMAGE_CHSW_DEBUG`).
`allwell.main()` wires the top-of-file constants in `allwell.py` into
these flags so toggling them is a one-line edit. Each flag gates `logger.debug`
calls inside its domain; off by default.

### 10.3 Build / packaging

- **Source run:** `python allwell.py [--data_dir …]`.
- **macOS .app:** `_Docs/_Installation/build_all_well.sh` (mamba env +
  PyInstaller), spec at `_Docs/_Installation/all_well.spec`.
- **Windows .exe:** `scripts\build_executable.ps1 [-Clean]` — the PowerShell
  twin of `scripts/build_executable.sh`, driving the same spec. It invokes
  `python -m PyInstaller` rather than the `pyinstaller.exe` shim (not reliably
  on PATH from a fresh Miniforge Prompt) and reads the same `ALLWELL_PROFILE` /
  `ALLWELL_THEME` / `ALLWELL_WITH_TRACKING` environment switches. Output is
  `dist\AllWell-<version>\AllWell.exe`; the spec's `BUNDLE` step is a macOS-only
  no-op, so no `.app` is produced. Both scripts resolve the bundle folder they
  report through `bundle_name()` (§5.3) instead of a hardcoded path.
- **Feature-subset binary:** `ALLWELL_PROFILE=<name> scripts/build_executable.sh`
  builds a binary containing only that profile's features, with excluded
  features' source and unique heavy deps dropped from the bundle and the profile
  baked as `active_profile.txt` (§5.3 *Build profiles*). Unset = the full app.
- **Pipeline-only run:** `python process_microscopy.py --input_dir … --output_dir …`.
  No GUI deps required.

### 10.4 Running the tests — the full-app OOM caveat (read before `pytest tests/`)

The pytest suite has two very different kinds of test, and running them the
wrong way will **hang the run and OOM-kill the container**. Know which lane
you're in before you type the command.

- **Pure-logic / unit tests** (Qt-free: `pipeline/*`, `domain/*`,
  `spatial_compute`, `multivariate_compute`, layout math, …). Fast, cheap,
  safe to run in bulk. This is the CI "fast lane" — run it with
  **`scripts/test.sh`** (~1,770 tests, ~55s). CI invokes that same script, so a
  local run and the CI run cannot drift in flags or config; hand-rolling
  `pytest tests/ -m "not slow"` skips the `--timeout=90` CI applies.
- **Full-app tests** (anything using the `viewer_app` / `loaded_app` /
  `loaded_app_shape` / `image_table_app` fixtures — ~70 files). Each one
  constructs a **real `WellViewerApp`** under the offscreen QPA. `conftest.py`'s
  `pytest_collection_modifyitems` auto-marks these `slow`, precisely so the fast
  lane skips them.

**Before pushing, `scripts/preflight.sh`** runs ruff, the fast lane, and a
working-tree check in one pass (`--quick` skips the fast lane). The tree check is
the non-obvious one: it fails on *untracked* files, because a hand-run job's
scratch output is what `git add -A` sweeps into a commit — that is how a sweep's
JUnit reports once reached `main`.

**No test may reach a modal dialog.** An `autouse` fixture in `conftest.py`
replaces every `QMessageBox` / `QInputDialog` / `QFileDialog` / `QColorDialog` /
`QFontDialog` opener and `QDialog.exec` with one that raises. Offscreen, a modal
dialog waits for a click that never comes, inside Qt's C++ event loop where
pytest's signal-based `--timeout` cannot reach it — so the run can only die at
the batch wall-clock ceiling, before pytest writes its JUnit report, taking a
whole batch down and skipping the rest of the hanging file. A test that means to
exercise a dialog path patches that specific call itself; its `monkeypatch` is
applied later and wins.

**Why the full-app tests are dangerous in bulk.** Constructing the app builds
~1,800 QObjects wired together with signal→bound-method connections
(`signal.connect(app._method)`), closures that capture `app`, and child widgets
that keep an `_app` back-reference (coordinators, `LabelGrid`,
`RatioMetricsPanel`, the `WidgetRegistry` / `ViewerContext` / `PersistenceRegistry`).
That web of Qt-C++ ↔ Python references **is not reclaimed within the process**:
`close()` + `deleteLater()` + `gc.collect()` do **not** free it — measured, the
`WellViewerApp` instance and its widgets survive teardown, so `app.allWidgets()`
climbs ~1,800 per test and RSS grows ~40 MB per test, monotonically. pytest runs
every test in **one process**, and the container has **no swap**, so a big slow
run:
- **hangs** — the ever-growing live-widget count makes each later test's Qt work
  progressively slower, until the suite crawls; and/or
- **gets OOM-killed** — RSS climbs unbounded until the orchestrator kills the
  container (which looks like a mid-run "restart").

This is an inherent property of tearing a large PySide app up and down in-process,
not a bug in any one test — do not try to "fix" it by adding `gc.collect()` to a
fixture (it doesn't help).

**Rules for running tests here:**

1. **Bulk runs use the fast lane only:** `scripts/test.sh`. Never run a bare
   `pytest tests/` (or `-m slow` across everything) in this environment — that's
   the command that kills the container.
2. **Run full-app tests in small, fresh processes.** Invoke them a *file or two at
   a time* — `scripts/test.sh tests/test_app_smoke.py -m ""`,
   `scripts/test.sh tests/test_multivariate_tab.py -m ""` — so the OS reclaims the leaked memory
   when each short-lived process exits. Budget roughly ≤ ~15–20 full-app tests per
   process.
3. **Prefer the pure-logic layer.** New behaviour should be testable without the
   full app wherever possible (the `collect.py` / `_compute.py` / renderer-snapshot
   split exists partly for this). A Qt-free unit test never pays the per-app cost.
4. **Long runs will be interrupted.** A single `pytest` invocation that takes many
   minutes is at risk of a container recycle. Keep each invocation short; if you
   need broad coverage, script several small invocations rather than one large one.
5. **To run the whole slow lane, use `scripts/test_slow.sh`.** It applies rules 1,
   2 and 4 for you: it discovers the lane from pytest's own `slow` marker (the
   fixture closure in `conftest.py` is the only thing that knows the real
   membership), runs it as many short pytest processes a couple of files each,
   and aggregates the per-batch JUnit reports. A batch that dies before writing
   its report fails the run — that is the OOM / C-level-hang signal, and it must
   never read as success. `--shard I/N` deals the files out round-robin for a CI
   matrix; `--batch 1` is the safest setting on a constrained machine.

   Two ceilings guard a batch, and they do different jobs. `--timeout=300`
   (signal-based) catches a hung *test*. `SLOW_BATCH_TIMEOUT` (default 1800s) is
   the wall-clock backstop for a C-level hang inside Qt, which ignores signals.
   Keep the second one generous: app construction dominates this lane — the
   heaviest pair, `test_multivariate_tab` + `test_playground` (29 tests), takes
   ~12 minutes with the machine to itself and passes — and a batch cut off here
   writes no report, so too tight a ceiling reports green tests as a failure.

**The slow lane runs nightly, not on PRs** — `.github/workflows/slow-lane.yml`,
six sharded runners, plus `workflow_dispatch` for on demand. It is not a PR gate
because it takes minutes rather than seconds and its per-test memory profile is
fragile under parallel load. It exists because the ~270 tests it covers
previously had *no* automated coverage anywhere, so a test could sit red
indefinitely with nothing to report it — several did, each one a `ctx`-taking
function called with the raw app from the day that function migrated. Run it by
hand before merging anything that reshapes a feature package.

That particular defect also has a *static* counterpart in the fast lane
(`tests/test_frozen_ctx_guardrail.py`), which scans the test suite's own call
sites: on a real `WellViewerApp`, `ctx.window` resolves to Qt's `QWidget.window`
**method** instead of raising, so the failure surfaces frames later — or not at
all, when the callee wraps its body in a `try`, leaving a green test that
asserted nothing. Catching it statically means it no longer depends on the
nightly lane.

**What the environment gives you that CI does not (and vice versa).** The two
lanes are not the same machine, and the differences decide which failures you
can reproduce:

| | CI | A dev machine |
|---|---|---|
| Deps | `tests/requirements-dev.txt`, pinned to the app's stack (numpy 1.24, pandas 2.2, PySide6 6.7.3) | `_Docs/requirements.txt` — same pins, plus the ML stack |
| ML stack | absent — `importorskip("stardist")` / `("tensorflow")` tests **skip** | present, so those tests actually run |
| OS / Python | ubuntu-latest, 3.11, case-sensitive FS | typically macOS arm64, 3.10.14, case-**in**sensitive FS |
| Timeout | `--timeout=90` per test | same, via `scripts/test.sh` |
| State | fresh clone — no `~/.allwell/scratch`, no datasets, no `__pycache__` | whatever you have lying around |

So a green CI run has *not* exercised StarDist or TensorFlow, and a green local
run has not proven your filenames are case-correct for Linux. The dep pins were
lower bounds until recently, which had CI resolving numpy 2.x / pandas 3.x
against an app that ships numpy 1.24 / pandas 2.2 — the single largest source of
"passes locally, fails in CI". Keep them pinned.

The pytest configuration lives in **`pyproject.toml`** (`[tool.pytest.ini_options]`),
not `tests/pytest.ini`. That matters: an ini under `tests/` is found by
`pytest tests/` (rootdir becomes `tests/`) but *not* by a bare `python -m pytest`
from the repo root (rootdir stays at the root), so markers, `filterwarnings`, and
`testpaths` silently applied in one lane and not the other. A root config is
discovered from either.

### 10.5 End-to-end segmentation tests (`tests/test_segmentation_e2e.py`)

One test per segmentation method the Analyze dropdown offers — `stardist_nuclei`,
`stardist_seeded_watershed_cell`, `cellpose`, `cellpose_labelfree`,
`classical_threshold` — each run through the **real Analyze tab**: a synthetic
plate of schema-named TIFFs is written to a tmp dir, the form is filled the way
an operator would fill it, `_run()` is called, and the Qt event loop is pumped
until the `process_microscopy.py` subprocess the tab spawned exits. Nothing is
mocked, so one test covers the grouping (zipper) phase, the subprocess launch,
the log/progress plumbing, segmentation, quantification, per-well CSV assembly
and output zipping. The assertions are on the artefacts that land in
`raw/out/` (per-well CSV rows, `pipeline_info.json`, `_labels.tif` inside
`<well>_out.zip`) plus the tab's completion contract (the "Processing Complete"
banner and the `on_pipeline_complete` callback).

Complementary to, not a replacement for, `test_segmentation_methods.py` /
`test_labelfree_phase.py` (form state + CLI arg mapping, no run) and
`test_pipeline_e2e.py` (`process_image_group` / `process_well_folder_task`
directly, no GUI).

Test images come from `tests/fixtures/synthetic_pipeline.write_synthetic_channels`,
the multi-channel generalisation of `write_synthetic_fov`: every channel draws the
same nuclei centres, with the kind (`nuclear` / `fluor` / `cytoplasm` / `phase`)
picking disk radius and intensities, so cytoplasm and phase channels are
registered with the nuclei the way a real acquisition is.

Skips and cost. The module is marked `slow` (each test loads a segmentation
model), so the fast lane never picks it up; run it on its own:
`scripts/test.sh tests/test_segmentation_e2e.py -m ""`. The StarDist tests need
TensorFlow + StarDist and the Cellpose tests need Torch + Cellpose *and* the
model weights already cached under `~/.cellpose/models` (or
`CELLPOSE_LOCAL_MODELS_PATH`) — Cellpose downloads them from cellpose.org on
first use, which an offline or egress-restricted machine cannot do. Where any of
that is missing the affected tests skip and `classical_threshold`, which needs
nothing beyond scipy/skimage, still runs. Availability is probed with
`importlib.util.find_spec`, never an import: TensorFlow and Torch cannot always
share a process, and the pipeline keeps each run in its own subprocess precisely
to avoid that.

### 10.6 Environment bootstrap for remote sessions

`.claude/hooks/session-start.sh`, registered as a `SessionStart` hook in
`.claude/settings.json`, provisions a Claude Code on the web container so the
linter and the fast lane can run there. It installs the Qt system libraries
(`libegl1`, `libgl1`, `libxkbcommon0`, `libdbus-1-3`) and
`tests/requirements-dev.txt`, mirroring `.github/workflows/ci.yml`'s setup steps
so a remote session and CI resolve the same pinned versions from the same file.
Both steps are guarded by a presence check, so a cached container re-runs the
hook in well under a second.

Without it a fresh remote container has no test stack at all, and every test
collection dies at `import PySide6` with `libEGL.so.1: cannot open shared object
file` — Qt needs GL/EGL even under the offscreen QPA platform.

The hook exits immediately when `CLAUDE_CODE_REMOTE` is not `true`: a developer
machine has its own environment (`_Docs/requirements.txt`) and the hook must
never touch it. It also deliberately does **not** install the ML stack —
TensorFlow + StarDist and Torch + Cellpose are several GB and only the `slow`
segmentation tests need them, which is the same line `tests/requirements-dev.txt`
draws. A remote session that needs to run §10.5's StarDist or Cellpose tests
installs that stack by hand.

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
   `core.plot_style.apply_export_style_to_current(ctx, fig, canvas)` so the
   Properties sidebar's toggles apply. (`features.export.style` re-exports it
   for older call sites.)

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
line in `AllWellApp._install_shortcuts` (`allwell.py`). The help drawer's
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
title bar, search `allwell.py`.

### 12.2 "The plot is showing the wrong data"

In order of probability:

1. **Stale `_active_channel`.** Check `_update_channel_selector` ran with
   the current dataset's channel list and that the global ctxbar combo's
   current text matches `_active_channel.upper()`.
2. **A controller didn't get the redraw entry-point call.** Check the
   feature's `on_selection_changed` hook in `features/registry.py`, which
   `selection_controller._refresh_tab_view` dispatches to.
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
`python allwell.py` from a terminal and watch stderr. The most common
failure is a missing widget attribute on `WellViewerApp` because two
builders touch a shared attribute in the wrong order. Use the
`_centre_lazy_only_titles` set in `centre_view.py` to control which
builders never run from the background drain.

### 12.6 "The figure's Properties panel doesn't stick"

Every figure-styling toggle writes into the shared prefs dict
(`core/export_prefs_state.py`, reached as `app._export_style_prefs`; the panel
itself is `features/export/sidebar_view.py`). Every controller ends its redraw
with `core.plot_style.apply_export_style_to_current(ctx, fig, canvas)`.
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

### 12.8 "The app died with no Python traceback (segfault / access violation)"

`allwell.main()` calls `faulthandler.enable()` at startup (guarded — skipped in a
windowed PyInstaller build that has no `stderr`), so a hard native crash dumps a
C/Python stack to `stderr` instead of vanishing. Run from a terminal to capture
it. These usually come from touching a Qt widget tree mid-event-dispatch or after
its C++ object is deleted — e.g. the Split View focus filter (§5.10) defers its
pane switch with `QTimer.singleShot(0, …)` precisely to avoid reparenting during
`MouseButtonPress` delivery.

---

## 13. The `Markdowns/` library

The `Markdowns/` folder is intentionally small — it holds live reference docs,
not historical plans / status write-ups (those live in git history). What
remains:

| File | What it's for |
|------|---------------|
| `ARCHITECTURE.md` | This document — the developer-oriented architectural overview. |
| `README_WellViewer.md` | User-facing README — install, run, troubleshoot. |
| `GOD_OBJECT_REPOSITORY_ROADMAP.md` | Living plan for the god-object teardown + repository introduction; tracks the ratchet numbers as they come down. |

The on-disk shape of `pipeline_info.json::sample_definitions` (the saved
selections schema) is described in the `well_viewer/domain/selections_model.py`
module docstring; that module is the live source of truth for the
contract.

`design/` (at the repo root) holds the **non-Markdown** design artifacts — the
HTML mockups (`grey_theme_mockup.html`, `image_table_builder_mockup.html`) — for
visual reference only. Rendered screenshots live under
`_Docs/visual_audit/screenshots/`, produced by `tests/visual/render_views.py` —
a generated directory, git-ignored, so a re-run does not show up as two dozen
modified binaries.

That script builds the real `AllWellApp` off-screen against the `mini_dataset`
fixture and walks every rail entry, inner sub-tab, mode and dialog. Two things
it has to get right, because both have silently cost it coverage: it drives
controllers with the frozen `ViewerContext` (`rev._ctx`), never the window —
passing the window resolves `ctx.window` to `QWidget.window`, a bound method, and
the first controller attribute access raises; and a tab whose alternate views are
a `SegmentedControl` rather than an inner `QTabWidget` needs its own capture pass,
since the generic `_capture_inner` walker cannot see them. Multivariate is the
case in point — its four analysis modes swap different option groups into the
control row, so only a per-mode pass exercises the one that makes the row wrap.

---

## 14. Glossary

| Term | Meaning |
|------|---------|
| **Pane** | One of the three top-level pages: Review, Analyze, or Datasets. |
| **Tab** | A centre-stack page inside the Review pane (Plotting, Statistics, …). |
| **Sub-tab** | A leaf page inside a parent tab (e.g. Plotting → Line Graphs). |
| **Sidebar** | The left rail in the Review pane. Hosts the rail nav + the well picker + per-tab control panels. |
| **Ctxbar** | The horizontal contextual control row above each plot card. Hosts channel / metric / timepoint combos + action buttons. |
| **PlotCard** | The framed container around each matplotlib figure. Provides the screen↔publication toggle, the custom toolbar, and the stats chip slot. |
| **Properties sidebar / Export Style sidebar** | The right-side dock that slides out when the user clicks the Properties button on a plot card. Backed by `app._export_style_prefs`. |
| **Selection** | A named, persisted subset of wells. Lives in `app._selections` (schema v2). Aliased as "Sample Definition" in the UI. |
| **Replicate set** | A group of wells treated as replicates for a single condition. On the Sample Definitions → Groups page a group **is** exactly one replicate set — its `wells` are its replicate members (or a single well); there is no `replicates` sub-partition (the field is reserved/`None`). "Quick Replicates" auto-builds groups of a chosen size N (spinbox) along rows/columns. (The separate batch-export `BarGroup` — from a user-picked `bar_groups.json` — can still hold multiple member sets + solo wells; that is a distinct feature.) |
| **`pipeline_info.json`** | The sidecar file written by `process_microscopy.py` next to the per-well CSVs. Holds schema, channel tokens, available timepoints, persisted selections, gating thresholds. |
| **Well token** | Two-letter / two-digit well identifier (`A01` … `H12`). Always uppercase, always zero-padded. |
| **fov, tp** | Field-of-view and timepoint extracted from a filename via the schema-aware extractor in `viewer_state.make_schema_extractor`. |
| **Split View** | Optional mode (§5.10) showing two different Review tabs side-by-side, both live. All machinery is in `core/split_view.py`, wired by one `install_split_view(app)` call (comment it out → feature gone). |
| **Provider hook** | A module-level callable on `tab_controller` (`split_focus_provider` / `split_visible_provider` / `split_nav_intercept`), `None` by default, that an optional feature registers to extend a core resolver without the core importing the feature. The seam that keeps Split View one-line removable. |

---

## 15. Conventions and gotchas

- **Never silently swallow exceptions.** Don't write `except Exception: pass`.
  Use `well_viewer.core.safe.swallow(...)` / `swallowed(...)` (GUI side) or
  `aw_safe.swallow(...)` / `swallowed(...)` (pipeline/`services` side, Qt-free) so
  a suppressed failure still leaves a DEBUG breadcrumb. Enforced by
  `tests/test_no_silent_except.py`. (Two near-identical helpers exist on purpose:
  the pipeline must not import `well_viewer` — see §6.2.)
- **Never hardcode colours.** Use `theme.Colors.*` or QSS object names.
- **Never block the UI thread.** Heavy operations spawn workers (see
  `well_viewer/features/smfish/worker.py` and the `services.pipeline_runner`
  pattern). Streaming progress messages route through `logging` and the
  status-bar label.
- **Plate selections are well IDs (strings), not (row, col) tuples.**
  `_selected_wells: set[str]`. Convert to / from tuples at the
  edge with `data_loading.parse_well_token`.
- **Parse a well name with `util.well_token`, never a local regex.** The
  canonicaliser strips surrounding whitespace, uppercases the row and
  zero-pads the column (`" a1 "` → `"A01"`), and returns `None` for anything
  outside A–H / 1–12. Ask it once and use what it returns rather than
  validating and then re-matching — `canonical_well_label` is both the test and
  the conversion. A second parser is not a style problem: when the pipeline
  carried its own, a folder named `"A1 "` was a well to the Analyze-side input
  resolver and not a well to the pipeline that then processed it.
- **Normalise FOV / timepoint tokens with
  `data.image_resolver.normalize_numeric_token`.** These arrive from filenames,
  CSV columns and combo text, so `1`, `01` and `1.0` must collapse to one key
  before anything compares them. A local copy that formats differently strands
  a filter silently — a combo whose text never matches any row.
- **Read a timepoint token as hours with `util.timepoint.parse_hours`, and sort
  timepoints with `util.timepoint.sort_key`.** Both are stdlib-only, so the
  pipeline, `services/` and `well_viewer/` all import them (`services/` may not
  import `well_viewer` — §6.2 — which is what forced a separate copy before).
  Three names remain for call-site reasons and all delegate:
  `data.image_identity.parse_timepoint_hours` (the viewer's, consumed by
  `normalize_tp` for canonical keys), `pipeline.auto_threshold.parse_tp_hours`,
  and `process_microscopy._parse_timepoint_hours` — the last a deliberate
  `nan`-returning adapter, so `timepoint_hours` stays a float column with no
  `None` to special-case into the CSV. Its one trade is that a literal `"nan"`
  token and an unreadable one are indistinguishable in its return; use
  `parse_hours` and test for `None` when that matters.
  `tests/test_timepoint_parsing.py` fuzzes the parsers against each other and
  asserts the sort-key identity, so a reintroduced local copy fails there. The
  grammar is deliberately strict: it will not scavenge a digit from anywhere in
  the token, because a guessed number becomes a canonical key nothing else
  recognises.
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
- **`_Docs/Markdowns/` is documentation, not code.** It is kept intentionally
  small: this file, the user README, and the teardown roadmap. The
  selections-model contract lives in its module docstring, not a separate doc.
  Port-time plans and status write-ups stay in git history.

If you read all the way to here and the structure still doesn't make sense,
the right next move is to open `well_viewer/runtime_app.py` and scroll
through `_build_ui` (around line 934) — that's where every widget tree
hanging off `WellViewerApp` gets stitched together, and every name you'll
need to grep for is in plain sight.

---

## 16. Enforced contracts (guardrail tests)

Many of the rules above are not just prose — they're checked in CI by AST
guardrail tests, so contract drift fails the suite rather than rotting silently.
The map (each rule links to the test that holds it):

| Contract (§) | Enforced by |
|---|---|
| Pipeline self-sufficiency — no `well_viewer`/`widgets`/Qt in the pipeline (§6.2) | `tests/test_architecture_contracts.py::test_pipeline_is_self_sufficient` |
| Every repo-root module the pipeline imports is uploaded to the remote (§6.2) | `tests/test_remote_manifest.py` + `test_remote_runner.py::test_required_files_list_is_what_we_promised` |
| `services/` stay Qt-free (CLI-reusable, §13) | `test_architecture_contracts.py::test_services_have_no_qt_imports` |
| `widgets/` carry no app coupling (§13) | `test_architecture_contracts.py::test_widgets_do_not_import_the_app` |
| Controllers reach the app via the `app` arg, not by importing `WellViewerApp` (§9) | `test_architecture_contracts.py::test_nothing_imports_wellviewerapp_at_runtime` |
| EventBus is a capped catalogue of exactly 5 signals (§9) | `test_architecture_contracts.py::test_event_bus_signal_catalogue_is_capped` |
| Feature-module naming scheme (§3) | `test_architecture_contracts.py::test_feature_module_naming` |
| `domain/` & `data/` never import `features/` (the layering) | `tests/test_layering.py` |
| No silent broad-except anywhere (§15) | `tests/test_no_silent_except.py` |
| `runtime_app` line/attr ratchets + state-package import boundary | `tests/test_guardrails.py` |
| No `ctx.app` — features reach the window shell only through the frozen `ViewerContext` accessors (§5.9) | `tests/test_ctx_app_burndown.py` (`CTX_APP_BUDGET = 0`) |
| No `ctx._x` scratch attributes, and no `ctx`-taking function called with the raw `app` / `self` — including the module-qualified `sc.foo(app)` spelling. Call sites are scanned in `well_viewer/` **and** `tests/`, since a test handing a real-app fixture to a `ctx` function is the same defect (§5.9) | `tests/test_frozen_ctx_guardrail.py` |
| The `ViewerContext` window-service accessor set is closed — after the A5 teardown it is exactly `{"window"}`; every other window service is now a held collaborator (`ctx.notify` / `ctx.figures` / `ctx.coordinator` / `ctx.persistence` / `ctx.features`), and adding a new accessor is a reviewed act (§5.9) | `tests/test_ctx_service_accessors.py` |
| `AppRuntime` is live-object-only — `persistence/` never imports `app_runtime` (§5.9) | `tests/test_app_runtime.py` |
| `process_microscopy` / `image_table` controller line budgets | `tests/test_monolith_budgets.py` |
| Split View is one-line removable — uninstalled, the resolvers collapse to single-tab (§5.10) | `tests/test_split_view.py::test_not_installed_is_single_tab` |
| Build profiles: a subset boots + loads, the strip plan matches the runtime filter, and the binary drops the excluded features' deps (§5.3 *Build profiles*) | `tests/test_feature_profile.py`, `tests/test_derive_profile.py`, `tests/test_profile_build.py` |
| CSV / batch export produces the expected values, and the single-plot and batch paths agree byte-for-byte | `tests/test_csv_batch_export_eval.py` (drives a real headless `WellViewerApp`) |

What is **not** statically enforced (and so relies on review + behavioural
tests): "never block the UI thread", route redraws through the `WellViewerApp`
shim, lazy-tab-build `hasattr` guards, app-wide-selection discipline, the
bar-math / fold-change invariants, and the Playground↔pipeline parity (which has
its own dedicated parity test). "Never hardcode colours/pixels" is also
review-only for now — there are pre-existing inline hex literals that would need
an allowlist before a guard could be clean.

---

## 17. Troubleshooting (build, environment, runtime)

Moved here from the user manual (`README_WellViewer.md`): a *running* app is
already past install and build, so these recipes belong with the developer docs
rather than the in-app help drawer.

### Packaging / launch (macOS)

**`AllWell.app is damaged and can't be opened.`** — macOS quarantine on an
unsigned bundle. Fix:
```sh
xattr -cr /Applications/AllWell.app
codesign --force --deep --sign - /Applications/AllWell.app
```

**The app opens and immediately closes.** — Run the bundle from a terminal to
see the actual error:
```sh
dist/AllWell-<version>.app/Contents/MacOS/AllWell 2>&1 | head -100
```

**`No module named 'pkg_resources'`.** — `setuptools` was upgraded past v70 in
the mamba environment. Recreate the environment from scratch. Don't
`pip install setuptools` and don't `mamba update --all`.

### Environment (Windows)

**`Cannot uninstall Shiboken6 6.x.y, RECORD file not found` during
`pip install`.** — A conda-installed `shiboken6` (or `pyside6`) is sitting in
the env, and pip cannot uninstall it to satisfy the `PySide6==6.7.3` pin (conda
packages have no `RECORD` manifest pip can read). Two common sources:

1. An older `mamba create` line listed `pyside6` explicitly.
2. The conda-forge `matplotlib` meta-package pulled `pyside6` in as a
   transitive dep. **This is the gotcha.** The fix is to use `matplotlib-base`
   instead — same matplotlib, no Qt binding.

Bulletproof recovery (one block, paste into Miniforge Prompt at repo root):

```powershell
mamba deactivate
mamba env remove -n allwell -y
mamba create -n allwell python=3.10.14 setuptools=69.5.1 "numpy>=1.24,<1.25" scipy scikit-image matplotlib-base pillow imageio h5py vs2015_runtime -c conda-forge -y
mamba activate allwell
pip install --no-build-isolation -r _Docs\requirements.txt
```

Do NOT include `pyside6` or `matplotlib` in the mamba step — both pull
shiboken6 in via conda where pip can't remove it. PySide6 is installed by pip
from the requirements file; `vs2015_runtime` from conda supplies the MSVC DLLs
PySide6 needs.

**`VCRUNTIME140_1.dll` was not found / PySide6 import crashes.** — The
conda-forge MSVC redistributable isn't visible to PySide6. Verify
`vs2015_runtime` is in the env (`mamba list vs2015_runtime`) and re-activate the
env so the conda `DLLs` directory is on PATH:
```powershell
mamba deactivate
mamba activate allwell
python -c "from PySide6 import QtWidgets; print('OK')"
```

**`pip install` fails on tensorflow with "no matching distribution".** —
TensorFlow 2.13.0 ships Windows wheels for Python 3.10 and 3.11 only. Confirm
with `python --version`. If you're on 3.12+, recreate the env pinning
`python=3.10.14`.

**`scripts\build_executable.ps1` reports "PyInstaller is not importable".** —
The active env doesn't have the deps installed. Activate the env first and
re-run pip:
```powershell
mamba activate allwell
pip install --no-build-isolation -r _Docs\requirements.txt
```

### GPU / models / build output

**`No GPU device found` / TensorFlow not using Metal.** — Install
`tensorflow-metal`:
```sh
pip install tensorflow-metal==1.1.0
python -c "import tensorflow as tf; print(tf.config.list_physical_devices('GPU'))"
```

**StarDist model download fails (SSL error).** — Pre-download the model on an
unrestricted machine and copy `~/.keras/models/` across.

**`No module named 'stardist'` in the built app.** — The build forgot the
PyInstaller hooks. Make sure `_Docs/_Installation/hooks/` (notably
`hook-stardist.py`) was on the spec path.

**The built bundle is > 900 MB.** — Normal. TensorFlow + Numba + LLVMLite +
SciPy + Matplotlib account for the bulk.

### Runtime / data

**Segmentation / smFISH / Image Table show "No images found".** — Check that
your results directory contains `<well>_out.zip` files. The viewer reads
top-hat-filtered fluorescence (`*_tophat.tif`), masks (`*_labels.tif`), overlays
(`*_overlay.png`), and smFISH-processed channels (`*_smfish.tif`) from inside
those ZIPs. If channel tokens differ from what the viewer expected, re-open the
directory — `pipeline_info.json` is re-read on every open.

**Wells fail with `OSError: [Errno 9] Bad file descriptor`.** — One of the
volumes the run touches is a network mount. macOS's SMB client invalidates every
open file handle when the session drops and reconnects, and a run's parallel I/O
is more than enough to provoke that; whatever had files open at that moment
fails. `mount | grep <path>` confirms the protocol — `smbfs` gives `EBADF`, NFS
would give `ESTALE`. The first question is *which side* the errno came from, and
the log now says so rather than leaving it to be guessed:

- **A local run.** Check the `Scratch  :` line at the top of the run log — it
  must **not** be a network path (it defaults to `~/.allwell/scratch`; set
  Analyze → *Scratch disk* or `--scratch_dir` to a local SSD if it isn't). That
  line carries the bulk of the run's I/O. What remains on the output dir is the
  two per-well result writes, which retry through the transient; if they still
  exhaust the budget the message names the volume. Lower `--workers` to cut the
  concurrent load, or write results locally and copy to the NAS afterwards.
- **A remote / Slurm run.** The pipeline runs on the cluster, so a well that
  fails there is the *cluster's* storage; the failure is logged by the pipeline
  itself, on the remote's stdout, with the well label. Anything prefixed
  `[remote]` is the **client**, on this machine, and points at the input or
  output volume instead — staging reads the whole plate off the input volume and
  every fetched well is published onto the output volume. Both retry, and a
  `[retry]` line in the run log is the early warning that a volume is flapping
  before it costs a well. See §6.5 for the full list of crossings.

**A remote run fails while staging, before anything uploads.** — Same cause as
above, on the input volume: staging copies every `<WELL>.zip` off it back to
back. The copy retries, and the exhausted-budget message names the mount. Copy
the plate to local storage and point All-Well at that copy.

**A remote run dies immediately with `EnvironmentLocationNotFound: not a conda
environment`.** — The pipeline's conda env isn't usable where the run looked for
it. The run log names the env it chose (`[remote] conda env: name allwell` or
`prefix <path>`), and the guard prints the host, conda's base, and every env
conda can see. Three causes, and the guard says which:

- *A directory exists at the prefix but is not an environment* (no
  `conda-meta/history`) — an env creation that died part-way, usually a full
  home-directory quota. The guard names the directory and prints the `rm -rf`
  line; remove it yourself (check the quota first, or the next install stops the
  same way) and then re-run Analyze → Remotes → *Bootstrap*. All-Well does not
  delete it for you. Bootstrap stops with the same note rather than attempting
  the `conda create` that conda would refuse. See §6.6.
- *The host was never bootstrapped*, or its env was removed. Run Analyze →
  Remotes → *Bootstrap* for that host.
- *The host's `remote_python` points at an env that isn't there.* The field is
  only consulted when the user has changed it from the placeholder; either fix
  the path or clear it to fall back to the env named `allwell`.

A value that isn't shaped like `<env>/bin/python` cannot name an environment;
the runner keeps the name selector and says so on a `[remote] warn:` line
rather than guessing at a prefix.

**Bootstrap fails with `CondaValueError: prefix already exists`.** — The same
half-created directory, on a build that predates the check above. Remove the
prefix the message names and re-run Bootstrap.

**Auto-threshold reports `timepoint unknown`.** — `pipeline_info.json` doesn't
have a schema that names a `tp` or `timepoint` field. Re-run the pipeline with
the correct `--filename_schema` so the JSON sidecar carries the right field map.

**Bar-plot timepoint dropdown is empty.** — Either no wells are selected, or the
CSVs don't have a `timepoint_hours` column. Confirm the schema you ran the
pipeline with included a `timepoint` field. Single-timepoint experiments show
one entry (e.g. `0`).

**`WellPlateZipper` produces no zip files.** — Schema or separator does not
match the filenames. Make sure `well` points at the right token, the separator
is right, and the token is a valid plate position (`A01`–`H12` or `A1`–`H12`).
The schema used is logged at the start of every run.

**Channel dropdown shows a different channel than the plot.** — Should be fixed;
if you ever see it again it means the active channel state and the combo got out
of sync. Refresh the dataset (Open the same folder again). File a bug.

---

## Change history

This document is a map for **making changes**, not a changelog — it describes how
the app is structured today, not how it got here. Do not add `(vX.Y.Z)` markers,
"new in", "was changed to", or any other note of *when* something landed: prose
here states what is true now. The only version claims that belong in this file
are the current-release ones (the header and the `version.py` directory-map
entry), which `tests/test_doc_version_sync.py` pins.

For the history of a given file or feature, use `git log` / `git blame` and the
PRs on GitHub; deep design rationale for specific subsystems lives in the topic
docs under `_Docs/Markdowns/` (see §13). When you change the architecture, update
the relevant section above rather than appending a version note here.
