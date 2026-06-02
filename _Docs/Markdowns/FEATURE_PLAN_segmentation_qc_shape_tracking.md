# Feature Plan: Label-free Segmentation · QC Tooling · Shape Features · Cell Tracking

> **Status:** design / implementation plan. Nothing in this document is built yet.
> **Scope:** one combined plan for four features that share infrastructure. Read the
> **Shared infrastructure** section first — the four features deliberately reuse one
> regionprops pass, one background reference, one PyTorch stack, one sidecar contract, and
> one plot-consumption path, so they are planned together rather than in isolation.
>
> **Audience:** whoever implements these later. Every integration point cites a real file
> path and line range (verified against the current source). All work must respect
> `ARCHITECTURE.md` — the relevant constraints are restated under **Architecture rules**.

---

## 1. Why these four

A comparison of `allwell` against commercial / open platforms (Cytely, CellProfiler,
Harmony, HALO, Cellpose) surfaced four gaps:

1. **Label-free segmentation** — today segmentation is StarDist-only, which needs a
   fluorescent nuclear stain. Brightfield/phase-only experiments can't be processed.
2. **QC tooling** — no debris removal and no illumination flat-field correction; only the
   single top-hat background option exists.
3. **User-selectable shape features** — only `area_px` + intensity stats are emitted;
   competitors emit dozens of morphology features per cell.
4. **Cell tracking across time** — timepoints exist but cells are not linked frame-to-frame
   (no lineage, motility, or single-cell time courses).

This plan covers all four. Features 1–3 are buildable now; feature 4 (tracking) is a
detailed design only.

---

## 2. Architecture rules every part of this plan obeys

From `ARCHITECTURE.md`, verified against the source:

- **The pipeline stays Qt-free (§6.2, §3).** `process_microscopy.py` must not import
  `well_viewer/`, `widgets/`, or Qt — it runs headless on CI / SSH workers. All new
  pipeline code (Cellpose, classical segmentation, flat-field, BaSiC, debris, regionprops)
  lives in the pipeline. Anything **both** the pipeline and the GUI must agree on goes in a
  **new Qt-free repo-root module**, modeled on the existing `auto_threshold_core.py` /
  `well_token.py`.
- **Shared contracts live in one place.** The shape-feature catalog (§5) goes in a new
  repo-root `shape_features_core.py`, imported by both the pipeline (to compute/emit) and
  the GUI (to detect/label), so the two cannot drift.
- **No new loose `app._*` attributes (§5.1).** A CI guardrail
  (`tests/test_guardrails.py`) budgets the count and only ratchets down. Review-side
  widgets register through `ctx.widgets.register(...)`; feature state lives in a
  feature-local `state.py`; tab builders read `ctx.app` / `ctx.state`
  (`TAB_CTX_APP_UNWRAP_BUDGET = 0`). The per-tab sidebar frames (`app._sidebar_*_frame`) are
  an **existing** exception that the tracking feature reuses rather than inventing a new
  attribute family.
- **The smFISH worker is the canonical CSV-rewriting post-pass (§5.5, §15).** Any
  background job copies it exactly: a `well_to_csv` map, keys via
  `data/image_identity.CellKey`, additive-column write-back, `atomic_write_text`, and an
  after-CSV callback marshalled through a **queued `Signal(object)` bridge** — never
  `QTimer.singleShot` from the worker thread (§5.5 documents that exact dropped-refresh
  bug).
- **Tabs follow §11.1, sidebar frames §11.2, on-disk state §11.3** — a
  `persistence/<name>.py` `load_from_data_dir` / `save_to_data_dir` pair registered with
  the `PersistenceRegistry`, atomic writes via `persistence/_io`, and an mtime fingerprint
  against `<well>_out.zip` so a pipeline re-run drops stale state (§5.7).
- **No hardcoded colours or sizes (§8, §15).** Reuse `widgets/` (`ChipGroup`, `Stepper`,
  `ToggleSwitch`, `SegmentedControl`) and `theme` tokens.

---

## 3. Shared infrastructure (the synergy spine)

The four features are planned together because they share these pieces. Each feature
section below references this spine instead of re-describing the plumbing.

