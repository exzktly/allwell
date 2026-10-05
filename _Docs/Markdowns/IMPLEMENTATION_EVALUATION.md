# Implementation Evaluation

**Evaluated:** 2026-10-01

**Baseline:** All-Well 9.10.14, commit `1127c41`

**Scope:** application Python, test architecture, and CI configuration

## Executive summary

The implementation is substantially more robust and modular than its size first
suggests. The boundaries described in `ARCHITECTURE.md` are not merely
aspirational: AST guards enforce the Qt-free layers, dependency direction,
feature removability, root-module policy, remote manifest, atomic-write policy,
and several domain single-sources-of-truth. The data-processing and dataset-tool
code is intentionally separated from Qt, and failure-prone filesystem and remote
operations have explicit staging, retry, cleanup, and diagnostic behavior.

The principal risk is **concentrated complexity**, not a generally tangled
architecture. A small set of orchestration and UI-construction functions remain
very large, while `services/remote_runner.py` is 6,809 lines and mixes local
staging, remote process lifecycle, batching, cache coordination, output fetching,
and cleanup. The repository has ratchets that stop these areas from getting
worse, but a ratchet is containment rather than completion. The other major risk
is that the full-application test lane is manual: the tests most capable of
detecting integration failures can remain red until someone elects to run them.

### Overall assessment

| Quality | Assessment | Rationale |
|---|---|---|
| Efficiency | **Good, with one structural hotspot** | Lazy feature construction, cached AST scans, bounded test batches, incremental pipeline paths, Parquet caching, and pipelined remote transfers are strong. Full `ReviewApp` construction remains expensive and unreclaimable in-process. |
| Robustness | **Strong** | Atomic replacement, durable writes, targeted I/O retry, explicit cleanup, completeness records, and unusually extensive static contracts address realistic failure modes. Manual integration coverage and optional scientific dependencies leave known gaps. |
| Unnecessary complexity | **Moderate** | Most complexity has a stated operational reason, but compatibility re-exports, very large builders, and a few multi-role modules make the implementation harder to change than the domain requires. |
| Modularity | **Strong at package level; mixed inside hotspots** | Package dependency direction and Qt boundaries are excellent. Function-level cohesion in remote orchestration, view construction, style application, and plot collection is weaker. |

## Method

This review used the canonical architecture map as the intended design, then
checked whether the implementation and enforcement mechanisms match it. It also
measured all non-test Python modules with a small AST script using the same
decision-point definition as `tests/test_complexity_ratchet.py`.

Measured snapshot:

- 592 application Python files and 217,597 source lines (excluding tests,
  documentation, `.git`, and virtual environments).
- 7,522 functions or methods.
- 18 functions above the repository's cyclomatic-complexity threshold of 40.
- 202 functions or methods at least 100 lines long.
- Largest application modules: `services/remote_runner.py` (6,809 lines),
  `process_microscopy.py` (3,792),
  `review/features/batch_export/base_panel.py` (3,576), and
  `review/features/export/sidebar_view.py` (2,332).
- 888 test files and 9,237 statically discoverable `test_*` functions. These
  are source counts rather than pytest collection counts; parametrization and
  dynamic collection can change the executed total.

`ruff check .` passed. The guard-family pytest run could not start in the audit
environment because `numpy` was not installed; this is an environment
limitation, not evidence that those tests pass or fail on this baseline.

## What is working well

### 1. The module boundaries are explicit and executable

The package taxonomy is unusually clear for a desktop scientific application:
pane-specific code lives below `review/`, `analyze/`, or `datasets/`; reusable
application infrastructure lives in `core/`; pure shared concepts live in
`domain/`; remotely deployed standard-library helpers live in `util/`; and
headless work lives in `pipeline/` and `services/`. More importantly, the test
suite checks these claims. It scans whole directories rather than relying only
on hand-maintained file lists, rejects reverse imports, follows transitive Qt
dependencies, and checks that optional features remain removable.

This is effective modularity: packages can be reasoned about from their path,
and the CI suite detects common forms of architectural erosion. The separate
compute/apply/controller/view structure in Datasets and the collect/renderer
split in Review also put substantial behavior behind Qt-free seams, improving
both reuse and test speed.

### 2. Filesystem behavior is designed around failure

`util/json_write.py` stages JSON in the destination directory, flushes and
optionally synchronizes it, preserves destination permissions, commits with
`os.replace`, and cleans up staging files on failure. `util/retry_io.py`
distinguishes transient storage faults from permanent conditions, preserves
`errno`, applies bounded exponential backoff, cleans partial outputs for every
exception class, and turns `ENOSPC` into a volume-specific diagnostic. These
details materially improve operation on SMB/NFS shares and during process
termination.

