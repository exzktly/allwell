# Implementation remediation plan: evaluation suggestions 2–5

> **Live execution document.** This is the single source of truth for the
> structural remediation described below. Every implementation commit in this
> workstream updates the applicable row, evidence, baselines, and next-action
> field here. A code change without the matching plan update is incomplete.
> This file is a remediation tracker in the sense of `CLAUDE.md` §6.1 and is
> deleted by the pull request that closes its last row.

**Created:** 2026-10-01

**Planning baseline:** `main` at `1127c41` (All-Well 9.10.14)

**Current state:** R0, C0 and Q0 done; the rest `todo`. Workstream branch:
`implementation-remediation-plan` (one pull request at the end of the whole
plan, so one version bump: 9.10.14 → 9.10.15). Row commits are found with
`git log --grep '^<ROW>:'`.

**Next action:** Q1 — attribute what keeps a closed `ReviewApp` alive
(batch-1 fast lane running against R0 + C0)

## 1. Objective and scope

The plan originates from `IMPLEMENTATION_EVALUATION.md`, an evaluation of the
baseline commit that sits beside this file. That evaluation is a snapshot and
is not updated as rows close; this file is. It made five suggestions:

1. automate a small full-application integration signal;
2. extract cohesive state owners from `services/remote_runner.py`;
3. reduce the three highest-complexity Review functions;
4. measure and then reduce retained Qt ownership; and
5. shrink compatibility re-exports and other transitional API surfaces.

This plan implements suggestions 2–5.

**Suggestion 1 is declined by the repository owner.** The slow lane
(`.github/workflows/slow-lane.yml`) stays a manually dispatched workflow, and
no row in this plan changes CI trigger policy or adds a scheduled or
path-filtered full-app job. The consequence is accepted deliberately: any row
whose evidence depends on a full-app test runs the bounded full-app files
locally (or dispatches the slow-lane workflow by hand) and records the result,
the commit it ran against, and the files it covered in the row's evidence cell.
A full-app check that was not run is recorded as not run, never as passing.

The intended outcome is not merely smaller files. Completion means:

- each remote-run state transition has one named owner;
- the three targeted Review functions are below the existing CC threshold;
- full-app retention has a repeatable measurement and a materially smaller or
  explicitly explained retained graph;
- first-party code and tests import private helpers from their real owners; and
- temporary compatibility aliases are either deleted or explicitly classified
  as supported facade names.

No row may change scientific results, exported values, remote cleanup safety,
or persisted-state compatibility unless a separate behavior change is proposed
and reviewed. This is a structural workstream.

## 2. How this document stays live

### 2.1 Status vocabulary

- **`todo`** — scoped but not started.
- **`wip`** — actively being changed; the row must say where the work stopped.
- **`blocked`** — cannot proceed until a named condition is resolved.
- **`done`** — code landed, its proof command passed, and the evidence cell
  contains the commit and resulting measurement.
- **`declined`** — investigated and intentionally not implemented; the evidence
  cell contains the measured reason. A declined row is never silently deleted.

### 2.2 Update protocol

1. At the start of a session, fetch `main`, read the status board, and take the
   first `todo` row whose dependencies are `done` or `declined`. Re-measure the
   row's baseline against the current tree before relying on it: every figure
   here is a hypothesis about the tree as it stood when it was written.
2. Change that row to `wip` before product-code edits. Record the branch and
   any newly discovered constraint in the row's evidence cell.
3. Keep one row landable: extraction, import migration, tests, ratchet update,
   the matching `ARCHITECTURE.md` edit, and this document move in the same
   commit series. Each pull request bumps `version.py` once, per `CLAUDE.md` §6.
4. A discovered task becomes a sub-row under the row that exposed it before it
   is implemented. Do not hide follow-up work in prose or a commit message.
5. Mark a row `done` only after its Definition of Done and proof commands pass.
   Record actual post-change measurements; never copy the target into the
   evidence cell as if it were observed.
6. **Evidence cites something that will still exist.** A commit hash on a
   branch that is later squashed or rebased stops resolving. Cite the pull
   request number, or a commit reachable from `main` once the PR has merged,
   and confirm it with `git cat-file -e <hash>` before writing it down.
7. **Evidence lists the commands that actually ran.** "The facade guard
   passes" is not evidence that the code the guard describes still works; the
   row's verification group in section 9 is. If a group was not run, the cell
   says so.
8. Update **Current state** and **Next action** at the top in every workstream
   commit. When all rows close, replace **Next action** with `none` and record
   the terminal proof commands and commit in section 10.
9. If the tree contradicts this plan, update the plan in the same commit as the
   correction. The code and guards are authoritative; stale plan text is not.

### 2.3 Status count

Counts are derived from the status board rather than maintained separately:

```bash
F=_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md
for s in todo wip blocked done declined; do
  printf '%-9s %s\n' "$s" "$(awk -F'|' -v state="\`$s\`" \
    '$3 ~ "^[[:space:]]*" state "[[:space:]]*$" {n++} END {print n+0}' "$F")"
done
```

At creation the board contains 20 `todo` rows and no rows in another state.

## 3. Baseline and invariants

### 3.1 Measured baseline

All figures are measured on `main` at `1127c41`.

