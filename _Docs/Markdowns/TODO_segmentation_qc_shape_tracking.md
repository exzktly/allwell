# Implementation TODO — Segmentation / QC / Shape Features / Tracking

Living status tracker for the four tentpole features. Design spec:
[`FEATURE_PLAN_segmentation_qc_shape_tracking.md`](FEATURE_PLAN_segmentation_qc_shape_tracking.md)
(§A label-free seg · §B QC · §C shape features · §D tracking).

**Current status:** ✅ PR1 · ✅ PR2 (shape) · ✅ PR3 (QC tooling) · ✅ QC editor (non-destructive QC layer in the Segmentation tab; legacy `cell_area_threshold` retired) · ✅ PR4 (label-free seg + phase ingestion) — all complete & verified headless. Stack is linear: PR1→PR2→PR3→QC→PR4→PR5. ◐ PR5 (tracking chain) in progress: PR5a core (greedy + guarded btrack engines + data model + orchestrator + `tracks.json` persistence; `test_tracking_core.py` 8/8). **Next: PR5b–e (worker/`--track`, Review Tracking tab, edit, analytics, trackastra).**

## Confirmed decisions
- **Dependencies:** all new packages in **core** requirements, pinned to one mutually
  compatible set (no optional-extras split). `motile`/ILP is the sole optional extra.
- **BaSiC:** **pure-numpy/scipy reimplementation** — no `jax`/`basicpy`.
- **Backwards compatibility:** datasets analyzed before these features must load and behave
  identically — a mandatory gate on every PR.

---

## Part 1 — Dependency resolution (the foundational gate)

### The hard constraints
- Python **3.10.14**, `setuptools<70` (for `pkg_resources` → stardist/csbdeep).
- `tensorflow==2.13.0` fixes a cluster of upper bounds: **`numpy<=1.24.3`**,
  **`typing-extensions<4.6`**, `tensorboard~=2.13`, `keras~=2.13.1`, `protobuf>=3.20.3,<5`.
- `numba==0.57.1` independently caps `numpy<1.25`.