The deliberate specialized JSON writer in Review persistence is justified by a
different Windows retry contract. That is useful duplication rather than
accidental duplication: the common invariant is centralized, while the one
different control flow remains local and documented.

### 3. Remote execution favors recoverability over optimistic cleanup

The remote path uses per-well transfer units, persistent dataset caching,
overlapped fetching, manifests, run journals, and verified cleanup. Remote state
is retained when local output verification fails, which is the correct bias for
scientific data. Text subprocesses have an explicit encoding contract, remote
paths and run IDs have single-definition guards, and tests are prevented from
accidentally invoking real SSH tools.

This subsystem is complex because distributed execution and resumability are
complex. The problem is not that the mechanisms exist; it is that too many of
their coordinating roles still share one module.

### 4. Performance work is based on observed costs

The codebase documents and guards several measured optimizations rather than
applying generic micro-optimizations:

- Review can use a column-selective Parquet cache rather than repeatedly parse
  large CSV files.
- The test suite caches parsed ASTs and avoids quadratic temporary-directory
  allocation.
- Fast tests reap parentless Qt widgets to avoid stylesheet work growing with
  test history.
- Slow full-app tests run in bounded fresh processes so the operating system
  reclaims Qt/Python object graphs.
- Remote processing pipelines upload, computation, and output fetching instead
  of serializing those phases.

These choices target end-user or CI latency at the dominant cost centers.

### 5. Robustness policy is encoded close to the failure modes

The repository goes beyond conventional unit coverage with guards for silent
broad exceptions, unbound annotations, optional-return declarations, stale
allowlists, subprocess decoding, log-handler thread affinity, incomplete
archive deletion, feature-state ownership, and single domain vocabularies.
Several of these guard their own negative examples. That is a strong defense
against a common weakness of static policy tests: a green check that no longer
examines the relevant code.

## Risks and improvement opportunities

### Priority 1 — make full-application integration results routine

The fast lane is a good PR gate, but the slow lane is on-demand only. The
architecture documentation explicitly records the consequence: a full-app test
can remain broken until a person runs the workflow. Static guards reduce this
risk but cannot validate widget wiring, real application construction, or all
cross-feature interactions.

**Recommendation:** keep the slow lane out of every PR if its cost is
prohibitive, but restore an automatic signal with one of these bounded options:

1. run six shards weekly and on changes to shell/context/persistence paths;
2. maintain a small integration-smoke subset as a required PR job, leaving the
   complete lane scheduled or manual; or
3. require a successful slow-lane check only for a path-filtered set of
   architecture-changing PRs.

The selected policy should publish when the lane last passed for the target
commit. A manual capability without freshness information is difficult for a
reviewer to use as evidence.

### Priority 1 — continue decomposing `services/remote_runner.py`

At 6,809 lines, the remote runner is the dominant maintenance hotspot. Existing
extractions (`remote_transport`, `remote_commands`, `remote_conda`,
`remote_suspend`, and others) are the right direction, but the original module
still owns multiple independently testable roles. It also re-exports many
extracted private names to preserve old imports. That reduces migration cost in
the short term while keeping the old module's effective API and conceptual
surface large.

**Recommendation:** define a small supported facade and move one cohesive owner
at a time:

- local input classification and staging plan;
- dataset-cache upload coordination;
- remote process and batch state machines;
- fetched-output verification and materialization; and
- top-level run orchestration.

Move tests to the owning modules as each extraction lands, retain compatibility
aliases only for externally supported names, and add a ratchet on the number of
private re-exports. The goal is not a smaller file alone; it is for each state
transition to have one owner and for callers not to know the implementation
module graph.

### Priority 1 — reduce function-level concentration

The complexity ratchet identifies 18 functions over CC 40, and the line-length
scan finds 202 functions of at least 100 lines. The most important current
examples are:

| Function | CC | Lines | Main concern |
|---|---:|---:|---|
| `review.core.plot_style.apply_export_style_prefs` | 104 | 464 | Many independent preference families and target types in one mutation pass. |
| `ExportStyleSidebar._build_ui` | 103 | 1,080 | Construction, binding, layout, and visibility policy are co-located. |
| `collect_bar_items` | 95 | 477 | Grouping, timepoint semantics, normalization, and presentation-ready assembly interact. |
| `review.persistence.view_state.restore` | 83 | 336 | Schema compatibility and feature-specific restoration share one dispatcher. |
| `redraw_heatmap` | 78 | 361 | Collection, validation, rendering, annotations, and state updates are interleaved. |
| `review.core.centre_view.build_centre` | 77 | 686 | Feature composition is encoded as a large imperative builder. |

The threshold test prevents new debt but allows existing entries indefinitely.

