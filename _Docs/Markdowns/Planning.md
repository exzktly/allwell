# All-Well — Implementation Audit and Improvement Plan

This document is a code-review companion to `Markdowns/ARCHITECTURE.md` and
`Markdowns/README_WellViewer.md`. It walks the codebase in the same order
those documents describe it and calls out places where the implementation
is illogical, inconsistent with stated invariants, not best practice, or
inefficient. Each finding gives a location, a concrete failure mode, and a
recommended fix.

Findings are grouped by audit area (matching architecture §1–§10) and
tagged by severity:

- **C — Critical:** silent data loss, security, or correctness errors.
- **H — High:** documented invariant violated, perf cliff, or
  user-visible bug.
- **M — Medium:** convention drift, latent fragility, perf cost in normal
  use.
- **L — Low:** hygiene, dead code, cosmetic correctness, docstring drift.

A "must-fix-before-next-release" shortlist sits at the bottom (§7).

---

## 0. Handoff — for a fresh chat session picking this up

If you're a new Claude instance starting from zero on this branch, read
this section first. It tells you what state the repository is in, how
this document was produced, what hasn't been done, and where to begin.

### 0.1 Current state

- **Branch:** `claude/review-app-architecture-Sj0rr` (pushed to
  `origin`). The audit lives in **`_Docs/Planning.md`** (this file)
  and is the only change on the branch.
- **No code changes have been made.** Every finding below is analysis
  only. The repo still builds and behaves exactly as it did when the
  audit started.
- **No tests were added.** §6.8 recommends a test scaffold but none
  exists yet.
- **No PR has been opened.** Do not open one without the user's
  explicit request.

### 0.2 How this document was produced

The previous session read `Markdowns/README_WellViewer.md` and
`Markdowns/ARCHITECTURE.md` end-to-end, then dispatched **five
parallel `general-purpose` audit agents**, each scoped to one slice of
the codebase. Their reports were synthesised here. The slices:

1. **Analyze side** — `allwell.py`, `all_well_launcher.py`,
   `analyze_tab.py`, `process_microscopy.py` (3312 lines),
   `WellPlateZipper.py`, `services/*.py`. Produced findings C1–C3,
   H1–H5, M1–M5, L1–L7. Mapped to §1 below.
2. **Review-side shell** — `well_viewer/runtime_app.py` (7140 lines),
   `views/centre_view.py`, `views/sidebar_view.py`,
   `plot_orchestrator.py`, `load_controller.py`,
   `selection_controller.py`, `figure_export_editor.py`,
   `status_signal.py`, `debug_flags.py`, `ui_helpers.py`. Produced
   H6–H10, M6–M10, L8–L10. Mapped to §2.
3. **Controllers / renderers** — lineplot, barplot, scatter,
   distribution, heatmap, stats, fold_change, data_loading,
   export_service, auto_threshold, plot_style, metric_labels,
   grouping, preview controllers. Produced H11–H13, M11–M17, L11–L15.
   Mapped to §3.
4. **Data + image + persistence** — data_loading, image_discovery,
   image_resolver, viewer_state, plate_layout, selections_model,
   sample_definitions, batch_models, ratio_models, gating_state,
   image_table_controller (1798 lines), review_image_*, smfish_*,
   montage_controller, crop_tool, every file under
   `well_viewer/persistence/`. Produced C4–C7, H14–H19, M18–M24,
   L16–L20. Mapped to §4.
5. **Widgets + theming** — every file under `widgets/`, `theme.py`,
   `ui/theme/`, `well_viewer/ui_helpers.py`, sample tab views.
   Produced H20–H23, M25–M30, L21–L24. Mapped to §5.

If a section number is wrong because new findings shifted the
numbering, **don't renumber retroactively** — append new findings at
the end of the relevant section and use the next free number. Cross-
references (e.g. "see C5") elsewhere in this document assume stable
IDs.

### 0.3 Severity ID conventions

The C/H/M/L letter is followed by a sequential number that is **global
across the document**, not per-section. So C1–C7 are seven critical
findings *anywhere*; H1–H23 are 23 high-severity findings across all
audit areas. This means future additions claim the next free number,
not a per-section number.

### 0.4 How to start implementing

The recommended order is the **must-fix shortlist in §7**, but the
single highest-leverage starting point is:

1. **Create the shared atomic-write helper** described in §6.1.
   File suggestion: `well_viewer/persistence/_io.py` exporting
   `atomic_write_json(path: Path, data: object) -> None`. Convert all
   persistence modules (`ratios.py`, `heatmap_layouts.py`,
   `bar_groups.py`, `cell_overrides.py`, `line_order.py`,
   `sample_definitions.py`) to use it. Then point `smfish_worker.py`
   at it for CSV writes (H15) and `process_microscopy.write_pipeline_info`
   too (C1). Single low-risk PR; closes C1, C6, H15.
2. **Factor out the auto-threshold helpers** (C2). Create
   `well_viewer/auto_threshold_core.py` with stdlib + numpy + skimage
   only; have both `process_microscopy.py` and
   `well_viewer/auto_threshold.py` import from it. The pipeline
   contract (no imports from `well_viewer/`) is preserved because the
   new module has no Qt or GUI dependencies. Pipeline-only deployments
   already have numpy + skimage. Single-file change with broad
   correctness payoff.
3. **Cap the analyze-tab log** (C3) — three-line fix:
   `document().setMaximumBlockCount(10000)`, `Queue(maxsize=10000)`,
   drop-with-warning policy.

After those three, the "selection paint→commit" cluster
(H6/H8/H10/M7/L9) is the next coherent piece of work and the largest
user-visible perf win. It wants extracting into a new
`channel_state_controller.py`; the plan is in §2 and §6.3.

### 0.5 Verifying findings before acting on them

Several findings reference specific line numbers. Line numbers may
shift if anyone has edited the files between audit and
implementation. Before changing code:

- `git log --oneline -- <file>` to confirm the file hasn't moved.
- Open the file at the cited range. If the snippet matches the
  finding's description, the line numbers are still valid. If the
  snippet has moved, grep for a stable identifier mentioned in the
  finding (function name, variable name, distinctive string) and
  update mentally.