| Shared piece | Defined in | Reused by |
|---|---|---|
| **One `skimage.regionprops_table` pass per FOV** | §5 (shape features) | QC debris removal (§4), shape features (§5), tracking centroids (§6) |
| **`shape_centroid_row/col` always computed** | §5 | tracking links these across timepoints (§6) — shape features unlock tracking for free |
| **Empty/background-well reference** | §4 (B1) | background subtraction *and* `empty_well` flat-field (§4 B2) |
| **PyTorch stack** | §3.A Cellpose | trackastra (§6) — choosing Cellpose lowers the DL-tracker's marginal cost |
| **`pipeline_info.json` config record** | §4/§5 | one place records segmentation engine + `qc_options` + `shape_features`; the GUI reads it via `domain/viewer_state.read_pipeline_info` |
| **`resolve_value_series` + dropdown path** | existing `data/data_loading.py:225-286` | shape columns (§5) and tracking's `track_id` grouping (§6) flow into existing plots unchanged |
| **`shape_features_core.py` (new, Qt-free)** | §5 | the one shared contract underpinning shape features and tracking |

---

## 4. Feature 1 — Label-free segmentation

Add two new `--segmentation_method` choices alongside the existing `stardist_nuclei` /
`stardist_seeded_watershed_cell`. The dispatch lives in `process_image_group` at
`process_microscopy.py:1401-1427`.

### 4.1 Engines (both, user-selectable)

- **`cellpose`** — deep-learning label-free segmentation using the Cellpose `cyto3` model.
  Add `_segment_cellpose(image, model)` next to `_segment_stardist_nuclei`
  (`process_microscopy.py:1002`). Load the model **once per worker** in `_worker_init`
  into a new guarded global `_CELLPOSE_MODEL`, mirroring the StarDist cache
  (`_STARDIST_MODEL`). Optional `--cellpose_diameter` (0 = auto-estimate).
- **`classical_threshold`** — Otsu threshold + distance-transform watershed, reusing
  `auto_threshold_core` (Otsu) plus `scipy.ndi.distance_transform_edt` and
  `skimage.segmentation.watershed` (already imported for the seeded-watershed mode at
  `process_microscopy.py:1046-1054`). No new dependency.

### 4.2 Wiring

- Add both names to the `if/elif/else` segmentation dispatch (`:1401-1427`).
- Add both to the argparse `choices` in `build_parser` (`:3119`).
- Thread the new option through `process_image_group(...)` (signature `:1321-1338`).
- For label-free, `--nuclear_token` is reused as the **segmentation channel** (the
  brightfield/phase channel). Intensity measurement on the fluorescence channels is
  unchanged.
- GUI: extend `_seg_method_cb` items (`analyze_tab.py:294-299`) and the hint/visibility
  logic in `_refresh_segmentation_hints` (`:707-724`) so the labels make sense in
  label-free mode (e.g. "Segmentation channel" instead of "Nuclear channel").

### 4.3 Dependencies

- Add `cellpose` (pulls PyTorch) to `_Docs/requirements.txt` **and**
  `_Docs/requirements-remote.txt` — headless workers segment too (§6.2).
- **Guard the import** so the app and pipeline run without Cellpose installed; raise a
  clear error only when the `cellpose` method is actually selected.
- Add Cellpose/torch submodules to the PyInstaller spec `hiddenimports`
  (`_Docs/_Installation/all_well.spec`, §12.7).

---

## 5. Feature 2 — QC tooling

All correction steps slot into the per-image preprocessing block around the existing
top-hat calls (`process_microscopy.py:1372-1394`; `apply_tophat` is `:958-972`). They
**compose with** top-hat rather than replacing it. Order of operations:

```
raw → background-well subtract → flat-field correct → top-hat (existing) → segment / measure
```

Each step is independently toggleable.

### 5.1 B1 — Empty / background-well reference

- New args: `--background_well <token|path>` and `--background_mode {subtract,divide,auto}`.
- New helper `load_background_reference(...)` resolves the empty well's images per channel
  (pipeline-side input handling, reusing the existing zip/folder discovery) and builds a
  per-channel mean reference image.
- `subtract` → `clip(image - bg, 0)`. `divide` → flat-field-style normalize. `auto` picks
  based on whether the reference looks like a dark background or a uniform field.

