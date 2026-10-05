# Implementation remediation plan: evaluation suggestions 2–5

> **Live execution document.** This is the single source of truth for suggestions
> 2–5 in `IMPLEMENTATION_EVALUATION.md`. Every implementation commit in this
> workstream updates the applicable row, evidence, baselines, and next-action
> field here. A code change without the matching plan update is incomplete.

**Created:** 2026-10-01

**Planning baseline:** `e7c6e36` (All-Well 9.10.14)

**Current state:** R0 and C0 complete; Q0 is blocked on the unavailable PySide6
test environment

**Next action:** A0 — classify the transitional re-exports in the currently
touched modules while Q0 waits for a runnable Qt environment

## 1. Objective and scope

This plan implements four recommendations from the evaluation's suggested
sequence:

2. extract cohesive state owners from `services/remote_runner.py`;
3. reduce the three highest-complexity Review functions;
4. measure and then reduce retained Qt ownership; and
5. shrink compatibility re-exports and other transitional API surfaces.

Suggestion 1, automating a small full-application integration signal, is not in
scope because the request is specifically for suggestions 2–5. The test changes
below may identify a useful smoke subset, but this plan does not change CI
trigger policy.

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

1. At the start of a session, read the status board and take the first `todo`
   row whose dependencies are `done` or `declined`.
2. Change that row to `wip` before product-code edits. Record the branch commit
   and any newly discovered constraint in the row's evidence cell.
3. Keep one row landable: extraction, import migration, tests, ratchet update,
   and this document move in the same commit.
4. A discovered task becomes a sub-row under the row that exposed it before it
   is implemented. Do not hide follow-up work in prose or a commit message.
5. Mark a row `done` only after its Definition of Done and proof commands pass.
   Record actual post-change measurements; never copy the target into the
   evidence cell as if it were observed.
6. Update **Current state** and **Next action** at the top in every workstream
   commit. When all rows close, replace **Next action** with `none` and record
   the terminal proof commands and commit in section 10.
7. If the tree contradicts this plan, update the plan in the same commit as the
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

| Measure | Baseline | Authority |
|---|---:|---|
| `services/remote_runner.py` lines | 6,809 initial / 6,807 current | `wc -l services/remote_runner.py` |
| Application functions above CC 40 | 18 | `tests/test_complexity_ratchet.py` metric |
| `apply_export_style_prefs` | CC 104 / 464 lines | AST measurement |
| `ExportStyleSidebar._build_ui` | CC 103 / 1,080 lines | AST measurement |
| `collect_bar_items` | CC 95 / 477 lines | AST measurement |
| First-party non-test files importing `remote_runner` | 5 | Import search; four are package modules and one is `scripts/allwell_remote.py` |
| Test files referring to `services.remote_runner` | 48; 65 private imported names now inventoried | `rg -l` source count + `tests/test_remote_runner_facade.py` |
| Full `ReviewApp` construction | about 1,800 QObjects and about 40 MB retained per test | existing measurements in `ARCHITECTURE.md` §10.4; Q0 must remeasure |

The import counts measure coupling, not defects. They are recorded so moving a
definition cannot be mistaken for reducing the facade while all consumers keep
using its compatibility alias.

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
8. Full-app tests run in the bounded batches prescribed by `ARCHITECTURE.md`;
   no row invokes the entire slow lane in one pytest process.

## 4. Workstream R — remote-runner ownership

### R0 · Freeze the facade and seed coupling ratchets

Before moving code, define what callers are allowed to import from
`services.remote_runner`. Add an explicit `__all__` for supported runtime names
and an AST guard with two inventories:

- supported facade names used by application/CLI consumers; and
- temporary private compatibility exports used only by tests or legacy
  first-party call sites.

The guard must fail when a new private re-export appears and when an allowlisted
alias becomes unused, so the list only shrinks. Extend
`tests/test_monolith_budgets.py` with the current line baseline if necessary;
the existing 6,810 ceiling is already the primary size ratchet.

**Definition of Done:** facade inventory exists, every entry has a reason, its
liveness is tested, current consumers pass, and no runtime behavior changes.

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
remote delete.”

**Definition of Done:** fetched-output behavior has one owner, application and
tests import it from that owner, no new compatibility aliases are added, and
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

**Definition of Done:** `stage_local_zips` delegates to the new owner or moves
there, all staging-focused tests import the owner directly, cleanup semantics
are characterized, and the module-size/private-export ratchets decrease.

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

