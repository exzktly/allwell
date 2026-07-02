# All-Well — Code Review Audit Report

**Date:** 2026-07-02
**Scope:** `/home/user/allwell` — PySide6 desktop app for fluorescence-microscopy well-plate analysis. ~90,462 LOC non-test across 267 modules; 179 test files (~800 fast-lane tests).
**Method:** Structured multi-pass audit (Pass 0 inventory; Pass 1 architecture; Pass 2 async/state; Pass 4 logic/business rules; Pass 5 quality). Findings verified by direct file reads; every item carries `file:line`, severity, and an actionable fix. No source files were modified.

---

## Executive summary

All-Well is an **unusually well-structured** codebase for its size and generation style: it has enforced monolith line-budget ratchets, AST-based architecture-contract tests, a clean Qt-free `pipeline/` layer, a persistence registry, and a genuinely behavioral test suite (~75% behavioral asserts). The numeric/compute layer is systematically hardened against empty/zero/single-item inputs — a real strength.

The debt is concentrated and predictable, and it clusters into **five actionable themes**:

1. **"Silent success" is the dominant failure mode.** Worker-thread exceptions vanish and the layer above reads a default that *means success*. On remote runs this chains into `verify_and_cleanup(ok=True)` **deleting real data though nothing ran**. (Pass 2 — highest priority.)
2. **Two scientific-correctness bugs** produce wrong numbers silently: per-cell overlays use a different fold-change transform than the bars on the same plot, and timepoint matching uses inconsistent tolerances so fractional timepoints silently fail to match. (Pass 4.)
3. **App teardown is structurally broken**: `AnalyzeTab.closeEvent` is dead code (child-widget mount), so quitting during a run orphans local subprocesses and leaks remote SLURM allocations. (Pass 2.4.)
4. **Guardrails create false confidence.** The silent-except test only bans literal `except Exception: pass`; the far more common `return None`, tuple/typed `pass`, and DEBUG-only `swallowed()` patterns all pass it — and several critical files aren't even scanned. Likewise, CI's ruff config disables F401, hiding 55 unused imports. (Pass 2.2 / Pass 5.)
5. **Concentrated god-modules and copy-paste**: `cell_gating_tab_view.py` (2392 lines, view + domain), a CC-98 untested discovery function, and pipeline group-discovery / CSV-merge logic duplicated across the local and remote paths (divergence changes results). (Pass 1 / Pass 5.)

### Top 12 prioritized actions

