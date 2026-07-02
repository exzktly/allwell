# All-Well — Code Review Audit & Remediation Tracker

**Audit date:** 2026-07-02 · **Living tracker since:** 2026-07-02
**Scope:** `/home/user/allwell` — PySide6 desktop app for fluorescence-microscopy well-plate analysis. ~90,462 LOC non-test across 267 modules; 179 test files (~800 fast-lane tests).
**Method:** Structured multi-pass audit (Pass 0 inventory; Pass 1 architecture; Pass 2 async/state; Pass 4 logic/business rules; Pass 5 quality). Findings verified by direct file reads; every item carries `file:line`, severity, and an actionable fix.
**Status at a glance:** **0 / 77 items resolved.** Umbrella tracking issue: [#598](https://github.com/exzktly/allwell/issues/598). See the [Progress](#progress) table.

> This document started as a static audit and is now a **living work plan**. Each finding below carries a stable ID and a status box; fixing it flips the box here, the Progress-table row, the Status log, and the umbrella issue checkbox — in the same commit. It mirrors the house style of `_Docs/Markdowns/Code_Diet.md` and `_Docs/Markdowns/FABLE_PLAN.md`.

---

## ⚠️ HOW TO USE THIS FILE

Update the status box the moment an item's state changes:
- `[ ]` TODO · `[~]` IN PROGRESS · `[x]` DONE · `[!]` BLOCKED (reason inline) · `[-]` DROPPED (reason inline)

**Rules:**
1. Update the item's inline box **and** its row in the [Progress](#progress) table in the **same change** that executes it.
2. When an item is `[x]`, record the **commit SHA + the proving test** in the item's `Notes`.
3. **Verify before acting:** re-check the cited `file:line` at execution time — the tree moves; a line number here is *as of the audit*.
4. **Do not delete items** — mark `[x]`/`[-]` (with a reason) so the history stays legible. Dropped IDs are never reused.
5. Keep the **umbrella GitHub issue ([#598](https://github.com/exzktly/allwell/issues/598))** checklist in sync (tick the matching box).
6. **Reference the finding ID in the commit message** (e.g. `Fix E1, E3: surface remote worker crashes`).
7. Append a dated line to the [Status log](#status-log) and bump the `Last updated` footer whenever a box changes.

## Verification harness

CI runs `ruff check .` + a fast-lane pytest job; run the rest locally.
- **Lint:** `ruff check .` must stay green. (`_Docs` is in `extend-exclude`, so this file is not linted.)
- **Fast lane (CI):** `QT_QPA_PLATFORM=offscreen python -m pytest -m "not slow" -q --timeout=90 --timeout-method=signal` (~800 tests, ~40s).
- **Full/manual pass:** `python -m pytest` (no marker filter) or `-m slow`. Any test using the `viewer_app`/`loaded_app*` fixtures is **auto-marked `slow`** (conftest) and thus excluded from the fast lane — an item proved only by those must be run in the full pass.
- **Guardrails (the regression locks):** `tests/test_no_silent_except.py`, `tests/test_layering.py`, `tests/test_monolith_budgets.py`, `tests/test_architecture_contracts.py`, `tests/test_guardrails.py`, `tests/test_extracted_module_names.py`, `tests/test_remote_manifest.py`.
- **Single file / subset:** `python -m pytest tests/test_fold_change.py` · `... ::test_name` · `-k "expr"`.
- **Per-area suites** are named in each item's `Verify` field.

## Progress

Severity: **C**ritical / **H**igh / **M**edium / **L**ow. Status uses the legend above. One row per tracked finding; grouped by workstream.

### E — Async / error handling / silent-success (Pass 2)
| ID | Item | Sev | Status | Commit/PR |
|----|------|-----|--------|-----------|
| E1 | `_run_batches` no-except → rc None→0 → cleanup deletes data | C | `[ ]` TODO | |
| E2 | image loader `except:return None` → undercount cells | C | `[ ]` TODO | |
| E3 | corrupt `metadata.json` swallowed+overwritten → user-data loss | C | `[ ]` TODO | |
| E4 | malformed tophat/FFT opts swallowed → wrong pipeline params | C | `[ ]` TODO | |
| E5 | per-well failure never sets exit code → all-fail exits 0 | H | `[ ]` TODO | |
| E6 | `_run_pipeline_thread` emits `finished` not `error` on setup raise | H | `[ ]` TODO | |
| E7 | `GatingWorker.run` no except + no error signal → UI hang | H | `[ ]` TODO | |
| E8 | smFISH `_run` no try/except → "Apply Global Threshold" hangs | H | `[ ]` TODO | |
| E9 | auto-threshold DEBUG-skip → silently biased threshold | H | `[ ]` TODO | |
| E10 | saved-plot capture `return None` → silently loses groups/stats | H | `[ ]` TODO | |
| E11 | corrupt input zip silently uploaded whole | H | `[ ]` TODO | |
| E12 | `Fetcher._loop` no except → wells silently not downloaded | M | `[ ]` TODO | |
| E13 | failed `scancel`/`kill` unreported → lingering SLURM alloc | M | `[ ]` TODO | |
| E14 | `ALLWELL_SCRATCH_DIR` silently ignored → ENOSPC | M | `[ ]` TODO | |
| E15 | corrupt mask → "no overlay" → misread QC | M | `[ ]` TODO | |
| E16 | view_state `swallowed()` + sample_defs persistence fail invisibly | M | `[ ]` TODO | |
| E17 | unguarded remote-dialog `_runner` → QTimer poll spins forever | L | `[ ]` TODO | |
| E18 | smFISH Apply-to-All re-entrancy → dup workers + clobbered cancel | H | `[ ]` TODO | |
| E19 | smFISH worker TOCTOU: dataset swap during write loop | M | `[ ]` TODO | |
| E20 | `_current_proc` read without lock → Stop can miss child | M | `[ ]` TODO | |
| E21 | `pipeline_runner.stop()` reads worker-assigned `_proc` unsynced | M | `[ ]` TODO | |
| E22 | cell_browser unbounded threads read invalidated zip cache | M | `[ ]` TODO | |
| E23 | playground `_ReferenceBridge` never disconnected | L | `[ ]` TODO | |

### S — Scientific correctness (Pass 4 / 2.5)
| ID | Item | Sev | Status | Commit/PR |
|----|------|-----|--------|-----------|
| S1 | `build_cell_scaling` ignores op/order/baseline → overlays ≠ bars | H | `[ ]` TODO | |
| S2 | timepoint tolerance mismatch (abs 1e-6 vs rel 1e-3) → tps unmatched | H | `[ ]` TODO | |
| S3 | per-cell fold-change collapses multi-tp to first tp | M | `[ ]` TODO | |
| S4 | `stats_cache` keys omit gate/on-population state → stale stats | M | `[ ]` TODO | |
| S5 | image-decode numeric-close/first-FOV fallback → wrong frame | M | `[ ]` TODO | |
| S6 | even-n median reported as upper element | L | `[ ]` TODO | |
| S7 | `_missing_col_warned` keyed by `id(df)` | L | `[ ]` TODO | |
| S8 | circularity computed before `per>0` mask (+ empty-vals CDF) | L | `[ ]` TODO | |

### L — Lifecycle / teardown (Pass 2.4)
| ID | Item | Sev | Status | Commit/PR |
|----|------|-----|--------|-----------|
| L1 | `MainWindow.closeEvent` never stops runner → orphan + remote leak | C | `[ ]` TODO | |
| L2 | `_poll_log` self-reschedules forever, no stop/teardown | M | `[ ]` TODO | |
| L3 | `runtime_app` no closeEvent → smFISH cancel unset, threads unjoined | M | `[ ]` TODO | |
| L4 | app-global `focusChanged` not disconnected on teardown | L | `[ ]` TODO | |

### G — Guardrails / hygiene / dead code (Pass 1.5 / 2.2 / 5.2)
| ID | Item | Sev | Status | Commit/PR |
|----|------|-----|--------|-----------|
| G1 | `sys` used but never imported → local auto-threshold pass dead | H | `[ ]` TODO | |
| G2 | FOLDER-mode verify requires out-zip retry can't re-pull → no converge | M | `[ ]` TODO | |
| G3 | `qc_*` params ignored; `filter_debris` dead | M | `[ ]` TODO | |
| G4 | extend `test_no_silent_except` (tuple/typed pass + broad return None) | M | `[ ]` TODO | |
| G5 | re-enable ruff `F401` with targeted `# noqa`; drop 55 unused imports | M | `[ ]` TODO | |
| G6 | add a function-level complexity ratchet | M | `[ ]` TODO | |
| G7 | phantom guards / no-op handlers (5 sites) | L | `[ ]` TODO | |
| G8 | latent NameError (missing `typing` import) + dead `prefix` param | L | `[ ]` TODO | |

### A — Architecture / god-modules / duplication (Pass 1 / 5.1 / 5.2)
| ID | Item | Sev | Status | Commit/PR |
|----|------|-----|--------|-----------|
| A1 | unify `find_image_groups` vs `_in_zip` (folder/zip parse divergence) | H | `[ ]` TODO | |
| A2 | fetch-side reuse `pipeline.io` merge instead of reimpl | H | `[ ]` TODO | |
| A3 | extract `cell_gating` domain compute out of the view | H | `[ ]` TODO | |
| A4 | characterization tests for CC-98 `find_well_images_and_masks`, decompose | H | `[ ]` TODO | |
| A5 | characterization tests for CC-69 `redraw_line_plots` + CC-52 `run_stats` | H | `[ ]` TODO | |
| A6 | delete cosmetic `WidgetRegistry` (one caller, double-write) | H | `[ ]` TODO | |
| A7 | split `scatter/callbacks.py` (handlers vs single-cell viewer) | M | `[ ]` TODO | |
| A8 | `ctx.app` / god-object burn-down: lint + migrate hot clusters | M | `[ ]` TODO | |
| A9 | delete dead frameless modules (`_window_chrome`, `window_resize_grips`) | M | `[ ]` TODO | |
| A10 | parametrize the `process_well_*` task/driver pairs | M | `[ ]` TODO | |
| A11 | consolidate 3 rsync transport/retry impls | M | `[ ]` TODO | |
| A12 | loop the barplot mean/frac/n triplet (6× paste) | M | `[ ]` TODO | |
| A13 | `_annotated_matrix` helper for the 4× heatmap block | M | `[ ]` TODO | |
| A14 | decompose `batch_export/base_panel.py` (export exec → service) | M | `[ ]` TODO | |
| A15 | lazy/deferred feature descriptors for the 49-import registry | M | `[ ]` TODO | |
| A16 | collapse remaining re-inlined helpers | L | `[ ]` TODO | |
| A17 | normalize feature-file naming or document exceptions | L | `[ ]` TODO | |

### X — Cross-dataset state / atomicity / input validation (Pass 1.2 / 4.3 / 4.4 / 5.4 / 5.5)
| ID | Item | Sev | Status | Commit/PR |
|----|------|-----|--------|-----------|
| X1 | reset gates/overrides in `load_directory` → stop cross-dataset leak | H | `[ ]` TODO | |
| X2 | `_materialize` atomic tmp→replace; delete-remote only after confirm | H | `[ ]` TODO | |
| X3 | batch-export: stage-temp→rename on full success + manifest | H | `[ ]` TODO | |
| X4 | validate radius/FFT numeric inputs at entry (stop silent flag-drop) | H | `[ ]` TODO | |
| X5 | init undeclared `app._*` (esp. `_label_to_channel_key` lossy fallback) | M | `[ ]` TODO | |
| X6 | validate negative/zero + QC bound ordering at entry | M | `[ ]` TODO | |
| X7 | add `view_state` schema `_migrate()` (stop silent discard on bump) | M | `[ ]` TODO | |
| X8 | persist only non-default `export_style_prefs` (replace, not merge) | M | `[ ]` TODO | |
| X9 | unlink `tmp_zip` on incomplete fetch (`try/finally`) | M | `[ ]` TODO | |
| X10 | enqueue stdout sentinel before `flush_and_join` (stop reader hang) | M | `[ ]` TODO | |
| X11 | offer optional SSH key passphrase (or document risk) | M | `[ ]` TODO | |
| X12 | `services/env.py` startup validation of `ALLWELL_*` | M | `[ ]` TODO | |
| X13 | delete dead state `_bar_quick_*`, `_montage_chan_cb` | L | `[ ]` TODO | |
| X14 | preserve absent saved wells (don't prune-then-persist) | L | `[ ]` TODO | |
| X15 | bootstrap-log retention cap / "clear logs" | L | `[ ]` TODO | |
| X16 | route full tracebacks to dev-gated sinks only | L | `[ ]` TODO | |
| X17 | use `rc_context` instead of hand-rolled rcParams save/restore | L | `[ ]` TODO | |

**Totals:** 77 items — E 23 · S 8 · L 4 · G 8 · A 17 · X 17. By severity: C 5 · H 21 · M 34 · L 17.

---

## Executive summary

All-Well is an **unusually well-structured** codebase for its size and generation style: it has enforced monolith line-budget ratchets, AST-based architecture-contract tests, a clean Qt-free `pipeline/` layer, a persistence registry, and a genuinely behavioral test suite (~75% behavioral asserts). The numeric/compute layer is systematically hardened against empty/zero/single-item inputs — a real strength.

The debt is concentrated and predictable, and it clusters into **six actionable themes** (each is a workstream in the Progress table above):

1. **"Silent success" is the dominant failure mode.** Worker-thread exceptions vanish and the layer above reads a default that *means success*. On remote runs this chains into `verify_and_cleanup(ok=True)` **deleting real data though nothing ran**. (Workstream E.)
2. **Two scientific-correctness bugs** produce wrong numbers silently: per-cell overlays use a different fold-change transform than the bars on the same plot, and timepoint matching uses inconsistent tolerances so fractional timepoints silently fail to match. (Workstream S.)
3. **App teardown is structurally broken**: `AnalyzeTab.closeEvent` is dead code (child-widget mount), so quitting during a run orphans local subprocesses and leaks remote SLURM allocations. (Workstream L.)
4. **Guardrails create false confidence.** The silent-except test only bans literal `except Exception: pass`; the far more common `return None`, tuple/typed `pass`, and DEBUG-only `swallowed()` patterns all pass it — and several critical files aren't even scanned. Likewise, CI's ruff config disables F401, hiding 55 unused imports. (Workstream G.)
5. **Concentrated god-modules and copy-paste**: `cell_gating_tab_view.py` (2392 lines, view + domain), a CC-98 untested discovery function, and pipeline group-discovery / CSV-merge logic duplicated across the local and remote paths (divergence changes results). (Workstream A.)
6. **Cross-dataset state and export leakage**: undeclared `app._*` state (gates, per-cell overrides, gating mode) survives a dataset swap and silently mis-filters the new dataset (Pass 1.2), the batched remote fetch and the entire batch-export subsystem lack the stage-to-temp→rename→then-delete discipline the atomic single-file layer already models (Pass 4.4), and analyze-tab numeric inputs are unvalidated `QLineEdit`s whose bad values are swallowed downstream (Pass 4.3). (Workstream X.)

### Top 16 prioritized actions

| # | ID | Severity | Area | Action | Anchor |
|---|----|----------|------|--------|--------|
| 1 | **E1** | Critical | 2.1 | `_run_batches` worker has `try/finally` with no `except` → rc stays `None` → `wait()` returns 0 → success reported → remote data deleted. Wrap body, set rc=1 on error; treat dead-worker `None` rc as failure. | `services/remote_runner.py:2575` |
| 2 | **L1** | Critical | 2.4 | Wire `MainWindow.closeEvent` to stop the runner + cancel workers. Today quitting mid-run orphans the pipeline subprocess and leaks remote SLURM/staging/scratch. | `allwell.py:918` |
| 3 | **E2** | Critical | 2.2 | Image loader `except Exception: return None` makes a corrupt image indistinguishable from "no image" → undercounted cells / wrong per-well stats. Narrow the catch; record a failed FOV. | `process_microscopy.py:947` |
| 4 | **E3** | Critical | 2.2 | Corrupt existing `metadata.json` is swallowed (`except (OSError, JSONDecodeError): pass`) then overwritten → irreversible loss of user `cell_gating`/`sample_definitions`/`ratios`/`notes`. | `process_microscopy.py:4015` |
| 5 | **E5** | High | 2.1 | Per-well failures never set the process exit code → an all-failed run exits 0 → remote layer reports success and cleans up. `sys.exit(1)` when `all_failed` is non-empty. | `process_microscopy.py:4306/4695` |
| 6 | **S1** | High | 4.2 | `build_cell_scaling` ignores fold-change operator/order/baseline_tp and always divides → per-cell violin/beeswarm overlays disagree with the bars on the same plot (subtract vs divide). | `domain/fold_change.py:1109`, `features/barplot/renderer.py:207/986` |
| 7 | **S2** | High | 4.1 | Timepoint matching uses absolute `1e-6` in stats/aggregation but relative `1e-3` in fold-change → fractional timepoints silently unmatched ("no data") in Statistics while fold-change succeeds. Share one tolerance. | `domain/stats_compute.py:93/285/431` + 5 sites |
| 8 | **A3** | High | 1.3 | `cell_gating_tab_view.py` (2392 lines) is a "view" embedding domain compute (numpy, optimizer dataclass, off-thread masking). Extract compute into `cell_gating_compute.py` + a controller. | `features/grouping/cell_gating_tab_view.py` |
| 9 | **A4** | High | 5.2 | `find_well_images_and_masks` (CC 98) — highest-complexity function in the repo, **zero direct tests**, drives which images/masks feed the viewer. Add characterization tests, then decompose. | `well_viewer/data/image_discovery.py:339` |
| 10 | **A1/A2** | High | 5.1 | Pipeline group-discovery (`find_image_groups` vs `_in_zip`) and CSV/zip merge are duplicated across local and remote-fetch paths — a parsing/schema fix on one path is silently missed on the other. Unify. | `process_microscopy.py:2144/2234`; `services/remote_runner.py:3252` |
| 11 | **E18** | High | 2.3 | "Apply Global Threshold" has no re-entrancy guard; a double-click runs two workers writing the same CSVs and the 2nd `.clear()`s the shared cancel event. Add a busy flag + per-run cancel event. | `features/smfish/tab_view.py:752` |
| 12 | **E4** | High | 2.2 | Malformed tophat/FFT CLI options are swallowed (`except (TypeError, ValueError, KeyError): pass`) → the pipeline runs with **different parameters than configured**. Validate up front. | `services/pipeline_service.py:93/97` |
| 13 | **G1** | High | 1.5 | `_run_local_auto_threshold` calls `sys.path.insert` but `sys` is never imported → `NameError` on line 1, swallowed → batched remote runs **never compute auto-threshold defaults**. Add `import sys`. | `services/remote_runner.py:2835` |
| 14 | **X1** | High | 1.2 | Opening a gated dataset then a plain one **silently carries the prior dataset's gates/overrides into the new data**: `load_directory` never resets `_scatter_gates`/`_review_included_overrides`/`_cell_gating_use_scatter_*`, and the persistence loaders early-return when the new sidecar is absent. Reset these before hydration. | `core/load_controller.py:121`, `persistence/cell_gating.py:59` |
| 15 | **X2** | High | 4.4 | Batched per-well fetch: `_materialize` copies straight to the final path with the error only logged, then unconditionally marks the well fetched and **deletes the remote copy** → an interrupted copy is irrecoverable loss. Copy to `.tmp` + `os.replace`; delete remote only after confirming a non-empty local artifact. | `services/remote_runner.py:3309/3190/3211` |
| 16 | **X3** | High | 4.4 | Batch export has no atomicity: writers open the final path in `"w"` and de-dupe only against a per-run set, so a **re-run that fails at job K has already truncated the prior complete export** for jobs 1..K-1, with no manifest. Stage to a temp dir → rename on full success. | `features/batch_export/base_panel.py:1194/1418`, `_common.py:35` |

> Each finding in the pass sections below is tagged with its **ID** and status box. The prose is the evidence; the box is the live state.

---

## PASS 0 — Pre-Audit Inventory

### 0.1 Structural map
- **Two-mode shell** (`allwell.py` → `MainWindow`): Review pane (`well_viewer/runtime_app.py`) + Analyze pane (`analyze_tab.py`) in a `QStackedWidget`. A dataset folder is the unit of work flowing Analyze → Review.
- **Layout:** `well_viewer/` (194 modules: `core/ domain/ data/ persistence/ features/*`), `widgets/` (34), `services/` (8), `pipeline/` (8, Qt-free by design), plus top-level monoliths.
- **Largest modules:** `process_microscopy.py` (4696), `services/remote_runner.py` (4286), `features/grouping/cell_gating_tab_view.py` (2392), `domain/fold_change.py` (1755), `features/batch_export/base_panel.py` (1589), `features/image_browser/image_table/controller.py` (1475).
- **Critical shared hubs (highest audit priority):** `features/registry.py` (597 lines, **49 internal imports** — highest fan-in), `runtime_app.py` `WellViewerApp` god-object, `viewer_context.ctx.app` (**894 references across 20 files**).

### 0.2 AI-authorship markers
- **Clean of stale markers:** 1 `TODO`, 0 `FIXME`, 1 `HACK` — better than typical.
- **Error-swallowing at scale:** 573 broad `except` and 303 originally-`except: pass` sites, migrated to `swallowed()` / `aw_safe` helpers that log at **DEBUG only** (invisible in normal runs). See Pass 2.2.
- **Style switching between files:** comment density ranges from ~3.6% (`fold_change.py`) to ~17% (`remote_runner.py`).
- **Repeated shapes:** `save_to_data_dir`/`load_from_data_dir` ×10 each, `_build_qss` ×17, `_build_ui` ×5 — persistence/theming patterns (mostly legitimate; see Pass 5.1).

### 0.3 Iteration depth
Excluded from scope per reviewer direction (commit history not considered).

### Mitigating maturity (credit where due)
Self-aware housekeeping (`_Docs/Markdowns/Code_Diet.md`); guardrail tests (`test_no_silent_except`, `test_layering`, `test_monolith_budgets`, `test_architecture_contracts`); ruff + fast-lane pytest CI; an accurate `ARCHITECTURE.md`; and a genuinely Qt-free `pipeline/` layer.

---

## PASS 1 — Architectural Integrity

### 1.1 Orphan / dead modules
- **Medium — `widgets/_window_chrome.py`** (130+ lines): a full frameless-titlebar policy engine (`should_use_frameless`, `frameless_source`, accessibility probe) with **zero importers**. Dead code masquerading as live infrastructure. *Fix: delete, or wire into window construction if intended.* — **A9** `[ ]` TODO
- **Medium — `widgets/window_resize_grips.py`** (`WindowResizeGrips`): 8-grip resize helper, **never instantiated**. Companion to the above (both implement a custom-frame feature never adopted). *Fix: delete or adopt together.* — **A9** `[ ]` TODO
- **Low — `scripts/decode_mockup.py`, `scripts/make_static_fonts.py`**: standalone dev utilities with no production importers. *Fix: move under a labeled `tools/` dir.* — **A9** `[ ]` TODO
- **False positives (verified live):** persistence modules (`ratios.py`, `line_order.py`, `crop_region.py`, …) are wired via `persistence/registry.py:71-106`; `WellPlateZipper.py` via `input_resolution_service.py:14`; PyInstaller hooks are spec-consumed. → see [Non-goals](#non-goals--verified-correct--do-not-re-open).

### 1.2 Orphan state
Structural root cause: the "controllers" are **stateless function-modules operating on the `app` god-object**, not classes with `self` state (e.g. `channel_state_controller.py`, `selection_controller.py` are collections of `def foo(app, …)`). State lives as **dynamically-created `app._*` attributes** set by one function and read by another, with **no central declaration** — the exact structure that yields conditional-write / unconditional-read `AttributeError` risk.
The **only** dataset-reload reset is `load_directory` (`core/load_controller.py:121-224`), which clears `selection.*`, `plot.bar_order`, `channel.threshold_range_cache`, the image/review-image/zipfile caches — **and nothing else**. Everything below survives a dataset swap.

**Category C — uncleaned on reload (stale-data / silent mis-filtering; the biggest hazard):** — **X1** `[ ]` TODO

| attribute | write | read | risk |
|---|---|---|---|
| `_scatter_gates` | `persistence/cell_gating.py:115` | `stats_compute.py:227`, `scatter/gating.py:463+`, `cell_browser/collect.py:300` | **High** — `load_from_pipeline_info` early-returns when the new dataset has no `cell_gating` block (`cell_gating.py:59-60`), so the prior dataset's gates leak and silently filter cells in stats/scatter/cell-browser |
| `_review_included_overrides` | `runtime_app.py:327`, `review_image/controller.py:583` | `review_image/collect.py:238/252/382` | stale per-cell include/exclude overrides leak into the new dataset (only cleared on a crop change) |
| `_cell_gating_use_scatter_gates` / `_use_scatter_thresh` | `persistence/cell_gating.py:117-118` | `stats_compute.py:225`, `cell_gating_tab_view.py:985/1052` | inclusion-mode + fraction-on semantics (Gate_On vs ThreshFracOn) carry across the swap |
| `_cell_gating_gating_choices` / `_thresh_choices` | `persistence/cell_gating.py:133/135` | `cell_gating_tab_view.py:871/913/954` | old gate-name selections mis-select gates the new dataset lacks |
| `_active_heatmap_layout_name` | `runtime_app.py:263`, `heatmap/sidebar_view.py:272` | `heatmap/controller.py:49` | points at a layout the new dataset lacks (guarded → degrades) |

*Fix:* reset all of these in `load_directory`'s `begin_load` region **before** hydration, mirroring how `threshold_range_cache` and the image caches are already cleared. This is the single highest-value 1.2 fix.

**Category A/D — undeclared, guarded (mostly degrade, two can misbehave):** ~40 `getattr(app, "_x", default)` sites exist; each admits a writer may not have run. Two are not merely defensive: `_label_to_channel_key` (`channel_state_controller.py:297` write / `:801-806` **lossy lowercase fallback** → a ratio label read before the map is built resolves to a bogus real-channel key instead of `ratio:<name>`) and `_cell_gating_worker` (`cell_gating_tab_view.py:1117` **un-guarded** read, safe only by call-ordering). *Fix:* initialize these (`_label_to_channel_key`, `_seg_channel_token`, `_analyze_pane`, `_smfish_cancel_event`, `_thresh_frac_on_saved`) empty in `__init__`. — **X5** `[ ]` TODO

**Category B — genuinely dead state:** `_bar_quick_pair_dir`, `_bar_quick_iter_order` (`runtime_app.py:306-307`, never read; their live twins `_rep_quick_*` are read at `grouping/controller.py:42`), and the `_montage_chan_cb` placeholder (`runtime_app.py:288`, never read — the montage combo uses `_montage_chan_var`). *Fix: delete, or wire up like the working twins.* — **X13** `[ ]` TODO

- *Root-cause fix (tracked under A8):* move `app._*` reads/writes onto a typed `AppState`/per-feature dataclass (scaffolding already exists in `core/app_state.py`) with an explicit `reset()` on dataset reload.

### 1.3 Pattern consistency (god modules)
Intended pattern: `controller.py` + `tab_view.py` + `renderer.py` (+ `collect.py`). Canonical: `barplot/`, `multivariate/`.
- **High — `features/grouping/cell_gating_tab_view.py`** (2392): a "view" embedding domain compute (numpy, `ThreshFracOptimizationResult`, off-thread masking `lines 77-204`, Otsu orchestration). *Fix: extract `cell_gating_compute.py` + controller.* — **A3** `[ ]` TODO
- **High — `runtime_app.py` `WellViewerApp`** (1258): acknowledged god-object with `__getattr__` forwarder and dozens of externally-`setattr`'d handles. *Fix: continue the strangler migration to typed state.* — **A8** `[ ]` TODO
- **Medium-High — `features/scatter/callbacks.py`** (1190): non-canonical name mixing click-handlers + a full single-cell image viewer + raw Qt UI. *Fix: split `click_handlers.py` + a reusable `single_cell_viewer`.* — **A7** `[ ]` TODO
- **Medium — `features/batch_export/base_panel.py`** (1589): abandons the split for an inheritance hierarchy; a 1589-line base `QWidget` mixing UI + export orchestration. *Fix: pull export execution into a non-Qt service.* — **A14** `[ ]` TODO
- **Medium — `features/registry.py`** (49 internal imports): single-point recompile/fragility hub. *Fix: lazy/deferred feature descriptors.* — **A15** `[ ]` TODO
- **Low —** naming drift (`stats/view.py`, `grouping/view.py` vs `tab_view.py`; `export/` and `review_csv/` lack a controller). *Fix: normalize or document sanctioned exceptions.* — **A17** `[ ]` TODO

### 1.4 Abstraction audit
Key finding: the codebase has **no ABCs and no `typing.Protocol` at all** — so the risk is not over-abstraction via interfaces but **cosmetic indirection that provides no isolation**:
- **High — `core/widget_registry.py`**: `register()` is called **exactly once** (`lineplot/tab_view.py:31`); the entire `get/has/names/…` API is **never called**; and `register()` *also* does `setattr(self._app, name, widget)`, so state is stored **twice** and the one consumer reads it as `app._line_export_dock` anyway. Pure ceremony. *Fix: delete it (or drop the double-write and route reads through `get()`).* — **A6** `[ ]` TODO
- **Medium-High — `viewer_context.ctx.app`**: the DI context exists to decouple features from the god-object, yet `ctx.app` is referenced **894 times across 20 files** — the abstraction leaks so thoroughly it provides near-zero isolation. *Fix: treat the count as a burn-down metric; lint against new `ctx.app` uses.* — **A8** `[ ]` TODO
- **Low —** five registries exist; only `widget_registry` is unused. The rest (`feature_registry`, `persistence/registry`, `metric_registry`, `scope_registry`) earn their keep. → see Non-goals.

### 1.5 Dead code paths
- **High — `services/remote_runner.py:2835`: the entire local auto-threshold pass is dead.** `_run_local_auto_threshold()` starts with `sys.path.insert(0, str(repo_root()))`, but **`import sys` appears nowhere in the file** (verified). It raises `NameError` on its first line every time; the whole body is wrapped in a broad `except` (~`:2880`) that logs "local auto-threshold pass failed" and continues. Because per-batch passes are deliberately suppressed (`--no_auto_threshold`, `:2755`) on the assumption this pass runs, **batched remote runs never compute/write auto-threshold defaults**. *Fix: add `import sys`.* — **G1** `[ ]` TODO
- **Medium — `services/remote_runner.py:3983` vs `:4077`: FOLDER-mode out-zip can never converge.** `_verify_local_manifest` requires `{well}_out.zip` for any FULL run *including FOLDER mode* (`want_zip_artifact = output_filter is FULL`), but `_retry_missing_wells` gates the zip re-pull on `local_input_mode in (ZIP, FLAT, MIXED)` — **FOLDER is omitted** (verified). A FOLDER+FULL run whose out-zip failed to land loops verify→retry→verify forever and remote cleanup is skipped permanently. *Fix: make the two conditions consistent.* — **G2** `[ ]` TODO
- **Medium — `process_microscopy.py:2063-2065`: `qc_min_area`/`qc_max_area`/`qc_min_solidity` are structurally ignored.** Declared and forwarded from `main()` (`:4512-4514`) but never read; the helper `filter_debris` (`:1222`) is never called in-module (QC is deferred to the Review layer). *Fix: drop the dead params + `filter_debris`, or wire them in.* — **G3** `[ ]` TODO
- **Low — latent `NameError` in `features/grouping/controller.py:190`**: `Dict`/`Optional`/`List` are used in annotations with no `typing` import — safe only because `from __future__ import annotations` stringifies signatures. Also `make_replicate_pairs(toks, prefix)` (`:24`) never uses `prefix`. *Fix: add the import; drop the dead param.* — **G8** `[ ]` TODO
- **Low — phantom guards / redundant branches (verified):** `process_microscopy.py:554` (`not in exclude_exts` can never fire — whitelist and blacklist are disjoint); `:3915` (`isinstance(h, float)` always True, `else 0.0` unreachable); `:4430-4431` (redundant `else` recomputes identical `workers`/`threads_per_worker`); `remote_runner.py:2086` (`if not line: continue` unreachable); `remote_runner.py:671` (`except OSError: raise` is a no-op handler). *Fix: remove each.* — **G7** `[ ]` TODO
- **Low — unused imports beyond the F401 count:** `barplot/controller.py:8-18` (10 stranded imports post-`collect.py` extraction, no `noqa`); `analyze_tab.py:45-48` (4 imports live only in the form mixin). — **G5** `[ ]` TODO
- **Medium — 55 unused imports hidden by CI.** CI runs `ruff check .` (green), but `pyproject.toml` **ignores `F401`, `F811`, `F821`** — so `ruff --select F401` surfaces **55 unused imports** CI never reports. Many are stranded refactor residue: `barplot/controller.py:11-18` still imports `BarGroup`, `fold_change`, and five `stats_compute` functions that moved to `collect.py`; `review_image/controller.py:22-25` imports unused `numpy`/`pandas`/`data_loading` helpers. *Fix: run `ruff --select F401 --fix`, then re-enable F401 with targeted `# noqa` on genuine side-effect imports (e.g. `import tifffile  # codec registration`).* — **G5** `[ ]` TODO
- No `if True:`/`if False:` phantom guards found in the sampled controllers.
- Lead to confirm: `core/scope_registry.py` fold-change scope contributions were "relocated" to `features/registry.py:456` — verify no residual dead branch. (Tracked as a sub-check of A15.)
- **`vulture` sweep of the two largest files (completed):** `vulture --min-confidence 70` reports **zero** findings for both `process_microscopy.py` and `remote_runner.py`; at confidence 60 every hit was verified a **false positive**. Net: no additional confirmed dead code beyond the manual findings. → see Non-goals.

---

## PASS 2 — Asynchronous Logic & State Machine

Concurrency model: `threading.Thread` (11 live starts) + `QThread` (2) + `QTimer.singleShot` (29, uncancellable) + `ProcessPoolExecutor` (3) + `subprocess` (~25). No asyncio.

### 2.1 Async/thread exception surfacing
- **Critical — `services/remote_runner.py:2575`** (`_BatchedRemoteProcess._run_batches`): worker body is `try/…/finally` with **no `except`**. Any raise kills the daemon thread leaving `self._returncode = None`; `wait()` returns `rc if not None else 0` → **reports success (rc 0)** → `_run_pipeline_remote` calls `verify_and_cleanup(ok=True)` which can **delete the remote dataset though nothing ran**, with no logged error. *Fix: `except Exception: set rc=1, push error line`; treat dead-worker `None` rc as failure.* — **E1** `[ ]` TODO
- **High — `process_microscopy.py:4306/4695`**: per-well failures collected into `all_failed` are only `log.warning`'d; `main()` returns `None` and `__main__` never `sys.exit`s → an all-failed run exits 0. *Fix: `sys.exit(1)` when `all_failed` non-empty.* — **E5** `[ ]` TODO
- **High — `services/pipeline_runner.py:401-475`** (`_run_pipeline_thread`): body wrapped only in `try/finally` + a narrow `except TypeError`; a grouping/zipper `RuntimeError` skips to `finally`, emitting `("finished", None)` but **never `("error", …)`**. *Fix: add `except Exception: enqueue(("error", …))`.* — **E6** `[ ]` TODO
- **High — `features/grouping/cell_gating_tab_view.py:98`** (`GatingWorker.run`): calls `_run_inner()` with no try/except and the class has **no error signal**; a pandas error terminates the QThread, `finished_ok` never fires, UI hangs. *Fix: add `failed = Signal(str)`, wrap, always release the UI.* — **E7** `[ ]` TODO
- **High — `features/smfish/worker.py:227`**: `_run` has no try/except; a `pd.read_csv` `ParserError` kills the thread so `done_cb` never fires → "Apply Global Threshold" hangs. *Fix: wrap `_run`, route failure through `done_cb`.* — **E8** `[ ]` TODO
- **Medium — `services/remote_runner.py:3035/3091`** (`Fetcher._loop`→`_fetch_one`): `_fetch_one` called with no try/except; an unexpected exception kills the fetcher, **remaining wells never downloaded**, with only a misleading "N/M downloaded" line. *Fix: wrap the call, log, continue.* — **E12** `[ ]` TODO
- **Low —** `analyze_remote_dialog.py:590/676` unguarded `_runner`s → `QTimer` poll spins forever; `cell_browser/worker.py:191` DEBUG-only blank cell. *Fix: wrap runners, put a failure tuple on the queue.* — **E17** `[ ]` TODO
- **Verified correct:** `_RemoteProcess._pump`, `_run_pipeline_subprocess`/`_run_pipeline_remote`, `subprocess_kill.signal_subprocess`, `_AutoThresholdWorker`. → see Non-goals.

### 2.2 Error propagation
**The guardrail is narrow by design.** `test_no_silent_except.py` flags a handler only when it is *both* broad (`except:` / exactly `except Exception:`) *and* its body is a single `Pass`/`Continue`. What slips through (non-test counts): **96** `except <Type>: pass`, **34** `except (A, B): pass` tuple handlers, **122** broad/typed `return None`/`return False`, and all `except Exception: swallowed()` (DEBUG-only, `core/safe.py:76`). Uncovered files/dirs: `pipeline/auto_threshold.py`, `flatfield.py`, `shape_features.py`, `allwell.py`, `analyze_tab.py`, `widgets/`, `util/`, `ui/`, `scripts/`.

- **Critical — `process_microscopy.py:947`**: image loader `except Exception: return None` → corrupt/undecodable image indistinguishable from "no image" → FOV silently skipped → **under-counted cells / wrong per-well statistics**. *Fix: catch `(KeyError, OSError, BadZipFile, ValueError)`; log ERROR + record a failed FOV on anything else.* — **E2** `[ ]` TODO
- **Critical — `process_microscopy.py:4015`**: `except (OSError, JSONDecodeError): pass` while merging into existing `metadata.json` → a corrupt file silently drops preserved user blocks and overwrites on re-run → **irreversible loss**. *Fix: distinguish absent vs present-but-unreadable; abort the write and surface an error.* — **E3** `[ ]` TODO
- **Critical — `services/pipeline_service.py:93/97`** (+ `fft_*` blocks): CLI arg assembly `except (TypeError, ValueError, KeyError): pass` → a malformed option is silently omitted → **pipeline runs with different parameters than configured**. *Fix: validate up front; a bad value must raise/warn.* — **E4** `[ ]` TODO
- **High —** `process_microscopy.py:1561` (auto-threshold per-channel DEBUG-skip → silently biased threshold from a subset) + `data/auto_threshold.py:70` (same, viewer-side). *Fix: WARNING + report skipped counts.* — **E9** `[ ]` TODO
- **High —** `features/saved_plots/collect.py:81/101/119` (`return None` → saved plot silently loses its groups/stats config). *Fix: log ERROR; fail the save visibly.* — **E10** `[ ]` TODO
- **High —** `services/remote_runner.py:1153` (corrupt input zip silently treated as "upload whole well"). *Fix: WARNING naming the bad zip.* — **E11** `[ ]` TODO
- **Medium —** `remote_runner.py:4178/4228` (failed `scancel`/`kill` → lingering SLURM allocation unreported). — **E13** `[ ]` TODO
- **Medium —** `remote_runner.py:80` (`ALLWELL_SCRATCH_DIR` silently ignored on `OSError` → later ENOSPC). — **E14** `[ ]` TODO
- **Medium —** `image_table/image_compose.py:485/516/523` (corrupt mask → "no overlay" → user misreads QC). — **E15** `[ ]` TODO
- **Medium —** `persistence/view_state.py` (~25 `swallowed()` sites) + `sample_definitions.py` (persistence fails invisibly). — **E16** `[ ]` TODO
- **Top recommendation:** extend `test_no_silent_except.py` to flag tuple/typed `pass` and broad `return None` in `pipeline/`, `well_viewer/data/`, `well_viewer/persistence/`, add the missing pipeline files, and route data-integrity failures to at least WARNING. — **G4** `[ ]` TODO

### 2.3 Race conditions
- **High — `features/smfish/tab_view.py:752`** (`_smfish_apply_to_all`): launches a thread + `ThreadPoolExecutor` with **no re-entrancy guard**; a double-click runs two workers writing the **same per-well CSVs**, and the 2nd launch `.clear()`s the shared `app._smfish_cancel_event`. *Fix: single busy flag + per-run cancel event.* — **E18** `[ ]` TODO
- **Medium —** smfish `worker.py:265-279` TOCTOU (dataset swap during the write loop is never re-checked → late wells written into the swapped dataset). — **E19** `[ ]` TODO
- **Medium —** `remote_runner.py:2557-2571` (`_current_proc` read without a lock → Stop can miss the running child). — **E20** `[ ]` TODO
- **Medium —** `pipeline_runner.py:364-387` (`stop()` reads `_proc` assigned on the worker thread with no sync). — **E21** `[ ]` TODO
- **Medium —** `cell_browser/worker.py:202` (unbounded threads read a `zipfile_cache` a dataset swap can `invalidate()`). — **E22** `[ ]` TODO
- **Low —** playground `_ReferenceBridge` connected per call, never disconnected. — **E23** `[ ]` TODO
- **Verified correct:** the plate-selection debounce; `channel_state_controller` `blockSignals` discipline. → see Non-goals.

### 2.4 State lifecycle / teardown
**Root cause:** `AnalyzeTab` is mounted as a *child* widget (`allwell.py:225-229`); in Qt, `closeEvent` reaches only top-level windows, so **`AnalyzeTab.closeEvent` never fires**.
- **Critical — `allwell.py:918`**: `MainWindow.closeEvent` only saves geometry + cleans the review temp dir; it **never stops the runner**. Quitting mid-run orphans the local `subprocess.Popen`; for remote runs the `finally` teardown in `pipeline_runner._run_pipeline_remote:646-663` runs on a daemon thread killed abruptly → **remote SLURM allocation, remote staging dir, and local scratch all leak**. *Fix: in `MainWindow.closeEvent`, `self._analyze._runner.stop()` (and briefly wait) before `super().closeEvent`.* — **L1** `[ ]` TODO
- **Medium —** `analyze_tab.py:1035` `_poll_log` self-reschedules `singleShot(80,…)` forever with no stop condition. *Fix: stop re-arming when `not self._running`.* — **L2** `[ ]` TODO
- **Medium —** `runtime_app.py` has no `closeEvent`, so the smfish cancel event is never set on close and worker threads aren't joined at shutdown. *Fix: on close, set the cancel event + signal loaders to stop.* — **L3** `[ ]` TODO
- **Low —** `core/split_view.py:222-246` disconnects the app-global `focusChanged` only when Split View is toggled off, not on teardown. — **L4** `[ ]` TODO
- **Correctly paired:** `AnalyzeTab._eta_timer`; remote-dialog `_poll` self-termination; remote-dialog `closeEvent`s. → see Non-goals.

### 2.5 Boundary conditions — **a strength**
The sampled data/stats surface is unusually well-hardened; no Critical/High/Medium boundary defect found. → see Non-goals. Only Low residues: `data_loading.py:112-113` (`circ` computed before the `per>0` mask; result still correct, just wasteful) and `cell_gating_tab_view.py:2302/2329` (empty-vals CDF, harmless). *Fix: compute on the masked subset; early-return on empty vals.* — **S8** `[ ]` TODO

---

## PASS 4 — Logic & Business-Rule Integrity

### 4.1 Conditional exhaustiveness / float equality
- **High — timepoint tolerance mismatch.** `fold_change.py:75/342` deliberately uses a **relative** tolerance (`_TP_REL_TOL = 1e-3`) because a value like 2/3 h drifts ~3e-5 when parsed back from 4-sig-fig text — but the rest of the pipeline matches with **absolute `1e-6`**: `stats_compute.py:93/285/431`, `stats/controller.py:108`, `data_loading.py:611`, `scatter/gating.py:237/412`, `image_compose.py:467`. Effect: for any tp needing >4 sig figs, Statistics / per-cell KS / bar aggregation return "no data" while fold-change succeeds. *Fix: extract the relative matcher into a shared `domain` helper and use it everywhere.* — **S2** `[ ]` TODO
- **Low —** `stats/controller.py:407` reports the even-n median as the upper-middle element (`sv[len(sv)//2]`). *Fix: `statistics.median(...)`.* — **S6** `[ ]` TODO
- **Note (not defects):** `_filter_rows_by_timepoint` float `==` is safe; the optimizer's sign-safe gap is correct. → see Non-goals.

### 4.2 Return-value consistency
- **High — `domain/fold_change.py:1109` `build_cell_scaling`** takes only `fc_state=(vs_control_on, control_label, vs_t0_on)` — **no operator/order/baseline_tp** — always builds a multiplicative factor, and the renderer always **divides** (`barplot/renderer.py:207/355`). Meanwhile the bars honor per-axis ratio-vs-subtract, ctrl/t0 ordering, and the resolved `baseline_tp` (`barplot/collect.py:155-385`). Effect: with "subtract" selected, **bars subtract but overlaid per-cell distributions divide**. *Fix: thread `op_ctrl/op_t0/order/baseline_tp` into `build_cell_scaling` and use `scale_bar_value` semantics.* — **S1** `[ ]` TODO
- **Medium — `barplot/renderer.py:986`**: per-cell fold-change passes `target_t=target_ts[0]`, silently collapsing multi-timepoint selections to the first tp. *Fix: build scaling per (well, tp), or annotate/disable when multiple tps are active.* — **S3** `[ ]` TODO
- **Note (correct):** `multivariate_compute` and `resolve_value_series` return correctly-typed/indexed values on every edge path. → see Non-goals.

### 4.3 Data-flow integrity
- **Verified correct:** ratio division epsilon **clamp** (`data_loading.py:281-297`); stats entry validation; stats-group serialize/deserialize composition preservation. → see Non-goals.
- **Low — `data_loading.py:307`**: `_missing_col_warned` is keyed by `id(df)`, reused after GC → a legitimately-due warning can be suppressed, and the global set grows unbounded per session. *Fix: key by a stable dataset/well id.* — **S7** `[ ]` TODO

**Numeric input boundary (`analyze_tab.py` / `analyze_tab_form.py`) — every numeric field is a plain `QLineEdit` with no validator; `_validate_run_request:692-720` checks only folders/schema/tokens/background-well:**
- **High —** a plausible non-integer radius/FFT entry like `100.5` raises `ValueError` at `pipeline_service.py:92/96/100/104`, the `--tophat_radius_*` flag is **silently omitted**, and the pipeline falls back to its own default while the form still shows "100.5" → **measured intensities silently diverge from what the user configured**. *Fix: `QIntValidator`/`QSpinBox`, or block the run in `_validate_run_request`.* — **X4** `[ ]` TODO
- **Medium —** negative/zero values accepted unvalidated: `mesh_ratio=0` → downstream division-by-zero; negative radius/`tf_threads`/`remote_batch_size` pass through; negative `workers` silently dropped. **And** QC bounds: `_safe_qc_float:57-62` coerces bad text to `0.0`; inverted `min>max` area or `min_solidity>1` accepted → debris filter silently drops cells. *Fix: enforce ranges + `min<=max` + 0–1 solidity at entry.* — **X6** `[ ]` TODO

**`view_state.py` round-trip drift:**
- **Medium —** a schema-version bump silently discards **all** saved view-state (`view_state.py:1049-1052`; `_doc.py:89-94`) — no migration function. *Fix: add a `_migrate(state, from_version)` step, or surface the discard.* — **X7** `[ ]` TODO
- **Medium —** `export_style_prefs` restore **merges** into the full default dict (`:466-470`) and `snapshot` writes the whole dict (`:72`) → defaults re-persisted as user-set, so a future default change is silently overridden. *Fix: persist only keys differing from `DEFAULT_EXPORT_STYLE_PREFS`; replace, not merge.* — **X8** `[ ]` TODO
- **Low —** `selected_wells`/`solo_wells` narrowed to the current dataset on restore (`:366/384`) then re-saved (`:45`) → a subset-load-then-save irreversibly drops absent wells. *Fix: preserve unmatched saved wells.* — **X14** `[ ]` TODO
- **Clean:** every other `snapshot` key is read back by `restore`. → see Non-goals.

### 4.4 Transaction / atomicity
**The atomic single-file layer is exemplary and the remote-cleanup *ordering* is correct** — the gaps are where a *silent* success or partial slips past that ordering.
- **Critical — `remote_runner.py:2553` / `_run_batches:2575`** (same defect as E1, CLI path): `wait()` coerces a never-set `None` returncode to `0`, so a crashed batched run reports success to `scripts/allwell_remote.py:192` → `verify_and_cleanup(ok=True)` → empty expected-set verifies as "all 0 wells present" → `rm -rf` the run dir (+ dataset cache under `cleanup_mode="all"`). The **GUI path is only *accidentally* safe** because `pipeline_runner.py:591` reads `.returncode` (raw `None`). *Fix: as E1 — plus align both callers on `.returncode`.* → tracked under **E1**.
- **High — `remote_runner.py:3309/3190/3211`**: `_materialize` does `shutil.copyfile(...)` **straight to the final `{well}_out.zip`** (truncating any prior good copy) with its `OSError` only logged; control then unconditionally `fetched_wells.add(well)` and, in batched mode, `_delete_well_files_on_remote(well)` → an interrupted copy marks the well fetched **and deletes the only complete (remote) copy**. *Fix: copy to `.tmp` then `os.replace`; add-to-fetched + delete-remote only after confirming a non-empty local artifact.* — **X2** `[ ]` TODO
- **Medium — `remote_runner.py:3106/3133-3138`**: on the incomplete-fetch branch `tmp_zip` is returned without unlinking (only `:3187` cleans up) → partial temp zips accumulate. *Fix: `try/finally` unlink.* — **X9** `[ ]` TODO
- **Medium — `remote_runner.py:2810-2824`**: the `finally` calls `flush_and_join()` **before** enqueuing the `None` stdout sentinel → if teardown raises, the reader loop (`pipeline_runner.py:589`) blocks forever. *Fix: enqueue the sentinel first / in its own inner `finally`.* — **X10** `[ ]` TODO
- **Batch export (`batch_export/`, `export/service.py`) — no transactional guarantee at any level:** re-run overwrites a prior *complete* export in place (`unique_export_stem` de-dupes only against a per-run set; writers open the final path in `"w"` at `base_panel.py:1418`, `bar_panel.py:355`, `scatter_panel.py:456+`); no cross-job rollback (`_run_batch_jobs:1194-1226` continue-on-error, no manifest); per-job CSV-then-figure is non-atomic. *Fix: stage to a temp subdir → confirm → `os.replace`/commit on full success → write a `manifest.json`.* — **X3** `[ ]` TODO
- **Verified robust (credit):** `persistence/_io.py`; `pipeline/io.py` merge helpers; `remote_runner._zip_well_folder:737`; cleanup ordering; `_stage_mixed_mode`. → see Non-goals.

### 4.5 Concurrency invariants / caches
- **Medium — `domain/stats_compute.py:347` (+ siblings `:381/415/452`)**: `stats_cache` keys omit the **on-population definition** returned by `_thresh_on_col(app)` (driven by `_cell_gating_use_scatter_thresh`, thresh-layer choices, gate geometry). `_notify_gates_changed` (`scatter/gating.py:431`) does **not** invalidate the stats cache → a gate resize/rename can return **stale cached statistics**. *Fix: add a hash of the active gate set + thresh choices to all four keys, and/or invalidate from `_notify_gates_changed`.* — **S4** `[ ]` TODO
- **Low/Medium — `image_table/image_compose.py:447-476`**: after an exact `(fov, tp)` miss, `_load_array` falls back to a numeric-close (`<1e-6`) any-match and, when `fov` is empty, the **first** available FOV → can surface a neighboring FOV/timepoint image. *Fix: prefer "no image" over a numeric-close tp match; annotate non-exact fallbacks.* — **S5** `[ ]` TODO
- **Verified correct — `data/zipfile_cache.py`**: keyed by `Path`, validates `st_mtime_ns` on every access, LRU-bounded. → see Non-goals.

**Matplotlib global state + module caches (deep-dive) — this surface is clean; no High/Medium defect.**
- **Low — `batch_export/base_panel.py:1238-1251` and `core/plot_orchestrator.py:105-121`** hand-roll `rcParams` save/restore around `savefig` instead of `with rc_context({...})` (already used at `widgets/fig_clipboard.py:31`, `export/sidebar_view.py:992`). Safe today but brittle. *Fix: switch to `rc_context`.* — **X17** `[ ]` TODO
- **Info —** `plt.close(fig)` on figures pyplot never registered (`image_table/export.py:447`, `base_panel.py:1230/1252`) — harmless but misleading. (Folded into X17.)
- **Verified correct (credit):** no pyplot state-machine in production; colormaps `.copy()`'d before `set_bad`; `rcParams` defaults set once at import; instance-scoped image-decode cache; the only production `lru_cache` is `_rsync_version` (`maxsize=1`); idempotent font-bootstrap; `force=False` backend selection. → see Non-goals.

---

## PASS 5 — Code Quality & Maintainability

### 5.1 Duplication
- **High — data-correctness duplication:**
  - `find_image_groups` vs `find_image_groups_in_zip` (`process_microscopy.py:2234-2382` vs `2144-2233`, ~90 lines each): same stem/channel/FOV/tp parsing, once over a directory and once over a `ZipFile` namelist. Segmentation correctness depends on both parsing identically. *Fix: one grouping fn taking an entry-iterator.* — **A1** `[ ]` TODO
  - `services/remote_runner.py:3252-3327` reimplements zip-extract + CSV-append that `pipeline/io.py` already centralizes. *Fix: call the `pipeline.io` merge helper.* — **A2** `[ ]` TODO
- **Medium —** `process_well_folder_task`/`process_well_zip_task` + `process_well_folders`/`process_well_zips` (identical phase sequences + ~130-line executor scaffolding). *Fix: parametrize a shared driver by task callable.* — **A10** `[ ]` TODO
- **Medium —** rsync-over-ssh transport+retry in **three** overlapping impls (`remote_runner.py:2028/2150/2246`). *Fix: one cancellable retrying runner.* — **A11** `[ ]` TODO
- **Medium —** the barplot fold-change mean/frac/n triplet pasted **6×** (`barplot/collect.py:265-387/496-579`). *Fix: loop over a field-spec tuple.* — **A12** `[ ]` TODO
- **Medium —** the annotated-heatmap block ×4 in `multivariate/renderer.py`. *Fix: one `_annotated_matrix(ax, mat, cmap, fmt)` helper.* — **A13** `[ ]` TODO
- **Low —** helper-exists-but-re-inlined: `scatter/_layout.py:46 _style_scatter_ax` re-inlined at `scatter/controller.py:83/267`; `distribution/controller.py:143` mirrors `scatter/_layout.py:66`; `timepoint_color_map` duplicated in `multivariate/renderer.py:82`; persistence `_coerce_*` map helpers with **drifted** NaN handling. *Fix: call the shared helpers.* — **A16** `[ ]` TODO
- **Positive:** well-plate coordinate math is centralized; rsync transport/retry and `pipeline/io.py` CSV/zip merge are single-source. → see Non-goals.

### 5.2 Complexity (radon CC; "untested" = no test references the function)
| Function | file:line | CC | Tested? |
|---|---|---|---|
| `find_well_images_and_masks` | `data/image_discovery.py:339` | **98** | **0 refs** |
| `_apply_image_table` | `persistence/view_state.py:771` | 93 | indirect |
| `apply_export_style_prefs` | `core/plot_style.py:327` | 85 | 4 refs |
| `restore` | `persistence/view_state.py:352` | 73 | 15 refs |
| `redraw_line_plots` | `features/lineplot/controller.py:361` | **69** | **0 refs** |
| `main` | `process_microscopy.py:4306` | 65 | e2e only |
| `run_stats` | `features/stats/controller.py:283` | **52** | **0 refs** |

The monolith budgets constrain *file size* but not *function complexity*.
- **Priority — characterization tests for CC-98 `find_well_images_and_masks`, then decompose.** — **A4** `[ ]` TODO
- **Characterization tests for CC-69 `redraw_line_plots` + CC-52 `run_stats`, then decompose.** — **A5** `[ ]` TODO
- **Add an intra-file function-complexity ratchet alongside the line-budget ratchets.** — **G6** `[ ]` TODO

### 5.3 Test quality — **a strength**
~75% behavioral / ~20% presence-or-structural / ~5% weak. Enforcement tests are genuine (`test_monolith_budgets`, `test_architecture_contracts`). **Under-tested:** `batch_export` render path + the CC-52/69/98 functions above (tracked under A4/A5). → see Non-goals.

### 5.4 Logging security
No credential/token literals are logged anywhere; `ssh_key_setup.py` delegates passphrases to ssh's native prompt; no stdout `print()` leaks in production paths. → see Non-goals for credit.
- **Medium — `services/ssh_key_setup.py:66-68`**: SSH keys are generated **without a passphrase** by design → a passwordless private key grants remote cluster access to anyone with home-dir read access. *Fix: offer an optional passphrase, or document the residual risk.* — **X11** `[ ]` TODO
- **Low —** the bootstrap log (`scripts/_bootstrap_lib.py:138-160`) is never auto-deleted and captures host + home-dir paths; `remote_runner._log` echoes `ssh_alias`/`remote_output_dir` at INFO. *Fix: retention cap / "clear logs" action.* — **X15** `[ ]` TODO
- **Low —** full `traceback.format_exc()` written to per-well error files and debug panels (`process_microscopy.py:209`, `scatter/callbacks.py:313/1145`, `cell_gating_tab_view.py:1177`). *Fix: keep full tracebacks in dev-gated sinks; show users a short message.* — **X16** `[ ]` TODO

### 5.5 Environment configuration
No central startup validation exists, but this is **Low risk**: every env *read* uses `.get(...)` with a default or is `if`-guarded. The residual weakness is that misconfiguration surfaces as a **silent fallback** rather than a clear error, with reads scattered across 10 files. *Fix: a small `services/env.py` that reads the ~6 `ALLWELL_*`/config vars once at startup, logs active overrides, and validates provided paths exist.* — **X12** `[ ]` TODO

---

## Non-goals — verified correct / do NOT re-open

Sections the audit checked and found sound; folded here so nobody re-audits them. (Mirrors `Code_Diet.md`'s Non-goals.)

- **Boundary/numeric handling (Pass 2.5)** — `_aggregate_arrays`, `resolve_value_series`, `_beeswarm_jitter`, `fold_change` scale fns, `multivariate_compute`, all JSON readers are systematically guarded against empty/zero/single-item/zero-variance inputs.
- **Atomic single-file writes (Pass 4.4)** — `persistence/_io.py` `atomic_write_json`/`atomic_write_text`; `pipeline/io.py` `merge_records_into_csv`/`merge_images_into_zip`; `remote_runner._zip_well_folder`; remote-cleanup ordering; `_stage_mixed_mode`.
- **Caches / global state (Pass 4.5)** — `data/zipfile_cache.py` (mtime-validated); no pyplot state-machine in production; colormaps copied before mutation; instance-scoped decode cache; bounded `lru_cache`; idempotent font bootstrap.
- **Data-flow (Pass 4.3)** — ratio epsilon-clamp division; stats entry validation; stats-group serialize/deserialize composition preservation; view_state round-trip completeness (all keys read back).
- **Async correctness (Pass 2.1/2.3)** — `_RemoteProcess._pump` sentinel; `_run_pipeline_subprocess`/`_run_pipeline_remote`; `subprocess_kill.signal_subprocess`; `_AutoThresholdWorker`; plate-selection debounce; `channel_state_controller` `blockSignals`.
- **Teardown that IS paired (Pass 2.4)** — `AnalyzeTab._eta_timer`; remote-dialog `_poll` self-termination; remote-dialog `closeEvent`s.
- **Architecture (Pass 1)** — persistence/`WellPlateZipper` "orphans" are live via the registry; the 4 non-`widget_registry` registries earn their keep; `vulture` found no additional dead code in the two monoliths.
- **Test quality (Pass 5.3)** — the suite is ~75% behavioral; `test_monolith_budgets`/`test_architecture_contracts` are genuine locks.
- **Logging security (Pass 5.4)** — no credential/token/PII leaks; passphrases delegated to ssh's native prompt; no debug `print()` in production paths.
- **Conditional logic (Pass 4.1/4.2)** — `_filter_rows_by_timepoint` float `==` is safe; the optimizer's sign-safe gap is correct; `multivariate_compute`/`resolve_value_series` return correctly-typed values on edge paths.

---

## Status log

> Append a dated line whenever a status box changes. Newest first.

- 2026-07-02 **Tracker created** — converted the static audit into a living work plan; seeded **77 items** (E 23 · S 8 · L 4 · G 8 · A 17 · X 17), all `[ ]` TODO. Umbrella issue [#598](https://github.com/exzktly/allwell/issues/598). 0 resolved.

**Last updated:** 2026-07-02 — tracker created; **0 / 77 resolved.**