### 5.2 B2 — Illumination flat-field correction

`--flatfield {none,empty_well,gaussian,basic}`, all applied through one
`apply_flatfield(image, field, mode="divide")`:

- **`empty_well`** — use the B1 reference as the shading field (divide). Shares the
  reference from B1.
- **`gaussian`** — per-FOV self-estimate: a large-σ `scipy.ndi.gaussian_filter` of each
  image approximates the illumination field; divide by the normalized blur. No new
  dependency; a good default when there's no empty well and no BaSiC.
- **`basic`** — **BaSiC** (`basicpy`) retrospective estimator: estimate the flatfield /
  darkfield from the per-channel image stack. Estimation is global per channel — run it
  **once** in `main()` near the auto-threshold pass (`:3527-3560` region), cache the field,
  and pass it into the workers (not recomputed per FOV).

Dependency: add `basicpy` to `_Docs/requirements.txt` + `requirements-remote.txt` + spec
`hiddenimports`; guard the import (only needed when `--flatfield basic`). `empty_well` and
`gaussian` need no new dependency.

### 5.3 B3 — Debris removal

- `--qc_remove_debris` plus optional `--debris_min_area`, `--debris_max_area`,
  `--debris_min_solidity`.
- A post-segmentation label filter that drops labels whose area / solidity / intensity fall
  outside the bounds. Implement as `filter_debris(labels, props)` applied right after
  segmentation (after `:1427`, before the measurement loop at `:1504`), **reusing the
  regionprops pass from §6** so debris metrics are computed once.

### 5.4 GUI + sidecar

- New **"Quality Control"** section in `analyze_tab.py`, built with the existing
  `_section` / `_row` helpers: a debris checkbox, a flat-field combo
  (`none / empty_well / gaussian / basic`), a background-mode combo
  (`subtract / divide / auto`), and a **Browse…** background-well picker following the
  input-folder picker pattern (`analyze_tab.py:374-393`). The background-well picker is only
  required for `empty_well` flat-field or background subtraction.
- Extend `_collect_run_options` (`:992-1022`) with the new keys and
  `services/pipeline_service.build_pipeline_args` (`:23-104`) with the new flags (boolean
  flags via `if opts.get(flag): args.append(...)`, value flags appended conditionally).
- Record a `qc_options` block **directly in the fresh `info` dict** in `write_pipeline_info`
  (`:3056-3082`). **Not** the preserved-keys loop at `:3092-3099` — that loop is for
  GUI-written user state (`cell_gating`, `sample_definitions`, `ratios`, `notes`), whereas
  `qc_options` describes *this run's* configuration and should be written fresh each run.

---

## 6. Feature 3 — User-selectable shape features

### 6.1 Shared catalog — `shape_features_core.py` (new, repo root, Qt-free)

A single `SHAPE_FEATURES` mapping each feature key to `(regionprops property, friendly
label, value-kind)`. Candidate features (skimage `regionprops` names):

`area`, `perimeter`, `eccentricity`, `solidity`, `extent`, `major_axis_length`,
`minor_axis_length`, `orientation`, and a derived `circularity` (`4π·area / perimeter²`).

**`centroid_row` / `centroid_col` are always computed**, regardless of the user's
selection — they are the per-cell input the tracking feature (§7) links across timepoints.

This module is imported by both `process_microscopy.py` (to compute/emit) and the GUI (to
detect/label), so the two sides cannot drift — the same pattern `auto_threshold_core.py`
uses.

### 6.2 Analyze checkbox list → args

- New **"Shape Metrics"** section in `analyze_tab.py` using the existing
  `widgets/chip_group.ChipGroup(exclusive=False)` multi-select widget
  (`addChip(label, data=key)` / `checkedData()`), with a sensible default subset checked.
- Extend `_collect_run_options` → `shape_features=self._shape_chips.checkedData()`.
- `services/pipeline_service.build_pipeline_args`: append
  `--shape_features <k1> <k2> ...` when non-empty (mirrors the `--smfish_tokens` list
  pattern).
- `process_microscopy.build_parser`: add `--shape_features nargs="*" default=[]`; thread it
  through `process_image_group`.

### 6.3 Compute + emit columns