| # | Severity | Area | Action | Anchor |
|---|----------|------|--------|--------|
| 1 | **Critical** | 2.1 | `_run_batches` worker has `try/finally` with no `except` → rc stays `None` → `wait()` returns 0 → success reported → remote data deleted. Wrap body, set rc=1 on error; treat dead-worker `None` rc as failure. | `services/remote_runner.py:2575` |
| 2 | **Critical** | 2.4 | Wire `MainWindow.closeEvent` to stop the runner + cancel workers. Today quitting mid-run orphans the pipeline subprocess and leaks remote SLURM/staging/scratch. | `allwell.py:918` |
| 3 | **Critical** | 2.2 | Image loader `except Exception: return None` makes a corrupt image indistinguishable from "no image" → undercounted cells / wrong per-well stats. Narrow the catch; record a failed FOV. | `process_microscopy.py:947` |
| 4 | **Critical** | 2.2 | Corrupt existing `metadata.json` is swallowed (`except (OSError, JSONDecodeError): pass`) then overwritten → irreversible loss of user `cell_gating`/`sample_definitions`/`ratios`/`notes`. | `process_microscopy.py:4015` |
| 5 | **High** | 2.1 | Per-well failures never set the process exit code → an all-failed run exits 0 → remote layer reports success and cleans up. `sys.exit(1)` when `all_failed` is non-empty. | `process_microscopy.py:4306/4695` |
| 6 | **High** | 4.2 | `build_cell_scaling` ignores fold-change operator/order/baseline_tp and always divides → per-cell violin/beeswarm overlays disagree with the bars on the same plot (subtract vs divide). | `domain/fold_change.py:1109`, `features/barplot/renderer.py:207/986` |
| 7 | **High** | 4.1 | Timepoint matching uses absolute `1e-6` in stats/aggregation but relative `1e-3` in fold-change → fractional timepoints silently unmatched ("no data") in Statistics while fold-change succeeds. Share one tolerance. | `domain/stats_compute.py:93/285/431` + 5 sites |
| 8 | **High** | 1.3 | `cell_gating_tab_view.py` (2392 lines) is a "view" embedding domain compute (numpy, optimizer dataclass, off-thread masking). Extract compute into `cell_gating_compute.py` + a controller. | `features/grouping/cell_gating_tab_view.py` |
| 9 | **High** | 5.2 | `find_well_images_and_masks` (CC 98) — highest-complexity function in the repo, **zero direct tests**, drives which images/masks feed the viewer. Add characterization tests, then decompose. | `well_viewer/data/image_discovery.py:339` |
| 10 | **High** | 5.1 | Pipeline group-discovery (`find_image_groups` vs `_in_zip`) and CSV/zip merge are duplicated across local and remote-fetch paths — a parsing/schema fix on one path is silently missed on the other. Unify. | `process_microscopy.py:2144/2234`; `services/remote_runner.py:3252` |
| 11 | **High** | 2.3 | "Apply Global Threshold" has no re-entrancy guard; a double-click runs two workers writing the same CSVs and the 2nd `.clear()`s the shared cancel event. Add a busy flag + per-run cancel event. | `features/smfish/tab_view.py:752` |
| 12 | **High** | 2.2 | Malformed tophat/FFT CLI options are swallowed (`except (TypeError, ValueError, KeyError): pass`) → the pipeline runs with **different parameters than configured**. Validate up front. | `services/pipeline_service.py:93/97` |
| 13 | **High** | 1.5 | `_run_local_auto_threshold` calls `sys.path.insert` but `sys` is never imported → `NameError` on line 1, swallowed → batched remote runs **never compute auto-threshold defaults** (per-batch passes are suppressed on the assumption this runs). Add `import sys`. | `services/remote_runner.py:2835` |

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
- **Medium — `widgets/_window_chrome.py`** (130+ lines): a full frameless-titlebar policy engine (`should_use_frameless`, `frameless_source`, accessibility probe) with **zero importers**. Dead code masquerading as live infrastructure. *Fix: delete, or wire into window construction if intended.*
- **Medium — `widgets/window_resize_grips.py`** (`WindowResizeGrips`): 8-grip resize helper, **never instantiated**. Companion to the above (both implement a custom-frame feature never adopted). *Fix: delete or adopt together.*
- **Low — `scripts/decode_mockup.py`, `scripts/make_static_fonts.py`**: standalone dev utilities with no production importers. *Fix: move under a labeled `tools/` dir.*
- **False positives (verified live):** persistence modules (`ratios.py`, `line_order.py`, `crop_region.py`, …) are wired via `persistence/registry.py:71-106`; `WellPlateZipper.py` via `input_resolution_service.py:14`; PyInstaller hooks are spec-consumed.

### 1.2 Orphan state
Structural root cause: the "controllers" are **stateless function-modules operating on the `app` god-object**, not classes with `self` state (e.g. `channel_state_controller.py`, `selection_controller.py` are collections of `def foo(app, …)`). State lives as **dynamically-created `app._*` attributes** set by one function and read by another, with **no central declaration** — the exact structure that yields conditional-write / unconditional-read `AttributeError` risk.
- **`runtime_app.py:923`** reads review-image colors via `getattr(self, attr, DEFAULTS[which])` — the defensive default is itself an admission the attribute may never have been created.
- *Fix:* move `app._*` reads/writes onto a typed `AppState`/per-feature dataclass (scaffolding already exists in `core/app_state.py`) with an explicit `reset()` on dataset reload.
- *Gap:* the exhaustive per-attribute write-site/read-site table was not completed (delegated sub-agent did not return).

### 1.3 Pattern consistency (god modules)
Intended pattern: `controller.py` + `tab_view.py` + `renderer.py` (+ `collect.py`). Canonical: `barplot/`, `multivariate/`.
- **High — `features/grouping/cell_gating_tab_view.py`** (2392): a "view" embedding domain compute (numpy, `ThreshFracOptimizationResult`, off-thread masking `lines 77-204`, Otsu orchestration). *Fix: extract `cell_gating_compute.py` + controller.*
- **High — `runtime_app.py` `WellViewerApp`** (1258): acknowledged god-object with `__getattr__` forwarder and dozens of externally-`setattr`'d handles. *Fix: continue the strangler migration to typed state.*
- **Medium-High — `features/scatter/callbacks.py`** (1190): non-canonical name mixing click-handlers + a full single-cell image viewer + raw Qt UI. *Fix: split `click_handlers.py` + a reusable `single_cell_viewer`.*
- **Medium — `features/batch_export/base_panel.py`** (1589): abandons the split for an inheritance hierarchy; a 1589-line base `QWidget` mixing UI + export orchestration. *Fix: pull export execution into a non-Qt service.*
- **Medium — `features/registry.py`** (49 internal imports): single-point recompile/fragility hub. *Fix: lazy/deferred feature descriptors.*
- **Low —** naming drift (`stats/view.py`, `grouping/view.py` vs `tab_view.py`; `export/` and `review_csv/` lack a controller). *Fix: normalize or document sanctioned exceptions.*