- For findings that claim something is missing (e.g. "no atomic
  write"), confirm by reading the file rather than trusting the
  finding — atomic-write helpers may have been added since.

The audit was thorough but the sub-agents had short read windows;
**spot-check critical findings before committing fixes**.

### 0.6 Repo layout reminders (in case you skip ARCHITECTURE.md)

- `allwell.py` — app entry + shell.
- `analyze_tab.py` — Analyze pane.
- `process_microscopy.py` — the pipeline; **must run with no
  `well_viewer/` imports** (CI / headless workers).
- `well_viewer/` — Review pane. `runtime_app.WellViewerApp` is a
  deliberate god-object; controllers are pure functions of `(app, …)`.
- `widgets/` — reusable Qt widgets; must not import from
  `well_viewer/` or `allwell`.
- `services/` — Analyze-side service modules.
- `ui/theme/` — claimed-dormant theme system that is actually
  load-bearing (see H20).
- `_Docs/` — installer + requirements + **this file**.
- `Markdowns/` — the two live docs (ARCHITECTURE.md, README_WellViewer.md).
- `tests/` — does not exist yet. §6.8 recommends creating it.

### 0.7 What this document is NOT

- It is not a design doc for a new feature.
- It is not a refactor plan for the whole codebase — most findings are
  surgical.
- It is not exhaustive — the sub-agents flagged genuine issues and
  skipped nits; a fresh pass would likely find more, especially in
  files not opened (`batch_export/`, every individual `tabs/*.py`,
  every individual `views/*.py`).
- It does not recommend rewrites. Where a finding says "extract into
  …" the recommended size is a single new module plus thin shims; not
  a redesign.

### 0.7.5 What has already been implemented

A first implementation pass closed a large fraction of the audit. The
commits below all sit on `claude/review-app-architecture-Sj0rr` *after*
the §0 handoff commit (`506a53a`) and *after* PR #247 was merged. A
fresh session can pick up where this left off by reading `git log
506a53a..HEAD --reverse` and the corresponding finding sections below.

Each completed finding still appears in its section so a future
re-audit can see the original analysis; the **STATUS** line on each one
calls out the fix commit.

**Done (in this branch, post-merge):**

| Finding | Commit | One-line |
|---|---|---|
| C1, C6, H15, H17, L19 | `4c870c2` | Atomic JSON / CSV writes via shared helper |
| C2 | `d50da9a` | `auto_threshold_core.py` — pipeline + GUI share helpers |
| C3, L3, L4 | `b71c149` | Cap analyze-tab log + queue; drop dead code |
| C7, H12, H16, H18, H19 | `2842f46` | Mixed v1+v2 reject; style on line/scatter; gating warn; fluor-column warn; ratio denom clamp |
| L8, L11, L12, L13, L18, L23, M21 | `24656e2` | Dead Movie Montage; NaN-area; heatmap reorder; scatter imports; tp/fov sanity; opacity no-op; pipeline_info partial parse |
| M2, M18, M19 | `2e7a146` | Well-zip predicate; palette collisions; canonical "tophat" name |
| C5, H13, M3, M14 | `9e053b6` | Segmentation fingerprint on cell_overrides; BH-FDR; tf_threads clamp; heatmap content-keyed cache |
| H1, H2 | `2e9af7b` | Stop reaches zipper; PID-reuse-safe SIGKILL |
| C4, M22 | `e57eb95` | Process-wide ZipFile LRU cache |
| H14 | `17d1857` | smFISH worker cancellation + dataset-swap safety |
| H11 | `75f220f` | Sample SD (ddof=1) across aggregator / rep stats / scatter |
| H6 | `679998b` | Coalesce plate-paint commits via QTimer |
| H8 | `cf649c3` | `_set_selected_wells` single mutator |
| H3, M5 | `3879423` | Single-pass walk + schema validation in WellPlateZipper |
| L6, L7, M4 | `024300d` | Deferred icon render; status balance; warning dedup |
| H9 (partial) | `743c33c` | Lazy-build guards in line / scatter controllers |
| M8 | `b0fa79b` | Debounce ratios + heatmap-layouts saves |
| L24, M23, M24 | `040c402` | Alpha edit blank; backup TOCTOU; rglob depth cap |

**Items closed in PR #249** (post-merge follow-up to PR #247):

| Finding | Commit | One-line |
|---|---|---|
| H5, M16, M17, M25, M27 | `b8d3195` | argv `-c` check tightened; controllers stop reaching into runtime_app; distribution `_plot_card` back-ref re-attached after `fig.clear()`; MplToolbar disconnects mpl callback; PlotCard stops recolouring lines by index |
| M20, M26 | `d31719c` | Separator-aware fallback fov/tp extractor; PlotCard styling applied per-figure (no global rcParams mutation) |
| H10 (partial) | `74547cf` | ctxbar combo deselects on empty-state instead of showing a stale label |
| M15 | `6597cea` | Heatmap `_cell_value` pools 1-D arrays instead of `pd.concat`'ing per-well DataFrames in the O(T·R·C) inner loop |
| L14, L15 | `973d697` | Index-based dedup in line plot ordering; per-FOV bucket key aligned to aggregator's `"1"` |
| L5 | `a8c540f` | `__aw_tmp_` prefix on pipeline scratch files (no more false-positive drops of user files containing `.pid`) |
| H20 (doc) | `a371d15` | ARCHITECTURE §8 rewritten to describe the actual two-theme split |
| M1 | `964d584` | New top-level `well_token.py` (stdlib-only) — `WellPlateZipper`, `process_microscopy`, `services/input_resolution_service` all delegate to it |
| H20 (values), M28, M29, M30 | `eb02dcc` | `ui/theme/_DARK_THEME` sources overlapping tokens from `theme.Colors`; new `Colors.ink_light` / `ink_dark` replace hardcoded `#FFFFFF` / `#000000` in 6 widgets; fontMetrics-derived sizes in `PreviewStrip` / `RailNavRow` / `CollapsibleRail` / `ColorPickerPopover`; `install_qss_refresh` helper + PlotCard adoption for runtime theme rebuilds |
| M10 (verified), M12 | `c244739` | New `WellViewerApp._well_aggregate_stats` shared by `_compute_rep_stats` (line/bar rep-set stats) and `scatter_controller._agg_wells` (scatter-aggregate); M10 traced and confirmed already-closed by PR #248's scope-aware redraw |
| L9 | `62287e2` | Hoist `_recalculate_threshold` / `_set_active_channel` / `_update_channel_selector` / `_refresh_metric_combo_for_channel` (~360 lines of god-object methods) into new `well_viewer/channel_state_controller.py`. Runtime_app methods become 2-line delegating shims; `_set_combo_values` moves to `ui_helpers.set_combo_values` to break the circular import |
| M6, M7 | `85b7f8a` | `plot_orchestrator.redraw` now fans out across line + distribution + heatmap (one place to add a new default-scope tab); `recalculate_threshold` caches per-channel `(min, max)` so the 3.5M-float scan only runs once per channel |

**Already closed upstream by PR #248** (so the audit's analysis is now stale):

- **H7** — `_set_active_channel` now routes through `redraw_scopes_or_defer` (fold-change scope pattern); only the visible scope redraws.
- **M11** — bar-plot fold-change pipeline consolidated around `collect_bar_items_for_group` / `BarItem`; no more duplicate per-rset fetching.
- **M13** — bar CSV switched to the additive schema (raw + `fold_change_*` columns), matching the line CSV shape.

**Major remaining items:**

- **H4** — `WellPlateZipper` copies instead of moving — left alone; behavioural change requiring user opt-in (a `--keep-originals` flag default True + Analyze tab checkbox would be the right shape).
- *(M6, M7 closed in `85b7f8a` — orchestrator now fans out across line/distribution/heatmap; threshold scan now cached per-channel)*
- **M9** — CSV load on UI thread — left; `_step_progress` already calls `QApplication.processEvents()` between files so it's not as bad as the audit suggested. Full off-thread load wants a `QThread` worker plumbed through the load progress signal.
- **L1, L2, L10, L16, L17, L20, L21, L22** — Various Low items. L21 / L22 (keyboard accessibility) are the most worthwhile; both want a focused UI PR. L9 closed in `62287e2`.

**M30 follow-up:** `install_qss_refresh` is wired into PlotCard only. The other ~23 widgets with per-instance QSS (`chip_group`, `popover`, `stepper`, `range_pair`, …) should adopt it when the runtime theme switcher actually ships — until then, the global QSS is static so they don't need it.

### 0.8 Suggested workflow for a fresh session

1. `git status` — confirm clean tree, confirm you're on
   `claude/review-app-architecture-Sj0rr`.
2. Read this §0, then skim §7 (the shortlist), then read whichever §1–§5
   subsections cover the work you're picking up.
3. Pick the smallest item from §7 that fits your time budget. The
   first three are explicitly ordered for that.
4. Verify the cited locations (see §0.5).
5. Implement, test (manually if no test scaffold exists yet), commit
   on this branch.
6. When committing, reference the finding ID(s) in the commit message
   (e.g. "Fix C1, C6, H15: introduce atomic_write_json helper").
7. **Do not delete findings from this document when you fix them.**
   Strike them with `~~strikethrough~~` and append a
   `→ Fixed in <commit-sha>` note. Keeps the audit trail intact.
8. Do not open a PR unless asked.

---

## 1. The Analyze side (`analyze_tab.py`, `process_microscopy.py`, `services/`, `WellPlateZipper.py`, `all_well_launcher.py`)

### C1. `pipeline_info.json` is written non-atomically and by two writers
- **Location:** `process_microscopy.py:2861-2864` (`write_pipeline_info`);
  conflicts with `process_microscopy.py:1295-1298`
  (`_apply_thresholds_to_pipeline_info`, which *is* atomic) and the GUI's
  `gating_state.save_gating_to_pipeline_info`.
- **What's wrong:** Bare `p.write_text(json.dumps(info, indent=2))`. A
  concurrent reader (`viewer_state.read_pipeline_info`) can observe a
  half-written or empty file. The whole-file overwrite also drops any
  `cell_gating` / `sample_definitions` block written by the GUI while the
  pipeline is rerunning.
- **Failure mode:** `JSONDecodeError` on viewer reload after Analyze
  finished; silent loss of persisted gating thresholds and saved
  selections when the user re-runs the pipeline against the same output
  folder.
- **Fix:** Use the tmp + fsync + `os.replace` pattern that
  `_apply_thresholds_to_pipeline_info` already implements. Before
  overwriting, read the existing file and merge `cell_gating`,
  `sample_definitions`, `ratios`, etc., so a re-run preserves user state.

### C2. Auto-threshold inlined into the pipeline has already drifted from the viewer copy
- **Locations:**
  - `process_microscopy.py:1060-1256` — `_sample_cell_and_bg`,
    `_pick_endpoint_timepoints`, `_parse_timepoint_hours`.
  - `well_viewer/auto_threshold.py:63-117, 147-419` — the viewer copy.
- **Three concrete divergences:**
  1. **Timepoint parser.** Viewer's `_parse_tp_hours` understands
     `01d02h30m`, prefixed ordinals (`T01`, `day02`), `48h`/`30m`/`2d`,
     and plain numerics, returning `None` on failure. The pipeline copy
     (`_parse_timepoint_hours`) is shaped differently and returns `NaN`
     on failure; plain numerics and mixed strings get sorted into
     different orders.
  2. **Endpoint picker sort key.** Pipeline copy sorts by `(0, float(s))
     else (1, s)`; viewer uses the chronological parser. First / middle
     / last endpoint selection diverges on real timepoint strings.
  3. **Dead key.** Pipeline copy reads
     `fields.get("tp") or fields.get("timepoint")` (lines 1187, 1194).
     Schema-derived fields use `"timepoint"` exclusively — the `tp`
     branch is unreachable but misleads readers.
- **Failure mode:** Pipeline writes a `thresh_frac_on` default that
  differs from what the GUI's "Auto-threshold" button computes on the
  same dataset.
- **Fix:** Factor the three shared helpers (`_parse_tp_hours`,
  `_pick_endpoint_timepoints`, `_sample_cell_and_bg`) into a stdlib-only
  module (e.g. `well_viewer/auto_threshold_core.py`) that both
  `process_microscopy.py` and `well_viewer/auto_threshold.py` import.
  Skia-clean: depends only on `numpy + skimage`, both already present in
  pipeline-only deployments. Drop the dead `tp` alias.

### C3. Analyze-tab log buffer and queue are unbounded — long runs OOM the GUI
- **Locations:** `analyze_tab.py:709-716` (`QTextEdit` doc), `:888-895`
  (`_log_line`); `services/pipeline_runner.py:206-219` (the queue
  producer); the queue itself is plain `queue.Queue()` with no cap.
- **Failure mode:** A multi-hour pipeline run emits tens of thousands
  of stdout lines. The `QTextDocument` grows without bound; if the UI
  thread blocks on a redraw, the reader thread keeps stuffing the
  queue. Hundreds of MB of accumulated text → unresponsive GUI →
  eventual OOM.
- **Fix:** `self._log.document().setMaximumBlockCount(10000)` at
  construction; switch the queue to `queue.Queue(maxsize=10000)` and
  drop-with-warning on overflow, or batch lines per poll. The ring
  buffer in `allwell._attach_log_ring_buffer` is correctly capped to
  1000 lines — the on-screen widget should be capped similarly.

### H1. Stop button leaves zombies on macOS/Linux and can SIGKILL a reused PID
- **Locations:** `services/pipeline_runner.py:144-171`;
  `services/input_resolution_service.py:67-85`.
- **What's wrong:**
  1. `stop()` calls `os.killpg(os.getpgid(proc.pid), SIGTERM)` then
     schedules a fire-and-forget `threading.Timer(5.0, …)` SIGKILL. If
     the process exits cleanly within 5 s and the OS reuses its PID,
     the timer can SIGKILL the *wrong* process group.
  2. Repeat clicks stack additional timers.
  3. The zipper subprocess in `run_zipper` is spawned without
     `start_new_session=True`; `stop()` never reaches it.
- **Fix:** Cache `pgid` once in `stop()` and pass it to the timer
  closure. In the timer, double-check `proc.poll() is None` before
  signalling. Add `start_new_session=True` (POSIX) /
  `CREATE_NEW_PROCESS_GROUP` (Windows) to the zipper Popen, expose its
  handle on the runner, and signal both from `stop()`. Track a
  `threading.Event` so a Stop during the zipper phase aborts the
  pipeline launch.

### H2. Stop button cannot cancel the zipper phase
- **Location:** Same as H1; `analyze_tab._run_pipeline_thread` only
  attaches `self._proc` *after* `resolve_dirs → run_zipper` completes.
- **Failure mode:** User clicks Stop during "Grouping" — UI shows
  nothing happened until the (potentially many-minute) zipper run
  finishes.
- **Fix:** See H1; surfacing the zipper as a tracked subprocess
  delivers cancellability for free.

### H3. `WellPlateZipper.main` re-walks the input directory 96 times
- **Location:** `WellPlateZipper.py:91-138`.
- **What's wrong:** Outer loop over 96 well labels; inner
  `find_matching_files(well, search_dir, …)` calls `os.walk` per well.
  Cost is O(96 · N) inspections; for ~10⁵ TIFs that's ~10⁷.
- **Failure mode:** The "Grouping" phase the user stares at takes
  minutes when it should be seconds.
- **Fix:** Walk the directory once, parse each filename's well token,
  push into `files_by_well: dict[str, list[Path]]`, then iterate the
  dict.

### H4. `WellPlateZipper` copies instead of moving — doubles disk usage; docstring lies
- **Location:** `WellPlateZipper.py:112-120` (`shutil.copy2`);
  `analyze_tab.py:4-8` ("move TIFs to folder/in").
- **Failure mode:** On a 100 GB TIF directory, the user silently loses
  100 GB of free space and is confused about which copy is "the
  source".
- **Fix:** Either switch to `shutil.move` after a successful per-well
  copy verification, or add a `--keep-originals` flag defaulted to
  False. Update the Analyze-tab docstring either way.

### H5. Frozen-bundle re-dispatch matches `-c` anywhere in argv
- **Location:** `all_well_launcher.py:107-113`
  (`_dispatch_multiprocessing_child`).
- **What's wrong:** `if "-c" in argv:` then `argv.index("-c")` plus a
  startswith-check on the next arg. The startswith check rescues the
  common case, but the broader pattern is brittle — any user path
  containing the bare token `-c` exercises the suspicious branch.
- **Fix:** Restrict to `argv[1] == "-c"` with a known argv length, the
  way the `--multiprocessing-fork` branch above already does.

### M1. Three different "what's a well token" parsers
- **Locations:** `analyze_tab._WELL_NAME_RE` (line 74),
  `WellPlateZipper._extract_well_from_filename` (line 60),
  `process_microscopy._canonical_well_label` (line 485).
- **What's wrong:** They agree today but only by coincidence;
  `analyze_tab._WELL_NAME_RE` doesn't validate the column range.
- **Fix:** Hoist a canonical `is_valid_well_name` / `canonical_well` pair
  into a shared services module; all three callers import.

### M2. `_has_well_content` returns True for any `.zip`, even non-well zips
- **Location:** `services/input_resolution_service.py:27-34`; mirrored in
  `analyze_tab.py:78-84`.
- **Failure mode:** A stray `archive.zip` in the input folder causes
  the resolver to skip the zipper; `process_microscopy` then emits
  per-file "skipped: not a well zip" warnings that confuse users.
- **Fix:** Predicate should filter on well-name regex:
  `any(is_valid_well_name(p.stem) for p in folder.glob("*.zip"))`.

### M3. Worker-count math degenerates on small hosts
- **Location:** `process_microscopy.py:3079-3125`.
- **What's wrong:** `available = max(1, cpu_count − 1)`,
  `tf_threads = 4`, `workers = max(1, available // tf_threads)`. On a
  2-core host: 4 TF threads share 1 reserved core; on a 4-core host:
  4 TF threads on 3 cores. README claims `cpu_count // tf_threads`
  but code uses `(cpu_count − 1) // tf_threads`.
- **Fix:** Clamp `tf_threads = min(tf_threads_request, available)` so
  on a 2-core host tf_threads collapses to 1. Update the README and
  the build_parser help string.

### M4. Zip-mode warns twice about ignored compression flags
- **Locations:** `services/pipeline_service.py:82-86` always emits the
  flags; both `pipeline_runner._run_pipeline_thread:184-188` and
  `ProgressTracker.parse:77-84` log the resulting "ignored" warning.
- **Fix:** Omit the flags from argv when the resolved input is
  zip-mode, or remove one of the warning emissions.

### M5. Zipper schema validation is weaker than the GUI's
- **Locations:** `WellPlateZipper.py:16-23` vs
  `process_microscopy.py:789-809`.
- **What's wrong:** Zipper silently maps unknown tokens to `"ignore"`;
  the GUI enforces exactly-one `channel` and exactly-one `well`. CLI
  users get silent misclassification.
- **Fix:** Have `WellPlateZipper.main` call the shared validator and
  `parser.error` on any returned messages.

### L1. `_attach_log_ring_buffer` raises root logger level globally
- **Location:** `allwell.py:719-727`.
- **Fix:** Attach the handler only to `well_viewer` and `__main__`
  loggers; leave root at WARNING so third-party libs don't spam INFO.

### L2. `_poll_log` re-queues into the queue it just polled
- **Location:** `analyze_tab.py:993-1001`. Synthesised lines from
  `ProgressTracker.parse` are pushed back into `self._log_q`; next poll
  handles them. Adds ~80 ms latency and is a re-entrancy hazard.
- **Fix:** Handle synthesised events inline.

### L3. Dead `_LOG_LEVELS` constant in `services/pipeline_runner.py:32`.

### L4. `build_pipeline_args` catches only `ValueError`, not `TypeError`
- **Location:** `services/pipeline_service.py:65-72`. A missing key
  raises `TypeError` on `int(None)` and escapes. Today every key is
  set; fragile.
- **Fix:** `except (TypeError, ValueError):` to match the workers /
  min_area branches.

### L5. `_safe_imwrite` tmp filename collides with user files containing `.pid`
- **Location:** `process_microscopy.py:138-140` (tmp scheme),
  `:639` (`".pid" not in p.name` filter).
- **Fix:** Switch to a less-ambiguous prefix (`__aw_tmp_`) and filter on
  that.

### L6. `_install_app_icon` renders 8 pixmaps synchronously on startup
- **Location:** `allwell.py:305-405`. Heavy QPainter work on the GUI
  thread before first paint.
- **Fix:** Defer with `QTimer.singleShot(0, …)` after first paint, or
  cache to `QPixmapCache`.

### L7. `closeEvent` doesn't pop the `status_signal.warn_push`
- **Location:** `analyze_tab.py:1068-1071`. Closing mid-run leaves the
  warn-scope unbalanced (in-memory only; no persisted impact).

---

## 2. The Review-side shell (`well_viewer/runtime_app.py`, `views/`, `plot_orchestrator.py`, `selection_controller.py`, `load_controller.py`)

### H6. `_on_sidebar_plate_selection_changed` commits on every paint event
- **Location:** `runtime_app.py:3317-3343` (paint handler) vs `:3345`
  (drag-finished handler).
- **What's wrong:** Architecture §9.2 documents a "paint vs commit"
  split — drag-paint fires many times per second and must *not*
  redraw. The current paint handler ends with
  `self._on_plate_sel_change()`, so every paint tick triggers a full
  redraw via `_refresh_after_selection_change` (line / bar / scatter /
  …).
- **Failure mode:** A 96-well drag triggers 96 full matplotlib draws +
  export-style reapply during the drag, then one more on release.
- **Fix:** Remove the commit call from
  `_on_sidebar_plate_selection_changed`. Keep only the cheap
  `_refresh_sidebar_map()` (palette repaint). The drag-finished
  handler is the commit point.

### H7. `_set_active_channel` fan-outs two-to-three redraws per click
- **Location:** `runtime_app.py:3706-3711` (and the parallel pattern in
  `_set_active_metric` `:3905-3909`, `_toggle_sem` `:4199-4201`,
  `_toggle_fov_replicates` `:4219-4221`).
- **What's wrong:** `_recalculate_threshold()` already triggers
  bar-channel sync that calls `_redraw_bars` indirectly; the explicit
  `_redraw()` + `_redraw_bars()` afterwards re-do the work. Same pattern
  in three other property setters.
- **Failure mode:** Channel-switch jitter; produces the "drop-down
  doesn't match plot" race called out in README §troubleshooting and
  ARCHITECTURE §12.2 (the dropdown sync sits between the two redraws,
  so the wrong frame can win).
- **Fix:** Collapse to a single dispatch. Have `_redraw()` always call
  the active-tab redraw (including Bar/Scatter) instead of every caller
  doing both. Coalesce bursts with a `QTimer.singleShot(0, …)` debounce.

### H8. Multiple call sites mutate `_selected_wells` without committing
- **Locations:**
  - `heatmap_controller.py:621` — `app._selected_wells = selected`.
  - `review_image_controller.py:118` — `app._selected_wells.add(…)`.
  - `batch_export/scatter_panel.py:449` — `app._selected_wells =
    set(grp.solo_wells)`.
  - `runtime_app.py:5354-5357` — smFISH activation shrinks
    `_selected_wells` to `{keep}` without calling commit.
- **What's wrong:** Bypasses `_refresh_after_selection_change`,
  leaves `_prev_sel` stale, and the next selection diff
  mis-attributes additions/removals. Architecture §15 warns that
  selections survive tab switches — these leaks cross tabs.
- **Fix:** Funnel all writes through a single helper
  `selection_controller.set_selection(app, new_set, *, commit=True)`
  that updates `_prev_sel` and dispatches to the active tab.

### H9. Lazy-build invariant violated by direct attribute access
- **Locations (unguarded `app._<fig>_fig` / `_<fig>_canvas` reads):**
  - `distribution_controller.py:153, 390`.
  - `lineplot_controller.py:107, 117, 122, 130, 278`.
  - `scatter_controller.py:198, 223, 288, 512, 535, 586`.
  - `barplot_controller.py:461, 481, 486`;
    `barplot_renderer.py:370, 458`.
  - `plot_orchestrator.py:99, 104, 113, 145`.
  - `runtime_app.py:5210` (figure unguarded; canvas guarded —
    inconsistent).
- **What's wrong:** Architecture §15 says "Don't assume `app._heatmap_fig`
  exists; do `if hasattr(app, '_heatmap_canvas'): …`". The eager-built
  Line and Bar tabs survive in practice; Distribution, Heatmap,
  Scatter, Scatter-Aggregate are lazy and would `AttributeError` if
  their controllers ever run before the tab body builds.
- **Fix:** Every controller entry point should `hasattr`-guard its own
  canvas before touching `app._<x>_fig`. A cleaner alternative is to
  stop stashing figures on `app` and pass them explicitly into the
  controller.

### H10. `_active_channel` written without combo-sync at three sites
- **Locations:** `runtime_app.py:3584` (`_rebuild_ratio_index` fallback),
  `:4127` (`_update_channel_selector` empty path), `:3466`
  (`_recalculate_threshold` initial set).
- **What's wrong:** These bypass `_set_active_channel` so the
  combo-sync loop at 3691-3704 never runs. The orphan path at 4127
  sets `_active_channel = ""` *before*
  `_refresh_metric_combo_for_channel()` reads it — the metric combos
  go empty and the global ctxbar combo displays whatever index 0 is.
  Likely cause of the "dropdown doesn't match plot" symptom in
  ARCHITECTURE §12.2.
- **Fix:** Replace the bare assignments with a single
  `_set_active_channel("")` (or extract a private helper that always
  runs combo-sync). At 4127, explicitly clear the ctxbar combo to
  prevent displaying a stale label.

### M6. Plot orchestrator misnamed — bar / scatter / scatter-agg redraws don't go through it
- **Locations:** `plot_orchestrator.py:23-50`; the dist/heatmap branches
  in `runtime_app._redraw:5215-5226`.
- **What's wrong:** Only `lineplot_redraw` and a montage hook run
  through the orchestrator; bar / scatter / scatter-agg have their own
  entry points called from selection / channel / tab paths. The
  distribution + heatmap dispatch is duplicated in `runtime_app._redraw`,
  so a new tab added to either place is silently missed by the other.
- **Fix:** Move all dispatch (line / bar / scatter / dist / heatmap)
  into `plot_orchestrator.redraw` so it is the single fan-out point —
  matches its documented role. Or rename it
  `redraw_line_and_montage` to make the truth visible.

### M7. `_recalculate_threshold` is a hidden O(total cells) scan
- **Location:** `runtime_app.py:3440-3536` (called from
  `_set_active_channel`, `_set_active_metric`, `load_directory`,
  `_rebuild_ratio_index`).
- **What's wrong:** Concatenates `_all_fluor_values` across every loaded
  well to compute lo/hi on every channel toggle (lines 3511-3513). On
  96 wells × 12 timepoints × ~3 k cells/well that's ~3.5 M float
  scans per click.
- **Fix:** Cache per-channel `(min, max)` on the rows cache; the
  threshold lookup becomes a dict get. If the scan must run, move it
  to a background `QThread`.

### M8. Ratios / heatmap-layouts persistence saves on every signal
- **Locations:** `views/ratio_panel_view.py:233-234`,
  `views/heatmap_layout_sidebar_view.py:449-451`,
  `tabs/heatmap_tab_view.py:400-402`.
- **What's wrong:** `cell_overrides`, `line_order`, and notes are
  debounced (500 ms); ratios and heatmap-layouts fire a JSON write per
  user edit. On a network share, perceptible per-drop lag.
- **Fix:** Plumb both through a `schedule_save(app)` helper like the
  `cell_overrides.py:84` debouncer.

### M9. CSV loading runs on the UI thread
- **Location:** `well_viewer/load_controller.py:65-72`. With 96 CSVs
  and a progress bar, the UI is unresponsive for the whole load. Image
  decode in `_refresh_review_image` (via `open_imgref_as_array`) also
  runs synchronously on the UI thread; multi-MB TIFF decode blocks for
  100–300 ms.
- **Fix:** Move CSV load into a `QThread` with a progress signal.
  Generalise the smFISH worker pattern for image decode.

### M10. Tab-switch handler is itself a redraw fan-out
- **Location:** `runtime_app._on_tab_change:5274-5380`. On every tab
  change with non-empty selections, fires `_refresh_sidebar_map()` +
  `_redraw()` + sometimes `_redraw_bars()`; the new tab's lazy builder
  can then call `_recalculate_threshold` → another redraw chain.
- **Fix:** Set a `_in_tab_switch` guard so lazy builds skip
  incremental redraws and one explicit `_redraw()` runs at the end.

### L8. Dead "Movie Montage" branches everywhere
- **Locations:** `runtime_app.py:754, 4103-4106, 4176, 5308`;
  `plot_orchestrator.py:48`.
- **What's wrong:** Movie Montage was folded into Image Table per
  `centre_view.py:727`. Yet every channel selector update, every tab
  change, and the orchestrator branch on a `"Movie Montage"` page name
  that `NamedPageStack` will never produce.
- **Fix:** Remove the dead branches and the
  `_montage_chan_cb` / `_montage_chan_var` references. ~40 lines net.

### L9. Thin-shim methods that crept into real logic
- **Location:** `runtime_app.py` — `_recalculate_threshold` (3440-3536,
  97 lines), `_set_active_channel` (3625-3715, 90 lines),
  `_update_channel_selector` (4054-4181, 128 lines), `_on_tab_change`
  (5274-5380, 100+ lines), `_select_invert` (4258-4270).
- **What's wrong:** Architecture §5.1 mandates shim-on-class /
  logic-in-controller. These methods are most of the dropdown-out-of-sync
  bug surface.
- **Fix:** Hoist into a new `channel_state_controller.py` /
  extend `selection_controller`; leave only the shim on
  `WellViewerApp`.

### L10. `signal_failed()` not wired into bar / scatter redraws
- **Location:** `runtime_app.py:5827, 6785, 6950`. `_redraw` wraps in
  `warn_scope()` and calls `signal_failed()` on exception;
  `_redraw_bars`, `_redraw_scatter`, `_redraw_scatter_agg` do not.
- **Fix:** Wrap all redraw shims uniformly.

---

## 3. Controllers and renderers (`well_viewer/*_controller.py`, `*_renderer.py`, `fold_change.py`, `export_service.py`, `stats_controller.py`)

### H11. SD definition disagrees across stats / aggregator / scatter
- **Locations:**
  - `data_loading.py:408, 413, 420, 426` — `ddof=0` (population).
  - `runtime_app.py:6142, 6151` (`_compute_rep_stats`) — `ddof=0`.
  - `scatter_controller.py:457` — `ddof=0`.
  - `stats_controller.py:300` — `pystats.stdev` is `ddof=1`.
- **Failure mode:** The Statistics tab's "sd=…" doesn't match the same
  group's bar/line error bar — papers will end up quoting whichever
  was copied last.
- **Fix:** Use `ddof=1` (sample SD) everywhere there is a "sample"
  interpretation (`_compute_rep_stats`, scatter agg, per-FOV branches
  of `_aggregate_arrays`). The per-cell SD in `_aggregate_arrays:426`
  can stay `ddof=0` if labelled "cell-level population SD"; otherwise
  switch. Document the choice in the metric axis labels.

### H12. Line / scatter redraws do not re-apply export style
- **Locations:** `lineplot_controller.py:278` (end of
  `redraw_line_plots`); `scatter_controller.py:288, 586`.
- **What's wrong:** Architecture §9.4: "Each per-tab redraw ends with
  `apply_export_style_to_current(app, fig, canvas)`". The bar
  renderer (`barplot_renderer.py:456-458`), heatmap, and distribution
  all call it; line and scatter do not. Property-sidebar tweaks
  (fonts, axis limits, log scale, Pub/Screen mode) silently revert on
  every well click.
- **Fix:** Append `apply_export_style_to_current` at the end of
  `redraw_line_plots`, `redraw_scatter`, `redraw_scatter_agg`. Better
  still: call it once in `plot_orchestrator.redraw` after fan-out
  (lines up with M6).

### H13. Statistics tab has no multiple-comparison correction
- **Location:** `stats_controller.py:311-336`.
- **What's wrong:** Pairwise tests run with no Bonferroni / Holm / BH
  adjustment; t-test is hard-coded Welch's; KS hard-coded two-sided;
  no paired option even when groups come from the same plate.
- **Failure mode:** With ≥3 groups (≥3 pairs), family-wise type-I rate
  is meaningfully above nominal α — a user quoting "p < 0.05" from
  the GUI is overstating significance.
- **Fix:** Add an "Adjust p-values" combo (None / Bonferroni / Holm /
  BH-FDR), apply after computing all p's. Add a "paired" checkbox
  gated on equal-length matched sample lists. Surface the KS
  `alternative=` parameter.

### M11. Bar-plot fold-change is computed twice in the rep-set draw path
- **Locations:** `barplot_renderer.py:259-297`;
  `barplot_controller.py:124-129`.
- **What's wrong:** `_collect_bar_items` already applies
  `scale_bar_value` to every rep-set item; `draw_grouped_bar_mode`
  discards those items and re-runs the pipeline (re-fetching per-rset
  stats, re-aggregating control wells, re-applying scaling). `_compute_rep_stats`
  is cached but `_fc.control_mean_at` and `_fc.first_tp_value` are
  not — both are full `_aggregate_group` calls per rep-set per redraw.
- **Failure mode:** Scrubbing the bar timepoint slider with 20
  rep-sets triggers ~40 full aggregations per redraw.
- **Fix:** Make `_collect_bar_items` return all fields the renderer
  needs (per-rset t0 mean, already-scaled values) and have the
  renderer trust them; or cache `control_mean_at` /
  `first_tp_value` on `app` keyed on
  `(rset.name, threshold, val_col, gates, cell_area)`.

### M12. Scatter Aggregate has its own threshold + ratio resolution
- **Location:** `scatter_controller.py:369-370, 428-459`
  (`collect_scatter_agg_data._agg_wells`).
- **What's wrong:** Private re-implementation of
  `_compute_rep_stats`, with subtle divergences on metric-driven
  `val_col` selection and a third ratio-resolution path. Three places
  now answer "what's the mean of channel X at timepoint t under
  active gating" — same numbers only for default settings.
- **Fix:** Push `collect_scatter_agg_data` to a shared helper
  `(wells, target_t, threshold, val_col, metric_kind)`; remove
  `_agg_wells`.

### M13. Line / bar CSV exports disagree under fold-change
- **Locations:** `export_service.py:283-304` (line) vs `:354-411` (bar).
- **What's wrong:** Line CSV writes *raw* aggregator values into the
  `mean_…` / `sd_…` columns and adds `fold_change_*` columns. Bar CSV
  writes *scaled* values into the existing `mean_…` columns and only
  annotates with `fold_change_mode` / `fold_change_control`. A user
  joining line + bar CSVs from the same dataset sees two different
  number scales under the same column name.
- **Fix:** Pick one shape. Recommend matching the *plotted* values
  (bar's current behaviour) for both — drop the `fold_change_mean` /
  `fold_change_sd` value columns and keep only the mode / control
  annotation columns. Update ARCHITECTURE §9.5 to reflect the chosen
  shape.

### M14. Heatmap `_compute_global_range` uses `id()` as cache key
- **Location:** `heatmap_controller.py:260-314`.
- **What's wrong:** Cache key includes `id(layout)` / `id(ratios)` —
  memory addresses, not content. Any code path that rebuilds the
  ratios dict misses the cache and pays a full O(T·R·C) sweep.
- **Fix:** Replace `id(layout)` with `layout.to_dict()`-derived hash;
  `id(ratios)` with `tuple(sorted(ratios.keys()))` plus an explicit
  version counter on `app._ratio_index` invalidated when ratios change.

### M15. Heatmap / distribution `pd.concat` in hot path
- **Locations:** `heatmap_controller.py:181-216` (METRIC_RATIO,
  METRIC_MEAN_ALL); `data_loading.py:608-610, 614` (`iter_plot_groups`).
- **What's wrong:** Each heatmap cell with multiple wells re-concats
  full per-well frames on every redraw. `_cell_value` is called R·C
  times per redraw + R·C·T inside `_compute_global_range`. For 8×12
  plate × 4 wells/cell × 12 tp: ~4600 full-frame concats per slider
  step.
- **Fix:** Cache pooled DataFrames per rep-set / per cell on `app`,
  keyed on composition, invalidated by `_invalidate_stats_cache`. Or
  refactor `_all_fluor_values_filtered` to accept an iterable of
  frames and concat only the resulting 1-D value arrays.

### M16. Controllers reaching into `runtime_app` for module-level constants
- **Locations:** `export_service.py:490, 526-527, 537, 542, 549,
  553-554, 569, 575-576, 659` (lazy
  `from well_viewer import runtime_app as rt`);
  `image_table_controller.py:1008, 1229`.
- **What's wrong:** Controllers pull `_extract_well_token`, `_np`,
  `PLOT_BG`, `TXT_*` from `runtime_app`. These have proper homes
  (`ui.theme`, `viewer_state`, top-level `numpy`). Hidden
  controller→runtime_app edge that ARCHITECTURE §5.5 forbids; lazy
  import hides it from static analysis.
- **Fix:** Move `_extract_well_token` to `viewer_state`; pull theme
  constants from `ui.theme` directly; import numpy at module top. Move
  `make_fluor_thumb` / `make_overlay_thumb` into a new `image_render.py`
  alongside `image_resolver.py`.

### M17. Distribution `fig.clear()` drops `_plot_card` back-ref
- **Location:** `distribution_controller.py:159-176` (`_setup_axes`).
- **What's wrong:** `fig.clear()` is not guaranteed to preserve custom
  figure attributes; `_plot_card` is a `PlotCard.__init__`-attached
  attribute that `plot_style.tokens_for` reads. Subsequent style
  application falls through to the "infer from facecolor" branch.
- **Fix:** After `fig.clear()`, re-attach `_plot_card` from
  `app._distribution_card`; or replace `fig.clear()` with iterative
  axes removal.

### L11. `aggregate_with_threshold_df` and `_all_fluor_values_filtered`
disagree on NaN-area handling
- **Locations:** `data_loading.py:301-306` vs `:462-469`.
- **What's wrong:** `~(area <= cell_area_threshold)` keeps NaN areas
  (`~False = True`); `area > cell_area_threshold` drops them (False on
  NaN). Distribution / stats / raw export drop; line / bar / heatmap
  keep.
- **Fix:** Use `np.isfinite(area) & (area > cell_area_threshold)`
  everywhere.

### L12. Heatmap label-reorder is correct only by accident
- **Location:** `heatmap_models.py:74, 92`. Code does
  `order.pop(src); order.insert(dst, src)` — the popped element
  happens to equal `src` because `order = list(range(rows))`. A future
  refactor that ever passes a non-identity ordering breaks silently.
- **Fix:** `popped = order.pop(src); order.insert(dst, popped)`.

### L13. `scatter_controller` imports unused `plt`; uses deprecated `cm.get_cmap`
- **Locations:** `scatter_controller.py:8, 395`.
- **Fix:** Remove the `plt` import (it also creates a default figure
  manager — controllers must not). Replace `cm.get_cmap('viridis')`
  with `matplotlib.colormaps['viridis']`.

### L14. `lineplot_controller._apply_order` uses `id()` for dedup
- **Location:** `lineplot_controller.py:33-44`. `id()` of small interned
  strings can collide / unintentionally dedup; use index-based tracking.

### L15. Stats per-FOV bucketing collapses missing FOVs to `"_"`,
aggregator uses `"1"`
- **Location:** `stats_controller.py:85-94` vs aggregator. Same dataset,
  different bucketing. Pick one — recommend `"1"` to match the
  aggregator.

---

## 4. Data + image + persistence layers (`data_loading.py`, `image_*`, `viewer_state.py`, `persistence/`, `smfish_*`, `selections_model.py`)

### C4. Image-zip cache is no cache — every render re-opens every zip
- **Location:** `image_table_controller.py:1244` (fresh `cache: Dict =
  {}` per Generate), `:1341` (stash on `app`), `:1380` (auto-LUT
  reuse), `:1560` (export copy); the actual reads go through
  `preview_controller.scan_zip_members:121` and
  `read_member_bytes:89`, both of which open
  `zipfile.ZipFile(zip_path, "r")` per call.
- **What's wrong:** The "cache" stores `ImgRef` dicts, not decoded
  arrays. For an N×M Image Table the zip is opened ~N·M times for
  Generate, again for auto-LUT, again for export. On a network mount
  this is the dominant latency cost.
- **Fix:** Cache decoded `np.ndarray`s in
  `_image_table_image_cache` keyed on `(well, channel, variant)`. *Or*
  introduce a process-wide `ZipFile` LRU keyed on `zip_path` so member
  reads share an open file handle — smaller change.

### C5. `cell_overrides.json` collides across segmentation re-runs
- **Location:** `persistence/cell_overrides.py:31` (key shape
  `(well, fov, tp, nucleus_id)`).
- **What's wrong:** Nucleus IDs are segmentation-label IDs, re-allocated
  from 1 on every re-run. Old override for cell #12 silently re-applies
  to *new* cell #12 — a different cell or none at all.
- **Failure mode:** User excludes a cell, re-runs the pipeline (the
  common workflow), gets ghost exclusions on cells they never touched.
- **Fix:** Include a per-well segmentation fingerprint in the key
  (e.g. mtime of `<well>_out.zip` or a content hash stored in
  `pipeline_info.json`). Discard overrides whose key no longer
  matches; warn the user.

### C6. `ratios.json` / `heatmap_layouts.json` / `bar_groups.json`
non-atomic writes
- **Locations:** `persistence/ratios.py:25-29`,
  `persistence/heatmap_layouts.py:40-53`,
  `persistence/bar_groups.py:56-61`.
- **What's wrong:** Bare `open(path, "w")` truncates before writing.
  Crash / OOM / signal mid-write produces a truncated file; on next
  load `ratios_from_dict` silently returns `[]` and the user's ratios
  are gone (along with any `cell_gating["thresh_frac_on"]["ratio:…"]`
  entries that reference them, which become dangling).
  `bar_groups.json` is the only sharable user-curated selection
  artifact — silent loss is unrecoverable.
- **Fix:** Add a single `_atomic_write_json(path, data)` helper (tmp +
  `Path.replace`) and use it from every persistence module
  (`cell_overrides`, `line_order`, `sample_definitions` already do
  this — just extract the common shape). Also `smfish_worker.py:102`
  (`df.to_csv`) needs the same treatment.

### C7. `selections_model.block_is_v2` accepts mixed v1+v2 payloads
- **Location:** `selections_model.py:433-441` (`block_is_v2`),
  `:458-468` (branching in `from_block`).
- **What's wrong:** Returns True if *either* `schema_version >= 2` or
  `selections` is a list. A mixed file (both `rep_sets`/`groups` *and*
  `selections`) is treated as pure v2 — `migrate_v1` never runs and
  legacy keys are silently dropped on next save. Mixed payloads arise
  after manual merges or v1↔v2 read/write races.
- **Fix:** Require `schema_version >= 2` *and* absence of legacy keys;
  on conflict, log + abort migration and disable v2 writes for the
  session via the existing `_selections_v2_writes_disabled` guard
  (`persistence/sample_definitions.py:34-39`).

### H14. smFISH worker is not cancellable; can corrupt the wrong dataset on shutdown
- **Location:** `smfish_worker.py:117-162`. Worker runs on a daemon
  thread with a `ThreadPoolExecutor` — no cancel token. `_write_counts_to_csvs`
  iterates CSVs in `out_dir` and rewrites them.
- **Failure mode:** User closes the dataset mid "Apply to All" then
  opens a *different* dataset; the in-flight worker writes counts into
  the *new* dataset's CSVs with the *old* dataset's results.
- **Fix:** Pass an `Event` cancel token through
  `apply_global_threshold_async`; check between members and before the
  write loop. On dataset change / shutdown, set the event and join
  with a timeout. Pin `out_dir` at submit time and re-verify it still
  equals `app._data_dir` before each write.

### H15. smFISH per-well CSV write is not atomic and is unlocked
- **Location:** `smfish_worker.py:102` — `df.to_csv(csv_path,
  index=False)`. No tmp+rename. Review CSV may be reading the file at
  the same moment.
- **Fix:** Write to `<csv_path>.tmp` and `Path(tmp).replace(csv_path)`.

### H16. `gating_state.save_gating_to_pipeline_info` silently no-ops on missing sidecar
- **Location:** `gating_state.py:104-107`. Returns `None` without log
  or toast when `pipeline_info.json` doesn't exist; the caller in
  `persistence/sample_definitions.save_all` doesn't check the return
  and shows a "saved" status.
- **Failure mode:** User clicks Save, sees the status message, but
  thresholds aren't persisted. Silent data loss.
- **Fix:** Return an explicit "skipped" sentinel; have the caller warn
  the user or refuse Save All when no sidecar exists.

### H17. `cell_overrides.load_from_data_dir` clobbers in-memory state before validating
- **Location:** `persistence/cell_overrides.py:62`.
- **What's wrong:** `app._review_included_overrides.clear()` runs after
  JSON parse but before any entry-level validation. A reload of an
  empty / broken file wipes an unsaved session of include/exclude
  edits.
- **Fix:** Build the new dict in a local; assign only on success.

### H18. `data_loading.aggregate_with_threshold_df` silently returns `[]` on missing column
- **Location:** `data_loading.py:308-311`.
- **What's wrong:** Cell-gating row saved against `gfp_mean_intensity`,
  CSV from a different schema omits it — plot just shows nothing, no
  log.
- **Fix:** Log a warning once per (key, df-id); show a hint in the
  empty-plot state.

### H19. Ratio denominator clamp lets negative-near-zero spikes through
- **Location:** `data_loading.py:216-221`. When `denom + epsilon` is
  small-negative, the ratio sign-flips and can reach ±10⁷ for
  individual cells (background-corrected channels often go negative).
- **Failure mode:** Beeswarms / means dominated by a handful of cells.
- **Fix:** Clamp `denom = np.maximum(denom, epsilon)`, or expose a
  configurable min-denominator on `RatioMetric`.

### M18. 48-colour palette ≠ 96 wells → guaranteed collisions on full plates
- **Locations:** `plate_layout.py:28-41`, `selections_model.rank_color:121-135`
  (`pal[min(ranks) % len(pal)]`), `data_loading.iter_plot_groups._color:594`
  (`fallback_palette[idx % 9]`).
- **What's wrong:** Two selections with disjoint wells whose lowest
  ranks collide get visually identical colours. No tiebreak, no
  warning. Architecture invariant "same well = same colour everywhere"
  silently violated.
- **Fix:** Either expand to 96 colours, or hash the entire sorted
  well-list into the palette index, or step to the next free slot when
  collisions occur. Document the chosen rule.

### M19. `image_resolver` / `preview_controller` disagree on the canonical name for tophat outputs
- **Location:** `image_resolver.py:46-52, 73-75, 116-123`;
  `preview_controller.classify_member`.
- **What's wrong:** `OUTPUT_KIND_PRECEDENCE` lists both `tophat` and
  `fluor_processed`; tophat always wins. `preview_controller.classify_member`
  returns `tophat_fluor` for the same case. Anything that compares
  on the raw kind string disagrees with the resolver.
- **Fix:** Unify on one canonical name (`tophat`); update every
  comparison site; add a unit test that walks every kind path.

### M20. `_FNAME_RE` legacy fallback hardcodes underscore separator
- **Location:** `image_discovery.py:77-80`,
  `_default_fov_tp_extractor:139-145`.
- **What's wrong:** When `pipeline_info.json` is missing, this 5-field
  underscore regex is the only fallback. Datasets using `-` or `.`
  separators collapse every image to `("unknown", "unknown")`.
- **Fix:** Try the separator from the dataset's CSV first; or ensure
  every caller passes a schema-aware extractor.

### M21. `read_pipeline_info` swallows partial corruption silently
- **Location:** `viewer_state.py:101-103`. Blanket `except Exception`
  returns `(None, [], set(), {})` — loses `fluor_tokens` even when
  only one field is bad. Triggers the "timepoint unknown" cascade in
  ARCHITECTURE §12.4.
- **Fix:** Validate fields individually; surface a `_set_status`
  warning naming the failed key; keep usable partial info.

### M22. `_image_table_image_cache` grows unboundedly
- **Location:** `image_table_controller.py:1341`. No eviction. Long
  sessions walking wells accumulate every well's image dict.
  `_review_image_include_cache` and `_review_image_threshold_map_cache`
  cap at 32 entries via `.clear()` — first miss after the cap blows
  away the *active* frame's entry too.
- **Fix:** Replace with an `OrderedDict` LRU bounded by entry count.

### M23. `sample_definitions._backup_pre_v2` is a TOCTOU race
- **Location:** `well_viewer/sample_definitions.py:108-114`. Two
  threads / processes both see backup-absent, both copy, second
  overwrites the first's "precious" backup.
- **Fix:** `os.open(..., O_CREAT | O_EXCL)` for the timestamped path;
  ignore `EEXIST`.

### M24. `find_well_images_and_masks` flat-mode `rglob` has no recursion guard
- **Location:** `image_discovery.py:443`. When zips aren't found, it
  recurses the entire data directory tree — scans everything if the
  user points at `~` or a project root by mistake.
- **Fix:** Cap depth at 2-3; skip directories that don't carry the
  well token.

### L16. `well_rank` sentinel `1<<30` flows into colour-index modulo
- **Location:** `selections_model.py:68-79`. `rank_color` filters out
  the sentinel; `reorder_by_line_order:411-417` doesn't. Two reloads of
  the same unparseable selection can yield different fallback colours.
- **Fix:** Seed the fallback index from the selection's stable `id`,
  not iteration order.

### L17. `data_loading.load_well_csv:99` normalises FOV `"-1"` → `"1"`
- Documented but worth a unit test — silent re-bucketing of per-FOV
  stats.

### L18. `viewer_state.read_pipeline_info` doesn't validate `fov_index != tp_index`
- A typo making both indices equal returns the same string for both,
  silently coalescing the entire dataset.
- **Fix:** Assert distinct indices; raise / fall back if equal.

### L19. `smfish_worker._write_counts_to_csvs` picks `csv_matches[0]` silently
- **Location:** `smfish_worker.py:75`. When multiple CSVs match (e.g.
  `A01.csv`, `gfp_A01.csv`), filesystem-order determines which is
  rewritten.
- **Fix:** Reject ambiguous matches with a warning.

### L20. `aggregate_with_threshold_df` cell-area NaN handling
- Already captured in L11 (same root cause). Filed twice from
  different audit angles.

---

## 5. Widgets and theming (`widgets/`, `theme.py`, `ui/theme/`)

### H20. The "dormant" theme system is actually load-bearing
- **Location:** ARCHITECTURE §8 says `ui/theme/` is "dormant scaffolding
  for a future theme switcher." Reality (greppable):
  `runtime_app.py:216`, `analyze_tab.py:37`, `plate_layout.py:9`,
  `barplot_renderer.py:13`, `plot_style.py:15`,
  `batch_export/_common.py:7`, `batch_export/well_grid_button.py:8`,
  `ui_helpers.py:318`, `tabs/cell_gating_tab_view.py:42`,
  `tabs/smfish_tab_view.py:34`, and the themed nav toolbar in
  `ui_helpers.py:_themed_nav_toolbar_class` all import
  `from ui.theme import get_color / PLOT_BG / TXT_PRI / FM_UI /
  build_stylesheet`.
- **What's wrong:** Two competing token systems with **different
  values**:
  - `theme.Colors.accent = "#6B8AFD"` vs `ui.theme … ACCENT = "#3B82F6"`
  - `text_primary = "#E6E9EF"` vs `TXT_PRI = "#F8FAFC"`
  - `panel = "#131A24"` vs `BG_PANEL = "#111827"`
- **Failure mode:** Plot chrome and toolbar icons render in a
  different palette than the QSS chrome. A "Light" theme switch
  would desync widget code from app code.
- **Fix:** Either (a) collapse `ui/theme` to derive from `theme.Colors`
  (single source of truth, `get_color("ACCENT")` proxies to
  `Colors.accent`), or (b) update ARCHITECTURE to admit `ui/theme/` is
  the live token system for non-widget code and reconcile values.
  Whichever — the docs and the values are both currently wrong.

### H21. `ChipGroup.setCurrentIndex` silently fails to emit
- **Location:** `widgets/chip_group.py:85-88`.
- **What's wrong:** Only calls `self._buttons[index].setChecked(True)`.
  If the button is already checked (initial state after `addChip`),
  `setChecked(True)` is a no-op, no signal fires, `_current` stays
  `-1`, and `currentData()` returns `None`. Programmatic sets bypass
  bindingAdapter.
- **Fix:** Update `_current` explicitly and emit `currentChanged`
  when changed — mirror `SegmentedControl.setCurrentIndex`.

### H22. `ChipGroup` multi-select bindingAdapter has mismatched signal shape
- **Location:** `widgets/chip_group.py:126-133`. Exclusive returns
  `(currentData, …, currentChanged[int])`; multi returns
  `(checkedData, …, chipToggled[int, bool])`. ARCHITECTURE §7 says
  all form controls expose a uniform `(getter, setter, change_signal)`
  triple — a generic binder breaks on the 2-arg signal.
- **Fix:** Add `valuesChanged()` zero-arg or `Signal(list)` to
  `ChipGroup`; re-emit from `chipToggled`.

### H23. `Stepper.setRange` can emit a spurious `valueChanged`
- **Location:** `widgets/stepper.py:108-111`. Always calls
  `setValue(self._value)` after range update, which can re-emit even
  though no user action occurred — the Export Style sidebar then
  triggers a redraw with unchanged prefs.
- **Fix:** `blockSignals(True)` around the re-set, or check
  `changed` before emitting.

### M25. `MplToolbar` mpl callback never disconnected
- **Location:** `widgets/mpl_toolbar.py:101`. `mpl_connect` is held
  for the lifetime of the canvas; if the toolbar is destroyed
  separately (lazy tab rebuild, export-style sidebar reparenting),
  the closure outlives the `QLabel` it updates → `RuntimeError:
  wrapped C/C++ object … deleted` in stderr.
- **Fix:** Store the `cid` from `mpl_connect`; `canvas.mpl_disconnect(cid)`
  in `closeEvent` / `hideEvent`-paired-with-detach.

### M26. `PlotCard.setPlotTheme` mutates `matplotlib.rcParams` globally
- **Location:** `widgets/plot_card.py:392-404`. `rcParams` is
  process-global; toggling Publication on Line Graphs silently
  changes colours on a batch-export Bar Plots redraw.
- **Fix:** Use `rc_context` per figure, or configure each new
  Figure on construction via `figure.set_*` only. Drop the global
  `rcParams.update`.

### M27. `PlotCard.setPlotTheme` re-colours lines by insertion order
- **Location:** `widgets/plot_card.py:374-380`. Iterates
  `ax.get_lines()` and assigns palette by `idx % len(palette)`. But
  traces are keyed by well-rank via `plate_layout._rank_color_well` —
  toggling theme breaks the "same well = same colour" invariant.
- **Fix:** Don't recolour lines in `setPlotTheme`. The controllers'
  redraw path will repaint with the correct rank colour. Style
  spines / grid / labels only.

### M28. Hardcoded `#FFFFFF` / `#000000` strokes ignore the theme
- **Locations:** `widgets/well_plate_selector.py:707, 777, 784`;
  `widgets/color_picker_popover.py:118, 134, 200, 202`;
  `widgets/color_swatch_row.py:257`; `widgets/toggle_switch.py:140`;
  `widgets/drawer.py:48` (drop shadow).
- **What's wrong:** ARCHITECTURE §7 says no hardcoded hex. These
  spots short-circuit the token system; a Light theme would invert
  these wrong.
- **Fix:** Add `Colors.ink_light` / `Colors.ink_dark` tokens (or
  `Colors.contrast_on_color`); route through them. Use the existing
  `Colors.drop_shadow_md` for the Drawer backdrop.

### M29. Hardcoded device pixels in widgets that claim DPI awareness
- **Locations:** `widgets/preview_strip.py:39` (`setFixedHeight(48)`,
  `QSize(240, 48)`); `widgets/rail_nav.py:81` (accent strip
  `setFixedWidth(2)`); `:96` (`_glyph.setFixedSize(15, 15)`); `:91`
  (row body padding `(9, 7, 9, 7)`);
  `widgets/collapsible_rail.py:132` (drag handle
  `setFixedSize(14, 64)`); `widgets/color_picker_popover.py:228, 230`
  (`setMinimumSize(160, 140)`, `setMinimumWidth(16)`).
- **What's wrong:** ARCHITECTURE §7 mandates fontMetrics-derived
  sizes.
- **Fix:** Derive from `fontMetrics().height()` / `theme.Spacing.*`.
  `RailNav` strip width should be
  `max(2, round(fontMetrics().height() * 0.13))`.

### M30. Per-instance `setStyleSheet` defeats future global QSS rebuilds
- **Locations:** Many — e.g. `segmented_control.py:156`,
  `chip_group.py:58`, `kbd_hint.py:51`, `popover.py:86`,
  `collapsible_section.py:127`, `collapsible_rail.py:79`,
  `range_pair.py:79`, `plot_canvas.py:108`, `lut_selector.py:190`,
  `pill_tab_bar.py:66`, `preview_strip.py:48`, `empty_state.py:75`,
  `stepper.py:90`.
- **What's wrong:** Per-instance stylesheets win over the global
  `theme.qss()`. Today the global QSS is built once at startup; if a
  runtime theme switcher ever ships (which is exactly what
  `ui/theme/` is built for), these widgets keep their snapshotted
  colours.
- **Fix:** Either move theme-driven rules into `theme.qss()`, or
  override `changeEvent(QEvent.StyleChange)` on each widget to
  rebuild inline QSS.

### L21. `widgets/well_plate_selector` has no keyboard navigation
- 96 wells, mouse-drag-primary. `setFocusPolicy` is default (none).
  Accessibility gap.
- **Fix:** `setFocusPolicy(StrongFocus)`, arrow-key navigation,
  Space/Enter to toggle.

### L22. `SegmentedControl._Segment.setFocusPolicy(Qt.NoFocus)` blocks Tab
- Title-bar Review/Analyze switcher can't be reached by keyboard.
- **Fix:** Make the parent `SegmentedControl` focusable; route
  arrow-key navigation between segments.

### L23. `setStyleSheet(f"opacity: {op};")` is a no-op
- **Location:** `widgets/saved_selections_list.py:321`. Qt QSS doesn't
  support generic `opacity:` on `QLabel`. Misleading; delete.

### L24. `ColorPickerPopover._on_alpha_edited` accepts blank → "255"
- **Location:** `widgets/color_picker_popover.py:351-360`. Empty field
  silently snaps to 255 and re-emits `colorPicked`. Should treat
  blank as "no change".

---

## 6. Cross-cutting and recipes

### General recommendations not tied to one finding

1. **Introduce one `_atomic_write_json(path, data)` helper** in (e.g.)
   `well_viewer/persistence/_io.py` and convert every persistence
   module to use it (fixes C6 cluster + hardens C1 and the smFISH
   writer H15). This is the single highest-leverage fix in the audit.

2. **Introduce a process-wide `ZipFile` LRU cache** keyed on
   `zip_path` and used by `preview_controller.scan_zip_members`,
   `read_member_bytes`, `open_imgref_as_array`, and
   `smfish_worker._process_well`. Closes most of C4 / M22 and the
   smFISH worker's repeated opens.

3. **Audit `_set_active_channel` / `_recalculate_threshold` /
   `_update_channel_selector` together.** These three methods (H7,
   H10, L9, M7) are entangled and own most of the dropdown-sync race
   surface. They want extracting into a single
   `channel_state_controller.py` with a unit-tested entry contract.

4. **Surface a `selection_controller.set_selection(app, new_set,
   commit=True)`** as the single mutation point for `_selected_wells`
   (fixes H8). Then audit all current callers.

5. **Move the orchestrator to actually orchestrate.** Pull dist /
   heatmap dispatch in from `runtime_app._redraw`, pull bar / scatter
   dispatch in from `selection_controller`, and end *every* branch
   with `apply_export_style_to_current`. Fixes M6 + H12 + L10 in one
   refactor.

6. **Single-source the "auto-threshold" helpers** between pipeline and
   viewer (C2 + H1 from the controller audit are the same fix).

7. **Run a one-pass repo grep for hardcoded `#`-colour values, fixed
   pixel sizes, and `from well_viewer import runtime_app` lazy
   imports.** This is the way to verify the architecture's
   "no hardcoded values / no controller→shell coupling" invariants
   end up enforceable rather than aspirational.

8. **Add a small unit-test scaffold** under `_Docs` or a new `tests/`
   for the highest-leverage invariants: well-token canonicalisation
   (M1), schema validator (M5), selections v1/v2 round-trip + mixed
   rejection (C7), atomic JSON helper (C6), and palette uniqueness
   under full-plate selection (M18).

---

## 7. Must-fix-before-next-release shortlist

In rough order:

1. **C1** atomic + merging writes to `pipeline_info.json`.
2. **C2 / H1 (controller side)** factor out the shared auto-threshold
   helpers; pipeline and GUI must agree.
3. ~~**C3** bound the Analyze-tab log and queue.~~ → `b71c149`
4. ~~**C4** zip-handle caching for the Image Table (and incidentally
   smFISH).~~ → `e57eb95`
5. ~~**C5** segmentation-run fingerprint in `cell_overrides.json`.~~
   → `9e053b6`
6. ~~**C6** atomic JSON helper across every persistence module.~~
   → `4c870c2`
7. ~~**C7** reject mixed v1+v2 selections.~~ → `2842f46`
8. ~~**H1 / H2** Stop button reaches the zipper and survives PID reuse.~~
   → `2e9af7b`
9. **H6 / H7 / H8 / H10** the selection + channel-state churn — the
   user-visible perf and "dropdown out of sync" cluster. **H6 and H8 done**
   (`679998b`, `cf649c3`). H7 / H10 still pending — they sit inside the
   most entangled methods on the god-object and want extraction into a
   new `channel_state_controller.py` first.
10. ~~**H11** unify SD ddof choice across stats / aggregator / scatter.~~
    → `75f220f`
11. ~~**H12** apply export style on line / scatter redraws.~~ → `2842f46`
12. ~~**H13** add multiple-comparison correction to the Statistics
    tab.~~ → `9e053b6`
13. ~~**H14 / H15** smFISH worker cancellation + atomic CSV write.~~
    → `17d1857` + `4c870c2`
14. **H20** reconcile `theme.py` vs `ui/theme/` colour values. *Not
    yet done.* Theme reconciliation is sizable and touches every
    widget that imports either system; left for a focused follow-up.

The rest (M-series and L-series) is hygiene worth scheduling but does
not, on its own, change user outcomes today.

**Items 1–8, 10–13 merged via PR #247 → main.** Items 9 (H6, H8 only),
and item 14 (H20) remain. PR #249 closes H5, H10 (partial), M15, M16,
M17, M20, M25, M26, M27, L5, L14, L15 on top of that. See §0.7.5 for
the full status table.

---

## 8. Files referenced

A flat list of files this plan recommends touching, for grep-friendly
follow-up:

```
allwell.py
all_well_launcher.py
analyze_tab.py
process_microscopy.py
WellPlateZipper.py
theme.py
ui/theme/styles.py
services/pipeline_runner.py
services/pipeline_service.py
services/input_resolution_service.py
well_viewer/auto_threshold.py
well_viewer/barplot_controller.py
well_viewer/barplot_renderer.py
well_viewer/data_loading.py
well_viewer/distribution_controller.py
well_viewer/export_service.py
well_viewer/figure_export_editor.py
well_viewer/gating_state.py
well_viewer/heatmap_controller.py
well_viewer/heatmap_models.py
well_viewer/image_discovery.py
well_viewer/image_resolver.py
well_viewer/image_table_controller.py
well_viewer/lineplot_controller.py
well_viewer/load_controller.py
well_viewer/plot_orchestrator.py
well_viewer/plot_style.py
well_viewer/preview_controller.py
well_viewer/review_image_controller.py
well_viewer/runtime_app.py
well_viewer/sample_definitions.py
well_viewer/scatter_controller.py
well_viewer/selection_controller.py
well_viewer/selections_model.py
well_viewer/smfish_worker.py
well_viewer/stats_controller.py
well_viewer/viewer_state.py
well_viewer/persistence/bar_groups.py
well_viewer/persistence/cell_overrides.py
well_viewer/persistence/heatmap_layouts.py
well_viewer/persistence/ratios.py
well_viewer/persistence/sample_definitions.py
well_viewer/views/centre_view.py
well_viewer/views/heatmap_layout_sidebar_view.py
well_viewer/views/ratio_panel_view.py
well_viewer/tabs/heatmap_tab_view.py
well_viewer/batch_export/scatter_panel.py
widgets/chip_group.py
widgets/color_picker_popover.py
widgets/drawer.py
widgets/mpl_toolbar.py
widgets/plot_card.py
widgets/preview_strip.py
widgets/rail_nav.py
widgets/saved_selections_list.py
widgets/segmented_control.py
widgets/stepper.py
widgets/toggle_switch.py
widgets/well_plate_selector.py
```