| Measure | Baseline | Authority |
|---|---:|---|
| `services/remote_runner.py` lines | 6,809 (budget ceiling 6,810) | `wc -l services/remote_runner.py`; `REMOTE_RUNNER_BUDGET` in `tests/test_monolith_budgets.py` |
| Application functions above CC 40 | 18, all on the allowlist | `tests/test_complexity_ratchet.py::_functions_over_threshold` |
| Sum of allowlisted CC | 1,192 | same helper, summed over `ALLOWLIST` |
| `apply_export_style_prefs` | CC 104 / 464 lines | AST measurement |
| `ExportStyleSidebar._build_ui` | CC 103 / 1,080 lines | AST measurement |
| `collect_bar_items` | CC 95 / 477 lines | AST measurement |
| `collect_bar_items._items_for_tp` | CC 77 | AST measurement; nested inside `collect_bar_items` and in scope for C3 |
| `stage_local_zips` | CC 62 | AST measurement; allowlisted, and moved by R2 |
| First-party non-test modules importing `remote_runner` | 5 | `services/remote_bootstrap.py`, `services/pipeline_runner.py`, `analyze/remote_dialog.py`, `analyze/preflight.py`, `scripts/allwell_remote.py` |
| Test files referring to `services.remote_runner` | 55 | `grep -rl` source count |
| … of which use `from services.remote_runner import …` | 33 files, 74 distinct names, 48 with a leading underscore | AST scan of `ImportFrom` nodes |
| … of which import the module and use attributes (`rr.X`) | 28 files | `import services.remote_runner [as …]` source count |
| `monkeypatch.setattr(<runner module>, "name", …)` sites | 24 | source count |
| `"services.remote_runner.<name>"` string patch targets | 5 distinct | source count |
| Full `ReviewApp` construction (Q0 baseline) | mini dataset loaded: 3,855 QObject children open; per construction after close, the `ReviewApp` survives (5 of 5) with +31.1 MB RSS (31.4 / 30.8 in two runs), +40 `QWidget`s (+8 top-level), +746 PySide wrappers, +455 app-class objects. Unloaded: 1,257 QObjects; +4.9 MB, +20 widgets (+4 top-level), +277 wrappers, +197 app objects | `python tests/perf/bench_app_retention.py --iterations 5 [--fixture empty]`; Linux, Python 3.11.15, PySide6/Qt 6.7.3, offscreen, RSS from `/proc/self/statm`. The historical "~1,800 QObjects, ~40 MB per test" was a pytest-context figure |

The import counts measure coupling, not defects. They are recorded so moving a
definition cannot be mistaken for reducing the facade while all consumers keep
using its compatibility alias. **The three access forms are counted separately
on purpose:** an inventory that reads only `from … import` statements misses
every attribute access, `setattr` target and patch string, and therefore
reports an alias as unused while a test still depends on it.

### 3.2 Invariants that every row preserves

1. `services/`, `pipeline/`, `util/`, and `domain/` remain Qt-free, including
   transitive module-scope imports.
2. The remote manifest remains complete; any new remotely required module is
   added to `services/remote_manifest.py` and its guard in the same commit.
3. Remote deletion happens only after local output verification. No extraction
   may weaken retry, run-journal, suspend, or reattach semantics.
4. Existing entry points remain stable: `RemoteRunner`, `OutputFilter`,
   `LocalInputMode`, and the supported CLI imports must continue to resolve.
5. Plot values, ordering, normalization, artist properties, and persisted view
   state remain behaviorally identical through C1–C4.
6. A benchmark is not correctness evidence. Every structural change needs a
   behavioral test that can fail independently of its timing or object count.
7. Line and complexity ratchets only move down. An extraction does not earn
   headroom for unrelated additions.
8. Full-app tests run in the bounded batches prescribed by `ARCHITECTURE.md`
   §10.4, locally or through a manual slow-lane dispatch; no row invokes the
   entire slow lane in one pytest process, and no row adds the slow lane to an
   automatic CI trigger.
9. Removing a module-level name is only safe once every access form in section
   3.1 has been checked. Mutable module state (for example the
   `_transit_refused` set re-exported from `services/remote_scratch.py`) is the
   sharpest case: tests mutate it through the alias, so the alias is live even
   though no `from … import` names it.

## 4. Workstream R — remote-runner ownership

### R0 · Freeze the facade and seed coupling ratchets

Before moving code, define what callers are allowed to import from
`services.remote_runner`. Add an explicit `__all__` for supported runtime names
and an AST guard with two inventories:

- supported facade names used by application/CLI consumers; and
- temporary private compatibility exports used only by tests or legacy
  first-party call sites.

The guard's consumer scan must see **every** access form, not only
`from services.remote_runner import X`:

- `from services.remote_runner import X`;
- `import services.remote_runner [as m]` followed by `m.X` attribute loads and
  stores, under any alias name;
- `monkeypatch.setattr(m, "X", …)` and `patch.object(m, "X", …)` where `m` is
  bound to the runner module; and
- string targets such as `"services.remote_runner.X"` passed to `patch` or
  `monkeypatch.setattr`.

Add a case for each form to `tests/test_guards_bite.py` so the scan cannot
silently narrow later.

The guard must fail when a new private re-export appears and when an allowlisted
alias becomes unused, so the list only shrinks. Removing an alias the guard
reports as unused is a separate step that also runs every test file referring
to `services.remote_runner` (the 55 files in section 3.1), because the guard
is only as good as its scan. Lower `REMOTE_RUNNER_BUDGET` in
`tests/test_monolith_budgets.py` to the measured size after any removal.