### 1.4 Abstraction audit
Key finding: the codebase has **no ABCs and no `typing.Protocol` at all** — so the risk is not over-abstraction via interfaces but **cosmetic indirection that provides no isolation**:
- **High — `core/widget_registry.py`**: `register()` is called **exactly once** (`lineplot/tab_view.py:31`); the entire `get/has/names/…` API is **never called**; and `register()` *also* does `setattr(self._app, name, widget)`, so state is stored **twice** and the one consumer reads it as `app._line_export_dock` anyway. Pure ceremony. *Fix: delete it (or drop the double-write and route reads through `get()`).*
- **Medium-High — `viewer_context.ctx.app`**: the DI context exists to decouple features from the god-object, yet `ctx.app` is referenced **894 times across 20 files** — the abstraction leaks so thoroughly it provides near-zero isolation. *Fix: treat the count as a burn-down metric; lint against new `ctx.app` uses.*
- **Low —** five registries exist; only `widget_registry` is unused. The rest (`feature_registry`, `persistence/registry`, `metric_registry`, `scope_registry`) earn their keep.

### 1.5 Dead code paths
- **High — `services/remote_runner.py:2835`: the entire local auto-threshold pass is dead.** `_run_local_auto_threshold()` starts with `sys.path.insert(0, str(repo_root()))`, but **`import sys` appears nowhere in the file** (verified). It raises `NameError` on its first line every time; the whole body is wrapped in a broad `except` (~`:2880`) that logs "local auto-threshold pass failed" and continues. Because per-batch passes are deliberately suppressed (`--no_auto_threshold`, `:2755`) on the assumption this pass runs, **batched remote runs never compute/write auto-threshold defaults**. *Fix: add `import sys`.* (A textbook case of the Pass 2.2 silent-swallow theme hiding a real bug.)
- **Medium — `services/remote_runner.py:3983` vs `:4077`: FOLDER-mode out-zip can never converge.** `_verify_local_manifest` requires `{well}_out.zip` for any FULL run *including FOLDER mode* (`want_zip_artifact = output_filter is FULL`), but `_retry_missing_wells` gates the zip re-pull on `local_input_mode in (ZIP, FLAT, MIXED)` — **FOLDER is omitted** (verified). A FOLDER+FULL run whose out-zip failed to land loops verify→retry→verify forever and remote cleanup is skipped permanently. *Fix: make the two conditions consistent.*
- **Medium — `process_microscopy.py:2063-2065`: `qc_min_area`/`qc_max_area`/`qc_min_solidity` are structurally ignored.** Declared and forwarded from `main()` (`:4512-4514`) but never read; the helper `filter_debris` (`:1222`) is never called in-module (QC is deferred to the Review layer). *Fix: drop the dead params + `filter_debris`, or wire them in.*
- **Low — latent `NameError` in `features/grouping/controller.py:190`**: `Dict`/`Optional`/`List` are used in annotations with no `typing` import — safe only because `from __future__ import annotations` stringifies signatures. Also `make_replicate_pairs(toks, prefix)` (`:24`) never uses `prefix`. *Fix: add the import; drop the dead param.*
- **Low — phantom guards / redundant branches (verified):** `process_microscopy.py:554` (`not in exclude_exts` can never fire — whitelist and blacklist are disjoint); `:3915` (`isinstance(h, float)` always True, `else 0.0` unreachable); `:4430-4431` (redundant `else` recomputes identical `workers`/`threads_per_worker`); `remote_runner.py:2086` (`if not line: continue` unreachable — the iterator only yields non-empty lines); `remote_runner.py:671` (`except OSError: raise` is a no-op handler, comment unfulfilled).
- **Low — unused imports beyond the F401 count:** `barplot/controller.py:8-18` (10 stranded imports post-`collect.py` extraction, no `noqa`); `analyze_tab.py:45-48` (4 imports live only in the form mixin).
- **Medium — 55 unused imports hidden by CI.** CI runs `ruff check .` (green), but `pyproject.toml` **ignores `F401`, `F811`, `F821`** — so `ruff --select F401` surfaces **55 unused imports** CI never reports. Many are stranded refactor residue: `barplot/controller.py:11-18` still imports `BarGroup`, `fold_change`, and five `stats_compute` functions that moved to `collect.py`; `review_image/controller.py:22-25` imports unused `numpy`/`pandas`/`data_loading` helpers. *Fix: run `ruff --select F401 --fix`, then re-enable F401 with targeted `# noqa` on the genuine side-effect imports (e.g. `import tifffile  # codec registration`) rather than a blanket ignore.*
- No `if True:`/`if False:` phantom guards found in the sampled controllers.
- Lead to confirm: `core/scope_registry.py` fold-change scope contributions were "relocated" to `features/registry.py:456` — verify no residual dead branch.
- *Gap:* the unreachable-branch/unused-return sweep of the two largest files (`process_microscopy.py`, `remote_runner.py`) was not completed. *Fix: run `vulture`/`pyflakes` on those two specifically.*