### The decisive landmine — `typing-extensions`
TF 2.13 pins **`typing-extensions<4.6`**. Modern Torch raises that floor: **Torch ≥2.2
requires `typing-extensions>=4.8`** (≥2.6 → `>=4.10`). Torch 2.0.1 predates that floor and
works with **`typing-extensions==4.5.0`** (the highest version under TF's `<4.6`). ⇒ the
entire ML stack must be pinned to the **Torch 2.0.1 line**, and every transitive dep
(lightning, kornia) must be pinned to versions that accept Torch 2.0.1 + `typing-extensions
4.5` — no floating versions (a loose `lightning`/`kornia` pin would drag in newer Torch and
break TF).

### Resolved pin set (in-core) — EMPIRICALLY VERIFIED (`pip --dry-run`, full set, no conflicts)
| Package | Pin | Rationale / verified resolution |
|---|---|---|
| numpy | `==1.24.3` | TF 2.13 cap `<=1.24.3`; numba `<1.25` |
| typing-extensions | `==4.5.0` | ceiling under TF's `<4.6`; resolves with torch 2.0.1 ✅ |
| tensorflow / keras / tensorboard / protobuf | `2.13.0` / `2.13.1` / `2.13.0` / `3.20.3` | existing |
| numba / llvmlite / stardist / csbdeep | `0.57.1` / `0.40.1` / `0.9.1` / `0.8.0` | existing |
| torch / torchvision | `==2.0.1` / `==0.15.2` | last line compatible with `typing-extensions<4.6`; resolves with TF 2.13 ✅ |
| cellpose | `==3.1.1.2` | cyto3; resolves on numpy 1.24.3 (`numpy<2.1,>=1.20`) ✅ |
| btrack | `==0.6.5` | cell tracking (Bayesian + lineage); **pulls pydantic 1.10.26 (v1)** → no typing-ext clash ✅ |
| trackastra | `==0.3.2` | transformer tracking; resolves with `kornia==0.7.0` + `lightning==2.0.9.post0` ✅ |
| kornia / lightning | `==0.7.0` / `==2.0.9.post0` | pinned to hold trackastra on the torch-2.0.1 line ✅ |
| BaSiC | (none) | pure-numpy reimplementation — **no basicpy/jax** |
| ~~laptrack~~ | **DROPPED** | ✗ no version fits: `>=0.16.1` needs `numpy>=1.26`; `<=0.16.0` needs `pandas<2.0` (vs pandas 2.2.3). btrack covers the lightweight/lineage role. |
| motile (ILP) | optional extra only | needs SCIP/Gurobi; trackastra defaults to `greedy` |

### Empirical results (py3.11 sandbox — `pip --dry-run` AND a real clean-venv install)
- ✅ Full set resolves with **no conflicts** (dry-run) and **installs together** (real venv):
  TF-cpu 2.13.0 + torch 2.0.1 + torchvision 0.15.2 + typing-extensions 4.5.0 + numpy 1.24.3 +
  numba 0.57.1 + cellpose 3.1.1.2 + btrack 0.6.5 + trackastra 0.3.2 + lightning 2.0.9.post0 +
  kornia 0.7.0 + pydantic 1.10.26.
- ✅ **Import coexistence verified**: all 15 modules import in one interpreter;
  `cellpose.models` + `btrack.BayesianTracker` + `trackastra.model.Trackastra` + `tensorflow`
  load together (no binary clash; `tf` sees CPU, torch 2.0.1).
- ✅ Both earlier risks resolved: **btrack → pydantic 1.10.26 (v1)** (no typing-ext clash);
  **trackastra's lightning/kornia chain** resolves on the torch-2.0.1 line.
- ❌ **laptrack is incompatible** and was dropped (numpy/pandas squeeze above).
- ⚠ torch installed as `2.0.1+cu117` (PyPI default pulled the CUDA build); the project's
  macOS-arm64 (MPS) / Windows-CPU / Linux-`+cpu` wheels differ — confirm per platform.

### Remaining gate tasks (run on the py3.10 `allwell` dev env, not the CI sandbox)
- ✅ Resolve + install + import coexistence (done in py3.11 sandbox; see above).
- ☐ Re-confirm on **py3.10.14** (target) incl. `stardist`/`csbdeep` (need build, omitted in sandbox).
- ☐ Confirm the new deps coexist with the project's **held** `tifffile==2023.9.26` /
  `imagecodecs==2023.9.18` / conda `scikit-image`/`scipy` pins (the dry-run let those float
  to latest — pin them and re-resolve).
- ☐ `tf.config.list_physical_devices('GPU')` still detects Metal on Apple Silicon.
- ☐ StarDist segmentation output unchanged vs current pins (determinism check).
- ☐ Linux remote: torch 2.0.1 CPU wheels via the `+cpu` index (avoid the CUDA wheel pull);
  confirm aarch64 wheel availability for torch/trackastra.

---

## Part 2 — Backwards compatibility (gated on every PR)

Old datasets = pre-feature `pipeline_info.json` (no new keys) + per-well CSVs (no new columns)
+ no `tracks.json` + StarDist-segmented + no `_phase` images.

- New CSV columns (`shape_*`, `track_id`, `track_parent_id`, `tree_id`) are **additive &
  optional**; no read path may require them. `detect_shape_features(df)` → `[]` ⇒ no Shape
  Features dropdown section; sectioned-combo helper tolerates empty sections.
- `read_pipeline_info` defaults new keys via `.get(...)`: `shape_features=[]`,
  `qc_options={}`, `phase_token=""`, segmentation engine → legacy StarDist behavior.
- New sidecars carry `"version"`; absence = no data (no crash). Missing `tracks.json` ⇒ empty
  Tracking tab; `track_id` grouping inactive.
- `Role.PHASE` is additive — legacy `fluor/tophat/mask/overlay/smfish` classification unchanged.
- Re-running the pipeline on an old output dir preserves user blocks (existing
  `write_pipeline_info` preserved-keys loop); new keys written fresh; QC/shape default off.
- Regression harness: `tests/fixtures/legacy_dataset/` + `tests/test_backwards_compat.py`
  (loads, channel/metric detection, `resolve_value_series`, no new section, legacy role
  classification). Added in PR1; extended per feature PR.

---

## Part 3 — PR sequence (foundational first)

```
main
 └─ PR1  foundation: deps + shape_features_core + bw-compat harness   (base: main)
     ├─ PR2  shape features §C                                        (base: PR1)
     │    ├─ PR3  QC tooling §B  (debris reuses §C regionprops)        (base: PR2)
     │    └─ PR5a tracking core §D (needs §C centroids)               (base: PR2)
     │         ├─ PR5b tracking UI: tab + viewer                      (base: PR5a)
     │         │    └─ PR5c lineage + editor + pruning                (base: PR5b)
     │         │         └─ PR5d analytics + export                   (base: PR5c)
     │         └─ PR5e trackastra engine (+ optional ilp)             (base: PR5a)
     └─ PR4  label-free seg §A + phase ingestion (needs PR1 deps)     (base: PR1)
```

> Base = the parent **branch** while the parent PR is open; once the parent merges to `main`,
> retarget the child PR's base to `main` and rebase.

---

### ◐ PR1 — Foundation: dependencies + shared core + backwards-compat harness
- **Branch:** `claude/feat-foundation-deps-core`  **Base:** `main`  **Depends on:** —
- **Tasks:**
  - ✅ Create the roadmap (`TODO_segmentation_qc_shape_tracking.md`, this file).
  - ✅ Resolve the concrete candidate dependency pin set (Part 1).
  - ✅ Run the **empirical dependency gate** (`pip --dry-run`, full set resolves; laptrack dropped).
  - ✅ Update `_Docs/requirements-remote.txt` with the verified set (`typing-extensions==4.5.0`,
    `torch==2.0.1`, `torchvision==0.15.2`, `cellpose==3.1.1.2`, `btrack==0.6.5`,
    `trackastra==0.3.2`, `kornia==0.7.0`, `lightning==2.0.9.post0`).
  - ✅ Add Qt-free `shape_features_core.py` (catalog; `centroid_row/col` always-on; self-tested).
  - ✅ Add `read_pipeline_info` backwards-compat defaults for the new keys
    (`shape_features=[]`, `qc_options={}`, `phase_token=""`, `segmentation_method`,
    `schema_version=1`) in `well_viewer/domain/viewer_state.py`.
  - ✅ Add `tests/fixtures/legacy_dataset/` + `tests/test_backwards_compat.py` (validated
    standalone: 14/14 assertions pass — legacy sidecar → empty defaults; new-key sidecar
    parses; `shape_features_core` catalog invariants).
  - ☐ Real install + import + GPU verification on the py3.10 dev env (Part 1 remaining gate);
    run `pytest tests/test_backwards_compat.py` there (sandbox lacks pytest/Qt).
  - ✅ Update PyInstaller spec `hiddenimports`/binaries via `collect_all` for
    torch/torchvision/cellpose/btrack/trackastra/kornia/lightning (build-verify on dev env).
  - ☐ Stamp `schema_version` in `process_microscopy.write_pipeline_info` (deferred to PR2,
    bundled with the first feature that writes a new key).
- **Commits:**
  `build: pin unified dependency set (torch 2.0.1 line; cellpose/trackastra/btrack)` ·
  `feat: add shape_features_core shared catalog` ·
  `test: legacy-dataset backwards-compat harness`
- **BW-compat gate:** `read_pipeline_info` returns sane defaults on a legacy sidecar; harness green.
- **Verification:** dependency gate passes; `pytest tests/` green; app launches; StarDist
  determinism unchanged.

### ✅ PR2 — Shape features (§C)
- **Branch:** `claude/feat-shape-features`  **Base:** `claude/feat-foundation-deps-core`
  **Depends on:** PR1 (`shape_features_core`).
- **Tasks:**
  - ✅ `compute_shape_features(labels, selected)` via `regionprops_table`; merge `shape_*`
    columns (incl. always-on centroids) into per-cell records. Validated on synthetic labels.
  - ✅ `--shape_features` arg (`build_parser`) + thread through `process_image_group` + `shared_kwargs`.
  - ✅ Record `shape_features` in the fresh `pipeline_info` `info` dict + stamp `schema_version=2`.
  - ✅ Analyze "Shape Metrics" `ChipGroup` section + `_collect_run_options` +
    `build_pipeline_args` (`--shape_features` mapping verified).
  - ✅ `detect_shape_features(df)` (catalog-ordered; `[]` on legacy CSVs) + bw-compat test extended.
  - ✅ **Review dropdown wiring** — `ui_helpers.set_combo_values_sectioned` (disabled,
    non-selectable headers on the combo's own model); "Shape Features" section in
    `update_channel_selector` + label→col map; `set_active_shape_feature` routing +
    `is_shape_active`; `populate_metric_combo` collapse; `active_channel_label` branch;
    `recalculate_threshold` populates `shape_features`; `threshold=-1.0` (all-cells)
    aggregation in line/bar/heatmap/distribution; scatter `fluor_gate` bypass for `shape_*`.
    **Verified headless** (PySide6 offscreen).
- **Commits (landed):** `feat(pipeline): emit selectable shape-feature columns` ·
  `feat(review/data): detect shape-feature columns` · `feat(analyze): shape-metrics chip selector` ·
  `feat(review): shape features as sectioned virtual channels`
- **BW-compat gate:** ✅ old CSVs (no `shape_*`) ⇒ `detect_shape_features` returns `[]` (no
  dropdown section); `read_pipeline_info` defaults hold; regression test green.
- **Verification:** ✅ `tests/test_shape_features.py` (5) + `test_backwards_compat.py` (5) +
  full suite green in a headless PySide6 env (8 unrelated pre-existing remote/rsync failures,
  confirmed failing on base). Set up: `apt install libegl1 …`; `QT_QPA_PLATFORM=offscreen`.
  Visual polish (section styling) is the only thing left for a real-display dev-env pass.

### ✅ PR3 — QC tooling (§B)
- **Branch:** `claude/feat-qc-tooling`  **Base:** `claude/feat-shape-features`
  **Depends on:** PR2 (debris reuses the regionprops pass).
- **Tasks:**
  - ✅ Flat-field `--flatfield {none,empty_well,gaussian,basic}` via `apply_flatfield`
    (mean-1 field); `gaussian` self-estimated per image; **pure-numpy BaSiC** (`basic`) +
    `empty_well` (`--background_well`) precomputed per channel in `main()` via
    `build_flatfield_models` (no jax/basicpy).
  - ✅ Debris removal (`--qc_remove_debris` + min/max area + min-solidity) via `filter_debris`,
    reusing §C regionprops.
  - ✅ `qc_options` block in the fresh `pipeline_info` `info` dict (read defaults to `{}`).
  - ✅ Analyze "Quality Control" section + `_collect_run_options` + `build_pipeline_args` plumbing.
- **Commits (landed):** `feat(pipeline): flat-field correction + debris removal + qc_options` ·
  `feat(analyze): Quality Control section + QC arg plumbing`
- **BW-compat gate:** ✅ QC off by default (`none` / unchecked); old datasets unaffected;
  `qc_options` written fresh per run (user analysis blocks like `cell_gating` untouched).
- **Verification:** ✅ `tests/test_qc_tooling.py` (8): flat-field estimators/application, debris
  bounds + solidity, `build_flatfield_models` over synthetic TIFFs, Analyze→CLI mapping; full-app
  smoke (`test_app_smoke`/`interaction`/`characterization`) builds the QC section. Headless
  PySide6. **Visual + a real `--flatfield basic` end-to-end run are the dev-env pass.**

### ✅ PR4 — Label-free segmentation + phase ingestion (§A)
- **Branch:** `claude/feat-labelfree-phase`  **Base:** `claude/feat-foundation-deps-core`
  **Depends on:** PR1 (deps). Folds in the merged phase-ingestion design (FEATURE_PLAN §4.3).
- **Tasks:**
  - ✅ `_segment_cellpose` (cyto3, lazy-loaded `_CELLPOSE_MODEL`, guarded import) +
    `_segment_classical` (Otsu + EDT watershed); dispatch + argparse `choices`;
    `_worker_init` skips the StarDist/TF load for label-free workers.
  - ✅ Per-channel **Phase** checkbox (mutually exclusive across rows; not also smFISH) →
    rows are `(edit, smfish_cb, phase_cb, remove_btn)` + `_phase_token()`.
  - ✅ `--phase_token` (+ `--cellpose_diameter`) args; `Role.PHASE` in
    `image_identity.ROLE_CATALOG` (+ kind→role map); `main()` maps the phase token onto the
    nuclear slot so the segmenters read it (reusing all grouping).
  - ✅ `phase_token` in the fresh `pipeline_info` `info` dict (read defaults to `""`);
    validation in `_segmentation_validation_errors` + nuclear-row hide in
    `_refresh_segmentation_hints`.
- **Commits (landed):** `feat(pipeline): label-free segmentation (cellpose + classical)` ·
  `feat(identity): Role.PHASE classification on ingestion` ·
  `feat(analyze): per-channel phase designation + label-free plumbing`
- **BW-compat gate:** ✅ StarDist path unchanged (default; `_worker_init` loads it for the
  StarDist methods as before); legacy filenames classify identically; datasets without
  `phase_token` segment exactly as before.
- **Verification:** ✅ `tests/test_labelfree_phase.py` (5): Role.PHASE, classical segmenter +
  cellpose guard, arg mapping, Phase-checkbox mutual-exclusivity + validation;
  `test_image_identity` (52) + full-app smoke green. Headless PySide6. **A real label-free
  `--segmentation_method cellpose` run on brightfield images is the dev-env pass** (cellpose's
  torch model isn't exercised in the sandbox).

### ◐ PR5a — Tracking core (§D)
- **Branch:** `claude/feat-tracking-core`  **Base:** `claude/feat-shape-features`
  **Depends on:** PR2 (centroids); PR1 deps (btrack) via PR2.
- **Tasks:**
  - ✅ Data model (`model.py`): additive `track_id`/`track_parent_id`/`tree_id` columns;
    DataFrame⇄frames; versioned `tracks.json` payload (nodes/tracks/lineage/trees).
  - ✅ Engine adapter interface (`run(frames, params) -> TrackResult`) in
    `features/tracking/engines/` — **greedy** (dependency-free LAP, the lightweight default
    replacing dropped laptrack) + **btrack** (guarded, Bayesian/lineage); registry with
    availability + clear errors.
  - ✅ Qt-free orchestrator (`orchestrator.py`): per-FOV tracking with track-id offsets;
    `track_well()` → `(tracked_df, tracks.json payload)`.
  - ✅ `persistence/tracking.py` (Qt-free `save_tracks`/`load_tracks` with `<well>_out.zip`
    fingerprint staleness; app wrappers; registered with `PersistenceRegistry`).
  - ☐ Post-pass **GUI worker** (`features/tracking/worker.py`, smFISH `_WorkerBridge` pattern)
    — deferred to PR5b (it calls `orchestrator.track_well`; needs the GUI bridge it triggers).
  - ☐ Optional headless `--track` post-pass in `process_microscopy.main()` (calls the same
    Qt-free `orchestrator`) — deferred/optional.
- **Commits (landed):** `feat(tracking): track data model + engine adapters` ·
  `feat(tracking): tracks.json persistence + registry registration`
- **BW-compat gate:** ✅ no `tracks.json`/`track_id` on old datasets ⇒ `load_tracks` → `{}`
  (no crash); the three columns are additive/optional; grouping inactive.
- **Verification:** ✅ `tests/test_tracking_core.py` (8): greedy linking (+ distance gate +
  gap closing), orchestrator FOV-offset, `track_well` columns/payload, persistence roundtrip +
  staleness, engine registry + btrack guard; app-construction + bw-compat green. Headless.
  **A real btrack/timelapse run is the dev-env pass (btrack's torch-free model not in sandbox).**

### ☐ PR5b — Tracking UI: tab + viewer
- **Branch:** `claude/feat-tracking-ui`  **Base:** `claude/feat-tracking-core`  **Depends on:** PR5a.
- **Tasks:**
  - ☐ "Tracking" parent tab + nested `NamedPageStack` in `centre_view.py`; `_SECTION_ICONS`.
  - ☐ `_sidebar_tracking_frame` + `tab_controller.on_tab_change` branch + `tracking_sync_from_app`.
  - ☐ Setup & Run sub-tab (engine `SegmentedControl` + conditional param panel + Run/Cancel).
  - ☐ Track Viewer sub-tab (overlay paths + time scrubber, reuse `review_image`).
- **Commits:** `feat(tracking): tracking tab + sidebar` · `feat(tracking): setup/run + track viewer`
- **BW-compat gate:** empty-state when no tracks; no crash on legacy datasets.
- **Verification:** open Tracking tab on tracked + legacy datasets; `pytest tests/`.

### ✅ PR5c — Tracking: lineage + editor + pruning
- **Branch:** `claude/feat-tracking-edit`  **Base:** `claude/feat-tracking-ui`  **Depends on:** PR5b.
- **Tasks:**
  - ✅ Lineage sub-tab — matplotlib lineage view per well (track time-segments in lanes +
    dashed parent→child division links) from the track columns.
  - ✅ Editor sub-tab — relabel / merge / split / delete / assign+clear-division + Undo,
    driving a per-well cached `TrackEditor` (Qt-free `edit.py`, snapshot undo stack); each edit
    writes the well CSV back + rebuilds its `tracks.json` entry (`model.payload_from_df`).
  - ✅ Pruning/QC sub-tab — `PruneRules(min_frames, min_displacement)` with Preview (count) +
    Apply (Qt-free `pruning.py`).
- **Commits (landed):** `feat(tracking): track-edit (undo) + rule-based pruning cores` ·
  `feat(tracking): Lineage / Editor / Pruning sub-tabs`
- **BW-compat gate:** ✅ edits/pruning operate only on the additive track columns; re-persisted
  `tracks.json` carries fingerprints; no-op on datasets without tracks.
- **Verification:** ✅ `tests/test_tracking_edit.py` (9: relabel/merge/delete/split/division +
  undo, pruning rules + preview, payload rebuild) + `test_tracking_tab.py` (lineage/editor/prune
  via the sub-tabs) + app-smoke builds/switches the 5-sub-tab Tracking tab; guardrails green.
  **On-screen visual pass pending (user).**

### ◐ PR5d — Tracking: analytics + export
- **Branch:** `claude/feat-tracking-analytics`  **Base:** `claude/feat-tracking-edit`  **Depends on:** PR5c.
- **Tasks:**
  - ✅ Motility metrics (`motility.py`): per-(fov, track) speed/displacement/directionality/MSD/
    turning-angle + the Analytics sub-tab's directionality histogram + summary ("local plots").
  - ✅ Export (`export.py`): per-track motility CSV, CTC `man_track.txt`, lineage JSON — wired to
    the Analytics sub-tab's Export buttons (QFileDialog).
  - ✅→**design change** `TrackGroup` + `track_id` grouping branch in `collect_line_series` /
    `well_aggregate_stats` was **intentionally dropped**. The main plotting path is *well-pooled*
    aggregation (`well_aggregate_stats`: one mean per well, SD across wells, fluor-gating,
    rep-sets) — the wrong unit for per-cell time-series, so grafting `track_id` in would be a
    foreign branch on a hot path. Instead, per-track plotting lives in a **separate "Track Plots"
    sub-tab** (`track_plots.py` + the Tracking tab): multi-well via the well picker, one colour
    per well, optional per-well average track, live on `selection_changed`. Additive — the main
    plots are untouched.
- **Commits (landed):** `feat(tracking): motility metrics + export writers` ·
  `feat(tracking): Analytics sub-tab — motility summary + export`
- **BW-compat gate:** ✅ analytics/export operate only on tracked rows; empty on legacy datasets;
  existing plots unchanged (no main-plot grouping added yet).
- **Verification:** ✅ `tests/test_tracking_analytics.py` (7: motility straight/zig-zag/per-FOV/
  empty; CSV/CTC/JSON export) + `test_tracking_tab.py` (analytics refresh + export) + app-smoke
  builds/switches the 6-sub-tab Tracking tab. **On-screen visual pass pending (user).**

### ☐ PR5e — Tracking: trackastra engine
- **Branch:** `claude/feat-tracking-trackastra`  **Base:** `claude/feat-tracking-core`
  **Depends on:** PR5a (+ PR1 torch/trackastra deps).
- **Tasks:**
  - ☐ trackastra engine adapter (`from_pretrained`, `track(imgs, masks, mode=…)`); model/device/
    linking-mode controls; weight-download progress.
  - ☐ Optional `ilp` mode behind `motile` (guarded; disabled with a hint if absent).
- **Commits:** `feat(tracking): trackastra engine` · `feat(tracking): optional ILP linking`
- **BW-compat gate:** engine selectable only when installed; greedy default.
- **Verification:** trackastra greedy run on a sample; `pytest tests/`.

---

## Notes
- Update statuses here as each task/commit lands. After PR1 merges, this file lives on `main`;
  later PRs make small localized status edits to avoid cross-PR conflicts.
- Never commit onto a merged-PR branch; cut each new branch from the correct base per the DAG.