**Definition of Done:** private compatibility-export inventory is zero;
`remote_runner.__all__` contains only supported facade names; no test imports a
leading-underscore name from `services.remote_runner`; liveness guards remain
to prevent regression.

## 5. Workstream C — complexity burn-down

The three functions are independent after C0 and can be worked in any order,
but each extraction must remove its stale entry from
`tests/test_complexity_ratchet.py` when it drops to CC 40 or below.

### C0 · Add behavioral snapshots and a total-debt ratchet

The existing ratchet blocks new functions over CC 40 and stale allowlist
entries, but it permits the total complexity of allowlisted functions to rise.
Add a `TOTAL_ALLOWLISTED_CC` ceiling seeded from the measured tree and a
not-slack test. Capture behavior at the three seams before decomposition:

- style preferences: relevant axes/artists before and after applying every
  preference family, including heatmap and log-axis special cases;
- export sidebar: control roster, object names, defaults, signals, visibility,
  and enabled states without relying on screenshots alone; and
- bar collection: serialized `BarItem` output across grouped/ungrouped,
  pooled/unpooled, multiple-timepoint, fold-change, missing-data, and events
  modes.

**Definition of Done:** snapshots fail under a deliberate representative
mutation, total allowlisted CC cannot grow, and no product code changes.

**Live split (2026-10-01):** direct inspection found that the three targets
already have a much larger behavioral suite than the plan assumed, so C0 is
split rather than adding redundant snapshots blindly.

- **C0a — `done`:** `TOTAL_ALLOWLISTED_CC_BUDGET = 1192` plus its growth and
  not-slack tests are in `tests/test_complexity_ratchet.py`. Direct invocation
  against the whole AST inventory passes.
- **C0b — `done`:** the audit found no missing behavior class. The matrix below
  records the executable evidence. No new monolithic snapshot was added: the
  existing focused assertions identify which property drifted, while a single
  serialized object/artist dump would be more brittle and less diagnostic.

| Seam | Required behavior | Existing executable characterization |
|---|---|---|
| Style application | axes targeting, typed/free limits, invalid log bounds | `test_typed_axis_limits_are_the_last_word.py`, `test_export_axis_prefs_per_figure.py` |
| Style application | log bases, major/minor ticks, removal of stale locators | `test_log_minor_ticks.py`, `test_response_log_axis_controls.py` |
| Style application | grids, theme restyle, heatmap/colorbar exemptions | `test_the_grid_stays_off_through_a_theme_flip.py`, `test_grid_show_turns_the_grid_off.py`, `test_heatmap_value_format.py` |
| Style application | markers, density, response colours, value labels | `test_scatter_gating_and_markers.py`, `test_scatter_density_gating.py`, `test_response_color_mode.py`, `test_plot_value_labels.py` |
| Sidebar construction | section roster and feature-specific visibility | `test_properties_hides_what_it_cannot_do.py` |
| Sidebar construction | defaults, getters/setters, state writes, redraw signals | `test_properties_colour_control.py`, `test_properties_value_labels.py`, `test_properties_autoscale_button.py`, `test_properties_time_controls.py` |
| Sidebar construction | rebuild/persistence behavior | `test_properties_panel_survives_a_dataset_load.py`, `test_figure_scoped_style_prefs.py` |
| Bar collection | grouped/ungrouped, pooled/unpooled, ordering, multiple timepoints | `test_barplot_collect.py` |
| Bar collection | fold-change axes, missing controls/data, ratio/subtract operations | `test_barplot_collect.py`, whose H1–H11 and per-axis cases are explicit regression characterizations |
| Bar collection | events, spread, undefined-vs-zero, tab/batch parity | `test_events_csv_columns.py`, `test_a_spread_reaches_a_csv_as_a_number_or_a_blank.py` |

The mutation requirement is satisfied by regression-shaped tests at each seam,
not by the existence of their names: these files state the prior wrong behavior
and assert the downstream state or artist that differed. During C1–C3, each
extraction still has to run its seam's listed tests; C0 does not substitute an
AST roster check for those behavioral assertions.

### C1 · Split export-style preference application by concern

Keep `apply_export_style_prefs(fig, prefs)` as the stable coordinator. Extract
private passes with explicit inputs for axes selection, scales/ticks/grid,
typography/spines, line/scatter artists, bars/error bars, and legend/layout.
Each pass owns one preference family and does not reread unrelated keys.

Avoid a generic “apply property by name” engine: Matplotlib artist types have
different semantics, and hiding those behind reflection would exchange visible
branching for implicit complexity.