---

## PASS 2 — Asynchronous Logic & State Machine

Concurrency model: `threading.Thread` (11 live starts) + `QThread` (2) + `QTimer.singleShot` (29, uncancellable) + `ProcessPoolExecutor` (3) + `subprocess` (~25). No asyncio.

### 2.1 Async/thread exception surfacing
- **Critical — `services/remote_runner.py:2575`** (`_BatchedRemoteProcess._run_batches`): worker body is `try/…/finally` with **no `except`**. Any raise (e.g. `Popen(ssh_argv)` → `FileNotFoundError`) kills the daemon thread leaving `self._returncode = None`; `wait()` returns `rc if not None else 0` → **reports success (rc 0)** → `_run_pipeline_remote` emits `("done")` and calls `verify_and_cleanup(ok=True)` which can **delete the remote dataset though nothing ran**, with no logged error. *Fix: `except Exception: set rc=1, push error line`; treat dead-worker `None` rc as failure.*
- **High — `process_microscopy.py:4306/4695`**: per-well failures collected into `all_failed` are only `log.warning`'d; `main()` returns `None` and `__main__` never `sys.exit`s → an all-failed run exits 0 → remote layer reports success. *Fix: `sys.exit(1)` when `all_failed` non-empty.*
- **High — `services/pipeline_runner.py:401-475`** (`_run_pipeline_thread`): body wrapped only in `try/finally` + a narrow `except TypeError`; a grouping/zipper `RuntimeError` skips to `finally`, emitting `("finished", None)` but **never `("error", …)`** → GUI shows "finished" with no failure reason. *Fix: add `except Exception: enqueue(("error", …))` (matches the correct `_run_pipeline_subprocess`).*
- **High — `features/grouping/cell_gating_tab_view.py:98`** (`GatingWorker.run`): calls `_run_inner()` with no try/except and the class has **no error signal**; a pandas error terminates the QThread, `finished_ok` never fires, UI hangs. The sibling `_AutoThresholdWorker:1158` does it correctly. *Fix: add `failed = Signal(str)`, wrap, always release the UI.*
- **High — `features/smfish/worker.py:227`**: `_run` has no try/except; a `pd.read_csv` `ParserError` kills the thread so `done_cb` never fires → "Apply Global Threshold" hangs. *Fix: wrap `_run`, route failure through `done_cb`.*
- **Medium — `services/remote_runner.py:3035/3091`** (`Fetcher._loop`→`_fetch_one`): `_fetch_one` called with no try/except; an unexpected exception kills the fetcher, `flush_and_join` returns immediately, **remaining wells never downloaded**, with only a misleading "N/M downloaded" line. *Fix: wrap the call, log, continue.*
- **Low —** `analyze_remote_dialog.py:590/676` unguarded `_runner`s → `QTimer` poll spins forever with the button stuck; `cell_browser/worker.py:191` DEBUG-only blank cell.
- **Verified correct:** `_RemoteProcess._pump` (sentinel prevents reader hang), `_run_pipeline_subprocess`/`_run_pipeline_remote` (rc checked, error emitted), `subprocess_kill.signal_subprocess` (a genuinely good must-not-raise best-effort), `_AutoThresholdWorker`.

