# Implementation TODO — Segmentation / QC / Shape Features / Tracking

Living status tracker for the four tentpole features. Design spec:
[`FEATURE_PLAN_segmentation_qc_shape_tracking.md`](FEATURE_PLAN_segmentation_qc_shape_tracking.md)
(§A label-free seg · §B QC · §C shape features · §D tracking).

**Current status:** ◐ PR1 (foundation) — `todo.md` ✅ · dependency set resolved (candidate) ✅ ·
empirical install gate ☐ next.

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

### Resolved candidate pin set (in-core)
| Package | Pin | Rationale |
|---|---|---|
| numpy | `==1.24.3` | TF 2.13 cap `<=1.24.3`; numba `<1.25` |
| typing-extensions | `==4.5.0` | highest under TF's `<4.6`; satisfies torch 2.0.1 + lightning 2.0.x |
| tensorflow / keras / tensorboard / protobuf | `2.13.0` / `2.13.1` / `2.13.0` / `3.20.3` | existing; hold tensorboard at 2.13 so trackastra can't bump it |
| numba / llvmlite / stardist / csbdeep | `0.57.1` / `0.40.1` / `0.9.1` / `0.8.0` | existing |
| torch / torchvision | `==2.0.1` / `==0.15.2` | last line compatible with `typing-extensions<4.6`; numpy-1.24 safe; shared by cellpose + trackastra |
| cellpose | `==3.1.1.2` | cyto3; `numpy>=1.20`, `torch>=1.6`, `numba>=0.43` — all satisfied (the "numpy 1.26" advice is perf, not a hard pin) |
| laptrack | `==0.16.2` | pure-Python; py3.10; numpy/scipy/networkx only |
| btrack | `==0.6.5` **+ force `pydantic<2`** | ⚠ pydantic v2 needs `typing-extensions>=4.6.1` → conflicts with TF; must resolve on pydantic v1 |
| trackastra | `==0.1.x` **+ pin `lightning==2.0.9.post0`, `kornia==0.7.0`** | ⚠ heaviest; transitive chain must be pinned to the torch-2.0.1 line; `tensorboard` held at 2.13 |
| BaSiC | (none) | pure-numpy reimplementation — **no basicpy/jax** |
| motile (ILP) | optional extra only | needs SCIP/Gurobi; trackastra defaults to `greedy` |

### Two high-risk transitive pins (must confirm empirically)
1. **btrack ↔ pydantic v2.** pydantic 2 requires `typing-extensions>=4.6.1`, which TF 2.13
   forbids. Resolution: install btrack against **pydantic v1** (`pydantic<2`); if 0.6.5 hard-
   requires pydantic 2, drop to the newest btrack that supports pydantic 1.
2. **trackastra ↔ lightning/kornia.** A loose `lightning`/`kornia` would pull newer Torch
   (≥2.1) and bump `typing-extensions`/`tensorboard`. Resolution: pin `lightning==2.0.9.post0`
   + `kornia==0.7.0` (both accept Torch 2.0.1 + `typing-extensions 4.5`) and hold
   `tensorboard==2.13.0`. If trackastra hard-requires Torch≥2.1, it cannot share the TF-2.13
   env → fall back to running it out-of-process (separate env) while laptrack+btrack stay
   in-core.

### Gate tasks
- ✅ Resolve the concrete candidate pin set above (typing-extensions wall identified; torch
  pinned to 2.0.1 line).
- ☐ Scratch conda env (py3.10.14, numpy 1.24.3): `pip install --no-build-isolation` the set →
  resolve with **no conflicts** (special attention to btrack/pydantic and trackastra/lightning).
- ☐ `import tensorflow, torch, cellpose, trackastra, btrack, laptrack, stardist, csbdeep` all succeed.
- ☐ `tf.config.list_physical_devices('GPU')` still detects Metal on Apple Silicon.
- ☐ StarDist segmentation output unchanged vs current pins (determinism check).
- ☐ Resolve the same set against `requirements-remote.txt` on the remote platform (Linux
  aarch64 wheel availability for torch 2.0.1 / trackastra).