**Definition of Done:** coordinator and every helper are CC ≤ 40,
`apply_export_style_prefs` is removed from the complexity allowlist, behavioral
snapshots and existing style tests pass, and no public call site changes.

### C2 · Split export-sidebar construction into section builders

Replace the 1,080-line `_build_ui` body with one builder per visible control
section. Each returns a frozen dataclass of the controls/signals the parent must
retain; it must not stamp arbitrary new attributes on the sidebar. Centralize
only genuinely repeated row/chrome construction.

Keep visibility and enabled-state policy outside raw widget construction. The
parent assembles sections, registers stable object names, and connects the
small number of cross-section signals.

**Definition of Done:** `_build_ui` and all extracted builders are CC ≤ 40;
`_build_ui` is removed from the allowlist; control-roster and signal tests pass;
and a visual audit screenshot is captured because this is a perceptible UI
refactor even when intended to be pixel-identical.

### C3 · Split bar collection into a typed pipeline

Decompose `collect_bar_items` into stages that make its semantics visible:

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
both stale allowlist entries are removed if resolved, tab/batch numerical parity
passes, and `tests/perf/bench_selection.py` shows no material regression in its
bar-collection phase. Correctness tests, not the benchmark, decide acceptance.

### C4 · Close the selected complexity debt

Re-run the whole complexity inventory, remove stale allowlist entries, lower
the total-CC ceiling, and update the measured table in this plan. Record any
newly exposed helper above the threshold as a defect in C1–C3 rather than
allowlisting it.

**Definition of Done:** the three named targets are absent from `ALLOWLIST`, no
replacement helper exceeds CC 40, total allowlisted CC is lower, and the guard
family passes.

## 6. Workstream Q — retained Qt ownership

This workstream measures before changing ownership. It does not promise that
all PySide wrapper retention can be eliminated; it requires that the retained
roots be known and that avoidable application-owned cycles be removed.

### Q0 · Build a reproducible retention benchmark

Add `tests/perf/bench_app_retention.py`, a manual benchmark rather than a pytest
test. In a fresh process it must:

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
baseline replaces the historical estimates in section 3.1.

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
delete children. Do not manually delete ordinary parent-owned widgets.

Add focused lifecycle tests per collaborator. A second dispose call must be a
no-op, and no callback may re-arm work after disposal.

**Definition of Done:** every removable root from Q1 has an owner and tested
shutdown path; application close behavior remains green; no broad teardown
fixture is introduced.

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

**Definition of Done:** the document records before/after measurements and
environment, stable ownership metrics have a non-slack regression test, and any
remaining retention is classified. If measurements prove retention is entirely
inside Qt bindings and cannot be safely reduced, mark the corresponding Q2
sub-row `declined` with that evidence rather than forcing manual deletion.

## 7. Workstream A — compatibility/API surface

R0 and R5 handle the largest compatibility surface. These rows apply the same
policy to the rest of the planned work without launching a repository-wide API
rewrite.

### A0 · Inventory transitional re-exports in touched modules

Use AST symbol/import analysis plus `rg` verification to inventory re-exports
in `services/remote_runner.py`, `process_microscopy.py`, and the modules touched
by C1–C3. Classify each name as:

- supported public facade;
- packaging/entry-point contract;
- test-only compatibility alias;
- first-party transitional alias; or
- unused.

Add liveness tests for the last three categories. A wildcard import or an
unexplained `# noqa: F401` is not a classification.

**Definition of Done:** every relevant re-export is classified, unused aliases
are removed, and the inventory fails if a transitional entry becomes stale.

### A1 · Migrate first-party callers to owning modules

As R and C modules are extracted, change application, script, build, and test
imports to the defining module. Patch at the owning module in tests; patching a
compatibility facade hides the dependency the workstream is trying to remove.

`process_microscopy.py` remains an entry-point facade only for symbols that
external launch/build contracts genuinely require. Internal helpers belong to
`pipeline.*` and should not be re-exported merely to avoid updating tests.