- `compute_shape_features(labels, selected)` via `skimage.measure.regionprops_table`,
  computed once per FOV beside the intensity-stats loop (`process_microscopy.py:1516-1527`).
- Merge into each cell record in the loop at `:1529-1541` with a channel-independent
  **`shape_` prefix**: `shape_eccentricity`, `shape_solidity`, `shape_centroid_row`, …
  Shape features describe the mask, not a fluorescence channel, so they deliberately do
  **not** use the `<channel>_<metric>` convention.
- The CSV writer needs no change — `_write_single_csv` derives columns from
  `records[0].keys()`.
- Record the selected list as a top-level `shape_features` key in the fresh `info` dict in
  `write_pipeline_info`.

### 6.4 Review dropdowns — shape features as a sectioned virtual-channel group

Shape features are channel-independent, so model them like ratio channels (the existing
virtual-channel precedent) but under their own dropdown section.

- **Detect:** add `detect_shape_features(df)` in `well_viewer/data/data_loading.py`,
  sibling to `detect_fluor_channels` / `detect_smfish_channels` (`:147-162`); also surface
  the `shape_features` list from `pipeline_info.json`. (`shape_*` columns are already
  numeric-coerced and not in `_STRING_COLS`, so no `_METRIC_SUFFIXES` change is needed.)
- **Labels:** pull friendly labels from `shape_features_core` in
  `well_viewer/data/metric_labels.py`; teach the threshold-wording helpers
  (`threshold_qualifier` / `fraction_title`, `:81-106`) a shape branch that bypasses the
  "above threshold" phrasing (same shape as the existing `smfish_count` branch).
- **Sectioned combo:** add a new helper
  `ui_helpers.set_combo_values_sectioned(cb, sections)` using a `QStandardItemModel` with
  disabled, non-selectable header rows ("— Shape Features —"). No sectioned combo exists
  today (all use `addItems`), so this is the one genuinely new widget helper. Apply it where
  `update_channel_selector` populates the channel combos
  (`channel_state_controller.py:278-401`); real channels and ratio channels keep their
  current entries, and shape items append under the header.
- **Metric collapse:** when a shape feature is the active selection, collapse the metric
  combo to a single fixed entry (exactly like ratio channels via `CALCULATED_VAL_LABEL` in
  `populate_metric_combo`, `:538-602`). Set `active_val_col` directly to the
  `shape_<feature>` column.
- **Aggregation:** in `data/data_loading.aggregate_with_threshold_df`, treat shape metrics
  like `smfish_count` and pass `threshold=-1.0` so every cell contributes (shape features
  have no gating threshold) — the same rule §5.6 of the architecture documents for counts.
- **Scatter gate:** in `features/scatter/{controller,callbacks}.py`, **bypass the
  `fluor_gate_x/y` intensity floor for `shape_*` axis columns**, exactly as is already done
  for `_smfish_count` axes (§5.5). Otherwise the intensity gate would wrongly strip cells on
  a morphology axis.
- **Plots otherwise unchanged:** `resolve_value_series` (`:225-286`) consumes `shape_*` as
  plain columns, so line / bar / scatter / distribution / stats just work.

---

## 7. Feature 4 — Cell tracking subsystem (design only)

Tracking is a first-class subsystem surfaced as a **new sidebar/rail option that spans both
panes**:

- **Analyze pane** — an optional **"Cell Tracking"** section in the analyze form: an enable
  toggle, an engine picker, and core parameters, so a pipeline run can **chain tracking
  headlessly**. `process_microscopy.main()` gains a `--track …` post-pass that writes
  `track_id` during the run. This is the "fire-and-forget compute" entry point.
- **Review pane** — the **interactive "Tracking" parent tab** (a new top-level rail entry =
  the "sidebar option"), with its own left `_sidebar_tracking_frame` and a nested
  `NamedPageStack` of sub-tabs for **view / lineage / edit / prune / analytics / export**.
  This is where tracks are inspected, corrected, pruned, and plotted, and where tracking can
  be **re-run** with new parameters without re-segmenting.

The two panes share one engine layer and one on-disk format. The Review side is primary
because editing and pruning live there.

### 7.1 Data model & on-disk format