**Known candidates, unverified.** An import-statement-only scan of the baseline
reports eleven re-exports with no `from … import` consumer:
`CONDA_ENV_SHELL_HELPERS`, `DEFAULT_ENV_SELECTOR`, `EnvSelector`,
`UPLOAD_WAIT_POLL_S`, `_UPLOAD_WAIT_EXIT`, `_heartbeat_stale_snippet`,
`_wait_for_upload_snippet`, `_RSYNC_FILENAME_RE`, `SUSPEND_REQUEST_TIMEOUT_S`,
`RemoteRunPaths`, and `_transit_refused`. At least one of them is **not**
unused: `tests/test_remote_transit_volume.py` calls
`rr._transit_refused.clear()` through the module. Each candidate is removed
only after the full-form scan confirms it, or after its consumer is migrated to
the owning module in the same commit.

**Definition of Done:** the facade inventory exists, every entry has a reason,
its liveness is tested under all four access forms, `tests/test_guards_bite.py`
covers each form, the remote verification group in section 9 passes, and no
runtime behavior changes.

### R1 · Extract fetched-output verification and materialization

Move the cohesive download side of the remote run into
`services/remote_fetch.py`: output discovery, local staging/materialization,
manifest verification, retry of missing wells, and the decision that permits
remote cleanup. `Fetcher` may move whole or become a thin coordinator over a
new `FetchedOutputVerifier`; choose after the dependency inventory in R0.

The new owner receives explicit collaborators—host/transport, run paths,
output policy, journal sink, and log callback—instead of importing the parent
runner or reading globals from it. Keep remote cleanup outside the verifier:
verification returns a typed result, and the orchestrator decides whether to
delete.

**Characterization required before move:** missing manifest member, corrupt or
partial local ZIP, interrupted transfer, retry success, retry exhaustion,
incremental output, mid-run sidecar merge, and “verification failed means no
remote delete.” Each characterization is shown to fail under a deliberate
mutation of the code it describes before the move begins.

**Definition of Done:** fetched-output behavior has one owner, application and
tests import it from that owner, no new compatibility aliases are added, any
moved function's complexity-allowlist key is renamed in the same commit, and
the remote-runner line budget is lowered to the measured result.

### R2 · Extract local input staging

Move `LocalInputMode`, `ZippedInput`, input-mode detection, staging planning,
channel filtering, incremental FOV filtering, ZIP/folder materialization, and
their private helpers to `services/remote_staging.py`. Keep the public request
object immutable so a staging pass cannot mutate the run spec while deciding
what to upload.

The seam is a typed `StagingRequest -> StagingResult` operation. The result owns
its cleanup callback/context manager and enumerates uploaded wells, dropped
channels, already-complete keys, and diagnostics. That replaces tuple-shaped
coordination without introducing a general framework.

`stage_local_zips` is on the complexity allowlist at CC 62 under the key
`services/remote_runner.py::stage_local_zips`. If it moves, rename the key in
the same commit; if the move also decomposes it below 40, delete the entry and
lower the total-CC ceiling.

**Definition of Done:** `stage_local_zips` delegates to the new owner or moves
there, all staging-focused tests import the owner directly, cleanup semantics
are characterized, the allowlist entry is renamed or removed, and the
module-size/private-export ratchets decrease.

### R3 · Isolate remote process state machines

Move `_RemoteRun`, `_RemoteProcess`, `_BatchedRemoteProcess`, upload heartbeat,
and queue-line iteration behind `services/remote_process.py`. Preserve the
Popen-like contract used by `PipelineRunner`; do not expose internal thread,
queue, or heartbeat classes through the stable facade.

Write state-transition tests for launch, batch-ready, batch-done, suspend,
cancel, EOF, nonzero exit, upload timeout, and thread shutdown. Prefer an enum
plus explicit transition methods over flags distributed across runner and
fetcher objects. This row must not redesign the wire markers; it consolidates
their ownership.

**Definition of Done:** one module owns process lifecycle and teardown, all
worker threads are joined or intentionally daemonized with a tested reason,
and `tests/test_run_threads_are_reaped.py` plus batch/suspend tests pass.

### R4 · Reduce `RemoteRunner.spawn` to orchestration

After R1–R3, make `RemoteRunner.spawn` assemble the staging, process, and fetch
owners and return the established process facade. It must not parse manifests,
construct staging archives, implement transfer retries, or run its own state
machine. Target fewer than 120 executable lines; if that target is wrong, record
the measured dependency reason here rather than gaming it with tiny helpers.

**Definition of Done:** `spawn` reads as a phase orchestration, its tests use
public collaborators rather than patching private module globals, and
`services/remote_runner.py` contains only facade/orchestration code plus
genuinely shared types.

### R5 · Close the old import surface

Migrate first-party and test imports from private `remote_runner` aliases to
their owning modules. Delete each alias immediately after the last consumer
moves. Public compatibility is retained only for names classified as supported
in R0.

**Definition of Done:** the private compatibility-export inventory is zero
under all four access forms; `remote_runner.__all__` contains only supported
facade names; no test imports, attribute-accesses or patches a
leading-underscore name through `services.remote_runner`; liveness guards
remain to prevent regression.

## 5. Workstream C — complexity burn-down

The three functions are independent after C0 and can be worked in any order,
but each extraction must remove its stale entry from
`tests/test_complexity_ratchet.py` when it drops to CC 40 or below.

### C0 · Add behavioral characterization and a total-debt ratchet

The existing ratchet blocks new functions over CC 40 and stale allowlist
entries, but it permits the total complexity of allowlisted functions to rise.
Add a `TOTAL_ALLOWLISTED_CC_BUDGET` ceiling seeded from the measured tree
(1,192 at the baseline) with a growth test and a not-slack test. Add a case to
`tests/test_guards_bite.py` showing the growth test fires when an allowlisted
function gains a branch.

Then establish behavioral characterization at the three seams before any
decomposition:

