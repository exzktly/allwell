# Implementation TODO — Segmentation / QC / Shape Features / Tracking

Living status tracker for the four tentpole features. Design spec:
[`FEATURE_PLAN_segmentation_qc_shape_tracking.md`](FEATURE_PLAN_segmentation_qc_shape_tracking.md)
(§A label-free seg · §B QC · §C shape features · §D tracking).

**Current status:** ✅ PR1 (foundation) code-complete (dev-env verification pending).
✅ PR2 (shape features) **complete & verified headless** — pipeline emit + sidecar + Analyze
chip selector + `detect_shape_features` + the full Review dropdown wiring (sectioned combo,
metric collapse, virtual-channel routing, all-cells aggregation, scatter-gate bypass).
A headless PySide6 (offscreen) env now runs the suite; `test_shape_features.py` +
`test_backwards_compat.py` + full suite green (8 unrelated pre-existing remote/rsync failures).
**Next: PR3 (QC tooling) or PR5a (tracking core) — both base on PR2.**

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

### ☐ PR3 — QC tooling (§B)
- **Branch:** `claude/feat-qc-tooling`  **Base:** `claude/feat-shape-features`
  **Depends on:** PR2 (debris reuses the regionprops pass).
- **Tasks:**
  - ☐ Background-well reference (`--background_well` / `--background_mode`) + `load_background_reference`.
  - ☐ Flat-field `--flatfield {none,empty_well,gaussian,basic}` via `apply_flatfield`;
    **pure-numpy BaSiC** estimator (no jax) computed once in `main()`.
  - ☐ Debris removal (`--qc_remove_debris` + bounds) reusing §C regionprops.
  - ☐ `qc_options` block in the fresh `pipeline_info` `info` dict.
  - ☐ Analyze "Quality Control" section + options plumbing.
- **Commits:** `feat(pipeline): background-well + flat-field (empty/gaussian/basic) correction` ·
  `feat(pipeline): debris removal filter` · `feat(analyze): quality-control options`
- **BW-compat gate:** QC off by default; old datasets unaffected; re-run preserves user blocks.
- **Verification:** run with each flat-field mode; confirm `qc_options` sidecar; legacy re-run
  preserves `cell_gating`; `pytest tests/`.

### ☐ PR4 — Label-free segmentation + phase ingestion (§A)
- **Branch:** `claude/feat-labelfree-phase`  **Base:** `claude/feat-foundation-deps-core`
  **Depends on:** PR1 (deps). Folds in the merged `claude/feature-plan-phase-ingestion` doc.
- **Tasks:**
  - ☐ `_segment_cellpose` (cyto3, model cached in `_worker_init`) + `_segment_classical`
    (Otsu+watershed via `auto_threshold_core`); add to dispatch + argparse `choices`.
  - ☐ Per-channel **Phase** checkbox (mutually exclusive; not also smFISH) →
    `(edit, smfish_cb, phase_cb, remove_btn)` + `_phase_token()`.
  - ☐ `--phase_token` arg; `Role.PHASE` in `image_identity.ROLE_CATALOG` (+ kind→role map);
    segmenters read the phase channel.
  - ☐ `phase_token` in the fresh `pipeline_info` `info` dict; validation in
    `_segmentation_validation_errors` + `_refresh_segmentation_hints`.
- **Commits:** `feat(pipeline): label-free segmentation (cellpose + classical)` ·
  `feat(analyze): per-channel phase-image designation` ·
  `feat(identity): Role.PHASE classification on ingestion`
- **BW-compat gate:** StarDist path unchanged; legacy filenames classify identically; datasets
  without `phase_token` segment exactly as before.
- **Verification:** run label-free on a brightfield set; confirm masks + `phase_token`; run a
  legacy StarDist dataset unchanged; `pytest tests/`.

### ☐ PR5a — Tracking core (§D)
- **Branch:** `claude/feat-tracking-core`  **Base:** `claude/feat-shape-features`
  **Depends on:** PR2 (centroids); PR1 deps (btrack) via PR2.
- **Tasks:**
  - ☐ Data model: additive `track_id` / `track_parent_id` / `tree_id` columns; versioned,
    `<well>_out.zip`-fingerprinted `tracks.json`.
  - ☐ Engine adapter interface (`run(frames, params) -> TrackResult`); **btrack** engine in
    `features/tracking/engines/` (guarded imports, Qt-free). (laptrack dropped — incompatible
    with the numpy-1.24/pandas-2.2 core; btrack covers the lightweight/lineage role.)
  - ☐ Post-pass worker (`features/tracking/worker.py`) copying the smFISH `_WorkerBridge`
    pattern (`CellKey` keys, `atomic_write_text`, cache-drop → redraw → refresh).
  - ☐ `persistence/tracking.py` (load/save, registered with `PersistenceRegistry`).
  - ☐ Optional headless `--track` post-pass in `process_microscopy.main()`.
- **Commits:** `feat(tracking): track data model + tracks.json sidecar` ·
  `feat(tracking): btrack engine adapter` · `feat(tracking): post-pass worker + persistence`
- **BW-compat gate:** no `tracks.json`/`track_id` on old datasets ⇒ no-op; grouping inactive.
- **Verification:** run tracking on a timelapse; confirm `track_id` columns + `tracks.json`;
  open legacy dataset (no tracks); `pytest tests/`.

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

### ☐ PR5c — Tracking: lineage + editor + pruning
- **Branch:** `claude/feat-tracking-edit`  **Base:** `claude/feat-tracking-ui`  **Depends on:** PR5b.
- **Tasks:**
  - ☐ Lineage Tree sub-tab (arboretum-style, from `tracks.json`).
  - ☐ Editor sub-tab (link/split/merge/assign-division/relabel/delete + undo stack).
  - ☐ Pruning/QC sub-tab (rule-based bulk cleanup + preview).
- **Commits:** `feat(tracking): lineage tree` · `feat(tracking): track editor + undo` ·
  `feat(tracking): pruning/QC rules`
- **BW-compat gate:** edits fingerprinted; dropped if segmentation changed.
- **Verification:** edit/prune round-trips persist to `tracks.json`; `pytest tests/`.

### ☐ PR5d — Tracking: analytics + export
- **Branch:** `claude/feat-tracking-analytics`  **Base:** `claude/feat-tracking-edit`  **Depends on:** PR5c.
- **Tasks:**
  - ☐ `TrackGroup` (`domain/batch_models`) + `track_id` grouping branch in `collect_line_series`
    / `well_aggregate_stats`.
  - ☐ Motility metrics (speed, displacement, directionality, MSD, turning angle) + local plots.
  - ☐ Export (tracks CSV, CTC, lineage JSON, figures) via `features/export`.
- **Commits:** `feat(tracking): track_id plot grouping` · `feat(tracking): motility analytics` ·
  `feat(tracking): track export (CSV/CTC/figures)`
- **BW-compat gate:** grouping inactive without tracks; existing plots unchanged.
- **Verification:** per-track time courses + exports; legacy plots unchanged; `pytest tests/`.

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