### 2.2 Error propagation
**The guardrail is narrow by design.** `test_no_silent_except.py` flags a handler only when it is *both* broad (`except:` / exactly `except Exception:`) *and* its body is a single `Pass`/`Continue`. What slips through (non-test counts): **96** `except <Type>: pass`, **34** `except (A, B): pass` tuple handlers, **122** broad/typed `return None`/`return False`, and all `except Exception: swallowed()` (DEBUG-only, `core/safe.py:76`). Uncovered files/dirs: `pipeline/auto_threshold.py`, `flatfield.py`, `shape_features.py`, `allwell.py`, `analyze_tab.py`, `widgets/`, `util/`, `ui/`, `scripts/`.

- **Critical — `process_microscopy.py:947`**: image loader `except Exception: return None` → corrupt/undecodable image indistinguishable from "no image" → FOV silently skipped → **under-counted cells / wrong per-well statistics**. *Fix: catch `(KeyError, OSError, BadZipFile, ValueError)`; log ERROR + record a failed FOV on anything else.*
- **Critical — `process_microscopy.py:4015`**: `except (OSError, JSONDecodeError): pass` while merging into existing `metadata.json` → a corrupt/unreadable file silently drops preserved user blocks (`cell_gating`, `sample_definitions`, `ratios`, `notes`) and overwrites on re-run → **irreversible loss**. (Tuple handler → invisible to the test.) *Fix: distinguish absent vs present-but-unreadable; abort the write and surface an error.*
- **Critical — `services/pipeline_service.py:93/97`** (+ `fft_*` blocks): CLI arg assembly `except (TypeError, ValueError, KeyError): pass` → a malformed tophat/FFT option is silently omitted → **pipeline runs with different parameters than configured**. *Fix: validate up front; a bad value must raise/warn.*
- **High —** `process_microscopy.py:1561` (auto-threshold per-channel DEBUG-skip → silently biased threshold from a subset); `data/auto_threshold.py:70` (same, viewer-side); `features/saved_plots/collect.py:81/101/119` (`return None` → saved plot silently loses its groups/stats config); `services/remote_runner.py:1153` (corrupt input zip silently treated as "upload whole well").
- **Medium —** `remote_runner.py:4178/4228` (failed `scancel`/`kill` → lingering SLURM allocation unreported); `remote_runner.py:80` (`ALLWELL_SCRATCH_DIR` silently ignored on `OSError` → later ENOSPC); `image_table/image_compose.py:485/516/523` (corrupt mask → "no overlay" → user misreads QC); `persistence/view_state.py` (~25 `swallowed()` sites) + `sample_definitions.py` (persistence fails invisibly).
- **Top recommendation:** extend `test_no_silent_except.py` to also flag tuple/typed `pass` and broad `return None` in `pipeline/`, `well_viewer/data/`, `well_viewer/persistence/`, add the missing pipeline files, and route data-integrity failures to at least WARNING (not DEBUG).

### 2.3 Race conditions
- **High — `features/smfish/tab_view.py:752`** (`_smfish_apply_to_all`): launches a thread + `ThreadPoolExecutor` with **no re-entrancy guard** and no button disable; a double-click runs two workers writing the **same per-well CSVs**, and the 2nd launch `.clear()`s the shared `app._smfish_cancel_event`, discarding a cancel meant for the first. *Fix: single busy flag + per-run cancel event.*
- **Medium —** smfish `worker.py:265-279` TOCTOU (dataset swap during the write loop is never re-checked → late wells written into the swapped dataset); `remote_runner.py:2557-2571` (`_current_proc` read without a lock → Stop can miss the running child); `pipeline_runner.py:364-387` (`stop()` reads `_proc` assigned on the worker thread with no sync); `cell_browser/worker.py:202` (unbounded threads read a `zipfile_cache` a dataset swap can `invalidate()`).
- **Low —** playground `_ReferenceBridge` connected per call, never disconnected.
- **Verified correct:** the plate-selection debounce (`selection_controller.py:331-333`, all main-thread + coalesced); `channel_state_controller` `blockSignals` discipline.