- style preferences: relevant axes/artists before and after applying every
  preference family, including heatmap and log-axis special cases;
- export sidebar: control roster, object names, defaults, signals, visibility,
  and enabled states without relying on screenshots alone; and
- bar collection: `BarItem` output across grouped/ungrouped, pooled/unpooled,
  multiple-timepoint, fold-change, missing-data, and events modes.

A substantial suite already exists at each seam, so C0 audits it rather than
adding a monolithic snapshot. The following inventory is the starting point for
that audit; it records which files claim each behavior class and is **not**
evidence that they pin it:

| Seam | Behavior class | Existing test files to audit |
|---|---|---|
| Style application | axes targeting, typed/free limits, invalid log bounds | `test_typed_axis_limits_are_the_last_word.py`, `test_export_axis_prefs_per_figure.py` |
| Style application | log bases, major/minor ticks, removal of stale locators | `test_log_minor_ticks.py`, `test_response_log_axis_controls.py` |
| Style application | grids, theme restyle, heatmap/colorbar exemptions | `test_the_grid_stays_off_through_a_theme_flip.py`, `test_grid_show_turns_the_grid_off.py`, `test_heatmap_value_format.py` |
| Style application | markers, density, response colours, value labels | `test_scatter_gating_and_markers.py`, `test_scatter_density_gating.py`, `test_response_color_mode.py`, `test_plot_value_labels.py` |
| Sidebar construction | section roster and feature-specific visibility | `test_properties_hides_what_it_cannot_do.py` |
| Sidebar construction | defaults, getters/setters, state writes, redraw signals | `test_properties_colour_control.py`, `test_properties_value_labels.py`, `test_properties_autoscale_button.py`, `test_properties_time_controls.py` |
| Sidebar construction | rebuild/persistence behavior | `test_properties_panel_survives_a_dataset_load.py`, `test_figure_scoped_style_prefs.py` |
| Bar collection | grouped/ungrouped, pooled/unpooled, ordering, multiple timepoints | `test_barplot_collect.py` |
| Bar collection | fold-change axes, missing controls/data, ratio/subtract operations | `test_barplot_collect.py` |
| Bar collection | events, spread, undefined-vs-zero, tab/batch parity | `test_events_csv_columns.py`, `test_a_spread_reaches_a_csv_as_a_number_or_a_blank.py` |

For each behavior class, make one deliberate mutation of the product code in a
worktree of the branch (for example: skip one preference family in
`apply_export_style_prefs`, drop one section from `_build_ui`, swap pooled and
unpooled aggregation in `collect_bar_items`) and record which listed test
failed and with what `AssertionError`. A class where no test fails gets a new
focused test before C1–C3 may touch that seam. A file's name and docstring are
not evidence that it measures the behavior; only a mutation it catches is.

**C0 results.** Each mutation below was applied to the product code alone,
the seam's test files were run, and the code was restored. "Survived" means the
whole seam suite stayed green; each survivor got the test named beside it, and
that test was then shown failing under the same mutation.