**Recommendation:** add a burn-down expectation, for example removing at least
one allowlist entry per feature release or prohibiting an increase in total
allowlisted CC. Prefer extraction by data transformation or UI section, not
arbitrary line count. For builders, return small dataclasses containing the
constructed controls so wiring remains explicit without relying on hundreds of
attributes on one host object.

### Priority 2 — address full-app construction and teardown cost

A full `ReviewApp` creates roughly 1,800 QObjects and adds about 40 MB of
resident memory per test construction according to the repository's own
measurements. The application already lazily builds many feature trees, so the
remaining issue is the size and connectivity of the shell/context object graph
and its test lifecycle. Process batching is a pragmatic containment mechanism,
but it makes integration feedback expensive and complicates coverage.

**Recommendation:** profile ownership rather than applying more garbage
collection. Useful experiments are:

- inventory signal connections that capture the app or a bound app method;
- replace child-to-app back-references with narrow held collaborators;
- explicitly disconnect long-lived relays/coordinators on close;
- create a minimal shell fixture that mounts one feature for most integration
  tests; and
- record object count and RSS for repeated construction in a non-gating
  benchmark so improvements are measurable.

Do not treat process isolation as a defect by itself—it is currently the safe
runner design—but reducing the retained graph would improve both developer
speed and confidence.

### Priority 2 — narrow the public surface created by mixins and re-exports

Analyze's mixins successfully keep individual source files manageable, but they
still form one large `AnalyzeTab` object whose methods and widget attributes can
implicitly depend on one another. Similarly, compatibility re-exports from
`process_microscopy.py` and `remote_runner.py` mean the physical module split
does not always produce an ownership split for callers.

**Recommendation:** treat mixins as a transition toward collaborators, not as
the final unit of encapsulation. New behavior should prefer small objects with
constructor-declared dependencies and narrow return values. Publish explicit
`__all__` facades for stable APIs, migrate first-party imports to true owners,
then remove private compatibility aliases. This will make unused code and cycles
easier to identify and reduce the number of symbols a change can affect.

### Priority 2 — make broad exception policy more semantic

The source contains 859 `except Exception` sites and no bare `except` sites.
The existing guard correctly focuses on *silent* broad catches, and many broad
catches are legitimate boundaries: worker threads, optional backends,
best-effort diagnostics, subprocess cleanup, and user-facing error funnels.
Raw count alone is therefore not a defect.

The remaining risk is classification: a logged programming error can still be
mistaken for recoverable bad input, especially inside long orchestration paths.

**Recommendation:** incrementally tag boundary catches by purpose—such as
`user_input`, `optional_backend`, `worker_boundary`, or `cleanup`—and guard that
only boundary categories may catch `Exception`. Internal transformation helpers
should normally catch the specific library or I/O exceptions they can handle.
This evolves the current “never silent” rule into “broad only at a boundary”
without demanding a disruptive exception rewrite.

### Priority 3 — reduce architecture-document maintenance cost

`ARCHITECTURE.md` is comprehensive and valuable, but at more than 14,000 lines
it also duplicates inventories and measured counts that can become stale. The
test and CI documentation already contains different historical fast-lane
counts. The code guards structural truth better than prose can.

**Recommendation:** keep the document as the conceptual map, but generate or
link volatile inventories (feature rosters, test counts, module-size tables,
and guard lists) from source. Add a short “current hotspots” page or generated
report rather than embedding repeated numeric snapshots throughout the map.
This document should remain an evaluation snapshot, not another canonical
architecture source.

## Suggested sequence

The live implementation status, per-row proofs, and revised measurements for
suggestions 2–5 are maintained in
`_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`; this evaluation remains a
snapshot and must not be used as a progress tracker.

1. **Close the integration-signal gap:** define and automate the smallest useful
   full-app smoke lane.
2. **Extract one remote-runner state owner:** fetched-output verification is a
   good candidate because it has a clear input/output contract and high data
   safety value.
3. **Burn down the three highest-CC Review functions:** split style preferences
   by section/target, sidebar construction by control group, and bar collection
   by pure data stage.
4. **Measure retained Qt ownership:** establish a repeatable benchmark before
   changing teardown behavior.
5. **Shrink compatibility surfaces:** migrate private re-export consumers and
   add liveness ratchets so transitional aliases cannot become permanent.

## Conclusion

All-Well's implementation shows strong engineering discipline around the parts
most likely to lose scientific work: atomic persistence, resumability, remote
cleanup, incremental processing, and explicit data contracts. Its package-level
architecture is modular and unusually well defended by executable rules.

The next gains will not come from adding more global abstractions or more guard
categories. They will come from making a few large coordinators smaller,
turning mixin/re-export transitions into real ownership boundaries, and ensuring
the integration suite produces a fresh signal without requiring a person to
remember it. Those changes would reduce change risk and feedback time while
preserving the robustness mechanisms already in place.