- **Per-cell columns (additive), written back into each per-well CSV:**
  - `track_id` (int; `0` / `-1` = untracked).
  - `track_parent_id` (lineage parent track, `-1` if none).
  - `tree_id` (lineage-tree / root id grouping a cell and all its division descendants).
- **`tracks.json` sidecar** (new; modeled on `cell_overrides.json`) holds the lineage
  **graph**:
  - `nodes` — each a `CellKey` tuple `(well, fov, tp, nid)` + frame index + centroid.
  - `edges` — parent→child links within / across frames.
  - `tracks` — `track_id` → ordered node list + per-track summary metrics.
  - `lineage` — `track_id` → parent `track_id`.
  - `params` — engine + settings used to produce it.
  - **mtime-fingerprinted** against each `<well>_out.zip` so a pipeline re-run drops stale
    tracks on load (the exact `cell_overrides.py:_well_zip_fingerprints` pattern).
- **Identity:** every node is built via `data/image_identity.CellKey`. Tracking links cells
  by holding `(well, fov)` fixed and varying `tp`, matching `nid`s frame to frame. Per-FOV
  by default, configurable to stitch across FOVs later.
- **Persistence:** new `well_viewer/persistence/tracking.py` with `load_from_data_dir(app)`
  / `save_to_data_dir(app)`, registered in
  `persistence/registry.build_persistence_registry`; `atomic_write_json`; debounced via
  `app._persistence.schedule_save("tracking")` after edits.

### 7.2 The three engines — selection & behavior

All three sit behind one adapter interface in a new
`well_viewer/features/tracking/engines/` subpackage (guarded imports — the app runs without
any of them; an engine errors only when selected):

```python
run(frames: list[FrameDetections], params: EngineParams) -> TrackResult
#   FrameDetections = per-tp centroids (+ optional label mask, + optional features
#                     area/intensity/shape pulled from the per-cell CSV / <well>_out.zip)
#   TrackResult     = node -> track_id map, lineage edges, per-track metrics
```