- ☐ If btrack or trackastra cannot resolve in-core → apply the documented fallback (pydantic-v1
  btrack; out-of-process trackastra) and record it here.

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
  - ☐ Run the **empirical dependency gate** (Part 1) and lock the verified set.
  - ☐ Update `_Docs/requirements.txt` + `_Docs/requirements-remote.txt` with the pinned set
    (add `typing-extensions==4.5.0`, `torch==2.0.1`, `torchvision==0.15.2`, `cellpose==3.1.1.2`,
    `btrack` (pydantic-v1), `laptrack==0.16.2`, `trackastra`+`lightning==2.0.9.post0`+`kornia==0.7.0`).
  - ☐ Update PyInstaller spec `hiddenimports` (cellpose/torch/trackastra/btrack/laptrack).
  - ☐ Add Qt-free `shape_features_core.py` (key → regionprops property + label + value-kind;
    `centroid_row/col` always-on).
  - ☐ Bump `pipeline_info` schema version + add `read_pipeline_info` defaults for new keys.
  - ☐ Add `tests/fixtures/legacy_dataset/` + `tests/test_backwards_compat.py`.
- **Commits:**
  `build: pin unified dependency set (torch 2.0.1 line; cellpose/trackastra/btrack/laptrack)` ·
  `feat: add shape_features_core shared catalog` ·
  `test: legacy-dataset backwards-compat harness`
- **BW-compat gate:** `read_pipeline_info` returns sane defaults on a legacy sidecar; harness green.
- **Verification:** dependency gate passes; `pytest tests/` green; app launches; StarDist
  determinism unchanged.

### ☐ PR2 — Shape features (§C)
- **Branch:** `claude/feat-shape-features`  **Base:** `claude/feat-foundation-deps-core`
  **Depends on:** PR1 (`shape_features_core`).
- **Tasks:**
  - ☐ `compute_shape_features(labels, selected)` via `regionprops_table`; merge `shape_*`
    columns (incl. always-on centroids) into per-cell records (`process_microscopy.py:1516-1541`).
  - ☐ `--shape_features` arg (`build_parser`) + thread through `process_image_group`.
  - ☐ Record `shape_features` in the fresh `pipeline_info` `info` dict.
  - ☐ Analyze "Shape Metrics" `ChipGroup` section + `_collect_run_options` + `build_pipeline_args`.
  - ☐ `detect_shape_features(df)`; `ui_helpers.set_combo_values_sectioned`; "Shape Features"
    dropdown section + metric collapse; `threshold=-1.0` aggregation; scatter-gate bypass for
    `shape_*` axes.
- **Commits:** `feat(pipeline): emit selectable shape-feature columns` ·
  `feat(analyze): shape-metrics chip selector` ·
  `feat(review): shape features as sectioned virtual channels`
- **BW-compat gate:** old CSVs (no `shape_*`) ⇒ no dropdown section, plots unchanged; extend the
  regression test.
- **Verification:** run pipeline with `--shape_features`; confirm columns + sidecar key; open
  legacy dataset (no section); `pytest tests/`.

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
  **Depends on:** PR2 (centroids); PR1 deps (laptrack/btrack) via PR2.
- **Tasks:**
  - ☐ Data model: additive `track_id` / `track_parent_id` / `tree_id` columns; versioned,
    `<well>_out.zip`-fingerprinted `tracks.json`.
  - ☐ Engine adapter interface (`run(frames, params) -> TrackResult`); **laptrack** + **btrack**
    engines in `features/tracking/engines/` (guarded imports, Qt-free).
  - ☐ Post-pass worker (`features/tracking/worker.py`) copying the smFISH `_WorkerBridge`
    pattern (`CellKey` keys, `atomic_write_text`, cache-drop → redraw → refresh).
  - ☐ `persistence/tracking.py` (load/save, registered with `PersistenceRegistry`).
  - ☐ Optional headless `--track` post-pass in `process_microscopy.main()`.
- **Commits:** `feat(tracking): track data model + tracks.json sidecar` ·
  `feat(tracking): laptrack + btrack engine adapters` · `feat(tracking): post-pass worker + persistence`
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