### 2.4 State lifecycle / teardown
**Root cause:** `AnalyzeTab` is mounted as a *child* widget (`allwell.py:225-229`); in Qt, `closeEvent` reaches only top-level windows, so **`AnalyzeTab.closeEvent` never fires** — invalidating the teardown it appears to provide.
- **Critical — `allwell.py:918`**: `MainWindow.closeEvent` only saves geometry + cleans the review temp dir; it **never stops the runner**. Quitting mid-run orphans the local `subprocess.Popen` (and its process group); for remote runs the `finally` teardown in `pipeline_runner._run_pipeline_remote:646-663` runs on a daemon thread killed abruptly → **remote SLURM allocation, remote staging dir, and local scratch all leak**. *Fix: in `MainWindow.closeEvent`, `self._analyze._runner.stop()` (and briefly wait) before `super().closeEvent`.*
- **High —** the remote-cleanup `finally` (`pipeline_runner.py:646-663`) is correct but only unwinds if the worker thread isn't hard-killed → resolved by the fix above.
- **Medium —** `analyze_tab.py:1035` `_poll_log` self-reschedules `singleShot(80,…)` forever with no stop condition; `runtime_app.py` has no `closeEvent`, so the smfish cancel event is never set on close and worker threads aren't joined at shutdown.
- **Low —** `core/split_view.py:222-246` disconnects the app-global `focusChanged` only when Split View is toggled off, not on teardown.
- **Correctly paired:** `AnalyzeTab._eta_timer` start/stop; remote-dialog `_poll` self-termination; remote-dialog `closeEvent`s.

### 2.5 Boundary conditions — **a strength**
The sampled data/stats surface is unusually well-hardened; no Critical/High/Medium boundary defect found. Verified-safe: `_aggregate_arrays` (every `.std(ddof=1)` guarded by `size>1`, `frac` denominators filtered `>0`), `resolve_value_series` (epsilon clamp + `np.errstate`), `_beeswarm_jitter` (`n==0/1` early returns), `fold_change` scale fns (return NaN when `factor==0`), `multivariate_compute` (raises on bad shapes, guards zero-variance). Only Low residues: `data_loading.py:112-113` (`circ` computed before the `per>0` mask; result still correct, just wasteful) and `cell_gating_tab_view.py:2302/2329` (empty-vals CDF, harmless).

---

## PASS 4 — Logic & Business-Rule Integrity