Engine selection is a `SegmentedControl` / combo at the top of the **Setup & Run** sub-tab
(mirrored in the Analyze section). Choosing an engine swaps a **conditional parameter
panel** below it (stacked `QWidget`s shown per engine, like `_refresh_segmentation_hints`),
so only the relevant controls appear. A prerequisite hint chip ("Needs masks" / "Needs
PyTorch") shows requirements, and the Run button is disabled with an inline reason if a
dependency is missing.

#### laptrack — pure-Python LAP; recommended lightweight default

Pandas-native, fits the existing CSV + centroid columns with zero heavy dependencies.

- **Input:** centroid columns straight from the per-cell CSV (`shape_centroid_row/col`).
- **API:** `LapTrack(track_cost_cutoff, gap_closing_cost_cutoff,
  gap_closing_max_frame_count, splitting_cost_cutoff, merging_cost_cutoff,
  track_dist_metric)` → `predict_dataframe(df, ["centroid_row", "centroid_col"],
  frame_col=…)` → `(track_df, split_df, merge_df)` with `track_id` + `tree_id`.
- **UI controls:** **Max linking distance** (px → `track_cost_cutoff = d²`), **Max gap
  frames** (`gap_closing_max_frame_count`), **Gap-close distance**, **Allow splitting**
  (toggle → `splitting_cost_cutoff`), **Allow merging** (toggle → `merging_cost_cutoff`). An
  **OverLapTrack** toggle switches to mask-IoU linking (loads `*_labels.tif` from
  `<well>_out.zip` via `zipfile_cache`) for dense / touching cells.
- **Behavior:** instant on typical well sizes; no model download; division shown via
  `tree_id`.

#### btrack — Bayesian; recommended when mitosis / lineage matters

Native division → true lineage trees.

- **Input:** centroids (+ optional features area/intensity/shape from the CSV) as
  `btrack.btypes.PyTrackObject`s; `tracker.volume = ((0, W), (0, H))` from the image shape.
- **API:** `with BayesianTracker() as t: t.configure(cfg); t.append(objs); t.track();
  t.optimize(); tracks = t.tracks` (each `track.ID / parent / root / children`; lineage
  graph via `t.lbep`).
- **UI controls** (grouped, advanced-collapsible): **Motion** — `max_search_radius`,
  `accuracy`, `prob_not_assign`; **Update method** — `EXACT` / `APPROXIMATE` (APPROXIMATE
  for big FOVs); **Hypotheses / division** — enable branching + the
  `P_init / P_term / P_link / P_branch / P_dead` weights with sane presets; **Global
  optimize** toggle (`.optimize()`). A **"Load config…"** button accepts a btrack
  `cell_config.json`; defaults are shipped.
- **Behavior:** slower (the optimize step); richest lineage; feeds the **Lineage Tree**
  sub-tab directly.

#### trackastra — transformer; recommended for hard data / when PyTorch is already present

Pretrained, little tuning; pairs with Cellpose's torch (§3.A synergy).

- **Input:** raw images **and** integer label masks per frame (both from `<well>_out.zip`;
  masks = `*_labels.tif`), assembled into `(T, Y, X)` stacks.
- **API:** `model = Trackastra.from_pretrained("general_2d", device=…)`;
  `graph, masks_tracked = model.track(imgs, masks, mode=…)`; convert with `graph_to_ctc` /
  napari-tracks helpers.
- **UI controls:** **Pretrained model** combo (`general_2d`,
  `general_2d_w_SAM2_features`, or a local-folder picker), **Device**
  (`automatic` / `cuda` / `mps` / `cpu`), **Linking mode** (`greedy_nodiv` /
  `greedy` (division) / `ilp` (needs `motile` — disabled with a hint if absent)). The first
  run downloads weights (progress in the status dot + log drawer).
- **Behavior:** GPU-accelerated; best on crowded / ambiguous data; division via `greedy` /
  `ilp`.

**Dependencies:** `laptrack` is lightweight and always installable; `btrack` and
`trackastra` (+ torch, + optional `motile`) go in `requirements*.txt` + spec
`hiddenimports`, guarded. The `track_id` write-back and the worker are engine-agnostic.

### 7.3 Where it runs — the engine-agnostic worker

A **separate post-pass `QThread` worker** — `features/tracking/worker.py` — **not** inside
`process_image_group` (which is per-FOV; tracking needs all timepoints of a well at once).
It copies the smFISH worker contract verbatim (`features/smfish/worker.py` +
`tab_view.py:50-115`):

- Launch on a `threading.Thread(daemon=True)`; per-well `ThreadPoolExecutor`; a
  **`_WorkerBridge(QObject)`** with `status` / `done` / `after_csv = Signal(object)`
  marshals callbacks back to the GUI thread (never `QTimer.singleShot` from the worker
  thread).
- For each well: assemble per-tp `FrameDetections` (centroids from the cached CSV; masks /
  images lazily from `<well>_out.zip` via `zipfile_cache`, only for btrack-features /
  OverLapTrack / trackastra) → call the selected engine adapter → map results onto
  `CellKey`s.
- Write-back: `_write_tracks_to_csvs(...)` adds/overwrites `track_id` / `track_parent_id` /
  `tree_id` per row, keyed by `cell_keys_for_dataframe`, via `atomic_write_text`; also
  persist `tracks.json`. A `matched == 0` WARN fires on total key miss.
- `after_csv`: persist params (`persistence/tracking.save`), refresh the per-well CSV cache,
  drop the aggregation-stats cache, `app._redraw()`, refresh the Review CSV table — the
  exact six-step `_after_csv` sequence in `smfish/tab_view.py:732-802`.
- Optional headless path: `process_microscopy.main()` `--track` reuses the **same engine
  adapters**. The adapters are Qt-free (numpy / pandas + the engine library only), so they
  live in `engines/` which the pipeline can import; the worker / threading lives only on the
  GUI side.

### 7.4 The "Tracking" parent tab and its sub-tabs (Review)

Registered exactly like "Segmentation" (`centre_view.py:569-675`): a parent container with a
nested `NamedPageStack`, a `QTabBar` swapping sub-pages, lazy sub-tab builds, each calling a
`build_<sub>_tab(ctx, parent)`. Added to the **"Images"** group in `centre_view.py:groups`,
with an icon in `_SECTION_ICONS` (`runtime_app.py`). The left sidebar swaps to
`_sidebar_tracking_frame` via a new `elif tab == "Tracking"` branch in
`tab_controller.on_tab_change` (`:22-145`), calling `tracking_sync_from_app(app)`.

**Left sidebar (shared across sub-tabs):** well / FOV picker (reuse the main plate
selector), an engine + params summary, a **Track list** (id, length, #divisions, status)
with search / sort, and selection state (selected track(s) highlight in every sub-tab).

**Sub-tabs:**

1. **Setup & Run** — engine `SegmentedControl` + conditional per-engine parameter panel
   (§7.2); scope (selected wells / all wells; per-FOV vs stitch); **Run** / **Cancel**
   (worker + progress); a results summary (tracks found, mean length, #divisions, % cells
   tracked). Re-running overwrites `track_id` and `tracks.json` after a confirm.

2. **Track Viewer** — the interactive heart. Reuses the review-image overlay pattern
   (`features/review_image/{controller,renderer,state}.py`): per-FOV image (raw / tophat /
   mask LUT) with **track paths** drawn as polylines coloured by `track_id`, the current
   cell outlined, and a **time scrubber** (tp slider) animating motion. matplotlib events
   are wired as in `review_image/controller.on_review_image_click` (`:59-88`): click a path
   / cell → select that track (propagates to the sidebar + other sub-tabs); hover → tooltip
   (id, tp, speed). Toggles: show/hide paths, trail length, colour-by (track / lineage / a
   shape or intensity metric), only-selected.

3. **Lineage Tree** — an arboretum-style dendrogram of divisions for the selected
   well / FOV / lineage (`tree_id`): nodes = tracks, vertical = time, branches = divisions;
   click a node → select that track in the Viewer. Driven by `tracks.json` lineage edges
   (richest for btrack / trackastra `greedy` / `ilp`; laptrack via `tree_id` / `split_df`).

4. **Editor** — manual curation. All mutations write to `tracks.json` and rewrite the
   affected `track_id` / `parent` / `tree_id` columns (worker-free, in-process, then
   cache-drop + redraw): **Link/Connect** two track ends, **Break/Split** a track at a tp,
   **Merge** two tracks, **Assign division** (set a parent), **Relabel**, **Delete track**.
   An **undo stack** in `TrackingState`. Edits are scoped to a `CellKey` so they survive
   cache reloads; like `cell_overrides`, they are fingerprinted and dropped if the
   segmentation changed.

5. **Pruning / QC** — rule-based bulk cleanup with a live count + preview-on-overlay before
   apply. Filters: **min track length** (frames), **max gap count**, **edge-touching**
   (within N px of the `volume` bounds), **min net displacement**, **max area / intensity
   CV** (jitter = likely mislink), **divisions per track**. "Select matching → Delete / Mark
   untracked", reversible via the Editor undo stack.

6. **Analytics** — motility + dynamics per track from the node series: instantaneous & mean
   **speed**, **net / total displacement**, **directionality ratio**, **MSD curve**,
   **turning angle**, plus per-track time-series of any existing per-cell metric (intensity
   / shape). Two consumption paths: (a) tracking-local plots (speed histogram, MSD, rose
   plot) on a `PlotCard`; (b) **feed the existing line / scatter plots** by making
   `track_id` a new grouping key — extend `domain/batch_models` with a `TrackGroup` and
   branch `collect_line_series` (`lineplot/controller.py:143-292`) / `well_aggregate_stats`
   (`stats_compute.py:23-107`) on active track groups, exactly parallel to replicate-sets.
   Single-track time courses then reuse all fold-change / export machinery for free.

7. **Export** — `tracks.csv` (one row per node: `CellKey` + `track_id` / `parent` /
   `tree_id` + centroid + per-track metrics), **CTC** format (via the trackastra / btrack
   converters or a small writer), the lineage `tracks.json`, and figures (Viewer overlay,
   lineage tree, MSD) via the existing `features/export` Save / Copy pipeline.

### 7.5 Architecture placement (files to create)

New package `well_viewer/features/tracking/`:

- `__init__.py`
- `state.py` — `TrackingState` (selected track(s), engine, params, view transform, undo
  stack, caches).
- `controller.py` — run / edit / prune orchestration + cache invalidation.
- `renderer.py` — overlay paths + lineage-tree drawing.
- `worker.py` — the post-pass.
- `engines/{__init__, laptrack_engine, btrack_engine, trackastra_engine}.py` — guarded,
  Qt-free adapters.
- one `*_tab_view.py` per sub-tab (or a single `tab_view.py` that builds the sub-stack).

Plus `well_viewer/persistence/tracking.py`. Register the parent tab in
`centre_view.groups`, the icon in `_SECTION_ICONS`, the sidebar branch in
`tab_controller.on_tab_change`, and `_sidebar_tracking_frame` allocation in the shell
builder (alongside `_sidebar_stats_frame` etc.). Figures register via `ctx.widgets.register`
(no new loose `app._*`).

### 7.6 Open questions to resolve before building tracking

- Per-FOV vs cross-FOV stitching.
- 2D only (matches the pipeline; 3D is out of scope).
- Division support differs by engine / mode: btrack & trackastra-`greedy`/`ilp` = full
  lineage; laptrack = split/merge via `tree_id`; OverLapTrack / `greedy_nodiv` = none.
- Gap-closing limits.
- How edits interact with a later pipeline re-run (fingerprint-drop vs re-link).
- ILP solver availability (`motile` / Gurobi / SCIP) for trackastra-`ilp`.

---

## 8. Verify-when-built checklist (for features 1–3, and tracking once implemented)

1. Run the pipeline headless on a small dataset:
   `python process_microscopy.py --input_dir … --output_dir … --shape_features eccentricity
   solidity --flatfield gaussian --qc_remove_debris …`.
2. Confirm the per-well CSVs gained `shape_*` columns (and `track_id` once tracking is
   built), and that `pipeline_info.json` carries the `shape_features` and `qc_options`
   blocks (and a `tracks.json` sidecar exists once tracking is built).
3. Open the dataset in Review; confirm the **Shape Features** dropdown section appears and
   that selecting one drives the line / bar / scatter / distribution plots; confirm the
   **Tracking** tab and its sub-tabs (once built).
4. Run `pytest tests/test_guardrails.py` — no new loose `app._*` attributes and no
   state-package import-contract breaks.

---

## 9. Reference index (verified source locations)

| Concern | Location |
|---|---|
| Segmentation dispatch | `process_microscopy.py:1401-1427` |
| `apply_tophat` | `process_microscopy.py:958-972` |
| Preprocessing block (tophat calls) | `process_microscopy.py:1372-1394` |
| Per-cell measurement loop | `process_microscopy.py:1516-1541` |
| `process_image_group` signature | `process_microscopy.py:1321-1338` |
| `write_pipeline_info` (+ preserved keys) | `process_microscopy.py:3038-3112` (`:3092-3099`) |
| `build_parser` | `process_microscopy.py:3119` |
| Analyze form: seg method / hints / pickers | `analyze_tab.py:294-299`, `:707-724`, `:374-393` |
| Analyze form: collect options | `analyze_tab.py:992-1022` |
| Args assembly | `services/pipeline_service.py:23-104` |
| Channel/smfish/metric detection | `well_viewer/data/data_loading.py:147-162` |
| `resolve_value_series` | `well_viewer/data/data_loading.py:225-286` |
| Metric labels + threshold wording | `well_viewer/data/metric_labels.py:21-106` |
| Channel selector / metric combo | `well_viewer/core/channel_state_controller.py:278-401`, `:538-602` |
| Parent tab + sub-tabs | `well_viewer/core/centre_view.py:569-675` (groups list ~`:696-721`) |
| Per-tab sidebar swap | `well_viewer/core/tab_controller.py:22-145` |
| smFISH worker (the post-pass template) | `well_viewer/features/smfish/worker.py`, `tab_view.py:50-115`, `:732-802` |
| Review-image overlay viewer | `well_viewer/features/review_image/{controller,renderer,state}.py` |
| Cell identity | `well_viewer/data/image_identity.py` (`CellKey`, `cell_keys_for_dataframe`) |
| Persistence pattern + fingerprints | `well_viewer/persistence/{cell_overrides,registry,_io}.py` |
| Plot grouping | `well_viewer/features/lineplot/controller.py:143-292`, `domain/stats_compute.py:23-107` |