| # | Seam | Mutation | Before C0 | Caught by |
|---|---|---|---|---|
| S1a | Style | flat axis limit applied to every panel, not only the targeted one | survived (25 passed) | `test_export_axis_prefs_per_figure.py::test_a_flat_limit_reaches_only_the_targeted_panel` (new) |
| S1b | Style | typed limits not re-asserted at the end of the pass | caught | `test_typed_axis_limits_are_the_last_word.py::test_a_lost_limit_is_reported_rather_than_silently_fixed` |
| S2 | Style | log-axis subdivisions not re-installed after *Minor ticks* on | survived (39 passed) | `test_log_minor_ticks.py::test_turning_the_minor_tick_row_on_keeps_the_log_subdivisions` (new; base 2, because base 10 coincides with matplotlib's default) |
| S3a | Style | *Show grid* ignored | caught | `test_the_grid_stays_off_through_a_theme_flip.py::test_a_theme_flip_does_not_bring_the_grid_back` |
| S3b | Style | colour-bar exemption removed | caught | `test_heatmap_value_format.py::test_tagged_colorbar_keeps_its_log_scale` |
| S4a | Style | open-marker preference not applied to lines | caught | `test_scatter_gating_and_markers.py::test_style_pass_applies_open_markers_to_a_data_carrying_shape` |
| S4b | Style | scatter `PathCollection`s skipped | caught | `test_scatter_density_gating.py::test_density_respects_open_circle_marker` |
| S4c | Style | data-encoded marker fill overwritten | survived (103 passed) | `test_scatter_gating_and_markers.py::test_style_pass_leaves_a_data_carrying_fill_alone` (new, real `Line2D`) |
| SB1a | Sidebar | Legend section built where the figure has no legend | caught | `test_properties_hides_what_it_cannot_do.py::test_a_section_the_heat_map_cannot_use_is_not_built` |
| SB1b | Sidebar | a control drawn but its key no longer bound (Show grid) | survived (54 passed) | `test_properties_control_contract.py::test_each_panel_binds_its_measured_roster` (new) |
| SB2a | Sidebar | Show grid seeded with a constant, not its stored pref | survived (111 passed) | `test_properties_control_contract.py::test_a_rebuilt_panel_shows_every_stored_value` (new) |
| SB2b | Sidebar | Grid opacity bound under `grid_style` | survived (111 passed) | `test_properties_control_contract.py::test_no_pref_key_is_bound_to_two_controls` (new) |
| SB3 | Sidebar | Legend location not restored from the store | survived (111 passed) | `test_properties_control_contract.py::test_a_rebuilt_panel_shows_every_stored_value` (new) |
| SB4/5 | Sidebar | Error bars control disconnected, or its kind not stored | survived (303 passed across all 27 sidebar-touching files) | `test_properties_control_contract.py::test_choosing_an_error_bar_kind_reaches_the_plot_state` (new) |
| B1 | Bars | pool-replicates toggle ignored | caught | `test_barplot_collect.py::test_collect_bar_items_pool_replicates_off_expands_to_wells` |
| B2 | Bars | well-major emission order replaced by timepoint-major | caught | `test_barplot_collect.py::test_collect_bar_items_multi_tp_well_major_order` |
| B3a | Bars | t0 denominator dropped | caught | `test_barplot_collect.py::test_collect_bar_items_baseline_t0_uses_member_stat` |
| B3b | Bars | control denominator dropped | caught | `test_barplot_collect.py::test_collect_bar_items_fold_change_self_control_yields_one` |
| B4 | Bars | a NaN mean drawn as a bar (`has_mean=True`) | survived (56 passed) | `test_barplot_collect.py::test_a_well_with_no_value_at_the_timepoint_is_drawn_as_missing` (new) |
| B5 | Bars | replicate set ignores FOV mode | survived (56 passed) | `test_barplot_collect.py::test_a_replicate_set_s_spread_follows_the_replicate_unit` (new; helpers stubbed to differ, since the conftest mock computes both from the same numbers) |
| B6 | Bars | single well's Events ignore FOV mode | survived (59 passed) | `test_barplot_collect.py::test_a_single_well_s_events_follow_the_replicate_unit` (new) |
| B7 | Bars | a solo well given a cross-well total | survived (59 passed) | `test_barplot_collect.py::test_a_well_with_no_value_at_the_timepoint_is_drawn_as_missing` (extended) |
| B8 | Bars | replicate set's per-FOV Events spread dropped | survived (59 passed) | `test_barplot_collect.py::test_a_replicate_set_s_spread_follows_the_replicate_unit` (extended) |
| B9 | Bars | the batch collector's copy of B6 | survived (1,276 bar, batch-export and fold-change tests) | `test_barplot_collect.py::test_a_solo_well_draws_the_same_bar_on_the_tab_and_in_a_batch` (new) |

**Definition of Done:** the total-debt ratchet exists with its bite case;
every behavior class in the table has a recorded mutation that a named test
catches, or a new test that does; and no product code changes.

### C1 · Split export-style preference application by concern

Keep `apply_export_style_prefs(fig, prefs)` as the stable coordinator. Extract
private passes with explicit inputs for axes selection, scales/ticks/grid,
typography/spines, line/scatter artists, bars/error bars, and legend/layout.
Each pass owns one preference family and does not reread unrelated keys.

Avoid a generic “apply property by name” engine: Matplotlib artist types have
different semantics, and hiding those behind reflection would exchange visible
branching for implicit complexity.

**Definition of Done:** coordinator and every helper are CC ≤ 40,
`apply_export_style_prefs` is removed from the complexity allowlist, the
total-CC ceiling is lowered to the measured sum, the C0 characterization and
existing style tests pass, re-exports of the touched modules are classified per
A0, and no public call site changes.

### C2 · Split export-sidebar construction into section builders

Replace the 1,080-line `_build_ui` body with one builder per visible control
section. Each returns a frozen dataclass of the controls/signals the parent must
retain; it must not stamp arbitrary new attributes on the sidebar. Centralize
only genuinely repeated row/chrome construction.

Keep visibility and enabled-state policy outside raw widget construction. The
parent assembles sections, registers stable object names, and connects the
small number of cross-section signals.

**Definition of Done:** `_build_ui` and all extracted builders are CC ≤ 40;
`_build_ui` is removed from the allowlist and the total-CC ceiling lowered;
control-roster and signal tests pass; re-exports of the touched modules are
classified per A0; and a before/after screenshot is captured with
`tests/visual/render_views.py` because this is a perceptible UI refactor even
when intended to be pixel-identical.

### C3 · Split bar collection into a typed pipeline

Decompose `collect_bar_items` (CC 95) and its nested `_items_for_tp` (CC 77)
into stages that make their semantics visible:

1. snapshot the request from context;
2. resolve member/timepoint ordering;
3. collect raw member observations;
4. apply pooling and aggregation;
5. apply fold-change/control denominators;
6. represent missing/undefined outcomes; and
7. assemble immutable `BarItem` values and band metadata.

Use typed intermediate dataclasses local to the barplot feature. Do not merge
single-plot and batch code by adding mode flags; share the pure stages and keep
their input adapters separate.

**Definition of Done:** `collect_bar_items` and `_items_for_tp` are CC ≤ 40,
both allowlist entries are removed and the total-CC ceiling lowered, tab/batch
numerical parity passes, re-exports of the touched modules are classified per
A0, and `tests/perf/bench_selection.py --no-draw` shows no material regression
in its bar-collection phase. Correctness tests, not the benchmark, decide
acceptance.

### C4 · Close the selected complexity debt

Re-run the whole complexity inventory, remove stale allowlist entries, lower
the total-CC ceiling, and update the measured table in this plan. Record any
newly exposed helper above the threshold as a defect in C1–C3 rather than
allowlisting it.

**Definition of Done:** the three named targets and `_items_for_tp` are absent
from `ALLOWLIST`, no replacement helper exceeds CC 40, total allowlisted CC is
lower than 1,192 by at least the four removed scores, and the guard family
passes.

## 6. Workstream Q — retained Qt ownership

This workstream measures before changing ownership. It does not promise that
all PySide wrapper retention can be eliminated; it requires that the retained
roots be known and that avoidable application-owned cycles be removed.

**Environment.** Every Q row needs PySide6 and the Qt system libraries. On
Claude Code on the web, `.claude/hooks/session-start.sh` installs both (PySide6
6.7.3, `libegl1` and friends), so these rows are runnable there. In an
environment where PySide6 cannot be installed, the row is `blocked` with that
condition named; a benchmark written but never executed is not a baseline.

All Q-row full-app evidence comes from local bounded runs or a manual
slow-lane dispatch, per section 1. Nothing in this workstream is added to an
automatic CI job.

### Q0 · Build a reproducible retention benchmark

Add `tests/perf/bench_app_retention.py`, a manual benchmark rather than a pytest
test, beside the existing `tests/perf/bench_selection.py`. In a fresh process it
must:

1. create a `QApplication` once;
2. construct `ReviewApp` with a fixed minimal fixture;
3. record QObject count and RSS;
4. close it, process deferred-delete events, drop strong references, and run GC;
5. report surviving weak references grouped by Python type;
6. repeat a bounded number of times; and
7. emit machine-readable JSON alongside a human summary.

Record OS, Python, PySide, Qt, fixture, iteration count, and whether RSS comes
from `resource` or `/proc`. Unsupported platforms report a missing metric rather
than zero. The script must impose no pass/fail threshold.

**Definition of Done:** two consecutive clean-process runs are within a stated
tolerance, output includes enough environment data to compare runs, and the
baseline replaces the historical estimate in section 3.1.

### Q1 · Identify retaining roots

Extend the benchmark with opt-in diagnostics for Python-owned survivors:
`gc.get_referrers`, signal/relay registries, closures capturing `ReviewApp`,
child objects with `_app`/window back-references, and long-lived singleton or
module collections. Diagnostics must summarize types and known ownership edges;
never dump full frames, datasets, or unbounded object representations.

Classify each root as:

- required Qt parent/child ownership;
- required application lifetime ownership;
- test-harness ownership;
- removable signal/closure/back-reference; or
- unknown, with a reproduction pointer.

**Definition of Done:** the top retained roots accounting for at least 80% of
surviving Python wrappers are classified, or the document records why object
graph tooling cannot attribute that proportion.

### Q2 · Add an explicit shutdown contract

Introduce idempotent `close`/`dispose` methods only for collaborators proven by
Q1 to outlive the window: worker relays, coordinators, registries, timers, and
cross-object signal owners. Shutdown stops timers/workers first, disconnects
long-lived outbound signals, clears external registries, and only then lets Qt
delete children. Do not manually delete ordinary parent-owned widgets — doing
so from a teardown fixture is known to segfault the interpreter
(`ARCHITECTURE.md` §10.4).

Add focused lifecycle tests per collaborator. A second dispose call must be a
no-op, and no callback may re-arm work after disposal.

**Definition of Done:** every removable root from Q1 has an owner and tested
shutdown path; the bounded full-app close tests pass in a local or manually
dispatched run, recorded with its commit; no broad teardown fixture is
introduced.

### Q3 · Introduce minimal-shell integration fixtures

Where a slow test exercises one feature rather than shell integration, provide
a supported fixture that mounts that feature with narrow `ViewerContext`
collaborators instead of constructing all of `ReviewApp`. Migrate only tests
whose assertions remain meaningful; shell/navigation/persistence integration
tests continue using the real app.

**Definition of Done:** a documented subset of slow tests becomes fast or uses
substantially fewer QObjects, and each migrated test is shown to fail under the
same representative product mutation as before. No fixture fakes away the
behavior under assertion.

### Q4 · Remeasure and set a regression budget

Run Q0 after Q2/Q3 in clean processes. Set budgets only for stable metrics:
for example, surviving Python `ReviewApp` weakrefs and registered top-level
widgets after close. Keep RSS informational if allocator behavior is noisy.

A regression test that constructs `ReviewApp` is a slow-lane test and is
therefore run manually, like the rest of that lane. If a stable metric can be
asserted without a full app, put that half in the fast lane.

**Definition of Done:** the document records before/after measurements and
environment, stable ownership metrics have a non-slack regression test (with
its lane stated), and any remaining retention is classified. If measurements
prove retention is entirely inside Qt bindings and cannot be safely reduced,
mark the corresponding Q2 sub-row `declined` with that evidence rather than
forcing manual deletion.

## 7. Workstream A — compatibility/API surface

R0 and R5 handle the largest compatibility surface. These rows apply the same
policy to the rest of the planned work without launching a repository-wide API
rewrite.

### A0 · Inventory transitional re-exports in the remote and pipeline facades

Using the same four-form consumer scan as R0, inventory re-exports in
`services/remote_runner.py` and `process_microscopy.py`. Classify each name as:

- supported public facade;
- packaging/entry-point contract;
- test-only compatibility alias;
- first-party transitional alias; or
- unused.

Add liveness tests for the last three categories. A wildcard import or an
unexplained `# noqa: F401` is not a classification.

Modules touched later by C1–C3 are not known until those rows start, so A0
does not inventory them. Instead each of C1–C3 classifies the re-exports of
the modules it touches as part of its own Definition of Done, using this
category list.

**Definition of Done:** every re-export in the two named modules is
classified, aliases confirmed unused under all four access forms are removed,
and the inventory fails if a transitional entry becomes stale.

### A1 · Migrate first-party callers to owning modules

As R and C modules are extracted, change application, script, build, and test
imports to the defining module. Patch at the owning module in tests; patching a
compatibility facade hides the dependency the workstream is trying to remove.

`process_microscopy.py` remains an entry-point facade only for symbols that
external launch/build contracts genuinely require. Internal helpers belong to
`pipeline.*` and should not be re-exported merely to avoid updating tests.

**Definition of Done:** no first-party caller imports, attribute-accesses or
patches a private transitional alias; public facade tests cover supported
names; test imports do not determine the production API.

### A2 · Replace Analyze mixin cross-reach only where touched

This plan does not rewrite the Analyze pane. If a row touches an Analyze mixin,
replace newly encountered implicit sibling-mixin dependencies with a narrow
collaborator or returned controls dataclass. Add an AST ratchet for the exact
cross-reach pattern before changing it. Do not create speculative service
objects for untouched form sections.

**Definition of Done:** touched code introduces no new sibling-mixin dependency;
any migrated seam has constructor-declared inputs and a focused test. If no
implementation row touches Analyze, mark A2 `declined` at close with “not
encountered,” rather than manufacturing work.

### A3 · Freeze the resulting public surfaces

Add or update `__all__` for the stable remote and plotting facades, remove
temporary aliases whose liveness count reached zero, and document import paths
in `ARCHITECTURE.md`. Guards should test resolution and ownership, not merely
the spelling of a list.

**Definition of Done:** all transitional inventories are zero or carry a
time-bounded external-compatibility reason; stable symbols resolve from exactly
their documented facade; architecture documentation matches the tree.

## 8. Status board

Rows are ordered by execution priority. `Depends on` names hard ordering only;
independent C and Q work may proceed while an R row is under review, but each
commit still owns one row.

| ID | Status | Depends on | Deliverable / proof | Evidence |
|---|---|---|---|---|
| R0 | `done` | — | Facade inventory, four-form liveness guard with bite cases, seeded ratchets | `git log --grep '^R0:'`. `__all__` = the 10 supported names; `tests/test_remote_runner_facade.py` inventories the 85 other names reached through the runner (13 alias, 41 binding, 31 owned) under all four access forms via `tests/_facade_scan.py`. Of the 11 candidates, 10 confirmed dead and removed; `_transit_refused` is live (`tests/test_remote_transit_volume.py:120`) and kept. Bite cases: 8 reach shapes + 3 non-reaches; mutating out the attribute, named-setattr and string-target branches failed 4, 2 and 1 of them respectively. Runner 6,810 lines after adding `__all__` (budget 6,810, unchanged). Ran: ruff; guard family + `test_guards_bite` + facade guard (554 passed); remote group + all 55 runner-reaching test files (1,418 passed). Fast lane: see the batch-1 entry in section 11 |
| C0 | `done` | — | Total allowlisted-CC ratchet + mutation-verified characterization per behavior class | `git log --grep '^C0:'`. `TOTAL_ALLOWLISTED_CC_BUDGET = 1192` with growth and not-slack tests and three bite cases. 24 mutation rows across the three seams (section 5, *C0 results*; SB4/5 is two mutations): 10 caught by existing tests, 14 survived; every survivor now fails a named test (11 new tests, 2 extended). Product code unchanged. Ran: ruff; the seam files; guard family. Fast lane: batch 1 in section 11 |
| Q0 | `done` | — | Reproducible `bench_app_retention.py` + JSON output | `git log --grep '^Q0:'`. Two clean-process 5-iteration runs: integer metrics identical, RSS growth 31.4 vs 30.8 MB/iteration (2.1% apart). Stated tolerance: counts exact, RSS ±5%. Baseline recorded in section 3.1. No product code changed |
| A0 | `todo` | R0 | Classified re-export inventory for `remote_runner` and `process_microscopy` | Not started |
| R1 | `todo` | R0, A0 | Fetched-output owner and verification characterization | Candidate: `services/remote_fetch.py` |
| R2 | `todo` | R0, A0 | Typed local-staging owner; `stage_local_zips` allowlist key moved | Candidate: `services/remote_staging.py` |
| R3 | `todo` | R0, A0 | Remote process state-machine owner | Candidate: `services/remote_process.py` |
| R4 | `todo` | R1, R2, R3 | Thin `RemoteRunner.spawn` orchestrator | Target <120 executable lines; validate target |
| R5 | `todo` | R4 | Zero private aliases from `remote_runner` under all access forms | Ratchets from R0 prove closure |
| C1 | `todo` | C0 | `apply_export_style_prefs` CC ≤ 40 | Existing style/log/heatmap tests + C0 characterization |
| C2 | `todo` | C0 | `ExportStyleSidebar._build_ui` CC ≤ 40 | Control contract + `tests/visual/render_views.py` capture |
| C3 | `todo` | C0 | `collect_bar_items` and `_items_for_tp` CC ≤ 40 | Numerical parity + selection benchmark |
| C4 | `todo` | C1, C2, C3 | Stale entries removed; total CC lowered | Complexity guard family |
| Q1 | `todo` | Q0 | Retaining-root classification | Attribute ≥80% of surviving wrappers or explain limit |
| Q2 | `todo` | Q1 | Idempotent explicit shutdown for proven roots | Lifecycle tests + bounded app-close test, run locally |
| Q3 | `todo` | Q0 | Minimal-shell fixture and safe test migrations | Mutation-tested assertions |
| Q4 | `todo` | Q2, Q3 | Before/after result and stable regression budget | Fresh-process benchmark runs |
| A1 | `todo` | R1, R2, R3 | First-party imports point to owners | Private facade access count decreases to zero |
| A2 | `todo` | R4, C4, Q2 | Touched Analyze seams explicit, or declined as not encountered | Scope-controlled |
| A3 | `todo` | R5, C4, Q4, A1, A2 | Stable surfaces frozen and documented | Terminal API guard |

## 9. Verification matrix

Every row runs targeted tests plus the guard family. Commands are adjusted as
files move, and any adjustment is recorded in the row's evidence cell. Every
row also runs `scripts/preflight.sh` before its pull request is opened.

### Always

```bash
ruff check .
scripts/test.sh \
  tests/test_optional_returns_declared.py \
  tests/test_no_unbound_names.py \
  tests/test_no_silent_except.py \
  tests/test_safe_helpers_used_right.py \
  tests/test_architecture_contracts.py \
  tests/test_layering.py \
  tests/test_guardrails.py \
  tests/test_complexity_ratchet.py \
  tests/test_monolith_budgets.py \
  tests/test_stale_app_attrs.py \
  tests/test_doc_version_sync.py \
  tests/test_ctx_app_burndown.py \
  tests/test_guards_bite.py
```

### Remote rows

```bash
scripts/test.sh \
  tests/test_remote_runner.py \
  tests/test_remote_incremental.py \
  tests/test_remote_write_retry.py \
  tests/test_remote_midrun_sidecar.py \
  tests/test_batch_driver.py \
  tests/test_remote_allocation_overlap.py \
  tests/test_run_threads_are_reaped.py \
  tests/test_remote_manifest.py \
  tests/test_first_party_imports_resolve.py

# Every test file that reaches into the runner, by any access form.
scripts/test.sh $(grep -rlE "services\.remote_runner|services import remote_runner" tests --include='*.py')
```

The second command is mandatory for any row that removes or moves a name
`services.remote_runner` exposes; the first group alone does not exercise the
attribute-access and patch-target consumers. The full fast lane is mandatory
for R4, R5, and A3 because those rows change central import and orchestration
surfaces.

### Complexity rows

```bash
scripts/test.sh \
  tests/test_heatmap_value_format.py \
  tests/test_log_minor_ticks.py \
  tests/test_the_grid_stays_off_through_a_theme_flip.py \
  tests/test_scatter_gating_and_markers.py \
  tests/test_response_color_mode.py \
  tests/test_barplot_collect.py \
  tests/test_barplot_errorbars.py \
  tests/test_bar_geometry.py \
  tests/test_events_csv_columns.py
python tests/perf/bench_selection.py --no-draw
```

Each row also runs every file the C0 table lists for its seam. C2 additionally
runs its sidebar/widget tests and captures before/after screenshots with
`tests/visual/render_views.py`. The exact screenshot paths and comparison result
are recorded in its row.

### Qt ownership rows

```bash
python tests/perf/bench_app_retention.py --iterations 5 --json /tmp/app-retention.json
scripts/test.sh tests/test_app_smoke.py -m ""
```

These are run locally or through a manual slow-lane dispatch; none is added to
an automatic CI trigger. Full-app files run one or two at a time, never as one
monolithic pytest process. Q4 runs the benchmark at least twice from fresh
processes. Environment metadata must match before results are compared.

## 10. Terminal Definition of Done

Suggestions 2–5 are complete only when all of the following are true:

1. every status-board row is `done` or `declined` with evidence;
2. `services/remote_runner.py` is a supported facade/orchestrator rather than an
   owner of staging, fetch verification, and process state machines;
3. no test or first-party module imports, attribute-accesses or patches a
   private compatibility name through `services.remote_runner`;
4. the three named Review functions, `_items_for_tp`, and their extracted
   helpers are CC ≤ 40, and the allowlist plus total-CC ceiling have ratcheted
   down;
5. Qt retention has repeatable before/after measurements, classified remaining
   roots, and regression checks for stable ownership metrics;
6. touched stable APIs have explicit, tested facades and no unclassified
   re-exports;
7. the guard family, all targeted test groups, `ruff check .`, and the full fast
   lane pass on the terminal commit;
8. the relevant bounded full-app files pass in fresh processes, run locally or
   by manual slow-lane dispatch and recorded with the commit they ran against;
   and
9. a reconciliation pass re-reads every row against the tree (not against this
   board), and the pull request that closes the last row deletes this file and
   says so in its body.

Terminal evidence belongs below and is filled only when the work closes:

| Evidence | Result |
|---|---|
| Terminal commit | pending |
| `remote_runner.py` lines | pending |
| Private `remote_runner` compatibility aliases | pending |
| Target function CCs | pending |
| Qt retention before / after | pending |
| Guard family | pending |
| Full fast lane | pending |
| Bounded full-app files | pending |

## 11. Fast-lane log

The full fast lane (`scripts/test.sh`) is run locally after each batch of work,
against a tree with no edits in flight, and recorded here with the commit it ran
against. A run that straddled an edit is not recorded.

**Run it in an independent clone, not a `git worktree`.** A baseline run in a
worktree of `main`, while the primary checkout was being edited, reported nine
failures in remote-runner tests whose `inspect.getsource` returned a different
method from the one asked for — the source it read did not match the code it
had imported. All nine passed in isolation on both trees, and the same commit
in a `git clone --no-hardlinks` copy ran clean. The mechanism was not pinned
down; the clone is simply what removes the shared state.

| Batch | Rows | Commit | Result |
|---|---|---|---|
| 0 (baseline) | — | `main` at `1127c41`, in an independent clone | 10,462 passed, 10 skipped, 0 failed (7 min 50 s); exit 139 is the expected PySide6 teardown SIGSEGV after a green run |
