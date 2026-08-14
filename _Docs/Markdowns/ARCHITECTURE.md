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

The mode bar (`Review` / `Analyze` / `Datasets`) drives the stack; segment index
maps straight to stack index. It is built by `AllWellApp` but lives on the
Review pane's mode band, along with the dataset action cluster — see
[The rail head](#the-rail-head) — leaving the title bar itself carrying the logo
and the version pill. The stack lives inside
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
  `well_viewer` logger (which sets `propagate = False`) *and* to root — the
  latter is how it sees the `services.*` loggers the Analyze runners use — at
  startup, so it captures history before first open and appends live. `emit`
  fills the ring and hands the formatted line to a
  `core.worker_bridge.GuiLogRelay`; it reads no widget state at all, because it
  runs on whichever thread logged the record. Whether the drawer is open is
  decided on the other side of that relay, in `_append_log_line`.
- **Status bar** (bottom of the window) with a status-message label and
  key-hint chips.
- **App icon, window geometry persistence.**

---

## 3. Top-level directory map

```
allwell/
├── allwell.py                  ← app entry point + title bar shell (+ version pill)
├── version.py                   ← single source of truth for the app version (read by the pill + the PyInstaller spec)
├── help_topics.py               ← Qt-free: which manual heading documents which page (help drawer)
├── mpl_bootstrap.py             ← restrict matplotlib to bundled fonts (called first in main; see §4)
├── all_well_launcher.py         ← PyInstaller entry point + --run-pipeline dispatch
├── analyze_tab.py               ← Analyze pane (form + run controls + live log)
├── analyze_tab_form.py          ← Analyze form builders + _collect_run_options (mixin)
├── analyze_tab_log.py           ← Analyze mixin: the output pane — log view, raw-log
│                                   toggle, run-outcome surfacing, "Last run ·" card
├── analyze_tab_pinfo.py         ← Analyze mixin: restore a prior run's pipeline_info into the form
├── analyze_tab_chrome.py        ← Analyze mixin: form chrome (sections, rows, entries, ⓘ markers)
├── analyze_tab_seg_params.py    ← Analyze mixin: per-method segmentation params + StarDist thresholds
├── analyze_input.py             ← Analyze input mixin: run-dir resolver, OME-TIFF
│                                   redirect, channel rows, browse-time folder scan
├── analyze_progress.py          ← Analyze run-progress rendering mixin (ETA + progress events → dashboard)
├── datasets_tab.py              ← Datasets pane (page-rail shell; see §6b)
├── datasets/                    ← dataset tools: Qt-free compute/apply + their feature views (§6b)
│   ├── _common/                ← what more than one tool needs: dataset_paths,
│   │                              containers, zip_passthrough, member_names,
│   │                              concurrency, log_panel, tool_controller,
│   │                              dataset_fields
│   ├── concatenate/            ← Concatenate datasets (compute/apply/worker/view/controller/dashboard)
│   ├── merge_plates/           ← Merge two plates into one, remapping wells
│   ├── subset/                 ← Subset / Split a dataset by well, FOV or timepoint
│   ├── import_loose/           ← Zip a loose image folder into per-well archives
│   ├── validate/               ← Validate & Repair an existing dataset
│   ├── job_queue/              ← the queue the six tools (and Analyze) submit runs to
│   └── ome_import/             ← Convert OME-TIFF (de-interleave a packed OME-TIFF → dataset folder)
├── process_microscopy.py        ← analysis pipeline orchestrator + CLI (decomposed; see §6.2)
├── pipeline/                    ← Qt-free analysis-pipeline modules (re-exported by pm)
│   ├── background.py            ← background/illumination-correction math
│   ├── auto_threshold.py       ← Otsu / threshold helpers (GUI Cell-Gating reads these
│   │                              too) + the run's own estimator over *_out.zip
│   ├── threshold_optimize.py    ← two-population threshold sweep (shared with Cell Gating)
│   ├── flatfield.py            ← flat-field illumination estimators (GUI validation view too)
│   ├── shape_features.py       ← per-cell shape-feature catalog (pipeline emits, GUI labels)
│   ├── cellpose.py             ← Cellpose segmenters (Torch imported lazily)
│   ├── stardist.py             ← StarDist segmenters + StarDistParams + per-worker model state (TF lazy)
│   ├── ome_explode.py          ← de-interleave a packed OME-TIFF into per-well .zip
│   │                              archives (Z max-projected; positions → wells via §OME)
│   ├── incremental_keys.py     ← what an incremental run considers already done
│   ├── completeness.py         ← the one definition of "is this well complete"
│   │                              (shared with Datasets → Validate) + the
│   │                              end-of-run check that records run_check
│   ├── well_skip.py            ← per-well skip decisions for a resumed run,
│   │                              and where a well's output files live
│   ├── suspend.py              ← suspend / resume a run in flight
│   ├── run_report.py           ← the end-of-run summary the log and Review both read
│   ├── capacity.py             ← disk-space accounting: what a run needs on the
│   │                              scratch and output volumes, what is left there
│   ├── sidecar.py              ← the pipeline_info.json blocks the user owns
│   │                              (cell_gating / sample_definitions / ratios /
│   │                              notes), preserved across any wholesale rewrite
│   └── io.py                   ← atomic output writers (images / CSVs / overlay)
│                                 + the scratch base & staging-dir lifecycle (§6.5)
├── util/                        ← general Qt-free utilities (no pipeline/GUI specificity)
│   ├── well_token.py           ← canonical 96-well token parser (single source of truth:
│   │                              process_microscopy, WellPlateZipper, analyze_tab and
│   │                              input_resolution_service all parse well names through it)
│   ├── plate_grid.py           ← position index → 96-well label mapping: serpentine
│   │                              (boustrophedon) + normalize_well_order (arbitrary
│   │                              click-defined order); used by the OME-TIFF de-interleave
│   ├── schema.py               ← filename-schema vocabulary + parse/validate
│   │                              (shared by process_microscopy and WellPlateZipper)
│   ├── timepoint.py            ← timepoint token → hours + the shared sort key
│   │                              (pipeline, services and well_viewer all use it)
│   ├── fov.py                  ← FOV token → number + the shared sort key, so a
│   │                              prefixed token ("f10") orders numerically, not
│   │                              lexicographically (pipeline, services, viewer)
│   ├── channel_roles.py        ← channel-name → role heuristic (nuclear/fluor/
│   │                              phase), shared by the OME converter + Analyze;
│   │                              also the quantified-channel list order and the
│   │                              phase/segmentation exclusivity rule, shared with
│   │                              the pipeline and the remote runner
│   ├── schema_infer.py         ← guess a filename schema + channels by scanning an
│   │                              input folder (loose TIFs / zips); Analyze pre-fill
│   ├── aw_safe.py              ← swallow()/swallowed() (logged exception suppression)
│   ├── retry_io.py             ← bounded retry with backoff for transient IO failures
│   ├── disk_space.py           ← free-space primitives + the one wording for a
│   │                              volume that filled (shared by the retry shield
│   │                              and pipeline.capacity)
│   ├── frozen_io.py            ← reading bundled data from a PyInstaller bundle
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
`pipeline.io`, `pipeline.capacity`) and the `util/` package
(`util.well_token`, `util.aw_safe`, `util.zip_write`, `util.retry_io`,
`util.disk_space`)
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
│   ├── frame_render.py               ← build_frame_rgb: one frame + label mask → RGB
│   ├── worker_bridge.py              ← run_off_thread: the one-shot queued-signal bridge
│   │                                   (+ progress / stage reporting for long jobs,
│   │                                    and GuiLogRelay for logging.Handlers)
│   ├── bus_wiring.py, redraw_coordinator.py, selection_coordinator.py,
│   │   scope_registry.py, projector_registry.py
│   ├── load_controller.py, tab_controller.py, selection_controller.py,
│   │   channel_state_controller.py
│   ├── fold_change_controls_view.py    ← the Control + Baseline dropdown pair,
│   │                                    shared by Bar Plots and Line Graphs
│   ├── plot_orchestrator.py, plot_style.py, plot_toggles.py,
│   │   plot_card_actions.py, figure_saver.py
│   ├── timepoint_sync.py             ← shared "focus timepoint" across plotting tabs
│   ├── safe.py                       ← swallow()/swallowed() (GUI side; §15)
│   ├── ui_helpers.py, notifications.py, dnd.py, app_fonts.py, debug_flags.py
├── data/                          ← IO + decode
│   ├── data_loading.py, image_discovery.py, image_resolver.py,
│   │   zipfile_cache.py, zip_members.py, auto_threshold.py, crop_tool.py,
│   │   metric_labels.py, cache_projectors.py, well_repository.py
│   ├── frame_load.py, dataset_dims.py   ← the layer under every image grid:
│   │   an address → its pixels, and what a dataset offers per dimension
│   ├── image_identity.py             ← single source of truth for filenames + CellKey
├── domain/                        ← Qt-free models + pure logic
│   ├── batch_models.py, ratio_models.py, selections_model.py, plate_layout.py
│   ├── fold_change.py, fold_change_scopes.py, fold_change_state.py
│   ├── fold_change_series_cache.py, control_ref.py
│   ├── plot_members.py, plot_order.py
│   ├── gating_state.py, sample_definitions.py, viewer_state.py, stats_compute.py
│   ├── bar_groups.py, plot_groups.py, metric_registry.py, qc_params.py, tp_match.py
│   ├── multivariate_compute.py       ← PCA/correlation/clustering/faceting math (Qt-free)
│   ├── spatial_compute.py            ← spatial/neighbourhood math (Qt-free)
├── features/                      ← one package per UI feature
│   ├── barplot/      controller.py, renderer.py, tab_view.py
│   ├── lineplot/     controller.py, tab_view.py
│   ├── scatter/      controller.py, collect.py, callbacks.py,
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
│   │   │                 box_select.py, callbacks.py
│   │   │                 (collect.review_build_mfi_map → per-cell MFI-on-hover
│   │   │                  tooltips, shared by Segmentation + Image Table;
│   │   │                  callbacks.py = what a finished canvas drag does —
│   │   │                  the scoped region delete + the override writer)
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
│   ├── preview/      controller.py, preview_view.py, panel_view.py,
│   │                 frame_scope_view.py, delete_scope_view.py,
│   │                 crop_scope_view.py
│   ├── smfish/       controller.py, worker.py, tab_view.py
│   ├── home/         tab_view.py, controller.py, collect.py, plate_panel.py,
│   │   │             state.py   ← the page Review opens on, and a notebook
│   │   │             (Overview is the report those five modules build; the
│   │   │              tabs after it are one sub-package each, enumerated from
│   │   │              the registry — the same shape as image_browser)
│   │   ├── cell_gating/  controller.py, tab_view.py, renderer.py,
│   │   │                 optimizer_view.py, opt_curve_view.py, gating_compute.py
│   │   │                      ← split by layer: the tab builder wires controls
│   │   │                        to the controller, which owns the two QThread
│   │   │                        workers, the ThreshFracOn optimizers and the
│   │   │                        scatter-gate plumbing; the CDF panel, the
│   │   │                        optimizer's own window and the optimizer-curve
│   │   │                        window each draw on their own, over Qt-free
│   │   │                        math in gating_compute
│   │   ├── groups/       tab_view.py, selections_view.py, controller.py
│   │   │                      ← replicate sets: the plate-backed editor, the
│   │   │                        group list, and the Quick-Replicates pairing
│   │   ├── labels/       tab_view.py   ← the 96-QLineEdit well-label grid
│   │   ├── ratios/       tab_view.py   ← the ratio-metric editor [removable]
│   │   └── pipeline_info/ tab_view.py  ← read-only run parameters [removable]
│   ├── spatial/      controller.py, collect.py, renderer.py, state.py, tab_view.py
│   ├── saved_plots/  controller.py, collect.py, state.py, tab_view.py, _report.py
│   ├── crop/         controller.py, region.py        ← support lib (always present)
│   ├── qc/           controller.py, params.py, tab_view.py   ← support lib
│   ├── export/       service.py, style.py, sidebar_view.py
│   ├── batch_export/ tab_view.py, base_panel.py, bar_panel.py,
│   │                 scatter_panel.py, service.py, _common.py,
│   │                 _line_series_runner.py
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
| `callbacks.py` | What a canvas interaction on a feature *does*, separated from the handler that detects it. | `features/scatter/callbacks.py`, `features/image_browser/review_image/callbacks.py` |
| `worker.py` | Background `QThread` worker. | `features/smfish/worker.py` |
| `row.py` | One repeated list entry's widget, split out when it grows its own menu / drag handling beyond what the page holding it should carry. | `datasets/job_queue/row.py` |
| `session.py` | A mode a feature can be in that outlives a single click and has rules of its own about entering, abandoning and committing it. | `datasets/job_queue/session.py` |
| `_panel.py` | A self-contained slice of a page's chrome: a per-plot-type panel, or a block of furniture several pages share. | `features/batch_export/bar_panel.py`, `datasets/_common/log_panel.py` |
| `_` prefix | Private helper module (not part of the package's public surface). | `persistence/_doc.py`, `_io.py`, `features/batch_export/_common.py`, `_line_series_runner.py` |

**Known exceptions** (legacy or single-purpose modules that don't fit the grid; do not propagate these naming choices to new files):

- `features/export/` (service.py + style.py) and `features/review_csv/` (tab_view.py) intentionally have **no `controller.py`**: `export/` is a stateless service layer (figure-style application + save), and `review_csv/` is a thin read-only CSV inspector tab with no behaviour to orchestrate. Adding an empty controller would be ceremony, not structure.
- `features/heatmap/models.py` is the only `models.py` in `features/` (the convention under `domain/` is `_models.py`).
- `data/` modules use bare names (`data_loading.py`, `auto_threshold.py`, `crop_tool.py`, `image_discovery.py`, `image_resolver.py`, `metric_labels.py`, `zipfile_cache.py`) — each is one self-contained utility, no internal suffix scheme.
- `domain/` mixes suffixed (`stats_compute`, `fold_change_scopes`, `fold_change_state`, `fold_change_series_cache`, `gating_state`) and bare (`fold_change.py`, `control_ref.py`, `plot_members.py`, `plot_order.py`, `selections_model.py`, `viewer_state.py`, `plate_layout.py`) names.
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
    │       │    ├── title bar (logo + version pill; the mode seg, the Review
    │       │    │            action cluster and the help button after it are
    │       │    │            re-parented into the Review pane's mode band below)
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
│  data/    ← IO + decode; Qt-free but for one allowlisted module     │
│           ── data_loading, image_discovery, image_resolver,          │
│              zipfile_cache, auto_threshold, crop_tool, metric_labels │
├──────────────────────────────────────────────────────────────────────┤
│  domain/  ← Qt-free models + pure logic, shared by multiple features │
│           ── batch_models, ratio_models, selections_model,           │
│              plate_layout, gating_state, sample_definitions,         │
│              viewer_state, stats_compute, fold_change,               │
│              fold_change_scopes, fold_change_state, control_ref,     │
│              fold_change_series_cache, plot_members, plot_order      │
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
  standing. `tests/test_guardrails.py` meters the class's *file* at
  `RUNTIME_APP_LINE_BUDGET` lines and its loose `self._*` attributes at
  `RUNTIME_APP_ATTR_BUDGET`; there is no method-count budget, so the method
  count is a number to watch rather than one that is enforced. Every one of
  those ratchets only moves down. Pattern:

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
| Home | **Home** | Overview / Cell Gating / Groups / Well Labels / Ratios / Pipeline Info |
| Analysis | **Plotting** | Bar Plots / Line Graphs / Scatter Plot / Distribution / Heat Map |
| Analysis | **Statistics** | — |
| Analysis | **Multivariate** | — (higher-dimensional analysis: correlation / PCA·UMAP·t-SNE / clustering / faceting) |
| Analysis | **Spatial** | — (neighbourhood / colony-level spatial analysis; §5.11) |
| Images | **Image Browser** | Segmentation / Image Table / Cell Browser / Playground |
| Images | **smFISH** | — |
| Data | **Review CSV** | — |
| Data | **Saved Plots** | — (the plot gallery; decorates every plot card with a "save to gallery" button) |
| Data | **Batch Export** | — |

**Plotting** is a `NamedPageStack`; **Image Browser** (`QTabWidget`: the unified
home for the disparate image-review surfaces — the review-image **Segmentation**
panel, the **Image Table** grid, the filtered **Cell Browser** gallery, and the
**Playground** single-frame correction testbed — each sub-tab body + sidebar
lazily built on first show; see `features/image_browser/{tab_view,controller}.py`)
and **Home** (`QTabWidget` led by Overview — the dataset's front page, §5.3 —
followed by the editors where the user declares what the wells mean) also nest.
`WellViewerApp._current_centre_tab()` descends through the Plotting *and* Image
Browser levels so the rest of the app can pattern-match on a leaf name (e.g.
`"Bar Plots"`, `"Segmentation"`) — that one-level descent is exactly what keeps
the `on_tab_change` leaf dispatch working unchanged after the consolidation.
It deliberately does **not** descend into Home: every one of Home's tabs resolves
to `"Home"`, so the page-level predicates (`per_well_selection`, and
`selection_controller._WELL_EDITING_TAB_NAMES`, which keeps the rail plate an
active per-well selector when groups exist) apply across the whole page —
Overview included, where the plate's clicks already select wells. Home's own
sub-tab handler does the per-tab work a descent would otherwise dispatch, so a
Home sub-leaf's `on_activate` would never fire and none declares one. The one
place the page-level answer is not good enough is the help drawer: standing on
any Home tab resolves to `"Home"`, so `allwell.AllWellApp._review_help_section`
asks Home which sub-tab is current and prefers that heading before falling back
to the page's own. Fixing it there rather than in `_resolve_leaf` is deliberate —
descending would flip the per-well predicates above from page-level to tab-level.
smFISH stays its own rail entry (it is not an image-*review* surface), following
the standard one-package-per-tab layout
(`features/smfish/{controller,worker,tab_view}.py`).

**Home is the landing page, and registry order is the whole mechanism.**
`build_centre` adds pages in the order `_features()` declares them and then calls
`setCurrentIndex(0)`; Home is the first descriptor, so it is page 0. There is no
special case anywhere, which is the point — a page that had to be *named* as the
landing page would be a second place to keep in step.

That has one consequence which is not obvious and is invisible to the fast lane.
`win._plotting_notebook` is assigned inside Plotting's builder, and Plotting used
to be built eagerly only because it held the first slot (`group_idx == 0 and
tab_idx_in_group == 0`). A dozen modules read that attribute, so Plotting is now
force-built explicitly beside the existing `Batch Export` pre-build hook, after
the group loop and before `setCurrentIndex(0)`. Startup cost is unchanged —
Plotting was eagerly built before — and the hook also covers a build profile
whose first group is empty, where nothing would otherwise be built eagerly at
all.

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

#### The Plotting control band

Below the plot-type tab bar, Plotting's controls are a single
`widgets.control_band.ControlBand` — the shared band described in §7. Scope
controls go in its left zone through `add_scope`, which bundles each label with
its control as one flow item; actions sit flush right. `add_scope` returns the
group widget, and `centre_view` keeps those on `app._plotting_channel_grp`,
`_plotting_metric_grp`, `_plotting_thresh_frac_grp` and `_plotting_scatter_grp`
so the sub-tab handler can swap which ones are in the row.

The Channel and Property combos are `widgets.labeled_combo.LabeledComboBox`
rather than a bare `QComboBox`, so each is one self-describing widget — the
closed box reads "Channel: RFP" / "Property: Mean Intensity" — instead of a
separate `QLabel` caption beside a plain combo. `add_scope` is called with an
empty label for both (`""`), which adds the combo alone rather than wrapping it
in a `ControlBandGroup`; the widget paints its own prefix in `paintEvent` and
leaves `currentText()` / `currentIndex()` / every Qt signal reporting the plain
item value, so nothing downstream that reads the combo's value needed to
change. The Threshold stepper keeps its separate `add_scope("Threshold", …)`
caption — `LabeledComboBox` is a `QComboBox` specialisation, and folding a
static label into an editable numeric field isn't the same operation.

Threshold is added with **`trailing=True`**, which pins it to the end of the
scope zone whatever order its tab built its controls in. It is the one scope
control that *narrows* the data rather than choosing which data is shown, so it
reads last; but `add_scope` appends in call order and each tab's builder runs in
its own order, which left Threshold mid-row with Timepoints after it. The flag
keeps the rule with the control instead of asking every builder to remember to
call it last. `FlowLayout.move_to_end` does the reordering — `removeWidget`
followed by `addWidget` cannot, because Qt deletes the `QWidgetItem` that
`takeAt` returns and the re-add would then operate on a dead object.

The stepper is also pinned to `stepper.control_row_height()`, which measures a
`LabeledComboBox` rather than hardcoding a number, so the band's controls share
one height through any theme or font change. Left to itself a `Stepper` is
taller — its two stacked step buttons each want a full text row, which came to
35px against the combos' 25px and made the row read as ragged. The measurement
has to use the same subclass the band uses: a bare `QComboBox` is 3px taller,
so probing one reproduced the mismatch the function exists to remove.

A `Stepper` carrying a prefix or suffix draws it inside its line-edit, so the
validator on that edit is `stepper._AffixValidator`, which strips the affixes
before delegating to the `QDoubleValidator` underneath. Validating the whole
line instead makes every *displayed* value invalid the moment an affix is set,
and `QLineEdit` only reverts an edit that takes a field from valid to invalid —
an already-invalid field accepts every keystroke. The affix therefore converted
"input refused" into "input accepted, then silently discarded on commit". The
affixes are matched without their own padding, because deleting the digits from
`20 spots` leaves `" spots"`, which is the state the box passes through every
time a value is typed over. No `Stepper` in the app carries an affix today —
the ON-cutoff quick-edit takes its unit from the caption beside it rather than
repeating it inside the field — so this is the widget honouring its own API for
the next caller, not a live code path.

The five sub-tabs share that one band; none builds its own. A sub-tab claims a
`ControlBandSlot` through `ui_helpers.plotting_band_slot(ctx, title[, mode])` and
adds its controls to it exactly as it would to a band — the slot mirrors
`add_scope` / `add_action` and is a real widget, so the sub-tab can still parent
popover contents to it. `ControlBand.show_slot` then shows one sub-tab's
contributions and hides the rest. Scatter is two leaves behind one sub-tab, a
Cells page and a Wells page, so its slots are named per mode and
`scatter/tab_view._show_mode_controls` swaps them — guarded on scatter being the
sub-tab on screen, since the mode is also set from a restored session and from
the build itself.

Two ordering rules make that work. Controls the whole page scopes by are added to
the band when the page is built, before any leaf exists, so a leaf's controls
always follow them in the row. And a slot's items arrive **hidden** unless that
slot is the current one: `QLayout.addWidget` shows what it is handed, and
sub-tabs build lazily, so a sub-tab built while another is on screen would
otherwise drop its controls into the visible row and leave them there.

The alternative — a page band carrying the shared controls with a per-sub-tab
band beneath it — is what this replaced. It put two full-width control rows above
every plot, 97px of chrome where the row now costs 53.

That swap is what the band buys on the **Scatter Plot** sub-tab. Scatter has
per-axis channel and property selectors of its own, so the global
Channel / Property / Threshold controls do not drive its plot; they are removed
from the band while Scatter is current, and a `Cells | Wells` **View** toggle
takes their place. The band therefore only ever carries controls that do
something.

The View toggle lives in the band, which is core, while the Scatter page is a
feature. They are wired through `ctx.runtime.scatter.set_mode` and the widget
registry, never an import: the band calls `set_mode`, and `build_scatter_tab`
mirrors the toggle back through `ctx.widgets.get_or_none("_plotting_scatter_view")`
so a mode set from elsewhere — restoring a saved view calls `set_mode`
directly — cannot leave the toggle showing the other mode. `ctx.state.scatter.mode`
stays the record of which mode is live.

Scatter's own page is a bare `NamedPageStack` with `cells` and `aggregate` pages
and no tab bar of its own; the band's toggle is its only selector.

#### Other tabs on the band

**Heat Map** puts its Aggregation combo and its `_heatmap_tp_cb` Timepoint
combo (a `LabeledComboBox`, painting its own "Timepoint: 4.0h" — was a
`StyledSlider` until unified with every other Timepoint control in Review) in
the scope zone, and `Display ▾` / `Export ▾` / Properties in the action zone.
Everything about how the grid is *coloured* — colour map, scale, vmin/vmax,
transpose, rep-set averaging, log scale — is appearance rather than scope and
lives in the `Display ▾` popover. The tab no longer wraps its row with
`ui_helpers.wrap_ctrl_row_with_properties`, since that helper exists to
right-align a button a `FlowLayout` cannot, which is what the band's action
zone already does.

**Statistics** states the same five selectors — Channel, Property, Statistic,
Timepoint, Test — as scope groups, with **Run test** as the single action. It
had been a five-row label/field `QGridLayout` with Run on a sixth row and a
separator under it: a form, in a pane where every other tab has a control row.

Every other plot tab — **Bar Plots**, **Line Graphs**, both **Scatter** views,
**Distribution**, **Spatial** and **Multivariate** — is on the band too, and
three of them lost a whole row to it. Spatial's second row held colony-detection
and summary parameters; Multivariate's held the per-mode option group and the
timepoint filter. Both are conditional: only the groups the current (level, viz)
or mode consumes are visible, and a flow layout skips a hidden widget, so those
groups now live in the band's scope zone and the row carries a handful of live
controls rather than a second row of mostly-hidden ones. `ui_helpers.make_properties_button`
replaces `wrap_ctrl_row_with_properties` at every one of these call sites.

**Playground** keeps every control it had; what changed is which side they are
on. Its column moved from the left to the right and took the `ExportStyleSidebar`
chrome — `#PropertyPanel` fill, a hairline down its left edge, and
`theme.Metrics.dock_w`, which is now the one width for every right-hand dock in
the pane (the Properties sidebar reads the same token). A per-tab inspector on
the left, when every other one in the app is on the right, is the kind of
inconsistency that costs a user a second on every tab switch.

The image and data tabs follow. **Segmentation** puts its well caption, channel,
FOV, timepoint, zoom cluster and ON-threshold quick-edit in the scope zone and
its Delete / Delete scope ▾ / Crop cells / Crop scope ▾ / Edit ▾ / View ▾ /
LUT ▾ / Colors ▾ controls
in the action zone. **Delete scope ▾** and **Crop scope ▾** are the same control
(§ the frame-scope table row below); each sits next to the button it modifies and
carries its own readout in its label ("Delete scope: this frame", "Crop scope:
2 wells · all FOVs · 1 TP") — a scope stays armed across gestures, so it has to
be legible without opening anything. **Cell Browser** does the same with its channel chips and page size against
LUT ▾ / Display ▾ / Load next / Generate. **smFISH** and **Review CSV** each
collapse from two rows to one — selectors left, buttons right, which is the split
they already had, stacked instead of side by side. Zoom on Segmentation goes in
as a single scope group of four widgets, so a wrap can never separate the
readout from the buttons that drive it.

One `TabCtrl` control row remains, and it is deliberate: Home's Cell
Gating frame is inside a tab, not a centre tab's control row. That page's own
Import button is an action on its band — it used to be a bespoke toolbar row with
a separator under it, a whole strip of chrome, which started every one of its
sub-tab bodies 52px below where the other pages' content starts. It is labelled
rather than left as a bare glyph: the band renders level with the Cell Gating
sub-tab, so an unlabelled icon there reads as an action on cell gating rather
than on the page.

**Image Table** is the deliberate exception: its controls stay split between the
builder shelf and an action strip directly above the images, because the strip's
crop and ON-threshold controls act on the images below them and belong beside
them. The strip is consolidated *in place* to one row — `Reset table` and
`Clear images` became one `Table ▾` menu, the loose `(full FOV)` caption folded
into the Crop button through `CropTool.set_status_widget`, and the `Zoom:`
caption went, since the `100%` readout between the − and + buttons already says
what they do.

#### Plot cards without a header

`PlotCard(header=False)` does not build the header at all, rather than building
it and hiding it. Every tab on the band passes it: the band and the pane's tab
bar carry the view switcher, the Screen/Publication toggle and the stats chip,
so the header had nothing left to show and was hidden at every call site. The
header accessors (`setFigureTitle`, `setStatsChipVisible`, `setLeftHeaderWidget`,
…) are no-ops on a headerless card rather than raising, so a caller need not
branch on how the card was built; `hasHeader()` answers if one needs to.

`make_plot_view_switcher` is gone with them — it built a five-segment
Line/Bar/Scatter/Dist/Heat control into each card's header, directly beneath the
tab bar offering the same five choices.

A tab that pins a minimum height for its figure adds `card.chromeHeight()`,
which measures every pixel of the card that is not the canvas. That total is not
a constant: it depends on the theme's spacing, on whether the controls row holds
anything, and on whether the card has a header. Bar Plots and Line Graphs used
to add a literal `110` commented "header / controls / toolbar rows" — the kind
of number that goes stale silently, giving the figure a little more or less room
than the tab asked for.

#### The rail head

The window's left column is one thing from the top down. `app_shell_view.RAIL_W`
is the single statement of the Review rail's width, and the mode bar starts
there; it fills the header strip's height (`ModeBar.setFillHeight`) and sits
flush, with no inset — the topbar's left content margin is zero and the widgets
after the bar carry their own leading pad, so the bar and the panel below it are
one column rather than a chip row floating over one.

All three modes put a panel down the left edge, and all three pin to the same
`RAIL_W` — Review's rail, the Datasets sidebar, and Analyze's form panel (fixed
there too, not merely a minimum inside a resizable splitter) — so switching
modes never visibly resizes the window. `AllWellApp._sync_mode_bar`
still matches the bar to whichever panel is current, re-running on every mode
switch and on any resize of the three panels it watches, as a defensive floor
rather than reconciling genuinely different widths: it floors at the bar's own
natural width, so a panel narrower than the three segments elides their labels
rather than shrinking the bar.

Because the bar and the panel are one column, they collapse together. The same
method hides the bar whenever the rail is collapsed (`Ctrl+B`, or the button
floating on the rail's edge), and the page tab strip then runs to the window's
left edge. It is driven off the panels' own show/hide events, so it follows
both routes into `tab_controller.apply_sidebar_visibility` without either of
them having to know the bar exists. Left behind, the bar was a rail-width band
of chrome hanging above an empty column, squeezed to a natural width its three
labels do not fit inside. Re-expanding the rail brings it back — which is also
the only way back to Analyze and Datasets while collapsed, since the bar is the
app's sole mode switch.

The band's right end carries the dataset action cluster — reload, presentation
mode, full reset of saved state, reveal the dataset folder, and reset the view —
and, outboard of it, the help-drawer button. They are built in `AllWellApp`
alongside their handlers and then moved into the band by
`_mount_review_actions`, which appends them after the page-tabs host; that host
holds the band's only stretch, so both are pushed hard right whatever the tab
strip is showing, and the band's pinned height rises to whatever the taller of
the two needs rather than clipping them. Every one of the five acts on the
loaded dataset or on the plots drawn from it, so `_sync_review_actions` shows
the cluster in Review alone and hides it in Analyze and Datasets, where each
would be a control with nothing behind it. Both mode-change handlers call it, so
the cluster follows the seg and the pane stack alike.

The help button is deliberately not one of them, which is why it is mounted
after the cluster rather than inside it: the manual it opens documents all three
modes, and it keeps the band's rightmost slot in every mode while the cluster in
front of it comes and goes. Anything placed inside the cluster inherits the
Review-only gating, and for this control that would leave Analyze and Datasets
with `Ctrl+/` as their only route to the drawer — a shortcut nothing on screen
advertises once the band's right end is empty.

Opening the drawer aims it at the page the user is on.
`_help_section_for_current_page` branches on the current mode and asks that
mode's pane which of its own pages is showing: Review resolves its leaf tab
through `tab_controller.current_centre_tab`, `DatasetsTab.current_help_page`
returns the rail key of the tool page, and `AnalyzeTab.current_help_section`
answers from focus and scroll position, since the Analyze pane is one tall form
rather than a stack of pages. Its sections are recorded by `_section` in build
order in `_section_widgets`, and the lookup reads two signals in order: the
section containing the focused widget, then — among sections with any part of
themselves on screen — the one whose *header* is nearest the viewport top.

Both halves of that second rule are load-bearing, and the two obvious phrasings
are wrong in mirrored ways. The scrollbar's travel is the form's height minus one
viewport, so at the end of the form every remaining header sits *below* the
viewport top: "the last header above the top" names a section several screens out
of sight. And a section whose body is taller than the viewport still clips the top
of it by a few pixels long after the user has scrolled past everything it says, so
"the first section with any part on screen" names the one they have just left.

Geometry alone cannot name the short sections packed into the form's final
screenful — they are all visible at once, and no scroll position singles one out.
That is what the focus branch is for, and it is the only route to those sections'
help; `tests/test_analyze_help_section.py` asserts focus reaches every entry in
`ANALYZE_SECTION_HELP`, not a subset.

`help_topics.py` holds the maps from a page to the exact manual heading that
documents it — `SECTION_BY_PAGE` for Review leaves, `ANALYZE_SECTION_HELP` for
form sections, `DATASETS_PAGE_HELP` for rail labels, and `MODE_OVERVIEW` for the
fallback each mode uses when its pane cannot say. It is Qt-free so
`tests/test_help_topics.py` can check every entry against the real
`README_WellViewer.md` in the fast lane: every value must be a heading that
exists, every Datasets tool and Analyze section must have a key, and no key may
name a page that no longer exists. `tests/test_guards_bite.py` carries that
guard's bite cases, since `scripts/prove_guard.sh` cannot reach a guard whose
maps live in a module the base ref does not have — the run there dies importing
`help_topics` before it measures anything.

The action cluster sits on the band
rather than in the window header a row above because that put a second strip of
chrome across the top of the window carrying controls for a mode the user might
not be in. An earlier pass set `RAIL_W` to whichever mode's content needed the
most room (Analyze's, at 438) and let the other two panes — neither of which
needs anywhere near that width — inherit it unchanged; the result read as an
oversized sidebar with no content to justify it in Review and Datasets.
`RAIL_W` is now 329 (438 reduced 25%) with Analyze's own form narrowed to fit
it instead: `analyze_tab_form.py`'s row label column shrank
(`_row()`'s `_ROW_LABEL_W`, 150→108) and word-wraps instead of clipping a
label that doesn't fit on one line, its two widest fixed-width combos
(segmentation method, auto-threshold method) shrank to 165 and rely on Qt's
built-in text eliding plus a tooltip carrying the full label, and the Compute
Options page's inline "(…)" hints moved to their own wrapped line below the
row instead of squeezing in beside a narrow field. `tests/test_analyze_narrow_sidebar_fit.py`
drives every collapsible section open (a real `QTest.mouseClick`, since a
collapsed section contributes nothing to the form's measured width until
expanded) and asserts the form's laid-out minimum width fits inside `RAIL_W`.

**The page tab bars are in this band too**, to the right of the mode bar and
sharing its bottom edge. They used to sit inside each tab's own body, one row
below, so the top of the window was two stacked strips of chrome; they are the
same kind of control as the mode selector, one level down, and belong beside it.
Each bar is **bottom-aligned** inside its page rather than filling it. A
`QTabBar` sizes its tabs from the font and its padding and lays them from the
top of whatever height it is given, so stretched to the band's height it drew
its selected-tab underline part-way up — eight pixels clear of the band's bottom
edge, while the mode bar's fill ran all the way down to it. Pinning the bar to
its own height and pushing it to the bottom puts the underline on the band's
bottom edge, level with the active mode segment beside it, at any font size or
band height.

`build_page_tabs_host` is the slot — a `QStackedWidget` keyed by top-level page
name, whose page 0 is blank and is what the six pages with no sub-tabs
(Statistics, Spatial, smFISH, Review CSV, Batch Export, Saved Plots) show, and
what **Analyze and Datasets** show: the strip names Review's pages, and left
carrying whatever Review was on it offered Bar Plots / Line Graphs / Heat Map
beside the Analyze form. `show_page_tabs` gates on the current mode for that. The
band keeps its height there rather than collapsing, so switching pages never
shifts the content below it. `register_page_tabs` mounts a bar and
`on_notebook_current_changed` swaps them; because pages build lazily a bar can
register *after* its page is current, so registering also shows it.

Plotting's bar is a standalone `QTabBar` and simply moves; so is Batch Export's
Line / Bar / Scatter (cells) / Scatter (aggregate) selector, which is a page
selector like any other and reaches the band the same way. The other two — Image
Browser's and Home's — are `QTabWidget`s whose own API
(`tabText` / `currentIndex` / `count` / `addTab` / `currentChanged`) is read by
controllers, persistence and eight test modules, so `mirror_tab_widget` hides
each one's own bar and drives it from a `QTabBar` in the band instead.
Converting them to a bar plus a page stack would have broken every one of those
readers to change where a strip of tabs is drawn. Hiding the bar also means the
pane border around the body has no frame left to close, so `mirror_tab_widget`
puts the widget in document mode and `theme.py` clears
`QTabWidget#ImageBrowserSubTabs::pane` / `#HomeSubTabs::pane` —
otherwise that border's top edge starts every nested body a pixel below where an
un-nested page's body starts.

The band is pinned to `tab_bar_height()` and carries no vertical padding of its
own, and holds nothing but the mode bar and the current page's tabs.

The **collapse toggle** is not in the band. It floats on the left panel's own
right margin, straddling the boundary with the centre pane
(`_build_rail_toggle`, `position_rail_toggle`). It is parented to the **window**
and positioned by hand, which is the whole trick: a toggle inside the rail would
be hidden along with it, so the one control that collapses the rail would be the
one control the collapse takes away. Floating it over the boundary keeps it
reachable in both states — it slides to the window's left edge once the rail is
gone. Straddling rather than sitting inside the rail's content also keeps it
clear of the dataset head and the plate picker.

The same toggle collapses all three modes' panels, so it has to find the one on
screen rather than assume Review's rail: `visible_left_panel` returns the first
visible of the rail and the Analyze / Datasets panes' `_left_sidebar`, and the
edge is **mapped** into window coordinates rather than read off `geometry()` —
the button is an overlay on the window while the panels sit at different depths,
Analyze's and Datasets' inside their own pane's splitter rather than the shell's.

It is **vertically centred on whichever panel is showing**, in all three modes
— a user scanning down the panel's right edge for it finds it at the same
predictable spot regardless of mode or what that panel currently contains,
rather than pinned near the top the way an earlier "level with the rail head"
placement read as stuck. Review's sidebar carries an 8×12 well-picker plate
near the top of its content, and true centring lands on it at common window
heights — turning a click-and-drag well selection into an accidental sidebar
collapse. `position_rail_toggle` detects that specific overlap against
`win._sidebar_plate`'s own bounds and nudges the toggle just below the plate
instead of covering it; Analyze and Datasets have no such widget to dodge, so
there it's a plain panel-height centre. The rail head still carries an `xl`
right inset so its caret has room regardless of exactly where the toggle
lands.

A mode switch re-parks it twice: once immediately, and once through a
`QTimer.singleShot(0, …)`. The first call runs while the pane being shown still
carries the geometry it had before it was laid out into the stack, so it parks
against a width that is about to change; the queued call sees the settled
layout. `position_rail_toggle` also installs the placement filter on whichever
panel is current, so a splitter drag re-parks it too — re-installing an
already-installed filter only moves it to the front, which is why that is safe to
run on every reposition rather than tracked.

Two things follow from it being an overlay. It has no layout to size it, so
`position_rail_toggle` calls `adjustSize()` — left at Qt's default a widget with
no layout keeps 100×30 and paints its chrome around an off-centre glyph. And it
needs its own fill and border in `theme.qss` (`#RailToggle`), unlike every other
`IconButton` in the app, because there is nothing behind it: without them it is
a glyph hanging in mid-air over whichever pane is underneath. `_RailTogglePlacement`
re-parks it on every window resize, and `apply_sidebar_visibility` re-parks it on
every collapse and mode change.

The mode bar is a `ModeBar`, not the `SegmentedControl` the rest of the app uses
for view toggles. Review / Analyze / Datasets are three applications sharing a
window; switching replaces the whole interface, and the control says so at rest
by keeping every segment filled (§7).

Directly beneath it, as the rail's own head, is the **dataset control**:
`app_shell_view._dataset_button`, full rail width, naming the loaded folder and
carrying `Open…`. It replaces both the `SECTION` caption (the rail's items are
self-evidently sections) and the `Open…` button that sat alone at the far end of
the topbar — the action and the thing it acts on are now in one place, and the
loaded dataset finally has a durable on-screen identity rather than living in a
hidden label and the OS window title. `refresh_dataset_button` is called from
`load_controller` on every load. Long names elide in the **middle**, because
dataset folders differ in their tail as often as their head
(`plate3_run2_2024-06-11` against `…-12`) and a right-elided name can leave two
datasets indistinguishable; the full path is on the tooltip.

The name shown is not always `dataset.data_dir.name`. The standard
Analyze-pipeline layout loads the sibling `out/` folder as `data_dir` (the
`in/` + `out/` split `load_path` detects), so a naive last-path-component read
would show the literal string "out" for every such dataset — useless for
telling two datasets apart. `refresh_dataset_button` detects that layout
structurally, the same way `load_path` does (`data_dir.name == "out"` and a
sibling `in/` folder exists), and climbs one level to the dataset root's own
name in that case; a folder that happens to be named `out` but has no `in/`
sibling — a genuinely flat, non-split dataset — keeps showing its own name
unchanged. The tooltip still shows the literal `data_dir` path that's actually
loaded (the `.../out` path in the split case), not the climbed display name.

#### Empty states and build failures

`ui_helpers.wrap_with_empty_state` wraps every centre tab whose body needs a
dataset — Home, the plots, Statistics, Spatial, Review CSV, Batch Export and
smFISH — so a tab with nothing to show renders the same placeholder rather than
an empty table, a blank panel, or a bare matplotlib canvas that reads as a
failure. The wrap registers a `QStackedWidget` on `win._plot_empty_stacks`
(page 0 the `EmptyState`, page 1 the body) and the refresh helpers flip all of
them together, which is what keeps the wording and the moment of the swap
identical across tabs.

**A placeholder may carry one action.** `EmptyState.setAction(label, on_click)`
adds a button under the hint; without it there is no button at all, which is
what every site but Home wants — their hint names a control somewhere else and
a button would be a second way to say it. Home is the page the application opens
on, so its placeholder is the first screen and gets an explicit *Open a
dataset…*. The refresher rewrites only a placeholder's text, hint and icon, so
an action set once at build time survives every pass.

**The placeholder says which "nothing" it is; the axes carry per-plot causes.**
Each wrap site may pass a `reason(ctx) -> EmptyReason | None`, called on every
refresh, where `None` means the tab has something to draw. An `EmptyReason`
carries a title, a hint and optionally a glyph, which are written onto that
stack's own placeholder — one widget, two pages, no third page and no second
placeholder per tab. **`no_dataset` short-circuits ahead of every site reason**:
with no dataset loaded every stack shows the shared "Load a directory." copy,
because asking someone to pick wells on a plate that is not there is a worse
message than the one it would replace.

`selection_controller.has_plottable_selection` is the single predicate behind the
shared `no_selection_reason` — wells picked, or a visible replicate set. Four
renderers each spelled that condition out for themselves before it existed.
Messages about *other* empty causes — no timepoints, no channel data, no feature
columns, no smFISH/mask pair — stay on the axes, because the placeholder cannot
know about them. `tests/test_no_axes_selection_message.py` enforces the split: no
interactive renderer may paint a message that *asks for a well selection*, and it
reads module constants and the message-drawing helpers as well as direct
`ax.text` calls, because that is how the messages it replaced actually reached
the canvas. Export renderers are exempt per file, since a figure written to disk
carries no placeholder with it and must state its own shortfall (§7).

Two tabs need reasons of their own rather than the shared one. **Heat Map is not
selection-driven**: `redraw_heatmap` fills from `layout.cells` and reads the
selection only to outline cells, so gating it on the selection would hide a
populated saved map the moment the plate was cleared. It gates on an empty
layout — which is the common case rather than a corner, because
`_ensure_sidebar_layout` creates an empty `Sidebar` layout while the sidebar
builds and leaves `active_layout_name` unset, so `active_layout` returns it and
the populated `make_plate_layout` default is never reached. **Spatial gates only
at its three per-frame levels**, where an empty selection otherwise renders the
first key of `well_paths` — one arbitrary well, unannounced. Its Summary level
keeps falling back to every loaded well, which is a defensible default rather
than an arbitrary pick.

Statistics uses the same widget on its own trigger (fewer than two comparison
groups — one group cannot be compared either), so a
user moving between pages reads one treatment rather than two. Batch Export
deliberately does not: the widget wrapped there is the whole panel including
OUTPUT SETTINGS and the Run button, and swapping that for a placeholder would
hide the output configuration exactly while it is being set up.

Two Image Browser sub-tabs are dataset-gated but **not** selection-gated, and for
different reasons. **Image Table** has its own picker plate, so the app-wide
selection does not drive it; its placeholder is generation-gated and lives
outside `_plot_empty_stacks`, so `image_table_refresh_empty_state` swaps its two
strings instead — with a dataset it asks for wells and channels and a Generate,
and without one it says what the rest of Review says, because the picker has no
wells to choose and its own Channels panel already reads "load a dataset to see
channels". **Cell Browser** mirrors the plotting layer's active wells rather than
reading the plate directly and is scoped by its own criteria / timepoint / FOV
filters, so it takes the plain dataset gate; before that it had no empty state at
all, only a blank grid with its explanation in the status bar.

Both build through `image_browser/controller.build_image_browser_subtab`, which is
the Image Browser's **own** lazy funnel and does not pass through
`centre_view._build_pending` — so it repeats the same `refresh_plot_empty_states`
call. That was latent for as long as no sub-tab registered a stack; the moment
Cell Browser got one it reproduced the Spatial / smFISH bug in a new place.

**A reason is only as current as its last refresh, and that is the rule's sharp
edge.** A reason may read any state, but nothing re-evaluates it on its own. The
app-wide triggers cover app-wide state — dataset load, lazy build, selection
change — so a reason reading *feature-local* state must be re-run from that
feature's own funnel, or the placeholder is decided once and then stands over a
page that has something to draw. Heat Map's placeholder says "click Auto-populate
from selection"; doing exactly that filled the layout and left the placeholder up,
because `_persist_and_redraw` is the only thing that knows the layout changed.
Spatial's reason is level-dependent and nothing re-ran it when the level changed.
So `redraw_heatmap`, Spatial's `_sync_empty_state` (called from every
`render_spatial` path), Cell Browser's `apply_filters` and
`stats_refresh_group_list` each end by calling `refresh_empty_state_pages`. When
you add a reason, the question to answer is *what changes the answer, and what
runs then*.

The other way out of that trap is to make the reason read state no build order
can move, and **smFISH is the worked example**. Its reason asks whether the run
had any smFISH channel, and the tab holds an answer to that in
`ctx.runtime.smfish.tokens` — but that list is the tab's own scratch: the builder
sets it to `[]` and `smfish_sync_from_app` fills it in afterwards, with the
lazy-build refresh between the two. Read from there, the reason was computed at
the one moment the list is guaranteed empty, so the first open of the tab told a
dataset full of spot counts that the pipeline had generated none, and any later
refresh cleared it. It reads `ctx.state.dataset.pipeline_info["smfish_tokens"]`
instead — parsed by the loader before any tab is built, so the ordering question
does not arise. `ctx.state.channel.smfish_channels` is the trap next door:
`recalculate_threshold` overwrites it with channels *detected from
`_smfish_count` columns*, so a freshly-processed dataset with smFISH images but
no counts yet would be told it has no smFISH data — which is the tab's primary
use case, since generating those counts is what it is for. The tab's two blank
states are also distinct strings: "the run had no smFISH channel" is a claim
about the analysis, "the sidecar could not be read" is a claim about a file, and
printing the first for both sent users to re-run an analysis that was fine.

**Where the refresh is called from matters as much as the rule.**
`refresh_empty_state_pages` is the cheap half — one reason call and a
`currentIndex()` comparison per stack — and runs from `apply_selection_refresh`,
the sole `selection_changed` subscriber, *before* its per-tab loop: that loop
only touches `_visible_tabs`, and a feature with an `on_selection_changed` hook
never reaches `plot_orchestrator.redraw_app`, so a background page would
otherwise keep whatever state it held when last seen. `refresh_plot_empty_states`
adds the dataset-gated greying of `TabCtrl` / `ControlBand` containers, which is
two recursive `findChildren` walks and answers a question only a dataset load can
change. It is also called from `centre_view._build_pending` after a deferred body
is built: a `lazy_only` tab registers its stack *after* the load-time refresh, so
without that call Spatial and smFISH showed "Load a directory." over a loaded
dataset for the life of the session.

An actual failure says so. A deferred tab builder that raises used to log a
traceback and leave an empty page — indistinguishable from a tab with nothing to
show, with the explanation in a drawer the user has no reason to open.
`centre_view._build_pending` now also puts the tab's name on the status bar,
where every other failure in the app reports itself.

#### The page body: where a page's first row is drawn

Every main page in the app — each Review page, the Analyze pane's output side,
the Datasets pane — has the same body: a `ControlBand` flush against the top of
the page, one row tall, then the page's content directly beneath it.
`widgets/page_body.py`'s `build_page_body(parent, *, content_margins,
horizontal)` lays that out and returns `(band, content host)`. It strips the
page root's own margins and spacing, and there is deliberately no top-margin
parameter: left, right and bottom padding are a page's business (a canvas wants
a gutter, a table does not), but the top is what alignment is made of.

**The band is added whether or not the page has controls to put in it.** Image
Table and the Playground author through a builder shelf and a right-hand dock,
Batch Export selects its mode from the shell band and configures inside its
panels, the Datasets tool pages carry their Run controls down inside each page —
each still reserves the row, so its content starts level with its neighbours'.
That is the same reservation the mode band's page-tabs host makes for pages with
no sub-tab bar (§5.1).

`ControlBand.ROW_H` (28px, `band_height()` == 44 with the band's own padding)
fixes what one row measures. Without it the height came from whichever control
the page happened to hold — Qt sizes a `QPushButton` at 24, a `LabeledComboBox`
at 25, a `QToolButton` at 27 and a `QComboBox` at 28 — so an unwrapped band was
41, 43, 44 or 49px tall and moved the content under it by the same amount. It is
enforced as a real `setMinimumHeight`, not as a floor inside `heightForWidth`: a
parent layout asks the band's `QWidgetItem` for its height, and `QWidgetItem`
delegates to `wid->layout()->heightForWidth()` whenever the widget has a layout,
so an override on the class is never consulted.

A band still *grows*: it wraps to a second row when its controls do not fit the
window's width, and one holding a control taller than a row (the
`SegmentedControl` on Multivariate, Spatial and Scatter) is taller than 44px.
Both move only the content below the band, never its top edge.

`tests/test_page_alignment.py` measures the rule against a real window with
every page and sub-tab built: each page's band starts at the centre stack's top
in all three modes, no page root re-introduces a top inset, no band is shorter
than one row, and a body nested in a `QTabWidget` starts at that widget's own
top. That last one is why `mirror_tab_widget` sets `setDocumentMode(True)` and
`theme.py` clears the pane border for `ImageBrowserSubTabs` and
`HomeSubTabs` — with the bar hidden, `QTabWidget::pane`'s border
closed a frame that is not drawn and started every nested body a pixel low.

#### The band's gating and its ratchet

The band carries the object name `ControlBand`, which is in
`ui_helpers._DATA_GATED_OBJECT_NAMES`, so a tab picks up "grey the controls
until a dataset is loaded" by adopting the band rather than by remembering a
name. `tests/test_control_band_adoption.py` is an AST ratchet over the modules
that have adopted it: one control row per module — a `ControlBand`, a
`build_page_body` or a `plotting_band_slot`, never two of them — no hand-rolled
`FlowLayout` control row beside it, and no plot-card header built only to be
hidden. It also asserts the specific shape of the Plotting page: every one of
its five sub-tabs claims a slot and none constructs a band; and that every
module building a top-level page's tab strip (Plotting, Image Browser, Home,
Batch Export) hands it to the shell band rather than to its own
body. It is a static scan on purpose — a test that builds a tab needs the
`viewer_app` fixture, which `conftest.py` auto-marks `slow`, and
`scripts/test.sh` runs `-m "not slow"`, so such a test would never run in CI.
The geometry itself is checked in the slow lane by `test_page_alignment.py`
above, which builds the window once.

#### The Home page

`features/home/` is the page Review opens on, and it is a notebook. **Overview**
leads it and is everything described below: the report the app opens on. The
tabs after it — **Cell Gating**, **Groups**, **Well Labels**, **Ratios**,
**Pipeline Info** — are where the user *declares* what the wells mean. The two
halves are one page because they are one subject: what this plate is, and what
you have said about it. Splitting them across two rail entries is what put a
Notes box on each and needed a mirroring helper to stop one writing back over
the other.

Home owns the page — the control band (carrying **Import**, which merges a
sample-definitions JSON and is hidden on Overview because it names nothing
there), the `QTabWidget`, and the tab-change handler. Each tab body is a
sub-package under `features/home/`, the same arrangement
`features/image_browser/` uses for the three of its four sub-tabs that have
one (Segmentation's body is `review_image/`, shared with the Review CSV jump).

**Home does not name its tabs and does not import their bodies.**
`active_subtabs` is `("Overview", *ctx.features.sub_leaves("Home"))` — the
registry's declaration order, already filtered by the active profile — and each
tab is built by calling its own descriptor's `build(ctx, frame)`. There is no
name ladder and no import of a sibling package. Home used to filter a module
constant through `Profile.includes_subtab`, a bare string check against the
`[subtabs]` allowlist that never consults descriptors, so a manifest could drop
tabs nothing had declared; enumerating from the registry is what makes every
name a `[subtabs]` line can reference a real descriptor.

Two of the five builders do not have the `(ctx, frame)` shape and are adapted in
`features/registry.py`: Cell Gating's takes no frame (it finds its own, because
persistence can build the tab with no frame to hand over), and the Ratios editor
is *returned* rather than mounted. Both adapters are function-local imports
inside the descriptor, which is what keeps the removable sub-packages removable.

Two more per-tab facts are descriptor fields rather than name lists in the Home
package: `reads_well_selection` decides whether the rail plate is live while that
tab shows (Groups edits a group's membership with it, Cell Gating draws its CDFs
from the selected wells, Well Labels' prefix/suffix apply to them; Ratios and
Pipeline Info ask nothing of it), and `subtab_inset` decides whether the tab's
frame takes a gutter. Both were constants keyed by tab name, which is issue #14's
shape in miniature — a tab added to the registry and not to the constant inherits
whichever answer the constant happens to give it, silently. `reads_well_selection`
is finer than `per_well_selection`: that one is resolved per *page*, and Home is
per-well as a page while only three of its five editors ask the plate anything.

`Cell Gating`, `Groups` and `Well Labels` carry `removable=False`. That is not a
statement of preference: `centre_view.build_centre` builds the rail plate that
edits groups at startup with no descriptor check, so a profile dropping the
Groups tab would leave a plate editing something that is not there. `Ratios` and
`Pipeline Info` are removable, with `[[feature]]` blocks in `profiles/full.toml`.

**Overview is built eagerly and the definition tabs are drained, one per
event-loop tick.** Neither extreme works. Home holds the eager first-tab slot,
so building five editors inline would put all of them on the startup path; but
building them on first click would change what exists after a load, and
persistence hydration, the plot tabs' ThreshFracOn quick-edit and the
Playground's context bar all read those widgets without opening a tab. Draining
keeps both properties, and is what the definitions already had when they were a
page of their own that `centre_view` drained. `build_home_subtab` is idempotent
against `ctx.runtime.home.built`, so a click that arrives before the drain
reaches a tab builds it once, not twice.

The rail's plate follows the tab, in three states rather than two. It is
**hidden** on Overview, which has a plate of its own in the page body asking a
different question. It is **live** on Groups, Cell Gating and Well Labels — the
three tabs that read the well selection, respectively to compose a group, to
draw its CDFs from the selected wells, and to apply Add Prefix / Add Suffix to
them. It is **disabled** on Ratios and Pipeline Info, which ask nothing of it:
leaving it live there put a plate on screen whose clicks had no consequence and
which said nothing about that.

Disabling it required the widget to acquire a disabled appearance.
`WellPlateSelector` consulted `isEnabled()` nowhere in its paint path, so
`setEnabled(False)` rendered **pixel-identically** — grab the widget enabled,
grab it disabled, compare: zero differing pixels. `paintEvent` now ends by laying
a rail-toned scrim over the finished plate, which takes the same comparison to
roughly seven eighths of the widget. The absolute count is deliberately not
quoted: it scales with whatever size the host gives the plate, so it says more
about the measurement than about the widget. A scrim rather than a disabled variant of each fill, because
every state this widget paints — lit, recessed, dial, ring, marker, path — is
carried by *colour*, so draining the colour is what reads as "not active here",
and one veil does that uniformly where ten paired palettes would drift apart.
`setEnabledWells([])` is not an alternative: an empty socket already means "this
well is not in the dataset", and reusing it would make an inactive tab
indistinguishable from a plate whose wells are missing.
`tests/test_disabled_controls_look_disabled.py` carries the roster this class of
defect is caught by, and the plate is on it.

`ctx.runtime.home.subtabs` is what the plate's commit guard and
`selection_controller.groups_centre_refresh` consult to answer "which tab is
showing" — the refresh tests the *tab*, not the page, because Home is what the
app opens on and a page-level test would be true from startup.

The report itself: it answers what a returning user
asks before anything else — what this plate is, whether the run that produced it
finished, what it was run with, what they have already done to it — and it does
so by **reporting a record rather than measuring**. Establishing per-well
coverage costs a zip central-directory read and a full CSV parse per well, which
a landing page cannot pay on every open, and a cached answer that silently ages
is worse than none. The pipeline records the answer at the end of every run
(§6.2, `pipeline_info.json::run_check`), the Datasets **Validate & Repair** tool
records one for a dataset that has none (§6b), and this page reads whichever is
there. Activating the page re-reads that one key from the sidecar, so a scan run
while the dataset is open shows up on returning to Home rather than needing a
full reload; nothing else is re-read, because the rest of the sidecar holds
user-side blocks whose in-memory copy may carry edits not yet saved.
`tests/test_home_collect.py` pins that literally: it makes `open`, `Path.open`,
`Path.iterdir`, `Path.glob` and `zipfile.ZipFile` raise, then calls every
function on the page.

Everything computable lives in `collect.py`, Qt-free and ctx-first, so the whole
page is exercised in the fast lane against a plain namespace; `controller.py`
paints widgets from what it returns and `tab_view.py` holds the arrangement.

**The Run parameters card names the settings that shaped the numbers, and only
those.** A method's name alone is not a receipt: two plates corrected with
`robust_mesh` at different mesh ratios read identically, and an `empty_well`
correction that never says which wells it measured cannot be reproduced. So
`collect._correction_parameter_rows` renders the radius, the FFT small-structure
cutoff, the mesh ratio and sigma, and the reference wells — but each only when
the chosen method actually consults it. Which those are is
`pipeline.background.BACKGROUND_METHOD_PARAMS` and
`pipeline.flatfield.FLATFIELD_MODE_PARAMS`, kept beside the dispatches that
define them so the receipt cannot drift from the code: listing a mesh ratio
beside `sliding_disk`, which never sees one, asserts an effect that did not
happen, and that is the same class of untruth as omitting the ones that did.
`tests/test_correction_parameter_roster.py` fails if a method is added without
an entry, or if an entry names a parameter `subtract_background` does not take.
The two per-channel-role settings (radius, FFT cutoff) print as one value when
the nuclear and fluorescence roles share it and name both when they differ.
A reference-well correction with **no** well named is painted as a warning
rather than an em-dash: no field was built, so every frame passed through
untouched and the output is bit-for-bit identical to a corrected one — the
silent substitution §7 of `CLAUDE.md` is about, and the reason `severity` is
honoured on a receipt row at all.

**Before a dataset is open, the page is not shown at all.** Home's body is
wrapped in the shared `wrap_with_empty_state` stack like every other Review
page, so the same "Load a directory." placeholder covers it and the same
refresher flips it — a landing page reporting on nothing would otherwise open
the application on a masthead reading "No dataset open" above an empty plate and
a column of em-dashes. The one thing Home adds is an action on the placeholder
itself (`EmptyState.setAction`, optional and unused by the other twelve sites):
the hint names a control in the rail, and on the page the app launches on it is
worth also being able to press the thing. The button runs `run_home_action(ctx,
"open")`, the same route the next-step row takes, which reaches the window's own
`_browse` rather than opening a second file dialog of its own.

Home's own "is there a dataset" predicate is `collect.has_dataset`, which tests
the **wells** rather than `data_dir`. The two disagree for the whole duration of
a load — `data_dir` is set when one begins — and permanently for a folder that
opened and yielded nothing readable. Since the placeholder covering the page is
gated on the wells, a page gated on anything else could describe a dataset the
placeholder says is not there.

**Opening a dataset always lands on Home**, whatever page the last session
ended on: the arrival of a *dataset* is the moment a reader most needs to be
told what they just opened, and dropping them onto a plot of a plate they have
not been oriented to is not that. A profile with no Home page falls back to the
saved tab.

That override is opt-in, and only the dataset-load path opts in.
`persistence/view_state.restore` activates the `active_centre_tab` its snapshot
names unless the caller passes `prefer_home=True`, which `load_from_data_dir` —
the one caller that runs on the arrival of a dataset — does. Every other caller
is replaying a view the user asked to see rather than opening a folder, and the
one that exists is Saved Plots' *Restore settings*: it re-applies one figure's
parameters and must leave the user looking at the tab that figure came from.
Sending it to Home instead would apply the whole restored view to a tab nobody
is on, which on screen is indistinguishable from a restore that did nothing.

Two other ways that restore can look like it did nothing. A tab that renders on
demand rather than on every state change comes back with its controls restored
and its picture untouched — that is the Image Table, which does not regenerate
on a restore (§5.3) and instead marks itself stale so the shelf asks for the
Generate. And a record saved before its tab began storing a per-tab block
restores the cross-cutting state and nothing tab-specific:
`features/saved_plots/controller.py`'s `_report_missing_tab_block` says so in
the status line,
gated on `view_state.owns_tab_block`, which separates "this tab keeps no block"
(true of most tabs, and not worth a warning) from "this record has none of the
block this tab keeps" (worth one — the fix is to re-save the plot, and a user
who is told nothing re-clicks Restore instead).

**Every shortfall Home reports offers the same next step: finish the run.**
Failures, partial wells, images without measurements, staged-but-never-processed
— all four route to `run_home_action(ctx, "complete")`, which hands the dataset
to Analyze through the shell's `_open_dataset_in_analyze`, the same entry point
Validate & Repair's *Complete this run in Analyze* uses. That carries three
things a bare folder path does not: the dataset's own recorded sidecar (so the
schema, separator and `csv_prefix` match the run being finished — a mismatched
prefix reads its done-set from files that do not exist and silently reprocesses
the plate), FOV-level incremental mode, and the per-well force list. The last is
`collect.wells_needing_force`, the same rule Validate applies: a well with
images and no CSV has nothing missing by the incremental diff, so it is skipped
forever unless named, while a merely partial well the diff does fix. Sending the
reader to a page that shows them the gap a second time is not a next step.

**A Datasets tool is handed the project folder, not the loaded one.** Review's
`data_dir` is where the CSVs are, which for the pipeline's layout is `out/`;
every Datasets tool wants the folder above it holding both `in/` and `out/`.
`controller.dataset_root` reads `dataset.in_dir`, which the loader sets exactly
when it detected that layout, and falls back to `data_dir` for a flat folder of
CSVs that has no parent to prefer.

**Home's plate takes `role="editor"`, not `"picker"`.** The wells are clickable
— that is what separates it from a dashboard's read-only map — but the built-in
All / Invert / Clear row is not shown. Those act on the plot selection, and
nothing on this page plots, so their effect is invisible from where they sit.

**The masthead carries one action.** Reveal-folder and open-dataset are in the
shell's own action cluster directly above the page, and Analyze and Datasets are
two of the three title-bar mode segments, permanently on screen. Only Reload is
here, because nothing else offers it and this page reports a record another
process can replace underneath it.

**Home is the one page with no rail body.** Every other feature's activation
hook re-shows one of the sidebar's per-page frames, which `on_tab_change` has
just hidden; Home shows none, because its plate is on the page and the rail's
picker would be a second one on screen. That is a supported state rather than an
accident: `RailNav` caps itself at its size hint and the rail column ends in a
stretch-0 spacer, so the surplus height has somewhere to go. Before both, the
nav was the only growable item left in a column stretched to the viewport and
its rows spread from 33px to 81px on every visit to Home.

Three rules the page is built around:

- **A figure it could not obtain renders as an em-dash with its reason, never as
  zero.** `collect.Missing` is a distinct type rather than `None` so a caller
  cannot format it as a number and a test can tell it from a real zero without
  reading prose. Real zeros — the user has defined no replicate sets — read
  "None yet". Conflating *not computed* with *computed as zero* is the bug class
  the completeness record exists to end, and the page reporting it must not
  reintroduce it.
- **Every colour it spends means what it means on the other plate maps.**
  `success` = complete, `warn` = partial, `danger` = an errored well, and
  `accent` = the anomalous one, which here is *images with no measurements* —
  Concatenate paints the identical condition (merged with no measurements CSV on
  one side) in the same token. `text_muted` carries *staged, never processed*,
  the token the Datasets dashboards use for pending and for Validate's "no
  output yet". `accent2` is unused: it means "the other of two normal groups"
  (Merge Plates), which no state here is. The rule is one colour per state and
  no colour that means something else elsewhere; `tests/test_home_plate_colors.py`
  asserts the table, and `tests/test_home_plate_render.py` samples the rendered
  widget, because the two can disagree — a table of legible tokens can still
  arrive as near-black cells if something dims them on the way to the screen.
- **The two states that describe an absence are painted in nothing at all.**
  `unchecked` (no completeness record exists) and `no_output` (the record holds
  the well and it has no evidence at all) both assert nothing, so they get the
  plate's own uncoloured cell and the legend gives them a hollow swatch. They
  cannot be confused, because `plate_cells` can only produce one of them per
  plate: `unchecked` is what a plate with no record is made of, `no_output` can
  only come from a record.
- *Partial* gets a coverage arc as well as a colour — the one state the page
  exists to make visible, and the one that has to survive a small cell and a
  fast skim.
- **The states it paints are renderings, not a second opinion.**
  `pipeline.completeness.health` remains the only classifier; `plate_cells`
  separates a staged-but-never-processed well from a half-finished one on
  `covered_any == 0`, a number already in the record.

It builds on `WellPlateSelector` + `apply_plate_geometry(role="picker")`
directly rather than subclassing `PlateProgress`. That class is a *run
dashboard* — headline, overall bar, ETA, done/failed/cancelled setters — and it
makes its plate inert (`WA_TransparentForMouseEvents`, the `status` role, which
is passive selection). Home's wells are clickable, and undoing inertness on a
base whose whole point is inertness is fighting it; the four-line read-only
idiom at `plate_progress.py`'s own construction is what is borrowed.

A click **selects** rather than navigates: a click that teleported the reader
off the summary page would make multi-select impossible and an exploratory click
expensive. Navigation lives in the one contextual next-step row, and every
button on the page — masthead and next-step alike — routes through
`controller.run_home_action`, so "open this in Analyze" cannot come to mean two
things depending on which control was pressed.

Home must paint from its **builder**, not from its `on_activate` hook:
`tab_controller.on_tab_change` early-returns until `_line_ax_mean` exists, and
that widget comes from Line Graphs, which is drained after startup. The hook is
for repaints.

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
2. Adds the per-tab controls row (channel, metric combo, timepoint combo, an
   **Export** dropdown, **Properties**). Plotting's row is the shared
   `ControlBand` (§5.2).
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
  Graphs / Scatter Plot / Distribution / Heat Map / Spatial register it). **The
  Image Table deliberately registers none.** Its render reads images off disk,
  so it happens when the user presses Generate and at no other time — including
  on a Saved Plots restore, where a saved grid can be hundreds of frames and
  paying for them is the user's decision to make. What restore owes the reader
  instead is not implying the pictures match the controls: `apply_image_table`
  marks the table stale (`runtime.image_table.builder_dirty`), which is the flag
  a shelf edit already sets, so the shelf header carries its standing
  "edited — press Generate" cue;
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
  channel options, per-cell values, **and the table's scope** — the
  `active_wells` / `active_timepoints` / `active_fovs` selections that decide
  *what the table is of*, as against how it is laid out. That scope is the half
  that has to be captured deliberately, because an **empty** set does not mean
  empty: `grid_layout._axis_dim_values` reads it as *all*
  (`sel if sel else _well_options(ctx)`). A snapshot that omitted it therefore
  restored a saved three-well, one-timepoint grid as the whole plate across the
  whole time course, with the right channels on it — which reads as a restore
  that did nothing rather than one that lost something. The sets are stored as
  sorted lists (wells in `parse_rc` plate order) because the block is written
  into `persistence.json` and `json.dump` cannot serialize a `set`; an empty
  selection is omitted rather than written out well by well, since the omitted
  and empty forms restore identically. A missing key **leaves the live value
  alone** — the one place in this module that does not overwrite from state,
  because every plot saved before the scope was captured is silent about it and
  clearing the user's plate selection on their behalf would be its own bug.
  Restoring the wells also re-lights the Image Table's sidebar plate, which is a
  separate selection from `selection.selected_wells`; state is written first, so
  `picker_view._on_plate_changed` short-circuits and no second layout rebuild
  fires. It then calls `builder_view.builder_refresh`
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
Home); `registry.sub_leaves(parent)` enumerates a container's
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
features = ["Plotting", "Image Browser", "Home"]

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
| `Image Browser` | `Image Browser` → `Image Table`, `Cell Browser`, `Playground` |
| `smFISH` | |
| `Review CSV` | |
| `Home` | `Home` → `Ratios`, `Pipeline Info` |
| `Saved Plots` | |
| `Batch Export` | |

**A sub-package in the catalog is removable from its siblings too.** A nested
`[[feature]]` block — Image Browser's three sub-tabs, Home's two — is a directory
`derive_profile` deletes on its own, so nothing outside it may name it at module
scope, and that includes the package next door.
`test_architecture_contracts.py::test_a_container_feature_does_not_import_its_own_removable_sub_packages`
reads the nested blocks out of the manifest and enforces it, because the
sibling-level guard compares only the first path segment under `features/` and
reads an intra-`image_browser` import as intra-feature.

A function-local import is **not** the repair: a lazy import of a deleted module
fails just as hard, one frame later. Shared code moves down a layer instead.
That is why an address-to-pixels lookup lives in `data/frame_load.py`, the
per-dimension option lists in `data/dataset_dims.py`, and the channel-tint
palette in `widgets/lut_cmaps.py` — each was owned by one Image Browser sub-tab
and reached for by another, so deleting the Image Table took the Cell Browser
with it while the manifest said all three were independent.

**Some sub-leaves cannot be narrowed away.** A descriptor carrying
`removable=False` is admitted by `Profile.includes` whatever a `[subtabs]` line
says, so it never appears in the table above and listing it there does nothing.
Home's `Cell Gating`, `Groups` and `Well Labels` and Image Browser's
`Segmentation` are the four. The flag exists because a container that enumerates
its sub-tabs from the registry hands the profile a lever over *every* leaf,
including ones other code reaches unconditionally: `centre_view.build_centre`
builds the rail plate that edits groups at startup with no descriptor check, so
a profile dropping Home's Groups tab would leave a plate editing something that
is not there. Declaring the tab and then relying on nobody excluding it is not a
contract. `test_feature_profile.py` holds the flag and `profiles/full.toml`
to the same story: a non-removable descriptor must not carry a catalog entry,
since `derive_profile` would delete source the registry insists is present.

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

One Qt builder sits in `core/` rather than under a feature package:
`core/fold_change_controls_view.py` exposes
`install_fold_change_controls(ctx, parent, layout, scope=…)` — the
Control + Baseline dropdown pair installed on both the Bar Plots and
Line Graphs control rows (inside each tab's `Fold change ▾` popover, not
on a visible row). It is in `core/` because both of its consumers
are features, and because it owns the cross-tab widget sync
(`_sync_widgets_to_state`) that mirrors each state change into the
other tab's widget instances, so the shared `state.plot.fc_*` values stay
visually in lockstep across tab switches. Its pure-logic half — the
scope registry and dirty-flag helpers it re-exports — stays in
`domain/fold_change_scopes.py`, which is what lets the unit tests reach
that logic without importing Qt.

### The Cell Gating tab

The most control-dense tab in Review, and the one whose layout carries the most
meaning, because it composes four gating layers that are easy to confuse. It is
laid out as the two questions it answers, in the order the app applies them:

1. **Cell inclusion — which cells count.** The QC debris gate leads, labelled
   *always applied*: it composes with everything below and is not one of the
   alternatives. Then a radio pair for the source of the inclusion decision —
   per-channel intensity floors, or the Scatter Plot's rectangular gates.

   `qc/tab_view.build_qc_gate_controls` lays the gate out as a grid with one row
   per *measured quantity* — Area, Solidity, Region area, Circularity — and its
   min / max bounds in aligned columns, rather than the single wrapping strip of
   six independently-labelled fields it was. A quantity that admits only one
   bound (solidity has no upper, region area no lower) renders an em-dash in the
   other column: a bound that does not exist must not look like one left blank.
2. **Fraction-on — which included cells count as ON.** The same radio pair
   shape: per-channel cutoffs, or a scatter gate. Then Auto-threshold and the
   button that opens the optimizer.

Radio pairs rather than checkboxes because the code has always treated the two
sources as exclusive — ticking the scatter option *replaces* the per-channel
numbers rather than composing with them — and each layer's gate picker sits
inside its own radio's row, since the two pickers are otherwise identical
buttons reading "All gates". Only the scatter radio of each pair is registered
(`_cell_gating_use_scatter_gates_cb` / `_cell_gating_use_scatter_thresh_cb`,
names kept from the checkboxes they replace): exclusivity means one signal
carries the whole state, and the controller keeps reading a single
`isChecked()`. Gates are drawn on a *different* tab, so whether the scatter
option is selectable is refreshed by `_cell_gating_refresh_gate_filter` — which
also shows or hides the "no gates drawn yet" hint — rather than decided once at
build time.

Each section carries **its own** per-channel table, built by
`controller._cell_gating_build_channel_controls` into `_cell_gating_inner`
(the inclusion floors) and `_cell_gating_thresh_inner` (the ON cutoffs), one row
per fluorescence channel and ratio metric. The fraction-on table has two value
columns, because the cutoff that applies follows the selected Property:
`ThreshFracOn` for an intensity, `ThreshCountOn` for an smFISH count. A channel
with no spot-count column renders an em-dash in the count column rather than an
empty cell — it has no count cutoff to set, which is not the same as one left
unfilled. Two tables rather than one with two
columns: the numbers share a shape but not a meaning — one decides membership of
the dataset, the other only how members are counted — so a shared table needs a
heading that names neither decision, which is the original defect (both columns
under the ThreshFracOn header) wearing a politer label. The columns also grey out
independently, since `_cell_gating_apply_scatter_gate_mode` disables whichever
layer a scatter gate has replaced, so a shared table is half-dead whenever either
layer is gate-driven. Neither table is inside a scroll area of its own: the tab
already scrolls as a whole, and a second, height-capped scroll around the primary
input surface scrolls inside the scrolling page.

Under the table sits the readout: the gate summary, then the **inclusion
funnel** — `gating_compute.inclusion_funnel`, each layer applied on top of the
one above it in `df_included_mask`'s own order, so the last figure equals the
Included total. A layer whose column is absent reports `None`, rendered "not
applied", never "−0": a layer that was never applied and one that removed
nothing are different facts (§7 of CLAUDE.md). Manual curation and the intensity
gate share one line because they write the same `Included` column and cannot be
separated from the frame — the label says so rather than implying a split the
data does not support.

The **CDFs** follow immediately, so a threshold edit and the distribution it is
placed against are on one screen. The optimizer — twelve controls answering an
occasional question — is not on the tab at all: `optimizer_view` builds it into
its own **non-modal** window (non-modal because its answer is judged against the
CDFs behind it), hidden, at tab-build time, so every optimizer widget handle
registers at the same moment as the rest and the refreshers that repopulate
those combos on a data change need no build-state guard.

### 5.4 The left sidebar

`core/sidebar_view.build_sidebar(app, parent)` builds the well picker
(`widgets/well_plate_selector.WellPlateSelector`, sized by the shared
`widgets/plate_geometry.apply_plate_geometry` — see §7) plus the **Select all /
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
mutate the sample definitions.

Plot Groups have two homes on disk: the `plot_groups` section of
`persistence.json` (written on every drawer edit) and a copy inside the
`sample_definitions` block of `pipeline_info.json` (written whenever sample
definitions autosave). The `pipeline_info.json` hydrator runs first on load and
`persistence/plot_groups.load_from_data_dir` runs second; an *absent*
`persistence.json` section leaves the hydrated groups in place rather than
clearing them, so a folder shared or copied without the sidecar keeps its
groups. The corollary matters for anything that clears them: writing only one of
the two copies clears nothing, because the surviving copy re-hydrates on the next
load. The Sample definitions reset scope writes both (§5.7).

Other sidebar bodies live with their feature: the preview picker is
`features/preview/preview_view.py`, the image-table picker is
`features/image_browser/image_table/picker_view.py`, Home's Groups editor
is `features/home/groups/panel_view.build_replicate_panel`, and the
Statistics group editor is `features/stats/tab_view.build_stats_group_editor`. They're stacked inside the same sidebar QWidget
and shown/hidden by `core/tab_controller.on_tab_change` based on the
active tab.

The collapse toggle (`Ctrl+B` / the button floating on the rail's edge) routes through
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
| `core/plot_style.py` | `apply_ax_style` — token-aware axes styling for matplotlib. `apply_export_style_prefs` then stamps the Properties-sidebar prefs (fonts, line/marker, grid, legend) over every artist, which means it can overwrite something a renderer meant as data. Three per-artist opt-outs bound it: `_aw_no_style_override` (skip the artist entirely — decorative lines like significance brackets), `_scatter_only_artist` (keep markers-only; don't join the points with a line), and `_aw_marker_is_data` (keep the marker *shape*; size and open-vs-filled still apply). The last is what the aggregate scatter sets, because there the shape encodes well identity and the legend is keyed on it — without it every point took the pref's shape while the legend kept advertising one per well. There are per-*axes* opt-outs too: `_categorical_xaxis` / `_categorical_yaxis` keep the log-scale pref off an axis whose ticks are category labels, `_aw_force_xlog` / `_aw_force_ylog` let an axis pin its own log state against the shared pref, and **`_aw_colorbar` takes an axes out of the pass entirely from the tick-rotation step onward**. A colour bar is a legend for its mappable's norm, not a plot axis: `fig.axes` includes it all the same, so without the flag the walk reset a `LogNorm` bar to a linear scale — discarding the log locator and formatter matplotlib installed, leaving the colours log-mapped beside linear labels — and drew gridlines and marker-edge styling over the bar's mesh. Typography and colours are applied before the opt-out, so a flagged bar still tracks the plot theme. Every colour bar the app creates (Heat Map, Spatial, Multivariate) sets it; the Heat Map re-stamps it each redraw because it refills a persistent `cax` through `cla()`. A third per-axes stamp, `_aw_time_scale`, carries the Properties sidebar's **time-axis label multiplier** (`PlotState.time_scale`, `domain/time_rescale.py`) through the pass: `apply_time_rescale` installs a `FuncFormatter` that prints each tick multiplied by the factor, and because the log step above calls `set_xscale` — which resets an axis's locator and formatter — the pass re-installs from the stamp rather than trusting the renderer's one call. The rescale is **labels only**: tick positions, plotted values, the timepoint menus, the Statistics tab and every export keep the hours the pipeline parsed out of the filenames, so nothing that reads a timepoint can desynchronise. The deliberate cost is that a rescaled axis disagrees with those readers, which is why `domain/time_rescale.axis_label` is the only way a time-axis label reaches a figure — it appends the factor, and `tests/test_time_rescale_marker.py` scans the source for any time-axis label that bypasses it. |
| `core/bus_wiring.py`, `core/redraw_coordinator.py` | Wire the `EventBus` signals to the redraw coalescer. |
| `core/frame_scope.py` | "Which wells / FOVs / timepoints does this act on?" — the Qt-free half of the Segmentation tab's two scope controls. A `ScopeSpec` names the three `set \| None` fields on typed state plus the wording and the un-armed meaning; `scope_options` / `current_frame` / `describe_scope` / `refresh_scope_button` and the state writers are shared by both, so the delete's scope and the crop's cannot drift in normalisation or in readout grammar. **Writing a field and re-deriving from it are separate calls** (`write_scope_field(..., apply=False)` then `apply_scope_change`) because their costs differ by orders of magnitude: the write is an attribute set, while the crop's re-derivation re-projects `Crop_Pass` over every cached well, redraws the active plot and re-renders the canvas. A `CheckableDropdown` emits `changed` once per ticked item, so the popover has to be able to do the first eleven times and the second once. The `None`-vs-empty-set distinction is load-bearing on both: `None` is the un-armed field (the spec says whether that means "this frame" or "all frames"), an empty set names no frames at all, and collapsing the two would turn a stray click into either a plate-wide delete or a silently disabled crop. Lives in core because two features consume it — `crop` owns one spec, `preview` builds both popovers — and a shared module that either one owned would be a feature the other had to import to reach its own control. |
| `core/scope_registry.py` | Register each feature's fold-change scope with its redraw callable (`features.barplot.renderer.redraw_bars`, `core.plot_orchestrator.redraw_app`). Called once from `wire_event_bus` during construction so the domain-layer `fold_change_scopes` module stays free of `features/` imports. |
| `core/status_view.py` | The status/log footer, and `_GUILogHandler`, which routes `well_viewer` log records into the drawer's `QTextEdit`. It is attached at DEBUG and gated on nothing, so it sees every record the app produces — including the `well_viewer.swallowed` breadcrumbs written from inside `except` blocks on worker threads — and `emit` is therefore entered off the GUI thread as a matter of course. It crosses that boundary through a `core.worker_bridge.GuiLogRelay`, never a timer; §10.1 has the mechanism and why the distinction is not cosmetic. Two further rules keep it from outliving its widget badly. **It never logs from its own failure path.** A failed append is caught as `RuntimeError` and handled silently — deliberately *not* `swallowed()`, whose DEBUG record would propagate straight back to the logger this handler is attached to, fail in the same place, and log again; a redraw's `processEvents()` drains that queue forever, which is a hang, not a slow run. **A handler whose widget is gone detaches itself** from every logger holding it, because nothing removes it on teardown — in a process that builds more than one window (the test suite) the dead handlers otherwise stay subscribed and every record fans out to widgets that cannot receive it. The document is capped at `_LOG_MAX_BLOCKS`, so the drawer is a bounded tail rather than an unbounded archive. |
| `core/ui_helpers.py` | Cross-cutting widget helpers (combo population, button factories, theme application). |

Per-feature controllers / renderers (under `well_viewer/features/`):

| Feature package | Role |
|-----------------|------|
| `features/lineplot/controller.py` | Line Graphs: redraw the 3-stacked-subplot figure. |
| `features/barplot/{controller,renderer}.py` | Bar Plots: redraw + bar-draw math (split because batch export reuses the math). **Significance brackets are matched to bars by identity, not by position.** `draw_significance_brackets` takes each pairwise record from `StatsState.pairwise` and resolves both compared groups to a drawn bar: first by name (a Statistics group seeded from a replicate set shares its name), then by well membership. The membership map covers both bar modes — the active replicate sets, keyed by the whole set of wells each pools, *and* the per-well bars, each keyed by the single well its bar token names, since in per-well mode a Statistics group holding one well has no replicate set to match through. A bar key that is also a replicate set's name is left out of the single-well half of the map: there the key stands for whatever the set pools, which need not be the one well of the same name, and bracketing a pooled bar with a comparison computed on a single well would overlay a result on data it was not computed from. A comparison that resolves to no bar (a group spanning several bars, say) is skipped with a status line saying so, rather than drawing nothing silently. |
| `features/scatter/{controller,callbacks}.py` | Scatter Plot (cells + aggregate). The per-axis fluor-gate (`fluor_gate_x` / `fluor_gate_y`) comes from the Cell Gating tab's per-channel floors, looked up by the axis's channel — which the scatter holds as the upper-cased dropdown label, so `_cell_gating_resolve_key` matches case-insensitively (a case-sensitive lookup returned the 0.0 "gate off" default and the tab's gate silently did nothing here). In the per-cell (Cells) mode it is bypassed when an axis column ends with `_smfish_count` — the gate is a fluorescence-intensity floor and would strip every integer-count cell on a count axis — while the mixed-axis case (one count, one intensity) still gates the intensity axis. The per-well (Wells, internally `aggregate`) mode gates differently and deliberately: it passes `get_all_fluor_gates` wholesale to the aggregator, which decides cell *inclusion* against every channel's `_mean_intensity`, so the two modes can report different populations when a fluor gate is non-zero. Each Wells axis reads its ON cutoff through `on_cutoff_for_column` keyed on its own per-axis channel, so a count axis is cut against `ThreshCountOn`. When that collector returns nothing it reports *why* through a `cut_sink`: an axis whose cells were all cut names the channel, the cutoff and the kept/total counts, which is a different message from having no cells at the selected timepoints — conflating the two told users to fix a selection that was never wrong. The prose an empty aggregate draws — and its status-line mirror — lives in `scatter/_messages.py`, not in `collect.py`: writing to the status bar is a Qt side effect, and `collect.py` is the pure-collection half. `clear_cut_status` runs once at the top of `redraw_scatter_agg_orchestrate`, not on the branch that succeeds in drawing: every other exit — no timepoints available, none ticked, no wells selected, a collector error — replaces the figure while leaving the shared status line telling the user to lower a cutoff for a plot that is no longer on screen. The interactive rectangular gates live in `scatter/gating.py` (a documented naming exception): each box is stamped at draw time with its axis metric columns (`x_col`/`y_col`) and a `name` (auto-assigned `Gate N`, shown on the plot, editable in the Properties sidebar's gate list via `rename_gate`, which captions each row with those two columns via `data/metric_labels.gate_axes_label` so a gate list spanning several metric pairs is readable), so beyond the on-plot "% inside" labels its `compute_gate_inclusion` (matplotlib-free, reuses `_in_gate` + `resolve_value_series`) lets the Cell Gating tab turn the selected gate(s) into the `Gate_Pass` inclusion layer. The gating (inclusion) and thresholding (fraction-on) layers each have their OWN *Gates* multi-select checkbox menu, so they can be configured independently (`cell_gating_selected_scatter_gates(ctx, mode)`); each selection is a set of names on the typed slice, `ctx.state.cell_gating.gating_choices` / `.thresh_choices` (`None` ⇒ all gates, auto-including ones drawn later). Each box's stamped `x_col`/`y_col` also drive on-axes rendering: `gating.draw_gates` / `gate_edge_at` only show and hit-test a gate when `ctx.runtime.scatter.axes_cols` (the columns currently on the axes, recorded by `paint_gates`) match it — a gate drawn on a different X/Y pair is hidden, since its box coords are meaningless there. Drawing / editing / clearing a gate fans out through `gating._notify_gates_changed` to the callback the Cell Gating tab registers on `ctx.runtime.scatter.gates_changed_cb` (`cell_gating_sync_scatter_gates`), which refreshes the pickers and recomputes inclusion immediately (signature-guarded); the per-cell scatter opts out of `Gate_Pass` by default, so its cloud never shrinks under its own gates. That opt-out is the tab's **Apply gates** toggle (`ScatterState.apply_gates_to_points`, in the `Display ▾` popover with the other view options): on, the plot shows only the gated population. It filters on the gate *geometry*, not on the `Gate_Pass` column — `redraw_scatter` passes `collect_scatter_data(gates=gates_for_axes(ctx, col_x, col_y))` and `_collect_well` ANDs in `compute_gate_inclusion`. That is deliberate and load-bearing: `Gate_Pass` is written by the Cell Gating worker, asynchronously and against *its own* gate selection, so it can describe a different set of boxes than the ones on screen — a gate drawn or resized since the last pass, a selection that omits it, or gates belonging to another metric pair whose boxes this plot hides. Each of those produced the same symptom: survivors in one corner, an empty box in another. Computing membership here makes "the points you see are the points inside the boxes you see" true by construction. `gates_for_axes` applies the same column-pair rule `_gate_on_current_axes` uses for drawing, minus unstamped gates (`compute_gate_inclusion` skips those, so including them would contribute an all-false mask and blank the plot — a box drawn but not filtering beats an empty cloud). `collect.scatter_points_gated` gates the whole thing on the toggle plus one more condition: no gate being drawn or edge-dragged. That matters because a gate release fans out to a recompute and a redraw, so a live filter would erase every point outside the box the instant it was drawn, leaving nothing to judge or resize the gate against. The toggle is otherwise self-contained — it does not consult `use_scatter_gates`, and filtering here changes nothing about what any other view counts. `collect.gate_filter_title_suffix` tags the axes title with `· gate-filtered` while it bites, since the toggle itself does not travel into a copied or saved figure. A second, independent Cell Gating toggle applies the same selected gate as a **fraction-on threshold** instead of (or as well as) inclusion: the worker writes a per-cell `Gate_On` column and `aggregate_with_threshold_df(on_col="Gate_On")` (selected via `stats_compute._thresh_on_col`) makes gate membership the "on" population for fraction-on, replacing the `value > ThreshFracOn` cutoff. The per-cell tab's **Color by time** toggle (`_scatter_color_by_tp`) colours each point by its timepoint instead of by well identity (one `Line2D` per well-timepoint in a shared viridis scale from `collect.timepoint_color_map`, which the aggregate scatter reuses); it overrides density shading, and with >1 well selected it forces the "Separate axes" stacked layout (`_render_scatter_faceted`) so each well's time-coloured cloud gets its own panel. |
| `features/heatmap/{controller,models}.py` | Heat Map: layout + redraw. A canvas click selects the cell's wells; Shift adds and Ctrl toggles. `_event_modifiers` resolves those from the Qt `QMouseEvent` behind `event.guiEvent` — PySide6 hands back a `KeyboardModifier` flag object that `int()` rejects, so the bits are read through `.value` — and falls back to matplotlib's own `event.key` when no GUI event is attached. **Mouse motion over the grid drives the row/column label drag and nothing else.** Hovering a cell writes no well-and-value readout into the control band: the band is a row of persistent controls sized to hold them, so a per-cell string appearing in it after the Timepoint combo grew the row on hover and pushed the ON-threshold control out of reach — a readout that costs the user a control they were reaching for is worse than no readout, and the cell's number is already drawn on the cell. `features/heatmap/sidebar_view.py` holds the layout editor; its **Auto-populate from selection** plans cells through the pure `_autopopulate_cells`, which reads whichever store the plate's current mode expresses the selection in — `selection.selections` in replicate mode and `selection.selected_wells` in plain mode (one cell per well, in plate order). Reading only the latter laid out whatever stale individual wells were left in it whenever replicate groups were defined, since replicate mode leaves that store empty. In replicate mode the shape of the plan then follows `heatmap.repset_avg`, which is what decides whether a cell means a set or a well: on, one cell per *visible* set holding all of its loaded wells; off, one cell per member well in plate order. A cell holding several wells renders as `A01+3` (members in the tooltip) with its well token in `_CELL_TOKEN_ROLE`, so the summary never reaches the model, and a drag moves the cell's whole well list rather than the single token the drag carried. **Cell numbers.** `_format_cell_value` picks a format from the metric and the magnitude — counts whole, fractions to two places, intensities scaled to their size — unless the Properties panel's **Value format** (`heatmap_value_format`, a printf-style string) overrides it; a format the value cannot satisfy falls back to the automatic choice rather than blanking the cell. Because the format changes annotation *text* rather than style, the Properties row triggers a real redraw instead of the auto-wired restyle. **Log scale** routes the values through a `LogNorm` (raw data reaches `imshow`, so the cell numbers stay in raw units) and masks non-positive entries; when the data carries no usable positive range it says so on the status line instead of quietly rendering linear, which was indistinguishable from the toggle doing nothing. |
| `features/distribution/controller.py` | Distribution tab: histogram / KDE / violin. Log-X/Log-Y is a **single per-figure axis pref** (`x_log` / `y_log`) that the on-plot checkboxes and the Properties-sidebar toggles both drive — they previously wrote two independent stores and the export pass clobbered the button. Log-Y histograms pass `log=log_y` straight into `ax.hist` rather than computing a density from 0 and log-scaling afterwards (which corrupted the bar heights). The tab mounts **no** `make_band_controls` row: it draws each group's whole population and never reduces to a mean, so nothing here reads `PlotState.use_sem`. That control is app-global — `make_band_controls` registers its button on `ctx.runtime.sem` and calls `core/plot_toggles.toggle_sem`, which writes the flag every aggregate path reads — so on a tab with no consumer it retuned Bar / Line / Scatter-aggregate / Batch Export while the figure under it stayed identical. `tests/test_band_controls_have_a_consumer.py` holds the rule: a module mounting the control must sit in a package that reads `use_sem`. The Properties panel's *Statistics* → *Error bars* segment is built for every scope and still writes the same flag, so the setting stays reachable from this tab — it is a panel-wide control that says so, not a tab control implying a local band. |
| `domain/fov_integrity.py` | **Are a well's FOV labels real?** Every per-field number in the app — the bar / line error bars, the events-per-field panel, the Statistics tab's `n` — groups on the CSV's `fov` column, and that column is only as good as the filename parsing that produced it: `process_microscopy.parse_filename` fills a schema slot it can't find with `""`, and `data/image_identity.normalize_fov` maps `""` and `"-1"` onto `"1"` (the single-field convention). A well whose filenames don't match the schema therefore arrives with every row labelled field 1, and *after loading* that is indistinguishable from a well that genuinely has one field — so the fields pool, the spread across fields becomes undefined, and the "mean events per field" silently becomes the pooled total. `check_fov_labels(df)` decides it from evidence the rows already carry, with no image reads: `filename` records the nuclear image each cell came from (one per field per timepoint), so **more distinct images than field labels within a timepoint** proves the labels don't separate the images; and `nucleus_id` is assigned per label image, so a **repeated id inside one (field, timepoint)** proves several images were pooled. The image-count signal is the one that survives an incremental re-run, because `pipeline/io.merge_records_into_csv` dedups on `(well, fov, timepoint, nucleus_id)` and drops the colliding rows outright. Either signal is conclusive alone and neither can fire for a genuine single-field well, so a dataset the checker doesn't understand behaves exactly as before. `data/data_loading.get_rows` runs it once as each well's frame is built and records the verdict on `state.dataset.fov_integrity`; `core/load_controller._report_unusable_fov_labels` names the affected wells in the status line, a toast, and the log, because silence about *why* is what made the original report look like a plotting glitch. `aggregate_with_threshold_df` consults it before computing anything per field: an untrusted well's per-field spread **and its per-field event count** come back `NaN` rather than a pooled total wearing a per-field label. |
| **Undefined spread is `NaN`, never `0.0`** | A spread of zero is a measurement — it says the replicates agreed exactly — and one replicate is not that. `data_loading._aggregate_arrays` returns `NaN` for every spread it did not compute (a single field, a single above-threshold cell, untrusted FOV labels), and that undefinedness is preserved end to end: `domain/fold_change.scale_bar_value` scales the value but keeps an undefined input spread undefined instead of folding it to zero; `features/export/service.bar_metric_row` writes an **empty cell**, so `err_SEM_*` never reads `0.000000` for a quantity that was never computed; and `features/barplot/controller._mark_undefined_spread` puts a hollow cap and an `n/a` on the bar when error bars are on. The marker is the point — consumers already skip a non-positive spread, so before it, a bar with nothing computable drew *identically* to a bar whose error bars had been switched off. |
| `features/stats/controller.py` | Statistics tab: groupwise tests, KS CDF. The replicate unit is the **one rule** `domain/stats_compute.replicate_unit` — shared with the bar/line error bars so the test's `n` and the plotted spread never disagree. In **FOV mode** every FOV **across all of a group's wells** is an independent replicate (`n` = total FOVs, pooled across wells); otherwise each well is one replicate. Pooling FOVs across wells is pseudo-replication (smaller p-values than a well-level test) — an explicitly chosen mode. |
| `features/image_browser/image_table/controller.py` | Image Table grid: render-model assembly + Generate/export orchestration. The "turn cache + state into pixels" work is split into sibling modules it re-exports (so call sites and external importers are unchanged) and which `test_monolith_budgets.py` ratchets: `image_compose.py` (LUT palette + per-channel cmap/LUT resolution, array loading, mask/threshold rendering, the RGB cell composer `cell_display_rgb`, the boundary overlay, and the channel-overlay blender `overlay_cell_rgb`), `grid_layout.py` (axis/cell layout math + the per-dimension well/channel/FOV/timepoint option lists), `build_layout.py` (flat + nested grid assembly + `image_table_apply_spec`), and `facet_view.py` (faceted render placement — axis labels + group spacing). **One facet plan, two surfaces.** `facet_view.plan_facets` computes the placement and the label text from the builder's nesting; `facet_view.render_facet_chrome` turns that plan into the live grid's `QLabel` axis strips, and `renderer.py` replays the *same* plan onto the export figure's `GridSpec`. So Export to File, Copy PNG, Copy SVG and Save to Saved Plots all carry the row/column axis labels, the rotated group strips, the corner label and the inter-group gaps the user sees on screen — the per-cell titles (`show_titles`) are an Export-settings opt-in on top, off by default because the axis labels already identify every cell. `renderer.py` is Qt-free: it duck-types the plan through its argument rather than importing `facet_view`, so the geometry is unit-testable without building a widget, and it restates the label palette in dark-on-light because the figure is saved transparent and lands on a white page where the app's dark chrome colours are unreadable. The export gridspec **pins its own margins and solves `wspace`/`hspace` for an absolute inch pad** (matplotlib spreads one uniform separator between adjacent slots, sized as `wspace × usable/n` before the ratios apply). It deliberately does not use `tight_layout` or `constrained_layout`: both redistribute space to fit each axes' content, which flattens exactly the group gaps the layout exists to express. **Brightness parity.** The Saved Plots snapshot pre-shrinks each cell to a pixel budget with an *area-average* pooler (`export._area_pool_rgb`) — the same averaging Qt's smooth transform applies to the live thumbnail — so the saved image reads at the grid's brightness; peak-preserving pooling dilates sparse fluorescence and makes the snapshot brighter than its source. Symmetrically, the live thumbnail passes an explicit `(0.0, 255.0)` window to `make_overlay_thumb`, because `cell_display_rgb` has already applied the channel's LUT and colormap and re-fitting a finished image to its own min/max distorts any colormap whose ends are not black and white. **The decode cache owns the pixels, and nothing else holds them.** A rendered grid keeps no frames: `ctx.runtime.image_table.cells` and `state.image_table.last_render` record *which* cells drew, never their arrays, and a cell's `QLabel` carries `_raw_shape` (an `(h, w)` tuple, the contract `crop_tool.label_source_shape` reads) instead of the frame itself. The pixel readout fetches its cell's array on demand (`controller._hover_array`, which holds the one cell under the cursor so a drag across it decodes once); the export figure and the composite column already lazy-load on a missing array, and that load is a decode-cache hit. This is what makes `_DECODE_CACHE`'s byte budget an actual ceiling rather than a suggestion: when the render model and the widgets each pinned every frame, the budget bounded nothing — emptying the whole cache released no memory — and resident pixels grew as (cells × frame size), which at the 2048×2048 frames microscopy produces is ~16 MB a cell and over a gigabyte for a plate walk. The budget is sized from installed RAM (`image_discovery._default_max_decode_bytes`, ~15% clamped to [512 MiB, 2 GiB]) precisely because it is now the ceiling: a grid larger than the budget re-decodes through LRU eviction, which is slow, instead of exhausting memory, which fails allocations and surfaces as frames that decode for some cells and not others. **Decode caching is two-tier and shared across every export path.** `ctx.state.image_table.image_cache` holds the per-`(well, chan_lower, variant)` ref-dicts (which files exist for a well+channel); `data/image_discovery.py`'s module-global `_DECODE_CACHE` holds the actual decoded pixel arrays, keyed by `(resolved_path, greyscale, mtime_ns)`. `export._build_export_figure` writes into the *same* `image_cache` object `image_table_generate` uses (never a throwaway copy), and calls the identical threaded `controller._prefetch_cell_arrays` warm-up Generate uses before rendering — so a Save to Saved Plots or Export to File run benefits from a prior Generate's decode work and vice versa, and a save without a prior Generate still decodes in parallel rather than one cell at a time on the GUI thread. `_prefetch_cell_arrays` dedupes across calls via `state.image_table.prefetched_cells`, a persistent `set` of `(well, chan_lower, variant, tp, fov)` tuples already submitted to the thread pool — a *per-cell* key, unlike `image_cache`'s per-well+channel ref-dicts, because a grid faceted by timepoint or FOV needs every distinct cell warmed, not just one representative per well+channel. Because `export.py` needs one function out of `controller.py` (which already imports `export` at module scope as its re-export point), that one edge is a function-scoped import inside `_build_export_figure` — the standard cycle-break pattern the codebase uses elsewhere in this feature (§ `test_deferred_imports.py`'s budget). **Save to Saved Plots quality is a tunable preset**, not a fixed constant: `GALLERY_QUALITY_PRESETS` (`export.py`) maps `draft`/`standard`/`high` to a `(cell_max_px, gallery_max_px)` pair — the per-cell pre-shrink budget and the assembled grid's longest-side cap — so a single user choice (the Export dropdown's quick-pick submenu, or the "Export settings…" dialog) scales both together; scaling only one independently would either cap quality at the other's bound or waste compute. Export to File / Copy PNG / Copy SVG always render full resolution — only the gallery-save path reads this setting. **A Generate pass degrades cell by cell.** `image_table_generate` clears the grid before it draws anything, so an exception escaping mid-pass leaves a wiped or half-drawn table and no account of why. Every pre-pass — the parallel prefetch, the pooled Auto-LUT window, the chrome plan, the facet plan — is wrapped and degrades to a usable default (per-cell auto-fit, no scale bar, a plain unfaceted grid), and each cell's render is wrapped on its own, so one unreadable frame costs that one cell instead of abandoning every cell after it. A cell that produces no pixmap carries a *reason*: `(no image)` when nothing decoded, `(no mask)` when it asks for a mask / ≥threshold view the segmentation output can't supply, `(render failed)` when the render raised (logged with the cell's well / channel / timepoint / FOV). The status line counts the holes — a table that quietly draws fewer cells than it was asked for reads as a finished table. **A binary render never falls back to the intensity image.** `cell_display_rgb` returns `None` when a mask / ≥threshold cell can't resolve its segmentation mask, rather than composing the fluorescence it would otherwise show: that fallback made a threshold view come out half switched, with the cells whose mask resolved turning into silhouettes while the rest kept showing their picture, indistinguishable from cells with no signal above threshold. `_load_mask_array` resolves through the *same* `_match_image_ref` tiers as the image lookup (exact → normalized → numeric-close, plus the ambiguous-FOV refusal), so the mask and the image agree cell for cell instead of the mask resolving a subset of what the image does. **It also drops the cells the analysis does not count**, through `data/inclusion.drop_excluded_labels`: a pipeline mask holds every object that was segmented, including cells deleted by hand on the Segmentation tab and cells a crop excluded, and outlining those states that they are counted while every plot, statistic and export has already dropped them. Every mask this tab draws from arrives through that one call — the boundary overlay, Binary (mask), Binary (threshold), and the mask channel's own LUT pass — so the filter is applied once rather than at each of the four. The exception is the pipeline's pre-rendered **NUC+SEG overlay** channel, whose boundaries are baked into a saved image and cannot be filtered; that channel still shows every segmented cell. The dependency runs one way only: a binary cell draws its segmentation and never reads an intensity value, so when no fluorescence frame resolves it falls back to the mask as the cell's array (which also carries the source shape the crop-drag conversion and the pixel readout need) rather than reporting `(no image)` for a cell it could have drawn — and a cell with no mask either is labelled `(no mask)`, the thing that is actually absent. **An empty scan is remembered for one pass, not for the session.** `image_cache` outlives every Generate (it is dropped on dataset load), so an empty `find_well_images_and_masks` result written into it pins "this well/channel has no images" until the dataset is reloaded — and empty is not always permanent: a drive that stalls, a directory a running pipeline is still writing, a transient `OSError` inside the walk all produce it, and the affected cells then read `(no image)` forever while their neighbours render. `_load_well_channel` caches only non-empty results (including the `__mask__` sentinel dict) and records the empty ones in `state.image_table.scan_misses`, cleared at the top of each Generate / export run — so one pass still scans a genuinely absent well/channel only once, and the next pass gives it another chance. `_prefetch_cell_arrays` marks only the keys that actually decoded, for the same reason. **Headless plain-data render model.** `ctx.runtime.image_table.cells` is a `rows × cols` grid of plain `{"well","chan","tp","fov"}` string dicts — the render contract, no Qt widgets. The renderer (`image_table_generate`), the layout math, the per-cell override popover (`grid_ops.py`), and `persistence/view_state.py` all read/write those strings directly; off-axis held values live on `state.fixed_vals` (read by `grid_layout._fixed_off_axis`) and grid size on `state.rows`/`state.cols`, so there are no held-value combos or grid-size spinners. A **grouped/tiled** well layout (`group_size`, 0 = off) bands each channel across multiple rows; it persists through `persistence/view_state.py` (`group_size` / `group_row_span`). **Builder shelf.** The **sole** authoring surface is `builder_view.py` — a root + nested-channels drag-and-drop tree: each axis gets a root (Well/Timepoint/FOV) with channels nested under it, each channel carrying its own render options (raw/corrected · mask · ≥thr · boundary · colormap/LUT) via a ⚙ menu; a derived **Fixed** panel auto-lists the off-axis roots with value pickers; a **Separate ↔ Overlay** switch (`state.composite`) collapses a root's channel axis to a single entry and additively blends every selected channel into each cell — "blend this root's channels into one cell" (the layout emits the `image_compose.OVERLAY_CHAN` sentinel as the sole channel value via `grid_layout._axis_dim_values`; `image_compose.overlay_cell_rgb` blends it in both the live grid and export, and the flag round-trips through `view_state`); wells come from the sidebar plate. **Every drop carries a position, because the position is the whole point of a list.** Each nested-channel pill is a drop target in its own right (`_ChanPill`), not just a drag source: the half of the pill under the cursor picks a side, drawn as an accent rule on that edge, and `_on_pill_drop` turns it into an insert index that `_move_channel` / `_add_channel` honour through their `at=` argument. The axis zone behind the pills still appends, which is what a drop on its bare background means, and a root token dropped on a pill is deliberately left unaccepted so it propagates out to that zone — the widget that knows how to place a root. Without a positional target the zone was the only place a drop could land, so a channel dragged within its own axis could reach nothing but the bottom of the list, and dragging the bottom one did nothing at all. Chips also claim the mouse press that starts a drag rather than letting `QFrame` ignore it and hand the press to the parent zone, and they carry themselves as the drag pixmap so the gesture reads as moving that chip. The shelf edits a Qt-free `TableSpec` (`state.py`) and calls `build_layout.image_table_apply_spec`, which flattens it onto the existing `row_dims`/`col_dims`/`active_*`/`composite`/`group_size` state (+ a per-channel `channel_opts` block) and drives the layout + a debounced `image_table_generate`. Per-channel display is **position-independent**: the four `image_compose._row_*` helpers resolve options from `channel_opts` keyed by the cell's channel, and the live generate path is unified onto `cell_display_rgb` so the live grid, the channel-overlay blend, and export always agree. **A channel's render mode is part of its axis token, because the token is the grid's identity for that channel.** `ChannelSpec.axis_token` emits `"<CHAN> (raw)"`, `"<CHAN> (mask)"` and `"<CHAN> (binary)"` for those three renders and the bare `"<CHAN>"` for `corrected` — the default, left bare so previously saved views keep their `channel_opts` keys. That is not cosmetic: `spec_to_state_fields` keys `channel_opts` by the token and `grid_layout._axis_dim_values` derives the channel axis from those keys, so a token that ignored the render gave the same channel shown two ways — its image and its cell mask — one key, one row, and the second chip vanished with nothing on screen saying so (`spec_from_state` rebuilds the shelf from the same keys, so it did not come back on restore either). `image_compose` owns the suffix vocabulary: `is_variant_token` / `_is_mask_variant` / `_is_binary_variant` classify a token and `_base_channel` strips it back to the channel whose pixels get loaded, since every variant loads the same frame and differs only in what is done with it. `_channel_options`' default branch filters every variant out, so a plain "Channels × Time" build is not silently multiplied. **A binary cell paints white or one colour per label.** `ChannelSpec.label_colors` (the ⚙ *Colour cells* item, offered only on `mask` / `≥threshold` since no other render has label ids) reaches the pixels through `image_compose._row_label_colors`, and `_export_binary_mask` keeps the label ids rather than flattening to a membership mask — a boolean array has already discarded the one thing that distinguishes a nucleus from its neighbour. The white-or-colour decision itself is `core.frame_render.fill_label_rgb`, shared with the Playground's binary view, so a cell is the same colour in both panes and there is one deterministic palette rather than two. **The LUT panel offers a row only for channels the LUT can change, and names the ones it cannot.** `builder_view._has_display_range` is the single predicate: a binary (`mask` / `≥threshold`) render paints a silhouette and the `NUC+SEG` channel is a pre-rendered RGB overlay, and `image_compose._compose_image_table_rgb` passes both through without consulting the LUT window, colormap, or log flag. Those channels get no editable row — a control whose every edit repaints nothing is worse than no control — and `_lut_inert_channels` lists them under the rows instead. They still draw an image, so without that line a LUT edit on a grid that mixes them reads as "only part of the table updated", with nothing on screen saying which cells the panel governs or why the rest held still. One row per *base* channel, so a channel shown as both its fluorescence and its ≥Thresh binary appears once (the fluorescence instance drives the LUT) and again in the inert list — under the axis token the grid labels it with, so the line names the row the reader can see. **A cell served the raw frame is marked on the cell.** When the correction pass never produced a top-hat image for a cell's `(fov, tp)`, `_load_array` falls back to the raw frame and records the cell in `state.image_table.raw_fallbacks`. That cell draws, but it draws a different measurement: a corrected image is `img - grey_opening(img)` floored at zero, so its background sits near 0, while the raw frame still carries the camera offset and background. One LUT window cannot suit both — a window tuned on the corrected population pushes the raw cells past its ceiling, where the shared clip saturates them and every further LUT edit produces a byte-identical image. `image_compose.mark_uncorrected` burns a short `raw` badge into those cells through the same `draw_cell_chrome` the scale bar and identity label use (in a corner neither has claimed, after the chrome so nothing paints over it), the tooltip spells out the reason, and the export figure carries the badge too. `_compute_auto_luts` skips them when pooling the per-channel Auto window, because one raw cell's offset otherwise stretches the window every genuinely corrected cell of that channel shares and renders them all near-black. The Generate status line still counts them; the badge is what says *which*. Per-cell tweaks come from right-clicking a rendered cell (`grid_ops.image_table_cell_override_menu`, which writes the cell dict). **Plate (96-well) layout.** A **Plate A–H × 1–12** toggle in the shelf header lays each selected well's image out in true plate positions with correct axis labels, using two *virtual* layout dims — `well_row` (A–H) and `well_col` (1–12), enumerated from the plate selection in `grid_layout` (`_well_row_options` / `_well_col_options`). They never get their own render-model cell key: `grid_layout.compose_plate_well` resolves each `(row, col)` back to the real `well` token (blank when that position is absent), and `facet_view._cv` derives them back from the composed well so the A–H / 1–12 headers label every band — unlike the `group_size`/Wrap trick, whose facet headers only reflect the first band. It rides the existing single-dim `_build_nested_layout` path (`row_dims=["well_row"]`, `col_dims=["well_col"]`); the channel is held fixed (`TableSpec.plate` flag round-trips through `spec_to_state_fields` / `spec_from_state`), and it's mutually exclusive with Wrap *and* with channel Overlay (plate mode writes `composite=False`). Because both axes belong to the plate there is nowhere for a channel axis to tile, so **plate mode displays exactly one channel** — the first in the shelf, pinned as `fixed_vals["channel"]`. The LUT panel follows: `builder_view._plate_shown_token` is the one channel that gets an editable row, and `_plate_hidden_channels` names the rest on their own hint line. Offering a row for a channel with no cells is the all-or-nothing sibling of the inert-channel case above — the edit lands in `channel_opts`, nothing reads it, and no cell moves. |
| `features/preview/controller.py` | Preview-panel helpers (well-picker sync for the image tabs). The image pixel-value hover tooltip lives in `review_image/controller.py` next to its only caller. |
| `data/image_identity.py` | **Single source of truth for image filenames, cell identity, and the suffix/role catalog.** Owns three concerns deliberately co-located so they cannot drift: (1) `CellKey` — `(well, fov, timepoint, nucleus_id)` with one canonical normalisation chain; every site that joins CSV-row data to image-derived data builds keys via `CellKey.from_row` / `cell_keys_for_dataframe` (the smFISH writer, `cell_overrides`, `review_image`'s row keys, and `_process_well`'s counts construction all consume it). (2) `parse_image_filename` / `build_image_filename` — exact inverses driven by a `PipelineSchema` loaded once from `pipeline_info.json` via `load_schema(...)`. No parallel regex fallbacks; single-FOV pipelines are modelled by `has_fov=False` rather than a special-case extractor synthesising `"1"` (the empty-FOV-to-`"1"` rule is enforced uniformly by `normalize_fov`). (3) `ROLE_CATALOG` — declarative `RoleSpec` per role (`fluor`/`tophat`/`mask`/`overlay`/`smfish`) mapping suffix, output extensions, channel-scoping, and precedence; `classify_filename_role` and `role_output_filename` both consume it. (As of the §6.2 self-sufficiency fix this catalog is **reader-side only** — the pipeline writer, `process_microscopy.build_processed_output_path`, uses explicit `suffix=` strings rather than importing `well_viewer`. If catalog-driven *writing* is revived, move `ROLE_CATALOG` into a Qt-free `*_core` module first so the pipeline can import it without breaking the contract.) The full time-format matrix (`T01`/`24`/`24h`/`1.0`/`day3`/`02d04h30m`/`timepoint_hours` float) collapses to a single canonical form via `normalize_tp`. `frame_token_series` is the frame half of `cell_keys_for_dataframe` without the per-row `CellKey` construction — normalised `(fov, tp)` Series for a frame, for a caller that only has to decide which frame each row belongs to (the crop scope); both resolve their columns through the same `resolve_identity_column`, so they cannot end up reading different columns off one CSV. Stdlib-only at import; pandas is lazy inside `normalize_fov_series`, `frame_token_series` and `cell_keys_for_dataframe` so `process_microscopy.py` can import the module without adding pandas as a pipeline dependency. |
| `features/smfish/{controller,worker}.py` | smFISH spot detection + the background worker. Apply Global Threshold rewrites every per-well CSV's `<channel>_smfish_count` column, then in its after-csv callback persists the threshold to `pipeline_info.json::smfish_thresholds` via `persistence/smfish.py`, drops the per-well DataFrame cache + the aggregation stats cache, redraws plots, and refreshes the Review CSV table — without those last three steps, downstream plots and the review table kept showing pre-apply counts. The after-csv callback is routed through `_WorkerBridge.after_csv` (a queued `Signal(object)`) rather than invoked directly from `apply_global_threshold_async`'s daemon thread — the worker thread has no Qt event loop, so the previous `QTimer.singleShot(0, …)`-from-worker scheme silently dropped the refresh on the floor and the user had to close and reopen the app to see the new counts. Progress crosses the same bridge: the worker's `progress_cb(done, total)` fires once per well in the counting pass and again per well in the CSV-write pass, `_WorkerBridge.progress` (a queued `Signal(int, int)`) marshals it onto the GUI thread, and the tab drives the shared status-row `_progress_bar` — the widget a dataset load steps — through `ctx.widgets.get_or_none`, so the bar is simply absent rather than fatal in a build without it. The bar is shown when the run is launched and hidden by the `done` signal, which every terminal path emits: success, cancel, a crash caught by `_run_guarded`, and the empty-channel guard (that guard returning on a status line alone left the tab's `apply_running` flag set, so the button rejected every later click as "already running"). The write loop reports from a `finally`, so a well skipped for an unresolvable or locked CSV still advances the bar instead of parking it short of its maximum. The writer takes the explicit `well_to_csv` map the loader populated (not a `out_dir.glob("*_<well>.csv")` lookup, which silently skipped wells whose CSVs had no underscore prefix), drops any case-equivalent column before reassigning (so a stale `W3Cy5_smfish_count` from an older pipeline run is overwritten cleanly), and builds both producer (`_process_well`) and consumer (`_write_counts_to_csvs`) keys via `data/image_identity.CellKey` — the unified identity module enforces the same well/fov/tp/nid normalisation chain on both sides, so empty-fov, `"-1"`, letter-prefixed tp like `"T01"`, padding like `"001"`, and `.0` float artefacts on nid all collapse identically. A `matched == 0` WARN fires from the writer when every lookup misses so future `(fov, timepoint, nucleus_id)` format drift names itself in the log drawer. |
| `features/image_browser/review_image/{controller,renderer}.py` | Segmentation tab review-image + Review-CSV jump-to-image. A **Raw / Flat-field / Corrected** `SegmentedControl` (`ReviewImageState.image_source`) picks which image stage the canvas shows: "Corrected" (the saved final = flat-field + background, default), "Raw" (unprocessed), or "Flat-field" — the raw frame with *only* the selected flat-field applied, recomputed live via `core.flatfield` (`flatfield_field_for`, cached per channel) to validate the flat-field step before any background subtraction. The Segmentation and Image Table control rows each host an **ON-threshold quick-edit** `Stepper` (mirroring Plotting's band) that edits the active channel's `ThreshFracOn` via `set_thresh_frac_on` and re-renders so the binary "≥ ON threshold" overlay updates live; `channel_state_controller.refresh_thresh_frac_quick_edit` keeps all three steppers in sync on channel switch / edit. Double-clicking a Review-CSV row jumps to that cell: the handler switches to the Segmentation sub-tab, sets `preview.selected_well`, renders, drives the FOV and timepoint combos, and zooms to the nucleus. It ends by re-running the sidebar well picker's refresh — the sub-tab activation hook already refreshed it, but that ran before the row's well was chosen, so without the second pass the jump lands on the right image while the sidebar still reads "No well selected" with no well lit on the plate. The Review-CSV table normalises its FOV combo values through the same `review_norm_fov` chain the cell-click navigation uses (the combo is non-editable, so a raw-vs-normalised mismatch would silently miss `setCurrentText` and strand the filter), and the table is hard-capped at `controller.MAX_TABLE_ROWS` (5000) — a `QTableWidget` builds one heavyweight item per cell, so an unbounded build (the no-match fallback, or a multi-well selection spanning every FOV/timepoint) OOM-kills the process. **The cap is applied while the rows are fetched, not after them.** Applied afterwards it bounded the widget and nothing above it: the combo values and the filter both ran over one Python dict per cell for every selected well, and only the survivors of that pool were sliced. Those dicts cost ~7 KB resident each on a 40-column plate — 200 MB for 28,800 rows, several gigabytes for a real plate of a few hundred thousand cells — which is where a `MemoryError` while opening a plate came from, the failing allocation being a later 4.56 MiB request the exhausted process could no longer satisfy. `collect.review_filter_values` now derives the FOV / timepoint combo entries from each frame's **distinct column values** (a per-well constant, whatever the cell count), `collect.review_pooled_rows` filters on the frame and materialises at most `limit` rows while still counting the true total for an honest "showing N of M", and `collect.any_review_rows` answers the "is there anything at all?" question the pooled list used to answer by existing. The Segmentation control strip (`preview/panel_view.py`) is the shared `ControlBand`, with **Edit / View / LUT / Colors ▾** menu-buttons (built with the shared `ui_helpers.make_action_menu_button` / `add_menu_action` helpers) in its action zone so it wraps instead of clipping when Split View halves the pane; every widget keeps its registered name / runtime attribute, so the renderer and controllers reach them exactly as before. Zoom is the exception to the popover treatment: **− / readout / + / Fit** sit inline on that row, since zoom is in constant use while reviewing segmentation. The readout (`_review_image_zoom_lbl`) shows the factor relative to fit-to-window and is refreshed by `renderer.refresh_review_image_zoom_label` from inside `render_review_image_display` — the single point every zoom path funnels through, so the buttons, the scroll wheel, and the Review-CSV jump's zoom-to-nucleus all keep it honest. `_draw_crop_outline` paints the crop rectangle over the composed RGB in cyan, **dashed and dimmed when the Crop scope does not name the displayed frame**: the box is worth showing where it sits, but nothing in that frame is excluded by it, and a solid outline saying otherwise is the silent substitution §7 of `CLAUDE.md` is about. Which case it is comes from `crop.controller.crop_covers_frame` → `region.frame_in_scope`, judged against the well's own rows — the same inputs that decide the `Crop_Pass` verdict. Restating the comparison here was wrong in both directions: a scope holding a raw `T24` read as out-of-scope while cells were being excluded, and a frame whose CSV carries no timepoint column read as in-scope while no row of it could match an armed timepoint. |
| `features/image_browser/review_image/callbacks.py` | What a finished rubber-band drag in Delete mode does, and the override writer it shares with the single-cell click. `commit_included_overrides` is the one writer that can span wells: it takes `(well, fov, tp, nid)` keys from `override_key`, projects onto each touched well's cached rows, invalidates the stats cache, schedules the debounced save, bumps `override_version` and re-renders **once** for the whole batch, restoring zoom/pan around the refresh; it counts distinct keys rather than writes, so a CSV with two rows for one cell does not overstate the edit. `override_key` runs FOV / timepoint / nucleus through `CellKey` — the constructor `collect.review_row_keys_series` looks overrides back up with — but leaves the **well** exactly as the dataset spells it: well labels are `extract_well_token(stem) or stem`, so a CSV whose stem is not a well position keeps its raw stem, and canonicalising it here would produce a key no reader ever asks for. `apply_box_delete` deletes on the displayed frame by any-pixel overlap against its label mask; when the **Delete scope** control has armed further wells / FOVs / timepoints, `collect.collect_region_delete_targets` resolves the same rectangle across those frames by *centroid*, sharing `crop/region.centroids_inside` with the crop layer. The two rules differ because they must: an off-screen frame's mask would have to be decoded out of its `<well>_out.zip`, one decode per well × FOV × timepoint, where the centroid columns are already in the CSV. `centroids_inside` answers only "definitely inside", which is what lets the two callers take opposite sides on the unplaceable cells — the crop keeps them (fail-open), the delete spares them (fail-closed). Both halves are counted separately on the status line, and everything the delete could not judge is named in a warning toast rather than folded silently into a smaller total: wells with no `shape_centroid_*` columns, wells whose rows won't load, and armed wells this dataset does not have. The scoped scan runs on the UI thread and costs seconds on a full plate — the row scan plus one override projection per touched well — so `_busy` puts up a wait cursor and a status line for any multi-well scope and logs the elapsed time; moving it to a worker is the open follow-up. |
| `features/preview/frame_scope_view.py` | The one **frame-scope popover** the Segmentation tab builds twice — once for the drag-delete, once for the crop. Three `CheckableDropdown` multi-selects (wells / FOVs / timepoints), *Use plate selection* (reads `selection.selected_wells`, never writes it) and a reset. Everything that differs between the two is a `ScopeSpec` (`core/frame_scope.py`): which typed-state fields to write, what an un-armed field means, the wording, and what to re-derive after a change. **Each dropdown names its own field and carries no caption label.** `CheckableDropdown` prints its `noun` in its own button text ("2 of 3 timepoints", "No wells"), so a caption beside it printed the same word twice — the label-beside-a-self-describing-control pattern every Timepoint control in Review folded away, and one `test_timepoint_control_folding` fails on. The three share a fixed width (`_FIELD_W`) so they still line up as a column, which is the part the caption's own minimum width used to supply; the muted hint above them says what the fields are *for*, which is the part no button can state. Options are refilled on the menu's `aboutToShow` rather than at build time — the tab is built lazily and outlives datasets, so a list captured once would offer the previous plate's wells. An un-armed field seeds its dropdown from whatever that field currently *means* — the displayed frame for the delete, every option for the crop — while leaving the state `None`, so opening the popover to look at it arms nothing. **A burst of ticks settles into one re-derivation.** Each tick writes its field and relabels the button immediately; the spec's `on_change` runs on a short single-shot timer (parented to the popover, so it cannot fire into a torn-down tab), flushed early when the popover closes, which is the user saying they are done. Without that, ticking eleven wells one at a time ran eleven full `Crop_Pass` re-projections and redraws on the UI thread. |
| `features/preview/delete_scope_view.py` | The **Delete scope ▾** spec and its thin wrappers. Fields live on `ReviewImageState.delete_scope_*`, an un-armed field means "the displayed one", the reset reads *Reset to this frame*, and nothing has to be re-derived on a change because the scope is only read when a drag finishes. |
| `features/crop/{controller,region}.py` | The crop layer: one rectangle in mask-pixel space, the frames it is applied to, and the `Crop_Pass` column that carries its verdict into the `df_included_mask` chokepoint. `region.py` is pure pandas — `centroids_inside` (shared with the region delete), `compute_crop_pass`, `crop_effect_for_df`, and `FrameScope` / `rows_in_scope`, the per-row "is this row's frame in scope?" test built on `image_identity.frame_token_series`, plus `frame_in_scope`, the frame-level question a *view* asks ("is the box I am drawing filtering this picture?") answered from the same rows rather than from a second comparison of the same tokens. **The token pass is memoised on the frame** (identity + length keyed, so a copy recomputes): normalising a well's FOV/timepoint tokens is per-row Python and dominates every scope question — ~430 ms on a 200k-row well — and it is asked once per projection and again on every canvas redraw. A scope that names only wells skips it altogether. It never touches `Included`, so moving, scoping or clearing the crop cannot clobber hand-curated edits. **Everything about it fails open**: no region, no centroid columns, a NaN centroid, or a frame the scope does not name all leave the cell passing — the crop excludes only cells it can place inside a box it was armed for. The scope's default is every frame, so an unscoped crop is the plate-wide filter it has always been; narrowing it re-projects and reports the new counts once the run of ticks settles (see `frame_scope_view.py` below), because changing which frames the box filters changes the numbers exactly as moving the box would. `set_crop_region` reports the crop's *effect* (cells excluded of otherwise-included, across loaded wells) rather than only that a box was set, and warns loudly when the dataset has no `shape_centroid_*` columns at all — a crop that excludes nothing must not look like one that worked. |
| `features/preview/crop_scope_view.py` | The **Crop scope ▾** builder. The spec itself (`CROP_SCOPE`) lives in `features/crop/controller.py`, next to the `Crop_Pass` projection and the Reset actions that also have to keep it honest — every path that changes a scoped crop is on that side, and each must relabel the button or it goes on advertising a scope the state no longer holds. |
| `features/image_browser/playground/{controller,renderer,worker}.py` | Segmentation-page **Playground** sub-tab: an interactive testbed for the pipeline's background-flattening / subtraction methods *and* its StarDist segmentation, on a single frame. **Two frame sources**, resolved by `_resolve_frame`: the dataset frame addressed by the sidebar plate's well plus the channel / timepoint / FOV combos, or — winning when set — a **loose image** opened straight from disk with "Open image…" (`state.loose_image_path`, read through the pipeline's own `load_2d_gray`, cached by path so the correction memo's `id()` key still hits). The loose source is what makes the tab usable with **no dataset loaded at all**, which is the state you are in when choosing settings for a plate you have not run yet; it disables the dataset selectors, has no precomputed mask, and refuses the reference-well correction modes (`empty_well` / `basic`, which need a whole well to sample) **by name** in the status line rather than presenting an uncorrected frame as corrected. Loading a dataset ends loose mode (`PlaygroundState.clear_caches`). Applies the chosen flat-field + background method through `process_microscopy.correct_single_frame` — the **same** per-frame orchestration the batch worker's `_correct` uses (extracted so pipeline and Playground share one code path; a parity test pins the equivalence), so no method is reimplemented. The `empty_well` / `basic` reference fields are estimated from a chosen background well on a daemon thread via a queued-signal bridge (smFISH pattern). **Those fields are cached by everything that decides which one gets estimated** — `("ff", well, channel, mode)` and `("bg", well, channel)` in `state.ref_fields` — because `basic` and `empty_well` produce *different* flat-fields from the same frames (`estimate_basic_flatfield` versus `normalize_flatfield(B)`) and the additive background field is only estimated when the background method is `empty_well`. A cache keyed on less than that answers "already built" for a field it never built, and `apply_flatfield` then no-ops **silently** on the `None` it is handed, so the correction appears to do nothing. `_reference_fields_for` is the single resolver both the render path and the Segment button go through, so the frame on screen and the frame handed to StarDist can never be corrected differently; when a needed field is absent it **downgrades that half of the correction to `none`** and names the refusal in the read-out, rather than reporting a mode that did not run. A build that finds no usable frames is recorded in `state.ref_missing` with its reason — held apart from `ref_fields` so "not built yet" (build it) stays distinguishable from "cannot be built" (report it, and never respawn the worker on the next keystroke). `_build_reference_then_render` captures the well and the modes at *request* time and files the result under keys built from those captures, so a dropdown changed while the worker runs cannot relabel what it computed. The correction itself stays synchronous on the GUI thread, so `playground_on_change` memoizes its result (`state.last_correction_key`/`last_corrected_arr`, keyed on the decoded frame's identity — safe because `image_discovery`'s decode cache returns the same array object on a hit — plus every correction parameter) and skips recomputing when nothing that affects the output changed; `build_playground_tab` triggers exactly one render on first open (via `registry._activate_playground`, which always follows the tab's construction in the same dispatch), not its own redundant call plus the activation hook's. **The stage control — labelled "Correct up to" (`state.source`: raw / flatfield / corrected) — heads the Correction section and is a master switch, not a view option** — `raw` returns before any correction runs and `flatfield` forces `bg_method="none"`, so most of that section is dead on those stages. `correction_controls_enabled(stage)` is the pure rule for which controls each stage consults (radius / small-px / mesh are *background* parameters, so they are dead on `flatfield` too) and `_sync_method_rows` greys out the rest; it previously sat in the Display section two groups below the controls it silenced, which made an inactive panel read as a broken one. A muted caption under it states what it governs — "decides which controls below run — Raw runs none" — because "Stage" named a step of the pipeline rather than the effect on the panel, and a control whose meaning lives only in its tooltip has already failed the reader who did not hover. **The read-out** (`_status_text`, the "What you're looking at" box) states in words everything this tab can be ambiguous about, because none of it is visible in the picture: the frame; the stage and the correction *actually applied*; the displayed array's range/mean; the mask's provenance (`_mask_source`), label count, what the QC size filter removed, and whether it even fits the frame; and which rule drew what is on screen. `_render` records those facts in `state.display_facts` as it draws them, so the read-out reports what the renderer did rather than re-deriving it — a second derivation is a second chance to describe something that did not happen. The range is load-bearing rather than decorative: the LUT re-derives its window from the displayed array while it reads `auto`, so a correction can move the data while leaving the picture identical (for `fov_median`, a scalar subtraction, *pixel*-identical), and the numbers are how it proves it ran. **Binary mode is the sharpest case**: `build_frame_rgb` picks between three rules — a per-cell mean gate, a per-*pixel* threshold when no usable mask exists, and the whole labelled footprint when there is no per-cell verdict — which mean completely different things and render identically as white-on-black. `_binary_rule` names which one ran, and the read-out says so along with where the threshold came from (this tab, or the channel's app-wide ON value). Binary mode fills each label in **its own colour** by default (`state.binary_label_colors`, the *Colour cells* toggle): cells painted a common white merge into one shape wherever they touch, so a segmentation that failed to split two nuclei looks identical to one that split them correctly — which is the question the tab exists to answer. `frame_render.label_color_lut` is a golden-ratio hue walk indexed by label id, deterministic and RNG-free so a cell keeps its colour between redraws, with saturation and value stepping on their own irrationals so near-colliding hues still separate; consecutive ids land far apart, which is what matters because touching nuclei are usually numbered consecutively. The per-pixel fallback is left white and reported as such — it has no labels to colour, and colouring it would imply a per-cell distinction it never made. A **Cells** row sits directly under the Segmentation controls with the one number those controls are tuned against: `_count_text` formats the *same* `display_facts` the paragraph read-out uses — a formatting, never a second derivation — giving the labels drawn, what the QC filter dropped, and (only under the per-cell rule, the only one that reaches a per-cell verdict) how many clear the threshold. It reads `—` rather than `0` wherever there is no count to give — no mask at all, or live labels that no longer describe this frame — because zero is a measurement and unknown is not. Display goes through `core.frame_render.build_frame_rgb` (re-exported here as `build_playground_rgb`) and a binary/boundary overlay; per-source LUT memory, click-drag pan, frame-local binary threshold (defaults from but never writes back to the app-wide `ThreshFracOn`), and a QC size-cleanup preview are Playground-local. **Live StarDist preview** (the Segmentation section): a *Live StarDist* toggle, the two detection thresholds, and an explicit **Segment** button run `pipeline.stardist.segment_nuclei` on the frame currently displayed, so the preview is the pipeline's own inference rather than a lookalike — including the input scrubbing and near-constant-field refusal that make StarDist's uncatchable ClipperLib abort unreachable, which matters more here because an abort in the GUI process would take the whole application down. The inference itself lives in `services.frame_preview`, which holds the session's single loaded model and keeps the TF import out of a Qt module (this package is imported at GUI start-up). `worker.stardist_labels_for_frame` is the thin seam onto it; `request_stardist_labels` and `request_reference_fields` both marshal their result back through `core.worker_bridge.run_off_thread`, the one-shot queued-signal bridge (`ctx.runtime.playground.seg_bridge` / `.ref_bridge`). Both also **report while they run**, through that bridge's `progress` / `stage` signals, onto the shared status-row bar the smFISH Apply-to-All and the dataset load already drive (`_show_progress` / `_step_progress` / `_hide_progress`, all GUI-thread). The reference build is determinate — one step per frame decoded, which is where its wall-clock goes. Segmentation is **indeterminate**: one frame's inference has no interior milestones to count and the wait is dominated by a one-off model load, so a percentage would be invented; instead `segment_frame` names its two phases (`STAGE_LOADING` / `STAGE_SEGMENTING`) through an `on_stage` callable and the status line says which is running, because reporting the 10-30 s TensorFlow load identically to the seconds-long segmentation leaves a first Segment looking like a hang. The bar is hidden on **every** exit path including failure — one left sweeping under an error message is its own false statement. The GUI-thread correction (`correct_single_frame`) deliberately gets no bar: it is synchronous, so nothing could animate through it. Segmentation runs on the **corrected** frame whatever stage is displayed, because that is what the pipeline segments — `_segmentation_frame` pins the stage to `corrected` and returns a note naming the mismatch, so a nucleus count can never be read as belonging to a picture it did not come from. For the same reason `seg_key` excludes the display stage: switching Raw ↔ Corrected describes the same pixels and must not invalidate labels. `_mask_source` decides the overlay: live labels when they are current, the pipeline's saved mask when live mode is off, and **nothing** when live labels have gone stale — it must not fall back to the pipeline's mask under a status line describing live ones, which is one mask on screen and a different one claimed. The tab is the app's **only** interactive segmenter, and does two jobs: choosing settings before a run (Open image… a representative frame, tune, then type the two values into the Analyze form's matching fields) and diagnosing after one (a processed well, with the pipeline's own saved mask to compare against). The Analyze form is deliberately the only place the run's parameters are *stored*; re-typing two numbers is cheaper than a second, thinner preview that duplicates this one. |
| `features/image_browser/cell_browser/{controller,collect,worker,tab_view,criteria_view}.py` | Cell Browser gallery: a filtered grid of per-cell crops composed off-thread (`worker.CellImageLoader`, reusing the Image Table loaders + zip cache). Sources cells from the active **plate selection** (`collect.cell_browser_source_wells`). **Deferred render:** every control — filters (criteria/timepoint/FOV/sort), channel chips, the per-channel `LUT ▾` popover (LUT min/max + a colour swatch editing `tint_by_channel`), and the **Raw / Corrected** source `SegmentedControl` (`CellBrowserState.use_tophat`, threaded through `load_page` → `compose_cell_rgb` → `_load_array`) — only *stages* state; the single top-bar **Generate** button (`controller.apply_filters`) re-collects and re-composites. There is no separate "Apply filters" button. **Gate-source mode** (the sidebar's *From scatter gate* checkbox, `CellBrowserState.scatter_gate_mode`) swaps the numeric filter for the scatter gate(s): `criteria_view._set_criteria_enabled` disables the criteria rows + *Add criterion* and reveals a muted line stating why. The checkbox is deliberately sticky — nothing resets it on tab switch or dataset load — so that line is the only on-screen account of an editor that is otherwise inertly greyed out. |
| `features/home/groups/controller.py` | Home → **Groups**: replicate-set editing — adding a set from the plate picker, refreshing the rail map, and the Quick-Replicates pairing that chunks the current selection into groups of a chosen size along a Rows / Columns walk. The pure part of that (`make_replicate_pairs`) is here; the colour and membership rules it reads are `domain/selections_model.py`, which `core/` uses without importing a feature. |
| `features/export/{service,style}.py` | CSV export (`service.py`) + the floating Export Style sidebar's prefs + apply pipeline (`style.py`); `style.py` also owns `copy_figure_to_clipboard` (Copy SVG → native macOS pasteboard via pyobjc, Qt-clipboard fallback). |

Shared domain helpers (under `well_viewer/domain/`):

| Module | Role |
|--------|------|
| `domain/fold_change.py` | Fold-change normalization helpers (vs control well/rep-set, vs each member's first timepoint). Consumed by line / bar plot, batch export, and `features/export/service.py`. |
| `domain/control_ref.py` | `ControlRef` — the frozen `(kind, name)` a fold-change control is addressed by, plus its storage form. The kind travels beside the name because replicate-set names are unrestricted free text, so no single string can distinguish a well from a set that shares its token (§9.5). |
| `domain/fold_change_state.py` | `FoldChangeState` — the request object every collection path is handed, replacing the `(vs_control_on, control_label, vs_t0_on)` tuple it used to be. Read by name: it briefly emulated the tuple, which both broke the `__eq__`/`__hash__` contract and let a call site drop a newly added field by unpacking three values, and for this object a dropped field means raw values drawn under a fold-change axis. `coerce_fc_state` still accepts a tuple as input, since many tests spell a request inline; nothing hands one back. |
| `domain/fold_change_series_cache.py` | `ControlSeriesCache` — a per-redraw memo of control denominator series, keyed on the reference. The cross-axis stacking blocks resolve a control inside the member loop and each call rebuilds the whole series, so a 24-member plate over 6 timepoints issued 882 builds on the vs-control + vs-t0 path against 75 with the memo. It captures the threshold, value column, spread mode and gates it was built with and has no invalidation, so it is a collector local and must not outlive one redraw. |
| `domain/plot_members.py` | `bar_member_keys` / `line_member_keys` / `plotted_member_keys` — which members a plot draws, in the order it draws them, plus `member_replicate_sets` for the well → owning-set map. One question with one answer, because more than one consumer asks it: `ctx.runtime.barplot.current_keys()` returns replicate-set names whenever any set is active, while `collect_bar_items` emits **well tokens** once the pool-replicates toggle is off, so anything built on the runtime's list disagrees with the plot in that case. It reaches the bar tab's drag order through a runtime attribute lookup rather than an import, which is what keeps `domain/` free of `features/`. `tests/test_plot_members.py` asserts the keys **equal** those the collectors emit; that equality is the guard, not the extraction. |
| `domain/plot_order.py` | `apply_order` — reorder members so the ones named in a saved drag order come first, the rest in natural order. Pure list manipulation with no notion of what a member is. It lives here because the Line tab, the Scatter tab and member enumeration all need it, and while it sat in the Line controller the Scatter collector imported one feature from another to get at it. |
| `domain/stats_compute.py` | `compute_rep_stats`, `aggregate_well`, `aggregate_group` — the pure-math layer the bar / line / scatter controllers share. `aggregate_well` / `aggregate_group` resolve an optional delegate through `ctx.runtime.aggregate.well` / `.group`, which is the hook the pure-logic test `MockApp` uses to short-circuit aggregation (its `_aggregate_well` / `_aggregate_group` methods surface through the runtime's back-compat view); the production `WellViewerApp` provides no such delegate, so the value is `None` and the DataFrame path is taken at runtime. The result cache is keyed on everything that can move a number, including `_gate_cache_sig` — the scatter-gate state for **both** layers. Thresholding is in there because gate membership defines the on-population; inclusion is in there because "Use scatter gates" replaces the per-channel intensity floors, and the `fluor_gates` already in the key do not move when that checkbox is toggled. Mode-on-with-no-gates still differs from mode-off (the floors are bypassed either way), so the flag itself is part of the signature, not just the gate geometry. It returns `None` when neither layer is active, leaving a plain threshold-mode session's keys byte-identical. |
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
| `data/data_loading.py` | CSV parsing, channel detection, threshold-aware aggregation, per-well array helpers. `df_included_mask` is the single inclusion chokepoint — effective inclusion is `Included==1 AND QC_Pass==1 AND Crop_Pass==1 AND Gate_Pass==1` (the optional `QC_Pass` / `Crop_Pass` / `Gate_Pass` layers default to pass when absent), and every consumer (overlay, plots, stats, aggregation, export) composes it so QC debris-gating, the Segmentation crop, and scatter-gate inclusion apply uniformly. The `Included` layer is set by the Cell Gating tab's `GatingWorker` from the per-channel FluorGating intensity floors; that tab reports the composed result live — `cell_gating.controller.cell_gating_refresh_included_count` walks the row cache through this same chokepoint and renders `gating_compute.format_inclusion_counts` into a muted line under the gate controls ("Included for analysis: N / M cells · K failed QC"). It recomputes rather than reading the worker's own tally, for two reasons: the worker counts the gating layer alone, so pairing that number with a QC count gives a line whose figures do not reconcile (most QC failures sit inside the gating layer's kept set); and a dataset whose gates are all at their defaults skips the pass entirely on load, which would leave a worker-fed label blank on the commonest path. It refreshes on pass completion, on failure (flagged as such), while a pass is in flight (marked `updating…`), on Cell Gating tab activation — the catch-all covering crop edits, manual per-cell deletes and that default cold load — and from `ctx.runtime.qc.changed_cb`, a feature-agnostic seam `qc.recompute_qc_all` fires after rewriting `QC_Pass` so neither feature imports the other; the separate `Gate_Pass` layer carries the scatter-gate inclusion (the union of the Scatter Plot's rectangular gates via `scatter.gating.compute_gate_inclusion`) and, when "Use scatter gates" is on, *replaces* the intensity floors (the worker sets `Included=1`, `Gate_Pass=`membership). Keeping it a separate layer lets the **per-cell scatter** default to `df_included_mask(df, include_gate=False)` so it keeps showing the full cloud (and the gates over it) instead of filtering itself by its own gates, while every other view filters via the default. That tab's **Apply gates** toggle does *not* switch this layer on: it filters on the gate geometry instead (`collect_scatter_data(gates=…)`), because this column can describe a different set of boxes than the plot is drawing. `gates` defaults to `None` for every other caller (batch export, the CSV export service), so a batch job can never inherit the live tab's choice. Measurement columns are normalised to lower-case at read time so per-channel lookups are case-insensitive. `aggregate_with_threshold_df` defines the "on" population as `value > threshold`, and *which* threshold reaches it is decided in one place, because there are three scales and one number cannot serve them: shape/morphology has no ON cutoff and passes `-1.0` to keep every cell; an integer count column is cut against the channel's `ThreshCountOn`, which starts below the minimum a tally can take so a cell carrying no spots still counts (zero spots is a measurement, not a gap); a fluorescence column is cut against its `ThreshFracOn`. `domain/metric_registry.cutoff_kind_for_column` is the predicate that classifies the column and `core/channel_state_controller.on_cutoff_for_column` the resolver that turns that answer into a number — every aggregate path (bar, line, scatter-aggregate, heat map, stats, export, batch export) goes through the resolver rather than choosing for itself, and `tests/test_on_cutoff_sweep.py` fails when one reaches past it for a raw accessor. Feeding the wrong kind is not a rounding error: the intensity cutoff sits above the entire range a spot tally can occupy, so it empties the population and the plot draws nothing at all. `resolve_value_series` emits a deduped WARN naming the missing column when both the direct and case-folded lookups miss, surfacing schema-mismatch bugs that would otherwise show up as silently-empty plots. |
| `data/image_discovery.py` | Scans zips / loose directories for masks, overlays, tophat images. Classifies by suffix + schema. |
| `data/image_resolver.py` | Filename → kind classifier (`mask` / `overlay` / `fluor_processed` / `smfish`). Also the single home for `normalize_numeric_token`, the FOV/timepoint token normaliser that collapses `1`, `01` and `1.0` to one key. Every site that compares such a token routes through it: `review_image/collect.review_norm_fov` (the review side's public name, kept because the image-table and review-image call sites are written against it), `core/dataset_state._norm_fov` and `data/frame_load.norm_token` are aliases of it. Tokens reach the viewer from filenames, CSV columns and combo text, so a second copy that formats differently silently strands a filter. |
| `data/frame_load.py` | An address — `(well, channel-token, timepoint, fov)` — resolved to the float32 array it names, or to the int32 segmentation mask beside it, plus the channel-token vocabulary that decoding one needs (`base_channel`, the ` (binary)` / ` (mask)` / ` (raw)` suffixes, `NUC_SEG_TOKEN`). Three separate tabs ask this question — the Image Table composes a grid of them, the Cell Browser crops per-cell thumbnails out of them, the Playground corrects a single one — and each is independently removable, so the answer cannot live in any of them. **Resolving a render variant is addressing, not rendering:** `"GFP (binary)"` loads exactly the pixels `"GFP"` does, and only what the caller then draws with them differs, which is why the suffix vocabulary is here and the rendering it selects is not. **Two shortfalls are reported rather than absorbed**, through sets the caller owns and passes in: `scan_misses` (a well/channel scan that came up empty, so a pass says it once instead of once per cell) and `raw_fallbacks` (a cell that asked for the corrected top-hat image and was served the raw one — common, since a pipeline often corrects only some of a well's frames, and a grid silently mixing the two would look uniformly corrected). They are parameters, not something read off `ctx`, because they belong to the *caller's* render pass; the Image Table binds its own through thin wrappers in `image_compose`, and a caller with no pass simply gets its pixels. |
| `data/dataset_dims.py` | The four option lists every image grid starts from — which wells are loaded, which channel tokens can be shown (including each channel's binary variant, `NUC+SEG` when a nuclear token is known, and the user's ratio metrics as virtual channels), which timepoints and which FOVs exist. Each has the same two-tier shape: prefer what the run recorded in `pipeline_info.json`, fall back to scanning the loaded CSVs when it recorded nothing (older runs, custom schemas). The fallback is what keeps a thin-sidecar dataset usable and is deliberately second — the sidecar names every value the run produced, the cache only the ones whose rows happen to be loaded. The Image Table fills its axis dropdowns from these and the Playground fills its single-frame selectors from the same lists, so the two cannot disagree about what a dataset contains. |
| `data/inclusion.py` | The inclusion chokepoint's answer, keyed for an image view: `excluded_nids_for_frame` gives the nucleus ids in one frame that `df_included_mask` rejects, and `drop_excluded_labels` zeroes them out of a label image. An image view that draws straight off a segmentation mask shows curated-away cells as segmented, counted cells — the plots disagree with the picture and nothing on screen says which is right — and filtering the labels once fixes every render that reads them. The per-well map is memoised on the row frame's own `attrs`, keyed by `ReviewImageState.override_version` (the counter `invalidate_stats_cache` bumps on every manual delete, crop, QC and gating change) **and by the frame's identity**, so it dies with the frame it describes, cannot outlive an edit, and cannot be inherited by a derived frame — `attrs` propagates through `df.copy()`, where the counter alone would call another frame's map fresh. A build with no Review-Image slice has no counter to key on and simply recomputes. The all-included case — the common one — short-circuits before the per-row frame-token normalisation, which is the expensive part, and `drop_excluded_labels` returns the array untouched, so a dataset with no curation pays nothing at all. The filter's query is built at 64-bit width and narrowed to the ids the frame actually holds rather than cast to the label array's dtype — casting wrapped an id larger than `int32` into a negative one, and a wrapped value colliding with a live label blanks the wrong cell. A non-integer label array is returned untouched and a read-only one is copied. **An already-rendered grid is not repainted** when an inclusion layer changes: `invalidate_stats_cache` fires `ctx.runtime.image_table.inclusion_changed_cb`, which marks the table stale so the header cues a Generate. Re-rendering would be seconds of decoding per edit, and an edit comes in runs of ten; the cue is what keeps a stale grid from quietly disagreeing with every plot. |
| `data/cache_projectors.py` | Registry of the inclusion-verdict projectors, plus `project_verdict_column` — the one cache walk they all perform (resolve the target wells, skip empty / missing frames, hand the verdict the frame *and its well label*, write the int column, log a computation failure against its layer rather than raising). The label is passed because a verdict can be scoped to particular wells (the crop's `FrameScope`) and a cached frame need not carry a well column — this walk is the only place that knows which well each frame belongs to. `features/qc` and `features/crop` register here and share that walk; the registry is what lets `data_loading.get_rows` apply them without the data layer importing `features/`, and the shared walk lives here for the same reason — neither feature may depend on the other. |
| `domain/viewer_state.py` | `read_pipeline_info` + the `make_schema_extractor(sep, fov_idx, tp_idx)` factory used everywhere the app needs a `(fov, tp)` from a filename. It returns the channel-token lists both separately *and* inside the curated `pipeline_info` dict (`fluor_tokens`, `smfish_tokens`), so a consumer holding only that dict can answer "which channels did this run have?" without re-reading the file. An unreadable or unparseable sidecar yields an empty dict, which is how a caller tells "no sidecar" apart from "a sidecar naming no such channel". |
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
| `pipeline_info.json::cell_gating` | `cell_gating.py` | Cell-area + per-channel `fluor_gates` + `thresh_frac_on` + `thresh_count_on`, plus the scatter-gate inclusion state (`use_scatter_gates` + `scatter_gates`, each gate carrying its `x_col`/`y_col`) written only when that mode is on. Only values that differ from their default are written, and the ThreshFracOn default is per-channel: `domain/gating_state.default_thresh_frac_on` returns 50.0 for a readout channel and 0.0 for the dataset's nuclear/segmentation channel (`pipeline_info.json::nuclear_token`, held on `ChannelState.seg_channel_token` and reached through `channel_state_controller.seg_channel_token_of`). That channel is what nuclei are *found* with, so an ON/OFF brightness cut on it has no meaning and the readout default only hid nuclei from the Image Table's "≥ ON threshold" view; the pipeline's own auto-threshold pass writes it `0` for the same reason. The block builder has to know which channel it is, or a nuclear cutoff the user deliberately raised to 50 would diff as "at default" and be dropped. `thresh_count_on` is the same shape for the *count* metrics — a separate row because a spot tally and a brightness are different scales — and needs no such channel knowledge: `default_thresh_count_on` answers -1.0 for every channel. A sidecar written without the key is not migrated; the accessor's default applies, which is the value that lets a count plot draw at all. Three call sites build this block — `persistence/cell_gating.py`, the saved-plot capture (`features/saved_plots/collect.py`) and the auto-threshold pass (`data/auto_threshold.py`) — and the latter two reconstruct it from named keys rather than editing in place, so a key neither reads is deleted on write. |
| `pipeline_info.json::smfish_thresholds` | `smfish.py` | Per-channel smFISH spot-detection threshold (`{channel: float}`). Saved only on a successful Apply Global Threshold so the value the spinbox shows matches the spot counts baked into the per-well CSVs. Restored on dataset open and on channel-combo switch. |
| `pipeline_info.json::sample_definitions` | `sample_definitions.py` (top level) | Saved selections + replicate sets / groups + well labels + notes. **Auto-saves** on edit via the registry (`autosave_to_pipeline_info`, a silent variant — no dialogs/toast, no-op without a data dir) in addition to the explicit Save button; well-label cells and the Notes box commit on `editingFinished` / focus-out (not per keystroke), discrete rep-set/group edits via `rebuild_all`. |
| `persistence.json::crop_region` | `crop_region.py` | The crop rectangle drawn on the Segmentation tab (`DatasetState.crop_region`, source-mask pixel tuple) **and the frames it is scoped to** (`DatasetState.crop_scope_*`, `null` per field meaning every value). Written as `{"region": [...], "scope": {...}}`; a bare four-element list is the older shape and reads as an unscoped crop. The scope is persisted because it is part of what a saved crop *means* — reloading the region without it would widen a two-well crop to the whole plate and move every number downstream, where the drag-delete's scope is deliberately transient by the opposite argument (it arms a gesture, not a stored verdict). A malformed scope resolves to un-armed rather than to a guess: an unreadable scope must widen to the documented default, never narrow to a set nobody chose. Feeds the `Crop_Pass` inclusion layer; an absent / cleared crop removes the section. |
| `persistence.json::ratios` | `ratios.py` | User-defined ratio channels. |
| `persistence.json::heatmap_layouts` | `heatmap_layouts.py` | Saved heatmap layouts + cmap/scale state. |
| `persistence.json::line_order` | `line_order.py` | Line plot replicate-set / well draw order. |
| `persistence.json::view_state` | `view_state.py` | Active tab, selected wells, channel/metric, fold-change scopes, per-tab combos, export-style prefs. Saved on dataset switch + app close. |
| `persistence.json::qc_params` | `qc_params.py` | QC debris-gating thresholds edited on the Home → Cell Gating tab (the fundamental always-applied inclusion layer); seeded from `pipeline_info.json` `qc_options` when unset. |
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

**Resetting saved state — the scope model.** `persistence/reset_scopes.py` is
the one map from what a user names to the state that name owns. State is
modelled in two tiers and both title-bar reset buttons are expressed in them:

- **View** — how the data currently looks: selected wells and group visibility,
  fold-change scopes, per-tab controls, plot and figure preferences, draw order,
  the heatmap arrangement, the Multivariate configuration, the Image Table grid.
  Cheap to rebuild and carrying no scientific decision, it resets as a whole:
  `reset_view(ctx)`, behind the mode band's `rotate-ccw` button.
- **Analysis** — the decisions themselves. One `ANALYSIS_SCOPES` entry each for
  sample definitions, gating, curation, ratio metrics, smFISH thresholds and
  Batch Export groups, picked individually from the checklist behind the
  `trash-2` button (`core/reset_scopes_view.py`), then cleared by
  `clear_analysis(ctx, keys)` and followed by a dataset reload.

The dataset's own image and CSV files are a third tier no reset touches.

A scope names every storage **domain** it owns, and its `clear` is responsible
for all of them — the live typed state and every file the value is written to,
whether that is `persistence.json`, `pipeline_info.json`, or a standalone
sidecar. That is the property the design turns on, because the same value is
often stored twice: plot groups live in both `persistence.json::plot_groups` and
the `sample_definitions` block of the sidecar, and clearing one of the two is a
no-op once the loader (which treats an absent section as "nothing to say", so a
folder shared without its sidecar keeps its groups) re-hydrates from the other.
Manual cell curation is in neither file — it has its own `cell_overrides.json`.

`tests/test_reset_scopes.py` asserts the scopes **partition** the domain set
exactly: every name the persistence registry registers, plus the two sidecar
domains (`cell_gating`, `smfish`), is claimed by exactly one scope. A new
persistence domain that belongs to no scope fails the suite. Its companion
`test_the_partition_guard_bites` proves the guard fires on both halves of that —
an unclaimed domain and a claim on a domain that no longer exists.

Two consequences worth stating, because both were bugs before the model existed:

- **Reset one domain at a time, never the file.** There is deliberately no
  "delete `persistence.json`" helper. Deleting the shared document meant one
  wrong argument at one call site could silently spare every section at once,
  and the failure was invisible because the reload afterwards looked like a
  reload of reset state.
- **What resets live must equal what is dropped from disk.** `view_state.reset`
  restores `PlotState` and `ExportPrefsState` generically via
  `domain/state_defaults.restore_defaults` (so a field added to either slice is
  covered without anybody extending a reset), then **re-saves** the section
  instead of deleting it. Screen and disk therefore agree by construction rather
  than by two lists staying in sync.

`reset_scopes.py` imports no feature module. It reaches a built feature's
widgets through callbacks the feature stamps on its `ctx.runtime.<feature>`
namespace — `qc.refresh_widgets_cb`, `image_table.reset_cb`, alongside the
existing `qc.changed_cb` — and recomputes derived columns by clearing the row
cache so the registered cache projectors re-derive `QC_Pass` / `Crop_Pass` on the
next fetch. An unstamped callback means the lazily-built feature was never
built, so there are no widgets to refresh. The Segmentation tab's own "Reset
crop + curation" menu action calls the same `clear_curation` the Curation
checkbox does (`crop.controller.reset_crop_and_overrides_now` wraps it with the
crop-mode exit, the Crop-scope button relabel and the redraw only that entry
point needs), so the two cannot drift. `clear_curation` widens the crop scope
back to every frame along with dropping the region: a scope left armed would
silently narrow the *next* crop the user draws to one they had just reset away.
The Segmentation tab's **Edit ▾ → Clear crop** clears both for the same reason,
so the two entry points leave the same state — and because the persisted section
is dropped once there is no region to save, a scope kept in memory there would
not survive a restart, leaving the running app and the file disagreeing.

Each clear writes through `PersistenceRegistry.save` rather than
`schedule_save`: the reload that follows would otherwise race the debounce timer
and read the pre-reset value back.

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
| `DatasetState` | data dir, well paths, row cache, canonical timepoint / FOV lists, `pipeline_info`, and the crop: the rectangle (`crop_region`) plus the frames it filters (`crop_scope_wells` / `_fovs` / `_tps`, where `None` means *every* value of that field and an empty set means none). The scope is persisted with the region and cleared on a dataset swap — it names one plate's wells, and carrying it into the next either quietly filters nothing or filters the wrong thing | `core/dataset_state.py` |
| `PlotState` | SEM/SD, per-FOV, legend, draw order, fold-change normalization, the bar **significance-bracket** toggle (`show_significance`, N7), and the shared **focus timepoint** (`focus_timepoint`, N5 — the one cross-tab timepoint the Heat Map / Distribution combos read/write, synced via `core/timepoint_sync.py`), and the **time-axis label multiplier** (`time_scale`, 1.0 = off — see `core/plot_style.py` above) | `core/plot_state.py` |
| `StatsState` | Statistics-tab comparison groups + the structured **pairwise** results (`pairwise`, N7 — fed to the Bar plot's significance brackets). The group-statistics result cache lives in core (`AppState.stats_cache`), not here. | `features/stats/state.py` |
| `ReviewImageState` / `PreviewState` | Review-image view transform + caches; the armed drag-delete scope (`delete_scope_wells` / `_fovs` / `_tps`, where `None` means "the displayed one" and an empty set means "none" — the inverse of `ImageTableState`'s empty-means-all, and never persisted, so a wide scope cannot survive a restart or a dataset swap); preview image maps (`fluor` / `overlay` / `mask`) shared by the Segmentation review-image and Image Table | `features/image_browser/review_image/state.py` |
| `ImageViewState` | Cross-view image display shared by the image viewers. The per-channel display LUT (`lut_by_channel`), so a brightness tuned in Review Image is the default an *auto* Image Table row inherits (per-row edits override). The **Cell Browser** participates too: its `LUT ▾` popover edits the same `lut_by_channel` (blank = auto per crop), and a per-channel **display colour** (`tint_by_channel`, `get_tint`/`set_tint`) overrides the additive composite tint a channel is painted with (`worker.resolve_channel_tint` — override wins, else the fixed per-index palette) | `core/image_view_state.py` |
| `PlaygroundState` | Playground frame selection (dataset well/channel/timepoint/FOV **or** a loose image path) + chosen correction method/params, the reference-well fields keyed by well+channel+mode (`ref_fields`) and the builds that produced nothing (`ref_missing`), the live-StarDist thresholds and last label image with the key it was computed from, per-source LUT memory, frame-local binary threshold, QC size cleanup, and zoom/pan + caches | `features/image_browser/playground/state.py` |

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
  `_is_tab_visible`: a group edit on Home → Groups no longer
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
  finish first. Split auto-exits on a mode switch (connected to
  `app.modeChanged`), on rail navigation (`split_nav_intercept` in
  `_on_section_nav_changed`), and at the **start of a dataset load**
  (`load_controller.load_directory`) — the load restores view-state + redraws
  against the single-notebook layout, so leaving split first avoids driving
  detached pages (the reported open-dataset crash).

  It is also a **Review-only feature**: both panes it splits are Review tabs,
  but the top-bar toggle it lives in is shared across all three modes (Review,
  Analyze, Datasets mount into the same `WellViewerApp` central-pane stack —
  see §6b). The same `modeChanged` handler that exits an active split also
  hides the toggle button (and its right-pane dropdown, if open) outside
  Review and shows it again on returning — previously it just sat in the
  Analyze/Datasets top bar with nothing for it to act on.

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

### 6.1 `analyze_tab.py` / `analyze_tab_form.py` / `analyze_tab_log.py`

The Qt form that builds the input panel (folder pickers, channel tokens,
top-hat radii, segmentation method, smFISH options, worker count) plus a
live-streaming log panel and a Run / Stop button. `AnalyzeTab` itself is a thin
host: it owns the runner, the queue drain (`_poll_log`), the run-state latch and
the signals a job queue binds to, and it mixes in the three modules that hold
everything else.

**The drain runs in bounded slices, and writes its lines in batches.** Both
because it is on the GUI thread and the queue is fed by a subprocess that can
talk faster than any painter. It used to loop until the queue reported empty —
which a remote run's segmentation phase does not do for minutes — and to write
each line through its own insert. The scroll bookkeeping around an insert reads
`verticalScrollBar().maximum()`, and on a `NoWrap` document that forces a full
re-layout, so a line's cost grew with the document under it: 3.4 ms into an
empty log, 19.1 ms at three thousand lines, 57 seconds for the batch. The
window answered nothing throughout, which is how the "Show raw log" checkbox
came to be unclickable exactly when there was most to read. `PipelineRunner`'s
`_LOG_QUEUE_MAXSIZE` had long anticipated the same failure in its own comment —
it caps the queue so "a stalled UI thread" cannot exhaust memory. A slice now
ends on `_POLL_BUDGET_S` or `_POLL_MAX_LINES` and re-arms immediately when work
remains, and `_log_lines` writes a slice's lines in one insert per colour run:
0.03 ms per line, flat. Terminal `done` / `error` lines flush the buffer first,
so a run's summary cannot land above the output it summarises. Each of the three files carries a line budget
(`test_monolith_budgets.py`).

The split follows the pane's three jobs. *Gathering* what a run is launched with
is `analyze_tab_form.AnalyzeTabFormMixin`: anything that reads the form's
widgets lives with the widgets, so `_collect_run_options`, the well-field
validators and the entry-boundary numeric parsers (`_safe_qc_float`,
`_safe_seg_float`, `_numeric_option_errors`) are all there. *Driving* the run is
the tab plus `analyze_run_spec` / `analyze_suspend`. *Showing what the run said*
is `analyze_tab_log.AnalyzeLogPaneMixin`, described below — the log view, the
raw-log toggle, the run-outcome surfacing and the idle "Last run ·" card. That
last mixin is inherited by `AnalyzeTab` directly rather than through the form
mixin, so the outcome path is not reached through the class named for form
building. Verbose inline helper paragraphs are collapsed behind hover `ⓘ` icons
(`_info_icon` / `_section(info=…)`).

The form's own chrome — `_section` (a collapsible titled section), `_row`,
`_entry`, `_info_icon`, and `reveal_section` — is `analyze_tab_chrome.AnalyzeFormChromeMixin`,
inherited by `AnalyzeTabFormMixin`. `reveal_section(title)` unfolds a folded
section on demand: the fold-by-default rule is right for a user filling the form
in and wrong for a control something else just changed on their behalf, which
otherwise leaves no visible trace at all (see `_open_dataset_in_analyze`).

**A failed run says so where the user is looking.** The runner turns a non-zero
exit into an `("error", …)` event, and the log view that event's text lands in
is hidden at build — on its own that makes a pipeline which refused to start
indistinguishable from a run that was never clicked: progress stops, the Run
button returns, nothing states a reason. `_surface_run_failure` is the whole
outcome path, and it is in the log mixin because the pane it has to open is.

`set_failed` — the same dashboard failure state every Datasets tool uses —
takes the message's first sentence, capped, since the headline is a wrapping
label above the plate and a several-hundred-character refusal pushes the plate
off screen; the untruncated text goes to the same call's `tooltip=`, which is
`set_done`'s contract too. `PlateProgress.clear_outcome` drops both the tooltip
and the outcome colour, because neither belongs to the run that follows: a
tooltip nobody clears makes a hover over a *successful* plate report the
previous run's failure, and a colour nobody clears paints the next run's live
progress and its "Complete —" line in danger red. Every Datasets tool was
immune to the second of those by accident — each calls `set_done` on success,
whose green overwrites the red — so it is the base class's job, not the
callers'.

A **Stop or Suspend is not a failure**: both end the subprocess by signal and
arrive as the same kind of event, so both take the amber `set_cancelled` and
neither opens a pane the user had closed. Which of the three it was is read
from `PipelineRunner.cancel_state_at_failure`, a snapshot taken when the
failure is *queued*, not from the live `was_stopped` / `was_suspended`. The GUI
drains one timer tick later, and a Stop clicked inside that window would
otherwise turn a genuine failure amber and silent.

The raw log is **open by default** and the "Show raw log" toggle is there to put
it away. Several things are said in that pane and nowhere else — why a run
refused to start, the volume that filled, the `errors/` folder a partial run
points at, the scheduler's queue-wait breadcrumbs — and the app used to have to
override its own closed-by-default to show them, which is the tell that the
default was the wrong way round. The six Datasets tool pages
(`datasets/_common/log_panel.py`) still start theirs closed, deliberately: each
reports a short, bounded job whose dashboard is the whole story.

`force_raw_log_open` therefore covers the user who has closed the pane and then
hits a failure: it re-opens it, because the explanation is in there and they
have no reason to know it at the moment they need it. That tick changes a
setting the user chose, so it is recorded rather than kept:
`_set_running_ui_state` calls `restore_raw_log` at the start of the next run,
which puts the toggle back unless `_on_toggle_raw_log` saw the user work it
themselves in the meantime, in which case the choice is theirs and stands. A message that names a collapsible
section unfolds it, for the same reason `reveal_section` exists at all — naming
a remedy the user cannot see is the same dead end as naming none.

**The earliest error is the cause; everything after it is that cause's
fallout.** That is one rule with two enforcement points, and they must not
disagree, because the second is what the first reports. `_run_error` latches the
first `("error", …)` event of a run and `_surface_run_failure` is called only
for that one, so the dashboard headline and the message `runFinished` carries
are the same string. Inside a single event, `pipeline_runner.exit_message`
quotes the earliest `ERROR` line still in the runner's tail ring beside the
exit code, because "Pipeline exited with code 2" names nothing and for a run
refused before any work it is the only thing the user would otherwise be given.

"Earliest" is over the lines that can *be* a cause, which is not every line
`classify_log_line` scores ERROR. Two shapes are error-level and explain
nothing: the pipeline's own end-of-run summary ("N well(s) failed or produced
partial output — exiting non-zero…"), which restates the exit code and is
emitted *after* the per-well error that produced it, and a traceback's
`Traceback (most recent call last):` header, which scores ERROR on the word
"traceback". Both are dropped first. A traceback is then the one shape read
bottom-up — Python prints frames first and the exception last, and the
intervening `raise Foo(…)` source lines carry the exception's own name — so
inside one the *last* candidate is the reason. With nothing left after the
filter, quoting the summary still beats the bare code.

The `finished` branch gates `_refresh_last_run_summary` on the run having
succeeded: the card's only test is that `pipeline_info.json` exists, and the
pipeline writes that *before* any image work, so on a failure it would describe
a plate that was never produced.

The per-method segmentation parameters are their own mixin,
`analyze_tab_seg_params.AnalyzeSegParamsMixin`: the `NamedPageStack` of
per-routine pages (StarDist / watershed / Cellpose / label-free / classical) plus
the StarDist detection thresholds, which sit *below* the stack because both
StarDist routines share them and ride the stack's own `currentChanged` for
visibility (so no second hook in `_refresh_segmentation_hints` is needed). The
hints and per-method validation (`_refresh_segmentation_hints` /
`_segmentation_validation_errors` / `_collapse_seg_param_stack`) live here too,
beside the panel they drive. Every widget still lands on the `AnalyzeTab`
instance under the attribute name it had when this lived in the form, so
`analyze_tab_pinfo` and `_collect_run_options` reach them unchanged.

Restoring a prior run's settings is another mixin,
`analyze_tab_pinfo.AnalyzePipelineInfoMixin` (`_open_pipeline_info` →
`_pinfo_flat` → `_apply_pipeline_info`), inherited by `AnalyzeTabFormMixin` so
those names stay reachable on the form where every caller expects them. Three
paths arrive there: the "Load previous run" action, the OME converter's
channel-role handoff, and Validate & Repair's "Complete this run in Analyze".
`_pinfo_flat` prefers the sidecar's `execution_options` (the full `vars(args)`
snapshot the pipeline records) and falls back to the top-level/`qc_options` keys
for older sidecars; `None`-valued keys are dropped so a sparse sidecar can't
clobber widget defaults with blanks.

The **Incremental — process only missing FOVs** checkbox in Output & Compute
carries `--incremental` into the run: each well's already-recorded
`(fov, timepoint)` pairs are read from the output folder and only the missing
ones are processed, then merged into the existing per-well CSV and
`<WELL>_out.zip`. Without it a well whose out-zip and CSV both exist is skipped
whole however few of its FOV-groups landed, which is why a crashed run cannot be
completed by pressing Run again — the checkbox is the run mode Validate &
Repair's completion handoff ticks on the way in, and the pipeline ignores it
when `--force` is also set. `select_incremental_scope` may still switch a run to
a full `--force` reprocess during input resolution (loose images landing under a
cross-well flat-field/background model), but it never clears a `True` the form
set: with nothing appended it returns before touching the key.

Rendering the run's progress is a third mixin, `analyze_progress.AnalyzeProgressMixin`:
`_apply_progress_event` turns each `ProgressTracker` event into dashboard state
(headline, overall bar, per-well plate colour, per-well FOV and transfer dials),
and the ETA cluster (`_update_eta` / `_render_eta` / `_stop_eta_timer`) projects a
deadline from the cumulative per-well rate and ticks it down at 1 Hz between
events. It answers to one rule: **what it draws must never overstate what the run
achieved.** A run that finishes with fewer wells than it announced gets a headline
naming the shortfall ("Complete — 28 of 40 well(s) processed; 12 never reported
completion"), a bar left short of full, and a warning line in the log; a well that
announces completion twice is reported rather than folded into the count.

Picking an input folder through the browse dialog also **auto-fills the schema
and channel fields** from what the folder's filenames look like:
`_browse_input` → `analyze_input.AnalyzeInputMixin._autofill_from_input_scan`
briefly samples the names via `util.schema_infer.infer_from_dir`, which reads all
three grouped layouts — loose TIFs, the members of per-well zips, and the files
inside per-well `<WELL>/` folders — looking in both the selected folder and its
`in/`; it guesses the separator, slot layout, channel tokens and their roles (roles
via `util.channel_roles`, the same heuristic the OME converter uses), and applies
the guess through the existing `_apply_pipeline_info` path. It is best-effort and
editable — a folder whose names don't parse leaves the form as the user set it —
and only the interactive picker triggers it, so the converter's "Open in Analyze"
handoff keeps supplying its own channel roles instead of being re-guessed.

**A guess must be visible and must not destroy.** The fields it writes look no
different from hand-entered ones, so `_report_scan_guess` names the channels it
found and which of them took the segmentation and phase roles in the run
dashboard's status line; otherwise a wrong guess first surfaces in a sidecar
after a run. A scan that recognised no channel slot contributes no channel keys
at all, and `_apply_pipeline_info` treats an absent `fluor_tokens` key as "leave
the rows alone" — the same rule its scalar setters follow. An empty list reaching
`_set_fluor_rows` reads as "no channels" and collapses the rows to the single
default one, which would wipe a hand-typed list.

The sample the guess is built from is **strided, not a leading slice**
(`schema_infer._spread`). Plates are routinely written with one slot varying
slowest — channel-first filenames, or a per-well archive holding all of one
channel's fields before the next — so the first few hundred names of a sorted
listing can hold a single channel token. A slot with one distinct value is not
recognised as the channel slot at all, so a prefix sample reports *no* channels
on exactly the largest plates.

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

Both presence checks go through `input_resolution_service.well_present_in_input`,
which accepts any of the pipeline's input layouts (per-well `<WELL>.zip`,
per-well `<WELL>/` folder, or loose TIFs parsed via the schema's `well` field).
The form validates the *selected* folder, but a folder holding an `in/`
subfolder — the ordinary `<dataset>/in` + `<dataset>/out` layout — feeds the
pipeline from that `in/`, not the parent (see `resolve_input_output`). The
grouped-layout lookup therefore searches both the selected dir and its `in/`, so
a well packaged as `in/<WELL>.zip` / `in/<WELL>/` is not reported missing when it
lives one level down.

**One layout decision, rendered twice.** Analyze asks "what will this folder do?"
under the input field the moment a folder is picked, and again when Run is
pressed. `services.input_resolution_service.plan_input_layout` is that decision,
and it is pure: it reads the filesystem and returns an `InputPlan` naming the
action, the `(input_dir, output_dir)` pair the pipeline will be handed, and the
one-line message the user sees. `AnalyzeTab._refresh_output` displays the plan;
`resolve_input_output` executes it. The GUI holds no layout rules of its own,
which `tests/test_layout_readout_single_source.py` enforces by requiring the
readout label to be written from a value rather than from a hand-composed
sentence.

The actions are the layouts. A folder **named** `in` is the input directly
(output `../out`); a folder that **contains** `in/` feeds the pipeline from that
subfolder (output `<selected>/out`). Either way the same classification runs on
the grouped folder: loose TIFs beside well zips are folded into those zips
(`fold_loose`), loose TIFs on their own are grouped into per-well folders in
place (`group_loose`), per-well zips or per-well folders are used as they stand
(`grouped`), and a folder holding something else entirely is still handed to the
pipeline (`unrecognised`) with the readout saying what was not recognised —
`in/Plate1_A1.zip`, or a timepoint level between `in/` and the wells, is input
this module cannot name wells for but the pipeline is what reads it. More than
three loose TIFs at the *selected* root outrank an `in/` with nothing usable in
it (`zip_root`), because that is a fresh drop beside a folder an interrupted run
left behind, and it still has to reach `WellPlateZipper`. What remains is
unrunnable and says which: an `in/` that exists and is empty (`empty_in`), a
packed OME-TIFF (`packed_ome`, redirected to the Datasets converter), a path
that is not a directory, or nothing usable at all.

Both selections reaching the same verdict is the point of classifying them
together. `count_expected_wells` takes its grouped root from the same plan, so
the dashboard's denominator counts the folder the run will actually read; it
stays best-effort by design, falling back to the loose-TIF count and then to 1,
because a progress denominator cannot be zero — saying there is no input is
`resolve_input_output`'s job, not the dial's.

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

**Collecting a run's configuration is split from starting it**, in
`analyze_run_spec.py`'s `AnalyzeInputMixin`-style mixin `AnalyzeRunMixin`
(mixed into `AnalyzeTab` alongside `AnalyzeInputMixin`/`AnalyzeTabFormMixin`).
`_collect_run_spec()` turns the live form into a `services.run_spec.RunSpec` —
a frozen, JSON-round-trippable dataclass (`opts`, the chosen remote's *name*
rather than its resolved `RemoteHost`, cleanup/dry-run flags, output filter,
selective-channels flag). `start_run(spec)` launches a `RunSpec` on the tab's
own `PipelineRunner`, re-resolving the remote by name through
`services.remote_config.get_remote` at call time (so a `remotes.toml` edited
between collection and launch is honoured, and a deleted remote fails the run
with a clear message via `RunSpecError` rather than silently running locally)
and re-resolving the pipeline script fresh rather than trusting a stored path
(which would break across a relaunch from a different install location).
`start_run` never raises and never shows a dialog — it returns an error
string, so it stays callable with no form on screen; `_run()` itself is a
three-line delegation (`_collect_run_spec()` → `start_run()` → show a dialog
on failure). This split exists so the Job Queue (§6b) can persist a run's
configuration as plain JSON and start it later, independent of whatever the
live form happens to hold at that moment. The queue's own "+ Queue" entry
point on this page is built here too. The whole run-controls row —
Run / Suspend / + Queue / Stop — is `_build_run_row`, called by
`analyze_tab_form.py` in one line, both because that file is at its line
budget (moving the row out lowered it) and because the queue button belongs
beside the queue hooks. Its layout is a `widgets/flow_layout.FlowLayout`, not
a `QHBoxLayout`: the four buttons want 398px and the panel is `RAIL_W` (329)
wide and *fixed*, so a box layout cannot fit them and simply compresses the
labels. FlowLayout also watches its children for show/hide, which this row
needs — "+ Queue" appears only once `DatasetsTab` injects a sink via
`set_queue_sink` (which is how the run reaches the queue without this module
importing it), and the row has to re-wrap when it does. An unset sink hides
the button, which is what a build without the Datasets pane gets.

`analyze_job_edit.py`'s `AnalyzeJobEditMixin` (mixed into `AnalyzeRunMixin`,
since it is part of the same queue-facing surface) supplies `hydrate_job`:
the **full inverse** of `collect_spec`, used only by the queue's edit flow.
`show_job` is not that inverse and is not meant to be — it sets the input
folder and nothing else, because a preview only has to make the page *look*
like the job while the queue re-derives a real `RunSpec` from the persisted
JSON at dequeue time. Collecting a previewed page back over a stored spec
would replace every option the preview did not write, in a spec the user
believes they edited one field of, so the two paths stay separate.

Most of the restore is `AnalyzePipelineInfoMixin._apply_pipeline_info`, whose
`execution_options` branch already takes argparse-style keys — and a job's
`spec["opts"]` is argparse-style, being what `build_pipeline_args` consumes.
It is not identical, though, and every difference is a silently wrong value
rather than a crash: `raw` here is the sidecar's `input_dir`;
`auto_threshold` is positive here and recorded as its negative
`no_auto_threshold` there; `auto_threshold_method` is explicit in a spec but
*inferred* from whether both control wells are named when restoring a
sidecar, which is wrong for an Otsu run carrying dormant control wells; the
two StarDist thresholds are raw text where blank means "the model's own
default", while `_restore_stardist_thresholds` skips anything it cannot read
as a float and would leave the previous job's number in the field; and
`remote_batch_size` has no sidecar equivalent at all. `RunSpec`'s
`remote_name`, `cleanup_remote` and `dry_run_cleanup` live beside `opts`
rather than inside it and no sidecar restore touches them.
`tests/test_analyze_job_edit.py` is the guard — a `collect` → `hydrate` →
`collect` round trip over a spec whose every value differs from the form's
default (asserted, since a round trip over defaults restores nothing and
passes anyway), plus a key-set comparison that fails as soon as an option is
added to the form with no hydration counterpart. Two of the cases need shapes
the obvious fixture does not produce: a spec whose method and control wells
agree round-trips even when the method is never restored, and blank
thresholds only prove anything when hydrated over a form already holding real
numbers. Note also that `cleanup_remote`/`dry_run_cleanup` are pinned by
`_refresh_remote_controls` for a local run and gated on each other for a
remote one, so not every combination of them is a spec the form can collect;
hydrating an impossible one normalises it.

`AnalyzeTab.runFinished(ok: bool, message: str)` is a **positive-latch**
completion signal: `_poll_log`'s `"done"` branch sets a per-run `_run_ok`
latch, `"error"` records the first message seen, and the `"finished"` branch
emits based on that latch — never on "no error was seen", since several of
`PipelineRunner._run_pipeline_thread`'s early-return paths (stopped before
launch, a cancelled zipper spawn) reach `"finished"` having emitted neither
`"done"` nor `"error"`. The emit is the **last statement** of that branch,
because a job-queue-driven slot connected to it may start the next run
synchronously, re-entering `_set_running_ui_state()` — anything after the
emit would clobber that new run's state. `AnalyzeTab.jobFinished(ok, message,
out_dir)` is a second, separate signal for the Job Queue specifically (kept
distinct so `runFinished`'s two-argument shape stays exactly what it always
was), emitted immediately after `runFinished` for the same reason. A user
Stop makes the pipeline subprocess exit non-zero, which surfaces as the same
kind of `"error"` event a genuine failure would — `PipelineRunner.was_stopped`
(readable after completion, cleared only at the *start* of the next run) is
how a caller tells a cancel apart from a real failure; `AnalyzeRunMixin.was_cancelled()`
exposes it under the name the Job Queue's bridge looks for.

**Single packed OME-TIFF input — the Datasets converter.** A plate scan can
arrive as one OME-TIFF holding every timepoint, channel, Z-plane and stage
position. De-interleaving it is a **standalone converter** in the Datasets pane
(`datasets/ome_import/`, §6b), *separate from processing*: it writes an ordinary
dataset folder, which the user then opens in Analyze and runs like any other
input. Keeping the two apart is deliberate — when the explode ran *inside* an
Analyze run the wells only existed mid-run, so pre-run checks that look for a
well (an `empty_well` background, control-well auto-threshold, both via
`well_present_in_input`) failed against the raw `.ome.tif`. Analyze now only ever
processes **folders**; `plan_input_layout` classifies a raw OME-TIFF as
`packed_ome` and carries `PACKED_OME_MESSAGE`, so the readout under the input
field and the run-time refusal (`analyze_input.AnalyzeInputMixin._ome_input_hint`,
which the pre-run validation reads) redirect the user to the converter in the
same words instead of trying to run it.

The conversion itself is `pipeline.ome_explode.explode_ome_tiff`. It reads the
OME-XML for dimension order and channel names, **max-projects Z**, maps each
stage position to a 96-well label via `util.plate_grid`, and writes the planes
named
`<experiment>_<channel>_<well>_f<fov>_<timepoint>.tif` **directly into per-well
`<destination>/in/<WELL>.zip` archives** — the exact layout `resolve_input_output`
accepts as ready input, so Analyze uses it with **no** `WellPlateZipper` pass.
Wells are converted **in parallel** across a `ThreadPoolExecutor` (`workers`,
0 = auto), each worker owning a disjoint set of wells and its own read handle and
writing that well's archive alone (via `util.zip_write.atomic_zip`, so no locking
on the zip handles). Writing per-well zips also collapses what a loose-TIF explode
would cost in per-file fsyncs (one per plane — tens of thousands) into two per
well, and a cancelled/failed well leaves **no** partial archive (`atomic_zip`
discards its staging file). A read-vs-write timing line is logged so the dominant
cost is visible; the parallelism grain is per-well, so a degenerate single-well
grid stays serial.

**The file already knows most of the layout.** An OME-TIFF's `<Plane>`
elements carry `PositionX`/`PositionY` — where on the stage each image was taken
— and `read_ome_layout` collects them into `OmeLayout.stage_positions` from **both**
sets of metadata an OME-TIFF carries. The OME-XML's own `<Plane>` elements come
first, and where they have no coordinates the *instrument's* metadata is read
instead: Bio-Formats copies the original file's metadata into the OME-XML as
`<OriginalMetadata>` key/value annotations, and `tifffile` parses the vendor's
own tags into blocks of its own (`micromanager_metadata`, `imagej_metadata` and
the rest of `_VENDOR_BLOCKS`). Both are flattened to `{path: leaf}` and scanned
by `_paired_coordinates`, which pairs an X key with the Y that shares its *stem*
— "Position 3 XPos" belongs with "Position 3 YPos" and nothing else — so a stray
coordinate elsewhere in a block cannot pull a pair out of line, and fewer than
two complete pairs yields nothing at all. `OmeLayout.metadata_blocks` and
`metadata_key_sample` are collected on the same pass and decide nothing: they
exist so a file that yields no layout can report what it *does* hold, since
"no stage coordinates found" and "looked in the wrong place" are otherwise the
same sentence and telling them apart needs the file itself. The `<Plane>` collection is **per
`<Image>`**: a multi-image plate scan is one `<Image>` per stage position and
every one of them numbers its planes from `TheT="0"`, so collapsing duplicates
document-wide keeps the first image and discards the rest of the plate.
`datasets/ome_import/stage_layout.py` reads a plate layout back out of them, on
one physical separation: wells sit on a 9 mm pitch while the fields of view
inside a well are hundreds of micrometres apart, so each axis carries two scales
and the larger one is the well step. Both are measured against the axis's
**smallest** gap, never its median: an axis holding one within-well step and one
between-well step has a median equal to the larger, so nothing is ever three
times it and the two scales read as one. And the smallest instance of whichever
scale wins is the step, because a gap spans a whole number of steps and never a
fraction of one — a scan that skips wells leaves gaps of two and three pitches
beside the ordinary ones, and an average of those is a step no well sits on. The
axes share a plate and therefore a pitch, so an axis showing only the within-well
spacing (a single row or column of wells) takes the other's; an axis that shows a
well gap outranks one that does not, and among those that do the *smallest* wins
(`_pitch`) — taking the larger read a plate imaged on rows A and C as one whose
wells are 18 mm apart. `_well_step` divides by the **wells** between two occupied
groups rather than treating them as adjacent, for the same reason: that number is
the conversion from stage units to plate cells, so a doubled one draws the map at
half scale. There is one pitch for the plate, not one per axis, which is what
makes a one-sided skip recoverable: rows sampled A/C/E with every column present
measure 18 mm down and 9 mm across, and the axis showing the single step supplies
the pitch that reads the other's gaps as the multiples they are. **When the file states a unit, the pitch is known rather than inferred.**
`ome_explode._stage_positions` converts stage coordinates to micrometres
whenever every plane names a length unit for both axes (`PositionXUnit`), and
reports `stage_positions_in_um`; mixed or missing units convert nothing, since
OME's own default is "reference frame" — the microscope's frame, which has no
defined size, and treating it as micrometres would put a confident wrong mapping
on every file that omits the attribute. With a unit, `_known_pitch` uses the
nominal `WELL_PITCH_UM` where the measured steps agree with whole multiples of
it, and that is what maps an **arbitrary selection of wells**: a scattered set
(B02, D07, G11) has no adjacent pair anywhere to learn a pitch from, so relative
spacing alone reads it as three neighbouring wells or refuses outright. It also rescues the case the gap split cannot see at all: sixteen fields on a
2.25 mm step tile the 9 mm pitch *exactly*, so every step in the file is the same
and there is no wider gap anywhere to mark a well boundary — a perfectly regular
6×6 plate read as 24×24 wells of one field, far too big for a plate, and the
drawing said so. `_um_axis_groups` places the boundary instead of finding it:
each coordinate's residue modulo the known pitch says where in a well it sits,
and the boundary goes in the widest empty arc of those residues (the wrap-around
one on a tie, which keeps the first well whole). Taken only when the gap split
produced something that cannot be a plate *and* the result can, so a scan the
gaps already read correctly is untouched and another format's pitch still falls
back to the ratios. It also
settles the oldest ambiguity here — `_one_well` reports a montage across a
single well as one well of N fields, where without a unit a montage and a grid
of wells holding one field each are the same pattern at two scales and the grid
reading had to be taken. A spacing that is not a whole number of 9 mm pitches
falls back to the relative reading, so another plate format is no worse off.

That the two axes share one pitch is a real
assumption — an SBS plate is on 9 mm centres both ways — so it is **checked**
rather than relied on: the two axes' per-well steps must agree within
`_AXIS_AGREEMENT_TOL`, and where they do not there is no layout. Taking each axis
at its own word instead would read A/C/E as adjacent, and letting a disagreement
through is worse: a scan whose rows sit 1.5 pitches apart rounds to 2 and
produces *exactly* the layout a genuine skipped row produces, with the same
confidence. One adjacent
pair anywhere on one axis is enough. Where **every** step on every axis skips —
rows A, C and E in a single column — nothing in the file distinguishes that from
three adjacent wells on a coarser plate, since no absolute unit is trusted here;
it reads as adjacent, which the map draws at plate scale and the user's grid
overrides; where neither shows two scales the reading is genuinely
ambiguous between "one well holding a grid of fields" and "a grid of wells
holding one field each", and the latter is taken because it is the ordinary plate
scan. Coordinates are merged within a fraction of the coarsest spacing first, or
a stage's few-micrometre repeatability reads as a position of its own and the
grid explodes. **A single well imaged as a montage still reads as a grid of
wells**, because that ambiguity has no answer in the coordinates alone — telling
them apart needs the absolute scale, which means trusting the file's
`PositionXUnit` — and the common reading is chosen. Degenerate input is refused
outright rather than answered: every plane at one coordinate, fewer than two
distinct positions, or a scale that is not finite and positive all return
`None`, since a confident layout out of those is offered to the user as the
file's own answer. What is **not** always inferred is which physical well the block starts at: stage coordinates are relative to the microscope's origin, which no file
states in plate terms, so `StageLayout.relative_order` is `(row, col)` offsets and
`place()` puts them at the origin the user chose.

**A shape need not be a rectangle.** An L, a diagonal, a plate with a row left
out: ordinary experiments, and the fields per well and the visit order are right
whatever the outline is. `rows`/`cols` are the block's **extent** and `n_wells`
counts the cells actually visited, with `filled` reporting whether they agree —
`suggest_layout` hands anything sparse over as an explicit `well_order`, since a
`rows × cols` sweep visits every cell and cannot describe one that does not.
Group indices count *wells, not groups* (`_group_axis` advances by
`round(gap / pitch)`), so a skipped well leaves a hole: indexing occupied groups
consecutively closed the gap up, and a scan of rows A and C in two columns
collapsed to two wells, `A01` and `B01`. The one refusal left is **uneven fields
per well**, which no single FOVs-per-well setting can express, and the page says
which of the two it is hit. `stage_layout.path_in_cells` converts the same coordinates into **plate
cells** — one unit per well, fractional inside one — anchored so the first
position sits at the centre of the well the user says the scan starts in.
What reaches it is `stage_layout.single_cycle(...)`, the coordinates with
every return to a place already imaged dropped, because a timelapse walks
the same route once per timepoint: drawn in full that is **one map
overdrawn N times**, every dot landing on a dot, every line on a line, and
the numbering running to N times the count of real positions while showing
the geometry of one. Repeats are dropped rather than the list cut at the
first one, because a file ordered timepoints-within-position repeats its
*second* coordinate and truncating would leave it a single dot.
`ome_import_compute.stage_path_cycles` returns `(drawn, in the file)` and the
page's caption states both whenever they differ — a picture showing a twelfth
of the coordinates must say so in the artifact, not only in the code that
decided it.

**Which way the stage ran is the user's answer, not an inference.** A microscope's
axes are not the plate's: how the plate sits in the holder and where the objective
is decide which way X grows, so a scan that swept left to right across the plate
can be written with X *decreasing*. Read straight it comes out mirrored — the
inferred visit order walks the wells backwards, the map draws the run back to
front, and the conversion writes each image into the horizontally opposite well,
with every count correct and nothing on screen broken. Nothing in an OME-TIFF says
which way the plate faced, and the coordinates alone cannot tell a right-to-left
scan from a left-to-right one on a plate loaded the other way round, so the page
carries two checkboxes (left↔right, top↔bottom) rather than guessing.
`stage_layout.mirrored` negates the chosen axes — a reflection, so shape, spacing
and pitch are untouched and only the direction of travel changes — and
`ome_import_compute.oriented` wraps a layout in the reflected coordinates,
returning the layout itself when neither axis flips so the page's identity-keyed
inference cache still sees one object per file. The page applies it **once**, in
`_read_layout`, so the inference, the suggested grid, the frame order, the drawn
map and the wells a run writes cannot disagree; mirroring only the drawing would
be the worse bug of the two, leaving the picture the user checks the layout
against contradicting the layout the conversion uses. Toggling re-offers the
file's answer whenever the form still holds it — `_holds(suggestion)`, which
compares the controls against the last offer, because `_untouched` asks whether
the form is at its *defaults* and a file whose metadata was applied on load has
left it holding the file's answers instead.
`widgets/stage_path_view.py` draws that under the acquisition plate at the
plate's own scale: a dot per imaged position, joined in acquisition order, over
the same 8×12 lattice. It is the one view that can show anything about the
fields *within* a well, since a plate map has one cell for all of them. Its
geometry comes from `WellPlateSelector.cellRect(row, col)` rather than from its
own width, because the plate reserves room for its headers before sizing cells
and two widgets of equal width otherwise disagree about where a well is. The
scale factor is `StageLayout.pitch`, the **centre-to-centre** well step measured
from the group means — not the gap the grouping threshold uses, which runs from
the last field of one well to the first of the next and is short by the width of
a well's own block of fields; at that scale the drawing shrinks by that fraction
per column and lands a whole cell out of place across twelve of them.
**The inference is a function of the file, and the page is not.** Every box on
the converter recomputes the readout as it is typed in, so `suggest_layout` and
`stage_path_for` both take an already-computed `StageLayout` and the page holds
one per source (`_inferred_for`, keyed on the layout object `_read_layout`
already caches per path+mtime). Left to re-derive itself it ran **twice per
keystroke**; the loops behind it are linear now, but a 96-well plate at 16 fields
over 24 timepoints is 36,864 positions and still ~90 ms, which is not something
to spend per character on the UI thread. Two things in that measurement are worth
keeping in mind for anything else that walks these lists: `c not in cells[:i]` is
the obvious way to take first visits and is quadratic (five seconds at that
size), and a timelapse revisits every position once per timepoint, so counting
*visits* per well rather than distinct coordinates reports a plate's fields
multiplied by its timepoints. `ome_import_compute.suggest_layout`
wraps that for the page, which *offers* it. It returns `None` when `place`
refuses the block — one larger than a 96-well plate, which is what a pitch read
as the *field* spacing produces — and the page then says so, naming the block it
read: the path drawing needs no placement and draws regardless, so a silent
`None` leaves an overflowing picture, an untouched form and no account of
either. Whether it arrives as grid fields or
as an explicit order is decided by **comparing** the placed order against the
serpentine those fields would produce, not by asking whether the scan is a
serpentine: a stage reporting Y or X descending sweeps a perfectly good
serpentine whose first well is the block's *bottom*-left, and the grid always
starts top-left, so the rows come out mirrored with every count still correct.
One comparison covers that, a raster, and any other starting corner.

**The grid says whether it snakes.** `util/plate_grid.block_well_labels` is the
one generator of a block's labels — `serpentine_well_labels` is now a wrapper
that passes `serpentine=True` — and its flag decides what happens at the end of
each line: snake back, or fly back to the near edge and raster. Both are ordinary
acquisition patterns, and a raster read as a snake labels every second line
*backwards*, which is a silent well-mapping error with the right well count. It
reaches the pipeline as `ConversionParams.serpentine` and sits in `_MAPPING_FIELDS`,
so a resume refuses to finish a run under a different sweep than started it. The
page carries it as a Snake checkbox beside the Sweep combo, filled from the
inference like the rest; before it existed, the grid could only generate a snake
and a raster had to be clicked well by well.

**Two more settings come out of the same metadata.** `stage_layout.infer_frame_order`
reads the converter's *frame order* — the one thing the file's own
`DimensionOrder` cannot express, since an OME `<Image>` has no position
dimension — from which coordinates repeat and how: every position visited once
is `single`, a sequence running through all of them before repeating is
`pos_in_time`, one repeating each position consecutively is `time_in_pos`, and
anything else is `None` rather than a guess. That also yields the timepoint
count.

**"The same place" is one definition for the module** — `stage_layout.place_keys`,
which snaps each coordinate to the merged axis values `_distinct` / `_nearest`
group wells with, at the `_jitter_tolerance` both the grouping and the revisit
test read from the coarsest gap on either axis. The question is asked in four
places (how many fields a well holds, whether the sequence repeats and how
often, which places the map draws, which cell a coordinate belongs to), and a
stage sent back to a nominal position reports it a fraction of a micrometre
off, so an equality test finds no repeats at all: the same file then reports a
well's fields *multiplied* by its timepoints and `single` for its frame order,
each contradicting the other. Comparison is O(n log n) rather than pairwise —
a plate timelapse holds tens of thousands of positions and this runs on the UI
thread, on every keystroke. `StageLayout.absolute_origin` reads the **starting well** straight out of the
coordinates when they are stated in the plate's own frame — A01 at the origin,
every well centre a whole number of pitches out — believed only when every well
in the block lands on its grid position within `_PLATE_FRAME_TOL` of a pitch and
inside the plate, since a microscope-framed file has an arbitrary zero and the
same arithmetic would be meaningless. Failing that,
`ome_import_compute.wells_from_names` reads it
out of `OmeLayout.position_names` (each `<Image>`'s `<StageLabel>` name, else
its own) — the only thing in an OME-TIFF that locates a position on the *plate*
rather than on the stage. Every name must yield exactly one well token or the
answer is discarded whole: a set where some parse and some do not is a partial
mapping, and filling the rest by inference is how a plate ends up half
labelled. When every position names its well the visit order is taken from the
names outright, and nothing is inferred at all. The page applies the result
**automatically while the form still holds its defaults** and never over a value
the user has entered.

**The grid and the clicked path are alternatives, and only one of them is an
input.** The converter page draws the grid's serpentine onto the plate as a live
*preview*, and the plate the preview lands on is the same one the user clicks
into. So the page reads `well_order` from the plate **only once the widget is
dirty** (`OmeImportPage.raw_fields`), and `PlateOrderEditor` makes the first click
*replace* the preview rather than extend it: a preview is the host's guess, and
carrying its wells into the order the user is defining makes them positions 1..n,
pushing every clicked well one stage position later — each well then receives the
previous position's images, with no count on screen that looks wrong. A path the *file* supplied is not a hand-drawn one, and the page tracks which it
has (`_order_from_file`): editing Origin re-places the file's order at the new
well (`_reposition_file_order`) rather than being ignored, because the starting
well is the one answer an inference can get wrong in a way only the user can
see. A click on the plate makes the order the user's, and only then do the grid
controls go dead. While a genuinely
hand-drawn path is in force the grid, origin and serpentine controls are disabled
with a note saying why, since they describe the other way of producing the same
order and a control that accepts input and discards it is the page's most
confusing state. `ConversionPlan.summary()` names the wells at both ends of the
order and which input produced it for the same reason: the counts are identical
either way.

**The well ordering need not be rectangular.** Each stage position `i` maps to
`well_labels[i // fovs_per_well]`, so the acquisition path is just an ordered list
of wells (one well × FOV per position; FOVs-per-well is entered explicitly and
cross-checked, or derived). By default that list is the **serpentine** sweep of
the imaged `grid_rows × grid_cols` block (`serpentine_well_labels`). But
`explode_ome_tiff` / `plan_conversion` also accept an explicit **`well_order`** —
an ordered list of wells that need not form a rectangle or any particular shape
(`util.plate_grid.normalize_well_order` canonicalises it and rejects an invalid or
duplicated token) — which replaces the serpentine entirely, with the grid fields
acting only as a seed. The converter page builds this order on
`widgets/plate_order_editor.PlateOrderEditor`, which is the app's shared
`WellPlateSelector` driven in its ordered `"path"` selection model: it draws the
numbered visit sequence plus a subtle arrow tracing position→well across the
plate (the visual confirmation), and clicking wells appends / removes-and-
renumbers them to define an arbitrary order (component 2). The page keeps the
plate a live serpentine preview of the grid until the user clicks (the widget's
*dirty* flag), after which the grid no longer overwrites it.

Two source structures are handled (`read_ome_layout` → `OmeLayout.single_image`),
because OME allows only X/Y/Z/C/T inside one `<Image>` — there is no position
dimension:

- **Multi-image** — several `<Image>` elements (one `tifffile` series each), one
  per stage position; each series' `T` axis is that position's timepoints.
- **Single-image packed** — one `<Image>` whose `T` axis packs *every* position
  (and any real timepoints) into a flat frame sequence. A **frame-order** choice
  (`single` / `pos_in_time` / `time_in_pos`) says how that sequence splits:
  `n_positions = wells × FOVs`, `n_timepoints = n_frames / n_positions`. Planes
  are read **one page at a time** (`_packed_plane` ravels the T/C/Z indices
  against the series' leading-axis sizes, honouring the file's `DimensionOrder`)
  because a packed plate array is routinely tens–hundreds of GB — far too large
  to `asarray` whole.

**The source is validated before anything is written.** `read_ome_layout`
audits the file in the same pass that reads its sizes — all metadata, no pixels,
so the cost is one file open however large the plate — and
`OmeLayout.validation_report()` returns the one blocking problem, ordered by how
far upstream the fault is. **Not an OME-TIFF:** a file *named* `.ome.tif` whose
OME-XML cannot be read has almost certainly been truncated in transit; it still
opens as a valid TIFF, `tifffile` invents a plain series from the pages it could
enumerate, and a forty-frame plate converts silently into one well. A plain
`.tif` stack carries no OME-XML either and is a legitimate input, which is why
the check is keyed on the *name* claiming OME rather than on the metadata alone.
**Metadata that does not describe the pages:** `series_kind` is `tifffile`'s own
verdict, and anything but `"ome"` means it declined the OME structure and fell
back to enumerating pages, so each plane's channel and timepoint is a guess.
**Declared sizes that were not realised:** `declared_sizes` holds `SizeT`/`SizeC`/
`SizeZ` as the XML states them; `SizeT` on a single-image file is the one
compared against the series that actually came out, since that is the axis the
converter splits into positions. The other two are collected for a caller that
wants them and are not themselves a refusal.
`OmeLayout.warnings` keeps what `tifffile` logged while reading — it diagnoses
every one of these in its own words and writes them to a logger nothing was
attached to, which is the file's own account of what is wrong with it.

**An incomplete source is refused before anything is written.** `tifffile` sizes
a series' page list from the dimensions the *metadata* declares and leaves a slot
`None` wherever the file carries no IFD for it — an acquisition stopped early
(the planned `SizeT` stays in the header), or a multi-file OME whose companion
files are not beside the master. Its own `series.asarray()` zero-fills those
slots, which is exactly the substitution §7's design rule forbids: a converted
plate would carry solid-black planes no downstream step could tell from real
ones. Reading pages individually, as the single-image path must, instead puts the
gap in front of `.asarray()` as a bare `None`. So `read_ome_layout` counts
the gaps in the same pass it reads the sizes (`series_page_gaps`, scanned only
for the series kinds that can hold one) and carries them on `OmeLayout` as
`total_planes` / `missing_planes` / `incomplete_positions` /
`first_missing_plane`, plus `missing_files` — the companion filenames the OME-XML
references that are not on disk beside the source, which is what separates the
two causes and therefore the two fixes. A gap has a **third** cause besides those two, and it needs the
opposite remedy: a *broken index map* — `TiffData` entries pointing at IFDs the
file does not have, or carrying a coordinate outside the declared sizes, which
`tifffile` reports and then leaves as a hole — loses nothing at all, because the
pixels are on disk and only the metadata is wrong. Telling that user to re-export
a shorter file would discard data that is present. `unreferenced_pages` is the
tell: physical IFDs no series slot points at, which is zero for a truncated run
and non-zero for a broken map. `OmeLayout.validation_report()` is the
single wording every caller uses: `explode_ome_tiff` raises it as a `ValueError`
before the destination directory is even created, and `plan_conversion` raises it
as an `OmeImportError` *ahead of* its grid arithmetic — no grid makes a gapped
file convertible, so a divisibility error derived from one would be a red
herring. The converter page shows it in the readout and leaves **Convert**
disabled. `_packed_plane` keeps a per-plane guard behind that check, naming the
frame/channel/Z it was asked for rather than raising on `None`.

**An interrupted conversion is finished, not repeated.** Every well is
published atomically (`util.zip_write.atomic_zip`), so a run that stopped
part-way leaves whole, correct archives and no half-written one — the work
already done is on disk and only the rest is missing. `explode_ome_tiff(...,
resume=True)` skips a well whose archive already holds **every member name these
settings call for** (`_expected_members` / `_well_is_complete`, read from the
zip's central directory, so a couple of seeks each), reports it through the same
progress callbacks as a converted one — otherwise the plate map shows a finished
well as one that never ran, and the run-wide bar never reaches its total — and
names the count in `ExplodeResult.wells_resumed` and the closing log line.

**Resume is off by default, and gated on the settings rather than the
filenames.** Two different mappings — another grid, origin, ordering, FOVs-per-
well, or another source file — produce *identical* member names from different
images, so an archive on disk cannot say which run made it. `apply.convert`
writes `datasets/ome_import/ome_import_compute.IN_PROGRESS_MARKER`
(`.aw_ome_in_progress`) into the destination before the first archive, holding
the source path and the mapping fields, and removes it on success; so the marker
exists exactly while a run is unfinished. `resume_state` compares it against the
current settings and answers *resumable*, *mismatched* or neither, and
`destination_problem` reads that: a folder carrying this tool's marker for the
same settings is not a refusal but a resume, for different settings it is
refused in its own words, and with no marker at all the ordinary
"already holds converted wells" rule applies. This is the same device the other
Datasets tools use (`_common.dataset_paths.make_dataset_api`), for the same
reason: a folder holding *this* tool's interrupted attempt is a resume, while the
same folder holding another tool's leftovers is not.

`explode_ome_tiff` also takes a `progress_fn`/`should_abort` pair (raising
`OmeExplodeCancelled`) so the converter's worker can drive a progress bar and
Cancel. Channel roles are *not* set in the converter — it writes the OME channel
**names** as tokens; the user assigns nuclear/fluor/phase in Analyze's normal
Channel Tokens section on the converted folder. `pipeline/ome_explode.py` and
`util/plate_grid.py` are in the remote manifest so it stays a complete mirror of
`pipeline/` + `util/` (`test_remote_manifest.py`).

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

That is also why the auto-threshold estimator lives in `pipeline/auto_threshold.py`
(`estimate_thresholds_from_outputs`, `sample_cell_and_bg`,
`pick_endpoint_timepoints`) beside `process_microscopy`'s
`_apply_thresholds_to_pipeline_info` — earlier versions imported the equivalent from
`well_viewer.auto_threshold`, which crashed the pass on hosts without the
viewer. The viewer's copy is still used by the GUI's "Auto-threshold"
button on the Cell Gating tab; the pipeline's copy is now the standalone
fallback.

**The end-of-run threshold pass** is `run_auto_threshold_pass`, one call at the
tail of `main()`, with three modes:

| condition | what it writes |
|---|---|
| `--no_auto_threshold` | nothing |
| no control wells | Otsu on a pooled per-cell / background-pixel distribution, per channel (`pipeline.auto_threshold.estimate_thresholds_from_outputs`) |
| `--pos_control_well` **and** `--neg_control_well` | per channel, the threshold maximising the relative fraction-on gap between those two wells (`pipeline.threshold_optimize.estimate_thresholds_from_controls`) |

**The Otsu mode reads its samples out of the output archives, and pairs them by
name.** `estimate_thresholds_from_outputs` walks `<output_dir>/*_out.zip` and
matches each channel's `_tophat` member to the `_labels` member of the same
FOV-group, which means parsing member stems — output names are the input stem
plus a role suffix — against the run's own `--filename_schema`. All of that is
keyed on `(fov, timepoint)`, computed for both member roles by the single
`_SchemaReader.group_key` so a label and its tophat cannot disagree about either
coordinate; `_index_members`, `_sample_one_well` and `_otsu_per_channel` are the
three steps around it.

A schema need not name every slot, and an unnamed slot is not a parse failure.
`well:channel:timepoint` (files like `A01_w2Turq_T04.tif`) has no fov token to
find, and two images of the same well/channel/timepoint cannot exist under it —
they would be the same filename — so an unnamed fov collapses to a constant that
merges nothing distinct. A slot the schema *does* name but a given stem does not
fill is the opposite case: that member is malformed, and a constant there would
merge genuinely different fields, so it stays rejected. The timepoint slot takes
the constant whenever it comes out empty either way, because a single-timepoint
dataset writes filenames with fewer tokens than the schema has fields.

The failure this guards against is a silent one: an archive whose members the
schema cannot place looks exactly like a well that held no images, and every
channel then reports `only 0 sample(s)` — a line that reads as "no data" and
names neither the schema nor a filename. A well that yields nothing while holding
role-suffixed members now warns, naming the schema, the separator and an example
stem. The dispatcher separates the two outcomes it used to report as one, as
well: "no new defaults" (every channel already had a stored value) is routine,
while estimating nothing at all for every readout channel is a warning — and it
cannot hide behind the segmentation channel's unconditional zero, which is itself
a successful write and made such a run read as a partial success.

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
the two paths cannot drift. That rebuild forces `no_auto_threshold=False` on
purpose: when the pass is wanted it must ignore the batcher's copy of the flag.

Which makes `--no_auto_threshold` ambiguous on that argv — it arrives there for
two unrelated reasons, the operator unchecking the box and the batcher
suppressing the per-batch pass, and once appended the two are indistinguishable.
So the *decision to call* the local pass is what carries the operator's answer:
`_BatchedRemoteProcess._auto_threshold_off_by_user` reads the flag off
`self._pipeline_argv`, the argv as handed in, which `_run_batches` copies rather
than mutates. Reading it any later answers the wrong question.

**A remote fetch must not take the user's blocks with it.** `verify_and_cleanup`
rsyncs the remote `pipeline_info.json` over the local one, and a remote output
dir starts empty — so the fetched file carries none of the cell-gating
thresholds, sample definitions, ratios or notes the Review side wrote locally,
including the ones `_run_local_auto_threshold` produced moments earlier.
`pipeline/sidecar.py` holds the list of those user-owned keys and the
capture/restore pair around the fetch; `process_microscopy.write_pipeline_info`
preserves the same list when a re-run rewrites the sidecar in place, so the two
paths share one definition of whose data it is. `run_report` keeps its own
merge (`pipeline/run_report.py`) because its records merge per well rather than
being restored wholesale.

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
`_refresh_segmentation_hints`) built by
`analyze_tab_seg_params.AnalyzeSegParamsMixin`, so only the chosen routine's
controls appear. All routines segment the **Segmentation channel**
(`--nuclear_token`, which may be any fluorescent or brightfield channel) unless
noted:
- `stardist_nuclei` / `stardist_seeded_watershed_cell` — StarDist (needs a
  fluorescent stain); watershed adds a cytoplasm token + min-nucleus-area.

  **Detection thresholds.** Both methods take `--stardist_prob_thresh` (the
  object-probability cut applied to the network's objectness map before polygon
  NMS: lower detects more nuclei, including dim and marginal ones; higher keeps
  only confident detections) and `--stardist_nms_thresh` (the polygon overlap
  cut: higher lets more overlapping nuclei survive, recovering touching nuclei
  on a confluent field; lower merges more aggressively). Both default to a
  **negative sentinel** meaning *use the threshold the pretrained weights ship
  in their own `thresholds.json`* (≈ 0.479 / 0.3 for `2D_versatile_fluo`) —
  which is also what `predict_instances` does with `None`, so an untuned run
  makes byte-identical calls to one from before the thresholds were exposed
  (`tests/test_stardist_params.py` asserts the empty kwargs). The rows sit under
  the method stack, shown for both StarDist methods; the run log states the
  thresholds in force beside `Segmentation method`, because a nucleus count is
  only interpretable next to them.

  **One spelling of "auto".** `pipeline.stardist.parse_threshold` is the single
  rule: blank, the word `auto`, unparseable, or the CLI's negative sentinel all
  resolve to `None` (model default); anything else must be a probability in
  `[0, 1]` and is **rejected rather than clamped**, since a silently clamped
  threshold segments a plate with a parameter nobody chose. The CLI validates
  through it in `main()`, the Analyze form emits blank-or-in-range text through
  `pipeline_service._emit_stardist_threshold_flags`, and the Playground parses
  its own two fields with it.

  **The form's starting value is not the CLI's default.** The Analyze form ships
  `analyze_tab_seg_params.DEFAULT_PROB_THRESH_TEXT` (`"0.2"`) in the Nucleus
  probability field, because the pretrained model's own ~0.479 is conservative
  for these plates — so a run started from an untouched form detects more nuclei
  than the model default would. `--stardist_prob_thresh` keeps its `-1.0`
  sentinel, so "no flag" still means the model's own threshold and a script that
  passes nothing is unaffected; the GUI emits the number explicitly, so a GUI run
  stays fully described by its command line and its sidecar. Clearing the field
  restores the model's. Overlap (NMS) still starts blank, and the Playground's
  matching control starts blank too — an untouched preview there is deliberately
  not what an untouched Analyze run produces, which is why the help text names
  the form's starting value even when read from the Playground.

  **One description, too.** `PROB_THRESH_HELP` / `NMS_THRESH_HELP` live in the
  same module and are the text both panels show — the Analyze form (through
  `analyze_tab_seg_params._threshold_help`, a function-local import so the form
  does not pull scipy/scikit-image into its start-up cost) and the Playground.
  They previously kept private copies and had drifted into describing the same
  two numbers differently depending on which tab you were standing in. Both
  panels attach the text to the **row and its label**, not only the entry box —
  the label is the word being read and the larger target — and put a
  `widgets.info_icon` ⓘ beside it so the explanation is findable at all.

  **Where the parameters live.** `StarDistParams` (frozen dataclass:
  `prob_thresh`, `nms_thresh`, `tile_px`) is installed once per worker by
  `_worker_init` via `set_params`, mirroring `set_model`. The inference itself is
  `pipeline.stardist.segment_nuclei(img, *, model, params)`, which reads **no**
  module state — `_segment_stardist_nuclei` is the worker-global wrapper over it,
  and the Review pane's Playground calls the same function directly, so an
  interactive preview and a batch run cannot diverge.

  **Tiling.** Large frames blow up StarDist's peak memory, so `pipeline.stardist`
  can tile the prediction: `--stardist_tile_px` (an Analyze-tab spinner with an
  ⓘ bubble, default `0` = **off** = predict the whole frame) sets the **max
  pixels per tile side** as `StarDistParams.tile_px`, and each worker splits any
  frame larger than that into an `n_tiles` grid (a 4K frame → 2×2 ≈ ¼ the peak);
  small frames stay untiled (`n_tiles=None`) so nothing slows needlessly.
  `set_tile_px` sets that one field without disturbing the detection thresholds —
  tiling is a memory knob, not a detection one, and must never move what is
  detected. `n_tiles` is logged per FOV. The tile-size math is pure integer logic
  covered by `tests/test_stardist_tiling.py`.
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
skips *all* background correction (flat-field + subtraction) for every
well/method — brightfield/phase has no fluorescence background to remove, and
running the correction on it only injects artefacts. The token flows to the
workers as a `--phase_token` arg threaded through `shared_kwargs`;
`_correct_image_group` takes a `phase_token` param and short-circuits that
channel. The Phase channel is still *quantified* like any other: it is a member
of `fluor_tokens`, and that membership is what gives Analyze a channel row on
which to carry the Phase checkbox.

**Phase and segmentation are mutually exclusive outside label-free.** Because
the Phase channel skips correction entirely, naming it as the segmentation (or
cytoplasm) input under a fluorescence routine runs that routine over an
uncorrected brightfield frame — a run that completes and writes a
complete-looking set of CSVs measured on the wrong image. Both entry points
refuse the pairing: `analyze_tab_seg_params._phase_collision_errors` in the form,
and a `parser.error` in `process_microscopy.main()` for anything arriving as
argv (the CLI and remote runs). `cellpose_labelfree` is exempt — segmenting the
phase image is its purpose.

Where the roles come from when nobody types them: `util/channel_roles` guesses a
role per channel name for both the OME converter and the Analyze folder scan. A
strong nuclear stain (`DAPI`/`Hoechst`/…) takes the segmentation role, else a
secondary `NIR`/`RFP` channel, else the first channel that does *not* read as
phase/brightfield (`_PHASE_HINT_RE` — `phase`, `brightfield`, `trans`, `dic`,
`bf`). That last exclusion matters because the channel list arrives in
sorted-filename order rather than acquisition order, so a `DIC` channel leads it
routinely; only when every channel reads as phase does one of them take the
segmentation role, since segmentation still needs an input.

**Worker-pool memory + failure accounting.** Two properties of how the
parent process supervises the per-well worker pool:
- **Don't hoard records (~34 GB leak).** `_run_well_worker_pool` used to
  `extend` every per-cell record of the whole run into one parent-side list only
  to `len()` it for the final summary — at scale that pinned gigabytes in the
  orchestrator. It now keeps a running integer `total_nuclei` and lets each
  well's records free as the loop advances, so the parent holds at most one
  well's worth transiently (per-well CSV writing stays worker-side).
  `tests/test_well_worker_pool.py` pins the count.
- **Completeness is part of the output contract.** A well whose FOV-groups partly
  failed still writes its per-well CSV and `<well>_out.zip` from the groups that
  succeeded, and on disk that output is shaped exactly like a clean well's — so
  every well records how much of its input its output actually covers.
  `pipeline/run_report.py` is that record: each worker drops one small JSON file
  (`groups_total` from the well's input group count, `groups_covered` read back
  from the finished CSV's distinct `(fov, timepoint)` pairs — ground truth from
  the artifact, so it stays honest across incremental runs, resumed per-FOV
  checkpoints, and groups a failing disk never let the run attempt). `main`
  folds them into a `run_report` block inside `pipeline_info.json` and logs the
  shortfall; `core/load_controller._report_incomplete_wells` names the affected
  wells in the status line, a toast and the log at dataset load. One file per
  well, written atomically and never appended to, so the worker pool cannot
  interleave two wells into a corrupt record. **`run_report` describes one
  invocation, not the dataset** — it is cleared at the start of every run,
  merged back wholesale, and written only by wells the run actually processed,
  so an incremental run that adds a timepoint leaves a block naming the handful
  of wells that had new data. That is the reason the completeness *record*
  below is a separate thing rather than a reading of this one. Without this,
  per-field statistics
  silently described whatever fields survived and the first visible symptom was
  an absent error bar several layers from the cause. The per-FOV tracebacks in
  `<output_dir>/errors/` remain the detailed evidence.
- **A well stops attempting FOV-groups once its storage has failed repeatedly.**
  `_MAX_CONSECUTIVE_IO_FAILURES` consecutive `OSError`s abort the well's
  remaining groups (they are still reported as missing, which is what they are).
  Storage that is out of room does not recover mid-well, and `_safe_imwrite` has
  already retried each write five times with backoff before raising — so by the
  third group in a row the volume has had fifteen chances. Non-`OSError`
  failures do not trip it: a segmentation bug throwing on every frame is not a
  storage problem, and aborting would hide how widespread it is.
- **Disk capacity is accounted for in one place: `pipeline/capacity.py`.** It is
  Qt-free and stdlib-only (its free-space primitives and the single wording for
  a full volume live one layer down again, in `util/disk_space.py`, because the
  shared retry shield and the `services/` remote client need them without the
  imaging stack). Four jobs:
  - **Sizing the dataset.** `sample_image_bytes` returns one input image's
    *uncompressed* size — stat'ed from a loose TIF, or read from a well
    archive's central directory, which costs one open and extracts nothing. The
    zip path is not an optimisation but the main one: `in/<WELL>.zip` is the
    layout the pipeline segments from, and sizing a run from the archive's
    compressed size on disk under-states everything downstream.
  - **The up-front preflight.** `preflight_scratch_space` sizes the transient
    working set (per-image bytes × channels + ~6 float32 intermediates per
    FOV-group, times the run's own field/timepoint enumeration, times how many
    wells `--workers` stages at once) and the plate's durable output, and
    — the channel count being `fluor_tokens_for_quant`'s own length, since
    that list already leads with the segmentation channel and adding one for it
    would charge every FOV-group a frame that is not there —
    compares them against the *resolved* scratch base and the output directory.
    Below one well's worth of scratch it exits before reading an image, because
    a run that starts there cannot finish; everything else warns, since the
    estimates are deliberately rough. **That exit is the only hard stop in the
    app driven by an estimate, so it states the figure it tested and it can be
    overruled.** It fires on the one-well need and must quote that, not the
    whole-run figure beside it — the two differ by `--workers`, and reporting
    the larger one under the words "not enough for even one well" describes a
    shortfall the test never measured. `--allow_low_disk` (Analyze → Compute
    Options) downgrades it to the warning path, because a hard exit sitting on
    top of an estimate can refuse a run that would have finished. **The estimate
    is rough in both directions**, which is why it is a refusal that can be
    overruled rather than a number to trust: it takes the *largest* member and
    gives no deflate credit, which overshoots, while `_INTERMEDIATES_PER_GROUP`
    is a flat count where several of those images are written *per channel*,
    which undershoots past about four channels. The free figure it is compared
    against is `statvfs`-available, which on APFS excludes purgeable space and
    reads well under what the machine will hand over — a Mac measured at 14.3 GB
    in Finder reported 6.0 GiB here. Any free figure quoted to a user says so,
    because it will not match their file manager and a check whose numbers
    cannot be reconciled reads as broken. The wording of the override
    lives once, in `capacity.ALLOW_LOW_DISK_HELP`, shared by the CLI flag's help
    and the checkbox's tooltip. When scratch and output share a volume —
    the default, `~/.allwell/scratch` beside an output dir on the home volume —
    the two needs are summed against one free figure, because checking either
    alone reports that the run fits when it does not. The resolved base is
    logged on the `Scratch  :` line either way.
  - **The mid-run projection.** `OutputCapacityMonitor`, fed one call per
    completed well by `_run_well_worker_pool`, measures the output actually
    written and projects the wells still to come. It warns once, naming the
    measured per-well size — a real number, carrying the real compression ratio
    and FOV count, unlike anything the preflight can estimate — while there is
    still room to act. The estimate of durable output in `estimate_well_output_bytes`
    takes no credit for deflate for the same reason: an assumed ratio would be
    invented rather than measured, which is why that half of the preflight only
    ever warns.
  - **Saying so when a volume fills anyway.** `util.disk_space.space_message` is
    the only wording for `ENOSPC`, and `util.retry_io.retry_io` rewrites the
    exception through it (preserving errno 28) for every caller that passes the
    target `path`. A bare `OSError: [Errno 28] No space left on device` names no
    volume, and the volume it happened on is routinely not the one the user
    would check.
- **Exhaustion is the common cause of a partial run, and "there is room next to
  my data" does not rule it out.** The scratch base defaults to
  `~/.allwell/scratch` (§6.5) — the *home* volume — while the dataset usually
  lives on an external or network disk. Each FOV-group stages ~6 float32
  intermediates there, so a 6-field × 23-timepoint well is several GB and a
  plate of them is far more; the input archives, meanwhile, are the only
  compressed copy in the run, so a volume with ten times their size free is not
  close to enough. The durable results compound it from the other side: every
  `<well>_out.zip` stays on the output volume for the rest of the plate, so a
  run can start with room and end without it. `pipeline.io._safe_imwrite`
  retries with backoff and triages disk-full versus transient, but once space is
  genuinely gone every remaining group fails the same way: the run continues,
  logging a failure per group, and produces a full complement of
  complete-looking per-well outputs. Point `$ALLWELL_SCRATCH_DIR` (or the
  Analyze tab's *Scratch disk* field) at a roomier volume when the home volume
  is tight.
- **A well's extracted inputs are reclaimed before its zip-up.** Building
  `<well>_out.zip` while the staging directory still holds every intermediate is
  the well's peak footprint, and on a single-volume setup the extracted TIFs are
  competing for the same bytes at exactly that moment. `process_well_zip_task`
  removes `_tmp_extract_<well>` as soon as the groups are processed; the
  `finally` block still removes it (it has to cover the failure paths) and is a
  no-op by then.
- **Per-FOV failure traces.** A failed FOV-group persists its full traceback to
  `<output_dir>/errors/<well>_<fov>_pid<N>.txt` (`_dump_fov_failure_trace`,
  mirroring `_dump_well_failure_trace`), and `classify_log_line` treats
  "failed" as ERROR so it surfaces in the GUI live log. **A line that states its
  own severity is believed before it is sniffed**: an explicit `[info]` /
  `[warn]` / `[error]` tag wins, and so does a `note:` marker, because the
  keyword pass reads whole lines *including paths* and this directory is called
  `errors/`. On a remote run the fetch pulls that directory back so Validate &
  Repair can read it, and a clean run has none to pull — so
  `[remote] note: …/errors/ not pulled` was painted red by the name of the
  folder whose absence it was reporting, while the identical line about
  `pipeline_info.json` came out INFO. The note itself now says what the absence
  means (`_rsync_optional`'s `absent_note`), since "not pulled" alone reads as a
  fault for a source whose absence is the good case. It stops short of declaring
  the run clean: a non-zero rc covers both "nothing was there" and "something
  was, and it could not be fetched", and the fetch cannot tell those apart.
  Whole-well vs
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
| `pipeline_service.py` | `find_pipeline_script` / `build_pipeline_args` / `spawn_pipeline`. `build_pipeline_args` emits each flag only when present and non-zero (including `--stardist_tile_px` (>0 only) and `--phase_token`); it is decomposed into small table-driven emitters to satisfy the complexity ratchet, behaviour unchanged (`tests/test_pipeline_arg_validation.py`). The StarDist detection thresholds get their own emitter (`_emit_stardist_threshold_flags`) rather than the integer one: they are probabilities, and blank / `auto` / out-of-range emits nothing so the run falls back to the pretrained model's own value. |
| `frame_preview.py` | The GUI-side StarDist inference behind the Playground's **Segment** button. Holds the session's single loaded model (`_GUI_STARDIST_MODEL`) and the `find_spec` dependency probe, and calls the pipeline's own `pipeline.stardist.segment_nuclei` — so what the Playground shows cannot disagree with what a run produces. **TF and StarDist are imported inside `segment_frame`, never at module scope**: the Playground's controller imports its worker (and through it this module) at GUI start-up, so a module-scope TF import would add tens of seconds and gigabytes to launching the app for everyone who never presses Segment. Qt-free and `well_viewer`-free, which is why it sits here rather than beside the feature that uses it. `segment_frame` takes an optional `on_stage(name)` — a plain callable, so the module stays Qt-free — and reports `STAGE_LOADING` then `STAGE_SEGMENTING`, because only the first call of a session pays the model load and a UI that reports both phases identically leaves that one looking like a hang. |
| `pipeline_runner.py` | Asynchronous subprocess + stdout-streaming wrapper used by Analyze. Hosts `ProgressTracker`, a stateless log-line parser that turns the pipeline's stdout into structured run events — coarse `("stage", name)`, per-well `("well_stage", (well, stage))` (`upload`/`active`/`process`/`threshold`/`download`), per-well FOV sub-progress `("well_fov", (well, done, total))`, per-well transfer progress `("well_xfer", (well, "up"|"down", fraction))`, and `("zipper_well", well)` — which drive the live `RunDashboard`. `download` fires only once **both** the output zip and the well's CSV(s) have landed (a server-side threshold or a zip-only fetch must not read as "downloaded"). Well completion is counted by **identity, not by marker**: the three terminal markers (`Well X — done.`, `Well X — up to date`, `SKIP well X — output already exists`) all feed one set of finished wells, so a well announced terminal twice is counted once and reported as `("well_repeat", well)`. That matters in batched remote mode, where the pipeline is launched once per batch: a well announced terminal by one batch and again by the next drove the headline to "N / N wells (100%)" with wells still outstanding. The batches once shared a remote input directory and relied on the Fetcher having emptied it in time, which is where the repeats came from; the driver's whole-directory swap removes that particular source, and the identity-based count stays because it is the honest way to count a marker that can legitimately repeat. The announced total is a floor, never a ceiling — more distinct wells finishing than the banner promised raises it (a fresh `("well_total", n)`) rather than printing a fraction over 100%. |
| `remote_runner.py` | The remote-execution path: uploads the input zip-set to a worker host, runs the pipeline there, and the `Fetcher` pulls each well's `<well>_out.zip` + CSVs back. **It pulls `pipeline_info.json` too, as early as the remote has one**, so the plate can be opened in Review while the run is still going: per-well CSVs arrive as each well finishes, and without the sidecar there is nothing to interpret them with — no filename schema, no channel tokens, no `available_timepoints` / `available_fovs`, so the viewer falls back to guessing channels from CSV column names. Nothing on stdout announces that file, so `Fetcher._sidecar_loop` polls for it on its own thread every `SIDECAR_POLL_S` rather than waiting on a marker; the first attempt waits out an interval, because the thread starts in the same breath as the ssh that launches the pipeline and at that instant the remote cannot have written it. The window is hours (`SIDECAR_POLL_MAX_S`) since what it waits out is the scheduler queue, not the pipeline's start-up. Two rules keep it from fighting the other writers of that path. It **never overwrites**: a sidecar already present locally belongs to a previous run of this dataset and carries cell-gating thresholds, sample definitions, ratios, notes and the `run_report`, none of which the remote's copy has. And what it installs is **stamped `provisional`**, because a sidecar written by a job still running describes only the input that job was handed — one batch's split, in batched mode — so its `available_*` are short of the plate and there is no `run_report` at all; `verify_and_cleanup` replaces the file wholesale at the end and the mark goes with it, while a run that never got there leaves the mark in place, which is the honest outcome. The poll is off by default (`poll_sidecar`) so a `Fetcher` built to exercise one method in a test never reaches the network on a timer; both production call sites enable it and `tests/test_remote_midrun_sidecar.py` fails if a third one forgets. All rsync calls funnel through `_rsync_with_retry` (transient exit codes 10/11/12/30/35 retried on the `_RSYNC_RETRY_DELAYS = (2,4,8)`s schedule, slept in ≤0.25s chunks so the Stop button stays responsive; 11 is *error in file I/O*, what a fault on the local end of a transfer looks like). Every write the client itself makes on a user volume — staging copies off the input dir, `Fetcher._materialize`, the `pipeline_info.json` rewrite — goes through `util.retry_io.retry_io` for the same reason (§6.5). `_zip_is_complete` (`zipfile.is_zipfile`) guards the final fetch so a timed-out-but-materialised rsync isn't mistaken for a still-pending one — the bug where the last download "just sat there." **Which conda env the pipeline runs in** is `_conda_env_selector`'s answer, resolved once per runner from the host's `remote_python` and used by both the argv builder and the shell composer so they cannot disagree: left at its default the field is a *placeholder* and the env is resolved by name (`conda run -n allwell`), which is what makes a host with a pre-existing system conda work at all; set by the user to an `<env>/bin/python` it selects that prefix (`conda run -p <env>`). `_conda_env_guard` then verifies the env exists on the remote — inside the scheduler allocation when there is one, since the compute node is where it has to exist — and aborts with a `[remote]`-prefixed message naming the host, conda's base, the envs it can see, and the fix. Without it the run reached `conda run` and died on conda's bare `EnvironmentLocationNotFound`, which in a scrolling log is indistinguishable from an error the pipeline raised. A cache hit execs `true` and skips the check. **"Does the env exist" is conda's own question** — a prefix is an environment iff it holds `conda-meta/history` — and the shell that answers it, `CONDA_ENV_SHELL_HELPERS`, is shared with `scripts/_bootstrap_lib.py` so the runner's check and the bootstrap's create-or-skip decision cannot disagree. See §6.6 for why that mattered. **Scheduled compute (HPC lobby/login nodes):** when the host's `compute_launcher` is set (e.g. an `srun …` line), `_compose_remote_cmd` wraps *only* the pipeline command into a scheduler allocation (`srun … bash -lc '<conda>; exec python …'`) so the work lands on a compute node the cluster schedules while staging/cache/fetch/cleanup stay on the login node against the shared filesystem; the run records `$SLURM_JOB_ID` and `close_remote_job` (`scancel`) guarantees the allocation is torn down on completion / failure / cancel. Empty `compute_launcher` = direct-on-host execution (the default). **A queue wait is announced, timed, and named**, because it is otherwise indistinguishable from a hang: the upload finishes and the log stops. `_scheduler_handoff_lines` writes the hand-off and is called from **every** launch path — the batched one composed the same launcher but announced nothing, so a batched run fell silent at the exact moment it began waiting. `ProgressTracker` turns those lines, plus the scheduler's own `srun: job N queued and waiting for resources` / `has been allocated resources`, into a `sched` event, and `AnalyzeProgressMixin._on_scheduler` runs a 1-Hz clock reading *Waiting for the scheduler — 4m12s (job 4812345)* with a log breadcrumb every two minutes repeating the elapsed time, the job id and the **request** (an unsatisfiable one — a partition that does not exist, a `--gres` the cluster cannot honour — is the usual cause of an indefinite queue). The job id comes from the scheduler's stderr rather than the `.slurm` sidecar because the sidecar is written from *inside* the allocation and so does not exist while the job is pending; a site whose scheduler words those lines differently still gets the wait announced and timed off the app's own lines, and loses only the id. The wait is what finally retracts the plate-wide `Transferring… N%` label: `set_transfer(None)` otherwise fires only on a *compute* event, which by definition has not happened while the job is queued, which is why the status sat on `Transferring… 100%` for the whole wait. **A remote run does not write to the client's home volume.** Every downloaded `<well>_out.zip` used to be staged through `~/.allwell/scratch` and then materialised into the output directory, so each byte of a plate's results crossed two filesystems and the first of them was one the run had no reason to touch — a full home volume stopped runs whose own input and output volumes had room, which is the `ENOSPC` users hit. `_transit_dir` puts a file in transit beside the volume it is going to (or, for the upload-side archive rewrites, the one it came from): a dot-prefixed `.allwell-transit`, so every dataset scan in the app skips it, removed as soon as the file it held is in place, and swept by `sweep_stale_scratch(also=…)` when a hard quit leaks one. This does not re-create what `pipeline.io.scratch_base_for` warns about — that warning is about the *pipeline's* per-FOV working set, tens of thousands of create/fsync/rename operations from every worker, where this is one file per well written once and moved once, onto a volume that already receives exactly those bytes by copy. What changes is that the move becomes a same-volume rename and the home volume is off the path. A volume that refuses the directory falls back to the scratch base and **says so**, once per volume; falling back silently would be the original failure with the explanation removed. **The pipeline's scratch on the remote is a per-host setting**, `remote_scratch_dir`, exported as `ALLWELL_SCRATCH_DIR` inside both composed commands (single-shot and batch driver) because that is what `pipeline.io.scratch_base_for` reads; `services/pipeline_runner` sets the same variable in the GUI's own environment, which reaches a local subprocess and nothing else. Empty leaves the remote on its `~/.allwell/scratch`, which on a cluster is the home quota — the worst available volume for a working set of several uncompressed images per field. It is the one path in the file deliberately **not** run through `_remote_quote`: the values that matter (`$TMPDIR`, `$SLURM_TMPDIR`, `/scratch/$USER`) need remote-side expansion, so it is emitted in double quotes with a leading `~` rewritten to `$HOME` first, and a value carrying shell syntax is refused rather than composed into a command that would fail on the remote naming nothing. The `remotes.toml` schema is the live contract in `services/remote_config.py`. **The end-of-run completeness check runs client-side, once, after the fetch.** `_build_remote_argv` appends `--no_run_check` to every remote launch and `verify_and_cleanup` calls `run_client_side_completeness_check` instead, immediately after `refresh_pipeline_info_available_from_outputs` (which rewrites the very `available_*` fields a check would otherwise read stale) and before the `ok` gate, since a run that came back short is exactly the one whose record is worth having. Three reasons it cannot run remotely, and they compound: an incremental run's remote output dir holds only the uploaded split, so a record written there inventories the wrong tree; the fetched sidecar overwrites the local one, so the record would be destroyed on arrival; and a batched run is N separate pipeline launches, each of which would describe its own slice as if it were the plate. Running on the client also answers a strictly better question — *did it get here*, rather than *did the remote produce it*. Whatever `run_check` the fetch brought back is **dropped before the local check runs**: the remote's suppression writes the same "turned off" stub a user's own `--no_run_check` would, and that stub surviving would tell a user they had turned the check off when they had not. The two outcomes are therefore an honest record or none at all, and none reads as "not checked", which is what happened. **Empty-well correction reaches every batch from one upload.** The pipeline builds its `empty_well` background and flat-field fields from whatever blank wells are in the directory it is pointed at, at the moment it starts — and batched mode stages one batch at a time, so a blank left in the ordinary slicing corrected the batch it landed in and nothing else. Wells sort alphabetically and blanks are conventionally H12/G12, so in practice it was the last batch, and every earlier one produced numbers with the background left in whole. Nothing marked them: `_empty_well_field` returns `None` for a channel with no model and the frame passes through untouched, so the output is bit-for-bit the raw input and indistinguishable from a corrected well. A blank is a full well of uncompressed frames — the same size as any other — so it cannot be copied into each batch. `_empty_well_blanks` reads the run's own argv for `--background_well` plus an `empty_well` mode, holds those archives out of the batch slicing, and uploads them **once** to `runs/<run_id>/bg/`, outside the staging the driver's `rm -rf in && mv b<i> in` replaces. The pipeline's `--background_dir` names that directory, so every batch builds the same reference; the driver hard-links each blank that still needs processing into the final batch (`ln`, one filesystem, no bytes) so it is run once for its own output — named one by one rather than globbing `bg/`, since on a re-run some blanks are references only and linking those back in would reprocess a well the user's own re-run declined. **The staging filters do not touch a blank at all**: `stage_local_zips` takes the background wells and `_plan_staging` stages them whole, the way a `--force_wells` well bypasses the incremental diff. Without that, an incremental re-run finds every one of the blank's FOVs already processed, drops it, and starves the whole plate of the correction — and a blank with *some* new fields would be thinned to those, so the field would be estimated from a fraction of the reference while the log reported it built. When the only wells left to run are blanks, they are treated as ordinary wells instead: an empty batch would be uploaded to a directory the driver cannot then `mv`. **And a correction that cannot be built stops the run.** `_require_empty_well_models` raises when `empty_well` was requested with named wells and no model came back, naming the wells and the directory searched — an `INFO` line reading `(none found)` was the only previous signal, for a run that had explicitly asked for the correction. **Rewriting a well archive is a cost decision, and it says why.** The upload planner (`_plan_staging`) reads each archive's central directory — member names *and* compressed sizes, one read — and rewrites an archive only when the channel filter (or an incremental run's already-done FOV-groups) drops members worth at least `remote_config.REWRITE_MIN_SAVING` of its bytes. Rewriting costs a full local read and write before a byte is uploaded and buys only a smaller transfer, so shedding two small frames from a multi-gigabyte well is a bad trade — the remote ignores channels it was not asked for anyway, so sending them costs bandwidth and nothing else. Either way the plan line names the count, the fraction and **the reason**: the channel token parsed out of the dropped members' own names (or the bare filename, when the name does not decompose under the run's schema, since a guessed token is worse than none), or the FOV-group count for an incremental drop. Previously the log said only *that* it was rewriting, which left "every channel is selected, so why is it copying my plate?" answerable from no line the run emits. An archive from which nothing is dropped is passed through untouched and logs nothing — `keep is None` holds exactly when nothing was dropped, which is the invariant every diagnosis of this path rests on. **What counts as an image is one question with one answer**, `util/image_exts.GROUP_IMAGE_EXTS`, read by both `process_microscopy`'s scanners and this planner: they had drifted to `.tif`/`.tiff` here against `.tif`/`.tiff`/`.png`/`.jpg`/`.jpeg` there, so a `.png` member was invisible to the planner and was silently dropped from any archive rewritten for another reason — input the remote would have processed. **Batched mode** (`remote_batch_size > 0` → `_BatchedRemoteProcess`) uploads and runs the plate in slices so peak remote disk is one batch of input plus about one well of output. **One allocation serves every batch.** Each batch used to compose its own `srun`, so a scheduler-backed host queued once per batch and handed the node back in between — an allocation is the expensive thing to obtain and the cheap thing to keep. `_batch_driver_snippet` builds a loop that stays inside one job: it waits for each batch's `BATCH_READY` file, swaps that batch's staged directory into place, runs the pipeline, writes the exit code into `BATCH_DONE` and prints `BATCH_DONE_MARKER`, which the local reader thread consumes (it never reaches the log — `ProgressTracker` has no reason to see the handshake). **The swap is the barrier.** Batched input now lives in the run directory rather than the shared dataset cache, batch *i* is uploaded to `b<i>` beside it, and the driver does `rm -rf in && mv b<i> in` — atomic within one filesystem, so a batch's pipeline sees exactly its own zips. The old shared directory relied on the Fetcher having deleted the previous batch's input in time and nothing waited for it, which is what `ProgressTracker.well_repeats` counts. **A failed batch keeps the node**: the loop carries on, the run's own code carries the first failure, and an end-of-run tally names which batches failed. **The stream ending is the only signal that the run is over.** Stop reaches the batched wrapper through `signal_subprocess`, which reads `proc.pid`; with one driver held for the whole run that is always a real pid, so the process group is killed and `terminate()` — the method that sets the stop flag and cancels the Fetcher — never runs. The reader thread therefore treats EOF with batches still unreported as the run being over: it sets the flag and cancels the Fetcher itself, which is what gets the worker out of the rsync it is sitting in (`upload_subset` watches that flag). A walltime expiry or a node failure arrives the same way and is reported differently, since one is the user's doing and the other is not. Two things do end it, both meaning the app is gone rather than slow — a heartbeat that stopped advancing and a batch that never arrived — and each batch is additionally wrapped in `timeout` at the budget the operator's original `--time` set for one batch, so one wedged well cannot burn the whole run's walltime. That `--time` is scaled by the batch count *plus one* (`remote_config.scale_launcher_time`, clamped at `MAX_HELD_ALLOCATION_S`) because it sized a single launch and now has to cover the plate: the node is also held through every upload after the first, since the driver waits for each batch inside the allocation, so budgeting only the compute puts the expiry somewhere in the last batch of any run whose transfers are not free. The extra batch's budget is a guess and the log line says so. A launcher that sets no `--time` is left alone, since inventing a number is worse than inheriting the cluster's. Each batch is still its own pipeline invocation and so emits its own "N well(s) to process" banner for its own slice; `_run_batches` therefore injects one synthetic `Zip mode: <N> wells — batched` line ahead of batch 1, and `ProgressTracker` locks the run's denominator onto the first banner it sees. That `<N>` is the whole **staged** plate, including the wells the batched path skips because their per-well CSV is already complete locally — each skipped well gets its own `SKIP well …` marker on the same stream, so it is counted and painted on the plate map instead of leaving the run reporting 100% against a denominator smaller than the plate the user staged. **The allocation is asked for before the plate is uploaded.** A queue wait and a transfer are independent waits, and spending them end to end adds the whole queue to every run; requested first, they overlap. The job `srun` places starts the moment a node is granted, which may be long before the last byte lands, so the composed command blocks on an `UPLOAD_SENTINEL` file written into the run directory once rsync returns — announcing both edges of that wait, because a granted node waiting on bytes looks exactly like a hung one. `UPLOAD_WAIT_TIMEOUT_S` bounds it, since the node is idle and billed throughout; a failed upload cancels the allocation immediately rather than leaving it to time out. That deadline is for an uploader that is alive but wedged — the case that actually happens is the app being *killed*, and for that the uploader beats `UPLOAD_HEARTBEAT` every `UPLOAD_HEARTBEAT_S` (a daemon thread, not a per-file hook: rsync can spend minutes inside one large archive). A beat that stops advancing frees the node within `UPLOAD_HEARTBEAT_STALE_S` however long the upload legitimately needed. The file is touched over ssh, so the mtime the job compares against its own `date` is the remote clock and no skew between two machines is being measured. **Crash recovery closes the other half.** `close_remote_job` covers every exit the app survives — clean quit, finished run, Stop — because it reads the job id from memory; a SIGKILL, segfault or power cut runs none of it. The id is on the shared filesystem the whole time and `run_id` is deterministic in (input, arguments), so `_sweep_orphaned_allocation` lets the *next* run of the same plate find and cancel exactly the job the crashed one left. That determinism cuts both ways — two sessions on the same plate compute the same `run_id` — so the sweep refuses to touch a job whose heartbeat is fresh or whose sidecar was written moments ago, and returns which of the four verdicts it reached (`swept`, `owned`, `notmine`, `unknown`) rather than only logging it: the batched path is about to `rm -rf` that same run directory, and on `owned` it stops the run instead, since both sessions would otherwise stage batches into one directory. **Nothing it cannot measure is treated as dead.** The ages come from `stat -c %Y` *or* BSD's `stat -f %m`, and a host that answers neither gets `unknown` and keeps its job — the earlier code gave the two files opposite defaults for the same failure (epoch 0 for the heartbeat, *now* for the sidecar), so such a host silently never swept anything. The beat itself runs for the **whole** run rather than just the upload, on both paths, because it is what the sweep reads: stopping it when the transfer ended made an hour-long compute look abandoned to the next session on the same plate. **Neither cancel path will touch a job that is not the caller's**: both ask `squeue` who owns the id and stop if the answer is not `id -un`, and both pass `-u` to `scancel` as well, so a job that changed hands between the question and the answer still cannot be cancelled. Slurm would refuse a non-owner anyway — but that is the scheduler's policy rather than this app's promise, and it does not hold for an operator account. The overlap is taken **only** where there is something to queue for: a host with no launcher runs on the login node as soon as ssh connects, and a cache hit runs `true`, so both keep the old upload-first order — starting either early would run the pipeline against an input directory that is still filling. The run directory can be known in advance because `cache_key` is a local computation over the input archives; `spawn` checks the upload landed under that same key, since a divergence means the bytes went somewhere the waiting job will never look. |
| `remote_config.py` | Per-host remote definitions loaded from `~/.config/allwell/remotes.toml` (or `$ALLWELL_REMOTES`). Each `[remote.<name>]` entry is an SSH alias + `remote_python` / staging dirs / cache budget, plus the optional **`compute_launcher`** (the `srun …` scheduler line consumed by `remote_runner`) and **`jupyterhub_config_path`** (where to find `jupyterhub_config.py` on the remote). Hosts are picked + edited in `analyze_remote_dialog.py`, which can **Fetch** the cluster's discrete compute options by reading the remote's `jupyterhub_config.py` spawner `profile_list` over SSH (no JupyterHub webpage needed) — picking a profile fills in the whole scheduler command. |
| `input_resolution_service.py` | `plan_input_layout` is the one answer to "what will this folder do?" — pure, returning an `InputPlan` that Analyze's readout displays and `resolve_input_output` executes (§6.1). Around it, the module validates the input folder shape (zip set vs flat) and drives the loose-TIF **grouping (zipper) phase** — `run_zipper` shells out to `WellPlateZipper.py`, which discovers every TIF with one `os.walk` and copies (or, with `--move-source-tifs`, moves) each into its per-well subfolder. `group_by_well` filters discovered entries through `os.path.isfile` (which follows symlinks) so a **broken symlink** — a link whose target is on an unmounted/moved volume, still listed by `os.walk` among `files` because it is a non-directory entry — is skipped at discovery with a per-file warning rather than being handed to `folder_well`'s symlink-following `shutil.copy2`, where opening the missing target raised a bare `FileNotFoundError` that crashed the subprocess and failed the whole plate. This matches the rest of the pipeline's discovery, which already treats a broken link as absent (`process_microscopy.organize_loose_tifs_into_well_folders` and `find_image_groups` both gate on `Path.is_file()` / an existence check). `WellPlateZipper.main` first calls `util.frozen_io.ensure_std_streams` — in a frozen windowed (`console=False`) build the bootloader leaves this subprocess's `sys.stdout` / `sys.stderr` set to `None` even though the parent piped fd 1 / fd 2, so without the rebind every per-well progress line and every skip warning is a silent no-op and the grouping phase looks frozen; the same shim backs `process_microscopy._ensure_pipeline_io_ready`, so both subprocess entry points share one implementation. |
| `env.py` | `validate_env` — a pure startup check of the `ALLWELL_*` overrides (`ALLWELL_SCRATCH_DIR` / `ALLWELL_REMOTES` / `ALLWELL_SEG_DEBUG`), returning warnings for any that are set but unusable so a mistyped path is not a silent fallback. |
| `ssh_key_setup.py` | Ed25519 keypair generation at `~/.ssh/allwell_<alias>` + the `ssh-copy-id` walkthrough behind the remote-host wizard. |
| `subprocess_kill.py` | Kills a spawned pipeline's whole process group / session (what Stop uses). |

These modules deliberately have no Qt imports beyond what the Analyze tab
itself uses — keeping them potentially reusable for a CLI driver.

### 6.5 Scratch, staging, and where each write lands

A run touches two classes of storage, and keeping them apart is what makes it
survive an unreliable output volume.

**Remote staging moves only what it uploads.** A remote run in zip mode with a
channel filter or `--incremental` cannot use the user's archives directly — both
paths rewrite the zip (dropping unused channels / already-done FOV-groups), and
rewriting in place would corrupt the user's dataset. So it stages. The staging is
**plan-then-materialise** (`remote_runner._plan_staging` / `_materialize_staging`):
the plan pass reads each archive's *member list* only — that comes from the zip's
central directory, a small read at the end of the file, so planning a whole plate
costs a handful of seeks even across a network mount — and the materialise pass
then writes only the members that will actually be uploaded, reading nothing
else. This ordering is the point: staging by copying each archive in full and
*then* rewriting it down to the wanted members pulls the entire dataset across
the network before a single byte reaches the remote, which is exactly backwards
when the data lives on a NAS — the normal case. The channel-filter typo guard
runs in the plan pass, so a mistyped channel name names every offending well
before any bytes move.

**"Changed" is decided on the images alone.** `_is_image_member` splits each
archive's member list before the channel filter sees it: anything with a leading
dot or a non-image suffix — `.DS_Store`, directory entries, AppleDouble sidecars
— is not a channel, so the filter has no opinion on it and it can never be the
reason an archive counts as changed. Without that split a single stray file per
zip made every archive on the plate look filtered (a report of 276 of 279 members
kept, on a plate whose zips held only the two requested channels) and rebuilt the
whole dataset into scratch to strip a few hundred bytes a well. The zero-match
guard and the incremental up-to-date check read the images too, so junk can
neither satisfy a typo'd channel filter nor keep a finished well in the upload.
An archive that survives is used exactly as it is, junk and all — the remote
ignores it anyway, since `find_image_groups_in_zip` skips the same members — and
one that is rewritten carries only the images the remote will read.

**Only an archive that actually changes is staged.** A plan that keeps every
member is not written anywhere: its source path goes straight into the upload
list and rsync reads it from the input dir. Copying it into scratch first would
move the same bytes twice — once onto the local scratch volume, once to the
remote — and only the second copy is the one anybody needs.
`_open_staging_if_needed` therefore creates the staging directory only when some
plan has a rewrite in it, and `stage_local_zips` returns `cleanup=None` and the
input dir as its `zip_dir` when none does; `well_zips`, not `zip_dir`, is the
authoritative list of what gets uploaded, and it can name files in both places.
This is not an edge case: the Analyze tab always requests selective channels, so
a run whose channel list happens to cover every image in the plate takes the
filtered path with a filter that drops nothing — and used to copy the whole plate
into the OS temp dir to get there. It also makes the upload cache work as
designed, since `RemoteRunner.cache_key` hashes each archive's `(name, size,
mtime)`: a freshly-written staging copy has a new mtime every run, so an
unchanged plate keyed differently every time and re-uploaded; passing the source
through keeps the key stable and the second run skips the transfer.
**MIXED mode** — some wells zipped, others still folders, the signature of a
run that died mid-compression — stages through the same two passes; it is the
mode most likely to arise on a slow share, so a full copy there was the worst
place to keep one. Its leftover folders are still zipped into the *input* dir
(persisted deliberately, so the next run sees plain ZIP mode and re-zips
nothing), and with no channel filter it uploads from the input dir with no
staging at all. `_filter_channels_in_staging` survives for the FOLDER / FLAT
path, which filters zips it has just written itself.

**Suspending a run.** Stop is a kill — SIGTERM to the process group, SIGKILL
five seconds later — and whatever each worker was mid-way through is lost.
Suspend (`PipelineRunner.suspend`) sends the same SIGTERM with the escalation
suppressed (`subprocess_kill.signal_subprocess(..., escalate=False)`, since a
well finishing its FOV-group can easily outlast five seconds), and the pipeline
*catches* it: `pipeline/suspend.py` installs a handler in the orchestrator and in
every pool worker, the per-group loops stop taking on new FOV-groups, wells not
yet started are cancelled, and each well that did any work returns through the
normal end-of-well path so its CSV and `<well>_out.zip` are written. A second
signal is a hard stop — the handler restores the previous disposition before
returning, so a suspend that hangs is still escapable.

Nothing records "where the run was". The output directory already is that:
`--incremental` reads the per-well CSVs and out-zips back as the done-set of
`(fov, timepoint)` groups, so a resumed run recomputes only what is missing, and
a suspended output is indistinguishable from one left by a run that ended early
for any other reason. A group the run never attempted is deliberately **not**
added to the well's `failed` list — it is missing, which the completeness record
already reports as the gap between the input's group count and the CSV's, and
calling it failed would put it in the PARTIAL warning as though the data were
broken rather than unfinished.

What the app *does* lose across a restart is intent — which folders and which of
the several dozen form fields produced that half-finished output — so
`services/suspend_record.py` persists exactly the `RunSpec` that was running,
plus enough to describe it. The same `to_json` the job queue already uses; no
second serialisation to keep in step with the form. It lives at
`~/.allwell/suspended_runs.json` rather than in the output folder because at
startup the app does not yet know which output folder to look in, and it holds a
list keyed by output directory so several plates can be suspended at once.
`analyze_suspend.AnalyzeSuspendMixin` is the seam: the Suspend button, the banner
above the Analyze form offering the most recent record, and the resume itself,
which forces `incremental=True` into the spec before restarting. That flag is not
a preference here — without it the pipeline skips any well whose CSV exists and
reprocesses nothing, so a half-done plate would come back still half-done with
the run reporting success. The record is cleared on resume, on discard, and when
a run completes normally; a *stopped* run keeps its record, because a hard Stop
also leaves every finished well's CSV on disk.

The **Datasets Job Queue** can suspend too, and carries the result across a
session. Its Pause button winds the running job down (`suspend_current`, which
probes the runner for a `suspend` hook and falls back to a plain cancel for the
Datasets tools, none of which has an equivalent boundary to stop at); a separate
`Stop job` button is the destructive answer. A wound-down job goes to
`JobState.SUSPENDED` — deliberately not through `mark_finished`, since a suspend
is not an outcome and treating it as a cancel would cascade its successors to
BLOCKED as though its output were never coming.

`SUSPENDED` and `INTERRUPTED` form `JobState.RESUMABLE`: work that stopped
part-way with its results intact on disk. `next_runnable` selects them alongside
`PENDING`, so starting the queue picks such a job up rather than requiring it to
be re-added by hand, and neither is in `TERMINAL` any more — a job with a future
is still worth reordering. `_repair_after_load` turns a `RUNNING` job (the app
went away mid-run) into `INTERRUPTED` and no longer cascade-blocks its
successors: they stay `PENDING` and wait for output that is still coming.

A run the queue started also suspends from the Analyze page, and the queue does
the right thing for free there: `suspend` goes through `stop`, so
`AnalyzeRunMixin.was_cancelled` is true, so `JobQueueController._on_job_finished`
marks the job cancelled and pauses the queue rather than starting the next one
underneath a user who has just asked for their machine back. What did *not* come
for free is restarting that job from the queue. A queued job's stored spec never
carries `incremental` — it is not a form field — and re-running without it skips
every well whose output zip exists: the wells that never began are recovered, but
the ones that were *in flight* wrote a CSV and an out.zip from the FOV-groups
they had finished, so they read as complete, are skipped, and never get their
missing groups back. The plate returns looking done.
`AnalyzeSuspendMixin.resume_aware_spec`, which `start_from_queue` routes through,
forces incremental when a suspend record names the same input directory.

**Grouping consumes each well folder as it goes.** The FOLDER / FLAT staging
path (and the MIXED leftover zip-up) turns every `<well>/` into `<well>.zip`, and
removes that folder the moment its own archive is published rather than at the
end of the run. `_zip_well_folder` writes through `util.zip_write.atomic_zip`,
which renames a verified archive into place or leaves nothing at all, so a folder
is only ever removed once a complete archive sits beside it — the same standard
`process_microscopy.compress_folder_images_to_zip_and_remove` and the Datasets
pane's `_zip_well_folders` already apply. Deferring the deletion to the
post-success hook meant the input dir carried both `<well>/` and `<well>.zip` for
every well for the whole run: two full copies of the plate on the user's own
volume at peak, on exactly the runs where the input is largest. Two folders are
left alone — one holding no TIFs (there is no archive to replace it with, and it
may hold other content) and one whose well already had a user-written zip (that
archive was not written by this run and nothing here verified it covers the
folder). There is no rollback of the archives written in the pass: unlinking them
on a later well's failure would now delete the only remaining copy of every well
that had already succeeded, and a part-zipped plate is the correct resume state
anyway — the finished wells read as ZIP mode on the next run.

The remote client resolves its own scratch base — the staging rewrites, transit
output zips, and incremental merge temps — through `remote_runner._scratch_dir`,
in the same order and to the same default as the pipeline below:
`$ALLWELL_SCRATCH_DIR` → `~/.allwell/scratch` → the OS temp dir (last resort,
when home is unwritable). The two resolvers cannot share a constant, because the
client must not import `pipeline.io` and drag numpy and tifffile into the remote
path, so `tests/test_remote_runner.py` pins them to the same value instead. They
used to disagree, the client defaulting to the OS temp dir: a remote run then
staged gigabytes into `/var/folders/<hash>/T` on macOS, or a tmpfs — RAM — on
Linux, at a path the user had never chosen and could not find.

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
a module that must stay imaging-stack-free. Three behaviours are worth knowing:

- **An error retrying cannot fix fails on the first attempt.** A missing path,
  wrong permissions, a full volume (`_PERMANENT_ERRNOS`) are re-raised straight
  away, so a caller that treats a missing source as an ordinary `OSError` stays
  responsive and the real cause isn't buried under a retry story.
- **`ENOSPC` is the one that gets rewritten on the way out.** Callers pass the
  target `path`, and the failure comes back as `util.disk_space.space_message` —
  the volume that filled, how much is left on it, and that the scratch staging
  may be sharing it — with errno 28 preserved so callers matching on it still
  work. Everything else in `_PERMANENT_ERRNOS` passes through untouched.
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

Each feature is one `_Feature` entry in `datasets_tab.py::_FEATURES` and a
`datasets/<name>/` package on the standard view/controller/worker + Qt-free
compute/apply split; a completed run hands its output to another pane through
an injected callback (`on_open_in_review` → `AllWellApp._open_dataset_in_review`;
`on_open_in_analyze` → `_open_dataset_in_analyze`). Helpers shared across more
than one feature — dataset-folder resolution, well-container IO, schema-safe
filename rewriting — live in `datasets/_common/` rather than being imported
from one feature package into another, so each feature stays independently
deletable:

| Module | What it holds |
|---|---|
| `datasets/_common/dataset_paths.py` | `DatasetPaths`, `resolve_dataset`, `read_info`, well enumeration (`in_containers`, `out_csvs`, `out_zips`, `out_folders`, `dataset_wells`), the destination-state predicates (each taking the caller's own in-progress marker filename as a parameter), and the input-side availability scan (`collect_available_schema_values`) that mirrors `process_microscopy._collect_available_timepoints`/`_fovs` without importing the pipeline. That scan is two halves: `collect_input_image_stems(in_dir, known_zip_members=…)` walks the input folder (loose TIFs, one level of well subfolders, well zips' TIF members), taking already-listed member names for any zip the caller has read itself rather than opening it again; `schema_values_from_stems` extracts one field's distinct values out of stems already in hand. `collect_available_schema_values` is the two composed, and remains the entry point for a caller asking about a single field from a cold start (Apply repairs). A caller asking about two fields, or one that has just read every archive for its own reasons (Validate does both), goes through the halves and walks once. `out_folders` is `out_zips`'s folder-mode twin (`out/<WELL>/`, written when `compressed_output=False`) — a dataset is written either zipped or folder-mode, never both, but a caller checking for "done" image evidence regardless of which mode produced it (Validate) should check both maps rather than assuming one. `dataset_wells` unions all four well-enumeration maps, including `out_folders`, matching its own docstring ("every 96-well position present in a dataset's `in` or `out`") — every current caller (Concatenate, Merge Plates, Subset) already refuses folder-mode input before calling it, so this only matters for a future caller that doesn't. `preview_wells(root)` is a best-effort, exception-safe `dataset_wells` a page calls on every keystroke while the user is still typing a path — it returns `[]` for anything that isn't (yet) a real dataset folder instead of raising `DatasetPathsError` the way `resolve_dataset` would, since "not a valid path yet" is the normal, expected state through most of a field edit, not an error to report. `shorten_headline(text, *full_paths)` replaces every occurrence of each of *full_paths* inside *text* with its own basename — a run's success summary is built by formatting a full destination (or source) path into a sentence, right for the log line but unreadable as a dashboard headline; the caller keeps the original *text* for the log and passes it again as the headline's own tooltip, so the full path is never actually lost, only the on-screen headline is shortened. It skips any path that is empty (Import Loose's in-place mode, whose `dest_root` is `""`) — `"".replace` would otherwise insert the basename between every character of *text*. |
| `datasets/_common/containers.py` | `copy_container_members` — the one path every Datasets tool moves image bytes through — plus `read_members` / `write_zip` (collision-safe, first-write-wins), `clear_destination`, and the `timepoint`/`timepoint_hours`/`filename` column-name and cell-overrides constants. **It moves bytes rather than reconstructing them.** No Datasets tool alters image data: Subset filters, Concatenate offsets a name's timepoint field, Merge plates rewrites a name's well token, and in every case the member's compressed payload is identical on both sides — a name lives in a member's headers, not in its data. So the helper publishes one destination archive from one or more source containers, taking a `rename(container, member_name)` callback that returns the destination name or `None` to leave the member out, in three tiers. **Whole-file copy:** a single zip source with every member kept under its own name is `shutil.copy2`'d and never opened for reading. That is a wells-only Subset, and it is the case a user would otherwise do by hand in Finder, so it has to cost what Finder costs. **Raw member passthrough:** otherwise each kept member's already-compressed bytes are streamed across by `datasets/_common/zip_passthrough.py` — no inflate, no deflate, no CRC recomputation — and a dropped member is never read at all, so a filter that keeps a quarter of an archive costs about a quarter of it. **Fallback:** a folder source, or a member the passthrough declines (a directory entry, an encrypted member), goes through `read_members`/`write_zip`'s ordinary decompress-and-recompress, one member at a time. The tier is chosen from member *lists* only — for a zip that is the central directory, a footer, so planning costs a couple of seeks however large the archive is and however slow the volume holding it — and peak memory is one chunk or one member rather than a whole uncompressed archive per thread. **Tiers 2 and 3 publish through `util/zip_write.atomic_zip`**, the same staged-then-verified-then-renamed writer the pipeline's own archives use: a plain `with ZipFile(dest, "w")` writes a central directory for whatever landed before an exception, so a member failing part-way through a well left a structurally valid archive holding fewer images than it was asked for with nothing on it saying so. The tools do write an in-progress marker and clear the destination on the next attempt, but nothing stops a user opening that half-written folder in Review first. **Two consequences of moving bytes rather than rebuilding them are worth knowing.** A whole-file copy reproduces the source exactly, so it keeps directory entries that a re-pack drops — inert either way, since `find_image_groups_in_zip`, `data/zip_members.py`, Validate and `incremental_keys` all filter by basename/extension or on a trailing slash. And a copied member keeps its source's compression method, so a subset or merge of a dataset whose `in/` archives were written `ZIP_STORED` (what `pipeline/ome_explode.py` writes) stays stored rather than being deflated on the way out; the destination can be larger on disk than the old re-packing path produced, which is the cost of not spending CPU to reproduce bytes that already exist. Returns `(written, dropped)`: *written* is what actually landed, so a caller can verify a CSV row's `filename` is really there; *dropped* is only the names lost to a **collision**, which is real data loss, never a member the `rename` callback declined. Nothing is created when nothing is kept — a zero-member zip is structurally valid but macOS Archive Utility refuses to expand it and All-Well reads it as a well whose images have vanished, the same rule `util/zip_write.atomic_zip` enforces for the pipeline's own archives. `read_members` / `write_zip` remain the tier-3 primitives and the shape a caller reaches for when it genuinely needs a member's decoded bytes in hand; both take an optional `progress(done, total)` keyword, called once per member against a total they know up front. `WellFraction` fuses per-pass counts into one dial: copying a well is several passes (the source container, then the QC `<WELL>_out.zip`), so the passes are declared up front as equal-weight *slices*, each pass reports into the slice `slice_progress()` hands it, and a pass with no work closes its slice with `skip()`. `copy_container_members` consumes exactly one slice whichever tier serves it, reporting a whole-file copy or an empty filter as a single closing step. Feeding each pass's own `(done, total)` straight to a dashboard instead would fill the same well's dial from empty once per pass, which reads as the well restarting; leaving a no-work slice unclaimed would strand a finished well short of full. `finish()` closes out whatever remains once the well's non-container work (its CSV) is done. The scale is a fixed 100 steps, deduplicated to whole steps, so a well holding thousands of files emits a bounded number of updates rather than one queued signal and one plate repaint per file. |
| `datasets/_common/zip_passthrough.py` | `copy_member_raw` / `member_is_raw_copyable` / `RawCopyUnsupported` — copying one member's already-compressed bytes from one archive into another. The destination member keeps the source's compression method, CRC and both sizes, so it decompresses to exactly the same data; only its name and its position in the file change. **This depends on four `zipfile` implementation details** — `ZipFile.fp`, `.filelist`, `.NameToInfo`, `.start_dir` (plus the `_didModify` flag) — because the standard library exposes no public API for adding a member whose bytes are already compressed. Two things keep that contained: `member_is_raw_copyable` gates every member first, so a directory entry or an encrypted member is routed to the ordinary path as an expected case rather than an error; and `RawCopyUnsupported` is raised, with the destination truncated back to its exact pre-member state, when an archive's local headers disagree with its central directory. That last case is raised on rather than worked around — the ordinary path would fail on the same member for the same reason, and an archive that quietly holds fewer images than it was asked for is the one outcome this code must never produce. Any ZIP64 record in the source's extra field is stripped before the header is rebuilt (`ZipInfo.FileHeader` appends a freshly-computed one, and the source's describes the member at its old offsets); the data-descriptor flag comes off for the same class of reason, since the copied header carries the CRC and sizes inline where the source kept them in a trailer. **A member that is being renamed also loses Info-ZIP's Unicode Path and Unicode Comment records** (`0x7075` / `0x6375`). Each embeds its own copy of the member's name alongside a CRC of the name it was derived from, so across a rename it is a second, stale name for the same member: a reader that honours it and skips the name-CRC restores the file under its *pre-rename* name, silently undoing the well remap or timepoint offset the copy existed to perform. A member keeping its own name keeps those records, which still describe it correctly. |
| `datasets/_common/member_names.py` | `rewrite_member_field` — the schema-safe, field-agnostic zip-member filename rewriter (extension check, field-count check, channel-scoped role suffix preserved) that Concatenate's timepoint shift and Merge plates' well remap both wrap with their own validity predicate — plus `is_os_junk_member`, the shared answer to "is this member operating-system debris rather than data?" (`.DS_Store`, `Thumbs.db`, `desktop.ini`, `__MACOSX/`, AppleDouble `._` sidecars, the Spotlight and Trash directories). It is deliberately a *named set*, not "anything that isn't an image": a `notes.txt` a user put in an archive on purpose is real content, and a caller that refuses to reconcile two conflicting copies of a real file should keep refusing. Concatenate is its only caller — see §6b's Verification note for what carrying that debris through a merge used to do. |
| `datasets/_common/tool_controller.py` | `DatasetToolController` — the worker lifecycle every tool controller shares (`jobFinished`, `stop`, `is_busy`, `shutdown`), with the two things that differ between tools as knobs: `_shutdown_label`, and a `_request_worker_stop` hook. The hook exists for one tool. Import Loose's worker spends the run blocked inside the zipper subprocess's stdout read loop and never polls `isInterruptionRequested()`, so Qt's cooperative flag is a no-op there and `request_stop` — which also signals the tracked child — is what actually stops it; a base that hard-coded `requestInterruption()` would have left an orphan process on app close, and `shutdown` swallows the failure it would then hit. `DestinationToolController` adds the run-and-confirm-restart cluster the three copy-into-a-destination tools share (Subset, Merge Plates, Concatenate), with `_destination_has_marker` supplied per tool because a folder holding *this* tool's interrupted attempt is a resume and another tool's leftovers are not. Deliberately **not** on either base: `_on_queue_requested`, `_on_finished` and `_on_succeeded`, which measured six, five and two distinct shapes across the six controllers — pulling them up would trade plain methods for a base full of flags. |
| `datasets/_common/dataset_fields.py` | `is_dataset` / `is_free_destination` / `promote_open_button` — the destination-field validators and the success-state button swap the pages share. `is_free_destination` takes its two predicates as arguments for the same marker reason as above. Cross-field rules ("the destination must differ from both sources") stay on the page, since they read fields the helper cannot see. Sharing the *wording* is part of the point: a user moving between Subset and Merge Plates should not be told the same thing two different ways. |
| `datasets/_common/concurrency.py` | `pick_workers(workers, n_units)` — the shared worker-thread-count heuristic (0 = auto, capped at 8 and at the CPU count, mirroring `pipeline.ome_explode._pick_workers`) every tool below uses for its per-well `ThreadPoolExecutor`. |

`datasets/concatenate/concatenate_compute.py` and `apply.py` re-export the
names they lifted from `_common`, so their own public API (and every existing
test against it) is unchanged.

All six tool pages share their status/log furniture through
`datasets/_common/log_panel.py::LogPanelMixin`. Each page ends the same way: a
header row naming its output section with a "Show raw log" checkbox, then a
separator and a read-only monospace log the checkbox reveals. The mixin owns
those two ends. The middle — a `ConcatDashboard`, a bare `QProgressBar`, a
`ValidateDashboard` above a findings list — differs per tool and stays on the
page, which is why the extraction stops where it does.

**One implementation of the toggle serves all six, with no overrides.** It
checks `_status`, `_log_sep` and `_log_hdr`; Validate is the only page holding a
log header of its own, and the rest contribute nothing there. Validate also
passes `clear=False`, keeping its Clear in the raw-log header rather than the
Findings one, where it reads as belonging to the findings and wipes a log that
is usually hidden. Per-page differences are parameters: section title, status
`objectName`, placeholder wording, and the toggle tooltip.

`datasets/_common/` otherwise holds only logic (containers, dataset paths,
member names, zip passthrough) — including `dataset_paths.split_stem_well`,
the pane's single reader of a trailing well token on a longer stem
(`measurements_b10` → `("measurements", "B10")`). That is not
`util.well_token.canonical_well_label`, which judges a whole token; it is
equally deliberately the only place the judgement is made, since the same
regex and zero-padding in three modules is the shape §15 warns about. A shared *view* layer matters here for a
specific reason: without one, a fix to any of this lands on one page of six,
and the other five keep whatever they had.

Two properties the seam makes enforceable, both swept parametrically over
`datasets_tab._FEATURES` by `tests/test_datasets_log_panel.py` — driven off the
registry rather than a roster written in the test, so a seventh tool is covered
from the moment it exists. No page may re-define a shared member, and every
page needs a trailing `outer.addStretch(1)` after its last real widget — since
`QScrollArea.setWidgetResizable(True)` grows the content widget to the viewport
and the dashboard is otherwise the only thing willing to absorb the slack,
turning into a dead zone below its own height-capped plate.

**Convert OME-TIFF** (`datasets/ome_import/`) is the standalone OME-TIFF
de-interleaver described in §6.1: `ome_import_compute.plan_conversion` validates
the well-grid / FOVs / frame-order against the file's `OmeLayout` for the live
readout — and, first of all, that the file contains every image plane its
metadata declares (§6.1) — alongside `ome_import_compute.selection_target`, which reads the file's
position/frame budget from the same `OmeLayout` and (when FOVs is pinned) the well
count it implies, so the readout guides how many wells to place before any grid is
entered, not only errors at the end. `apply.convert` calls `explode_ome_tiff` (which writes per-well zips into
`<destination>/in/`, threaded, with progress + cancel), and `OmeImportWorker`
runs it off the UI thread. **The form is read once, through
`ome_import_compute.RawFields`** — the boxes exactly as typed, with `parse_count`
telling a blank ("auto", a real answer) from a value that is not a whole number.
`RawFields.problems()` returns the user-facing reasons a box can't be read and
`to_params()` the settings it describes; the page shows the former and refuses to
run while there are any, so a typo in FOVs per well or Workers is answered instead
of collapsing into the same silent default as an empty box. The grid boxes are
exempt while the order is hand-drawn, since nothing then reads them. The
destination field carries a validator of its own (`destination_problem`) like
every other tool's: a conversion overwrites the wells it writes and keeps the
rest, so a folder that already holds a plate would end up holding two.
The readout is two labels — neutral context above, and a pass/fail line below it
whose object name switches between `Muted`, `Warn` and `Danger`, because a
refusal and a green ✓ in the same grey differ only by a glyph. **A run locks
every input that would redraw the plate**, not only the plate: the plate is the
progress display, and a grid edit re-seeds its preview, which would swap the
wells being reported on — dials and all — for the ones the grid currently
describes while the conversion carries on against the order it started with.
**A finished conversion says how it went on the page.**
This is the one tool with no dashboard — it reports on the acquisition plate it
already draws (below) — so the verdict has a label of its own under the progress
bar (`set_outcome` / `clear_outcome`), painted by the shared
`widgets.plate_progress.apply_outcome` so a conversion reads the same way here as
on any dashboard headline. It belongs on the page and not only in the raw log,
which `LogPanelMixin` hides by default: a verdict that reaches only a hidden pane
leaves a failed run looking exactly like one that was never started. A terminal
state also lands the progress bar, which a run that ends before it has counted
its planes would otherwise leave in its indeterminate state — a bar still
sweeping is the one thing on the page claiming work is happening. On success
the page's "Open dataset in Analyze" button routes the produced folder to the
Analyze pane (`on_open_in_analyze`) — the output is an Analyze *input*, not a
Review dataset. The page also carries a per-channel **role picker** (one row
per OME channel name, seeded by `ome_import_compute.default_channel_roles`'s
name heuristic — a strong nuclear stain (`DAPI`/`Hoechst`/…) wins the
segmentation role, else a secondary `NIR`/`RFP` channel, else the first channel
that does not read as phase/brightfield); `channel_tokens_from_roles` aggregates
the picks into a minimal `pipeline_info`-shaped dict (`nuclear_token` /
`fluor_tokens` / `phase_token`, with the phase channel remaining a member of
`fluor_tokens` so Analyze has a row to carry its Phase checkbox)
that the open-in-Analyze callback (`AllWellApp._open_dataset_in_analyze`) feeds
to `AnalyzeTab._apply_pipeline_info`, so Analyze's Channel Tokens arrive
pre-filled. The channel names are written into the output filenames regardless
of the roles.

That callback takes a `pipeline_info`-shaped settings dict and an `incremental`
flag, and both of its callers use them: the converter passes the channel roles
it assigned, Validate & Repair passes the dataset's whole recorded sidecar with
`incremental=True`. **It has to be legible on arrival.** Everything it changes
is either a field whose new value is indistinguishable from the old one at a
glance, or — for *Incremental*, the setting that decides whether the run
completes anything at all — a checkbox inside **Compute Options**, which is one
of the sections that fold by default. A correct handoff therefore looked exactly
like landing on an untouched Analyze tab, which is how a working feature gets
reported as doing nothing. So it unfolds that section
(`AnalyzeFormChromeMixin.reveal_section`) and `_announce_analyze_handoff` states
on the run dashboard what was set up, naming the dataset — and, when the sidecar
could not be read, saying in those words that the settings did **not** travel,
since a user who can't tell the two apart presses Run on whatever the form
happened to be holding. It applies the settings **first** and writes the input field
**afterwards** — a full sidecar's `execution_options` carries the `input_dir`
recorded on whatever machine ran the pipeline, and letting that win would point
Analyze at a path that need not exist locally, whereas the folder the caller
names is the one the user just picked. It ends by calling `_refresh_output`, so
the resolved output folder (and with it the incremental run's prior directory)
is on screen before the user presses Run.

The readout and the Convert/"+ Queue" buttons sit above the acquisition plate
rather than below it — every other Datasets tool puts its primary action above
a height-capped plate map, and an uncapped acquisition plate would
push both off the bottom of the fold. It is capped by
`apply_plate_geometry(plate, role="editor")` — the same helper, and therefore
the same cell scale, as every other plate map in the app.

The page also carries that `PlateOrderEditor` (§6.1): the grid fields
seed a live serpentine preview, and the user can click wells to define an
arbitrary `ConversionParams.well_order` (populated from
`PlateOrderEditor.order()`) for a non-rectangular scan, which `plan_conversion`
/ `explode_ome_tiff` use in place of the serpentine.

**That same plate is the run's progress view**, the way Subset's picker is: the
wells on it are exactly the wells being written, so a second plate map would be
a copy of this one. It is locked (`setEnabled(False)`) for the run's duration —
a click would edit the very order being converted — and each well fills as a
dial. `explode_ome_tiff` takes a `well_progress_fn(well, done, total)` beside
its run-wide `progress_fn`, ticking once per plane written with that well's own
share (its FOVs × timepoints × channels); `datasets.ome_import.apply.convert`
quantises those ticks through `_common.containers.WellFraction` before they
cross the Qt signal boundary, so a well of thousands of planes emits at most 101
updates rather than one per plane. The quantising lives on the `datasets` side
deliberately: what counts as "often enough to be worth a repaint" is a UI
question, and `pipeline/` may not import `datasets/`. The run-wide
`QProgressBar` beneath it is hidden until a conversion starts — an idle bar
reading 0% from page load describes a run nobody asked for.

**Editing the order drops the dials.** A dial says how far through *this* order
a conversion got, so once the order changes it describes wells that are no
longer the ones on screen — `PlateOrderEditor._set_path` and its
`pathChanged` handler both clear the fractions. Without that, the plate mixes a
finished run's fills with a freshly edited path: a well removed from the order
keeps its full accent disc, because a well carrying a fraction is drawn *as*
the dial and never reaches the depressed branch. Subset's picker carries the
same guard (§11).

The page wraps its whole
content in a `QScrollArea`, the same pattern every other Datasets tool page
uses (mirroring `analyze_tab.py`'s own form panel): without it, growing the
status section (the raw log filling with lines, the progress bar becoming
visible) left no room in a fixed-height window for both it and the
`PlateOrderEditor` above, so the plate quietly rendered at a fraction of its
natural size instead of the page scrolling to reach the status section.

Convert OME-TIFF is the sixth and last Datasets tool wired into the Job Queue
(see the Job Queue entry below): `OmeImportController` implements the same
`QueueableController` protocol as the five plate-driven tools —
`collect_spec`/`show_job` round-trip `ConversionParams` (including
`well_order`, serialised through `dataclasses.asdict`) alongside the source and
destination paths, `start_from_queue` calls the same `_launch()` a manual "▶
Convert" press does, and `jobFinished` fires once per run from `_on_finished`.
Its own page carries a "+ Queue" button beside "▶ Convert", matching the other
five tools' run rows.

**Concatenate Datasets** (`datasets/concatenate/`) appends one dataset's
timepoints after another's. Two datasets of the same plate, imaged in separate
sessions, are stitched into one longer timelapse: dataset 2's timepoints are
relabelled to continue after dataset 1's last timepoint, and the union of both
datasets' wells is written to a fresh destination folder (both originals
untouched).

The package splits along the standard suffix contract, and deliberately keeps
everything that isn't Qt out of the two `QThread`/view modules so the whole merge
is unit-testable without the app (`tests/test_concatenate_compute.py`):

| Module | Role |
|--------|------|
| `concatenate_compute.py` | Pure planner. Reads + validates both `pipeline_info.json` (refuses on differing schema/separator/indices/nuclear-token/segmentation-method, and on dataset 1 and dataset 2 being the same folder), detects dataset 1's timepoint label format, computes the hour offset, enumerates wells, and returns a `MergePlan`. Also owns the structural filename-rewrite gate (`rewrite_member_name`) and the in-progress-marker restart check. |
| `apply.py` | Qt-free executor. Given a `MergePlan`, copies dataset 1's per-well inputs/CSVs/QC-zips as-is, re-packs dataset 2's with every timepoint-derived value shifted, stitches shared wells (CSV columns unioned like `pipeline/io.merge_records_into_csv`), carries over `cell_overrides.json`, verifies the result, and writes the merged sidecar. Copying goes through `_common.containers.copy_container_members`, so a well that comes from one dataset only is a whole-file copy, and a merged well moves each member's compressed bytes straight from whichever side holds it — dataset 2's under a name whose timepoint field has been shifted, dataset 1's under its own. A timepoint offset changes the name in the member's header and nothing else, so there is nothing to decode on either side. It tracks which member names collided and were dropped rather than silently discarding them; a nonzero drop count marks that well `"warned"` and fails `_verify` outright, since a name that "looks fine" in the merged archive can be the wrong dataset's bytes under it (see Verification below). Progress via a `log` callback, a `progress(well, stage)` callback (`"merging"` → `"merged"`/`"warned"`) that drives the live dashboard, and a `sub_progress(well, done, total)` callback that drives that well's dial *within* the stage (fused across the well's two container passes by `_common.containers.WellFraction`). `sub_progress` is the one callback deliberately left outside the callback lock: every call names its own well, so interleaving is harmless, and taking the lock once per member would serialise the parallel copy it reports on. Wells merge in parallel through a `ThreadPoolExecutor` (`pick_workers`) — each owns its own destination files, so `log`/`progress` calls before a well's work starts are the only thing serialised behind a lock; aggregating each future's result into `well_stats`/`dropped_members`/etc. and reporting the terminal `"merged"`/`"warned"` state both happen back on `execute_plan`'s own thread as futures complete, so that bookkeeping needs no lock at all. |
| `worker.py` | Thin `ConcatenateWorker(QThread)`; runs planning + `execute_plan` off the UI thread, marshalling progress/result/errors through queued signals (`logLine`, `wellsPlanned`, `wellStage`, `wellFraction`, `succeeded`, `failed`), and forwards `isInterruptionRequested` as a cancellation check. `wellFraction(well, done, total)` is the per-well sub-progress signal every Datasets tool's worker that copies data carries beside `wellStage` (Validate has none — it inspects rather than copies, so a well has no sub-steps); it is what lets a well render as a progress dial rather than a flat in-progress colour. A controller connects it to its dashboard's `set_well_progress` when that dashboard owns a plate, and straight to its page's own plate otherwise — Subset's dashboard is built `show_plate=False`, so `SubsetController._on_well_fraction` drives the picker plate's `setWellFraction` instead. |
| `view.py` | `ConcatenatePage(QWidget)` — a one-line equation row (dataset 1 + dataset 2 = destination; compact `_FolderField`s showing a basename with the full path on hover, each with its own inline validity hint) above a `ConcatDashboard`, with the raw text log demoted to a collapsible "Show raw log" checkbox (the same furniture as `analyze_tab_log.py`'s log panel, started closed rather than open — see §6.1). Emits `runRequested` / `openInReviewRequested`. |
| `controller.py` | `ConcatenateController` — wires the page to the worker, drives the dashboard's per-well and per-run state at each signal, distinguishes a cancelled run from a failed one, and confirms before resuming into a destination with leftovers from an interrupted attempt. |
| `dashboard.py` | `ConcatDashboard`, built on `widgets/plate_progress.py::PlateProgress` (shared with Analyze's `RunDashboard` — see §7). Two independent state axes: **per well** (`pending`/`merging`/`merged`/`warned` — image data survived on one side with no matching measurements CSV) and **per run**, a phase rail (Validate → Merge → Verify → Finalize) with a terminal Done/Failed/Cancelled outcome painted on the headline. The per-run axis exists because `apply._verify` and the cell-override carry-over run *after* every well already shows green — without it, a verification failure would leave the plate looking like a complete success. Every well's terminal states carry a marker glyph too, not just its colour — `merged` gets `✓`, `warned` gets `!` — so state is legible without relying on distinguishing success-green from accent-blue by colour alone. A well still `merging` carries no glyph: it is drawn as a progress dial, and a glyph would sit in the middle of exactly the area the dial fills. `set_done(summary, *, tooltip=None)` shows a caller-shortened headline (via `_common.dataset_paths.shorten_headline`, stripping the destination path down to its basename) while the full original text — the one still written to the log — survives as the headline's own tooltip. |

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

**Operating-system debris is left out of dataset 2's side of a merge**
(`_MemberRename`, gated on `_common.member_names.is_os_junk_member`). A
`.DS_Store` names no data, so its name is the only thing it has — and both
datasets have the same one as soon as either folder has been opened in
Finder, which is the normal state of any plate someone has looked at. Carried
through, the two copies collided in the merged archive, one was dropped by
first-write-wins, and the verification below read that drop as dataset 2's
data failing to survive: two ordinary plates could not be concatenated at
all, over files neither the pipeline nor Review has ever read. Dataset 1's
copies pass through untouched, as everything of dataset 1's does, and the run
log says how many were skipped rather than dropping them silently. This does
not loosen the check it was blocking — the skip is scoped to
`is_os_junk_member`'s narrow, explicitly-named set, so two datasets that
disagree about a file a user actually put in an archive still collide and
still fail.

**Verification.** Before writing the sidecar, `apply._verify` first checks
whether any zip member collided by name during the merge and was dropped by
`copy_container_members` — a dataset-2 member whose filename fails the structural
rewrite gate (see above) keeps its original, unshifted name, and if that
name coincides with a dataset-1 member (most easily reproduced by pointing
both datasets at the same folder), the merge would otherwise keep only one
side's bytes under a name that still "looks fine" in the archive, with
nothing else able to tell the difference — a dataset-2 row's `filename`
check below only confirms the name *exists* somewhere, not whose bytes are
under it. Any dropped member fails verification immediately, naming the
well and file. A member the rename callback *declined* — dataset 2's OS
debris, above — is not a dropped member and never reaches this check;
"nobody asked for it" and "it was asked for and lost" are different facts,
and conflating them is what made the check fire on `.DS_Store`. It then checks that the merge actually wrote *any*
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

`ConcatenatePage._update_status_preview` reveals `ConcatDashboard` as soon as
both dataset fields are valid, rather than only once "▶ Concatenate" is
pressed — seeded via `_common.dataset_paths.preview_wells` on both dataset
roots with a "Ready — N well(s)" headline, so the plate answers "what would
this actually touch" before a run starts rather than sitting invisible with
nothing to show yet. It steps aside once a real run begins
(`ConcatenateController` drives `set_wells`/`set_phase`/well colouring from
that point on) and resets again on the next field edit
(`_on_field_changed`), which also hands the "▶ Concatenate" button's primary
emphasis back from a still-visible post-success "Open in Review" button — see
below. `set_running(True)` locks all three fields
(`PathField.setLocked`, disabling only each field's Browse button, not the
whole field) for the run's duration — previously the one Datasets tool that
locked none of its fields at all, leaving a mid-run pick free to change what a
subsequent Concatenate press would act on. On success,
`show_open_in_review` promotes "Open in Review" to `variant="primary"` and
demotes "▶ Concatenate" to `variant="secondary"` (via `widgets._support.
set_variant`, see §7) — the natural next step is opening the result, not
re-running the same merge. The status section's header/dashboard title read
"Concatenate Output"/"Concatenate Progress" rather than "Merge Output"/"Merge
Progress" so they don't collide with Merge Plates' own identically-purposed
labels; the page heading and the Datasets-pane sidebar entry are both
"Concatenate Datasets", title-cased to match the other five tool headings.

**Validate & Repair Dataset** (`datasets/validate/`) answers a question the
GUI otherwise has no answer for: which `(well, fov, timepoint)` work an
Analyze run actually finished, versus what `pipeline_info.json` — written
*before* any image work, per §6.2 — claims. A crashed or partial run leaves
the sidecar advertising the full plate while `out/` holds a fraction, and a
plain re-run then treats a partially-done well as complete
(`output_exists_for_well`), so the FOV-level gap is otherwise invisible.

**The rule it applies is not its own.** `pipeline/completeness.py` holds the
single definition of a complete well — the CSV ∪ archive union, the
`intent - done` diff with its single-FOV fallback, and the four health states —
and both this tool and the pipeline's own end-of-run check (§6.2) go through it,
so the two cannot disagree about a well. Validate keeps everything built *on
top* of that answer: run-record evidence, the twelve finding codes, the
per-artefact read budget its tests pin exactly, and repair. The shared functions
open only a well's output container, taking input member names and parsed CSV
rows from whichever caller already has them — which is precisely what lets this
scan delegate without changing how many times it touches the disk.

**A finished scan is recorded.** `datasets/validate/record.py` writes the scan's
own result into `pipeline_info.json`'s `run_check` block, through the same
`pipeline.completeness.build_block` / `well_record` pair the pipeline's end-of-run
check goes through, so there is one record format rather than a pipeline one and
a Validate one. That closes the loop Review's Home page opens: Home reports a
record rather than scanning, a dataset produced before the end-of-run check
existed has none, and the only action Home offers such a dataset is to open this
tool. Until the scan recorded its answer, following that offer measured the whole
plate and then discarded it, leaving Home saying exactly what it had said before.

The block carries `source`, and the distinction is not cosmetic: a `"run"` record
describes the run that had just finished, a `"validate"` record describes the
folder as it stood when someone looked at it, possibly months later and after
hand-edits. Home's masthead reads *checked <date>* for one and *run finished
<date>* for the other. Two smaller consequences of the same difference — a scan
has no run boundary to filter by, so its `errors` count is every traceback
present in `out/errors/` rather than only those the last run produced, and
`WellReport` carries `intent`/`covered_csv`/`covered_any` alongside `missing`,
because how much is absent does not say how much was asked for and the plate map
draws a fraction from both.

Only a whole scan is recorded. A cancelled one covers an arbitrary prefix of the
plate, and a record built from it would paint every unscanned well as measured
and found absent. A failure to write is reported in the scan log and does not
fail the scan: the findings are the deliverable and are already in hand, and
saying so out loud is what keeps "recorded" and "silently not recorded"
distinguishable — the latter looks, from Home, exactly like a scan that never
ran.

`validate_compute.py::build_report(root)` is a read-only Qt-free scan.
*Intent* — what a well's raw input actually contains — is read through
`pipeline.incremental_keys.fov_keys_from_member_names`, the name-parsing half of
`read_done_fov_keys_from_zip`/`_from_folder`, applied to `in/<WELL>.zip`/
`<WELL>/`'s members instead of the output archive those functions were written
for; nothing about them cares which side of the pipeline a container sits on,
they only parse `(fov, timepoint)` positionally off member names. Validate calls
the name-based half because it has already listed the container (see "one read
per artefact" below); the two path-based readers call it after listing, so all
three agree by construction. *Done* is read the same way `incremental_done_fov_keys` composes it
(CSV rows ∪ out-zip members ∪ output folder — `_well_done` unions all three,
the last via `_common.dataset_paths.out_folders`, the `out_zips`-shaped
well→path map for `compressed_output=False` datasets; a folder-mode well
that legitimately segmented zero cells at a timepoint still writes an image
there even with no CSV row for it, the same "done" evidence a zip-mode
well's out-zip member supplies) but on the real, already-resolved CSV/zip
paths rather than by reconstructing `f"{csv_prefix}_{safe_well}.csv"`
— `csv_prefix` is a CLI arg never recorded in the sidecar, and `_safe_well`
only strips path separators, so an unpadded on-disk CSV would reconstruct to a
nonexistent filename and silently report "0 FOVs done". `out/*.csv` is globbed
directly rather than through `out_csvs()` (which plainly overwrites on a
duplicate well key) so a dataset holding CSVs from two different
`--csv_prefix` runs is flagged rather than silently resolved to whichever file
happened to glob last. A CSV that matches a well by filename but whose header
lacks `timepoint_hours` is flagged as a phantom (an unrelated file, not
broken data) rather than counted as that well's state.

A schema with no `fov` field (a single-FOV pipeline — `PipelineSchema.has_fov`
false, a normal and common configuration, not an edge case) can't diff on
`(fov, timepoint)` pairs directly: the intent-side reader can only ever
extract `fov=""` for such a schema (there's no positional slot for it in the
filename), while a written CSV row's own `fov` column carries the separate
`normalize_fov` single-FOV convention (`""` → `"1"`). `_scan_one_well` diffs
on timepoint alone in that case instead of skipping the diff outright — `fov`
carries no real information for a schema that doesn't declare it, so
comparing intent's and done's timepoint sets is both correct and is what
lets a single-FOV dataset's per-well completeness still be checked. An
`info`-severity `no_fov_field` finding says so once per report.

A well's *done* keys should always be a subset of its *intent* keys when the
schema actually matches how that well's real filenames are structured —
*done* describes the finished portion of *intent*. If `pipeline_info.json`'s
`schema_fields`/`separator` don't (drifted since the files were written, or
were simply wrong for this dataset), `_well_intent`'s positional parse
silently reads the wrong filename token as `fov`/`timepoint`, and most of
*done*'s own keys stop appearing in *intent* at all — not "this well is
mostly unfinished," but "these two sides aren't even parsing the same
schema." `_scan_one_well` detects that shape directly (a well with at least
4 done keys, at least half of which don't appear in intent) and reports a
single `error`-severity `schema_mismatch_suspected` finding naming the exact
mismatch count and pointing at `schema_fields`/`separator`, rather than
letting the well's diff produce a wall of individually-false `missing_fov_tp`
findings with nothing explaining why a well that's actually done reads as
almost entirely missing.

The per-well diff itself (`_scan_one_well`) runs on a `ThreadPoolExecutor`
(`pick_workers`) — unlike Concatenate/Subset/Merge Plates it's pure reads with
no destination file, so there's no lock to take: the only shared state is the
caller's own progress callback, and that does nothing but hand its result off.
`ThreadPoolExecutor.map()` (not `as_completed`) hands results back in the same
order they were submitted regardless of which well's thread finishes first, so
the report's well/finding ordering is identical to the old sequential loop's no
matter the worker count.

**What that pool does and does not buy.** The per-well work is pure-Python
parsing — `csv.DictReader` building a dict per row, `zipfile`'s central-directory
parse, `fov_tp_from_name` per member — and none of it releases the GIL, so on a
warm local disk the threads interleave rather than overlap and a wider pool is
measurably *slower* than one worker. The pool is kept because the picture
inverts as soon as a container open costs a round trip, which is the normal case
for a dataset on a network mount: with latency in front of each open, four
workers beat one comfortably. It is deliberately not a `ProcessPoolExecutor`
(`_scan_one_well` takes and returns plain data, so it would pickle, but the
spawn cost inside a frozen app is real and the win is only on the half of the
work that is already cheap). The way to make a local-disk scan faster is
therefore to do less work, not to run it on more threads — which is why the
read discipline below is a tested ratchet rather than a nicety.

**One read per artefact.** `_scan_one_well` lists its input container **once**
and parses each of the well's candidate CSVs **once**, then hands those results
to the three consumers that need them: `_well_intent` derives the intent keys
from the member names it is given (via `incremental_keys.fov_keys_from_member_names`,
the name-parsing half that `read_done_fov_keys_from_zip`/`_from_folder` both
delegate to, so skipping the second open cannot drift from what opening again
would have said), `_well_done` reads the CSV rows out of the parsed map, and
`_row_content_findings` takes both rather than re-reading either. `_scan_wells`
returns the member names it collected, and `_check_sidecar_availability` walks
the input folder once with them (`collect_input_image_stems(known_zip_members=…)`)
and extracts both fields from that single set of stems
(`schema_values_from_stems`). The budget is two container opens per well —
`in/<WELL>.zip` and `out/<WELL>_out.zip` — and one CSV parse;
`test_validate_compute.py::test_scan_reads_each_container_once_and_each_csv_once`
fails on any re-read. An archive that is not in the per-well map (a loose TIF, a
non-well-named zip) is still read normally by the availability walk, so its
coverage is unchanged — the thing Apply repairs *writes back* into
`pipeline_info.json` must not narrow because a scan got faster.

`build_report` also takes an optional `ScanProgress` — two plain callables, so
the module stays Qt-free — that reports the scan as it happens rather than only
when it lands. `on_wells` fires once with the full set of wells the scan will
cover, before any of them is read; `on_well_done` fires per well as that well's
diff finishes. The threading contract is the reason it's a documented dataclass
rather than a bare callback argument: `on_wells` runs on the calling thread,
`on_well_done` on whichever pool thread scanned that well, in completion order
(never the report's own order). `ValidateWorker` satisfies that by making both
callbacks bare Qt signal emissions (`wellsDiscovered`, `wellScanned`), which
are delivered as queued calls on the GUI thread. Without this channel a scan
exposed nothing at all between "started" and "finished" — and since the scan
opens every well's input archive and reads every measurements CSV, that gap is
the whole duration of a plate-sized run, during which the page had nothing to
show. `well_health(WellReport)` classifies a well into the four states the
plate map paints (`complete` / `partial` / `missing` / `no_output`) and is the
single classifier behind both the live per-well paint and the finished
report's, so a well cannot change colour depending on which of the two got
there first.

Beyond the per-well diff, `build_report` also checks: the sidecar's
`available_timepoints`/`available_fovs` against what
`_common.collect_available_schema_values` finds on disk (both directions —
stale entries and missing ones); `timepoint` vs `timepoint_hours` agreement
and `filename`-in-archive presence per row; zero-member or unreadable zips;
and `cell_overrides.json` entries whose `seg_fp` no longer matches the
current out-zip mtime (silently dropped on load regardless — see
`well_viewer/persistence/cell_overrides.py`).

`apply.py::apply_repairs` is metadata-only — it never rewrites a measurement
CSV or a well archive. It read-modify-writes the already-loaded sidecar dict
(replacing only `available_timepoints`/`available_fovs` with what's actually
on disk), prunes stale `cell_overrides.json` entries, and sweeps leftover
`__aw_tmp_*` staging files. `services.remote_runner.refresh_pipeline_info_available_from_outputs`
is deliberately not reused here — it only ever *unions* into the existing
availability lists, so a stale entry can never be dropped, which is the
opposite of what a repair needs to do. Resolves `schema_fields` through
`load_schema(info)` — the same call `build_report` makes for its own
stale/missing diff — rather than a bare `info.get("schema_fields") or []`: a
sidecar with no `schema_fields` key at all (only the legacy `schema`
colon-string, or `fov_index`/`tp_index`, or nothing — every one of them a
fallback `load_schema` itself handles) resolved to `[]` under the bare form,
and `collect_available_schema_values([], ...)` returns `[]` unconditionally
for a schema that doesn't have `field_name` — silently wiping a real, correct
`available_timepoints`/`available_fovs` list to empty on a dataset
`build_report` had just diffed correctly.

The page's **Complete this run** button hands the dataset off to Analyze
through the existing `on_open_in_analyze` seam — the same one `Convert
OME-TIFF` uses — so finishing a partial run is an ordinary incremental Analyze
run, not a new execution path. What travels through that seam is what makes the
handoff able to finish anything: the dataset's own recorded
`out/pipeline_info.json` (read best-effort by
`validate_compute.read_dataset_settings`, which returns `(info, problem)` and
never raises), `incremental=True`, and the per-well force list
`validate_compute.wells_needing_force(report)` — every well the report found
with images and no measurements CSV, which is precisely the set an incremental
run would skip forever. Wells that are merely *partial* are deliberately left
off it: incremental does fix those, at a fraction of the cost of re-segmenting
them whole. `ValidatePage.show_report`'s `needs_completion` keys off that list
as well as off missing `(fov, timepoint)` pairs, because a no-CSV well has no
missing pairs at all — its own archive covers them — and a dataset whose only
fault is that kind of well is exactly the case where the button is the only
remedy on offer. The `no_csv` finding says so in words: an incremental run
counts the well's own output images as already done and reports it up to date,
so only a forced reprocess regenerates the CSV.

**The forced reprocess itself.** `process_microscopy --force_wells W1 W2 …` is
the per-well counterpart to `--force`: a named well runs as if `--incremental`
were off — no done-set filter, a fresh CSV, a rewritten archive — while every
other well still runs incrementally and is not re-segmented. The three per-well
decisions (does output already exist, is this well forced, does it therefore run
incrementally) live in the stdlib-only `pipeline/well_skip.py`, which is in the
remote upload manifest. Labels match through `canonical_well_label`, since `C8`
and `C08` name the same well and the two sides of that comparison are a GUI
field and a zip stem. The Analyze form carries it as a visible **Force these
wells** field rather than hidden run state: it re-segments and overwrites, so it
must be something the user can see and clear.

**A remote run needs telling twice.** The flag on the remote command line is not
enough on its own, because the client decides what to upload before the remote
sees anything. `remote_runner._plan_staging` computes its own done-set — the
local CSV's rows unioned with the local `<WELL>_out.zip`'s members — and drops a
well whose input it considers covered, logging `✓ <well>: up to date (local) —
not uploaded`. For the well this whole path exists to recover (images present,
CSV missing) that check fires, the archive is never uploaded, and the flag
downstream has nothing to act on. `_plan_staging` therefore takes the same force
list, canonicalised through `pipeline.well_skip._canonical_force_wells`, and
stages a forced well in full before the incremental diff is consulted at all;
`services.pipeline_runner` passes it from `opts["force_wells"]`, the same key
`build_pipeline_args` reads. Planning also logs what it concluded (`planned N
well archive(s) to upload, M already up to date`) — it emits a line only for a
well it drops or rewrites, so a plate whose remaining wells all stage unchanged
went silent right after the two or three it did report, which reads as a stall
with most of the plate still to go.

Staging is not the last place the intent has to reach. Three further decisions
downstream of it can each discard a forced run's work on their own — the
pre-flight cache-hit check that decides whether to launch the pipeline at all,
the Fetcher's decision to skip a well whose local copy looks complete, and
verify's judgement of whether the output arrived — so `_RemoteRun` carries
`force` / `force_wells` and all three consult it. §6.4 has the mechanics.

**What the end-of-run sweep brings back.** Two things beyond the per-well
outputs, both because a remote run's records are useless if they stay on the
remote. `out/errors/` — the per-failure tracebacks — is pulled, so Validate's
`_scan_error_files` has something to read; without it that whole half of the
"read the run's own records first" rule was local-run-only, and a remote user
got the hedged verdict every time. And the fetched `pipeline_info.json`
**overwrites** the local one, while a remote output dir starts empty — so the
`run_report` block it carries describes only the wells that run processed (on an
incremental run, only the uploaded split). Left alone that replaces a whole
plate's completeness records with a handful, and every skipped well loses the
record saying whether its empty output was a measurement or a gap — which is
precisely what Validate reads to avoid forcing a well that legitimately
segments zero cells, so the loss shows up as a well being re-segmented to the
same empty result on every run. `run_report.read_run_report_block` captures the
block before the rsync and `merge_fetched_run_report` folds it back after:
fetched records win per well, prior records survive for wells the fetched block
does not mention, and the derived fields (`incomplete_wells`, the totals) are
recomputed rather than merged so they cannot disagree with the `wells` map they
summarise. Both sit beside `refresh_pipeline_info_available_from_outputs`, which
already read-modify-writes the same file for the same reason.

**The run estimate reports bytes, not a verdict.** `RunEstimate` used to end in
"remote wins" / "LOCAL would be faster", computed from a hardcoded
`max(60s, 5s per well)` local-compute figure and a flat assumed 4× remote
speedup. Neither measured anything: on a real plate it guessed 60s against a run
that took eight hours, and the verdict recommended running locally — printed
beside a genuinely measured transfer size, which is precisely what made a
constant read as a measurement. It could not have been right in principle
either, because the comparison assumes local compute pays no I/O and a
NAS-resident dataset pays network reads for every image whether or not a remote
is involved, plus scratch on top. The verdict is gone rather than tuned: the
type now carries only `bytes_to_transfer` and `transfer_s`, both of which the
staged archives and the host's measured bandwidth actually support. Which side
is faster is a question it does not answer, and a better-guessed answer would
have been the same failure in a smarter disguise.

**A phase that logs is invisible unless it logs to the stream.** The GUI drives
its stages by parsing the run's line stream, so the post-fetch auto-threshold
pass — which runs *in-process* (`_run_local_auto_threshold`) rather than as a
subprocess, and reports itself through `logging` — had its records going
somewhere nothing was reading. The only things on the stream were that method's
own two bookends, one line as it started and one when it finished, so a full
plate's auto-threshold sat silent at the end of every remote run with the
Threshold stage unmoved. `_LogToLineStream` forwards the records onto the same
stream, unformatted: the consumers (`_RE_TF_SCAN`, `_RE_TF_PROG`,
`_RE_THRESH_WELL`) match the pipeline's own message text, so a prefix or
timestamp added here would break them the same way a never-passed progress flag
does.

**A transfer is painted in the colour it is heading toward.** The dial fills
from a light tint of its colour to the full one, so a download painted in the
"on server" blue the well is *leaving* filled blue-into-blue — light meaning
"not yet", on a plate where light is exactly how an unstarted dial looks — and
left no moment at which the well simply *was* blue (dark blue, then pale blue,
then green). `RunDashboard._well_color` now returns the destination state's
colour while a transfer is in flight: `success` for a download, `accent2` for an
upload, so a well holds blue while it waits on the server and fills green as it
lands. The colour change *is* the arrival. A well that has moved on (already
computing, already fetched) keeps its own colour, so a stale transfer marker
cannot repaint it backwards.

**`set_well_transfer` pushes the colour as well as the fraction**, and that is
the whole of what makes the paragraph above true on screen rather than only in
the model. `set_well_stage` writes both to the plate; a transfer that wrote only
the fraction left the destination colour computed and discarded on every tick,
so a downloading well kept the blue of its last stage change and filled
blue-into-blue — the exact rendering the destination-colour rule exists to
prevent. The regression guards for it assert on what the plate was *told*
(`setWellColor` calls), not on what `_well_color` returns: the computed value was
green throughout while the widget painted blue, so asserting the computed value
is what hid it.

**A well's output arrives as two transfers, and the dial spans both.** The
`{well}_out.zip` and the well's CSVs are separate rsyncs, and the well is not
fetched until both have landed — so `Fetcher._fetch_one_well` **lists the CSVs
before it pulls the zip**, purely to learn their byte total, and both halves
then measure against one combined denominator. The listing is the same ssh the
pull needs anyway, moved earlier, so this costs no extra round trip.
`_list_remote_glob` returns `(host, paths, total bytes)` — the paths from
`ls -1` exactly as the pull has always parsed them, the total from an `ls -ln |
awk` sum in the *same* script, kept separate because those columns vary between
GNU and BSD and break on a filename with a space. `_transfer_heartbeat` takes a
`bytes_so_far` counter (the CSVs land as several files, not one named path) and
a `base_bytes` offset carrying everything the zip already contributed, so the
gauge continues rather than restarting near zero — a dial that dropped back
partway through a fetch would read as a retry.

Either size being unknown drops the combined total to the zip's own, or to
`None`, and the dial simply stops or never runs: an unknown denominator must not
become an invented one. That is when the widget's **freeze** rule carries the
rendering. A download reaching 1.0 does not end the transfer the way an upload
does; the ring holds at the last measured fraction — green, visibly short of
full — until `set_well_stage` marks the well `download`. Both alternatives are
indistinguishable from some other state: dropping the entry returns the well to
a full ring in the computed blue (a well that has not started downloading), and
storing the 1.0 fills the ring green (a well already fetched). A closing 1.0
with no prior tick creates nothing, since a well whose size probe failed never
had a dial at all.

**Rail denominators are per stage.** A remote run's stages do not count the
same population: every well on the plate is grouped locally, while only the
subset the incremental split sends is uploaded, processed and thresholded, and
Download ends up covering the whole plate again (a well that was already current
locally is, by the end, fetched locally). `RunDashboard` carried one shared
`_total`, so whichever population announced last became every row's divisor —
which is how the rail came to read `Group 40/6` and `Download 34/6`, numerators
counting the plate against a denominator counting the run, with Download ahead
of an Upload that had not started. `set_stage_total(stage, total)` overrides one
row's divisor; `services.pipeline_runner` announces the grouped population for
`group` and `download` (a `stage_total` event, rendered by
`analyze_progress._apply_grouping_event`) and leaves the stages between them on
the run's own well count.

**rsync has to be told to narrate.** Everything the Upload rail and the plate's
per-well transfer state are built from is parsed out of rsync's own stdout:
`_looks_like_rsync_filename` turns each per-file line into `[remote] ↑ <well>.zip`
(which drives the rail and the "uploaded" colour), and the percentage stream
drives the transfer bar and `note_bytes`' per-well pie slices.
`--info=name1,stats2,progress2` needs rsync ≥ 3.1.0, and macOS ships 2.6.9 — the
machine most runs are launched from — so on the common case the gate fell
through to no progress flags at all. rsync then ran silently for the whole
transfer and every consumer went dark with it: `Upload 0/N` and an unchanged
plate from the first second to the last, indistinguishable from a hang, for as
long as the upload took. `_rsync_progress_flags()` is the single decision, used
by both upload sites: `--info=…` where it exists, `-v --progress` (both older
than 3.0) where it does not. The existing parsers already accept both shapes.
On legacy rsync the percentage is per file rather than whole-transfer, so the
bar sweeps once per archive; the per-well attribution is unaffected.

**The fetch skips what this run did not produce.** A well the remote skipped —
up to date, or never uploaded by the split — emits no `Well X — done.` marker,
so it is absent from `expected_wells` and this run produced nothing new for it.
`Fetcher._already_local` short-circuits such a well when its local artefacts are
already complete (a readable `<WELL>_out.zip` under the FULL filter, plus a
`*_<WELL>.csv`): there is nothing on the remote a transfer could improve, and
pulling it anyway costs an ssh size probe and two retry-wrapped
`rsync --timeout=120` invocations — the shape behind a fetch that sits on one
well for minutes while the file it is pulling is already on disk and untouched.
The skip is deliberately narrow, and there are two ways out of it: a well this
run *did* process is always re-fetched however complete the local copy looks,
because that local artefact is exactly what the new output replaces; and so is a
well the run was **told to reprocess**. "It already exists locally" is the one
reason that cannot justify skipping a forced well, since that file is the thing
being replaced — and a forced well missing from `expected_wells` means its
completion marker was never seen, which is a reason to look harder rather than
to skip. Each fetch logs which of the three reasons applies (`this run produced
new output`, `forced reprocess — the local copy is being replaced`, or `not
complete locally`), since a slow fetch is only diagnosable if they are
distinguishable.

**Force is carried to the decisions that act on it.** `_RemoteRun` holds
`force` / `force_wells`, read off the pipeline argv by `_forced_from_argv` at
both construction sites (the single-shot and batched paths). Before that the
force intent reached the staging planner and stopped there, which left the two
decisions that actually determine whether a local artefact gets replaced — the
Fetcher's skip and verify — structurally unable to know a replacement had been
demanded.

**The pre-flight cache hit has a force term.** `_compute_run_id` deliberately
excludes `--force` from its hash so a forced re-run lands in the *same* remote
output dir and overwrites it. That makes the dir complete precisely when a
forced run starts, so counting `*_out.zip` files there is not on its own a
reason to skip the pipeline: `_pipeline_launch_is_pointless` answers yes only
when the remote is complete **and** nothing was asked to be reprocessed. When
the remote is complete but the run is forced, the log says so explicitly — the
cache-hit line is what a user expects there, and its silent absence is otherwise
unexplained.

**Verify proves replacement, not existence.** `_snapshot_local_artifacts`
fingerprints the output dir (`name -> (size, mtime_ns)`) at run construction
and parks it on `_RemoteRun.pre_run_artifacts`. `_verify_local_manifest` still
requires the CSV and (under the FULL filter) a non-empty `<WELL>_out.zip`, and
additionally requires that the zip **differ from the pre-run fingerprint** for
every well the run undertook to rewrite — all of them when `force` is set,
otherwise `force_wells | expected_wells`. A well whose artefact carries the same
size and mtime it had before the run is reported by name as *not replaced*,
which fails verify and so leaves the remote state in place. An empty snapshot
means every artefact is new rather than none changed, so a run over a clean
output dir is unaffected. `_retry_missing_wells` treats such a well as the one
exception to its "a present zip needs no re-pull" cost guard and re-pulls it,
turning the report into a repair.

**Every well is classified in a file beside the outputs.**
`RemoteRunner._reconcile_fetch` runs before verify and writes
`<output_dir>/remote_fetch_report.txt`: the run id, the force intent, and one
line per well the remote holds output for, marked `fetched`,
`skipped` (with the Fetcher's reason, recorded on `_RemoteRun.skipped_wells`),
or `never-announced`. "Why was this well not fetched?" was previously answerable
only from stdout as it streamed, which is no use once the log drawer is closed;
a diagnostic that only exists live is not a diagnostic. A failure to write the
report is logged and never fails the run.

`_filter_new_fovs_in_staging` is an **older, production-dead** split kept only by
`tests/test_remote_incremental.py`. A change to the incremental split has to land
in `_plan_staging` to reach a real run; fixing that one alone looks right and
does nothing.

The sidecar and the incremental flag both matter, and each was missing on its
own account. Without the sidecar the Analyze form keeps whatever was already in
it, so the schema, separator, channel roles, QC options and — the one that
silently breaks the done-set lookup — the `csv_prefix` all have to be re-entered
by hand and matched exactly; `incremental_done_fov_keys` reconstructs
`f"{csv_prefix}_{safe_well}.csv"`, so a mismatched prefix reads the done-set
from a file that doesn't exist and reprocesses the whole plate. Without
`incremental` the run is a plain re-run, which *skips* any well whose
`<well>_out.zip` and CSV both exist however few of its FOV-groups actually
landed — precisely the state this page exists to report, so pressing Run did
nothing for the very wells the findings list had just named. When the sidecar
can't be read at all, the handoff still happens (refusing would strand the user)
and `_on_complete_run` writes a warning to the page's own log saying the form
was *not* pre-filled, rather than opening Analyze in a silently unconfigured
state.

`ValidatePage.set_running` locks the dataset `PathField` (`setLocked`,
disabling only Browse — see `PathField` in §7) for the run's
duration, the same as Subset's source/destination fields — `_on_succeeded`
records the finished report's `report.root` (the path the worker actually
scanned) rather than re-reading the page's live field, but the field itself
must still not drift out from under the user mid-scan. `ValidateController._run`
re-seeds the dashboard from `preview_wells` immediately after resetting it, on
the GUI thread and before the worker starts — `PlateProgress.reset()` clears the
plate's enabled wells, so a run that began by resetting blanked the very plate
map `_update_status_preview` had just painted and left it empty for the scan's
whole duration; the worker's `wellsDiscovered` then replaces the seed with the
wells the scan actually covers, and `wellScanned` paints each one as it lands.
`_on_succeeded` still repaints every well from the finished report afterwards:
the report is the authority, and a plate disagreeing with the findings list
below it would be exactly the silent substitution this feature exists to catch.
The controller also logs the run's summary for every run, not only a repair —
a plain Check wrote one line ("Scanning dataset…") and never recorded that it
had finished or what it concluded, because the completion line was gated on a
`RepairResult` a Check never produces. `ValidateController._run`'s
"no dataset path" early return also emits `jobFinished(False, …)` directly
(mirroring `MergePlatesController._launch`'s remap-failure branch) instead of
silently returning: a queued job whose persisted `spec["dataset"]` is empty or
missing (a hand-edited `job_queue.json`, per §Job Queue's own repair
discipline) would otherwise never start a worker and never signal completion,
stalling `QueueBridge._current` forever.

The dataset-folder field carries its own validator (`view.py::_is_dataset`,
mirroring Concatenate's) — Validate used to be the one dataset-folder
`PathField` in the whole pane with no validator at all, leaving "▶ Check"
silently disabled with no inline reason when the folder was missing `in`/
`out`. `ValidatePage._update_status_preview` reveals `ValidateDashboard` as
soon as the field is valid, seeded via `preview_wells` with a "Ready — N
well(s)" headline, the same reveal-on-valid-input pattern Concatenate's own
dashboard uses (see above) rather than staying hidden until "▶ Check" is
pressed. `ValidateDashboard.set_well_state` marks a well's terminal states
with a glyph as well as colour — `!` for `partial`/`missing`, `✓` for
`complete` — so a well's state reads without relying on distinguishing
success-green from danger-red by colour alone; `set_done(summary, *,
tooltip=None)` carries the same shortened-headline/full-tooltip split
Concatenate's does. Validate is the one run dashboard whose wells stay solid
discs rather than filling as progress dials, and deliberately so: its worker
carries no `wellFraction` signal because Validate *inspects* rather than
copies, so a well has no sub-steps to report and a fabricated fraction would
be worse than none.

The findings list itself is **hidden until a check produces something to put in
it** — a finding, a failure, or a note. It carries a layout stretch and a panel
border, so an empty one is not a small widget: it drew a page-height bordered
box holding a single line of placeholder text, and that line asked the user to
pick a dataset folder and went on saying so after they had picked one. The run
row above is where that state actually reads (Check enables the moment the
folder is valid, and the dashboard appears beside it seeded with the wells a
check would cover). `_hide_findings` / `_show_findings` are the two sides of it,
and the page carries the trailing `outer.addStretch(1)` every other Datasets
page has, so with both the list and the raw log hidden the dashboard does not
inherit the leftover viewport height as a dead zone. The **Clean — no issues found.** headline is built
directly from whether `report.findings` is empty, not from
`report.is_clean` — `is_clean` ignores warning-severity findings (e.g. a
`no_csv` warning on a well that legitimately segmented zero cells at every
timepoint), so a dataset with exactly that one warning used to show a green
"Clean" headline directly above the very well the plate painted red and the
findings list explained; "Clean" now means what the findings list itself
shows, zero findings, full stop. The findings list shows a centred,
non-selectable placeholder ("Pick a dataset folder and press Check.") while
empty instead of rendering as a large blank box with no explanation. A failed
check goes through `ValidatePage.show_failure`, which puts the reason *where the
findings were* and hides **Complete this run in Analyze**. Both halves matter and
neither was true before: resetting to the "Pick a dataset folder" placeholder
says nothing happened, directly under a red "Failed" headline, and the
completion button — set visible by the last `show_report` and touched by nothing
else — stayed live next to it, still offering to complete a run against a
dataset whose sidecar had just proved unreadable. Taking that offer then handed
Analyze a root with no settings to send, so the user landed on an Analyze tab
that looked untouched. A completion offer belongs to the report that produced
it. `ValidatePage.show_note` appends a line to the findings list without
disturbing it, for something the user must see that isn't a scan finding — the
handoff reporting unreadable settings writes there as well as to the log, since
the log sits behind a checkbox that is off by default.
`ValidatePage.set_dataset_path` runs `_on_dataset_changed` itself after
setting the field: `PathField.set_path` deliberately emits `changed` only for
an interactive Browse pick, so a page populated through the setter —
`QueueBridge.show_job` is its only caller — otherwise never ran the handler
that enables "▶ Check", clears the previous job's findings, and paints the
plate. Selecting a queued Validate job filled in a valid dataset folder and
left the page inert: a disabled Check button that did nothing at all when
pressed, above a blank plate. **Apply repairs** carries a tooltip explaining why it's
greyed out when `report.findings` is non-empty but `report.repairable` is
false — previously a disabled button with findings still listed above it had
no way to say whether that meant "nothing to repair" or "can't repair this".
The "+ Queue" button sits second in the run row, immediately after "▶
Check" and before "Apply repairs", matching every other Datasets tool's
run-row ordering (primary action, then "+ Queue", then any tool-specific
secondary actions).

**Subset / Split Dataset** (`datasets/subset/`) copies a filtered slice of a
dataset — chosen wells, timepoints, and/or FOVs — into a fresh destination,
leaving the source untouched. It deliberately **keeps every kept row's
original timepoint hours** (no re-basing): a subset of 24h–72h stays
labelled 24h–72h. That single decision is what keeps the feature small — the
four coupled timepoint-derived values §6b documents for Concatenate (the
`timepoint` label, the numeric `timepoint_hours` column, the `filename`
column, and the zip member's own name) never diverge here because nothing
about a kept row or member is rewritten at all; filtering is a pure predicate
(`subset_compute.member_matches` / `row_matches`) over what's already there.
`SubsetFilter.fovs` are the raw `available_fovs` strings the page's FOV
checkboxes carry; `fov_tokens` (a derived property, mirroring `timepoint_hours`'s
relationship to `timepoint_labels`) resolves them through
`normalize_fov` once, and every comparison site (`member_matches`,
`row_matches`, `build_subset_plan`'s `merged_info["available_fovs"]` rebuild)
compares against `fov_tokens`, not `fovs` directly — a row's/member's own FOV
is always read through `normalize_fov` too (`ParsedName.fov`, the CSV `fov`
column), so comparing either against the *raw* label set silently matched
nothing for any dataset with purely-numeric FOV tokens (`"001"` normalizes to
`"1"`, which was never a member of `{"001"}`): every row and member for the
selected FOV was dropped, `available_fovs` in the merged sidecar went to
`[]`, and the run died at `_verify` with a "wrote zero measurement rows"
message that pointed at the wrong cause. Non-numeric tokens (`"F001"`) pass
`normalize_fov` unchanged, which is why a dataset built with `"F001"`-style
FOVs never surfaced this.

`build_subset_plan` reuses the destination-state predicates with its own
marker (`.aw_subset_in_progress`), refuses a filter matching zero wells, and
refuses a folder-mode source (`out/<WELL>/`, `compressed_output=False`) the
same way Concatenate and Merge Plates do — nothing here copies member-by-member
out of a folder, so a folder-mode source's QC images would otherwise be
silently absent from the destination while `execute_plan` still reported
success. `apply.execute_plan` follows Concatenate's skeleton — marker written
first, per-well copy through a `ThreadPoolExecutor` (`pick_workers`, same
lock-only-around-the-pre-work-callback shape Concatenate uses), `_verify`
(currently: at least one measurement row written, and every kept row's
`filename` present in the filtered `in/` archive), sidecar write, marker
cleared last.

**A subset copies bytes; it never rebuilds them.** Each of a well's two
container passes is a single `_common.containers.copy_container_members` call
whose `rename` callback returns a member's own name when `member_matches`
passes and `None` otherwise — Subset renames nothing, which is what lets an
unfiltered archive take that helper's whole-file-copy tier and a filtered one
take its raw-passthrough tier. This matters most in the commonest use of the
feature: a wells-only filter drops no member of any archive it keeps, so the
run is `shutil.copy2` per well and costs what copying the zips by hand in
Finder costs. A timepoint or FOV filter costs roughly in proportion to what
it keeps, because a member the filter drops is never read. Neither ever holds
a whole uncompressed archive in memory, which with eight worker threads and
the 2048x2048 frames the pipeline produces is the difference between a
bounded footprint and several gigabytes a thread.

`subset_compute.member_matches` resolves each zip member through
`_common.member_names.discover_parsed_member` — trying each of the source
dataset's known channel tokens (`SubsetPlan.channels`, built the same way
`merge_plates_compute.known_channel_tokens` is, from `fluor_tokens`/
`smfish_tokens`/`phase_token`) as the role-suffix discriminator — rather than
a bare `parse_image_filename`. A channel-scoped role suffix like `_tophat_gfp`
doesn't resolve its role without that discovery loop; without it, a
filtered-out timepoint's `_tophat_<chan>`/`_smfish_<chan>` image fails the
field-count check and falls through the "unparseable ⇒ keep" fallback below,
leaking into the destination.

Cell overrides need one thing Concatenate's carry-over doesn't: **filtering
before restamping**. `_merge_cell_overrides` there carries every entry
unconditionally because nothing is dropped; Subset drops wells, timepoints,
and FOVs, so an override for a filtered-out cell is dropped too — copying
Concatenate's restamp verbatim would otherwise stat a well's destination zip
for an override that doesn't belong there. The restamp itself (`seg_fp` set
to the destination `<well>_out.zip`'s own mtime, as the **last** write to
that archive) is unchanged from Concatenate's pattern. The membership-test
keys `_copy_one_well` builds from each kept row's raw CSV `fov`/`timepoint`
values are normalized (`well_viewer.data.image_identity.normalize_fov`/
`normalize_tp`) before the comparison in `_copy_filtered_overrides` — a
`cell_overrides.json` entry's `fov`/`tp` are already normalized on write
(`CellKey.as_tuple()`, per `well_viewer/persistence/cell_overrides.py`), so
comparing them against *raw* CSV values (`"24h"` vs. the stored `"24"`)
silently failed the match for any override whose timepoint label parses,
dropping every one of them with no log line about the loss.

Subset's page is the one place a Datasets tool already has an interactive
plate map before a run even starts — the well picker above the Run button.
Rather than drawing a second, separate status plate below it the way every
other dashboard does, `SubsetController` paints per-well progress directly
onto that same `WellPlateSelector` (`page._plate.setWellColor(...)`, the
`pending`/`copying`/`done`/`empty` states `SubsetWorker.wellStage` already
reports, each terminal state also carrying a marker glyph — `✓` for `done`,
`!` for `empty` — so a stage reads without relying on distinguishing
success-green from accent-lilac by colour alone), locking it
(`setEnabled(False)`) for the run's duration so the overlay can't be disturbed
by further clicks, then unlocking it once finished.

**Progress within a stage goes to that same picker.** `SubsetWorker` carries
the `wellFraction(well, done, total)` sub-progress signal every Datasets
worker has, fed by `apply.py`'s two container passes fused into one 0 → 1
sweep by `_common.containers.WellFraction`. The sibling controllers hand that
signal to their dashboard's `set_well_progress`; here that call would land on
a dashboard with no plate behind it and silently do nothing, so
`SubsetController._on_well_fraction` drives `page._plate.setWellFraction`
instead, through the shared `plate_progress.fraction_or_none`. A copying well
therefore fills as a dial like every other plate map in the app, and carries
no `⚙`: the glyph would sit in the middle of exactly the area the dial fills.
The two terminal stages settle the dial explicitly rather than trusting the
last tick to have arrived — `done` pins it full, `empty` drops it, since a
well nothing matched has no copy progress to describe and a part-filled dial
beside its `!` would suggest it is still going. Dials are cleared wherever
colours are (a fresh run, a source change, and a selection change on the
now-interactive plate after a run), so no run inherits the previous one's fill.

`SubsetDashboard` itself is built with
`PlateProgress(show_plate=False, show_legend=False)` — see that widget's own
entry below — so it keeps only the headline, overall bar, and per-well-state
counts rail; the separate legend column was dropped (`_build_legend` no
longer overridden) because the rail's own rows already pair a coloured dot
with a state label, so a second column repeating the same four dot+label
pairs added nothing a glance at the rail didn't already say.
`datasets/subset/dashboard.py` exports `well_color`/`well_tooltip_label`
so the controller's plate overlay and the dashboard's rail can't disagree on
what a stage looks like. Source-dataset changes clear the overlay
(`SubsetPage.sourceChanged` → the controller's `_on_source_changed`) so a
stale colour from a previous run's wells can never survive onto a
newly-picked dataset that happens to reuse the same tokens — but that alone
only handles a source change *between* runs. `SubsetPage.set_running` also
locks `_src_field`/`_dest_field` (`setLocked`, not the plate's own
`setEnabled(False)` — disabling only each field's Browse button rather than
dimming the already-read-only path text) for the run's duration: picking a
*different* source mid-run does not stop the still-running `SubsetWorker`,
so without the lock, `sourceChanged` would repaint the plate for the
newly-picked dataset while the old run keeps executing in the background —
its still-arriving `wellStage` signals would then paint the *previous*
dataset's progress onto wells the user now believes belong to the new one.

The picker plate's own "selected" wells get a dedicated `theme.Colors.
accent_dim` tint (`SubsetPage._on_plate_selection_changed`) rather than
`WellPlateSelector`'s default multi-select rendering, which cycles a
rank-based rainbow colour per selection order — indistinguishable at a
glance from the same widget's own post-run state colours, one of which
(`empty`) is `theme.Colors.accent`, the exact hue an unselected-but-enabled
well already renders in a faint default tint. `accent_dim` reads as "chosen,
not yet run" without colliding with either. The picker plate and every
dashboard's own status plate render wells at the same pixel cell size: the
picker's height cap is 258px (not the dashboards' 210px) because it also
carries a visible All/Invert/Clear action row the dashboards' own plates hide
(`setActionsVisible(False)`) — 258 was matched empirically (offscreen
geometry measurement of each plate's actual rendered cell size) against
210's grid scale, so the two plates read as the same control rather than the
picker's wells rendering visibly smaller for no visible reason. The
destination field carries the same folder-shape validator every other
dataset-folder/destination field in the pane already had
(`view.py::_is_destination`, mirroring Concatenate's) — Subset used to be
the one destination field with none, leaving "▶ Subset" silently disabled
against an already-finished destination with no inline reason.
`SubsetPage._update_status_preview` reveals `SubsetDashboard` as soon as the
inputs are otherwise ready, seeded with the picker's *current selection*
(not `preview_wells` — Subset already has the wells in front of it) and a
"Ready — N well(s) selected" headline, rather than staying hidden until "▶
Subset" is pressed; a heading above the Timepoints/FOVs checkbox rows reads
"Pick a source dataset to preview timepoints/FOVs." until a source is
chosen, matching the pattern Import Loose's own readout already used, so an
empty checkbox row doesn't read as broken before there's a source to derive
it from.

**Zip Loose Folder** (`datasets/import_loose/`) groups a folder of loose,
ungrouped TIFs into per-well `in/<WELL>.zip` archives with a live preview —
wells × FOVs × timepoints × channels, plus unparsed and unreadable-symlink
counts — that updates on every schema/separator keystroke, so a mismatch is
caught before conversion rather than mid-run. One `scan_folder` per change feeds
both the readout and the plate preview: they ask the same question of the same
folder with the same arguments, and walking a plate twice per keystroke is a
stall on the GUI thread. The schema is described with the shared `SchemaPicker`
(see the widgets table), the same control the Analyze form's Filename Schema
section mounts: this page's whole output is a dataset Analyze reads next, so the
two ask about the same filenames with the same vocabulary.

`_can_run` refuses three things, each with its reason on screen, because a
button that goes dark with no explanation is indistinguishable from a broken
one. A schema `util.schema.validate_schema` rejects — the in-place route shells
out to `WellPlateZipper.py`, which exits 2 on exactly those errors, and a schema
with no `channel` yields archives Analyze cannot read. A scan that found no
wells, which the page already knows from the preview it holds; offering that run
spends a click to report what the readout above the button says. And a
destination equal to the source, a cross-field rule the shared validator cannot
see, which would leave the folder half loose images and half dataset. The
destination itself goes through `_common/dataset_fields.is_free_destination`,
the same rule and the same wording as Concatenate, Subset and Merge Plates.

`ImportPreview.summary` distinguishes the three ways a scan comes back empty:
no images in the folder, images only in subfolders (the scan is deliberately
non-recursive, matching `WellPlateZipper`), and images that the schema does not
describe. One sentence covered all three, and two of them are not about the
schema — a per-condition subfolder layout is the ordinary shape of a loose
export, so the page sent those users to rebuild a schema that was never wrong.
An in-place run that succeeds calls `mark_in_place_done`, which stops the page
re-scanning a folder whose loose images are now archives (it would report that
nothing matches, directly above that run's own success headline) and disarms
Run, which otherwise stayed live and traded the result for a failure.

It does **not** modify
`WellPlateZipper.py` or `services.input_resolution_service.resolve_input_output`
— Analyze's own loose-TIF handling keeps working exactly as it does today.
Conversion progress is a per-well plate map
(`dashboard.py::ImportLooseDashboard`, a `PlateProgress` subclass — see the
`PlateProgress` row above) in the same Analyze-style graphical shape
`ConcatDashboard`/`MergePlatesDashboard`/`ValidateDashboard` use, rather than
a plain progress bar: `ImportLooseWorker.wellsPlanned` seeds the plate with
the wells `scan_folder` already found for the preview, and `wellStage`
colours each one `pending` → `writing` → `done` as its zip actually lands on
disk, marking `done` with a terminal `✓` (accessibility — colour alone doesn't
distinguish success-green from `pending`'s text-muted grey). A `writing` well
carries no glyph — it fills as a progress dial, and a glyph would sit in the
middle of exactly the area the dial fills. The rail's own "Done" count row reads
"Zipped" instead, matching the vocabulary the dashboard's own well-state
marker and every other reference to this state on the page already use — it
used to be the one place on this page still saying "Done" for the same
state. `ImportLoosePage._update_status_preview` reveals the dashboard as
soon as the source folder's live scan finds at least one well, seeded with
those wells and a "Ready — N well(s) to zip." headline, rather than staying hidden until "▶ Zip folder" is pressed; a scan
that finds nothing yet leaves the dashboard hidden rather than showing an
empty "Ready — 0 well(s)" plate, which would read as "broken" rather than
"not ready yet".

The preview (`import_loose_compute.scan_folder`) mirrors
`WellPlateZipper._extract_well_from_filename`'s rule exactly — schema-index
lookup, `canonical_well_label`, and `os.path.isfile` to drop dangling
symlinks the way `os.walk` lists them — rather than calling
`WellPlateZipper.group_by_well` directly, which `print()`s unconditionally (a
scan banner, a per-5000-files progress line, a completion line) for the
Analyze log and would otherwise pollute this app's own captured output.
`scan_folder` and the executor's `group_files_by_well` share one `_walk`
helper, so the preview and what actually gets written can't disagree.

Two conversion modes, one code path each, both in `apply.py`:

- **New destination** (default) — writes `<dest>/in/<WELL>.zip` directly via
  `util.zip_write.atomic_zip`, never publishing a member-less archive. The
  source folder is left untouched. Wells are converted in parallel — a
  `ThreadPoolExecutor` sized by `_pick_workers` (0 = auto, capped at 8 and at
  the CPU count, mirroring `pipeline.ome_explode._pick_workers`), since each
  well's zip is an independent file with no shared state. `log`/`progress`
  calls are serialised behind a lock so a caller's own closure never sees two
  wells' callbacks execute at once — the lock protects individual callback
  invocations from corruption, not the overall run from wells' "writing"..
  "done" spans overlapping in time, which is expected under real
  parallelism. `progress` reports `(well, "writing"|"done")` — the shape
  `ImportLooseDashboard.set_well_stage` expects.
- **In-place** (opt-in, destructive — "group in place, delete sources on
  success") — checking the "Group in place" box shows an explicit inline
  "Danger"-styled warning beneath it ("This deletes the original loose TIFs
  once each well is safely zipped. This can't be undone.") for as long as
  it's checked, and disables rather than hides the destination field
  (`_dest_field.setEnabled(not checked)`, not `setVisible`) — the field
  disappearing outright used to be the only signal this destructive mode had
  just been armed, easy to miss since a layout jump is the only thing that
  changes. Behaviourally it shells out through the existing
  `services.input_resolution_service.run_zipper` (which invokes
  `WellPlateZipper.py` as a subprocess) rather than reimplementing the same
  grouping a second time; `run_zipper` produces per-well *folders* only
  (`WellPlateZipper.py` never zips anything itself), so `apply.py`'s own
  `_zip_well_folders` then converts every one of them into `<well>.zip`,
  removing the folder once packed, so the result lands in the same
  ready-to-run layout the new-destination mode writes. This is deliberately
  **not** `services.input_resolution_service._consolidate_mixed_to_zips` —
  that helper only tops up a *mixed* zips+folders state (Analyze's own
  loose-TIF handling, which is happy to read pure per-well folders directly)
  and is a documented no-op when zero zips exist yet, which is exactly the
  state a fresh `run_zipper` call produces; calling it here left every well
  as a bare folder forever, with the run still reporting success.
  `move_source_tifs` is threaded through unconditionally from the
  in-place checkbox (`ImportLooseController._launch`) so the "delete loose
  TIFs on success" promise in the checkbox label and confirm dialog is
  actually honored — `WellPlateZipper.py`'s `folder_well()` copies rather
  than moving originals when this is left false. `run_zipper`'s own
  `progress_fn` only reports a well once WellPlateZipper.py finishes
  grouping its files into a folder — there is no earlier "started" signal
  from the subprocess — so that single event is reported as the dashboard's
  "writing" stage; `_zip_well_folders` then reports "done" once that well's
  zip actually lands on disk, giving both conversion modes the same
  `(well, "writing"|"done")` callback shape off one `ImportLooseWorker.wellStage`
  signal. A well already zipped from an earlier, crash-interrupted attempt
  (`_zip_well_folders`'s idempotent-rerun skip) still reports "done" rather
  than being silently skipped — if `wellsPlanned` (seeded from the pre-run
  scan) already listed it, skipping the report left it stuck showing
  "writing" on the dashboard forever despite the run overall succeeding.

`ImportLooseController._launch` captures the source path once at launch
(`self._launch_source_root`) rather than re-reading `self.page.source_path()`
inside `_on_succeeded` afterward, and `ImportLoosePage.set_running` locks the
source/destination `PathField`s and the schema/separator/in-place controls
for the run's duration — the same field-locking discipline Subset's picker
uses, closing off a source change mid-run from misattributing the produced
root in-place mode reports (what a chained queue successor uses, and what
"Open in Analyze" opens). `collect_spec`/`show_job` round-trip
`filename_schema`/`filename_sep` through the persisted queue spec too — they
used to be silently dropped, so a queued run always converted with the page's
default schema/separator regardless of what the user had actually set when
they queued it.

**Stop is worker-tracked, not just interruption-flagged, for in-place mode.**
`convert_to_new_destination`'s `ThreadPoolExecutor` loop polls
`isInterruptionRequested` cooperatively between wells, but `convert_in_place`
blocks inside `services.input_resolution_service.run_zipper`'s subprocess
stdout-read loop, which never polls anything — `QThread.requestInterruption()`
alone was a no-op there. `ImportLooseWorker.request_stop()` also signals the
tracked `WellPlateZipper.py` subprocess directly (`_track_proc`, wired through
`convert_in_place`'s existing `proc_hook` parameter, via
`services.subprocess_kill.signal_subprocess` — the same cross-platform
pgid-aware kill `PipelineRunner.stop()` uses for its own zipper subprocess).
`ImportLooseController.stop()`/`shutdown()` call `request_stop()`, not
`requestInterruption()` alone. The resulting nonzero-exit `RuntimeError` is
rewritten to the "Cancelled" convention every other Datasets worker's
`failed()` message uses for a Stop-driven exit, so `QueueBridge._was_cancelled`
recognises it instead of marking the queued job `FAILED`.

**New-destination mode refuses a destination that already has any content**
(`apply._check_new_destination`, mirrored by a page-side `PathField`
validator, `datasets/import_loose/view.py::_is_destination`). Unlike
Concatenate/Subset/Merge Plates, this mode has no destination-state check of
its own — it writes `<dest>/in/<WELL>.zip` directly via `atomic_zip`, which
publishes onto that path unconditionally on success. Pointing it at an
*existing analyzed dataset* silently overwrote that well's raw-input archive
with the loose folder's unrelated images while `out/` (its CSVs, QC zips,
`pipeline_info.json`) was left untouched — a dataset whose measurements no
longer correspond to its inputs, with the run still reporting success. The
guard is deliberately simpler than the sibling tools' marker-based
restart-detection (`destination_is_failed_run` / a resume confirmation) —
it refuses *any* pre-existing content under `in`/`out`, full stop, rather
than distinguishing a crash-interrupted attempt of its own from someone
else's dataset; the `.aw_import_in_progress` marker this module's docstring
in `datasets/_common/dataset_paths.py` names is not written by anything, so
there is no restart-resume feature here to preserve.

**Merge Plates** (`datasets/merge_plates/`) is the well-axis sibling of
Concatenate. Concatenate joins the same dataset imaged at different times (a
well present in both datasets is stitched — more timepoints for the same
sample); this joins **two different datasets at the same times** (two
24-well runs becoming one 96-well dataset). The page's own blurb calls both
sources "datasets", not "plates" — the codebase's own convention (§1)
reserves "plate" for the physical 96-well grid and "dataset" for the on-disk
folder — and states the same distinction without ALL-CAPS emphasis. The
semantics are the inverse of Concatenate's on the point that matters most: a
well present in both datasets here is **two different biological samples**,
so it is **refused**, not merged, unless the user remaps dataset 2's wells
onto free positions first (a block-placement control — "place dataset 2
starting at well E01" — translating dataset 2's occupied wells as a rigid
block via `merge_plates_compute.block_shift_well_map`).

`MergePlatesPage.set_running` locks the dataset 1/dataset 2/destination
`PathField`s (`setLocked`, disabling only each field's Browse button, not
the whole field) and the remap controls for the run's duration, and
`MergePlatesController._launch` captures `dataset1_path()`/`dataset2_path()`
once at launch (`self._launch_ds1_path`/`_launch_ds2_path`) for
`_on_wells_planned` to re-derive origin colours from, rather than re-reading
the (now field-locked, but still worth being defensive about) live page —
the same class of mid-run-field-change bug already fixed on Subset's and
Validate's own source fields. On success, `show_open_in_review` promotes
"Open in Review" to `variant="primary"` and demotes "▶ Merge" to
`variant="secondary"` (`widgets._support.set_variant`, see §7), and
`revalidate_fields(ok=True)` swaps the destination field's post-run
"already holds a finished dataset" validator message for a neutral note
(`PathField.show_note`) instead of re-showing what reads as a warning about
what the run just correctly produced — both mirror Concatenate's own
post-run page behaviour.

`MergePlatesPage._update_status_preview` reveals `MergePlatesDashboard` as
soon as both dataset fields are valid, seeded via `preview_wells` on both
roots — with the remap applied through `block_shift_well_map` when the
"Place dataset 2 starting at well" checkbox is on, so the preview shows
where dataset 2's wells will actually land, not their pre-remap positions
(an out-of-range/invalid remap target falls back to the unmapped positions,
since `_remap_message`'s own inline hint already surfaces that error) — with
a "Ready — N well(s)" headline, rather than staying hidden until "▶ Merge"
is pressed. Dataset 2's wells are painted with `theme.Colors.accent2`
instead of `theme.Colors.accent` — a second, distinct hue reserved for "this
belongs to the other of two normal groups", deliberately not `accent`, which
several other dashboards (Concatenate's merged-with-no-CSV, Subset's
no-rows-matched) already use to mean "anomaly"; a well merely belonging to
dataset 2 is not one. Every dataset-2 well also keeps a persistent `"2"`
marker glyph in every state, `merging` included — it reports *origin* rather
than progress, which is why it is the one glyph exempt from the app-wide rule
that a well being worked on wears none while its dial fills — so which dataset
a well came from survives independently of colour. Without it, the only way
to tell the two datasets apart, once both read as green/lilac dots on a small
plate, would be colour alone. A dataset-1 well's own
terminal "merged" state gets a `✓` for the same reason, since the "2" marker
above already covers dataset 2's own not-colour-alone signal.

A well is carried by four values, and all four must move together — the
same lock-step discipline this section documents for Concatenate's
timepoints, with a wider surface because three of these are filenames
rather than file contents: the `well` field inside each zip member's name
(rewritten through `datasets._common.member_names.rewrite_member_field`,
whose predicate is `canonical_well_label(old) == expected_source_well` — a
stricter gate than the timepoint case's `isfinite`, since a mismatch here
means the member is sitting in the wrong well's archive), the `well` column
in every CSV row, the three container names (`in/<WELL>.zip`,
`out/<WELL>_out.zip`, `out/<csv_prefix>_<WELL>.csv` — the prefix derived
from the actual filename, never reconstructed), and the `well` keys inside
`cell_overrides.json` and `pipeline_info.json::sample_definitions`.

`build_plate_merge_plan` reuses `check_compatible` and `known_channel_tokens`
from `concatenate_compute` unchanged, gates on the same destination-state
predicates with its own marker (`.aw_merge_in_progress`), refuses a
well-remap target that isn't a valid 96-well position, refuses a post-remap
collision against dataset 1 (naming the colliding tokens), and **warns**
rather than refuses when the two datasets don't share the same timepoints (a
merged plate is legitimately ragged — one plate imaged longer — but every
plot will show gaps, so it must be said). It also refuses a `well_map` that
maps two *different* dataset-2 wells onto the same destination position —
checked on the raw list of mapped targets before it's deduplicated into a
set, since deduplicating first (`{well_map.get(w, w) for w in wells2}`) would
silently collapse the duplicate and let the collision check downstream see
only one element, never firing. Not reachable through the shipping UI today
(the only `well_map` the GUI ever builds, `block_shift_well_map`'s rigid
translation, is always injective — it can't collapse two distinct wells), but
`build_plate_merge_plan`'s own validation must hold regardless of what a
future caller (a different remap UI, a persisted job-queue spec) passes in:
without it, `execute_plan` would submit both source wells as separate
`ThreadPoolExecutor` futures writing the same destination path concurrently —
silent, non-deterministic last-writer-wins data loss.

**When dataset 2's wells are already disjoint from dataset 1's (the identity
map, the common case), nothing is rewritten at all** — containers are
copied byte-for-byte and CSV rows verbatim, so the fast path is a pure copy.
A well that *is* remapped is barely more expensive: `_merge_input` and
`_merge_qc_zip` publish through `_common.containers.copy_container_members`
with `_remap_well_field` as the rename, so every member's compressed bytes
move across unchanged and only the well token in its header is rewritten.

`apply.execute_plan` merges dataset 1's and dataset 2's wells through **one**
`ThreadPoolExecutor` rather than two sequential rounds — a destination well
only ever comes from one source (`build_plate_merge_plan` already refused a
post-remap collision), so the two sides' per-well work is safe to run
concurrently in the same pool. Sized by `pick_workers`, same
lock-only-around-the-pre-work-callback shape Concatenate and Subset use.

Curation is where this is genuinely harder than Concatenate, which simply
carries dataset 1's forward unchanged: `merge_sample_definitions` remaps
dataset 2's `well_labels`/`rep_sets`/`groups` well references through the
well map and disambiguates dataset-2 rep-set/group names that collide with
dataset 1's by suffixing with dataset 2's folder name, rather than silently
overwriting a same-named definition. `cell_gating` still simply carries
dataset 1's forward, with the same "set under dataset 1's imaging
conditions, worth reviewing" note Concatenate gives. Cell overrides remap
their `well` key through the well map before the `seg_fp` restamp (from the
destination `<well>_out.zip`, as the last write to that archive) —
Concatenate's own precedent, not reinvented.

**Job Queue** (`datasets/job_queue/`) lines up Analyze and Datasets-tool runs
to work through unattended, built on a Qt-free scheduler
(`services/job_queue.py`) so ordering/gating logic is unit-testable with no
Qt event loop. Four layers:

- **`services/job_queue.py`** — `JobSpec` (one persisted queue entry: `id`,
  `kind`, `label`, a JSON-shaped `spec` for that tool, optional chain
  metadata, `state`, `message`, and whichever of `output_dir`/`dataset_root`/
  `in_dir` it produced), `JobState` (plain string constants — `pending`,
  `running`, `succeeded`, `failed`, `cancelled`, `blocked`, `interrupted`,
  `suspended` — not an `Enum`, so the persisted JSON needs no encode/decode
  step), and `JobQueue`, which only orders and gates jobs; it never runs one
  itself.

  Chaining links a job to a predecessor by **id**, never a list index (an
  index would silently re-point on reorder), a dotted path into its own
  `spec` to set, and which of the predecessor's three possible outputs it
  wants (`chain_kind`). Resolution happens at **dequeue**, via
  `JobQueue.materialise(job_id)`, never at enqueue — Analyze's own output
  location is only knowable after `resolve_input_output` runs. A failed or
  cancelled predecessor cascades every transitive successor still `pending`
  to `blocked` rather than `failed`, so those jobs are distinguishable from
  ones that ran and failed; `set_chain()` rejects a self-chain or a cycle
  before writing the link, and a reorder placing a dependent before its
  predecessor is refused.

  The editing half — everything that changes a job after it is queued —
  is `update_spec`, `set_label`, `duplicate`, `retry`, `clear_terminal`,
  `move_to` and `counts`. Two of those carry decisions worth stating.
  `update_spec` deliberately leaves `state` alone: re-arming a finished job
  on edit would strand successors that had already succeeded, whose
  predecessor would no longer read `succeeded`, and that half-state is
  reachable no other way — `retry` is the single explicit backwards move, so
  fixing a failed job and re-running it is two clear steps. `retry` runs
  `_cascade_unblock`, the mirror of `_cascade_block`, returning to `pending`
  only the successors reachable from the retried job; one blocked by a
  different, un-retried failure stays blocked, and since `chain_from` is a
  single id the links form a forest with no second parent to still be
  failing. `duplicate` drops the clone's chain link (a clone inheriting its
  twin's predecessor would wait on output it has no relationship to) and
  takes its new id as a parameter, so the module needs no `uuid` and a test
  can name the copy. `clear_terminal` goes through `remove` per id rather
  than filtering the list, so a survivor chained to a cleared job loses the
  link instead of pointing at a job that no longer exists. `move_to` is the
  drag counterpart of `move`: it shifts the rows it passes rather than
  swapping, because dragging picks a destination, and it validates through
  the same whole-list `_chain_order_valid`. The two are wired to different
  gestures on purpose — the row menu's Move up / Move down go to `move` (a
  neighbour swap), a dragged row to `move_to` via
  `JobQueueController._on_drag_move`, which turns the handle's step count into
  a destination index and *clamps* it, since a drag past either end of the
  list means "put it at the end" rather than "do nothing".

  `JobQueue.from_json` never auto-starts a loaded queue (a relaunch always
  comes up paused — `is_started` is never persisted) and maps a stale
  `running` state to `interrupted`. `interrupted` is in `JobState.RESUMABLE`,
  so the job is picked up again rather than needing re-adding, and its
  successors stay `pending` and wait for it — they are *not* cascaded to
  `blocked`, because the output they are waiting on is still coming.
  `_repair_after_load` also topologically repairs — or, on a genuine cycle,
  drops every chain link in — a hand-edited file whose stored order violates
  its own links, so `next_runnable()` can never deadlock on bad persisted
  data. Its human-readable notes are kept on the instance as
  `JobQueue.repair_notes` and rendered by the page: a repair the user cannot
  see reads as the queue quietly losing their jobs' state.

  Persistence goes through `services.remote_config.default_state_dir()` (a
  tmp-file-then-`os.replace` write of `job_queue.json`), deliberately **not**
  derived from `default_config_path()` — that function honours
  `$ALLWELL_REMOTES`, an override meaning "where my remotes live" rather than
  "where my app state lives", and its Windows branch is conditional on
  whether `remotes.toml` exists, which would silently relocate an
  already-written queue the day a user first creates one. `load_queue`'s
  malformed-file guard wraps `JobQueue.from_json`/`JobSpec.from_json` too,
  not just the `json.loads` call — a well-formed-JSON-but-malformed-*queue*
  file raised `KeyError`/`TypeError`/`AttributeError` uncaught, and
  `load_queue` runs unconditionally and synchronously from
  `QueueBridge.__init__` at `DatasetsTab` construction, so it crashed the
  whole app's startup rather than just the queue page.

- **`services/job_kinds.py`** — the per-kind tables, Qt-free because none of
  it is a view concern and all of it is worth testing without an event loop.
  `KIND_LABEL` (a tool's human name), `CHAIN_TARGET` (the dotted path into a
  job's *own* spec that a predecessor's output fills: `opts.raw` for Analyze,
  `dataset` for Validate, `source` for Subset / OME / Zip-loose, `dataset1`
  for the two-dataset tools), and the sending end of the same link —
  `produced_for(kind, path)` and `CHAIN_KIND`. Together they are why the
  interface can derive a chain from "run after that one" alone; a user should
  not also have to know where each tool receives its input.

  **`CHAIN_KIND` is a constant, `"dataset_root"`, not a per-kind table**, and
  that is the correction it exists to record. Every tool here — Analyze
  included — consumes a *dataset root*, the folder holding the `in/` + `out/`
  pair, and resolves its own way down from there. A per-kind table invites
  handing over the predecessor's most specific output, and Analyze's most
  specific output is its `out/` folder: chained that way, Validate and Subset
  looked for `out/in/`, found no wells, and quietly did nothing.
  `produced_for` therefore records **both** for an Analyze job — `out_dir`
  because it is what Analyze actually built, `dataset_root` because it is what
  a successor needs — deriving the root as the parent, which holds by
  construction since `resolve_input_output` returns `<root>/out` in every one
  of its layout branches. It records nothing at all for an empty path, so a
  `dataset_root` of `""` can never be handed to `materialise` as a real
  answer.

  `describe(kind, spec)` is what a row is actually called. Without it every
  row of a kind reads identically — eight queued subsets all saying "Subset"
  — and picking the right one to reorder, chain or remove is guesswork. It is
  basenames only ("Subset — plateA → plateA_sub"), since a row is an
  identifier and not a path; `describe_detail` carries the full paths for the
  row's second line. Analyze routes through `RunSpec.label()`, which already
  says `(no input)` for a run missing its folder — worth preserving, since a
  row reading plain "Analyze" is indistinguishable from a well-formed job.

- **`datasets/job_queue/controller.py`** — the Qt-touching layer.
  `QueueableController` is the duck-typed protocol every runnable `kind`
  implements: `start_from_queue(spec)`, `show_job(spec)`, `stop()`,
  `is_busy()` (`self._worker is not None and self._worker.isRunning()` for
  every Datasets controller; `self._runner.is_running` for `AnalyzeTab` via
  `AnalyzeRunMixin`), and a `jobFinished(ok, message, output_path)` signal
  that **must** be emitted only once per run, as the true last statement of
  that run's completion handler (mirroring `AnalyzeTab.runFinished`'s
  re-entrancy rule, since a bridge slot may start the next job synchronously
  from inside the emit). Each of the six Datasets controllers implements this
  directly (a `_launch()` split out of `_on_run()` so `start_from_queue` can
  skip the interactive restart/in-place confirmation a queued, unattended run
  has no one to answer); so does `AnalyzeTab`.

  `QueueBridge` owns the persisted `JobQueue` and a `kind -> QueueableController`
  registry, and is the only thing that actually starts a job: strictly
  serial, tracking the in-flight job as `(job_id, kind)` so a `jobFinished`
  from a controller that isn't the queue's own current job — e.g. the user
  pressed that tool's own Run button by hand while a *different* job was
  running — is dropped rather than misattributed. That tuple can only really
  distinguish by `kind` (`jobFinished` carries no job id), so the guard's
  soundness depends on every `start_from_queue` path eventually emitting
  `jobFinished` exactly once: a launch that silently no-ops on invalid input
  would leave `_current` stuck forever, which is why
  `MergePlatesController._launch`'s remap-failure branch and
  `ValidateController._run`'s "no dataset path" branch emit `jobFinished`
  directly rather than returning silently. On completion the bridge looks up
  `was_cancelled()` on the finishing controller (falling back to the Datasets
  convention that a cancelled worker's failure message starts with
  `"Cancelled"`) to tell a user-requested Stop apart from a genuine failure,
  since cancelling the running job **pauses the queue** — resuming is
  explicit — while a genuine failure just cascade-blocks that job's own
  successors and keeps going.

  Beside `add_job`/`remove_job`/`move_job` the bridge carries the editing
  mutators the page needs — `update_job`, `duplicate_job`, `retry_job`,
  `clear_completed`, `move_job_to`, `set_chain`, `clear_chain` — each
  following the same mutate → `_save()` → `queueChanged.emit()` shape and
  returning a `bool` so the caller can report a refusal. `update_job`
  re-derives the label from the new spec: a label describes its spec, so an
  edited job still named after what it used to run against would be worse
  than one with no name.

  `QueueBridge.show_job` (clicking a queued row to preview it) checks
  `runner.is_busy()` first and no-ops if the target controller already has a
  run in flight — `show_job(spec)` writes page fields **directly**, which
  bypasses `set_running`'s field locks (those only disable widgets against
  user clicks, not this programmatic path) and, for a controller like
  `SubsetController` that paints run progress onto the same picker plate
  `show_job` would repopulate, could visibly corrupt the in-flight run's own
  status display. It returns whether it actually populated, which is what
  lets an edit session refuse rather than open on an untouched page. It also
  hands the runner a deep copy of the job's spec
  (`json.loads(json.dumps(job.spec))`, matching `JobQueue.materialise`'s own
  assumption that a spec is JSON-shaped) rather than the queue's live
  persisted dict.

  **Populating a page and navigating to it are separate, because a running job
  wants one without the other.** `JobQueueController._on_show_requested`
  branches on the clicked job's state. A `running` job is *revealed only* — the
  page is already showing that run's own live progress, which is what the click
  is asking to see, and `show_job` is exactly what must not touch it. Anything
  else is populated and then revealed, since writing fields into a page nobody
  was taken to is the same silence in a quieter form. A job whose kind is busy
  with a *different* run is refused with a notice rather than shown, because the
  fields on that page would describe neither job. Before this split the click
  reached `show_job` alone, whose `False` was dropped: with the row's `Edit…`
  also disabled while running (`row.py`), a running job had no route to its page
  at all and nothing said so. Measured on a real `DatasetsTab`, clicking a
  running row moved the pane not at all and raised no notice.

  Navigation is a hook, `JobQueueController.set_reveal_hook`, supplied by
  `DatasetsTab._reveal_page` — deliberately **not** `_begin_session` with the
  banner suppressed. That one stands a page in for a job being *edited* and runs
  only after the queue has agreed a session may start; this is the plain "take
  me to that tool" half, and keeping them apart is what lets the running case
  navigate without populating. It returns whether it arrived: Analyze is the one
  queueable kind with no rail entry in this pane, so reaching it means changing
  pane, which only the shell can do — `allwell.py` passes
  `on_reveal_analyze=self._reveal_analyze_pane`, and a `DatasetsTab` built
  without a shell (tests, Datasets-only profiles) reports failure so the click
  says why rather than appearing to work.

  `QueueBridge.hydrate_job` is the edit-path counterpart. For the six
  Datasets tools `show_job` is already a complete inverse of `collect_spec` —
  the two read and write the same handful of keys — so hydration falls back
  to it. Analyze's is deliberately shallow (it sets the input folder and
  nothing else, because the queue re-derives a full `RunSpec` from the
  persisted JSON at dequeue time regardless, so the live form only has to
  *look* right), and collecting a previewed Analyze page back over a stored
  spec would replace every option the preview did not write. `AnalyzeTab`
  therefore supplies `hydrate_job`, which the bridge prefers when present —
  see §6.1.

  `JobQueueController` is the page-level controller (the usual page/worker
  shape, with `QueueBridge` standing in for a `QThread` worker): it owns the
  page, turns clicks into bridge calls, and refreshes the page on
  `queueChanged`.

- **`datasets/job_queue/session.py`** — `QueueSession`, the state of "a tool's
  page is currently standing in for a queued job", as `(kind, job_id or None)`
  where `None` means a new job of that kind. It lives beside the controller
  rather than inside it because its rules are substantial and none of them are
  about wiring a page to a bridge; `JobQueueController` keeps a thin façade
  (`begin_edit`, `begin_new_job`, `session`, `save_session`, `end_session`).

  It holds no widgets and imports no Qt, reaching outside through three
  injected callables — `notify`, `add_job`, and the `on_session` /
  `on_session_end` pair `DatasetsTab` supplies as "show that tool's page with
  this banner" and "take the banner off". That is what lets the whole class be
  exercised in a test with no page at all. `notify` must be dispatched through
  the controller at call time rather than bound at construction: it is the one
  seam a test patches to assert a refusal was reported, and capturing the bound
  method leaves the session calling the original past the patch.

  A session is refused while the job is running or its tool is busy with a run
  of its own, and abandoned with a note if the job starts running or leaves the
  queue while it is open — saving it then would either overwrite a spec already
  handed to a runner or write to a job that no longer exists.

  Three narrower hazards get their own handling, because each one silently
  corrupted the job under edit. **Clicking another queued row** while a
  session is open is refused: preview writes the clicked job's fields straight
  into the same page the session has open, so the next Save collected *that*
  job's settings into this one and turned it into a duplicate — and since
  nothing about the queue changed, the stale-session check never fired.
  **Starting a second session** says the first was closed without saving,
  rather than replacing it silently while its banner vanishes from a page the
  user has left. And `begin_edit` reports a **round-trip loss**: it collects
  the page it has just populated and names anything that was in the spec and
  is now gone. That is deliberately general rather than a per-tool patch —
  Subset builds its timepoint and FOV checkboxes by reading the source
  dataset, so a source that has moved leaves no boxes, `all_timepoints_
  selected()` answers "yes" over an empty list, and a one-timepoint subset
  silently widens to every timepoint on save. A value that merely changed form
  (a well relabelled `A1` → `A01`) is not a loss and is not reported. Every refusal in the class routes through one `_notify`
  seam rather than scattered `Toast` calls, so a test can assert that a
  refusal was reported without standing up a real `Toast` and its fade
  animation and dismiss timer.

- **`datasets/job_queue/view.py` + `row.py`** — `JobQueuePage` and `JobRow`,
  split because the row grew its own menu and drag handling.

  `JobRow` is two lines. The first identifies: drag handle, ordinal, kind
  icon, derived name, an indeterminate progress strip while running (a
  controller only ever reports a free-text status message, never a
  percentage), a `widgets.status_dot.StatusDot` state chip, and a `⋯` menu.
  The second is muted and descriptive — the predecessor's *label* (not its
  ten-hex-character id), `describe_detail`'s paths, and the latest status
  message — elided with `QFontMetrics` against the width the label is
  actually given, from `resizeEvent`, and hidden when empty. The `⋯` menu
  carries *Edit…*, *Duplicate*, *Retry* (terminal states only), *Run after ▸*,
  *Move up*/*Move down* (hidden on terminal rows, where reordering means
  nothing) and *Remove*; it is built by `build_menu()` and `exec()`d only
  from the click handler, so a test can inspect and `trigger()` actions
  without opening a popup that would block under the offscreen QPA. The
  *Run after* submenu is constructed as `QMenu(title, parent)` rather than
  via `addMenu(str)` — the menu that call returns is not kept alive on the
  Python side and can be collected out from under the menu that owns it.

  The ordinal is shown only on `pending` rows: a number on a row that already
  has a terminal outcome doesn't mean "queue position" any more. Rows are
  styled from `theme.qss` keyed on the `#JobRow` object name, **not** an
  inline stylesheet — a bare `border-bottom` set on the row widget has no
  selector, so Qt applies it to every child and each label, tool button and
  progress bar draws its own underline.

  `JobQueuePage` holds its rows in a plain `QVBoxLayout` inside the page's
  `QScrollArea`, not a `QListWidget`. A `QListWidgetItem`'s size hint is set
  once from the row's `sizeHint()`, so a row that later shows or hides its
  second line changes height while the hint does not; and the drag-handle
  reorder is `widgets/saved_selections_list.py`'s, which hosts its own rows
  in a box layout, so matching it is a like-for-like transfer. `set_jobs`
  reuses the existing rows whenever the id sequence is unchanged and calls
  `JobRow.update_from` on each — the page refreshes on every `queueChanged`,
  which during a run includes each status-message tick, and rebuilding every
  row for those threw away scroll position and hover state several times a
  second. Every child of a row is therefore built once and shown/hidden;
  nothing is conditionally constructed.

  The header is `▶ Start queue` (primary, at the left edge, matching every
  other Datasets page's own run button), `■ Stop job`, `+ Add job` — a menu
  of all seven kinds, each opening a new-job session on that tool's page —
  and `Clear completed` at the right. Under it sits a one-line state tally
  (states with no jobs are omitted; "0 failed" is not news) and the load-time
  `repair_notes`. The empty-queue hint is a plain caption, deliberately not
  `widgets.empty_state.EmptyState`: that widget is `Expanding/Expanding`,
  which is exactly the page-height empty container this page was already
  fixed against — one sentence rendered as a bordered box filling the
  viewport. A trailing `outer.addStretch(1)` collects the leftover height.

  Each of the six Datasets tool pages also keeps its own "+ Queue" button
  beside Run, calling the controller's `collect_spec()` and handing the
  result up through an injected `on_add_to_queue(kind, spec)` callback to
  `DatasetsTab._add_to_queue`.

**`widgets/context_banner.py`** carries a session on screen. `ContextBanner`
is a strip of glyph + sentence + confirm + cancel that says a page is being
used for something other than its own job; without it the borrowed page looks
exactly as it always does and the only way to finish is to guess. It is
host-agnostic — it knows nothing about queues, jobs or datasets, and whoever
shows it decides what confirming means — and it does not block the page
beneath it, which is the point, since the user is editing that page.
`DatasetsTab` inserts it at index 0 of the target page's own layout, which
works uniformly because all six Datasets pages hold a single `QScrollArea`
there; for Analyze it goes at index 0 of `_form_layout`, where the resume
banner already sits. Its Cancel routes through
`JobQueueController.end_session` rather than tearing the strip down directly:
the controller owns whether a session exists, and dropping only the strip
would leave it believing one was live and willing to save it later.

`datasets_tab.py` wires all of this: each of the six Datasets controllers is
constructed with `on_add_to_queue=tab._add_to_queue` and stashes itself in
`tab._controllers[kind]`; the `"Job Queue"` `_Feature` entry is built **last**
so `_build_job_queue` can register every already-built controller (plus the
injected `AnalyzeTab`, under kind `"analyze"`) against a single
`JobQueueController`. `allwell.py` injects the already-built `AnalyzeTab` as
`DatasetsTab(analyze_controller=...)` the same way it injects
`on_open_in_review`/`on_open_in_analyze` — so neither `datasets_tab.py` nor
anything under `datasets/job_queue/` ever imports `analyze_tab`. The reverse
direction goes the same way: `_build_job_queue` calls the injected tab's
`set_queue_sink`, which is where Analyze's own "+ Queue" button sends a run,
so `analyze_run_spec.py` never imports the queue either. An unset sink hides
the button, which is what a profile build without the Datasets pane gets.

Each `_Feature` carries the job-queue `kind` its page runs (`None` for the
Job Queue's own page), so a session can find the page for a job without a
second name → kind map to keep in step. `DatasetsTab._begin_session` switches
the rail and inserts the banner; `_end_session` takes it down and returns to
the queue page.

---

## 7. The widget library (`widgets/`)

Every widget under `widgets/` is:

- **Standalone.** It only imports from `theme`, Qt, and the Qt-free repo-root
  helpers under `util/` (`util.well_token`, `util.aw_safe`) — plus
  `widgets._support` for tiny RGB helpers and `set_variant`, a runtime
  dynamic-property + repolish helper (see below). It does *not* import
  `well_viewer` and knows nothing about `WellViewerApp` or any tab. That is why
  a widget's suppressed exceptions go through `util.aw_safe.swallowed`, the same
  helper the pipeline uses, rather than `well_viewer.core.safe`.
- **Token-styled.** Sizes derive from font metrics; colours come from
  `theme.Colors` / QSS object names. No hardcoded device pixels.
- **DPI-aware.** Geometry is computed from `fontMetrics()`, so the widgets
  scale correctly at 1×, 1.5×, 2×.
- **Singly responsible.** One widget per file. The file may contain a
  helper class or two but never another reusable widget.

Notable members worth knowing about by name:

| Widget | What it is |
|--------|-----------|
| `CropTool` (`well_viewer/data/crop_tool.py`) | The drag-a-region crop shared by Image Table and Segmentation. `set_status_widget(widget, template=…)` writes the crop state into an existing control's text — the Image Table's Crop button reads `Crop · (full FOV)` — instead of a separate caption beside it; `make_status_label` still returns a standalone label for call sites that want one. |
| `LabeledComboBox` (`widgets/labeled_combo.py`) | A `QComboBox` that paints its own scope label as a fixed prefix inside the closed box ("Channel: RFP", "Timepoint: 4.0h") rather than sitting beside a separate `QLabel`. `currentText()` / `currentIndex()` / every Qt signal still report the plain item value — only `paintEvent` changes, drawing `opt.currentText` set to the prefixed string. `sizeHint()` measures *only what's actually painted* (the current item's text plus the prefix, via `self.style().sizeFromContents(QStyle.CT_ComboBox, opt, content, self)`), not plain `QComboBox`'s default — which measures every item in the list up front (`AdjustToContentsOnFirstShow`) and sizes the closed box to the widest one, so a metric combo showing "N" sat as wide as its longest possible entry ("Property: Fraction Threshold On") the whole time. Callers must not layer `setFixedWidth` / `setMinimumContentsLength` / a QSS `min-width` on top — every one of those re-floors the box wider than its current content needs, which is exactly the waste this widget exists to cut; centre_view.py's Channel/Property combos and `review_csv/tab_view.py`'s Timepoint and FOV combos carried exactly that stale floor from before the box was made self-sizing, until removed. Before a caller populates the combo (or an empty dataset leaves it with no items), `currentText()` is `""` — `_painted_text()` falls back to the bare label ("Timepoint") rather than painting nothing, since a blank sliver reads as broken once the box tracks content width; `paintEvent` calls the same `_painted_text()` rather than duplicating the fallback, so painting and sizing can't drift apart. A repaint recomputes `sizeHint()` and calls `updateGeometry()` when it changed, which is what catches a text change made under `blockSignals(True)` (a common "seed without re-triggering the handler" pattern that suppresses `currentIndexChanged` but not Qt's own repaint scheduling). `ControlBand.add_scope("", combo)` is called with an empty label for one of these — the combo describes itself, so it's added alone rather than paired with a caption. Used for Plotting's page-level Channel and Property combos, every single-select Timepoint combo across Review (Stats, Spatial, Multivariate, Distribution, Heat Map, smFISH, Review CSV, Segmentation preview) — Heat Map's was a `StyledSlider` until unified onto the same combo every sibling tab uses — and every FOV combo (smFISH, Review CSV, Segmentation preview). Not every folded scope control is a `LabeledComboBox`: the checkbox-menu multi-select buttons (Bar Plots' and Scatter Cells/Agg's Timepoints pickers) fold the same way but by a different means, since there's no per-item combo box to paint a prefix on — `core.ui_helpers.multiselect_button_text` builds the readout string and the button's own `setText` carries it. The `ControlBand.add_scope(label, widget)` pairing is still what every other scope control uses — Heat Map's own Aggregation combo included, since there's no per-tab convention for what its prefix would read. |
| `multiselect_button_text` (`core/ui_helpers.py`) | The button-text counterpart to `LabeledComboBox` for a checkbox-menu multi-select control (the "Select Timepoints" pattern Bar Plots and both Scatter sub-tabs use): folds a prefix and a `{token: bool}` selection dict into one string — `"Timepoints: 2/5 selected"`, `"Timepoints: All 5 selected"`, `"Timepoints: 0 selected"` — for the picker button's own text, in place of a separate count `QLabel` beside it. The three call sites (`barplot/controller.py::update_bar_tp_selection_display`, `scatter/controller.py::update_scatter_tp_selection_display` / `update_tp_selection_display`) call it on every selection change; `persistence/view_state.py`'s bar-tab restore path calls it directly too, since it predates and is Qt-free like the rest of `core`. |
| `ControlBand` (`widgets/control_band.py`) | The one control row a Review tab uses, so that switching tabs does not also switch control layouts. Two zones and no third: **scope** on the left (what data is shown — channel, property, timepoint, threshold) and **actions** flush right (how it is shown and what to do with it — `Display ▾`, `Export ▾`, Properties). Which view is being shown is the tab bar's job, not the band's, so nothing is centred. The scope zone is a `FlowLayout` rather than a `QHBoxLayout`: a box layout has no wrap, so at narrow widths Qt compresses the children until a label is drawn over the combo beside it. `add_scope(label, *widgets)` bundles the label and its control through `flow_group` as **one** flow item, so a wrap can never strand a label from what it names, and returns that group so a caller can show or hide the pair. Three details are load-bearing and each cost a render to find: the action zone is `Qt.AlignTop`, or it centres itself against a wrapped scope zone and the buttons drift half a row down; the band's height floors at the action zone's, since `FlowLayout.heightForWidth` returns ~0 for an empty scope zone and the band would collapse behind its own buttons; and the band answers `heightForWidth` from the flow at the width the scope zone will actually get, because a tab built inside a stacked page is first realised at a narrow transient width where `sync_flow_height` pins the fully-wrapped height as the host's minimum — inheriting `QWidget`'s cached hint there left a 45px band 151px tall for good. `ROW_H` (28px; `band_height()` == 44 with the band's own padding) is what one row measures, whatever it holds — set as a real `setMinimumHeight`, because a parent layout asks the band's `QWidgetItem`, which delegates to `wid->layout()->heightForWidth()` and never reaches an override on the class. Its object name is `ControlBand`, which is data-gated (§5.2). A page whose body is a stack of leaf views still gets **one** band: each leaf claims a `ControlBandSlot` through `band.slot(name)`, adds its controls through the slot's mirror of `add_scope` / `add_action`, and `show_slot` shows one leaf's while hiding the rest. A slot is a real widget — a child of the band that is never laid out, so it is never painted — because leaves parent popover contents to what they think is their band. Items claimed by a slot that is not current arrive hidden, since `QLayout.addWidget` shows what it is handed and leaves build lazily. Both `add_scope` and `add_action` pass any `QCheckBox` they are handed through `widgets/checkbox_fit.fit_checkbox_width`, for the same reason `trailing=True` lives on the band rather than in each builder: the rule belongs with the control row, not in a builder's memory. |
| `checkbox_fit` (`widgets/checkbox_fit.py`) | **A checkbox wide enough for its own label, under whatever style is painting it.** `QCheckBox.sizeHint()` budgets an indicator, the QSS `spacing` gap and the text; a style that places the label further right than that budget assumed leaves the tail outside the widget, and `FlowLayout` then packs the next control at `x + width + spacing`, so the neighbour butts straight against the cut rather than leaving a gap that would make the clipping legible. Reported on macOS for the Distribution tab's `log x` / `log y` boxes, at every window width — width-independence is what places the defect in the item's own size rather than in pressure from the row. Deliberately not a constant: `theme.Metrics.checkbox_chrome_w` exists for call sites that run before the stylesheet is applied and cannot use `sizeHint()` at all, and the `QCheckBox::indicator` QSS comment records an earlier fixed `margin` added for this and removed for stacking on `spacing` everywhere it was not wanted — the shortfall is per-style and per-context. So it is asked of the style that will paint the widget: `SE_CheckBoxContents` is the rect reserved for the label, and when it is narrower than the text needs, that difference is the floor to add. Measured at the width a layout will actually give the widget (`sizeHint().expandedTo(minimumSize())`), not the bare hint — measuring at the hint reports a shortfall the helper has already closed. Fusion reserves ~6px more than `log x` needs, so it is a no-op there and can only ever add space a style is already clipping away; `tests/test_checkbox_label_fits.py` models the divergence with a checkbox whose `sizeHint` under-reports what its style paints, because CI cannot see this class of bug — exactly as `flow_layout.py` documents for `QMacStyle`'s layout spacing. A `QProxyStyle` overriding `SE_CheckBoxContents` does **not** work for that: once a global stylesheet is installed Qt wraps the widget's style in `QStyleSheetStyle`, which derives checkbox geometry from the stylesheet and never delegates the sub-element back, so the case reproduced when the file ran alone and was silently absorbed in the fast lane. |
| `build_page_body` (`widgets/page_body.py`) | The standard body of a main page: a `ControlBand` flush against the page's top, then the page's content beneath it. `build_page_body(parent, *, content_margins=(left, right, bottom), horizontal=False)` strips the page root's own margins and spacing, adds the band, and returns it together with a content host. There is no top-margin parameter — side and bottom padding are a page's own business, the top is what every page's first row is aligned by. **The band is added whether or not the page has controls for it**: a page with none (Image Table, the Playground, Batch Export, the Datasets tool pages) reserves the row so its content starts level with its neighbours', the same reservation the mode band's page-tabs host makes for a page with no sub-tab bar. `horizontal=True` gives the content host a `QHBoxLayout` for a page whose body is a row rather than a column (the Playground's canvas beside its property dock). No All-Well coupling, so the Review feature packages, `analyze_tab_log.py` and `datasets_tab.py` all build their bodies through it. Measured by `tests/test_page_alignment.py` (§5.2). |
| `ModeBar` (`widgets/mode_bar.py`) | The app's top-level Review / Analyze / Datasets selector. These are three applications sharing a window, not three tabs of one screen, and the control says so at rest: **every segment is filled at all times** — the active one accent-filled, the inactive ones on `panel_elevated` with full-strength `text_primary` and full-opacity glyphs. Nothing here is muted, because muting is how the rail's section list and the centre tab bar express "not currently selected", and borrowing it demotes a switch between applications to a third tab strip. Segments are flush — no gutters, no radius — so the bar is one band rather than three chips. `addSegment` takes a `widgets.icons` glyph *name*, not a `QIcon`, and repaints it in whichever foreground the segment's state calls for; a fixed `QIcon` draws its Normal pixmap in `text_secondary`, which on the accent fill is a dark glyph on saturated blue. The bar's default height is `tab_bar_height()`, measured off a throwaway polished `QTabBar` so the two bands share a bottom edge through any change to tab padding. That measurement is deliberately uncached — it depends on the application stylesheet and font, and a module-level cache freezes whichever were in force at the first call, which for a widget built during startup is a stylesheet that has not been applied yet. |
| `PlotCard` | The `QFrame` chrome around every matplotlib figure. Provides a custom toolbar (`MplToolbar`), Screen↔Publication mode toggle, and stats chip slot. |
| `WellPlateSelector` | **The app's only painted well grid.** Every 8×12 plate the user sees is this control — Review's rails and the sample-definitions editor, the Datasets tools' pickers and status maps, Convert OME-TIFF's acquisition-path editor, and Batch Export's group map. What differs between them is which *selection model* the host drives it in (`setSelectionModel`): `"set"` (the default, an unordered selection), `"path"` (an ordered acquisition route — a click appends an off-path well or removes an on-path one, the wells carry their visit numbers derived from the list's own order, and an alpha-blended arrow traces the route across the plate), or `"paint"` (the grid owns **nothing** — it reports the press/drag/release gesture as `wellPaintPressed` / `wellPaintDragged` / `paintDragFinished` and the host applies it to whatever it is editing). The corner select-all toggle appears only in `"set"`, since it toggles a selection set the other two models do not have, and a paint gesture ends with `paintDragFinished` rather than `selectionDragFinished` — the latter means "the selection settled", and a paint gesture never changed one. Supports per-well colour overrides, drag-to-select, and drag mime types for the heatmap layout (a drag source arms on press and applies the ordinary click on release, so clicking still selects — see §9.2). Every lightening of a state colour goes through `_lighten` (an RGB blend toward white), never `QColor.lighter`: Qt's version raises the HSV *value* and, once value is at the ceiling, starts dropping *saturation* — it lightens toward white rather than toward a pale version of the hue. Every state colour in the app sits at value 245–253, so `accent2 #B98CF7` came back as literally `#ffffff`; a dial's unfilled share and the sphere gradient's highlight both used it, which is how an uploaded well rendered as a white disc with no hue left to say which stage it was in. `_blend` is the one lerp behind `_lighten` and `_mix_with_rail`. `darker()` needs no equivalent — lowering value never touches saturation. **The sphere gradient is damped on a dial and only on a dial** (`_DIAL_RELIEF`, scaling the stops `_sphere_stops` returns). Blending toward white keeps the hue but *compresses two tones together* — the nearer both are to white, the smaller the gap — so at a plain well's full relief the done and remaining halves of a green well came within 25 luminance of each other under the highlight, against 39 at the centre, and the completed wedge was itself 35 % of the way to white. Reported as "the completed portion is too light and I can't see the difference between complete and incomplete". At 0.43 relief the highlight is 15 % toward white and that worst case is 33. A plain well keeps the full relief because it has no internal boundary for the highlight to compress; only the dial carries a second signal in the same pixels, so only the dial pays. Tone alone still cannot carry green, so the wedge's radii and arc are **stroked** in `base.darker(170)` — skipped at a full dial, where the boundary *is* the rim the well already has. A non-interactive "lit" mode (`setColoredWellsLit`) paints colour-override wells with the raised/lit style instead of the depressed one, plus three decoration layers that stack independently of each other and of the fill: a `setWellMarker` glyph, a `setWellFraction` / `clearWellFractions` progress dial, and a `setWellRing` / `setWellRings` / `clearWellRings` accent ring drawn just *outside* the well body. The dial is used by every live plate map that reports a run, whether that is a dashboard's own status plate (Analyze, Concatenate, Import Loose, Merge Plates) or a page's input plate doubling as the progress display (Subset's picker, Convert OME-TIFF's path editor); Validate has no dial because it inspects rather than copies. The ring exists because a host can need a third channel: Batch Export colours a well by which group owns it and rings the wells of the group *currently being edited*, two different questions that would otherwise fight over the same fill. **Every Batch Export mode shares one set of export groups.** Line, Bar, Scatter (cells) and Scatter (aggregate) used to persist under four private slots chosen by `_compute_persist_key`, so groups built in one mode were invisible in the others and anyone exporting the same conditions four ways built them four times; the groups describe the dataset's conditions, not the plot type, so they live in one slot (`base_panel.SHARED_PERSIST_KEY`). A document written before that has its four legacy keys merged in a fixed order and de-duplicated by name, and only when the shared key is absent — otherwise deleting a group would be undone by a legacy slot re-supplying it on the next load. The four panels are cached children of one host toggled by `setVisible`, so `tab_view._show_mode` calls `_reload_persisted_groups()` on the panel it reveals; that is not the auto-resync from the *sample-definition* replicate sets which the same function's comment rules out (that one wiped the user's export groups on every visit). No `EventBus` signal — the catalogue is capped at five by contract and a mode switch is local. Batch Export drives the plate in `"paint"` because an export group's model is replicate sets plus solo wells, not a flat set of clicked wells: `_apply_drag` resolves the well under the cursor to the sample definition that owns it and adds or removes that whole **member**, falling back to a solo well only for a well no sample claims. Membership is compared **by name** — `domain.selections_model.replicate_sets_loaded` builds a fresh `ReplicateSet` on every call and the class defines no `__eq__`, so an identity test never matched an already-present member: dragging across two wells of one sample appended it twice (and the group then exported that condition twice), while dragging a sample back off a group removed nothing. The name is already the identity key everywhere else, being what `_serialize_groups` persists and what hydration matches on. All three decorations, plus the path, are pruned by `setEnabledWells` / `setWellEnabled` — a disabled well paints as an empty socket, and a ring or a dial on a socket describes a well that is not there. Per-well rank colours are now pushed on **every** tab (not just the plotting tabs), so a given well reads as the same colour everywhere in the app. Every host sizes and places its plate through `plate_geometry.apply_plate_geometry` (below) rather than setting its own caps. |
| `plate_geometry` (`widgets/plate_geometry.py`) | The single sizing and placement rule for every `WellPlateSelector` in the app — Review's five rail pickers, the "New Replicate Set" dialog, the saved-selections compose popover, Subset's well picker, Batch Export's group map, Convert OME-TIFF's acquisition-path plate, and each run dashboard's status map. **Every well grid the user can see goes through it**, with two documented exceptions that are not `WellPlateSelector`s at all: Home's *Well Labels* grid (`features/home/labels/tab_view.py`), which is 96 `QLineEdit`s because each cell takes free text, and the window icon's decorative plate in `allwell.py`, which is a painted graphic with no model behind it. `apply_plate_geometry(plate, *, role)` takes a `role` of `"rail"` (action row hidden, host has its own buttons), `"picker"` (action row shown), `"status"` (action row hidden, selection `"passive"`) or `"editor"` (action row hidden, interactive, and — unlike `"rail"` — subject to the size caps); the role decides **only** those behavioural things. Size, panel treatment and alignment are identical across all of them, which is the point: the same control used to render at three different cell scales depending on the pane. `"editor"` exists for an interactive plate that sits in a page rather than the rail and supplies its own actions — Convert OME-TIFF's acquisition-path plate, whose Generate/Clear buttons sit beneath it; uncapped it grows to the scroll area's full width and pushes the run button below the fold, which is why that page used to restate `330` / `210` as literals of its own. `PLATE_MAX_WIDTH` (330, the rail's own width) caps the card because the selector paints its panel background across its *entire* allocated box while the grid inside sizes cells to `min(width / 12, height / 8)` — past the height cap, extra width stretches the panel into a wide, mostly-empty card with a small grid floating in it. `PLATE_MAX_HEIGHT` (210) is the budget for a plate with its action row hidden; for a `"picker"` the helper *measures* that row from the widget's own layout and adds its height on top, so the grid budget — and therefore the cell size — is the same either way, and no caller re-derives an empirical "+48". `PLATE_ALIGNMENT` is the alignment flag every host passes to `addWidget`, so a container wider than the width cap centres the card instead of stranding it against an edge. **The `"rail"` role is exempt from both caps.** Review's left-rail picker is the app's primary interaction surface rather than a status readout: it scales to whatever width the rail gives it, with no ceiling. Capping it alongside the others shrank its cells from 23.7px to 20.8px — unifying the two by making the one people actually click on smaller, which is the wrong direction. It still takes the shared size *policy*, because the aspect-locked scaling genuinely is the same rule; only the ceiling differs. The module lives under `widgets/` rather than in `well_viewer/core/ui_helpers.py` because `widgets/` and `datasets/` both need it and `widgets/` must not import `well_viewer` (`test_architecture_contracts.py::test_widgets_do_not_import_the_app`). Every caller names its own role; Review's sidebar plate asks for `"rail"`. One rule the helper cannot enforce for its callers: a plate's container must add no horizontal inset, since the grid paints from the widget's own `width()` and a gutter on one host shrinks that host's plate in both dimensions. |
| `PathField` (`widgets/path_field.py`) | A labelled folder/file picker: caption + read-only path box (basename shown, full path on hover) + icon Browse button, with an optional inline validity hint. Unifies two near-duplicate private widgets the Concatenate and Convert OME-TIFF pages grew independently. The dialog is opened by an injected `pick(current, parent)` callable rather than hardcoded, so the same widget serves a folder dialog (`folder_picker(prompt)`, the common case) or a page-owned file dialog (Convert OME-TIFF's `.ome.tif` source picker) — the picker takes *parent* explicitly because an unparented `QFileDialog` opens as a detached window on macOS instead of an app sheet. `setLocked(locked)` disables only the Browse button, not the whole widget — every Datasets tool page uses it (not `setEnabled(False)`) to lock a field for a run's duration, since the latter dims the *entire* field, including the already-read-only path text, the one piece of context a user watching a long run most wants to check. `show_note(text)` shows a neutral, "Muted"-styled inline note in place of the validator's own red "Danger"-styled hint, until the next `revalidate()` call — used after a successful run, when the validator's own message would otherwise be the exact "already holds a finished dataset" wording a pre-run pick gets, which is technically true but reads as a warning about the very output that run just correctly produced. `show_warning(text)` is its red counterpart, for a rule the validator cannot reach because it spans two fields — a validator sees one path, so "this destination is also the source" can only be decided by the page, and without somewhere to say it the page's Run button goes dark with no reason attached. |
| `StagePathView` (`widgets/stage_path_view.py`) | **Carries its own size hint** (`plate_geometry`'s shared caps) and a `Fixed` vertical policy. A bare `QWidget` has none, so a `QVBoxLayout` gives it nothing and it lays out at 0×0 — every visibility flag true, `setPath` full, and not one pixel drawn; asserting the points it holds cannot see that, only its geometry can. **Navigable, and self-labelling.** The wheel zooms about the cursor (`setZoom`, bounded 1–40×), a drag pans, a double-click fits — zooming about the cursor rather than the widget's centre is what makes a scan of hundreds of positions usable, since the well being looked at stays put instead of sliding away while the view grows around the middle. Fully zoomed out always resets the pan, so "out" is the whole map and never a panned fragment. Positions number themselves when there is **room** — the gap to the nearest neighbour exceeding `_LABEL_ROOM_PX` — rather than under a count: at full extent the marks are pixels apart and labels would overlap each other and the dots they name, while zoomed in the numbers are the reason to have zoomed, so they appear exactly where the eye already went. That spacing test is necessary and not sufficient, so each label is also measured before it is drawn (`QFontMetricsF`) and **skipped where its padded box hits one already drawn**. The gap it clears is between *consecutive* marks and says nothing about a mark from another row landing in the same place, and a four-digit number — which a scan of a few thousand positions reaches — is nearly twice as wide as the gap the test asks for; every label then sat on its neighbours and the result read as white scribble rather than as numbers. Text also gets its own render hint (`TextAntialiasing`), a point-size floor (`_LABEL_MIN_PT`) below which digits stop being distinguishable and a label is not worth drawing, and the panel colour laid under it first — the digits are white and the thing they annotate is the accent route line, which inside a well doubles back through its own labels, so unbacked text is white-on-accent exactly where the numbers matter most. `tests/test_stage_path_view.py` asserts this at the painter, not on the widget's own values: what makes a label legible is where it lands beside the others, and nothing inside the widget knows that. One transform serves the lattice and the path together; drawn at plate scale while the path zoomed, the lattice stopped being the frame the path is read against and became a decoration the path had flown off. **Plate scale only while the path fits a plate.** A run whose well pitch was read too small out of the coordinates spans far more than twelve cells, and at true scale everything past the first few wells falls outside the widget — the user sees a fragment with nothing saying there is more. Then the drawing is scaled to its own bounding box and centred instead, and `isFitted()` says so, because a picture where a square is no longer a well must not be read as one; the page swaps its caption to say the same and to point at the grid, since a pitch that wrong usually means the layout is wrong too. The microscope's actual travel, drawn at a plate's scale and normally directly under it. A plate map answers "which well?"; every field of view inside one well is the same cell there, so the route the stage took through them, and how far off-centre they sit, has nowhere else to appear. Takes points already in cell units (`datasets.ome_import.stage_layout.path_in_cells`) and a reference plate — `setPlate`, anything exposing `cellRect(row, col)` — and does no coordinate maths of its own beyond scaling cells to pixels. It borrows geometry rather than deriving it from its own width because a `WellPlateSelector` reserves room for its row and column headers before sizing cells, so equal widths do not give equal cells. Dots, line widths and the lattice are fractions of a cell, so the drawing scales with the plate instead of pinning sizes that only look right at one width; past `_DOT_BUDGET` positions the per-point dots are dropped and only the route is drawn, since a plate scan's thousands of points merge into a blob that hides the route they were decorating. The first position always keeps a dot, in `success` rather than `accent`: where the scan begins is the one thing on the drawing the user chose rather than read. Carries no All-Well knowledge, so it stays inside the `widgets/` no-app-coupling contract. |
| `PlateOrderEditor` (`widgets/plate_order_editor.py`) | A `WellPlateSelector` driven in its ordered `"path"` selection model, so it is the same painted control as every other plate in the app; this ~90-line subclass adds only what the shared widget has no opinion about. The path model itself — lit wells carrying their 1-based visit number, the alpha-blended arrow tracing the order across the plate, and the click that appends an off-path well or removes an on-path one and renumbers the rest — lives in `well_plate_selector`. A *dirty* flag distinguishes a hand-drawn order from a `set_preview` seed (which is ignored once dirty). Used by the Convert OME-TIFF page to define an arbitrary, possibly non-rectangular `well_order`. |
| `PlateProgress` (`widgets/plate_progress.py`) | The shared chrome behind every live run dashboard: a header (title/status/eta/collapse toggle), a headline label, one overall completion bar, and a 3-column body — a subclass-owned left "rail" slot, the plate map, and a legend. `RunDashboard` (Analyze), `ConcatDashboard`, `SubsetDashboard`, `MergePlatesDashboard`, `ValidateDashboard`, and `ImportLooseDashboard` each subclass it, building their own rail content and well-colour rules into the shared layout so none of them can visually drift apart. The plate map (`self._plate`, a `WellPlateSelector`) takes its size and placement from the shared `widgets/plate_geometry.apply_plate_geometry(plate, role="status")` — the one rule every plate map in the app goes through, so the status map here renders at exactly the cell scale of the Review rail's picker and of the Datasets tools' own pickers. `well_plate_selector._metrics()` derives cell size as `min(available_width / 12, available_height / 8)`, so a height-starved plate renders illegibly small even with hundreds of spare horizontal pixels — every Datasets tool page with a dashboard wraps its whole content in a `QScrollArea` (mirroring `analyze_tab.py`'s own form panel) for that reason: at the app's default launch size (`1640×980`, `allwell.py`), a plain `QVBoxLayout` crushes whatever's at the bottom of a long page instead of letting the user scroll to it. The plate is added with `center.addWidget(self._plate, 0, PLATE_ALIGNMENT)` and no layout stretch, so it sits centred in a centre column that is as wide as the page allows. `PlateProgress` also promotes the selector's per-well dial API onto the dashboard itself — `set_well_fraction(well, fraction)`, `set_well_progress(well, done, total)` and `clear_well_fractions()` — so a controller reports a well's progress without reaching through `self._plate`, which does not exist at all under `show_plate=False`; all three no-op in that case. The `done`/`total` arithmetic lives in the module-level `plate_progress.fraction_or_none`, which returns `None` for a non-positive `total` rather than dividing by it, since "not counted yet" must not render as a definite zero-filled dial — the same picture as a well that has genuinely done nothing. It is module-level because both kinds of dial host need it: `set_well_progress`, and a controller painting its page's *own* plate. **A no-op is the right base-class behaviour but it is not a progress report**, so a controller whose dashboard was built `show_plate=False` must drive the plate it does own instead of calling these and assuming something happened — `SubsetController` routes its worker's `wellFraction` signal to the picker plate's `setWellFraction` for exactly that reason (see the Subset rows in §11). Each host page's own outer layout ends in a trailing `outer.addStretch(1)` after its last real widget (and adds the dashboard itself with no stretch factor) for the same class of reason one level up: `QScrollArea.setWidgetResizable(True)` grows the page's content widget to fill the viewport, and with the raw log hidden by default nothing else in the column was willing to absorb that leftover height — the dashboard was, turning into a growing dead zone below the plate via its own internal `center.addStretch(1)`. The trailing stretch outranks every 0-stretch item above it, so slack collects as blank page background below everything instead. (Validate's page needs neither fix itself — its findings list is always visible with its own stretch factor, so it was already absorbing the slack correctly.) `PlateProgress.__init__` also takes `show_plate: bool = True` — a page whose input already has its own interactive plate map (Subset) passes `False` to skip the centre column entirely rather than leave a redundant second plate map, or worse, an empty-but-still-stretch-1 dead column between the rail and legend; see `SubsetDashboard` above. A second parameter, `show_legend: bool = True`, independently skips the legend column — `SubsetDashboard` passes `show_legend=False` alongside `show_plate=False`, since its counts rail already pairs a coloured dot with a state label per row, and a separate legend column would only repeat those same pairs without the live counts. Skipping either column when the centre plate column is also skipped no longer leaves a wide dead gap between whatever's left: with no widget left carrying a layout stretch factor, a trailing `body.addStretch(1)` collects the leftover width at the row's own right edge instead of Qt distributing it as blank padding around the remaining fixed-width items. The five dashboards on the Datasets-pane tools (`ConcatDashboard`, `ValidateDashboard`, `SubsetDashboard`, `ImportLooseDashboard`, `MergePlatesDashboard`) are each constructed **hidden** (`setVisible(False)`) by their host page, then revealed by that page's own `_update_status_preview` as soon as its inputs are valid enough to answer "what would this actually touch" — seeded with the real wells a run would touch (via `_common.dataset_paths.preview_wells`, or the tool's own live preview — Import Loose's `scan_folder`, Subset's own picker selection, Merge Plates' remap-applied union of both sources) and a "Ready — N well(s)" headline, rather than gated on the tool's own run/check method actually starting; each page hides it again on the next input edit that invalidates the preview. This replaced an earlier convention, gating the same reveal on a run actually starting, that read as "something is wrong" — an idle, empty status strip before anything had happened looked like a stuck or broken state rather than "nothing has run yet" (and, for `ConcatDashboard` specifically, an even earlier construction-time-visible default that painted a coloured plate with *zero* wells enabled from the moment the page loaded). `RunDashboard` (Analyze) is the one dashboard that still stays visible from construction — Analyze's own form has no comparable "not yet valid" gate to key a reveal off. Every well-coloured dashboard also marks a well's *terminal* states with a glyph layer (`WellPlateSelector.setWellMarker`), not just its colour: colour alone doesn't distinguish success-green from a warning hue for a user who can't tell the two apart, so each terminal state carries its own glyph (`✓` for success, `!` for a problem, `✕` for an error, and Merge Plates' persistent `"2"` for "this well came from dataset 2"). **A well being worked on carries no glyph anywhere in the app** — it is drawn as a progress dial, and a glyph sits in the middle of exactly the area the dial fills, while the fill and the colour already say the well is working. That is one rule across every plate map, not a per-dashboard choice: the `⚙` a computing/merging/writing/copying well used to wear is gone from Analyze's run dashboard, Concatenate, Import Loose, Merge Plates and Subset alike. Merge Plates' `"2"` is the one glyph that survives on a well mid-dial, because it reports *origin* rather than progress and is the only thing telling the two datasets apart once both sides are coloured dots on a small plate. |
| `RunDashboard` | A thin `PlateProgress` subclass — the live, visual pipeline-run view shown above the Analyze log: a left stage rail (`group → upload → process → threshold → download`, the upload/download rungs only for remote runs) plus a centre plate map that colours each well by the stage it has reached. **Every well that has started is drawn as a dial rather than a solid disc** (`PlateProgress.set_well_fraction` → `WellPlateSelector.setWellFraction`): the well body itself is a two-tone pie of its own state colour — dark for the share completed, light for what is left, filled clockwise from twelve — so the hue still says which stage the well is in while the fill says how far through it is. Before that, a well on its first FOV-group and one on its last looked identical for however long the well took, which on a timelapse plate is most of the run. **The dial tracks whichever stage the well is actually inside**, decided by `_well_fraction`: a transfer in flight wins (bytes of its input zip sent while uploading, bytes of its output zip landed while downloading — a download outranks everything else, since a well being fetched has by definition already computed), then the FOV-group counts `set_well_fov` records from `well_fov` events, then the stages reached. A well sitting uploaded on the remote, waiting its turn, reads as a **full** dial: its archive is there in full, the dial has just spent the whole upload filling with the bytes sent, and what it waits for now is its turn rather than more transfer. Emptying it there — which is what "started but nothing finished" used to mean — reset the well from nearly-full to empty at the exact instant its upload completed, so the transfer appeared to undo itself. A full dial therefore means *uploaded* or *computed* depending on the well's colour, which is the accepted cost of a dial that never runs backwards. A well that is genuinely computing with no group measured yet is the empty dial, and that zero is true. A well with nothing started (grouped at most) and an errored well have no dial at all, since a part-filled ring beside the `✕` would suggest it is still going. Per-well transfer fractions arrive as `well_xfer` events (see `pipeline_runner` in §7) and are held in `_xfer` only while the transfer is live: a fraction of 1.0 *ends* the transfer rather than pinning the dial full, because a well is marked `upload` as rsync starts sending it. `setWellFraction` is opt-in, and only a surface that is *reporting a run* sets it — this dashboard, the four Datasets tool pages that copy per-well data, and nothing else — so Review's rail, the heat-map drag source and the replicate pickers are untouched. A computing well carries **no glyph** — the `⚙` it used to wear sat in the middle of exactly the area the dial now fills, and the fill plus the colour already say it is working; only `✕` for an error survives as a marker. There is no rail of per-well progress bars beneath the plate: the dial carries that information where the eye already is, and the exact counts (`FOV 7/12`, `downloading 42%`) live in the well's hover tooltip. |

The dial covers **transfers as well as compute**: a well filling with the bytes of its own archive while it uploads or downloads, and with FOV-groups while it computes, so the stages that dominate a remote run's wall-clock are the ones the plate describes best. Plate-wide transfer progress is separate and lives on the status line, fed by `remote_xfer` (below). An uploaded-but-not-started well has its own colour on a remote run (`accent2`): it used to share `warn` with a computing well, so a plate mid-run was one block of amber that said nothing about which wells were actually being worked on — and once a computing well gained a dial, the two differed in *shape* while their colour still claimed they were the same state. `accent2` is a neighbour of the `accent` used for "computed, awaiting fetch"; both mean "it is over there", so the pair reads as one family. The legend gains a matching **uploaded** row, on remote runs only.

**The status line belongs to whatever phase is current.** `remote_xfer` events carry the plate-wide rsync fraction; `AnalyzeTab` hands them to `RunDashboard.set_transfer(pct)`, and the first compute-phase event (`well_total` / `well_done` / `well_fov`, in `AnalyzeProgressMixin._COMPUTE_PHASE_EVENTS`) hands it `None`. The dashboard holds the fraction as state and formats the label itself. Two different objects drive this dashboard — `analyze_tab` for the runner's own events, `analyze_progress` for the pipeline's — and the label used to be a string one of them pushed and neither retracted, so it sat on the last transfer tick (`Transferring… 92%`) for the whole compute phase, until the end of the run cleared it. Clearing is keyed off the pipeline's own progress rather than off rsync reporting exactly 100%, which it need not do: the final tick before rsync exits can be any value, and in batched mode it is scaled per batch.

**Transfer progress is live state, so it stays out of the log.** A remote
transfer used to write a line every eight seconds (bytes so far, time elapsed)
plus rsync's own percentage stream once a second. A log records what *happened*,
and "still transferring" is a sample of a continuous variable; one line per
sample also evicted the record it was crowding, since the log holds 10 000
blocks and `analyze_tab._log_line`'s scroll-compensation comment already blamed
this stream for making that trimming common. Those lines now carry
`remote_runner.TRANSIENT_PREFIX`; `ProgressTracker.parse` turns them into a
single `("status", …)` event and nothing else, and `AnalyzeTab._poll_log` skips
`_log_line` for them — so they repaint the dashboard's status line beside the
transfer bar and never reach the log. The prefix is applied at the source rather
than sniffed downstream: the per-file `[remote] ↑ <well>.zip` names look similar
and are real events (they drive the Upload rail), so telling them apart by shape
would be fragile. It lives in `remote_runner` with the only code that emits it,
because `pipeline_runner` already imports that module and the reverse is a
cycle. `Fetcher._transfer_heartbeat` runs two cadences out of one loop: the live
sample every `interval`, and a durable breadcrumb every `_BREADCRUMB_INTERVAL_S`
(60s) that *does* go to the log — a status line cannot answer "was it stuck at
3am?" the next morning, and one line a minute is a record rather than a flood.

**A well turns green only when both halves of its output have landed** — the
`{well}_out.zip` and its per-well CSV(s) are two separate rsyncs, and
`ProgressTracker._mark_fetch_part` fires the `download` stage on the transition
where both are present. Two paths bypass that handshake, and both must announce
themselves or the well stays painted "computed, awaiting fetch" with its data
already on disk: a well the remote skipped as already current
(`[remote] ✓ W: up to date`), and one rescued by the end-of-run backstop. The
backstop reports in the **same two lines** the in-stream fetch uses, tagged
`(end-of-run retry)` — `RemoteRunner._retry_missing_wells` emits both halves for
every well it finishes with, including a half it did not have to re-pull,
because by the time it runs the well has usually registered *neither*: the
in-stream fetcher returns early when the zip pull fails, before it reaches the
CSVs. Reusing the fetch vocabulary rather than inventing a form of its own means
a rescued well reaches green by the same route, with nothing extra for the
tracker to learn. The drain's own chatter
(`draining fetcher: N well(s)`, `still downloading… (5/8 wells)`) carries counts
rather than well ids and deliberately marks nothing.

**Per-well transfer progress** rides the same marker. `WELL_XFER_PREFIX` is
`TRANSIENT_PREFIX` plus `xfer `, so the "never write this to the log" rule
covers it without a second place to remember, and `ProgressTracker.parse`
claims the longer marker first — turning `[remote] ⋯ xfer down C02 0.4180`
into a `("well_xfer", (well, direction, fraction))` event that
`AnalyzeTab._apply_progress_event` routes to `RunDashboard.set_well_transfer`
and thence to the well's dial. A line carrying the marker that does not parse
yields no event at all rather than falling through to the status line, where it
would appear to the user as prose.

The two directions derive their fraction differently, because the two rsyncs
expose different things. **Downloading**, `Fetcher._transfer_heartbeat` already
had the numerator — it sums the growing rsync partial file(s) in the
destination directory — and the denominator comes from
`Fetcher._remote_file_size`, one small `bash -s` over ssh per well that asks the
remote for the size of `<well>_out.zip` (GNU `stat`, BSD `stat`, then `ls -ln`,
so it works on a Linux cluster or a macOS host without knowing which). It is
piped over stdin rather than passed as an argument for the same reason every
other remote script here is: a `~` in the staging path only expands unquoted.
When the size cannot be read the heartbeat still reports bytes-so-far on the
status line and emits **no fraction** — an unknown denominator must not become
an invented percentage. **Uploading**, `--info=progress2` reports bytes for the
whole rsync rather than per file, so a percentage alone says nothing about any
one well; `_UploadWellProgress` pairs it with `--info=name1`, which prints each
filename as its transfer begins. The cumulative total at that moment is the
well's zero point, the local archive's own `st_size` (`_well_zip_sizes`) is its
denominator, and everything until the next filename belongs to it. Files rsync
skips emit no name line and contribute no bytes, so they never open a window at
all — which is correct, since nothing about them is in flight.

The **Upload rail** counts wells off `[remote] ↑ …<well>.zip` lines, which used to come only from rsync's own per-file output. That is a poor thing to depend on: rsync says nothing on a cache hit (it moves nothing), nothing for files an incremental re-send skips, and nothing at all when the local rsync predates `--info=name1` — macOS still ships 2.6.9. In each case the transfer had succeeded and the rail sat at 0/N, reading as a stalled upload. Batched mode was worst, since `upload_subset` had no compensator at all where `ensure_uploaded` at least synthesised lines on a cache hit. `_log_wells_uploaded` now emits one line per archive from both paths' success branch, where the caller knows which archives it asked for and that rsync returned 0; the dashboard de-dupes into sets, so it costs nothing alongside live per-file lines, and a non-zero rc emits nothing rather than drawing a rail ahead of the data. Driven entirely by `pipeline_runner.ProgressTracker` events; tracks each stage as an independent well-set (so a server-side `threshold` can't make `download` read N/N before anything is fetched) and goes blue=computed-on-server vs green=downloaded-locally on remote runs. The raw colour log stays underneath, collapsible. |
| `NamedPageStack` (in `core/centre_view.py`, not `widgets/` — but morally a widget) | A `QStackedWidget` keyed by page name; supports `detachPage`/`reattachPage` (Split View). |
| `RailNav` | The left-rail section navigator. |
| `info_icon` (`widgets/info_icon.py`) | The `ⓘ` hover-for-more marker, styled by the `#InfoHint` QSS rule. A function rather than a class — it returns a configured `QLabel`. Shared by the Analyze form (`AnalyzeFormChromeMixin._info_icon` delegates to it) and the Playground's threshold rows, so the app has one "there is an explanation here" affordance rather than a copy per pane. Sets WhatsThis as well as the tooltip, which is the route that works for someone who cannot hold a hover. |
| `SchemaPicker` (`widgets/schema_picker.py`) | The filename-schema builder: a separator box, one dropdown per filename position, and the colon-delimited schema string the two of them write. The dropdowns and the string are two views of one value and sync both ways, each write blocking the other's signals so they cannot chase each other. Vocabulary and validity come from `util.schema` — the module the pipeline parses schemas with — so the picker offers exactly the fields the pipeline understands, reads a string the way `parse_schema` reads it (empty tokens collapse, rather than the dropdowns showing a layout the run will not use), and reports errors in `validate_schema`'s own wording. `schema()` returns the typed string in preference to the dropdowns, since the string can express more positions than there are dropdowns. `row_factory(parent, label) -> cell` is the layout seam: the Analyze form passes its own `_row` so the picker sits inside that form's label column, and the Datasets pane's Zip Loose Folder page takes the default plain row. Mounted by both, because both ask the user about the same filenames — Zip Loose Folder produces the archives Analyze then reads. |
| `SegmentedControl`, `ChipGroup`, `Stepper`, `StyledSlider`, `ToggleSwitch` | Form controls; each exposes `bindingAdapter()` returning `(getter, setter, change_signal)` for the Export Style sidebar's binding loop. |
| `SavedSelectionsList` | The rich saved-selection list with composition (drag-to-reorder, per-group flat well editing, recolour popovers). A group is one condition — its wells are its implicit replicate set, so there are no replicate sub-partitions / solo strips. |
| `LutSelector`, `GradientStrip`, `ColorPickerPopover` | The colour-map picker + its preview strip + the freeform colour-picker popover. |
| `lut_cmaps.lut_cmap` | Qt-free resolver that maps a LUT name to a matplotlib colormap: single-hue names (Reds/Greens/Blues/Purples/Oranges) become a **black→colour** `LinearSegmentedColormap` (so a dark background reads as "off"), Greys stays black→white, everything else is the builtin (`_r`-aware). Routed through `ui_helpers.make_fluor_thumb` / `lut_colormap_to_rgb` and `LutSelector._cmap_stops`, so thumbnails, the Image Table compose/composite/export paths, Playground, and the picker strip all flip together. |
| `ContextBanner` (`widgets/context_banner.py`) | A strip of glyph + sentence + confirm + cancel saying a page is being used for something other than its own job — the Job Queue borrows a tool's page to set up or correct a queued job (§6b). Host-agnostic: it knows nothing about queues or jobs, and whoever shows it decides what confirming means. Deliberately not a dialog and it does not block the page beneath, since the point is that the user is editing that page. `EmptyState` is *not* a substitute for a one-line caption in the same situations — it is `Expanding/Expanding`, so a single sentence in one renders as a page-height bordered box. |
| `Popover`, `Drawer`, `Toast`, `EmptyState` | Generic overlay primitives. `Drawer` slides in from `edge="right"` (default — the Help drawer) or `edge="bottom"` (a full-width panel sized by `height_hint`/`height_fraction` — the Application Log drawer, whose long lines the narrow right panel clipped). |
| `FlowLayout` | A `QLayout` (not a widget) that reflows its children onto multiple lines instead of clipping — the standard Qt Flow Layout adapted to PySide6. Control rows that carry more widgets than fit at a narrow width (the plotting / image-review "TabCtrl" rows) use it so **Split View** halving the pane grows the row taller rather than hiding controls off the right edge. Four rules go with it. (1) A flow row skips hidden widgets when it places items, so *which* widgets are visible is an input to the layout, not merely a blanked slot — `_FlowItemVisibility` watches show/hide on every managed widget and invalidates the layout, without which a row that swaps one option group for another (the Multivariate tab's per-mode row) keeps the positions computed for the previous visible set and paints the newly-shown group on top of them. That watcher resolves the layout from its own `parent()` and keeps **no** per-instance state: its C++ object belongs to the layout, so it outlives the Python instance `__init__` ran on, and PySide builds a fresh wrapper for it once that instance is collected. Anything stashed on `self` is gone by then — a watcher that remembered its layout in an attribute raised `AttributeError` inside the Qt event loop on every subsequent show and hide, and the row stopped reflowing. (2) A label and the control it names must go in as one `flow_group` item, or the row can wrap between them. (3) A host whose row may wrap needs `sync_flow_height`, since a stacked page sizes the row from its one-line minimum and clips the second line otherwise. (4) Spacing is fixed — 14px horizontal, `theme.Spacing.xs` vertical — **not** derived from the platform style, and no call site overrides it. Deriving it asks `pixelMetric(PM_Layout*Spacing)`, which Qt documents as returning -1 on styles that implement the size-class-aware `layoutSpacing()` instead. Measured, the two styles are exact opposites: QMacStyle reports `pixelMetric = -1, layoutSpacing = 14`, Fusion reports `pixelMetric = 6, layoutSpacing = -1`. The layout clamps with `max(0, …)`, so on macOS every row that relied on the derived value packed its controls edge to edge, while CI (on Fusion) saw 6 and looked fine. The 14 is QMacStyle's own button-row spacing, adopted app-wide because it is the one number measured against a real control row — it is deliberately off the `theme.Spacing` scale. Passing `-1` still derives, but now asks both APIs and falls back to the defaults rather than to nothing. |
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

**Every interactive class the app disables needs a `:disabled` rule, and the
base rule's `color` is why.** A stylesheet colour declared with no disabled
variant is applied in *every* state — it overrides the palette's disabled
group, so `setEnabled(False)` takes effect functionally (the popup will not
open, the click does nothing) with no change on screen at all. `QComboBox` was
the one input class missing the rule, so the Spatial tab's Timepoint picker,
greyed at Summary level by `tp_cb.setEnabled(not is_summary)`, rendered
pixel-identical to a live one; the same omission silenced the Metric picker
beside it and every other disabled combo in the app. It also reached the custom
painter: `LabeledComboBox.paintEvent` takes its pen from
`palette().color(foregroundRole())`, which the stylesheet had forced to the
enabled colour. A widget may answer this itself instead — `StyledSlider.paintEvent`
branches on `isEnabled()` — but then it owes the same visible difference.
`tests/test_disabled_controls_look_disabled.py` asserts it on **rendered
pixels** rather than on the QSS text or on `isEnabled()`: `isEnabled()` was
already `False` throughout and proved nothing, and a text check would pass the
day the rule is spelled differently. `QTextEdit` / `QPlainTextEdit` share the
`QLineEdit` base rule but not its `:disabled` variant; nothing disables either
today (every `…_edit` the app greys is a `QLineEdit`), so that gap is recorded
rather than filled with a rule describing nothing.

**A disabled control should also say what would re-enable it.** Greying tells
the user the control is inert; it does not distinguish "wrong mode for this"
from "broken". The Spatial band does this with a swapped tooltip on both of its
gated pickers — the Metric picker names the vizzes that ignore it, and the
Timepoint picker states that Summary walks every timepoint because they are the
columns of the matrix it draws.

**`accent` and `accent2` mean two different things, and every caller has to
pick the right one.** `Colors.accent` is used across several Datasets
dashboards to flag an anomalous well — one whose state doesn't fit the normal
run (Concatenate's "merged with no matching measurements CSV on one side",
Subset's "no rows matched the filter"). `Colors.accent2` is a second, distinct
hue for the unrelated case of "this belongs to the other of two normal
groups" — currently only Merge Plates' dataset-2 wells — and must never be
reused to mean an anomaly, or a well merely belonging to the other source
would read as suspect on the same plate map. Both are `theme.Colors` tokens
like any other; there's nothing structurally different about `accent2`, only
this convention about what each is allowed to mean.

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
`theme.qss()`: `#Separator` (what `build_hline_separator` /
`build_vline_separator` apply), `#HRule` / `#VRule` (the equivalent hand-rolled
spellings — `widgets/plate_progress.py`'s vertical rules, the Batch Export
divider that picks its orientation at run time, and
`datasets/_common/log_panel.hrule`), and the
`hline`/`vline` dynamic properties. They all paint `Colors.border_subtle` with
`border: none`, and `#HRule` / `#VRule` are pinned to one pixel in the QSS so a
caller need not also call `setFixedHeight(1)`. `widgets/mpl_toolbar.py` carries
a fifth, `#MplToolbarSep`, because that widget styles itself with a
per-instance stylesheet rather than reading the application QSS — it restates
the same fill and `border: none` there.

The `border: none` is the load-bearing part. A `QFrame` whose shape is
`HLine`/`VLine` otherwise paints Qt's default etched line, which is derived
from the widget palette rather than the theme. Measured against the default
theme:

```
    HLine + #Separator, pinned to 1 px   1 px tall, ink #1b2331 (border_subtle)
    HLine + #HRule                       1 px tall, ink #1b2331
    HLine, unnamed, pinned to 1 px       1 px tall, ink #e6e9ef
    HLine, unnamed, unpinned             3 px tall: #e6e9ef between two rows of background
```

So the ink is the wrong colour in every unnamed case — a pale `#e6e9ef` line
where the theme wants `border_subtle` — and a frame nobody pinned also reserves
three pixels of height for it. Several of the sites this rule displaced already
called `setFixedHeight(1)`, so for those only the colour was wrong.

**Build one with `ui_helpers.build_hline_separator(parent)` or
`build_vline_separator(parent)`** rather than by hand; both name the frame,
set its shape, and pin it to `theme.Metrics.hairline`. `build_hline_separator`
takes a `thickness` keyword for the one divider that is deliberately heavier
(the Image Table's shelf rule, at 2 px). `datasets/_common/log_panel.hrule` is
the Datasets-side twin, one helper for the six tool pages that build the same
log header. `tests/test_divider_naming.py` fails on any
`setFrameShape(QFrame.HLine|VLine)` whose frame is never given a sanctioned
name.

One divider chooses its orientation at run time: Batch Export's output panel
folds from side-by-side to stacked as the viewport narrows, and its rule turns
with it. It therefore cannot take a fixed name — `#HRule` pins `max-height`
and `#VRule` pins `max-width`, so either name applied to the other orientation
draws a hairline stretched along the wrong axis. `base_panel._apply_split_sep_orientation`
sets the shape and the name together and repolishes, because Qt's stylesheet
cache only re-evaluates a selector on an explicit `unpolish`/`polish` pair —
the same reason `widgets/_support.set_variant` exists for dynamic properties.

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

`widgets/_support.set_variant(widget, variant)` is the smaller, everyday
sibling of the same repolish problem: it changes a `QPushButton`'s
`variant` dynamic property at runtime and forces `style().unpolish()` +
`style().polish()`, so a `QPushButton[variant=...]` QSS selector picks up the
new value immediately. `setProperty("variant", ...)` alone does nothing
visible — Qt's stylesheet cache only re-evaluates property selectors on an
explicit repolish. It generalises the precedent
`well_viewer.core.plot_toggles.refresh_fov_btn_state` set (a checked-state
button variant flipping live the same way) so call sites don't each
reimplement the unpolish/polish pair by hand; every Datasets tool page uses it
to swap its Run/Open buttons between `primary`/`secondary` once a run
succeeds (§6b).

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
  (`fluor_gates` / `thresh_frac_on` / `thresh_count_on`); `recalculate_threshold`
  later in the load rebuilds the Cell-Gating rows before re-applying any
  persisted values.
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
`selection_controller._PER_WELL_TAB_NAMES` = **Home, Review CSV,
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
Plotting band QComboBox.currentIndexChanged
        │
        ▼
WellViewerApp._on_plot_channel_selected(source=band combo)
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

The Events (N-above) panel is the same rule applied to a count: a replicate
set's value is the **mean of its per-well counts** with the cross-well spread,
matching the Mean and Fraction panels beside it, on both plotting tabs and both
batch paths. `compute_rep_n_above_stats` is that statistic. A cross-well
**total** is a different number and is reporting-only — it is what the CSV's
integer `n_above_threshold` column has always carried, it comes from
`compute_rep_n_above_total`, and `tests/test_events_total_isolation.py` keeps it
out of anything that draws. The two are easy to substitute for one another by
name, and substituting them is what made a set divided by itself land on 1/n
rather than 1.0.

The bar and line CSVs therefore report both: `n_above_threshold` (the integer
count) alongside `n_above_replicate_mean`, `err_n_above_replicate_<band>` and
`n_above_replicate_unit` (`well` / `fov`, empty for a single well). The unit
column is not decoration — nothing else in the row records whether
Aggregate-FOVs was on, which would leave the mean and its spread ambiguous.

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
                            state.plot.fc_vs_control_on ─┐
User → Control combo  ─→    state.plot.fc_control       ─┼─→ fold_change.fold_change_state(ctx)
                            state.plot.fc_vs_t0_on      ─┘        → FoldChangeState
User → Baseline combo ─→ (sets fc_vs_t0_on)

core/fold_change_controls_view.set_fold_change_state(ctx, *, …, initiating_scope)
        │   mirrors the shape of channel_state_controller.set_active_channel:
        │   updates state → _sync_widgets_to_state →
        │     redraw_scopes_or_defer(app) (scope coalescer: visible scope's
        │     callable runs now, the rest are marked dirty)
        ▼
Line tab — lineplot_controller.redraw_line_plots(…) →
            collect_line_series(ctx, …) → list[LineSeries]
   1. Single source of truth, mirrors the bar plot's
      ``collect_bar_items``. Each member becomes a ``LineSeries``
      with a list of ``LinePoint``s (full AggPoint shape so the
      renderer and CSV writers consume the same record).
   2. fold_change.member_stats_series(ctx, fc.control, …) →
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
   5. After redraw, ctx.notify.set_status(…) surfaces a one-line warning
      naming any timepoints where the control had no sample.

Bar tab — barplot_controller.collect_bar_items(ctx, target_t,
                                                miss_sink=fc_misses)
          → list[BarItem]  (single source of truth)
   1. fold_change.control_mean_at_for_bar(ctx, ref, target_t, …)
      → single float (or None). Uses the same stat as the bar's
      numerator (mean-of-per-well-means for rep-set, per-FOV pool
      when Aggregate-FOVs is on).
   2. For each bar's (mean, spread): scale_bar_value(…).
   3. t0 baseline = member_first_tp_value(ctx, member_label, …) for
      rep-sets (same stat) or first_tp_value(pts) for per-well bars.
   4. If vs-control was requested but the resolver returned None, the
      affected bars are forced to NaN (no silent fall-back to raw values)
      and the target_t is recorded in miss_sink for the status warning
      surfaced by barplot_renderer.draw_grouped_bar_mode. The cross-axis
      stacking that runs when the baseline axis is also on is guarded on
      the same condition, in both the grouped and per-well branches — it
      divides by the control, so reaching it with an unresolved one raised
      a TypeError rather than drawing a placeholder.

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
   core/fold_change_controls_view.install_fold_change_controls(scope=…).
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

**A control is a reference, not a name.** `PlotState.fc_control` holds a
`domain/control_ref.ControlRef` — a frozen `(kind, name)` where `kind` is
`"well"`, `"set"`, or `"legacy"` — and `None` means no control. The kind is
carried beside the name rather than encoded into it because replicate-set names
are unrestricted free text: `selections_model._unique_name` strips whitespace
and ensures uniqueness, and reserves no character. A set may therefore be named
`A01 (well)`, or share a token with a loaded well, and no spelling of a single
string can tell the two apart. The combo still shows `A01 (well)` when a well's
token collides with a set name, but that suffix is display chrome: the combo
carries the reference as item data, matches its selection on it, and nothing
parses the text back.

`domain/fold_change.resolve_control` is the single place a control becomes
wells, returning `("set"|"well"|"", name)`. Every path that needs a denominator
goes through it — `resolve_control_wells`, `_label_to_replicate_set` and
`_member_series` are its callers rather than three implementations of the same
decision. A reference that states its kind is believed, so a hidden set can
never capture a well of the same name. Only a `"legacy"` reference — a bare
string, or a document written before the kind was recorded — falls through to
`classify_control_label`, which applies the precedence such a value was saved
under: suffixed-well first, then set name, then bare well token, with a set
whose wells are all unloaded falling through rather than claiming the label.

Persistence writes the object under `control` and keeps the plain token in the
older `control_label` key, so a rolled-back build finds a value it understands.
A legacy string is upgraded when it loads and written concrete on the next
save, which retires the ambiguity once per document. `ControlRef.display_name`
is the plain token and is what reaches an axis title, the `fold_change_control`
CSV cell and the saved-plot summary; the suffix never appears in a figure or an
export. The sample-definitions UI does not prevent the name collision at input
time, and does not need to.

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
        ├── estimate_thresholds_from_outputs(output_dir, …)
        │     └── _apply_thresholds_to_pipeline_info(output_dir, thresholds, log)
        │           ↳ pipeline_info.json["cell_gating"]["thresh_frac_on"]
        └── pipeline.completeness.record_run_check(…)
              ↳ pipeline_info.json["run_check"]
        │
        ▼
Process exits. Analyze tab offers the dataset to Review.
```

**The end-of-run completeness check.** `pipeline/completeness.py` holds one
definition of "is this well complete", shared by this check and the Datasets
**Validate & Repair** tool (§6b) — `datasets/validate/validate_compute.py`
delegates its union, its `intent - done` diff and its four health states here,
so a well cannot read complete on one surface and partial on another. What is
*not* shared is the scan: Validate parallelises across wells and layers
findings, evidence and repair on top, while `scan_run` walks a directory the run
has just written. The shared predicate takes data the caller already holds and
opens exactly one thing between its functions — the well's output container —
which is what lets Validate delegate without changing its per-well read count,
a number `tests/test_validate_compute.py` pins exactly.

The check runs after the auto-threshold pass and before the exit-code decision,
and records a `run_check` block per well: how many `(fov, timepoint)` groups the
input held, how many the measurements CSV covers, how many the CSV and the
output archive cover between them, which are missing, and the resulting health
state. **It takes no part in the exit code** — a plate that came up short is
recorded and warned about, but whether the *run* failed stays `failed_wells`'
question, which the remote layer depends on.

Three properties are load-bearing:

- **It reads artefacts, never `run_report`.** Its well list is the input map the
  run resolved (passed in, not re-derived — the run has already walked the input
  directory and logged what it skipped) unioned with whatever the output holds.
  A record built on `run_report` would describe an incremental run's 96-well
  plate as the three wells that happened to have new timepoints.
- **Two covered counts.** `covered_any` (CSV ∪ archive) is what an incremental
  re-run treats as done, so it is the honest denominator for "what would
  re-running recover"; `covered_csv` is what every plot actually reads. A well
  whose archive is whole and whose CSV is empty has full coverage by the first
  and none by the second, and one figure alone hides that.
- **`run_check` is deliberately not a `USER_SIDE_BLOCKS` key.** It describes one
  run's output, so carrying it across a sidecar rewrite would leave a run that
  died early holding the previous run's description. Losing a record is visible;
  inheriting a stale one is not.

Its presence is also the only honest end-of-run signal in the sidecar —
`pipeline_info.json` itself is written before any image work, so the file
existing says nothing about whether the run got anywhere. `analyze_tab_log`'s
last-run card reads the block for exactly that reason. Note that a **suspended**
run still reaches the check and writes a record: suspend exits through the
normal end-of-well path, so the block carries a `suspended` flag and the
shortfall is reported as a stop rather than shouted at as a fault.

`--no_run_check` turns it off, spelled negative-sense like `--no_auto_threshold`
because the GUI checkbox is the positive. Turning it off still writes a stub
recording that it was turned off: "the check was off" and "this dataset predates
the check" need different things from a reader, so there are three
distinguishable states on disk, not two. A fourth is a record whose `source` is
`validate` — the dataset was never checked by its run but has been scanned
since (§6b).

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

**The completeness record still covers the whole plate.** A well with nothing
new returns before writing a run-report entry, so `run_report` after an
incremental run names only the wells that had new data. The end-of-run check
(§6.2) does not read that block: it enumerates from the input map the run
resolved, unioned with what the output holds, and re-reads the artefacts. So a
run that touched three wells of ninety-six still leaves a `run_check` describing
ninety-six.

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
   `cell_gating.thresh_frac_on` (so established thresholds are stable), and
   carries `cell_gating.thresh_count_on` through untouched — it rebuilds the
   block from named keys, so a cutoff it did not read would be dropped on write.
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
- **A `logging.Handler` runs on whichever thread logged the record**, and in
  this app that includes every background thread there is: the remote runner's
  batch loop and its fetcher, the smFISH and cell-browser loaders, the gating
  `QThread`s, and every `swallowed()` breadcrumb any of them writes. So a
  handler that writes to a widget has to marshal, and `QTimer.singleShot(0, …)`
  is not a way to marshal even though it reads like one — the timer is created
  on the *calling* thread, so the callback never fires there (the line is lost)
  and destroying the abandoned `QObject` as the thread ends raises
  `QObject::killTimer: Timers cannot be stopped from another thread`. Both of
  the app's GUI log handlers go through `core.worker_bridge.GuiLogRelay`, a
  `QObject` built on the GUI thread whose queued signal may be emitted from
  anywhere. `tests/test_log_handler_marshalling.py` forbids the timer shape
  statically; `tests/test_log_handler_thread_safety.py` shows a worker-thread
  line arriving at both widgets.

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

6. **Install the application stylesheet once per module, never per test.**
   `QApplication.setStyleSheet` re-polishes every live widget in the process, and
   because rule 1's leak means that set only grows, a single call late in the fast
   lane costs tens of seconds. A function-scoped fixture that installs
   `theme.qss()` therefore multiplies that cost by the file's case count — enough
   on its own to triple the lane's wall time. Files that need the real sheet
   (`test_theme_roles_render.py`, `test_control_band_widget.py`,
   `test_disabled_controls_look_disabled.py`, `test_analyze_narrow_sidebar_fit.py`,
   `test_analyze_help_section.py`) install it from a `scope="module"` fixture and
   pay for it once. Applying the sheet to a single host widget instead
   (`test_control_row_spacing.py`) is cheaper again, and is the right shape
   whenever the assertion does not need it applied app-wide. `--durations=N` is
   how this shows up: the cost lands in `setup`, not in the test body, and the
   same file run on its own is fast because almost nothing is alive yet.

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

### 11.6 Give a new page its help

Adding a Review tab, a Datasets tool, or an Analyze form section means adding
two things: a section in `_Docs/Markdowns/README_WellViewer.md`, and an entry in
the matching map in `help_topics.py` whose value is that heading's exact text.
Without both, the drawer opens on the mode overview for that page and nothing
says so.

For a **Datasets tool or an Analyze form section**, `tests/test_help_topics.py`
fails until they are there: it reads the Datasets roster out of `datasets_tab.py`
and the section titles out of `analyze_tab_form.py`, so neither can be added
without the guard noticing.

For a **Review tab** nothing enforces it. Review leaves are resolved at run time
through `tab_controller.current_centre_tab` and `SECTION_BY_PAGE` deliberately
keeps loose aliases for renamed and prefixed ones, so there is no roster to check
a key against. A new Review tab without a `SECTION_BY_PAGE` entry silently opens
the drawer on the Review overview, and the fast lane stays green. Add the entry
by hand.

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
   the current dataset's channel list and that the Plotting band combo's
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

`boot_diag` is imported before PySide6 and arms `faulthandler` onto a per-launch
**boot log** — `%LOCALAPPDATA%\allwell\allwell-boot.log` on Windows,
`~/.cache/allwell/allwell-boot.log` elsewhere — so a hard native crash dumps a
C/Python stack there even from a windowed build with no console, and a *hang*
leaves its last-reached milestone as the log's final line. `install_excepthook`
writes any uncaught Python exception to the same file.

**A log that recorded a crash is kept.** The boot log is truncated per launch so
it describes the current attempt, which is what makes it useful after a hang.
That erased the evidence in the one case it mattered most: the thing a user does
after a crash is relaunch, and the relaunch deleted the traceback the crash
dialog had just told them to read. `_rotate_if_it_recorded_a_crash` therefore
moves a log carrying a crash marker (`FATAL uncaught exception`, or one of
`faulthandler`'s) aside to `allwell-boot.log.1` … `.CRASH_LOG_KEEP` first;
`crash_log_paths()` lists the survivors, newest first. Logs from launches that
ended quietly are still overwritten, so a dozen ordinary restarts cannot push
the one crash out of the ring. The crash dialog also carries the traceback in
its "Show Details" section, so the usual case needs no file at all — and it no
longer says "failed to start", which was untrue for every crash that happened
after start-up.

Run from a terminal to capture the same output live. These usually come from touching a Qt widget tree mid-event-dispatch or after
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
| **Replicate set** | A group of wells treated as replicates for a single condition. On Home → Groups a group **is** exactly one replicate set — its `wells` are its replicate members (or a single well); there is no `replicates` sub-partition (the field is reserved/`None`). "Quick Replicates" auto-builds groups of a chosen size N (spinbox) along rows/columns. (The separate batch-export `BarGroup` — from a user-picked `bar_groups.json` — can still hold multiple member sets + solo wells; that is a distinct feature.) |
| **`pipeline_info.json`** | The sidecar file written by `process_microscopy.py` next to the per-well CSVs. Holds schema, channel tokens, available timepoints, persisted selections, gating thresholds. |
| **Well token** | Two-letter / two-digit well identifier (`A01` … `H12`). Always uppercase, always zero-padded. |
| **fov, tp** | Field-of-view and timepoint extracted from a filename via the schema-aware extractor in `viewer_state.make_schema_extractor`. |
| **Split View** | Optional mode (§5.10) showing two different Review tabs side-by-side, both live. All machinery is in `core/split_view.py`, wired by one `install_split_view(app)` call (comment it out → feature gone). |
| **Provider hook** | A module-level callable on `tab_controller` (`split_focus_provider` / `split_visible_provider` / `split_nav_intercept`), `None` by default, that an optional feature registers to extend a core resolver without the core importing the feature. The seam that keeps Split View one-line removable. |

---

## 15. Conventions and gotchas

- **Never silently swallow exceptions.** Don't write `except Exception: pass`.
  Use `well_viewer.core.safe.swallow(...)` / `swallowed(...)` (GUI side) or
  `aw_safe.swallow(...)` / `swallowed(...)` (pipeline / `services` / `widgets`,
  none of which may import `well_viewer`) so a suppressed failure still leaves a
  DEBUG breadcrumb. Enforced by `tests/test_no_silent_except.py`, which guards
  `well_viewer/`, `widgets/`, and the pipeline modules. (Two near-identical
  helpers exist on purpose: the pipeline must not import `well_viewer` — see
  §6.2.) `widgets/` is the newest of the three and was the last un-guarded body
  of GUI code: a widget that quietly fails to paint or to recolour is
  indistinguishable from one that was never asked to.
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
  resolver and not a well to the pipeline that then processed it. A local copy
  drifts in both directions at once — the ones this rule displaced rejected a
  trailing space and a lower-case `a1/` directory that the canonicaliser
  accepts, while accepting an `A0` or `A13` that it rejects.
  `tests/test_well_token_single_parser.py` fails on any *whole-token* well
  pattern outside `util/well_token.py`. Two neighbouring shapes are deliberately
  not caught by it, because they are different operations: a **trailing**-token
  extractor reading a well off the end of a longer stem
  (`domain/viewer_state.extract_well_token`, `datasets/_common/dataset_paths.split_stem_well`),
  and a **filename-shape** matcher (`<WELL>_out.zip` in
  `data/image_discovery.py`) — which still hands the token it captures to the
  canonicaliser rather than padding it itself. Log-line scrapers in `services/`
  are the third: they recover a well from a message the pipeline already
  formatted.
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
  **Emptiness is decided on `None`, never on the value's truthiness.** Both
  `parse_hours` and `sort_key` stringify through `util.timepoint._token`, the
  stdlib-only twin of `data.image_identity._raw_token`, for the reason that rule
  exists at all: a numeric `0` is falsy, and `0` is the timepoint every time
  course starts at. Under `str(value or "")` a baseline arriving as a number
  rather than as text read as no token at all, which each consumer then
  expressed in its own vocabulary — `parse_hours` answered "unreadable",
  `sort_key`'s `(unparseable, …)` tuple put it *after* the twelve-hour column
  with the opaque labels, and `process_microscopy._parse_timepoint_hours`
  returned the `nan` its `h != h` callers read as raw-passthrough. The
  duplicated helper is the cost of this module's stdlib-only contract; both
  copies say so.
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
| Pipeline self-sufficiency — no `well_viewer`/`widgets`/Qt anywhere under `pipeline/` or `util/`, nor in `process_microscopy.py` / `WellPlateZipper.py`, at any nesting (§6.2). Scoped by directory walk rather than a file list, so a new module in either package is covered from the moment it exists | `tests/test_architecture_contracts.py::test_pipeline_is_self_sufficient` |
| Every repo-root module the pipeline imports is uploaded to the remote (§6.2) | `tests/test_remote_manifest.py` + `test_remote_runner.py::test_required_files_list_is_what_we_promised` |
| `services/` stay Qt-free (CLI-reusable, §13) | `test_architecture_contracts.py::test_services_have_no_qt_imports` |
| `widgets/` carry no app coupling (§13) | `test_architecture_contracts.py::test_widgets_do_not_import_the_app` |
| Controllers reach the app via the `app` arg, not by importing `WellViewerApp` (§9) | `test_architecture_contracts.py::test_nothing_imports_wellviewerapp_at_runtime` |
| EventBus is a capped catalogue of exactly 5 signals (§9) | `test_architecture_contracts.py::test_event_bus_signal_catalogue_is_capped` |
| Feature-module naming scheme (§3) | `test_architecture_contracts.py::test_feature_module_naming` |
| One definition of "is this well complete" — no module outside `pipeline/completeness.py` spells out the dataset-health states as its own roster, so the states a plate legend paints cannot diverge from the states the classifier returns (§6.2, §6b) | `tests/test_no_second_completeness_predicate.py`, with its bite cases in `tests/test_guards_bite.py` |
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
| Never hardcode a colour or a pixel size (§3, §8) — allowlisted for colormap/palette data and the `__main__` demo blocks; `datasets/` is out of scope while its tool pages consolidate | `tests/test_hardcoded_tokens.py` |
| Every divider frame carries a name the QSS styles, so it paints a 1 px `border_subtle` hairline rather than Qt's 3 px etched default (§8) | `tests/test_divider_naming.py` |
| A well token is judged by `util.well_token`, never a local whole-token regex (§15) | `tests/test_well_token_single_parser.py` |
| No `logging.Handler.emit` creates or arms a Qt timer — `emit` runs on the thread that logged the record, so a timer built there never fires and its teardown raises `killTimer` from another thread. Handlers marshal through `core.worker_bridge.GuiLogRelay` (§10.1) | `tests/test_log_handler_marshalling.py` (static) + `tests/test_log_handler_thread_safety.py` (behavioural), with bite cases in `tests/test_guards_bite.py` |
| `pipeline/`, `util/`, `services/` and `domain/` import no Qt and no `widgets` at any nesting; `data/` too, but for one allowlisted module (§5, §13) | `tests/test_qt_free_layers.py` |
| No Datasets tool imports another Datasets tool, so each stays independently deletable (§6b) | `tests/test_datasets_tool_isolation.py` |
| Every Datasets dashboard ends a run through the shared outcome setters rather than its own copies (§6b) | `tests/test_datasets_dashboard_outcomes.py` |
| Every Datasets tool controller owns its worker through `DatasetToolController`, and the one tool whose worker cannot be interrupted still reaches its own stop method (§6b) | `tests/test_datasets_controller_lifecycle.py` |
| The plot-card roster is written once — the shell reads `plot_card_actions.PLOT_CARD_ATTRS` rather than restating the names (§5.3) | `tests/test_plot_card_roster.py` |
| The Analyze layout readout renders `plan_input_layout`'s message rather than composing its own sentence about the folder, so the preview and the run cannot disagree (§6.1) | `tests/test_layout_readout_single_source.py` |
| Every plot path reaches its ON cutoff through `on_cutoff_for_column` rather than a raw per-channel accessor, so a spot tally is never measured against a fluorescence threshold (§5.6). An allowlist keyed on `(file, dotted enclosing function)` — the accessors stay reachable for the resolver and the Cell Gating rows, and the dotted chain stops a closure inheriting an exemption granted to a same-named closure elsewhere in the module — paired with two liveness tests: one fails on an entry naming no real reach, the other on a guarded name no module defines | `tests/test_on_cutoff_sweep.py` |

What is **not** statically enforced (and so relies on review + behavioural
tests): "never block the UI thread", route redraws through the `WellViewerApp`
shim, lazy-tab-build `hasattr` guards, app-wide-selection discipline, the
bar-math / fold-change invariants, and the Playground↔pipeline parity (which has
its own dedicated parity test).

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

**A large Image Table grid gets slower rather than failing.** — Decoded frames
live only in the bounded decode cache, sized from installed RAM. A grid whose
pixels exceed that budget re-decodes evicted frames on each pass, so a plate-wide
walk of large frames takes longer to Generate; it no longer grows resident memory
with the grid, which is what previously turned a big grid into failed allocations
and cells that would not load. If a grid is slow, narrow the well / timepoint
selection or crop the view.

**A well has fewer fields of view than were imaged.** — Its FOV-groups failed
during processing; the well still wrote a complete-looking CSV from the ones that
succeeded. `ls <output_dir>/errors/` names every failed (well, field, timepoint)
with its exception type, and `cat` on one gives the traceback. The most common
cause is the scratch volume filling mid-run (§6.2) — the message reports the free
space at the moment of failure. Recover with `--incremental`, which reprocesses
only the (fov, timepoint) groups missing from each CSV and merges them in; the
rows already present are correct, so nothing needs deleting.

**A well's error bars are missing and its bars carry `n/a`.** — Nothing was
computable for that spread. Either the well has one replicate (one field with
FOV errors on; one above-threshold cell without), or its FOV labels don't
identify its fields — check the status line and log at load, which name any well
in the second state. `n/a` is deliberately distinct from a bar with no marker at
all: the marker means "not computed", its absence with error bars on means the
spread is genuinely tiny.

**A whole well's per-field numbers are blank.** — Its `fov` column carries no
usable labels, so the fields pooled into one. The cause is upstream: the image
filenames don't split into as many tokens as `--filename_schema` has fields, so
the pipeline wrote an empty FOV for every row (it now warns once per filename
shape during the run). The well's mean and fraction are still valid — they don't
depend on the field labels — but anything per field is undefined until the CSVs
are rewritten with a schema that matches the filenames.

**An Image Table cell reads `(no mask)`.** — That cell's ≥threshold or mask view
has no segmentation mask for its (well, timepoint, FOV). The Image Table renders
nothing rather than showing the intensity image in its place, so a threshold view
is never half silhouettes and half pictures. Check that the well's output carries
a `*_labels.tif` for that frame, and that its FOV / timepoint tokens match the
fluorescence filenames — the mask and the image resolve through the same matcher,
so a mask that is missing here is genuinely absent or named differently.

**Cells that read `(no image)` come back on the next Generate.** — Empty scans are
not cached across passes, so a source that stalled (an external drive spinning
down, a directory still being written) only costs the pass it happened in. Press
Generate again rather than reloading the dataset; the log records each empty scan
and each discovery failure with the well and channel.

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