### 4.1 Conditional exhaustiveness / float equality
- **High — timepoint tolerance mismatch.** Combo widgets format tps with `:.4g`. `fold_change.py:75/342` deliberately uses a **relative** tolerance (`_TP_REL_TOL = 1e-3`) because a value like 2/3 h drifts ~3e-5 when parsed back from 4-sig-fig text — but the rest of the pipeline still matches with **absolute `1e-6`**: `stats_compute.py:93/285/431`, `stats/controller.py:108`, `data_loading.py:611`, `scatter/gating.py:237/412`, `image_compose.py:467`. Effect: for any tp needing >4 sig figs, Statistics / per-cell KS / bar aggregation return "no data" while fold-change on the same tp succeeds — silent and dataset-dependent. *Fix: extract the relative matcher into a shared `domain` helper and use it everywhere a `:.4g`-parsed `target_t` meets a data-derived timepoint.*
- **Low —** `stats/controller.py:407` reports the even-n median as the upper-middle element (`sv[len(sv)//2]`) — a displayed statistic (not the p-value). *Fix: `statistics.median(...)`.*
- **Note (not defects):** `_filter_rows_by_timepoint` float `==` is safe (value drawn from the same column's `.unique()`); the optimizer's sign-safe `max(|fa|,|fb|)` gap is correct.

### 4.2 Return-value consistency
- **High — `domain/fold_change.py:1109` `build_cell_scaling`** takes only `fc_state=(vs_control_on, control_label, vs_t0_on)` — **no operator/order/baseline_tp** — always builds a multiplicative factor, and the renderer always **divides** (`barplot/renderer.py:207/355`). Meanwhile the bars on the identical plot honor per-axis ratio-vs-subtract, ctrl/t0 ordering, and the resolved `baseline_tp` (`barplot/collect.py:155-385`). Effect: with "subtract" selected, **bars subtract but overlaid per-cell distributions divide** — a wrong, silently inconsistent transform; with a non-earliest baseline, per-cell points normalize to a different baseline than the bars. *Fix: thread `op_ctrl/op_t0/order/baseline_tp` into `build_cell_scaling` and use `scale_bar_value` semantics.*
- **Medium — `barplot/renderer.py:986`**: per-cell fold-change passes `target_t=target_ts[0]`, silently collapsing multi-timepoint selections to the first tp's control. *Fix: build scaling per (well, tp), or annotate/disable the overlay when multiple tps are active.*
- **Note (correct):** `multivariate_compute` and `resolve_value_series` return correctly-typed/indexed values (incl. NaN rows) on every edge path.

### 4.3 Data-flow integrity
- **Verified correct:** ratio division uses an epsilon **clamp** (`data_loading.py:281-297`, no sign-flip near zero); stats entry validates tp/threshold/val_col with fallbacks; `serialize/deserialize_stats_groups` stores member wells alongside names so rename/delete reproduces composition (`stats/controller.py:780-831`).
- **Low — `data_loading.py:307`**: `_missing_col_warned` is keyed by `id(df)`, which is reused after GC → a legitimately-due warning can be suppressed (or duplicated), and the global set grows unbounded per session. *Fix: key by a stable dataset/well id.*
- *Gap:* the `view_state.py` round-trip semantic-drift review and the `analyze_tab.py` numeric-input boundary trace were delegated and did not return — recommend re-running.

### 4.4 Transaction / atomicity
- **Verified robust — `persistence/_io.py`** (exercised by `test_atomic_write_retry.py`): uniquified temp name `<path>.<pid>.<uuid>.tmp`, retry-with-backoff on `PermissionError` around both `open` and `os.replace`, and swallowed tmp-cleanup that never masks a successful primary write. Correct write-then-rename pattern; JSON inherits it.
- *Gap:* multi-step rollback review of `remote_runner.py` (staging → upload → remote exec) and `batch_export/` / `export/service.py` (partial success across many files) did not return. **Residual risk to check:** (a) do batch exports that write N files leave 1..N-1 on disk with no manifest/rollback when file N fails; (b) does remote submission delete/overwrite prior outputs before the new run is confirmed.

### 4.5 Concurrency invariants / caches
- **Medium — `domain/stats_compute.py:347` (+ siblings `:381/415/452`)**: `stats_cache` keys omit the **on-population definition** returned by `_thresh_on_col(app)` (driven by `_cell_gating_use_scatter_thresh`, thresh-layer choices, and gate geometry). `_notify_gates_changed` (`scatter/gating.py:431`) does **not** invalidate the stats cache, so a gate resize/rename that changes membership without a worker re-run can return **stale cached statistics**. *Fix: add a hash of the active gate set + thresh choices to all four keys, and/or invalidate from `_notify_gates_changed`.*
- **Low/Medium — `image_table/image_compose.py:447-476`**: after an exact `(fov, tp)` miss, `_load_array` falls back to a numeric-close (`<1e-6`) any-match and, when `fov` is empty, the **first** available FOV → can surface a neighboring FOV/timepoint image when filename tokens and `pipeline_info` disagree. *Fix: prefer "no image" over a numeric-close tp match; annotate non-exact fallbacks.*
- **Verified correct — `data/zipfile_cache.py`**: keyed by `Path`, validates `st_mtime_ns` on every access, LRU-bounded — no cross-dataset stale-image leak.
- *Gap:* matplotlib-global / pyplot cross-figure and LUT/font-cache review did not fully return.

---

## PASS 5 — Code Quality & Maintainability

### 5.1 Duplication
- **High — data-correctness duplication:**
  - `find_image_groups` vs `find_image_groups_in_zip` (`process_microscopy.py:2234-2382` vs `2144-2233`, ~90 lines each): the same stem/channel/FOV/tp parsing, once over a directory and once over a `ZipFile` namelist. Segmentation correctness depends on both parsing identically. *Fix: one grouping fn taking an entry-iterator.*
  - `services/remote_runner.py:3252-3327` reimplements zip-extract + CSV-append that `pipeline/io.py` already centralizes → a schema/dedup fix on the local path is missed on the remote-fetch path. *Fix: call the `pipeline.io` merge helper.*
- **Medium —** `process_well_folder_task`/`process_well_zip_task` (CC 31/22, `process_microscopy.py:3122/2946`) and `process_well_folders`/`process_well_zips` (`:3663/3527`) — identical phase sequences and ~130-line executor scaffolding differing only in the task callable (*fix: parametrize a shared driver*); rsync-over-ssh transport+retry in **three** overlapping impls (`remote_runner.py:2028/2150/2246`); the barplot fold-change mean/frac/n triplet pasted **6×** (`barplot/collect.py:265-387/496-579` — the densest copy-paste in the app); the annotated-heatmap block ×4 in `multivariate/renderer.py`.
- **Low —** helper-exists-but-re-inlined: `scatter/_layout.py:46-63 _style_scatter_ax` re-inlined at `scatter/controller.py:83/267`; `distribution/controller.py:143` mirrors `scatter/_layout.py:66`; `timepoint_color_map` duplicated in `multivariate/renderer.py:82`; persistence `_coerce_*` map helpers with **drifted** NaN handling (`ratios.py`/`line_order.py`/`heatmap_layouts.py`).
- **Positive:** well-plate coordinate math is centralized (`parse_well_token`, `well_rank`, `canonical_well_label`); rsync transport/retry and `pipeline/io.py` CSV/zip merge are single-source.

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

The monolith budgets constrain *file size* but not *function complexity*. **Priority: characterization tests for the three CC-52/69/98 untested functions, then decompose.** Consider an intra-file complexity ratchet alongside the existing line-budget ratchets.

### 5.3 Test quality — **a strength**
~75% behavioral / ~20% presence-or-structural (much of it legitimate guardrails) / ~5% weak. `test_fold_change.py` independently derives expected values (`# mean: 20/10=2`); `test_remote_runner.py` (329 asserts), `test_multivariate_compute.py`, `test_shape_features.py` assert specific numerics. Circular literal-echo tests are rare. Enforcement tests are genuine: `test_monolith_budgets.py` (6 down-only size ratchets), `test_architecture_contracts.py` (8 AST layering contracts — pipeline is Qt-free, services import no Qt, event-bus signal catalogue capped). **Under-tested:** `batch_export` render path (`base_panel.py` 1589 lines, only empty-groups/persistence tests), plus the CC-52/69/98 functions above.

### 5.4 Logging security
No credential/token literals are logged anywhere; `ssh_key_setup.py` delegates passphrases to ssh's native prompt (never in-process); no stdout `print()` leaks in production paths.
- **Medium — `services/ssh_key_setup.py:66-68`**: SSH keys are generated **without a passphrase** by design → a passwordless private key on disk grants remote cluster access to anyone with home-dir read access. *Fix: offer an optional passphrase, or document the residual risk.*
- **Low —** the bootstrap log (`~/.cache/allwell/bootstrap-<ts>.log`, `scripts/_bootstrap_lib.py:138-160`) is never auto-deleted and captures host + home-dir paths (usually embedding the username); `remote_runner._log` echoes `ssh_alias`/`remote_output_dir` at INFO. *Fix: add a retention cap / "clear logs" action.*
- **Low —** full `traceback.format_exc()` written to per-well error files and debug panels (`process_microscopy.py:209`, `scatter/callbacks.py:313/1145`, `cell_gating_tab_view.py:1177`) can embed local paths with usernames. *Fix: keep full tracebacks in dev-gated sinks; show users a short message.*

### 5.5 Environment configuration
No central startup validation exists, but this is **Low risk**: every env *read* uses `.get(...)` with a default or is `if`-guarded, and every bare `os.environ[...] =` is a **write** — so there's no `KeyError`/None-propagation crash surface. The residual weakness is that misconfiguration (a bad `ALLWELL_SCRATCH_DIR` / `ALLWELL_REMOTES` path) surfaces as a **silent fallback** rather than a clear error, with reads scattered across 10 files. *Fix: a small `services/env.py` that reads the ~6 `ALLWELL_*`/config vars once at startup, logs active overrides, and validates provided paths exist — turning silent fallbacks into visible warnings.*

---

## Recommended sequencing

1. **Stop the silent-success data-loss chain first** (actions 1, 3, 4, 5, 12): these can delete or corrupt user data / produce wrong cell counts with no error. Low-effort, high-impact.
2. **Fix the two scientific-correctness bugs** (actions 6, 7) and the stale-stats cache (4.5) — wrong numbers are the worst outcome for scientific software.
3. **Wire `MainWindow.closeEvent`** (action 2) — one change resolves the Critical + dependent High teardown/leak findings.
4. **Tighten the guardrails so they can't drift back:** extend `test_no_silent_except.py` (2.2), re-enable `F401` with targeted `# noqa` (1.5), add a function-complexity ratchet (5.2).
5. **Then de-risk the god-modules** (action 8, `cell_gating_tab_view.py`) and unify the duplicated pipeline discovery/merge (action 10) with characterization tests (action 9) as the safety net.

### Coverage gaps in this audit (recommend a follow-up pass)
- 1.2 exhaustive per-attribute orphan-state table.
- 1.5 unreachable-branch/unused-return sweep of `process_microscopy.py` + `remote_runner.py` (run `vulture`).
- 4.3 `view_state.py` round-trip drift + `analyze_tab.py` input boundary.
- 4.4 multi-step rollback in `remote_runner.py` / `batch_export/` / `export/service.py`.
- 4.5 matplotlib/pyplot global state + LUT/font caches.