**Definition of Done:** no first-party caller imports a private transitional
alias; public facade tests cover supported names; test imports do not determine
the production API.

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
| R0 | `done` | — | Facade inventory, liveness guard, seeded ratchets | `fc06871`: explicit 10-name facade, 65 live private test imports inventoried, 9 stale aliases removed, line budget 6,810→6,807; all three facade guards pass by direct invocation. Full lane waived by user after the environment could not install dependencies. |
| C0 | `done` | — | Behavioral snapshots + total allowlisted-CC ratchet | `a41dd62` added the 1,192 total-debt ratchet; C0b audited the existing focused behavioral characterizations and found every planned behavior class already pinned, recorded in the C0 matrix. |
| Q0 | `blocked` | — | Reproducible `bench_app_retention.py` + JSON output | PySide6 is absent; package installation is blocked by HTTP 403. A retention benchmark written but never executed would manufacture a baseline, so Q0 waits for a runnable Qt environment. |
| A0 | `todo` | R0 | Classified touched-module re-export inventory | No inventory yet |
| R1 | `todo` | R0, A0 | Fetched-output owner and verification characterization | Candidate: `services/remote_fetch.py` |
| R2 | `todo` | R0, A0 | Typed local-staging owner | Candidate: `services/remote_staging.py` |
| R3 | `todo` | R0, A0 | Remote process state-machine owner | Candidate: `services/remote_process.py` |
| R4 | `todo` | R1, R2, R3 | Thin `RemoteRunner.spawn` orchestrator | Target <120 executable lines; validate target |
| R5 | `todo` | R4 | Zero private aliases from `remote_runner` | Ratchets from R0 prove closure |
| C1 | `todo` | C0 | `apply_export_style_prefs` CC ≤ 40 | Existing style/log/heatmap tests + snapshots |
| C2 | `todo` | C0 | `ExportStyleSidebar._build_ui` CC ≤ 40 | Control contract + visual audit |
| C3 | `todo` | C0 | `collect_bar_items` and helper CC ≤ 40 | Numerical parity + selection benchmark |
| C4 | `todo` | C1, C2, C3 | Stale entries removed; total CC lowered | Complexity guard family |
| Q1 | `todo` | Q0 | Retaining-root classification | Attribute ≥80% of surviving wrappers or explain limit |
| Q2 | `todo` | Q1 | Idempotent explicit shutdown for proven roots | Lifecycle tests + bounded app-close test |
| Q3 | `todo` | Q0 | Minimal-shell fixture and safe test migrations | Mutation-tested assertions |
| Q4 | `todo` | Q2, Q3 | Before/after result and stable regression budget | Fresh-process benchmark runs |
| A1 | `todo` | R1, R2, R3 | First-party imports point to owners | Private facade import count decreases to zero |
| A2 | `todo` | R4, C4, Q2 | Touched Analyze seams explicit, or declined as not encountered | Scope-controlled |
| A3 | `todo` | R5, C4, Q4, A1, A2 | Stable surfaces frozen and documented | Terminal API guard |

## 9. Verification matrix

Every row runs targeted tests plus the guard family. Commands are adjusted as
files move, and any adjustment is recorded in the row's evidence cell.

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
  tests/test_ctx_app_burndown.py -m ""
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
  tests/test_remote_manifest.py -m ""
```

Run additional importer-specific files named by the R0 inventory. The full fast
lane is mandatory for R4, R5, and A3 because those rows change central import
and orchestration surfaces.

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
  tests/test_events_csv_columns.py -m ""
python tests/perf/bench_selection.py --no-draw
```

C2 additionally runs its sidebar/widget tests and captures a screenshot through
the repository's visual-audit harness. The exact screenshot path and comparison
result are recorded in its row.

### Qt ownership rows

```bash
python tests/perf/bench_app_retention.py --iterations 5 --json /tmp/app-retention.json
scripts/test.sh tests/test_app_smoke.py -m ""
```

Full-app files run one or two at a time, never as one monolithic pytest process.
Q4 runs the benchmark at least twice from fresh processes. Environment metadata
must match before results are compared.

## 10. Terminal Definition of Done

Suggestions 2–5 are complete only when all of the following are true:

1. every status-board row is `done` or `declined` with evidence;
2. `services/remote_runner.py` is a supported facade/orchestrator rather than an
   owner of staging, fetch verification, and process state machines;
3. no test or first-party module imports a private compatibility name from
   `services.remote_runner`;
4. the three named Review functions and their extracted helpers are CC ≤ 40,
   and the allowlist plus total-CC ceiling have ratcheted down;
5. Qt retention has repeatable before/after measurements, classified remaining
   roots, and regression checks for stable ownership metrics;
6. touched stable APIs have explicit, tested facades and no unclassified
   re-exports;
7. the guard family, all targeted test groups, `ruff check .`, and the full fast
   lane pass on the terminal commit; and
8. the relevant bounded full-app files pass in fresh processes.

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
